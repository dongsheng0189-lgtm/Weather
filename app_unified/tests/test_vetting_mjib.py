"""Tests for vetting/mjib.py — 法務部調查局通緝/嫌疑人名單。"""
import os
import pytest

from vetting.mjib import parse_list, search_by_name_in_html

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vetting")


def _read_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def test_parse_list_returns_entries():
    """解析 Crimes_List → list of dicts，每筆含 name/eng_name/masked_id/birth/photo_url/detail_url。"""
    html = _read_fixture("mjib_list.html")
    entries = parse_list(html)
    assert isinstance(entries, list)
    assert len(entries) >= 1
    e = entries[0]
    for k in ("name", "eng_name", "masked_id", "birth", "photo_url", "detail_url"):
        assert k in e, f"missing {k} in {e}"
    # 遮蔽身分證形式 e.g., A12336****
    assert "*" in e["masked_id"]


def test_search_by_name_filters_match_only():
    """過濾僅回姓名含查詢字的列。"""
    html = _read_fixture("mjib_list.html")
    entries = parse_list(html)
    if not entries:
        pytest.skip("fixture 沒有資料")
    target = entries[0]["name"]
    matched = search_by_name_in_html(html, target[:1])  # 用第一個字（姓）做 partial 查
    assert any(e["name"] == target for e in matched)


def test_search_by_name_no_match_returns_empty():
    html = _read_fixture("mjib_list.html")
    matched = search_by_name_in_html(html, "絕對不會有的姓名XYZ")
    assert matched == []
