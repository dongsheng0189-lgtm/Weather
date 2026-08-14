# Spec: Q 店一鍵清除筆記按鈕

**Date**: 2026-05-13
**Owner**: hirain0127
**Status**: 待 user review
**Lifespan**: **暫時功能**。Q 店業務終止後、所有筆記/附件需要一次性清除。用完即砍（搜 `TEMP: q-store-purge` 找移除位置）。

---

## 1. 目標

在 `/notes/` 頁面、**只有 root_admin 在切到 Q 店 tab 時**看得到一個紅色按鈕「🗑️ 清除 Q 店全部筆記」。按下走「預覽數量 → 打字確認 → 永久刪除 → R2 也清乾淨」流程。

不影響：A/B/C 等其他店、Announcement、PrivateNote(mynotes)、User、TrustedDevice、單筆刪除 API。

---

## 2. UI / UX

### 2.1 按鈕渲染條件
`templates/notes/index.html` 在 store-tabs 區塊後面加：
```jinja
{% if current_user.is_root_admin() and current_store == 'Q' %}
  <button id="q-purge-btn" ...>🗑️ 清除 Q 店全部筆記</button>
{% endif %}
```
**兩個條件都成立才渲染**（不只 hide、是 server-side 不輸出 HTML）。其他帳號或其他店 tab → DOM 內完全沒這個按鈕。

### 2.2 點按鈕後的流程

1. **預覽**：前端 JS GET `/admin/q-store/notes-preview`
   - 後端回 `{"notes_count": 271, "attachments_count": 43}`
   - JS 用數字填 modal 內容
2. **Modal 出現**：
   ```
   ┌─────────────────────────────────────────────┐
   │ ⚠️ 即將永久刪除                              │
   │                                              │
   │ • 271 筆筆記 / 確認表單                      │
   │ • 43 個圖片/影片附件                         │
   │                                              │
   │ 不可復原。                                   │
   │ 要繼續、請在下方輸入店名「Q」：              │
   │ ┌──────────┐                                │
   │ │          │                                │
   │ └──────────┘                                │
   │                                              │
   │              [取消]  [永久刪除]              │
   └─────────────────────────────────────────────┘
   ```
   - 「永久刪除」按鈕**預設 disabled**、紅色背景
   - 「取消」按鈕**永遠可按**、灰色背景、關閉 modal 不動任何事
3. **打字驗證**：input 內每次 keyup 檢查 `value.trim() === 'Q'`、若相等則啟用「永久刪除」按鈕
4. **確認執行**：按「永久刪除」→ JS POST `/admin/q-store/notes-purge` (body empty)
   - 後端回 `{"deleted_notes": 271, "deleted_r2_ok": 43, "deleted_r2_fail": 0, "errors": []}`
   - JS 用 `alert()` 或頁面 flash 顯示「已刪除 271 筆 + 43 個附件」
   - 隨後 `window.location.reload()` 重新載入頁面（Q tab 自然會空）
5. **取消**：modal 關閉、無任何動作

### 2.3 「永久刪除」按鈕被點之後的 button state
- 立即 disabled + 改文字「處理中…」、防 double-click
- POST 完成（success 或 fail）→ modal 自動關
- Network 失敗 → modal 內顯示錯誤訊息、保留 modal 讓 user 看

---

## 3. Backend

### 3.1 新 endpoints（兩個、都在 `admin/routes.py`）

| Endpoint | Method | Body / Query | Response | 註解 |
|---|---|---|---|---|
| `/admin/q-store/notes-preview` | GET | — | `{notes_count, attachments_count}` | TEMP: q-store-purge |
| `/admin/q-store/notes-purge` | POST | empty | `{deleted_notes, deleted_r2_ok, deleted_r2_fail, errors}` | TEMP: q-store-purge |

兩者都加註解 `# TEMP: q-store-purge — Q 店一次性清理，2026-05-13 加。完成後整段 endpoint + 對應 UI 移除`。

### 3.2 Auth gate

兩個 endpoint 都用 `features.require_root_admin()`：
```python
from features import require_root_admin

@admin_bp.route("/q-store/notes-preview", methods=["GET"])
@login_required
def q_store_notes_preview():
    require_root_admin()  # 非 'admin' seed 帳號 → 404
    ...
```

### 3.3 Hardcoded store check（雙重防線）

```python
_PURGE_STORE = "Q"  # hardcoded、不接 url param、不接 body

notes = Note.query.filter_by(store=_PURGE_STORE)
```

即使 endpoint 被偽造 request 打、後端也只會動 Q 店。

### 3.4 Purge 流程（POST handler 內、單一 transaction）

```python
def q_store_notes_purge():
    require_root_admin()
    
    # 1. 撈 R2 keys（必須在 DB delete 前）
    attachments = NoteAttachment.query.join(
        Note, NoteAttachment.note_id == Note.id
    ).filter(Note.store == _PURGE_STORE).all()
    keys = [a.object_key for a in attachments]
    
    # 2. DELETE notes（DB cascade 砍 checklist_items + note_attachments）
    deleted_notes = Note.query.filter_by(store=_PURGE_STORE).delete(
        synchronize_session=False
    )
    
    # 3. UserLog 記錄
    log = UserLog(
        operator_id=current_user.id,
        operator_name=current_user.username,
        target_id=None,
        target_name=f"store:{_PURGE_STORE}",
        action="purge_store_notes",
        detail=f"notes={deleted_notes}, attachments={len(keys)}",
    )
    db.session.add(log)
    
    # 4. Commit DB
    db.session.commit()
    
    # 5. R2 bulk delete（DB 已 commit、不再 rollback）
    ok = 0
    fail = 0
    errors = []
    if keys:
        from storage import _get_client  # 直接呼叫 boto3 bulk delete
        bucket = current_app.config["R2_BUCKET_NAME"]
        client = _get_client()
        # boto3 delete_objects 一次最多 1000 keys、43 個一次搞定
        resp = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in keys]}
        )
        ok = len(resp.get("Deleted", []))
        for err in resp.get("Errors", []):
            fail += 1
            errors.append({"key": err.get("Key"), "msg": err.get("Message")})
    
    return jsonify({
        "deleted_notes": deleted_notes,
        "deleted_r2_ok": ok,
        "deleted_r2_fail": fail,
        "errors": errors,
    })
```

### 3.5 R2 失敗策略

DB 已 commit、不 rollback。R2 失敗只在 response 回報、UserLog 也會記到 `r2_ok/r2_fail`。

如果 R2 全失敗（例如 boto3 沒設定、endpoint 不通）→ deleted_r2_ok=0、deleted_r2_fail=43。**DB 已經乾淨、R2 變孤兒檔**。Q 店 R2 孤兒檔可以後續手動 Cloudflare dashboard 清、不影響主功能。

---

## 4. Data model（不動）

不新增任何 table、不改 schema。

`UserLog.action` 新增一個值 `'purge_store_notes'`、本來就是 free-text TEXT 欄位、不需要 migration。

---

## 5. Audit log

每次成功 purge 寫 1 條 UserLog：

| 欄位 | 值 |
|---|---|
| `operator_id` | `current_user.id`（即 root_admin） |
| `operator_name` | `'admin'` |
| `target_id` | NULL（不指特定 user） |
| `target_name` | `'store:Q'` |
| `action` | `'purge_store_notes'` |
| `detail` | `'notes=271, attachments=43'` |
| `created_at` | auto |

既有 `/admin/user-logs` 頁面就能查得到（不需另開查詢介面）。

---

## 6. Out of scope

- ❌ 通用化變成「任意店清除」按鈕（明確只支援 Q 店 hardcoded）
- ❌ 軟刪除 / trash bin（直接 hard delete）
- ❌ 部分匯出備份（不做、user 已知要砍）
- ❌ 刪 Announcement / PrivateNote / User / TrustedDevice / Store row 本身
- ❌ async/background job（synchronous 處理、預期 1-3 秒完成）
- ❌ 通用 R2 bucket cleanup（只清這次刪掉的 attachments、不掃 orphan）
- ❌ 「移除按鈕」自動化（之後手動找 `TEMP: q-store-purge` 全砍）

---

## 7. 既有功能保護

**強制不變**：
- `/notes/api/<id>` DELETE（單筆刪）整段 untouched
- `/notes/` GET 渲染邏輯（只加 `{% if root_admin and store=='Q' %}` 一個新 block）
- 其他店切 tab 的行為（B/C/D/E/F/G/J/JJ/K/S）完全不影響
- `Announcement` / `PrivateNote` / `User` / `TrustedDevice` 一行不動
- `storage.delete_attachment()` 一行不動
- 既有 UserLog 寫入路徑不變、只是新增一個 action 值

---

## 8. Tests

### 8.1 Auth gate test（`tests/test_admin_q_store_purge.py`、新檔）
- `test_preview_non_root_admin_404`：非 root_admin → GET 回 404
- `test_purge_non_root_admin_404`：非 root_admin → POST 回 404

### 8.2 Preview test
- `test_preview_returns_counts`：seed 3 個 Q 店 notes + 2 個附件 → GET 回 `{notes_count:3, attachments_count:2}`
- `test_preview_with_no_q_data`：沒 Q 店資料 → 回 `{notes_count:0, attachments_count:0}`
- `test_preview_other_stores_not_counted`：seed Q 跟 B 店各幾筆 → 只 count Q

### 8.3 Purge integration test
- `test_purge_deletes_q_notes_only`：seed Q + B 店各 3 筆 notes → POST → Q 店 0 筆、B 店仍 3 筆
- `test_purge_cascades_attachments`：seed 1 個 Q note + 1 個 attachment → POST → DB attachment 也沒了
- `test_purge_writes_user_log`：POST → UserLog 多 1 行 action='purge_store_notes', target_name='store:Q'
- `test_purge_returns_correct_counts`：seed 5 個 notes + 3 個 attachments → response = `{deleted_notes:5, deleted_r2_ok:0(在 test 環境 R2 mock 為 noop), deleted_r2_fail:0, errors:[]}`
- `test_purge_hardcoded_to_q_even_if_query_tampered`：query string 帶 `?store=B` → 後端仍只刪 Q（無視 query）

### 8.4 R2 mock
測試時用 `monkeypatch` 把 `_get_client` 換成 mock 版本、`delete_objects` 回固定 `{"Deleted": [...], "Errors": []}`、避免真的打 Cloudflare。

### 8.5 Frontend smoke
- `test_purge_button_visible_for_root_admin_on_q_tab`：root_admin GET `/notes/?store=Q` → HTML 含 `id="q-purge-btn"`
- `test_purge_button_hidden_for_root_admin_on_other_store`：root_admin GET `/notes/?store=B` → HTML 不含
- `test_purge_button_hidden_for_super_admin_on_q_tab`：super_admin（非 seed admin）GET `/notes/?store=Q` → HTML 不含

---

## 9. 部署 / Branch

### 9.1 Branch：`chore/q-store-purge-button`

開 off **main**（不依賴 vetting-social-search）。

### 9.2 與其他 branch 的關係

| Branch | Push? |
|---|---|
| `chore/default-features-add-vetting` | ✓ 一起 push |
| `chore/q-store-purge-button`（本 spec） | ✓ 一起 push |
| `feat/vetting-social-search` | ✗ 不動、不 push、留著 user 後續再做 |

兩條 chore merge 進 main、一次 push、Zeabur 1 次 redeploy 同時上線：
1. 新 user 預設有 vetting feature
2. root_admin 在 `/notes/?store=Q` 看得到清除按鈕

### 9.3 環境變數
**無新增**。R2 credentials 既有 env var 已涵蓋（`R2_ENDPOINT_URL` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET_NAME`）。

### 9.4 部署後驗證
1. Prod 進 `https://<prod 網域>/notes/?store=Q`（用 root_admin 帳號）
2. 確認頂部出現紅色「🗑️ 清除 Q 店全部筆記」按鈕
3. 按下 → 預覽彈窗顯示「271 筆 + 43 附件」(或當下實際數字)
4. 輸入 `Q` → 按「永久刪除」
5. 等 1-3 秒 → 頁面 reload、Q tab 空 + flash 訊息
6. 進 `/admin/user-logs` 查到一條 `action=purge_store_notes` 紀錄
7. Cloudflare R2 dashboard 確認 Q 店 attachments 真的不見

---

## 10. 後續移除（將來）

Q 店清完之後要把這個功能完整移除、不要留死代碼：

1. 全 repo 搜 `TEMP: q-store-purge`、把該段 endpoint + 對應 import 砍掉
2. 在 `templates/notes/index.html` 砍掉 `{% if root_admin and store=='Q' %}` 那 block
3. 砍掉 `tests/test_admin_q_store_purge.py` 整檔
4. 開 `chore/remove-q-store-purge-button` branch、merge main、push

UserLog 既有的 `action='purge_store_notes'` 紀錄保留（audit）。

---

## 11. 開放問題

無。
