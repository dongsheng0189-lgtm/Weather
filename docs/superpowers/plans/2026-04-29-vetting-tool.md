# 人員查詢 (Vetting) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 webapp 增加「人員查詢」模組：純 HTTP 查詢司法院刑事判決（含驗證碼接力 + 深度掃描）+ 法務部調查局通緝/嫌疑人名單，初期 admin only。

**Architecture:** 新 blueprint `/vetting/`，無 DB/storage 依賴。後端用 `requests` + `BeautifulSoup` 純 HTTP 抓取，無 Playwright/Chromium。司法院驗證碼透過「網頁接力」UX：後端拿 captcha image → 顯示給使用者 → 使用者輸入 → 後端帶 cookie 繼續送出。完整套用反 bot 防護（真人 headers / 節流 / Flask-Limiter / robots.txt 尊重）。

**Tech Stack:** Flask + requests + beautifulsoup4 + flask-limiter（既有）。

**Spec:** `docs/superpowers/specs/2026-04-29-vetting-tool-design.md`

---

## File Structure

**Create:**
- `app_unified/vetting/__init__.py` — blueprint object
- `app_unified/vetting/routes.py` — 三個 HTTP routes + query_state dict
- `app_unified/vetting/http_client.py` — `make_session()` 真人 headers helper
- `app_unified/vetting/judicial.py` — 司法院 commonQuery.jsp 抓取/解析
- `app_unified/vetting/mjib.py` — 法務部調查局 Crimes_List 抓取/過濾
- `app_unified/templates/vetting/index.html` — 姓名輸入頁
- `app_unified/templates/vetting/captcha.html` — 驗證碼接力頁
- `app_unified/templates/vetting/result.html` — 結果頁
- `app_unified/tests/test_vetting_http.py` — http_client tests
- `app_unified/tests/test_vetting_judicial.py` — judicial.py tests + 測試 fixture HTML
- `app_unified/tests/test_vetting_mjib.py` — mjib.py tests + 測試 fixture HTML
- `app_unified/tests/test_vetting_routes.py` — routes integration tests
- `app_unified/tests/fixtures/vetting/judicial_home.html` — 司法院首頁 HTML 樣本（recon 後存）
- `app_unified/tests/fixtures/vetting/judicial_results.html` — 結果頁 HTML 樣本
- `app_unified/tests/fixtures/vetting/judicial_detail.html` — 判決書詳情頁 HTML 樣本（含關鍵字 + 馬賽克 ID）
- `app_unified/tests/fixtures/vetting/mjib_list.html` — MJIB 列表頁 HTML 樣本

**Modify:**
- `app_unified/requirements.txt` — 加 `beautifulsoup4>=4.12`
- `app_unified/features.py` — 加 vetting entry
- `app_unified/app.py` — register blueprint
- `app_unified/templates/base.html` — 確認 nav 會用 `visible_features` 渲染（既有應該已是）

---

## Task 1: 模組 scaffolding + feature gate

**Files:**
- Create: `app_unified/vetting/__init__.py`, `app_unified/vetting/routes.py`, `app_unified/templates/vetting/index.html`
- Modify: `app_unified/features.py`, `app_unified/app.py`, `app_unified/requirements.txt`
- Test: `app_unified/tests/test_vetting_routes.py`

- [ ] **Step 1: 加 dependency**

`app_unified/requirements.txt` 末尾加：
```
beautifulsoup4>=4.12
```

跑：
```bash
cd /home/hirain0126/projects/webapp/app_unified && source ../venv/bin/activate
pip install beautifulsoup4>=4.12
```

- [ ] **Step 2: 寫 failing test for module gate**

Create `app_unified/tests/test_vetting_routes.py`:

```python
"""Tests for vetting blueprint routes + feature gate."""
import uuid
import pytest
from models import User
from extensions import db


def _mk_user(db_session, username=None, role="user", root_admin=False):
    u = User(
        username="admin" if root_admin else (username or f"u_{uuid.uuid4().hex[:6]}"),
        role=role,
        store="B",
        is_active=True,
    )
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_vetting_index_requires_auth(client):
    res = client.get("/vetting/")
    # 未登入會被 flask-login 導去 login
    assert res.status_code in (302, 401)


def test_vetting_index_404_for_non_admin(app, client, db_session):
    u = _mk_user(db_session)  # 非 admin
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 404


def test_vetting_index_200_for_root_admin(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/vetting/")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "人員查詢" in body
    # 表單只有姓名一個 input
    assert 'name="name"' in body
```

- [ ] **Step 3: Run tests，確認 fail（blueprint 還沒建）**

```bash
python -m pytest tests/test_vetting_routes.py -v
```
Expected: FAIL with 404 even for admin (route doesn't exist yet)

- [ ] **Step 4: Create `app_unified/vetting/__init__.py`**

```python
from .routes import vetting_bp

__all__ = ["vetting_bp"]
```

- [ ] **Step 5: Create `app_unified/vetting/routes.py` (minimal)**

```python
from flask import Blueprint, render_template
from flask_login import login_required

vetting_bp = Blueprint("vetting", __name__, url_prefix="/vetting")


@vetting_bp.before_request
def _gate():
    """Feature gate：只有具 'vetting' feature 的使用者可進入。
    admin (is_root_admin) bypass 由 user_has_feature 內建。"""
    from flask_login import current_user
    from features import user_has_feature
    from flask import abort
    if current_user.is_authenticated and not user_has_feature(current_user, "vetting"):
        abort(404)


@vetting_bp.route("/")
@login_required
def index():
    return render_template("vetting/index.html")
```

- [ ] **Step 6: Create `app_unified/templates/vetting/index.html`**

```html
{% extends "base.html" %}
{% block title %}人員查詢{% endblock %}
{% block content %}
<div style="max-width:560px; margin:1rem auto; padding:0 .75rem;">
  <h1 style="margin-bottom:.75rem;">🛡️ 人員查詢</h1>
  <p style="color:#666; font-size:.9rem;">將同時查詢司法院刑事判決與法務部調查局通緝名單。</p>
  <form id="vet-form" style="margin-top:1rem;">
    <label style="display:block; margin-bottom:.4rem; font-size:.9rem;">姓名</label>
    <input type="text" name="name" id="vet-name" required
           style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.75rem;">
    <button type="submit" class="btn btn-primary" style="width:100%; padding:.6rem;">開始查詢</button>
  </form>
  <div id="vet-status" style="margin-top:1rem; color:#666; font-size:.9rem;"></div>
</div>
<script>
document.getElementById('vet-form').addEventListener('submit', function(e) {
  e.preventDefault();
  var name = document.getElementById('vet-name').value.trim();
  if (!name) return;
  document.getElementById('vet-status').textContent = '查詢中…（將顯示驗證碼）';
  // 實作會在 Task 8 補完整流程
});
</script>
{% endblock %}
```

- [ ] **Step 7: 加 features.py entry**

`app_unified/features.py` 的 `AVAILABLE_FEATURES` list 加：

```python
    {
        "key": "vetting",
        "label": "人員查詢",
        "emoji": "🛡️",
        "url": "/vetting/",
        "enabled": True,
    },
```

- [ ] **Step 8: Register blueprint in `app_unified/app.py`**

找到 `app.register_blueprint(mynotes_bp)` 那行，下面加：

```python
    from vetting import vetting_bp
    app.register_blueprint(vetting_bp)
```

- [ ] **Step 9: Run tests**

```bash
python -m pytest tests/test_vetting_routes.py -v
```
Expected: 3 passed

- [ ] **Step 10: Run all tests for regression**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 89 passed (86 existing + 3 new)

- [ ] **Step 11: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/vetting/ app_unified/templates/vetting/ app_unified/features.py app_unified/app.py app_unified/requirements.txt app_unified/tests/test_vetting_routes.py
git commit -m "feat(vetting): scaffolding with admin-gated blueprint + nav entry

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: HTTP politeness helper (`http_client.py`)

**Files:**
- Create: `app_unified/vetting/http_client.py`
- Test: `app_unified/tests/test_vetting_http.py`

- [ ] **Step 1: 寫 failing tests**

Create `app_unified/tests/test_vetting_http.py`:

```python
"""Tests for vetting/http_client.py — 真人 headers + politeness helpers."""
import pytest
from vetting.http_client import make_session, BROWSER_HEADERS, throttle_sleep


def test_make_session_has_browser_headers():
    s = make_session()
    assert "Mozilla/5.0" in s.headers["User-Agent"]
    assert "zh-TW" in s.headers["Accept-Language"]
    assert "python-requests" not in s.headers["User-Agent"]


def test_browser_headers_include_essentials():
    for k in ("User-Agent", "Accept", "Accept-Language", "Accept-Encoding"):
        assert k in BROWSER_HEADERS


def test_throttle_sleep_in_range(monkeypatch):
    """節流 sleep 應該落在 0.5~1.2 秒區間"""
    captured = []
    monkeypatch.setattr("time.sleep", lambda s: captured.append(s))
    for _ in range(10):
        throttle_sleep()
    assert all(0.5 <= s <= 1.2 for s in captured)
```

- [ ] **Step 2: Run tests，確認 fail**

```bash
python -m pytest tests/test_vetting_http.py -v
```
Expected: ImportError

- [ ] **Step 3: Create `app_unified/vetting/http_client.py`**

```python
"""HTTP 真人化 headers + 節流 helper（給 vetting 各 source 共用）。"""
import random
import time

import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_THROTTLE_MIN_S = 0.5
_THROTTLE_MAX_S = 1.2


def make_session() -> requests.Session:
    """回傳預設帶真人 headers 的 Session。"""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    return s


def throttle_sleep():
    """連續對外請求之間呼叫，隨機 sleep 模擬人手點擊。"""
    time.sleep(random.uniform(_THROTTLE_MIN_S, _THROTTLE_MAX_S))
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_vetting_http.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/vetting/http_client.py app_unified/tests/test_vetting_http.py
git commit -m "feat(vetting): http_client with browser headers + throttle helper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 司法院 captcha state 抓取

**Files:**
- Create: `app_unified/vetting/judicial.py`
- Create: `app_unified/tests/fixtures/vetting/judicial_home.html`
- Test: `app_unified/tests/test_vetting_judicial.py`

**重要**：本 task 需要先實機抓 commonQuery.jsp HTML 才能寫 parser。

- [ ] **Step 1: Recon — fetch real 司法院首頁，存 fixture**

```bash
cd /home/hirain0126/projects/webapp/app_unified
mkdir -p tests/fixtures/vetting
curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0" \
  "https://judgment.supreme.court.gov.tw/wkw/commonQuery.jsp" \
  > tests/fixtures/vetting/judicial_home.html
ls -la tests/fixtures/vetting/judicial_home.html
```

開檔案看 HTML，**人工確認**並記下：
- 表單 `<form>` 的 `action` URL（送出位置）
- captcha image 的 `<img>` 的 `src`（可能是相對路徑）
- 所有 `<input type="hidden">` 的 name + value（要原封不動帶回去）
- 姓名輸入欄的 `name`
- 法院類別「全台法院」radio/checkbox 的 `name` + `value`
- 判別類別「刑事」radio/checkbox 的 `name` + `value`
- 驗證碼輸入欄的 `name`

> 如果頁面已改版（404 / 換結構），**STOP** 回報 BLOCKED，更新 spec。

- [ ] **Step 2: 寫 failing tests against fixture**

Create `app_unified/tests/test_vetting_judicial.py`:

```python
"""Tests for vetting/judicial.py — 司法院抓取/解析。
所有測試只用本地 fixture HTML，不打外部站。
"""
import os
import pytest
from unittest.mock import patch, MagicMock
from vetting.judicial import (
    parse_home_state,
    HOME_URL,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vetting")


def _read_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def test_parse_home_state_extracts_form_fields():
    """從首頁 HTML 解析出表單必要欄位 + captcha image URL。"""
    html = _read_fixture("judicial_home.html")
    state = parse_home_state(html, base_url=HOME_URL)
    # 必須有的 keys
    assert "form_action" in state  # 表單送到的 URL（absolute）
    assert "captcha_url" in state  # captcha image absolute URL
    assert "hidden_fields" in state  # dict of hidden inputs
    assert "name_field" in state  # 姓名 input 的 name 屬性
    # 至少有一個 hidden field（JSP 站通常有 session token）
    assert isinstance(state["hidden_fields"], dict)
```

- [ ] **Step 3: Run tests，確認 fail**

```bash
python -m pytest tests/test_vetting_judicial.py -v
```
Expected: ImportError

- [ ] **Step 4: Create `app_unified/vetting/judicial.py`（先做 parse_home_state）**

讀取 Step 1 的 HTML，依實際 form 結構填欄位名稱。下面範本以「假設 form action="qryresult.jsp"，姓名 input name='v_court_class' / 'name'，captcha image src='captcha.jpg?...'」為例，實作時要對齊真實 HTML：

```python
"""司法院 commonQuery.jsp 抓取/解析。"""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

HOME_URL = "https://judgment.supreme.court.gov.tw/wkw/commonQuery.jsp"


def parse_home_state(html: str, base_url: str = HOME_URL) -> dict:
    """從首頁 HTML 解析出送表單需要的所有欄位 + captcha image URL。

    回傳：
      {
        "form_action": absolute URL,
        "captcha_url": absolute URL,
        "hidden_fields": {name: value, ...},
        "name_field": str,  # 姓名 input 的 name 屬性
      }
    """
    soup = BeautifulSoup(html, "html.parser")
    # 找出主要查詢表單（依實際 HTML 調整 selector）
    form = soup.find("form")
    if not form:
        raise ValueError("無法在頁面找到 <form>")
    form_action = urljoin(base_url, form.get("action", ""))

    # 抓所有 hidden inputs
    hidden_fields = {}
    for inp in form.find_all("input", type="hidden"):
        n = inp.get("name")
        v = inp.get("value", "")
        if n:
            hidden_fields[n] = v

    # 抓 captcha image
    captcha_img = soup.find("img", id=lambda x: x and "captcha" in x.lower())
    if not captcha_img:
        # fallback：找含 captcha keyword 的 src
        captcha_img = soup.find("img", src=lambda x: x and "captcha" in x.lower())
    if not captcha_img:
        raise ValueError("找不到 captcha 圖片")
    captcha_url = urljoin(base_url, captcha_img["src"])

    # 姓名 input — 依實機 HTML 確認 name 屬性
    name_input = soup.find("input", attrs={"name": "v_court_class"}) or \
                 soup.find("input", attrs={"name": "name"}) or \
                 soup.find("input", attrs={"placeholder": lambda x: x and "姓名" in x})
    if not name_input:
        raise ValueError("找不到姓名 input")

    return {
        "form_action": form_action,
        "captcha_url": captcha_url,
        "hidden_fields": hidden_fields,
        "name_field": name_input.get("name"),
    }
```

> **實作筆記**：上面 selector 可能要根據真實 HTML 調整。**首要原則**：parse_home_state 只回客觀資料，不寫死「司法院 input name 一定叫 X」。

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_vetting_judicial.py -v
```
Expected: PASS（如果 selector 對；不對就調整 selector 直到 PASS）

- [ ] **Step 6: 加 fetch_home_state（live）函式**

繼續編輯 `judicial.py`，加：

```python
from .http_client import make_session


def fetch_home_state(session=None) -> tuple:
    """打司法院首頁，回 (state_dict, captcha_bytes, captcha_content_type, session)。

    session 可外部傳入以共享 cookie；不傳就 make_session 建一個。
    captcha_bytes 是 image 的 raw bytes（給前端 base64 用）。
    """
    if session is None:
        session = make_session()
    r = session.get(HOME_URL, timeout=10)
    r.raise_for_status()
    state = parse_home_state(r.text, base_url=HOME_URL)
    # 抓 captcha image
    img = session.get(state["captcha_url"], timeout=10)
    img.raise_for_status()
    return state, img.content, img.headers.get("Content-Type", "image/jpeg"), session
```

> 不寫 unit test for `fetch_home_state` — 它只是 IO + parse_home_state 組合，下層已測。

- [ ] **Step 7: 跑全測**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 90 passed

- [ ] **Step 8: Commit**

```bash
cd /home/hirain0126/projects/webapp
git add app_unified/vetting/judicial.py app_unified/tests/test_vetting_judicial.py app_unified/tests/fixtures/vetting/judicial_home.html
git commit -m "feat(vetting): parse 司法院 home form state + captcha image fetch

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 司法院 search submit + parse 結果列表

**Files:**
- Modify: `app_unified/vetting/judicial.py`
- Create: `app_unified/tests/fixtures/vetting/judicial_results.html`
- Modify: `app_unified/tests/test_vetting_judicial.py`

- [ ] **Step 1: Recon — 取一份結果頁 HTML**

實機跑一次（auto + 人工輸入 captcha）：

```bash
cd /home/hirain0126/projects/webapp/app_unified && source ../venv/bin/activate
python -c "
from vetting.judicial import fetch_home_state
import base64
state, img, ct, s = fetch_home_state()
with open('/tmp/captcha.jpg', 'wb') as f: f.write(img)
print('captcha saved /tmp/captcha.jpg')
print('form_action:', state['form_action'])
print('hidden_fields:', state['hidden_fields'])
print('name_field:', state['name_field'])
print('cookies:', dict(s.cookies))
"
```

開 `/tmp/captcha.jpg` 看驗證碼，肉眼讀出來。然後手動 POST：

```bash
# 把 <CAPTCHA> 換成讀出來的字
# 把欄位名換成實機抓到的 (name_field, captcha 欄名等)
curl -s -X POST "<form_action_url_from_above>" \
  -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0" \
  -b "<cookies>" \
  -d "<name_field>=<姓名>&v_court=ALL&law_class=M&captcha=<CAPTCHA>&<other_hidden_fields>" \
  > tests/fixtures/vetting/judicial_results.html

# 確認結果頁有資料
grep -c "案由\|字號" tests/fixtures/vetting/judicial_results.html
```

> 如果第一次驗證碼讀錯，重跑 fetch_home_state 拿新驗證碼再試。
> 如果結果頁是「請重新搜尋」/「驗證碼錯誤」，重跑。
> 結果頁要含至少 1-3 筆判決才能寫 parser。

- [ ] **Step 2: 寫 failing test**

Append to `tests/test_vetting_judicial.py`:

```python
def test_parse_results_extracts_rows():
    """結果頁 HTML 解析出 N 筆判決，每筆含案由/字號/日期/法院/detail_url。"""
    from vetting.judicial import parse_results
    html = _read_fixture("judicial_results.html")
    rows = parse_results(html, base_url=HOME_URL)
    assert isinstance(rows, list)
    assert len(rows) >= 1
    r = rows[0]
    assert all(k in r for k in ("subject", "case_no", "date", "court", "detail_url"))
    # detail_url 必須是 absolute URL
    assert r["detail_url"].startswith("http")
```

- [ ] **Step 3: Run test，確認 fail（parse_results 不存在）**

```bash
python -m pytest tests/test_vetting_judicial.py::test_parse_results_extracts_rows -v
```
Expected: ImportError or AttributeError

- [ ] **Step 4: 在 `judicial.py` 加 parse_results**

依 Step 1 抓到的真實 HTML 結構寫（以下範本以「結果是 `<table>` + 每列 `<tr>`，欄位順序：序號/案由/字號/日期/法院」為假設）：

```python
def parse_results(html: str, base_url: str = HOME_URL) -> list:
    """解析搜尋結果頁，回 list of dicts。"""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    # 依實際 HTML 結構調整 selector
    for tr in soup.select("table tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue  # 跳過 header / 空列
        link = cells[1].find("a") if len(cells) > 1 else None
        if not link:
            continue
        rows.append({
            "subject": cells[1].get_text(strip=True),     # 案由
            "case_no": cells[2].get_text(strip=True),     # 字號
            "date": cells[3].get_text(strip=True),        # 日期
            "court": cells[4].get_text(strip=True) if len(cells) > 4 else "",
            "detail_url": urljoin(base_url, link.get("href", "")),
        })
    return rows
```

> **實作筆記**：依真實 HTML 結構調 selector + cell index。**測試 PASS = 設計正確**。

- [ ] **Step 5: Run test**

```bash
python -m pytest tests/test_vetting_judicial.py::test_parse_results_extracts_rows -v
```
Expected: PASS

- [ ] **Step 6: 加 submit_search 函式（live IO）**

繼續編輯 `judicial.py`：

```python
def submit_search(session, state: dict, name: str, captcha_text: str,
                  court_class: str = "ALL", law_class: str = "M") -> list:
    """送出搜尋表單，回 results list。

    court_class: 法院類別代碼（'ALL' = 全台）— 依實機調整
    law_class: 判別類別代碼（'M' = 刑事）— 依實機調整
    """
    payload = dict(state["hidden_fields"])
    payload[state["name_field"]] = name
    # captcha 欄位名 + court/law class 欄位名 — 依實機調整
    payload["v_yy"] = captcha_text  # placeholder：實際 name 由 Step 1 確認
    # ... 補 court_class / law_class 對應的 form 欄位

    r = session.post(state["form_action"], data=payload, timeout=15)
    r.raise_for_status()

    # 偵測驗證碼錯誤訊息（依實機回應字串調整）
    if "驗證碼" in r.text and ("錯誤" in r.text or "不正確" in r.text):
        raise ValueError("captcha_invalid")

    return parse_results(r.text, base_url=HOME_URL)
```

> 不寫 unit test，但完整流程會在 Task 8 routes integration test 測到。

- [ ] **Step 7: Run all tests**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 91 passed

- [ ] **Step 8: Commit**

```bash
git add app_unified/vetting/judicial.py app_unified/tests/test_vetting_judicial.py app_unified/tests/fixtures/vetting/judicial_results.html
git commit -m "feat(vetting): submit 司法院 search + parse results table

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 司法院 詳情頁深度掃描

**Files:**
- Modify: `app_unified/vetting/judicial.py`
- Create: `app_unified/tests/fixtures/vetting/judicial_detail.html`
- Modify: `app_unified/tests/test_vetting_judicial.py`

- [ ] **Step 1: Recon — 取一份判決書詳情頁**

從 Task 4 的結果頁拿一個 detail URL，curl 抓回來：

```bash
curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0" \
  -b "<cookies_from_task_4_session>" \
  "<detail_url>" \
  > tests/fixtures/vetting/judicial_detail.html
```

確認 fixture 內**含**：
- 至少一個關鍵字（詐欺/洗錢/有期徒刑/定讞）
- 至少一個遮蔽 ID 形式（如 `A12****567`）

如果隨機抽到的判決書不含這些，再從結果頁挑另一個。

- [ ] **Step 2: 寫 failing tests**

Append to `tests/test_vetting_judicial.py`:

```python
def test_scan_detail_finds_keywords_and_masked_ids():
    from vetting.judicial import scan_detail_html
    html = _read_fixture("judicial_detail.html")
    result = scan_detail_html(html)
    assert "keyword_hits" in result
    assert "masked_ids" in result
    # fixture 是事先選的，至少含 1 個關鍵字
    assert len(result["keyword_hits"]) >= 1
    # 至少有 1 個遮蔽 ID
    assert len(result["masked_ids"]) >= 1
    # 每個遮蔽 ID 都有 *
    for mid in result["masked_ids"]:
        assert "*" in mid


def test_scan_detail_clean_doc_no_hits():
    """無關鍵字、無遮蔽 ID 的乾淨內文 → 空命中。"""
    from vetting.judicial import scan_detail_html
    html = "<html><body><p>這是一份純民事判決書內文，沒有刑事關鍵字。</p></body></html>"
    result = scan_detail_html(html)
    assert result["keyword_hits"] == []
    assert result["masked_ids"] == []
```

- [ ] **Step 3: Run tests，確認 fail**

```bash
python -m pytest tests/test_vetting_judicial.py -v
```
Expected: 2 new tests fail with AttributeError

- [ ] **Step 4: 在 `judicial.py` 加 scan_detail_html + scan_detail**

```python
import re

DEEP_KEYWORDS = ["詐欺", "洗錢", "有期徒刑", "定讞"]

# 馬賽克 ID：前 1 字母 + 1 位數字 + 2-3 數字 + 連續 *(2~5) + 3-4 數字
# 示例：A12***567、B1234**789
MASKED_ID_RE = re.compile(r"[A-Z][12]\d{1,3}\*{2,5}\d{3,4}")


def scan_detail_html(html: str) -> dict:
    """掃描判決書 HTML 內文，回關鍵字命中清單與遮蔽 ID 清單。"""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    keyword_hits = [k for k in DEEP_KEYWORDS if k in text]
    masked_ids = list(set(MASKED_ID_RE.findall(text)))
    return {"keyword_hits": keyword_hits, "masked_ids": masked_ids}


def scan_detail(session, detail_url: str) -> dict:
    """live：抓詳情頁，呼叫 scan_detail_html。"""
    r = session.get(detail_url, timeout=15)
    r.raise_for_status()
    return scan_detail_html(r.text)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_vetting_judicial.py -v
```
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app_unified/vetting/judicial.py app_unified/tests/test_vetting_judicial.py app_unified/tests/fixtures/vetting/judicial_detail.html
git commit -m "feat(vetting): scan judicial detail for keywords + masked IDs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 法務部調查局 (MJIB) Crimes_List 抓取

**Files:**
- Create: `app_unified/vetting/mjib.py`
- Create: `app_unified/tests/fixtures/vetting/mjib_list.html`
- Create: `app_unified/tests/test_vetting_mjib.py`

- [ ] **Step 1: Recon — 抓 Crimes_List**

```bash
curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0" \
  "https://www.mjib.gov.tw/Crimes/Crimes_List" \
  > tests/fixtures/vetting/mjib_list.html
ls -la tests/fixtures/vetting/mjib_list.html
```

開檔看看：
- 是否需要表單參數（地區）？看 `<form>` 內容
- 列表結構是 `<table>` 或 `<div>`？
- 每筆有：姓名 / 罪名 / 照片 URL / 詳情連結？

如果預設 GET 已含全部地區，直接用。
如果需要 query string 帶地區，記下參數名 + 值。
如果需要 POST，記下 form 欄位。

- [ ] **Step 2: 寫 failing tests**

Create `app_unified/tests/test_vetting_mjib.py`:

```python
"""Tests for vetting/mjib.py — 法務部調查局通緝/嫌疑人名單。"""
import os
import pytest
from vetting.mjib import parse_list, search_by_name_in_html

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vetting")


def _read_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def test_parse_list_returns_entries():
    """解析 Crimes_List 頁面，回 list of dicts，至少 1 筆。"""
    html = _read_fixture("mjib_list.html")
    entries = parse_list(html)
    assert isinstance(entries, list)
    assert len(entries) >= 1
    e = entries[0]
    assert all(k in e for k in ("name", "crime", "photo_url", "detail_url"))


def test_search_by_name_filters_match_only():
    """過濾僅回姓名含查詢字的列。"""
    html = _read_fixture("mjib_list.html")
    entries = parse_list(html)
    if not entries:
        pytest.skip("fixture 沒有資料可測過濾")
    target = entries[0]["name"]
    # 部分字串配對也應命中
    matched = search_by_name_in_html(html, target[:1])
    assert any(e["name"] == target for e in matched)


def test_search_by_name_no_match_returns_empty():
    html = _read_fixture("mjib_list.html")
    matched = search_by_name_in_html(html, "絕對不會有的姓名測試字串XYZ")
    assert matched == []
```

- [ ] **Step 3: Run tests，確認 fail**

```bash
python -m pytest tests/test_vetting_mjib.py -v
```
Expected: ImportError

- [ ] **Step 4: Create `app_unified/vetting/mjib.py`**

依 Step 1 觀察的 HTML 結構填 selector：

```python
"""法務部調查局通緝/嫌疑人名單 (Crimes_List) 抓取與過濾。"""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http_client import make_session

LIST_URL = "https://www.mjib.gov.tw/Crimes/Crimes_List"


def parse_list(html: str, base_url: str = LIST_URL) -> list:
    """解析 Crimes_List HTML，回 list of dicts。"""
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    # 依真實 HTML 結構調整 selector
    # 假設每筆是 <li class="crime-item"> 或 <div class="card">，內含名字/罪名/img
    for item in soup.select(".crime-item, .crime-card, li.list-item"):
        name_el = item.select_one(".name, h3, .title")
        crime_el = item.select_one(".crime, .charge, .reason")
        img = item.find("img")
        link = item.find("a")
        if not name_el:
            continue
        entries.append({
            "name": name_el.get_text(strip=True),
            "crime": crime_el.get_text(strip=True) if crime_el else "",
            "photo_url": urljoin(base_url, img["src"]) if img and img.get("src") else "",
            "detail_url": urljoin(base_url, link["href"]) if link and link.get("href") else "",
        })
    return entries


def search_by_name_in_html(html: str, name_query: str) -> list:
    """從 HTML 過濾出姓名包含 name_query 的列。"""
    return [e for e in parse_list(html) if name_query and name_query in e["name"]]


def search_by_name(name_query: str, session=None) -> list:
    """live：GET Crimes_List → 過濾。"""
    if session is None:
        session = make_session()
    r = session.get(LIST_URL, timeout=15)
    r.raise_for_status()
    return search_by_name_in_html(r.text, name_query)
```

> **實作筆記**：parse_list 的 selectors 必須依 Step 1 真實 HTML 調整。Tests PASS = selector 對。如果頁面是 JS 渲染（純 HTML 看不到資料），STOP 回報 BLOCKED — 純 requests 抓不到。

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_vetting_mjib.py -v
```
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add app_unified/vetting/mjib.py app_unified/tests/test_vetting_mjib.py app_unified/tests/fixtures/vetting/mjib_list.html
git commit -m "feat(vetting): MJIB Crimes_List parse + name filter

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 串接 routes（start / submit）+ query_state + rate limit

**Files:**
- Modify: `app_unified/vetting/routes.py`
- Modify: `app_unified/tests/test_vetting_routes.py`

- [ ] **Step 1: 寫 failing tests for /start endpoint**

Append to `tests/test_vetting_routes.py`:

```python
def test_start_endpoint_returns_query_id_and_captcha(app, client, db_session, monkeypatch):
    """POST /vetting/start → 後端假裝去打司法院首頁 → 回 query_id + captcha base64。"""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)

    fake_state = {
        "form_action": "https://judgment.supreme.court.gov.tw/wkw/qry.jsp",
        "captcha_url": "https://judgment.supreme.court.gov.tw/wkw/captcha.jpg",
        "hidden_fields": {"_csrf": "abc123"},
        "name_field": "v_kw",
    }
    fake_session = MagicMock()
    monkeypatch.setattr(
        "vetting.routes.fetch_home_state",
        lambda: (fake_state, b"FAKE_JPG_BYTES", "image/jpeg", fake_session),
    )

    res = client.post("/vetting/start", json={"name": "王小明"})
    assert res.status_code == 200
    data = res.get_json()
    assert "query_id" in data
    assert "captcha_b64" in data
    # captcha_b64 應該是合法 base64 字串
    import base64
    base64.b64decode(data["captcha_b64"])  # 不丟例外即合法
```

> 注意：`tests/test_vetting_routes.py` 開頭要 `from unittest.mock import MagicMock`。

- [ ] **Step 2: Run test，確認 fail（/start 還沒實作）**

```bash
python -m pytest tests/test_vetting_routes.py::test_start_endpoint_returns_query_id_and_captcha -v
```
Expected: 404 or AttributeError

- [ ] **Step 3: 改 `vetting/routes.py` 加 start endpoint + query_state**

```python
import base64
import secrets
import time

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required

from extensions import limiter

vetting_bp = Blueprint("vetting", __name__, url_prefix="/vetting")

# Process-local 暫存：每筆查詢的 cookie + form state
_QUERY_STATE = {}
_QUERY_TTL = 300  # 5 分鐘


def _gc_expired():
    now = time.time()
    for qid in list(_QUERY_STATE.keys()):
        if _QUERY_STATE[qid].get("expires_at", 0) < now:
            del _QUERY_STATE[qid]


def _put_state(state: dict, session) -> str:
    qid = secrets.token_urlsafe(16)
    _QUERY_STATE[qid] = {
        "state": state,
        "session": session,
        "expires_at": time.time() + _QUERY_TTL,
    }
    _gc_expired()
    return qid


def _take_state(qid: str):
    _gc_expired()
    return _QUERY_STATE.get(qid)


@vetting_bp.before_request
def _gate():
    from flask_login import current_user
    from features import user_has_feature
    if current_user.is_authenticated and not user_has_feature(current_user, "vetting"):
        abort(404)


@vetting_bp.route("/")
@login_required
def index():
    return render_template("vetting/index.html")


# 為了讓測試能 monkey-patch fetch_home_state，這裡明確 import
from vetting.judicial import fetch_home_state, submit_search, scan_detail
from vetting.mjib import search_by_name as mjib_search
from vetting.http_client import throttle_sleep


@vetting_bp.route("/start", methods=["POST"])
@login_required
@limiter.limit("3/minute", key_func=lambda: f"vet-start:{__import__('flask_login').current_user.id}")
def start():
    """打司法院首頁，拿驗證碼回前端。"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        state, captcha_bytes, _ct, session = fetch_home_state()
    except Exception as e:
        return jsonify({"error": "judicial_unavailable", "message": str(e)}), 502
    qid = _put_state(state, session)
    captcha_b64 = base64.b64encode(captcha_bytes).decode("ascii")
    return jsonify({"query_id": qid, "captcha_b64": captcha_b64, "name": name})
```

- [ ] **Step 4: Run test**

```bash
python -m pytest tests/test_vetting_routes.py::test_start_endpoint_returns_query_id_and_captcha -v
```
Expected: PASS

- [ ] **Step 5: 寫 failing test for /submit endpoint**

Append to `tests/test_vetting_routes.py`:

```python
def test_submit_endpoint_returns_results(app, client, db_session, monkeypatch):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)

    # 先模擬 /start 把 state 放進去
    from vetting.routes import _put_state
    fake_session = MagicMock()
    fake_state = {
        "form_action": "x", "captcha_url": "y",
        "hidden_fields": {}, "name_field": "kw",
    }
    qid = _put_state(fake_state, fake_session)

    monkeypatch.setattr(
        "vetting.routes.submit_search",
        lambda session, state, name, captcha_text, **kw: [
            {"subject": "詐欺", "case_no": "113上易123", "date": "113/03/15",
             "court": "高等法院", "detail_url": "https://x/d/1"},
        ],
    )
    monkeypatch.setattr(
        "vetting.routes.scan_detail",
        lambda session, url: {"keyword_hits": ["詐欺", "定讞"], "masked_ids": ["A12****567"]},
    )
    monkeypatch.setattr(
        "vetting.routes.mjib_search",
        lambda name, session=None: [],
    )
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "name": "王小明", "captcha": "ABCD"})
    assert res.status_code == 200
    data = res.get_json()
    assert "judicial" in data
    assert "mjib" in data
    assert len(data["judicial"]) == 1
    assert data["judicial"][0]["keyword_hits"] == ["詐欺", "定讞"]
    assert data["judicial"][0]["masked_ids"] == ["A12****567"]
    assert data["mjib"] == []


def test_submit_endpoint_expired_query_id(app, client, db_session):
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.post("/vetting/submit",
                      json={"query_id": "nonexistent", "name": "x", "captcha": "x"})
    assert res.status_code == 410
    assert res.get_json()["error"] == "query_expired"
```

- [ ] **Step 6: Run tests，確認 fail**

```bash
python -m pytest tests/test_vetting_routes.py -v
```
Expected: 2 new fail

- [ ] **Step 7: 加 /submit endpoint**

繼續編輯 `vetting/routes.py`，加：

```python
@vetting_bp.route("/submit", methods=["POST"])
@login_required
@limiter.limit("1/minute", key_func=lambda: f"vet-submit:{__import__('flask_login').current_user.id}")
def submit():
    data = request.get_json(silent=True) or {}
    qid = data.get("query_id")
    name = (data.get("name") or "").strip()
    captcha_text = (data.get("captcha") or "").strip()
    if not qid or not name or not captcha_text:
        return jsonify({"error": "missing_params"}), 400
    entry = _take_state(qid)
    if not entry:
        return jsonify({"error": "query_expired"}), 410

    try:
        rows = submit_search(entry["session"], entry["state"], name, captcha_text)
    except ValueError as e:
        if str(e) == "captcha_invalid":
            return jsonify({"error": "captcha_invalid"}), 400
        return jsonify({"error": "submit_failed", "message": str(e)}), 502
    except Exception as e:
        return jsonify({"error": "submit_failed", "message": str(e)}), 502

    # 深掃每筆，串 keyword_hits / masked_ids
    enriched = []
    for r in rows:
        try:
            scan = scan_detail(entry["session"], r["detail_url"])
        except Exception:
            scan = {"keyword_hits": [], "masked_ids": []}
        enriched.append({**r, **scan})
        throttle_sleep()

    # MJIB（獨立 session 避免共用司法院 cookie）
    try:
        mjib_results = mjib_search(name)
    except Exception:
        mjib_results = []

    # query_state 一旦使用即失效（不允許重用同 qid 重打）
    _QUERY_STATE.pop(qid, None)

    return jsonify({"judicial": enriched, "mjib": mjib_results})
```

- [ ] **Step 8: Run tests**

```bash
python -m pytest tests/test_vetting_routes.py -v
```
Expected: 5 passed (3 from Task 1 + 2 new)

- [ ] **Step 9: 全測 regression**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 95 passed

- [ ] **Step 10: Commit**

```bash
git add app_unified/vetting/routes.py app_unified/tests/test_vetting_routes.py
git commit -m "feat(vetting): wire /start + /submit routes with query_state + limits

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: UI — 完整三步流程（index → captcha → result）

**Files:**
- Modify: `app_unified/templates/vetting/index.html`
- Create: `app_unified/templates/vetting/_partial_captcha.html` (optional)
- Create: `app_unified/templates/vetting/_partial_result.html` (optional)

UI 改動，hand-verify。為避免多頁切換複雜，**改成單頁 SPA-style**：所有 step 在 `index.html` 內 div 切換。

- [ ] **Step 1: 改寫 `templates/vetting/index.html`（含完整三步驟 SPA）**

```html
{% extends "base.html" %}
{% block title %}人員查詢{% endblock %}
{% block content %}
<div style="max-width:680px; margin:1rem auto; padding:0 .75rem;">
  <h1>🛡️ 人員查詢</h1>
  <p style="color:#666; font-size:.9rem;">同時查詢司法院刑事判決與法務部調查局通緝/嫌疑人名單。</p>

  <!-- Step 1: 姓名 -->
  <div id="step-name" style="margin-top:1rem;">
    <form id="vet-name-form">
      <label style="display:block; margin-bottom:.4rem;">姓名</label>
      <input type="text" id="vet-name" required
             style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.75rem;">
      <button type="submit" class="btn btn-primary" style="width:100%; padding:.6rem;">開始查詢</button>
    </form>
  </div>

  <!-- Step 2: captcha -->
  <div id="step-captcha" style="display:none; margin-top:1rem;">
    <p>請輸入下方驗證碼（看不清楚可以點圖重整）：</p>
    <img id="vet-captcha-img" src="" alt="captcha"
         style="border:1px solid #ddd; cursor:pointer;" onclick="reloadCaptcha()">
    <div style="font-size:.78rem; color:#888;">點圖換一張</div>
    <form id="vet-captcha-form" style="margin-top:.6rem;">
      <input type="text" id="vet-captcha-text" required maxlength="10" autofocus
             style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.5rem;">
      <button type="submit" class="btn btn-primary" style="width:100%; padding:.6rem;">送出 / 開始深掃</button>
    </form>
  </div>

  <!-- Step 3: result -->
  <div id="step-result" style="display:none; margin-top:1rem;"></div>

  <div id="vet-status" style="margin-top:1rem; color:#666; font-size:.85rem;"></div>
</div>

<script>
var _qid = null;
var _name = null;

function show(id) {
  ['step-name','step-captcha','step-result'].forEach(function(s){
    document.getElementById(s).style.display = (s === id) ? 'block' : 'none';
  });
}

function setStatus(msg) { document.getElementById('vet-status').textContent = msg || ''; }

document.getElementById('vet-name-form').addEventListener('submit', function(e) {
  e.preventDefault();
  _name = document.getElementById('vet-name').value.trim();
  if (!_name) return;
  setStatus('連線司法院、取得驗證碼…');
  fetch('/vetting/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: _name})
  }).then(function(r){return r.json().then(function(d){return [r.status, d];});}).then(function(arr){
    var st = arr[0], d = arr[1];
    if (st !== 200) {
      setStatus('錯誤：' + (d.error || '未知') + (d.message ? ' / '+d.message : ''));
      return;
    }
    _qid = d.query_id;
    document.getElementById('vet-captcha-img').src = 'data:image/jpeg;base64,' + d.captcha_b64;
    show('step-captcha');
    setStatus('');
    document.getElementById('vet-captcha-text').focus();
  }).catch(function(err){ setStatus('連線失敗：' + err.message); });
});

function reloadCaptcha() {
  setStatus('重整驗證碼…');
  fetch('/vetting/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: _name})
  }).then(function(r){return r.json();}).then(function(d){
    if (d.query_id) {
      _qid = d.query_id;
      document.getElementById('vet-captcha-img').src = 'data:image/jpeg;base64,' + d.captcha_b64;
      setStatus('');
    } else {
      setStatus('重整失敗');
    }
  });
}

document.getElementById('vet-captcha-form').addEventListener('submit', function(e) {
  e.preventDefault();
  var captcha = document.getElementById('vet-captcha-text').value.trim();
  if (!captcha || !_qid) return;
  setStatus('查詢中…（將打司法院 + 調查局，最多 30 秒）');
  fetch('/vetting/submit', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query_id: _qid, name: _name, captcha: captcha})
  }).then(function(r){return r.json().then(function(d){return [r.status, d];});}).then(function(arr){
    var st = arr[0], d = arr[1];
    if (st !== 200) {
      if (d.error === 'captcha_invalid') {
        setStatus('驗證碼錯誤，請點圖換一張再試');
        document.getElementById('vet-captcha-text').value = '';
        reloadCaptcha();
        return;
      }
      if (d.error === 'query_expired') {
        setStatus('查詢逾時（5 分鐘），請重新開始');
        show('step-name');
        return;
      }
      setStatus('錯誤：' + (d.error || '未知') + (d.message ? ' / ' + d.message : ''));
      return;
    }
    renderResult(d);
    show('step-result');
    setStatus('');
  }).catch(function(err){ setStatus('連線失敗：' + err.message); });
});

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});
}

function renderResult(d) {
  var html = '';

  // 司法院
  html += '<h2 style="margin-top:0;">A. 司法院刑事判決</h2>';
  if (!d.judicial || d.judicial.length === 0) {
    html += '<p style="color:#888;">查無資料。</p>';
  } else {
    html += '<p style="color:#666; font-size:.9rem;">共 ' + d.judicial.length + ' 筆，⚠ 為命中關鍵字</p>';
    d.judicial.forEach(function(r, i) {
      var hit = (r.keyword_hits || []).length > 0;
      html += '<div style="border:1px solid #e0e0e0; border-radius:6px; padding:.6rem; margin-bottom:.5rem; '
            + (hit ? 'border-left:4px solid #e74c3c; background:#fff5f5;' : '') + '">';
      html += '<div style="font-weight:600;">' + (hit ? '⚠ ' : '') + escapeHtml(r.subject) + '</div>';
      html += '<div style="color:#666; font-size:.88rem;">字號：' + escapeHtml(r.case_no)
            + '　日期：' + escapeHtml(r.date) + '　法院：' + escapeHtml(r.court) + '</div>';
      if (hit) {
        html += '<div style="color:#c0392b; font-size:.88rem; margin-top:.2rem;">命中關鍵字：'
              + r.keyword_hits.map(escapeHtml).join(' / ') + '</div>';
      }
      if (r.masked_ids && r.masked_ids.length > 0) {
        html += '<div style="font-family:monospace; font-size:.88rem; margin-top:.2rem;">遮蔽 ID：'
              + r.masked_ids.map(escapeHtml).join('、') + ' ← 自行比對</div>';
      }
      html += '<div style="margin-top:.3rem;"><a href="' + escapeHtml(r.detail_url)
            + '" target="_blank" rel="noopener" style="font-size:.85rem;">→ 開原判決書</a></div>';
      html += '</div>';
    });
  }

  // MJIB
  html += '<h2 style="margin-top:1rem;">B. 法務部調查局通緝/嫌疑人</h2>';
  if (!d.mjib || d.mjib.length === 0) {
    html += '<p style="color:#888;">✅ 查無資料。</p>';
  } else {
    html += '<p style="color:#c0392b;">⚠ 命中 ' + d.mjib.length + ' 筆</p>';
    d.mjib.forEach(function(e) {
      html += '<div style="border:1px solid #e0e0e0; border-radius:6px; padding:.6rem; margin-bottom:.5rem;">';
      html += '<div style="font-weight:600;">' + escapeHtml(e.name) + '</div>';
      if (e.crime) html += '<div style="font-size:.9rem;">罪名：' + escapeHtml(e.crime) + '</div>';
      if (e.photo_url) html += '<img src="' + escapeHtml(e.photo_url) + '" alt="" style="max-width:120px; margin-top:.3rem;">';
      if (e.detail_url) html += '<div><a href="' + escapeHtml(e.detail_url) + '" target="_blank" rel="noopener">→ 詳情</a></div>';
      html += '</div>';
    });
  }

  html += '<div style="margin-top:1rem;"><a href="/vetting/" class="btn btn-secondary">↻ 重新查詢</a></div>';

  document.getElementById('step-result').innerHTML = html;
}
</script>
{% endblock %}
```

- [ ] **Step 2: 全測 regression**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 95 passed (沒影響到 backend)

- [ ] **Step 3: hand-verify in dev server**

```bash
cd /home/hirain0126/projects/webapp && bash start_app1.sh &
```

到 `http://localhost:5000/vetting/` 用 admin 登入：

1. 看到姓名輸入頁
2. 輸入「王」(常見姓 → 容易出結果) → 開始查詢
3. 看到驗證碼圖 → 肉眼讀 → 輸入 → 送出
4. 等 10-30 秒 → 看到兩段結果（司法院 + MJIB）
5. 點某筆「→ 開原判決書」應該開新分頁去原站
6. 點「重新查詢」回 step 1

如果驗證碼讀錯：
- 應該看到「驗證碼錯誤」訊息 + 自動重整圖

如果 5 分鐘內沒送 captcha：
- 應該看到「查詢逾時」並回 step 1

- [ ] **Step 4: Commit**

```bash
git add app_unified/templates/vetting/index.html
git commit -m "feat(vetting): full SPA-style UI with captcha relay + results

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: robots.txt 確認 + 429/403 處理

**Files:**
- Modify: `app_unified/vetting/judicial.py`, `app_unified/vetting/mjib.py`
- Modify: `app_unified/vetting/routes.py`

- [ ] **Step 1: 手動確認 robots.txt**

```bash
curl -s "https://judgment.supreme.court.gov.tw/robots.txt"
echo "---"
curl -s "https://www.mjib.gov.tw/robots.txt"
```

人工檢查兩站 robots.txt：
- 確認查詢用的路徑（`/wkw/`、`/Crimes/`）**沒被 Disallow**
- 如果有 Disallow → STOP，回報使用者，重評方案

把 robots.txt 內容貼到 commit message 或 task report 裡留痕跡。

- [ ] **Step 2: 在 judicial / mjib 加 429/403 detection**

修改 `app_unified/vetting/judicial.py` 的 `submit_search` 與 `scan_detail`，把網路錯誤分類：

```python
import requests as _r


class RateLimited(Exception):
    """429/503 — 對方限流，立刻停止"""
    pass


class Forbidden(Exception):
    """403 — 對方擋人，立刻停止"""
    pass


def _check_blocked(response):
    if response.status_code == 429 or response.status_code == 503:
        raise RateLimited(f"upstream {response.status_code}")
    if response.status_code == 403:
        raise Forbidden(f"upstream 403")
```

在 `submit_search` 與 `scan_detail` 內 `r = session.X(...)` 之後立刻加 `_check_blocked(r)`，再 `r.raise_for_status()`。

`mjib.py` 的 `search_by_name` 同樣處理。

- [ ] **Step 3: 在 routes.py 抓 RateLimited / Forbidden 給友善訊息**

修改 `vetting/routes.py::submit`，把外層 try/except 升級：

```python
from vetting.judicial import (
    fetch_home_state, submit_search, scan_detail,
    RateLimited, Forbidden,
)
# ...

    try:
        rows = submit_search(entry["session"], entry["state"], name, captcha_text)
    except RateLimited:
        return jsonify({"error": "rate_limited",
                        "message": "外部站暫時限流，請 5 分鐘後再試"}), 429
    except Forbidden:
        return jsonify({"error": "forbidden",
                        "message": "外部站拒絕請求，請聯絡管理員"}), 403
    except ValueError as e:
        if str(e) == "captcha_invalid":
            return jsonify({"error": "captcha_invalid"}), 400
        return jsonify({"error": "submit_failed", "message": str(e)}), 502
    except Exception as e:
        return jsonify({"error": "submit_failed", "message": str(e)}), 502
```

scan_detail 內 RateLimited/Forbidden → 直接 break loop 並標記，後面的 detail 不再打：

```python
    enriched = []
    blocked = False
    for r in rows:
        if blocked:
            enriched.append({**r, "keyword_hits": [], "masked_ids": []})
            continue
        try:
            scan = scan_detail(entry["session"], r["detail_url"])
        except (RateLimited, Forbidden):
            blocked = True
            scan = {"keyword_hits": [], "masked_ids": []}
        except Exception:
            scan = {"keyword_hits": [], "masked_ids": []}
        enriched.append({**r, **scan})
        throttle_sleep()
```

- [ ] **Step 4: 寫 test for 429 path**

Append to `tests/test_vetting_routes.py`:

```python
def test_submit_returns_429_when_external_ratelimits(app, client, db_session, monkeypatch):
    from vetting.routes import _put_state
    from vetting.judicial import RateLimited
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    qid = _put_state({"form_action": "x", "captcha_url": "y", "hidden_fields": {}, "name_field": "kw"},
                     MagicMock())

    def raise_rl(*a, **kw):
        raise RateLimited("upstream 429")
    monkeypatch.setattr("vetting.routes.submit_search", raise_rl)

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "name": "x", "captcha": "x"})
    assert res.status_code == 429
    assert res.get_json()["error"] == "rate_limited"
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_vetting_routes.py -v
```
Expected: all pass (6 total)

- [ ] **Step 6: 全測 regression**

```bash
python -m pytest tests/ --ignore=tests/test_auth.py --ignore=tests/test_notes.py --ignore=tests/test_face.py -q
```
Expected: 96 passed

- [ ] **Step 7: Commit**

```bash
git add app_unified/vetting/judicial.py app_unified/vetting/mjib.py app_unified/vetting/routes.py app_unified/tests/test_vetting_routes.py
git commit -m "feat(vetting): detect 429/403 from upstream + friendly error mapping

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 端對端 smoke + push

- [ ] **Step 1: 全套實機驗證**

```bash
cd /home/hirain0126/projects/webapp && bash start_app1.sh &
```

依序驗：

1. **無權限隱藏**：用非 admin 登入 → nav 無「人員查詢」連結 + 直接打 `/vetting/` 回 404
2. **Admin 入口**：admin 登入 → nav 看到「🛡️ 人員查詢」 → 點進入
3. **完整查詢流程**：
   - 輸入「王小明」（隨便常見名）
   - 看到驗證碼圖 → 肉眼讀 → 輸入
   - 等 10-30 秒
   - 看到司法院結果（≥ 1 筆）+ MJIB 段落
   - 命中關鍵字的筆有紅色標記 + 馬賽克 ID 顯示
4. **驗證碼錯誤**：故意打錯 captcha → 跳「驗證碼錯誤」+ 自動換新圖 → 重打能繼續
5. **逾時**：在 step 2 等 6 分鐘不動 → 送出 → 看到「查詢逾時」回 step 1
6. **Rate limit**：成功查詢 1 次後立刻再查 → 應該 429（每分鐘 1 次）

- [ ] **Step 2: Push**

```bash
cd /home/hirain0126/projects/webapp
git push
```

- [ ] **Step 3: Zeabur 部署後驗證**

部署完成後，在 Zeabur prod 上重複 Step 1 的流程（admin 登入 → 完整查詢 → 確認 OK）。

如果 prod 連線外部站失敗（`judicial_unavailable` 或 `mjib_unavailable`），可能是 Zeabur egress IP 被擋 → 看 Zeabur logs：

```
2026-04-29 12:34:56 vetting WARNING: judicial fetch_home_state failed: HTTPSConnectionPool...
```

若被擋，**STOP** 回報使用者，討論是否要加 proxy 或改方案。

- [ ] **Step 4: 不需要 migration / 不需要其他部署動作**

本 feature 沒新增 DB 欄位、沒新增 R2 路徑，純 code-only。Zeabur deploy 完即可用。

只需要在 admin dashboard（如果有 feature 開關 UI）給其他想用的使用者勾「vetting」。`is_root_admin` (username='admin') 自動 bypass。
