"""白名單比對/新增/去重/改/刪 邏輯。"""
from vetting import whitelist


def test_add_then_match_various_formats(app, db_session):
    assert whitelist.add("0912-345-678", user_id=1, user_name="alice") == "ok"
    assert whitelist.is_whitelisted("0912345678") is True       # 不同格式同號
    assert whitelist.is_whitelisted("+886912345678") is True
    assert whitelist.is_whitelisted("0911000000") is False


def test_add_duplicate_returns_exists(app, db_session):
    assert whitelist.add("0922333444") == "ok"
    assert whitelist.add("0922-333-444") == "exists"            # 正規化後相同


def test_add_invalid_returns_invalid(app, db_session):
    assert whitelist.add("not-a-phone") == "invalid"
    assert whitelist.add("") == "invalid"


def test_is_whitelisted_empty_false(app, db_session):
    assert whitelist.is_whitelisted("") is False
    assert whitelist.is_whitelisted(None) is False


def test_list_all(app, db_session):
    whitelist.add("0933444555", user_name="bob")
    rows = whitelist.list_all()
    assert any(r["phone"] == "0933444555" and r["created_by_name"] == "bob"
               for r in rows)


def test_update(app, db_session):
    whitelist.add("0955666777")
    rid = whitelist.list_all()[0]["id"]
    assert whitelist.update(rid, "0955-666-888") == "ok"
    assert whitelist.is_whitelisted("0955666888") is True
    assert whitelist.is_whitelisted("0955666777") is False


def test_update_to_existing_returns_exists(app, db_session):
    whitelist.add("0966111222")
    whitelist.add("0966333444")
    rid = [r for r in whitelist.list_all() if r["phone"] == "0966333444"][0]["id"]
    assert whitelist.update(rid, "0966-111-222") == "exists"


def test_update_invalid_and_not_found(app, db_session):
    whitelist.add("0977222333")
    rid = whitelist.list_all()[0]["id"]
    assert whitelist.update(rid, "xxx") == "invalid"
    assert whitelist.update(999999, "0988555666") == "not_found"


def test_delete(app, db_session):
    whitelist.add("0999888777")
    rid = whitelist.list_all()[0]["id"]
    assert whitelist.delete(rid) is True
    assert whitelist.is_whitelisted("0999888777") is False
    assert whitelist.delete(rid) is False
