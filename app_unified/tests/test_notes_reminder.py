"""店務筆記到期提醒 + 稽核（reminder_date / NoteLog）。

使用專案既有 fixture（conftest.py）：
  - logged_in_client：role=user 的 test_user 已透過 session bypass 登入
  - test_user：對應的 User 物件（username = testuser_xxxx）
建立筆記走 POST /notes/api、更新走 PUT /notes/api/<id>（見 notes/routes.py）。
"""
from datetime import date, timedelta

from models import Note, NoteLog


def test_set_note_reminder_writes_snapshot_and_log(logged_in_client, test_user, app):
    r = logged_in_client.post("/notes/api", json={"note_type": "note", "title": "月報"})
    assert r.status_code == 201
    nid = r.get_json()["id"]
    due = (date.today() + timedelta(days=5)).isoformat()
    r = logged_in_client.put(f"/notes/api/{nid}", json={"reminder_date": due})
    assert r.status_code == 200
    with app.app_context():
        n = Note.query.get(nid)
        assert n.reminder_date.isoformat() == due
        assert n.reminder_set_by_name == test_user.username
        assert n.reminder_set_at is not None
        logs = NoteLog.query.filter_by(note_id=nid, action="reminder_set").all()
        assert len(logs) == 1
        assert "到期日" in logs[0].diff


def test_change_note_reminder_logs_change(logged_in_client, app):
    nid = logged_in_client.post("/notes/api", json={"note_type": "note", "title": "x"}).get_json()["id"]
    d1 = (date.today() + timedelta(days=2)).isoformat()
    d2 = (date.today() + timedelta(days=4)).isoformat()
    logged_in_client.put(f"/notes/api/{nid}", json={"reminder_date": d1})
    logged_in_client.put(f"/notes/api/{nid}", json={"reminder_date": d2})
    with app.app_context():
        assert NoteLog.query.filter_by(note_id=nid, action="reminder_change").count() == 1


def test_clear_note_reminder_logs_clear(logged_in_client, app):
    nid = logged_in_client.post("/notes/api", json={"note_type": "note", "title": "x"}).get_json()["id"]
    d1 = (date.today() + timedelta(days=2)).isoformat()
    logged_in_client.put(f"/notes/api/{nid}", json={"reminder_date": d1})
    logged_in_client.put(f"/notes/api/{nid}", json={"reminder_date": None})
    with app.app_context():
        assert Note.query.get(nid).reminder_date is None
        assert NoteLog.query.filter_by(note_id=nid, action="reminder_clear").count() == 1
