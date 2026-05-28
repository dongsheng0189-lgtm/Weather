"""Guard: attachment lists are returned in id asc (upload order).

Caller build items[] for fullscreen swipe gallery depends on this ordering.
If anyone adds a default order_by elsewhere that breaks id-asc, this test
will catch the regression.

Covers 3 models:
  - NoteAttachment       (Note.attachments)
  - AnnouncementAttachment (Announcement.attachments)
  - PrivateAttachment    (PrivateNote.attachments)
"""
import uuid

from models import (
    db,
    User,
    Note,
    NoteAttachment,
    Announcement,
    AnnouncementAttachment,
    PrivateNote,
    PrivateAttachment,
)


def _new_user(db_session, store_name="ST-test"):
    """Create a fresh User in the given store. store is a Text field on User."""
    suffix = uuid.uuid4().hex[:8]
    user = User(username=f"u-{suffix}", store=store_name)
    user.set_password("pw")
    db_session.add(user)
    db_session.flush()
    return user


# ── Note ──────────────────────────────────────────────────────────────────────

def test_note_attachment_order_is_id_asc(app, db_session):
    user = _new_user(db_session)
    note = Note(
        title="t",
        content="c",
        store="ST-test",
        category="misc",
        user_id=user.id,
    )
    db_session.add(note)
    db_session.flush()

    a = NoteAttachment(
        note_id=note.id, user_id=user.id,
        object_key="k1", filename="a.png",
        content_type="image/png", file_size=1,
    )
    b = NoteAttachment(
        note_id=note.id, user_id=user.id,
        object_key="k2", filename="b.mp4",
        content_type="video/mp4", file_size=2,
    )
    c = NoteAttachment(
        note_id=note.id, user_id=user.id,
        object_key="k3", filename="c.png",
        content_type="image/png", file_size=3,
    )
    db_session.add_all([a, b, c])
    db_session.commit()
    expected = [a.id, b.id, c.id]

    db_session.expire(note)
    ids = [att.id for att in note.attachments]
    assert ids == expected, f"NoteAttachment order broken: expected {expected}, got {ids}"


# ── Announcement ──────────────────────────────────────────────────────────────

def test_announcement_attachment_order_is_id_asc(app, db_session):
    user = _new_user(db_session)
    ann = Announcement(
        title="t",
        content="c",
        user_id=user.id,
        author_name="snapshot",
    )
    db_session.add(ann)
    db_session.flush()

    a = AnnouncementAttachment(
        announcement_id=ann.id, user_id=user.id,
        object_key="k1", filename="a.png",
        content_type="image/png", file_size=1,
    )
    b = AnnouncementAttachment(
        announcement_id=ann.id, user_id=user.id,
        object_key="k2", filename="b.mp4",
        content_type="video/mp4", file_size=2,
    )
    db_session.add_all([a, b])
    db_session.commit()
    expected = [a.id, b.id]

    db_session.expire(ann)
    ids = [att.id for att in ann.attachments]
    assert ids == expected, f"AnnouncementAttachment order broken: expected {expected}, got {ids}"


# ── PrivateNote ───────────────────────────────────────────────────────────────

def test_private_attachment_order_is_id_asc(app, db_session):
    user = _new_user(db_session)
    pn = PrivateNote(user_id=user.id, content="c", note_type="todo_note")
    db_session.add(pn)
    db_session.flush()

    a = PrivateAttachment(
        note_id=pn.id, user_id=user.id,
        object_key="k1", filename="a.png",
        content_type="image/png", file_size=1,
    )
    b = PrivateAttachment(
        note_id=pn.id, user_id=user.id,
        object_key="k2", filename="b.png",
        content_type="image/png", file_size=2,
    )
    db_session.add_all([a, b])
    db_session.commit()
    expected = [a.id, b.id]

    db_session.expire(pn)
    ids = [att.id for att in pn.attachments]
    assert ids == expected, f"PrivateAttachment order broken: expected {expected}, got {ids}"
