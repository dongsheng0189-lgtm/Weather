"""Admin device list: reauth_candidates hint for pending devices.

When admin opens /admin/devices, each pending (is_approved=False, is_revoked=False)
device row must carry a `reauth_candidates` field listing same-fingerprint
already-approved sibling devices' user info — so admin can tell at a glance
whether this is a 全新裝置 (empty list) or 需重綁定 (non-empty).

Approved / revoked rows: field must be empty list.

Permission boundary: non-super_admin only sees same-store candidates.
"""
import uuid
import pytest
from models import User, TrustedDevice, Store
from extensions import db


def _mk_user(db_session, username=None, role="user", store=None, active=True):
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:6]}",
        role=role,
        store=store,
        is_active=active,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _mk_device(db_session, fingerprint, user=None, is_approved=True, is_revoked=False, device_name="iPhone / Safari"):
    d = TrustedDevice(
        fingerprint=fingerprint,
        device_name=device_name,
        user_id=user.id if user else None,
        is_approved=is_approved,
        is_revoked=is_revoked,
        client_uid=uuid.uuid4().hex,
    )
    db_session.add(d)
    db_session.commit()
    return d


def _mk_store(db_session, name=None):
    s = Store(name=name or f"S_{uuid.uuid4().hex[:4]}", login_enabled=True)
    db_session.add(s)
    db_session.commit()
    return s


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _find_device(devices, device_id):
    for d in devices:
        if d["id"] == device_id:
            return d
    return None


def test_pending_device_with_no_sibling_is_brand_new(app, client, db_session):
    """單獨 fingerprint 的 pending → reauth_candidates 為空 = 全新裝置。"""
    store = _mk_store(db_session)
    admin = _mk_user(db_session, role="super_admin", store=store.name)
    fp_unique = "fp_unique_" + uuid.uuid4().hex
    pending = _mk_device(db_session, fp_unique, user=None, is_approved=False)
    _login(client, admin)

    r = client.get("/admin/devices")
    assert r.status_code == 200
    devices = r.get_json()
    row = _find_device(devices, pending.id)
    assert row is not None, "pending device must appear in list"
    assert row["reauth_candidates"] == [], (
        "pending with unique fp must have empty reauth_candidates (= 全新裝置)"
    )


def test_pending_device_with_approved_sibling_lists_candidate(app, client, db_session):
    """同 fp 有 approved 員工 → reauth_candidates 列出那位員工 = 需重綁定。"""
    store = _mk_store(db_session)
    admin = _mk_user(db_session, role="super_admin", store=store.name)
    alice = _mk_user(db_session, username=f"alice_sib_{uuid.uuid4().hex[:6]}", store=store.name)
    fp_shared = "fp_shared_" + uuid.uuid4().hex
    _mk_device(db_session, fp_shared, user=alice, is_approved=True)
    pending = _mk_device(db_session, fp_shared, user=None, is_approved=False)
    _login(client, admin)

    r = client.get("/admin/devices")
    devices = r.get_json()
    row = _find_device(devices, pending.id)
    cands = row["reauth_candidates"]
    assert len(cands) == 1
    assert cands[0]["username"] == alice.username
    assert cands[0]["user_id"] == alice.id


def test_pending_device_multiple_approved_siblings_all_listed(app, client, db_session):
    """fp 共用很多員工（你 SQL 看到的真實情境）→ candidates 列全部。"""
    store = _mk_store(db_session)
    admin = _mk_user(db_session, role="super_admin", store=store.name)
    fp_shared = "fp_shared_many_" + uuid.uuid4().hex
    suffix = uuid.uuid4().hex[:6]
    employees = [_mk_user(db_session, username=f"emp{i}_{suffix}", store=store.name) for i in range(3)]
    for e in employees:
        _mk_device(db_session, fp_shared, user=e, is_approved=True)
    pending = _mk_device(db_session, fp_shared, user=None, is_approved=False)
    _login(client, admin)

    r = client.get("/admin/devices")
    devices = r.get_json()
    row = _find_device(devices, pending.id)
    cands = row["reauth_candidates"]
    assert len(cands) == 3
    assert {c["username"] for c in cands} == {e.username for e in employees}


def test_revoked_sibling_excluded_from_candidates(app, client, db_session):
    """已掛失 sibling 不該出現在重綁定候選清單。"""
    store = _mk_store(db_session)
    admin = _mk_user(db_session, role="super_admin", store=store.name)
    alice = _mk_user(db_session, username=f"alice_rev_{uuid.uuid4().hex[:6]}", store=store.name)
    bob = _mk_user(db_session, username=f"bob_rev_{uuid.uuid4().hex[:6]}", store=store.name)
    fp_shared = "fp_revoked_test_" + uuid.uuid4().hex
    _mk_device(db_session, fp_shared, user=alice, is_approved=True, is_revoked=True)  # 掛失
    _mk_device(db_session, fp_shared, user=bob, is_approved=True, is_revoked=False)
    pending = _mk_device(db_session, fp_shared, user=None, is_approved=False)
    _login(client, admin)

    r = client.get("/admin/devices")
    devices = r.get_json()
    row = _find_device(devices, pending.id)
    cands = row["reauth_candidates"]
    assert len(cands) == 1, "revoked sibling must be excluded"
    assert cands[0]["username"] == bob.username


def test_non_super_admin_sees_only_same_store_candidates(app, client, db_session):
    """admin 只能看到本店員工候選；別店的員工同 fp 也不該列出來。"""
    store_a = _mk_store(db_session, name=f"A_{uuid.uuid4().hex[:4]}")
    store_b = _mk_store(db_session, name=f"B_{uuid.uuid4().hex[:4]}")
    admin = _mk_user(db_session, role="admin", store=store_a.name)
    alice = _mk_user(db_session, username="alice_a", store=store_a.name)
    bob = _mk_user(db_session, username="bob_b", store=store_b.name)
    fp_shared = "fp_xstore_" + uuid.uuid4().hex
    _mk_device(db_session, fp_shared, user=alice, is_approved=True)
    _mk_device(db_session, fp_shared, user=bob, is_approved=True)
    pending = _mk_device(db_session, fp_shared, user=None, is_approved=False)
    _login(client, admin)

    r = client.get("/admin/devices")
    devices = r.get_json()
    row = _find_device(devices, pending.id)
    cands = row["reauth_candidates"]
    assert len(cands) == 1, "admin must not see cross-store candidates"
    assert cands[0]["username"] == "alice_a"


def test_super_admin_sees_all_store_candidates(app, client, db_session):
    """super_admin 跨店看得到所有候選。"""
    store_a = _mk_store(db_session, name=f"A_{uuid.uuid4().hex[:4]}")
    store_b = _mk_store(db_session, name=f"B_{uuid.uuid4().hex[:4]}")
    sa = _mk_user(db_session, role="super_admin", store=store_a.name)
    alice = _mk_user(db_session, username="alice_super_a", store=store_a.name)
    bob = _mk_user(db_session, username="bob_super_b", store=store_b.name)
    fp_shared = "fp_super_" + uuid.uuid4().hex
    _mk_device(db_session, fp_shared, user=alice, is_approved=True)
    _mk_device(db_session, fp_shared, user=bob, is_approved=True)
    pending = _mk_device(db_session, fp_shared, user=None, is_approved=False)
    _login(client, sa)

    r = client.get("/admin/devices")
    devices = r.get_json()
    row = _find_device(devices, pending.id)
    cands = row["reauth_candidates"]
    assert len(cands) == 2, "super_admin must see cross-store candidates"
    assert {c["username"] for c in cands} == {"alice_super_a", "bob_super_b"}


def test_approved_device_has_empty_candidates(app, client, db_session):
    """已核准 row 的 reauth_candidates 一律空（前端不顯示提示）。"""
    store = _mk_store(db_session)
    admin = _mk_user(db_session, role="super_admin", store=store.name)
    alice = _mk_user(db_session, username="alice_approved", store=store.name)
    fp = "fp_approved_test_" + uuid.uuid4().hex
    approved = _mk_device(db_session, fp, user=alice, is_approved=True)
    _login(client, admin)

    r = client.get("/admin/devices")
    devices = r.get_json()
    row = _find_device(devices, approved.id)
    assert row["reauth_candidates"] == [], "approved row must have empty candidates"


def test_candidate_payload_shape(app, client, db_session):
    """單筆 candidate dict 須帶 user_id, username, store, last_seen 四欄。"""
    store = _mk_store(db_session)
    admin = _mk_user(db_session, role="super_admin", store=store.name)
    alice = _mk_user(db_session, username="alice_shape", store=store.name)
    fp = "fp_shape_" + uuid.uuid4().hex
    _mk_device(db_session, fp, user=alice, is_approved=True)
    pending = _mk_device(db_session, fp, user=None, is_approved=False)
    _login(client, admin)

    r = client.get("/admin/devices")
    devices = r.get_json()
    row = _find_device(devices, pending.id)
    cand = row["reauth_candidates"][0]
    assert set(cand.keys()) >= {"user_id", "username", "store", "last_seen"}
    assert cand["user_id"] == alice.id
    assert cand["username"] == "alice_shape"
    assert cand["store"] == store.name
    # last_seen format: "YYYY/MM/DD HH:MM" or empty string for never-seen
    assert isinstance(cand["last_seen"], str)
