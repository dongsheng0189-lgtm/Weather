"""/ingest 是 app-unified 卡死當下唯一能送出證據的管道。

它必須：擋掉沒授權的、吞不下的資料要明確拒絕、而且絕不能因為欄位缺失就 500
（app-unified 那端是 fire-and-forget，500 對它沒意義，只會讓證據靜靜消失）。
"""
import json


def _post(client, payload, token="test-token"):
    return client.post("/ingest", data=json.dumps(payload),
                       content_type="application/json",
                       headers={"Authorization": f"Bearer {token}"})


def test_healthz_ok(client):
    assert client.get("/healthz").status_code == 200


def test_rejects_missing_token(client):
    r = client.post("/ingest", data="{}", content_type="application/json")
    assert r.status_code == 401


def test_rejects_wrong_token(client):
    r = _post(client, {"kind": "hub_block", "summary": "x"}, token="bad")
    assert r.status_code == 401


def test_accepts_hub_block_and_persists(client, app):
    r = _post(client, {"kind": "hub_block", "severity": "warning",
                       "summary": "hub blocked 42.0s",
                       "detail": "Traceback...\n  face_locations"})
    assert r.status_code == 201
    with app.app_context():
        from models import Event
        e = Event.query.one()
        assert e.kind == "hub_block"
        assert "face_locations" in e.detail
        assert e.source == "app-unified"


def test_missing_summary_is_400_not_500(client):
    r = _post(client, {"kind": "hub_block"})
    assert r.status_code == 400


def test_unknown_kind_rejected(client):
    """白名單制：只收診斷事件。收一般 app log 需先補欄位加密。"""
    r = _post(client, {"kind": "user_note", "summary": "leaked pii"})
    assert r.status_code == 400


def test_accepts_rate_limit_and_persists(client, app):
    """限流取證事件（2026-08-18）。只帶雜湊與層數，不含 IP。"""
    r = _post(client, {"kind": "rate_limit", "severity": "warning",
                       "summary": "rate limited: /auth/verify ×17, 3 clients, pid=42",
                       "detail": "window: 63.2s since last report\n"
                                 "clients: a3f1c2=14, 7b09de=2, 1e4405=1\n"
                                 "xff_hops: 2"})
    assert r.status_code == 201
    with app.app_context():
        from models import Event
        e = Event.query.one()
        assert e.kind == "rate_limit"
        assert "a3f1c2=14" in e.detail
