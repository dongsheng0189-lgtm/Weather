"""3 張 attachment 表都有 thumb_key 欄位(nullable)。"""
def test_note_attachment_has_thumb_key():
    from models import NoteAttachment
    assert hasattr(NoteAttachment, "thumb_key")


def test_private_attachment_has_thumb_key():
    from models import PrivateAttachment
    assert hasattr(PrivateAttachment, "thumb_key")


def test_announcement_attachment_has_thumb_key():
    from models import AnnouncementAttachment
    assert hasattr(AnnouncementAttachment, "thumb_key")


def test_thumb_key_nullable_roundtrip(app, db_session):
    """建一筆 thumb_key=None + 一筆有值,都存得起來。"""
    from models import NoteAttachment, Note
    n = Note(note_type="note", title="t", content="", store="A",
             status="active")
    db_session.add(n); db_session.commit()
    a1 = NoteAttachment(note_id=n.id, object_key="k1.jpg", filename="a.jpg",
                        content_type="image/jpeg", file_size=1, thumb_key=None)
    a2 = NoteAttachment(note_id=n.id, object_key="k2.jpg", filename="b.jpg",
                        content_type="image/jpeg", file_size=1,
                        thumb_key="k2_thumb.jpg")
    db_session.add_all([a1, a2]); db_session.commit()
    assert a1.thumb_key is None
    assert a2.thumb_key == "k2_thumb.jpg"
