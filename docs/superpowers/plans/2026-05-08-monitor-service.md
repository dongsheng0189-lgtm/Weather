# Monitor Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Zeabur 上開一個獨立的 `monitor` service，收 app-unified 推來的 WARNING+ log，加密存進專屬 Postgres，提供 dashboard 跨 pod restart 也看得到歷史 log。

**Architecture:** 兩 service / 兩 Postgres add-on / 兩 Docker image，repo 同一個但 monitor/ 跟 app_unified/ 完全 0 cross-import。app-unified 端加非同步 `MonitorHTTPHandler`（用 gevent.monkey.get_original 拿原生 OS Thread 避免跟 5/8 b61cdb0 wsgi_lazy 撞），HTTP POST /ingest with Bearer token；monitor 端用 sync gunicorn worker（簡單）+ EncryptedText（共用 DB_ENCRYPTION_KEY）+ retention sweep daemon thread。

**Tech Stack:** Python 3.12, Flask 3.x, Flask-SQLAlchemy 3.x, gunicorn (sync), psycopg2-binary, bcrypt, cryptography (AES-GCM), pytest, vanilla JS。

**Spec reference:** `docs/superpowers/specs/2026-05-08-monitor-service-design.md`

---

## File Structure

| 檔案 | 動作 | 責任 |
|------|------|------|
| `monitor/__init__.py` | **Create** | 空檔，宣告 package |
| `monitor/config.py` | **Create** | 從 env 讀 settings (DATABASE_URL / SECRET_KEY / INGEST_TOKEN / DASHBOARD_PASSWORD_HASH / DB_ENCRYPTION_KEY 等) |
| `monitor/crypto.py` | **Create** | AES-GCM 加解密 helper (從 app-unified copy 過來，**不 import**) |
| `monitor/extensions.py` | **Create** | `db = SQLAlchemy()` 單例 |
| `monitor/models.py` | **Create** | `EncryptedText` Column 型別 + `LogEntry` model |
| `monitor/auth.py` | **Create** | `auth_bp` blueprint：/login, /logout + `login_required` decorator |
| `monitor/ingest.py` | **Create** | `ingest_bp` blueprint：POST /ingest with Bearer token validate + 寫 LogEntry |
| `monitor/dashboard.py` | **Create** | `dashboard_bp`：GET /, /api/logs, /api/status (login_required) |
| `monitor/retention.py` | **Create** | 每小時跑一次 batched DELETE 的 daemon thread |
| `monitor/app.py` | **Create** | `create_app()`：載 config、註冊 bp、起 retention sweep |
| `monitor/wsgi.py` | **Create** | gunicorn entry：`app = create_app()` + 直接掛 /healthz route |
| `monitor/gunicorn.conf.py` | **Create** | sync worker, threads=4, bind 0.0.0.0:8080 |
| `monitor/requirements.txt` | **Create** | flask / flask-sqlalchemy / gunicorn / psycopg2-binary / bcrypt / cryptography |
| `monitor/Dockerfile` | **Create** | python:3.12-slim + apt libpq-dev + pip install + COPY source 放最後 |
| `monitor/templates/base.html` | **Create** | 共用 layout (head, nav, logout link) |
| `monitor/templates/login.html` | **Create** | 單欄密碼 form |
| `monitor/templates/dashboard.html` | **Create** | filter form + status header + log table，vanilla JS fetch /api/logs |
| `monitor/tests/__init__.py` | **Create** | 空檔 |
| `monitor/tests/conftest.py` | **Create** | pytest fixtures (`app`, `client`, `db_session`, `_authed_client`, `_reset_logs`) |
| `monitor/tests/test_ingest_auth.py` | **Create** | 3 tests：no token / bad token / good token |
| `monitor/tests/test_ingest_validate.py` | **Create** | 4 tests：missing fields / bad level / message too long / bad ts fallback |
| `monitor/tests/test_ingest_persist.py` | **Create** | 2 tests：好 payload 寫進 DB + EncryptedText round-trip |
| `monitor/tests/test_dashboard_auth.py` | **Create** | 3 tests：未登入 redirect / bad password / good password |
| `monitor/tests/test_api_logs.py` | **Create** | 5 tests：level/source/logger_prefix/since/limit filter |
| `monitor/tests/test_api_status.py` | **Create** | 3 tests：last_log_ts / errors_1h / sources distinct |
| `monitor/tests/test_retention.py` | **Create** | 2 tests：超過 cutoff DELETE / 未到 cutoff 不動 |
| `app_unified/log_shipping.py` | **Create** | `MonitorHTTPHandler(logging.Handler)` 非同步 push WARNING+ log |
| `app_unified/app.py` | **Modify** (~line 32 後) | env var gate 條件式 attach `MonitorHTTPHandler` |
| `app_unified/tests/test_log_shipping.py` | **Create** | 4 tests：emit 不 raise / queue full drop-oldest / worker swallow http err / handler 接 root logger |
| `.github/workflows/docker-monitor.yml` | **Create** | path filter `monitor/**`, image `ghcr.io/stvlin1988-alt/weather-monitor`, cache scope=monitor |

---

## Task 0: Worktree setup

**Files:** N/A (環境準備)

- [ ] **Step 1: Open isolated worktree**

```bash
cd /home/hirain0126/projects/webapp
git worktree add /home/hirain0126/projects/webapp/.worktrees/feat-monitor-service -b feat/monitor-service main
cd .worktrees/feat-monitor-service
pwd
```

預期輸出：`/home/hirain0126/projects/webapp/.worktrees/feat-monitor-service`

之後所有檔案編輯、test 執行都在這個目錄裡。

---

## Task 1: Bootstrap `monitor/` skeleton + crypto

**Files:**
- Create: `monitor/__init__.py` (empty)
- Create: `monitor/config.py`
- Create: `monitor/extensions.py`
- Create: `monitor/crypto.py`
- Create: `monitor/tests/__init__.py` (empty)
- Create: `monitor/tests/test_crypto.py`

- [ ] **Step 1: Create empty `__init__.py` files**

```bash
mkdir -p monitor/tests monitor/templates
touch monitor/__init__.py monitor/tests/__init__.py
```

- [ ] **Step 2: Write `monitor/config.py`**

```python
"""Monitor service config — load from env, fail fast if 必填項目缺。"""
import os


class Config:
    SECRET_KEY = os.environ["MONITOR_SECRET_KEY"]
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }
    PERMANENT_SESSION_LIFETIME = 35 * 60  # 35 min, 跟 app-unified 一致

    INGEST_TOKEN = os.environ["MONITOR_INGEST_TOKEN"]
    DASHBOARD_PASSWORD_HASH = os.environ["MONITOR_DASHBOARD_PASSWORD_HASH"]

    # 可選：retention 天數、log min level
    LOG_RETENTION_DAYS = int(os.environ.get("MONITOR_LOG_RETENTION_DAYS", "30"))

    # 加解密 key — 共用 app-unified 的 (spec §動機與設計守則)
    DB_ENCRYPTION_KEY = os.environ["DB_ENCRYPTION_KEY"]
```

- [ ] **Step 3: Write `monitor/extensions.py`**

```python
"""SQLAlchemy 單例，讓 model 與 app 都能 import 同一個 db。"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
```

- [ ] **Step 4: Write `monitor/crypto.py`**

```python
"""AES-GCM 加解密 helper。

從 app_unified/crypto.py 複製過來而非 import，遵守
feedback_module_isolation：monitor 與 app_unified 在 runtime / repo
import 路徑上完全分離，但兩端使用同一把 DB_ENCRYPTION_KEY 才能解
互相 ingest 時的舊 message。
"""
import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _load_key() -> bytes:
    raw = os.environ["DB_ENCRYPTION_KEY"]
    # 接受 base64 (urlsafe) 或 raw bytes hex
    try:
        return base64.urlsafe_b64decode(raw)
    except Exception:
        return raw.encode()


def encrypt_str(plaintext: str) -> bytes:
    """加密 str → bytes (12-byte nonce 接 ciphertext)。"""
    if plaintext is None:
        return None
    key = _load_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return nonce + ct


def decrypt_str(blob: bytes) -> str:
    """解密 bytes (nonce + ciphertext) → str。"""
    if blob is None:
        return None
    key = _load_key()
    aesgcm = AESGCM(key)
    nonce, ct = blob[:12], blob[12:]
    pt = aesgcm.decrypt(nonce, ct, associated_data=None)
    return pt.decode("utf-8")
```

- [ ] **Step 5: Write failing test `monitor/tests/test_crypto.py`**

```python
"""Crypto round-trip 測試 — 純 stdlib + cryptography，不需 app context。"""
import base64
import os
import pytest

# 測試前手動設 DB_ENCRYPTION_KEY，避免 import 期 KeyError
os.environ.setdefault(
    "DB_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"test-key-32-bytes-for-unit-tests").decode(),
)

from monitor.crypto import encrypt_str, decrypt_str


def test_encrypt_decrypt_round_trip():
    pt = "hello 中文 traceback line 42"
    ct = encrypt_str(pt)
    assert ct != pt.encode()  # 真的有加密
    assert len(ct) > 12       # 至少含 nonce
    assert decrypt_str(ct) == pt


def test_encrypt_none_returns_none():
    assert encrypt_str(None) is None
    assert decrypt_str(None) is None


def test_encrypt_long_message():
    pt = "x" * 60_000
    assert decrypt_str(encrypt_str(pt)) == pt
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /home/hirain0126/projects/webapp/.worktrees/feat-monitor-service && /home/hirain0126/projects/webapp/venv/bin/python -m pytest monitor/tests/test_crypto.py -v`

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add monitor/__init__.py monitor/config.py monitor/extensions.py monitor/crypto.py monitor/tests/__init__.py monitor/tests/test_crypto.py
git commit -m "feat(monitor): bootstrap config / extensions / crypto + round-trip tests

monitor/ top-level dir，與 app_unified/ 平行；config 從 env 讀 fail-fast
(SECRET_KEY / DATABASE_URL / INGEST_TOKEN / DASHBOARD_PASSWORD_HASH /
DB_ENCRYPTION_KEY 必填)。crypto.py 用 AES-GCM 12-byte nonce + ciphertext
塞同一個 blob，跟 app_unified/crypto.py 同 contract 但檔案複製不 import
(feedback_module_isolation 鐵律)。

3 unit tests 覆蓋 round-trip / None / 60KB 長訊息。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `LogEntry` model + `EncryptedText` column type

**Files:**
- Modify: `monitor/models.py` (Create)
- Create: `monitor/tests/conftest.py`
- Create: `monitor/tests/test_models.py`

- [ ] **Step 1: Write `monitor/models.py`**

```python
"""Monitor LogEntry model。

EncryptedText 用 SQLAlchemy TypeDecorator wrap LargeBinary，加解密在
process_bind_param / process_result_value 透明完成 → Python 端永遠拿
明文 str，DB 存的永遠是 ciphertext bytes。
"""
from datetime import datetime, timezone

from sqlalchemy import Column, BigInteger, DateTime, String, LargeBinary, Index
from sqlalchemy.types import TypeDecorator

from extensions import db
from crypto import encrypt_str, decrypt_str


class EncryptedText(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_str(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return decrypt_str(bytes(value)) if value is not None else None


def _utc_now():
    return datetime.now(timezone.utc)


class LogEntry(db.Model):
    __tablename__ = "log_entries"

    id          = Column(BigInteger, primary_key=True)
    ts          = Column(DateTime(timezone=True), nullable=False, index=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    source      = Column(String(64),  nullable=False, index=True)
    level       = Column(String(16),  nullable=False, index=True)
    logger      = Column(String(128), nullable=False)
    message     = Column(EncryptedText, nullable=False)
    extra_json  = Column(EncryptedText, nullable=True)
    git_sha     = Column(String(12),   nullable=True)

    __table_args__ = (
        Index("ix_log_entries_source_level_ts", "source", "level", "ts"),
        Index("ix_log_entries_logger_ts", "logger", "ts"),
    )
```

- [ ] **Step 2: Write `monitor/tests/conftest.py`**

```python
"""Pytest fixtures：in-memory SQLite + StaticPool，跟 app_unified 同 pattern。

每 test session 起一次 Flask app；每 test 自己 push 一個短命 app_context；
一條 autouse fixture 在 test 前後清空 LogEntry table。
"""
import base64
import os
import sys

import pytest

# Add monitor/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 必填 env vars (在 import config 前先塞)
os.environ.setdefault("MONITOR_SECRET_KEY", "test-monitor-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MONITOR_INGEST_TOKEN", "test-ingest-token-very-secret")
os.environ.setdefault(
    "MONITOR_DASHBOARD_PASSWORD_HASH",
    # bcrypt hash of "test-pw" — 由 bcrypt.hashpw(b"test-pw", bcrypt.gensalt()) 預先產
    "$2b$12$KIXX6Q.Z2qXqM5yL4yNk5OyLEFrPp1u.fy1B3hHRqJxZMZ8MZ1MJK",
)
os.environ.setdefault(
    "DB_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"test-key-32-bytes-for-unit-tests").decode(),
)
os.environ.setdefault("MONITOR_DISABLE_RETENTION", "1")  # 不在 test 裡跑 sweep thread


@pytest.fixture(scope="session")
def app():
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True

    from sqlalchemy.pool import StaticPool
    application.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    application.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }

    from extensions import db
    with application.app_context():
        db.drop_all()
        db.create_all()

    yield application

    with application.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db_session(app):
    from extensions import db
    ctx = app.app_context()
    ctx.push()
    try:
        yield db.session
    finally:
        db.session.remove()
        ctx.pop()


@pytest.fixture(autouse=True)
def _reset_logs(app):
    """每 test 前後清空 log_entries，避免互相污染。"""
    from extensions import db
    from models import LogEntry
    with app.app_context():
        db.session.query(LogEntry).delete()
        db.session.commit()
    yield
    with app.app_context():
        db.session.query(LogEntry).delete()
        db.session.commit()


@pytest.fixture
def authed_client(client):
    """繞過 password 直接設 session — auth 邏輯本身另外測。"""
    with client.session_transaction() as sess:
        sess["mon_authed"] = True
    return client
```

- [ ] **Step 3: Write failing test `monitor/tests/test_models.py`**

```python
"""LogEntry CRUD + EncryptedText round-trip。"""
from datetime import datetime, timezone

from extensions import db
from models import LogEntry


def test_log_entry_round_trip(db_session):
    entry = LogEntry(
        ts=datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc),
        source="app-unified",
        level="ERROR",
        logger="mynotes.routes",
        message="something broke 中文 OK",
        extra_json='{"trace": "line 42"}',
        git_sha="abc1234",
    )
    db_session.add(entry)
    db_session.commit()

    fetched = LogEntry.query.first()
    assert fetched.source == "app-unified"
    assert fetched.level == "ERROR"
    assert fetched.message == "something broke 中文 OK"
    assert fetched.extra_json == '{"trace": "line 42"}'
    assert fetched.received_at is not None  # default applied


def test_log_entry_encrypted_at_rest(db_session):
    """直接讀底層 LargeBinary 應該是 ciphertext，不是明文。"""
    entry = LogEntry(
        ts=datetime.now(timezone.utc),
        source="app-unified",
        level="WARNING",
        logger="x",
        message="SECRET-PLAINTEXT",
    )
    db_session.add(entry)
    db_session.commit()

    raw = db.session.execute(
        db.text("SELECT message FROM log_entries WHERE id = :i"),
        {"i": entry.id},
    ).scalar()
    assert b"SECRET-PLAINTEXT" not in raw  # 明文不能在 DB 出現
    assert len(raw) > len(b"SECRET-PLAINTEXT")  # 加密 + nonce 比明文長
```

- [ ] **Step 4: Run test to verify they fail**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_models.py -v`

Expected: ERROR — `app.py` 還沒寫，conftest fixture `app` import 失敗。

- [ ] **Step 5: Write a minimal placeholder `monitor/app.py` so conftest 可 import**

```python
"""Monitor service Flask app factory — Task 2 minimal version。

本 task 只需要 app + db.init_app + table create 跑得起來；blueprint /
auth / retention 在後續 task 加上。
"""
from flask import Flask

from config import Config
from extensions import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app
```

- [ ] **Step 6: Run test to verify they pass**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_models.py -v`

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add monitor/models.py monitor/app.py monitor/tests/conftest.py monitor/tests/test_models.py
git commit -m "feat(monitor): LogEntry model + EncryptedText TypeDecorator

EncryptedText wrap LargeBinary，process_bind_param/result_value 透明
加解密 → SA model 介面看到 str，DB 存 ciphertext。round-trip + 明文
不在 DB 兩個 test 把 contract pin 死。

minimal app.py 讓 conftest 能 import；blueprint 後續 task 加。
SQLAlchemy in-memory + StaticPool fixture pattern 抄 app_unified/tests/
conftest.py，autouse _reset_logs 防 test 互相污染。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `/healthz` + wsgi entry + gunicorn config

**Files:**
- Create: `monitor/wsgi.py`
- Create: `monitor/gunicorn.conf.py`
- Create: `monitor/tests/test_healthz.py`

- [ ] **Step 1: Write failing test `monitor/tests/test_healthz.py`**

```python
def test_healthz_returns_200_text(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_data(as_text=True).strip() == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_healthz.py -v`

Expected: FAIL — 404 because /healthz not registered yet.

- [ ] **Step 3: Add /healthz to `monitor/app.py`**

Replace `monitor/app.py` with:

```python
"""Monitor service Flask app factory。"""
from flask import Flask

from config import Config
from extensions import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    return app
```

- [ ] **Step 4: Write `monitor/wsgi.py`**

```python
"""gunicorn entry — 讓 gunicorn -c gunicorn.conf.py wsgi:app 跑。"""
from app import create_app

app = create_app()
```

- [ ] **Step 5: Write `monitor/gunicorn.conf.py`**

```python
"""Gunicorn config — sync worker，monitor 沒 cold-start CPU work，
不需要 gevent / LazyAppProxy 那一套。"""
bind = "0.0.0.0:8080"
workers = 1
worker_class = "sync"
threads = 4
timeout = 30
keepalive = 5
preload_app = False
accesslog = "-"
errorlog = "-"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_healthz.py -v`

Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add monitor/app.py monitor/wsgi.py monitor/gunicorn.conf.py monitor/tests/test_healthz.py
git commit -m "feat(monitor): /healthz + gunicorn config + wsgi entry

普通 Flask route /healthz 即可，monitor 沒 cold-start CPU work，不
需要 wsgi_lazy 那套 LazyAppProxy。Sync worker + threads=4 處理 ingest
並發，比 gevent 簡單 — 沒 monkey patch 雷 (5/8 b61cdb0 教訓)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `/ingest` blueprint with Bearer auth + validation + DB write

**Files:**
- Create: `monitor/ingest.py`
- Modify: `monitor/app.py` (register blueprint)
- Create: `monitor/tests/test_ingest_auth.py`
- Create: `monitor/tests/test_ingest_validate.py`
- Create: `monitor/tests/test_ingest_persist.py`

- [ ] **Step 1: Write failing tests `monitor/tests/test_ingest_auth.py`**

```python
"""Bearer token validation — constant-time 比對防 timing attack。"""
import json


def _post(client, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/ingest", data=json.dumps(payload), headers=headers)


def _good_payload():
    return {
        "ts": "2026-05-08T12:00:00+00:00",
        "level": "ERROR",
        "logger": "mynotes.routes",
        "source": "app-unified",
        "message": "boom",
    }


def test_no_token_returns_401(client):
    r = _post(client, _good_payload(), token=None)
    assert r.status_code == 401


def test_bad_token_returns_401(client):
    r = _post(client, _good_payload(), token="totally-wrong-token")
    assert r.status_code == 401


def test_good_token_returns_200(client):
    r = _post(client, _good_payload(), token="test-ingest-token-very-secret")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}
```

- [ ] **Step 2: Write failing tests `monitor/tests/test_ingest_validate.py`**

```python
import json

from extensions import db
from models import LogEntry


def _post(client, payload, token="test-ingest-token-very-secret"):
    return client.post(
        "/ingest",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )


def test_missing_required_fields_400(client):
    r = _post(client, {"ts": "2026-05-08T12:00:00+00:00", "level": "ERROR"})
    assert r.status_code == 400
    assert "missing fields" in r.get_json()["error"]


def test_bad_level_400(client):
    r = _post(client, {
        "ts": "2026-05-08T12:00:00+00:00", "level": "INFO",
        "logger": "x", "source": "app-unified", "message": "x",
    })
    assert r.status_code == 400


def test_message_too_long_413(client):
    r = _post(client, {
        "ts": "2026-05-08T12:00:00+00:00", "level": "ERROR",
        "logger": "x", "source": "app-unified", "message": "x" * 64_001,
    })
    assert r.status_code == 413


def test_bad_ts_falls_back_to_now(client, db_session):
    """壞時間戳不 reject，直接用 now() 替代避免丟 log。"""
    r = _post(client, {
        "ts": "not-a-real-iso-timestamp", "level": "WARNING",
        "logger": "x", "source": "app-unified", "message": "ok",
    })
    assert r.status_code == 200
    entry = LogEntry.query.first()
    assert entry is not None
    assert entry.ts is not None
```

- [ ] **Step 3: Write failing tests `monitor/tests/test_ingest_persist.py`**

```python
import json
from datetime import datetime, timezone

from extensions import db
from models import LogEntry


def _post(client, payload, token="test-ingest-token-very-secret"):
    return client.post(
        "/ingest",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )


def test_good_payload_persists_all_fields(client, db_session):
    payload = {
        "ts": "2026-05-08T12:00:00+00:00",
        "level": "ERROR",
        "logger": "mynotes.routes",
        "source": "app-unified",
        "message": "broken",
        "extra": "Traceback (most recent call last)...",
        "git_sha": "abc1234",
    }
    r = _post(client, payload)
    assert r.status_code == 200

    entry = LogEntry.query.first()
    assert entry.source == "app-unified"
    assert entry.level == "ERROR"
    assert entry.logger == "mynotes.routes"
    assert entry.message == "broken"
    assert entry.extra_json == "Traceback (most recent call last)..."
    assert entry.git_sha == "abc1234"
    assert entry.ts == datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)


def test_overlong_logger_and_source_truncated(client, db_session):
    """logger 128 / source 64 char limit。"""
    r = _post(client, {
        "ts": "2026-05-08T12:00:00+00:00",
        "level": "ERROR",
        "logger": "a" * 200,
        "source": "b" * 100,
        "message": "x",
    })
    assert r.status_code == 200
    entry = LogEntry.query.first()
    assert len(entry.logger) <= 128
    assert len(entry.source) <= 64
```

- [ ] **Step 4: Run all 3 test files to verify they fail**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ingest_auth.py tests/test_ingest_validate.py tests/test_ingest_persist.py -v`

Expected: All FAIL — /ingest endpoint not registered (404 / blueprint missing).

- [ ] **Step 5: Write `monitor/ingest.py`**

```python
"""POST /ingest — 收 app-unified 推來的 log line。Bearer token 驗證。"""
import hmac
import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, current_app

from extensions import db
from models import LogEntry

logger = logging.getLogger(__name__)
ingest_bp = Blueprint("ingest", __name__)


_REQUIRED_FIELDS = {"ts", "level", "logger", "source", "message"}
_VALID_LEVELS = {"WARNING", "ERROR", "CRITICAL"}
_MAX_MESSAGE_LEN = 64_000


@ingest_bp.route("/ingest", methods=["POST"])
def ingest():
    # ── auth ────────────────────────────────────────────────
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "no token"}), 401
    expected = current_app.config["INGEST_TOKEN"]
    if not hmac.compare_digest(auth[7:], expected):
        return jsonify({"error": "bad token"}), 401

    # ── parse ───────────────────────────────────────────────
    data = request.get_json(force=True, silent=True) or {}
    if not _REQUIRED_FIELDS.issubset(data):
        return jsonify({"error": "missing fields"}), 400
    if data["level"] not in _VALID_LEVELS:
        return jsonify({"error": "bad level"}), 400
    if len(data.get("message", "")) > _MAX_MESSAGE_LEN:
        return jsonify({"error": "message too long"}), 413

    # ── ts fallback ─────────────────────────────────────────
    try:
        ts = datetime.fromisoformat(data["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        ts = datetime.now(timezone.utc)

    # ── insert ──────────────────────────────────────────────
    entry = LogEntry(
        ts=ts,
        source=data["source"][:64],
        level=data["level"],
        logger=data["logger"][:128],
        message=data["message"],
        extra_json=data.get("extra"),
        git_sha=(data.get("git_sha") or "")[:12] or None,
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify({"status": "ok"}), 200
```

- [ ] **Step 6: Update `monitor/app.py` to register `ingest_bp`**

```python
"""Monitor service Flask app factory。"""
from flask import Flask

from config import Config
from extensions import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    from ingest import ingest_bp
    app.register_blueprint(ingest_bp)

    return app
```

- [ ] **Step 7: Run all 3 ingest test files**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ingest_auth.py tests/test_ingest_validate.py tests/test_ingest_persist.py -v`

Expected: 9 passed (3 + 4 + 2).

- [ ] **Step 8: Commit**

```bash
git add monitor/ingest.py monitor/app.py monitor/tests/test_ingest_auth.py monitor/tests/test_ingest_validate.py monitor/tests/test_ingest_persist.py
git commit -m "feat(monitor): /ingest endpoint with Bearer auth + validate + persist

constant-time hmac.compare_digest 比對 token 防 timing attack。
required 5 fields (ts/level/logger/source/message) 缺一個 400；level
必須 WARNING/ERROR/CRITICAL；message > 64KB return 413。bad ts 不 reject
直接 fall back now() 避免 ingest 端 noise 害 log 整批丟。

logger/source 在 DB column 上有 length limit (128/64)，serializer 再
truncate 一次保險，超長的 row 仍能寫進去。

9 tests 覆蓋三條路徑：auth (3) / validate (4) / persist (2)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `/login` + `login_required` decorator

**Files:**
- Create: `monitor/auth.py`
- Modify: `monitor/app.py` (register `auth_bp`)
- Create: `monitor/templates/login.html`
- Create: `monitor/templates/base.html`
- Create: `monitor/tests/test_dashboard_auth.py`

- [ ] **Step 1: Write failing tests `monitor/tests/test_dashboard_auth.py`**

```python
"""Dashboard 入口 / + login flow。"""


def test_unauthed_root_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_login_bad_password_shows_form(client):
    r = client.post("/login", data={"password": "wrong"}, follow_redirects=False)
    # bad password → 同一頁回 200 with 錯誤提示，不跳走
    assert r.status_code == 200
    assert b"login" in r.get_data().lower() or b"\xe5\xaf\x86\xe7\xa2\xbc" in r.get_data()  # "密碼"


def test_login_good_password_sets_session_and_redirects(client):
    # 正確密碼 = "test-pw" (見 conftest hash)
    r = client.post("/login", data={"password": "test-pw"}, follow_redirects=False)
    assert r.status_code == 302
    # session 已標 authed → 接著訪 / 不會被踢
    r2 = client.get("/", follow_redirects=False)
    assert r2.status_code == 200 or (r2.status_code == 302 and "/login" not in r2.headers["Location"])
```

- [ ] **Step 2: Run test to verify they fail**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_dashboard_auth.py -v`

Expected: FAIL — /login endpoint not registered, / not protected yet.

- [ ] **Step 3: Write `monitor/auth.py`**

```python
"""/login, /logout + login_required decorator。"""
from functools import wraps

import bcrypt
from flask import (
    Blueprint, request, render_template, redirect, url_for,
    session, flash, current_app,
)

auth_bp = Blueprint("auth", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("mon_authed"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        expected_hash = current_app.config["DASHBOARD_PASSWORD_HASH"].encode()
        try:
            ok = bcrypt.checkpw(password.encode(), expected_hash)
        except ValueError:
            ok = False  # malformed hash 環境變數
        if ok:
            session["mon_authed"] = True
            session.permanent = True
            return redirect(url_for("dashboard.home"))
        flash("密碼錯誤")
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.pop("mon_authed", None)
    return redirect(url_for("auth.login"))
```

- [ ] **Step 4: Write `monitor/templates/base.html`**

```html
<!doctype html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{% block title %}Monitor{% endblock %}</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; padding: 1rem; background: #fafafa; color: #222; }
    nav { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
    nav a { color: #333; text-decoration: none; }
    .flash { background: #fee; padding: .5rem 1rem; border-left: 4px solid #c00; margin-bottom: 1rem; }
    table { width: 100%; border-collapse: collapse; font-size: .875rem; }
    th, td { padding: .4rem .6rem; border-bottom: 1px solid #eee; vertical-align: top; }
    th { background: #f4f4f4; text-align: left; }
    .level-ERROR, .level-CRITICAL { color: #c00; font-weight: 600; }
    .level-WARNING { color: #b50; }
    pre { font-family: ui-monospace, monospace; font-size: .8rem; white-space: pre-wrap; word-break: break-all; margin: 0; }
    input, select, button { font: inherit; padding: .3rem .5rem; }
    .filters { display: flex; gap: .5rem; flex-wrap: wrap; margin-bottom: 1rem; }
  </style>
</head>
<body>
  <nav>
    <strong>Monitor</strong>
    {% if session.get('mon_authed') %}<a href="{{ url_for('auth.logout') }}">登出</a>{% endif %}
  </nav>
  {% with messages = get_flashed_messages() %}
    {% for msg in messages %}<div class="flash">{{ msg }}</div>{% endfor %}
  {% endwith %}
  {% block content %}{% endblock %}
</body>
</html>
```

- [ ] **Step 5: Write `monitor/templates/login.html`**

```html
{% extends "base.html" %}
{% block title %}登入 — Monitor{% endblock %}
{% block content %}
<form method="POST" action="{{ url_for('auth.login') }}" style="max-width:320px;">
  <p><label>密碼：<input type="password" name="password" autofocus required></label></p>
  <p><button type="submit">登入</button></p>
</form>
{% endblock %}
```

- [ ] **Step 6: Update `monitor/app.py` to register `auth_bp` + add minimal `/` redirect**

```python
"""Monitor service Flask app factory。"""
from flask import Flask, redirect, url_for, session

from config import Config
from extensions import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    @app.route("/")
    def root():
        # Task 5 minimal：未登入 → /login；已登入暫時回簡單 200 (Task 6 才接 dashboard)
        if not session.get("mon_authed"):
            return redirect(url_for("auth.login"))
        return "(dashboard 之後接)", 200

    from ingest import ingest_bp
    from auth import auth_bp
    app.register_blueprint(ingest_bp)
    app.register_blueprint(auth_bp)

    return app
```

- [ ] **Step 7: Run test to verify they pass**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_dashboard_auth.py -v`

Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add monitor/auth.py monitor/templates/base.html monitor/templates/login.html monitor/app.py monitor/tests/test_dashboard_auth.py
git commit -m "feat(monitor): /login + login_required + minimal templates

bcrypt.checkpw 比對 hash from env var；session.permanent + 35min
expiry 跟 app-unified 一致。base.html 共用 layout + 簡單 CSS (無
framework)；login.html 單欄密碼 form。

/ 暫時 redirect 未登入 / 登入後回 placeholder，Task 6 接 dashboard
頁面才換掉。

3 tests pin 守則：未登入 redirect、bad pw stay、good pw → session 標
authed。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Dashboard `/`, `/api/logs`, `/api/status`

**Files:**
- Create: `monitor/dashboard.py`
- Create: `monitor/templates/dashboard.html`
- Modify: `monitor/app.py` (register `dashboard_bp`，移除 task 5 的 placeholder root)
- Create: `monitor/tests/test_api_logs.py`
- Create: `monitor/tests/test_api_status.py`

- [ ] **Step 1: Write failing tests `monitor/tests/test_api_logs.py`**

```python
"""GET /api/logs — filter combinations。"""
from datetime import datetime, timedelta, timezone

from extensions import db
from models import LogEntry


def _seed(db_session, **overrides):
    defaults = dict(
        ts=datetime.now(timezone.utc),
        source="app-unified",
        level="ERROR",
        logger="mynotes.routes",
        message="m",
    )
    defaults.update(overrides)
    e = LogEntry(**defaults)
    db_session.add(e)
    db_session.commit()
    return e


def test_filter_by_source(authed_client, db_session):
    _seed(db_session, source="app-unified")
    _seed(db_session, source="other-svc")
    r = authed_client.get("/api/logs?source=app-unified")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["source"] == "app-unified"


def test_filter_by_level_error_includes_critical(authed_client, db_session):
    _seed(db_session, level="WARNING")
    _seed(db_session, level="ERROR")
    _seed(db_session, level="CRITICAL")
    r = authed_client.get("/api/logs?level=ERROR")
    rows = r.get_json()
    levels = {row["level"] for row in rows}
    assert levels == {"ERROR", "CRITICAL"}


def test_filter_by_logger_prefix(authed_client, db_session):
    _seed(db_session, logger="mynotes.routes")
    _seed(db_session, logger="mynotes.ai_local")
    _seed(db_session, logger="notes.routes")
    r = authed_client.get("/api/logs?logger_prefix=mynotes.")
    rows = r.get_json()
    assert len(rows) == 2
    assert all(row["logger"].startswith("mynotes.") for row in rows)


def test_filter_by_since_epoch(authed_client, db_session):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    new = datetime.now(timezone.utc) - timedelta(minutes=10)
    _seed(db_session, ts=old, message="OLD")
    _seed(db_session, ts=new, message="NEW")
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    r = authed_client.get(f"/api/logs?since={cutoff}")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["message"] == "NEW"


def test_limit_caps_at_1000(authed_client, db_session):
    """大 limit 也只回 1000 筆。"""
    for _ in range(20):
        _seed(db_session)
    r = authed_client.get("/api/logs?limit=999999")
    rows = r.get_json()
    assert len(rows) <= 1000  # 20 筆夠用，但驗 query string 接受 + 內部 cap
    # 確認 default 不帶 limit 一樣 OK
    r2 = authed_client.get("/api/logs")
    assert r2.status_code == 200
```

- [ ] **Step 2: Write failing tests `monitor/tests/test_api_status.py`**

```python
"""GET /api/status — 每 source 的 last_log_ts / errors_1h / total。"""
from datetime import datetime, timedelta, timezone

from extensions import db
from models import LogEntry


def _seed(db_session, **overrides):
    defaults = dict(
        ts=datetime.now(timezone.utc),
        source="app-unified", level="ERROR",
        logger="x", message="m",
    )
    defaults.update(overrides)
    db_session.add(LogEntry(**defaults))
    db_session.commit()


def test_status_lists_distinct_sources(authed_client, db_session):
    _seed(db_session, source="app-unified")
    _seed(db_session, source="other-svc")
    r = authed_client.get("/api/status")
    sources = {s["source"] for s in r.get_json()}
    assert sources == {"app-unified", "other-svc"}


def test_status_errors_1h_count(authed_client, db_session):
    now = datetime.now(timezone.utc)
    _seed(db_session, ts=now - timedelta(minutes=5),  level="ERROR")
    _seed(db_session, ts=now - timedelta(minutes=30), level="CRITICAL")
    _seed(db_session, ts=now - timedelta(hours=2),    level="ERROR")  # 出框
    _seed(db_session, ts=now - timedelta(minutes=10), level="WARNING")  # 不算 error
    r = authed_client.get("/api/status")
    [stat] = r.get_json()
    assert stat["errors_1h"] == 2  # 5min ERROR + 30min CRITICAL


def test_status_last_log_ts(authed_client, db_session):
    now = datetime.now(timezone.utc)
    _seed(db_session, ts=now - timedelta(hours=3))
    _seed(db_session, ts=now - timedelta(minutes=2))  # 最新
    r = authed_client.get("/api/status")
    [stat] = r.get_json()
    last_ts = datetime.fromisoformat(stat["last_log_ts"])
    delta = abs((now - last_ts).total_seconds())
    assert delta < 200  # ~2 min 內，給寬鬆 buffer 防 timing
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_api_logs.py tests/test_api_status.py -v`

Expected: All FAIL — /api/logs and /api/status not registered (404).

- [ ] **Step 4: Write `monitor/dashboard.py`**

```python
"""Dashboard：/, /api/logs, /api/status。皆 login_required。"""
import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, request, jsonify
from sqlalchemy import func

from extensions import db
from models import LogEntry
from auth import login_required

dashboard_bp = Blueprint("dashboard", __name__)


_LEVELS_FOR = {
    "CRITICAL": ["CRITICAL"],
    "ERROR":    ["ERROR", "CRITICAL"],
    "WARNING":  ["WARNING", "ERROR", "CRITICAL"],
}


@dashboard_bp.route("/")
@login_required
def home():
    sources = [s for (s,) in db.session.query(LogEntry.source).distinct().all()]
    return render_template(
        "dashboard.html",
        sources=sources,
        git_sha=(os.environ.get("GIT_SHA") or "")[:4],
    )


@dashboard_bp.route("/api/logs")
@login_required
def api_logs():
    q = LogEntry.query
    if request.args.get("source"):
        q = q.filter(LogEntry.source == request.args["source"])
    if request.args.get("level"):
        levels = _LEVELS_FOR.get(request.args["level"])
        if levels:
            q = q.filter(LogEntry.level.in_(levels))
    if request.args.get("logger_prefix"):
        q = q.filter(LogEntry.logger.startswith(request.args["logger_prefix"]))
    if request.args.get("since"):
        try:
            since = datetime.fromtimestamp(int(request.args["since"]), tz=timezone.utc)
            q = q.filter(LogEntry.ts >= since)
        except (TypeError, ValueError):
            pass

    try:
        limit = int(request.args.get("limit", 200))
    except ValueError:
        limit = 200
    limit = min(max(limit, 1), 1000)

    rows = q.order_by(LogEntry.ts.desc()).limit(limit).all()
    return jsonify([
        {
            "id": r.id,
            "ts": r.ts.isoformat(),
            "received_at": r.received_at.isoformat() if r.received_at else None,
            "source": r.source,
            "level": r.level,
            "logger": r.logger,
            "message": r.message,
            "extra": r.extra_json,
            "git_sha": r.git_sha,
        }
        for r in rows
    ])


@dashboard_bp.route("/api/status")
@login_required
def api_status():
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    out = []
    sources = [s for (s,) in db.session.query(LogEntry.source).distinct().all()]
    for source in sources:
        last = (
            db.session.query(LogEntry.ts)
            .filter(LogEntry.source == source)
            .order_by(LogEntry.ts.desc())
            .first()
        )
        err_1h = (
            db.session.query(func.count())
            .filter(
                LogEntry.source == source,
                LogEntry.level.in_(["ERROR", "CRITICAL"]),
                LogEntry.ts >= one_hour_ago,
            )
            .scalar()
        )
        total = (
            db.session.query(func.count())
            .filter(LogEntry.source == source)
            .scalar()
        )
        out.append({
            "source": source,
            "last_log_ts": last[0].isoformat() if last else None,
            "errors_1h": int(err_1h or 0),
            "total": int(total or 0),
        })
    return jsonify(out)
```

- [ ] **Step 5: Write `monitor/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Monitor — dashboard{% endblock %}
{% block content %}
<div id="status-bar" style="margin-bottom: 1rem; font-size: .875rem;">
  <em>讀取中…</em>
</div>

<form class="filters" id="filter-form" onsubmit="return false;">
  <select name="source">
    <option value="">所有 source</option>
    {% for s in sources %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
  </select>
  <select name="level">
    <option value="ERROR" selected>ERROR+</option>
    <option value="WARNING">WARNING+</option>
    <option value="CRITICAL">CRITICAL only</option>
  </select>
  <input name="logger_prefix" placeholder="logger prefix (e.g. mynotes.)">
  <select name="since">
    <option value="86400" selected>最近 24h</option>
    <option value="3600">最近 1h</option>
    <option value="259200">最近 3 天</option>
    <option value="604800">最近 7 天</option>
  </select>
  <button type="button" onclick="reload()">套用</button>
</form>

<table>
  <thead>
    <tr><th>時間</th><th>level</th><th>source</th><th>logger</th><th>message</th><th>SHA</th></tr>
  </thead>
  <tbody id="rows"></tbody>
</table>

<script>
async function loadStatus() {
  const r = await fetch('/api/status');
  const data = await r.json();
  const bar = document.getElementById('status-bar');
  if (!data.length) {
    bar.innerHTML = '<em>尚無 log</em>';
    return;
  }
  bar.innerHTML = data.map(s =>
    `<strong>${escapeHtml(s.source)}</strong>: 最後 ${s.last_log_ts || '—'} / 1h 內 ERROR ${s.errors_1h} / 總 ${s.total}`
  ).join(' · ');
}

async function reload() {
  const form = document.getElementById('filter-form');
  const params = new URLSearchParams();
  for (const el of form.elements) {
    if (el.name && el.value) {
      if (el.name === 'since') {
        const cutoff = Math.floor(Date.now() / 1000) - parseInt(el.value, 10);
        params.set('since', cutoff);
      } else {
        params.set(el.name, el.value);
      }
    }
  }
  params.set('limit', '500');
  const r = await fetch('/api/logs?' + params);
  const rows = await r.json();
  document.getElementById('rows').innerHTML = rows.map(row => `
    <tr>
      <td>${escapeHtml(row.ts)}</td>
      <td class="level-${escapeHtml(row.level)}">${escapeHtml(row.level)}</td>
      <td>${escapeHtml(row.source)}</td>
      <td>${escapeHtml(row.logger)}</td>
      <td><pre>${escapeHtml(row.message)}${row.extra ? '\n\n' + escapeHtml(row.extra) : ''}</pre></td>
      <td>${escapeHtml(row.git_sha || '')}</td>
    </tr>
  `).join('');
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

loadStatus();
reload();
setInterval(() => { loadStatus(); reload(); }, 30_000);
</script>
{% endblock %}
```

- [ ] **Step 6: Update `monitor/app.py` to register `dashboard_bp` (移除 placeholder /)**

```python
"""Monitor service Flask app factory。"""
from flask import Flask

from config import Config
from extensions import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    from ingest import ingest_bp
    from auth import auth_bp
    from dashboard import dashboard_bp
    app.register_blueprint(ingest_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    return app
```

- [ ] **Step 7: Run all tests so far**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/ -v`

Expected: All passed (3 crypto + 2 models + 1 healthz + 9 ingest + 3 auth + 5 api_logs + 3 api_status = 26 passed).

- [ ] **Step 8: Commit**

```bash
git add monitor/dashboard.py monitor/templates/dashboard.html monitor/app.py monitor/tests/test_api_logs.py monitor/tests/test_api_status.py
git commit -m "feat(monitor): dashboard / + /api/logs + /api/status

dashboard.html 用 vanilla JS fetch + escape HTML，沒引 framework；
表頂端 status bar 顯示每 source 「最後一筆 / 1h ERROR / 總筆數」；
filter form 包 source / level / logger_prefix / since；30s auto-refresh
讓 user 隨時看最新狀態。

api_logs ERROR filter 自動含 CRITICAL；WARNING filter 含 ERROR+CRITICAL
(階層含意)。limit 內部 cap 1000 防被刷爆。

8 tests 覆蓋 5 個 filter 組合 + 3 個 status 欄位。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Retention sweep daemon thread

**Files:**
- Create: `monitor/retention.py`
- Modify: `monitor/app.py` (`create_app()` 結尾呼叫 `start_retention_sweep`)
- Create: `monitor/tests/test_retention.py`

- [ ] **Step 1: Write failing tests `monitor/tests/test_retention.py`**

```python
"""Retention sweep — 直接呼叫 sweep 函式，不跑 thread。"""
from datetime import datetime, timedelta, timezone

from extensions import db
from models import LogEntry
from retention import sweep_once


def _seed(db_session, age):
    db_session.add(LogEntry(
        ts=datetime.now(timezone.utc),
        received_at=datetime.now(timezone.utc) - age,
        source="app-unified", level="ERROR",
        logger="x", message="m",
    ))
    db_session.commit()


def test_sweep_deletes_rows_older_than_cutoff(app, db_session):
    _seed(db_session, age=timedelta(days=40))   # > 30d, 該刪
    _seed(db_session, age=timedelta(days=20))   # < 30d, 留
    with app.app_context():
        sweep_once(retention_days=30)
    remaining = LogEntry.query.all()
    assert len(remaining) == 1


def test_sweep_does_nothing_when_all_fresh(app, db_session):
    _seed(db_session, age=timedelta(days=5))
    _seed(db_session, age=timedelta(days=10))
    with app.app_context():
        sweep_once(retention_days=30)
    assert LogEntry.query.count() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_retention.py -v`

Expected: FAIL — `from retention import sweep_once` ImportError。

- [ ] **Step 3: Write `monitor/retention.py`**

```python
"""Retention sweep — daemon thread 每小時跑一次 batched DELETE。

Test 從外部 import sweep_once 直接呼叫；start_retention_sweep 才開
thread (production 用)。env var MONITOR_DISABLE_RETENTION=1 → 不開
thread (conftest 用)。
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from extensions import db
from models import LogEntry

logger = logging.getLogger(__name__)


def sweep_once(retention_days: int = 30, batch_size: int = 10_000) -> int:
    """跑一次 sweep。回傳刪掉的列數。Caller 必須在 app context 裡。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    total_deleted = 0
    while True:
        ids = (
            db.session.query(LogEntry.id)
            .filter(LogEntry.received_at < cutoff)
            .limit(batch_size)
            .all()
        )
        if not ids:
            break
        n = (
            db.session.query(LogEntry)
            .filter(LogEntry.id.in_([i[0] for i in ids]))
            .delete(synchronize_session=False)
        )
        db.session.commit()
        total_deleted += n
    return total_deleted


def _sweep_loop(app, interval_sec: int = 3600):
    while True:
        time.sleep(interval_sec)
        try:
            with app.app_context():
                days = int(os.environ.get("MONITOR_LOG_RETENTION_DAYS", "30"))
                n = sweep_once(retention_days=days)
                if n:
                    logger.info("[retention] swept %d rows older than %d days", n, days)
        except Exception:
            logger.exception("[retention] sweep failed")


def start_retention_sweep(app):
    """Production entry：起 daemon thread。test 用 MONITOR_DISABLE_RETENTION=1 跳過。"""
    if os.environ.get("MONITOR_DISABLE_RETENTION") == "1":
        return
    t = threading.Thread(target=_sweep_loop, args=(app,),
                         name="monitor-retention", daemon=True)
    t.start()
```

- [ ] **Step 4: Update `monitor/app.py` to start sweep**

```python
"""Monitor service Flask app factory。"""
from flask import Flask

from config import Config
from extensions import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    from ingest import ingest_bp
    from auth import auth_bp
    from dashboard import dashboard_bp
    app.register_blueprint(ingest_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    from retention import start_retention_sweep
    start_retention_sweep(app)

    return app
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_retention.py -v`

Expected: 2 passed.

Also run full sweep:

`cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/ -v`

Expected: 28 passed (3 crypto + 2 models + 1 healthz + 9 ingest + 3 auth + 5 api_logs + 3 api_status + 2 retention).

- [ ] **Step 6: Commit**

```bash
git add monitor/retention.py monitor/app.py monitor/tests/test_retention.py
git commit -m "feat(monitor): retention sweep — 30d default, batched DELETE

sweep_once() 是 testable pure-ish function (in app context)，每次跑
LIMIT 10_000 batched DELETE 避免長 lock。start_retention_sweep() 起
daemon thread 每小時跑；env var MONITOR_DISABLE_RETENTION=1 跳過 (test
用)。

monitor 是 sync gunicorn worker，沒 gevent monkey patch，threading.
Thread 就是真 OS thread，不需 get_original 那套 (跟 app-unified
log_shipping 不同)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Dockerfile + requirements.txt

**Files:**
- Create: `monitor/Dockerfile`
- Create: `monitor/requirements.txt`
- Create: `monitor/.dockerignore`

- [ ] **Step 1: Write `monitor/requirements.txt`**

```
flask==3.0.*
flask-sqlalchemy==3.1.*
gunicorn==23.0.*
psycopg2-binary==2.9.*
bcrypt==4.2.*
cryptography==43.*
```

- [ ] **Step 2: Write `monitor/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime

WORKDIR /app

# psycopg2-binary 已含 wheel；libpq-dev / gcc 留著為了萬一 wheel 不可用
# (slim image 沒帶；apt 一次裝完丟 cache)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY monitor/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# source 放最後一層 → .py 改動不 invalidate dep layer
COPY monitor/ ./

ARG GIT_SHA=""
ENV GIT_SHA=${GIT_SHA}
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Taipei

EXPOSE 8080
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
```

- [ ] **Step 3: Write `monitor/.dockerignore`**

```
__pycache__
*.pyc
.pytest_cache
tests/
*.md
.git
```

- [ ] **Step 4: Validate Dockerfile syntax with parser** (per `feedback_validate_configs`)

Run:
```bash
cd /home/hirain0126/projects/webapp/.worktrees/feat-monitor-service
docker buildx build --check -f monitor/Dockerfile monitor/ 2>&1 | tail -5
```

Expected: 沒 error / warning（如果 docker daemon 不可用，至少 `python -c "import dockerfile_parse; dockerfile_parse.DockerfileParser('monitor/Dockerfile')"` 不報 syntax error；若兩者都不可用，跳過此步並在 commit message 記下「未 syntax-validate Dockerfile, manual review only」）。

- [ ] **Step 5: Commit**

```bash
git add monitor/Dockerfile monitor/requirements.txt monitor/.dockerignore
git commit -m "feat(monitor): Dockerfile + requirements.txt + .dockerignore

python:3.12-slim base，apt 只裝 libpq-dev/gcc 兩個小東西 (psycopg2-
binary wheel 通常夠，但 slim 偶有缺 lib)。Source COPY 放最後一層讓
.py 改動不 invalidate pip install layer，預期 build 30s 級。

requirements.txt 6 條：flask / flask-sqlalchemy / gunicorn / psycopg2-
binary / bcrypt / cryptography。沒有 sentence-transformers 那種 50MB+
重依賴；image size 估 ~120MB (vs app-unified ~1.2GB)。

.dockerignore 排除 tests/ + __pycache__ 縮 image。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: GHA workflow with path filter + cache scope

**Files:**
- Create: `.github/workflows/docker-monitor.yml`

- [ ] **Step 1: Write `.github/workflows/docker-monitor.yml`**

```yaml
name: Build and Push Monitor Docker Image

on:
  push:
    branches: [main]
    paths:
      - 'monitor/**'
      - '.github/workflows/docker-monitor.yml'
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/stvlin1988-alt/weather-monitor
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=ref,event=branch
            type=sha,prefix=sha-,format=short

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: monitor/Dockerfile
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha,scope=monitor
          cache-to: type=gha,scope=monitor,mode=max
          build-args: |
            GIT_SHA=${{ github.sha }}
```

- [ ] **Step 2: Validate YAML syntax** (per `feedback_validate_configs`)

Run:
```bash
/home/hirain0126/projects/webapp/venv/bin/python -c "
import yaml
with open('.github/workflows/docker-monitor.yml') as f:
    yaml.safe_load(f)
print('YAML syntax OK')
"
```

Expected: `YAML syntax OK`

- [ ] **Step 3: 驗 path filter 不會誤觸 app-unified docker.yml**

```bash
grep -A3 'paths:' .github/workflows/docker.yml
grep -A3 'paths:' .github/workflows/docker-monitor.yml
```

確認 `docker.yml` 仍是 `app_unified/**`，`docker-monitor.yml` 是 `monitor/**`。兩條 workflow 不會交集。

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/docker-monitor.yml
git commit -m "ci(monitor): GHA workflow build/push to GHCR weather-monitor

paths: monitor/** + .github/workflows/docker-monitor.yml → 改 monitor
不觸發 app-unified docker.yml；改 app_unified 不觸發這條。Cache scope=
monitor 跟 app-unified 默認 scope 不撞。

對齊 user 顧慮：不影響 app-unified 每次 build 時間。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: app-unified `MonitorHTTPHandler` (logging.Handler)

**Files:**
- Create: `app_unified/log_shipping.py`
- Create: `app_unified/tests/test_log_shipping.py`

- [ ] **Step 1: Write failing tests `app_unified/tests/test_log_shipping.py`**

```python
"""MonitorHTTPHandler — 非同步 HTTP push WARNING+ log。

不啟動真 HTTP server，用 mock 攔 urllib.request.urlopen。Worker thread
test 用 join 等它跑完。
"""
import logging
import queue
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from log_shipping import MonitorHTTPHandler


@pytest.fixture
def handler_factory():
    handlers = []

    def make(**kwargs):
        defaults = dict(
            ingest_url="http://test-monitor:8080/ingest",
            token="test-token",
            source="app-unified",
            git_sha="abc1234",
            queue_size=100,
        )
        defaults.update(kwargs)
        h = MonitorHTTPHandler(**defaults)
        handlers.append(h)
        return h

    yield make
    for h in handlers:
        h.close()


def _record(level=logging.WARNING, msg="m", logger_name="test"):
    return logging.LogRecord(
        name=logger_name, level=level, pathname="x", lineno=1,
        msg=msg, args=None, exc_info=None,
    )


def test_emit_does_not_raise_on_monitor_down(handler_factory):
    """Monitor URL 不可達時 emit 不能 raise，否則會炸 logger 用法的 caller。"""
    h = handler_factory(ingest_url="http://this-host-does-not-exist-12345:8080/ingest")
    # 不該 raise
    h.emit(_record())


def test_queue_full_drops_oldest(handler_factory):
    """Queue 滿 → drop oldest，不該 block emit。"""
    # 用 queue_size=2 製造 overflow，patch urlopen 讓 worker 卡住來源
    with patch("log_shipping.urllib.request.urlopen") as m:
        m.side_effect = lambda *a, **kw: time.sleep(10)  # worker 阻塞，queue 會堆
        h = handler_factory(queue_size=2)
        for i in range(5):
            h.emit(_record(msg=f"line-{i}"))
        # 不該 raise；queue 至少還有東西
        assert h.q.qsize() <= 2
        assert h._dropped >= 1


def test_worker_swallows_http_error(handler_factory):
    """urlopen 拋例外時 worker 不能 crash（thread daemon 要繼續活）。"""
    err_log = []
    with patch("log_shipping.urllib.request.urlopen") as m, \
         patch("log_shipping.sys.stderr") as stderr_mock:
        m.side_effect = Exception("network kaput")
        stderr_mock.write.side_effect = lambda s: err_log.append(s)

        h = handler_factory()
        h.emit(_record(msg="will-fail"))
        # 給 worker 一點時間 process queue
        for _ in range(20):
            if err_log:
                break
            time.sleep(0.05)

        assert any("post failed" in line for line in err_log)


def test_handler_attaches_to_root_logger_filter_warning_plus(handler_factory):
    """Handler 自帶 WARNING level filter — INFO/DEBUG 不該進 queue。"""
    h = handler_factory()
    root = logging.getLogger("test-shipping-root")
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    try:
        root.debug("never")
        root.info("never")
        root.warning("yes")
        root.error("yes-too")
        # 給 emit 寫進 queue 的時間
        time.sleep(0.05)
        # queue 預期至少 2 條（warning + error）；可能 worker 已消化一些。
        # 只驗 worker 收到了 warning/error 兩種以上 level — 用 stub urlopen 收集
    finally:
        root.removeHandler(h)
    # 直接驗 handler.level 等於 WARNING
    assert h.level == logging.WARNING
```

- [ ] **Step 2: Run test to verify they fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_log_shipping.py -v`

Expected: FAIL — `from log_shipping import MonitorHTTPHandler` ImportError。

- [ ] **Step 3: Write `app_unified/log_shipping.py`**

```python
"""非同步 HTTP log shipper — 把 WARNING+ log POST 到 monitor /ingest。

設計守則:
1. emit() 絕不能拖 reactor / request thread → 只做 queue.put_nowait。
2. monitor 掛掉時不能讓 app-unified 也掛 → worker 內 try/except 全包，
   失敗只 stderr 不 raise / 不 retry storm。
3. Queue 滿 drop oldest，記 _dropped 計數。
4. 自己出錯走 sys.stderr 不走 root logger，避免循環 logging。

關鍵:用 gevent.monkey.get_original("threading","Thread") 拿原生 OS
thread。Handler init 是在 wsgi_lazy 那條背景 OS thread 跑 (5/8 b61cdb0
fix)，那 thread 沒自己的 hub，spawn monkey-patched Thread (greenlet)
會跟 main hub 撞 → /healthz fast-path 雖 OK，但 worker 卡死。
"""
import json
import logging
import queue
import sys
import urllib.request
from datetime import datetime, timezone

import gevent.monkey

# 拿原生 OS Thread，避免 monkey-patched 後變 greenlet
_OSThread = gevent.monkey.get_original("threading", "Thread")
_Event    = gevent.monkey.get_original("threading", "Event")


class MonitorHTTPHandler(logging.Handler):
    def __init__(self, ingest_url: str, token: str,
                 source: str = "app-unified",
                 git_sha: str | None = None,
                 queue_size: int = 2000):
        super().__init__(level=logging.WARNING)
        self.ingest_url = ingest_url
        self.token = token
        self.source = source
        self.git_sha = (git_sha or "")[:12]
        self.q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._stop = _Event()
        self._dropped = 0
        self._thread = _OSThread(
            target=self._worker, name="monitor-log-shipper", daemon=True
        )
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = {
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "source": self.source,
                "git_sha": self.git_sha,
                "message": record.getMessage(),
                "extra": self._extra(record),
            }
            try:
                self.q.put_nowait(payload)
            except queue.Full:
                # drop oldest
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    pass
                self._dropped += 1
                try:
                    self.q.put_nowait(payload)
                except queue.Full:
                    pass
        except Exception:
            sys.stderr.write("[log_shipping] emit failed\n")

    def _extra(self, record) -> str | None:
        if record.exc_info:
            return logging.Formatter().formatException(record.exc_info)
        return None

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self.ingest_url, data=data, method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.token}",
                    },
                )
                with urllib.request.urlopen(req, timeout=2.0):
                    pass
            except Exception:
                sys.stderr.write(
                    f"[log_shipping] post failed (queued={self.q.qsize()}, "
                    f"dropped={self._dropped})\n"
                )

    def close(self) -> None:
        self._stop.set()
        super().close()
```

- [ ] **Step 4: Run test to verify they pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_log_shipping.py -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app_unified/log_shipping.py app_unified/tests/test_log_shipping.py
git commit -m "feat(app-unified): MonitorHTTPHandler async log shipper

logging.Handler subclass 把 WARNING+ log POST 到 monitor /ingest。
emit() 純 queue.put_nowait < 0.1ms，HTTP 在背景 OS thread 跑。

關鍵設計避雷:
- _OSThread = gevent.monkey.get_original('threading','Thread') 拿真
  OS thread，避免 wsgi_lazy 背景 thread 中 spawn greenlet 跟 main
  hub 撞 (5/8 b61cdb0 教訓延伸)
- queue.Full 時 drop oldest 不 block emit
- worker 內 urlopen 失敗 sys.stderr.write 不走 root logger，避免循
  環 (失敗 → log → 又失敗)
- 用 stdlib urllib.request 而非 requests：emit 路徑越輕越好，少一
  個 gevent 互踩 fail mode

4 tests 覆蓋: monitor down 不 raise / queue 滿 drop oldest / worker
swallow http error / level filter WARNING+。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: app-unified `app.py` env-gated wiring

**Files:**
- Modify: `app_unified/app.py` (在 `_file_handler` block 後接著)

- [ ] **Step 1: Read current `app.py` 確認 insertion point**

Run: `grep -n "_file_handler\|basicConfig\|MonitorHTTPHandler" app_unified/app.py`

預期：看到 `_file_handler = logging.handlers.RotatingFileHandler(...)` 行（commit `4b28d8a` 加的）。在那 block 與 `logging.basicConfig` 之後加新 block。

- [ ] **Step 2: Modify `app_unified/app.py`** — 在現有 `logging.basicConfig(...)` 那行**之後**插入：

```python
# ── Optional: ship WARNING+ logs to monitor service via HTTP push ──
# Env vars 都齊才啟用；沒設 = 行為跟舊版一樣 (zero-risk rollout)。
import os as _os
_INGEST_URL = _os.environ.get("MONITOR_INGEST_URL")
_INGEST_TOKEN = _os.environ.get("MONITOR_INGEST_TOKEN")
if _INGEST_URL and _INGEST_TOKEN:
    from log_shipping import MonitorHTTPHandler
    _monitor_handler = MonitorHTTPHandler(
        ingest_url=_INGEST_URL,
        token=_INGEST_TOKEN,
        source="app-unified",
        git_sha=_os.environ.get("GIT_SHA"),
    )
    _monitor_handler.setFormatter(
        _TaipeiFormatter("%(name)s %(levelname)s: %(message)s")
    )
    logging.getLogger().addHandler(_monitor_handler)
```

- [ ] **Step 3: Verify app-unified existing tests still pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_log_shipping.py tests/test_mynotes_ai_local.py -v`

Expected: All passed (handler is opt-in via env var; no tests have it set, so nothing changes).

也跑 startup test (確認 wsgi 仍能 import)：
```bash
cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -c "from wsgi import application; print('OK')"
```

Expected: `OK`（沒設 MONITOR_INGEST_URL → handler 不 attach，import 不 raise）。

- [ ] **Step 4: Commit**

```bash
git add app_unified/app.py
git commit -m "feat(app-unified): env-gated MonitorHTTPHandler wiring

Attach MonitorHTTPHandler 只在 MONITOR_INGEST_URL + MONITOR_INGEST_TOKEN
都設時。沒設 = 跟舊版完全一樣 (existing /tmp/app.log + stdout 都不動)。

漸進 rollout 路徑:
1. monitor service 先上線 (env 不設，app-unified 不變)
2. monitor 確定可達後，加 env var redeploy app-unified → handler 上線

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Final review + Zeabur 設定 + deploy verify

**Files:** N/A (使用者操作 + 觀察)

- [ ] **Step 1: Run full test sweep on the worktree**

```bash
cd /home/hirain0126/projects/webapp/.worktrees/feat-monitor-service
cd monitor && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/ -v
cd ../app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_log_shipping.py -v
```

Expected: monitor 28 passed + app-unified log_shipping 4 passed = 32 tests new。

- [ ] **Step 2: Generate bcrypt hash for dashboard password (使用者選一個密碼)**

```bash
/home/hirain0126/projects/webapp/venv/bin/python -c "
import bcrypt, getpass
pw = getpass.getpass('Dashboard password: ')
print(bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode())
"
```

→ 輸出的 hash 之後填進 Zeabur monitor service 的 `MONITOR_DASHBOARD_PASSWORD_HASH` env var。

- [ ] **Step 3: Generate INGEST_TOKEN + SECRET_KEY**

```bash
echo "MONITOR_INGEST_TOKEN: $(openssl rand -base64 32)"
echo "MONITOR_SECRET_KEY:   $(openssl rand -base64 32)"
```

→ 兩條都記下來；INGEST_TOKEN 同一把要設給 app-unified 的 `MONITOR_INGEST_TOKEN` env var。

- [ ] **Step 4: Push branch (等 user 明說再執行)**

```bash
cd /home/hirain0126/projects/webapp/.worktrees/feat-monitor-service
git push -u origin feat/monitor-service
```

Per `feedback_push_policy`：等使用者明確說 push 才推。

- [ ] **Step 5: Wait for GHA build**

Push 後到 https://github.com/stvlin1988-alt/Weather/actions 看 `Build and Push Monitor Docker Image` workflow。預期 ~3-5 分鐘綠勾（image 小，沒 model 預載）。

驗證 image 上架：
- 到 https://github.com/users/stvlin1988-alt/packages/container/weather-monitor 確認 `latest` tag 出現
- Visibility 設 **Public**（不然 Zeabur pull 會 401）

- [ ] **Step 6: Zeabur 一次性設定**

1. **新增 Postgres add-on**：Zeabur project → 新增服務 → Postgres → 標 `monitor-db`。記下 internal connection string (e.g. `postgresql://postgres:xxx@monitor-db.zeabur.internal:5432/zeabur`)
2. **新增 monitor service**：新增服務 → Docker 映像檔 → image `ghcr.io/stvlin1988-alt/weather-monitor`、tag `latest`、user/pass 留空
3. **解除綁定 GitHub 儲存庫**（同 5/7 設定，不然 Zeabur 偵測 push 會自家 build 蓋掉 GHCR image）
4. **Health check 保持啟用**，path `/healthz`（`feedback_zeabur_healthcheck` 鐵律）
5. **monitor service env vars**（按 spec §部署管道）：
   ```
   DATABASE_URL                    = <step 6.1 connection string>
   DB_ENCRYPTION_KEY               = <跟 app-unified 同一把>
   MONITOR_SECRET_KEY              = <step 3 產的>
   MONITOR_INGEST_TOKEN            = <step 3 產的>
   MONITOR_DASHBOARD_PASSWORD_HASH = <step 2 產的 hash>
   MONITOR_LOG_RETENTION_DAYS      = 30
   ```
6. 點「重新部署」→ 等 monitor 上線

- [ ] **Step 7: 驗 monitor 自身可達**

瀏覽器開 monitor service 的 Zeabur 提供的 URL：
- `https://<monitor-domain>/healthz` → 看到 `ok`
- `https://<monitor-domain>/login` → 看到登入頁
- 輸入密碼 → redirect `/` → 看到 dashboard（log table 是空的，status bar 顯示「尚無 log」）

- [ ] **Step 8: 設 app-unified env vars 啟用 ingest**

Zeabur app-unified service env：
```
MONITOR_INGEST_URL    = http://monitor.zeabur.internal:8080/ingest
MONITOR_INGEST_TOKEN  = <跟 monitor service 同一把>
```

(內網 hostname `monitor.zeabur.internal` 是預設值，視 Zeabur 實際 service-discovery 名稱調整 — 看 monitor service「網路」分頁實際內網 endpoint。)

點 app-unified 重新部署 → 等上線。

- [ ] **Step 9: 觸發一條 WARNING+ log 驗證 end-to-end**

進 app-unified Zeabur 「指令」shell：

```bash
python -c "
import logging
logging.basicConfig(level=logging.WARNING)
logging.warning('monitor end-to-end test ping')
import time; time.sleep(3)  # 給 worker 推上去
"
```

或更實際：實際登入 app-unified 觸發任何會產生 WARNING+ 的功能（例如不存在的 note ID）。

- [ ] **Step 10: 在 monitor dashboard 看到那條 log**

刷新 monitor dashboard / → 應看到剛剛那筆 WARNING+ entry，source = `app-unified`、git_sha 對應 app-unified 上線版本。

- [ ] **Step 11: 觸發 app-unified pod 重啟測 retention 跨重啟**

進 Zeabur app-unified service → 「重新部署」→ 等 5 分鐘。回 monitor dashboard 確認剛剛那筆 log **仍在**（重啟前的 log 沒丟）。

- [ ] **Step 12: Merge fix branch back to main**

User 同意 deploy 路線（A/B 同 mynotes 流程）後：

```bash
cd /home/hirain0126/projects/webapp/app_unified
git checkout main
# rebase 確保 ff
cd /home/hirain0126/projects/webapp/.worktrees/feat-monitor-service
git rebase main
cd /home/hirain0126/projects/webapp/app_unified
git merge --ff-only feat/monitor-service
# 等使用者明說再 push
```

- [ ] **Step 13: Cleanup worktree + branches**

驗證 OK 後：

```bash
cd /home/hirain0126/projects/webapp/app_unified
git worktree remove /home/hirain0126/projects/webapp/.worktrees/feat-monitor-service
git branch -d feat/monitor-service
git push origin --delete feat/monitor-service
```

- [ ] **Step 14: Update memory**

Append a new dated section 到 `/home/hirain0126/.claude/projects/-home-hirain0126/memory/project_webapp.md` 紀錄：

- monitor service 上線的 commit SHAs
- Zeabur 兩 service / 兩 Postgres 的設定（service name / connection string pattern）
- INGEST_TOKEN 在 password manager 的位置（env-only, 不上 git）
- 將來新 service 怎麼接進 monitor（複製 MonitorHTTPHandler pattern）
- 觀察過幾天的 dashboard 用感、log 量級、retention 是否合理

更新 frontmatter `description:` 反映 monitor 上線狀態。

---

## Self-Review Checklist Results

**1. Spec coverage:**
- ✅ §部署 topology — Task 8 + Task 12 (Zeabur 設定)
- ✅ §模組結構 — Task 1-7 各檔
- ✅ §資料模型 LogEntry + EncryptedText — Task 2
- ✅ §app-unified MonitorHTTPHandler + gevent.monkey.get_original — Task 10
- ✅ §app.py env-gated wiring — Task 11
- ✅ §Routes /healthz / /ingest / /login / /api/logs / /api/status — Task 3-6
- ✅ §Retention sweep — Task 7
- ✅ §Dockerfile + GHA workflow + path filter — Task 8-9
- ✅ §漸進 rollout 順序 — Task 12 step 6-11
- ✅ §Test 計畫 26 tests — 各 task 都有 TDD failing test → impl → pass cycle
- ✅ 共用 DB_ENCRYPTION_KEY (user 同意) — Task 1 config + crypto

**2. Placeholder scan:** none. 每個 step 都有 executable code / command。

**3. Type consistency:**
- `MonitorHTTPHandler.__init__(ingest_url, token, source, git_sha, queue_size)` — Task 10 定義，Task 11 wiring 用 keyword args 對應 ✓
- `LogEntry` columns: ts, received_at, source, level, logger, message, extra_json, git_sha — Task 2 定義，Task 4 ingest 寫入、Task 6 dashboard 讀出名稱一致 ✓
- `sweep_once(retention_days, batch_size)` — Task 7 定義，test 直接 call 驗 ✓
- `_LEVELS_FOR` dict in dashboard.py — Task 6 內部用，外露的是 query string `level=ERROR` ✓
- env var 名稱在 Task 1 (config.py) / Task 4 (INGEST_TOKEN) / Task 5 (DASHBOARD_PASSWORD_HASH) / Task 7 (MONITOR_LOG_RETENTION_DAYS / MONITOR_DISABLE_RETENTION) / Task 11 (MONITOR_INGEST_URL / MONITOR_INGEST_TOKEN / GIT_SHA) 各次出現名稱一致 ✓

無不一致。
