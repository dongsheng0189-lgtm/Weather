"""Tests for notes blueprint."""
import pytest


def test_notes_index_requires_login(client):
    res = client.get('/notes/')
    assert res.status_code in (302, 401)


def test_notes_index_logged_in(logged_in_client):
    res = logged_in_client.get('/notes/')
    assert res.status_code == 200


def test_create_note(logged_in_client):
    res = logged_in_client.post('/notes/api', json={
        'title': 'Test Note', 'content': 'Hello world', 'store': 'B'
    })
    assert res.status_code == 201
    data = res.get_json()
    assert data['status'] == 'ok'
    assert 'id' in data
    return data['id']


def test_get_note(logged_in_client):
    # Create first
    res = logged_in_client.post('/notes/api', json={'title': 'Get Test', 'content': 'body'})
    note_id = res.get_json()['id']

    res = logged_in_client.get(f'/notes/api/{note_id}')
    assert res.status_code == 200
    data = res.get_json()
    assert data['title'] == 'Get Test'


def test_update_note(logged_in_client):
    res = logged_in_client.post('/notes/api', json={'title': 'Old Title', 'content': 'old'})
    note_id = res.get_json()['id']

    res = logged_in_client.put(f'/notes/api/{note_id}', json={'title': 'New Title'})
    assert res.status_code == 200
    assert res.get_json()['status'] == 'ok'

    res = logged_in_client.get(f'/notes/api/{note_id}')
    assert res.get_json()['title'] == 'New Title'


def test_delete_note(logged_in_client):
    res = logged_in_client.post('/notes/api', json={'title': 'To Delete', 'content': ''})
    note_id = res.get_json()['id']

    res = logged_in_client.delete(f'/notes/api/{note_id}')
    assert res.status_code == 200

    res = logged_in_client.get(f'/notes/api/{note_id}')
    assert res.status_code == 404


def test_list_notes(logged_in_client):
    logged_in_client.post('/notes/api', json={'title': 'List Test', 'content': ''})
    res = logged_in_client.get('/notes/api')
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list)


def test_store_filter(client, db_session):
    """Cross-store filter is super_admin only (admin/user are locked to own store).

    Original test used logged_in_client (role=user) but server coerces store to
    current_user.store for non-super_admins (notes/routes.py:437-442). To test
    real cross-store filter behavior, log in as super_admin with valid Store
    records in DB.
    """
    import time
    import uuid
    from models import User, Store
    # Create two real stores so the route's `store in stores` check passes
    suffix = uuid.uuid4().hex[:6]
    sb = Store(name=f'B-{suffix}', login_enabled=True)
    sc = Store(name=f'C-{suffix}', login_enabled=True)
    db_session.add_all([sb, sc])
    db_session.commit()

    sa = User(username=f'sa-{suffix}', role='super_admin')
    sa.set_password('0000')
    db_session.add(sa)
    db_session.commit()

    with client.session_transaction() as sess:
        sess['_user_id'] = str(sa.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())

    try:
        client.post('/notes/api', json={'title': 'Store B', 'content': '', 'store': sb.name})
        client.post('/notes/api', json={'title': 'Store C', 'content': '', 'store': sc.name})

        res = client.get(f'/notes/api?store={sb.name}')
        notes = res.get_json()
        assert notes, 'expected at least one note from store B'
        assert all(n['store'] == sb.name for n in notes)
    finally:
        from models import Note
        Note.query.filter_by(user_id=sa.id).delete()
        db_session.delete(sa)
        db_session.delete(sb)
        db_session.delete(sc)
        db_session.commit()


def test_summarize_requires_admin(logged_in_client):
    res = logged_in_client.post('/notes/api', json={'title': 'Summary', 'content': 'body'})
    note_id = res.get_json()['id']
    res = logged_in_client.post(f'/notes/api/{note_id}/summarize')
    assert res.status_code == 403


def test_api_response_format(logged_in_client):
    """Contract C: API responses use {status: ok/error}."""
    res = logged_in_client.post('/notes/api', json={'title': 'T', 'content': ''})
    assert res.get_json()['status'] == 'ok'


# ── Keyword search preserves context across editor navigation (2026-05-11) ───


def test_index_with_keyword_highlights_matches_in_title_and_content(client, db_session):
    """When /notes/?q=foo is requested, every occurrence of `foo` in the
    rendered note title / content must be wrapped in <mark> tags so the
    UI visually highlights the match (case-insensitive)."""
    import time
    import uuid
    from models import User, Store, Note

    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f"S-{suffix}", login_enabled=True)
    db_session.add(store)
    db_session.commit()

    sa = User(username=f"sa-{suffix}", role="super_admin")
    sa.set_password("0000")
    db_session.add(sa)
    db_session.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(sa.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())

    try:
        client.post("/notes/api", json={
            "title": "找到FOO關鍵字的測試標題",
            "content": "內文也有 foo 出現兩次：foo bar baz",
            "store": store.name,
        })
        r = client.get("/notes/?q=foo")
        html = r.get_data(as_text=True)
        # Title's FOO (uppercase) wrapped — case-insensitive match
        assert "<mark" in html, "expected <mark> tag to appear when keyword present"
        # The keyword text inside should be the original-cased match (FOO / foo)
        assert ("<mark" in html) and (">FOO<" in html or ">foo<" in html), \
            "keyword text (any case) should appear inside <mark>"
    finally:
        Note.query.filter_by(user_id=sa.id).delete()
        db_session.delete(sa)
        db_session.delete(store)
        db_session.commit()


def test_index_without_keyword_does_not_inject_mark_tags(client, db_session):
    """When no keyword, output should have no <mark> tags from highlighting."""
    import time
    import uuid
    from models import User, Store, Note

    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f"S-{suffix}", login_enabled=True)
    db_session.add(store)
    db_session.commit()

    sa = User(username=f"sa-{suffix}", role="super_admin")
    sa.set_password("0000")
    db_session.add(sa)
    db_session.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(sa.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())

    try:
        client.post("/notes/api", json={
            "title": "一般筆記標題",
            "content": "普通內文",
            "store": store.name,
        })
        r = client.get("/notes/")
        html = r.get_data(as_text=True)
        assert "<mark" not in html, "no <mark> should be injected without keyword"
    finally:
        Note.query.filter_by(user_id=sa.id).delete()
        db_session.delete(sa)
        db_session.delete(store)
        db_session.commit()


def test_index_with_keyword_propagates_q_into_note_card_link(client, db_session):
    """When viewing /notes/?q=foo, each note's card link MUST include
    `q=foo` so that clicking into a note → 返回 round-trip preserves the
    search context (instead of clearing it like the pre-fix behavior)."""
    import time
    import uuid
    from models import User, Store, Note

    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f"S-{suffix}", login_enabled=True)
    db_session.add(store)
    db_session.commit()

    sa = User(username=f"sa-{suffix}", role="super_admin")
    sa.set_password("0000")
    db_session.add(sa)
    db_session.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(sa.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())

    try:
        client.post("/notes/api", json={
            "title": "搜尋關鍵字測試標題",
            "content": "body",
            "store": store.name,
        })
        # Search by keyword that matches the title
        r = client.get("/notes/?q=" + "搜尋關鍵字")
        html = r.get_data(as_text=True)
        # Card link must propagate q so /notes/<id>?...&q=搜尋關鍵字 keeps context
        assert "q=" in html, "expected q param in note card link"
        # The card-onclick attribute references edit URL; assert the keyword
        # round-trips via URL encoding.
        import urllib.parse
        encoded = urllib.parse.quote("搜尋關鍵字")
        assert (
            "q=" + encoded in html or "q=搜尋關鍵字" in html
        ), "keyword must appear in onclick navigation URL"
    finally:
        Note.query.filter_by(user_id=sa.id).delete()
        db_session.delete(sa)
        db_session.delete(store)
        db_session.commit()
