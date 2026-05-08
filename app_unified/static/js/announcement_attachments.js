/* 公告附件 UI — 複製 notes/editor.html 的 createAttItem / openFullscreen pattern。
 * 提供：
 *   - createAnnAttItem(att) : 編輯/建立頁的附件 item（含刪除 ×）
 *   - renderAnnAttView(att) : index 卡片唯讀展示（無刪除 ×）
 *   - handleAnnFileUpload(input) : 編輯/建立頁 file input change handler
 *   - uploadAnnPendingFiles(annId, cb) : submit 後逐檔上傳
 *   - openFullscreen(url)
 * 依賴 window.ANN_ID（編輯頁 = id；建立頁 = null）+ window.ANN_PENDING_FILES。
 */

function openFullscreen(url) {
  var overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,.92);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:zoom-out;';
  var img = document.createElement('img');
  img.src = url;
  img.style.cssText = 'max-width:95vw;max-height:95vh;';
  overlay.appendChild(img);
  overlay.onclick = function() { document.body.removeChild(overlay); };
  document.body.appendChild(overlay);
}

function _formatSize(bytes) {
  if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1024).toFixed(0) + ' KB';
}

function _renderMedia(att) {
  if (att.content_type && att.content_type.indexOf('image/') === 0) {
    var img = document.createElement('img');
    img.src = att.url;
    img.alt = att.filename;
    img.onclick = function(e) { e.stopPropagation(); openFullscreen(att.url); };
    return img;
  }
  if (att.content_type && att.content_type.indexOf('video/') === 0) {
    var video = document.createElement('video');
    video.src = att.url;
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    return video;
  }
  var a = document.createElement('a');
  a.href = att.url;
  a.textContent = '📎 ' + att.filename;
  a.target = '_blank';
  return a;
}

function createAnnAttItem(att) {
  var div = document.createElement('div');
  div.className = 'att-item';
  div.id = 'ann-att-' + att.id;
  div.appendChild(_renderMedia(att));

  var info = document.createElement('div');
  info.className = 'att-info';
  info.textContent = att.filename + ' (' + _formatSize(att.file_size) + ')';
  div.appendChild(info);

  var delBtn = document.createElement('button');
  delBtn.className = 'att-delete';
  delBtn.textContent = '×';
  delBtn.onclick = function(e) {
    e.stopPropagation();
    if (!confirm('刪除此附件？')) return;
    fetch('/notes/announcement/api/attachments/' + att.id, {method: 'DELETE'})
      .then(function(r) {
        if (r.status === 200) {
          var el = document.getElementById('ann-att-' + att.id);
          if (el) el.remove();
        } else {
          alert('刪除失敗');
        }
      });
  };
  div.appendChild(delBtn);
  return div;
}

function renderAnnAttView(att) {
  var div = document.createElement('div');
  div.className = 'att-view';
  div.appendChild(_renderMedia(att));
  return div;
}

function handleAnnFileUpload(input) {
  var files = input.files;
  if (!files.length) return;
  var statusEl = document.getElementById('ann-upload-status');
  var list = document.getElementById('ann-attachment-list');

  if (!window.ANN_ID) {
    for (var i = 0; i < files.length; i++) {
      window.ANN_PENDING_FILES.push(files[i]);
      var f = files[i];
      var url = URL.createObjectURL(f);
      var div = document.createElement('div');
      div.className = 'att-item';
      var fakeAtt = {url: url, content_type: f.type, filename: f.name, file_size: f.size};
      div.appendChild(_renderMedia(fakeAtt));
      var info = document.createElement('div');
      info.className = 'att-info';
      info.textContent = f.name + ' (本地預覽，建立後上傳)';
      div.appendChild(info);
      list.appendChild(div);
    }
    input.value = '';
    return;
  }
  Array.prototype.forEach.call(files, function(f) {
    var fd = new FormData();
    fd.append('file', f);
    statusEl.textContent = '上傳中：' + f.name + ' …';
    fetch('/notes/announcement/api/' + window.ANN_ID + '/attachments',
          {method: 'POST', body: fd})
      .then(function(r) { return r.json().then(function(j) { return [r, j]; }); })
      .then(function(pair) {
        var r = pair[0], d = pair[1];
        if (r.status === 201) {
          list.appendChild(createAnnAttItem(d.attachment));
          statusEl.textContent = '';
        } else {
          alert('上傳失敗：' + (d.message || r.statusText));
          statusEl.textContent = '';
        }
      });
  });
  input.value = '';
}

function uploadAnnPendingFiles(annId, cb) {
  var pending = window.ANN_PENDING_FILES || [];
  if (!pending.length) { cb(); return; }
  var idx = 0;
  function next() {
    if (idx >= pending.length) { cb(); return; }
    var f = pending[idx++];
    var fd = new FormData();
    fd.append('file', f);
    fetch('/notes/announcement/api/' + annId + '/attachments',
          {method: 'POST', body: fd})
      .then(function(r) { next(); })
      .catch(function() { next(); });
  }
  next();
}
