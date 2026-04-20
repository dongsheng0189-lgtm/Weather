"""Tests for notes.diff_utils paragraph diff logic."""
import pytest


def test_no_change():
    from notes.diff_utils import compute_paragraph_diff
    old = "第一行\n第二行\n第三行"
    new = "第一行\n第二行\n第三行"
    result = compute_paragraph_diff(old, new)
    assert result is None  # no change → None


def test_one_paragraph_modified():
    from notes.diff_utils import compute_paragraph_diff
    old = "今天採買水果20斤\n下週排班表已更新"
    new = "今天採買水果30斤\n下週排班表已更新"
    result = compute_paragraph_diff(old, new)
    assert result == "今天採買水果30斤"


def test_multiple_paragraphs_modified():
    from notes.diff_utils import compute_paragraph_diff
    old = "第一行\n第二行\n第三行"
    new = "第一行改\n第二行\n第三行改"
    result = compute_paragraph_diff(old, new)
    assert "第一行改" in result
    assert "第三行改" in result
    assert "第二行" not in result


def test_paragraph_added():
    from notes.diff_utils import compute_paragraph_diff
    old = "第一行\n第二行"
    new = "第一行\n第二行\n新增第三行"
    result = compute_paragraph_diff(old, new)
    assert result == "新增第三行"


def test_paragraph_deleted():
    from notes.diff_utils import compute_paragraph_diff
    old = "第一行\n要刪掉的\n第三行"
    new = "第一行\n第三行"
    result = compute_paragraph_diff(old, new)
    # deletion → return None (no new content to summarize)
    assert result is None


def test_old_empty_returns_none():
    """First save (old is empty) → None so caller uses full content."""
    from notes.diff_utils import compute_paragraph_diff
    result = compute_paragraph_diff("", "全新的內容\n第二段")
    assert result is None


def test_old_none_returns_none():
    from notes.diff_utils import compute_paragraph_diff
    result = compute_paragraph_diff(None, "全新的內容")
    assert result is None


def test_separator_in_output():
    """Multiple changed paragraphs are joined by the separator."""
    from notes.diff_utils import compute_paragraph_diff
    old = "A\nB\nC"
    new = "A改\nB\nC改"
    result = compute_paragraph_diff(old, new)
    parts = result.split("\n---\n")
    assert len(parts) == 2
    assert parts[0] == "A改"
    assert parts[1] == "C改"


def _login_admin(client, db_session):
    """Create admin + login via session_transaction."""
    from models import User
    import uuid
    u = User(username=f"adm_{uuid.uuid4().hex[:6]}", role="admin", store="A")
    u.set_password("0000")
    db_session.add(u)
    db_session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(u.id)
    return u


def _mk_note(db_session, user, content="第一行\n第二行\n第三行"):
    from models import Note
    n = Note(
        user_id=user.id,
        author_name=user.username,
        note_type="note",
        title="test",
        content=content,
        store=user.store,
        status="active",
        category="unclassified",
    )
    db_session.add(n)
    db_session.commit()
    return n


def test_save_stores_paragraph_diff(app, client, db_session):
    """Editing a note via REST stores paragraph diff in NoteLog.diff."""
    from models import NoteLog
    from notes.diff_utils import PARAGRAPH_DIFF_PREFIX
    user = _login_admin(client, db_session)
    note = _mk_note(db_session, user, content="今天採買水果20斤\n排班表已更新")

    resp = client.put(f"/notes/api/{note.id}", json={
        "content": "今天採買水果30斤\n排班表已更新"
    })
    assert resp.status_code == 200

    log = NoteLog.query.filter_by(note_id=note.id, action="edit").first()
    assert log is not None
    assert log.diff.startswith(PARAGRAPH_DIFF_PREFIX)
    assert "今天採買水果30斤" in log.diff


def test_format_note_uses_diff(app, client, db_session):
    """When since is given and NoteLog has paragraph diff, _format_note_for_ai returns diff only."""
    from models import NoteLog
    from notes.diff_utils import PARAGRAPH_DIFF_PREFIX
    from datetime import datetime, timedelta

    user = _login_admin(client, db_session)
    note = _mk_note(db_session, user, content="原始第一行\n原始第二行\n原始第三行")

    # Simulate an edit that stored paragraph diff
    log = NoteLog(
        note_id=note.id,
        note_title=note.title,
        user_id=user.id,
        action="edit",
        diff=f"{PARAGRAPH_DIFF_PREFIX}修改後第二行",
    )
    db_session.add(log)
    db_session.commit()

    since = datetime.utcnow() - timedelta(hours=1)
    from notes.routes import _format_note_for_ai
    result = _format_note_for_ai(note, since=since)

    assert "修改後第二行" in result
    assert "原始第一行" not in result
    assert "原始第三行" not in result


def test_format_note_fallback_no_diff(app, client, db_session):
    """When no paragraph diff exists, _format_note_for_ai returns full content."""
    from datetime import datetime, timedelta

    user = _login_admin(client, db_session)
    note = _mk_note(db_session, user, content="第一行\n第二行")

    since = datetime.utcnow() - timedelta(hours=1)
    from notes.routes import _format_note_for_ai
    result = _format_note_for_ai(note, since=since)

    assert "第一行" in result
    assert "第二行" in result


def test_format_note_no_since_returns_full(app, db_session):
    """Without since parameter, always return full content."""
    from models import User
    import uuid
    user = User(username=f"adm_{uuid.uuid4().hex[:6]}", role="admin", store="A")
    user.set_password("0000")
    db_session.add(user)
    db_session.commit()

    note = _mk_note(db_session, user, content="全部內容\n第二行")

    from notes.routes import _format_note_for_ai
    result = _format_note_for_ai(note)

    assert "全部內容" in result
    assert "第二行" in result
