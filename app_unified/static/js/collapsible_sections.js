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

  var FILTER_KEY = 'notes_filter_panel_open';

  function initFilterSection() {
    var section = document.querySelector('.collapsible[data-section="filters"]');
    if (!section) return;
    var activeCount = parseInt(section.getAttribute('data-active-count') || '0', 10);
    var stored;
    try { stored = localStorage.getItem(FILTER_KEY); } catch (e) { stored = null; }
    var shouldExpand;
    if (activeCount > 0) {
      shouldExpand = true;  // URL 有 active filter → 強制展開
    } else if (stored === '1') {
      shouldExpand = true;
    } else {
      shouldExpand = false;  // '0' 或沒設過 → 摺疊
    }
    setSectionExpanded(section, shouldExpand);
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

  function init() {
    initAnnouncementSection();
    initAiSummarySection();
    initFilterSection();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
