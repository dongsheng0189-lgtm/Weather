"""WSGI proxy that fast-paths /healthz before Flask app finishes initializing.

Cold start runs db.create_all + ~30 PG migrations + heavy blueprint imports
(face_recognition, sentence_transformers) that take 30+ seconds. During that
window the Gunicorn worker can't accept any request, so Zeabur readiness probe
times out and kills the pod in a restart loop.

This proxy answers GET /healthz with 200 OK immediately while real Flask init
runs in a background OS thread (gevent threadpool, so the gevent hub stays free
to handle the probe).
"""
import threading

# How long non-/healthz requests / attribute access wait for init to finish
# before failing. Init shouldn't take this long — but if it does, fail fast
# instead of holding the request indefinitely.
_INIT_TIMEOUT_SECONDS = 120


def _real_thread(target):
    """Spawn a real OS thread that runs `target`, even under gevent monkey-patching.

    Under monkey.patch_all(), threading.Thread is replaced with a greenlet wrapper
    that runs on the hub — heavy CPU work in the wrapped target would block the
    hub and starve /healthz. gevent.monkey.get_original() retrieves the original
    pre-patched Thread class so we get a real OS thread regardless of patch state.
    """
    try:
        from gevent.monkey import get_original
        Thread = get_original("threading", "Thread")
    except ImportError:
        Thread = threading.Thread
    t = Thread(target=target, daemon=True)
    t.start()
    return t


class LazyAppProxy:
    def __init__(self, factory):
        self._factory = factory
        self._real_app = None
        self._init_error = None
        self._init_done = threading.Event()
        _real_thread(self._init)

    def _init(self):
        try:
            self._real_app = self._factory()
        except BaseException as e:
            self._init_error = e
        finally:
            self._init_done.set()

    def __call__(self, environ, start_response):
        if environ.get("PATH_INFO") == "/healthz":
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]
        if not self._init_done.wait(timeout=_INIT_TIMEOUT_SECONDS):
            start_response("503 Service Unavailable",
                           [("Content-Type", "text/plain")])
            return [b"app init timeout"]
        if self._init_error is not None:
            body = f"app init failed: {self._init_error!r}".encode("utf-8")
            start_response("500 Internal Server Error",
                           [("Content-Type", "text/plain"),
                            ("Content-Length", str(len(body)))])
            return [body]
        return self._real_app(environ, start_response)

    def __getattr__(self, name):
        # Internal attrs (start with _) miss to AttributeError so __init__
        # can set them without infinite recursion through __getattr__.
        if name.startswith("_"):
            raise AttributeError(name)
        if not self._init_done.wait(timeout=_INIT_TIMEOUT_SECONDS):
            raise RuntimeError("LazyAppProxy: init timeout")
        if self._real_app is None:
            raise RuntimeError(f"LazyAppProxy: init failed: {self._init_error!r}")
        return getattr(self._real_app, name)
