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


def parse_results(html: str) -> list:
    """解析 doCEQuery 結果頁。

    結構未知（需實機查一次才知道），目前用寬鬆 selector 嘗試多種命中：
    - <div class="result-item">
    - <table> with rows
    - 至少回 [{name, crime, detail_url}, ...] 或空 list。
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    # 寬鬆 selector：找含「姓名/罪名」字串的卡片或行
    for sel in [".result-item", ".wanted-card", ".card", "tr"]:
        items = soup.select(sel)
        for item in items:
            text = item.get_text(" ", strip=True)
            if not text or len(text) < 5:
                continue
            # 簡易啟發式：含「罪/名」字樣再撈
            if any(kw in text for kw in ("罪", "通緝", "嫌疑")):
                name_el = item.select_one(".name, h3, .title, td:nth-child(2)")
                crime_el = item.select_one(".crime, .charge, td:nth-child(3)")
                link = item.find("a")
                results.append({
                    "name": name_el.get_text(strip=True) if name_el else "",
                    "crime": crime_el.get_text(strip=True) if crime_el else text[:80],
                    "detail_url": urljoin(QUERY_URL, link["href"]) if link and link.get("href") else "",
                })
        if results:
            break
    return results


# ─────────────────────────────────────────────
# Live IO functions
# ─────────────────────────────────────────────

def fetch_home_state(session=None) -> tuple:
    """打首頁，回 (tokens_dict, session)。"""
    if session is None:
        session = make_session()
    r = session.get(HOME_URL, timeout=10)
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
    r.raise_for_status()
    # 偵測驗證碼錯誤 / token 過期
    if "驗證碼" in r.text and ("錯誤" in r.text or "不正確" in r.text):
        raise ValueError("captcha_invalid")
    return parse_results(r.text)
