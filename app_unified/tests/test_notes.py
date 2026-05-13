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


def test_summarize_allowed_for_user_role(logged_in_client, monkeypatch):
    """user role 也能呼叫 /summarize、不再回 403（spec 2026-05-13）。

    擴大 AI 摘要權限：去掉 is_admin() gate、登入即可用。原 test
    `test_summarize_requires_admin` 被此 test 取代（admin gate 已拆）。
    """
    # Avoid scheduling real LLM call inside test
    monkeypatch.setattr("notes.routes.gevent.spawn", lambda *a, **kw: None)

    res = logged_in_client.post('/notes/api', json={'title': 'Summary', 'content': 'body'})
    note_id = res.get_json()['id']
    res = logged_in_client.post(f'/notes/api/{note_id}/summarize')
    # Was 403 ('僅限管理員') before spec change. Now should be accepted (or busy=429).
    assert res.status_code != 403
    body = res.get_json()
    assert body.get("status") in ("accepted", "busy")


def test_ai_summary_card_visible_to_user_role(logged_in_client):
    """user role GET /notes/ → 頁面有 AI 摘要 card（template 也拆 admin gate）。

    第一階段只解 backend gate，按鈕仍被 template `{% if is_admin() %}`
    隱藏。此 test 鎖住 template gate 已同步拆掉。
    """
    res = logged_in_client.get('/notes/')
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "🤖 AI 摘要" in html
    assert 'id="btn-ai-summary"' in html


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


def test_keyword_search_excludes_ai_summary_notes(client, db_session):
    """AI 摘要筆記的 content 是其他 note 的彙整，搜尋會跟原 note 重複出現。
    關鍵字搜尋（index + JSON API）必須過濾掉 is_ai_summary=True 的 row。"""
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

    # Unique markers — avoid collision with static UI labels like "🤖 AI 摘要"
    # that always render in the page chrome regardless of search state.
    regular_marker = f"AISUMTEST_REGULAR_{suffix}"
    ai_marker = f"AISUMTEST_AISUM_{suffix}"
    try:
        client.post("/notes/api", json={
            "title": regular_marker,
            "content": "店務 代理商 K單機",
            "store": store.name,
        })
        client.post("/notes/api", json={
            "title": ai_marker,
            "content": "代理商：K單機... S監視...",
            "store": store.name,
            "is_ai_summary": True,
        })

        r = client.get("/notes/?q=" + "代理商")
        html = r.get_data(as_text=True)
        assert regular_marker in html, "regular note must appear in keyword results"
        assert ai_marker not in html, "AI summary must NOT appear in keyword results"

        r = client.get("/notes/api?q=" + "代理商")
        data = r.get_json()
        assert isinstance(data, list)
        titles = [n.get("title") for n in data]
        assert any(regular_marker in (t or "") for t in titles)
        assert not any(ai_marker in (t or "") for t in titles), \
            f"AI summary leaked into API keyword results: {titles}"
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


def _seed_admin_for_keyword_ui(client, db_session, role="super_admin"):
    """Helper: create a Store + User(role) and log in. Returns (user, store, suffix)."""
    import time
    import uuid
    from models import User, Store

    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f"S-{suffix}", login_enabled=True)
    db_session.add(store)
    db_session.commit()

    user = User(username=f"u-{suffix}", role=role, store=store.name)
    user.set_password("0000")
    db_session.add(user)
    db_session.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())
    return user, store, suffix


def test_index_with_keyword_hides_filter_tabs(client, db_session):
    """Under keyword mode the category / shift / range / status / sort blocks
    must be hidden — keyword search already returns the full matched set, so
    these limiting filters / sort overlay are off-topic and misleading."""
    from models import Note

    user, store, _ = _seed_admin_for_keyword_ui(client, db_session, role="admin")
    try:
        r = client.get("/notes/?q=foo")
        html = r.get_data(as_text=True)
        assert "種類：" not in html, "category filter must be hidden under keyword"
        assert "班別：" not in html, "shift filter must be hidden under keyword"
        assert "時間：" not in html, "range filter must be hidden under keyword"
        assert "狀態：" not in html, "status filter must be hidden under keyword"
        assert "排列：" not in html, "sort row must be hidden under keyword"
    finally:
        Note.query.filter_by(user_id=user.id).delete()
        db_session.delete(user)
        db_session.delete(store)
        db_session.commit()


def test_index_without_keyword_shows_filter_tabs(client, db_session):
    """Without keyword, the filter + sort blocks must render (regression guard)."""
    from models import Note

    user, store, _ = _seed_admin_for_keyword_ui(client, db_session, role="admin")
    try:
        r = client.get("/notes/")
        html = r.get_data(as_text=True)
        assert "種類：" in html, "category filter must render without keyword"
        assert "班別：" in html, "shift filter must render without keyword"
        assert "時間：" in html, "range filter must render without keyword"
        assert "狀態：" in html, "status filter must render without keyword"
        assert "排列：" in html, "sort row must render without keyword"
    finally:
        Note.query.filter_by(user_id=user.id).delete()
        db_session.delete(user)
        db_session.delete(store)
        db_session.commit()


def test_index_with_keyword_hides_ai_summary_card(client, db_session):
    """Admin sees the AI summary card on /notes/ index. Under keyword mode
    it must be hidden — AI summary scope ≠ keyword result scope."""
    from models import Note

    user, store, _ = _seed_admin_for_keyword_ui(client, db_session, role="admin")
    try:
        r0 = client.get("/notes/")
        html0 = r0.get_data(as_text=True)
        assert 'id="btn-ai-summary"' in html0, \
            "admin must see AI summary card without keyword (sanity)"

        r = client.get("/notes/?q=foo")
        html = r.get_data(as_text=True)
        assert 'id="btn-ai-summary"' not in html, \
            "AI summary button must be hidden under keyword search"
        assert "🤖 AI 摘要" not in html, \
            "AI summary card header must be hidden under keyword search"
    finally:
        Note.query.filter_by(user_id=user.id).delete()
        db_session.delete(user)
        db_session.delete(store)
        db_session.commit()


def test_index_keyword_preserves_store_tabs_for_super_admin(client, db_session):
    """super_admin keeps store-tabs visible under keyword mode so they can
    pivot between stores while searching."""
    from models import Note

    user, store, _ = _seed_admin_for_keyword_ui(client, db_session, role="super_admin")
    try:
        r = client.get("/notes/?q=foo")
        html = r.get_data(as_text=True)
        assert 'class="store-tabs"' in html, \
            "store-tabs must remain visible under keyword for super_admin"
    finally:
        Note.query.filter_by(user_id=user.id).delete()
        db_session.delete(user)
        db_session.delete(store)
        db_session.commit()


# ── Checklist editor: completion partition tab row (2026-05-12) ─────────────


def test_checklist_editor_renders_completion_tab_row(client, db_session):
    """Opening a checklist note's editor must render the 排列 tab row
    (全部/已完成/未完成) so users can re-partition items client-side."""
    import time
    import uuid
    from models import User, Store, Note

    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f"S-{suffix}", login_enabled=True)
    db_session.add(store)
    db_session.commit()

    user = User(username=f"u-{suffix}", role="admin", store=store.name)
    user.set_password("0000")
    db_session.add(user)
    db_session.commit()

    note = Note(title="check", note_type="checklist", store=store.name,
                user_id=user.id, status="pending", category="misc")
    db_session.add(note)
    db_session.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())

    try:
        r = client.get(f"/notes/{note.id}")
        html = r.get_data(as_text=True)
        assert 'data-completion-tab="all"' in html, \
            "checklist editor must render 全部 tab"
        assert 'data-completion-tab="done"' in html, \
            "checklist editor must render 已完成 tab"
        assert 'data-completion-tab="undone"' in html, \
            "checklist editor must render 未完成 tab"
        assert "setCompletionMode" in html, \
            "tab click handler setCompletionMode must be present"
    finally:
        Note.query.filter_by(user_id=user.id).delete()
        db_session.delete(user)
        db_session.delete(store)
        db_session.commit()


def test_regular_note_editor_does_not_render_completion_tab_row(client, db_session):
    """Non-checklist note editor must NOT render the completion tab buttons
    — they live inside the checklist {% else %} branch only."""
    import time
    import uuid
    from models import User, Store, Note

    suffix = uuid.uuid4().hex[:6]
    store = Store(name=f"S-{suffix}", login_enabled=True)
    db_session.add(store)
    db_session.commit()

    user = User(username=f"u-{suffix}", role="admin", store=store.name)
    user.set_password("0000")
    db_session.add(user)
    db_session.commit()

    note = Note(title="reg", note_type="note", store=store.name,
                user_id=user.id, status="pending", category="misc")
    db_session.add(note)
    db_session.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())

    try:
        r = client.get(f"/notes/{note.id}")
        html = r.get_data(as_text=True)
        # `id="completion-tabs"` is the container div — only emitted in checklist branch.
        # Plain selector-string `data-completion-tab` lives in JS even for non-checklist
        # so we anchor on the wrapper div instead.
        assert 'id="completion-tabs"' not in html, \
            "regular note editor must NOT render completion tab wrapper"
    finally:
        Note.query.filter_by(user_id=user.id).delete()
        db_session.delete(user)
        db_session.delete(store)
        db_session.commit()
