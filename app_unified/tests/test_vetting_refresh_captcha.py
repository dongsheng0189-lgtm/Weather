"""refresh_captcha 端點：只重取 NPA 驗證碼，保留已查好的司法院/MJIB，不重跑。"""
import uuid
import pytest
from models import User
from vetting import routes, npa, judicial, mjib


@pytest.fixture(autouse=True)
def _reset_limiter(app):
    from extensions import limiter
    with app.app_context():
        limiter.reset()
    yield


def _mk(db_session, features=None):
    u = User(username=f"u_{uuid.uuid4().hex[:6]}", role="user", store="B", is_active=True)
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _seed_state():
    """放一筆已查好的 state（含司法院/MJIB 結果 + 舊 NPA 欄位）。"""
    return routes._put_state({
        "npa_session": "OLD_SESSION",
        "npa_tokens": {"_csrf": "old"},
        "npa_jwt": "OLD_JWT",
        "name": "王小明",
        "id_no": "",
        "judicial": [{"case_no": "已查好的案件", "snippet": ""}],
        "mjib": [{"name": "MJIB命中"}],
        "social": {"people": []},
    })


def _patch_npa_ok(monkeypatch):
    monkeypatch.setattr(npa, "fetch_home_state", lambda s=None: ({"_csrf": "new", "_key": "k"}, s or object()))
    monkeypatch.setattr(npa, "fetch_auth_token", lambda s, t: "NEW_JWT")
    monkeypatch.setattr(npa, "fetch_captcha_bytes", lambda s: b"NEWIMG")


def test_refresh_returns_new_captcha_and_keeps_results(app, client, db_session, monkeypatch):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    # 司法院/MJIB 搜尋若被呼叫 = 重跑 = bug
    monkeypatch.setattr(judicial, "submit_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("judicial re-run!")))
    monkeypatch.setattr(mjib, "search_by_name", lambda *a, **k: (_ for _ in ()).throw(AssertionError("mjib re-run!")))
    _patch_npa_ok(monkeypatch)
    qid = _seed_state()

    res = client.post("/vetting/refresh_captcha", json={"query_id": qid})
    assert res.status_code == 200
    body = res.get_json()
    assert body["query_id"] == qid
    assert body["captcha_b64"]  # 有新驗證碼圖

    # 司法院/MJIB 結果原封不動（沒重跑）
    entry = routes._take_state(qid)
    assert entry["judicial"] == [{"case_no": "已查好的案件", "snippet": ""}]
    assert entry["mjib"] == [{"name": "MJIB命中"}]
    # NPA 欄位換新
    assert entry["npa_jwt"] == "NEW_JWT"


def test_refresh_missing_qid_400(app, client, db_session):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    res = client.post("/vetting/refresh_captcha", json={})
    assert res.status_code == 400


def test_refresh_expired_qid_410(app, client, db_session, monkeypatch):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    _patch_npa_ok(monkeypatch)
    res = client.post("/vetting/refresh_captcha", json={"query_id": "nonexistent"})
    assert res.status_code == 410


def test_refresh_requires_vetting_feature_404(app, client, db_session):
    u = _mk(db_session, features=["notes"]); _login(client, u)  # 無 vetting
    res = client.post("/vetting/refresh_captcha", json={"query_id": "x"})
    assert res.status_code == 404
