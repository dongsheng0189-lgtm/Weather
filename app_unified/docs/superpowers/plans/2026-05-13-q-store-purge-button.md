# Q-Store Purge Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-click "🗑️ 清除 Q 店全部筆記" button visible only to root_admin on `/notes/?store=Q`, that purges all Q-store notes (incl. checklist type) + cascades child rows + cleans R2 attachments + writes UserLog audit row. Temporary feature — will be removed after Q-store wipe completes (marked with `TEMP: q-store-purge`).

**Architecture:** Two new endpoints in `admin/routes.py` (`GET /admin/q-store/notes-preview` and `POST /admin/q-store/notes-purge`), both `require_root_admin()`-gated and hardcoded to `STORE_NAME='Q'`. UI button + modal injected into `templates/notes/index.html` conditional on `{% if current_user.is_root_admin() and current_store == 'Q' %}`. R2 cleanup synchronous (43 keys × 1 bulk call < 1s). DB delete uses bulk SQL with PG-level FK cascades wiping `checklist_items` + `note_attachments` automatically.

**Tech Stack:** Python 3.12 / Flask 3 / SQLAlchemy 2 / pytest / boto3 (R2) / vanilla JS (matching existing notes/admin patterns).

**Reference spec:** `app_unified/docs/superpowers/specs/2026-05-13-q-store-purge-button-design.md`

---

## File Structure

### Create
- `app_unified/tests/test_admin_q_store_purge.py` — all backend tests (~12 tests)

### Modify
- `app_unified/admin/routes.py` — add 2 endpoints + import `NoteAttachment` (~80 lines added with `# TEMP: q-store-purge` markers)
- `app_unified/templates/notes/index.html` — add conditional button + modal + JS handler (~80 lines added inside `{% if root_admin and store=='Q' %}` block, also marker comment)

### Not touched (per spec §7)
- `notes/routes.py` (single-note delete unchanged)
- `vetting/*`, `mynotes/*`, `weather/*`, `features.py`, `models.py`, `dev/*`, `storage.py`
- Other stores' behaviour completely untouched

---

## Task Breakdown

### Task 1: Create test scaffold + first auth-gate test

**Files:**
- Create: `app_unified/tests/test_admin_q_store_purge.py`

- [ ] **Step 1: Write failing scaffold test**

```python
"""Backend tests for Q-store purge endpoints.

Spec: docs/superpowers/specs/2026-05-13-q-store-purge-button-design.md
TEMP: q-store-purge — remove this whole test file when feature is taken out.
"""
import uuid
import pytest
from unittest.mock import MagicMock
from models import User, Store, Note, NoteAttachment, UserLog
from extensions import db


def _mk_user(db_session, username=None, role="user", root_admin=False, store="B"):
    """Make a test user. root_admin=True forces username='admin' (seed)."""
    target = "admin" if root_admin else (username or f"u_{uuid.uuid4().hex[:6]}")
    existing = User.query.filter_by(username=target).first()
    if existing:
        return existing
    u = User(username=target, role=role, store=store, is_active=True)
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_preview_endpoint_exists(app, client, db_session):
    """Smoke: /admin/q-store/notes-preview endpoint is registered."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/admin/q-store/notes-preview")
    # We don't care about status yet — just confirm route exists (not 404 NotFound from Flask routing)
    # If 404 here means routing miss; if 200/403/etc means routing matched
    assert res.status_code != 404 or "q-store" in res.get_data(as_text=True).lower() or res.status_code == 404
    # The above tautology is intentional — proper assertion comes in Task 2
```

> Note: The above scaffold test is intentionally weak; Task 2 replaces it with proper auth-gate assertions. We need a starting point that exercises the import path.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v`
Expected: FAIL with `ImportError: cannot import name 'NoteAttachment' from 'models'` OR test passes vacuously if NoteAttachment exists. **If it imports fine and passes, skip ahead to Task 2 — scaffold is just to bootstrap the file.**

- [ ] **Step 3: Commit scaffold**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/tests/test_admin_q_store_purge.py
git commit -m "test(admin): scaffold q-store purge test file"
```

---

### Task 2: Auth gate — non-root-admin gets 404 for preview

**Files:**
- Modify: `app_unified/tests/test_admin_q_store_purge.py`
- Modify: `app_unified/admin/routes.py` (add preview endpoint)

- [ ] **Step 1: Replace scaffold test with real auth-gate tests**

Replace the contents of `tests/test_admin_q_store_purge.py` (keep imports, `_mk_user`, `_login` helpers) with:

```python
"""Backend tests for Q-store purge endpoints.

Spec: docs/superpowers/specs/2026-05-13-q-store-purge-button-design.md
TEMP: q-store-purge — remove this whole test file when feature is taken out.
"""
import uuid
import pytest
from unittest.mock import MagicMock, patch
from models import User, Store, Note, NoteAttachment, UserLog
from extensions import db


def _mk_user(db_session, username=None, role="user", root_admin=False, store="B"):
    target = "admin" if root_admin else (username or f"u_{uuid.uuid4().hex[:6]}")
    existing = User.query.filter_by(username=target).first()
    if existing:
        return existing
    u = User(username=target, role=role, store=store, is_active=True)
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


# ── Auth gate ────────────────────────────────────────────────────


def test_preview_404_for_anonymous(client):
    """Anonymous (not logged in) → preview redirects to login."""
    res = client.get("/admin/q-store/notes-preview")
    assert res.status_code in (302, 401)


def test_preview_404_for_user_role(app, client, db_session):
    """user role → 404 (require_root_admin guard)."""
    u = _mk_user(db_session)  # default role=user
    _login(client, u)
    res = client.get("/admin/q-store/notes-preview")
    assert res.status_code == 404


def test_preview_404_for_super_admin(app, client, db_session):
    """super_admin role (not username='admin') → 404."""
    u = _mk_user(db_session, username="boss", role="super_admin")
    _login(client, u)
    res = client.get("/admin/q-store/notes-preview")
    assert res.status_code == 404


def test_preview_200_for_root_admin(app, client, db_session):
    """username='admin' seed → 200 + json with both keys."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/admin/q-store/notes-preview")
    assert res.status_code == 200
    data = res.get_json()
    assert "notes_count" in data
    assert "attachments_count" in data
    assert isinstance(data["notes_count"], int)
    assert isinstance(data["attachments_count"], int)
```

- [ ] **Step 2: Run tests to verify auth-gate tests fail**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v`
Expected: FAIL on `test_preview_404_for_user_role`, `test_preview_404_for_super_admin`, `test_preview_200_for_root_admin` because the endpoint doesn't exist yet (will return Flask 404 NotFound).

- [ ] **Step 3: Add preview endpoint + NoteAttachment import**

Modify `app_unified/admin/routes.py`:

(1) Update the `from models import` line at top (line 8) — add `NoteAttachment`:

```python
from models import User, Note, Store, NoteLog, UserLog, TrustedDevice, NoteAttachment, STATUS_CHOICES
```

(2) At the **end of admin/routes.py**, append:

```python


# ════════════════════════════════════════════════════════════════════
# TEMP: q-store-purge — Q 店一次性清理，2026-05-13 加。
# 完成後整段 endpoint + 對應 UI + tests 移除。
# 詳見 docs/superpowers/specs/2026-05-13-q-store-purge-button-design.md
# ════════════════════════════════════════════════════════════════════

_Q_PURGE_STORE = "Q"  # hardcoded — endpoint 不接受 url param 或 body 指定店


@admin_bp.route("/q-store/notes-preview", methods=["GET"])
@login_required
def q_store_notes_preview():
    """Preview how many notes/attachments will be purged from Q store."""
    from features import require_root_admin
    require_root_admin()
    notes_count = Note.query.filter_by(store=_Q_PURGE_STORE).count()
    attachments_count = (
        NoteAttachment.query
        .join(Note, NoteAttachment.note_id == Note.id)
        .filter(Note.store == _Q_PURGE_STORE)
        .count()
    )
    return jsonify({
        "notes_count": notes_count,
        "attachments_count": attachments_count,
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v`
Expected: PASS (4 tests including anonymous redirect, 2× 404 for non-root-admin, 200 for root_admin with json keys).

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/admin/routes.py app_unified/tests/test_admin_q_store_purge.py
git commit -m "feat(admin): q-store purge preview endpoint with auth gate"
```

---

### Task 3: Preview returns correct counts (only Q-store data)

**Files:**
- Modify: `app_unified/tests/test_admin_q_store_purge.py`

- [ ] **Step 1: Append correctness tests**

Append to `tests/test_admin_q_store_purge.py`:

```python
# ── Preview correctness ─────────────────────────────────────────


def test_preview_counts_q_notes_only(app, client, db_session):
    """3 Q notes + 5 B notes → preview reports notes_count=3."""
    admin = _mk_user(db_session, root_admin=True)
    # Need Store rows for FK validity in some setups; create if missing
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    if not Store.query.filter_by(name="B").first():
        db_session.add(Store(name="B", login_enabled=True))
    db_session.commit()

    for _ in range(3):
        db_session.add(Note(store="Q", title="q-note", content="x"))
    for _ in range(5):
        db_session.add(Note(store="B", title="b-note", content="x"))
    db_session.commit()

    _login(client, admin)
    res = client.get("/admin/q-store/notes-preview")
    data = res.get_json()
    assert data["notes_count"] == 3
    assert data["attachments_count"] == 0


def test_preview_counts_q_attachments(app, client, db_session):
    """2 Q notes, 1 with 2 attachments + 1 with 0 → preview reports 2."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()

    n1 = Note(store="Q", title="with-attach", content="x")
    n2 = Note(store="Q", title="no-attach", content="x")
    db_session.add_all([n1, n2])
    db_session.commit()

    a1 = NoteAttachment(
        note_id=n1.id, object_key="q-test/1.jpg",
        filename="1.jpg", content_type="image/jpeg", file_size=100,
    )
    a2 = NoteAttachment(
        note_id=n1.id, object_key="q-test/2.jpg",
        filename="2.jpg", content_type="image/jpeg", file_size=200,
    )
    db_session.add_all([a1, a2])
    db_session.commit()

    _login(client, admin)
    res = client.get("/admin/q-store/notes-preview")
    data = res.get_json()
    assert data["notes_count"] == 2
    assert data["attachments_count"] == 2


def test_preview_counts_zero_when_no_q_data(app, client, db_session):
    """No Q notes at all → preview returns {0, 0}."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()

    _login(client, admin)
    res = client.get("/admin/q-store/notes-preview")
    data = res.get_json()
    assert data["notes_count"] == 0
    assert data["attachments_count"] == 0
```

- [ ] **Step 2: Run tests**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v`
Expected: PASS (7 total: 4 auth + 3 correctness).

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/tests/test_admin_q_store_purge.py
git commit -m "test(admin): preview endpoint counts Q-store data only"
```

---

### Task 4: Purge endpoint — DB delete + UserLog (no R2 yet)

**Files:**
- Modify: `app_unified/admin/routes.py`
- Modify: `app_unified/tests/test_admin_q_store_purge.py`

- [ ] **Step 1: Write failing purge tests (R2 mock helper + 4 tests)**

Append to `tests/test_admin_q_store_purge.py`:

```python
# ── Purge happy path (with R2 mocked to no-op) ──────────────────


def _mock_r2_client(success_keys, fail_entries=None):
    """Build a MagicMock R2 client whose delete_objects returns deterministic shape."""
    fail_entries = fail_entries or []
    client = MagicMock()
    client.delete_objects.return_value = {
        "Deleted": [{"Key": k} for k in success_keys],
        "Errors": fail_entries,
    }
    return client


def _patch_r2(monkeypatch, mock_client):
    """Replace storage._get_client to return our mock; also set R2_BUCKET_NAME."""
    monkeypatch.setattr("admin.routes._get_r2_client", lambda: mock_client)


def test_purge_404_for_user_role(app, client, db_session, monkeypatch):
    """Non-root-admin POSTing purge → 404."""
    u = _mk_user(db_session)
    _login(client, u)
    res = client.post("/admin/q-store/notes-purge")
    assert res.status_code == 404


def test_purge_deletes_q_notes_only(app, client, db_session, monkeypatch):
    """3 Q notes + 5 B notes → after purge: 0 Q, 5 B remain."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    if not Store.query.filter_by(name="B").first():
        db_session.add(Store(name="B", login_enabled=True))
    db_session.commit()

    for _ in range(3):
        db_session.add(Note(store="Q", title="q-note", content="x"))
    for _ in range(5):
        db_session.add(Note(store="B", title="b-note", content="x"))
    db_session.commit()

    _patch_r2(monkeypatch, _mock_r2_client(success_keys=[]))
    _login(client, admin)
    res = client.post("/admin/q-store/notes-purge")
    assert res.status_code == 200
    data = res.get_json()
    assert data["deleted_notes"] == 3

    # Verify DB state
    assert Note.query.filter_by(store="Q").count() == 0
    assert Note.query.filter_by(store="B").count() == 5


def test_purge_writes_user_log(app, client, db_session, monkeypatch):
    """After purge → UserLog has 1 new row with action='purge_store_notes'."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()
    db_session.add(Note(store="Q", title="x", content="x"))
    db_session.commit()

    before = UserLog.query.filter_by(action="purge_store_notes").count()
    _patch_r2(monkeypatch, _mock_r2_client(success_keys=[]))
    _login(client, admin)
    client.post("/admin/q-store/notes-purge")

    after = UserLog.query.filter_by(action="purge_store_notes").count()
    assert after == before + 1
    log = UserLog.query.filter_by(action="purge_store_notes").order_by(UserLog.id.desc()).first()
    assert log.target_name == "store:Q"
    assert log.operator_name == admin.username
    assert log.target_id is None
    assert "notes=1" in (log.detail or "")


def test_purge_returns_correct_response_shape(app, client, db_session, monkeypatch):
    """Response has deleted_notes, deleted_r2_ok, deleted_r2_fail, errors keys."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()

    _patch_r2(monkeypatch, _mock_r2_client(success_keys=[]))
    _login(client, admin)
    res = client.post("/admin/q-store/notes-purge")
    data = res.get_json()
    assert "deleted_notes" in data
    assert "deleted_r2_ok" in data
    assert "deleted_r2_fail" in data
    assert "errors" in data
    assert isinstance(data["errors"], list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v`
Expected: FAIL on the 4 purge tests (404 from no route, or missing `_get_r2_client` attribute).

- [ ] **Step 3: Implement purge endpoint in `admin/routes.py`**

Add the necessary import at top of file (after the existing storage-related imports — actually `admin/routes.py` doesn't import boto3 directly, we'll wrap it):

In `admin/routes.py`, **just before the existing `_Q_PURGE_STORE = "Q"` line added in Task 2**, insert:

```python
def _get_r2_client():
    """Build boto3 S3 client for R2. Wrapped to allow monkeypatch in tests.

    TEMP: q-store-purge — see Q-store purge endpoint.
    """
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=current_app.config["R2_ENDPOINT_URL"],
        aws_access_key_id=current_app.config["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=current_app.config["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
```

Then **after** the existing `q_store_notes_preview` endpoint, append the purge endpoint:

```python
@admin_bp.route("/q-store/notes-purge", methods=["POST"])
@login_required
def q_store_notes_purge():
    """Purge all Q-store notes + their attachments (DB + R2).

    Hardcoded to Q store. Logs to UserLog. Synchronous R2 cleanup.
    TEMP: q-store-purge
    """
    from features import require_root_admin
    require_root_admin()

    # 1. Collect R2 keys BEFORE DB delete (cascade will wipe NoteAttachment rows)
    attachments = (
        NoteAttachment.query
        .join(Note, NoteAttachment.note_id == Note.id)
        .filter(Note.store == _Q_PURGE_STORE)
        .all()
    )
    keys = [a.object_key for a in attachments]

    # 2. Bulk DELETE notes (PG-level FK cascade wipes checklist_items + note_attachments)
    deleted_notes = (
        Note.query.filter_by(store=_Q_PURGE_STORE)
        .delete(synchronize_session=False)
    )

    # 3. Audit log
    audit = UserLog(
        operator_id=current_user.id,
        operator_name=current_user.username,
        target_id=None,
        target_name=f"store:{_Q_PURGE_STORE}",
        action="purge_store_notes",
        detail=f"notes={deleted_notes}, attachments={len(keys)}",
    )
    db.session.add(audit)

    # 4. Commit DB changes (do this before R2 — if R2 fails, DB still consistent)
    db.session.commit()

    # 5. R2 bulk delete (DB already committed; failures only reported, not rolled back)
    ok = 0
    fail = 0
    errors = []
    if keys:
        try:
            r2 = _get_r2_client()
            bucket = current_app.config.get("R2_BUCKET_NAME", "")
            resp = r2.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in keys]},
            )
            ok = len(resp.get("Deleted", []))
            for err in resp.get("Errors", []):
                fail += 1
                errors.append({"key": err.get("Key"), "msg": err.get("Message")})
        except Exception as e:
            # R2 client unreachable / not configured — DB is already committed,
            # mark all as failed and surface error so operator can manual-clean R2.
            fail = len(keys)
            errors.append({"key": "*", "msg": f"{type(e).__name__}: {e}"})

    # 6. Update UserLog detail with R2 outcome (audit completeness)
    audit.detail = f"notes={deleted_notes}, attachments={len(keys)}, r2_ok={ok}, r2_fail={fail}"
    db.session.commit()

    return jsonify({
        "deleted_notes": deleted_notes,
        "deleted_r2_ok": ok,
        "deleted_r2_fail": fail,
        "errors": errors,
    })
```

- [ ] **Step 4: Run tests**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v`
Expected: PASS (11 tests total: 4 auth + 3 preview + 4 purge).

- [ ] **Step 5: Run full test suite — regression check**

Run: `cd app_unified && ../venv/bin/pytest tests/ --tb=short 2>&1 | tail -5`
Expected: All previously passing tests still pass. New tests pass.

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/admin/routes.py app_unified/tests/test_admin_q_store_purge.py
git commit -m "feat(admin): q-store purge endpoint with DB delete + UserLog audit"
```

---

### Task 5: Purge — R2 cleanup correctness (mock-verified)

**Files:**
- Modify: `app_unified/tests/test_admin_q_store_purge.py`

- [ ] **Step 1: Write test verifying R2 receives the right keys**

Append to `tests/test_admin_q_store_purge.py`:

```python
def test_purge_calls_r2_delete_with_collected_keys(app, client, db_session, monkeypatch):
    """R2 client.delete_objects should be called with the Q-store attachments' object_keys."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()

    n = Note(store="Q", title="x", content="x")
    db_session.add(n)
    db_session.commit()
    a1 = NoteAttachment(
        note_id=n.id, object_key="q-store/key1.jpg",
        filename="k1.jpg", content_type="image/jpeg", file_size=10,
    )
    a2 = NoteAttachment(
        note_id=n.id, object_key="q-store/key2.jpg",
        filename="k2.jpg", content_type="image/jpeg", file_size=20,
    )
    db_session.add_all([a1, a2])
    db_session.commit()

    mock_client = _mock_r2_client(success_keys=["q-store/key1.jpg", "q-store/key2.jpg"])
    _patch_r2(monkeypatch, mock_client)

    _login(client, admin)
    res = client.post("/admin/q-store/notes-purge")
    assert res.status_code == 200
    data = res.get_json()
    assert data["deleted_r2_ok"] == 2
    assert data["deleted_r2_fail"] == 0

    # Verify boto3 client received the right call shape
    mock_client.delete_objects.assert_called_once()
    call_kwargs = mock_client.delete_objects.call_args.kwargs
    delivered_keys = sorted(o["Key"] for o in call_kwargs["Delete"]["Objects"])
    assert delivered_keys == ["q-store/key1.jpg", "q-store/key2.jpg"]


def test_purge_r2_partial_failure_reported(app, client, db_session, monkeypatch):
    """R2 returns Deleted=[1 ok] + Errors=[1 fail] → response reports both."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()

    n = Note(store="Q", title="x", content="x")
    db_session.add(n)
    db_session.commit()
    db_session.add(NoteAttachment(
        note_id=n.id, object_key="qkey-a", filename="a", content_type="image/jpeg", file_size=10,
    ))
    db_session.add(NoteAttachment(
        note_id=n.id, object_key="qkey-b", filename="b", content_type="image/jpeg", file_size=10,
    ))
    db_session.commit()

    mock_client = _mock_r2_client(
        success_keys=["qkey-a"],
        fail_entries=[{"Key": "qkey-b", "Message": "AccessDenied"}],
    )
    _patch_r2(monkeypatch, mock_client)

    _login(client, admin)
    res = client.post("/admin/q-store/notes-purge")
    data = res.get_json()
    assert data["deleted_r2_ok"] == 1
    assert data["deleted_r2_fail"] == 1
    assert data["errors"] == [{"key": "qkey-b", "msg": "AccessDenied"}]


def test_purge_r2_no_attachments_skips_call(app, client, db_session, monkeypatch):
    """Q store has notes but zero attachments → R2 client.delete_objects NOT called."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()
    db_session.add(Note(store="Q", title="x", content="x"))
    db_session.commit()

    mock_client = _mock_r2_client(success_keys=[])
    _patch_r2(monkeypatch, mock_client)
    _login(client, admin)
    res = client.post("/admin/q-store/notes-purge")
    assert res.status_code == 200
    mock_client.delete_objects.assert_not_called()
```

- [ ] **Step 2: Run tests**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v`
Expected: PASS (14 total).

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/tests/test_admin_q_store_purge.py
git commit -m "test(admin): q-store purge R2 cleanup with mocked boto3 client"
```

---

### Task 6: Purge — hardcoded Q enforcement (tamper-proof)

**Files:**
- Modify: `app_unified/tests/test_admin_q_store_purge.py`

- [ ] **Step 1: Write test verifying URL/body tampering is ignored**

Append to `tests/test_admin_q_store_purge.py`:

```python
def test_purge_hardcoded_to_q_ignores_query_string(app, client, db_session, monkeypatch):
    """POST with ?store=B → still only Q is deleted (hardcoded, not parameterized)."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    if not Store.query.filter_by(name="B").first():
        db_session.add(Store(name="B", login_enabled=True))
    db_session.commit()

    db_session.add(Note(store="Q", title="q", content="x"))
    db_session.add(Note(store="B", title="b", content="x"))
    db_session.commit()

    _patch_r2(monkeypatch, _mock_r2_client(success_keys=[]))
    _login(client, admin)
    res = client.post("/admin/q-store/notes-purge?store=B",
                      json={"store": "B"})  # Tamper with both query & body
    assert res.status_code == 200
    # Verify only Q was deleted, B is intact
    assert Note.query.filter_by(store="Q").count() == 0
    assert Note.query.filter_by(store="B").count() == 1
```

- [ ] **Step 2: Run tests**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v`
Expected: PASS (15 total).

- [ ] **Step 3: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/tests/test_admin_q_store_purge.py
git commit -m "test(admin): verify purge endpoint ignores store= tampering"
```

---

### Task 7: Frontend — conditional button rendering + visibility smoke tests

**Files:**
- Modify: `app_unified/templates/notes/index.html`
- Modify: `app_unified/tests/test_admin_q_store_purge.py`

- [ ] **Step 1: Write failing frontend visibility tests**

Append to `tests/test_admin_q_store_purge.py`:

```python
# ── Frontend conditional rendering ──────────────────────────────


def test_purge_button_visible_for_root_admin_on_q_tab(app, client, db_session):
    """root_admin GET /notes/?store=Q → page has id='q-purge-btn'."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()
    _login(client, admin)
    res = client.get("/notes/?store=Q")
    html = res.get_data(as_text=True)
    assert 'id="q-purge-btn"' in html
    # Modal markers also present
    assert 'id="q-purge-modal"' in html
    assert "清除 Q 店全部筆記" in html


def test_purge_button_hidden_for_root_admin_on_other_store(app, client, db_session):
    """root_admin on store=B (not Q) → no purge button."""
    admin = _mk_user(db_session, root_admin=True)
    if not Store.query.filter_by(name="B").first():
        db_session.add(Store(name="B", login_enabled=True))
    db_session.commit()
    _login(client, admin)
    res = client.get("/notes/?store=B")
    html = res.get_data(as_text=True)
    assert 'id="q-purge-btn"' not in html


def test_purge_button_hidden_for_super_admin_on_q_tab(app, client, db_session):
    """super_admin (non-seed admin) on store=Q → no purge button."""
    boss = _mk_user(db_session, username="boss", role="super_admin", store="Q")
    if not Store.query.filter_by(name="Q").first():
        db_session.add(Store(name="Q", login_enabled=True))
    db_session.commit()
    _login(client, boss)
    res = client.get("/notes/?store=Q")
    html = res.get_data(as_text=True)
    assert 'id="q-purge-btn"' not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v -k purge_button`
Expected: FAIL (template has no `q-purge-btn` yet).

- [ ] **Step 3: Inject conditional button + modal into `templates/notes/index.html`**

Find the existing `<div class="store-tabs">...</div>` block (around line 134). Just **after** the closing `</div>` of `.store-tabs`, insert this block:

```html
{# TEMP: q-store-purge — Q 店一次性清理按鈕，用完整段移除（含 modal + JS）#}
{% if current_user.is_root_admin() and current_store == 'Q' %}
<div id="q-purge-wrap" style="margin: .5rem 0 1rem; text-align:right;">
  <button id="q-purge-btn" type="button"
          onclick="qPurgeOpen()"
          style="background:#c0392b;color:#fff;border:0;padding:.5rem 1rem;border-radius:6px;font-size:.9rem;font-weight:600;cursor:pointer;">
    🗑️ 清除 Q 店全部筆記
  </button>
</div>

<div id="q-purge-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9999;align-items:center;justify-content:center;">
  <div style="background:#fff;padding:1.5rem;border-radius:8px;max-width:480px;width:90%;box-shadow:0 4px 20px rgba(0,0,0,.2);">
    <h3 style="margin:0 0 1rem;color:#c0392b;">⚠️ 即將永久刪除</h3>
    <div id="q-purge-preview" style="font-size:.95rem;line-height:1.6;margin-bottom:1rem;color:#444;">
      載入中…
    </div>
    <p style="font-size:.88rem;color:#666;margin:.5rem 0;">不可復原。要繼續、請在下方輸入店名「Q」：</p>
    <input id="q-purge-confirm-input" type="text" placeholder="輸入 Q 確認"
           style="width:100%;padding:.5rem;border:1px solid #ccc;border-radius:4px;font-size:1rem;margin-bottom:1rem;"
           oninput="qPurgeOnInput()">
    <div style="display:flex;gap:.5rem;justify-content:flex-end;">
      <button type="button" onclick="qPurgeClose()" id="q-purge-cancel"
              style="background:#bbb;color:#fff;border:0;padding:.5rem 1rem;border-radius:4px;cursor:pointer;">
        取消
      </button>
      <button type="button" id="q-purge-confirm-btn" disabled onclick="qPurgeExecute()"
              style="background:#c0392b;color:#fff;border:0;padding:.5rem 1rem;border-radius:4px;cursor:pointer;opacity:.5;">
        永久刪除
      </button>
    </div>
  </div>
</div>

<script>
function qPurgeOpen() {
  document.getElementById('q-purge-modal').style.display = 'flex';
  document.getElementById('q-purge-preview').textContent = '載入中…';
  document.getElementById('q-purge-confirm-input').value = '';
  qPurgeOnInput();
  fetch('/admin/q-store/notes-preview')
    .then(function(r){ return r.json(); })
    .then(function(d){
      document.getElementById('q-purge-preview').innerHTML =
        '• <b>' + d.notes_count + '</b> 筆筆記 / 確認表單<br>' +
        '• <b>' + d.attachments_count + '</b> 個圖片/影片附件';
    })
    .catch(function(e){
      document.getElementById('q-purge-preview').textContent = '預覽失敗：' + e.message;
    });
}

function qPurgeOnInput() {
  var v = document.getElementById('q-purge-confirm-input').value.trim();
  var btn = document.getElementById('q-purge-confirm-btn');
  if (v === 'Q') {
    btn.disabled = false;
    btn.style.opacity = '1';
  } else {
    btn.disabled = true;
    btn.style.opacity = '.5';
  }
}

function qPurgeClose() {
  document.getElementById('q-purge-modal').style.display = 'none';
}

function qPurgeExecute() {
  var btn = document.getElementById('q-purge-confirm-btn');
  btn.disabled = true;
  btn.textContent = '處理中…';
  fetch('/admin/q-store/notes-purge', { method: 'POST' })
    .then(function(r){ return r.json().then(function(d){ return [r.status, d]; }); })
    .then(function(arr){
      var st = arr[0], d = arr[1];
      if (st !== 200) {
        alert('刪除失敗：' + JSON.stringify(d));
        btn.disabled = false;
        btn.textContent = '永久刪除';
        return;
      }
      alert('已刪除 ' + d.deleted_notes + ' 筆筆記 + R2 OK ' + d.deleted_r2_ok + ' / fail ' + d.deleted_r2_fail);
      window.location.reload();
    })
    .catch(function(e){
      alert('連線失敗：' + e.message);
      btn.disabled = false;
      btn.textContent = '永久刪除';
    });
}
</script>
{% endif %}
{# TEMP: q-store-purge END #}
```

- [ ] **Step 4: Run frontend visibility tests**

Run: `cd app_unified && ../venv/bin/pytest tests/test_admin_q_store_purge.py -v -k purge_button`
Expected: PASS (3 tests).

- [ ] **Step 5: Run full test suite**

Run: `cd app_unified && ../venv/bin/pytest tests/ --tb=short 2>&1 | tail -5`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/notes/index.html app_unified/tests/test_admin_q_store_purge.py
git commit -m "feat(notes): Q-store purge button + modal (root_admin + store=Q only)"
```

---

### Task 8: Local dev end-to-end (manual)

**Files:** none (manual verification step)

- [ ] **Step 1: Start local dev server**

Run: `cd app_unified && bash scripts/run_local.sh > /tmp/run_local.log 2>&1 &`
Wait for "Running on http://127.0.0.1:5000" in `/tmp/run_local.log`.

- [ ] **Step 2: Seed Q-store data in local dev DB**

Open another shell:
```bash
cd /home/hirain0126/projects/webapp/app_unified
../venv/bin/python -c "
from app import create_app
from extensions import db
from models import Store, Note, NoteAttachment
app = create_app()
with app.app_context():
    if not Store.query.filter_by(name='Q').first():
        db.session.add(Store(name='Q', login_enabled=True))
    db.session.commit()
    # add 3 notes + 2 attachments
    n1 = Note(store='Q', title='Q test 1', content='x', note_type='note')
    n2 = Note(store='Q', title='Q checklist', content='x', note_type='checklist')
    n3 = Note(store='Q', title='Q test 3', content='x', note_type='note')
    db.session.add_all([n1, n2, n3])
    db.session.commit()
    db.session.add(NoteAttachment(note_id=n1.id, object_key='dev/local/x1.jpg', filename='x', content_type='image/jpeg', file_size=10))
    db.session.add(NoteAttachment(note_id=n1.id, object_key='dev/local/x2.jpg', filename='x', content_type='image/jpeg', file_size=10))
    db.session.commit()
    print('seeded Q store with', Note.query.filter_by(store='Q').count(), 'notes')
"
```

Expected output: `seeded Q store with 3 notes`.

- [ ] **Step 3: Manual browser test**

- Open `http://localhost:5000/dev` → login as the seed `admin` user (root_admin)
- Click "店務筆記" → navigate to `/notes/?store=Q`
- Verify red button "🗑️ 清除 Q 店全部筆記" is visible at top of store-tabs area
- Click the button → modal appears with `• 3 筆筆記 / 確認表單` + `• 2 個圖片/影片附件`
- Try typing nothing → `永久刪除` button disabled
- Type `Q` → `永久刪除` button enabled
- Click `取消` → modal closes, no DB change (verify: switch to /notes/?store=Q, 3 notes still there)
- Re-open modal, type `Q`, click `永久刪除`
- Should see alert "已刪除 3 筆筆記 + R2 OK 0 / fail 0" (R2 dev locally NOT configured so will report fail; in dev that's OK because outbound_guard blocks it anyway)

Note: Because dev mode outbound_guard blocks R2 calls, you may see `r2_fail=2` or similar — that's expected locally. The DB delete must succeed.

- [ ] **Step 4: Verify with different store tab**

- Navigate to `/notes/?store=B` → button must NOT be present in DOM
- Navigate to `/notes/` (no store) → button must NOT be present

- [ ] **Step 5: Verify other roles**

- Login as a non-root-admin user (any seed user other than `admin`) via `/dev`
- Navigate to `/notes/?store=Q` → button must NOT be present

- [ ] **Step 6: Stop dev server**

```bash
fuser -k 5000/tcp 2>/dev/null
```

- [ ] **Step 7: No commit (this task is manual verification only)**

---

## Post-Implementation: Verification before merge

After T1-T8 all done:

```bash
cd /home/hirain0126/projects/webapp
git status                          # expect: clean working tree
cd app_unified && ../venv/bin/pytest tests/ --tb=short 2>&1 | tail -5   # all tests green
git log --oneline main..HEAD        # expect: ~7 commits (T1-T7, T8 no commit)
git diff --stat main..HEAD          # expect: 3 files changed
```

**Do NOT push.** Per [[feedback_push_policy]] — wait for user to OK push.

---

## Self-Review Checklist (run after writing this plan)

**1. Spec coverage** — each spec section traced to a task:

- §1 目標 + lifespan (TEMP) → all tasks marked with `TEMP: q-store-purge` comments
- §2.1 按鈕渲染條件 (`{% if root_admin and store=='Q' %}`) → T7
- §2.2 預覽 → 打字 → 確認 → reload flow → T7 JS handlers
- §2.3 button state 處理中 → T7 `qPurgeExecute` sets btn.disabled + text
- §3.1 2 endpoints → T2 (preview), T4 (purge)
- §3.2 `require_root_admin()` gate → T2 (preview), T4 (purge)
- §3.3 hardcoded `_Q_PURGE_STORE = "Q"` → T2 + T4 + T6 (tamper test)
- §3.4 purge flow (collect keys → DELETE → log → commit → R2) → T4 (DB+log) + T5 (R2 mock test)
- §3.5 R2 failure non-rollback → T5 test covers partial failure path
- §5 audit log (`action='purge_store_notes'`) → T4 test_purge_writes_user_log
- §6 out of scope → not implemented (correctly absent)
- §7 既有功能保護 → all existing files except admin/routes + notes/index.html untouched
- §8.1 auth gate (2 tests) → T2 (3 actually: anon/user/super_admin + 1 for root_admin happy)
- §8.2 preview tests (3) → T3
- §8.3 purge tests (5) → T4 + T5 + T6 covers all 5 themes
- §8.4 R2 mock → T4 helpers + T5
- §8.5 frontend smoke (3) → T7
- §9 部署 → captured in post-impl section
- §10 後續移除 → all `TEMP: q-store-purge` markers placed for future search

**2. Placeholder scan** — no "TBD", no "fill in later", every test has full code. ✓

**3. Type consistency**:
- Endpoint paths: `/admin/q-store/notes-preview` (T2-T3), `/admin/q-store/notes-purge` (T4-T7) — stable
- Response keys: `notes_count`, `attachments_count` (T2-T3); `deleted_notes`, `deleted_r2_ok`, `deleted_r2_fail`, `errors` (T4-T6) — stable
- DOM ids: `q-purge-btn`, `q-purge-modal`, `q-purge-preview`, `q-purge-confirm-input`, `q-purge-confirm-btn`, `q-purge-cancel`, `q-purge-wrap` — stable across T7
- JS function names: `qPurgeOpen`, `qPurgeOnInput`, `qPurgeClose`, `qPurgeExecute` — stable
- Backend constant: `_Q_PURGE_STORE = "Q"` (T2+T4+T6) — stable
- UserLog action value: `'purge_store_notes'` (T4 test + T4 implementation) — stable
