"""mynotes meeting note local parser — unit tests for pure functions."""
import pytest

from mynotes.ai_local import _split_sections


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
