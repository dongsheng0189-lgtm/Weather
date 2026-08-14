"""WSGI proxy that fast-paths /healthz before Flask app finishes initializing.

Cold start runs db.create_all + ~30 PG migrations + heavy blueprint imports
(face_recognition, sentence_transformers) that take 30+ seconds. During that
window the Gunicorn worker can't accept any request, so Zeabur readiness probe
times out and kills the pod in a restart loop.

This proxy answers GET /healthz with 200 OK immediately while real Flask init
runs in a background OS thread (gevent threadpool, so the gevent hub stays free
to handle the probe).

After init completes, /healthz delegates to the real Flask /healthz route. A
hung Flask (gevent hub blocked by sync work, DB deadlock) then trips the K8s
liveness probe → pod restart → recovery. The previous always-fast-path masked
runtime hangs and led to the 2026-05-09 20:00 incident (10+ min outage waiting
for gunicorn timeout=600 to SIGKILL the worker).
"""
import threading
import traceback

# How long non-/healthz requests / attribute access wait for init to finish
# before failing. Init shouldn't take this long — but if it does, fail fast
# instead of holding the request indefinitely.
_INIT_TIMEOUT_SECONDS = 120


def _real_thread(target):
    """Spawn a real OS thread that runs `target`, even under gevent monkey-patching.

    Under monkey.patch_all(), threading.Thread is replaced with a greenlet wrapper
    that runs on the hub — heavy CPU work in the wrapped target would block the
    hub and starve /healthz. So we need a genuine OS thread.

    But `get_original("threading", "Thread")` is NOT enough: that class's `start()`
    waits on `self._started`, a `threading.Event` — and Event IS monkey-patched to
    gevent's. The real OS thread setting it never wakes the main greenlet promptly,
    so `start()` blocks until the target *finishes* (measured: 10.01s for a 10s
    target, 2026-08-14). That turns this "background" init into a synchronous call
    and defeats the whole point of the lazy proxy — /healthz would go unanswered
    during cold start, exactly the readiness-probe restart loop this module exists
    to prevent.

    `_thread.start_new_thread` has no Event handshake: it creates the OS thread and
    returns immediately.
    """
    def _guarded():
        try:
            target()
        except BaseException:
            # A bare thread has no default excepthook — dying silently here would
            # leave the proxy waiting on an event that never gets set.
            traceback.print_exc()

    try:
        from gevent.monkey import get_original
        start_new_thread = get_original("_thread", "start_new_thread")
    except (ImportError, AttributeError):
        import _thread
        start_new_thread = _thread.start_new_thread
    return start_new_thread(_guarded, ())


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
        is_healthz = environ.get("PATH_INFO") == "/healthz"

        # Fast-path /healthz only WHILE init is in progress (cold start window).
        # Once init is done — success or failure — fall through so K8s probe
        # reflects the real Flask state.
        if is_healthz and not self._init_done.is_set():
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        if not self._init_done.wait(timeout=_INIT_TIMEOUT_SECONDS):
            start_response("503 Service Unavailable",
                           [("Content-Type", "text/plain")])
            return [b"app init timeout"]
        if self._init_error is not None:
            if is_healthz:
                body = b"app init failed"
                start_response("503 Service Unavailable",
                               [("Content-Type", "text/plain"),
                                ("Content-Length", str(len(body)))])
                return [body]
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
