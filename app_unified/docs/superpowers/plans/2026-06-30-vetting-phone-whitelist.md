# 電話白名單 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在人員查詢的電話查詢加入「白名單」：查詢電話時比對內部白名單，命中就顯示「✓ 白名單」並列出號碼；授權者可新增、root admin 可看全部清單並改/刪。

**Architecture:** 新增加密表 `VettingWhitelistPhone`（全店共用、只存正規化號碼）。比對採「載入全部解密、正規化在記憶體比對」（名單小）。DB 存取集中在新模組 `vetting/whitelist.py`（`social.py` 維持純函數無 DB）。權限用既有 features 機制新增一個 `grant_only` 項（只在「模組權限管理」出現、不變模組磚）。

**Tech Stack:** Flask + SQLAlchemy + `EncryptedText`(AES-256-GCM) + flask-login + flask-limiter；Vanilla JS + Jinja2；pytest。

## Global Constraints

- prod 不跑 alembic：**新表**由既有 `db.create_all()`（`app.py:142`）自動建立（prod/dev 皆是）→ 不需 inline `CREATE TABLE`；仍補 alembic migration 供 dev 對齊。**注意**：本表只新增「整張表」，無「對既有表加 column」，故不適用三檔對齊那條（那條針對加 column）。
- 加密：白名單號碼、新增者帳號一律用 `EncryptedText`（同 `DB_ENCRYPTION_KEY`）。
- 正規化一律用 `vetting/social.py::normalize_phone()`（台灣 10 碼 canonical）。
- 不寫任何稽核 log（user 明確要求）。
- 權限預設**對所有人關閉**；root admin（`is_root_admin()`，`username=='admin'`）`user_has_feature` 永遠 True；只有 root admin 能在「模組權限管理」授權。
- SW：所有 UI inline 於 `index.html` / `dashboard.html`，**不碰 `/static/`** → 不 bump SW。
- 本機驗證一律 `pytest tests/`（與 CI 一致）。

---

### Task 1: 資料模型 `VettingWhitelistPhone` + migration

**Files:**
- Modify: `models.py`（在 `class VettingQueryLog` 之後、`# 公告` 註解區塊之前插入）
- Create: `migrations/versions/018_vetting_whitelist_phones.py`
- Test: `tests/test_vetting_whitelist_model.py`

**Interfaces:**
- Produces: `models.VettingWhitelistPhone`，欄位 `id:int`、`phone:str`(加密)、`created_by:int|None`、`created_by_name:str|None`(加密)、`created_at:datetime`，`__tablename__="vetting_whitelist_phones"`。

- [ ] **Step 1: Write the failing test**

`tests/test_vetting_whitelist_model.py`：
```python
"""VettingWhitelistPhone model — 建立 + 號碼加密落地。"""
from datetime import datetime
from extensions import db
from models import VettingWhitelistPhone


def test_create_and_query(app, db_session):
    row = VettingWhitelistPhone(
        phone="0912345678", created_by=None,
        created_by_name="alice", created_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()
    got = VettingWhitelistPhone.query.filter_by(id=row.id).first()
    assert got is not None
    assert got.phone == "0912345678"          # ORM 端解密回明文
    assert got.created_by_name == "alice"


def test_phone_encrypted_at_rest(app, db_session):
    row = VettingWhitelistPhone(
        phone="0912345678", created_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()
    raw = db_session.execute(
        db.text("SELECT phone FROM vetting_whitelist_phones WHERE id=:i"),
        {"i": row.id},
    ).scalar()
    assert raw.startswith("enc:v1:")          # DB 端是密文
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vetting_whitelist_model.py -v`
Expected: FAIL（`ImportError: cannot import name 'VettingWhitelistPhone'`）

- [ ] **Step 3: Add the model**

`models.py`，在 `class VettingQueryLog(...)` 整段之後插入：
```python
class VettingWhitelistPhone(db.Model):
    """電話白名單 — 人員查詢電話比對用。全店共用、號碼加密存。"""
    __tablename__ = "vetting_whitelist_phones"
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(EncryptedText, nullable=False)          # 正規化 09xxxxxxxx，加密
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by_name = db.Column(EncryptedText, nullable=True)  # 新增者帳號快照（加密）
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
```
（`datetime` 與 `EncryptedText` 已在 `models.py` 頂部 import，無需新增。）

- [ ] **Step 4: Create the migration**

`migrations/versions/018_vetting_whitelist_phones.py`：
```python
"""VettingWhitelistPhone: 電話白名單表

Revision ID: 018
Revises: 017
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa

revision = '018'
down_revision = '017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vetting_whitelist_phones',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('phone', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_by_name', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('vetting_whitelist_phones')
```
（已確認 `migrations/versions/017_note_reminder.py` 的 `revision = '017'` → `down_revision = '017'` 正確。）

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_vetting_whitelist_model.py -v`
Expected: PASS（2 passed）。`db.create_all()` 會在測試 app 建表。

- [ ] **Step 6: Commit**

```bash
git add models.py migrations/versions/018_vetting_whitelist_phones.py tests/test_vetting_whitelist_model.py
git commit -m "feat(vetting): 電話白名單資料表 VettingWhitelistPhone + migration 018"
```

---

### Task 2: 權限 `vetting_whitelist_add`（grant_only，不變模組磚）

**Files:**
- Modify: `features.py`（`AVAILABLE_FEATURES` 加項、`user_visible_features` 過濾 grant_only）
- Test: `tests/test_vetting_whitelist_feature.py`

**Interfaces:**
- Produces: feature key `"vetting_whitelist_add"`（在 `AVAILABLE_FEATURES`、含 `"grant_only": True`）。`user_visible_features` 不回傳含 `grant_only` 的項。

- [ ] **Step 1: Write the failing test**

`tests/test_vetting_whitelist_feature.py`：
```python
"""vetting_whitelist_add 權限：grant_only、不入模組磚、root 預設有、預設全關。"""
import uuid
from features import (AVAILABLE_FEATURES, feature_meta,
                      user_has_feature, user_visible_features)
from models import User
from extensions import db


def _mk(db_session, root_admin=False, features=None):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        if features is not None:
            existing.set_features(features); db_session.commit()
        return existing
    u = User(username=name, role="user", store="B", is_active=True)
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u); db_session.commit()
    return u


def test_in_catalog(app):
    assert feature_meta("vetting_whitelist_add") is not None
    assert any(f["key"] == "vetting_whitelist_add" for f in AVAILABLE_FEATURES)


def test_grant_only_not_in_visible_features_for_root(app, db_session):
    root = _mk(db_session, root_admin=True)
    keys = [f["key"] for f in user_visible_features(root)]
    assert "vetting_whitelist_add" not in keys           # 不變模組磚
    assert "notes" in keys                                # 一般模組仍在


def test_default_off_for_normal_user(app, db_session):
    u = _mk(db_session, features=["vetting"])
    assert user_has_feature(u, "vetting_whitelist_add") is False


def test_granted_user_has_it(app, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    assert user_has_feature(u, "vetting_whitelist_add") is True


def test_root_admin_has_it(app, db_session):
    root = _mk(db_session, root_admin=True)
    assert user_has_feature(root, "vetting_whitelist_add") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vetting_whitelist_feature.py -v`
Expected: FAIL（`test_in_catalog` 找不到 key、`test_grant_only_...` 因 key 不存在或未過濾而 fail）

- [ ] **Step 3: Add the feature + filter grant_only**

`features.py`：`AVAILABLE_FEATURES` 結尾 `]` 之前加一項：
```python
    {
        "key": "vetting_whitelist_add",
        "label": "新增白名單電話",
        "grant_only": True,   # 只在「模組權限管理」勾選清單出現，不是 dashboard 模組
    },
```
改寫 `user_visible_features`（過濾 grant_only，兩個分支都套用）：
```python
def user_visible_features(user):
    """使用者可看到的模組清單（給導覽列/選單頁用）。grant_only 權限不算模組、不顯示。"""
    if not user or not user.is_authenticated:
        return []
    modules = [f for f in AVAILABLE_FEATURES if not f.get("grant_only")]
    if user.is_root_admin():
        return list(modules)
    keys = set(user.get_features())
    return [f for f in modules if f["key"] in keys]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vetting_whitelist_feature.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add features.py tests/test_vetting_whitelist_feature.py
git commit -m "feat(vetting): vetting_whitelist_add 權限(grant_only)，不變模組磚"
```

---

### Task 3: `vetting/whitelist.py` — 白名單 DB 存取 + 比對

**Files:**
- Create: `vetting/whitelist.py`
- Test: `tests/test_vetting_whitelist_logic.py`

**Interfaces:**
- Consumes: `models.VettingWhitelistPhone`、`vetting.social.normalize_phone`。
- Produces:
  - `is_whitelisted(phone) -> bool`
  - `add(phone, *, user_id=None, user_name=None) -> str`（`'ok'|'exists'|'invalid'`）
  - `list_all() -> list[dict]`（key：`id, phone, created_by_name, created_at`）
  - `update(entry_id, phone) -> str`（`'ok'|'exists'|'invalid'|'not_found'`）
  - `delete(entry_id) -> bool`

- [ ] **Step 1: Write the failing test**

`tests/test_vetting_whitelist_logic.py`：
```python
"""白名單比對/新增/去重/改/刪 邏輯。"""
from vetting import whitelist


def test_add_then_match_various_formats(app, db_session):
    assert whitelist.add("0912-345-678", user_id=1, user_name="alice") == "ok"
    assert whitelist.is_whitelisted("0912345678") is True       # 不同格式同號
    assert whitelist.is_whitelisted("+886912345678") is True
    assert whitelist.is_whitelisted("0911000000") is False


def test_add_duplicate_returns_exists(app, db_session):
    assert whitelist.add("0922333444") == "ok"
    assert whitelist.add("0922-333-444") == "exists"            # 正規化後相同


def test_add_invalid_returns_invalid(app, db_session):
    assert whitelist.add("not-a-phone") == "invalid"
    assert whitelist.add("") == "invalid"


def test_is_whitelisted_empty_false(app, db_session):
    assert whitelist.is_whitelisted("") is False
    assert whitelist.is_whitelisted(None) is False


def test_list_all(app, db_session):
    whitelist.add("0933444555", user_name="bob")
    rows = whitelist.list_all()
    assert any(r["phone"] == "0933444555" and r["created_by_name"] == "bob"
               for r in rows)


def test_update(app, db_session):
    whitelist.add("0955666777")
    rid = whitelist.list_all()[0]["id"]
    assert whitelist.update(rid, "0955-666-888") == "ok"
    assert whitelist.is_whitelisted("0955666888") is True
    assert whitelist.is_whitelisted("0955666777") is False


def test_update_to_existing_returns_exists(app, db_session):
    whitelist.add("0966111222")
    whitelist.add("0966333444")
    rid = [r for r in whitelist.list_all() if r["phone"] == "0966333444"][0]["id"]
    assert whitelist.update(rid, "0966-111-222") == "exists"


def test_update_invalid_and_not_found(app, db_session):
    whitelist.add("0977222333")
    rid = whitelist.list_all()[0]["id"]
    assert whitelist.update(rid, "xxx") == "invalid"
    assert whitelist.update(999999, "0988555666") == "not_found"


def test_delete(app, db_session):
    whitelist.add("0999888777")
    rid = whitelist.list_all()[0]["id"]
    assert whitelist.delete(rid) is True
    assert whitelist.is_whitelisted("0999888777") is False
    assert whitelist.delete(rid) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vetting_whitelist_logic.py -v`
Expected: FAIL（`ModuleNotFoundError: vetting.whitelist`）

- [ ] **Step 3: Implement the module**

`vetting/whitelist.py`：
```python
"""電話白名單 — DB 存取 + 比對。

號碼加密存（EncryptedText），故無法 WHERE phone=? 查；名單小 → 載入全部解密、
正規化後在記憶體比對。social.py 維持純函數無 DB，白名單 DB 邏輯集中此處。
"""
from datetime import datetime

from extensions import db
from models import VettingWhitelistPhone
from vetting.social import normalize_phone


def _canonical_map():
    """{正規化號碼: row} —— 解密全部、跳過解不出/非法的。"""
    out = {}
    for r in VettingWhitelistPhone.query.all():
        c = normalize_phone(r.phone or "")
        if c:
            out[c] = r
    return out


def is_whitelisted(phone) -> bool:
    canonical = normalize_phone(phone or "")
    if not canonical:
        return False
    return canonical in _canonical_map()


def add(phone, *, user_id=None, user_name=None) -> str:
    canonical = normalize_phone(phone or "")
    if not canonical:
        return "invalid"
    if canonical in _canonical_map():
        return "exists"
    db.session.add(VettingWhitelistPhone(
        phone=canonical, created_by=user_id, created_by_name=user_name,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    return "ok"


def list_all() -> list:
    rows = (VettingWhitelistPhone.query
            .order_by(VettingWhitelistPhone.created_at.desc())
            .all())
    return [{
        "id": r.id,
        "phone": normalize_phone(r.phone or "") or (r.phone or ""),
        "created_by_name": r.created_by_name or "",
        "created_at": r.created_at.isoformat() if r.created_at else "",
    } for r in rows]


def update(entry_id, phone) -> str:
    canonical = normalize_phone(phone or "")
    if not canonical:
        return "invalid"
    row = VettingWhitelistPhone.query.get(entry_id)
    if not row:
        return "not_found"
    others = {normalize_phone(r.phone or "")
              for r in VettingWhitelistPhone.query.all() if r.id != entry_id}
    if canonical in others:
        return "exists"
    row.phone = canonical
    db.session.commit()
    return "ok"


def delete(entry_id) -> bool:
    row = VettingWhitelistPhone.query.get(entry_id)
    if not row:
        return False
    db.session.delete(row)
    db.session.commit()
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vetting_whitelist_logic.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add vetting/whitelist.py tests/test_vetting_whitelist_logic.py
git commit -m "feat(vetting): whitelist.py 白名單 DB 存取+比對(載入解密比對)"
```

---

### Task 4: `POST /vetting/whitelist/add` 端點（權限把關）

**Files:**
- Modify: `vetting/routes.py`（新增 route；頂部已 import `user_has_feature`、`limiter`、`abort`、`jsonify`、`request`、`login_required`、`current_user`）
- Test: `tests/test_vetting_whitelist_add_route.py`

**Interfaces:**
- Consumes: `vetting.whitelist.add`、`features.user_has_feature`。
- Produces: `POST /vetting/whitelist/add`，body `{"phone": str}` → `200 {status:'ok'|'exists'}` / `400 {error:'invalid_phone'}` / `404`(無權限)。

- [ ] **Step 1: Write the failing test**

`tests/test_vetting_whitelist_add_route.py`：
```python
"""新增白名單端點：權限、格式、去重。"""
import uuid
import pytest
from models import User
from extensions import db, limiter


@pytest.fixture(autouse=True)
def _reset_limiter(app):
    with app.app_context():
        limiter.reset()
    yield


def _mk(db_session, root_admin=False, features=None):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        if features is not None:
            existing.set_features(features); db_session.commit()
        return existing
    u = User(username=name, role="user", store="B", is_active=True)
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_granted_user_can_add(app, client, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    res = client.post("/vetting/whitelist/add", json={"phone": "0912-345-678"})
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_duplicate_returns_exists(app, client, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    client.post("/vetting/whitelist/add", json={"phone": "0922333444"})
    res = client.post("/vetting/whitelist/add", json={"phone": "0922-333-444"})
    assert res.status_code == 200
    assert res.get_json()["status"] == "exists"


def test_invalid_phone_400(app, client, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    res = client.post("/vetting/whitelist/add", json={"phone": "nope"})
    assert res.status_code == 400


def test_user_without_permission_404(app, client, db_session):
    """有 vetting 但沒 vetting_whitelist_add → 404。"""
    u = _mk(db_session, features=["vetting"])
    _login(client, u)
    res = client.post("/vetting/whitelist/add", json={"phone": "0912345678"})
    assert res.status_code == 404


def test_user_without_vetting_404(app, client, db_session):
    """連 vetting 都沒有 → before_request gate 先擋 404。"""
    u = _mk(db_session, features=["notes"])
    _login(client, u)
    res = client.post("/vetting/whitelist/add", json={"phone": "0912345678"})
    assert res.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vetting_whitelist_add_route.py -v`
Expected: FAIL（route 不存在 → 404 對 granted user，`test_granted_user_can_add` fail）

- [ ] **Step 3: Add the route**

`vetting/routes.py`，在 `submit()` 函式之後新增：
```python
@vetting_bp.route("/whitelist/add", methods=["POST"])
@login_required
@limiter.limit("20/minute", key_func=lambda: f"vet-wl-add:{current_user.id}")
def whitelist_add():
    """授權者新增白名單號碼（只能新增；看不到清單、不能改刪）。"""
    if not user_has_feature(current_user, "vetting_whitelist_add"):
        abort(404)
    from vetting import whitelist
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    status = whitelist.add(phone, user_id=current_user.id,
                           user_name=current_user.username)
    if status == "invalid":
        return jsonify({"error": "invalid_phone", "message": "電話格式錯誤"}), 400
    if status == "exists":
        return jsonify({"status": "exists", "message": "此號碼已在白名單"}), 200
    return jsonify({"status": "ok", "message": "已加入白名單"}), 200
```
（`before_request` 的 `_gate` 已先要求 `vetting` feature，故無 `vetting` 者連此 route 都進不來。）

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vetting_whitelist_add_route.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add vetting/routes.py tests/test_vetting_whitelist_add_route.py
git commit -m "feat(vetting): POST /vetting/whitelist/add 端點(權限把關+去重)"
```

---

### Task 5: 查詢時比對白名單（`start()` 標記 `is_whitelisted`）

**Files:**
- Modify: `vetting/routes.py::start()`（把 social bundle 抽成變數、加白名單標記後再存 state）
- Test: `tests/test_vetting_whitelist_match.py`

**Interfaces:**
- Consumes: `vetting.whitelist.is_whitelisted`。
- Produces: state 內 `social["phone"]["is_whitelisted"]: bool`（隨既有加密 social blob 跨 worker，無需改 `_serialize_state`）。

- [ ] **Step 1: Write the failing test**

`tests/test_vetting_whitelist_match.py`：
```python
"""查詢電話時，命中白名單會在 social.phone 標 is_whitelisted。"""
import uuid
from unittest.mock import MagicMock
import pytest
from models import User
from extensions import db, limiter
from vetting import whitelist
from vetting.routes import _take_state


@pytest.fixture(autouse=True)
def _reset_limiter(app):
    with app.app_context():
        limiter.reset()
    yield


def _mk_root(db_session):
    existing = User.query.filter_by(username="admin").first()
    if existing:
        return existing
    u = User(username="admin", role="user", store="B", is_active=True)
    u.set_password("1234")
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _mock_npa_judicial_mjib(monkeypatch):
    monkeypatch.setattr("vetting.routes.npa.fetch_home_state",
                        lambda sess=None: ({"_csrf": "x", "_csrf_header": "h", "_key": "k"}, MagicMock()))
    monkeypatch.setattr("vetting.routes.npa.fetch_auth_token", lambda sess, tok: "JWT")
    monkeypatch.setattr("vetting.routes.npa.fetch_captcha_bytes", lambda sess: b"\xff\xd8FAKE")
    monkeypatch.setattr("vetting.routes.judicial.fetch_home_state",
                        lambda sess=None: ({"form_action": "x", "hidden_fields": {}}, MagicMock()))
    monkeypatch.setattr("vetting.routes.judicial.submit_search", lambda sess, state, name: [])
    monkeypatch.setattr("vetting.routes.mjib.search_by_name", lambda name, session=None: [])
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)


def test_whitelisted_phone_flagged(app, client, db_session, monkeypatch):
    whitelist.add("0912345678")
    u = _mk_root(db_session); _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)
    res = client.post("/vetting/start", json={"name": "王", "phone": "0912-345-678"})
    assert res.status_code == 200
    entry = _take_state(res.get_json()["query_id"])
    assert entry["social"]["phone"]["is_whitelisted"] is True


def test_non_whitelisted_phone_not_flagged(app, client, db_session, monkeypatch):
    u = _mk_root(db_session); _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)
    res = client.post("/vetting/start", json={"name": "王", "phone": "0911000000"})
    assert res.status_code == 200
    entry = _take_state(res.get_json()["query_id"])
    assert entry["social"]["phone"]["is_whitelisted"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vetting_whitelist_match.py -v`
Expected: FAIL（`KeyError: 'is_whitelisted'`）

- [ ] **Step 3: Wire whitelist check into start()**

`vetting/routes.py::start()`，把目前 `_put_state({...})` 內 inline 的 social 改成先建變數再標記。將：
```python
    qid = _put_state({
        "npa_session": npa_session,
        "npa_tokens": npa_tokens,
        "npa_jwt": npa_jwt,
        "name": name,
        "id_no": id_no,
        "judicial": judicial_results,
        "mjib": mjib_results,
        "social": social.build_url_bundle(
            name, phone=phone, relatives=relatives, feature=feature
        ),
    })
```
改為：
```python
    social_bundle = social.build_url_bundle(
        name, phone=phone, relatives=relatives, feature=feature
    )
    # 電話命中內部白名單 → 標記（隨加密 social blob 跨 worker，不另存明文）
    if social_bundle.get("phone"):
        from vetting import whitelist
        social_bundle["phone"]["is_whitelisted"] = whitelist.is_whitelisted(phone)

    qid = _put_state({
        "npa_session": npa_session,
        "npa_tokens": npa_tokens,
        "npa_jwt": npa_jwt,
        "name": name,
        "id_no": id_no,
        "judicial": judicial_results,
        "mjib": mjib_results,
        "social": social_bundle,
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vetting_whitelist_match.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add vetting/routes.py tests/test_vetting_whitelist_match.py
git commit -m "feat(vetting): 查詢電話時比對白名單，social.phone 標 is_whitelisted"
```

---

### Task 6: 人員查詢頁 — 新增白名單鈕 + modal + D 段白名單標記

**Files:**
- Modify: `templates/vetting/index.html`
- Test: `tests/test_vetting_whitelist_index_wiring.py`

**Interfaces:**
- Consumes: `has_feature('vetting_whitelist_add')`(Jinja)、`POST /vetting/whitelist/add`、`social.phone.is_whitelisted`。

- [ ] **Step 1: Write the failing test**

`tests/test_vetting_whitelist_index_wiring.py`：
```python
"""人員查詢頁：授權者顯示新增白名單鈕；D 段渲染含白名單標記。"""
import uuid
from models import User
from extensions import db


def _mk(db_session, root_admin=False, features=None):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        if features is not None:
            existing.set_features(features); db_session.commit()
        return existing
    u = User(username=name, role="user", store="B", is_active=True)
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_add_button_shown_to_granted(app, client, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    _login(client, u)
    body = client.get("/vetting/").get_data(as_text=True)
    assert "新增白名單電話" in body
    assert "/vetting/whitelist/add" in body


def test_add_button_hidden_without_permission(app, client, db_session):
    u = _mk(db_session, features=["vetting"])
    _login(client, u)
    body = client.get("/vetting/").get_data(as_text=True)
    assert "新增白名單電話" not in body


def test_d_section_renders_whitelist_badge_marker(app, client, db_session):
    u = _mk(db_session, root_admin=True)
    _login(client, u)
    body = client.get("/vetting/").get_data(as_text=True)
    assert "is_whitelisted" in body            # D 段 JS 有處理白名單
    assert "白名單" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vetting_whitelist_index_wiring.py -v`
Expected: FAIL（`新增白名單電話` / `is_whitelisted` 尚未出現在頁面）

- [ ] **Step 3: Add button (gated), modal, D段 badge**

(a) `templates/vetting/index.html` 把標題列（line 7 `<h1>🛡️ 人員查詢</h1>`）改成標題 + 右上角鈕：
```html
  <div style="display:flex; align-items:center; justify-content:space-between; gap:.5rem;">
    <h1 style="margin:0;">🛡️ 人員查詢</h1>
    {% if has_feature('vetting_whitelist_add') %}
    <button type="button" id="wl-open-btn"
            style="background:#0e7490; color:#fff; border:none; padding:.45rem .7rem; border-radius:6px; cursor:pointer; font-size:.85rem; white-space:nowrap;">
      ➕ 新增白名單電話
    </button>
    {% endif %}
  </div>
```

(b) 在 `<div id="vet-status" ...></div>`（line 63）之後、外層 `</div>`（line 64）之前，加 modal（只在有鈕時用得到，但 markup 放著無妨；JS 綁定也只在鈕存在時生效）：
```html
  {% if has_feature('vetting_whitelist_add') %}
  <div id="wl-modal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,.4); z-index:1000; align-items:center; justify-content:center;">
    <div style="background:#fff; border-radius:8px; padding:1.2rem; width:90%; max-width:360px;">
      <h3 style="margin:0 0 .6rem;">➕ 新增白名單電話</h3>
      <input type="tel" id="wl-phone" placeholder="例：0912-345-678"
             style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.5rem;">
      <div id="wl-msg" style="font-size:.85rem; color:#666; min-height:1.2rem;"></div>
      <div style="display:flex; gap:.5rem; justify-content:flex-end; margin-top:.5rem;">
        <button type="button" id="wl-cancel" class="btn btn-secondary" style="padding:.4rem .8rem;">取消</button>
        <button type="button" id="wl-submit" class="btn btn-primary" style="padding:.4rem .8rem;">加入</button>
      </div>
    </div>
  </div>
  {% endif %}
```

(c) D 段 badge：`templates/vetting/index.html` 在 D 段 normalized 顯示那行（`html += '<div style="font-family:monospace; font-size:1.05rem; margin-bottom:.4rem;">' + escapeHtml(p.normalized) + '</div>';`）之後插入：
```javascript
      if (p.is_whitelisted) {
        html += '<div style="background:#e7f7ee; border:1px solid #16a085; color:#0e7a5f; '
              + 'border-radius:6px; padding:.4rem .6rem; margin-bottom:.4rem; font-weight:600;">'
              + '✓ 此號碼已在白名單中（' + escapeHtml(p.normalized) + '）</div>';
      }
```

(d) modal 行為 JS：在 `</script>`（檔尾 `EditKeepalive.init();` 之後、`</script>` 之前）加：
```javascript
// 新增白名單電話 modal（僅授權者頁面才有這些元素）
(function() {
  var openBtn = document.getElementById('wl-open-btn');
  if (!openBtn) return;
  var modal = document.getElementById('wl-modal');
  var input = document.getElementById('wl-phone');
  var msg = document.getElementById('wl-msg');
  function openM() { msg.textContent = ''; input.value = ''; modal.style.display = 'flex'; input.focus(); }
  function closeM() { modal.style.display = 'none'; }
  openBtn.addEventListener('click', openM);
  document.getElementById('wl-cancel').addEventListener('click', closeM);
  modal.addEventListener('click', function(e) { if (e.target === modal) closeM(); });
  document.getElementById('wl-submit').addEventListener('click', function() {
    var phone = input.value.trim();
    if (!phone) { msg.textContent = '請輸入電話'; return; }
    msg.textContent = '送出中…';
    fetch('/vetting/whitelist/add', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: JSON.stringify({phone: phone})
    }).then(function(r) { return r.text().then(function(t) {
      var d = null; try { d = JSON.parse(t); } catch (e) {}
      return [r.status, d];
    }); }).then(function(arr) {
      var st = arr[0], d = arr[1];
      if (st === 200 && d && d.status === 'ok') { msg.textContent = '✓ 已加入白名單'; setTimeout(closeM, 800); }
      else if (st === 200 && d && d.status === 'exists') { msg.textContent = '此號碼已在白名單'; }
      else if (st === 400) { msg.textContent = '❌ 電話格式錯誤'; }
      else { msg.textContent = '❌ ' + ((d && d.message) || '新增失敗'); }
    }).catch(function(err) { msg.textContent = '❌ 連線失敗：' + err.message; });
  });
})();
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vetting_whitelist_index_wiring.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add templates/vetting/index.html tests/test_vetting_whitelist_index_wiring.py
git commit -m "feat(vetting): 人員查詢頁新增白名單鈕/modal + D段白名單標記"
```

---

### Task 7: Root admin 管理端點（list / add / update / delete）

**Files:**
- Modify: `admin/routes.py`（新增 4 個 route；頂部已 import `require_root_admin` 用法見既有 `features_*`）
- Test: `tests/test_vetting_whitelist_admin_routes.py`

**Interfaces:**
- Consumes: `vetting.whitelist`、`features.require_root_admin`。
- Produces:
  - `GET    /admin/vetting/whitelist` → `{entries:[{id,phone,created_by_name,created_at}]}`
  - `POST   /admin/vetting/whitelist` body `{phone}` → `{status:'ok'|'exists'}` / 400
  - `POST   /admin/vetting/whitelist/<int:eid>` body `{phone}` → `{status:'ok'|'exists'|'not_found'}` / 400
  - `DELETE /admin/vetting/whitelist/<int:eid>` → `{status:'ok'|'not_found'}`
  - 全部非 root admin → 404。

- [ ] **Step 1: Write the failing test**

`tests/test_vetting_whitelist_admin_routes.py`：
```python
"""Root admin 白名單管理端點。"""
import uuid
from models import User
from extensions import db
from vetting import whitelist


def _mk(db_session, root_admin=False):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        return existing
    u = User(username=name, role="user", store="B", is_active=True)
    u.set_password("1234")
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_list_requires_root_admin(app, client, db_session):
    u = _mk(db_session)            # 一般 user
    _login(client, u)
    assert client.get("/admin/vetting/whitelist").status_code == 404


def test_root_admin_list(app, client, db_session):
    whitelist.add("0912345678", user_name="alice")
    u = _mk(db_session, root_admin=True); _login(client, u)
    res = client.get("/admin/vetting/whitelist")
    assert res.status_code == 200
    phones = [e["phone"] for e in res.get_json()["entries"]]
    assert "0912345678" in phones


def test_root_admin_add(app, client, db_session):
    u = _mk(db_session, root_admin=True); _login(client, u)
    res = client.post("/admin/vetting/whitelist", json={"phone": "0922-333-444"})
    assert res.status_code == 200 and res.get_json()["status"] == "ok"
    assert whitelist.is_whitelisted("0922333444") is True


def test_root_admin_add_invalid(app, client, db_session):
    u = _mk(db_session, root_admin=True); _login(client, u)
    assert client.post("/admin/vetting/whitelist", json={"phone": "x"}).status_code == 400


def test_root_admin_update(app, client, db_session):
    whitelist.add("0955666777")
    eid = whitelist.list_all()[0]["id"]
    u = _mk(db_session, root_admin=True); _login(client, u)
    res = client.post(f"/admin/vetting/whitelist/{eid}", json={"phone": "0955-666-888"})
    assert res.status_code == 200 and res.get_json()["status"] == "ok"
    assert whitelist.is_whitelisted("0955666888") is True


def test_root_admin_delete(app, client, db_session):
    whitelist.add("0999888777")
    eid = whitelist.list_all()[0]["id"]
    u = _mk(db_session, root_admin=True); _login(client, u)
    res = client.delete(f"/admin/vetting/whitelist/{eid}")
    assert res.status_code == 200 and res.get_json()["status"] == "ok"
    assert whitelist.is_whitelisted("0999888777") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vetting_whitelist_admin_routes.py -v`
Expected: FAIL（route 不存在）

- [ ] **Step 3: Add the routes**

`admin/routes.py`，在 `set_user_features` 之後（`# ── 設備管理` 註解之前）新增：
```python
# ── 電話白名單管理（僅種子 admin username="admin" 可用） ──────
@admin_bp.route("/vetting/whitelist", methods=["GET"])
@login_required
def vetting_whitelist_list():
    from features import require_root_admin
    require_root_admin()
    from vetting import whitelist
    return jsonify({"entries": whitelist.list_all()})


@admin_bp.route("/vetting/whitelist", methods=["POST"])
@login_required
def vetting_whitelist_create():
    from features import require_root_admin
    require_root_admin()
    from vetting import whitelist
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    status = whitelist.add(phone, user_id=current_user.id,
                           user_name=current_user.username)
    if status == "invalid":
        return jsonify({"status": "invalid", "message": "電話格式錯誤"}), 400
    return jsonify({"status": status}), 200


@admin_bp.route("/vetting/whitelist/<int:eid>", methods=["POST"])
@login_required
def vetting_whitelist_update(eid):
    from features import require_root_admin
    require_root_admin()
    from vetting import whitelist
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    status = whitelist.update(eid, phone)
    if status == "invalid":
        return jsonify({"status": "invalid", "message": "電話格式錯誤"}), 400
    return jsonify({"status": status}), 200


@admin_bp.route("/vetting/whitelist/<int:eid>", methods=["DELETE"])
@login_required
def vetting_whitelist_delete(eid):
    from features import require_root_admin
    require_root_admin()
    from vetting import whitelist
    ok = whitelist.delete(eid)
    return jsonify({"status": "ok" if ok else "not_found"}), 200
```
（`request`、`jsonify`、`current_user`、`login_required` 已在 `admin/routes.py` 頂部 import——比照既有 `set_user_features`。）

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vetting_whitelist_admin_routes.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add admin/routes.py tests/test_vetting_whitelist_admin_routes.py
git commit -m "feat(admin): 電話白名單管理端點(list/add/update/delete, root only)"
```

---

### Task 8: 後台管理 UI + 模組權限管理依店別分組

**Files:**
- Modify: `admin/routes.py::features_user_list()`（排序改 store+username）
- Modify: `templates/admin/dashboard.html`（白名單管理 card + 使用者下拉 `<optgroup>` 分組）
- Test: `tests/test_features_user_list_sort.py`、`tests/test_admin_dashboard_whitelist_wiring.py`

**Interfaces:**
- Consumes: `GET/POST/DELETE /admin/vetting/whitelist`（Task 7）。

- [ ] **Step 1: Write the failing tests**

`tests/test_features_user_list_sort.py`：
```python
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
    for uname, store in [("zoe", "B"), ("amy", "A"), ("bob", "A")]:
        u = User(username=uname, role="user", store=store, is_active=True)
        u.set_password("1234"); db_session.add(u)
    db_session.commit()
    root = _mk_root(db_session); _login(client, root)
    data = client.get("/admin/features/users").get_json()
    stores = [u["store"] for u in data if u["store"] in ("A", "B")]
    # A 店全部排在 B 店之前
    assert stores == sorted(stores)
```

`tests/test_admin_dashboard_whitelist_wiring.py`：
```python
"""後台 dashboard：root admin 看得到白名單管理 card 與 optgroup 分組。"""
import uuid
from models import User
from extensions import db


def _mk(db_session, root_admin=False):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        return existing
    u = User(username=name, role=("user" if not root_admin else "user"),
             store="B", is_active=True)
    u.set_password("1234"); db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_whitelist_card_shown_to_root(app, client, db_session):
    u = _mk(db_session, root_admin=True); _login(client, u)
    body = client.get("/admin/dashboard").get_data(as_text=True)
    assert "電話白名單" in body
    assert "/admin/vetting/whitelist" in body


def test_optgroup_grouping_present(app, client, db_session):
    u = _mk(db_session, root_admin=True); _login(client, u)
    body = client.get("/admin/dashboard").get_data(as_text=True)
    assert "optgroup" in body                # 使用者下拉依店別分組
```
（已確認 dashboard route 為 `@admin_bp.route("/dashboard")` → 路徑 `/admin/dashboard`。）

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_features_user_list_sort.py tests/test_admin_dashboard_whitelist_wiring.py -v`
Expected: FAIL（排序未改、card/optgroup 未加）

- [ ] **Step 3: Backend sort by store**

`admin/routes.py::features_user_list()`，把：
```python
    users = User.query.order_by(User.username).all()
```
改為：
```python
    users = User.query.order_by(User.store, User.username).all()
```

- [ ] **Step 4: Dashboard — 白名單管理 card**

`templates/admin/dashboard.html`，在模組權限管理 card 結束 `{% endif %}`（line 315，`</div>` 之後的 `{% endif %}`）之後插入：
```html
{% if current_user.is_root_admin() %}
<div class="card" style="margin-top:1rem;">
  <h3>📞 電話白名單</h3>
  <p style="font-size:.82rem;color:#777;margin-top:.4rem;">
    人員查詢電話比對用。命中即在結果顯示「✓ 白名單」。僅你（種子管理員）能看清單與改/刪。
  </p>
  <div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-top:.6rem;">
    <input type="tel" id="wl-admin-phone" placeholder="例：0912-345-678"
           style="font-size:.9rem;padding:.4rem .6rem;border-radius:6px;border:1px solid #ddd;max-width:200px;">
    <button class="btn btn-primary" onclick="wlAdminAdd()" style="padding:.4rem .8rem;">新增</button>
    <span id="wl-admin-msg" style="font-size:.85rem;color:#666;"></span>
  </div>
  <div id="wl-admin-list" style="margin-top:.75rem;"></div>
</div>
{% endif %}
```

- [ ] **Step 5: Dashboard — 白名單管理 JS + optgroup 分組**

(a) `templates/admin/dashboard.html`，在 `{% block scripts %}` 的 `<script>` 區塊內（與其他 dashboard JS 並列，例如 `_loadFeatureUserList` 附近）加白名單管理 JS：
```javascript
// ── 電話白名單管理（root admin） ──
function wlAdminRender(entries) {
  var box = document.getElementById('wl-admin-list');
  if (!box) return;
  if (!entries.length) { box.innerHTML = '<div style="color:#999;font-size:.85rem;">目前沒有白名單號碼。</div>'; return; }
  var html = '<table style="width:100%;border-collapse:collapse;font-size:.88rem;">';
  html += '<tr style="text-align:left;color:#666;"><th style="padding:.3rem;">號碼</th><th>新增者</th><th>時間</th><th></th></tr>';
  entries.forEach(function(e) {
    html += '<tr style="border-top:1px solid #eee;">'
      + '<td style="padding:.3rem;font-family:monospace;">' + e.phone + '</td>'
      + '<td>' + (e.created_by_name || '') + '</td>'
      + '<td style="color:#999;">' + (e.created_at || '').slice(0, 10) + '</td>'
      + '<td style="white-space:nowrap;">'
      + '<button class="btn btn-secondary" style="padding:.2rem .5rem;font-size:.8rem;" onclick="wlAdminEdit(' + e.id + ',\'' + e.phone + '\')">改</button> '
      + '<button class="btn btn-secondary" style="padding:.2rem .5rem;font-size:.8rem;" onclick="wlAdminDelete(' + e.id + ')">刪</button>'
      + '</td></tr>';
  });
  html += '</table>';
  box.innerHTML = html;
}
function wlAdminLoad() {
  fetch('/admin/vetting/whitelist').then(function(r){return r.json();}).then(function(d){
    wlAdminRender(d.entries || []);
  });
}
function wlAdminMsg(m) { var el = document.getElementById('wl-admin-msg'); if (el) el.textContent = m || ''; }
function wlAdminAdd() {
  var phone = document.getElementById('wl-admin-phone').value.trim();
  if (!phone) { wlAdminMsg('請輸入電話'); return; }
  fetch('/admin/vetting/whitelist', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({phone: phone})
  }).then(function(r){return r.json();}).then(function(d){
    if (d.status === 'ok') { wlAdminMsg('✓ 已新增'); document.getElementById('wl-admin-phone').value=''; wlAdminLoad(); }
    else if (d.status === 'exists') { wlAdminMsg('此號碼已存在'); }
    else { wlAdminMsg('❌ ' + (d.message || '格式錯誤')); }
  });
}
function wlAdminEdit(id, cur) {
  var v = prompt('修改號碼：', cur);
  if (v === null) return;
  fetch('/admin/vetting/whitelist/' + id, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({phone: v.trim()})
  }).then(function(r){return r.json();}).then(function(d){
    if (d.status === 'ok') { wlAdminMsg('✓ 已修改'); wlAdminLoad(); }
    else if (d.status === 'exists') { wlAdminMsg('此號碼已存在'); }
    else { wlAdminMsg('❌ ' + (d.message || '修改失敗')); }
  });
}
function wlAdminDelete(id) {
  if (!confirm('確定刪除此白名單號碼？')) return;
  fetch('/admin/vetting/whitelist/' + id, {method:'DELETE'})
    .then(function(r){return r.json();}).then(function(){ wlAdminLoad(); });
}
if (document.getElementById('wl-admin-list')) { wlAdminLoad(); }
```

(b) 同檔 `_loadFeatureUserList()`，把扁平 append 改成依 store 分組（`<optgroup>`）：
```javascript
function _loadFeatureUserList() {
  fetch('/admin/features/users').then(function(r){return r.json();}).then(function(users){
    var sel = document.getElementById('feat-user-select');
    var groups = {};           // store -> [users]，保留後端排序
    var order = [];
    users.forEach(function(u) {
      var key = u.store || '（未綁店）';
      if (!groups[key]) { groups[key] = []; order.push(key); }
      groups[key].push(u);
    });
    order.forEach(function(store) {
      var og = document.createElement('optgroup');
      og.label = store;
      groups[store].forEach(function(u) {
        var opt = document.createElement('option');
        opt.value = u.id;
        opt.textContent = u.username;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    });
  });
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_features_user_list_sort.py tests/test_admin_dashboard_whitelist_wiring.py -v`
Expected: PASS（2 passed）

- [ ] **Step 7: Commit**

```bash
git add admin/routes.py templates/admin/dashboard.html tests/test_features_user_list_sort.py tests/test_admin_dashboard_whitelist_wiring.py
git commit -m "feat(admin): 白名單管理UI + 模組權限管理依店別optgroup分組"
```

---

### Task 9: 全套測試 + 收尾

- [ ] **Step 1: Run the full suite**

Run: `pytest tests/`
Expected: 全綠（既有 + 新增約 32 個測試），無 regression。

- [ ] **Step 2: 本機手動驗證（run_local.sh）**

```bash
bash scripts/run_local.sh
```
- 以 root admin（admin）登入：後台「📞 電話白名單」card 新增一支號碼 → 列表出現。
- 「🎛️ 模組權限管理」使用者下拉確認依店別分組。
- 開「🛡️ 人員查詢」，右上角「➕ 新增白名單電話」鈕在（root admin 一定看得到）。
- 用該白名單號碼做一次查詢（填姓名 + 該電話）→ 送出後 D 段顯示綠底「✓ 此號碼已在白名單中（…）」+ 號碼，Whoscall/Google 連結照舊。
- 用非白名單號碼查 → D 段無白名單標記。
- （可另開一般 user、用 /dev 切帳號驗權限：給「人員查詢」但不給「新增白名單電話」→ 查詢頁無新增鈕；後台無白名單 card。）

> dev SQLite 不會自動同步新表 → 第一次跑前若 `vetting_whitelist_phones` 不存在，`db.create_all()` 會建；若用既有 dev.db 仍報無此表，`rm instance/dev.db` 或 `rm dev.db` 後重新 seed（見 feedback_dev_sqlite_alter）。

- [ ] **Step 3: 確認不需 bump SW**

確認本次只動 `templates/`（inline）+ 後端 + DB，未動 `static/`：
Run: `git diff --name-only main... | grep -c '^app_unified/static/'`
Expected: `0`（不需改 `sw.js` CACHE_NAME）

## Self-Review 結果

- **Spec 覆蓋**：①資料模型→T1 ②權限grant_only/預設關/僅root授權→T2 ③新增UI(只能新增)→T6 ④D段命中顯示+列號碼→T5+T6 ⑤root管理list/改/刪→T7+T8 ⑥不寫log→全程無log table/呼叫 ⑦店別排序→T8 ⑧加密→T1(EncryptedText)+T3(解密比對) ⑨不bump SW→T9。皆有對應。
- **Placeholder**：無 TBD/TODO；migration `down_revision` 標註先確認 017 revision id（具體可執行）。
- **型別一致**：`whitelist.add/is_whitelisted/list_all/update/delete` 簽章在 T3 定義，T4/T5/T7 使用一致；status 字串 `'ok'/'exists'/'invalid'/'not_found'` 全程一致；`social.phone.is_whitelisted` 在 T5 產生、T6 消費一致。
