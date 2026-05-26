import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
# 2 workers so a single hung worker doesn't take the whole app down. Each
# worker independently lazy-inits via wsgi_lazy. Sized for the Zeabur 2 vCPU
# + 8 GB plan; each worker holds its own dlib/sentence_transformers models.
workers = 2
worker_class = "geventwebsocket.gunicorn.workers.GeventWebSocketWorker"
# 60s (was 600s): a hung worker is SIGKILLed in 1 minute instead of 10.
# Combined with wsgi_lazy /healthz delegating to Flask post-init, the K8s
# liveness probe also catches hangs and triggers pod restart.
timeout = 60
keepalive = 2
accesslog = "-"
errorlog = "-"
loglevel = "info"


# post_fork hook removed: previously did `with app.app_context(): db.engine.dispose()`
# to reset connections inherited from a preloaded master. We don't use --preload,
# AND the DB engine is now created inside this worker's lazy-init thread (see
# wsgi_lazy.LazyAppProxy), so there's nothing fork-inherited to dispose. Keeping
# the hook would block worker startup on `app.app_context()` (which waits for
# init via LazyAppProxy.__getattr__), defeating the /healthz fast-path purpose.


def post_worker_init(worker):
    """Pre-load torch / sentence_transformers / sklearn dep chain in worker
    main thread (default gevent hub).

    Why: torch init transitively calls `ctypes.util.find_library('dl')` →
    `subprocess.Popen(['/sbin/ldconfig', '-p'])`. Under gevent monkey patching
    this becomes gevent.subprocess.Popen, which builds a libev child watcher
    on the *current* hub. If first-time import lands inside gevent.threadpool
    worker (where the worker thread has its own non-default hub), libev raises
    `TypeError: Child watchers are only available on the default loop` and AI
    summary fails (2026-05-26 incident).

    Running the import here happens BEFORE the worker accepts requests, in the
    worker's main thread → default hub → subprocess.Popen works → torch init
    completes once per worker. Module cache absorbs subsequent imports inside
    threadpool workers (no second subprocess call).

    Cost: ~3-5s extra worker boot + ~700MB resident per worker (within Zeabur
    2 vCPU / 8GB plan). Model weights still lazy-load on first AI summary call
    (~470MB, pulled from local cache baked into Docker image, no subprocess).
    """
    worker.log.info("[post_worker_init] pre-loading torch / sentence_transformers chain...")
    try:
        import sentence_transformers  # noqa: F401 - triggers torch init chain
        import sklearn.cluster  # noqa: F401
        import numpy  # noqa: F401
        worker.log.info("[post_worker_init] pre-load done — AI summary deps ready")
    except Exception:
        worker.log.exception(
            "[post_worker_init] pre-load FAILED — AI summary will fail with "
            "child-watcher TypeError when first request hits"
        )
