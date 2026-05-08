"""mynotes meeting note local parser — unit tests for pure functions."""
import pytest

from mynotes.ai_local import _split_items, _split_sections


def test_split_sections_both_present():
    text = """
店務:
1. 外牆修補
2. 客訴

人員:
1. 小李離職
""".strip()
    result = _split_sections(text)
    assert set(result.keys()) == {"店務", "人員"}
    assert "外牆修補" in result["店務"]
    assert "客訴" in result["店務"]
    assert "小李離職" in result["人員"]


def test_split_sections_only_one():
    text = """
店務:
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "店務" in result
    assert "人員" not in result


def test_split_sections_neither():
    text = "今天會議聊了很多事情。沒有結構化的標頭。"
    result = _split_sections(text)
    assert result == {}


def test_split_sections_full_width_colon():
    text = """
店務：
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "店務" in result


def test_split_sections_brackets():
    text = """
【店務】
1. 外牆修補

[人員]
1. 小李離職
""".strip()
    result = _split_sections(text)
    assert "店務" in result
    assert "人員" in result


def test_split_sections_bare_word():
    text = """
店務
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "店務" in result


def test_split_sections_preamble_dropped():
    """Header 之前的內容不出現在任何 section body 裡。"""
    text = """
日期：2026/05/07
出席：店長、副店

店務:
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "日期" not in result.get("店務", "")
    assert "出席" not in result.get("店務", "")


def test_split_sections_duplicate_header_last_wins():
    """重複 section header → 後出現的覆蓋前面（記錄當前 contract，避免 regression）。"""
    text = """
店務:
1. 第一段店務

店務:
2. 第二段店務
""".strip()
    result = _split_sections(text)
    assert "店務" in result
    assert "第二段店務" in result["店務"]
    assert "第一段店務" not in result["店務"]


def test_split_items_arabic_dot():
    section = """
1. 外牆修補
2. 客訴
3. 庫存不足
""".strip()
    items = _split_items(section)
    assert len(items) == 3
    assert items[0] == "外牆修補"
    assert items[1] == "客訴"


def test_split_items_chinese_punct():
    section = """
1、外牆修補
2、客訴
""".strip()
    items = _split_items(section)
    assert len(items) == 2
    assert items[0] == "外牆修補"


def test_split_items_paren():
    section = """
(1) 外牆修補
(2) 客訴
""".strip()
    items = _split_items(section)
    assert len(items) == 2
    assert items[0] == "外牆修補"


def test_split_items_bullets():
    section = """
- 外牆修補
- 客訴
* 庫存不足
● 收銀機卡紙
""".strip()
    items = _split_items(section)
    assert len(items) == 4
    assert items[0] == "外牆修補"
    assert items[1] == "客訴"
    assert items[2] == "庫存不足"
    assert items[3] == "收銀機卡紙"


def test_split_items_multiline_item():
    """每個項目可跨多行直到下個 item bullet。"""
    section = """
1. 外牆修補, 廠商報價 8 萬太貴
   結論：請小張下週前找 3 家報價
2. 客訴
""".strip()
    items = _split_items(section)
    assert len(items) == 2
    assert "結論" in items[0]
    assert "請小張下週前找 3 家報價" in items[0]


def test_split_items_empty_string():
    assert _split_items("") == []


def test_split_items_whitespace_only():
    assert _split_items("   \n  ") == []


def test_split_items_no_bullets():
    """沒 bullet 的內容回空 list（避免誤把整段當一個項目）。"""
    section = "今天討論很多事情但沒列點"
    assert _split_items(section) == []
