# 附件縮圖 + 上傳壓縮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 上傳圖片時產縮圖(640px)+ 壓縮原圖(3200px),列表用縮圖 + `loading=lazy`,既有舊圖用 backfill 補縮圖;解決「讀取圖片非常慢」。

**Architecture:** 新增純函式模組 `image_utils.py` 做 Pillow resize/壓縮(可獨立測試)。`storage.upload_attachment()` 改成透過 gevent threadpool 呼叫它,回傳 `(object_key, thumb_key)`。3 張 attachment 表加 `thumb_key` 欄位。序列化加 `thumb_url`。前端 `img.src = att.thumb_url || att.url` + lazy。Backfill script 對舊圖只補縮圖、不動原圖。

**Tech Stack:** Flask + SQLAlchemy + Pillow 12.2.0 + gevent(threadpool)+ boto3(R2)+ pytest。

## Global Constraints

- **Schema 三檔對齊(🚨 BLOCKING)**:任何加 column 必須同時改 `models.py` + `app.py` inline `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + `migrations/versions/`。漏一處 prod 100% ISE。prod 不跑 alembic,靠 app.py inline repair。
- **本機 dev SQLite 不自動同步 schema**:加 column 後本機要 `rm app_unified/dev.db` reseed 或手動 ALTER(`IF NOT EXISTS` SQLite 不支援、被 try/except 吞)。
- **gevent hub**:Pillow 是 CPU-heavy native code,必須走 `gevent.get_hub().threadpool.apply`(比照 `mynotes/routes.py:540`),不可在 request greenlet 直接跑而卡 hub。
- **SW bump**:動到 `/static/**`(js/css)必 bump `sw.js` 的 `CACHE_NAME`;純 template inline script 改動不用。
- **CI 測試指令**:`pytest tests/`(非裸 `pytest`)。所有測試放 `app_unified/tests/`。
- **常數**:`MAX_LONG_EDGE=3200`、`THUMB_LONG_EDGE=640`、`MAIN_QUALITY=85`、`THUMB_QUALITY=78`。
- **可處理型別**:`image/jpeg`、`image/png`、`image/webp`。`image/gif` + 所有 video 跳過(回原圖、thumb=None)。
- **Backfill 非破壞性**:舊原圖不覆寫,只補產縮圖。壓縮(3200)只套用在新上傳。
- 所有工作在 branch `feat/attachment-thumbnails`(已建、spec 已 commit)。

---

## Task 1: `image_utils.py` 純函式模組 + 單元測試

**Files:**
- Create: `app_unified/image_utils.py`
- Test: `app_unified/tests/test_image_utils.py`

**Interfaces:**
- Produces:
  - `process_upload_image(raw_bytes: bytes, content_type: str) -> tuple[bytes, bytes|None]` — 回 `(main_bytes, thumb_bytes)`。非可處理型別 / Pillow 失敗 → `(raw_bytes, None)`。
  - `thumb_key_for(object_key: str) -> str` — 由 object_key 推導 thumb 的 R2 key(`<base>_thumb.jpg`)。
  - 常數 `MAX_LONG_EDGE`、`THUMB_LONG_EDGE`、`PROCESSABLE_TYPES`。

- [ ] **Step 1: 寫 failing tests**

```python
# app_unified/tests/test_image_utils.py
"""image_utils: Pillow 縮圖/壓縮純函式測試。"""
import io
from PIL import Image


def _png_bytes(w, h, color=(200, 30, 30)):
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(w, h):
    img = Image.new("RGB", (w, h), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _dims(b):
    return Image.open(io.BytesIO(b)).size


def test_large_image_main_downscaled_to_3200():
    from image_utils import process_upload_image, MAX_LONG_EDGE
    raw = _jpeg_bytes(6000, 3000)
    main, thumb = process_upload_image(raw, "image/jpeg")
    w, h = _dims(main)
    assert max(w, h) == MAX_LONG_EDGE   # 6000 → 3200
    assert (w, h) == (3200, 1600)


def test_small_image_main_not_upscaled():
    from image_utils import process_upload_image
    raw = _jpeg_bytes(800, 600)
    main, thumb = process_upload_image(raw, "image/jpeg")
    assert _dims(main) == (800, 600)    # 不放大


def test_thumb_long_edge_640_and_is_jpeg():
    from image_utils import process_upload_image, THUMB_LONG_EDGE
    raw = _jpeg_bytes(4000, 2000)
    main, thumb = process_upload_image(raw, "image/jpeg")
    assert thumb is not None
    tw, th = _dims(thumb)
    assert max(tw, th) == THUMB_LONG_EDGE   # 4000 → 640
    assert Image.open(io.BytesIO(thumb)).format == "JPEG"


def test_png_with_alpha_thumb_flattened_to_jpeg():
    from image_utils import process_upload_image
    img = Image.new("RGBA", (1000, 800), (0, 0, 0, 0))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    main, thumb = process_upload_image(buf.getvalue(), "image/png")
    assert Image.open(io.BytesIO(thumb)).mode == "RGB"   # alpha 被 flatten


def test_gif_not_processed():
    from image_utils import process_upload_image
    img = Image.new("P", (500, 500))
    buf = io.BytesIO(); img.save(buf, format="GIF")
    raw = buf.getvalue()
    main, thumb = process_upload_image(raw, "image/gif")
    assert main == raw and thumb is None


def test_video_not_processed():
    from image_utils import process_upload_image
    raw = b"\x00\x00\x00\x18ftypmp42fake-video-bytes"
    main, thumb = process_upload_image(raw, "video/mp4")
    assert main == raw and thumb is None


def test_corrupt_bytes_returns_raw_no_raise():
    from image_utils import process_upload_image
    raw = b"not-an-image-at-all"
    main, thumb = process_upload_image(raw, "image/jpeg")
    assert main == raw and thumb is None   # 不 raise、fallback


def test_thumb_key_for():
    from image_utils import thumb_key_for
    assert thumb_key_for("attachments/12/abcd.jpg") == "attachments/12/abcd_thumb.jpg"
    assert thumb_key_for("private/3/xy.png") == "private/3/xy_thumb.jpg"
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_image_utils.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'image_utils'`

- [ ] **Step 3: 實作 `image_utils.py`**

```python
# app_unified/image_utils.py
"""附件影像處理:壓縮原圖 + 產縮圖。純函式,不碰 R2/DB,可獨立測試。"""
import io
import logging
from PIL import Image, ImageOps

_LOG = logging.getLogger(__name__)

MAX_LONG_EDGE = 3200      # 原圖長邊上限
THUMB_LONG_EDGE = 640     # 縮圖長邊
MAIN_QUALITY = 85
THUMB_QUALITY = 78

# 只處理靜態圖;gif(可能動圖)與 video 一律跳過
PROCESSABLE_TYPES = {"image/jpeg", "image/png", "image/webp"}

_PIL_FORMAT = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def thumb_key_for(object_key: str) -> str:
    """由原圖 R2 key 推導縮圖 key:<base>_thumb.jpg(縮圖一律 JPEG)。"""
    base = object_key.rsplit(".", 1)[0] if "." in object_key else object_key
    return f"{base}_thumb.jpg"


def _to_rgb(img):
    """JPEG 不支援 alpha/P;flatten 到白底 RGB。"""
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        conv = img.convert("RGBA")
        bg.paste(conv, mask=conv.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _encode_main(img, content_type: str) -> bytes:
    fmt = _PIL_FORMAT[content_type]
    buf = io.BytesIO()
    if fmt == "JPEG":
        _to_rgb(img).save(buf, format="JPEG", quality=MAIN_QUALITY, optimize=True)
    elif fmt == "PNG":
        img.save(buf, format="PNG", optimize=True)
    else:  # WEBP
        img.save(buf, format="WEBP", quality=MAIN_QUALITY)
    return buf.getvalue()


def _encode_thumb(img) -> bytes:
    buf = io.BytesIO()
    _to_rgb(img).save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return buf.getvalue()


def process_upload_image(raw_bytes: bytes, content_type: str):
    """回 (main_bytes, thumb_bytes)。
    - content_type 非可處理型別 → (raw_bytes, None)
    - Pillow 任何錯誤 → (raw_bytes, None)(記 log,不中斷上傳)
    """
    if content_type not in PROCESSABLE_TYPES:
        return raw_bytes, None
    try:
        src = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))

        main_img = src.copy()
        if max(main_img.size) > MAX_LONG_EDGE:
            main_img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.LANCZOS)
        main_bytes = _encode_main(main_img, content_type)

        thumb_img = src.copy()
        thumb_img.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.LANCZOS)
        thumb_bytes = _encode_thumb(thumb_img)

        return main_bytes, thumb_bytes
    except Exception as e:
        _LOG.warning("process_upload_image failed (%s): %s", content_type, e)
        return raw_bytes, None
```

- [ ] **Step 4: 跑測試確認 pass**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_image_utils.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/image_utils.py app_unified/tests/test_image_utils.py
git commit -m "feat(attachments): add image_utils — resize/compress + thumbnail pure helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Schema — 3 張表加 `thumb_key`

**Files:**
- Modify: `app_unified/models.py`(`NoteAttachment` ~L266、`PrivateAttachment` ~L360、`AnnouncementAttachment` ~L455)
- Modify: `app_unified/app.py`(schema repair 區塊,line 487 `db.session.commit()` 之前)
- Create: `app_unified/migrations/versions/019_attachment_thumb_key.py`
- Test: `app_unified/tests/test_attachment_thumb_key_schema.py`

**Interfaces:**
- Produces:3 個 model 皆有 `thumb_key`(nullable Text)屬性。

- [ ] **Step 1: 寫 failing test**

```python
# app_unified/tests/test_attachment_thumb_key_schema.py
"""3 張 attachment 表都有 thumb_key 欄位(nullable)。"""
def test_note_attachment_has_thumb_key():
    from models import NoteAttachment
    assert hasattr(NoteAttachment, "thumb_key")


def test_private_attachment_has_thumb_key():
    from models import PrivateAttachment
    assert hasattr(PrivateAttachment, "thumb_key")


def test_announcement_attachment_has_thumb_key():
    from models import AnnouncementAttachment
    assert hasattr(AnnouncementAttachment, "thumb_key")


def test_thumb_key_nullable_roundtrip(app, db_session):
    """建一筆 thumb_key=None + 一筆有值,都存得起來。"""
    from models import NoteAttachment, Note
    n = Note(note_type="note", title="t", content="", store="A",
             status="active", priority="medium")
    db_session.add(n); db_session.commit()
    a1 = NoteAttachment(note_id=n.id, object_key="k1.jpg", filename="a.jpg",
                        content_type="image/jpeg", file_size=1, thumb_key=None)
    a2 = NoteAttachment(note_id=n.id, object_key="k2.jpg", filename="b.jpg",
                        content_type="image/jpeg", file_size=1,
                        thumb_key="k2_thumb.jpg")
    db_session.add_all([a1, a2]); db_session.commit()
    assert a1.thumb_key is None
    assert a2.thumb_key == "k2_thumb.jpg"
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_attachment_thumb_key_schema.py -v`
Expected: FAIL — `AttributeError` / `thumb_key` 不存在(本機 dev.db 也還沒欄位)

- [ ] **Step 3: `models.py` 三個 model 各加欄位**

在每個 attachment model 的 `file_size` 欄位下方加(3 處):

```python
    thumb_key = db.Column(db.Text, nullable=True)   # 縮圖 R2 key;舊圖/gif/影片為 None
```

- [ ] **Step 4: `app.py` inline schema repair 加 3 個 ALTER**

在 `app.py` line 487(`checklist_items ... reminder_set_at` 的 `except Exception: pass` 之後、`db.session.commit()` 之前)插入:

```python
                # 附件縮圖 thumb_key(2026-07-03）— 3 張 attachment 表
                for _tbl in ("note_attachments", "private_attachments",
                             "announcement_attachments"):
                    try:
                        db.session.execute(db.text(
                            f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS thumb_key TEXT"))
                    except Exception:
                        pass
```

- [ ] **Step 5: 建 migration 019**

```python
# app_unified/migrations/versions/019_attachment_thumb_key.py
"""attachment thumb_key: 附件縮圖 R2 key

Revision ID: 019
Revises: 018
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa

revision = '019'
down_revision = '018'
branch_labels = None
depends_on = None

_TABLES = ("note_attachments", "private_attachments", "announcement_attachments")


def upgrade():
    for t in _TABLES:
        op.add_column(t, sa.Column('thumb_key', sa.Text(), nullable=True))


def downgrade():
    for t in _TABLES:
        op.drop_column(t, 'thumb_key')
```

- [ ] **Step 6: 本機 dev.db 補欄位(SQLite 不自動同步)**

```bash
cd /home/hirain0126/projects/webapp/app_unified
python -c "
import sqlite3
db = sqlite3.connect('dev.db')
for t in ('note_attachments','private_attachments','announcement_attachments'):
    try:
        db.execute(f'ALTER TABLE {t} ADD COLUMN thumb_key TEXT')
        print('added', t)
    except Exception as e:
        print('skip', t, e)
db.commit(); db.close()
"
```
(若 `dev.db` 不存在或路徑不同,改跑 `bash scripts/run_local.sh` 讓它 reseed;dev DB 路徑見 `DATABASE_URL`。)

- [ ] **Step 7: 跑測試確認 pass**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_attachment_thumb_key_schema.py -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/models.py app_unified/app.py \
        app_unified/migrations/versions/019_attachment_thumb_key.py \
        app_unified/tests/test_attachment_thumb_key_schema.py
git commit -m "feat(attachments): add thumb_key column to 3 attachment tables (3-file aligned)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `upload_attachment` 回傳 `(object_key, thumb_key)` + 3 呼叫端寫 thumb_key

**Files:**
- Modify: `app_unified/storage.py`(`upload_attachment` L62-83;新增 `_offload` helper)
- Modify: `app_unified/notes/routes.py`(L1926-1938 upload 路徑)
- Modify: `app_unified/notes/announcement_routes.py`(L298-310)
- Modify: `app_unified/mynotes/routes.py`(L401-412)
- Test: `app_unified/tests/test_attachment_upload_thumb.py`

**Interfaces:**
- Consumes:`image_utils.process_upload_image`、`image_utils.thumb_key_for`(Task 1)。
- Produces:`upload_attachment(...) -> tuple[str|None, str|None]`(object_key, thumb_key)。

- [ ] **Step 1: 寫 failing integration test**

```python
# app_unified/tests/test_attachment_upload_thumb.py
"""上傳圖 → 回傳 (object_key, thumb_key) 且 thumb 被上傳;非圖 → thumb_key None。"""
import io
from unittest.mock import patch
from PIL import Image


def _jpeg(w=4000, h=2000):
    img = Image.new("RGB", (w, h), (12, 200, 90))
    buf = io.BytesIO(); img.save(buf, format="JPEG"); return buf.getvalue()


def test_upload_attachment_returns_object_and_thumb_key(app):
    from storage import upload_attachment
    puts = {}

    class FakeClient:
        def put_object(self, Bucket, Key, Body, ContentType):
            puts[Key] = (len(Body), ContentType)

    with app.app_context():
        app.config["R2_ENDPOINT_URL"] = "https://fake.r2"
        app.config["R2_BUCKET_NAME"] = "b"
        with patch("storage._get_client", return_value=FakeClient()):
            obj, thumb = upload_attachment(_jpeg(), 7, "photo.jpg", "image/jpeg")
    assert obj and obj.endswith(".jpg")
    assert thumb == obj.rsplit(".", 1)[0] + "_thumb.jpg"
    assert obj in puts and thumb in puts           # 兩顆都上傳了
    assert puts[thumb][1] == "image/jpeg"          # 縮圖 content-type


def test_upload_video_no_thumb(app):
    from storage import upload_attachment

    class FakeClient:
        def put_object(self, **kw):
            pass

    with app.app_context():
        app.config["R2_ENDPOINT_URL"] = "https://fake.r2"
        app.config["R2_BUCKET_NAME"] = "b"
        with patch("storage._get_client", return_value=FakeClient()):
            obj, thumb = upload_attachment(b"fake-mp4", 7, "v.mp4", "video/mp4")
    assert obj and thumb is None
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_attachment_upload_thumb.py -v`
Expected: FAIL — 目前 `upload_attachment` 回單一 str,unpack 兩個值報錯

- [ ] **Step 3: 改 `storage.py`**

在 `upload_attachment` 上方新增 offload helper:

```python
def _offload(fn, *args):
    """CPU-heavy 工作丟 gevent threadpool 跑,避免卡 hub。無 gevent 則直接跑(測試/dev)。"""
    try:
        import gevent
        hub = gevent.get_hub()
    except Exception:
        return fn(*args)
    return hub.threadpool.apply(fn, args)
```

把 `upload_attachment` 整段換成:

```python
def upload_attachment(file_bytes: bytes, note_id: int, filename: str, content_type: str, prefix: str = "attachments"):
    """Upload attachment to R2. 回 (object_key, thumb_key)。

    - 靜態圖(jpeg/png/webp):壓縮原圖(長邊≤3200)+ 產縮圖(640,JPEG),兩顆都上傳。
    - gif/影片/其他:原檔上傳,thumb_key=None。
    - R2 未設定 / boto3 不可用:回 (None, None)。
    """
    from image_utils import process_upload_image, thumb_key_for

    if not BOTO3_AVAILABLE:
        return None, None
    cfg = current_app.config
    if not cfg.get("R2_ENDPOINT_URL"):
        return None, None

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    object_key = f"{prefix}/{note_id}/{uuid.uuid4().hex}.{ext}"

    main_bytes, thumb_bytes = _offload(process_upload_image, file_bytes, content_type)

    client = _get_client()
    client.put_object(
        Bucket=cfg["R2_BUCKET_NAME"],
        Key=object_key,
        Body=main_bytes,
        ContentType=content_type,
    )

    thumb_key = None
    if thumb_bytes is not None:
        thumb_key = thumb_key_for(object_key)
        try:
            client.put_object(
                Bucket=cfg["R2_BUCKET_NAME"],
                Key=thumb_key,
                Body=thumb_bytes,
                ContentType="image/jpeg",
            )
        except Exception:
            thumb_key = None   # 縮圖失敗不擋主上傳,前端 fallback 原圖

    return object_key, thumb_key
```

- [ ] **Step 4: 改 3 個呼叫端**

**4a. `notes/routes.py` L1926**(`upload_attachment_api`):

```python
    object_key, thumb_key = upload_attachment(file_bytes, note_id, file.filename, content_type)
    if not object_key:
        return jsonify({"status": "error", "message": "儲存服務未設定"}), 503

    attachment = NoteAttachment(
        note_id=note_id,
        checklist_item_id=item_id,
        user_id=current_user.id,
        object_key=object_key,
        thumb_key=thumb_key,
        filename=file.filename,
        content_type=content_type,
        file_size=len(file_bytes),
    )
```

**4b. `notes/announcement_routes.py` L298**:

```python
    object_key, thumb_key = upload_attachment(file_bytes, a.id, file.filename,
                                              content_type, prefix="announcements")
    if not object_key:
        return jsonify({"status": "error", "message": "儲存服務未設定"}), 503

    att = AnnouncementAttachment(
        announcement_id=a.id,
        user_id=current_user.id,
        object_key=object_key,
        thumb_key=thumb_key,
        filename=file.filename,
        content_type=content_type,
        file_size=len(file_bytes),
    )
```

**4c. `mynotes/routes.py` L401**:

```python
    key, thumb_key = upload_attachment(data, n.id, file.filename or "file", ct, prefix=prefix)
    if not key:
        return jsonify({"status": "error", "message": "儲存失敗"}), 500
    att = PrivateAttachment(
        note_id=n.id,
        item_id=item_id,
        user_id=current_user.id,
        object_key=key,
        thumb_key=thumb_key,
        filename=file.filename or "file",
        content_type=ct,
        file_size=len(data),
    )
```

- [ ] **Step 5: 跑測試 + 回歸(既有上傳/下載測試不能壞)**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_attachment_upload_thumb.py tests/test_attachment_download.py tests/test_storage_download.py tests/test_announcements_api.py -v`
Expected: 新測試 PASS;既有測試若有 assert `upload_attachment` 回單值處需一併更新(grep `upload_attachment(` 找呼叫、確認都已 unpack)。

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/storage.py app_unified/notes/routes.py \
        app_unified/notes/announcement_routes.py app_unified/mynotes/routes.py \
        app_unified/tests/test_attachment_upload_thumb.py
git commit -m "feat(attachments): upload_attachment returns (object_key, thumb_key); gen thumb via threadpool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 序列化加 `thumb_url`

**Files:**
- Modify: `app_unified/notes/routes.py`（`_checklist_item_json` L1648;`list_attachments` L1984;`upload_attachment_api` 回應 L1950）
- Modify: `app_unified/notes/announcement_routes.py`（L78、L322、L343）
- Modify: `app_unified/mynotes/routes.py`（`_attachment_json` ~L102）
- Test: `app_unified/tests/test_attachment_thumb_url.py`

**Interfaces:**
- Produces:所有 attachment JSON 多一欄 `thumb_url`（`get_signed_url(thumb_key)` 或 `None`）。

- [ ] **Step 1: 寫 failing test**

```python
# app_unified/tests/test_attachment_thumb_url.py
"""attachment JSON 含 thumb_url:有 thumb_key → 簽 URL;無 → None。"""
from unittest.mock import patch


def test_checklist_item_json_includes_thumb_url(app, db_session):
    from models import Note, ChecklistItem, NoteAttachment
    from notes.routes import _checklist_item_json
    n = Note(note_type="checklist", title="t", content="", store="A",
             status="active", priority="medium")
    db_session.add(n); db_session.commit()
    it = ChecklistItem(note_id=n.id, order_index=0, text="x", is_checked=False)
    db_session.add(it); db_session.commit()
    a = NoteAttachment(note_id=n.id, checklist_item_id=it.id, object_key="k.jpg",
                       thumb_key="k_thumb.jpg", filename="p.jpg",
                       content_type="image/jpeg", file_size=1)
    db_session.add(a); db_session.commit()

    with app.app_context():
        with patch("storage.get_signed_url", side_effect=lambda key, **kw: f"signed::{key}"):
            j = _checklist_item_json(it)
    att = j["attachments"][0]
    assert att["thumb_url"] == "signed::k_thumb.jpg"
    assert att["url"] == "signed::k.jpg"


def test_thumb_url_none_when_no_thumb_key(app, db_session):
    from models import Note, ChecklistItem, NoteAttachment
    from notes.routes import _checklist_item_json
    n = Note(note_type="checklist", title="t", content="", store="A",
             status="active", priority="medium")
    db_session.add(n); db_session.commit()
    it = ChecklistItem(note_id=n.id, order_index=0, text="x", is_checked=False)
    db_session.add(it); db_session.commit()
    a = NoteAttachment(note_id=n.id, checklist_item_id=it.id, object_key="v.mp4",
                       thumb_key=None, filename="v.mp4",
                       content_type="video/mp4", file_size=1)
    db_session.add(a); db_session.commit()

    with app.app_context():
        with patch("storage.get_signed_url", side_effect=lambda key, **kw: f"signed::{key}"):
            j = _checklist_item_json(it)
    assert j["attachments"][0]["thumb_url"] is None
```

> 註:`_checklist_item_json` 與 `_thumb_url` 都是函式內 `from storage import get_signed_url`,所以測試 patch **`storage.get_signed_url`**(如上)才有效,不能 patch `notes.routes.get_signed_url`。

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_attachment_thumb_url.py -v`
Expected: FAIL — `KeyError: 'thumb_url'`

- [ ] **Step 3: 加 `thumb_url` — 定義小 helper**

在 `notes/routes.py` 適當位置(靠近 `_checklist_item_json`)加:

```python
def _thumb_url(att):
    from storage import get_signed_url
    return get_signed_url(att.thumb_key) if att.thumb_key else None
```

`_checklist_item_json` 的 attachments dict(L1648）每個 attachment 加一行:

```python
        "attachments": [{
            "id": a.id,
            "filename": a.filename,
            "content_type": a.content_type,
            "file_size": a.file_size,
            "url": get_signed_url(a.object_key),
            "thumb_url": _thumb_url(a),
        } for a in item.attachments],
```

`list_attachments`(L1984 迴圈)與 `upload_attachment_api` 回應(L1950)同樣加 `"thumb_url": _thumb_url(a)`(upload 回應用剛建立的 `attachment`,可直接 `get_signed_url(thumb_key) if thumb_key else None`）。

- [ ] **Step 4: announcement + mynotes 序列化同步加**

- `notes/announcement_routes.py` L78/L322/L343 每個 attachment dict 加 `"thumb_url": get_signed_url(att.thumb_key, expires_in=3600) if att.thumb_key else None`。
- `mynotes/routes.py` `_attachment_json`(~L102)加 `"thumb_url": get_signed_url(att.thumb_key) if att.thumb_key else None`。

- [ ] **Step 5: 跑測試 + 回歸**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_attachment_thumb_url.py tests/test_announcements_api.py tests/test_notes.py -v 2>&1 | tail -15`
Expected: 新測試 PASS,既有不壞。

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/notes/routes.py app_unified/notes/announcement_routes.py \
        app_unified/mynotes/routes.py app_unified/tests/test_attachment_thumb_url.py
git commit -m "feat(attachments): expose thumb_url in all attachment JSON

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 前端 — 列表用縮圖 + lazy load

**Files:**
- Modify: `app_unified/templates/notes/editor.html`（`img.src` 各處:L1150、L1317、L1614）
- Modify: `app_unified/templates/notes/index.html`（若有 attachment 縮圖 render 處;經查列表本身無 `<img>`,以 editor 為主）
- Modify: mynotes / announcement 對應 template（grep `att.url` / `\.url`）
- (可能) Modify: `app_unified/static/js/fullscreen_viewer.js` + `sw.js`（僅在動到 static js 時）

**Interfaces:**
- Consumes:attachment JSON 的 `thumb_url` / `url`（Task 4）。

- [ ] **Step 1: grep 出所有 attachment `<img>` render 點**

```bash
cd /home/hirain0126/projects/webapp/app_unified
grep -n "\.src *= *att\.url\|\.src *= *a\.url\|\.src *= *url\|att\.url\|a\.url" \
  templates/notes/editor.html templates/mynotes/*.html templates/notes/*.html
```
列出全部縮圖 `<img>`（非 fullscreen 開啟用的 URL）。

- [ ] **Step 2: 縮圖 `<img>` 改用 thumb_url + lazy**

`templates/notes/editor.html` 的縮圖建立處（例 L1149-1150）:

```javascript
    var img = document.createElement('img');
    img.loading = 'lazy';
    img.src = att.thumb_url || att.url;    // 縮圖優先,舊圖/影片 fallback 原圖
    img.alt = att.filename;
    window.markGalleryItem(img, att, _notesDownloadHref(att));
    img.onclick = function() {
      window.openGalleryFromEl(img);       // 開全螢幕仍用原圖(見 Step 3)
    };
```

其餘縮圖 render 點（L1316 待上傳 preview、L1613 note-level 附件、mynotes/announcement 對應處）同樣加 `img.loading='lazy'` + `img.src = att.thumb_url || att.url`。
**影片 `<video>` 不動**（無 thumb 概念）。

- [ ] **Step 3: 確認全螢幕 gallery 用原圖(url 非 thumb)**

檢查 `static/js/fullscreen_viewer.js` 的 `markGalleryItem` / `openGalleryFromEl`:全螢幕檢視取的是 `att.url`(原圖),不是縮圖。

```bash
grep -n "url\|thumb\|src\|dataset" static/js/fullscreen_viewer.js | head -20
```

- 若 `markGalleryItem(el, att, ...)` 內部是用 `att.url` 建 fullscreen slide → **不用改**(縮圖 `<img>` 的顯示 src 是 thumb,但 fullscreen 從 att.url 開)。確認即可。
- 若它讀的是 `el.src`(=縮圖 URL)→ 需改成優先讀 `att.url`。此時**動到 static js → 必 bump `sw.js` CACHE_NAME**(見 Global Constraints)。

- [ ] **Step 4: (條件)bump SW**

僅在 Step 3 動到 `static/js/fullscreen_viewer.js` 時:`sw.js` 的 `const CACHE_NAME = 'xxx-vNN'` 版號 +1。純 editor.html inline 改動不用。

- [ ] **Step 5: 本機手動驗證**

```bash
cd /home/hirain0126/projects/webapp && bash app_unified/scripts/run_local.sh
```
1. 進一個 checklist 筆記、上傳一張大圖 → DevTools Network 看列表縮圖請求 size(應 ~數十 KB,非數 MB)。
2. 點縮圖開全螢幕 → 載入的是壓縮原圖(≤3200)、清晰。
3. 上傳 gif / mp4 → 仍正常顯示(fallback 原圖 / video)。
4. 往下捲 → 未進視窗的圖不預先下載(lazy 生效,Network 逐張出現)。

- [ ] **Step 6: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/templates/ app_unified/static/js/fullscreen_viewer.js app_unified/sw.js
git commit -m "feat(attachments): list uses thumb_url + loading=lazy; fullscreen keeps full image

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
（未動 static js/sw 則只 add templates。）

---

## Task 6: Backfill script — 舊圖補縮圖(不動原圖)

**Files:**
- Create: `app_unified/scripts/backfill_thumbnails.py`
- Test: `app_unified/tests/test_backfill_thumbnails.py`

**Interfaces:**
- Consumes:`image_utils.process_upload_image` / `thumb_key_for`（Task 1）。
- Produces:`backfill_one(content_type, object_key, get_bytes, put_bytes) -> str|None`（回 thumb_key 或 None）— 可測純邏輯;`main()` 走 psycopg2 + boto3。

- [ ] **Step 1: 寫 failing test（測核心邏輯,不碰真 DB/R2）**

```python
# app_unified/tests/test_backfill_thumbnails.py
import io
from PIL import Image


def _jpeg(w=3000, h=1500):
    img = Image.new("RGB", (w, h), (5, 5, 5))
    buf = io.BytesIO(); img.save(buf, format="JPEG"); return buf.getvalue()


def test_backfill_one_image_produces_thumb():
    from scripts.backfill_thumbnails import backfill_one
    puts = {}
    tk = backfill_one("image/jpeg", "attachments/1/a.jpg",
                      get_bytes=lambda k: _jpeg(),
                      put_bytes=lambda k, b: puts.__setitem__(k, b))
    assert tk == "attachments/1/a_thumb.jpg"
    assert tk in puts
    assert Image.open(io.BytesIO(puts[tk])).format == "JPEG"


def test_backfill_one_video_skipped():
    from scripts.backfill_thumbnails import backfill_one
    puts = {}
    tk = backfill_one("video/mp4", "attachments/1/v.mp4",
                      get_bytes=lambda k: b"x", put_bytes=lambda k, b: puts.__setitem__(k, b))
    assert tk is None and not puts
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_backfill_thumbnails.py -v`
Expected: FAIL — module 不存在

- [ ] **Step 3: 實作 `scripts/backfill_thumbnails.py`**

```python
#!/usr/bin/env python3
"""Backfill 附件縮圖:對既有 thumb_key IS NULL 的圖片附件補產縮圖。
**不覆寫既有原圖**。冪等(已有 thumb_key 的跳過)。

用法(prod / 本機):
    R2_ENDPOINT_URL=... R2_BUCKET_NAME=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
    DATABASE_URL=postgresql://... python scripts/backfill_thumbnails.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from image_utils import process_upload_image, thumb_key_for, PROCESSABLE_TYPES

TABLES = ("note_attachments", "private_attachments", "announcement_attachments")


def backfill_one(content_type, object_key, get_bytes, put_bytes):
    """回 thumb_key(已上傳)或 None(非圖/失敗)。純邏輯,IO 由 callback 注入。"""
    if content_type not in PROCESSABLE_TYPES:
        return None
    raw = get_bytes(object_key)
    _main, thumb = process_upload_image(raw, content_type)
    if thumb is None:
        return None
    tk = thumb_key_for(object_key)
    put_bytes(tk, thumb)
    return tk


def main():
    import psycopg2
    import boto3

    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

    def get_bytes(key):
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    def put_bytes(key, body):
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="image/jpeg")

    conn = psycopg2.connect(dsn)
    total = done = 0
    for tbl in TABLES:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, object_key, content_type FROM {tbl} "
            f"WHERE thumb_key IS NULL AND content_type LIKE 'image/%'"
        )
        rows = cur.fetchall()
        print(f"[{tbl}] {len(rows)} 筆待處理", flush=True)
        for aid, object_key, content_type in rows:
            total += 1
            try:
                tk = backfill_one(content_type, object_key, get_bytes, put_bytes)
            except Exception as e:
                print(f"  ! {tbl}#{aid} {object_key}: {e}", flush=True)
                continue
            if tk:
                up = conn.cursor()
                up.execute(f"UPDATE {tbl} SET thumb_key=%s WHERE id=%s", (tk, aid))
                conn.commit()
                done += 1
                if done % 20 == 0:
                    print(f"  ...{done} 完成", flush=True)
    conn.close()
    print(f"完成:{done}/{total} 補上縮圖", flush=True)


if __name__ == "__main__":
    main()
```

> 需 `scripts/__init__.py` 存在才能 `from scripts.backfill_thumbnails import ...`;若無則新增空檔。

- [ ] **Step 4: 跑測試確認 pass**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/test_backfill_thumbnails.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/scripts/backfill_thumbnails.py app_unified/tests/test_backfill_thumbnails.py app_unified/scripts/__init__.py
git commit -m "feat(attachments): backfill script — add thumbnails to existing images (non-destructive)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 全套測試 + 最終驗證

- [ ] **Step 1: 全套測試**

Run: `cd /home/hirain0126/projects/webapp/app_unified && python -m pytest tests/ 2>&1 | tail -15`
Expected: 全綠(基準 725 passed + 1 skipped,新增測試後數字上升,無新失敗)。

- [ ] **Step 2: 本機整合驗證(run_local.sh)**

依 Task 5 Step 5 的 4 項 + 額外:
- 上傳前後比對 R2 是否多出 `*_thumb.jpg`。
- 舊圖(reseed 前既有)在跑 backfill 後列表也載縮圖。

- [ ] **Step 3: 本機跑一次 backfill(對 dev 資料)**

```bash
cd /home/hirain0126/projects/webapp/app_unified
# dev 若用 SQLite,backfill main() 走 psycopg2 不適用 → 本機驗證以「新上傳產縮圖」為主;
# backfill 邏輯已由 test_backfill_thumbnails.py 單元覆蓋。prod 才實跑 main()。
python -m pytest tests/test_backfill_thumbnails.py -v
```

- [ ] **Step 4: 列出更新後驗證步驟給 user(依 feedback_relist_test_steps)**

整理本機 link + 逐項驗證步驟,交 user 本機驗 OK 後才談 merge/push(依 feedback_branch_workflow / feedback_local_test_before_push)。

---

## 上線注意(交付 user 後)

- **不 bump SW**(除非 Task 5 Step 4 動到 static js)。
- **merge / push 等 user 明說**(feedback_branch_workflow)。prod 部署後**先跑 backfill**(env 帶 prod R2 + DATABASE_URL,見 script docstring;依 feedback_prod_repl_psycopg2 用 psycopg2 直連,不觸發 create_app schema race)。
- prod 尖峰(台灣 19:30–21:00)禁 push/deploy(feedback_push_peak_hours)。
- backfill 對既有原圖**只補縮圖、不覆寫**,可安全重跑(冪等)。
