"""Wiring: notes editor 返回連結 + index 卡片連結保留 `page` 參數（分頁返回）。"""
from pathlib import Path

ROOT = Path(__file__).parent.parent
EDITOR = ROOT / 'templates' / 'notes' / 'editor.html'
INDEX = ROOT / 'templates' / 'notes' / 'index.html'


def test_index_card_link_preserves_page():
    src = INDEX.read_text(encoding='utf-8')
    assert 'page=pagination.page' in src


def test_editor_back_link_preserves_page():
    src = EDITOR.read_text(encoding='utf-8')
    assert "page=request.args.get('page')" in src
