"""法務部調查局通緝/嫌疑人名單 (Crimes_List) 抓取與過濾。

頁面結構：
- GET /Crimes/Crimes_List → HTML（無驗證碼）
- 每筆是 <div class="card crimes-card ...">
  - <a href="/Crimes/Crimes_Detail?uid=N">
    - <img src="/FileUploads/prisoner_pic/...">  # 照片
    - <h5 class="card-title">姓名</h5>
    - <div>英文名</div>
    - <div>遮蔽身分證 e.g. A12336****</div>
    - <div><i class="fas fa-birthday-cake"/> 出生年</div>
"""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .errors import check_blocked
from .http_client import make_session

LIST_URL = "https://www.mjib.gov.tw/Crimes/Crimes_List"
BASE = "https://www.mjib.gov.tw"


def parse_list(html: str, base_url: str = LIST_URL) -> list:
    """解析 Crimes_List HTML → list of dicts。"""
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for card in soup.select(".crimes-card"):
        a = card.find("a", href=True)
        if not a:
            continue
        img = card.find("img")
        title = card.select_one(".card-title")
        if not title:
            continue
        # card-body 內 <h5 title> + 多個 <div>
        body = card.select_one(".card-body") or card
        divs = body.find_all("div")
        # 第一個 <div> 通常是英文名（class small text-muted）；第二個是遮蔽身分證；最後一個是出生年
        eng_name = divs[0].get_text(strip=True) if len(divs) > 0 else ""
        masked_id = divs[1].get_text(strip=True) if len(divs) > 1 else ""
        # 出生年：通常含 fa-birthday-cake icon
        birth = ""
        for d in divs:
            if d.find("i", class_=lambda c: c and "birthday" in c):
                birth = d.get_text(strip=True)
                break
        entries.append({
            "name": title.get_text(strip=True),
            "eng_name": eng_name,
            "masked_id": masked_id,
            "birth": birth,
            "photo_url": urljoin(base_url, img["src"]) if img and img.get("src") else "",
            "detail_url": urljoin(base_url, a["href"]),
        })
    return entries


def search_by_name_in_html(html: str, name_query: str) -> list:
    """從 HTML 過濾出姓名包含 name_query 的列。"""
    if not name_query:
        return []
    return [e for e in parse_list(html) if name_query in e["name"]]


def search_by_name(name_query: str, session=None) -> list:
    """live：GET Crimes_List → 過濾。"""
    if session is None:
        session = make_session()
    r = session.get(LIST_URL, timeout=15)
    check_blocked(r)
    r.raise_for_status()
    return search_by_name_in_html(r.text, name_query)
