"""Tests for vetting/judicial.py — 司法院抓取/解析。
所有測試只用本地 fixture HTML，不打外部站。
"""
import os
import pytest

from vetting.judicial import (
    parse_home_state,
    parse_results,
    scan_detail_html,
    HOME_URL,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vetting")


def _read_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def test_parse_home_state_extracts_form_fields():
    """從首頁 HTML 解析出表單必要 hidden 欄位 + form action。"""
    html = _read_fixture("judicial_home.html")
    state = parse_home_state(html, base_url=HOME_URL)
    assert "form_action" in state
    assert "hidden_fields" in state
    # 司法院 ASP.NET form 應有的 hidden
    assert "__VIEWSTATE" in state["hidden_fields"]
    assert "__EVENTVALIDATION" in state["hidden_fields"]
    # form action 是 absolute URL
    assert state["form_action"].startswith("http")


def test_parse_results_extracts_rows():
    """結果頁 HTML 解析出 N 筆判決，每筆含案由/字號/日期/法院/detail_url。"""
    html = _read_fixture("judicial_results.html")
    rows = parse_results(html, base_url=HOME_URL)
    assert isinstance(rows, list)
    assert len(rows) >= 1
    r = rows[0]
    assert all(k in r for k in ("subject", "case_no", "date", "court", "detail_url"))
    assert r["detail_url"].startswith("http")
    # 王小明 fixture 第一筆是「臺中地方法院 114 年度 金訴 字第 3392 號刑事判決」
    assert "刑事判決" in (r["case_no"] + r["court"])


def test_scan_detail_finds_keywords():
    """fixture 是事先選的，至少含 1 個關鍵字（詐欺/有期徒刑）。"""
    html = _read_fixture("judicial_detail.html")
    result = scan_detail_html(html)
    assert "keyword_hits" in result
    assert "masked_names" in result
    assert len(result["keyword_hits"]) >= 1


def test_scan_detail_clean_doc_no_keywords():
    """無關鍵字 HTML → 空命中。"""
    html = "<html><body><p>純民事內文，無刑事關鍵字。</p></body></html>"
    result = scan_detail_html(html)
    assert result["keyword_hits"] == []
    assert result["masked_names"] == []


def test_scan_detail_finds_masked_names():
    """司法院判決書常見人名遮罩 王○○、丙○○ 等。"""
    html = "<html><body><p>被告 丙○○ 於 民國 112 年涉嫌 詐欺。</p></body></html>"
    result = scan_detail_html(html)
    assert "詐欺" in result["keyword_hits"]
    assert "丙○○" in result["masked_names"]
