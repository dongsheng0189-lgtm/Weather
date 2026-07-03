# 附件縮圖 + 上傳壓縮 設計 (Attachment Thumbnails + Upload Compression)

**日期:** 2026-07-03
**狀態:** 待 user 審核

## 問題 (Root Cause)

使用者反應「讀取圖片非常慢」,主要在筆記/確認表單。系統性除錯查出三個疊加根因:

| | 問題 | 位置 |
|---|---|---|
| A(主因) | 沒有縮圖,列表直接載**全解析度原圖**,只用 CSS 縮成 200×150 顯示 | `editor.html:1149` `img.src = att.url`;`storage.py:upload_attachment()` 原檔原封不動上傳,`MAX_FILE_SIZE=50MB` |
| A2 | `<img>` 沒有 `loading="lazy"`,整份表單所有圖一次全下載 | `editor.html` 各 `img.src` |
| B | presigned URL 每次重簽、指向 R2 S3 endpoint(`*.r2.cloudflarestorage.com`),CDN/瀏覽器都不快取 | `storage.py:get_signed_url()` |

一張 5MB 手機照為了顯示 200px 縮圖把整個 5MB 下載;確認表單多個項目各帶照片 = 幾十 MB 一次灌下來。

## 決策 (User 拍板)

1. **範圍:** 縮圖 + lazy-load。**不做 B 快取**(縮圖把單張 5MB→~80KB,就算重載也差 50–100 倍,B 的痛幾乎消失;B 牽涉店務照片隱私,另評估)。
2. **既有舊圖:** 寫 backfill 一次性補產縮圖。
3. **上傳壓縮:** 新上傳的原圖壓縮,長邊上限 2048px(此為 user 離開時的推薦預設,待 user 確認)。

## 架構

### 1. 影像處理模組 `storage/image_utils.py`(新)

單一職責:純函式,輸入影像 bytes + content_type,輸出處理後的 bytes。不碰 R2、不碰 DB。

```
process_upload_image(raw_bytes, content_type) -> (main_bytes, thumb_bytes)
    # 只處理 jpeg/png/webp。gif / video / 其他 → 回 (raw_bytes, None)(不處理)
    # main_bytes:  長邊 > 2048 → 縮到 2048;re-encode(JPEG q85 / PNG optimize / WebP q85)
    #             honor EXIF orientation(ImageOps.exif_transpose)
    # thumb_bytes: 長邊縮到 640;一律輸出 JPEG q78(PNG 透明先 flatten 白底)
```

常數(可調):`MAX_LONG_EDGE = 2048`、`THUMB_LONG_EDGE = 640`、`MAIN_QUALITY = 85`、`THUMB_QUALITY = 78`。

**容量 / gevent:** Pillow resize 是 CPU-heavy native code,依 [[feedback_gevent_monkey_threadpool]] 必須走 `gevent.threadpool`(比照 `face/routes.py` dlib 的既有做法),不可直接在 request greenlet 跑而卡 hub。上傳非高頻(店員偶爾傳照),2 CPU / 8GB 容量無虞。

### 2. `storage.py:upload_attachment()` 改動

上傳流程:
1. 判斷是否可處理(靜態圖)→ 呼叫 `process_upload_image()` 拿到 `main_bytes` + `thumb_bytes`。
2. `put_object` 上傳 `main_bytes` 到 `object_key`(維持現有 key 規則)。
3. 若有 `thumb_bytes`,`put_object` 上傳到 `<object_key>.thumb.jpg`(或同 prefix 下 `thumbs/<uuid>.jpg`),回傳給呼叫端。
4. 回傳改為 `(object_key, thumb_key)`(thumb_key 可能 None)。

3 個呼叫端(`notes/routes.py:1926`、`notes/announcement_routes.py:298`、`mynotes/routes.py:401`)同步接新回傳,把 thumb_key 寫進各自 attachment row。

### 3. Schema:3 張表各加 `thumb_key`

嚴守 [[feedback_inline_schema_repair]](🚨 BLOCKING,prod 不跑 alembic、走 app.py inline ALTER,漏一處 prod 100% ISE):

- `models.py`:`NoteAttachment` / `PrivateAttachment` / `AnnouncementAttachment` 各加
  `thumb_key = db.Column(db.Text, nullable=True)`
- `app.py` inline schema repair:3 張表各加 `ADD COLUMN IF NOT EXISTS thumb_key TEXT`
- `migrations/`:新 migration(對齊 dev / 文件)

`nullable=True`:舊 row / 無法產縮圖的附件(gif/影片)→ null → 前端 fallback `object_key`。

⚠️ 本機 dev SQLite 不會自動同步(見 [[feedback_dev_sqlite_alter]]),需手動 ALTER 或 rm dev.db reseed。

### 4. 序列化:加 `thumb_url`

各 attachment JSON 加一欄:`thumb_url = get_signed_url(a.thumb_key) if a.thumb_key else None`(null 時**不**二次簽章,前端一律 `att.thumb_url || att.url` fallback)。
影響點:
- `notes/routes.py`:`_checklist_item_json`(1648)、`list_attachments`(1984)、upload 回應(1957)
- `notes/announcement_routes.py`:78 / 322 / 343
- `mynotes/routes.py`:102 附近

### 5. 前端

- **列表/編輯器縮圖**(`editor.html:1149`、1316、1613 等 `img.src`):
  `img.src = att.thumb_url || att.url;` + `img.loading = 'lazy';`
- **全螢幕 gallery**:開啟時仍用 `att.url`(壓縮後 2048 原圖),看大圖清晰。`markGalleryItem` 縮圖 grid 用 thumb、fullscreen 用 url。
- 若動到 `/static/js/fullscreen_viewer.js`(swipe gallery 共用元件),**必 bump SW CACHE_NAME**(踩雷 #5)。純 `editor.html` inline script 改動不 bump(network-first)。

### 6. Backfill(既有舊圖)

`scripts/backfill_thumbnails.py`(一次性,prod 用 psycopg2 直連或 REPL,見 [[feedback_prod_repl_psycopg2]]):

- 掃所有 attachment(3 表)where `thumb_key IS NULL` and content_type 屬靜態圖。
- 從 R2 下載 `object_key` → `process_upload_image()` 取 `thumb_bytes` → 上傳 thumb → 寫回 `thumb_key`。
- **只補縮圖,不動既有原圖**(不覆寫既有 R2 物件、不 re-encode,避免破壞性操作與資料遺失)。→ 壓縮(2048)只套用在**新上傳**;舊圖維持原圖 + 新增一張縮圖。列表(主訴)舊圖也會變快;舊圖的全螢幕仍載大原圖(可接受,非主訴)。
- 冪等:重跑跳過已有 thumb_key 的。分批 commit、印進度。

## 資料流

```
上傳: file → upload_attachment → process_upload_image → put(main) + put(thumb)
                                                       → row.object_key + row.thumb_key
讀取(列表): API → thumb_url(get_signed_url thumb_key) → <img loading=lazy src=thumb_url>
讀取(點開): gallery → url(get_signed_url object_key, 壓縮後 2048) → 全螢幕
Backfill: 舊 row(thumb_key null) → 下載 object_key → 產 thumb → put + 寫 thumb_key
```

## 錯誤處理

- `process_upload_image` 任何 Pillow 例外(壞檔/不支援)→ 記 log、fallback 回 `(raw_bytes, None)`,上傳不中斷(附件仍存,只是沒縮圖)。
- thumb put_object 失敗 → thumb_key 留 None,前端 fallback 原圖,不擋主上傳。
- 前端 `att.thumb_url || att.url` 保證永遠有 src。

## 測試

- **`tests/test_image_utils.py`**:resize 尺寸正確、長邊 ≤2048 不放大、thumb 長邊 640、gif/影片/非圖 回 (raw, None)、EXIF 方向、壞 bytes 不 raise。
- **上傳整合測試**:upload image → row.thumb_key 有值、main 被壓縮;upload gif → thumb_key None。
- **序列化測試**:attachment JSON 含 thumb_url;thumb_key null → thumb_url == url(或 None,擇一,前端都 fallback)。
- **backfill 測試**:null thumb_key 的舊圖跑完有 thumb_key、原 object_key 不變;冪等重跑不重產。
- 全套 `pytest tests/` 綠(現 725 passed + 1 skipped)。

## 不做 (YAGNI / 另評估)

- **B 快取**:presigned 長效期 / R2 公開網域 / CDN。縮圖已解主痛,B 牽涉店務照片隱私,獨立評估。
- **既有原圖 re-compress**:破壞性,不在 backfill 做。
- **影片轉碼/縮圖**:worker 無法負擔 ffmpeg,跳過。
- **on-the-fly 縮圖 endpoint**:會把 CPU + 頻寬壓在 2-worker 上,違反容量原則,不採用。

## 上線 / 驗證

- 本機:`bash app_unified/scripts/run_local.sh` → 上傳圖 → 確認列表載縮圖(DevTools Network 看 size)、點開載壓縮原圖、gif/影片仍正常。
- backfill 本機先跑過再 prod。
- 依 [[feedback_branch_workflow]] 開 branch、[[feedback_local_test_before_push]] user 驗 OK 才 push。
