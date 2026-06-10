"""Wiring: 關鍵字搜尋命中列高亮，與標籤搜尋 / AI 摘要統一用 .tag-highlight 持久黃底。"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def _func_body(src, name):
    start = src.index('function ' + name + '(')
    end = src.index('\nfunction ', start + 1)
    return src[start:end]


def test_keyword_search_adds_unified_tag_highlight():
    src = EDITOR.read_text(encoding='utf-8')
    body = _func_body(src, 'buildSearchHits')
    # 關鍵字命中列加 .tag-highlight（持久黃底），與 buildTagHits / AI 摘要一致
    assert "classList.add('tag-highlight')" in body


def test_tag_search_still_uses_tag_highlight():
    # 防回歸：標籤搜尋本來就用 .tag-highlight，確認沒被動到
    src = EDITOR.read_text(encoding='utf-8')
    body = _func_body(src, 'buildTagHits')
    assert "classList.add('tag-highlight')" in body
