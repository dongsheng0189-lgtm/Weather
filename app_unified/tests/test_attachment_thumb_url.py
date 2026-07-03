"""attachment JSON 含 thumb_url:有 thumb_key → 簽 URL;無 → None。"""
from unittest.mock import patch


def test_checklist_item_json_includes_thumb_url(app, db_session):
    from models import Note, ChecklistItem, NoteAttachment
    from notes.routes import _checklist_item_json
    n = Note(note_type="checklist", title="t", content="", store="A",
             status="active")
    db_session.add(n); db_session.commit()
    it = ChecklistItem(note_id=n.id, order_index=0, text="x", is_checked=False)
    db_session.add(it); db_session.commit()
    a = NoteAttachment(note_id=n.id, checklist_item_id=it.id, object_key="k.jpg",
                       thumb_key="k_thumb.jpg", filename="p.jpg",
                       content_type="image/jpeg", file_size=1)
    db_session.add(a); db_session.commit()

    with app.app_context():
        with patch("storage.get_signed_url", side_effect=lambda key, **kw: f"signed::{key}"):
            j = _checklist_item_json(it)
    att = j["attachments"][0]
    assert att["thumb_url"] == "signed::k_thumb.jpg"
    assert att["url"] == "signed::k.jpg"


def test_thumb_url_none_when_no_thumb_key(app, db_session):
    from models import Note, ChecklistItem, NoteAttachment
    from notes.routes import _checklist_item_json
    n = Note(note_type="checklist", title="t", content="", store="A",
             status="active")
    db_session.add(n); db_session.commit()
    it = ChecklistItem(note_id=n.id, order_index=0, text="x", is_checked=False)
    db_session.add(it); db_session.commit()
    a = NoteAttachment(note_id=n.id, checklist_item_id=it.id, object_key="v.mp4",
                       thumb_key=None, filename="v.mp4",
                       content_type="video/mp4", file_size=1)
    db_session.add(a); db_session.commit()

    with app.app_context():
        with patch("storage.get_signed_url", side_effect=lambda key, **kw: f"signed::{key}"):
            j = _checklist_item_json(it)
    assert j["attachments"][0]["thumb_url"] is None
