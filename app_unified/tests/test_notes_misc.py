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
