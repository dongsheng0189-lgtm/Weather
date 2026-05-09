# AI Task DB-Backed State + Concurrency Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. TDD throughout. Steps use checkbox (`- [ ]`).

**Goal:** 把 `notes/` 跟 `mynotes/` AI 摘要 task state 從 module-level dict 改成 DB-backed，修掉 5/9 改 `workers=2` 後出現的 cross-worker「任務不存在」bug；同時加並發保護防 3-5 人同時按炸服務。

**Branch:** `worktree-fix-ai-task-db-state` off main `2e9cc13`

**Root Cause（已驗證）:**
- `_ai_tasks = {}` 是 module-level dict、每個 gunicorn worker process 各自獨立
- 5/9 (`b5e6268`) 把 workers 1→2 → POST 落 worker A 寫進 A 的 dict、frontend GET polling 路由到 worker B → `_ai_tasks.get(task_id)` 回 None → 404「任務不存在」
- 5/10 prod log 確認：後端 `[ai_local] === done, output 3940 chars ===`、前端仍顯示「任務不存在」

**Scope:** 兩個 blueprint 一起修（同 PR），新共用 `AiTask` table，獨立 semaphore。

**Out of scope:** Pre-load model at boot、HF_HOME 修法、auth test fixture tech debt、background queue 改造。

---

## Architecture

### New: `AiTask` model (shared across notes + mynotes blueprints)

| Column | Type | Note |
|---|---|---|
| `id` | str(32) PK | task_id（保留 uuid hex 12 chars 慣例，欄位大小留 buffer） |
| `kind` | str(20) | `notes` / `mynotes` 區分 blueprint |
| `user_id` | int FK→users.id | indexed，方便 per-user query |
| `status` | str(16) | `processing` / `done` / `error` |
| `result_json` | Text nullable | JSON-serialized result（done 時填） |
| `error_msg` | Text nullable | error 時填 |
| `created_at` | DateTime | naive UTC（同其他表慣例） |
| `updated_at` | DateTime | onupdate=utcnow，stuck cleanup 用 |
| `expires_at` | DateTime indexed | TTL：done/error 設 now+30s（配合 grace window 語意） |

Composite index `(status, updated_at)` 給 stuck cleanup query 用。

### Per-worker concurrency cap

- `notes/routes.py`：module-level `_ai_summary_sem = gevent.lock.BoundedSemaphore(2)`
- `mynotes/routes.py`：module-level `_mynotes_ai_sem = gevent.lock.BoundedSemaphore(2)`（獨立，使用情境不同）
- 進入 spawn 之前 `if not sem.acquire(blocking=False): return 429`
- spawn target body 用 `try/finally` 確保 release

### Hard timeout in task body

- `_run_local_summary` 內：`gevent.with_timeout(45, summarize_local, ...)` 包 actual call
- 超時 → `gevent.Timeout` exception → 寫 error「摘要逾時，請稍後再試」
- 45s < gunicorn timeout 60s → 不會觸發 SIGKILL worker

### Stuck task cleanup

- `get_ai_task` 進來時 sweep：
  - `UPDATE ai_tasks SET status='error', error_msg='任務逾時' WHERE status='processing' AND updated_at < now()-60s`
  - `DELETE FROM ai_tasks WHERE expires_at < now()`
- 防 worker SIGKILL / pod restart 留下殭屍 task

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `models.py` | Modify | 加 `class AiTask(db.Model)` |
| `migrations/versions/011_ai_tasks.py` | Create | upgrade/downgrade、composite index |
| `notes/routes.py` | Modify | 拔 `_ai_tasks` dict、加 sem + helpers + DB writes、`get_ai_task` DB query |
| `mynotes/routes.py` | Modify | 同 notes 模式（獨立 sem） |
| `tests/test_ai_task_db_state.py` | Create | cross-worker / stuck cleanup / TTL |
| `tests/test_ai_task_concurrency.py` | Create | sem 限 2 / hard timeout / sem release |
| `tests/test_ai_task_race.py` | Modify | 既有 grace 語意改 DB `expires_at`，保留測試意圖 |
| `tests/test_ai_summary_threadpool.py` | Modify | mock target 從 `_ai_tasks` dict 換成 DB query |
| `.github/workflows/docker.yml` | Modify | 加 `worktree-fix-ai-task-db-state` 進 trigger branches |

---

## Steps（TDD throughout）

### 1. Add `AiTask` model + migration
- [ ] `models.py`：新增 `AiTask` class（schema 如上）
- [ ] `migrations/versions/011_ai_tasks.py`：`op.create_table(...)` + `op.create_index(...)`；下行 `op.drop_index` + `op.drop_table`
- [ ] `venv/bin/alembic -c migrations/alembic.ini upgrade head` local 跑過、`downgrade -1` 也乾淨
- [ ] commit `feat(models): add AiTask table for cross-worker task state`

### 2. Cross-worker DB-state tests (failing)
- [ ] `tests/test_ai_task_db_state.py`：
  - `test_task_persisted_across_sessions`：write 後 `db.session.expire_all()`、新 query 拿得到
  - `test_stuck_task_cleanup_on_get`：seed 70s 前的 processing → call `/notes/ai/task/<id>` → 變 error「任務逾時」
  - `test_expired_task_swept_on_get`：seed expires_at 過期 → call → 404 + DB row 已刪
- [ ] 測試應該全紅（功能還沒做）

### 3. Refactor `notes/routes.py`
- [ ] 刪 `_ai_tasks = {}` (line 153)
- [ ] 加 `_ai_summary_sem = gevent.lock.BoundedSemaphore(2)` 跟 helpers `_create_ai_task`、`_set_ai_task_done`、`_set_ai_task_error`、`_sweep_ai_tasks`（內部用 `db.session`）
- [ ] 改 3 處 spawn site：semaphore acquire 檢查 → 失敗回 429；`_create_ai_task(kind='notes', user_id=current_user.id)`
- [ ] 改 `_run_ai_task` / `_run_split_summary` / `_run_local_summary`：try/finally release sem；DB write；`_run_local_summary` 內 `gevent.with_timeout(45, ...)`
- [ ] 改 `get_ai_task`：sweep + DB query + 既有 404 行為
- [ ] cross-worker tests 變綠
- [ ] commit `fix(notes/ai-task): DB-backed state + sem(2) + 45s hard timeout`

### 4. Concurrency tests
- [ ] `tests/test_ai_task_concurrency.py`：
  - `test_third_concurrent_request_returns_429`：mock summarize_local sleep；前 2 拿 task_id、第 3 收 429
  - `test_hard_timeout_records_error`：mock summarize_local sleep 50s → task 結束 status=error
  - `test_semaphore_released_on_done`：第一個 done 後第 3 個能進
- [ ] 全綠

### 5. Refactor `mynotes/routes.py`
- [ ] 同 notes 模式（獨立 `_mynotes_ai_sem`）
- [ ] commit `fix(mynotes/ai-task): DB-backed state + sem(2) + 45s hard timeout`

### 6. Update existing regression tests
- [ ] `test_ai_task_race.py`：`_seed_task` 改寫 DB row、`expires_at` 控時間取代 `monkeypatch time.time`、保留 5 個 case 語意
- [ ] `test_ai_summary_threadpool.py`：assert 從 `_ai_tasks.get(...)` 改成 `AiTask.query.get(task_id)`，dict-equality 改 attribute 比對
- [ ] 全綠
- [ ] commit `test(ai-task): port grace/threadpool tests to DB schema`

### 7. CI workflow trigger
- [ ] `.github/workflows/docker.yml`：trigger branches 加 `worktree-fix-ai-task-db-state`
- [ ] YAML 用 `python -c "import yaml; yaml.safe_load(open('...'))"` 驗（feedback_validate_configs）
- [ ] commit `ci: trigger docker build for worktree-fix-ai-task-db-state branch`

### 8. Final verify
- [ ] `pytest tests/test_ai_task_db_state.py tests/test_ai_task_concurrency.py tests/test_ai_task_race.py tests/test_ai_summary_threadpool.py -v` 全綠
- [ ] `pytest tests/test_notes.py tests/test_notes_lock.py tests/test_notes_misc.py -q` regression 不破
- [ ] 向使用者報告：`merge --ff-only` 進 main 後等使用者 push（feedback_push_policy）

---

## Deploy & Verify (使用者執行)

1. `git push origin main`
2. GHA build 綠 → Zeabur 手動「重新部署」
3. Prod shell 驗：
   - `grep "^class AiTask" /app/models.py` → 1 筆
   - `cat /app/migrations/versions/011_*.py | head -1`
   - `psql ... -c '\dt ai_tasks'` 看 table
4. 實機測：
   - 單人連按 3 次 AI 摘要（今日／昨日／前 7 天）→ 全部顯示摘要、不出現「任務不存在」
   - 3 人 device 同時按 → 第 3 個收 429 友善訊息或排隊；都 OK 後不出現 worker SIGKILL
   - mynotes 也測一次

---

## Rollback

如 prod 出問題：
1. `git revert <merge-commit>` push → 重新部署 → 前端回 module-level dict（仍會有 cross-worker bug 但不會炸）
2. 或保留 fix code、跑 `alembic downgrade -1` 砍 ai_tasks table（但這樣 DB writes 會炸）→ 不建議
