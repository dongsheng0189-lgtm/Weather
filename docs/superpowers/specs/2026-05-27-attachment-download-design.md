# Attachment Download — 同店員工下載原檔

**Date**: 2026-05-27
**Status**: Draft, awaiting user review
**Scope**: 店務筆記附件 (`NoteAttachment`) + 公告附件 (`AnnouncementAttachment`)

## Problem

目前所有照片/影片附件只能在 fullscreen viewer 內檢視（透過 1 小時 presigned URL）。
使用者實際工作場景需要「下載原檔保存」— 例如要把現場照存進手機相簿、用 LINE 傳給總公司、彙整到 PDF 報表等。viewer 雖然技術上可長按存圖，但行為依瀏覽器/裝置而異、影片完全無法存。需要一個明確、可預期的「下載原檔」操作。

## Scope

| 覆蓋 | 不覆蓋 |
|---|---|
| 店務筆記附件（`NoteAttachment`、含確認表單） | 私人筆記附件（`PrivateAttachment`） |
| 公告附件（`AnnouncementAttachment`） | 跨店分享 |
| 同店員工 + super_admin（沿用既有 view 權限） | 對外公開連結（無登入下載） |
| 照片 + 影片 + 其他類型 | 批量 zip 匯出 |

未來如果要做以上「不覆蓋」項目，是另一個 spec 範圍。

## Architecture

### 高層流程

```
User tap 縮圖 ⬇️ icon (or viewer ⬇️ button)
    ↓
Frontend GET /api/attachments/<id>/download
    ↓
Flask: 跑既有 view 權限檢查 (login_required + 同店/super_admin)
    ↓
boto3 generate_presigned_url(
    'get_object',
    Params={
        'Bucket': R2_BUCKET,
        'Key': att.object_key,
        'ResponseContentDisposition': f'attachment; filename*=UTF-8\'\'{rfc5987(att.filename)}',
        'ResponseContentType': att.content_type,
    },
    ExpiresIn=300,  # 5 分鐘
)
    ↓
回傳 JSON: {"url": "https://r2.../...?...&response-content-disposition=..."}
    ↓
Frontend window.location.assign(url) 或建立隱形 <a download> 點擊
    ↓
Browser 直接從 R2 下載（不經 Flask）
```

### 為什麼用 presigned URL + Content-Disposition、不用 Flask streaming proxy

Zeabur prod = 2 CPU / 8GB RAM、gunicorn workers=2（gevent）。
若 Flask 開 stream 從 R2 拉檔再 chunk 給 client，單支 30MB 影片就佔 1 個 worker 數秒到數十秒。同時 2 個下載就剩 0 worker 給其他 request、會卡服務（按 [[feedback_capacity_check]] 跟 [[feedback_in_memory_state_workers]] 的容量教訓）。

R2 原生支援 `response-content-disposition` query parameter 覆蓋 Content-Disposition header — 等同於告訴 browser「這支檔請存成 xxx.jpg、不要 inline 顯示」。Flask 完全不參與檔案流量、worker 立刻釋放、Cloudflare R2 直送、邊緣 CDN 也吃得到。

## Components

### 1. Backend endpoints

```
GET /api/attachments/<int:att_id>/download
    blueprint: notes (notes/routes.py)
    auth: @login_required + 既有 NoteAttachment view 權限（同 store / super_admin）
    response: {"url": <presigned_url>, "expires_in": 300}

GET /announcement/api/attachments/<int:att_id>/download
    blueprint: notes (notes/announcement_routes.py)
    auth: @login_required + 既有 AnnouncementAttachment view 權限
    response: {"url": <presigned_url>, "expires_in": 300}
```

兩個 endpoint 共用一個 helper：

```python
# storage.py 新增 helper
def get_download_url(object_key: str, filename: str, content_type: str,
                    expires_in: int = 300) -> str:
    """Presigned URL with Content-Disposition: attachment override."""
    # RFC 5987 encode 中文檔名
    encoded = urllib.parse.quote(filename, safe="")
    return _get_client().generate_presigned_url(
        'get_object',
        Params={
            'Bucket': R2_BUCKET_NAME,
            'Key': object_key,
            'ResponseContentDisposition': (
                f"attachment; filename*=UTF-8''{encoded}"
            ),
            'ResponseContentType': content_type or 'application/octet-stream',
        },
        ExpiresIn=expires_in,
    )
```

### 2. Frontend — 縮圖 download icon overlay

在現有 attachment grid（`templates/notes/editor.html`、`templates/announcements/edit.html`、`templates/announcements/new.html` 跟 attachment chip 渲染 JS）的每張縮圖右上角加一個小 ⬇️ icon button：

- 大小：32×32 px、tap target 44×44 px（行動裝置）
- 樣式：半透明黑底圓（`rgba(0,0,0,0.5)`）+ 白色 icon、跟既有 fullscreen icon 同風格
- 行動裝置永遠顯示、桌面 hover 時亮起
- 點擊區用 `e.stopPropagation()` 阻止冒泡開啟 fullscreen viewer

```html
<div class="attachment-thumb" data-att-id="123">
  <img src="..." />
  <button class="att-download-btn" title="下載原檔" aria-label="下載原檔">
    <svg>...</svg>
  </button>
</div>
```

### 3. Frontend — fullscreen viewer download button

在 `static/js/fullscreen_viewer.js` 的 toolbar 左下角加一個 download button：

- icon 跟縮圖上的一致
- 點擊行為跟縮圖 icon 共用一個 JS function

### 4. 共用 download trigger function

```javascript
// 縮圖 icon + viewer button 都呼叫這個
async function downloadAttachment(attId, endpointPrefix) {
  // endpointPrefix = '/api/attachments' (notes) or '/announcement/api/attachments'
  try {
    const res = await fetch(`${endpointPrefix}/${attId}/download`, {
      credentials: 'same-origin'
    });
    if (!res.ok) {
      showToast('下載失敗，請稍後再試');
      return;
    }
    const { url } = await res.json();
    // 用隱形 <a download> 點擊觸發、不污染目前頁面導覽歷史
    const a = document.createElement('a');
    a.href = url;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } catch (err) {
    showToast('下載失敗：' + err.message);
  }
}
```

### 5. Loading 視覺

- 點下 icon → 把 icon 換成 spinner 1 秒（用既有 spinner css）→ recover icon
- 太短的 loading 不顯示也 OK（fetch presigned URL 通常 < 200ms）

## Data flow

1. User 點縮圖右上 ⬇️ icon
2. JS 攔截 click event、`stopPropagation()` 阻止冒泡開啟 viewer
3. JS 發 fetch `GET /api/attachments/123/download`
4. Flask 驗證：login + 該 attachment 所屬 note 的 store 規則
5. Flask 用 boto3 `generate_presigned_url` 產生 5-min URL（帶 Content-Disposition override）
6. Flask 回傳 `{"url": "..."}`
7. JS 用隱形 `<a download>` 觸發 browser download
8. Browser 從 R2 直接下載、Flask worker 已釋放

## Permission model

| Attachment 類別 | 既有 view 權限 | Download 權限（一致） |
|---|---|---|
| `NoteAttachment` | 同 store user/admin、super_admin 跨店 | 同 |
| `AnnouncementAttachment` | `_can_view()` (announcement_routes.py L42)：super_admin 跨店、其他人 `store == user.store` **OR** `store IS NULL`（全站公告） | 同 |

不開新的權限級別 — 能看就能下載（current viewer 已等同下載）。super_admin 既然能跨店查看附件、就能跨店下載。
Backend download endpoint 直接呼叫既有的 `_can_view(announcement)` / NoteAttachment 同店檢查 helper、複用同一條 auth path、不重寫權限邏輯。

## Filename handling

- `att.filename` column 存的是 user 上傳時的原檔名（含中文）
- Content-Disposition 必須用 RFC 5987 `filename*=UTF-8''...` 格式才能正確處理中文檔名
- `urllib.parse.quote(filename, safe="")` 處理 percent-encoding
- 防呆：filename 缺失時 fallback `attachment_{att_id}{ext}`，ext 從 `att.content_type` 推導（`image/jpeg` → `.jpg`、`video/mp4` → `.mp4`、其他或解不出 → 不加 ext）

## Error handling

| Error | 行為 |
|---|---|
| Attachment id 不存在 | 404 + JSON `{"status": "error", "message": "附件不存在"}` |
| Note 不存在（soft-deleted） | 404 + JSON `{"status": "error", "message": "筆記不存在"}` |
| 權限不足（跨 store user） | 403 + JSON `{"status": "error", "message": "無權限下載"}` |
| R2 unavailable / boto3 timeout | 503 + JSON `{"status": "error", "message": "儲存服務暫時無法使用"}`、前端 showToast |
| 未登入 | 401（既有 `@login_required` 行為） |

Frontend 任何 non-2xx → `showToast('下載失敗')` + 不觸發 navigation。

## Testing

### Unit tests（`tests/test_storage_download.py` 新檔）

1. `get_download_url` 產生的 URL 含 `response-content-disposition=attachment`
2. 中文檔名正確 RFC 5987 encode（不掉字）
3. expires_in 預設 300
4. 缺 filename 時 fallback 邏輯

### Integration tests（擴充既有 `tests/test_attachments_*.py`）

5. NoteAttachment download endpoint：登入 + 同 store → 200 + url
6. NoteAttachment download endpoint：跨 store user → 403
7. NoteAttachment download endpoint：未登入 → 401
8. NoteAttachment download endpoint：不存在 id → 404
9. AnnouncementAttachment download endpoint：同上 4 條
10. super_admin 跨 store NoteAttachment download → 200

### Markup smoke tests

11. notes editor template 渲染含 `att-download-btn` 元素
12. announcements edit/new template 渲染含 `att-download-btn` 元素
13. fullscreen_viewer.js 有 download button 元素

### Manual verification

- Desktop Chrome：點縮圖 icon → 檔案存到 Downloads/、檔名正確（中文不掉字）
- Mobile Safari：點縮圖 icon → 開「分享」面板、可存到 Photos 或 Files
- Mobile Android Chrome：點縮圖 icon → 直接存到 Downloads
- 影片檔下載（30MB+）：完整下載、不被 inline 播放
- Fullscreen viewer 內 download button 同上

## Out of scope

- 跨店下載 / 對外分享連結 / 批量 zip 匯出 / audit log
- 私人 mynotes 附件下載（隱私不同維度、另起 spec）
- 下載次數統計 / 限流 / R2 流量配額監控

## Migration / rollout

- 純加功能、不動 schema、不動既有 endpoints
- 不需 SW bump（除非 attachment 顯示 JS 邏輯改動觸發 cache）
  → 實際上有改 JS、所以 SW CACHE_NAME 要 bump（推實作時記得）
- 部署順序：backend endpoint + frontend JS 一起、本機驗證 → push origin/main → Zeabur auto deploy
