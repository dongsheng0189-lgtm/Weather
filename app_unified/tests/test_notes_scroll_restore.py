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
