"""檢視頁。單一密碼即可 —— 只有你一個讀者，RBAC 是過度設計。

但「未登入不得看到資料」必須擋死：堆疊裡有內部路徑，不該公開。
"""
from datetime import datetime, timezone


def test_dashboard_requires_login(client):
    r = client.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_wrong_password_rejected(client):
    r = client.post("/login", data={"password": "nope"})
    assert r.status_code == 401


def test_login_then_see_events(client, app):
    with app.app_context():
        from models import db, Event
        db.session.add(Event(ts=datetime.now(timezone.utc), source="app-unified",
                             kind="hub_block", severity="warning",
                             summary="hub blocked 42.0s",
                             detail="face_locations"))
        db.session.commit()

    assert client.post("/login", data={"password": "test-pw"}).status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert "hub blocked 42.0s" in body


def test_dashboard_flags_worker_restart(client, app):
    """pid 變動 = worker 被重啟。這是「卡死→被 SIGKILL」的關鍵佐證，要看得見。"""
    with app.app_context():
        from models import db, ProbeSample
        for pid in (100, 100, 200):
            db.session.add(ProbeSample(ts=datetime.now(timezone.utc),
                                       target="origin", verdict="ok",
                                       app_pid=pid))
        db.session.commit()

    client.post("/login", data={"password": "test-pw"})
    body = client.get("/").get_data(as_text=True)
    assert "重啟" in body
