/* tag_system.js — 標籤系統前端 (管理 + picker + filter row) */
(function() {
  'use strict';

  var PALETTE = [
    "#E74C3C","#C0392B","#E67E22","#D35400",
    "#F39C12","#F1C40F","#2ECC71","#27AE60",
    "#16A085","#1ABC9C","#3498DB","#2980B9",
    "#2C3E50","#9B59B6","#E91E63","#7F8C8D"
  ];

  function pickFg(hex) {
    if (!hex || hex.length !== 7) return '#fff';
    var r = parseInt(hex.slice(1,3), 16);
    var g = parseInt(hex.slice(3,5), 16);
    var b = parseInt(hex.slice(5,7), 16);
    var y = (r*299 + g*587 + b*114) / 1000;
    return y >= 160 ? '#000' : '#fff';
  }

  function chipEl(tag, opts) {
    opts = opts || {};
    var span = document.createElement('span');
    span.className = 'tag-chip';
    if (opts.button) span.classList.add('is-button');
    if (opts.active) span.classList.add('is-active');
    if (opts.disabled) span.classList.add('is-disabled');
    span.style.setProperty('--tag-bg', tag.color);
    span.style.setProperty('--tag-fg', tag.fg || pickFg(tag.color));
    span.textContent = tag.name;
    span.dataset.tagId = tag.id;
    return span;
  }

  function fetchJson(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers['Content-Type'] = 'application/json';
    }
    // 自動帶 ?store= 給 tag API、避免 super_admin (帳號 store=None) 拿不到 effective store
    if (window.CURRENT_STORE && url.indexOf('/notes/api/tags') === 0 && url.indexOf('store=') === -1) {
      url += (url.indexOf('?') === -1 ? '?' : '&') + 'store=' + encodeURIComponent(window.CURRENT_STORE);
    }
    return fetch(url, opts).then(function(r) {
      return r.json().then(function(j) { return {ok: r.ok, status: r.status, data: j}; });
    });
  }

  var state = { tags: [] };

  function paletteHtml(selectedColor) {
    return '<div class="palette">' + PALETTE.map(function(c) {
      var sel = (c === selectedColor) ? ' is-selected' : '';
      return '<span class="swatch' + sel + '" data-color="' + c + '" style="background:' + c + '"></span>';
    }).join('') + '</div>';
  }

  function buildForm(opts) {
    var form = document.createElement('div');
    form.className = 'tag-form';
    var initialName = (opts.name || '').replace(/"/g, '&quot;');
    form.innerHTML =
      '<div><label style="font-size:.85rem;color:#555;">名稱 (最多 20 字)</label>' +
      '<input type="text" maxlength="20" data-role="name" value="' + initialName + '"></div>' +
      '<div style="margin-top:.5rem;"><label style="font-size:.85rem;color:#555;">顏色</label>' +
      paletteHtml(opts.color) + '</div>' +
      '<div class="preview"><span class="preview-chip"></span></div>' +
      '<div class="form-actions">' +
      '<button type="button" data-role="cancel" class="btn btn-secondary" style="font-size:.8rem;">取消</button>' +
      '<button type="button" data-role="save" class="btn btn-primary" style="font-size:.8rem;">儲存</button>' +
      '</div>';
    var nameInput = form.querySelector('[data-role="name"]');
    var swatches = form.querySelectorAll('.swatch');
    var previewChip = form.querySelector('.preview-chip');
    var selectedColor = opts.color || PALETTE[0];
    function refreshPreview() {
      previewChip.className = 'tag-chip preview-chip';
      previewChip.style.setProperty('--tag-bg', selectedColor);
      previewChip.style.setProperty('--tag-fg', pickFg(selectedColor));
      previewChip.textContent = nameInput.value || '預覽';
    }
    swatches.forEach(function(s) {
      s.addEventListener('click', function() {
        swatches.forEach(function(x) { x.classList.remove('is-selected'); });
        s.classList.add('is-selected');
        selectedColor = s.dataset.color;
        refreshPreview();
      });
    });
    nameInput.addEventListener('input', refreshPreview);
    form.querySelector('[data-role="cancel"]').addEventListener('click', opts.onCancel);
    form.querySelector('[data-role="save"]').addEventListener('click', function() {
      var n = nameInput.value.trim();
      if (!n) { alert('名稱必填'); return; }
      opts.onSubmit(n, selectedColor);
    });
    refreshPreview();
    return form;
  }

  function showConfirmModal(message, onConfirm) {
    var modal = document.createElement('div');
    modal.className = 'tag-confirm-modal is-open';
    var panel = document.createElement('div');
    panel.className = 'panel';
    var hdr = document.createElement('div');
    hdr.style.fontWeight = '600';
    hdr.style.marginBottom = '.5rem';
    hdr.textContent = '⚠️ 確認刪除';
    var body = document.createElement('div');
    body.style.fontSize = '.9rem';
    body.style.color = '#555';
    body.textContent = message;
    var actions = document.createElement('div');
    actions.className = 'panel-actions';
    var cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-secondary';
    cancel.style.fontSize = '.85rem';
    cancel.textContent = '取消';
    cancel.addEventListener('click', function() { modal.remove(); });
    var ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'is-danger';
    ok.textContent = '確定刪除';
    ok.addEventListener('click', function() { modal.remove(); onConfirm(); });
    actions.appendChild(cancel);
    actions.appendChild(ok);
    panel.appendChild(hdr);
    panel.appendChild(body);
    panel.appendChild(actions);
    modal.appendChild(panel);
    document.body.appendChild(modal);
  }

  function renderMgmt() {
    var section = document.querySelector('.collapsible[data-section="tag-management"]');
    if (!section) return;
    // super_admin 在「全部」tab → 無 store context、標籤無法管理
    if (!window.CURRENT_STORE) {
      section.querySelector('[data-role="count"]').textContent = '';
      section.querySelector('[data-role="pin-count"]').textContent = '';
      section.querySelector('[data-role="pinned-list"]').innerHTML = '';
      section.querySelector('[data-role="other-list"]').innerHTML =
        '<div style="padding:.5rem;color:#888;font-size:.9rem;">⚠️ 請先在頂部切換到指定店別才能管理標籤（標籤是 per-store 的）</div>';
      var newBtn = section.querySelector('[data-role="new-tag-btn"]');
      if (newBtn) { newBtn.disabled = true; newBtn.title = '請先選擇店別'; }
      return;
    }
    var newBtn0 = section.querySelector('[data-role="new-tag-btn"]');
    if (newBtn0) { newBtn0.disabled = false; newBtn0.title = ''; }
    var pinned = state.tags.filter(function(t) { return t.pinned; });
    var others = state.tags.filter(function(t) { return !t.pinned; });
    section.querySelector('[data-role="count"]').textContent =
      state.tags.length > 0 ? '(' + state.tags.length + ')' : '';
    section.querySelector('[data-role="pin-count"]').textContent =
      '(' + pinned.length + '/3)';
    var pinList = section.querySelector('[data-role="pinned-list"]');
    var otherList = section.querySelector('[data-role="other-list"]');
    pinList.innerHTML = '';
    otherList.innerHTML = '';
    function makeRow(tag) {
      var row = document.createElement('div');
      row.className = 'tag-mgmt-row';
      row.appendChild(chipEl(tag));
      var actions = document.createElement('div');
      actions.className = 'row-actions';
      var pinBtn = document.createElement('button');
      if (tag.pinned) {
        pinBtn.textContent = '取消釘';
        pinBtn.addEventListener('click', function() { unpinTag(tag.id); });
      } else {
        pinBtn.textContent = '釘選';
        if (pinned.length >= 3) {
          pinBtn.disabled = true;
          pinBtn.title = '已釘滿、先取消其他釘選';
        } else {
          pinBtn.addEventListener('click', function() { pinTag(tag.id); });
        }
      }
      actions.appendChild(pinBtn);
      var editBtn = document.createElement('button');
      editBtn.textContent = '編輯';
      editBtn.addEventListener('click', function() { openEditForm(tag, row); });
      actions.appendChild(editBtn);
      var delBtn = document.createElement('button');
      delBtn.textContent = '🗑️';
      delBtn.className = 'is-danger';
      delBtn.addEventListener('click', function() { askDeleteTag(tag); });
      actions.appendChild(delBtn);
      row.appendChild(actions);
      return row;
    }
    pinned.forEach(function(t) { pinList.appendChild(makeRow(t)); });
    others.forEach(function(t) { otherList.appendChild(makeRow(t)); });
  }

  function reloadTags() {
    return fetchJson('/notes/api/tags').then(function(r) {
      if (r.ok) {
        state.tags = r.data.tags || [];
        renderMgmt();
        renderFilterRow();
      }
    });
  }

  function pinTag(id) {
    fetchJson('/notes/api/tags/' + id + '/pin', {method: 'POST'}).then(function(r) {
      if (r.ok) reloadTags();
      else alert(r.data.error || '釘選失敗');
    });
  }

  function unpinTag(id) {
    fetchJson('/notes/api/tags/' + id + '/pin', {method: 'DELETE'}).then(function(r) {
      if (r.ok) reloadTags();
    });
  }

  function askDeleteTag(tag) {
    var usage = tag.usage_count || 0;
    if (usage === 0) {
      doDeleteTag(tag.id);
    } else {
      showConfirmModal(
        '確定刪除「' + tag.name + '」？這個標籤目前被 ' + usage +
        ' 個 item 在用、刪後 chip 會消失（item 內容不變）。',
        function() { doDeleteTag(tag.id); }
      );
    }
  }

  function doDeleteTag(id) {
    fetchJson('/notes/api/tags/' + id, {method: 'DELETE'}).then(function(r) {
      if (r.ok) reloadTags();
      else alert(r.data.error || '刪除失敗');
    });
  }

  function openEditForm(tag, row) {
    var form = buildForm({
      name: tag.name,
      color: tag.color,
      onCancel: function() { form.remove(); },
      onSubmit: function(name, color) {
        fetchJson('/notes/api/tags/' + tag.id,
                  {method: 'PATCH', body: JSON.stringify({name: name, color: color})})
          .then(function(r) {
            if (r.ok) { form.remove(); reloadTags(); }
            else alert(r.data.error || '更新失敗');
          });
      }
    });
    row.parentNode.insertBefore(form, row.nextSibling);
  }

  function bindNewTagBtn() {
    var section = document.querySelector('.collapsible[data-section="tag-management"]');
    if (!section) return;
    var btn = section.querySelector('[data-role="new-tag-btn"]');
    var host = section.querySelector('[data-role="new-tag-form-host"]');
    btn.addEventListener('click', function() {
      host.innerHTML = '';
      var form = buildForm({
        onCancel: function() { host.innerHTML = ''; },
        onSubmit: function(name, color) {
          fetchJson('/notes/api/tags',
                    {method: 'POST', body: JSON.stringify({name: name, color: color})})
            .then(function(r) {
              if (r.ok) { host.innerHTML = ''; reloadTags(); }
              else alert(r.data.error || '建立失敗');
            });
        }
      });
      host.appendChild(form);
    });
  }

  function renderFilterRow() {
    var row = document.querySelector('.tag-filter-row[data-role="tag-row"]');
    if (!row) return;
    var currentTagId = parseInt(new URLSearchParams(window.location.search).get('tag') || '0', 10);
    // 排序：釘選優先、其餘按 id desc (新建立的先出)
    var pinned = state.tags.filter(function(t) { return t.pinned; });
    var others = state.tags.filter(function(t) { return !t.pinned; })
                           .sort(function(a, b) { return b.id - a.id; });
    var ordered = pinned.concat(others);
    var VISIBLE = 3;
    var head = ordered.slice(0, VISIBLE);
    var tail = ordered.slice(VISIBLE);
    row.innerHTML = '';
    if (state.tags.length === 0) return;
    var label = document.createElement('span');
    label.className = 'filter-icon';
    label.textContent = '🏷️ 標籤：';
    row.appendChild(label);

    function makeChip(t) {
      var chip = chipEl(t, {button: true, active: (t.id === currentTagId)});
      chip.addEventListener('click', function() {
        // 標籤是獨立互斥模式：導向乾淨 URL，只保留 store（+ tag）
        var u = new URL(window.location.origin + window.location.pathname);
        var store = new URLSearchParams(window.location.search).get('store');
        if (store) u.searchParams.set('store', store);
        if (t.id !== currentTagId) u.searchParams.set('tag', t.id);
        window.location.href = u.toString();
      });
      return chip;
    }

    head.forEach(function(t) { row.appendChild(makeChip(t)); });
    if (tail.length > 0) {
      var moreBtn = document.createElement('button');
      moreBtn.type = 'button';
      moreBtn.className = 'more-toggle';
      moreBtn.textContent = '+' + tail.length + ' more';
      var expanded = false;
      moreBtn.addEventListener('click', function() {
        if (expanded) {
          row.querySelectorAll('.tag-chip[data-more]').forEach(function(c) { c.remove(); });
          moreBtn.textContent = '+' + tail.length + ' more';
          expanded = false;
        } else {
          tail.forEach(function(t) {
            var c = makeChip(t);
            c.dataset.more = '1';
            row.insertBefore(c, moreBtn);
          });
          moreBtn.textContent = '收起';
          expanded = true;
        }
      });
      row.appendChild(moreBtn);
    }
  }

  function openPicker(targetType, targetId, currentIds, onSaved) {
    var modal = document.querySelector('.tag-picker-modal[data-role="tag-picker"]');
    if (!modal) { alert('picker 未載入'); return; }
    var selected = new Set(currentIds || []);

    function refreshList() {
      var list = modal.querySelector('[data-role="list"]');
      list.innerHTML = '';
      state.tags.forEach(function(t) {
        var isSel = selected.has(t.id);
        var isDisabled = !isSel && selected.size >= 5;
        var chip = chipEl(t, {button: true, active: isSel, disabled: isDisabled});
        chip.addEventListener('click', function() {
          if (selected.has(t.id)) { selected.delete(t.id); }
          else if (selected.size < 5) { selected.add(t.id); }
          refreshList();
          modal.querySelector('[data-role="count"]').textContent = selected.size;
        });
        list.appendChild(chip);
      });
    }

    modal.querySelector('[data-role="count"]').textContent = selected.size;
    refreshList();

    var newBtn = modal.querySelector('[data-role="new-tag-toggle"]');
    var newHost = modal.querySelector('[data-role="new-tag-form-host"]');
    newBtn.onclick = function() {
      newHost.innerHTML = '';
      var form = buildForm({
        onCancel: function() { newHost.innerHTML = ''; },
        onSubmit: function(name, color) {
          fetchJson('/notes/api/tags',
                    {method: 'POST', body: JSON.stringify({name: name, color: color})})
            .then(function(r) {
              if (!r.ok) { alert(r.data.error || '建立失敗'); return; }
              newHost.innerHTML = '';
              // 新建的 tag id auto-add 進 selected (尊重 5 個上限)
              var newTagId = r.data.tag.id;
              return reloadTags().then(function() {
                // 同步 window.ALL_STORE_TAGS 給 editor.html item rendering 用
                if (window.ALL_STORE_TAGS) {
                  window.ALL_STORE_TAGS.push({
                    id: r.data.tag.id, name: r.data.tag.name, color: r.data.tag.color
                  });
                }
                if (selected.size < 5) selected.add(newTagId);
                refreshList();
                modal.querySelector('[data-role="count"]').textContent = selected.size;
              });
            });
        }
      });
      newHost.appendChild(form);
    };

    modal.querySelector('[data-role="close"]').onclick =
      modal.querySelector('[data-role="cancel"]').onclick = function() {
      modal.classList.remove('is-open');
    };

    modal.querySelector('[data-role="save"]').onclick = function() {
      var ids = Array.from(selected);
      // Pre-save mode (new note/item — no id yet): just buffer + close, doSave 會帶進 POST
      if (!targetId) {
        modal.classList.remove('is-open');
        if (onSaved) onSaved(ids);
        return;
      }
      var url = (targetType === 'note')
        ? '/notes/api/' + targetId + '/tags'
        : '/notes/api/items/' + targetId + '/tags';
      fetchJson(url, {method: 'PUT', body: JSON.stringify({tag_ids: ids})})
        .then(function(r) {
          if (r.ok) {
            modal.classList.remove('is-open');
            if (window.markEditorDirty) window.markEditorDirty();
            if (onSaved) onSaved(ids);
          } else alert(r.data.error || '儲存失敗');
        });
    };

    modal.classList.add('is-open');
  }

  function bindNoteEditorPicker() {
    var btn = document.getElementById('open-tag-picker');
    if (!btn) return;
    var targetType = btn.dataset.targetType;
    var currentIds = [];
    try { currentIds = JSON.parse(btn.dataset.currentTagIds || '[]'); } catch (e) {}
    var badge = document.getElementById('note-tag-count');
    function refreshCountBadge(ids) {
      if (badge) badge.textContent = '(' + ids.length + '/5)';
    }
    refreshCountBadge(currentIds);
    // expose pre-save buffer 給 doSave 用
    window.__PRESAVE_TAG_IDS = currentIds.slice();
    btn.addEventListener('click', function() {
      // ⚠️ targetId 必須在**點擊當下**從 dataset 重讀，不可用 init 時抓的那份：
      // 新建筆記首次存檔後 dataset.targetId 才會被填上，沿用舊的 null 會讓選擇器
      // 一直以為自己還在「新建未存檔」模式 → 不發 PUT、只寫進 __PRESAVE_TAG_IDS，
      // 而下一次 doSave 因 NOTE_ID 已存在不會帶 tag_ids → **選的標籤完全沒存進去，
      // 但 badge 會更新、看起來像成功了**。
      var liveRaw = btn.dataset.targetId;
      var liveId = liveRaw ? parseInt(liveRaw, 10) : null;
      // 已存檔的筆記：每次點開都從 window.NOTE_TAG_IDS 重讀 —— 快速標籤鍵會
      // 直接改它，沿用 closure 裡載入時抓的那份會讓 PUT 把剛掛上的標籤靜默刪掉。
      // 尚未存檔的新筆記沒有 id，仍用 closure 的 pre-save 緩衝。
      var ids = liveId ? ((window.NOTE_TAG_IDS || []).slice()) : currentIds;
      openPicker(targetType, liveId, ids, function(newIds) {
        currentIds = newIds;
        if (liveId) window.NOTE_TAG_IDS = newIds.slice();
        refreshCountBadge(newIds);
        window.__PRESAVE_TAG_IDS = newIds.slice();
      });
    });
  }

  function bindItemPickers() {
    document.querySelectorAll('.item-tag-picker-btn').forEach(function(btn) {
      var targetId = parseInt(btn.dataset.targetId, 10);
      var currentIds = [];
      try { currentIds = JSON.parse(btn.dataset.currentTagIds || '[]'); } catch (e) {}
      btn.addEventListener('click', function() {
        openPicker('item', targetId, currentIds, function(ids) {
          currentIds = ids;
          window.location.reload();
        });
      });
    });
  }

  function init() {
    bindNewTagBtn();
    bindNoteEditorPicker();
    bindItemPickers();
    reloadTags();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.__tag_system = {
    state: state,
    reload: reloadTags,
    pickFg: pickFg,
    chipEl: chipEl,
    buildForm: buildForm,
    fetchJson: fetchJson,
    PALETTE: PALETTE,
    openPicker: openPicker,
  };
})();
