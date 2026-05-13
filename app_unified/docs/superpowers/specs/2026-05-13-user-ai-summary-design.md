# Spec: `user` role 可使用 AI 摘要

**Date**: 2026-05-13
**Owner**: hirain0127
**Status**: 待 user review

---

## 1. 目標

`/notes/` 頁面的 **「🤖 AI 摘要 → 產生摘要」**按鈕（POST `/notes/api/<id>/summarize`）目前 backend 擋 `is_admin()` → `user` role 按下會收 403。**改成登入即可用**、`user` / `admin` / `super_admin` / `root_admin` 都能跑。

「全店共享一份摘要、最新蓋舊」的既有行為**不變**——只是擴大誰能觸發。

---

## 2. Scope

### 2.1 動的東西（only 1 line）
`app_unified/notes/routes.py:1175-1176` 整段 `if not current_user.is_admin(): return 403` 刪掉。保留 `@login_required`（line 1173）做基本登入檢查。

### 2.2 不動的東西
- `/notes/api/<id>/outline`（單筆大綱、`notes/routes.py:1199`）— **保留** `is_admin()` 限制
  - 理由：UI 沒按鈕、改了也沒影響、保留 audit-clean
- `_ai_summary_sem = BoundedSemaphore(2)`（同時最多 2 個 summary 任務跑）— 不動
  - 這是既有的成本/負載保護、user 也照樣受限
- AI 摘要結果寫入邏輯（`Note.is_ai_summary=True` row 蓋舊）— 不動
- `MANAGER_PROMPT` / LLM 呼叫邏輯 — 不動

---

## 3. Backend 變動

`app_unified/notes/routes.py:1172-1177`：

**Before**：
```python
@notes_bp.route("/api/<int:note_id>/summarize", methods=["POST"])
@login_required
def summarize(note_id):
    if not current_user.is_admin():
        return jsonify({"status": "error", "message": "僅限管理員"}), 403
    note = Note.query.get_or_404(note_id)
```

**After**：
```python
@notes_bp.route("/api/<int:note_id>/summarize", methods=["POST"])
@login_required
def summarize(note_id):
    note = Note.query.get_or_404(note_id)
```

---

## 4. Frontend 變動

**無**。UI 上的「產生摘要」按鈕本來就對所有人可見、只是 backend 之前擋 user → 顯示「僅限管理員」錯誤訊息。後端拿掉 gate 後、按鈕對 user 正常作用、不用改 template。

---

## 5. Tests

`app_unified/tests/test_notes.py` 加 1 條 test：

```python
def test_summarize_allowed_for_user_role(app, client, db_session, monkeypatch):
    """user role 也可以呼叫 /summarize、不再回 403（spec 2026-05-13）。"""
    # seed user role + 1 個 note
    # mock _ai_summary_sem.acquire → True
    # mock gevent.spawn (避免真的跑 LLM)
    # POST /notes/api/<id>/summarize as user role
    # assert response.status_code in (200, 202)  # accepted task
    # assert response.json()["status"] in ("accepted", "ok")
```

既有 admin 用 /summarize 的 test 不動、regression 自然涵蓋。

---

## 6. Audit / Cost / Rate-limit

**無新增措施**。理由：
- 既有 `_ai_summary_sem = BoundedSemaphore(2)` 已限制同時最多 2 個 AI 任務跑
- 既有 task system `_create_ai_task` 已寫 audit（task 對應 user_id）
- 不額外加 per-user daily quota（暫時不考慮、若濫用再加）

---

## 7. Out of scope

- ❌ `/outline` endpoint 解鎖（沒 UI 入口、不做）
- ❌ per-user AI summary daily quota
- ❌ user 個人摘要（每人自己的版本、不蓋全店）— 暫不支援、若需求再開新 spec
- ❌ AI 摘要按鈕的 UI 變動（按鈕本來就所有人看得到）

---

## 8. 既有功能保護

- `/summarize` request/response shape **byte-identical**（只是不再回 403、改回 202 accepted）
- `/outline` route 一行不動
- AI task system / semaphore / prompt building 一行不動
- 既有 admin 用法不受影響

---

## 9. 部署

### 9.1 Branch：`chore/user-ai-summary`（off main）

### 9.2 與其他 branch 的關係

| Branch | Push? |
|---|---|
| `chore/default-features-add-vetting` | ✓ 一起 push |
| `chore/q-store-purge-button` | ✓ 一起 push |
| **`chore/user-ai-summary`（本 spec）** | ✓ **一起 push** |
| `feat/vetting-social-search` | ✗ 不動、留著 |

三條 chore merge 進 main、一次 push、Zeabur 1 次 redeploy 同時上線。

### 9.3 環境變數
**無新增**。

---

## 10. 開放問題

無。
