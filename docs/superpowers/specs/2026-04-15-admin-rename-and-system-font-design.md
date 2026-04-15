# Design: Admin 改名級聯 + 全站系統字體

Date: 2026-04-15
Project: `app_unified/`
Repo: github.com/stvlin1988-alt/Weather

## 背景

兩個獨立功能一起做：

1. Admin 需要能在「使用者列表」就地改 username，並把該 user 綁定的所有 TrustedDevice `device_name` 一起更新。
2. 目前 `maximum-scale=1.0` + 模板字體 px 硬寫，使用者調 OS 字體大小沒反應。要讓全站吃 OS 系統字體。

---

## 功能 1：改使用者名稱 + 級聯

### 範圍

- 改：`User.username` + 該 user 名下所有 `TrustedDevice.device_name`
- 不改：歷史快照（`Note.author_name`、`ChecklistItem.checked_by_name`、`NoteLog`、`PrivateNote.*` 加密欄位、`UserLog.target_name` 舊資料）。理由：加密欄位批次改成本高，且歷史紀錄本身是「當時點的證據」。
- 頁面即時顯示（`current_user.username`）自然隨下次請求更新。

### 後端

新路由 `admin/routes.py`：

```
POST /admin/users/<int:user_id>/rename
body: {"new_username": "..."}
```

**權限** — 沿用現有 helper：

- `require_admin()`（admin + super_admin 皆可過）
- 跨店限制比照 `approve_device` / `set_store`：
  - admin：只能改 `target.store == current_user.store` 的 user
  - super_admin：全部可改
- 不可改自己（避免把登入帳號打結）

**驗證**

- `new_username` strip 後非空、長度 ≤ 現有 `User.username` 欄位限制
- 唯一性：`User.query.filter(username==new, id!=user_id).first()` 為 None
- 若 `new == old`：直接回 ok（no-op）

**交易內步驟**

1. `user.username = new`
2. `TrustedDevice.query.filter_by(user_id=user.id).update({"device_name": new})`
3. `_log_user_action("rename", user, detail=f"{old} → {new}")`
4. `db.session.commit()`

**回傳**

- 成功：`{"status": "ok", "new_username": "..."}`
- 失敗：`{"status": "error", "message": "..."}`，HTTP 400/403/409

### 前端（`templates/admin/dashboard.html`）

使用者列表每列 `<td>{{ user.username }}</td>` 改為：

```html
<td>
  <span class="uname" data-uid="{{ user.id }}">{{ user.username }}</span>
  <button class="rename-btn" data-uid="{{ user.id }}" title="改名">✏️</button>
</td>
```

點 ✏️ → 就地換 `<input>` + 確認/取消 button → `fetch('/admin/users/<id>/rename', POST)` → 成功就地更新 `.uname` 文字；失敗顯示錯誤（alert 或 inline）。不整頁 reload，保持列表捲動位置。

按鈕僅在 `current_user` 有權限時顯示（模板內 `{% if ... %}` gate；後端仍會再驗）。

### `UserLog` action label

`admin/routes.py::list_user_logs` 的 `action_labels` 加入 `"rename": "改名"`。

### 邊界

- admin 改 admin：允許
- super_admin 改 super_admin：允許
- 兩個 admin 同時改同一 user：第二個在 unique constraint 失敗 → 回 409
- 該 user 正在線上：下一次 `@login_required` 請求 SQLAlchemy 會拿到新 username

---

## 功能 2：全站吃 OS 系統字體

方案：**解除 viewport 縮放限制 + px 改 rem**（跨 iOS/Android 皆有效）

### 改動 1：viewport（`templates/base.html:5`）

```diff
- <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
+ <meta name="viewport" content="width=device-width, initial-scale=1.0">
```

副作用：使用者也可 pinch zoom 整頁（視為備援）。

### 改動 2：字體 px → rem

基準 `1rem = 16px`。掃過以下模板的 inline `<style>`，把**字體相關**的 px 改 rem：

- `templates/base.html`
- `templates/notes/editor.html`
- `templates/notes/index.html`
- `templates/mynotes/editor.html`
- `templates/mynotes/index.html`
- `templates/weather/index.html`（多數已是 rem，掃過確認）
- `templates/admin/dashboard.html`
- `templates/auth/*.html`（登入相關畫面）

### 不動

- `input, textarea, select { font-size: 16px }` 保留 px — iOS 防 focus 自動放大輸入框的既定技巧
- 非字體的 layout px（寬高、padding、margin、邊框、圖片 overlay、閒置黑幕）
- `line-height` 用無單位值

### 驗證

- Android Chrome：系統字體放大 → 網頁跟著大 ✓
- iOS Safari：Settings > Display & Text Size > Larger Text → 網頁跟著大 ✓
- editor 圖片 fullscreen pinch overlay：不受影響
- 常見頁面（天氣、筆記列表、筆記編輯、私人筆記、admin dashboard、登入）在 OS 字體最大設定下不破版

### 風險

- 使用者調到極端大字體，某些 fixed-width 按鈕/表格可能擠壓 → 實作時在常見頁面目視驗證
- 桌面瀏覽器：OS 字體大小不會反映到瀏覽器（瀏覽器管）— 可接受，非本功能目標

---

## 測試計畫

### 功能 1

- 後端 unit test：
  - admin 改本店 user：ok，device_name 同步更新，UserLog 有記錄
  - admin 改他店 user：403
  - 改為已存在的 username：409
  - 改為空字串：400
  - 改自己：403
  - super_admin 改任何人：ok
- 手動：開 admin dashboard → 點 ✏️ → 改名 → 該 user 的裝置列表看 device_name 也變了

### 功能 2

- 手動在真機 iOS / Android 調 OS 字體大小驗證
- desktop 瀏覽器測未破版

---

## 交付範圍

- `admin/routes.py`：新 `rename_user` 路由 + `action_labels` 加 "rename"
- `templates/admin/dashboard.html`：UI + JS
- `templates/base.html`：viewport
- 多個模板：px → rem
- `tests/test_admin_rename.py`（新檔）
