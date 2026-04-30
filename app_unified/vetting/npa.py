"""警政署外逃通緝犯查詢系統 (eze8.npa.gov.tw/NpaE8ServerRWD/)。

Flow（實機 recon 結果）：
1. GET /CE_Query.jsp → 抓 <meta _csrf> + <meta _csrf_header> + <input #_key>
2. POST /Auth body=auu=<_key>, header X-CSRF-TOKEN=<_csrf> → response 是 JWT token
3. GET /genCheckCharImage?btnType=1&t=<rand> → captcha JPEG bytes
4. POST /doCEQuery body 含 QS_NAME/QS_NAMEA/QS_ID/QS_IDA/answer/answerA/_csrf/Authorization

注意：Authorization 是當作 form field 送，不是 HTTP header。
"""
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .errors import check_blocked
from .http_client import make_session

HOME_URL = "https://eze8.npa.gov.tw/NpaE8ServerRWD/CE_Query.jsp"
AUTH_URL = "https://eze8.npa.gov.tw/NpaE8ServerRWD/Auth"
CAPTCHA_URL = "https://eze8.npa.gov.tw/NpaE8ServerRWD/genCheckCharImage"
QUERY_URL = "https://eze8.npa.gov.tw/NpaE8ServerRWD/doCEQuery"


def parse_home_tokens(html: str) -> dict:
    """從首頁 HTML 抓 _csrf / _csrf_header / _key。"""
    soup = BeautifulSoup(html, "html.parser")
    csrf_meta = soup.find("meta", attrs={"name": "_csrf"})
    csrf_h_meta = soup.find("meta", attrs={"name": "_csrf_header"})
    key_input = soup.find("input", id="_key")
    if not csrf_meta or not csrf_h_meta or not key_input:
        raise ValueError("無法解析 NPA 首頁 token (csrf/_key)")
    return {
        "_csrf": csrf_meta.get("content", ""),
        "_csrf_header": csrf_h_meta.get("content", ""),
        "_key": key_input.get("value", ""),
    }


EMPTY_RESULT_MARKERS = (
    "查無資料", "查無符合", "查無此", "無此資料", "無此筆資料",
    "沒有符合", "沒有資料", "0 筆", "0筆", "查詢結果為 0",
    "本系統未發現", "未發現符合",
)


def parse_results(html: str) -> list:
    """解析 doCEQuery 結果頁。

    1. 先掃常見「查無」字樣 → 直接回 []
    2. 否則用寬鬆 selector 嘗試命中，但加嚴格條件避免 boilerplate 誤判
    3. 結構未知時，需在 prod 跑一次拿真實 HTML 再寫精準 parser
    """
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(" ", strip=True)

    # 早退：明確的「查無」訊號
    if any(marker in full_text for marker in EMPTY_RESULT_MARKERS):
        return []

    results = []
    # 卡片 / row 寬鬆 selector
    for sel in [".result-item", ".wanted-card", ".card"]:
        items = soup.select(sel)
        for item in items:
            text = item.get_text(" ", strip=True)
            if not text or len(text) < 10:
                continue
            if any(kw in text for kw in ("罪", "通緝", "嫌疑")):
                name_el = item.select_one(".name, h3, .title")
                crime_el = item.select_one(".crime, .charge")
                link = item.find("a")
                results.append({
                    "name": name_el.get_text(strip=True) if name_el else "",
                    "crime": crime_el.get_text(strip=True) if crime_el else text[:80],
                    "detail_url": urljoin(QUERY_URL, link["href"]) if link and link.get("href") else "",
                })
        if results:
            break

    # 表格 fallback：tr 嚴格條件 — 至少 3 個 td、跳過全 th 的 header row
    if not results:
        for row in soup.select("tr"):
            tds = row.find_all("td", recursive=False)
            if len(tds) < 3:
                continue
            text = row.get_text(" ", strip=True)
            if not any(kw in text for kw in ("罪", "通緝", "嫌疑")):
                continue
            link = row.find("a")
            results.append({
                "name": tds[1].get_text(strip=True) if len(tds) > 1 else "",
                "crime": tds[2].get_text(strip=True) if len(tds) > 2 else text[:80],
                "detail_url": urljoin(QUERY_URL, link["href"]) if link and link.get("href") else "",
            })

    return results


# ─────────────────────────────────────────────
# Live IO functions
# ─────────────────────────────────────────────

def fetch_home_state(session=None) -> tuple:
    """打首頁，回 (tokens_dict, session)。"""
    if session is None:
        session = make_session()
    r = session.get(HOME_URL, timeout=10)
    check_blocked(r)
    r.raise_for_status()
    return parse_home_tokens(r.text), session


def fetch_auth_token(session, tokens: dict) -> str:
    """POST /Auth 取 JWT token。"""
    r = session.post(
        AUTH_URL,
        data={"auu": tokens["_key"]},
        headers={
            tokens["_csrf_header"]: tokens["_csrf"],
            "Referer": HOME_URL,
        },
        timeout=10,
    )
    check_blocked(r)
    r.raise_for_status()
    return r.text.strip()


def fetch_captcha_bytes(session) -> bytes:
    """GET captcha image。"""
    r = session.get(
        CAPTCHA_URL,
        params={"btnType": "1", "t": str(time.time())},
        headers={"Referer": HOME_URL},
        timeout=10,
    )
    check_blocked(r)
    r.raise_for_status()
    return r.content


def submit_query(session, tokens: dict, jwt_token: str,
                 name: str, id_no: str, captcha_text: str) -> list:
    """POST /doCEQuery，回結果 list。"""
    payload = {
        "QS_NAME": name,
        "QS_NAMEA": name,
        "QS_ID": id_no.upper().strip(),
        "QS_IDA": id_no.upper().strip(),
        "answer": captcha_text,
        "answerA": captcha_text,
        "_csrf": tokens["_csrf"],
        "Authorization": f"Bearer {jwt_token}",
    }
    r = session.post(
        QUERY_URL,
        data=payload,
        headers={"Referer": HOME_URL},
        timeout=15,
    )
    check_blocked(r)
    r.raise_for_status()
    # 偵測驗證碼錯誤 / token 過期
    if "驗證碼" in r.text and ("錯誤" in r.text or "不正確" in r.text):
        raise ValueError("captcha_invalid")
    # debug log: 萃取 body 主要內容（去除 head/script/style/link，留下結果區塊）
    import logging
    _log = logging.getLogger("vetting.npa")
    try:
        _soup = BeautifulSoup(r.text, "html.parser")
        for tag in _soup(["script", "style", "link", "meta", "noscript"]):
            tag.decompose()
        body_html = (_soup.body and str(_soup.body)) or r.text
    except Exception:
        body_html = r.text
    # 切成 4 段每段 ~3500 字方便 log 不被截斷
    chunk_size = 3500
    for i in range(0, min(len(body_html), 14000), chunk_size):
        _log.warning("=== NPA body chunk %d ===\n%s", i // chunk_size + 1, body_html[i:i + chunk_size])
    return parse_results(r.text)
