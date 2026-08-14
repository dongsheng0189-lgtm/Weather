"""gunicorn 進入點必須真的能載入 —— 這裡壞掉在本機是看不到的，
會變成 prod 起不來、然後只剩一堆 restart log。
"""
import os


def test_gunicorn_config_uses_single_sync_worker():
    """workers>1 會讓每個 worker 各跑一份探測迴圈，取樣就重複了。"""
    ns = {"__file__": "gunicorn.conf.py"}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "gunicorn.conf.py")) as f:
        exec(f.read(), ns)
    assert ns["workers"] == 1
    assert "gevent" not in ns.get("worker_class", "sync")


def test_wsgi_module_exposes_app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MONITOR_DISABLE_PROBER", "1")
    monkeypatch.setenv("MONITOR_PASSWORD", "x")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "y")
    import wsgi
    assert wsgi.app is not None
