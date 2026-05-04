# 晶片居留證查詢（icinfo）+ 司法院排序 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「晶片居留證查詢」模組（內部 `icinfo`，對外 URL 偽裝為 `/v/aux/c` + `/v/aux/s`），UI 嵌在 vetting 頁面下方；順手 fix 司法院結果按關鍵字命中排序。

**Architecture:**
- 新獨立 blueprint `icinfo/`，`url_prefix='/v/aux'`，與 vetting 共用偽裝主題但程式邏輯完全分離（依 `feedback_module_isolation`）
- 透過 Flask session 暫存 JSESSIONID 做驗證碼接力（同 NPA 套路）：GET captcha 取 cookie 存 session → POST search 帶 cookie 送出
- 新 `IcinfoQueryLog` model + migration 009（table 名偽裝為 `v_aux_logs`），只記 metadata 不存照片
- 沿用 `vetting/http_client.py` 的 `make_session()` + `BROWSER_HEADERS` + `throttle_sleep()`
- HTML/CSS/JS 全部用中性命名（`auxBlock`、`loadAux`、`/v/aux/c`），F12 看不出 `icinfo`/`immigration`/`居留證` 字樣

**Tech Stack:** Flask + Flask-Login + SQLAlchemy + requests + BeautifulSoup（不需要）+ pytest + PostgreSQL

**Spec:** `docs/superpowers/specs/2026-05-04-icinfo-chip-residence-card-design.md`

---

## File Structure

| 檔案 | 動作 | 主要責任 |
|---|---|---|
| `vetting/routes.py:84-110` | Modify | 加 judicial 排序（hit 關鍵字優先） |
| `tests/test_judicial_sort.py` | Create | 排序穩定性測試 |
| `models.py` | Modify | 加 `IcinfoQueryLog` model |
| `migrations/versions/009_v_aux_logs.py` | Create | 建 `v_aux_logs` table + super_admin/admin features backfill |
| `features.py` | Modify | `AVAILABLE_FEATURES` 加 `v_aux` entry |
| `icinfo/__init__.py` | Create | export blueprint |
| `icinfo/errors.py` | Create | `CaptchaExpired` / `UpstreamError` |
| `icinfo/immigration.py` | Create | HTTP wrapper：`fetch_captcha` + `submit_search` |
| `icinfo/routes.py` | Create | `GET /v/aux/c` + `POST /v/aux/s` + features gate + log 寫入 |
| `app.py:~443` | Modify | 註冊 `icinfo_bp` |
| `templates/icinfo/_form.html` | Create | 表單 fragment + IIFE JS（中性命名） |
| `templates/vetting/index.html` | Modify | `{% if user_has_feature %} {% include %}` |
| `tests/test_icinfo_immigration.py` | Create | mock `requests` 驗 fetch_captcha / submit_search |
| `tests/test_icinfo_routes.py` | Create | Flask test client + features gate + session 行為 |

---

### Task 1: 司法院結果按關鍵字命中排序（trivial fix，獨立 commit）

**Files:**
- Modify: `vetting/routes.py:107`（在 id-filter 後加 sort）
- Create: `tests/test_judicial_sort.py`

- [ ] **Step 1: 寫 failing test**

`tests/test_judicial_sort.py`：

```python
"""Test judicial results 按關鍵字命中排序"""

def _judicial_sort_key(case):
    return (
        0 if case.get("keyword_hits") else 1,
        0 if case.get("id_match") else 1,
    )


def test_keyword_hits_first():
    cases = [
        {"id": "A", "keyword_hits": [], "id_match": True},
        {"id": "B", "keyword_hits": ["詐欺"], "id_match": False},
        {"id": "C", "keyword_hits": [], "id_match": None},
        {"id": "D", "keyword_hits": ["毒品", "搶劫"], "id_match": True},
    ]
    cases.sort(key=_judicial_sort_key)
    assert [c["id"] for c in cases] == ["D", "B", "A", "C"]


def test_stable_within_same_bucket():
    cases = [
        {"id": "A", "keyword_hits": ["詐欺"], "id_match": True},
        {"id": "B", "keyword_hits": ["洗錢"], "id_match": True},
    ]
    cases.sort(key=_judicial_sort_key)
    assert [c["id"] for c in cases] == ["A", "B"]


def test_empty_keyword_hits_means_no_hit():
    cases = [
        {"id": "A", "keyword_hits": [], "id_match": True},
        {"id": "B", "keyword_hits": ["詐欺"], "id_match": False},
    ]
    cases.sort(key=_judicial_sort_key)
    assert [c["id"] for c in cases] == ["B", "A"]
```

- [ ] **Step 2: Run test，確認失敗**

```bash
cd /home/hirain0126/projects/webapp/app_unified
pytest tests/test_judicial_sort.py -v
```

預期：`ImportError` 或函式找不到（因為 `_judicial_sort_key` 還沒在 routes.py）。test 自帶函式所以其實會 PASS — 這時改成 import 版本：

```python
from vetting.routes import _judicial_sort_key
```

再跑就會 FAIL with `ImportError`。

- [ ] **Step 3: 在 `vetting/routes.py` 加 helper + sort call**

`vetting/routes.py` 在檔案頂端 import 區塊後（約 line 16 之後）加：

```python
def _judicial_sort_key(case):
    """sort key：keyword_hits 優先，id_match True 次優先。"""
    return (
        0 if case.get("keyword_hits") else 1,
        0 if case.get("id_match") else 1,
    )
```

然後在 line 107 後（id_match filter 完）加：

```python
        # 排序：命中關鍵字的案件排前面（admin 第一眼看到重點）
        judicial_results.sort(key=_judicial_sort_key)
```

- [ ] **Step 4: Run test，確認通過**

```bash
pytest tests/test_judicial_sort.py -v
```

預期：3 個 case 全 PASS。

- [ ] **Step 5: 跑全套既有 vetting 測試確認沒打到回歸**

```bash
pytest tests/ -v -k "vetting or judicial" 2>&1 | tail -20
```

預期：全部 PASS（或維持原本的 stub error 但沒新增 fail）。

- [ ] **Step 6: Commit**

```bash
git add vetting/routes.py tests/test_judicial_sort.py
git commit -m "$(cat <<'EOF'
feat(vetting/judicial): sort cases with keyword hits first

Cases hitting deep keywords (詐欺/洗錢/有期徒刑/定讞/毒品/搶劫/偷竊)
排到列表最前面，admin 第一眼看到重點。次優先按 id_match True。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Model `IcinfoQueryLog` + Migration 009

**Files:**
- Modify: `models.py`（加 `IcinfoQueryLog` class，放在最後一個 model 之後）
- Create: `migrations/versions/009_v_aux_logs.py`

- [ ] **Step 1: 在 `models.py` 加 `IcinfoQueryLog` model**

加到 `models.py` 檔案最後（最後一個 model 之後）：

```python
class IcinfoQueryLog(db.Model):
    """晶片居留證查詢 log（只記 metadata，不存照片）。

    Table 名 v_aux_logs 為對外偽裝（共用 vetting 主題）。
    """
    __tablename__ = "v_aux_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    queried_uino = db.Column(db.String(10), nullable=False, index=True)
    matched = db.Column(db.Boolean, nullable=False)
    queried_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        index=True,
    )
```

- [ ] **Step 2: 建 migration 009**

`migrations/versions/009_v_aux_logs.py`：

```python
"""Add v_aux_logs table + backfill super_admin/admin features

Revision ID: 009
Revises: 008
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa
import json


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    # 1. 建 table
    op.create_table(
        "v_aux_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("queried_uino", sa.String(length=10), nullable=False),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column(
            "queried_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_v_aux_logs_user_id", "v_aux_logs", ["user_id"])
    op.create_index("ix_v_aux_logs_queried_uino", "v_aux_logs", ["queried_uino"])
    op.create_index("ix_v_aux_logs_queried_at", "v_aux_logs", ["queried_at"])

    # 2. Backfill features：super_admin / admin 自動加 v_aux
    # users.features 是 TEXT (JSON-encoded list)，需要逐筆 parse
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, features FROM users WHERE role IN ('super_admin', 'admin')")
    ).fetchall()
    for row in rows:
        try:
            current = json.loads(row.features or "[]")
            if not isinstance(current, list):
                current = []
        except Exception:
            current = []
        if "v_aux" not in current:
            current.append("v_aux")
            new_features = json.dumps(current)
            bind.execute(
                sa.text("UPDATE users SET features = :f WHERE id = :uid"),
                {"f": new_features, "uid": row.id},
            )


def downgrade():
    # 移除 features 中的 v_aux
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, features FROM users")).fetchall()
    for row in rows:
        try:
            current = json.loads(row.features or "[]")
            if not isinstance(current, list):
                continue
        except Exception:
            continue
        if "v_aux" in current:
            current.remove("v_aux")
            bind.execute(
                sa.text("UPDATE users SET features = :f WHERE id = :uid"),
                {"f": json.dumps(current), "uid": row.id},
            )

    op.drop_index("ix_v_aux_logs_queried_at", "v_aux_logs")
    op.drop_index("ix_v_aux_logs_queried_uino", "v_aux_logs")
    op.drop_index("ix_v_aux_logs_user_id", "v_aux_logs")
    op.drop_table("v_aux_logs")
```

- [ ] **Step 3: 本機跑 migration 確認 schema 正確**

本機若有 dev DB：
```bash
flask db upgrade
```

或用 SQLite 跑 test：
```bash
pytest tests/ -v -k "test_icinfo" 2>&1 | head -10
```

預期：建表成功、無錯誤。如果本機沒 DB 就跳過，等 Task 5 routes test 用 in-memory SQLite 驗證。

- [ ] **Step 4: Commit**

```bash
git add models.py migrations/versions/009_v_aux_logs.py
git commit -m "$(cat <<'EOF'
feat(icinfo): add IcinfoQueryLog model + migration 009

新 table v_aux_logs（偽裝名）只記 metadata（user_id / queried_uino /
matched / queried_at），照片不落地。Migration 同時把 super_admin /
admin 的 features 補上 v_aux。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: features.py 加 `v_aux` entry

**Files:**
- Modify: `features.py:13-33`（`AVAILABLE_FEATURES` list）

- [ ] **Step 1: 加新 entry**

`features.py` 在 `AVAILABLE_FEATURES` list 最後加：

```python
    {
        "key": "v_aux",
        "label": "晶片居留證",
        "emoji": "💳",
        "url": "/v/",  # UI 嵌在 vetting 頁面，沒有獨立 URL
        "enabled": True,
    },
```

注意：`url="/v/"` 是給 dashboard 顯示「點此進入」的連結用，因為 icinfo 沒有獨立首頁，它只是 vetting 頁的一個 section，所以連結 = vetting 首頁。

- [ ] **Step 2: 確認沒打到既有測試**

```bash
pytest tests/ -v 2>&1 | tail -10
```

預期：無新增 fail。

- [ ] **Step 3: Commit（先不 commit，跟 Task 4 合併）**

跳過 — 這個 entry 在沒有 routes 之前 admin 勾了也沒功能，留到 Task 5 一起 commit。

---

### Task 4: HTTP wrapper `icinfo/immigration.py`

**Files:**
- Create: `icinfo/__init__.py`
- Create: `icinfo/errors.py`
- Create: `icinfo/immigration.py`
- Create: `tests/test_icinfo_immigration.py`

- [ ] **Step 1: 建 `icinfo/__init__.py`**（暫時為空，等 Task 5 export blueprint）

```python
# icinfo blueprint — 晶片居留證查詢（對外 URL 偽裝為 /v/aux/）
```

- [ ] **Step 2: 建 `icinfo/errors.py`**

```python
"""icinfo 模組自訂例外。"""


class IcinfoError(Exception):
    """icinfo 模組基礎例外。"""


class CaptchaExpired(IcinfoError):
    """Session 中沒 JSESSIONID（使用者沒先 GET captcha 就 POST search，或 session 過期）。"""


class UpstreamError(IcinfoError):
    """上游移民署 API 錯誤（5xx、timeout、格式異常）。"""
```

- [ ] **Step 3: 寫 failing test `tests/test_icinfo_immigration.py`**

```python
"""Test icinfo/immigration.py — HTTP wrapper for 移民署 API."""
from unittest.mock import patch, MagicMock
import pytest

from icinfo.immigration import fetch_captcha, submit_search
from icinfo.errors import UpstreamError


def test_fetch_captcha_returns_jpeg_and_jsessionid():
    """GET /api/captcha 回 JPEG + Set-Cookie，wrapper 應抽出 (bytes, jsessionid)."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b"\xff\xd8\xff\xe0FAKE_JPEG"
    fake_resp.cookies = {"JSESSIONID": "ABC123"}
    fake_resp.raise_for_status = MagicMock()

    with patch("icinfo.immigration.requests.get", return_value=fake_resp) as mock_get:
        jpeg, jsessionid = fetch_captcha()

    assert jpeg.startswith(b"\xff\xd8")
    assert jsessionid == "ABC123"
    # 驗證有打到正確 URL
    args, kwargs = mock_get.call_args
    assert "niaicinfo.immigration.gov.tw" in args[0]
    assert "/api/captcha" in args[0]


def test_fetch_captcha_no_cookie_raises():
    """上游沒回 JSESSIONID 視為 UpstreamError。"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b"\xff\xd8FAKE"
    fake_resp.cookies = {}
    fake_resp.raise_for_status = MagicMock()

    with patch("icinfo.immigration.requests.get", return_value=fake_resp):
        with pytest.raises(UpstreamError):
            fetch_captcha()


def test_submit_search_success():
    """rcode='SUCCESS' 應回 (matched=True, image=base64, search_time)。"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "rcode": "SUCCESS",
        "data": {"searchTime": "2026-05-04 12:00:00", "image": "BASE64IMG"},
    }
    fake_resp.raise_for_status = MagicMock()

    payload = {
        "uino": "AB12345678",
        "issueDate": "2024-01-15",
        "expiryDate": "2026-01-15",
        "serialNo": "1234567890",
        "captcha": "abcd",
    }
    with patch("icinfo.immigration.requests.post", return_value=fake_resp) as mock_post:
        result = submit_search(payload, jsessionid="ABC123")

    assert result["matched"] is True
    assert result["image"] == "BASE64IMG"
    assert result["search_time"] == "2026-05-04 12:00:00"
    # 確認帶了 cookie
    _, kwargs = mock_post.call_args
    assert kwargs["cookies"]["JSESSIONID"] == "ABC123"


def test_submit_search_no_match():
    """rcode != SUCCESS 應回 matched=False, image=None。"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"rcode": "ERR", "data": {}}
    fake_resp.raise_for_status = MagicMock()

    with patch("icinfo.immigration.requests.post", return_value=fake_resp):
        result = submit_search(
            {"uino": "X", "issueDate": "", "expiryDate": "", "serialNo": "", "captcha": ""},
            jsessionid="ABC",
        )

    assert result["matched"] is False
    assert result["image"] is None


def test_submit_search_upstream_5xx_raises():
    """上游 5xx 應 raise UpstreamError。"""
    import requests as req_lib

    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.raise_for_status = MagicMock(
        side_effect=req_lib.HTTPError("500 server error")
    )

    with patch("icinfo.immigration.requests.post", return_value=fake_resp):
        with pytest.raises(UpstreamError):
            submit_search(
                {"uino": "X", "issueDate": "", "expiryDate": "", "serialNo": "", "captcha": ""},
                jsessionid="ABC",
            )


def test_submit_search_invalid_json_raises():
    """上游回非 JSON 應 raise UpstreamError。"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.side_effect = ValueError("not json")
    fake_resp.raise_for_status = MagicMock()

    with patch("icinfo.immigration.requests.post", return_value=fake_resp):
        with pytest.raises(UpstreamError):
            submit_search(
                {"uino": "X", "issueDate": "", "expiryDate": "", "serialNo": "", "captcha": ""},
                jsessionid="ABC",
            )
```

- [ ] **Step 4: Run test，確認失敗**

```bash
pytest tests/test_icinfo_immigration.py -v
```

預期：`ImportError: cannot import 'fetch_captcha' from 'icinfo.immigration'`

- [ ] **Step 5: 寫 `icinfo/immigration.py` 實作**

```python
"""HTTP wrapper for 移民署晶片居留證查詢 API。

對外 URL 偽裝在 /v/aux/，但內部模組保留 icinfo 命名（不洩漏給使用者）。
"""
import time
import requests

from vetting.http_client import BROWSER_HEADERS, throttle_sleep
from icinfo.errors import UpstreamError


_BASE = "https://niaicinfo.immigration.gov.tw/icinfo-frontend"
_CAPTCHA_URL = f"{_BASE}/api/captcha"
_SEARCH_URL = f"{_BASE}/api/search"
_TIMEOUT = 15


def fetch_captcha() -> tuple[bytes, str]:
    """打移民署 /api/captcha，回 (jpeg_bytes, jsessionid)。"""
    ts = int(time.time() * 1000)
    url = f"{_CAPTCHA_URL}?{ts}"
    headers = {**BROWSER_HEADERS, "Referer": f"{_BASE}/zh"}
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UpstreamError(f"captcha fetch failed: {e}")

    jsessionid = resp.cookies.get("JSESSIONID")
    if not jsessionid:
        raise UpstreamError("captcha response missing JSESSIONID")

    return resp.content, jsessionid


def submit_search(payload: dict, jsessionid: str) -> dict:
    """送查詢，回 dict。

    payload: {uino, issueDate, expiryDate, serialNo, captcha}
    回傳：{matched: bool, image: str|None, search_time: str|None}
    """
    throttle_sleep()  # 模擬人手節流
    headers = {
        **BROWSER_HEADERS,
        "Content-Type": "application/json",
        "Referer": f"{_BASE}/zh",
    }
    try:
        resp = requests.post(
            _SEARCH_URL,
            json=payload,
            cookies={"JSESSIONID": jsessionid},
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UpstreamError(f"search failed: {e}")

    try:
        data = resp.json()
    except ValueError as e:
        raise UpstreamError(f"search response not json: {e}")

    rcode = data.get("rcode")
    body = data.get("data") or {}
    matched = rcode == "SUCCESS"
    return {
        "matched": matched,
        "image": body.get("image") if matched else None,
        "search_time": body.get("searchTime"),
        "rcode": rcode,  # 給 log debug 用
    }
```

- [ ] **Step 6: Run test，確認 6 個 case 全過**

```bash
pytest tests/test_icinfo_immigration.py -v
```

預期：6 PASS。

- [ ] **Step 7: Commit**

```bash
git add icinfo/__init__.py icinfo/errors.py icinfo/immigration.py tests/test_icinfo_immigration.py
git commit -m "$(cat <<'EOF'
feat(icinfo): HTTP wrapper for 移民署 captcha + search API

icinfo/immigration.py 包兩個 endpoint，沿用 vetting 的
BROWSER_HEADERS 與 throttle_sleep。失敗統一 raise UpstreamError。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Routes `GET /v/aux/c` + `POST /v/aux/s` + 權限 gate + log

**Files:**
- Create: `icinfo/routes.py`
- Modify: `icinfo/__init__.py`（export blueprint）
- Modify: `app.py`（註冊 blueprint）
- Modify: `features.py`（Task 3 的 entry，這次一起 commit）
- Create: `tests/test_icinfo_routes.py`

- [ ] **Step 1: 寫 failing test `tests/test_icinfo_routes.py`**

```python
"""Test icinfo routes — features gate + session 行為 + log 寫入。"""
from unittest.mock import patch
import json
import pytest


def _login_user(client, app, *, role="user", features=None):
    """建一個 user 並登入。"""
    from models import db, User
    if features is None:
        features = ["notes"]
    with app.app_context():
        u = User(
            username=f"u_{role}",
            password_hash="x",
            role=role,
            features=json.dumps(features),
        )
        db.session.add(u)
        db.session.commit()
        uid = u.id

    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


def test_user_without_v_aux_feature_gets_404(client, app):
    """普通 user 沒 v_aux feature 打 /v/aux/c 應 404。"""
    _login_user(client, app, role="user", features=["notes"])
    r = client.get("/v/aux/c")
    assert r.status_code == 404


def test_admin_with_v_aux_can_get_captcha(client, app):
    """admin 有 v_aux feature 打 /v/aux/c 應拿到 base64 JPEG。"""
    _login_user(client, app, role="admin", features=["notes", "v_aux"])

    with patch("icinfo.routes.fetch_captcha", return_value=(b"\xff\xd8\xff\xe0JPEG", "JS123")):
        r = client.get("/v/aux/c")

    assert r.status_code == 200
    body = r.get_json()
    assert "image" in body and len(body["image"]) > 0
    # 驗證 session 有存 jsessionid
    with client.session_transaction() as s:
        assert s.get("icinfo_jsessionid") == "JS123"


def test_search_without_session_jsessionid_returns_captcha_expired(client, app):
    """先 POST 不先 GET captcha → captcha_expired。"""
    _login_user(client, app, role="admin", features=["v_aux"])
    r = client.post("/v/aux/s", json={
        "uino": "A12345678", "issueDate": "2024-01-01",
        "expiryDate": "2026-01-01", "serialNo": "1234567890", "captcha": "abcd",
    })
    assert r.status_code == 200
    assert r.get_json()["error"] == "captcha_expired"


def test_search_success_returns_image_and_writes_log(client, app):
    """SUCCESS 回 matched=True + image，並寫入 log。"""
    uid = _login_user(client, app, role="admin", features=["v_aux"])
    # 模擬先 GET captcha
    with client.session_transaction() as s:
        s["icinfo_jsessionid"] = "JS123"

    fake = {"matched": True, "image": "BASE64IMG", "search_time": "2026-05-04 12:00", "rcode": "SUCCESS"}
    with patch("icinfo.routes.submit_search", return_value=fake):
        r = client.post("/v/aux/s", json={
            "uino": "A12345678", "issueDate": "2024-01-01",
            "expiryDate": "2026-01-01", "serialNo": "1234567890", "captcha": "abcd",
        })

    assert r.status_code == 200
    body = r.get_json()
    assert body["matched"] is True
    assert body["image"] == "BASE64IMG"

    # 驗證 log 寫入
    from models import db, IcinfoQueryLog
    with app.app_context():
        logs = IcinfoQueryLog.query.filter_by(user_id=uid).all()
        assert len(logs) == 1
        assert logs[0].queried_uino == "A12345678"
        assert logs[0].matched is True

    # 驗證 jsessionid 已清空（驗證碼一次性）
    with client.session_transaction() as s:
        assert s.get("icinfo_jsessionid") is None


def test_search_no_match_writes_log_with_matched_false(client, app):
    uid = _login_user(client, app, role="admin", features=["v_aux"])
    with client.session_transaction() as s:
        s["icinfo_jsessionid"] = "JS123"

    fake = {"matched": False, "image": None, "search_time": None, "rcode": "FAIL"}
    with patch("icinfo.routes.submit_search", return_value=fake):
        r = client.post("/v/aux/s", json={
            "uino": "B98765432", "issueDate": "", "expiryDate": "",
            "serialNo": "9876543210", "captcha": "wrong",
        })

    assert r.status_code == 200
    body = r.get_json()
    assert body["matched"] is False
    assert body["image"] is None

    from models import db, IcinfoQueryLog
    with app.app_context():
        log = IcinfoQueryLog.query.filter_by(user_id=uid).first()
        assert log.matched is False
        assert log.queried_uino == "B98765432"


def test_upstream_error_returns_upstream_error_field(client, app):
    """上游 timeout/5xx 應回 error=upstream，不寫 log。"""
    uid = _login_user(client, app, role="admin", features=["v_aux"])
    with client.session_transaction() as s:
        s["icinfo_jsessionid"] = "JS123"

    from icinfo.errors import UpstreamError
    with patch("icinfo.routes.submit_search", side_effect=UpstreamError("timeout")):
        r = client.post("/v/aux/s", json={
            "uino": "C11111111", "issueDate": "", "expiryDate": "",
            "serialNo": "1", "captcha": "x",
        })

    assert r.status_code == 200
    body = r.get_json()
    assert body.get("error") == "upstream"

    # log 不寫（上游錯誤不算一次有效查詢）
    from models import IcinfoQueryLog
    with app.app_context():
        assert IcinfoQueryLog.query.filter_by(user_id=uid).count() == 0
```

注意：`client` 和 `app` fixture 必須來自 `tests/conftest.py`（既有的）。如果 conftest 沒提供，先看 `tests/conftest.py` 是怎麼設置的，照抄。

- [ ] **Step 2: Run test，確認失敗**

```bash
pytest tests/test_icinfo_routes.py -v
```

預期：`ImportError` 或 404 全部（因為 blueprint 還沒註冊）。

- [ ] **Step 3: 寫 `icinfo/routes.py`**

```python
"""icinfo blueprint routes — 對外偽裝 /v/aux/c + /v/aux/s。

內部模組仍叫 icinfo（程式碼層）；只有對外 URL、HTML class、JS 變數
全部用中性命名（auxXxx）避免 F12 偵察。
"""
import base64
import logging

from flask import Blueprint, request, jsonify, session, abort
from flask_login import login_required, current_user

from features import user_has_feature
from models import db, IcinfoQueryLog
from icinfo.immigration import fetch_captcha, submit_search
from icinfo.errors import UpstreamError, CaptchaExpired

logger = logging.getLogger(__name__)
icinfo_bp = Blueprint("icinfo", __name__, url_prefix="/v/aux")

_SESSION_KEY = "icinfo_jsessionid"


@icinfo_bp.before_request
def _gate():
    if not current_user.is_authenticated:
        abort(404)
    if not user_has_feature(current_user, "v_aux"):
        abort(404)


@icinfo_bp.route("/c", methods=["GET"])
@login_required
def get_captcha():
    """GET /v/aux/c — 取驗證碼，存 JSESSIONID 到 Flask session。"""
    try:
        jpeg, jsessionid = fetch_captcha()
    except UpstreamError as e:
        logger.warning("icinfo captcha upstream error: %s", e)
        return jsonify({"error": "upstream"}), 200

    session[_SESSION_KEY] = jsessionid
    return jsonify({"image": base64.b64encode(jpeg).decode("ascii")})


@icinfo_bp.route("/s", methods=["POST"])
@login_required
def post_search():
    """POST /v/aux/s — 帶 session 中的 jsessionid + 4 欄位 + captcha 送查詢。"""
    jsessionid = session.get(_SESSION_KEY)
    if not jsessionid:
        return jsonify({"error": "captcha_expired"}), 200

    payload = request.get_json(silent=True) or {}
    uino = (payload.get("uino") or "").strip()
    if not uino:
        return jsonify({"error": "invalid_input"}), 200

    safe_payload = {
        "uino": uino,
        "issueDate": payload.get("issueDate") or "",
        "expiryDate": payload.get("expiryDate") or "",
        "serialNo": (payload.get("serialNo") or "").strip(),
        "captcha": (payload.get("captcha") or "").strip(),
    }

    try:
        result = submit_search(safe_payload, jsessionid=jsessionid)
    except UpstreamError as e:
        logger.warning("icinfo search upstream error: %s", e)
        # 不寫 log（上游失敗不算一次有效查詢）
        # 一律清掉 captcha session（驗證碼可能已被消耗）
        session.pop(_SESSION_KEY, None)
        return jsonify({"error": "upstream"}), 200
    finally:
        # 即使例外也清；正常情境也清（驗證碼一次性）
        session.pop(_SESSION_KEY, None)

    # 寫 log（只記 metadata）
    log = IcinfoQueryLog(
        user_id=current_user.id,
        queried_uino=uino[:10],
        matched=result["matched"],
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        "matched": result["matched"],
        "image": result["image"],
        "search_time": result["search_time"],
    })
```

- [ ] **Step 4: 修改 `icinfo/__init__.py` export blueprint**

```python
"""icinfo blueprint — 晶片居留證查詢（對外 URL 偽裝為 /v/aux/）。"""
from icinfo.routes import icinfo_bp

__all__ = ["icinfo_bp"]
```

- [ ] **Step 5: 在 `app.py` 註冊 blueprint**

找到 `app.py:443` 附近 `app.register_blueprint(vetting_bp)`，下一行加：

```python
    from icinfo import icinfo_bp
    app.register_blueprint(icinfo_bp)
```

- [ ] **Step 6: features.py 加 entry（合併 Task 3）**

`features.py` `AVAILABLE_FEATURES` list 加：

```python
    {
        "key": "v_aux",
        "label": "晶片居留證",
        "emoji": "💳",
        "url": "/v/",
        "enabled": True,
    },
```

- [ ] **Step 7: Run test，確認通過**

```bash
pytest tests/test_icinfo_routes.py -v
```

預期：6 PASS。

如果 conftest 沒有 `client`/`app` fixture，先看一下 `tests/conftest.py` 用既有測試的 pattern：

```bash
grep -nE "def app|def client" tests/conftest.py
```

照抄方法。

- [ ] **Step 8: Commit**

```bash
git add icinfo/__init__.py icinfo/routes.py app.py features.py tests/test_icinfo_routes.py
git commit -m "$(cat <<'EOF'
feat(icinfo): routes /v/aux/c + /v/aux/s with feature gate + log

- GET /v/aux/c：fetch captcha → JSESSIONID 存 Flask session
- POST /v/aux/s：帶 session cookie 送查詢 → 寫 IcinfoQueryLog
- features.py 加 v_aux entry，blueprint url_prefix=/v/aux 偽裝
- 驗證碼一次性（成功失敗都清 session）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Templates `_form.html` + JS（中性命名） + vetting/index.html include

**Files:**
- Create: `templates/icinfo/_form.html`
- Modify: `templates/vetting/index.html`（在三站結果區下方加 include）

- [ ] **Step 1: 建 `templates/icinfo/_form.html`**

```html
<!--
  icinfo 表單 fragment（include 自 vetting/index.html）
  對外 class/id/JS 全部用中性命名（auxXxx），不洩漏 icinfo/immigration 字樣。
-->
<section class="auxBlock" style="margin-top:32px;border-top:1px solid #ccc;padding-top:24px">
  <h3>💳 晶片居留證查詢</h3>
  <form id="auxForm" autocomplete="off" onsubmit="return false">
    <div class="auxRow">
      <label>統一證號
        <input type="text" name="uino" id="auxUino" maxlength="10" required>
      </label>
    </div>
    <div class="auxRow">
      <label>核發日期 / 發卡日期
        <input type="date" name="issueDate" id="auxIssueDate" required>
      </label>
    </div>
    <div class="auxRow">
      <label>居留期限（永居證可空）
        <input type="date" name="expiryDate" id="auxExpiryDate">
      </label>
    </div>
    <div class="auxRow">
      <label>卡片編號（請翻證件背面）
        <input type="text" name="serialNo" id="auxSerial" maxlength="10" required>
      </label>
    </div>
    <div class="auxRow">
      <label>驗證碼
        <input type="text" name="captcha" id="auxCaptcha" required>
      </label>
      <img id="auxCaptchaImg" alt="驗證碼" style="vertical-align:middle;cursor:pointer" title="點擊重新整理">
      <button type="button" id="auxRefresh">重新整理</button>
    </div>
    <div class="auxRow">
      <button type="submit" id="auxSubmit">查詢</button>
    </div>
  </form>

  <div id="auxResult" style="margin-top:16px;min-height:200px">
    <p style="color:#888">相片僅供本證查詢使用</p>
  </div>
</section>

<script>
(function() {
  const form = document.getElementById('auxForm');
  const captchaImg = document.getElementById('auxCaptchaImg');
  const refreshBtn = document.getElementById('auxRefresh');
  const result = document.getElementById('auxResult');

  async function loadAux() {
    try {
      const r = await fetch('/v/aux/c', { credentials: 'same-origin' });
      const data = await r.json();
      if (data.error) {
        captchaImg.alt = '驗證碼載入失敗';
        result.innerHTML = '<p style="color:#c00">⚠️ 服務暫時無法使用，請稍後再試</p>';
        return;
      }
      captchaImg.src = 'data:image/jpeg;base64,' + data.image;
    } catch (e) {
      result.innerHTML = '<p style="color:#c00">⚠️ 網路錯誤</p>';
    }
  }

  async function submitAux(e) {
    e.preventDefault();
    const payload = {
      uino: document.getElementById('auxUino').value.trim().toUpperCase(),
      issueDate: document.getElementById('auxIssueDate').value,
      expiryDate: document.getElementById('auxExpiryDate').value,
      serialNo: document.getElementById('auxSerial').value.trim(),
      captcha: document.getElementById('auxCaptcha').value.trim(),
    };
    result.innerHTML = '<p>查詢中...</p>';
    try {
      const r = await fetch('/v/aux/s', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });
      const data = await r.json();
      renderResult(data);
    } catch (e) {
      result.innerHTML = '<p style="color:#c00">⚠️ 網路錯誤</p>';
    } finally {
      // 驗證碼一次性，重新載入
      document.getElementById('auxCaptcha').value = '';
      loadAux();
    }
  }

  function renderResult(data) {
    if (data.error === 'captcha_expired') {
      result.innerHTML = '<p style="color:#c00">⚠️ 驗證碼已過期，請重新輸入</p>';
      return;
    }
    if (data.error === 'upstream') {
      result.innerHTML = '<p style="color:#c00">⚠️ 服務暫時無法使用，請稍後再試</p>';
      return;
    }
    if (data.matched && data.image) {
      result.innerHTML = '<p style="color:#080">✅ 資料相符</p>'
        + '<img src="data:image/jpeg;base64,' + data.image + '" alt="" style="max-width:240px;border:1px solid #ccc">'
        + '<p style="color:#888;font-size:12px">' + (data.search_time || '') + '</p>';
    } else {
      result.innerHTML = '<p style="color:#c00">❌ 資料不符</p>';
    }
  }

  form.addEventListener('submit', submitAux);
  refreshBtn.addEventListener('click', loadAux);
  captchaImg.addEventListener('click', loadAux);

  loadAux();
})();
</script>
```

- [ ] **Step 2: 在 `templates/vetting/index.html` 加 include**

先看一下 vetting/index.html 結構，找到「三站結果區」的結尾位置。

```bash
grep -nE "endblock|</main|<!--.*end\|--%}" templates/vetting/index.html | tail -10
```

在三站結果 div 結尾的 `</div>` 之後、頁面 main content `</main>` 或 endblock 之前，加：

```jinja
{% if user_has_feature(current_user, 'v_aux') %}
  {% include 'icinfo/_form.html' %}
{% endif %}
```

注意：`user_has_feature` 必須是 Jinja 環境可見的全域 helper，確認 `app.py` 有 `app.jinja_env.globals['user_has_feature'] = user_has_feature`（既有 vetting 應該已經這麼做了，否則 vetting 自己的 nav 也用不了）。

如果沒有，先在 `app.py` setup 階段加：
```python
from features import user_has_feature
app.jinja_env.globals['user_has_feature'] = user_has_feature
```

確認方法：
```bash
grep -nE "jinja_env|user_has_feature" app.py | head
```

- [ ] **Step 3: 啟動 dev server，手動煙霧測試**

```bash
cd /home/hirain0126/projects/webapp/app_unified
flask run
```

開瀏覽器（記得用 Chrome 非 snap）：

1. 用 admin 帳號登入 → 進 `/v/`
2. 滾到頁面下方應該看到「💳 晶片居留證查詢」區塊
3. 驗證碼圖片應該自動載入
4. F12 → Network → 應該看到 `/v/aux/c` 請求成功
5. 隨便填 4 欄位 + 驗證碼按查詢
6. 應該看到「資料不符」（因為填的是假資料）
7. F12 → Network → `/v/aux/s` 應該 200，body 含 `matched: false`
8. 驗證碼自動重新載入

如果上面任何一步失敗，回到 routes.py / template 排錯。

- [ ] **Step 4: Commit**

```bash
git add templates/icinfo/_form.html templates/vetting/index.html app.py
git commit -m "$(cat <<'EOF'
feat(icinfo): _form.html fragment + vetting/index.html include

- HTML/CSS class 與 JS 變數全部用中性命名（auxBlock/auxForm/loadAux/...）
- F12 偵察看不出 icinfo/immigration/居留證 字樣
- vetting/index.html 在三站結果區下方 include（用 user_has_feature gate）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: F12 偽裝 audit + Prod 實機測試 checklist

**Files:** （無新檔；只跑 audit 命令 + 寫 README）

- [ ] **Step 1: F12 偽裝 audit（grep 確認）**

```bash
# 確認對外 URL 沒洩漏 /icinfo/
grep -rE "/icinfo/(c|captcha|search|s)" templates/ static/ icinfo/routes.py 2>/dev/null
# 預期：無輸出（/icinfo/ 不應該在 templates 或 routes 對外 URL 出現）

# 確認 templates 沒洩漏 icinfo/immigration/居留證 class/id（注意：標題文字「晶片居留證查詢」不算）
grep -nE 'class="[^"]*icinfo|id="[^"]*icinfo|class="[^"]*immigration|id="[^"]*immigration' templates/
# 預期：無輸出

# 確認 JS 變數命名（icinfo*, immigration* 不該出現在 script tag 內）
grep -nE 'icinfo[A-Z]|immigration[A-Z]' templates/icinfo/_form.html
# 預期：無輸出
```

如果有輸出 → 改名後重新 commit。

- [ ] **Step 2: 整合測試 checklist（人工跑）**

部署到 Zeabur 後（migration 自動跑、env 已備好）：

**權限驗證：**
- [ ] 用普通 user（features=["notes"]）登入 → 進 `/v/`：底部「💳 晶片居留證查詢」區塊**不出現**
- [ ] 同帳號直接打 `/v/aux/c` → 應 404
- [ ] 同帳號打 `/v/aux/s` → 應 404

**admin / super_admin：**
- [ ] 進 `/v/` 看到表單區
- [ ] 驗證碼自動載入
- [ ] F12 → Elements → 全頁搜 `icinfo`、`immigration`、`居留證`（class/id 層級）→ **找不到**（標題的「晶片居留證查詢」中文字 OK，重點是 class/id）
- [ ] F12 → Network → 點重新整理驗證碼 → 看到 fetch `/v/aux/c`
- [ ] F12 → Network → 送查詢 → 看到 fetch `/v/aux/s`，**沒有** `/icinfo/` 字樣

**功能驗證（拿真實居留證跑一次）：**
- [ ] 4 欄位正確填 + 驗證碼正確 → 顯示照片 + 「✅ 資料相符」
- [ ] 故意填錯一個欄位 → 「❌ 資料不符」
- [ ] 故意填錯驗證碼 → 「❌ 資料不符」（移民署不細分，預期）
- [ ] 永居證 expiryDate 留空 → 應該也能查（如果手上有永居證樣本）

**Log 驗證：**
- [ ] 跑完上述 4 次查詢後，Zeabur SQL：
  ```sql
  SELECT user_id, queried_uino, matched, queried_at FROM v_aux_logs ORDER BY queried_at DESC LIMIT 10;
  ```
  → 應該看到 4 筆紀錄，matched 一個 true、三個 false

- [ ] **Step 3: Open Questions 收尾（spec 留下的 3 個）**

第一次 prod 跑完後檢查：

1. **`users.features` 的型別** — 已確認 `db.Text` (JSON-encoded string)，migration 009 已用 ORM-friendly 寫法
2. **CSRF token** — 跑一次完整流程後檢查移民署 search response，看有沒有額外 token 要帶（recon 沒看到，但 prod 實測可能露出）
3. **驗證碼錯 vs 4 欄位錯的 rcode** — 在 `submit_search` 加一行 `logger.info("icinfo rcode=%s", rcode)`，prod 跑幾次「故意錯」案例對 log 看 rcode 是否一致；若不同，未來可考慮前端文案分流

如果 (2) 或 (3) 發現問題 → 開新 plan 處理。

- [ ] **Step 4: Push（使用者確認後才做）**

按 `feedback_push_policy` 規則，push 等使用者明確說才推。本任務只 commit 到本地 main，等使用者驗證 OK 再說。

---

## Self-Review

**Spec 覆蓋檢查**：
- ✅ 動機 1 (icinfo 新功能) → Task 2-6
- ✅ 動機 2 (司法院排序) → Task 1
- ✅ Section A (新 blueprint + 偽裝) → Task 4-5
- ✅ Section B (UI 整合) → Task 6
- ✅ Section C (後端流程) → Task 4-5
- ✅ Section D (前端 JS) → Task 6
- ✅ Section E (Model + Migration) → Task 2
- ✅ Section F (司法院排序) → Task 1
- ✅ 測試（Unit + Integration）→ Task 1, 4, 5 (unit) + Task 7 (integration)
- ✅ YAGNI / 安全 / 開放問題 → Task 7 收尾

**Placeholder scan**：無 TBD / TODO（只有 Task 7 「使用者確認後才 push」這個是 process 步驟，不是內容缺漏）

**Type 一致性**：
- `submit_search` 簽名：`(payload: dict, jsessionid: str) -> dict`，Task 4 / 5 一致
- `fetch_captcha` 簽名：`() -> tuple[bytes, str]`，Task 4 / 5 一致
- `IcinfoQueryLog` 欄位：Task 2 定義、Task 5 寫入，欄位名一致（`user_id`, `queried_uino`, `matched`）
- `_SESSION_KEY = "icinfo_jsessionid"`：Task 5 定義、test 也對齊

OK，可以執行。
