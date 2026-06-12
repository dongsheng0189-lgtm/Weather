# 依賴根治：lockfile 鎖版 + torch CPU-only — Design

- **Date**: 2026-06-12
- **Status**: Approved (brainstorm), pending implementation plan
- **Branch (planned)**: `chore/dependency-hardening`
- **Trigger**: F17 incident #13（2026-06-11）— requirements 全 `>=` 鬆 pin，箱子重建抓到 redis-py 8.0.0 打爆 SocketIO pubsub。根因是「依賴漂移」，本案根治。

## 背景 / 問題

`app_unified/requirements.txt` 目前除 `redis>=4.5,<6` 外全部 `>=`（鬆 pin）。F17 自建 base image（`Dockerfile.base`）每次重建都跑全新 `pip install -r requirements.txt`，會抓「當下最新且滿足」的版本 → 不可重現、上游出新版即可能破壞 prod。同時 sentence-transformers 透過 transitive 拉進 **GPU 版 torch-2.12.0 + 整套 `nvidia-cuda-*`（~3GB）**，但 prod 是 **2 CPU / 8GB / 無 GPU**，這套 CUDA 是純死重。

## 目標

1. **完全可重現的依賴鎖版**（lockfile，含 transitive + hash），消除漂移。
2. **torch 換 CPU-only**，移除 `nvidia-cuda-*`，縮小 image ~2GB+。
3. **CI 自動把關 lockfile 不漂移**（drift-gate，非自動 commit）。

非目標：升級任何套件（鎖到「現在 prod 已驗證 OK」的版本為主）、改 runtime 行為（torch CPU-only 是唯一刻意的行為改變，需驗證）。

## 決策摘要（brainstorm 結論）

| 決策 | 選擇 | 理由 |
|---|---|---|
| 範圍 | 鎖版 + torch CPU-only **一次箱子重建** | 兩者都要重建箱子，一次做完最省中斷 |
| 機制 | **pip-tools**（`requirements.in` → `requirements.txt` + hashes） | 標準 lockfile；升級時 transitive 自動對齊；hash 防供應鏈竄改 |
| CI | **drift-gate**（方案 1） | 箱子重建很重（34min / 推 ~3GB），重建時機要人控、避尖峰；不自動 commit、不自動觸發重建 |

## 環境前提（已查證 2026-06-12）

- 本機無 docker（compile 在 venv 跑）。venv = **Python 3.12.3 / x86_64**，對齊 prod base `python:3.12-slim`（linux amd64）→ venv 產出的 lock 與 prod 一致。
- pip-tools 尚未裝（dev/CI-only，**不進 runtime image**）。

## 設計

### ① 檔案與機制

- **新增 `app_unified/requirements.in`** — 人手編輯的直接依賴清單（= 現有 requirements.txt 那串直接 dep，保留 `redis<6`，處理 torch / 排除 face_recognition，見 ②）。
- **`app_unified/requirements.txt`** — 改為 **pip-compile 產出**的全鎖（`==`）＋ `--hash` lockfile。**檔名不變**，故 `Dockerfile.base:29-30`（`COPY requirements.txt` + `pip install -r requirements.txt`）無需改路徑。
- **新增 `app_unified/scripts/lock-deps.sh`** — 在 venv 跑：
  - 預設：`pip-compile --generate-hashes --output-file requirements.txt requirements.in`（**不帶 `--upgrade`**：沿用既有 requirements.txt 當起點，沒人為改動則零變動）。
  - `scripts/lock-deps.sh --upgrade` → 加 `--upgrade`，明確升版。
  - 腳本先確保 venv 有 pip-tools（pin 版本，見下）。
- pip-tools 版本 **pin**（例如 `pip-tools==7.x`，實作時取當下穩定版），本機與 CI 用同一版避免 compile 輸出差異。
- runtime image **不含** pip-tools。

### ② torch CPU-only ＋ dlib/wheels 處理（關鍵雷）

- `requirements.in` 頂部加 `--extra-index-url https://download.pytorch.org/whl/cpu`，torch 釘 CPU build（`torch==<ver>+cpu`）。CPU 版 torch **不依賴** `nvidia-cuda-*` → 整套 CUDA 套件自動從 lock 消失。
  - 確切可用版本（含 py3.12/linux wheel）於實作時由箱子 build 驗證；驗收條件：**compile 後的 `requirements.txt` 內 `grep -i nvidia` 為空**。
  - pip-compile `--generate-hashes` 會一併抓 pytorch CPU index 的 wheel hash；`--extra-index-url` 行會被寫進 requirements.txt 開頭，`pip install` 時據此找 wheel。
- **dlib / face_recognition 不進 lockfile**：兩者由 `Dockerfile.base:25-26` 的 `COPY wheels/ + pip install --no-deps wheels/*.whl` 預裝（已是 pinned 編譯 wheel）。
  - 若放進 lockfile，`--require-hashes` 安裝會改去 PyPI 抓 dlib **原始碼重新編譯**（慢、需 build tools）→ 破壞 wheels 優化。
  - 故 `requirements.in` **排除 `face_recognition`**（連帶 dlib 不會進 lock），以註解標明「由 wheels/ --no-deps 提供」。
  - 安裝順序維持：Dockerfile 先裝 wheels（--no-deps、非 hash 檢查、獨立 pip 呼叫），再裝 requirements.txt（`Dockerfile.base:30` 改為 `pip install --require-hashes -r requirements.txt` 強制驗證所有 hash；pip-compile `--generate-hashes` 已保證每個套件都有 hash）。兩者為獨立 pip 呼叫、套件集不重疊，無衝突。
- 容量（[[feedback_capacity_check]]）：image 小 ~2GB+ → Zeabur 拉更快、disk / layer 記憶體更省、CPU torch 無 CUDA context RAM 佔用更低 → 對 2CPU/8GB 淨正向。

### ③ CI drift-gate

- **新 workflow `.github/workflows/deps-lock.yml`**：
  - 觸發：PR / push 動到 `app_unified/requirements.in` 或 `app_unified/requirements.txt`（+ `workflow_dispatch`）。
  - 步驟：`python:3.12-slim` → 裝 pinned pip-tools → 跑 `pip-compile --generate-hashes`（**不帶 `--upgrade`、沿用 repo 內既有 requirements.txt 當起點**）→ `git diff --exit-code app_unified/requirements.txt`。
  - 邏輯：不帶 `--upgrade` → pip-compile 保留既有 pin，只有當 `requirements.in` 改動逼出新 resolution 才會變 → 不會因上游出新版而誤報。
  - .in 改了卻忘記重 compile → diff 非空 → **CI fail**。
- CI **不自動 commit**、**不自動觸發箱子重建**；升版/重建是人為明確動作 → 時機可控、可避尖峰。

### ④ Rollout & 驗證（一次箱子重建）

1. 開 branch `chore/dependency-hardening`。
2. 加 `requirements.in` / 重產 `requirements.txt` / `scripts/lock-deps.sh` / `deps-lock.yml`；本機跑 `scripts/lock-deps.sh` 產 lock，確認 `grep -i nvidia requirements.txt` 為空、face_recognition/dlib 不在內。
3. 本機 `pytest tests/` 614 綠（純依賴 metadata 改動，行為應不變）。
4. push（**非尖峰、事前告知** [[feedback_warn_build_deploy_impact]]）→ `docker-base.yml` 重建箱子（~34min、推 ~3GB）→ `docker.yml` app build chain。
5. **箱子重建後實機驗證**：
   - 箱子 build log `grep -i nvidia` 為空（CUDA 真消失）、image size 確實縮小。
   - prod `python3 -c "import torch;print(torch.__version__)"` → 帶 `+cpu`。
   - **AI 摘要實跑一篇**確認 CPU 推論正常（torch 換版主要風險點）。
   - SocketIO 即時同步仍正常（redis 5.x lock 沒被動到）。
   - `pytest tests/` CI 綠。
6. **Rollback**：箱子有問題 → Zeabur 回前一版 `weather-base` image；app 層回 `dee56f1`。可逆、低風險。

## 風險

| 風險 | 緩解 |
|---|---|
| torch CPU build 版本/wheel 不存在或與 sentence-transformers 不相容 | 實作時由箱子 build 實際 resolve 驗證；驗收 `grep -i nvidia` 為空 + AI 摘要實跑 |
| dlib 誤入 lockfile → 重編譯 | requirements.in 排除 face_recognition，並在 build 後檢查 dlib 不在 requirements.txt |
| 本機 venv 與 CI 容器 compile 輸出有差 → drift-gate 誤報 | pip-tools pin 同版、py3.12/amd64 對齊、compile 不帶 `--upgrade` 沿用既有 lock |
| 箱子重建撞尖峰 | 非尖峰推、事前告知；CI 不自動觸發重建 |
| CPU 推論變慢影響 AI 摘要體感 | MiniLM-L12 在 CPU 上本就可接受（prod 無 GPU、現況 GPU torch 也沒真的用到 CUDA）；驗證時實測一篇 |

## 驗收條件

- `requirements.in`（人編）+ `requirements.txt`（pip-compile 全鎖+hash）+ `scripts/lock-deps.sh` + `deps-lock.yml` 落地。
- `requirements.txt` 內 `grep -i nvidia` 為空、無 face_recognition/dlib。
- drift-gate CI 在「.in 與 lock 同步」時綠、「故意改 .in 不重 compile」時紅。
- 箱子重建成功、image 縮小、prod torch 為 `+cpu`、AI 摘要實跑正常、`pytest tests/` 綠、SocketIO 即時同步正常。
