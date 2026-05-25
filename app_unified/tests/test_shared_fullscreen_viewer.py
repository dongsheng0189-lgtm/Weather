"""Markup smoke tests for F6 shared fullscreen viewer.

驗 page render 含 shared viewer.js + viewer.css link；
驗 notes/editor.html 已移除舊 inline marker。
Runtime 行為走 manual verification（spec § 測試策略）。
"""

VIEWER_JS = '/static/js/fullscreen_viewer.js'
VIEWER_CSS = '/static/css/fullscreen_viewer.css'


def _assert_includes_viewer(body):
    assert VIEWER_JS in body, "shared viewer JS link 缺失"
    assert VIEWER_CSS in body, "shared viewer CSS link 缺失"


def test_notes_editor_includes_shared_viewer(logged_in_client):
    res = logged_in_client.get('/notes/new')
    assert res.status_code == 200
    _assert_includes_viewer(res.get_data(as_text=True))


def test_notes_index_includes_shared_viewer(logged_in_client):
    res = logged_in_client.get('/notes/')
    assert res.status_code == 200
    _assert_includes_viewer(res.get_data(as_text=True))


def test_announcement_new_includes_shared_viewer(logged_in_admin_client, admin_user, db_session):
    # admin 必須有 store 才能建立公告（announcement_new_page 405 邊界）
    admin_user.store = 'B'
    db_session.commit()
    res = logged_in_admin_client.get('/notes/announcement/new')
    assert res.status_code == 200
    _assert_includes_viewer(res.get_data(as_text=True))


def test_notes_editor_no_inline_viewer_remnant(logged_in_client):
    """notes/editor.html 應該已經移除 inline viewer JS、不應該再有 _fsState/_fsOnPointerDown 字串。"""
    res = logged_in_client.get('/notes/new')
    body = res.get_data(as_text=True)
    assert '_fsOnPointerDown' not in body, "舊 inline viewer JS 殘留 (_fsOnPointerDown)"
    assert 'var _fsState' not in body, "舊 inline viewer state 殘留 (var _fsState)"
