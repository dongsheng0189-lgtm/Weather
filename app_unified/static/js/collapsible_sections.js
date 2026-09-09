/* collapsible_sections.js — 公告 + AI 摘要可摺疊 toggle 邏輯 */
(function() {
  'use strict';

  var ANN_KEY = 'notes_ann_last_seen';

  function todayStr() {
    var d = new Date();
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var dd = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + dd;
  }

  function readLastSeen() {
    try {
      var raw = localStorage.getItem(ANN_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;
      return {
        date: String(parsed.date || ''),
        last_ann_id: parseInt(parsed.last_ann_id || 0, 10) || 0
      };
    } catch (e) { return null; }
  }

  function writeLastSeen(date, lastAnnId) {
    try {
      localStorage.setItem(ANN_KEY, JSON.stringify({date: date, last_ann_id: lastAnnId}));
    } catch (e) {}
  }

  function shouldAutoExpandAnn(maxAnnId) {
    var stored = readLastSeen();
    if (!stored) return true;
    if (stored.date < todayStr()) return true;
    if (stored.last_ann_id < maxAnnId) return true;
    return false;
  }

  function setSectionExpanded(section, expanded) {
    if (expanded) section.classList.add('is-expanded');
    else section.classList.remove('is-expanded');
  }

  function toggleSection(section) {
    setSectionExpanded(section, !section.classList.contains('is-expanded'));
  }

  function bindSectionBar(section) {
    var bar = section.querySelector('.collapsible-bar[data-role="section-bar"]');
    if (!bar) return;
    bar.addEventListener('click', function() { toggleSection(section); });
  }

  function initAnnouncementSection() {
    var section = document.querySelector('.collapsible[data-section="announcements"]');
    if (!section) return;
    var maxAnnId = parseInt(section.getAttribute('data-max-ann-id') || '0', 10);
    if (shouldAutoExpandAnn(maxAnnId)) {
      setSectionExpanded(section, true);
      writeLastSeen(todayStr(), maxAnnId);
    } else {
      setSectionExpanded(section, false);
    }
    bindSectionBar(section);
  }

  function initAiSummarySection() {
    var section = document.querySelector('.collapsible[data-section="ai-summary"]');
    if (!section) return;
    setSectionExpanded(section, false);
    bindSectionBar(section);
  }

  function initTagManagementSection() {
    var section = document.querySelector('.collapsible[data-section="tag-management"]');
    if (!section) return;
    setSectionExpanded(section, false);
    bindSectionBar(section);
  }

  var FILTER_KEY = 'notes_filter_panel_open';

  /* 進階篩選面板要不要展開。抽成純函式是為了能被測試實際執行 ——
     這裡壞掉的是「判斷順序」，原始碼字串比對驗不到。

     ⚠️ 使用者明確收起來的選擇（stored === '0'）優先於 activeCount。
     舊版把 activeCount 排在第一順位，結果：只要 localStorage 存過
     notes_sort='created'，頁首那段 redirect script 每次載入都會把 ?sort=created
     補回 URL，active count 就永遠 >= 1、永遠強制展開，使用者收起來的動作每次
     都被蓋掉。2026-09-09 現場回報「關起來後滑掉重開還是打開的」就是這條。

     訊號沒有因此消失：橫幅標題仍然顯示 (n) 告訴你有幾個篩選在作用中，
     只是不再硬把面板撐開。 */
  function decideFilterExpanded(activeCount, stored) {
    if (stored === '0') return false;   // 明確收起來 → 尊重，即使有 active filter
    if (activeCount > 0) return true;   // 沒表達過意見 + 有篩選在作用 → 展開
    return stored === '1';              // '1' → 展開；沒設過 → 摺疊
  }

  function initFilterSection() {
    var section = document.querySelector('.collapsible[data-section="filters"]');
    if (!section) return;
    var activeCount = parseInt(section.getAttribute('data-active-count') || '0', 10);
    var stored;
    try { stored = localStorage.getItem(FILTER_KEY); } catch (e) { stored = null; }
    setSectionExpanded(section, decideFilterExpanded(activeCount, stored));
    var bar = section.querySelector('.collapsible-bar[data-role="section-bar"]');
    if (bar) {
      bar.addEventListener('click', function() {
        toggleSection(section);
        try {
          localStorage.setItem(FILTER_KEY,
            section.classList.contains('is-expanded') ? '1' : '0');
        } catch (e) {}
      });
    }
  }

  var REM_KEY = 'notes_reminders_last_seen';

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function readRemSeen() {
    try {
      var p = JSON.parse(localStorage.getItem(REM_KEY) || 'null');
      if (!p || typeof p !== 'object') return null;
      return { date: String(p.date || ''), last_sig: String(p.last_sig || '') };
    } catch (e) { return null; }
  }

  function renderReminders(box, list) {
    if (!list.length) {
      box.innerHTML = '<div style="color:#888;font-size:.85rem;padding:.5rem;">目前沒有到期提醒</div>';
      return;
    }
    // 「全部」視圖（super_admin 未指定單店、CURRENT_STORE 為空）一律顯示店名 chip，
    // 不管只有一間或多間店有提醒；切到單一店 / 一般 user 則不顯示（同店免雜訊）
    var showStore = !window.CURRENT_STORE;
    var html = '<ul class="rem-list">';
    list.forEach(function(r) {
      var chip = r.overdue ? '<span class="rem-chip overdue">逾期</span>'
                           : '<span class="rem-chip">今日</span>';
      var storeChip = (showStore && r.store)
        ? '<span class="rem-store">' + escapeHtml(r.store) + '</span>' : '';
      var label = r.kind === 'item'
        ? (escapeHtml(r.title) + ' — ' + escapeHtml(r.text))
        : escapeHtml(r.title);
      var noteId = encodeURIComponent(String(r.note_id == null ? '' : r.note_id));
      // 項目級提醒帶 #item-<id> 錨點：編輯器 _maybeScrollToHashItem() 會 scroll 到
      // 該項目並加 .tag-highlight 持久黃底（跟關鍵字/標籤搜尋一致），返回鈕沿用 #float-back-widget
      var href = '/notes/' + noteId;
      if (r.kind === 'item' && r.id != null) {
        href += '#item-' + encodeURIComponent(String(r.id));
      }
      html += '<li>' + chip + storeChip + '<a href="' + href + '">' + label + '</a>'
            + '<span class="rem-date">' + escapeHtml(r.reminder_date) + '</span></li>';
    });
    html += '</ul>';
    box.innerHTML = html;
  }

  function initRemindersSection() {
    var section = document.querySelector('.collapsible[data-section="reminders"]');
    if (!section) return;
    bindSectionBar(section);
    var box = document.getElementById('reminders-box');
    var store = (window.CURRENT_STORE || '');
    var url = '/notes/api/reminders/today' + (store ? ('?store=' + encodeURIComponent(store)) : '');
    fetch(url).then(function(r) { return r.json(); }).then(function(data) {
      var list = (data && data.reminders) || [];
      var lastSig = (data && data.last_sig) || '';
      var cnt = document.getElementById('reminders-count');
      if (cnt) cnt.textContent = '(' + list.length + ')';
      renderReminders(box, list);
      var stored = readRemSeen();
      var expand;
      if (!list.length) {
        expand = false;
      } else if (!stored || stored.date < todayStr() || stored.last_sig < lastSig) {
        expand = true;
      } else {
        expand = false;
      }
      setSectionExpanded(section, expand);
      if (expand) {
        try { localStorage.setItem(REM_KEY, JSON.stringify({date: todayStr(), last_sig: lastSig})); } catch (e) {}
      }
    }).catch(function() {
      if (box) box.innerHTML = '<div style="color:#c00;font-size:.85rem;padding:.5rem;">提醒載入失敗</div>';
      setSectionExpanded(section, false);
    });
  }

  function init() {
    initAnnouncementSection();
    initAiSummarySection();
    initTagManagementSection();
    initFilterSection();
    initRemindersSection();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
