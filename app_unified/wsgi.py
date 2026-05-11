from gevent import monkey
monkey.patch_all()

# Re-point psycopg2's wait callback at gevent.socket so PG I/O yields to the
# hub instead of blocking the worker natively. Without this, a stale PG
# connection (server-side idle timeout closes it) hangs the worker in native
# recv() — no heartbeat to gunicorn master — SIGKILL after timeout=60s.
# Must run AFTER monkey.patch_all() and BEFORE any psycopg2 connection opens.
from psycogreen.gevent import patch_psycopg
patch_psycopg()

import os, sys, types, time

# pkg_resources 替代方案：用 importlib 提供 resource_filename
try:
    import pkg_resources
    print("=== pkg_resources: available ===", flush=True)
except ImportError:
    print("=== pkg_resources: missing, creating shim ===", flush=True)
    pkg_resources = types.ModuleType("pkg_resources")

    def _resource_filename(package_or_req, resource_name):
        import importlib
        mod = importlib.import_module(str(package_or_req))
        mod_dir = os.path.dirname(mod.__file__)
        return os.path.join(mod_dir, resource_name)

    pkg_resources.resource_filename = _resource_filename
    sys.modules["pkg_resources"] = pkg_resources
    print("=== pkg_resources shim: OK ===", flush=True)

from wsgi_lazy import LazyAppProxy


def _create_flask_app():
    """Run create_app() with timing logs to /tmp/app.log so cold-start
    behavior is observable post-mortem (Zeabur runtime log viewer is
    unreliable; /tmp/app.log RotatingFileHandler is ground truth)."""
    t0 = time.time()
    print("=== wsgi: importing app module ===", file=sys.stderr, flush=True)
    from app import create_app
    t1 = time.time()
    # app.py module-level configures logging.basicConfig with /tmp/app.log
    # RotatingFileHandler at WARNING level — these warnings now land there.
    import logging
    logging.warning(f"=== wsgi: app module imported in {t1 - t0:.2f}s ===")
    logging.warning("=== wsgi: calling create_app() ===")

    application = create_app()

    t2 = time.time()
    logging.warning(
        f"=== wsgi: create_app() returned in {t2 - t1:.2f}s "
        f"(total wsgi init {t2 - t0:.2f}s) ==="
    )
    return application


# Module-level WSGI callable for `gunicorn ... wsgi:app`. The proxy responds
# /healthz with 200 OK immediately; the real Flask app is built in a background
# OS thread so /healthz stays available throughout cold start (otherwise Zeabur
# readiness probe times out and kills the pod in a restart loop).
app = LazyAppProxy(_create_flask_app)
