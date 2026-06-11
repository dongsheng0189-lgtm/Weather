# 自建 base image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 torch/sentence-transformers/MiniLM 模型/dlib 等「重又穩定」依賴 bake 成自家 base image，app image 只剩 code，讓每次 deploy 快又穩、user 零手動。

**Architecture:** 兩層 image — `Dockerfile.base`(重依賴，推 `weather-base:latest`) + 瘦身 `Dockerfile`(`FROM weather-base:latest` + COPY code)。新 `docker-base.yml` 在 base 輸入檔變動時自動重建箱子；`docker.yml` 拿掉不穩的 registry cache、用 `workflow_run` 串接、push 排除 base 輸入檔。

**Tech Stack:** Docker multi-image、GitHub Actions、GHCR、buildx。**無 pytest 單元測試**（純 infra）；驗證 = YAML parser + Dockerfile 結構檢查 + CI 實證。

**分支：** `feat/prebaked-base-image`（已開，從 main）

**前置事實（已驗證，照用）：**
- repo = `stvlin1988-alt/Weather`；app image = `ghcr.io/stvlin1988-alt/weather`；base image 將是 `ghcr.io/stvlin1988-alt/weather-base`。
- 現有 `app_unified/Dockerfile` 完整內容見 Task 1/2（從它拆）。
- 現有 `.github/workflows/docker.yml` 見 Task 4。
- 本機**無 docker CLI / hadolint**；驗證靠 parser + grep + CI。

---

## 檔案結構

| 檔案 | 動作 | 責任 |
|---|---|---|
| `app_unified/Dockerfile.base` | 新增 | 重依賴箱子（apt/wheels/torch/模型/dlib），pin upstream digest |
| `app_unified/Dockerfile` | 改瘦 | 只 `FROM 箱子 + COPY code` |
| `.github/workflows/docker-base.yml` | 新增 | base 輸入變動時自動重建箱子 |
| `.github/workflows/docker.yml` | 修改 | 去 cache、加 workflow_run、push 排除 base 輸入 |
| `app_unified/docs/superpowers/specs/2026-06-11-prebaked-base-image-design.md` | 已存在 | spec |

驗證指令（venv 有 PyYAML）：`source /home/hirain0126/projects/webapp/venv/bin/activate`；YAML 用 `python -c "import yaml,sys; yaml.safe_load(open(f))"`.

---

## Task 1: 建 `Dockerfile.base`（重依賴箱子）

**Files:** Create `app_unified/Dockerfile.base`

- [ ] **Step 1: 建檔**

寫入 `app_unified/Dockerfile.base`（內容＝從現有 Dockerfile 搬出的重層，原樣不改行為）：

```dockerfile
# Pre-baked base image: heavy + stable deps (torch, sentence-transformers, MiniLM
# model, dlib/face_recognition wheels, system libs). Rebuilt RARELY — only when
# requirements.txt / wheels / this file change — via .github/workflows/docker-base.yml.
# Pin upstream python:3.12-slim by digest so a Docker Hub retag can't cold-bust builds.
# Bump this digest deliberately to take base-OS updates.
FROM python:3.12-slim@sha256:2eb3cdf88d45b1c304d593dfe77f431e00ca13c6e707416a259ed3987d5c6aa8

LABEL "language"="python"
LABEL "framework"="flask"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libopenblas0 \
    liblapack3 \
    && rm -rf /var/lib/apt/lists/*

# 提前建 user，後面 chown / --chown 都靠它
RUN useradd -m -u 1000 appuser

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Pre-built wheels — no compilation, no git needed
COPY app_unified/wheels/ /tmp/wheels/
RUN pip install --no-cache-dir --no-deps /tmp/wheels/*.whl && rm -rf /tmp/wheels

# Build context is repo root — must use app_unified/ prefix
COPY app_unified/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# chown site-packages 拆獨立一層
RUN chown -R appuser:appuser /usr/local/lib/python3.12/site-packages

# Pre-load MiniLM model into image (saves 5-15s on first AI summary after redeploy)
ENV SENTENCE_TRANSFORMERS_HOME=/opt/st_cache
ENV HF_HUB_ETAG_TIMEOUT=30
# 重試 + 指數退避：HuggingFace 偶爾對 CI runner IP 回 HTTP 429（rate limit），
# 任一次成功即 break；6 次全失敗才讓 build fail。
RUN set -e; \
    for i in 1 2 3 4 5 6; do \
      echo "Model preload attempt $i/6..."; \
      if python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"; then \
        ok=1; break; \
      fi; \
      echo "attempt $i failed (likely HuggingFace HTTP 429); backing off $((i*15))s"; \
      sleep $((i*15)); \
    done; \
    if [ -z "$ok" ]; then echo "ERROR: model preload failed after 6 attempts"; exit 1; fi; \
    chown -R appuser:appuser /opt/st_cache

# 驗證 wheels 已安裝（dlib via face_recognition wheel）
RUN python -c "import dlib; print('dlib OK')"
```

- [ ] **Step 2: 結構驗證**

Run:
```bash
cd /home/hirain0126/projects/webapp && \
grep -qE '^FROM python:3.12-slim@sha256:2eb3cdf88d45' app_unified/Dockerfile.base && echo "FROM-pin OK"; \
grep -q 'SentenceTransformer' app_unified/Dockerfile.base && echo "model-preload OK"; \
grep -q 'import dlib' app_unified/Dockerfile.base && echo "dlib OK"; \
grep -q 'COPY app_unified/requirements.txt' app_unified/Dockerfile.base && echo "requirements OK"
```
Expected: 印出全部 4 個 OK。

- [ ] **Step 3: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add app_unified/Dockerfile.base
git commit -m "build(docker): add pre-baked base image Dockerfile (heavy deps + model)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 瘦身 `Dockerfile`（app 只剩 code）

**Files:** Modify `app_unified/Dockerfile`（整檔覆寫）

- [ ] **Step 1: 覆寫 Dockerfile**

把 `app_unified/Dockerfile` 整個內容換成：

```dockerfile
# App image: only the source code, layered on the pre-baked base image
# (ghcr.io/stvlin1988-alt/weather-base — see app_unified/Dockerfile.base +
# .github/workflows/docker-base.yml). torch / MiniLM model / dlib / system libs /
# SENTENCE_TRANSFORMERS_HOME are all INHERITED from the base.
# Floating tag is safe here: this base is our own (only our CI pushes it), unlike
# the upstream python:3.12-slim rolling tag that caused cold cache busts.
FROM ghcr.io/stvlin1988-alt/weather-base:latest

WORKDIR /app

# 唯一受 .py 改動觸發 rebuild 的層；--chown 直接帶 ownership（appuser 來自 base）
COPY --chown=appuser:appuser app_unified/ .

USER appuser

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
ENV SESSION_COOKIE_SECURE=true

# GHA push 時自動帶 commit SHA 進來；管理頁面顯示前 4 碼供版本比對
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
```

- [ ] **Step 2: 結構驗證（確認重層已搬走、只剩 code）**

Run:
```bash
cd /home/hirain0126/projects/webapp && \
grep -qE '^FROM ghcr.io/stvlin1988-alt/weather-base:latest' app_unified/Dockerfile && echo "FROM-base OK"; \
grep -q 'COPY --chown=appuser:appuser app_unified/ .' app_unified/Dockerfile && echo "copy-code OK"; \
! grep -q 'pip install' app_unified/Dockerfile && echo "no-pip OK"; \
! grep -q 'SentenceTransformer' app_unified/Dockerfile && echo "no-model OK"; \
! grep -q 'apt-get' app_unified/Dockerfile && echo "no-apt OK"
```
Expected: 印出全部 5 個 OK（重層確實只在 base、不在 app）。

- [ ] **Step 3: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add app_unified/Dockerfile
git commit -m "build(docker): slim app Dockerfile to FROM pre-baked base + COPY code

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 建 `docker-base.yml`（自動重建箱子）

**Files:** Create `.github/workflows/docker-base.yml`

- [ ] **Step 1: 建檔**

寫入 `.github/workflows/docker-base.yml`：

```yaml
name: Build base image

on:
  push:
    branches:
      - main
    paths:
      - 'app_unified/requirements.txt'
      - 'app_unified/wheels/**'
      - 'app_unified/Dockerfile.base'
      - '.github/workflows/docker-base.yml'
  workflow_dispatch:

env:
  REGISTRY: ghcr.io

jobs:
  build-base:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set lowercase base image name
        run: echo "BASE_IMAGE=$(echo '${{ github.repository }}-base' | tr '[:upper:]' '[:lower:]')" >> $GITHUB_ENV

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.BASE_IMAGE }}
          tags: |
            type=raw,value=latest
            type=sha,format=short

      - name: Build and push base
        uses: docker/build-push-action@v5
        with:
          context: .
          file: app_unified/Dockerfile.base
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

- [ ] **Step 2: YAML parse 驗證**（[[feedback_validate_configs]]）

Run:
```bash
cd /home/hirain0126/projects/webapp && source venv/bin/activate && \
python -c "import yaml; d=yaml.safe_load(open('.github/workflows/docker-base.yml')); assert isinstance(d,dict); assert 'jobs' in d and 'build-base' in d['jobs']; print('docker-base.yml parse OK')"
```
Expected: `docker-base.yml parse OK`。

- [ ] **Step 3: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add .github/workflows/docker-base.yml
git commit -m "ci(docker): add docker-base workflow (auto-rebuild base on dep changes)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 改 `docker.yml`（去 cache + workflow_run + 排除 base 輸入）

**Files:** Modify `.github/workflows/docker.yml`

- [ ] **Step 1: 改 `on:` 觸發（替換整段 `on:`）**

把 `.github/workflows/docker.yml` 開頭的：
```yaml
on:
  push:
    branches:
      - main
      - feat/ghcr-deploy
    paths:
      - 'app_unified/**'
      - '!app_unified/tests/**'
      - '!app_unified/dev/**'
      - '!app_unified/scripts/**'
      - '.github/workflows/docker.yml'
  workflow_dispatch:
```
換成（新增排除 base 輸入檔 + workflow_run 串接）：
```yaml
on:
  push:
    branches:
      - main
      - feat/ghcr-deploy
    paths:
      - 'app_unified/**'
      - '!app_unified/tests/**'
      - '!app_unified/dev/**'
      - '!app_unified/scripts/**'
      - '!app_unified/requirements.txt'
      - '!app_unified/wheels/**'
      - '!app_unified/Dockerfile.base'
      - '.github/workflows/docker.yml'
  workflow_run:
    workflows: ["Build base image"]
    types:
      - completed
    branches:
      - main
  workflow_dispatch:
```

- [ ] **Step 2: 加 job 守門（base 失敗時不接著 build app）**

把：
```yaml
jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
```
換成（在 runs-on 後加 `if:`）：
```yaml
jobs:
  build-and-push:
    runs-on: ubuntu-latest
    if: ${{ github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success' }}
    permissions:
```

- [ ] **Step 3: 移除 registry cache（卡死/0 命中根因）**

把 Build and push step 結尾這段整個刪掉：
```yaml
          # Registry cache (persists in GHCR as a :buildcache tag) instead of
          # GHA cache, which evicts after 7 days of no access — a >1 week gap
          # between deploys forced a full no-cache rebuild → all layer digests
          # changed → Zeabur had to re-pull the whole ~GB image → ~5 min "starting"
          # (2026-06-10). Registry cache has no such eviction, so layer digests
          # stay stable across long gaps and Zeabur keeps doing fast incremental pulls.
          cache-from: type=registry,ref=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:buildcache
          cache-to: type=registry,ref=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:buildcache,mode=max
```
讓 Build and push step 變成（結尾停在 labels）：
```yaml
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          file: app_unified/Dockerfile
          push: true
          build-args: |
            GIT_SHA=${{ github.sha }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

- [ ] **Step 4: YAML parse 驗證 + 確認 cache 已移除**

Run:
```bash
cd /home/hirain0126/projects/webapp && source venv/bin/activate && \
python -c "import yaml; d=yaml.safe_load(open('.github/workflows/docker.yml')); assert 'jobs' in d; print('docker.yml parse OK')" && \
! grep -q 'buildcache' .github/workflows/docker.yml && echo "cache-removed OK" && \
grep -q 'workflow_run' .github/workflows/docker.yml && echo "workflow_run OK" && \
grep -q "'!app_unified/requirements.txt'" .github/workflows/docker.yml && echo "exclude-base-inputs OK"
```
Expected: `docker.yml parse OK` / `cache-removed OK` / `workflow_run OK` / `exclude-base-inputs OK` 全印出。

- [ ] **Step 5: Commit**
```bash
cd /home/hirain0126/projects/webapp
git add .github/workflows/docker.yml
git commit -m "ci(docker): app build FROM base, drop flaky registry cache, chain after base

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 整體 config 驗證 + 確認沒動到 Python

**Files:** 無（驗證）

- [ ] **Step 1: 兩個 workflow + Dockerfile 全部再驗一次**

Run:
```bash
cd /home/hirain0126/projects/webapp && source venv/bin/activate && \
for f in .github/workflows/docker.yml .github/workflows/docker-base.yml; do \
  python -c "import yaml; yaml.safe_load(open('$f')); print('$f OK')"; done && \
grep -c 'FROM' app_unified/Dockerfile app_unified/Dockerfile.base
```
Expected: 兩個 workflow 印 OK；`Dockerfile` 與 `Dockerfile.base` 各 1 個 FROM。

- [ ] **Step 2: 確認 Python 測試未受影響**（純 infra 改動、不該動到 app code）

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/ -q`
Expected: 與改動前一致（612 passed, 1 skipped 等量），無退步。

- [ ] **Step 3: （若 Step 1/2 有發現需修正才 commit；否則跳過）**

---

## Task 6: Rollout / Bootstrapping（手動、合併後、需 user 配合）

> ⚠️ 這是部署步驟、會影響 build/部署（[[feedback_warn_build_deploy_impact]]）且首次有**順序依賴**（箱子要先存在 app 才 build 得起來）。**不由 subagent 自動執行**；由主 session 跟 user 協調、依 [[feedback_push_policy]] / [[feedback_push_peak_hours]] 在 user 放行且非尖峰時做。

> 🔑 **bootstrapping 順序修正（final review）**：箱子要**先存在**，否則 merge commit 會立刻觸發 docker.yml、但 `FROM weather-base:latest` 還不存在 → app build 失敗一次（不影響 prod、但有雜訊）。所以**先建箱子再 merge**。

- [ ] **Step 1: 先在 branch 上建箱子**：`gh workflow run docker-base.yml --ref feat/prebaked-base-image`。
  - ⚠️ 若 gh 因「workflow 還沒在 default branch」找不到（GitHub 限制），改走 fallback：直接 merge（見 Step 2 註），merge commit 會同時觸發 docker-base.yml(建箱子) 與 docker.yml(會先失敗一次)，箱子建好後 workflow_run 自動補一次成功的 app build → 最終收斂。
  - `gh run watch <id>` 等成功；確認 `ghcr.io/stvlin1988-alt/weather-base:latest` 已推上（GHCR UI 或 `gh api /users/stvlin1988-alt/packages/container/weather-base/versions`）。首建會跑滿（torch+模型 ~5-8min、HF 429 由 retry 撐）。
- [ ] **Step 2: 確認箱子在 GHCR 後，merge + push**（user 放行後）：`feat/prebaked-base-image` merge 進 main、push。merge commit 觸發 docker.yml → 此時箱子已存在 → app build 成功（~1-2min）。
  - 註（fallback 路徑）：若 Step 1 沒先建成，merge 後那次 docker.yml 直接 run 會失敗(base 不存在)，但同 merge 觸發的 docker-base.yml 建好箱子後、workflow_run 會自動補一次成功的 app build。容許一次紅 X。
- [ ] **Step 3: 確認 app build 成功**：`gh run watch` 確認 docker.yml（push 或 workflow_run 觸發的那次）成功、image 推上、app build 快（~1-2min）。
- [ ] **Step 4: 驗 prod**：Zeabur 部署完，確認 dashboard footer git SHA = 新 commit 前 4 碼、`/healthz` 200、AI 摘要（模型在 /opt/st_cache）與人臉登入（dlib）正常。
- [ ] **Step 5: 觀察下一次「純 code 改動」部署**：確認 app build ~1-2min、Zeabur 只拉小層（驗證 hands-off 目標達成）。

---

## Self-Review

- **Spec coverage：** Dockerfile.base（T1）✓；app 瘦身 FROM base（T2）✓；docker-base.yml 自動觸發+dispatch（T3）✓；docker.yml 去 cache+workflow_run+if 守門+排除 base 輸入（T4）✓；config 驗證+pytest 不退步（T5）✓；bootstrapping 順序+prod 驗證（T6）✓；取捨/容量在 spec 已述。
- **Placeholder scan：** 無 TBD；所有 step 有完整檔案內容或精確指令。Task 6 是部署程序（本質手動），步驟具體可執行。
- **一致性：** image 名 `ghcr.io/stvlin1988-alt/weather-base`（T2 FROM、T3 BASE_IMAGE 推導）一致；`weather-base:latest` 浮動 tag、upstream digest `2eb3cdf88d45` pin、`workflow_run: ["Build base image"]` 對應 docker-base.yml 的 `name:` 一致。
- **本機限制：** 無 docker CLI → 無法本機 build；驗證止於 parser+結構+pytest，真正 build 正確性在 T6 的 CI 首跑確認（已明列觀察點與 rollback）。
