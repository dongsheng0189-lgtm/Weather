"""Tests for vetting blueprint routes + feature gate."""
import base64
import uuid
import pytest
from unittest.mock import MagicMock
from models import User
from extensions import db, limiter


@pytest.fixture(autouse=True)
def _reset_limiter(app):
    """Clear flask-limiter state between tests (avoid cross-test rate limits)."""
    with app.app_context():
        limiter.reset()
    yield


def _mk_user(db_session, username=None, role="user", root_admin=False, features=None):
    """Make a test user. If `features` is given, override SQLAlchemy column default
    (e.g. pass `features=["notes"]` to explicitly exclude vetting for gate-check tests)."""
    target_name = "admin" if root_admin else (username or f"u_{uuid.uuid4().hex[:6]}")
    # Reuse existing 'admin' user if already created by a prior test
    existing = User.query.filter_by(username=target_name).first()
    if existing:
        if features is not None:
            existing.set_features(features)
            db_session.commit()
        return existing
    u = User(
        username=target_name,
        role=role,
        store="B",
        is_active=True,
    )
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_vetting_index_requires_auth(client):
    res = client.get("/vetting/")
    # 未登入會被 flask-login 導去 login
    assert res.status_code in (302, 401)


def test_vetting_index_404_for_non_admin(app, client, db_session):
    """Non-admin without vetting in features → 404 (gate still works after default change)."""
    u = _mk_user(db_session, features=["notes"])  # 明確排除 vetting
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 404


def test_vetting_index_200_for_root_admin(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "人員查詢" in body
    # 表單只有姓名一個 input
    assert 'id="vet-name"' in body


def test_vetting_index_404_for_super_admin_role(app, client, db_session):
    """super_admin 角色 + 沒 vetting feature → 仍被擋（gate 看 features 不看 role）。"""
    u = _mk_user(db_session, username="boss", role="super_admin", features=["notes"])
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 404


def test_vetting_index_404_for_admin_role(app, client, db_session):
    """admin 角色 + 沒 vetting feature → 仍被擋。"""
    u = _mk_user(db_session, username="manager", role="admin", features=["notes"])
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 404


def test_vetting_nav_hidden_when_user_lacks_vetting_feature(app, client, db_session):
    """nav 內不會出現「人員查詢」給 features 不含 vetting 的 user。"""
    u = _mk_user(db_session, username="boss2", role="super_admin", features=["notes"])
    _login(client, u)
    res = client.get("/notes/")  # 任一其他可進入的頁面
    body = res.get_data(as_text=True)
    assert "人員查詢" not in body
    assert "/vetting/" not in body


def test_vetting_nav_visible_to_root_admin(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/notes/")
    body = res.get_data(as_text=True)
    assert "人員查詢" in body


def test_start_endpoint_returns_query_id_and_captcha(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)

    fake_npa_session = MagicMock()
    fake_tokens = {"_csrf": "abc", "_csrf_header": "X-CSRF-TOKEN", "_key": "k123"}

    def fake_npa_home(sess=None):
        return fake_tokens, fake_npa_session
    monkeypatch.setattr("vetting.routes.npa.fetch_home_state", fake_npa_home)
    monkeypatch.setattr("vetting.routes.npa.fetch_auth_token",
                        lambda sess, tok: "JWT_FAKE")
    monkeypatch.setattr("vetting.routes.npa.fetch_captcha_bytes",
                        lambda sess: b"\xff\xd8\xff\xe0FAKE_JPEG")
    # 司法院 + MJIB 模擬無事發生
    monkeypatch.setattr("vetting.routes.judicial.fetch_home_state",
                        lambda sess=None: ({"form_action": "x", "hidden_fields": {}}, MagicMock()))
    monkeypatch.setattr("vetting.routes.judicial.submit_search",
                        lambda sess, state, name: [])
    monkeypatch.setattr("vetting.routes.mjib.search_by_name",
                        lambda name, session=None: [])
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)

    res = client.post("/vetting/start", json={"name": "王小明", "id": "A123456789"})
    assert res.status_code == 200
    data = res.get_json()
    assert "query_id" in data
    assert "captcha_b64" in data
    base64.b64decode(data["captcha_b64"])  # 合法 base64


def test_start_missing_name_400(app, client, db_session):
    """姓名必填，缺則 400。"""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.post("/vetting/start", json={"id": "A123"})
    assert res.status_code == 400


def test_start_id_optional(app, client, db_session, monkeypatch):
    """身分證選填，沒填只查姓名也應該成功。"""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)

    monkeypatch.setattr("vetting.routes.npa.fetch_home_state",
                        lambda sess=None: ({"_csrf": "x", "_csrf_header": "h", "_key": "k"}, MagicMock()))
    monkeypatch.setattr("vetting.routes.npa.fetch_auth_token", lambda sess, tok: "JWT")
    monkeypatch.setattr("vetting.routes.npa.fetch_captcha_bytes", lambda sess: b"\xff\xd8FAKE")
    monkeypatch.setattr("vetting.routes.judicial.fetch_home_state",
                        lambda sess=None: ({"form_action": "x", "hidden_fields": {}}, MagicMock()))
    monkeypatch.setattr("vetting.routes.judicial.submit_search", lambda sess, state, name: [])
    monkeypatch.setattr("vetting.routes.mjib.search_by_name", lambda name, session=None: [])
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)

    # 不帶 id 也要成功
    res = client.post("/vetting/start", json={"name": "王"})
    assert res.status_code == 200
    data = res.get_json()
    assert "query_id" in data


def test_submit_endpoint_returns_three_segments(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    from vetting import social as social_mod
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [{"subject": "詐欺", "case_no": "n", "date": "d",
                      "court": "c", "detail_url": "u",
                      "keyword_hits": ["詐欺"], "masked_names": []}],
        "mjib": [{"name": "王某", "eng_name": "", "masked_id": "A12***",
                  "birth": "1990", "photo_url": "", "detail_url": ""}],
        "social": social_mod.build_url_bundle("王"),
    })
    monkeypatch.setattr("vetting.routes.npa.submit_query",
                        lambda *a, **kw: [{"name": "王", "crime": "竊盜", "detail_url": ""}])

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "ABCD"})
    assert res.status_code == 200
    data = res.get_json()
    assert "judicial" in data and len(data["judicial"]) == 1
    assert "mjib" in data and len(data["mjib"]) == 1
    assert "npa" in data and len(data["npa"]) == 1


def test_submit_expired_query_id(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.post("/vetting/submit",
                      json={"query_id": "nonexistent", "captcha": "x"})
    assert res.status_code == 410
    assert res.get_json()["error"] == "query_expired"


def test_submit_returns_429_when_npa_ratelimits(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    from vetting.errors import RateLimited
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [], "mjib": [],
    })

    def raise_rl(*a, **kw):
        raise RateLimited("upstream 429")
    monkeypatch.setattr("vetting.routes.npa.submit_query", raise_rl)
    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "x"})
    assert res.status_code == 429
    assert res.get_json()["error"] == "rate_limited"


def test_submit_captcha_invalid(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [], "mjib": [],
    })

    def raise_invalid(*a, **kw):
        raise ValueError("captcha_invalid")
    monkeypatch.setattr("vetting.routes.npa.submit_query", raise_invalid)

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "WRONG"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "captcha_invalid"


def test_put_take_state_redis_roundtrip_rebuilds_session(monkeypatch):
    """cross-worker fix：state 經 Redis JSON round-trip 後，submit 端（可能是另一個
    gunicorn worker）用『不同 session 物件』仍拿得到 cookies/tokens/結果。

    舊版 _QUERY_STATE 是 process-local dict、存 live Session → 2 worker 間
    start/submit 落不同 worker 必逾時（feedback_in_memory_state_workers）。
    """
    import vetting.routes as vr
    from vetting.http_client import make_session

    class _FakeRedis:
        def __init__(self): self.kv = {}
        def set(self, k, v, ex=None): self.kv[k] = v
        def get(self, k): return self.kv.get(k)
        def delete(self, k): self.kv.pop(k, None)

    monkeypatch.setattr(vr, "_redis", _FakeRedis())

    sess = make_session()
    sess.cookies.set("JSESSIONID", "abc123")
    qid = vr._put_state({
        "npa_session": sess,
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [{"subject": "詐欺", "keyword_hits": ["詐欺"]}],
        "mjib": [],
        "social": {"phone": "886-912-345-678", "people": [{"name": "王"}]},
    })

    # 落 Redis 的 blob 必須是密文（PII：姓名/身分證/結果不得明文落地）
    stored = vr._redis.kv["vet:state:" + qid]
    assert isinstance(stored, str) and stored.startswith("enc:v1:")
    assert "A123" not in stored and "詐欺" not in stored

    # 模擬另一個 worker：原 session 物件不可用，只能靠 Redis 還原
    entry = vr._take_state(qid)
    assert entry is not None
    assert entry["npa_session"] is not sess                              # 重建的新 session
    assert entry["npa_session"].cookies.get("JSESSIONID") == "abc123"    # cookie 有還原
    assert entry["npa_tokens"] == {"_csrf": "x", "_csrf_header": "h", "_key": "k"}
    assert entry["npa_jwt"] == "jwt"
    assert entry["name"] == "王" and entry["id_no"] == "A123"
    assert entry["judicial"] == [{"subject": "詐欺", "keyword_hits": ["詐欺"]}]
    # social link-out bundle 也要隨加密 blob 跨 worker 還原（rebase 整併補的白名單欄位）
    assert entry["social"] == {"phone": "886-912-345-678", "people": [{"name": "王"}]}

    # 一次性：刪除後再取應為 None
    vr._del_state(qid)
    assert vr._take_state(qid) is None


def test_start_rate_limited_returns_json_429(app, client, db_session, monkeypatch):
    """超過 start 限流(6/分)→ 回 JSON 429（非預設 HTML），前端才不會噴
    'unexpected token <'。限流 key 為 user id（per-user，非 IP）。"""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    fake_sess = MagicMock()
    monkeypatch.setattr("vetting.routes.npa.fetch_home_state",
                        lambda sess=None: ({"_csrf": "a", "_csrf_header": "h", "_key": "k"}, fake_sess))
    monkeypatch.setattr("vetting.routes.npa.fetch_auth_token", lambda sess, tok: "JWT")
    monkeypatch.setattr("vetting.routes.npa.fetch_captcha_bytes", lambda sess: b"\xff\xd8FAKE")
    monkeypatch.setattr("vetting.routes.judicial.fetch_home_state",
                        lambda sess=None: ({}, MagicMock()))
    monkeypatch.setattr("vetting.routes.judicial.submit_search", lambda s, st, n: [])
    monkeypatch.setattr("vetting.routes.mjib.search_by_name", lambda name, session=None: [])
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)

    hdr = {"Accept": "application/json"}
    codes = [client.post("/vetting/start", json={"name": "王"}, headers=hdr).status_code
             for _ in range(7)]
    assert codes[:6] == [200] * 6, codes          # 6 次允許
    assert codes[6] == 429, codes                  # 第 7 次擋
    r = client.post("/vetting/start", json={"name": "王"}, headers=hdr)
    assert r.status_code == 429
    assert r.content_type.startswith("application/json")   # JSON 而非 HTML
    assert r.get_json().get("error") == "rate_limited"


# ────────────────────────────────────────────────────────────────
# Social / Google link-out (spec 2026-05-13)
# ────────────────────────────────────────────────────────────────

def _mock_npa_judicial_mjib(monkeypatch):
    """Common monkeypatch helper: stub external scraping/captcha calls."""
    monkeypatch.setattr("vetting.routes.npa.fetch_home_state",
                        lambda sess=None: ({"_csrf": "x", "_csrf_header": "h", "_key": "k"}, MagicMock()))
    monkeypatch.setattr("vetting.routes.npa.fetch_auth_token", lambda sess, tok: "JWT")
    monkeypatch.setattr("vetting.routes.npa.fetch_captcha_bytes", lambda sess: b"\xff\xd8FAKE")
    monkeypatch.setattr("vetting.routes.judicial.fetch_home_state",
                        lambda sess=None: ({"form_action": "x", "hidden_fields": {}}, MagicMock()))
    monkeypatch.setattr("vetting.routes.judicial.submit_search", lambda sess, state, name: [])
    monkeypatch.setattr("vetting.routes.mjib.search_by_name", lambda name, session=None: [])
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)


def test_start_accepts_new_social_fields(app, client, db_session, monkeypatch):
    """POST /start with phone/relatives/feature → 200, state has 'social' key."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)

    res = client.post("/vetting/start", json={
        "name": "王小明",
        "id": "A123456789",
        "phone": "0912-345-678",
        "relatives": ["李美麗", "張三"],
        "feature": "新北市",
    })
    assert res.status_code == 200
    data = res.get_json()
    assert "query_id" in data

    # Inspect state to confirm social bundle is stashed
    from vetting.routes import _take_state
    state = _take_state(data["query_id"])
    assert "social" in state
    assert state["social"]["phone"] is not None
    assert len(state["social"]["people"]) == 3  # 1 employee + 2 relatives


def test_start_social_optional_fields_default_to_no_phone_no_relatives(
        app, client, db_session, monkeypatch):
    """Omitting new fields → social bundle still produced, phone null, employee only."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)

    res = client.post("/vetting/start", json={"name": "王小明"})
    assert res.status_code == 200
    from vetting.routes import _take_state
    state = _take_state(res.get_json()["query_id"])
    assert state["social"]["phone"] is None
    assert len(state["social"]["people"]) == 1


def test_start_relatives_trimmed_to_2(app, client, db_session, monkeypatch):
    """Sending 4 relatives → backend caps at 2 (defense in depth, UI also caps)."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)

    res = client.post("/vetting/start", json={
        "name": "王",
        "relatives": ["A", "B", "C", "D"],
    })
    assert res.status_code == 200
    from vetting.routes import _take_state
    state = _take_state(res.get_json()["query_id"])
    assert len(state["social"]["people"]) == 3  # 1 + 2 (trimmed)


def test_submit_response_includes_social_key(app, client, db_session, monkeypatch):
    """End-to-end: /submit response must include 'social' alongside judicial/mjib/npa."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    from vetting import social as social_mod
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "",
        "judicial": [],
        "mjib": [],
        "social": social_mod.build_url_bundle(
            "王", phone="0912345678", relatives=["李"]
        ),
    })
    monkeypatch.setattr("vetting.routes.npa.submit_query", lambda *a, **kw: [])

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "ABCD"})
    assert res.status_code == 200
    data = res.get_json()
    assert "judicial" in data
    assert "mjib" in data
    assert "npa" in data
    assert "social" in data
    assert data["social"]["phone"] is not None
    assert len(data["social"]["people"]) == 2  # 1 emp + 1 rel


def test_submit_response_shape_unchanged_for_existing_keys(
        app, client, db_session, monkeypatch):
    """Regression: existing judicial/mjib/npa shapes are byte-identical to pre-spec."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    from vetting import social as social_mod
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [{"subject": "x", "case_no": "n", "date": "d",
                      "court": "c", "detail_url": "u",
                      "keyword_hits": [], "masked_names": []}],
        "mjib": [{"name": "王", "eng_name": "", "masked_id": "A***",
                  "birth": "1990", "photo_url": "", "detail_url": ""}],
        "social": social_mod.build_url_bundle("王"),
    })
    monkeypatch.setattr("vetting.routes.npa.submit_query",
                        lambda *a, **kw: [{"name": "王", "crime": "竊盜", "detail_url": ""}])

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "ABCD"})
    data = res.get_json()
    # judicial entry shape
    assert data["judicial"][0]["subject"] == "x"
    assert "keyword_hits" in data["judicial"][0]
    # mjib entry shape
    assert data["mjib"][0]["name"] == "王"
    assert "masked_id" in data["mjib"][0]
    # npa entry shape
    assert data["npa"][0]["name"] == "王"
    assert data["npa"][0]["crime"] == "竊盜"


# ────────────────────────────────────────────────────────────────
# DOM-level smoke tests: index.html render logic + new fields
# ────────────────────────────────────────────────────────────────

def test_vetting_index_has_new_input_fields(app, client, db_session):
    """GET /vetting/ renders the new optional fields in the form."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/vetting/")
    html = res.get_data(as_text=True)
    # Optional-fields hint (form lists fields inline, no collapse)
    assert "以下選填，填越多搜尋越準" in html
    # Input field IDs
    assert 'id="vet-phone"' in html
    assert 'class="vet-rel-input"' in html
    assert 'id="vet-feature"' in html
    # The + button
    assert 'id="vet-add-rel"' in html


def test_vetting_index_has_social_render_logic(app, client, db_session):
    """index.html includes the renderResult logic for D/E/F sections."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/vetting/")
    html = res.get_data(as_text=True)
    # Banner text
    assert "如何用無痕模式開啟" in html
    # Section headers (in the JS, will be rendered when social data present)
    assert "D. 📞 電話標記" in html
    assert "E + F. 🌐 社群 / Google 搜尋連結" in html
    # All 5 platform incognito guides present
    assert "Chrome / Edge" in html
    assert "Safari（Mac）" in html
    assert "Firefox" in html
    assert "Chrome（Android）" in html
    assert "Safari（iPhone" in html


def test_vetting_fb_ig_use_brand_icons(app, client, db_session):
    """FB/IG 按鈕用品牌色 + 品牌 SVG（取代不直覺的通用 📱）。"""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    html = client.get("/vetting/").get_data(as_text=True)
    # 不再用通用手機 emoji
    assert "📱 FB" not in html and "📱 IG" not in html
    # FB 品牌藍 + IG 漸層；各自有品牌 SVG icon 常數
    assert "#1877F2" in html
    assert "linear-gradient(45deg,#feda75" in html
    assert "var FB_ICON" in html and "var IG_ICON" in html


def test_vetting_phone_buttons_use_fitting_icons(app, client, db_session):
    """D 段電話按鈕用相對應圖示（取代純文字「→ Whoscall」）。TrueCaller 已移除。"""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    html = client.get("/vetting/").get_data(as_text=True)
    assert "→ Whoscall" not in html
    # TrueCaller 整個拿掉
    assert "TrueCaller" not in html and "truecaller" not in html.lower()
    # Whoscall 品牌徽章 icon + Google 四色 G（皆白底按鈕）
    assert "var WHOSCALL_ICON" in html and "var GOOGLE_G" in html
    assert "var PHONE_ICON" not in html  # 原本只給 TrueCaller 用，一併移除
