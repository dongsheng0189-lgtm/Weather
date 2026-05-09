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
