"""快速標籤鍵的前端接線（2026-09-08）。

同專案 editor.html wiring test 慣例：原始碼層斷言。
"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_all_store_tags_exposes_pinned():
    """前端要靠 pinned 決定哪些標籤渲染成按鈕 —— 沒傳就什麼都不會出現。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'pinned:' in src, 'ALL_STORE_TAGS 沒有帶 pinned 欄位'


def test_tag_meta_maps_exposed():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'window.ITEM_TAG_META_MAP' in src, '缺少項目標籤的作者資訊 map'
    assert 'window.NOTE_TAG_META' in src, '缺少筆記標籤的作者資訊 map'
