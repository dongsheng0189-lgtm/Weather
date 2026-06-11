# 自建 base image（pre-baked base）設計

日期：2026-06-11
分支：`feat/prebaked-base-image`

## 問題

每次 deploy 的 docker build 不穩又慢：
- `python:3.12-slim` 是上游浮動 tag，被 Docker Hub retag 後（6/10→6/11 `090ba77`→`2eb3`）整包 cache 冷掉。
- `type=registry,mode=max` cache 在 GHCR 不可靠：export「成功」但 import 0 命中，且會卡死在 export 階段（6/11 一次 build 卡 21min 才取消）。
- 結果：torch(~700MB) + MiniLM 模型預載每次 deploy 都可能重跑，HF 429 每次都可能咬，Zeabur 每次重拉整包 image。

## 目標（hands-off，見 [[feedback_prefer_handsoff_automation]]）

把「重又穩定」的依賴 bake 成一個自家 base image，app image 只剩 code。user 平常改 code/畫面照舊 push、零手動；改套件也只是 push，base 自動重建、app 自動接上。

## 架構：兩層 image

### 1. `app_unified/Dockerfile.base`（新）— 重又穩定的「箱子」
內容（從現有 `Dockerfile` 搬過來、原樣）：
- `FROM python:3.12-slim@sha256:2eb3cdf88d45b1c304d593dfe77f431e00ca13c6e707416a259ed3987d5c6aa8`（**base 自己 pin upstream digest** → 可重現、不被 Docker Hub 漂移；要更新 base OS 時手動 bump 此 digest）
- `apt-get install libgomp1 libopenblas0 liblapack3`
- `useradd -m -u 1000 appuser`
- `WORKDIR /app`
- `pip install --upgrade pip setuptools wheel`
- `COPY app_unified/wheels/ /tmp/wheels/` + `pip install --no-deps /tmp/wheels/*.whl`（dlib/face_recognition 預編 wheels）
- `COPY app_unified/requirements.txt .` + `pip install -r requirements.txt`（torch、sentence-transformers 等）
- `chown -R appuser /usr/local/lib/python3.12/site-packages`
- `ENV SENTENCE_TRANSFORMERS_HOME=/opt/st_cache`、`ENV HF_HUB_ETAG_TIMEOUT=30`
- MiniLM 模型預載 RUN（**含現有 6 次 retry + 退避**，HF 429 防護不變）+ `chown /opt/st_cache`
- `RUN python -c "import dlib; print('dlib OK')"`（驗證）

build context = repo root（`COPY app_unified/...` 需要）。
推送：`ghcr.io/stvlin1988-alt/weather-base:latest`。

### 2. `app_unified/Dockerfile`（改瘦）— 只剩 app code
```dockerfile
FROM ghcr.io/stvlin1988-alt/weather-base:latest
WORKDIR /app
# torch / 模型 / apt / wheels / ENV(st_cache) 全繼承自 base
COPY --chown=appuser:appuser app_unified/ .
USER appuser
EXPOSE 8080
ENV PYTHONUNBUFFERED=1
ENV SESSION_COOKIE_SECURE=true
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
```
- ENV（`SENTENCE_TRANSFORMERS_HOME` 等）由 base 繼承，不重宣告。
- 浮動 tag `weather-base:latest`（自家 base、只有我們會推 → 無上游漂移雷；見 [[feedback_prefer_handsoff_automation]] 為何不 pin digest）。

### 3. CI workflows

**`.github/workflows/docker-base.yml`（新）— 自動重建箱子**
- 觸發：
  - `push` 到 `main`，paths：`app_unified/requirements.txt`、`app_unified/wheels/**`、`app_unified/Dockerfile.base`、`.github/workflows/docker-base.yml`
  - `workflow_dispatch`（手動備援）
- `permissions: packages: write`
- buildx build `file: app_unified/Dockerfile.base`、`context: .`、push `ghcr.io/<repo>-base:latest`（+ `sha-<short>` 備查）
- 不需 registry cache（箱子很少 build，每次乾淨建可接受；且這正是要擺脫的不穩來源）。

**`.github/workflows/docker.yml`（改）— app build 變輕**
- 移除 `cache-from` / `cache-to`（registry cache 整段拿掉 → 解決卡死/0 命中）。app build 只是 `FROM 箱子 + COPY code`，~1–2min，不需 cache。
- 觸發新增 `workflow_run`：在 `docker-base.yml` 成功完成後自動觸發（接上新箱子重新部署），且只在 base run conclusion=success 時跑。保留既有 push(app code 變動) + workflow_dispatch。
- push paths **新增排除** base 輸入檔：`!app_unified/requirements.txt`、`!app_unified/wheels/**`、`!app_unified/Dockerfile.base` → 改這些只走 docker-base.yml → workflow_run 那條路徑，避免同一次 push 並行跑兩個 app build（消除「app build on 舊箱子」的 race）。
- 其餘（metadata tags latest/main/sha、build-args GIT_SHA、push）不變。

## 自動流程（user 視角零手動）

| user 動作 | 系統 |
|---|---|
| 改 routes/template/JS → push | docker.yml：`FROM 箱子 + COPY code` → ~1–2min → 部署 |
| 改 `requirements.txt` → push | docker-base.yml 自動重建箱子 → 完成後 `workflow_run` 觸發 docker.yml → app 接新箱子 → 部署 |
| 要更新 base OS digest | 手動改 `Dockerfile.base` 的 `FROM ...@sha256:` 後 push（少見、屬「會影響 build」需先告知 [[feedback_warn_build_deploy_impact]]） |

## Bootstrapping（首次上線順序，重要）

第一次合併此變更時 `weather-base:latest` 還不存在於 GHCR → app build 的 `FROM weather-base:latest` 會失敗。**rollout 順序必須：**
1. 合併/push 後，**先手動 `gh workflow run docker-base.yml`** 建出箱子、確認推上 GHCR 成功。
2. 再讓 docker.yml 跑（workflow_run 會自動接，或手動 dispatch）→ app build on 箱子。
3. 確認 prod 起得來（dashboard footer SHA、`/healthz`）。

## 解決 / 取捨

- ✅ torch/模型/apt 只在「箱子重建」(少見) 才跑；app deploy 不再重抓。
- ✅ 不再依賴 GHCR registry cache（卡死/0 命中根因）。
- ✅ HF 429 只在箱子重建時才碰。
- ✅ upstream 漂移：箱子 pin digest、app 用自家 base。
- ⚠️ 箱子 image ~2–3GB：Zeabur **首次**拉箱子層較久，之後每次只拉小 code 層 → 快。不影響 runtime RAM（prod 2CPU/8GB，見 [[feedback_capacity_check]]）。
- ✅ 同一次 push 同時改 `requirements.txt`+app code：因 docker.yml push 已排除 base 輸入檔，requirements 變動只觸發 docker-base.yml；箱子建好後 workflow_run 再觸發 app build → app 一定吃到新箱子（無 race）。純 app code 變動則直接走 docker.yml。
- ⚠️ GHCR `weather-base` 套件首次由 workflow GITHUB_TOKEN 建立；app build 同 repo token 可 pull；Zeabur 只拉 app image（箱子層含在內）不需 base 權限。

## 測試 / 驗證

本機無 docker CLI、無法本機 build。驗證走 CI + config parser：
- config 驗證（[[feedback_validate_configs]]）：commit 前用 YAML parser 驗 `docker-base.yml`/`docker.yml`、Dockerfile 語法肉眼+結構檢查。
- CI 實證：①手動 dispatch docker-base.yml → 確認 `weather-base:latest` 推成功 ②app build on 箱子成功、image 推成功 ③prod 部署起得來、AI 摘要(模型在 /opt/st_cache)、人臉(dlib) 正常。
- rollback：保留舊 `Dockerfile`（單檔版）git 歷史；若 base 方案出包，revert 此 branch 即回到 `01089c6` 後的單檔 build。

## 不在範圍 / YAGNI

- 不做 base image 多版本矩陣 / 多 arch（prod 單 amd64）。
- 不做 base 內容 hash 自動 tag（浮動 latest 已足夠 hands-off）。
- 不順手改 `COPY app_unified/ .` 把 wheels 排除（現狀本來就含、非本次目標；可日後 .dockerignore 優化）。
