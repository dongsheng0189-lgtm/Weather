"""Tests for vetting/judicial.py — 司法院抓取/解析。
所有測試只用本地 fixture HTML，不打外部站。
"""
import os
import re
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


def test_parse_results_includes_highlighted_snippet():
    """結果頁摘要列含司法院 <span class="highlight"> 命中人名 → 應保留成安全的黃底 snippet。"""
    html = _read_fixture("judicial_results.html")
    rows = parse_results(html, base_url=HOME_URL)
    # 至少有一筆摘要含命中人名的黃底標記
    assert any(r.get("snippet") for r in rows)
    joined = " ".join(r.get("snippet", "") for r in rows)
    # 用我們自己的 class（不沿用原站屬性），內文是被搜尋的人名
    assert '<span class="vet-hl">王小明</span>' in joined


def test_parse_results_snippet_is_xss_safe():
    """摘要是外站回傳 HTML，只能保留 highlight 標籤，其餘一律 escape（防 XSS）。"""
    malicious = (
        '<table>'
        '<tr><td>1</td>'
        '<td><a href="data.aspx?id=X">臺灣臺北地方法院 113 年度 訴 字第 1 號刑事判決</a></td>'
        '<td>113.01.01</td><td>詐欺</td></tr>'
        '<tr><td colspan="4">惡意'
        '<span class="highlight" onclick="evil()">王小明</span>'
        '<script>alert(1)</script>'
        '<img src=x onerror=alert(1)></td></tr>'
        '</table>'
    )
    rows = parse_results(malicious, base_url=HOME_URL)
    snip = rows[0]["snippet"]
    assert '<span class="vet-hl">王小明</span>' in snip  # 命中人名保留成黃底
    assert "onclick" not in snip                          # 原站屬性不沿用
    assert "<script" not in snip                          # script 標籤剝除
    assert "onerror" not in snip and "<img" not in snip   # 其餘標籤剝除


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


from vetting.judicial import is_allowed_detail_url

def test_is_allowed_detail_url_accepts_real_detail():
    u = "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&id=TCDM,114,%E9%87%91,3,20251229,1&ot=in"
    assert is_allowed_detail_url(u) is True

def test_is_allowed_detail_url_rejects_bad():
    assert is_allowed_detail_url("http://judgment.judicial.gov.tw/FJUD/data.aspx?id=x") is False  # 非 https
    assert is_allowed_detail_url("https://evil.com/FJUD/data.aspx?id=x") is False                 # 別的 host
    assert is_allowed_detail_url("https://judgment.judicial.gov.tw/FJUD/qryresultlst.aspx") is False  # 別的 path
    assert is_allowed_detail_url("https://judgment.judicial.gov.tw@evil.com/FJUD/data.aspx") is False # host 混淆
    assert is_allowed_detail_url("https://judgment.judicial.gov.tw/FJUD/data.aspxEVIL?id=x") is False  # path 精確比對
    assert is_allowed_detail_url("") is False
    assert is_allowed_detail_url(None) is False


from vetting.judicial import extract_judgment_text

def test_extract_judgment_text_from_detail_fixture():
    html = _read_fixture("judicial_detail.html")
    text = extract_judgment_text(html)
    assert "主文" in text                 # 內文容器有抓到
    assert re.search(r"</?[a-zA-Z]", text) is None  # 無 HTML 標籤洩漏（內文合法角括號如 <黑白賣客> 允許）
    assert "\n" in text                   # 保留換行
    assert len(text) > 300


from vetting.judicial import build_highlighted_judgment

def test_highlight_literal_when_full_name_present():
    # 有完整名字命中 → 只包完整名字、不碰同姓遮罩名（避免雜訊）
    text = "被告王小明與王○○共同犯詐欺，嗣後王小明坦承犯行。"
    html, n = build_highlighted_judgment(text, "王小明")
    assert n == 2
    assert html.count('<span class="vet-hl"') == 2
    assert 'id="vhit-0"' in html and 'id="vhit-1"' in html
    assert "王○○" in html                              # 遮罩名保留為文字
    assert '<span class="vet-hl" id="vhit-0">王小明</span>' in html
    # 遮罩名沒有被包
    assert '>王○○</span>' not in html

def test_highlight_fallback_surname_when_fully_masked():
    # 完整名字 0 命中（全遮罩）→ fallback 比對姓，王○ / 王○○ 都算命中
    text = "被告王○○與王○於民國113年間共同犯詐欺。"
    html, n = build_highlighted_judgment(text, "王小明")
    assert n == 2
    assert '<span class="vet-hl" id="vhit-0">王○○</span>' in html
    assert '<span class="vet-hl" id="vhit-1">王○</span>' in html

def test_highlight_is_xss_safe():
    text = '<script>alert(1)</script>王小明<img src=x onerror=alert(1)>'
    html, n = build_highlighted_judgment(text, "王小明")
    assert "<script" not in html and "<img" not in html          # 原始標籤未洩漏（已 escape）
    assert "&lt;img src=x onerror=alert(1)&gt;" in html           # 惡意 img 整段 escape 成 inert 文字，onerror 只是純文字非屬性
    assert "&lt;script&gt;" in html
    assert '<span class="vet-hl" id="vhit-0">王小明</span>' in html
    assert n == 1

def test_highlight_empty_name_returns_escaped_no_hits():
    html, n = build_highlighted_judgment("<b>王小明</b>", "")
    assert n == 0
    assert html == "&lt;b&gt;王小明&lt;/b&gt;"
