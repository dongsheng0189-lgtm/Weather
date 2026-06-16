"""店務筆記到期提醒 + 稽核（reminder_date / NoteLog）。

使用專案既有 fixture（conftest.py）：
  - logged_in_client：role=user 的 test_user 已透過 session bypass 登入
  - test_user：對應的 User 物件（username = testuser_xxxx）
建立筆記走 POST /notes/api、更新走 PUT /notes/api/<id>（見 notes/routes.py）。
"""
import time
import uuid

from datetime import date, timedelta

from models import Note, NoteLog


def _login(client, app, username="u1", store="A店", role="admin"):
    """建立（或取得）指定店別的使用者並以 session bypass 登入。

    與 conftest._login_via_session 同套作法；額外確保 Store 存在，
    這樣 admin 建立的筆記 store 會落在該店、reminders/today 才篩得到。
    """
    from extensions import db
    from models import User, Store
    with app.app_context():
        if store and not Store.query.filter_by(name=store).first():
            db.session.add(Store(name=store))
            db.session.commit()
        uname = f"{username}_{uuid.uuid4().hex[:6]}"
        u = User(username=uname, role=role, store=store)
        u.set_password("1234")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())
    return uid


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


def test_set_item_reminder_logs_with_item_text(logged_in_client, test_user, app):
    nid = logged_in_client.post(
        "/notes/api", json={"note_type": "checklist", "title": "進貨", "store": "A店"}
    ).get_json()["id"]
    iid = logged_in_client.post(
        f"/notes/api/{nid}/items", json={"text": "點收"}
    ).get_json()["item"]["id"]
    due = (date.today() + timedelta(days=3)).isoformat()
    r = logged_in_client.patch(f"/notes/api/items/{iid}", json={"reminder_date": due})
    assert r.status_code == 200
    assert r.get_json()["item"]["reminder_date"] == due
    with app.app_context():
        from models import ChecklistItem
        it = ChecklistItem.query.get(iid)
        assert it.reminder_set_by_name == test_user.username
        # 過濾出本項目層級的 reminder_set log（diff 帶「項目「...」」前綴），
        # 避免與其他測試殘留、note_id 被 SQLite rowid 重用而碰撞的 note-level log 混淆。
        logs = [
            l for l in NoteLog.query.filter_by(note_id=nid, action="reminder_set").all()
            if "項目「" in (l.diff or "")
        ]
        assert len(logs) == 1
        assert "點收" in logs[0].diff


def test_reminders_today_scopes_and_excludes(client, app):
    _login(client, app, username="u1", store="A店")
    today = date.today().isoformat()
    past = (date.today() - timedelta(days=1)).isoformat()
    n1 = client.post("/notes/api", json={"note_type": "note", "title": "今天到期", "store": "A店"}).get_json()["id"]
    client.put(f"/notes/api/{n1}", json={"reminder_date": today})
    n2 = client.post("/notes/api", json={"note_type": "note", "title": "已完成", "store": "A店"}).get_json()["id"]
    client.put(f"/notes/api/{n2}", json={"reminder_date": past})
    client.put(f"/notes/api/{n2}", json={"status": "resolved"})
    r = client.get("/notes/api/reminders/today")
    assert r.status_code == 200
    body = r.get_json()
    ids = [(x["kind"], x["id"]) for x in body["reminders"]]
    assert ("note", n1) in ids
    assert ("note", n2) not in ids
    assert body["last_sig"]


def test_reminders_today_excludes_other_store(client, app):
    # 先以 B店 使用者建立並設定一筆今日到期的 B店 提醒（走 API，store 被強制落在 B店）。
    _login(client, app, username="ub", store="B店")
    today = date.today().isoformat()
    nb = client.post("/notes/api", json={"note_type": "note", "title": "B店到期", "store": "B店"}).get_json()["id"]
    client.put(f"/notes/api/{nb}", json={"reminder_date": today})

    # 再切換回 A店 使用者，建立一筆同樣今日到期的 A店 提醒。
    _login(client, app, username="ua", store="A店")
    na = client.post("/notes/api", json={"note_type": "note", "title": "A店到期", "store": "A店"}).get_json()["id"]
    client.put(f"/notes/api/{na}", json={"reminder_date": today})

    r = client.get("/notes/api/reminders/today")
    assert r.status_code == 200
    ids = [(x["kind"], x["id"]) for x in r.get_json()["reminders"]]
    # A店 使用者看得到自己店的提醒，看不到 B店 的提醒（store filter 把關，不洩漏跨店資料）。
    assert ("note", na) in ids
    assert ("note", nb) not in ids


def test_reminders_today_excludes_checked_item(client, app):
    _login(client, app, username="u1", store="A店")
    past = (date.today() - timedelta(days=1)).isoformat()
    nid = client.post("/notes/api", json={"note_type": "checklist", "title": "c", "store": "A店"}).get_json()["id"]
    iid = client.post(f"/notes/api/{nid}/items", json={"text": "做完的事"}).get_json()["item"]["id"]
    client.patch(f"/notes/api/items/{iid}", json={"reminder_date": past})
    client.patch(f"/notes/api/items/{iid}", json={"is_checked": True})
    r = client.get("/notes/api/reminders/today")
    ids = [(x["kind"], x["id"]) for x in r.get_json()["reminders"]]
    assert ("item", iid) not in ids
