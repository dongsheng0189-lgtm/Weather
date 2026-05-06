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


def test_parse_results_real_hit():
    """真實命中頁（live recon 2026-05-06）→ 用 E8_NAMEx id 精準抓 1 筆。"""
    html = _read_fixture("npa_real_hit.html")
    results = parse_results(html)
    assert len(results) == 1
    r = results[0]
    assert r["name"] == "林肯帝"
    assert r["id_no"] == ""
    assert r["wt_unit"] == "基隆地檢"
    assert r["crime"] == "基隆地檢"  # 前端相容：crime 同 wt_unit


def test_parse_results_ignores_e8_note_section():
    """E8_NOTE（查詢結果注意事項）不能被誤判為通緝犯資料。"""
    html = _read_fixture("npa_real_hit.html")
    results = parse_results(html)
    assert all("依據刑事訴訟法" not in r["name"] for r in results)
    assert all("勿移作他用" not in r.get("crime", "") for r in results)
