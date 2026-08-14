# 最小可用版 Monitor Service 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一個獨立於 app-unified 的 Zeabur service，持續從外部探測站台、並接住 app-unified 在「卡死當下」推出來的診斷事件，讓 2026-08 每晚 20:00 連不上的問題有可回溯的證據。

**Architecture:** `monitor/` 是全新 top-level 目錄，獨立 container、獨立 gunicorn（**純 sync，不用 gevent**）。它做三件事：(1) 背景執行緒每 N 秒分層探測「對外網域（走 Cloudflare）」與「app-unified origin」，把分段計時與判定寫進 DB；(2) `/ingest` 收 app-unified 推來的診斷事件（Bearer token）；(3) `/` 一個密碼保護頁面呈現異常摘要。資料寫**現有** Postgres 的 `mon_` 前綴新表，app-unified 從不讀寫這兩張表。

**Tech Stack:** Flask 3 + Flask-SQLAlchemy + gunicorn(sync worker) + psycopg2；測試用 pytest + SQLite。

## 背景與設計依據

這是 `docs/superpowers/specs/2026-05-08-monitor-service-design.md` 的**縮減版**。原 spec 的獨立 Postgres、欄位加密、log shipping handler、GHA workflow、完整 dashboard 都**不在本次範圍**——那份仍然有效，將來要長成完整版時照它走。

本次只解決一個問題：**尖峰時段連不上，但事後查不到任何證據。**

證據會從兩個來源進來：

| 來源 | 回答什麼問題 |
|---|---|
| 探測取樣（monitor 主動） | 壞在哪一層：DNS / TCP / TLS / app 不回應 / 5xx |
| 診斷事件（app-unified 推送） | 是什麼卡住它：hub 被阻塞多久、當下的堆疊 |

### 為什麼事件必須「推」而不是「拉」

hub 被同步工作卡住時，app-unified 裡**任何 greenlet 都跑不動**——包含負責上報的那個。等 hub 恢復再上報通常已經太遲：gunicorn `timeout=60` 會 SIGKILL 該 worker，`/tmp/app.log` 隨容器重啟被清空，證據就永久消失。

所以上報必須由 `hub_watchdog` 的**真 OS thread** 當場送出。而真 OS thread **不能碰被 monkey patch 過的 socket**——那正是 prod 2026-05-09 出現過的
`using gevent objects from multiple threads ... crossing thread boundaries is not allowed`。

因此 Task 7 的上報一律用 `gevent.monkey.get_original` 取回未被 patch 的 `socket` 與 `getaddrinfo`，並且**只走內網明文 HTTP**（不引入 TLS，避免同樣要處理被 patch 的 `ssl` 模組）。

### 為什麼可以不做欄位加密

原 spec 要加密是因為它打算收 app-unified **全部** WARNING+ log，那可能帶到使用者資料。本次 `/ingest` 只收 `kind="hub_block"` 這類診斷事件，內容是阻塞秒數與**程式碼堆疊**，結構上不含個資。

**這是一條必須守住的界線**：日後若要擴大到收一般 app log，必須先補上 `EncryptedText`（照原 spec Task 2）才能開。

## Global Constraints

- Python 3.12（與 prod base image 一致）
- `monitor/` 與 `app_unified/` **0 cross-import**。共用邏輯用檔案複製，不用 import。兩者是兩個 container、兩個 process，誰崩誰活互不波及。
- monitor **不得使用 gevent**，不得 monkey patch。純 sync gunicorn。
- monitor gunicorn `workers = 1`。多個 worker 會各跑一份探測執行緒 → 重複取樣。
- Docker build context = `monitor/`（不是 repo root）。`monitor/` 必須自給自足。
- 時間一律存 UTC，顯示時轉台灣時間（UTC+8）。
- 任何情況下 monitor 故障都**不得影響 app-unified 服務**：上報失敗一律吞掉。
- 計畫文件與程式碼中**不得寫入實際網域**，一律用環境變數與 `<placeholder>`。

## File Structure

| 檔案 | 責任 |
|---|---|
| `monitor/probe_core.py` | 分層 HTTP/DNS 探測與判定。純函式，無 DB、無 Flask |
| `monitor/models.py` | `db` 實例 + `ProbeSample`、`Event` 兩個 model |
| `monitor/prober.py` | 背景執行緒：定時呼叫 probe_core → 寫 DB → 定時清老資料 |
| `monitor/app.py` | `create_app()`、`/healthz`、`/ingest`、`/login`、`/` |
| `monitor/templates/dashboard.html` | 異常摘要 + 事件列表 + 取樣表格 |
| `monitor/templates/login.html` | 單一密碼登入 |
| `monitor/wsgi.py` | gunicorn 進入點 |
| `monitor/gunicorn.conf.py` | sync worker、workers=1 |
| `monitor/requirements.txt` | 依賴 |
| `monitor/Dockerfile` | build context = `monitor/` |
| `monitor/zbpack.json` | `use_dockerfile: true` |
| `monitor/tests/conftest.py` | app/client/db fixture（SQLite） |
| `monitor/tests/test_*.py` | 各 task 的測試 |
| `app_unified/monitor_ship.py` | **app-unified 端**：unpatched socket 上報 |
| `app_unified/hub_watchdog.py` | 修改：偵測到阻塞時同時上報 |

---

## Task 1: `monitor/` 骨架 + 分層探測核心

**Files:**
- Create: `monitor/probe_core.py`
- Test: `monitor/tests/test_probe_core.py`

**Interfaces:**
- Produces: `probe(host: str, path: str, *, port: int = 443, use_tls: bool = True, timeout: float = 15.0) -> dict`
  回傳 dict 鍵：`verdict`(str), `code`(int|None), `err`(str|None), `t_dns`/`t_conn`/`t_tls`/`t_ttfb`/`t_total`(float|None), `cf_ray`(str|None), `app`(dict|None，含 `pid`/`uptime_s`)
  `verdict` 值域：`ok` / `slow` / `dns_fail` / `conn_fail` / `tls_fail` / `ttfb_hang` / `read_fail` / `http_4xx` / `http_5xx` / `bad_response`
- Produces: `resolve(name: str, resolver: str) -> list[str]`

- [ ] **Step 1: 寫失敗的測試**

```python
# monitor/tests/test_probe_core.py
"""分層探測：把「連不上」拆成可指認的層。

分段是重點 —— 「TCP 就接不上」和「TCP 通了但 app 不吐 byte」是完全不同的病，
前者要查網路/邊緣，後者要查 app。混在一起就查不下去。
"""
import socket
import threading

import pytest

from probe_core import probe


def _serve(handler):
    """起一個 localhost server，回傳 port。handler(conn) 決定它怎麼回應。"""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    port = s.getsockname()[1]
    held = []

    def loop():
        while True:
            try:
                conn, _ = s.accept()
            except OSError:
                return
            held.append(conn)      # 留住連線，否則 GC 會關掉它
            handler(conn)

    threading.Thread(target=loop, daemon=True).start()
    return port


def test_ok_response_is_classified_ok():
    def h(c):
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        c.close()
    port = _serve(h)
    r = probe("127.0.0.1", "/healthz", port=port, use_tls=False, timeout=3)
    assert r["verdict"] == "ok"
    assert r["code"] == 200


def test_connection_refused_is_conn_fail():
    r = probe("127.0.0.1", "/healthz", port=1, use_tls=False, timeout=3)
    assert r["verdict"] == "conn_fail"


def test_accepts_but_never_replies_is_ttfb_hang():
    """這是 gevent hub 被卡住時從外面看到的樣子 —— 最關鍵的一格。"""
    port = _serve(lambda c: None)          # 收下連線，永不回應
    r = probe("127.0.0.1", "/healthz", port=port, use_tls=False, timeout=1)
    assert r["verdict"] == "ttfb_hang"
    assert r["t_conn"] is not None, "TCP 有接上，必須記錄下來"
    assert r["t_ttfb"] >= 1


def test_5xx_captures_cf_ray_header():
    def h(c):
        c.recv(4096)
        c.sendall(b"HTTP/1.1 502 Bad Gateway\r\nCf-Ray: abc-TPE\r\n"
                  b"Content-Length: 9\r\n\r\nError 522")
        c.close()
    port = _serve(h)
    r = probe("127.0.0.1", "/", port=port, use_tls=False, timeout=3)
    assert r["verdict"] == "http_5xx"
    assert r["cf_ray"] == "abc-TPE"


def test_parses_healthz_pid_and_uptime():
    """app-unified 的 /healthz 回 pid —— pid 變了代表 worker 被重啟過。"""
    body = b'{"status":"ok","pid":4242,"uptime_s":88.5}'
    def h(c):
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s"
                  % (len(body), body))
        c.close()
    port = _serve(h)
    r = probe("127.0.0.1", "/healthz", port=port, use_tls=False, timeout=3)
    assert r["app"] == {"pid": 4242, "uptime_s": 88.5}


def test_plain_text_healthz_does_not_crash():
    """舊版 /healthz 回純文字 'ok' —— 不能因此爆掉。"""
    def h(c):
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        c.close()
    port = _serve(h)
    r = probe("127.0.0.1", "/healthz", port=port, use_tls=False, timeout=3)
    assert r["verdict"] == "ok"
    assert r["app"] is None
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd monitor && ../venv/bin/python -m pytest tests/test_probe_core.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'probe_core'`

- [ ] **Step 3: 實作**

```python
# monitor/probe_core.py
"""分層 HTTP/DNS 探測。

與 app_unified/scripts/probe_outage.py 同源，**刻意複製而非 import**：
monitor 與 app-unified 是兩個 container、兩個 process，誰崩誰活互不波及
（沿用 2026-05-08 monitor spec 的隔離守則）。

每次探測拆成四段計時：DNS → TCP connect → TLS handshake → 第一個 byte。
分段才分得出「網路到不了」與「app 不回應」。
"""
import json
import socket
import ssl
import subprocess
import time

SLOW_SECONDS = 3.0
_MAX_BODY = 200_000


def resolve(name: str, resolver: str) -> list[str]:
    """問指定 resolver 拿 A 記錄。解不出來回空 list。"""
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", "A", name, f"@{resolver}"],
            capture_output=True, text=True, timeout=8,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    return [ln.strip() for ln in out.splitlines()
            if ln.strip() and ln.strip()[0].isdigit()]


def probe(host, path, *, port=443, use_tls=True, timeout=15.0) -> dict:
    r = {"verdict": "ok", "code": None, "err": None, "cf_ray": None, "app": None,
         "t_dns": None, "t_conn": None, "t_tls": None, "t_ttfb": None, "t_total": None}
    t0 = time.monotonic()
    sock = None
    try:
        try:
            addrs = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        except socket.gaierror as e:
            r.update(verdict="dns_fail", err=f"getaddrinfo: {e}")
            return r
        r["t_dns"] = round(time.monotonic() - t0, 3)

        t = time.monotonic()
        try:
            sock = socket.create_connection(addrs[0][4], timeout=timeout)
        except OSError as e:
            r.update(verdict="conn_fail", err=f"tcp: {e}",
                     t_conn=round(time.monotonic() - t, 3))
            return r
        r["t_conn"] = round(time.monotonic() - t, 3)

        if use_tls:
            t = time.monotonic()
            try:
                sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=host)
            except Exception as e:
                r.update(verdict="tls_fail", err=f"tls: {type(e).__name__}: {e}",
                         t_tls=round(time.monotonic() - t, 3))
                return r
            r["t_tls"] = round(time.monotonic() - t, 3)

        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
               "User-Agent: monitor-probe/1\r\nCache-Control: no-cache\r\n"
               "Connection: close\r\n\r\n")
        t = time.monotonic()
        sock.sendall(req.encode())
        sock.settimeout(timeout)
        chunks, first_at = [], None
        try:
            while True:
                b = sock.recv(65536)
                if first_at is None:
                    first_at = time.monotonic()
                if not b:
                    break
                chunks.append(b)
                if sum(len(c) for c in chunks) > _MAX_BODY:
                    break
        except (socket.timeout, TimeoutError):
            if first_at is None:
                # TCP/TLS 都通，app 一個 byte 都不吐 —— hub 被卡住的典型樣貌
                r.update(verdict="ttfb_hang",
                         err=f"no response byte within {timeout}s",
                         t_ttfb=round(time.monotonic() - t, 3))
                return r
        except OSError as e:
            r.update(verdict="read_fail", err=f"read: {e}")
            return r

        r["t_ttfb"] = round((first_at or time.monotonic()) - t, 3)
        raw = b"".join(chunks)
        head, _, body = raw.partition(b"\r\n\r\n")
        head_s = head.decode("latin-1", "replace")
        try:
            r["code"] = int(head_s.split(None, 2)[1])
        except (IndexError, ValueError):
            r.update(verdict="bad_response", err=f"unparsable: {head_s[:120]!r}")
            return r
        for line in head_s.splitlines():
            if line.lower().startswith("cf-ray:"):
                r["cf_ray"] = line.split(":", 1)[1].strip()
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
            if isinstance(payload, dict) and "pid" in payload:
                r["app"] = {"pid": payload.get("pid"),
                            "uptime_s": payload.get("uptime_s")}
        except (ValueError, UnicodeDecodeError):
            pass   # 舊版 /healthz 回純文字，不是錯誤

        if r["code"] >= 500:
            r["verdict"] = "http_5xx"
        elif r["code"] >= 400:
            r["verdict"] = "http_4xx"
        return r
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if r["t_total"] is None:
            r["t_total"] = round(time.monotonic() - t0, 3)
        if r["verdict"] == "ok" and r["t_total"] > SLOW_SECONDS:
            r["verdict"] = "slow"
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd monitor && ../venv/bin/python -m pytest tests/test_probe_core.py -q`
Expected: PASS，6 passed

- [ ] **Step 5: Commit**

```bash
git add monitor/probe_core.py monitor/tests/test_probe_core.py
git commit -m "feat(monitor): 分層 HTTP/DNS 探測核心"
```

---

## Task 2: 資料模型

**Files:**
- Create: `monitor/models.py`, `monitor/tests/conftest.py`
- Test: `monitor/tests/test_models.py`

**Interfaces:**
- Consumes: 無
- Produces: `db`（Flask-SQLAlchemy 實例）、`ProbeSample`、`Event`
  - `ProbeSample(ts, target, verdict, code, t_dns, t_conn, t_tls, t_ttfb, t_total, cf_ray, app_pid, app_uptime_s, err)`；`target` ∈ {`public`, `origin`}
  - `Event(ts, source, kind, severity, summary, detail)`；`kind` 目前只有 `hub_block`

- [ ] **Step 1: 寫失敗的測試**

```python
# monitor/tests/conftest.py
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["MONITOR_PASSWORD"] = "test-pw"
    os.environ["MONITOR_INGEST_TOKEN"] = "test-token"
    os.environ["MONITOR_DISABLE_PROBER"] = "1"   # 測試不要背景執行緒亂跑
    from app import create_app
    application = create_app()
    with application.app_context():
        from models import db
        db.create_all()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()
```

```python
# monitor/tests/test_models.py
from datetime import datetime, timezone


def test_probe_sample_roundtrip(app):
    from models import db, ProbeSample
    s = ProbeSample(ts=datetime.now(timezone.utc), target="public",
                    verdict="ttfb_hang", t_conn=0.05, t_ttfb=15.0,
                    t_total=15.1, err="no response byte")
    db.session.add(s)
    db.session.commit()
    got = ProbeSample.query.one()
    assert got.target == "public"
    assert got.verdict == "ttfb_hang"
    assert got.code is None


def test_event_stores_full_stack_without_truncation(app):
    """堆疊是指認兇手的唯一線索，不能被截斷。"""
    from models import db, Event
    stack = "x" * 20000
    e = Event(ts=datetime.now(timezone.utc), source="app-unified",
              kind="hub_block", severity="warning",
              summary="hub blocked 42.0s", detail=stack)
    db.session.add(e)
    db.session.commit()
    assert len(Event.query.one().detail) == 20000
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd monitor && ../venv/bin/python -m pytest tests/test_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: 實作**

```python
# monitor/models.py
"""monitor 自己的資料表。

寫在 app-unified 既有的 Postgres 裡，但用 mon_ 前綴獨立成兩張表，
app-unified 從不讀寫它們。這是「最小可用版」的取捨：省掉另建一套 DB 的
運維成本；完整隔離版見 2026-05-08 monitor spec。

不做欄位加密：本服務只收 hub_block 這類診斷事件（阻塞秒數 + 程式碼堆疊），
結構上不含個資。日後若要擴大收一般 app log，**必須先補加密**。
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class ProbeSample(db.Model):
    __tablename__ = "mon_probe_samples"

    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    target = db.Column(db.String(16), nullable=False, index=True)  # public / origin
    verdict = db.Column(db.String(20), nullable=False, index=True)
    code = db.Column(db.Integer)
    t_dns = db.Column(db.Float)
    t_conn = db.Column(db.Float)
    t_tls = db.Column(db.Float)
    t_ttfb = db.Column(db.Float)
    t_total = db.Column(db.Float)
    cf_ray = db.Column(db.String(40))
    app_pid = db.Column(db.Integer)          # pid 變動 = worker 被重啟
    app_uptime_s = db.Column(db.Float)
    err = db.Column(db.Text)


class Event(db.Model):
    __tablename__ = "mon_events"

    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    source = db.Column(db.String(32), nullable=False)     # app-unified
    kind = db.Column(db.String(32), nullable=False, index=True)  # hub_block
    severity = db.Column(db.String(16), nullable=False)
    summary = db.Column(db.String(500), nullable=False)
    detail = db.Column(db.Text)              # 完整堆疊，不截斷
```

- [ ] **Step 4: 執行測試，確認通過**（需先有 Task 3 的 `create_app`；若尚未實作，本步驟與 Task 3 Step 4 合併執行）

Run: `cd monitor && ../venv/bin/python -m pytest tests/test_models.py -q`
Expected: PASS，2 passed

- [ ] **Step 5: Commit**

```bash
git add monitor/models.py monitor/tests/
git commit -m "feat(monitor): ProbeSample / Event 資料模型"
```

---

## Task 3: app factory + `/healthz` + `/ingest`

**Files:**
- Create: `monitor/app.py`
- Test: `monitor/tests/test_ingest.py`

**Interfaces:**
- Consumes: `models.db`, `models.Event`
- Produces: `create_app() -> Flask`；`POST /ingest`（Bearer token）；`GET /healthz`

- [ ] **Step 1: 寫失敗的測試**

```python
# monitor/tests/test_ingest.py
"""/ingest 是 app-unified 卡死當下唯一能送出證據的管道。

它必須：擋掉沒授權的、吞不下的資料要明確拒絕、而且絕不能因為欄位缺失就 500
（app-unified 那端是 fire-and-forget，500 對它沒意義，只會讓證據靜靜消失）。
"""
import json


def _post(client, payload, token="test-token"):
    return client.post("/ingest", data=json.dumps(payload),
                       content_type="application/json",
                       headers={"Authorization": f"Bearer {token}"})


def test_healthz_ok(client):
    assert client.get("/healthz").status_code == 200


def test_rejects_missing_token(client):
    r = client.post("/ingest", data="{}", content_type="application/json")
    assert r.status_code == 401


def test_rejects_wrong_token(client):
    r = _post(client, {"kind": "hub_block", "summary": "x"}, token="bad")
    assert r.status_code == 401


def test_accepts_hub_block_and_persists(client, app):
    r = _post(client, {"kind": "hub_block", "severity": "warning",
                       "summary": "hub blocked 42.0s",
                       "detail": "Traceback...\n  face_locations"})
    assert r.status_code == 201
    with app.app_context():
        from models import Event
        e = Event.query.one()
        assert e.kind == "hub_block"
        assert "face_locations" in e.detail
        assert e.source == "app-unified"


def test_missing_summary_is_400_not_500(client):
    r = _post(client, {"kind": "hub_block"})
    assert r.status_code == 400


def test_unknown_kind_rejected(client):
    """白名單制：只收診斷事件。收一般 app log 需先補欄位加密。"""
    r = _post(client, {"kind": "user_note", "summary": "leaked pii"})
    assert r.status_code == 400
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd monitor && ../venv/bin/python -m pytest tests/test_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: 實作**

```python
# monitor/app.py
"""Monitor service — 獨立於 app-unified 的取證服務。

刻意保持極簡：純 sync Flask，不用 gevent、不 monkey patch。
app-unified 出問題時它必須還活著，所以它跟 app-unified 共用的東西越少越好。
"""
import hmac
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from models import db, Event

# 白名單：只收診斷事件。擴大範圍前必須先補欄位加密（見 models.py 說明）。
ALLOWED_KINDS = {"hub_block"}


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_url()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())
    db.init_app(app)

    with app.app_context():
        db.create_all()

    _register_routes(app)

    if os.environ.get("MONITOR_DISABLE_PROBER") != "1":
        from prober import start_prober
        start_prober(app)

    return app


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL 未設定")
    # Zeabur 給的是 postgres://，SQLAlchemy 2.x 只認 postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _token_ok() -> bool:
    expected = os.environ.get("MONITOR_INGEST_TOKEN", "")
    header = request.headers.get("Authorization", "")
    if not expected or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:], expected)


def _register_routes(app: Flask) -> None:
    @app.route("/healthz")
    def healthz():
        return jsonify(status="ok"), 200

    @app.route("/ingest", methods=["POST"])
    def ingest():
        if not _token_ok():
            return jsonify(error="unauthorized"), 401

        data = request.get_json(silent=True) or {}
        kind = data.get("kind")
        summary = data.get("summary")
        if kind not in ALLOWED_KINDS:
            return jsonify(error=f"kind not allowed: {kind!r}"), 400
        if not summary:
            return jsonify(error="summary required"), 400

        db.session.add(Event(
            ts=datetime.now(timezone.utc),
            source=data.get("source", "app-unified"),
            kind=kind,
            severity=data.get("severity", "warning"),
            summary=str(summary)[:500],
            detail=data.get("detail"),
        ))
        db.session.commit()
        return jsonify(status="stored"), 201

    from views import register_views
    register_views(app)
```

註：`views.register_views` 在 Task 5 建立。本 task 先建一個佔位檔讓 import 成立：

```python
# monitor/views.py  （Task 5 會補完）
def register_views(app):
    pass
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd monitor && ../venv/bin/python -m pytest tests/ -q`
Expected: PASS，8 passed（Task 2 的 2 個 + 本 task 的 6 個）

- [ ] **Step 5: Commit**

```bash
git add monitor/app.py monitor/views.py monitor/tests/test_ingest.py
git commit -m "feat(monitor): app factory + /ingest（Bearer token + kind 白名單）"
```

---

## Task 4: 背景探測執行緒 + 保留期清理

**Files:**
- Create: `monitor/prober.py`
- Test: `monitor/tests/test_prober.py`

**Interfaces:**
- Consumes: `probe_core.probe`, `models.db/ProbeSample`
- Produces: `start_prober(app) -> threading.Thread`、`run_once(app) -> list[ProbeSample]`、`sweep_old(app, days) -> int`

- [ ] **Step 1: 寫失敗的測試**

```python
# monitor/tests/test_prober.py
"""探測迴圈：把每次取樣落地。

重點是「一次取樣 = 兩個 target」—— 只量對外網域分不出是 Cloudflare 壞還是
app 壞；origin 那筆才是分水嶺。
"""
from datetime import datetime, timedelta, timezone


def test_run_once_writes_one_sample_per_target(app, monkeypatch):
    import prober
    monkeypatch.setenv("PROBE_PUBLIC_URL", "https://pub.example/healthz")
    monkeypatch.setenv("PROBE_ORIGIN_URL", "http://origin.example:8080/healthz")

    calls = []

    def fake_probe(host, path, **kw):
        calls.append((host, path, kw.get("use_tls"), kw.get("port")))
        return {"verdict": "ok", "code": 200, "err": None, "cf_ray": None,
                "app": {"pid": 7, "uptime_s": 5.0}, "t_dns": 0.01, "t_conn": 0.02,
                "t_tls": 0.03, "t_ttfb": 0.04, "t_total": 0.1}

    monkeypatch.setattr(prober, "probe", fake_probe)
    with app.app_context():
        samples = prober.run_once(app)
        from models import ProbeSample
        assert ProbeSample.query.count() == 2
        assert {s.target for s in samples} == {"public", "origin"}
        assert {s.app_pid for s in samples} == {7}

    assert ("pub.example", "/healthz", True, 443) in calls
    assert ("origin.example", "/healthz", False, 8080) in calls


def test_run_once_survives_probe_exception(app, monkeypatch):
    """探測本身炸掉不能讓整條迴圈死掉 —— 監控停擺比監控報錯更糟。"""
    import prober
    monkeypatch.setenv("PROBE_PUBLIC_URL", "https://pub.example/healthz")
    monkeypatch.delenv("PROBE_ORIGIN_URL", raising=False)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(prober, "probe", boom)
    with app.app_context():
        samples = prober.run_once(app)
        assert samples == []


def test_sweep_deletes_only_old_rows(app):
    import prober
    from models import db, ProbeSample
    now = datetime.now(timezone.utc)
    db.session.add(ProbeSample(ts=now, target="public", verdict="ok"))
    db.session.add(ProbeSample(ts=now - timedelta(days=30),
                               target="public", verdict="ok"))
    db.session.commit()

    deleted = prober.sweep_old(app, days=14)
    assert deleted == 1
    assert ProbeSample.query.count() == 1
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd monitor && ../venv/bin/python -m pytest tests/test_prober.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'prober'`

- [ ] **Step 3: 實作**

```python
# monitor/prober.py
"""定時探測 app-unified，把結果落地。

跑在一般 OS thread（monitor 沒有 gevent，不需要特別處理）。
gunicorn workers=1 —— 多個 worker 會各跑一份，取樣就重複了。
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from models import db, ProbeSample
from probe_core import probe

_log = logging.getLogger(__name__)


def _targets() -> list[tuple[str, str]]:
    """回傳 [(target 名稱, url)]。沒設就跳過該 target。"""
    out = []
    for name, env in (("public", "PROBE_PUBLIC_URL"),
                      ("origin", "PROBE_ORIGIN_URL")):
        url = os.environ.get(env, "").strip()
        if url:
            out.append((name, url))
    return out


def run_once(app) -> list:
    samples = []
    for name, url in _targets():
        u = urlparse(url)
        use_tls = u.scheme == "https"
        port = u.port or (443 if use_tls else 80)
        try:
            r = probe(u.hostname, u.path or "/", port=port, use_tls=use_tls,
                      timeout=float(os.environ.get("PROBE_TIMEOUT", "15")))
        except Exception:
            # 探測炸掉不能讓迴圈死掉 —— 監控停擺比監控報錯更糟
            _log.exception("probe failed for %s", name)
            continue
        s = ProbeSample(
            ts=datetime.now(timezone.utc), target=name,
            verdict=r["verdict"], code=r["code"],
            t_dns=r["t_dns"], t_conn=r["t_conn"], t_tls=r["t_tls"],
            t_ttfb=r["t_ttfb"], t_total=r["t_total"], cf_ray=r["cf_ray"],
            app_pid=(r["app"] or {}).get("pid"),
            app_uptime_s=(r["app"] or {}).get("uptime_s"),
            err=r["err"])
        db.session.add(s)
        samples.append(s)
    db.session.commit()
    return samples


def sweep_old(app, days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = ProbeSample.query.filter(ProbeSample.ts < cutoff).delete()
    db.session.commit()
    return n


def start_prober(app) -> threading.Thread:
    interval = float(os.environ.get("PROBE_INTERVAL_SECONDS", "20"))
    retention = int(os.environ.get("RETENTION_DAYS", "14"))

    def loop():
        last_sweep = 0.0
        while True:
            started = time.monotonic()
            try:
                with app.app_context():
                    run_once(app)
                    if started - last_sweep > 3600:
                        sweep_old(app, retention)
                        last_sweep = started
            except Exception:
                _log.exception("prober loop iteration failed")
            time.sleep(max(1.0, interval - (time.monotonic() - started)))

    t = threading.Thread(target=loop, daemon=True, name="prober")
    t.start()
    return t
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd monitor && ../venv/bin/python -m pytest tests/ -q`
Expected: PASS，11 passed

- [ ] **Step 5: Commit**

```bash
git add monitor/prober.py monitor/tests/test_prober.py
git commit -m "feat(monitor): 背景探測迴圈 + 保留期清理"
```

---

## Task 5: 密碼保護的檢視頁

**Files:**
- Modify: `monitor/views.py`（取代 Task 3 的佔位）
- Create: `monitor/templates/login.html`, `monitor/templates/dashboard.html`
- Test: `monitor/tests/test_views.py`

**Interfaces:**
- Consumes: `models.db/ProbeSample/Event`
- Produces: `register_views(app)`；`GET /`（需登入）、`GET|POST /login`、`GET /logout`

- [ ] **Step 1: 寫失敗的測試**

```python
# monitor/tests/test_views.py
"""檢視頁。單一密碼即可 —— 只有你一個讀者，RBAC 是過度設計。

但「未登入不得看到資料」必須擋死：堆疊裡有內部路徑，不該公開。
"""
from datetime import datetime, timezone


def test_dashboard_requires_login(client):
    r = client.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_wrong_password_rejected(client):
    r = client.post("/login", data={"password": "nope"})
    assert r.status_code == 401


def test_login_then_see_events(client, app):
    with app.app_context():
        from models import db, Event
        db.session.add(Event(ts=datetime.now(timezone.utc), source="app-unified",
                             kind="hub_block", severity="warning",
                             summary="hub blocked 42.0s",
                             detail="face_locations"))
        db.session.commit()

    assert client.post("/login", data={"password": "test-pw"}).status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert "hub blocked 42.0s" in body


def test_dashboard_flags_worker_restart(client, app):
    """pid 變動 = worker 被重啟。這是「卡死→被 SIGKILL」的關鍵佐證，要看得見。"""
    with app.app_context():
        from models import db, ProbeSample
        for pid in (100, 100, 200):
            db.session.add(ProbeSample(ts=datetime.now(timezone.utc),
                                       target="origin", verdict="ok",
                                       app_pid=pid))
        db.session.commit()

    client.post("/login", data={"password": "test-pw"})
    body = client.get("/").get_data(as_text=True)
    assert "重啟" in body
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd monitor && ../venv/bin/python -m pytest tests/test_views.py -q`
Expected: FAIL — 4 failed（`/` 目前回 404）

- [ ] **Step 3: 實作**

```python
# monitor/views.py
"""檢視頁：單一密碼登入，呈現異常摘要 / 事件 / 取樣。

只有一個讀者，所以不做使用者系統。但未登入不得看到內容 —— 堆疊會揭露
內部路徑。
"""
import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (redirect, render_template, request, session, url_for)

from models import Event, ProbeSample

TW = timezone(timedelta(hours=8))


def _login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def _detect_restarts(samples) -> list:
    """依時間序找 app_pid 變動 —— 每次變動代表上一個 worker 沒了。"""
    out, prev = [], {}
    for s in sorted(samples, key=lambda x: x.ts):
        if s.app_pid is None:
            continue
        old = prev.get(s.target)
        if old is not None and old != s.app_pid:
            out.append({"ts": s.ts, "target": s.target,
                        "old_pid": old, "new_pid": s.app_pid})
        prev[s.target] = s.app_pid
    return out


def register_views(app):
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            expected = os.environ.get("MONITOR_PASSWORD", "")
            given = request.form.get("password", "")
            if expected and hmac.compare_digest(given, expected):
                session["authed"] = True
                return redirect(url_for("dashboard"))
            return render_template("login.html", error="密碼錯誤"), 401
        return render_template("login.html", error=None)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @_login_required
    def dashboard():
        hours = int(request.args.get("hours", "24"))
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        samples = (ProbeSample.query.filter(ProbeSample.ts >= since)
                   .order_by(ProbeSample.ts.desc()).limit(2000).all())
        events = (Event.query.filter(Event.ts >= since)
                  .order_by(Event.ts.desc()).limit(200).all())

        bad = [s for s in samples if s.verdict not in ("ok", "slow")]
        counts = {}
        for s in bad:
            counts[s.verdict] = counts.get(s.verdict, 0) + 1

        return render_template(
            "dashboard.html", hours=hours, samples=samples[:300],
            events=events, counts=counts, bad_total=len(bad),
            total=len(samples), restarts=_detect_restarts(samples),
            tw=TW)
```

```html
<!-- monitor/templates/login.html -->
<!doctype html>
<meta charset="utf-8"><title>monitor</title>
<style>body{font-family:system-ui;max-width:24rem;margin:6rem auto}
input,button{font-size:1rem;padding:.5rem;width:100%;box-sizing:border-box}
.err{color:#c00}</style>
<h1>monitor</h1>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
<form method="post">
  <input type="password" name="password" placeholder="密碼" autofocus>
  <button type="submit">登入</button>
</form>
```

```html
<!-- monitor/templates/dashboard.html -->
<!doctype html>
<meta charset="utf-8"><title>monitor</title>
<style>
body{font-family:system-ui;margin:1.5rem;max-width:76rem}
table{border-collapse:collapse;width:100%;font-size:.85rem;margin-bottom:2rem}
th,td{border:1px solid #ddd;padding:.3rem .5rem;text-align:left}
th{background:#f4f4f4}
.bad{background:#ffecec}.warn{background:#fff8e1}
pre{white-space:pre-wrap;font-size:.75rem;background:#f8f8f8;padding:.5rem;
    max-height:22rem;overflow:auto}
.sum{font-size:1.05rem;margin-bottom:1rem}
</style>
<h1>monitor — 最近 {{ hours }} 小時</h1>
<p class="sum">
取樣 {{ total }} 筆，異常 <strong>{{ bad_total }}</strong> 筆
{% if counts %}（{% for k, v in counts.items() %}{{ k }}×{{ v }} {% endfor %}）{% endif %}
· <a href="/logout">登出</a>
</p>

{% if restarts %}
<h2>worker 重啟 {{ restarts|length }} 次</h2>
<p>pid 變動代表上一個 worker 沒了。緊接在 ttfb_hang 之後出現，即為「卡死 → 被 SIGKILL → 恢復」。</p>
<table>
<tr><th>時間</th><th>target</th><th>pid</th></tr>
{% for r in restarts %}
<tr class="warn"><td>{{ r.ts.astimezone(tw).strftime('%m-%d %H:%M:%S') }}</td>
<td>{{ r.target }}</td><td>{{ r.old_pid }} → {{ r.new_pid }}</td></tr>
{% endfor %}
</table>
{% endif %}

<h2>診斷事件（{{ events|length }}）</h2>
{% if not events %}<p>無。</p>{% endif %}
{% for e in events %}
<div>
<strong>{{ e.ts.astimezone(tw).strftime('%m-%d %H:%M:%S') }}</strong>
[{{ e.kind }}] {{ e.summary }}
{% if e.detail %}<pre>{{ e.detail }}</pre>{% endif %}
</div>
{% endfor %}

<h2>探測取樣（最近 {{ samples|length }} 筆）</h2>
<table>
<tr><th>時間</th><th>target</th><th>判定</th><th>code</th>
<th>conn</th><th>tls</th><th>ttfb</th><th>total</th><th>pid</th><th>err</th></tr>
{% for s in samples %}
<tr class="{% if s.verdict not in ('ok','slow') %}bad{% elif s.verdict=='slow' %}warn{% endif %}">
<td>{{ s.ts.astimezone(tw).strftime('%m-%d %H:%M:%S') }}</td>
<td>{{ s.target }}</td><td>{{ s.verdict }}</td><td>{{ s.code or '' }}</td>
<td>{{ s.t_conn or '' }}</td><td>{{ s.t_tls or '' }}</td>
<td>{{ s.t_ttfb or '' }}</td><td>{{ s.t_total or '' }}</td>
<td>{{ s.app_pid or '' }}</td><td>{{ s.err or '' }}</td></tr>
{% endfor %}
</table>
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd monitor && ../venv/bin/python -m pytest tests/ -q`
Expected: PASS，15 passed

- [ ] **Step 5: Commit**

```bash
git add monitor/views.py monitor/templates/
git add monitor/tests/test_views.py
git commit -m "feat(monitor): 密碼保護的檢視頁（異常摘要 / 重啟 / 事件 / 取樣）"
```

---

## Task 6: 可部署成品（wsgi + gunicorn + Docker）

**Files:**
- Create: `monitor/wsgi.py`, `monitor/gunicorn.conf.py`, `monitor/requirements.txt`,
  `monitor/Dockerfile`, `monitor/zbpack.json`, `monitor/.dockerignore`
- Test: `monitor/tests/test_wsgi.py`

**Interfaces:**
- Produces: `wsgi:app`（gunicorn 進入點）

- [ ] **Step 1: 寫失敗的測試**

```python
# monitor/tests/test_wsgi.py
"""gunicorn 進入點必須真的能載入 —— 這裡壞掉在本機是看不到的，
會變成 prod 起不來、然後只剩一堆 restart log。
"""
import os


def test_gunicorn_config_uses_single_sync_worker():
    """workers>1 會讓每個 worker 各跑一份探測迴圈，取樣就重複了。"""
    ns = {"__file__": "gunicorn.conf.py"}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "gunicorn.conf.py")) as f:
        exec(f.read(), ns)
    assert ns["workers"] == 1
    assert "gevent" not in ns.get("worker_class", "sync")


def test_wsgi_module_exposes_app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MONITOR_DISABLE_PROBER", "1")
    monkeypatch.setenv("MONITOR_PASSWORD", "x")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "y")
    import wsgi
    assert wsgi.app is not None
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd monitor && ../venv/bin/python -m pytest tests/test_wsgi.py -q`
Expected: FAIL — 找不到 `gunicorn.conf.py` / `No module named 'wsgi'`

- [ ] **Step 3: 實作**

```python
# monitor/wsgi.py
"""gunicorn 進入點：gunicorn -c gunicorn.conf.py wsgi:app"""
from app import create_app

app = create_app()
```

```python
# monitor/gunicorn.conf.py
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"

# workers=1：探測迴圈跑在 worker 內的背景執行緒，多個 worker 會各跑一份，
# 取樣就重複了。這個服務負載極低，一個 worker 綽綽有餘。
workers = 1
worker_class = "sync"
threads = 4
timeout = 60
accesslog = "-"
errorlog = "-"
loglevel = "info"
```

```
# monitor/requirements.txt
Flask==3.1.0
Flask-SQLAlchemy==3.1.1
SQLAlchemy==2.0.36
psycopg2-binary==2.9.10
gunicorn==23.0.0
```

```dockerfile
# monitor/Dockerfile
# build context = monitor/（不是 repo root）。monitor 必須自給自足。
FROM python:3.12-slim

# dig：probe_core 用它問指定 resolver，才分得出是不是 DNS 問題
RUN apt-get update && apt-get install -y --no-install-recommends \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
ENV PYTHONUNBUFFERED=1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
```

```json
{
  "use_dockerfile": true
}
```
（存成 `monitor/zbpack.json`）

```
# monitor/.dockerignore
tests/
__pycache__/
*.pyc
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd monitor && ../venv/bin/python -m pytest tests/ -q`
Expected: PASS，17 passed

- [ ] **Step 5: 本機驗證 Docker build**

Run: `cd monitor && docker build -t monitor-test .`
Expected: build 成功。若本機沒有 docker，跳過並在 Task 8 部署時驗證。

- [ ] **Step 6: Commit**

```bash
git add monitor/wsgi.py monitor/gunicorn.conf.py monitor/requirements.txt \
        monitor/Dockerfile monitor/zbpack.json monitor/.dockerignore \
        monitor/tests/test_wsgi.py
git commit -m "feat(monitor): 可部署成品（wsgi + sync gunicorn + Dockerfile）"
```

---

## Task 7: app-unified 端上報（unpatched socket）

**Files:**
- Create: `app_unified/monitor_ship.py`
- Modify: `app_unified/hub_watchdog.py`（`_default_on_block` 加上上報）
- Test: `app_unified/tests/test_monitor_ship.py`

**Interfaces:**
- Consumes: 無（不 import monitor 任何東西）
- Produces: `ship_event(kind: str, summary: str, detail: str = "") -> bool`
  成功回 True；任何失敗回 False 且**絕不拋例外**。

- [ ] **Step 1: 寫失敗的測試**

```python
# app_unified/tests/test_monitor_ship.py
"""把診斷事件送到 monitor。

這段程式碼在 hub_watchdog 的**真 OS thread** 上執行，而且是在 gevent hub
已經被卡住的當下。兩個死規則：
  1. 只能用 unpatched socket。碰到被 monkey patch 的 socket 就會踩到
     "crossing thread boundaries is not allowed"（prod 2026-05-09 實錄）。
  2. 絕不能拋例外或卡住。監控失敗不該影響服務。
"""
import socket
import threading

import pytest


def _capture_server():
    """起一個假 monitor，回傳 (port, received list)。"""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    port = s.getsockname()[1]
    received = []

    def loop():
        conn, _ = s.accept()
        data = b""
        conn.settimeout(3)
        try:
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            length = 0
            for line in head.decode("latin-1").splitlines():
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            while len(body) < length:
                body += conn.recv(4096)
            received.append((head.decode("latin-1"), body.decode("utf-8")))
            conn.sendall(b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
        finally:
            conn.close()

    threading.Thread(target=loop, daemon=True).start()
    return port, received


def test_ships_event_with_bearer_token(monkeypatch):
    import monitor_ship
    port, received = _capture_server()
    monkeypatch.setenv("MONITOR_INGEST_URL", f"http://127.0.0.1:{port}/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "tok-123")

    ok = monitor_ship.ship_event("hub_block", "hub blocked 42.0s",
                                 "stack: face_locations")
    assert ok is True

    for _ in range(50):
        if received:
            break
        import time as _t
        _t.sleep(0.05)
    head, body = received[0]
    assert "POST /ingest HTTP/1.1" in head
    assert "Authorization: Bearer tok-123" in head
    assert "hub blocked 42.0s" in body
    assert "face_locations" in body


def test_returns_false_when_not_configured(monkeypatch):
    """沒設定就安靜跳過 —— 本機開發不該因此噴錯。"""
    import monitor_ship
    monkeypatch.delenv("MONITOR_INGEST_URL", raising=False)
    assert monitor_ship.ship_event("hub_block", "x") is False


def test_never_raises_when_monitor_is_down(monkeypatch):
    """monitor 掛掉時 app-unified 必須毫髮無傷。"""
    import monitor_ship
    monkeypatch.setenv("MONITOR_INGEST_URL", "http://127.0.0.1:1/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "t")
    assert monitor_ship.ship_event("hub_block", "x") is False


def test_rejects_https_url(monkeypatch):
    """只支援內網明文 HTTP。https 需要處理被 patch 的 ssl 模組，
    在真 OS thread 上不安全 —— 與其默默出錯，不如明確拒絕。"""
    import monitor_ship
    monkeypatch.setenv("MONITOR_INGEST_URL", "https://example.com/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "t")
    assert monitor_ship.ship_event("hub_block", "x") is False


def test_uses_unpatched_socket(monkeypatch):
    """核心保證：不得使用被 gevent monkey patch 的 socket。"""
    import monitor_ship
    assert monitor_ship._socket_factory() is not None
    from gevent.monkey import get_original
    assert monitor_ship._socket_factory() is get_original("socket", "socket")
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_monitor_ship.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor_ship'`

- [ ] **Step 3: 實作**

```python
# app_unified/monitor_ship.py
"""把診斷事件推到 monitor service。

**這段程式碼的執行情境很特殊，改動前務必讀完：**

它由 hub_watchdog 的真 OS thread 呼叫，時機是 gevent hub 已經被同步工作
卡死的當下。這帶來兩個硬性限制：

1. **只能用 unpatched socket。** monkey.patch_all() 之後 socket 與
   getaddrinfo 都是 gevent 版本，從非 greenlet 的真執行緒去用，會觸發
   "using gevent objects from multiple threads ... crossing thread
   boundaries is not allowed"（prod 2026-05-09 實際發生過）。

2. **只走內網明文 HTTP。** https 得再處理一個同樣被 patch 的 ssl 模組，
   在這個情境下不值得冒險。ingest endpoint 走 Zeabur 內網，不對外公開。

為什麼非得在當下送出：hub 卡住時任何 greenlet 都跑不動，等恢復再送通常
已經太遲 —— gunicorn timeout=60 會 SIGKILL 該 worker，/tmp/app.log 隨容器
重啟清空，證據就永久消失了。
"""
import json
import os
from urllib.parse import urlparse

_TIMEOUT = float(os.environ.get("MONITOR_INGEST_TIMEOUT", "3"))


def _socket_factory():
    """取回未被 monkey patch 的 socket class。"""
    try:
        from gevent.monkey import get_original
        return get_original("socket", "socket")
    except (ImportError, AttributeError):
        import socket
        return socket.socket


def _getaddrinfo():
    """取回未被 monkey patch 的 getaddrinfo（gevent 有自己的 resolver）。"""
    try:
        from gevent.monkey import get_original
        return get_original("socket", "getaddrinfo")
    except (ImportError, AttributeError):
        import socket
        return socket.getaddrinfo


def ship_event(kind: str, summary: str, detail: str = "") -> bool:
    """送出一則事件。成功 True，任何失敗 False —— 絕不拋例外。"""
    url = os.environ.get("MONITOR_INGEST_URL", "").strip()
    token = os.environ.get("MONITOR_INGEST_TOKEN", "").strip()
    if not url or not token:
        return False

    u = urlparse(url)
    if u.scheme != "http":
        return False        # 見模組說明：只支援內網明文 HTTP

    sock = None
    try:
        import socket as _stdsocket
        body = json.dumps({"kind": kind, "severity": "warning",
                           "source": "app-unified",
                           "summary": summary, "detail": detail},
                          ensure_ascii=False).encode("utf-8")
        host = u.hostname
        port = u.port or 80
        path = u.path or "/ingest"

        addrs = _getaddrinfo()(host, port, _stdsocket.AF_INET,
                               _stdsocket.SOCK_STREAM)
        sock = _socket_factory()(_stdsocket.AF_INET, _stdsocket.SOCK_STREAM)
        sock.settimeout(_TIMEOUT)
        sock.connect(addrs[0][4])

        req = (f"POST {path} HTTP/1.1\r\n"
               f"Host: {host}:{port}\r\n"
               f"Authorization: Bearer {token}\r\n"
               "Content-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\n"
               "Connection: close\r\n\r\n").encode("utf-8")
        sock.sendall(req + body)

        resp = sock.recv(200).decode("latin-1", "replace")
        return " 201" in resp or " 200" in resp
    except Exception:
        return False        # 監控失敗絕不影響服務
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_monitor_ship.py -q`
Expected: PASS，5 passed

- [ ] **Step 5: 把上報接進 hub_watchdog**

修改 `app_unified/hub_watchdog.py` 的 `_default_on_block`，在既有的 `os.write` 之後加上：

```python
    # 推一份到 monitor —— /tmp/app.log 會隨容器重啟清空，而「卡死→被 SIGKILL
    # →重啟」正是我們在追的劇本，最重要的那份 log 最容易消失。
    try:
        from monitor_ship import ship_event
        ship_event("hub_block",
                   f"gevent hub 被卡住 {blocked_for:.1f} 秒",
                   stack)
    except Exception:
        pass
```

- [ ] **Step 6: 補一個測試確認 watchdog 真的會上報**

```python
# 追加到 app_unified/tests/test_hub_watchdog.py
def test_block_report_is_shipped_to_monitor(tmp_path, monkeypatch):
    """hub 被卡住時證據必須離開容器，否則重啟就沒了。"""
    import hub_watchdog

    monkeypatch.setattr(hub_watchdog, "_LOG_PATH", str(tmp_path / "app.log"))
    shipped = []
    import monitor_ship
    monkeypatch.setattr(monitor_ship, "ship_event",
                        lambda *a: shipped.append(a) or True)

    hub_watchdog._default_on_block(12.5, "stack: face_locations")

    assert shipped, "阻塞事件沒有送到 monitor"
    kind, summary, detail = shipped[0]
    assert kind == "hub_block"
    assert "12.5" in summary
    assert "face_locations" in detail
```

- [ ] **Step 7: 執行完整測試**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: PASS，全數通過（原 790 + 新增 6）

- [ ] **Step 8: Commit**

```bash
git add app_unified/monitor_ship.py app_unified/hub_watchdog.py \
        app_unified/tests/test_monitor_ship.py app_unified/tests/test_hub_watchdog.py
git commit -m "feat(app): hub 阻塞事件即時推送到 monitor（unpatched socket）"
```

---

## Task 8: CI + Zeabur 部署 + 上線驗證

**Files:**
- Modify: `.github/workflows/test.yml`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 讓 CI 跑 monitor 測試**

在 `.github/workflows/test.yml` 既有 job 之後追加（確認縮排與既有檔案一致）：

```yaml
  monitor-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install deps
        run: pip install -r monitor/requirements.txt pytest
      - name: Run tests
        working-directory: monitor
        run: python -m pytest -q
```

- [ ] **Step 2: 本機確認 monitor 測試在乾淨環境可跑**

Run: `cd monitor && ../venv/bin/python -m pytest -q`
Expected: PASS，17 passed

- [ ] **Step 3: Commit 並 push**

```bash
git add .github/workflows/test.yml
git commit -m "ci: monitor 測試納入 CI"
git push -u origin <branch>
```

- [ ] **Step 4: 在 Zeabur 建立 monitor service（使用者操作）**

1. 在既有 project 內新增 service → 選同一個 GitHub repo
2. **Root Directory 設為 `monitor`**（讓 build context = `monitor/`）
3. 環境變數：

| 變數 | 值 |
|---|---|
| `DATABASE_URL` | 與 app-unified 相同的 Postgres 連線字串 |
| `MONITOR_PASSWORD` | 自訂，檢視頁登入用 |
| `MONITOR_INGEST_TOKEN` | 自訂長隨機字串 |
| `SECRET_KEY` | 自訂長隨機字串（session 簽章） |
| `PROBE_PUBLIC_URL` | `https://<你的對外網域>/healthz` |
| `PROBE_ORIGIN_URL` | `http://<app-unified 內網 hostname>:8080/healthz` |
| `PROBE_INTERVAL_SECONDS` | `20` |
| `RETENTION_DAYS` | `14` |

4. 給 monitor 一個 Zeabur 免費子網域（`*.zeabur.app`）。**不需要**另外買或設定
   Cloudflare 自訂網域 —— 只有你一個讀者，密碼保護足夠。

> **`PROBE_PUBLIC_URL` 不可省略。**
> 曾有人會想「monitor 跟 app-unified 都在 Zeabur 內網，直接探內網就好」。
> 不行：只探內網時，DNS / Cloudflare 邊緣故障會被回報成**一切正常**，而使用者
> 正連不上 —— 這正是 2026-08 這樁事故裡「Zeabur 顯示 Invalid DNS」的情境。
> 兩筆探測缺一不可：
>
> | 故障 | 只探 origin | origin + public |
> |---|---|---|
> | DNS / Cloudflare 邊緣 | 全部正常（錯誤結論） | public 壞、origin 正常 → 邊緣層 |
> | app hub 卡死 | ttfb_hang | 兩邊都壞 → app 層 |

- [ ] **Step 5: 在 app-unified service 補環境變數（使用者操作）**

| 變數 | 值 |
|---|---|
| `MONITOR_INGEST_URL` | `http://<monitor 內網 hostname>:8080/ingest` |
| `MONITOR_INGEST_TOKEN` | 與 monitor 相同 |

- [ ] **Step 6: 上線驗證**

1. 開 monitor 網域 → 用 `MONITOR_PASSWORD` 登入 → 應看到取樣持續增加，`public` 與 `origin` 各一筆/週期
2. 確認 `origin` 那筆的 `pid` 欄位有值（代表 app-unified 已部署新版 `/healthz`）
3. 驗證 ingest 通路（在 app-unified 的「指令」shell 內）：

```bash
python3 -c "
import os,sys; sys.path.insert(0,'/app')
from monitor_ship import ship_event
print('shipped:', ship_event('hub_block','上線驗證測試','(test)'))
"
```
Expected: `shipped: True`，且 monitor 頁面出現該筆事件

- [ ] **Step 7: 更新 CLAUDE.md**

在專案結構加入 `monitor/`，並在部署段落補上 monitor service 與其環境變數。

- [ ] **Step 8: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 補上 monitor service"
```

---

## Self-Review

**1. 範圍覆蓋**

| 需求 | 對應 |
|---|---|
| 從外部持續探測、分辨壞在哪一層 | Task 1（判定）+ Task 4（迴圈）|
| 接住卡死當下的證據，不隨容器重啟消失 | Task 3（ingest）+ Task 7（推送）|
| 不用開著自己的電腦 | Task 6（部署成品）+ Task 8（Zeabur）|
| 分辨「卡住」vs「被重啟」 | `/healthz` pid（已完成，未部署）+ Task 5 重啟偵測 |
| 看得到 | Task 5 |

**2. 佔位掃描**：無 TBD／無「類似 Task N」／每個程式步驟都有完整程式碼。Task 3 的 `views.py` 佔位是**刻意**的，Task 5 取代它，兩處都已標註。

**3. 型別一致性**

- `probe()` 回傳的鍵在 Task 1 定義、Task 4 消費 —— 已逐鍵對照（含 `app` 為 `None` 的情況）。
- `ProbeSample` 欄位名（`app_pid`/`app_uptime_s`）在 Task 2 定義，Task 4 寫入、Task 5 讀取 —— 一致。
- `ship_event(kind, summary, detail)` 在 Task 7 定義，同 task Step 5 消費 —— 一致。
- `/ingest` 接受的欄位（`kind`/`severity`/`source`/`summary`/`detail`）在 Task 3 定義，Task 7 送出 —— 一致。

**4. 已知限制（刻意接受）**

- monitor 與 app-unified 共用同一個 Postgres。DB 本身出問題時兩邊一起瞎。完整隔離見原 spec。
- monitor 跑在 Zeabur 上，Zeabur 整個 region／ingress 掛掉時它也一起掛。要涵蓋這種情況需另一個外部哨兵（如 UptimeRobot 免費版）。
- `workers=1` 沒有備援。monitor 掛掉時 app-unified 不受影響，但取樣會斷 —— 檢視頁的取樣時間軸會出現空隙，判讀時需注意「沒量到」不等於「正常」。
