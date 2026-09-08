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


def test_quick_tag_button_factory_exists():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'function makeQuickTagBtn(' in src, '缺少快速標籤鍵的建構函式'


def test_quick_tag_uses_dedicated_endpoint_not_full_list():
    """必須打單標籤端點。用 PUT .../tags 會覆寫整份清單、兩人同時操作互相蓋掉。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert "'/notes/api/items/' + targetId + '/tags/' + tag.id" in src or \
           '"/notes/api/items/" + targetId + "/tags/" + tag.id' in src, \
        '快速鍵沒有打單標籤端點'


def test_quick_tag_rendered_for_pinned_only():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'return t.pinned;' in src, '沒有依 pinned 過濾 —— 會把所有標籤都變成按鈕'


def test_quick_tag_shows_authorship_on_hover():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'ITEM_TAG_META_MAP' in src and 'title' in src, \
        '沒有把作者資訊放進 title（hover 提示）'
