# Monitor Service：app-unified log archive + status dashboard 設計

**日期**：2026-05-08
**模組**：新獨立 service `monitor/`（Zeabur 上跟 `app-unified` 平行的 container）
**Repo**：`webapp`（同 repo，monitor/ 為新 top-level dir，不在 `app_unified/` 之下）

## 動機

近兩週以來踩到的痛點：

1. **Zeabur runtime log viewer 不可信**：對 app-unified 的服務 log 卡 stale，第一代 pod 之後就不更新；除錯只能進「指令」shell 跑 `tail /tmp/app.log`（5/7 commit `4b28d8a` 加的 RotatingFileHandler）。
2. **`/tmp/app.log` 重啟即丟**：container restart 後 `/tmp` 清空，先前那批 log 無法再回頭看。worker recycle / pod 被 readiness probe 殺重啟那一刻的 log 對 debug 最重要，偏偏最容易丟。
3. **沒有跨 deploy / pod 的歷史視角**：要看「上次 X 是什麼時間出問題」、「ERROR 在哪個 release SHA 後變多」這種問題完全沒辦法。

需要一個獨立 service，**收 log + 持久化儲存 + 提供查詢 UI**，跟 app-unified 解耦：app-unified 死掉時 monitor 仍在跑、monitor 死掉時 app-unified 仍能服務 (退回 `/tmp/app.log` 兜底)。

## 範圍

- 新 top-level dir `monitor/`，跟 `app_unified/` 平行
- 新 Zeabur service：`monitor`（Docker 映像檔模式，從 GHCR pull `weather-monitor`）
- 新 Zeabur Postgres add-on：`monitor-db`（跟 app-unified 的 `postgres` 完全獨立）
- 新 GHA workflow `.github/workflows/docker-monitor.yml`，path filter 限 `monitor/**`
- app-unified 端：新檔 `app_unified/log_shipping.py` (`MonitorHTTPHandler` custom logging.Handler) + `app.py` 條件式 wiring
- 新 monitor 服務 routes：`/healthz`, `/ingest`, `/login`, `/logout`, `/`, `/api/logs`, `/api/status`

## 不在範圍

- **主動 alert**：不 push 通知到 Discord / email / Slack；要看狀況時開 dashboard 看（user 已決定）
- **Metrics / traces / APM**：不收 latency / request rate / 分散式 trace；只收 logging.WARNING+ 等級的事件
- **Multi-user / RBAC**：dashboard 單一密碼登入即可（user 自己用，沒第二個讀者）
- **Log structured search / full-text index**：query 走「level / source / logger prefix / time range」即可；message 是加密欄位無法 full-text index，這是 trade-off
- **取代 stdout log**：app-unified 仍照常寫 stdout 跟 `/tmp/app.log`（defense in depth），monitor 是**多一份 sink**，不是 replace
- **跨 service 收集**：第一階段只有 `source = "app-unified"`；架構保留 source 欄位，將來再開 monitor 收別的 service 不用改 schema

## 動機與設計守則

### 三大顧慮對應

| 顧慮 | 對應 |
|---|---|
| 不影響 app-unified 每次 build 時間 | path filter (`monitor/**` vs `app_unified/**`) + 獨立 GHA cache scope (`scope=monitor`) |
| 安全性 | (1) `/ingest` Bearer token + constant-time 比對 (2) `/dashboard` bcrypt 密碼 + session (3) Zeabur 內網 hostname 通訊不公開 ingest endpoint |
| 儲存加解密 | EncryptedText pattern from app-unified；`message` 與 `extra_json` 加密；**共用 `DB_ENCRYPTION_KEY`**（user 同意 — log 敏感度低於 user notes，不值得獨立 key 的運維成本） |

### 隔離守則 (`feedback_module_isolation`)

monitor service 跟 app-unified **0 cross-import**：
- 不允許 `from app_unified.* import ...`
- `crypto.py`、`models.py`(EncryptedText 部分)、auth helpers 用 **檔案複製**而非 import
- 兩邊 source 雖在同 repo，但 import path 完全分開
- DB 完全獨立 instance

理由：repo 同一個是為了 plan / spec / code review 一起管，**runtime 上是兩個 container 兩個 process**，誰崩誰活互不波及。

## 架構

### 部署 topology

```
                                       ┌───────────────────────────────┐
                                       │ Zeabur: app-unified service   │
                                       │  Flask + gunicorn (gevent)    │
   internet ─── HTTPS ────────────────▶│  R2 (attachments)             │
                                       │  Postgres-A (app-unified DB)  │
                                       │                               │
                                       │  ⓐ Custom logging.Handler     │
                                       └────────────────┬──────────────┘
                                                        │ HTTPS POST /ingest
                                                        │ Authorization: Bearer <SHARED_TOKEN>
                                                        │ {ts, level, source, message, extra}
                                                        ▼
                                       ┌───────────────────────────────┐
                                       │ Zeabur: monitor service       │
                                       │  Flask + gunicorn (sync)      │
                                       │  Postgres-B (monitor DB)      │
   internet ─── HTTPS (login) ────────▶│  ⓑ /dashboard, /api/logs       │
                                       │  ⓒ /ingest                    │
                                       └───────────────────────────────┘
```

### 模組結構

```
monitor/
├── Dockerfile
├── requirements.txt
├── wsgi.py                    # gunicorn entry, 包 fast /healthz
├── gunicorn.conf.py           # workers=1, sync worker, threads=4
├── app.py                     # create_app(), logging, retention sweep 啟動
├── config.py                  # 從 env 讀 DB URL / TOKEN / DASHBOARD_PASSWORD_HASH
├── extensions.py              # db = SQLAlchemy()
├── models.py                  # LogEntry + EncryptedText (從 app-unified copy)
├── crypto.py                  # AES-GCM helper (從 app-unified copy)
├── auth.py                    # /login, /logout, login_required decorator
├── ingest.py                  # /ingest blueprint (Bearer token)
├── dashboard.py               # /, /api/logs, /api/status blueprint (login_required)
├── retention.py               # 每小時清舊 log 的 sweep thread
└── templates/
    ├── base.html
    ├── login.html
    └── dashboard.html
```

## 資料模型 (monitor side)

```python
# monitor/models.py
class LogEntry(db.Model):
    __tablename__ = "log_entries"

    id          = Column(BigInteger, primary_key=True)
    ts          = Column(DateTime(timezone=True), nullable=False, index=True)  # 來源端產的時間
    received_at = Column(DateTime(timezone=True), nullable=False, default=now)  # monitor 收到的時間
    source      = Column(String(64),  nullable=False, index=True)  # "app-unified" / 將來其他 service
    level       = Column(String(16),  nullable=False, index=True)  # WARNING / ERROR / CRITICAL
    logger      = Column(String(128), nullable=False)              # logger name e.g. "mynotes.routes"
    message     = Column(EncryptedText, nullable=False)            # 加密
    extra_json  = Column(EncryptedText, nullable=True)             # 加密（exception traceback / dict context）
    git_sha     = Column(String(12),   nullable=True)              # 來源 build SHA（debug 用）
```

**索引**：
- `(ts DESC)`、`(source, level, ts DESC)`、`(logger, ts DESC)`
- 不對 `message` 做 full-text — 加密欄位無法 index（trade-off）

**Retention**：預設 30 天，env var `MONITOR_LOG_RETENTION_DAYS` 可調。每小時跑一次 batched DELETE (`LIMIT 10_000` per batch 避免長 lock)。

**Log level 收哪些**：預設 `WARNING+`（跟 app-unified 現在 `logging.basicConfig(level=WARNING)` 一致）。app-unified 端 handler 自帶 `setLevel(WARNING)` filter，不送 INFO/DEBUG。

## app-unified 端：MonitorHTTPHandler

新檔 `app_unified/log_shipping.py`：custom `logging.Handler`，把 WARNING+ log line **非同步** POST 到 monitor。

### 設計守則

1. **emit() 絕不能拖 reactor / request thread**：emit 只做 `queue.put_nowait`，HTTP POST 在背景 thread。
2. **monitor 掛掉時不能讓 app-unified 也掛**：worker 內 `try/except` 全包，失敗只 `sys.stderr.write`，**絕不 raise**、**不 retry storm**。
3. **不能無限長記憶體**：Queue maxsize=2000；滿了 drop-oldest，記一筆 `[log_shipping] dropped N`。
4. **避免循環 logging**：handler 自己出錯 log 到 stderr 不走 root logger，不然 monitor 失敗時自己又生一筆 log 又進 queue 又失敗。

### 與 5/8 cold-start fix (b61cdb0) 互不干擾

關鍵雷：gevent monkey patch 過後，`threading.Thread` 是 greenlet，不是真 OS thread。MonitorHTTPHandler init 是在 `wsgi_lazy` 那條**背景 OS thread** 跑（create_app 階段），那 thread 沒有自己的 hub，在那裡 spawn greenlet 會卡。

**解法**：跟 wsgi_lazy 同套路，用 `gevent.monkey.get_original("threading", "Thread")` 拿原生 OS Thread：

```python
import gevent.monkey
_OSThread = gevent.monkey.get_original("threading", "Thread")

class MonitorHTTPHandler(logging.Handler):
    def __init__(self, ...):
        ...
        t = _OSThread(target=self._worker, name="monitor-log-shipper", daemon=True)
        t.start()
```

worker thread 變真 OS thread → urllib socket 走真 blocking I/O → 完全不碰 main hub → /healthz fast-path 不受影響。

### 為什麼用 stdlib `urllib.request` 不用 `requests`

- emit 路徑越輕越好，`requests` import 帶 `chardet` / `urllib3` connection pool 等
- 少一個 fail mode（`requests` session 跟 gevent monkey 偶有互踩）
- 2 秒 timeout 已夠（monitor 內網延遲 < 50ms 預期）

### app.py wiring

接在現有 `_file_handler` 區塊後面，**只在 env var 都齊時才**啟用：

```python
_INGEST_URL = os.environ.get("MONITOR_INGEST_URL")
_INGEST_TOKEN = os.environ.get("MONITOR_INGEST_TOKEN")
if _INGEST_URL and _INGEST_TOKEN:
    from log_shipping import MonitorHTTPHandler
    _monitor_handler = MonitorHTTPHandler(
        ingest_url=_INGEST_URL,
        token=_INGEST_TOKEN,
        source="app-unified",
        git_sha=os.environ.get("GIT_SHA"),
    )
    _monitor_handler.setFormatter(_TaipeiFormatter("%(message)s"))
    logging.getLogger().addHandler(_monitor_handler)
```

→ Local dev / test / monitor 還沒 deploy = env 未設 = handler 不啟用 = 行為跟現在一模一樣。**zero-risk 漸進 rollout**。

## monitor 服務 routes

### `/healthz` (fast)

```python
@app.route("/healthz")
def healthz():
    return "ok", 200
```

monitor 沒 cold-start CPU work（no model load），普通 Flask route 就夠快，不需 LazyAppProxy。

### `POST /ingest`

```python
@ingest_bp.route("/ingest", methods=["POST"])
def ingest():
    # constant-time bearer token compare 防 timing attack
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "no token"}), 401
    if not hmac.compare_digest(auth[7:], current_app.config["INGEST_TOKEN"]):
        return jsonify({"error": "bad token"}), 401

    data = request.get_json(force=True, silent=True) or {}
    required = {"ts", "level", "logger", "source", "message"}
    if not required.issubset(data):
        return jsonify({"error": "missing fields"}), 400
    if data["level"] not in ("WARNING", "ERROR", "CRITICAL"):
        return jsonify({"error": "bad level"}), 400
    if len(data["message"]) > 64_000:
        return jsonify({"error": "message too long"}), 413

    try:
        ts = datetime.fromisoformat(data["ts"])
    except Exception:
        ts = datetime.now(timezone.utc)

    entry = LogEntry(
        ts=ts,
        source=data["source"][:64],
        level=data["level"],
        logger=data["logger"][:128],
        message=data["message"],
        extra_json=data.get("extra"),
        git_sha=(data.get("git_sha") or "")[:12],
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify({"status": "ok"}), 200
```

### `GET /` (login_required)

簡單 dashboard 頁，內含 `/api/logs` 與 `/api/status` 的 fetch + table render（vanilla JS、不引入 framework）。預設顯示**滑動 24h**（`since = now() - 24h`）內的 `ERROR+`，UI 上有 level / source / logger_prefix filter，with `?since=` 改 query string 即可往前撈。

### `GET /api/logs?level=&source=&logger_prefix=&since=&limit=` (login_required, JSON)

```python
@dashboard_bp.route("/api/logs")
@login_required
def api_logs():
    q = LogEntry.query
    if request.args.get("source"):
        q = q.filter(LogEntry.source == request.args["source"])
    if request.args.get("level"):
        levels_for = {"CRITICAL": ["CRITICAL"],
                      "ERROR":    ["ERROR", "CRITICAL"],
                      "WARNING":  ["WARNING", "ERROR", "CRITICAL"]}
        q = q.filter(LogEntry.level.in_(levels_for[request.args["level"]]))
    if request.args.get("logger_prefix"):
        q = q.filter(LogEntry.logger.startswith(request.args["logger_prefix"]))
    if request.args.get("since"):
        q = q.filter(LogEntry.ts >= datetime.fromtimestamp(int(request.args["since"]), tz=timezone.utc))
    limit = min(int(request.args.get("limit", 200)), 1000)
    rows = q.order_by(LogEntry.ts.desc()).limit(limit).all()
    return jsonify([
        {"id": r.id,
         "ts": r.ts.isoformat(),
         "received_at": r.received_at.isoformat(),
         "source": r.source,
         "level": r.level,
         "logger": r.logger,
         "message": r.message,        # decrypted by EncryptedText
         "extra": r.extra_json,
         "git_sha": r.git_sha}
        for r in rows
    ])
```

### `GET /api/status` (login_required, JSON)

簡單 stat：每個 source 最近一筆 ts、過去 1h ERROR 數、總筆數。dashboard 頁面頂端用，給「app-unified 最後一筆 log: 5/8 09:42 / 過去 1h 有 3 筆 ERROR」這種一眼能看的健康指標。

### `/login`, `/logout`

```python
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        expected_hash = current_app.config["DASHBOARD_PASSWORD_HASH"]
        if bcrypt.checkpw(password.encode(), expected_hash.encode()):
            session["mon_authed"] = True
            session.permanent = True
            return redirect(url_for("dashboard.home"))
        flash("密碼錯誤")
    return render_template("login.html")
```

- 不做 username — single-user dashboard
- bcrypt hash 存 env var `MONITOR_DASHBOARD_PASSWORD_HASH`
- session 35 分鐘過期跟 app-unified 一致

### Routes 總覽

| URL | Method | Auth | 用途 |
|---|---|---|---|
| `/healthz` | GET | none | Zeabur readiness |
| `/ingest` | POST | Bearer token | 收 log |
| `/login` | GET, POST | none | 登入頁 |
| `/logout` | GET | session | 登出 |
| `/` | GET | session | dashboard 頁 |
| `/api/logs` | GET | session | log query JSON |
| `/api/status` | GET | session | 健康指標 JSON |

## Retention sweep

`monitor/retention.py`：背景 OS thread 每小時跑一次 batched DELETE。

```python
def _sweep_loop(app, interval_sec=3600, batch_size=10_000):
    while True:
        time.sleep(interval_sec)
        try:
            with app.app_context():
                days = int(os.environ.get("MONITOR_LOG_RETENTION_DAYS", "30"))
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                while True:
                    ids = db.session.query(LogEntry.id).filter(
                        LogEntry.received_at < cutoff
                    ).limit(batch_size).all()
                    if not ids:
                        break
                    db.session.query(LogEntry).filter(
                        LogEntry.id.in_([i[0] for i in ids])
                    ).delete(synchronize_session=False)
                    db.session.commit()
        except Exception:
            logger.exception("[retention] sweep failed")
```

`create_app()` 結尾呼叫 `start_retention_sweep(app)` 起 thread。test 時用 `MONITOR_DISABLE_RETENTION=1` 關掉。

monitor 跑 `worker_class = "sync"` 不開 gevent，沒 monkey-patch，所以這條 thread 用 `threading.Thread` 即可、不需 `get_original`。**注意：`get_original` 雷只發生在 app-unified 端**（gevent worker），monitor 端完全不沾這個問題。

## 部署管道

### Dockerfile (`monitor/Dockerfile`)

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime

WORKDIR /app

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

`requirements.txt`（極簡）：
```
flask==3.0.*
flask-sqlalchemy==3.1.*
gunicorn==23.0.*
psycopg2-binary==2.9.*
bcrypt==4.2.*
cryptography==43.*
```

預期 image size ~120MB（vs app-unified ~1.2GB），build 30s 級。

### `gunicorn.conf.py`

```python
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

### GHA workflow (`.github/workflows/docker-monitor.yml`)

照 `docker.yml` 結構，差異只有 `path: monitor/**`、image name `weather-monitor`、`cache scope=monitor`。完整 workflow YAML 已在設計討論中定稿，照抄。

**雙保險**：path filter + 獨立 cache scope = 改 monitor 不觸發 app-unified build，反之亦然。

### Zeabur 設定（一次性手動）

1. 新增 monitor service，Docker 映像檔 `ghcr.io/stvlin1988-alt/weather-monitor:latest`，**解除綁定 GitHub 儲存庫**（5/7 設定避免 Zeabur auto-build 蓋掉）
2. Health check **保持啟用**，path `/healthz`（`feedback_zeabur_healthcheck` 鐵律）
3. 新增 monitor 專用 Postgres add-on，標 `monitor-db`
4. monitor service env vars：
   ```
   DATABASE_URL                    = postgresql://...@monitor-db:5432/...
   DB_ENCRYPTION_KEY               = <跟 app-unified 同一把>
   MONITOR_SECRET_KEY              = <openssl rand -base64 32>
   MONITOR_INGEST_TOKEN            = <openssl rand -base64 32>
   MONITOR_DASHBOARD_PASSWORD_HASH = <bcrypt hash, CLI 產>
   MONITOR_LOG_RETENTION_DAYS      = 30        # optional, default 30
   MONITOR_LOG_MIN_LEVEL           = WARNING   # optional, default WARNING
   ```
5. app-unified 加兩條 env var：
   ```
   MONITOR_INGEST_URL    = http://monitor:8080/ingest   # Zeabur 內網
   MONITOR_INGEST_TOKEN  = <跟 monitor 同一把>
   ```

### 漸進 rollout 順序

```
1. monitor branch 開好、code 寫好、tests pass (local)
2. push branch → GHA build monitor image → GHCR weather-monitor:latest
3. Zeabur 手動建 monitor service + monitor-db Postgres → 設 env → Redeploy
4. monitor /healthz 可達、/login 可進 → monitor 上線（但還沒收 log）
5. app-unified env var 加上 MONITOR_INGEST_URL + TOKEN → Redeploy
6. 開始有 log 流進 monitor，dashboard 看得到
```

**step 4 跟 5 之間可隔多久都不要緊**：monitor 沒 log 時 dashboard 是空的，但 service 沒問題。app-unified 沒設 env var 時 handler 不啟用，行為不變。

## Test 計畫

### monitor service 端

- `test_ingest_auth.py`：bad token / no token / good token / constant-time 不 leak（3 tests）
- `test_ingest_validate.py`：missing fields、bad level、message > 64KB、bad ts → fall back now（4 tests）
- `test_ingest_persist.py`：good payload → 寫進 DB → 各欄位正確（含 message 加密 round-trip）（2 tests）
- `test_dashboard_auth.py`：未登入 redirect /login、bad password 不過、good password 通（3 tests）
- `test_api_logs.py`：filter (level/source/logger_prefix/since/limit) 各組合（5 tests）
- `test_api_status.py`：source bucket / errors_1h / total（3 tests）
- `test_retention.py`：超過 cutoff 的 row 被 sweep、未到 cutoff 的不動（2 tests）

合計 22 tests。test 用 SQLite in-memory + StaticPool（抄 app-unified conftest.py）。

### app-unified 端

- `test_log_shipping.py`：emit 不 raise、queue full drop-oldest、worker swallow http error、handler 整合到 logging hierarchy（4 tests）

合計 26 tests new。

## Self-review checklist

1. **Spec coverage**：
   - ✅ 收 log 持久化（LogEntry table）
   - ✅ Status visibility（/api/status, dashboard）
   - ✅ Build time 不影響（path filter + cache scope）
   - ✅ 安全性（bearer token、bcrypt 密碼、Zeabur 內網）
   - ✅ 加解密（EncryptedText）
   - ✅ Cold-start /healthz 不受影響（`_OSThread = gevent.monkey.get_original`）
   - ✅ 隔離守則（0 cross-import、獨立 DB instance、獨立 image）
   - ✅ 漸進 rollout 路徑（env var gate）

2. **Internal consistency**：兩邊 token 名稱一致 (`MONITOR_INGEST_TOKEN`)；shared key (`DB_ENCRYPTION_KEY`) 跟原本 app-unified 同名同值

3. **Scope**：focus，不含 alerting / metrics / multi-source。一個 plan 可實作。

4. **Ambiguity**：
   - retention 模式：明確 30 天、batched DELETE
   - test 環境：sync worker、SQLite in-memory、autouse fixture 清 DB
   - Zeabur 內網 URL：`http://monitor:8080/ingest`（service-name DNS）
