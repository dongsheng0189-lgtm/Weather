"""司法院判決書系統 (judgment.judicial.gov.tw/FJUD/) 抓取與解析。

頁面結構（實機 recon 結果）：
- 首頁 default.aspx 是 ASP.NET form，含 __VIEWSTATE/__EVENTVALIDATION 等 hidden
- POST 表單 + txtKW 搜尋字詞 → 回應 HTML 含 <iframe src="qryresultlst.aspx?...">
- 結果列表頁是表格，每兩列一筆（奇數列 metadata + 偶數列摘要）
- 詳情頁 data.aspx 是完整判決書 HTML
- 全程無驗證碼
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .errors import check_blocked
from .http_client import make_session

HOME_URL = "https://judgment.judicial.gov.tw/FJUD/default.aspx"
BASE = "https://judgment.judicial.gov.tw/FJUD/"

DEEP_KEYWORDS = ["詐欺", "洗錢", "有期徒刑", "定讞", "毒品", "搶劫", "偷竊"]
# 司法院常見人名遮罩格式：姓 + ○○ 或 ○○○ (e.g., 王○○, 丙○○○)
MASKED_NAME_RE = re.compile(r"[一-龥]○{1,3}")
# 遮蔽身分證 / 居留證：A + 1或2 + 部分數字 + * + 可能的尾數字
# 涵蓋常見格式：A12345****、A123456****、A1******8、A1234****9
MASKED_ID_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{0,8}\*+\d{0,3}(?![A-Za-z0-9])")


def _id_matches(masked: str, user_id: str) -> bool:
    """模糊比對遮蔽 ID 與使用者輸入 ID。

    masked 例：A12345****  → 揭露 'A12345' 與 ''
              A1******8   → 揭露 'A1' 與 '8'
    """
    if not user_id:
        return True  # 沒輸入 ID → 視為相符（不限制）
    user_id = user_id.upper().strip()
    star_idx = masked.find("*")
    if star_idx < 0:
        return masked == user_id
    prefix = masked[:star_idx]
    suffix = masked[star_idx:].lstrip("*")
    if prefix and not user_id.startswith(prefix):
        return False
    if suffix and not user_id.endswith(suffix):
        return False
    return True


def parse_home_state(html: str, base_url: str = HOME_URL) -> dict:
    """從首頁 HTML 解析出 form_action + 所有 hidden 欄位。"""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if not form:
        raise ValueError("無法在頁面找到 <form>")
    form_action = urljoin(base_url, form.get("action", ""))
    hidden_fields = {}
    for inp in form.find_all("input"):
        n = inp.get("name")
        if n:
            hidden_fields[n] = inp.get("value", "")
    return {
        "form_action": form_action,
        "hidden_fields": hidden_fields,
    }


def parse_results(html: str, base_url: str = HOME_URL) -> list:
    """解析 qryresultlst.aspx 的結果列表頁。

    每兩列 <tr> 一筆：
      奇數列 cells: [序號, 「法院 + 案號 + 類型 + 大小」, 日期, 案由]
      偶數列：摘要（colspan=4）
    詳情連結是奇數列第二欄裡的 <a href="data.aspx?...">。
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue  # skip header / 摘要列
        link = cells[1].find("a")
        if not link or "data.aspx" not in (link.get("href") or ""):
            continue
        full_text = link.get_text(strip=True)
        # full_text 例：「臺灣臺中地方法院 114 年度 金訴 字第 3392 號刑事判決」
        rows.append({
            "subject": cells[3].get_text(strip=True),
            "case_no": full_text,
            "date": cells[2].get_text(strip=True),
            "court": _extract_court(full_text),
            "detail_url": urljoin(base_url, link.get("href", "").lstrip("./")),
        })
    return rows


def _extract_court(case_full_text: str) -> str:
    """從『臺灣臺中地方法院 114 年度 金訴 字第 3392 號刑事判決』取出『臺灣臺中地方法院』。"""
    m = re.match(r"([^\s]+(?:法院|地方法院|高等法院))", case_full_text)
    return m.group(1) if m else ""


def scan_detail_html(html: str, user_id: str = "") -> dict:
    """掃描判決書 HTML，回關鍵字命中清單、遮蔽人名 / 遮蔽 ID 清單。

    user_id 給定時計算 id_match：
      True  → 至少有一個 masked_id 跟 user_id 前綴/尾綴一致
      False → 找到 masked_id 但全部都跟 user_id 不符
      None  → 詳情頁完全沒抓到 masked_id（無從判斷）
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    keyword_hits = [k for k in DEEP_KEYWORDS if k in text]
    masked_names = sorted(set(MASKED_NAME_RE.findall(text)))
    masked_ids = sorted(set(MASKED_ID_RE.findall(text)))
    if not user_id:
        id_match = None
    elif not masked_ids:
        id_match = None
    else:
        id_match = any(_id_matches(m, user_id) for m in masked_ids)
    return {
        "keyword_hits": keyword_hits,
        "masked_names": masked_names,
        "masked_ids": masked_ids,
        "id_match": id_match,
    }


def fetch_home_state(session=None) -> tuple:
    """live：打司法院首頁，回 (state_dict, session)。"""
    if session is None:
        session = make_session()
    r = session.get(HOME_URL, timeout=10)
    check_blocked(r)
    r.raise_for_status()
    return parse_home_state(r.text, base_url=HOME_URL), session


def submit_search(session, state: dict, name: str) -> list:
    """送出查詢，回 results list（已解析）。"""
    payload = dict(state["hidden_fields"])
    payload["txtKW"] = name
    r = session.post(state["form_action"], data=payload,
                     headers={"Referer": HOME_URL}, timeout=15)
    check_blocked(r)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    iframe = soup.find("iframe")
    if not iframe or not iframe.get("src"):
        return []
    result_url = urljoin(HOME_URL, iframe["src"])
    r2 = session.get(result_url, headers={"Referer": HOME_URL}, timeout=15)
    check_blocked(r2)
    r2.raise_for_status()
    return parse_results(r2.text, base_url=result_url)


def scan_detail(session, detail_url: str, user_id: str = "") -> dict:
    """live：抓詳情頁，回 scan_detail_html 結果。"""
    r = session.get(detail_url, timeout=15,
                    headers={"Referer": HOME_URL})
    check_blocked(r)
    r.raise_for_status()
    return scan_detail_html(r.text, user_id=user_id)
