# Notes 標籤系統設計

**日期**: 2026-05-26
**作者**: hirain（with Claude）
**Branch**: `feat/notes-tag-system`
**Status**: Draft for review
**前置依賴**: `feat/notes-collapsible-sections` (已合 main `2e63a86`)、reuse `.collapsible` CSS/JS pattern

## 起點與動機

`/notes/` 內容愈來愈雜（員工/客人/機台/雜項 × 多店 × 多時間），既有 5 排 filter（種類/班別/時間/狀態/排列）只能切 axis、無法跨 axis 做「主題式」分群。User 要加標籤系統做 cross-cutting organization。

5/15 mockup 探過、確定 per-store 共用、item-level 為主、釘選機制。5/26 再對齊一次、把 layout 也順手收進新做的 `.collapsible` pattern。

## 目標

- Notes + ChecklistItem 都可掛標籤（多對多、上限 5 個 / item）
- 標籤 per-store 共用、同店所有人都可 新增/編輯/刪除/釘選
- 清單頁加標籤 filter（單選、URL `?tag=A`）
- 清單頁所有 filter（含搜尋、標籤、5 排既有）收進**一個可摺疊面板**、減版面、記住 user 偏好
- 標籤管理 UI inline 在 `/notes/`（不另開頁）、放 AI 摘要 collapsible section 下面
- 編輯 note / checklist item 時、tag picker modal 內也可 inline 新增標籤（不必跳離當前頁）
- 視覺：10 色 palette、無 emoji、bg/fg 自動配對符合 WCAG AA contrast

## 非目標（這次不做）

- mynotes (private notes) 標籤 — 完全另一組 schema、未來另議
- AND 多選 filter — 已定單選
- 自訂 hex 顏色 — palette 10 色夠用
- 標籤匯入/匯出、使用統計 heatmap、滑動動畫 — YAGNI
- emoji 完全不做
- 獨立 `/notes/tags` 頁 — 完全 inline 在主頁 collapsible section

## 設計

### A. 資料模型

新增 3 張 table、不動既有 schema。

```python
# models.py
class Tag(db.Model):
    __tablename__ = 'tags'
    id = db.Column(db.Integer, primary_key=True)
    store = db.Column(db.String(8), nullable=False, index=True)
    name = db.Column(db.String(20), nullable=False)
    color = db.Column(db.String(7), nullable=False)  # hex #RRGGBB
    pinned = db.Column(db.Boolean, default=False, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint('store', 'name', name='uq_tags_store_name'),)

class NoteTag(db.Model):
    __tablename__ = 'note_tags'
    note_id = db.Column(db.Integer, db.ForeignKey('notes.id', ondelete='CASCADE'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True)

class ChecklistItemTag(db.Model):
    __tablename__ = 'checklist_item_tags'
    item_id = db.Column(db.Integer, db.ForeignKey('checklist_items.id', ondelete='CASCADE'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True)
```

**約束**:
- per-store unique name (`UNIQUE(store, name)`)
- tag 刪除 → cascade 清 `note_tags` + `checklist_item_tags` 上的綁定（item / note 本身不動）
- note / item 刪除 → cascade 清各自 m2m 綁定
- created_by 用 `ON DELETE SET NULL`（user 帳號被刪標籤仍在）
- 釘選 per-store 上限 3 個（軟限、route-level enforce、DB schema 不限）
- 每個 item 上限 5 個 tag（route-level enforce）

**Schema 建立**:
1. `models.py` — 加 3 個 class
2. `migrations/versions/2026-05-26-tag-system.py` — alembic migration (本機 / 未來新環境用)
3. **不需要** `app.py` inline ALTER：`feedback_inline_schema_repair` 是針對「加 column 到既有 table」(SQLAlchemy `db.create_all()` 不會 ALTER 既有 table)。此次新增 3 張全新 table、`db.create_all()` 啟動時會自動 CREATE TABLE (PG + SQLite 都支援、idempotent)。Deploy 順序：push → GHA build → Zeabur Redeploy → app.py 啟動跑 `db.create_all()` → 自動建 3 張 table、user 不必手動下 SQL

### B. 清單頁 layout 改造：filter panel collapsible

複用既有 `.collapsible` pattern (5/26 公告/AI 摘要做的)、用 `data-section="filters"` 區分。標籤管理 section 用 `data-section="tag-management"`。

把既有 5 排 filter（種類/班別/時間/狀態/排列）+ 搜尋 form + 新標籤 row、**全部收進一個 `.collapsible` 面板**。

```
┌──────────────────────────────────────────────┐
│ ⚙️ 進階篩選 (3)                      ▶      │
├──────────────────────────────────────────────┤
│ 🔍 [______________] [搜尋] [✕取消]            │
│ 🏷️ [日常][重要][待議] [+5 more]              │
│ 種類: [全部][員工][客人][機台][雜項]           │
│ 班別: [全部][A][B][N]                         │
│ 時間: [今天][近3天][近7天][所有]               │
│ 狀態: [全部][待處理][待討論][已解決]           │
│ 排列: [照更新][照日期]                         │
└──────────────────────────────────────────────┘
```

**Active count badge `(3)`** = 目前套了幾個 non-default filter：
- 標籤有值 → +1
- 種類非「全部」→ +1
- 班別非「全部」→ +1
- 時間非「今天」(預設) → +1
- 狀態非「全部」→ +1
- 排列非「照更新」(預設) → +1
- 搜尋有值 → +1
- 0 時 badge 不顯示、只剩 `⚙️ 進階篩選`

**展開/摺疊邏輯**:
- localStorage key `notes_filter_panel_open` = `'1'` | `'0'`
- 沒設過 → 預設摺疊
- User 點 bar → toggle + 寫 localStorage
- **URL 帶 active filter 時強制展開**（不論 localStorage）。Active filter 定義：
  - `?tag=<id>` / `?category=<x>` / `?shift=<x>` / `?range=<非 today>` / `?status=<x>` / `?sort=<非 updated>` / `?q=<x>` 任一個有值
  - 排除 `?store=` (super_admin 切店、固定行為、不算「篩選結果」)
- 強制展開時、bar 也照 click 可 toggle、但下次 reload 仍會被 URL filter 強制展開（不蓋掉 user 偏好）

**標籤 row 顯示策略**:
- 釘選的標籤（最多 3 個）常駐顯示在 chip bar
- 未釘選的標籤 → 收在「+N more」按鈕後
- 點 `+N more` → in-place 展開所有未釘選 chip、再點收回
- 釘選滿 3 後、管理 section 的「釘選」按鈕 disabled、tooltip「已釘滿、先取消其他釘選」

**Tag chip 視覺規範**:
- 圓角 `border-radius: 16px`、padding `.2rem .65rem`、font `.78rem` weight 600
- Background = tag.color、color = `_pick_fg(tag.color)`（亮色用 `#000`、暗色用 `#fff`）
- 未選 chip：background opacity 0.85、selected：fully opaque + box-shadow 微強
- Min tap target 36px（手機友善）

### C. 標籤管理 section（在 `/notes/` 主頁、AI 摘要下方）

第三個 `.collapsible` section、預設摺疊（跟 AI 摘要同等級、低頻使用）：

```
┌──────────────────────────────────────────────┐
│ 🏷️ 標籤管理 (12)                     ▶      │
├──────────────────────────────────────────────┤
│ [+ 新增標籤]                                  │
│                                               │
│ 📌 釘選 (3/3)                                 │
│  🔥 重要                  [取消釘] [編輯] [🗑️]│
│  📋 日常                  [取消釘] [編輯] [🗑️]│
│  ⏰ 待議                  [取消釘] [編輯] [🗑️]│
│                                               │
│ 其他標籤                                       │
│  客訴                     [釘選]   [編輯] [🗑️]│
│  ...                                          │
└──────────────────────────────────────────────┘
```

**新增/編輯標籤 form**（inline、不開 modal）:
```
┌──────────────────────────────────────────────┐
│ 新增標籤                                       │
│ 名稱: [________] (最多 20 字)                  │
│ 顏色: 🟥🟧🟨🟡🟩🟢🟦🔵🟪🩷 ← 10 色 chip 點選 │
│ 預覽: ┃日常┃                                  │
│                       [取消] [儲存]            │
└──────────────────────────────────────────────┘
```

**權限**: 同店所有角色 (user / admin / super_admin) 都可建/編/刪/釘。
- 同店判斷: `current_user.store == tag.store` (或 super_admin 在 `current_store` 查看時、用 store filter 對齊)
- super_admin: 切到哪間店 (`?store=B店`) 就管那間店、跨店改別店標籤 → 403
- guest role / unauthenticated → 403

**Header `(N)`** = 該店標籤總數。

**刪除 confirm**:
- 用 `Tag.id` 查 `note_tags` + `checklist_item_tags` count
- count = 0 → 直接刪、不 confirm
- count > 0 → 跳 modal：「⚠️ 確定刪除「重要」？這個標籤目前被 N 個 item 在用、刪後 chip 會消失（item 內容不變）」+ 取消/確定按鈕
- 確定後 SQL DELETE Tag.id → DB 自動 cascade 清 m2m table

**釘選 toggle**:
- pinned=false → button 顯示「釘選」(disabled if 該店 pinned 已 = 3)
- pinned=true → button 顯示「取消釘」

### D. Tag picker modal（編輯 note / checklist editor / item 時用）

入口：
- 編輯 note (`/notes/edit/<id>`): toolbar 加「🏷️ 標籤 (2/5)」button
- 編輯 checklist 表單頁 (`/notes/edit/<id>` 同 endpoint 但 mode=checklist): note-level 加同 button
- 編輯 checklist item (現有 item modal): 加「🏷️」icon button

點按鈕開 modal：

```
┌────────────────────────────────────────┐
│ 🏷️ 選擇標籤 (已選 2/5)            ✕  │
├────────────────────────────────────────┤
│ ☑ ┃重要┃                              │
│ ☑ ┃日常┃                              │
│ ☐ ┃待議┃                              │
│ ☐ ┃客訴┃                              │
│ ...                                    │
├────────────────────────────────────────┤
│ [+ 新增標籤]                            │
│                                        │
│ (展開後 inline form, 同 section C)     │
│ 名稱: [______] 顏色: [palette]         │
│              [取消新增] [建立並選]      │
├────────────────────────────────────────┤
│                       [取消] [確定]    │
└────────────────────────────────────────┘
```

**行為**:
- 已選達 5 → 其他 unchecked chip 變灰、disabled、tooltip「最多 5 個」
- 「+ 新增標籤」展開 inline form 在 picker 內、確定後新標籤自動 prepended 到 list 並 checked
- 「確定」按鈕 → POST 對應 endpoint 更新 `note_tags` / `checklist_item_tags`
- 「取消」→ revert 不存

### E. Tag chip 在 note 卡片 + checklist item 顯示

**Note 卡片**（`/notes/` grid 上）:
```
┌────────────────────┐
│ ┃重要┃┃日常┃         │ ← chip 在標題上方一排
│ 📝 客人反映...       │
│ 內容預覽...          │
│ 日期 ・ 狀態 ・ 店家 │
└────────────────────┘
```

**Checklist item**（編輯頁、item 列表）:
```
┌──────────────────────────┐
│ ☐ 補貨牛奶                │
│   ┃重要┃┃待議┃            │ ← chip 在 item text 下方
└──────────────────────────┘
```

最多渲 5 個（資料層 enforce）。

### F. URL filter 結構

- `/notes/?tag=<tag_id>` — 標籤 filter，整數 id（不是 name、避免中文 URL encode）
- 跟既有 filter 共存：`/notes/?tag=3&category=employee&shift=A&range=today`
- 切其他 filter 時保留 `tag` query 參數（複用既有 query-preservation pattern）

**Backend filter 邏輯**（`notes/routes.py:index`）:
- `tag_id = request.args.get('tag', type=int)`
- `if tag_id:` → `Note.query.join(NoteTag).filter(NoteTag.tag_id == tag_id)`
- 只 filter note 層、不 filter item 層（item-level 標籤在 editor 內看）

### G. API endpoints (新增)

| Method | Path | Body | Response | 權限 |
|---|---|---|---|---|
| GET | `/notes/api/tags` | - | `{tags: [{id, name, color, pinned, usage_count}, ...]}` | 同店 user |
| POST | `/notes/api/tags` | `{name, color}` | `{tag: {...}}` | 同店 user |
| PATCH | `/notes/api/tags/<id>` | `{name?, color?}` | `{tag: {...}}` | 同店 user |
| DELETE | `/notes/api/tags/<id>` | - | `{ok: true, cascaded: N}` | 同店 user |
| POST | `/notes/api/tags/<id>/pin` | - | `{tag: {...pinned: true}}` | 同店 user、count <3 才可 |
| DELETE | `/notes/api/tags/<id>/pin` | - | `{tag: {...pinned: false}}` | 同店 user |
| PUT | `/notes/api/<note_id>/tags` | `{tag_ids: [1,3,5]}` | `{tag_ids: [...]}` | 同店 user |
| PUT | `/notes/api/items/<item_id>/tags` | `{tag_ids: [...]}` | `{tag_ids: [...]}` | 同店 user |

**Concurrency safety**:
- PUT note/item tags 用「全 replace」semantics（前端送完整 array、後端 DELETE existing + INSERT new、單一 transaction）
- 釘選 endpoint 用 SELECT FOR UPDATE 查當前 pinned count（避免 race condition 越限）

### H. WebSocket 同步（取捨）

現有 checklist item lock + edit 走 WebSocket 即時 broadcast。標籤改動是否也走 WS？

**v1 不做 WS broadcast**（YAGNI）:
- 標籤增刪改不會像 item edit 一樣 race condition 嚴重
- 同店多人同時編標籤的場景很少
- Reload 即可看到別人改動
- 省 WS 訊息類型 + Redis pub/sub 額外負擔

**未來再加** if user 反映「別人新增標籤我沒看到」。

### I. 邊界條件

| 情境 | 處理 |
|---|---|
| 標籤名重複（同店）| DB unique constraint + 後端先 SELECT 防衝、回 400「同名標籤已存在」|
| 釘選滿 3 仍嘗試 POST pin | 後端 SELECT FOR UPDATE 檢查、超過 → 400「釘選已滿 3 個」|
| Item 已掛 5 個再 PUT 6 個 | 後端 validation 拒、回 400「最多 5 個標籤」|
| 標籤 name 空字串 / 超過 20 字 | 後端 validation 拒、回 400 |
| color 非 palette 內 | 後端 validation 拒、回 400（防 XSS）|
| 跨店改別店標籤（不是 super_admin）| 後端權限檢查、回 403 |
| 刪除最後一個 pinned tag | OK、pinned count 變 2、其他 tag 仍可釘 |
| user 帳號被刪、tag.created_by → NULL | OK、tag 仍正常顯示 |
| 關鍵字搜尋模式 (`?q=`) | filter panel 強制展開、但只顯示搜尋 row + 「✕取消」、其他 6 排 row hidden（既有「搜尋時隱藏 limiting filter」邏輯延伸） |

### J. 容量檢核（按 [[feedback_capacity_check]]）

| 項目 | 估算 |
|---|---|
| Storage / store | ~20 tags × 50 bytes = 1 KB、加 m2m row ~20 bytes × 平均 2 tag/item × 1000 items = 40 KB / store。**忽略不計** |
| API call 增量 | 進 `/notes/` 多 1 GET `/api/tags`、編輯 note 多 1 PUT、編輯 item 多 1 PUT。手機 user 約 +3 request / day。**< 1% loading 增加** |
| SQL JOIN 增量 | `/notes/?tag=X` 多 1 INNER JOIN、其餘 query 不變。每 note 平均 2 tags、index `(store, id)` 已存在。**忽略不計** |
| Worker 影響 | 純 CRUD、無 CPU-heavy、不走 gevent threadpool。**0 風險** |

### K. 測試策略

**Backend (pytest)**:
- `tests/test_tags_model.py`: Tag/NoteTag/ChecklistItemTag 基本 CRUD、unique constraint、cascade delete
- `tests/test_tags_api.py`: 8 條 API endpoint × {auth ok / auth fail / validation / permission cross-store} ≈ 30 條 case
- `tests/test_tags_filter.py`: `/notes/?tag=X` 對的 note 列出、空 tag 回 empty、不存在 tag 回 400
- `tests/test_tags_pin_limit.py`: 釘選 3 個邊界、第 4 個拒、unpin 後 OK
- 預計 ~50 條新 test、加進現有 434 → 約 484 passed

**Frontend (manual)**:
- 無 frontend test framework、走 [[feedback_local_test_before_push]] + user 實機
- Verification checklist (寫進 plan)：CRUD picker / 釘選 toggle / filter panel auto-expand / cascade confirm modal / tag chip 顏色對比手機螢幕辨識度 / 5 tag 上限

### L. 部署計畫

1. Branch `feat/notes-tag-system`（已開）
2. 實作分階段（spec 寫完看 plan 怎麼切、預估拆 2-3 個 PR）
3. Schema：models.py 加 class + migrations/ 加 alembic file（**不必動 app.py**，純新 table 走 `db.create_all()`）
4. 按 [[feedback_local_test_before_push]] 本機驗 OK → push
5. Push 後 GHA build → Zeabur Redeploy → app.py 啟動自動建 3 張新 table → user prod 實機驗
6. SW v23 → v24 bump（新 CSS/JS asset）

## Risks 與 mitigation

| Risk | 機率 | 影響 | Mitigation |
|---|---|---|---|
| 開放所有人 CRUD 標籤、有人手滑刪錯導致全店 chip 不見 | 中 | 中 | 刪 confirm「N 個 item 在用」+ cascade 是 m2m 不是 item 本身、最壞 = 重新建 tag 重新貼 |
| 釘選給所有人、有人 toggle 釘選洗掉別人習慣 | 低 | 低 | per-store 釘選共識自然形成；如果亂可加 audit log（v2 再說） |
| filter panel 預設摺疊、老 user 一開始找不到 filter | 中 | 低 | URL 帶 filter 強制展開、user 首次 toggle 後記住 |
| 顏色 + 文字 contrast 在手機螢幕上仍看不清 | 低 | 中 | palette 10 色都過 WCAG AA 4.5:1、上線後 user 反映個別色再微調 |
| Tag picker modal 在編輯頁開、跟 lock 機制衝突 | 低 | 中 | picker 是純 UI 操作、不 acquire lock；確定後送 PUT 才走既有 lock check |
| 新 table 沒被 prod 建出來導致 ISE | 低 | 高 | 純新 table、`db.create_all()` 啟動時自動 CREATE、不需 inline ALTER；plan 加 deploy verification「prod REPL 查 `SELECT * FROM tags LIMIT 1` 不爆」 |

## 未來延伸（不在本 scope）

- mynotes 標籤
- 標籤使用頻率統計 (heatmap, 30-day usage chart)
- 標籤 audit log (誰建/刪/釘)
- AND 多選 filter
- 標籤 emoji（如果 user 反悔想要）
- Tag-based reminder / notification
- 標籤匯入/匯出 / 跨店複製
