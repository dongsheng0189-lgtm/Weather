"""Tests for misc notes UX changes (Task 5: checklist sort, Task 6: range=all)."""
from datetime import datetime, timedelta
import uuid
import pytest

from extensions import db
from models import Note, ChecklistItem, Store, User


@pytest.fixture
def user_alice(app, db_session):
    """Create a fresh admin alice for each test (uuid-suffixed username)."""
    if not Store.query.filter_by(name="A").first():
        db.session.add(Store(name="A"))
        db.session.commit()
    suffix = uuid.uuid4().hex[:8]
    u = User(username=f"alice-{suffix}", store="A", role="admin")
    u.set_password("x")
    db.session.add(u)
    db.session.commit()
    return u


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def test_checklist_sort_by_created_keeps_order_after_update(app, client, user_alice):
    """sort=created → 勾選 / 編輯不應改變 list 順序（按 created_at DESC）。"""
    n = Note(title="t", content="", store="A", category="misc")
    db.session.add(n); db.session.commit()
    nid = n.id
    base = datetime.utcnow() - timedelta(hours=1)
    item_ids = {}
    for i, t in enumerate(["a", "b", "c"]):
        it = ChecklistItem(note_id=nid, order_index=i, text=t, is_checked=False,
                           created_at=base + timedelta(minutes=i),
                           updated_at=base + timedelta(minutes=i))
        db.session.add(it)
        db.session.flush()
        item_ids[t] = it.id
    db.session.commit()
    # 故意把 a 的 updated_at 設成最新（模擬被勾選）— text 是加密欄位，用 id 抓
    a = db.session.get(ChecklistItem, item_ids["a"])
    a.updated_at = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()

    _login_as(client, user_alice)

    # sort=created → 順序維持 c, b, a（建立時間 DESC）
    r = client.get(f"/notes/api/{nid}/items?sort=created")
    assert r.status_code == 200, r.get_data(as_text=True)
    texts = [i["text"] for i in r.json["items"]]
    assert texts == ["c", "b", "a"], f"Expected created order, got: {texts}"

    # sort=updated（default）→ a 跑到最前（updated_at DESC）
    r2 = client.get(f"/notes/api/{nid}/items?sort=updated")
    assert r2.status_code == 200
    texts2 = [i["text"] for i in r2.json["items"]]
    assert texts2 == ["a", "c", "b"], f"Expected updated order, got: {texts2}"


def test_range_all_no_date_filter(app, client, user_alice):
    """range=all 應撈所有筆記，不限日期；range=7d 應該過濾。"""
    # 60 天前 + 今天 各一筆
    old_n = Note(title="old-note-xyz", content="", store="A", category="misc",
                 updated_at=datetime.utcnow() - timedelta(days=60))
    new_n = Note(title="new-note-xyz", content="", store="A", category="misc",
                 updated_at=datetime.utcnow())
    db.session.add_all([old_n, new_n])
    db.session.commit()

    _login_as(client, user_alice)

    # range=7d 應該只看到 new-note
    r1 = client.get("/notes/?store=A&range=7d")
    assert r1.status_code == 200
    body1 = r1.data.decode("utf-8")
    assert "new-note-xyz" in body1
    assert "old-note-xyz" not in body1

    # range=all 應該兩筆都看到（不限日期）
    r2 = client.get("/notes/?store=A&range=all")
    assert r2.status_code == 200
    body2 = r2.data.decode("utf-8")
    assert "new-note-xyz" in body2
    assert "old-note-xyz" in body2
