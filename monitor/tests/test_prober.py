"""探測迴圈：把每次取樣落地。

重點是「一次取樣 = 兩個 target」—— 只量對外網域分不出是 Cloudflare 壞還是
app 壞；origin 那筆才是分水嶺。
"""
from datetime import datetime, timedelta, timezone


def test_run_once_writes_one_sample_per_target(app, monkeypatch):
    import prober
    monkeypatch.setenv("PROBE_PUBLIC_URL", "https://pub.example/healthz")
    monkeypatch.setenv("PROBE_ORIGIN_URL", "http://origin.example:8080/healthz")

    calls = []

    def fake_probe(host, path, **kw):
        calls.append((host, path, kw.get("use_tls"), kw.get("port")))
        return {"verdict": "ok", "code": 200, "err": None, "cf_ray": None,
                "app": {"pid": 7, "uptime_s": 5.0}, "t_dns": 0.01, "t_conn": 0.02,
                "t_tls": 0.03, "t_ttfb": 0.04, "t_total": 0.1}

    monkeypatch.setattr(prober, "probe", fake_probe)
    with app.app_context():
        samples = prober.run_once(app)
        from models import ProbeSample
        assert ProbeSample.query.count() == 2
        assert {s.target for s in samples} == {"public", "origin"}
        assert {s.app_pid for s in samples} == {7}

    assert ("pub.example", "/healthz", True, 443) in calls
    assert ("origin.example", "/healthz", False, 8080) in calls


def test_run_once_survives_probe_exception(app, monkeypatch):
    """探測本身炸掉不能讓整條迴圈死掉 —— 監控停擺比監控報錯更糟。"""
    import prober
    monkeypatch.setenv("PROBE_PUBLIC_URL", "https://pub.example/healthz")
    monkeypatch.delenv("PROBE_ORIGIN_URL", raising=False)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(prober, "probe", boom)
    with app.app_context():
        samples = prober.run_once(app)
        assert samples == []


def test_sweep_deletes_only_old_rows(app):
    import prober
    from models import db, ProbeSample
    now = datetime.now(timezone.utc)
    db.session.add(ProbeSample(ts=now, target="public", verdict="ok"))
    db.session.add(ProbeSample(ts=now - timedelta(days=30),
                               target="public", verdict="ok"))
    db.session.commit()

    deleted = prober.sweep_old(app, days=14)
    assert deleted == 1
    assert ProbeSample.query.count() == 1
