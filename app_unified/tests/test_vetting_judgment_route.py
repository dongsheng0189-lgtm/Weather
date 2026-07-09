"""站內判決書檢視器 route：權限、SSRF 白名單、渲染。"""
import uuid
import pytest
from models import User
from extensions import db
from vetting import judicial


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


GOOD_URL = "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&id=TCDM,114,x,3,20251229,1&ot=in"


def test_judgment_renders_highlighted(app, client, db_session, monkeypatch):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    monkeypatch.setattr(judicial, "fetch_judgment_html",
                        lambda url, session=None: '<div class="jud_content">被告王小明犯詐欺。</div>')
    res = client.get(f"/vetting/judgment?u={GOOD_URL}&name=王小明")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'class="vet-hl"' in body
    assert "王小明" in body


def test_judgment_rejects_bad_url_400(app, client, db_session):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    res = client.get("/vetting/judgment?u=https://evil.com/FJUD/data.aspx&name=王小明")
    assert res.status_code == 400


def test_judgment_requires_vetting_feature_404(app, client, db_session):
    u = _mk(db_session, features=["notes"]); _login(client, u)   # 無 vetting
    res = client.get(f"/vetting/judgment?u={GOOD_URL}&name=王小明")
    assert res.status_code == 404


def test_judgment_fetch_failure_shows_error(app, client, db_session, monkeypatch):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    def _boom(url, session=None):
        raise RuntimeError("blocked")
    monkeypatch.setattr(judicial, "fetch_judgment_html", _boom)
    res = client.get(f"/vetting/judgment?u={GOOD_URL}&name=王小明")
    assert res.status_code == 502
    assert "開原判決書" in res.get_data(as_text=True)
