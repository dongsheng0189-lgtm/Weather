"""Wiring: notes 列表返回時還原捲動位置（scroll restore）。"""
from pathlib import Path

INDEX = Path(__file__).parent.parent / 'templates' / 'notes' / 'index.html'


def test_card_has_stable_id():
    """每張卡片要有 id="note-<id>" 才能被還原邏輯定位。"""
    src = INDEX.read_text(encoding='utf-8')
    assert 'id="note-{{ note.id }}"' in src


def test_card_click_remembers_note_id():
    """點卡片時把 note id 存進 sessionStorage('notes_return_to')。"""
    src = INDEX.read_text(encoding='utf-8')
    assert "sessionStorage.setItem('notes_return_to', '{{ note.id }}')" in src


def test_restore_reads_and_clears_key():
    """還原邏輯讀 notes_return_to，並用過即清（避免之後正常進列表亂捲）。"""
    src = INDEX.read_text(encoding='utf-8')
    assert "sessionStorage.getItem('notes_return_to')" in src
    assert "sessionStorage.removeItem('notes_return_to')" in src


def test_restore_scrolls_to_card():
    """讀回 id → getElementById('note-<id>') → scrollIntoView 定位。"""
    src = INDEX.read_text(encoding='utf-8')
    assert "getElementById('note-' + " in src
    assert 'scrollIntoView' in src


def test_restore_highlights_card():
    """套用卡片專用 highlight class（非 tag-highlight）。"""
    src = INDEX.read_text(encoding='utf-8')
    assert 'note-return-highlight' in src
    assert '.note-card.note-return-highlight' in src  # 卡片專用 CSS，非 .cl-item


def test_restore_covers_bfcache_and_dom_ready():
    """DOMContentLoaded（一般載入）+ pageshow（bfcache 還原）兩條路徑都觸發。"""
    src = INDEX.read_text(encoding='utf-8')
    assert "addEventListener('DOMContentLoaded'" in src
    assert "addEventListener('pageshow'" in src
