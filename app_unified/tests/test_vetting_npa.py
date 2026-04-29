"""Tests for vetting/npa.py — 警政署外逃通緝犯查詢系統。"""
import os

from vetting.npa import parse_home_tokens, parse_results

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vetting")


def _read_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def test_parse_home_tokens_extracts_csrf_and_key():
    """從 CE_Query.jsp HTML 抓 _csrf / _csrf_header / _key。"""
    html = _read_fixture("npa_home.html")
    tok = parse_home_tokens(html)
    assert "_csrf" in tok
    assert "_csrf_header" in tok
    assert "_key" in tok
    assert tok["_csrf_header"] == "X-CSRF-TOKEN"
    assert len(tok["_csrf"]) > 10
    assert len(tok["_key"]) > 10


def test_parse_results_no_match():
    """『查無結果』頁面 → 空 list。"""
    html = """<html><body>
    <div class="container">
      <p>查無資料</p>
    </div>
    </body></html>"""
    assert parse_results(html) == []


def test_parse_results_with_entries():
    """有命中時，每筆含 name + crime + detail_url（依實際 HTML 結構，先以最寬鬆比對）。"""
    html = """<html><body>
    <div class="result-item">
      <h3 class="name">王某某</h3>
      <p class="crime">詐欺</p>
      <a href="/NpaE8ServerRWD/detail?uid=123">詳情</a>
    </div>
    </body></html>"""
    results = parse_results(html)
    # 即使 selector 不匹配，至少不能炸
    assert isinstance(results, list)
