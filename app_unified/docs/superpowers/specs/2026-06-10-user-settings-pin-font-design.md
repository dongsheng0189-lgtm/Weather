# 使用者自助設定頁（改 PIN + 字體）Design

> Date: 2026-06-10 · Module: `auth` · Status: approved (待 user review spec)

## Goal

讓使用者自行修改登入 PIN（解決多人共用同一組 PIN 的「密碼重複」問題），並在右上角新增一個「⚙️ 設定」入口，集中放「修改 PIN」與「字體大小」兩項自助設定。現有獨立的「調整字體」頁併入設定頁後移除。

## Context（現況）

- **登入是 PIN + 人臉雙重**：輸入 PIN → 撈出所有 `check_password(pin)` 相符的帳號 → 再用人臉辨識本人（`auth/routes.py:verify`）。**PIN 本來就允許重複**，靠人臉區分；故改 PIN **不需要唯一性檢查**。多人共用同組 PIN 正是要解的痛點。
- **「密碼」= 4 位數 PIN**，`User.set_password(pin)` / `check_password(pin)`，存 `password_hash`（sha256）。本案**不改 hash 機制、不改 PIN 長度**。
- 登入/verify 的 PIN 輸入框是 `maxlength="4" inputmode="numeric"`。
- **字體調整已存在**：獨立頁 `GET /auth/font-size` → `templates/auth/font_size.html`，3 選項（小16 / 中19 / 大22 px），存 `localStorage('font_size_pref')`。
- **全域套用字體**的邏輯在 `templates/base.html` L10–11（每頁 load 時讀 localStorage 套到 `<html>`）→ **本案不動**。
- 右上角導覽在 `base.html`，兩套 nav：桌機 `.nav-links`（L91 有「調整字體」）、手機 `.nav-dropdown`（L110 同）。
- `UserLog` model（`models.py`）：`operator_id/name`、`target_id/name`、`action`、`detail`、`created_at`。admin 端有 `_log_user_action` helper（在 `admin/routes.py`）。
- `limiter` 已在 `auth/routes.py` 使用（`verify` 套 `20 per minute`）。

## 範圍決策（已與 user 拍板）

| 項目 | 決定 |
|---|---|
| 設定介面形式 | 獨立頁 `/auth/settings`（跟「調整字體」「人臉設定」一致的 page 模式） |
| 改 PIN 驗證 | 要驗「目前 PIN」+ 新 PIN ×2 |
| PIN 長度 | 維持 4 位數字 |
| 弱 PIN 檢查 | 擋「與目前相同」+ 明顯弱 PIN（四碼全同 / 連號升 / 連號降） |
| 舊 `/auth/font-size` | **直接刪除**路由 + `font_size.html`（不留 redirect） |

## 設計

### A. 路由（`auth` blueprint，prefix `/auth`）

| Method | 路徑 | 說明 |
|---|---|---|
| `GET` | `/auth/settings` | `@login_required`，render `auth/settings.html` |
| `POST` | `/auth/change-pin` | `@login_required` + `@limiter.limit("10 per minute", exempt_when=TESTING)`，收 JSON、回 JSON |
| ~~`GET /auth/font-size`~~ | — | **刪除**（連同 `templates/auth/font_size.html`） |

### B. 後端 `change_pin` 邏輯（`auth/routes.py`）

收 JSON `{current_pin, new_pin, confirm_pin}`，依序驗證，任一失敗回 `{"status":"error","message":<中文>}` + HTTP 400：

1. `not current_user.check_password(current_pin)` → 「目前 PIN 不正確」
2. `new_pin` 不符 `^\d{4}$` → 「新 PIN 需為 4 位數字」
3. `new_pin != confirm_pin` → 「兩次新 PIN 不一致」
4. `new_pin == current_pin` → 「新 PIN 不能與目前相同」
5. `_is_weak_pin(new_pin)` → 「PIN 過於簡單，請換一組」
6. 通過 → `current_user.set_password(new_pin)`；寫 `UserLog(operator_id=target_id=current_user.id, operator_name=target_name=current_user.username, action='change_pin', detail=None)`；`db.session.commit()` → `{"status":"ok"}`

**`_is_weak_pin(pin)` 定義**（4 碼）：
- 四碼全同：`0000`,`1111`,…,`9999`
- 連號遞增：`0123`,`1234`,…,`6789`（每位 +1）
- 連號遞減：`9876`,`8765`,…,`3210`（每位 −1）

**安全**：
- rate limit 10/min 防暴力猜「目前 PIN」。
- **絕不記錄 PIN 值**（log 與 response 都不含）。
- 後端為唯一真相：前端 maxlength/inputmode 僅輔助，所有驗證後端再做一次。

### C. 前端 `templates/auth/settings.html`（extends `base.html`）

CSS/JS 全部 inline 在 template（**不新增 static 檔 → 不用 bump SW**）。兩區塊：

- **🔑 修改 PIN**：3 個 `type=password inputmode=numeric maxlength=4` 輸入框（目前 / 新 / 再輸入）+「儲存」鈕。JS `fetch('/auth/change-pin', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON})`，inline 顯示成功 ✓ / 錯誤 message；成功後清空三欄。
- **🔤 字體大小**：搬 `font_size.html` 的 3 選項 picker（`data-size=small|medium|large`，`sizes={small:'16px',medium:'19px',large:'22px'}`），點選 → 更新 `localStorage('font_size_pref')` + 即時套 `document.documentElement.style.fontSize` + 切 active 樣式。**全域套用已在 base.html，不動**。

### D. 導覽列（`base.html`）

桌機 `.nav-links`（L91）+ 手機 `.nav-dropdown`（L110）兩處的 `<a ...auth.font_size...>調整字體</a>` → 換成 `<a href="{{ url_for('auth.settings') }}">⚙️ 設定</a>`。

### E. 邊界 / 權限
- 未登入打 `/auth/change-pin` → Flask-Login 擋（401 JSON / redirect）。
- user / admin / super_admin 都能用，純自助、與 store 無關（super_admin store=None 不影響）。

## 測試（pytest `tests/test_auth_change_pin.py`）

| 案例 | 預期 |
|---|---|
| 正確 current + 合法 new（含 confirm 一致） | `status:ok`，`check_password(new)` True |
| current 錯 | error「目前 PIN 不正確」 |
| new 與 confirm 不一致 | error「兩次新 PIN 不一致」 |
| new == current | error「新 PIN 不能與目前相同」 |
| 弱 PIN（`0000`、`1234`、`9876`） | error「PIN 過於簡單」 |
| 非 4 位（`123`、`12a4`） | error「需為 4 位數字」 |
| 未登入 | 401 / redirect |

字體區純前端 localStorage → 手動測（切大小、跨頁持久、reload 保持）。

## 不碰（YAGNI）
- 不改 sha256 hash、不改 PIN 長度、不動登入/verify UI、不加 face 重登錄、設定頁只放 PIN + 字體兩項、PIN 不做唯一性檢查（設計上允許重複）。

## Schema / 部署影響
- **無新增 DB 欄位**（沿用 `password_hash` + `UserLog`）→ 不涉及 inline schema repair。
- **不新增 static js/css**（設定頁樣式與腳本 inline）→ **不用 bump SW**。
- 變更為後端路由 + 1 個新 template + base.html 2 行 + 刪 1 路由/1 template + 1 測試檔。
