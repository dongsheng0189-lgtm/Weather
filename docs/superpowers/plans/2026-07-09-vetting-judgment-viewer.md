# 站內判決書檢視器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 人員查詢每筆司法院結果加「📖 站內檢視」，把判決書全文抓進我們自己的新分頁、命中人名黃底、可像 notes 查詢一樣逐一跳轉。

**Architecture:** 前端卡片按鈕 `window.open` 開新分頁 `GET /vetting/judgment?u=<detail_url>&name=<名字>`。後端嚴格白名單驗證 URL（防 SSRF）→ lazy 抓司法院內頁 → 抽內文純文字（保留換行）→ 全文 `html.escape` → 兩段式比對把命中人名包成自家 `vet-hl` span → render 純文字 `pre-wrap` 頁 + notes 式跳轉 widget。

**Tech Stack:** Flask、BeautifulSoup4、標準庫 `re`/`html`/`urllib.parse`。**不新增依賴。**

## Global Constraints

- **不新增任何 pip 依賴**（只用既有 beautifulsoup4 + 標準庫）。[[feedback_warn_build_deploy_impact]][[feedback_pin_dependency_versions]]
- **零 XSS 面**：判決書內文一律先 `html.escape`，只注入自家固定字面量 `<span class="vet-hl" id="vhit-K">`；不保留任何外站標籤/屬性。
- **SSRF 白名單**：抓取前 `scheme=='https'` 且 `netloc=='judgment.judicial.gov.tw'` 且 `path.startswith('/FJUD/data.aspx')`，否則 `abort(400)`。
- 遮罩符號 `○` = U+25CB，與現有 `MASKED_NAME_RE = [一-龥]○{1,3}` 一致。
- 命中兩段式（A 案）：完整名字命中 **≥1 → 只用完整**；**==0 → fallback 模糊比對姓** `re.escape(姓)+"○{1,3}"`。
- 路由沿用 vetting gate（`vetting_bp.before_request` `_gate`）+ `@login_required`。
- 時間 UI 一律台灣時間（本功能無時間顯示，略）。[[feedback_timezone_taiwan]]
- 本機測試 → user OK 才 push；動 prod code 在 branch `feat/vetting-judgment-viewer`。[[feedback_local_test_before_push]][[feedback_branch_workflow]][[feedback_push_policy]]

## File Structure

- Modify `app_unified/vetting/judicial.py` — 加 4 個純函式：`is_allowed_detail_url`、`fetch_judgment_html`、`extract_judgment_text`、`build_highlighted_judgment`。
- Modify `app_unified/vetting/routes.py` — 加 `GET /vetting/judgment` route。
- Create `app_unified/templates/vetting/judgment.html` — 純文字檢視頁 + 跳轉 widget。
- Modify `app_unified/templates/vetting/index.html` — 卡片加「📖 站內檢視」按鈕（排在「開原判決書」前）+ `openJudgment()` JS。
- Modify `app_unified/tests/test_vetting_judicial.py` — 純函式測試。
- Create `app_unified/tests/test_vetting_judgment_route.py` — route 測試（沿用 `test_vetting_whitelist_add_route.py` 模式）。

---

### Task 1: SSRF 白名單 `is_allowed_detail_url`

**Files:**
- Modify: `app_unified/vetting/judicial.py`（加函式；頂部已 `from urllib.parse import urljoin`，改成也 import `urlparse`）
- Test: `app_unified/tests/test_vetting_judicial.py`

**Interfaces:**
- Produces: `is_allowed_detail_url(url: str) -> bool`

- [ ] **Step 1: 寫失敗測試**（加到 test_vetting_judicial.py 末尾）

```python
from vetting.judicial import is_allowed_detail_url

def test_is_allowed_detail_url_accepts_real_detail():
    u = "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&id=TCDM,114,%E9%87%91,3,20251229,1&ot=in"
    assert is_allowed_detail_url(u) is True

def test_is_allowed_detail_url_rejects_bad():
    assert is_allowed_detail_url("http://judgment.judicial.gov.tw/FJUD/data.aspx?id=x") is False  # 非 https
    assert is_allowed_detail_url("https://evil.com/FJUD/data.aspx?id=x") is False                 # 別的 host
    assert is_allowed_detail_url("https://judgment.judicial.gov.tw/FJUD/qryresultlst.aspx") is False  # 別的 path
    assert is_allowed_detail_url("https://judgment.judicial.gov.tw@evil.com/FJUD/data.aspx") is False # host 混淆
    assert is_allowed_detail_url("") is False
    assert is_allowed_detail_url(None) is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && python -m pytest tests/test_vetting_judicial.py -k is_allowed_detail_url -q`
Expected: FAIL（ImportError: cannot import name 'is_allowed_detail_url'）

- [ ] **Step 3: 實作**

在 `judicial.py` 頂部把 `from urllib.parse import urljoin` 改為 `from urllib.parse import urljoin, urlparse`，並加：

```python
def is_allowed_detail_url(url: str) -> bool:
    """SSRF 白名單：只允許司法院判決書內頁。client 帶回的 URL 必經此驗證才可抓取。"""
    try:
        p = urlparse(url or "")
    except Exception:
        return False
    return (
        p.scheme == "https"
        and p.netloc == "judgment.judicial.gov.tw"
        and p.path.startswith("/FJUD/data.aspx")
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && python -m pytest tests/test_vetting_judicial.py -k is_allowed_detail_url -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add app_unified/vetting/judicial.py app_unified/tests/test_vetting_judicial.py
git commit -m "feat(vetting): is_allowed_detail_url SSRF 白名單(判決書檢視器用)"
```

---

### Task 2: 抽判決書內文純文字 `extract_judgment_text`

**Files:**
- Modify: `app_unified/vetting/judicial.py`
- Test: `app_unified/tests/test_vetting_judicial.py`（既有 `_read_fixture` helper + fixture `judicial_detail.html`）

**Interfaces:**
- Produces: `extract_judgment_text(html: str) -> str`

- [ ] **Step 1: 寫失敗測試**

```python
from vetting.judicial import extract_judgment_text

def test_extract_judgment_text_from_detail_fixture():
    html = _read_fixture("judicial_detail.html")
    text = extract_judgment_text(html)
    assert "主文" in text                 # 內文容器有抓到
    assert re.search(r"</?[a-zA-Z]", text) is None  # 無 HTML 標籤洩漏（內文合法角括號如 <黑白賣客> 允許）
    assert "\n" in text                   # 保留換行
    assert len(text) > 300

# 註：需 `import re` 於測試檔頂部（既有或本任務補上）。原始草案用 `"<" not in text`
# 過嚴 — fixture 內文合法含 FB 社團名 `<黑白賣客>`，改測「無標籤洩漏」。
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && python -m pytest tests/test_vetting_judicial.py -k extract_judgment_text -q`
Expected: FAIL（ImportError）

- [ ] **Step 3: 實作**（judicial.py，靠近 parse_results；已 import re / BeautifulSoup）

```python
def extract_judgment_text(html: str) -> str:
    """抽司法院判決書內頁的內文純文字，保留段落換行（給站內檢視器 pre-wrap 顯示）。

    內文容器 recon 確認：div.jud_content 首選、div#jud 次選、td.tab_content 備援。
    """
    soup = BeautifulSoup(html, "html.parser")
    container = (
        soup.find("div", class_="jud_content")
        or soup.find(id="jud")
        or soup.find("td", class_="tab_content")
        or soup
    )
    for br in container.find_all("br"):
        br.replace_with("\n")
    text = container.get_text("\n")
    text = re.sub(r"[ \t]+\n", "\n", text)     # 去行尾空白
    text = re.sub(r"\n{3,}", "\n\n", text)     # 收斂過多空行
    return text.strip()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && python -m pytest tests/test_vetting_judicial.py -k extract_judgment_text -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app_unified/vetting/judicial.py app_unified/tests/test_vetting_judicial.py
git commit -m "feat(vetting): extract_judgment_text 抽判決書內文純文字(保留換行)"
```

---

### Task 3: 命中黃底 + 兩段式比對 `build_highlighted_judgment`（核心）

**Files:**
- Modify: `app_unified/vetting/judicial.py`
- Test: `app_unified/tests/test_vetting_judicial.py`

**Interfaces:**
- Produces: `build_highlighted_judgment(text: str, name: str) -> tuple[str, int]`
  - 回 `(safe_html, hit_count)`；`safe_html` 已 escape、命中包成 `<span class="vet-hl" id="vhit-K">`（K 從 0 遞增）。

- [ ] **Step 1: 寫失敗測試**

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && python -m pytest tests/test_vetting_judicial.py -k highlight -q`
Expected: FAIL（ImportError）

- [ ] **Step 3: 實作**（judicial.py；已 `from html import escape as _escape`、`import re`）

```python
def build_highlighted_judgment(text: str, name: str) -> tuple:
    """把判決書純文字 escape 後，將命中的人名包成自家 vet-hl span，回 (safe_html, hit_count)。

    兩段式比對（避免同姓遮罩名雜訊）：
      - 完整名字（王小明）命中 >=1 → 只包完整名字。
      - 完整名字 0 命中（當事人全遮罩）→ fallback 比對姓 王○{1,3}。
    先 escape 全文、再對 escape 後字串做 regex，span 內容即 escape 後的命中字，零注入面。
    """
    name = (name or "").strip()
    esc = _escape(text)
    if not name:
        return esc, 0
    esc_name = _escape(name)                       # 中文不受影響；防禦性
    if esc.count(esc_name) >= 1:
        pattern = re.compile(re.escape(esc_name))
    else:
        surname = _escape(name[0])
        pattern = re.compile(re.escape(surname) + "○{1,3}")
    counter = {"k": 0}

    def _wrap(m):
        k = counter["k"]
        counter["k"] += 1
        return f'<span class="vet-hl" id="vhit-{k}">{m.group(0)}</span>'

    out = pattern.sub(_wrap, esc)
    return out, counter["k"]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && python -m pytest tests/test_vetting_judicial.py -k highlight -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 跑整個 judicial 測試檔確認沒弄壞既有**

Run: `cd app_unified && python -m pytest tests/test_vetting_judicial.py -q`
Expected: PASS（全部）

- [ ] **Step 6: Commit**

```bash
git add app_unified/vetting/judicial.py app_unified/tests/test_vetting_judicial.py
git commit -m "feat(vetting): build_highlighted_judgment 命中黃底+兩段式比對(核心,XSS-safe)"
```

---

### Task 4: 後端路由 `GET /vetting/judgment` + `fetch_judgment_html`

**Files:**
- Modify: `app_unified/vetting/judicial.py`（加 `fetch_judgment_html`）
- Modify: `app_unified/vetting/routes.py`（加 route；已 import `judicial`、`render_template`、`request`、`abort`、`logger`）
- Create: `app_unified/tests/test_vetting_judgment_route.py`

**Interfaces:**
- Consumes: `is_allowed_detail_url`、`extract_judgment_text`、`build_highlighted_judgment`（Task 1–3）
- Produces: `fetch_judgment_html(url: str, session=None) -> str`；route `GET /vetting/judgment?u=&name=`

- [ ] **Step 1: 寫失敗測試**（新檔 `tests/test_vetting_judgment_route.py`）

```python
"""站內判決書檢視器 route：權限、SSRF 白名單、渲染。"""
import uuid
import pytest
from models import User
from extensions import db
from vetting import judicial


def _mk(db_session, features=None):
    u = User(username=f"u_{uuid.uuid4().hex[:6]}", role="user", store="B", is_active=True)
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u); db_session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


GOOD_URL = "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&id=TCDM,114,x,3,20251229,1&ot=in"


def test_judgment_renders_highlighted(app, client, db_session, monkeypatch):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    monkeypatch.setattr(judicial, "fetch_judgment_html",
                        lambda url, session=None: '<div class="jud_content">被告王小明犯詐欺。</div>')
    res = client.get(f"/vetting/judgment?u={GOOD_URL}&name=王小明")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'class="vet-hl"' in body
    assert "王小明" in body


def test_judgment_rejects_bad_url_400(app, client, db_session):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    res = client.get("/vetting/judgment?u=https://evil.com/FJUD/data.aspx&name=王小明")
    assert res.status_code == 400


def test_judgment_requires_vetting_feature_404(app, client, db_session):
    u = _mk(db_session, features=["notes"]); _login(client, u)   # 無 vetting
    res = client.get(f"/vetting/judgment?u={GOOD_URL}&name=王小明")
    assert res.status_code == 404


def test_judgment_fetch_failure_shows_error(app, client, db_session, monkeypatch):
    u = _mk(db_session, features=["vetting"]); _login(client, u)
    def _boom(url, session=None):
        raise RuntimeError("blocked")
    monkeypatch.setattr(judicial, "fetch_judgment_html", _boom)
    res = client.get(f"/vetting/judgment?u={GOOD_URL}&name=王小明")
    assert res.status_code == 502
    assert "開原判決書" in res.get_data(as_text=True)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && python -m pytest tests/test_vetting_judgment_route.py -q`
Expected: FAIL（404 找不到 route / AttributeError fetch_judgment_html）

- [ ] **Step 3a: judicial.py 加 `fetch_judgment_html`**（已 import make_session、check_blocked、HOME_URL）

```python
def fetch_judgment_html(url: str, session=None) -> str:
    """live：抓司法院判決書內頁 HTML（給站內檢視器）。呼叫端須先過 is_allowed_detail_url。"""
    if session is None:
        session = make_session()
    r = session.get(url, headers={"Referer": HOME_URL}, timeout=15)  # make_session 已帶 UA
    check_blocked(r)
    r.raise_for_status()
    return r.text
```

- [ ] **Step 3b: routes.py 加 route**（放在 `/vetting/whitelist` 相關 route 附近；確認頂部有 `from flask import ... abort, render_template, request`）

```python
@vetting_bp.route("/judgment")
@login_required
def judgment():
    """站內判決書檢視器：抓司法院內頁 → 命中人名黃底 → 純文字 pre-wrap 顯示。"""
    u = request.args.get("u", "")
    name = request.args.get("name", "")
    if not judicial.is_allowed_detail_url(u):
        abort(400)
    try:
        html = judicial.fetch_judgment_html(u)
    except Exception as e:
        logger.warning("vetting: judgment fetch failed: %s", e)
        return render_template("vetting/judgment.html",
                               error="無法載入判決書全文，請稍後再試，或改用「開原判決書」開啟司法院原頁。",
                               body_html="", name=name, hit_count=0), 502
    text = judicial.extract_judgment_text(html)
    body_html, hit_count = judicial.build_highlighted_judgment(text, name)
    return render_template("vetting/judgment.html",
                           error=None, body_html=body_html, name=name, hit_count=hit_count)
```

- [ ] **Step 3c: 建最小 template 讓測試能 render**（`templates/vetting/judgment.html`；Task 5 再補 widget/CSS）

```html
{% extends "base.html" %}
{% block content %}
{% if error %}
  <p style="color:#c0392b;">{{ error }}</p>
  <p>你可以改用「開原判決書」開啟司法院原頁。</p>
{% else %}
  <div id="judgment-body" style="white-space:pre-wrap;">{{ body_html|safe }}</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && python -m pytest tests/test_vetting_judgment_route.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add app_unified/vetting/judicial.py app_unified/vetting/routes.py app_unified/templates/vetting/judgment.html app_unified/tests/test_vetting_judgment_route.py
git commit -m "feat(vetting): /vetting/judgment 路由 + fetch_judgment_html(SSRF gate + 友善錯誤)"
```

---

### Task 5: 檢視頁 UI — CSS + notes 式跳轉 widget

**Files:**
- Modify: `app_unified/templates/vetting/judgment.html`

**Interfaces:**
- Consumes: route 傳入的 `body_html`（含 `#vhit-0..N`）、`name`、`hit_count`、`error`

- [ ] **Step 1: 補完 template**（沿用 notes `search-hit-widget` 樣式，見 `templates/notes/editor.html` ~L165）

```html
{% extends "base.html" %}
{% block extra_head %}
<style>
  #judgment-body { white-space:pre-wrap; word-break:break-word; line-height:1.7;
    font-size:.95rem; color:#222; max-width:820px; margin:1rem auto; padding:0 1rem; }
  #judgment-body .vet-hl { background:#ff3; color:#000; padding:0 .1em; border-radius:2px; font-weight:600; }
  #judgment-body .vet-hl.current { outline:2px solid #e67e22; outline-offset:1px; }
  #jv-widget { display:none; position:fixed; bottom:20px; right:20px; z-index:1000;
    align-items:center; gap:.5rem; padding:.6rem .9rem; background:#fff8e1;
    border:2px solid #ffb300; border-radius:24px; font-size:.95rem; color:#5d4037;
    font-weight:600; box-shadow:0 4px 12px rgba(0,0,0,.18); }
  #jv-next { background:#ffb300; border:none; color:#fff; border-radius:50%; width:32px; height:32px;
    font-size:1rem; cursor:pointer; line-height:1; display:flex; align-items:center; justify-content:center; }
  #jv-back { background:#fff; border:1.5px solid #5d4037; color:#5d4037; border-radius:14px;
    padding:.3rem .7rem; font-size:.82rem; cursor:pointer; font-weight:600; }
</style>
{% endblock %}
{% block content %}
{% if error %}
  <div style="max-width:680px;margin:2rem auto;padding:0 1rem;">
    <p style="color:#c0392b;font-weight:600;">{{ error }}</p>
    <button onclick="window.close()" style="margin-top:.5rem;">關閉分頁</button>
  </div>
{% else %}
  <h2 style="max-width:820px;margin:1rem auto .3rem;padding:0 1rem;">判決書全文（命中「{{ name }}」已黃底標記）</h2>
  <div id="judgment-body">{{ body_html|safe }}</div>

  <div id="jv-widget">
    <span>🔍 <span id="jv-counter">0/{{ hit_count }}</span> 命中</span>
    <button type="button" id="jv-next" title="下一個">▶</button>
    <button type="button" id="jv-back" title="關閉分頁">← 返回</button>
  </div>

  <script>
  (function(){
    var hits = Array.prototype.slice.call(document.querySelectorAll('#judgment-body .vet-hl'));
    var widget = document.getElementById('jv-widget');
    var counter = document.getElementById('jv-counter');
    var idx = -1;
    if (hits.length > 0) {
      widget.style.display = 'flex';
      counter.textContent = '0/' + hits.length;
    }
    function go(n){
      if (!hits.length) return;
      if (idx >= 0) hits[idx].classList.remove('current');
      idx = (n + hits.length) % hits.length;
      hits[idx].classList.add('current');
      hits[idx].scrollIntoView({behavior:'smooth', block:'center'});
      counter.textContent = (idx + 1) + '/' + hits.length;
    }
    document.getElementById('jv-next').addEventListener('click', function(){ go(idx + 1); });
    document.getElementById('jv-back').addEventListener('click', function(){
      window.close();
      // 若瀏覽器擋 window.close（非 script 開啟的分頁），提示自行關閉
      setTimeout(function(){ alert('請自行關閉此分頁返回查詢結果。'); }, 300);
    });
    if (hits.length > 0) go(0);   // 自動跳到第一個命中
  })();
  </script>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: 驗證 template render 沒壞（既有 route 測試仍過）**

Run: `cd app_unified && python -m pytest tests/test_vetting_judgment_route.py -q`
Expected: PASS（4 passed）

- [ ] **Step 3: 語法檢查 template（jinja 能編譯）**

Run: `cd app_unified && python -c "from app import create_app; a=create_app(); a.jinja_env.get_template('vetting/judgment.html'); print('template OK')"`
Expected: 印出 `template OK`（無 TemplateSyntaxError）

- [ ] **Step 4: Commit**

```bash
git add app_unified/templates/vetting/judgment.html
git commit -m "feat(vetting): 判決書檢視頁 UI — 黃底 pre-wrap + notes 式命中跳轉 widget"
```

---

### Task 6: 前端卡片按鈕 `📖 站內檢視` + `openJudgment()`

**Files:**
- Modify: `app_unified/templates/vetting/index.html`（司法院 forEach 卡片，約 L297–299「→ 開原判決書」那段；`openJudgment` 加在 `escapeHtml` 附近）

**Interfaces:**
- Consumes: `/vetting/judgment` route（Task 4）；全域 `_name`（L107，查詢名字）；forEach 的 `r.detail_url`

- [ ] **Step 1: 卡片加按鈕（排在「開原判決書」之前）**

在 index.html 找到（司法院 forEach 內）：

```javascript
      html += '<div style="margin-top:.3rem;"><a href="' + escapeHtml(r.detail_url)
            + '" target="_blank" rel="noopener" style="font-size:.85rem;">→ 開原判決書</a></div>';
```

改成（站內檢視在前、開原判決書在後，同一列）：

```javascript
      html += '<div style="margin-top:.3rem; display:flex; gap:.8rem; flex-wrap:wrap;">'
            + '<a href="#" data-u="' + escapeHtml(r.detail_url) + '"'
            + ' onclick="openJudgment(this.getAttribute(\'data-u\')); return false;"'
            + ' style="font-size:.85rem;">📖 站內檢視</a>'
            + '<a href="' + escapeHtml(r.detail_url) + '" target="_blank" rel="noopener"'
            + ' style="font-size:.85rem;">→ 開原判決書</a>'
            + '</div>';
```

- [ ] **Step 2: 加 `openJudgment()`**（放在 `function escapeHtml` 之後，約 L131）

```javascript
function openJudgment(u) {
  if (!u) return;
  var url = '/vetting/judgment?u=' + encodeURIComponent(u)
          + '&name=' + encodeURIComponent(_name || '');
  window.open(url, '_blank');
}
```

- [ ] **Step 3: 語法檢查 index.html jinja 能編譯**

Run: `cd app_unified && python -c "from app import create_app; a=create_app(); a.jinja_env.get_template('vetting/index.html'); print('template OK')"`
Expected: 印出 `template OK`

- [ ] **Step 4: Commit**

```bash
git add app_unified/templates/vetting/index.html
git commit -m "feat(vetting): 結果卡片加「站內檢視」按鈕(排在開原判決書前)+ openJudgment"
```

---

### Task 7: 全套測試 + 本機端到端驗證

**Files:** 無（驗證）

- [ ] **Step 1: 全 vetting 測試綠**

Run: `cd app_unified && python -m pytest tests/test_vetting_judicial.py tests/test_vetting_judgment_route.py -q`
Expected: 全部 PASS

- [ ] **Step 2: 啟本機 server**

Run: `cd app_unified && bash scripts/run_local.sh`（背景），確認起在 http://localhost:5000

- [ ] **Step 3: 產一個真實命中頁供目視（本機 outbound_guard 擋司法院，用真實 fixture 跑後端函式）**

寫 scratchpad 小 script：對 `tests/fixtures/vetting/judicial_detail.html` 跑 `extract_judgment_text` → `build_highlighted_judgment(text, "王小明")`，把回傳 body_html 塞進 `judgment.html` 同款 CSS 的獨立頁，Chrome 開啟：
- 確認命中「王小明」黃底、右下 widget 顯示「k/N 命中」、▶ 循環捲動置中、目前命中有橘框、← 返回。
（本機 outbound_guard 擋外連，無法走真 route 抓司法院；此 harness 驗證前端渲染 + 跳轉。route 邏輯已由 Task 4 test client 覆蓋。）

- [ ] **Step 4: 列出「更新後驗證步驟 + 本機 link」給 user 目視確認**（[[feedback_relist_test_steps_after_change]]）

- [ ] **Step 5: user OK 後才 merge + push**（[[feedback_branch_workflow]][[feedback_push_policy]]，避開台灣 19:30–21:00 尖峰 [[feedback_push_peak_hours]]）

```bash
git checkout main && git merge --no-ff feat/vetting-judgment-viewer
git push origin main
```

---

## Self-Review

**Spec coverage：**
- 觸發按鈕（站內檢視前/開原判決書後）→ Task 6 ✓
- 新分頁 window.open → Task 6 ✓
- 路由 + SSRF 白名單 → Task 1 + Task 4 ✓
- lazy 抓 → Task 4 `fetch_judgment_html` ✓
- 抽內文純文字保留換行 → Task 2 ✓
- 兩段式命中比對 → Task 3 ✓
- escape 零注入 → Task 3（測試涵蓋 XSS）✓
- pre-wrap 呈現 → Task 4/5 template ✓
- notes 式跳轉 widget（k/N、▶、返回）→ Task 5 ✓
- 友善錯誤（抓取失敗/state 無關）→ Task 4 ✓
- 權限 gate → Task 4 test（feature 404）✓
- 測試 → Task 1–4 單元/route ✓；前端 → Task 7 本機目視 ✓

**Placeholder scan：** 無 TBD/TODO；每個 code step 有完整 code。

**Type consistency：** `is_allowed_detail_url(str)->bool`、`fetch_judgment_html(url,session=None)->str`、`extract_judgment_text(html)->str`、`build_highlighted_judgment(text,name)->(str,int)`、span id `vhit-K` / widget 讀 `#judgment-body .vet-hl` 一致；route 傳 `body_html/name/hit_count/error` 與 template 一致。
