# 店務筆記公告功能設計（notes/announcement）

- **建立日**：2026-05-08
- **作者**：hirain0126
- **狀態**：Design
- **影響模組**：notes（UI 入口 + index 路由 query）、新表 `announcements` / `announcement_attachments`
- **不影響**：mynotes、weather、admin、auth、vetting、AI summary pipeline

## 1. 動機與目標

### 1.1 動機

目前店務筆記只能依「店家」歸屬，super_admin 無法建立跨店公告，admin 也無法在自己店發佈不混在一般 note 裡的固定資訊。需要一種「公告型」筆記，符合下列特性：

- super_admin 可發跨店或單店的公告
- admin 可發自己店的公告
- 公告永遠置頂在 /notes/ index 最上面，不受日期/狀態排序影響
- 公告與一般 note 視覺上區隔（避免混淆）

### 1.2 目標

從**使用者視角**：公告是 notes 模組的一種類型，建立按鈕跟「新增筆記 / 新增確認表單」並列。

從**程式碼視角**：公告資料層完全與 Note 隔離（獨立 model / 獨立 routes 檔 / 獨立 templates 目錄 / 獨立 R2 storage prefix），符合 [feedback_module_isolation](memory) 規範。Note 模組在這次工作中**只動 `index()` route 跟 `templates/notes/index.html`** 兩個檔案。

### 1.3 非目標

- ❌ 不修復 Zeabur runtime logs（另案處理，本 spec 不含）
- ❌ 不重構 `notes/routes.py` 1537 行的肥大檔（另案）
- ❌ 不為公告建立 lock / WebSocket 即時協作（公告改動頻率低，第一版接受同時編輯衝突）
- ❌ 不讓公告進入 AI 摘要 pipeline
- ❌ 不為公告做有效期 / 過期自動下架（手動刪除即可）
- ❌ 不為公告做已讀 / 推播 / 未讀標記（未來擴充）

## 2. 範圍與權限

### 2.1 公告功能範圍

公告 = `title` + `content`（純文字 textarea）+ 附件（圖片可預覽放大、影片 inline 播放，跟 notes 既有附件 UX 一致）。

**不包含**：checklist 項目、lock 機制、WebSocket 協作、AI 摘要 / outline、status / category / shift / resolved_at 等 Note 特有欄位。

### 2.2 角色權限矩陣

| Role | 看 | 建立 | 編輯/刪除 |
|---|---|---|---|
| super_admin | 全部公告 | 任何 store + 「全部」 | 任何公告 |
| admin | NULL（全部）or `store == user.store` | 只能 `store = user.store`（不能選「全部」、不能選別店） | 只能 `store == user.store` 的公告 |
| user | NULL（全部）or `store == user.store` | ❌ | ❌ |

**「看不見即不存在」**：non-visible 公告對 user GET API 回 404 而不是 403（跟 mynotes ownership pattern 一致）。

### 2.3 Feature Gate

**不新增** `announcements` feature key。公告掛在現有 `notes` feature 之下：

- `/notes/announcement/*` 屬於 `notes_bp`，由現有 `_gate_notes_feature` 攔（要求 user 有 `notes` feature）
- 建立 / 編輯 / 刪除權限走 role check（`is_admin()` / `is_super_admin()`）
- `_sync_features_for_role` **不需修改**

## 3. 資料模型

### 3.1 表 `announcements`

```python
class Announcement(db.Model):
    __tablename__ = "announcements"

    id           = Column(Integer, primary_key=True)
    user_id      = Column(Integer, FK("users.id", ondelete="SET NULL"), nullable=True)
    author_name  = Column(EncryptedText, nullable=True)   # 作者刪除後 fallback
    title        = Column(EncryptedText, nullable=False, default="")
    content      = Column(EncryptedText, nullable=False, default="")
    store        = Column(Text, nullable=True)            # NULL = 全部；否則 = 單店店名
    created_at   = Column(DateTime, nullable=False, default=utcnow)
    updated_at   = Column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_announcements_created_at_desc", created_at.desc()),
        Index("ix_announcements_store", store),
    )

    @property
    def is_global(self) -> bool:
        return self.store is None

    @property
    def display_author(self) -> str:
        # 跟 Note.display_author 相同 pattern
        if self.user_id:
            from models import User
            u = User.query.get(self.user_id)
            if u:
                return u.username
        return f"{(self.author_name or '?')}（帳號已刪除）"
```

**設計重點**：

- `store IS NULL` 表示「全部」（不另加 `is_global` 欄位避免 redundant state）；application 層強制建立流程必選 radio
- `title` / `content` 走 `EncryptedText`（跟 Note 一致，data sovereignty 規範）
- `author_name` snapshot 跟 Note 對齊
- 不含 `status` / `category` / `shift` / `resolved_at` / `locked_*` / `is_ai_summary`

### 3.2 表 `announcement_attachments`

```python
class AnnouncementAttachment(db.Model):
    __tablename__ = "announcement_attachments"

    id              = Column(Integer, primary_key=True)
    announcement_id = Column(Integer, FK("announcements.id", ondelete="CASCADE"), nullable=False)
    user_id         = Column(Integer, FK("users.id", ondelete="SET NULL"), nullable=True)  # uploader
    object_key      = Column(Text, nullable=False)         # R2 key: announcements/{id}/{uuid}_{filename}
    filename        = Column(Text, nullable=False)         # 原檔名
    content_type    = Column(Text, nullable=False)
    file_size       = Column(Integer, nullable=False)
    created_at      = Column(DateTime, nullable=False, default=utcnow)

    announcement = relationship("Announcement",
        backref=backref("attachments", lazy=True, cascade="all, delete-orphan"))
    uploader = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("ix_announcement_attachments_announcement_id", "announcement_id"),
    )
```

**設計重點**：

- 完全照 `NoteAttachment` 的欄位/命名 convention（`object_key` / `filename` / `created_at` / `cascade="all, delete-orphan"`）
- ON DELETE CASCADE：刪公告時 DB rows 自動 cascade；R2 物件需在 `delete_announcement` route 內手動清理（跟現有 NoteAttachment delete pattern 一致）

### 3.3 Migration

`migrations/versions/0XX_announcements.py`（XX 編號接續現有最後 head）：

```python
def upgrade():
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.LargeBinary, nullable=True),
        sa.Column("title", sa.LargeBinary, nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("store", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_announcements_created_at_desc", "announcements", [sa.text("created_at DESC")])
    op.create_index("ix_announcements_store", "announcements", ["store"])

    op.create_table(
        "announcement_attachments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("announcement_id", sa.Integer,
                  sa.ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("object_key", sa.Text, nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("content_type", sa.Text, nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_announcement_attachments_announcement_id",
                    "announcement_attachments", ["announcement_id"])

def downgrade():
    op.drop_index("ix_announcement_attachments_announcement_id", "announcement_attachments")
    op.drop_table("announcement_attachments")
    op.drop_index("ix_announcements_store", "announcements")
    op.drop_index("ix_announcements_created_at_desc", "announcements")
    op.drop_table("announcements")
```

## 4. 模組結構

```
app_unified/
├── notes/
│   ├── routes.py                        ← 唯一改動：index() 加公告 query
│   └── announcement_routes.py           ← 新檔：公告 CRUD endpoints，註冊到 notes_bp
├── templates/
│   ├── notes/
│   │   └── index.html                   ← 改動：上方加公告區塊 + 第三個建立按鈕
│   └── announcements/                   ← 新目錄
│       ├── new.html                     ← 建立頁
│       ├── edit.html                    ← 編輯頁
│       └── _card_list.html              ← partial：在 /notes/ index 引用
├── static/
│   ├── js/
│   │   └── announcement_attachments.js  ← 新檔：附件 UI（複製 notes 的 createAttItem pattern）
│   └── css/
│       └── announcements.css            ← 新檔：公告卡片樣式
├── models.py                             ← 改動：加 Announcement / AnnouncementAttachment 兩個 class
└── migrations/versions/
    └── 0XX_announcements.py              ← 新檔
```

**關鍵設計點**：

1. 路由獨立檔 `notes/announcement_routes.py`，**註冊到既有 `notes_bp`**（不獨立 blueprint，URL prefix 共用 `/notes`）。避免 `notes/routes.py` 1537 行繼續肥
2. Templates 獨立目錄 `templates/announcements/`，符合 `feedback_module_isolation` 第 1 條
3. R2 storage prefix `announcements/{announcement_id}/{uuid}_{filename}`，跟 `attachments/` 完全隔離
4. 視覺區隔：公告 card 配淡黃 / 「全部」公告配淡紅 / 📢 icon / scope chip

## 5. API Endpoints

### 5.1 HTML 頁面

| Method | URL | Auth | 說明 |
|---|---|---|---|
| GET | `/notes/announcement/new` | `_can_create()` | 建立頁 |
| GET | `/notes/announcement/<int:id>/edit` | `_can_modify(a)` | 編輯頁 |

### 5.2 JSON API

| Method | URL | Auth | 說明 |
|---|---|---|---|
| POST | `/notes/announcement/api` | `_can_create()` + `_can_target_store(payload.store)` | 建立 |
| GET | `/notes/announcement/api` | login | 列表（依 visibility filter，分頁） |
| GET | `/notes/announcement/api/<int:id>` | `_can_view(a)` | 取單筆 |
| PUT | `/notes/announcement/api/<int:id>` | `_can_modify(a)` + `_can_target_store(new_store)` | 更新 |
| DELETE | `/notes/announcement/api/<int:id>` | `_can_modify(a)` | 刪除（含 R2 物件清理） |
| POST | `/notes/announcement/api/<int:id>/attachments` | `_can_modify(a)` | 上傳附件 |
| GET | `/notes/announcement/api/<int:id>/attachments` | `_can_view(a)` | 列附件 |
| DELETE | `/notes/announcement/api/attachments/<int:att_id>` | `_can_modify(a)` | 刪附件 |

**沒有的 endpoints**（公告不需要）：

- ❌ lock heartbeat / acquire / release
- ❌ AI summary / outline / task polling
- ❌ checklist items CRUD
- ❌ WebSocket 協作

### 5.3 Permission Helpers

```python
# notes/announcement_routes.py 內部 helper
def _can_create() -> bool:
    """誰能建立公告：admin / super_admin"""
    return current_user.is_admin()

def _can_target_store(store: str | None) -> bool:
    """檢查當前 user 能否對這個 scope 發/改公告"""
    if current_user.is_super_admin():
        return True   # super_admin: 任何店或 NULL（全部）皆可
    if current_user.is_admin():
        return store == current_user.store and store is not None
    return False

def _can_modify(a: Announcement) -> bool:
    if current_user.is_super_admin():
        return True
    if current_user.is_admin() and a.store == current_user.store:
        return True
    return False

def _can_view(a: Announcement) -> bool:
    if current_user.is_super_admin():
        return True
    return a.store is None or a.store == current_user.store
```

### 5.4 Request / Response

**POST `/notes/announcement/api`**

```jsonc
// Request (super_admin 可選 NULL=全部 或任一 store；admin 強制 user.store)
{ "title": "12月公司聚餐", "content": "12/20 晚上六點...", "store": null }

// Response 201
{
  "id": 42, "title": "...", "content": "...",
  "store": null, "is_global": true,
  "author_name": "alice", "user_id": 7,
  "created_at": "2026-05-08T10:30:00+08:00",
  "updated_at": "2026-05-08T10:30:00+08:00",
  "attachments": []
}
```

**GET `/notes/announcement/api`** (list)

- Query params: `page` (default 1), `per_page` (default 20)
- super_admin 可加 `?store=B店` filter；admin/user 永遠 server-side 強制 visibility filter
- Response: `{ "items": [...], "total": N, "page": 1, "per_page": 20, "has_next": false, "has_prev": false }`
- 排序：`created_at DESC`（最新建立在最上面）

### 5.5 Error 處理

| 情境 | HTTP |
|---|---|
| 未登入 | 302 → /auth/login |
| 沒有 `notes` feature | 404（before_request gate） |
| user role 嘗試 POST | 403（`_can_create()`） |
| admin 送 `store: null` 或別店 | 403（`_can_target_store()`） |
| admin 嘗試改別店公告 | 403（`_can_modify()`） |
| user 嘗試 GET 別店公告 ID | 404（看不見即不存在） |
| 公告不存在 | 404 |
| title 空字串 | 400 |
| store 不是合法 Store.name | 400 |
| 上傳 disallowed content type | 400 |
| 上傳超過 MAX_FILE_SIZE | 413 |

### 5.6 附件 URL 加密（必做）

```python
from storage import get_signed_url

def _att_to_json(att):
    return {
        "id": att.id,
        "filename": att.filename,
        "content_type": att.content_type,
        "file_size": att.file_size,
        "url": get_signed_url(att.object_key, expires_in=3600),  # ← R2 presigned URL，1 小時有效
    }
```

- 所有回傳附件 URL 的 endpoints 一律用 `get_signed_url`，**禁止**回傳裸 R2 public URL
- 過期後前端重新打 list endpoint 取新的 signed URL（跟 notes 行為一致）
- R2 bucket 維持 private，公告繼承既有 storage 設定

## 6. UI 設計

### 6.1 /notes/ index 上方變更

```
┌─ /notes/ index ─────────────────────────────────────┐
│ 🏠 店務筆記                                          │
│ [+ 新增筆記] [☑ 新增確認表單] [📢 新增公告]          │ ← 第三按鈕 admin/super_admin only
│ ┌──────────────────────────────────────────────────┐│
│ │ 📢 公告區塊（永遠置頂）                            ││
│ │ ┌──────────────────────────────────────────────┐ ││
│ │ │ 📢 [全部] 12月公司聚餐                       │ ││
│ │ │ 12/20 晚上六點...                            │ ││
│ │ │ [縮圖] [縮圖] (image/video preview)          │ ││
│ │ │ alice · 2026-05-08 10:30  [編輯][刪除]       │ ││
│ │ └──────────────────────────────────────────────┘ ││
│ └──────────────────────────────────────────────────┘│
│ ┌─ 一般筆記（既有 UI 完全不動）────────────────────┐│
│ │ ...                                              ││
│ └──────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────┘
```

**實作改動最小化**：

- `notes/routes.py::index()` 多 query 一次公告 + 算 `editable_ann_ids` set，傳進 template
- `templates/notes/index.html` 加 `<link>` 引入 `announcements.css`、加第三個按鈕、`{% include 'announcements/_card_list.html' %}`
- `notes/routes.py` 其他 1500+ 行**完全不動**

### 6.2 建立 / 編輯頁表單

| Field | Type | super_admin | admin | user |
|---|---|---|---|---|
| 標題 | text input, required | ✓ | ✓ | N/A |
| 適用範圍 | radio group | 「全部」+ 各 Store | 唯讀顯示 user.store | N/A |
| 內容 | textarea (6-10 行) | ✓ | ✓ | N/A |
| 附件 | file input multi (image/jpeg,png,gif,webp + video/mp4,quicktime,webm) | ✓ | ✓ | N/A |

編輯頁多一個 [刪除公告] 按鈕（在底部）。沒有 lock 機制（第一版簡化）。

### 6.3 附件 UI 對齊 notes

從 `notes/editor.html::createAttItem` / `openFullscreen` 複製到 `static/js/announcement_attachments.js`：

- 圖片：縮圖顯示，點擊 `openFullscreen(url)` 全螢幕放大
- 影片：inline `<video controls preload="metadata" playsInline>`
- 多檔上傳 + 個別刪除按鈕（×）
- 檔案大小顯示

CSS 樣式 `.att-item img` / `.att-item video` 從 `notes/editor.html` 同步到 `static/css/announcements.css`。

**為何不抽共用 helper**：`feedback_module_isolation` 第 4 條規範「共用 helper 只加可選覆寫」。本 spec 選擇 ~80 行 JS duplicated 而不是改 notes 既有檔；未來若要 dedupe 是另一個 refactor branch。

### 6.4 公告卡片視覺區隔

```css
/* static/css/announcements.css */
.announcement-section {
  margin-bottom: 1.5rem;
  border-left: 4px solid #f39c12;
  padding-left: .75rem;
}
.announcement-card {
  background: #fffbea;             /* 淡黃 */
  border: 1px solid #f9d77b;
  border-radius: 6px;
  padding: .75rem 1rem;
  margin-bottom: .5rem;
}
.announcement-card.is-global {
  background: #fff5f5;             /* 「全部」用淡紅，更醒目 */
  border-color: #f5a3a3;
}
.scope-chip {
  display: inline-block;
  padding: 2px 8px;
  background: #f39c12;
  color: white;
  border-radius: 10px;
  font-size: .75rem;
  margin-right: .5rem;
}
```

## 7. Notes 模組互動點

### 7.1 唯一改動處：`notes/routes.py::index()`

```python
@notes_bp.route("/")
def index():
    # ... 既有 notes query 邏輯不動 ...

    # 新增：query 公告（依 visibility filter）
    from models import Announcement
    from sqlalchemy import or_
    if current_user.is_super_admin():
        ann_q = Announcement.query
    else:
        ann_q = Announcement.query.filter(
            or_(Announcement.store.is_(None),
                Announcement.store == current_user.store)
        )
    announcements = ann_q.order_by(Announcement.created_at.desc()).all()

    # 算可編輯的公告 id set
    editable_ann_ids = set()
    if current_user.is_super_admin():
        editable_ann_ids = {a.id for a in announcements}
    elif current_user.is_admin():
        editable_ann_ids = {a.id for a in announcements
                            if a.store == current_user.store}

    return render_template("notes/index.html",
                          # ... 既有 context 不動 ...
                          announcements=announcements,
                          editable_ann_ids=editable_ann_ids)
```

### 7.2 AI 摘要 Pipeline 完全不參與

`notes/routes.py` 內 `_build_categorized_notes` / `_run_local_summary` / `_format_note_for_ai` 等函式只 query `Note` 表。**不掃 `announcements` 表**，零改動，零風險。

### 7.3 Storage 隔離

`storage.py::upload_attachment(file, prefix=...)` 既有設計支援 prefix 覆寫。公告附件呼叫：

```python
upload_attachment(file, prefix=f"announcements/{announcement_id}/")
```

R2 物件 key 完全跟 `attachments/` 隔離，符合 `feedback_module_isolation` 第 3 條。

## 8. Testing Strategy

### 8.1 Unit / Integration Tests

新檔 `tests/test_announcements.py`，覆蓋：

**Permission matrix**

- `test_user_cannot_create`：role=user POST → 403
- `test_admin_can_create_own_store`：admin (store=B) POST `{store: "B店"}` → 201
- `test_admin_cannot_create_global`：admin POST `{store: null}` → 403
- `test_admin_cannot_create_other_store`：admin (store=B) POST `{store: "C店"}` → 403
- `test_super_admin_can_create_global`：super_admin POST `{store: null}` → 201
- `test_super_admin_can_create_any_store`：super_admin POST `{store: "C店"}` → 201
- `test_admin_cannot_edit_other_store`
- `test_admin_cannot_edit_global`
- `test_super_admin_can_edit_anything`
- `test_user_cannot_edit_or_delete`

**Visibility**

- `test_user_sees_global_and_own_store`
- `test_user_get_other_store_returns_404`（看不見即不存在）
- `test_super_admin_sees_all`
- `test_list_orders_by_created_at_desc`

**Notes index 整合**

- `test_notes_index_renders_announcements_above`
- `test_notes_index_admin_sees_create_button`
- `test_notes_index_user_does_not_see_create_button`
- `test_notes_query_unaffected`（公告不出現在 AI 摘要分類）

**Schema / Validation**

- `test_create_requires_title`
- `test_create_invalid_store_name`
- `test_store_null_means_global`

**Attachments**

- `test_upload_attachment_signed_url`（response url 含 `X-Amz-Signature`）
- `test_attachment_storage_prefix`（DB `object_key` 開頭 `announcements/{id}/`）
- `test_delete_announcement_cascades_attachments`
- `test_disallowed_content_type_rejected`
- `test_attachment_size_limit`

**Feature gate**

- `test_no_notes_feature_returns_404`

### 8.2 Regression Tests

跑既有 `tests/test_notes_lock.py` / `test_notes_misc.py` / `test_notes.py` / `test_ai_task_race.py` / `test_category.py` / `test_admin_set_role.py` 全綠通過。

### 8.3 Migration Test

```bash
flask db upgrade head      # 確認新 migration 跑得起來
flask db downgrade -1      # 確認 downgrade 不炸
flask db upgrade head      # 再升回來
```

CI 既有 `.github/workflows/test.yml` 已含 alembic 步驟，照單運作。

### 8.4 部署後手動驗證 Checklist

- [ ] super_admin 建立「全部」公告 → 各店 user 都看得到
- [ ] super_admin 建立「B 店」公告 → B 店 user 看到、C 店 user 看不到
- [ ] admin (B 店) 建立公告 → 強制 B 店、看不到「全部」radio
- [ ] admin (B 店) 看不到 C 店公告（query level filter）
- [ ] 上傳 image → 公告卡片顯示縮圖，點擊放大全螢幕
- [ ] 上傳 video → 公告卡片 inline 播放
- [ ] 公告附件 URL 是 R2 presigned（含 `X-Amz-Signature`），1 小時有效
- [ ] 進入 /notes/ → 公告區塊在最上面，順序最新建立優先
- [ ] 刪除公告 → DB row 刪、R2 物件清掉
- [ ] /notes/ AI 摘要產出 → 公告**不出現**在分類結果中

## 9. 部署影響

| 項目 | 影響 |
|---|---|
| Zeabur health check | ✅ 不影響（announcements 路由載入時間極短，無 ML model） |
| Cold start | ✅ 不影響 |
| GHCR image build | ✅ 不影響（純 Python，無新 binary 依賴） |
| pytest CI | 新增 `tests/test_announcements.py`；既有 notes / mynotes / admin / category test 須全綠 |
| 既有 notes lock / AI / WS 行為 | ✅ 不影響（公告不參與這些 pipeline） |

## 10. 未來擴充（非本 spec 範圍）

- 公告已讀紀錄（`announcement_reads` 表）+ 未讀小紅點
- 公告推播通知（push notification）
- 公告有效期 / 排程發佈
- 公告固定置頂優先順序（手動拖曳）
- 公告 markdown / rich text 編輯器

## 11. Open Questions / Risks

- **Risk: 同一公告多人同時編輯衝突**：第一版接受 last-write-wins，因為公告改動頻率極低（每週數筆）。若實際遇到衝突再加 lock。
- **Risk: 公告卡片在 /notes/ index 過長**：第一版接受全展開（無收合），若實際公告內容過長造成 index 爆版面再加截斷 + 「展開」按鈕。
- **Open: 公告數量上限**：第一版不設上限。若公告無限累積造成 index 載入慢，考慮加分頁或自動歸檔（>30 天舊公告隱藏）。
