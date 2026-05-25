# Announcement Media Viewer — Spec

**Branch**: `feat/announcement-media-viewer`（chain on `feat/notes-editor-bigger`）
**Date**: 2026-05-25
**Scope**: 把 `notes/editor.html` 內 ~154 行 inline fullscreen viewer JS + 6 行 CSS 抽成共用 module、announcement 三個 page (edit / new / _card_list 透過 index) 對齊行為。純 frontend、無 backend / schema 變動。

## 問題

公告 (`/announcements/...`) 的圖片附件 viewer 跟筆記/確認表單 (`/notes/...`) 行為不一致：

| 行為 | 公告（`static/js/announcement_attachments.js`）| 筆記/確認表單（`templates/notes/editor.html`）|
|---|---|---|
| ✕ 關閉按鈕 | 無（點任意處關）| 有 44x44 ✕ 按鈕 |
| 雙指 pinch zoom | 無 | 有 |
| 雙擊放大 | 無 | 有 |
| 拖曳移動 | 無 | 有 |
| 滑鼠 wheel zoom | 無 | 有 |
| Hint 文字 | 無 | 有「雙指縮放 · 雙擊放大 · 拖曳移動」|
| 影片 viewer | HTML5 controls + iOS 原生 fullscreen | 同上（兩邊已一致、不動）|

User 要求公告對齊筆記行為。

## Root Cause

5/14 那輪實作 announcement attachments 時、`static/js/announcement_attachments.js` 自己寫了一個簡陋 `openFullscreen`（11-21 行），沒複用 notes editor 的完整 viewer。完整版只在 `notes/editor.html` inline、無法重用。

## Design

### File 結構

```
NEW:
  static/js/fullscreen_viewer.js    ~154 行（抽自 notes/editor.html line 914-1067）
  static/css/fullscreen_viewer.css  ~6 行（抽自 notes/editor.html line 59-64）

MODIFY:
  templates/notes/editor.html              移除 inline CSS/JS/HTML overlay markup、加 <link>/<script>
  templates/notes/index.html               加 <link>/<script>（讓 _card_list partial 包含的 viewer 也能用）
  templates/announcements/edit.html        加 <link>/<script>
  templates/announcements/new.html         加 <link>/<script>
  static/js/announcement_attachments.js    移除 line 11-21 的簡陋 openFullscreen、相依 shared 版（透過 window.openFullscreen）
```

### Public API（保持 backwards compatible）

```js
window.openFullscreen(url: string): void   // 顯示 image fullscreen + ✕ + zoom + drag
window.closeFullscreen(): void             // 關閉 fullscreen
```

第一次呼叫 `openFullscreen()` 時自動 `_fsInit()`：
1. 檢查 `document.body` 是否已有 `#fullscreen-overlay`（idempotent、避免多次 init 重複 append）
2. 沒有則 dynamically `appendChild` overlay markup：
   ```html
   <div class="fullscreen-overlay" id="fullscreen-overlay">
     <div class="fs-stage" id="fs-stage">
       <img id="fullscreen-img" src="">
     </div>
     <button type="button" class="fs-close" onclick="closeFullscreen()" aria-label="關閉">✕</button>
     <div class="fs-hint">雙指縮放 · 雙擊放大 · 拖曳移動</div>
   </div>
   ```
3. 綁定 pointer / wheel / dblclick events 在 `#fs-stage`（用 `stage._fsBound = true` flag 避免重綁）

兩邊 page 都不用 inline markup、JS 自動處理。

### Internal helpers（搬到 fullscreen_viewer.js 內、module-scoped）

從 notes/editor.html line 914-1067 整段搬：

- State: `_fsState`, `_fsPointers`, `_fsLastDist`, `_fsLastCenter`, `_fsLastTap`
- `_fsApply()` 把 state 套到 img transform
- `_fsReset()` 歸位
- `_fsClampPan()` 邊界 clamp
- Event handlers: `_fsOnPointerDown` / `_fsOnPointerMove` / `_fsOnPointerUp` / `_fsOnDblClick` / `_fsOnWheel`
- `_fsInit()`（加 idempotent + dynamic markup create）

### `announcement_attachments.js` 改動

```diff
-function openFullscreen(url) {
-  var overlay = document.createElement('div');
-  overlay.style.cssText = '...rgba(0,0,0,.92)...';
-  var img = document.createElement('img');
-  img.src = url;
-  img.style.cssText = 'max-width:95vw;max-height:95vh;';
-  overlay.appendChild(img);
-  overlay.onclick = function() { document.body.removeChild(overlay); };
-  document.body.appendChild(overlay);
-}
```

刪除整個 function — `_renderMedia` (line 32) 仍呼叫 `openFullscreen(att.url)`、現在解析到 shared 版（`window.openFullscreen`）。

### Template `<head>` / `<body>` 改動

每個 page block 加：

```html
<!-- 在 base.html block 內加 -->
<link rel="stylesheet" href="{{ url_for('static', filename='css/fullscreen_viewer.css') }}">
```

```html
<!-- body 末或 scripts block 加（一定要在用到 openFullscreen 的 inline script 之前）-->
<script src="{{ url_for('static', filename='js/fullscreen_viewer.js') }}"></script>
```

`notes/editor.html` 順序：先 viewer.js、再 inline JS（後者 `img.onclick = openFullscreen`）
`announcements/edit.html` + `new.html` 順序：先 viewer.js、再 announcement_attachments.js

## 邊界情境

| 情境 | 處理 |
|---|---|
| 多次 `openFullscreen` 連續呼叫 | `_fsInit()` idempotent、不會重複 append markup 也不會重綁 events |
| `closeFullscreen()` 在 init 前呼叫 | overlay 不存在、`getElementById` 拿到 null、加 `if (!overlay) return;` 守住 |
| 兩個 page 都載入 viewer.js（SPA-like navigation 不會發生、但保險）| 同一 page lifetime 只有一個 overlay、document.getElementById 拿同一個 |
| `_card_list.html` partial 被 include 但 `notes/index.html` 沒加 script | viewer 不 work；要明確在 `notes/index.html` 加 `<script>` |
| 影片附件 | 不動、保留 HTML5 controls + iOS 原生 fullscreen |
| 圖片 url 含特殊字元 | `img.src = url` 不轉義、由 browser 處理；既有行為一樣 |
| 公告 `_card_list.html` 唯讀展示（無刪除）| `renderAnnAttView` line 81-85 呼叫 `_renderMedia` → 包含 img.onclick openFullscreen、shared viewer 接到 |

## Capacity

- 純 frontend、0 prod CPU/RAM/PG 影響
- Browser 第一次抓 shared JS+CSS、cache 後跨 page 重用
- notes editor inline 砍 160 行 → 整體 transfer size 略降（cache 命中後尤其）

## 測試策略

### Pytest markup smoke test（新）

`tests/test_shared_fullscreen_viewer.py`：

驗 5 個 page render 都含 shared viewer script + css link：

- `/notes/` (index、含 _card_list)
- `/notes/<id>/edit`（既有 note 的編輯頁）
- `/announcements/edit/<id>`（既有公告編輯）
- `/announcements/new`（新增公告）
- 也驗 notes/editor.html 已**移除**舊 inline marker（例如 `id="fullscreen-overlay"` 不再 inline 出現）

### 既有 test 影響

`openFullscreen` / `closeFullscreen` 名稱不變、notes editor 行為不變（透過 shared viewer 走同一 API）→ 既有 notes editor test 全綠。

### Manual verification（按 [[feedback_local_test_before_push]]）

1. `bash scripts/run_local.sh` → `/dev` 切 admin
2. `/notes/<某 note 含圖片>/edit` → 點圖片 → 驗 fullscreen viewer ✕ / pinch / dbltap / drag 全部 work
3. `/announcements/edit/<某公告含圖片>` → 點圖片 → 驗同上行為（之前沒有的）
4. `/announcements/new` 上傳圖片 → 點預覽 → 驗 viewer 行為
5. `/notes/` index 的公告卡片區 → 點公告圖片 → 驗 viewer 行為
6. 影片附件：點影片 → 驗 HTML5 controls 仍 work、iOS 上原生 fullscreen 仍可用

## YAGNI 排除

- 不做影片自製 fullscreen overlay（user confirmed 保留原生）
- 不重命名 `openFullscreen`/`closeFullscreen`（保 backward compat）
- 不抽 attachment 其他 logic（scope creep）
- 不加新 UI（按鈕 / 動畫 / 多圖切換 swipe gallery）
- 不寫 JS unit test（無 framework；行為驗證走 manual flow）

## 部署

純 frontend、無 schema migration、無 dependency 變動。GHA Docker build 完成後 Zeabur Redeploy。
