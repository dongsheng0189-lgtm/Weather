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

  function bindAnnBullets(section) {
    section.querySelectorAll('.ann-bullet[data-ann-id]').forEach(function(bullet) {
      bullet.addEventListener('click', function(e) {
        e.stopPropagation();
        var annId = bullet.getAttribute('data-ann-id');
        var item = section.querySelector('.ann-item[data-ann-id="' + annId + '"]');
        if (!item) return;
        var isInPreview = !item.contains(bullet);
        if (isInPreview) {
          // Bullet 在 preview 區（section currently collapsed）
          // → open section + expand only this item
          section.querySelectorAll('.ann-item').forEach(function(it) {
            if (it !== item) it.classList.remove('is-expanded');
            else it.classList.add('is-expanded');
          });
          setSectionExpanded(section, true);
        } else {
          // Bullet 在 body 內某個 collapsed ann-item 上
          // → expand 該 item
          item.classList.add('is-expanded');
        }
      });
    });
  }

  function initAnnouncementSection() {
    var section = document.querySelector('.collapsible[data-section="announcements"]');
    if (!section) return;
    var maxAnnId = parseInt(section.getAttribute('data-max-ann-id') || '0', 10);
    var auto = shouldAutoExpandAnn(maxAnnId);
    if (auto) {
      setSectionExpanded(section, true);
      section.querySelectorAll('.ann-item').forEach(function(it) { it.classList.add('is-expanded'); });
      writeLastSeen(todayStr(), maxAnnId);
    } else {
      setSectionExpanded(section, false);
      section.querySelectorAll('.ann-item').forEach(function(it) { it.classList.remove('is-expanded'); });
    }
    bindSectionBar(section);
    bindAnnBullets(section);
  }

  function initAiSummarySection() {
    var section = document.querySelector('.collapsible[data-section="ai-summary"]');
    if (!section) return;
    setSectionExpanded(section, false);
    bindSectionBar(section);
  }

  function init() {
    initAnnouncementSection();
    initAiSummarySection();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
