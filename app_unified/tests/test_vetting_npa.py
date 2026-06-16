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


# ── Fix B：驗證碼錯誤偵測（NPA 回 200 + 表單頁，錯誤訊息在 JS errorMsg、
#    中文用 \uXXXX 跳脫，字面「錯誤」不出現）──────────────────────────────
from vetting.npa import _is_captcha_error

# prod probe 撈到的真實錯誤頁特徵（errorMsg = 「驗證碼輸入錯誤，請重新輸入！」escaped）
_WRONG_CAPTCHA_HTML = (
    '<label for="answer">(必填)驗證碼</label>'
    '<input name="answer" placeholder="輸入驗證碼">'
    '<script>'
    'var errorMsg = "\\u9A57\\u8B49\\u78BC\\u8F38\\u5165\\u932F\\u8AA4'
    '\\uFF0C\\u8ACB\\u91CD\\u65B0\\u8F38\\u5165\\uFF01";'
    'message("...", errorMsg);'
    '</script>'
)


def test_is_captcha_error_detects_escaped_errormsg():
    assert _is_captcha_error(_WRONG_CAPTCHA_HTML) is True


def test_is_captcha_error_no_false_positive_empty_errormsg():
    # 表單頁但 errorMsg 空（初次載入 / 正確查詢成功頁）：有「驗證碼」label、無錯誤
    html = ('<label>(必填)驗證碼</label><input name="answer">'
            '<script>var errorMsg = "";</script>')
    assert _is_captcha_error(html) is False


def test_is_captcha_error_literal_fallback():
    # 萬一 NPA 改回字面中文也要抓得到
    assert _is_captcha_error('驗證碼錯誤，請重新輸入') is True
