"""刪除附件時，若有縮圖（thumb_key）也要一併從 R2 刪除，不留孤兒縮圖。

用 tests/_r2_fixture.py 的 install_r2_mock 讓 storage._get_client() 回傳
MagicMock，直接斷言 delete_object 對 object_key、thumb_key 各呼叫一次。
"""
import time
import uuid

import pytest

from tests._r2_fixture import install_r2_mock


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())


@pytest.fixture(autouse=True)
def r2_configured(app, monkeypatch):
    yield install_r2_mock(monkeypatch, app)


def _create_store_user_note_attachment_with_thumb(db_session, store_name):
    from models import User, Note, NoteAttachment
    suffix = uuid.uuid4().hex[:8]

    user = User(username=f"u-{suffix}", store=store_name, role="user")
    user.set_password("pw")
    db_session.add(user)
    db_session.flush()

    note = Note(
        title="test note",
        content="",
        store=store_name,
        category="misc",
        user_id=user.id,
    )
    db_session.add(note)
    db_session.flush()

    object_key = f"attachments/{note.id}/{suffix}.jpg"
    attachment = NoteAttachment(
        note_id=note.id,
        user_id=user.id,
        object_key=object_key,
        thumb_key=object_key.rsplit(".", 1)[0] + "_thumb.jpg",
        filename=f"{suffix}.jpg",
        content_type="image/jpeg",
        file_size=1234,
    )
    db_session.add(attachment)
    db_session.commit()
    return user, attachment


def test_delete_attachment_also_deletes_thumb_from_r2(app, client, db_session, r2_configured):
    """DELETE /notes/api/attachments/<id> must delete both object_key and
    thumb_key from R2, not just the original image."""
    store_name = f"ST-{uuid.uuid4().hex[:6]}"
    user, attachment = _create_store_user_note_attachment_with_thumb(db_session, store_name)
    assert attachment.thumb_key  # sanity: fixture actually set a thumb_key

    _login(client, user)
    resp = client.delete(f"/notes/api/attachments/{attachment.id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)

    deleted_keys = {
        call.kwargs.get("Key") for call in r2_configured.delete_object.call_args_list
    }
    assert attachment.object_key in deleted_keys
    assert attachment.thumb_key in deleted_keys


def test_delete_attachment_without_thumb_only_deletes_original(app, client, db_session, r2_configured):
    """No regression: attachments with thumb_key=None must not trigger a
    second delete_object call (and must not error)."""
    from models import User, Note, NoteAttachment
    suffix = uuid.uuid4().hex[:8]
    store_name = f"ST-{uuid.uuid4().hex[:6]}"

    user = User(username=f"u-{suffix}", store=store_name, role="user")
    user.set_password("pw")
    db_session.add(user)
    db_session.flush()

    note = Note(title="t", content="", store=store_name, category="misc", user_id=user.id)
    db_session.add(note)
    db_session.flush()

    attachment = NoteAttachment(
        note_id=note.id,
        user_id=user.id,
        object_key=f"attachments/{note.id}/{suffix}.mp4",
        thumb_key=None,
        filename=f"{suffix}.mp4",
        content_type="video/mp4",
        file_size=1234,
    )
    db_session.add(attachment)
    db_session.commit()

    _login(client, user)
    resp = client.delete(f"/notes/api/attachments/{attachment.id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert r2_configured.delete_object.call_count == 1
