# 專案紀錄（webapp）

> 一個以 Flask 為核心的內部工具站台。**目前的主力是 `app_unified/`**（單體整合版，部署在
> Zeabur）。`app1_notes/`、`app2_weather/` 是早期拆分版本，已被 `app_unified` 取代，僅保留參考。

## 專案結構

```
webapp/
├── app_unified/        # ★ 主力：整合版 Flask 單體（所有現行功能都在這）
├── monitor/            # 取證用獨立 service：外部探測 + 收 app-unified 的 hub 阻塞事件
├── app1_notes/         # (legacy) 早期 App1：人臉辨識 + 隨身筆記
├── app2_weather/       # (legacy) 早期 App2：天氣查詢 + 人臉驗證
├── docs/superpowers/   # 設計文件：plans/（實作計畫）＋ specs/（設計規格），每個功能一份
├── Dockerfile          # 部署用（build context = repo root，路徑帶 app_unified/ 前綴）
├── zbpack.json         # Zeabur：use_dockerfile
├── start_app1.sh       # (legacy) 啟動 app1
├── start_app2.sh       # (legacy) 啟動 app2
└── CLAUDE.md           # 本紀錄
```

`docs/superpowers/` 之外，`app_unified/docs/superpowers/` 也有一份較新的 plans/specs（同一套工作流的產物）。
新功能開發前先寫 spec（設計）→ plan（實作步驟），再動手。

---

## app_unified — 整合版（本地 port 5000，prod 8080）

- 技術棧：Flask 3 + Flask-Login + Flask-SQLAlchemy + Flask-Migrate + Flask-Limiter
  + Flask-SocketIO（gevent async）+ SQLAlchemy
- 資料庫：**prod = PostgreSQL（Zeabur）**，**本地開發 = SQLite**
- 檔案儲存：Cloudflare R2（S3 相容，走 boto3）— 存人臉照與附件
- AI 摘要：依序嘗試 **Gemini → Ollama → Anthropic**（任一有 key 就用）
- 人臉辨識：`face_recognition`（dlib）— 用 `wheels/` 內預編 whl 安裝，不現場編譯
- 語意搜尋 / 嵌入：`sentence-transformers` + `torch`（CPU-only build，無 CUDA）
- 即時通訊：Redis + SocketIO（redis 釘 `<6`，6/7/8.x 在 gevent 下會壞 pubsub）
- 欄位級加密：`crypto.py`，AES-256-GCM，`EncryptedText` SQLAlchemy type（DB 存密文、Python 端拿明文）

### 應用入口與組態

| 檔案 | 說明 |
|------|------|
| `app.py` | `create_app()` 工廠、藍圖註冊、開機 schema 修復（跨 worker advisory lock 序列化）|
| `wsgi.py` / `wsgi_lazy.py` | gunicorn 進入點（`wsgi:app`）；含 `pkg_resources` shim 供 face_recognition |
| `config.py` | `Config`；`FLASK_DEV_MODE=1` 時硬性要求 sqlite（防手滑連到 prod PG）|
| `extensions.py` | `db` / `login_manager` / `limiter` / `socketio` 單例 |
| `models.py` | 全部資料表（見下）|
| `features.py` | 模組權限系統（`AVAILABLE_FEATURES` + 權限檢查 helper）|
| `crypto.py` | 欄位級 AES-256-GCM 加解密 |
| `gunicorn.conf.py` | prod：gevent worker、2 workers |

### 模組（藍圖）

| 藍圖 | URL | 功能 |
|------|-----|------|
| `auth` | `/` | 帳號註冊/登入、4 位數 PIN、trusted device |
| `face` | | 人臉登錄（enroll）與驗證 |
| `notes` | `/notes/` | 店務筆記 CRUD、checklist、附件、標籤、AI 摘要、公告 |
| `mynotes` | `/mynotes/` | 私人筆記（只有建立者本人看得到）|
| `vetting` | `/vetting/` | 人員背景查詢（多來源比對、判決書站內檢視、白名單電話）|
| `icinfo` | | 證件資訊 chip / 居留證卡 |
| `weather` | | 天氣查詢（OpenWeatherMap）|
| `device` | | 信任裝置管理 |
| `admin` | `/admin/` | 管理後台（使用者/角色/白名單/刪除）|
| `dev` | `/dev` | **僅開發模式註冊**：免密登入選擇器，一鍵切換身分 |

### 權限模型（`features.py`）

- 種子 admin（`username == 'admin'`）永遠擁有全部模組（bypass）。
- 其他所有人（含 super_admin/admin/user）**嚴格依 `user.features[]` 判斷**；角色本身不再自動授權。
- 沒權限的路由直接 `abort(404)`（看不見即不存在）。

### 資料表（`models.py`）

`Store` · `User` · `Note` · `NoteLog` · `UserLog` · `ChecklistItem` · `NoteAttachment` ·
`LoginToken` · `WeatherCache` · `TrustedDevice` · `PrivateNote` · `PrivateChecklistItem` ·
`PrivateAttachment` · `IcinfoQueryLog`（表名 `v_aux_logs`）· `VettingQueryLog` ·
`VettingWhitelistPhone` · `Announcement` · `AnnouncementAttachment` · `AiTask` ·
`Tag` · `NoteTag` · `ChecklistItemTag`

---

## 本地開發（SQLite，port 5000）

> 前提：已建好 repo 根目錄的 `venv`（見下方「環境重建」）。

```bash
cd app_unified
bash scripts/run_local.sh        # 會 source .env.local → 首次自動 seed → 啟動
```

- 設定檔：`app_unified/.env.local`（gitignore，不 commit）。`FLASK_DEV_MODE=1` + `DATABASE_URL=sqlite:///./instance/dev.db`。
- 首次啟動會跑 `scripts/seed_local.py` 建假資料（idempotent；刪掉 `instance/dev.db` 可重 seed）。
- 開發登入選擇器：<http://localhost:5000/dev>（免密切換身分）。
- `run_local.sh` 會自動殺掉佔用 5000 的舊 process。

需要的 env（`.env.local` 已備好）：`FLASK_DEV_MODE`、`DATABASE_URL`（sqlite）、`SECRET_KEY`、
`DB_ENCRYPTION_KEY`（AES-256 urlsafe base64 的 32 bytes；產生方式：
`python -c "from crypto import generate_key; print(generate_key())"`）。AI/R2/天氣的 key 本機可留空、功能自動降級。

### 測試

```bash
cd app_unified
../venv/bin/python -m pytest        # tests/ 下數十個測試（見 .github/workflows/test.yml）
```

---

## 環境重建（換新機 / 系統重灌後）

**重點：專案綁 Python 3.12**（prod base = `python:3.12-slim`；`wheels/` 內 dlib 是 `cp312`
專用；torch 釘 `2.12.0+cpu`）。系統預設的 Python 3.14 **不相容**，務必另裝 3.12。

1. **裝 Python 3.12**（Ubuntu 無 sudo 免密時，二選一）
   - 有 sudo：`sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv python3.12-dev`
   - 免 sudo：裝 `uv`（`curl -LsSf https://astral.sh/uv/install.sh | sh`）→ `uv python install 3.12`
2. **native 執行庫**（dlib/face_recognition/torch import 需要）：
   `sudo apt install -y libgomp1 libopenblas0 liblapack3`（對照 `Dockerfile`）
3. **建 venv（放 repo 根目錄 `webapp/venv`，`run_local.sh` 寫死找這裡）**
   ```bash
   python3.12 -m venv venv          # 或：uv venv --python 3.12 venv
   ./venv/bin/pip install --upgrade pip setuptools wheel
   ```
4. **裝預編 wheels（人臉，不現場編譯）＋ 主依賴**
   ```bash
   ./venv/bin/pip install --no-deps app_unified/wheels/*.whl   # dlib + face_recognition_models
   ./venv/bin/pip install face_recognition                      # 純 python，故意不入 requirements
   ./venv/bin/pip install -r app_unified/requirements.txt       # 已釘版+hash（含 torch CPU、sentence-transformers…約數 GB）
   ```
5. **設定＋啟動**：`.env.local` 已備好 →
   `cd app_unified && bash scripts/run_local.sh`

> 依賴鎖：改依賴只動 `requirements.in`，再跑 `bash app_unified/scripts/lock-deps.sh` 重新產生
> `requirements.txt`（CI `deps-lock.yml` 會擋 drift，兩個檔都要 commit）。

---

## 部署（Zeabur）

- `zbpack.json` → `use_dockerfile: true`，用根目錄 `Dockerfile`（build context = repo root，路徑帶 `app_unified/` 前綴）。
- `Dockerfile.base`（`app_unified/`）預裝重依賴＋wheels，產出 base image（CI `docker-base.yml` 在 requirements/wheels 變動時重建）；`Dockerfile` 疊上程式碼。
- 啟動：`gunicorn -c gunicorn.conf.py wsgi:app`，`EXPOSE 8080`。
- 開機時每個 worker 各跑一次 schema 修復 DDL；用 PG advisory lock（key `823641001`）序列化避免跨 worker deadlock（2026-07-10 prod 實錄過）。
- prod 環境變數在 Zeabur dashboard 設定：`DATABASE_URL`（PG）、`SECRET_KEY`、`DB_ENCRYPTION_KEY`、
  R2 憑證、AI keys、`RATELIMIT_STORAGE_URL`（Redis）、`ALLOWED_ORIGINS`、`SESSION_COOKIE_SECURE=true`。
- 日誌：容器內另寫一份 `/tmp/app.log`（10MB rotate ×2），因 Zeabur log viewer 對此 service 曾卡 stale。
- 遷移工具：`migrate_sqlite_to_postgres.py`、`migrate_encrypt_fields.py`、`migrations/`（Flask-Migrate）。

---

## monitor — 取證用獨立 service

尖峰時段連不上卻查不到證據（`/tmp/app.log` 隨容器重啟清空、Zeabur log viewer 卡 stale），
所以另開一個**獨立 container** 收證據。完整設計見
`docs/superpowers/specs/2026-05-08-monitor-service-design.md`（本版是其縮減實作，
計畫在 `docs/superpowers/plans/2026-08-14-minimal-monitor-service.md`）。

- 技術棧：Flask + Flask-SQLAlchemy + gunicorn **sync worker、`workers=1`**
  （多 worker 會各跑一份探測迴圈 → 取樣重複）。**不用 gevent、不 monkey patch。**
- 儲存：寫 app-unified 既有的 PG，兩張 `mon_` 前綴表（`mon_probe_samples`、`mon_events`）
- 與 `app_unified/` **0 cross-import**；Docker build context = `monitor/`，必須自給自足

| 路由 | 用途 |
|------|------|
| `/` | 密碼保護的檢視頁：異常摘要、worker 重啟、事件、取樣 |
| `/ingest` | 收 app-unified 推來的診斷事件（Bearer token）|
| `/healthz` | 健康檢查 |

**兩條證據線**：探測取樣回答「壞在哪一層」（DNS / TCP / TLS / app 不回應 / 5xx）；
診斷事件回答「是什麼卡住它」（阻塞秒數 + 當下堆疊）。

**`PROBE_PUBLIC_URL` 不可省略**：只探內網 origin 時，DNS / Cloudflare 邊緣故障會被回報成
「一切正常」，而使用者正連不上。

**`/ingest` 只收 `kind="hub_block"` 白名單事件**，資料結構上不含個資，因此不做欄位加密。
日後要擴大收一般 app log，**必須先補 `EncryptedText`**。

### app-unified 端的配套（`hub_watchdog.py` + `monitor_ship.py`）

`hub_watchdog` 用真 OS thread 監看 greenlet 心跳，hub 被同步工作卡住超過門檻就抓堆疊並
當場推給 monitor。**hub 卡住時任何 greenlet 都跑不動，等恢復再上報就太遲**（gunicorn
`timeout=60` 會先 SIGKILL worker）。因此這條路徑上有三個不可違反的規則，都有測試守著：

1. 檢查迴圈用 `get_original("time","sleep")` — 被 patch 的 `time.sleep` 會讓真 OS thread
   等一個它沒有的 hub，迴圈永遠不前進、監控靜靜失效。
2. 建執行緒用 `_thread.start_new_thread` — 連 `get_original` 的 `threading.Thread` 都不行，
   它的 `start()` 會等一個已被 patch 成 gevent 版本的 Event，跨執行緒 set 會隨機掛死。
3. 上報用 `get_original` 的 socket/getaddrinfo，且**只走內網明文 HTTP**（避開同樣被 patch
   的 `ssl`）。

### 環境變數

monitor service：`DATABASE_URL`（同 app-unified 的 PG）、`MONITOR_PASSWORD`、
`MONITOR_INGEST_TOKEN`、`SECRET_KEY`、`PROBE_PUBLIC_URL`、`PROBE_ORIGIN_URL`、
`PROBE_INTERVAL_SECONDS`、`RETENTION_DAYS`。

app-unified 端：`MONITOR_INGEST_URL`（monitor 內網 hostname）、`MONITOR_INGEST_TOKEN`（同值）、
`HUB_BLOCK_THRESHOLD`（預設 5 秒）。

> **內網 hostname 是 Zeabur 的服務 ID，不是介面上的顯示名稱。**
> 形如 `service-<24位十六進位>`，例如 `http://service-xxxxxxxx:8080/ingest`。
> `monitor`、`wnote` 這類自訂名稱**解析不出來**。查法：進 app-unified 的指令 shell 跑
> `cat /etc/resolv.conf` 看 search 網域，再從對方 pod 名稱取 `service-` 開頭那段
> （pod 名是 `service-<id>-<replicaset>-<隨機碼>`）。
>
> ⚠ **monitor service 若被刪除重建，ID 會變，ingest 會「靜默」失效** ——
> `ship_event` 設計成絕不拋例外，失敗只回 `False`，不會有任何告警。
> 重建後務必重測：
> ```
> python3 -c "import sys;sys.path.insert(0,'/app');import monitor_ship;print(monitor_ship.ship_event('hub_block','驗證','(test)'))"
> ```

> 本機臨時探測用 `app_unified/scripts/probe_outage.py`（同樣的分層判定，跑在自己電腦上，
> 適合 monitor 還沒上線、或要一個 Zeabur 之外的視角時）。

---

## CI（`.github/workflows/`）

| workflow | 觸發 | 作用 |
|----------|------|------|
| `test.yml` | push/PR | 跑 `pytest` |
| `deps-lock.yml` | `requirements.*` 變動 | 檢查 `requirements.txt` 是否與 `.in` 同步（drift 就 fail）|
| `docker-base.yml` | requirements/wheels/Dockerfile.base 變動 | 重建 base image 推到 ghcr |
| `docker.yml` | push | 建/推應用 image |

`test.yml` 內有兩個 job：`test`（app_unified，需 dlib/torch 重依賴）與 `monitor-test`
（monitor，只有幾個輕依賴、數秒跑完）。

---

## Legacy：app1_notes / app2_weather（已被 app_unified 取代）

早期把功能拆成兩個獨立 Flask app（App1 筆記 port 5000、App2 天氣 port 5001），用
`start_app1.sh` / `start_app2.sh` 啟動。功能與模組已整併進 `app_unified`。除非要對照舊行為，
一般開發都在 `app_unified`。

### 鏡頭問題紀錄（HP Wide Vision 5MP / VMware，仍具參考價值）

- 症狀：影像上方正常、下方 80% 純綠 → USB 2.0 頻寬不足致 MJPEG 幀損壞。
- 解法：VMware USB 控制器改 **USB 3.1 (xHCI)** → 裝 `v4l2loopback` → GStreamer 橋接出 `/dev/video10`
  → 用 **Google Chrome（非 snap）** 開啟（snap 瀏覽器的 XDG Portal 無法正確處理 PipeWire 鏡頭串流）。

```bash
sudo modprobe v4l2loopback devices=1 video_nr=10 card_label="VirtualCam" exclusive_caps=1
nohup gst-launch-1.0 v4l2src device=/dev/video1 io-mode=2 \
  ! image/jpeg,width=640,height=480,framerate=30/1 \
  ! avdec_mjpeg ! videoconvert ! video/x-raw,format=YUY2 \
  ! v4l2sink device=/dev/video10 sync=false > /tmp/gst_bridge.log 2>&1 &
```
