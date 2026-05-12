"""
Seed local SQLite dev DB with fake users / stores / notes / pending devices.

Run via: scripts/run_local.sh (which sources .env.local first), or manually:
  source .env.local  # MUST set DATABASE_URL to a local SQLite path
  python scripts/seed_local.py

Idempotent: drops and re-creates all rows on every run.
"""
import os
import sys
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Sanity guard — never seed against a remote/prod DB.
db_url = os.environ.get("DATABASE_URL", "")
if not db_url.startswith("sqlite:"):
    print(f"REFUSE: DATABASE_URL is {db_url!r}, not sqlite:// — refusing to seed.")
    print("Set DATABASE_URL=sqlite:///./dev.db (or source .env.local) and rerun.")
    sys.exit(1)

# DB_ENCRYPTION_KEY must be set before models import.
if not os.environ.get("DB_ENCRYPTION_KEY"):
    print("REFUSE: DB_ENCRYPTION_KEY not set. Source .env.local first.")
    sys.exit(1)


from app import create_app
from extensions import db
from models import (
    Store,
    User,
    Note,
    ChecklistItem,
    TrustedDevice,
)


STORES = ["北店", "南店"]


def _make_user(username, role, store, pin="1234"):
    u = User(username=username, role=role, store=store)
    u.set_password(pin)
    # No face_encoding — verify() route would reject these, but /dev/login skips that path
    u.set_features(["notes", "mynotes", "vetting"])
    return u


def _make_note(author, title, content, **kw):
    n = Note(
        user_id=author.id,
        author_name=author.username,
        title=title,
        content=content,
        store=author.store,
        **kw,
    )
    return n


def seed():
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        # ── Stores ───────────────────────────────────────────
        store_objs = {}
        for name in STORES:
            s = Store(name=name, login_enabled=True)
            db.session.add(s)
            store_objs[name] = s
        db.session.commit()

        # ── Users ────────────────────────────────────────────
        users = []
        users.append(_make_user("super_admin", "super_admin", None))
        users.append(_make_user("admin_north", "admin", "北店"))
        users.append(_make_user("admin_south", "admin", "南店"))
        users.append(_make_user("alice", "user", "北店"))
        users.append(_make_user("bob", "user", "北店"))
        users.append(_make_user("carol", "user", "南店"))
        users.append(_make_user("dave", "user", "南店"))
        # seed root admin (bypasses module restriction in some flows)
        users.append(_make_user("admin", "admin", "北店"))
        db.session.add_all(users)
        db.session.commit()

        by_name = {u.username: u for u in users}

        # ── Notes ────────────────────────────────────────────
        rng = random.Random(42)
        categories = ["employee", "customer", "machine", "misc"]
        shifts = ["A", "B", "N", None]
        statuses_with_weight = [("pending", 3), ("resolved", 2)]
        status_pool = [s for s, w in statuses_with_weight for _ in range(w)]

        sample_titles_by_cat = {
            "employee": ["小王請假", "新人到職", "排班調整", "員工教育訓練"],
            "customer": ["客訴桌號 3", "VIP 預約", "活動贈品庫存", "回頭客優惠"],
            "machine": ["咖啡機異音", "POS 卡單", "冷氣不冷", "Wi-Fi 不穩"],
            "misc": ["供應商來電", "清潔用品補貨", "公告張貼", "工讀生交接"],
        }

        notes_to_add = []
        for store in STORES:
            authors = [by_name["admin_north" if store == "北店" else "admin_south"]] + \
                      [u for u in users if u.role == "user" and u.store == store]
            for i in range(8):
                cat = rng.choice(categories)
                title = rng.choice(sample_titles_by_cat[cat]) + f" #{i+1}"
                content = f"{store} 的測試筆記內容，類別={cat}，序號 {i+1}。\n第二行內容做為摘要素材。"
                author = rng.choice(authors)
                note = _make_note(
                    author=author,
                    title=title,
                    content=content,
                    note_type=rng.choice(["note", "note", "checklist"]),
                    category=cat,
                    shift=rng.choice(shifts),
                    status=rng.choice(status_pool),
                )
                notes_to_add.append(note)

        db.session.add_all(notes_to_add)
        db.session.commit()

        # Add a couple checklist items to checklist-type notes
        checklist_notes = [n for n in notes_to_add if n.note_type == "checklist"][:4]
        for n in checklist_notes:
            for j, label in enumerate(["檢查 A", "檢查 B", "檢查 C"]):
                ci = ChecklistItem(
                    note_id=n.id,
                    text=label,
                    is_checked=(j == 0),
                    order_index=j,
                )
                db.session.add(ci)
        db.session.commit()

        # ── Trusted devices ──────────────────────────────────
        # For each user — one approved device (so they look "normal").
        for u in users:
            if u.username in ("super_admin", "admin"):
                continue
            db.session.add(TrustedDevice(
                user_id=u.id,
                fingerprint=f"fp-known-{u.username}",
                client_uid=f"uid-{u.username}",
                device_name=f"{u.username} 的 iPhone",
                is_approved=True,
                is_revoked=False,
            ))

        # Two pending devices per store — for testing 全新裝置 vs 需重綁定 hint
        # PendingA: brand-new fingerprint (no candidates → 🆕 全新裝置)
        db.session.add(TrustedDevice(
            user_id=None,
            fingerprint="fp-brandnew-north-001",
            client_uid="uid-pending-north-1",
            device_name="未知裝置 N1",
            is_approved=False,
            is_revoked=False,
        ))
        # PendingB: fingerprint shared with an existing user (alice 北店) → 需重綁定 1 人候選
        db.session.add(TrustedDevice(
            user_id=None,
            fingerprint="fp-known-alice",
            client_uid="uid-pending-north-2",
            device_name="未知裝置 N2",
            is_approved=False,
            is_revoked=False,
        ))
        # South store: brand new
        db.session.add(TrustedDevice(
            user_id=None,
            fingerprint="fp-brandnew-south-001",
            client_uid="uid-pending-south-1",
            device_name="未知裝置 S1",
            is_approved=False,
            is_revoked=False,
        ))
        # South store: fp shared by carol AND dave → 2 人候選
        for uname in ("carol", "dave"):
            u = by_name[uname]
            # Update existing devices to share the same fp so pending can find both
            extra = TrustedDevice(
                user_id=u.id,
                fingerprint="fp-shared-south",
                client_uid=f"uid-shared-{uname}",
                device_name=f"{uname} 的共用 iPhone",
                is_approved=True,
                is_revoked=False,
            )
            db.session.add(extra)
        db.session.add(TrustedDevice(
            user_id=None,
            fingerprint="fp-shared-south",
            client_uid="uid-pending-south-2",
            device_name="未知裝置 S2",
            is_approved=False,
            is_revoked=False,
        ))
        db.session.commit()

        print("✓ Seeded:")
        print(f"  - {len(STORES)} stores: {', '.join(STORES)}")
        print(f"  - {len(users)} users (all PIN=1234, features=notes+mynotes+vetting):")
        for u in users:
            print(f"      {u.username:14} {u.role:12} store={u.store or '—'}")
        print(f"  - {len(notes_to_add)} notes (mix of pending/resolved, all 4 categories)")
        print(f"  - 4 pending devices for testing device-hint candidates")
        print()
        print("Open  http://localhost:5000/dev  to pick an identity.")


if __name__ == "__main__":
    seed()
