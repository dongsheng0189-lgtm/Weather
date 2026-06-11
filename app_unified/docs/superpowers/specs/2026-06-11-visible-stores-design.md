# 可視店家（super_admin 個人跨店過濾）設計

日期：2026-06-11
分支：`feat/visible-stores`

## 問題 / 目標

super_admin 不綁店、跨店看資料。店家一多，筆記/確認表單/公告/後台清單全部混在一起，資訊過量。
需求：讓 **每個 super_admin 自行決定要看哪幾間店**，設定完之後走到哪都吃這份清單，避免雜訊。

## 核心概念

集中 helper：「這個登入者能看到的店家清單」。

- **super_admin** → 全部店家 **減去** 他自己隱藏的店
- **admin / user** → `[自己的店]`（本來就被角色鎖在自己店，helper 回自己店、邏輯統一）

### 資料模型：存「隱藏集」而非「可視集」

`User` 新增欄位 `hidden_stores`（`db.Text`，存 JSON 店名陣列，mirror 既有 `features` 欄），配 `get_hidden_stores()` / `set_hidden_stores()` helper。

存「隱藏」的理由：
- 預設 `NULL` / `[]` = 沒隱藏任何 = **全部可視** → 符合「預設全部打開」。
- 之後**新增**的店家不在任何人的隱藏清單裡 → **自動可視**，不需每個 super_admin 再去補勾。
- UI 顯示「可視」打勾（打勾 = 可視 = 不在隱藏清單）；取消打勾 = 加進隱藏清單。內部存補集，純實作細節。

### Helper 介面

於 `models.py` 的 `User` 上新增：

```python
def get_hidden_stores(self) -> list:
    """回傳此 user 隱藏的店名 list（解析失敗回 []）。"""

def set_hidden_stores(self, names: list):
    """覆寫隱藏店名（存 JSON）。"""

def visible_store_names(self, all_store_names: list) -> list:
    """回傳此 user 可視的店名（保持傳入順序）。
    super_admin: all_store_names 減去 get_hidden_stores()。
    admin/user: [self.store] if self.store else []。"""
```

`all_store_names` 由呼叫端提供（通常是 `Store.query.order_by(Store.name).all()` 的名字），避免 model 直接查 DB。

## UI：店家管理表新增「可視」欄

位置：`templates/admin/dashboard.html` 的「店家管理」card（現為 JS `loadStores()` 填 `#stores-tbody`）。

- 表頭：`店名 | 登入狀態 | 操作` → **`店名 | 可視 | 登入狀態 | 操作`**。
- 每列一個 checkbox：
  - **super_admin**：可點，`onchange` 即時 `POST /admin/stores/<name>/toggle-visible`，成功才更新 UI。
  - **admin**：`disabled`（唯讀）。
- 預設勾選狀態：
  - super_admin：可視（不在隱藏集）的店打勾；隱藏的不打勾。
  - admin：只有自己店打勾、其他不打勾，全部 disabled。
- **這張表永遠列出全部店家**（控制台；否則隱藏的店再也勾不回來）。`GET /admin/stores` 回傳每店時，附帶 `visible`（對「目前登入的 super_admin」而言）布林。
- **手機不跑板**：「可視」欄只放一個 checkbox，cell 設最小寬度、`white-space:nowrap`，整列維持單行；沿用現有 table 樣式不另外撐寬。

### Toggle endpoint

`POST /admin/stores/<name>/toggle-visible`（新增於 `admin/routes.py`）：
- 僅 super_admin 可呼叫；admin/user → 403。
- 切換 `current_user` 的 `hidden_stores` 是否含 `name`（在 → 移除=可視；不在 → 加入=隱藏）。
- **防呆：不准隱藏最後一間可視店**（隱藏後可視數 < 1 時拒絕，回 400 + 訊息）。
- 回傳 `{status:'ok', visible:<bool>}`。
- 寫一筆 `UserLog`（action 例如 `toggle_visible_store`，detail 記店名 + 新狀態），與既有 admin 操作一致。

## 作用範圍（super_admin 設定完，走到哪都吃）

透過「集中過濾店家清單來源」一次更新多處。實作上把既有的「全部店家」清單來源改成經 `visible_store_names(current_user)` 過濾。

| 畫面 | 來源 / 改法 |
|---|---|
| **筆記列表** `/notes`（含 checklist） | `notes/routes.py _get_stores()` 改回傳「可視店」。`index()` 中 super_admin 無 `?store` 時，從「看全部」改成 `Note.store IN 可視店`。store tabs / 篩選 dropdown 因 iterate `stores` 自動同步。 |
| **筆記 `?store=X` 顯式選店** | 僅當 `X` 在可視店才套用。`X` 不在可視店（含已隱藏 / 不存在）→ **忽略該 store 參數、回退成「可視店全集」view**（不報錯）。store switcher 本來就只列可視店，正常操作不會指到隱藏店。 |
| **公告** | super_admin 的公告查詢從「全部」改成 `store IN 可視店`；**`store IS NULL` 全站公告永遠顯示**。公告 compose 的店家 dropdown 只列可視店。 |
| **AI 摘要** | per-store 彙整只算可視店（隨 notes 查詢範圍收斂即可）。 |
| **admin 後台使用者清單** | super_admin 的 user 查詢加 `User.store IN 可視店 OR User.store IS NULL`（**未綁店者 / 其他 super_admin 永遠顯示**）。使用者店別 filter dropdown、新增使用者店別、裝置店別 filter、核准裝置店別 dropdown 全部只列可視店。 |
| **店家管理表本身** | **例外**：永遠全列全部店家（控制台）。 |
| **mynotes / vetting** | 不動（不分店）。 |

實作策略：
- `notes/routes.py`：`_get_stores()` 加參數或包一層 `_get_visible_stores()`，回傳經 helper 過濾的清單；`index()` 的 super_admin 分支加可視店範圍。
- `admin/routes.py` dashboard：`stores` 改用可視店；user 查詢加可視店範圍；`GET /admin/stores` 仍回全部店家 + 每店 `visible`。
- `notes/announcement_routes.py`：super_admin 公告查詢加可視店範圍（保留 `store IS NULL`）；compose dropdown 用可視店。

## Schema 對齊（[[feedback_inline_schema_repair]] — BLOCKING）

加 `User.hidden_stores` 必須 3 檔對齊，否則 prod 100% ISE：
- `models.py`：`hidden_stores = db.Column(db.Text, nullable=True)` + get/set/visible helper。
- `app.py`：inline `ALTER TABLE users ADD COLUMN IF NOT EXISTS hidden_stores TEXT`（mirror line 273 的 features ALTER；放進同一個 schema repair 區塊）。
- `migrations/versions/`：新增一支 migration（dev 用 alembic）。
- 本機 dev SQLite **不會**自動同步（[[feedback_dev_sqlite_alter]]）：手動 `ALTER TABLE users ADD COLUMN hidden_stores TEXT` 或 `rm instance/dev.db` reseed。

欄位 `nullable=True`、無預設（NULL = 無隱藏 = 全可視），避免既有列 backfill 問題。

## 容量（[[feedback_capacity_check]]）

每 request 多一次：讀 `current_user.hidden_stores`（已在 session/已載入的 user row）+ JSON parse + 一次 `Store.query` 名單（多數頁面本來就查）。可視店過濾是 `IN (...)` 條件，對既有 query 無額外 join。2 CPU / 8GB 無壓力。

## 測試

- **helper**：super_admin 隱藏 B → `visible_store_names` 不含 B、含其餘且保序；admin → 只回自己店；user 無店 → []；新店（不在隱藏集）→ 自動在 visible。
- **notes**：super_admin 隱藏某店 → `/notes` 列表與 store tabs 不出現該店筆記；隱藏店的 `?store=X` 不洩漏；admin 不受影響（只看自己店，與現狀一致）。
- **公告**：隱藏店的公告不出現；`store IS NULL` 全站公告仍在。
- **admin 後台**：使用者清單不列隱藏店的 user；未綁店者 / 其他 super_admin 仍在；店家管理表仍全列。
- **toggle endpoint**：super_admin 可改並反映；admin → 403；不准隱藏最後一間（回 400）。
- **schema**：grep 驗證 models.py + app.py + migration 三處 `hidden_stores` 對齊。

## 不在範圍 / YAGNI

- 不做「全域共用可視設定」（已確認 per-user）。
- 不動 mynotes / vetting。
- 不做可視店的群組 / 範本 / 批次套用（未要求）。
- admin/user 的 `hidden_stores` 永遠忽略（helper 對他們只回自己店），不提供 admin 操作。
