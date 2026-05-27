# Attachment Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓登入的同店員工（含 super_admin 跨店）能透過縮圖右上 ⬇️ icon 或 fullscreen viewer 左下 ⬇️ button 下載店務筆記附件（`NoteAttachment`）跟公告附件（`AnnouncementAttachment`）的原檔。

**Architecture:** 後端新增 1 個 storage helper (`get_download_url`) + 2 個 GET endpoint，呼叫 boto3 `generate_presigned_url` 帶 `ResponseContentDisposition='attachment; filename*=UTF-8''<encoded>'` 與 `ResponseContentType` 參數，5 分鐘過期。Flask 完全不參與檔案流量、瀏覽器直接從 R2 下載。前端在縮圖跟 viewer 兩處共用 `downloadAttachment(endpointUrl)` JS helper，建隱形 `<a download>` 觸發。

**Tech Stack:** Flask + boto3 (Cloudflare R2 S3-compatible)、SQLAlchemy（既有 model）、vanilla JS + Jinja2 templates。

**Spec reference:** `docs/superpowers/specs/2026-05-27-attachment-download-design.md`

**Branch:** `feat/attachment-download`（已建好、spec 已 commit 在 `3e0beeb` + `4f10a66`）

---

## File map

| File | 動作 | 責任 |
|---|---|---|
| `app_unified/storage.py` | Modify | 加 `get_download_url()` helper |
| `app_unified/notes/routes.py` | Modify | 加 `GET /api/attachments/<id>/download` endpoint |
| `app_unified/notes/announcement_routes.py` | Modify | 加 `GET /announcement/api/attachments/<id>/download` endpoint |
| `app_unified/static/js/fullscreen_viewer.js` | Modify | `openFullscreen()` 改吃 opts 物件、加 download button + 共用 `downloadAttachment()` helper |
| `app_unified/static/js/announcement_attachments.js` | Modify | 縮圖加 download icon overlay、`_renderMedia` 改回 container 包 img + icon、`openFullscreen` 改傳 endpoint |
| `app_unified/templates/notes/editor.html` | Modify | 3 處 attachment 渲染加 download icon overlay + 改 openFullscreen call |
| `app_unified/static/css/fullscreen_viewer.css` | Modify | 加 `.att-download-btn` 樣式 + viewer download button 樣式 |
| `app_unified/static/sw.js` | Modify | 把 CACHE_NAME 推下一版（既有 v30 → v31） |
| `app_unified/tests/test_storage_download.py` | Create | `get_download_url` 單元測試 |
| `app_unified/tests/test_attachment_download.py` | Create | 兩個 endpoint 的權限/200/4xx integration tests |

---

### Task 1: `storage.get_download_url()` helper (TDD)

**Files:**
- Test: `app_unified/tests/test_storage_download.py` (new)
- Modify: `app_unified/storage.py` (add helper at end of file)

#### Step 1: Write the failing test

Create `app_unified/tests/test_storage_download.py`:

```python
"""Tests for storage.get_download_url — presigned URL with attachment disposition."""
import urllib.parse

import pytest


def test_get_download_url_returns_presigned_with_attachment_disposition(app):
    from storage import get_download_url
    with app.app_context():
        url = get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="現場照片.jpg",
            content_type="image/jpeg",
        )
        assert url is not None
        # presigned URL contains the content-disposition override
        assert "response-content-disposition=" in url.lower()
        # Disposition is attachment + RFC 5987 filename* with utf-8 encoding
        decoded = urllib.parse.unquote_plus(url)
        assert "attachment" in decoded
        assert "filename*=UTF-8''" in decoded
        # Chinese filename percent-encoded
        encoded_filename = urllib.parse.quote("現場照片.jpg", safe="")
        assert encoded_filename in url


def test_get_download_url_default_expires_in_300(app):
    from storage import get_download_url
    with app.app_context():
        url = get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="x.jpg",
            content_type="image/jpeg",
        )
        # presigned URL contains X-Amz-Expires=300
        assert "x-amz-expires=300" in url.lower()


def test_get_download_url_custom_expires_in(app):
    from storage import get_download_url
    with app.app_context():
        url = get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="x.jpg",
            content_type="image/jpeg",
            expires_in=600,
        )
        assert "x-amz-expires=600" in url.lower()


def test_get_download_url_returns_none_when_r2_not_configured(monkeypatch, app):
    from storage import get_download_url
    with app.app_context():
        monkeypatch.setitem(app.config, "R2_ENDPOINT_URL", "")
        result = get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="x.jpg",
            content_type="image/jpeg",
        )
        assert result is None


def test_get_download_url_returns_none_for_empty_object_key(app):
    from storage import get_download_url
    with app.app_context():
        assert get_download_url(
            object_key="",
            filename="x.jpg",
            content_type="image/jpeg",
        ) is None
```

#### Step 2: Run tests to verify they fail

```bash
cd ~/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_storage_download.py -v
```

Expected: 5 FAILED with ImportError or AttributeError ("cannot import name 'get_download_url' from 'storage'").

#### Step 3: Implement `get_download_url` in `storage.py`

Append to `app_unified/storage.py` (after `get_signed_url`):

```python
def get_download_url(object_key: str, filename: str, content_type: str,
                     expires_in: int = 300) -> str | None:
    """Generate a presigned URL that forces browser to download the file with
    Content-Disposition: attachment + the user-supplied filename (RFC 5987
    UTF-8 encoded). Used by the new attachment download endpoints.

    Returns None when R2 is not configured or object_key is empty.
    """
    if not BOTO3_AVAILABLE or not object_key:
        return None

    cfg = current_app.config
    if not cfg.get("R2_ENDPOINT_URL"):
        return None

    import urllib.parse
    safe_filename = filename or f"attachment_{uuid.uuid4().hex}"
    encoded = urllib.parse.quote(safe_filename, safe="")
    disposition = f"attachment; filename*=UTF-8''{encoded}"
    resp_content_type = content_type or "application/octet-stream"

    client = _get_client()
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": cfg["R2_BUCKET_NAME"],
                "Key": object_key,
                "ResponseContentDisposition": disposition,
                "ResponseContentType": resp_content_type,
            },
            ExpiresIn=expires_in,
        )
        return url
    except ClientError:
        return None
```

#### Step 4: Run tests to verify they pass

```bash
cd ~/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_storage_download.py -v
```

Expected: 5 PASSED.

#### Step 5: Commit

```bash
cd ~/projects/webapp && git add app_unified/storage.py app_unified/tests/test_storage_download.py && git commit -m "feat(storage): get_download_url helper for presigned attachment download

Returns a presigned R2 URL with ResponseContentDisposition=attachment +
RFC 5987 UTF-8 filename encoding (中文檔名不掉字) + 5min default
expiry. Foundation for note + announcement attachment download endpoints."
```

---

### Task 2: NoteAttachment download endpoint (TDD)

**Files:**
- Test: `app_unified/tests/test_attachment_download.py` (new — both endpoints share this file)
- Modify: `app_unified/notes/routes.py` (add endpoint after existing `delete_attachment_api` ~ L1837)

#### Step 1: Read existing endpoint pattern

Read `app_unified/notes/routes.py:1800-1837` to see how `delete_attachment_api` does the auth check (super_admin vs same-store), so the new endpoint uses an identical pattern.

#### Step 2: Write failing tests

Create `app_unified/tests/test_attachment_download.py`:

```python
"""Integration tests for attachment download endpoints (notes + announcements)."""
import pytest
from io import BytesIO


# ── NoteAttachment download endpoint ─────────────────────────────────────────

def test_note_attachment_download_same_store_user_returns_url(
    client, logged_in_user, note_with_attachment
):
    """Logged-in same-store user → 200 + presigned URL."""
    att_id = note_with_attachment.attachments[0].id
    res = client.get(f"/notes/api/attachments/{att_id}/download")
    assert res.status_code == 200
    body = res.get_json()
    assert "url" in body
    assert body["url"].startswith("http")
    assert "response-content-disposition=" in body["url"].lower()
    assert body["expires_in"] == 300


def test_note_attachment_download_super_admin_cross_store_returns_url(
    client, logged_in_super_admin, note_with_attachment_other_store
):
    """super_admin viewing attachment from a different store → 200."""
    att_id = note_with_attachment_other_store.attachments[0].id
    res = client.get(f"/notes/api/attachments/{att_id}/download")
    assert res.status_code == 200
    assert "url" in res.get_json()


def test_note_attachment_download_cross_store_user_returns_403(
    client, logged_in_user, note_with_attachment_other_store
):
    """Non-super_admin user trying to download an attachment from a different
    store → 403."""
    att_id = note_with_attachment_other_store.attachments[0].id
    res = client.get(f"/notes/api/attachments/{att_id}/download")
    assert res.status_code == 403


def test_note_attachment_download_unauthenticated_returns_401(client, note_with_attachment):
    """No login → 401 (via @login_required)."""
    att_id = note_with_attachment.attachments[0].id
    res = client.get(f"/notes/api/attachments/{att_id}/download")
    # Flask-Login typically returns 401 or redirects to login. Accept either,
    # but body must NOT be a JSON with url field.
    assert res.status_code in (401, 302)


def test_note_attachment_download_nonexistent_id_returns_404(
    client, logged_in_user
):
    res = client.get("/notes/api/attachments/99999999/download")
    assert res.status_code == 404
```

If the conftest fixtures (`note_with_attachment`, `note_with_attachment_other_store`, `logged_in_user`, `logged_in_super_admin`) don't exist, look at `app_unified/tests/conftest.py` and the existing `test_attachments_*.py` for patterns and either reuse or create equivalents in conftest (or add helper at top of this test file).

#### Step 3: Run tests to verify they fail

```bash
cd ~/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_attachment_download.py -v
```

Expected: 5 FAILED with 404 (endpoint not yet defined).

#### Step 4: Implement the endpoint

Append to `app_unified/notes/routes.py` after `delete_attachment_api` (around L1838):

```python
@notes_bp.route("/api/attachments/<int:attachment_id>/download", methods=["GET"])
@login_required
def download_attachment_api(attachment_id):
    """Return a 5-minute presigned R2 URL with Content-Disposition: attachment
    so browser downloads the original file. Auth = existing view permission
    (same store / super_admin).
    """
    from models import NoteAttachment
    from storage import get_download_url

    attachment = NoteAttachment.query.get(attachment_id)
    if not attachment:
        return jsonify({"error": "not_found"}), 404

    note = Note.active().filter_by(id=attachment.note_id).first()
    if not note:
        return jsonify({"error": "not_found"}), 404

    can_view = False
    if current_user.is_super_admin():
        can_view = True
    elif note.store == current_user.store:
        can_view = True

    if not can_view:
        return jsonify({"error": "forbidden"}), 403

    url = get_download_url(
        object_key=attachment.object_key,
        filename=attachment.filename,
        content_type=attachment.content_type,
    )
    if not url:
        return jsonify({"error": "storage_unavailable"}), 503

    return jsonify({"url": url, "expires_in": 300})
```

#### Step 5: Run tests to verify they pass

```bash
cd ~/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_attachment_download.py -v
```

Expected: 5 PASSED.

#### Step 6: Commit

```bash
cd ~/projects/webapp && git add app_unified/notes/routes.py app_unified/tests/test_attachment_download.py && git commit -m "feat(notes): GET /api/attachments/<id>/download endpoint

NoteAttachment download endpoint. Returns presigned R2 URL (5min)
with attachment Content-Disposition. Auth sees super_admin cross-store
+ same-store user/admin (mirror of existing view permission)."
```

---

### Task 3: AnnouncementAttachment download endpoint (TDD)

**Files:**
- Modify: `app_unified/tests/test_attachment_download.py` (append section)
- Modify: `app_unified/notes/announcement_routes.py` (add endpoint after existing announcement_attachment_list ~L322)

#### Step 1: Write the failing tests

Append to `app_unified/tests/test_attachment_download.py`:

```python
# ── AnnouncementAttachment download endpoint ─────────────────────────────────

def test_ann_attachment_download_same_store_user_returns_url(
    client, logged_in_user, announcement_with_attachment_same_store
):
    """Logged-in same-store user → 200 + presigned URL."""
    att_id = announcement_with_attachment_same_store.attachments[0].id
    res = client.get(f"/notes/announcement/api/attachments/{att_id}/download")
    assert res.status_code == 200
    body = res.get_json()
    assert "url" in body
    assert "response-content-disposition=" in body["url"].lower()
    assert body["expires_in"] == 300


def test_ann_attachment_download_global_store_visible_to_all(
    client, logged_in_user, announcement_with_attachment_store_null
):
    """`store IS NULL` 全站公告 → 任何登入 user 都能下載。"""
    att_id = announcement_with_attachment_store_null.attachments[0].id
    res = client.get(f"/notes/announcement/api/attachments/{att_id}/download")
    assert res.status_code == 200


def test_ann_attachment_download_super_admin_cross_store_returns_url(
    client, logged_in_super_admin, announcement_with_attachment_other_store
):
    att_id = announcement_with_attachment_other_store.attachments[0].id
    res = client.get(f"/notes/announcement/api/attachments/{att_id}/download")
    assert res.status_code == 200


def test_ann_attachment_download_cross_store_non_admin_returns_403(
    client, logged_in_user, announcement_with_attachment_other_store
):
    att_id = announcement_with_attachment_other_store.attachments[0].id
    res = client.get(f"/notes/announcement/api/attachments/{att_id}/download")
    assert res.status_code == 403


def test_ann_attachment_download_unauthenticated_returns_401(
    client, announcement_with_attachment_same_store
):
    att_id = announcement_with_attachment_same_store.attachments[0].id
    res = client.get(f"/notes/announcement/api/attachments/{att_id}/download")
    assert res.status_code in (401, 302)


def test_ann_attachment_download_nonexistent_id_returns_404(
    client, logged_in_user
):
    res = client.get("/notes/announcement/api/attachments/99999999/download")
    assert res.status_code == 404
```

If the fixtures `announcement_with_attachment_*` don't exist, follow the same pattern as Task 2 — look at `app_unified/tests/test_announcement*.py` and copy/adapt.

#### Step 2: Run tests to verify they fail

```bash
cd ~/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_attachment_download.py -v -k ann
```

Expected: 6 FAILED with 404.

#### Step 3: Implement the endpoint

Append to `app_unified/notes/announcement_routes.py` after `announcement_attachment_list` (around line 340):

```python
@notes_bp.route("/announcement/api/attachments/<int:attachment_id>/download",
                methods=["GET"])
@login_required
def announcement_attachment_download(attachment_id):
    """Return a 5-minute presigned R2 URL with Content-Disposition: attachment
    so browser downloads the original file. Auth = `_can_view()` on the parent
    announcement (super_admin cross-store / same-store / store=NULL global).
    """
    from models import AnnouncementAttachment
    from storage import get_download_url

    attachment = AnnouncementAttachment.query.get(attachment_id)
    if not attachment:
        return jsonify({"error": "not_found"}), 404

    announcement = attachment.announcement
    if not announcement or not _can_view(announcement):
        # Returns 404 (not 403) when announcement missing to avoid info leak;
        # 403 only when announcement exists but user not in scope.
        if not announcement:
            return jsonify({"error": "not_found"}), 404
        return jsonify({"error": "forbidden"}), 403

    url = get_download_url(
        object_key=attachment.object_key,
        filename=attachment.filename,
        content_type=attachment.content_type,
    )
    if not url:
        return jsonify({"error": "storage_unavailable"}), 503

    return jsonify({"url": url, "expires_in": 300})
```

#### Step 4: Run tests to verify they pass

```bash
cd ~/projects/webapp/app_unified && ../venv/bin/python -m pytest tests/test_attachment_download.py -v
```

Expected: 11 PASSED (5 from Task 2 + 6 new).

#### Step 5: Commit

```bash
cd ~/projects/webapp && git add app_unified/notes/announcement_routes.py app_unified/tests/test_attachment_download.py && git commit -m "feat(announcements): GET /announcement/api/attachments/<id>/download

AnnouncementAttachment download endpoint. Same pattern as note
attachment download. Auth sees super_admin cross-store + same-store
+ store=NULL global announcements via existing _can_view() helper."
```

---

### Task 4: Frontend — fullscreen viewer download button + shared `downloadAttachment()` helper

**Files:**
- Modify: `app_unified/static/js/fullscreen_viewer.js`
- Modify: `app_unified/static/css/fullscreen_viewer.css` (the F6 viewer's CSS file — if not in this exact path, look for `fullscreen-overlay` rules and edit that file)

#### Step 1: Extend `openFullscreen` API to accept `downloadHref`

Edit `app_unified/static/js/fullscreen_viewer.js`. Change the public API:

```javascript
// Old signature (line ~162):
//   function openFullscreen(url)
// New signature (back-compat: 2nd arg optional):
//   function openFullscreen(url, opts)
//   opts = { downloadHref: '/notes/api/attachments/123/download' } (optional)
//
// When opts.downloadHref is present, the viewer shows a ⬇️ download button
// that calls downloadAttachment(opts.downloadHref) on click.

function openFullscreen(url, opts) {
  opts = opts || {};
  _fsInit();
  _fsReset();
  document.getElementById('fullscreen-img').src = url;
  document.getElementById('fullscreen-overlay').classList.add('open');
  // Toggle download button visibility based on whether opts.downloadHref given
  var dlBtn = document.getElementById('fs-download');
  if (dlBtn) {
    if (opts.downloadHref) {
      dlBtn.style.display = '';
      dlBtn.dataset.href = opts.downloadHref;
    } else {
      dlBtn.style.display = 'none';
      dlBtn.dataset.href = '';
    }
  }
}
```

#### Step 2: Update `_ensureMarkup` to include the download button

Change the `_ensureMarkup` function (line ~127):

```javascript
function _ensureMarkup() {
  if (document.getElementById('fullscreen-overlay')) return;
  var overlay = document.createElement('div');
  overlay.className = 'fullscreen-overlay';
  overlay.id = 'fullscreen-overlay';
  overlay.innerHTML =
    '<div class="fs-stage" id="fs-stage"><img id="fullscreen-img" src=""></div>' +
    '<button type="button" class="fs-close" aria-label="關閉">✕</button>' +
    '<button type="button" class="fs-download" id="fs-download" aria-label="下載原檔" style="display:none;">⬇️</button>' +
    '<div class="fs-hint">雙指縮放 · 雙擊放大 · 拖曳移動</div>';
  document.body.appendChild(overlay);
  overlay.querySelector('.fs-close').addEventListener('click', closeFullscreen);
  overlay.querySelector('.fs-download').addEventListener('click', function(e) {
    e.stopPropagation();
    var href = this.dataset.href;
    if (href) downloadAttachment(href);
  });
}
```

#### Step 3: Add the shared `downloadAttachment` helper

Add this function at top-level inside the IIFE (before `_fsApply` is fine):

```javascript
// Shared helper — fetches the download endpoint to get the presigned URL,
// then triggers a hidden <a download> click. Used by both fullscreen viewer
// button and thumbnail download icon. endpointUrl examples:
//   /notes/api/attachments/123/download
//   /notes/announcement/api/attachments/456/download
function downloadAttachment(endpointUrl) {
  return fetch(endpointUrl, { credentials: 'same-origin' })
    .then(function(res) {
      if (!res.ok) {
        return res.json().then(function(j) {
          var msg = j && j.error ? j.error : ('HTTP ' + res.status);
          throw new Error(msg);
        }).catch(function() { throw new Error('HTTP ' + res.status); });
      }
      return res.json();
    })
    .then(function(data) {
      var a = document.createElement('a');
      a.href = data.url;
      a.style.display = 'none';
      // The presigned URL's Content-Disposition tells browser the filename;
      // we don't set the `download` attribute (it would override the server
      // header in some browsers and cause double-naming).
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    })
    .catch(function(err) {
      var msg = '下載失敗：' + (err && err.message ? err.message : err);
      if (typeof window.showToast === 'function') {
        window.showToast(msg);
      } else {
        alert(msg);
      }
    });
}

// Expose for callers
window.downloadAttachment = downloadAttachment;
```

#### Step 4: Update CSS for the new download button

Look for the file that styles `.fullscreen-overlay` / `.fs-close`. Likely `app_unified/static/css/fullscreen_viewer.css` or inline in template — `grep -rln "fs-close" app_unified/static/css app_unified/templates` to find it. Append:

```css
.fs-download {
  position: fixed;
  left: 16px;
  bottom: 24px;
  z-index: 10001;
  background: rgba(0, 0, 0, 0.55);
  color: white;
  border: none;
  border-radius: 50%;
  width: 48px;
  height: 48px;
  font-size: 22px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  -webkit-tap-highlight-color: transparent;
}
.fs-download:hover, .fs-download:focus {
  background: rgba(0, 0, 0, 0.75);
}
.fs-download:active {
  transform: scale(0.93);
}
```

#### Step 5: Manual verification (no pytest for pure JS — exercise via local server)

```bash
cd ~/projects/webapp && bash scripts/run_local.sh
```

Then in browser open a note that has an attachment, click the attachment to open viewer. Observe:
- Viewer now shows ⬇️ button bottom-left
- (Download will not work yet because thumbnails don't pass `downloadHref` — Task 5 wires that)
- ✕ close still works as before

#### Step 6: Commit

```bash
cd ~/projects/webapp && git add app_unified/static/js/fullscreen_viewer.js app_unified/static/css/fullscreen_viewer.css && git commit -m "feat(viewer): add download button + downloadAttachment helper

Extend openFullscreen(url, opts) to take {downloadHref}; show ⬇️ button
bottom-left when downloadHref present. New shared downloadAttachment()
function fetches endpoint to get presigned URL then triggers hidden
<a download>. Browser uses Content-Disposition header to set filename
(Chinese filename preserved via RFC 5987)."
```

---

### Task 5: Frontend — thumbnail download icon + wire up viewer downloadHref

**Files:**
- Modify: `app_unified/static/js/announcement_attachments.js`
- Modify: `app_unified/templates/notes/editor.html` (3 call sites at L1010, L1132, L1411 + their `_renderMedia`-equivalent render functions)
- Modify: `app_unified/static/css/` — add `.att-thumb-wrap` + `.att-download-btn` styles to whichever CSS file owns `.att-item` / attachment thumbnail styling (`grep -rln "att-item" app_unified/static/css app_unified/templates`)

#### Step 1: Update `_renderMedia` in `announcement_attachments.js` to wrap media in a thumb container with download icon

Change `_renderMedia` (line 18):

```javascript
function _renderMedia(att) {
  // Wrapper holds the media element + the download icon overlay (when att.id
  // exists — local-preview before upload has no id, no download endpoint).
  var wrap = document.createElement('div');
  wrap.className = 'att-thumb-wrap';

  if (att.content_type && att.content_type.indexOf('image/') === 0) {
    var img = document.createElement('img');
    img.src = att.url;
    img.alt = att.filename;
    img.onclick = function(e) {
      e.stopPropagation();
      var endpoint = att.id
        ? '/notes/announcement/api/attachments/' + att.id + '/download'
        : null;
      openFullscreen(att.url, { downloadHref: endpoint });
    };
    wrap.appendChild(img);
  } else if (att.content_type && att.content_type.indexOf('video/') === 0) {
    var video = document.createElement('video');
    video.src = att.url;
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    wrap.appendChild(video);
  } else {
    var a = document.createElement('a');
    a.href = att.url;
    a.textContent = '📎 ' + att.filename;
    a.target = '_blank';
    wrap.appendChild(a);
    return wrap;  // No download icon overlay on link-style fallback
  }

  // Add download icon overlay if att has a backend id (skip local-preview attachments)
  if (att.id) {
    var dlBtn = document.createElement('button');
    dlBtn.type = 'button';
    dlBtn.className = 'att-download-btn';
    dlBtn.setAttribute('aria-label', '下載原檔');
    dlBtn.title = '下載原檔';
    dlBtn.textContent = '⬇️';
    dlBtn.onclick = function(e) {
      e.stopPropagation();
      window.downloadAttachment('/notes/announcement/api/attachments/' + att.id + '/download');
    };
    wrap.appendChild(dlBtn);
  }

  return wrap;
}
```

#### Step 2: Update `notes/editor.html` attachment renderers

Find the 3 onclick sites (line 1010, 1132, 1411) and the render functions around them. Each renders an `<img>` or video for a note/checklist-item attachment. Pattern to apply at each site:

Before:
```javascript
img.onclick = function() { openFullscreen(att.url); };
```

After:
```javascript
img.onclick = function() {
  openFullscreen(att.url, {
    downloadHref: '/notes/api/attachments/' + att.id + '/download'
  });
};
```

And in each render function, wrap the `<img>` (or video) in a `<div class="att-thumb-wrap">` and append a download button after it (mirror the structure in Step 1).

To keep the diff focused, look for each `img.onclick` line, find the enclosing function (`renderAtt(...)` or similar — grep for the function name shown one or two lines above), and apply:

```javascript
function renderAtt(att) {
  var wrap = document.createElement('div');
  wrap.className = 'att-thumb-wrap';
  var img = document.createElement('img');
  img.src = att.url;
  img.onclick = function() {
    openFullscreen(att.url, {
      downloadHref: '/notes/api/attachments/' + att.id + '/download'
    });
  };
  wrap.appendChild(img);
  if (att.id) {
    var dlBtn = document.createElement('button');
    dlBtn.type = 'button';
    dlBtn.className = 'att-download-btn';
    dlBtn.setAttribute('aria-label', '下載原檔');
    dlBtn.title = '下載原檔';
    dlBtn.textContent = '⬇️';
    dlBtn.onclick = function(e) {
      e.stopPropagation();
      window.downloadAttachment('/notes/api/attachments/' + att.id + '/download');
    };
    wrap.appendChild(dlBtn);
  }
  return wrap;
}
```

Each of the 3 call sites lives in its own helper function (note-level, checklist-item-level, list-view). Replace each one's `img.onclick` and wrap the img.

#### Step 3: Add CSS for thumbnail + download icon

In the CSS file owning `.att-item` (probably `app_unified/static/css/attachments.css` or inline `<style>` in `templates/notes/editor.html` — find it with `grep -rln "att-item" app_unified/static/css app_unified/templates/notes`):

```css
.att-thumb-wrap {
  position: relative;
  display: inline-block;
}
.att-thumb-wrap img,
.att-thumb-wrap video {
  display: block;
  max-width: 100%;
}
.att-download-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  background: rgba(0, 0, 0, 0.55);
  color: white;
  border: none;
  border-radius: 50%;
  width: 32px;
  height: 32px;
  min-width: 32px;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  z-index: 5;
  -webkit-tap-highlight-color: transparent;
}
.att-download-btn:hover, .att-download-btn:focus {
  background: rgba(0, 0, 0, 0.75);
}
.att-download-btn:active {
  transform: scale(0.92);
}
/* On touch devices, keep icon always visible; on desktop, dim and show on hover */
@media (hover: hover) {
  .att-download-btn {
    opacity: 0.55;
  }
  .att-thumb-wrap:hover .att-download-btn {
    opacity: 1;
  }
}
```

#### Step 4: Markup smoke test

Create or extend a test file (e.g., `app_unified/tests/test_attachment_download.py`, append section):

```python
# ── Markup smoke tests ───────────────────────────────────────────────────────

def test_announcement_attachments_js_has_download_btn_logic(app):
    """Sanity: announcement_attachments.js source contains the download icon
    overlay markup and call into downloadAttachment()."""
    with open('static/js/announcement_attachments.js', encoding='utf-8') as f:
        src = f.read()
    assert "att-download-btn" in src
    assert "downloadAttachment" in src
    assert "/notes/announcement/api/attachments/" in src


def test_fullscreen_viewer_js_exports_downloadAttachment(app):
    with open('static/js/fullscreen_viewer.js', encoding='utf-8') as f:
        src = f.read()
    assert "window.downloadAttachment" in src
    assert "fs-download" in src


def test_notes_editor_template_passes_downloadHref(app):
    with open('templates/notes/editor.html', encoding='utf-8') as f:
        src = f.read()
    # At least one openFullscreen call now passes downloadHref opt
    assert "downloadHref" in src
    assert "/notes/api/attachments/" in src
```

Adjust the file open paths if the test runs from a different working dir (look at how other markup tests in `app_unified/tests/` open template files — typically using `pathlib.Path(__file__).parent.parent / 'templates' / ...`).

#### Step 5: Run all tests + manual verification

```bash
cd ~/projects/webapp/app_unified && ../venv/bin/python -m pytest -v
```

Expected: all PASSED (existing tests + the new ones).

Then local server:
```bash
cd ~/projects/webapp && bash scripts/run_local.sh
```

In browser, verify:
- 縮圖 ⬇️ icon 顯示在右上角、半透明黑底圓
- 點 icon：browser 開始下載（檔名是原檔名、含中文）
- 點縮圖本體：開 fullscreen viewer
- viewer 內 ⬇️ button 顯示在左下角、點了下載
- 公告 + 店務筆記 + 確認表單 三處都行得通
- ✕ 關閉 viewer 正常
- 跨 super_admin 切店、看到別店的 attachment 也能下載

#### Step 6: Commit

```bash
cd ~/projects/webapp && git add app_unified/static/js/announcement_attachments.js app_unified/templates/notes/editor.html app_unified/static/css app_unified/tests/test_attachment_download.py && git commit -m "feat(attachments): thumbnail download icon + wire viewer downloadHref

縮圖右上角加 ⬇️ icon overlay；點 icon 直接下載、點圖開 viewer。
viewer 開時帶 downloadHref opt、viewer 內 ⬇️ button 點了走同一條
downloadAttachment helper。CSS：touch device 常駐顯示、desktop hover
才浮起。Markup smoke tests 涵蓋 3 個關鍵元素。"
```

---

### Task 6: SW bump + final verification

**Files:**
- Modify: `app_unified/static/sw.js` (bump CACHE_NAME)

#### Step 1: Bump CACHE_NAME

Open `app_unified/static/sw.js`, find the line with `CACHE_NAME = 'webapp-cache-vXX'` (memory says current is `v30`). Increment to `v31` (or whatever next-up is when this task runs).

```bash
cd ~/projects/webapp && grep -n "CACHE_NAME\s*=" app_unified/static/sw.js
```

Use Edit to bump the version number.

#### Step 2: Full test suite

```bash
cd ~/projects/webapp/app_unified && ../venv/bin/python -m pytest --no-header -q 2>&1 | tail -5
```

Expected: all PASSED, no regression.

#### Step 3: Local end-to-end verification

按 [[feedback_local_test_before_push]] 規則：

```bash
cd ~/projects/webapp && bash scripts/run_local.sh
```

開 `http://localhost:XXXX/dev` 切換到「店長」測試帳號、走完 user 提的核心 case：

1. 進一篇有照片附件的店務筆記 → 縮圖右上 ⬇️ icon → 下載原檔成功
2. 點縮圖開 viewer → 左下 ⬇️ button → 下載成功
3. 進一篇有影片附件的店務筆記 → 兩個入口都試一次（影片不要被 inline 播放、要被存下來）
4. 進公告編輯頁、有附件、走 1+2
5. 切到別店帳號（如非 super_admin、直接登別店）→ 想要下載剛剛那篇別店的附件 → 應該 403/找不到附件
6. 切 super_admin 帳號（store=None）→ 切到 A 店 → 下載 A 店附件 OK
7. 中文檔名（user 上傳一張「現場照片.jpg」）→ 下載後檔名保留中文不掉字

User OK 後才繼續下一步。

#### Step 4: Commit SW bump

```bash
cd ~/projects/webapp && git add app_unified/static/sw.js && git commit -m "chore(sw): bump CACHE_NAME for attachment download deploy"
```

#### Step 5: Push prompt（user 明說才動）

待 user 明確說「push」、才執行：

```bash
cd ~/projects/webapp && git checkout main && git merge --ff-only feat/attachment-download && git push origin main
```

按 [[feedback_push_peak_hours]]：台灣 19:30-21:00 禁 push、其他時段 user 說了才推。
按 [[feedback_branch_workflow]]：merge main 跟 push 都要 user 明確說。

Push 完追 Zeabur Redeploy + prod 實機驗。

---

## Self-Review

### Spec coverage
- ✅ NoteAttachment + AnnouncementAttachment download endpoints → Task 2 + Task 3
- ✅ Storage helper with Content-Disposition + RFC 5987 → Task 1
- ✅ super_admin 跨店 / store=NULL 全站公告 → Task 2 (super_admin test) + Task 3 (global store test)
- ✅ 縮圖 ⬇️ icon + viewer ⬇️ button → Task 4 + Task 5
- ✅ 共用 `downloadAttachment` helper → Task 4
- ✅ SW bump → Task 6
- ✅ 中文檔名保留 → Task 1 test + Task 5 manual case 7
- ✅ Loading 視覺：簡化處理（fetch < 200ms、不顯示）— spec 已允許
- ✅ 5 分鐘 expiry → Task 1 default test
- ✅ Error handling 表（404 / 403 / 401 / 503）→ Task 2 + Task 3 tests
- ✅ 不做：跨店分享連結 / zip / audit log / mynotes → 沒對應 task（intentional）

### Placeholder scan
- 沒有 "TBD" / "TODO" / "implement later"
- CSS 跟模板修改位置給了 grep 指令幫工程師定位，沒寫死路徑（templates 路徑略有不確定、grep 即可）
- 所有測試都有 actual test code

### Type / name consistency
- `get_download_url(object_key, filename, content_type, expires_in)` — Task 1 定義、Task 2 + Task 3 呼叫一致
- `downloadAttachment(endpointUrl)` — Task 4 定義、Task 5 在 announcement_attachments.js + editor.html 都呼叫一致
- `openFullscreen(url, opts)` 中 `opts.downloadHref` — Task 4 定義、Task 5 三個 caller 都用同名 key
- Endpoint paths: `/notes/api/attachments/<id>/download` 跟 `/notes/announcement/api/attachments/<id>/download` 跨 task 一致

---

## Branches in flight

| Branch | Status |
|---|---|
| `feat/attachment-download` | 已有 spec commit、實作 task 1-6 都加在這條 |
| `fix/mynotes-meeting-tweaks` | 獨立、跟 download 無關、待 user 決定 push 順序 |
| `main` | 98b8f18、未動 |
