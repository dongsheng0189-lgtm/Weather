import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = 1
worker_class = "geventwebsocket.gunicorn.workers.GeventWebSocketWorker"
timeout = 600
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
