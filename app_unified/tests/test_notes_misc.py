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


def _active_sort_from_html(body: str) -> str:
    """Parse rendered notes index HTML to find which sort tab is active.

    Sort tab template (templates/notes/index.html ~166):
      <a href=...sort=updated... class="store-tab {% if current_sort == val %}active{% endif %}"
         style="{% if current_sort == val %}background:#8e44ad;color:#fff;{% endif %}">照更新</a>

    The active one has both `active` class and the purple background style applied.
    We look for the substring `sort=<val>` followed (within the same <a>) by `active`
    AND `#8e44ad` before the closing </a>.
    """
    import re
    # Find all sort tab anchors
    for m in re.finditer(r'<a[^>]*sort=([a-z]+)[^>]*>[^<]*</a>', body):
        anchor = m.group(0)
        val = m.group(1)
        if val in ("updated", "created") and "active" in anchor and "#8e44ad" in anchor:
            return val
    return ""


def test_sort_param_not_persisted_in_session(app, client, user_alice):
    """Backend 不再用 session 存 sort：no `?sort=` 一律 default updated（即使先前訪問過 ?sort=created）。"""
    _login_as(client, user_alice)

    # 先帶 sort=created 訪問一次（之前會寫 session["notes_sort"]）
    r1 = client.get("/notes/?store=A&range=all&sort=created")
    assert r1.status_code == 200
    assert _active_sort_from_html(r1.data.decode("utf-8")) == "created"

    # 再訪問不帶 sort → 應該 default 回 updated（不再從 session 撈 created）
    r2 = client.get("/notes/?store=A&range=all")
    assert r2.status_code == 200
    active = _active_sort_from_html(r2.data.decode("utf-8"))
    assert active == "updated", f"Expected default 'updated' (no session memory), got: {active!r}"


def test_sort_param_explicit_updated(app, client, user_alice):
    """?sort=updated 顯式指定 → updated tab active。"""
    _login_as(client, user_alice)
    r = client.get("/notes/?store=A&range=all&sort=updated")
    assert r.status_code == 200
    assert _active_sort_from_html(r.data.decode("utf-8")) == "updated"


def test_sort_param_invalid_falls_back_to_updated(app, client, user_alice):
    """?sort=garbage → fallback default updated。"""
    _login_as(client, user_alice)
    r = client.get("/notes/?store=A&range=all&sort=garbage")
    assert r.status_code == 200
    assert _active_sort_from_html(r.data.decode("utf-8")) == "updated"
