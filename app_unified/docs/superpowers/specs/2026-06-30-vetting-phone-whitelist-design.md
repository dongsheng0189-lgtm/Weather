# 人員查詢 — 電話白名單 設計

日期：2026-06-30
模組：`vetting/`（人員查詢）
分支：`feat/vetting-phone-whitelist`

## 目標

在人員查詢的「電話查詢（D 段）」加入**白名單**：查詢電話時同時比對一份內部白名單，
若號碼命中就在結果顯示「✓ 白名單」並列出該號碼供 double check。白名單號碼可由
root admin（`username=='admin'`）授權的特定使用者新增；root admin 自己能看全部清單並
修改/刪除。

## 範圍與決策（已與 user 確認）

| 項目 | 決定 |
|---|---|
| 白名單欄位 | **只存電話號碼**（命中顯示「✓ 白名單」） |
| 範圍 | **全店共用一份**（不分店；符合 vetting 本身不分店現況） |
| 命中呈現 | **保留** Whoscall/Google 搜尋連結 **＋** 加「✓ 白名單」提示 **＋列出號碼**供 double check |
| 被授權者能力 | **只能新增**（看不到清單、不能改/刪） |
| Root admin 能力 | 看**全部清單** + **修改** + **刪除** |
| 加密 | 白名單號碼**加密儲存**（`EncryptedText`，AES-256-GCM，同 `DB_ENCRYPTION_KEY`） |
| 稽核 log | **不寫**（避免其他人看到這些資訊） |
| 附帶改善 | 「模組權限管理」使用者清單**依店別排序/分組** |

## 1. 資料模型

新表 `VettingWhitelistPhone`（全店共用、無 `store` 欄）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | Integer PK | |
| `phone` | `EncryptedText` | 正規化後的台灣號碼 `09xxxxxxxx` / 市話，加密存 |
| `created_by` | Integer | 新增者 user id（FK users.id，nullable，刪 user 後保留） |
| `created_by_name` | `EncryptedText` | 新增者帳號快照（加密；user 刪除後仍可顯示誰加的） |
| `created_at` | DateTime(UTC) | `default=datetime.utcnow` |

**三檔對齊（BLOCKING、prod 不跑 alembic 走 app.py inline ALTER）**：
1. `models.py`：新增 `VettingWhitelistPhone` model。
2. `app.py`：inline schema repair 區塊加 `CREATE TABLE IF NOT EXISTS vetting_whitelist_phone (...)`
   （比照現有 inline 建表/ALTER 寫法；PG 與 SQLite 皆可）。
3. `migrations/0XX_*.py`：對應 migration（dev alembic 用）。

> dev SQLite 不會自動同步 → 本機測試前手動建表或 `rm dev.db` reseed（見 feedback_dev_sqlite_alter）。

### 加密與比對策略

`EncryptedText` 為 AES-GCM、每次密文不同 → **不可** `WHERE phone=?` 查。
採**載入全部、逐筆解密、正規化後在記憶體比對**（whitelist 量小，數十～上百筆；
比對只在每次查詢送出時跑一次，NPA 流程本來就慢，解密成本毫秒級）。

- 正規化一律用既有 `vetting/social.py::normalize_phone()`（台灣號碼 canonical 化）。
- **去重**：新增時載入全部解密，正規化後已存在就回「已存在」、不重複寫入。

## 2. 權限（模組權限管理新增一項）

`features.py::AVAILABLE_FEATURES` 新增一個**非模組型**權限：

```python
{
    "key": "vetting_whitelist_add",
    "label": "新增白名單電話",
    "grant_only": True,   # 只在「模組權限管理」勾選清單出現，不是 dashboard 模組
}
```

- `grant_only` 旗標：`user_visible_features()` 過濾掉帶 `grant_only` 的項目
  → **不會**出現在「切換模組」頁 / dashboard 模組磚 / nav（nav 本來就是硬寫死 key，不受影響）。
- `/admin/features/catalog` 仍回完整 `AVAILABLE_FEATURES` → 在「模組權限管理」顯示為一個可勾選項。
- `set_user_features` 的 `valid_keys` 自動含此 key（從 `AVAILABLE_FEATURES` 生成），無需改動。
- **預設：所有人關閉**。新 feature key 不在任何人的 `features[]` 裡 → 預設沒有；
  角色（admin/super_admin）也**不會自動取得**（角色↔權限已完全脫鉤，`427f4dd`）。
- root admin（`is_root_admin()`）`user_has_feature` 永遠回 True → **只有你預設是開的**。
- **只有 root admin 能授權/調整**：`set_user_features` 為 `require_root_admin`，
  別人不被你在「模組權限管理」勾選就完全沒有此權限。
- **假設**：被授權者本來就需要 `vetting`（新增鈕在人員查詢頁上）。授權＝同時勾「人員查詢」+「新增白名單電話」。

## 3. 新增白名單 UI（被授權者）— 人員查詢頁右上角

`templates/vetting/index.html`：

- 右上角（人員查詢標題下方）顯示「➕ 新增白名單電話」鈕，
  **僅當** `has_feature('vetting_whitelist_add')` 為真（Jinja context helper 已有 `has_feature`）。
- 點擊開小視窗（modal，inline 於 index.html）：
  - 一個電話輸入框 + 送出鈕。
  - 前端先用同套規則正規化/基本驗證（非空、像號碼）。
  - `POST /vetting/whitelist/add`，body `{phone}`。
- **被授權者只能新增**：此頁不列出任何既有號碼、無刪改。
- 回饋訊息：`新增成功` / `此號碼已在白名單` / `電話格式錯誤`。

### 後端 endpoint

`vetting/routes.py`：

```
POST /vetting/whitelist/add   （login_required + 需 vetting_whitelist_add 權限）
```

- 權限把關：`user_has_feature(current_user, 'vetting_whitelist_add')` 否則 `abort(404)`。
- `normalize_phone(input)`；非法 → 400 `{error:invalid_phone}`。
- 載入全部解密去重；已存在 → 200 `{status:exists}`。
- 寫入（`created_by`/`created_by_name` 取 current_user）→ 200 `{status:ok}`。
- 加 rate limit（比照 vetting 其他 endpoint，per user）。

## 4. 比對與顯示（D 段電話）

- `vetting/routes.py::start()`：`social.build_url_bundle(...)` 之後，
  若 `bundle["phone"]` 不為 None → 查白名單（載入全部解密、正規化比對）
  → 設 `bundle["phone"]["is_whitelisted"] = True/False`。
  此欄位隨**既有加密 social blob** 一起 serialize/deserialize 跨 worker（`_serialize_state`
  已搬整個 `social`，無需改白名單欄位），**不另存明文**。
- `social.py` 維持**純函數無 DB**：白名單查詢只發生在 `routes.py`（有 DB），
  `build_url_bundle` 不碰 DB。
- 前端 `templates/vetting/index.html` D 段渲染：
  - `is_whitelisted` 為真 → 顯示綠底提示「✓ 此號碼已在白名單中」**並列出號碼**
    （用 `phone_block.normalized`，例 `0912-345-678`）供 double check。
  - Whoscall / Google 搜尋連結**照舊保留**。

## 5. Root admin 管理 UI（後台 dashboard）

`templates/admin/dashboard.html`：在「模組權限管理」附近新增**僅 root admin 可見**區塊：

- 列出**全部**白名單號碼（解密顯示）+ 新增者 + 時間。
- 每列可**刪除**；可**修改**號碼（改後重新正規化/去重/加密）。
- root admin 也可從這裡新增。

後端 `admin/routes.py`（全部 `require_root_admin()`）：

```
GET    /admin/vetting/whitelist           列出全部（解密）
POST   /admin/vetting/whitelist           新增
POST   /admin/vetting/whitelist/<id>      修改號碼
DELETE /admin/vetting/whitelist/<id>      刪除
```

## 6. 模組權限管理 — 依店別排序/分組（附帶改善）

`admin/routes.py::features_user_list()`：
- 排序由 `order_by(User.username)` 改為 `order_by(User.store, User.username)`
  （同店相鄰，NULL/未綁店者一組）。

`templates/admin/dashboard.html::_loadFeatureUserList()`：
- 下拉改用 `<optgroup label="<店別>">` 依店別分組（未綁店/管理員歸「（未綁店）」一組），
  option 文字維持 `username`，讓你照店別一眼找到人。

## 7. 不做 / 邊界

- 不寫任何稽核 log（user 明確要求）。
- 白名單**不分店**、**只存號碼**（無姓名/備註欄）。
- 被授權者**不能**查看或刪改清單。
- 命中只「加註提示」，**不**改變既有搜尋連結行為。

## 8. 測試 / 部署

- **TDD**：endpoint 行為（新增/去重/格式錯/權限 404）、白名單命中 `is_whitelisted` 標記、
  `grant_only` 不出現在 `user_visible_features`、店別排序。
- **wiring 測試**：index.html 有「新增白名單電話」鈕與 modal 元件、dashboard.html 有管理區塊。
- 本機驗證一律 `pytest tests/`（與 CI 一致）；run_local.sh 實測新增→查詢命中流程。
- **SW**：新增鈕/modal inline 於 index.html、管理 UI inline 於 dashboard.html → **沒碰 `/static/`，不 bump SW**。
- 變動面：後端（routes/admin/features/models/app.py）+ DB schema + 2 template + migration；無 static、無 Dockerfile/requirements/CI 影響。

---

## 修訂 v2（2026-06-30，實作後 user 需求變更）

實作完成、最終 whole-branch review 後，user 調整兩項需求 + review 提一個 Important，合併為三個變更：

### C1 — robustness（review Important，必修）
`vetting/routes.py::start()` 的 `whitelist.is_whitelisted(phone)` 未包 try/except（鄰近 judicial/mjib 都有）。
某筆白名單解密失敗或 DB 抖動會 raise → 把已查完（含 captcha）的整個 vetting 查詢 500 掉。
**修**：包 try/except，失敗 `logger.warning` 並設 `is_whitelisted=False`（degrade，不影響查詢主流程）。

### C2 — 命中顯示（user Q2 + 後續調整，最終版）
跨訊息需求綜合（最新覆蓋前面、但只覆蓋有講到的部分）：
- 「不要顯示比對到的電話號碼」（個資不外漏）→ 有效。
- 「Whoscall/Google 連結還是保留、只多一個顯示、查詢頁直接看得到不用點進去」→ 修正前一版「連連結都移除」。

**最終**：命中白名單時，D 段 → **保留 Whoscall/Google 搜尋連結** + **加「✓ 此號碼在白名單內」提示**（直接在查詢結果頁顯示、不用展開），但**移除那行 `p.normalized` 號碼明碼**（畫面不呈現比對到的號碼；號碼只在連結 href、不視覺顯示）。
未命中白名單時，D 段維持原本（號碼明碼 + Whoscall/Google 連結）不變。
（`is_whitelisted` 仍由後端 `start()` 標記、隨加密 social blob 傳回；前端只是命中時：藏號碼明碼、留連結、加提示。）

### C3 — 管理權限改由 feature 把關 + 端點/UI 搬到 vetting（user Q1 改）
原設計：被授權者只能新增；只有 root admin 能看清單 + 改 + 刪（管理 UI 在後台 dashboard）。
**改為**：**只要有 `vetting_whitelist_add` 權限的人**，就能 **新增 + 看清單 + 改 + 刪**（root admin 永遠有；super_admin 一樣要 root admin 個別開，**維持角色↔權限脫鉤**，不自動授予）。
- **端點搬家**：list/update/delete 從 `admin/routes.py`（`/admin/vetting/whitelist*`、`require_root_admin`）**移到 `vetting/routes.py`**（`/vetting/whitelist`、`/vetting/whitelist/<id>`），把關改 `user_has_feature(current_user, 'vetting_whitelist_add')`（+ blueprint 既有 `vetting` before_request gate）。`POST /vetting/whitelist/add` 已在 vetting，維持。移除 admin 的 4 個端點。
  - 理由：有權限的人可能是 `user` 角色，`/admin/dashboard` 要 admin 角色進不去；vetting 頁有 `vetting` feature 就能進。
- **管理 UI 搬家**：人員查詢頁（`templates/vetting/index.html`）的「新增白名單」改成完整**管理面板**（清單 + 新增 + 改 + 刪），`has_feature('vetting_whitelist_add')` 才顯示；移除 `templates/admin/dashboard.html` 的白名單 card。
- **保留**：dashboard 模組權限管理「依店別 optgroup 分組」（與白名單權限無關的獨立改善）。
