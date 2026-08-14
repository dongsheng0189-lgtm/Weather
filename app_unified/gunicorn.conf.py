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
# %(D)s = 請求處理耗時(微秒)。預設格式沒有耗時，事後根本看不出尖峰時是哪些請求
# 在拖時間；配合 hub_watchdog 的阻塞時長就能指認兇手。
access_log_format = '%(h)s %(t)s "%(r)s" %(s)s %(b)s %(D)sus "%(a)s"'

# hub 阻塞回報門檻(秒)。dlib 等不讓出 gevent hub 的 C 擴充一旦在 request
# handler 內直接執行，整個 worker 停擺 —— 設 5s 是為了只抓真正有感的阻塞，
# 不被正常的短運算洗版。
HUB_BLOCK_THRESHOLD_SECONDS = float(os.environ.get("HUB_BLOCK_THRESHOLD", "5"))


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

    # hub 阻塞偵測（診斷用）。真 OS thread 監看 greenlet 心跳，hub 被同步工作
    # 卡住超過門檻就記 WARNING + 當下堆疊 —— 直接指出是哪段程式碼霸佔 hub。
    # 已實測能抓到 dlib(face_recognition) 並在堆疊中指名 face_locations。
    try:
        from hub_watchdog import start as _start_hub_watchdog
        _start_hub_watchdog(threshold=HUB_BLOCK_THRESHOLD_SECONDS)
        worker.log.info("[post_worker_init] hub_watchdog started (threshold=%ss)",
                        HUB_BLOCK_THRESHOLD_SECONDS)
    except Exception:
        # 監控起不來絕不能擋住 worker 開機
        worker.log.exception("[post_worker_init] hub_watchdog failed to start")
