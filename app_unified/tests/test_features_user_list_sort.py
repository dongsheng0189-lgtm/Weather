"""模組權限管理使用者清單依店別排序。"""
import uuid
from models import User
from extensions import db


def _mk_root(db_session):
    existing = User.query.filter_by(username="admin").first()
    if existing:
        return existing
    u = User(username="admin", role="user", store="B", is_active=True)
    u.set_password("1234"); db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_users_sorted_by_store(app, client, db_session):
    sfx = uuid.uuid4().hex[:6]
    for uname, store in [(f"zoe_{sfx}", "B"), (f"amy_{sfx}", "A"), (f"bob_{sfx}", "A")]:
        u = User(username=uname, role="user", store=store, is_active=True)
        u.set_password("1234"); db_session.add(u)
    db_session.commit()
    root = _mk_root(db_session); _login(client, root)
    data = client.get("/admin/features/users").get_json()
    stores = [u["store"] for u in data if u["store"] in ("A", "B")]
    # A 店全部排在 B 店之前
    assert stores == sorted(stores)
