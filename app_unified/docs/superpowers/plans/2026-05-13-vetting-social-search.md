# Vetting Social Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `/vetting/` with three new result sections (D 電話標記、E Google 搜尋連結、F FB/IG 搜尋連結) implemented as **pure link-out** — backend generates URL strings only, user clicks to inspect in their own browser (incognito recommended).

**Architecture:** Single new module `vetting/social.py` with pure deterministic functions (no IO, no DB, no outbound HTTP). `vetting/routes.py` accepts 3 new optional input fields, calls `build_url_bundle()`, stashes into existing `_QUERY_STATE`, and adds `social` key to `/submit` response. Frontend renders new sections after existing C section. Existing A/B/C sources untouched.

**Tech Stack:** Python 3.12 / Flask 3 / pytest / Jinja2 / vanilla JS (matches existing vetting module patterns).

**Reference spec:** `app_unified/docs/superpowers/specs/2026-05-13-vetting-social-search-design.md`

---

## File Structure

### Create
- `app_unified/vetting/social.py` — URL builder module (~80 lines, pure functions)
- `app_unified/tests/test_vetting_social.py` — 12 unit tests for `social.py`

### Modify
- `app_unified/vetting/routes.py` — accept new fields in `/start`, embed `social` in `/submit` response (≈10 lines added at 3 spots)
- `app_unified/tests/test_vetting_routes.py` — append 6 new route tests for social behavior + regression
- `app_unified/templates/vetting/index.html` — add 3 input fields, relatives 2-row progressive UI, warning banner, D/E/F render functions in JS (≈200 lines added)

### Not touched (strict guarantee from spec §9)
- `vetting/judicial.py` / `vetting/mjib.py` / `vetting/npa.py` / `vetting/errors.py` / `vetting/http_client.py` / `vetting/__init__.py`
- `models.py` (no DB schema change)
- `features.py` (no new feature flag)
- `dev/outbound_guard.py` (no new whitelist needed — backend doesn't make outbound calls for this feature)

---

## Task Breakdown — Backend (PR 1 scope)

### Task 1: Create `vetting/social.py` skeleton + module-level constants

**Files:**
- Create: `app_unified/vetting/social.py`
- Test: `app_unified/tests/test_vetting_social.py`

- [ ] **Step 1: Write the failing test (module imports)**

Create `app_unified/tests/test_vetting_social.py`:

```python
"""Tests for vetting/social.py — pure URL builder (no IO)."""
import pytest
from vetting import social


def test_module_imports_and_exposes_constants():
    """Module exposes the URL templates + bucket keyword constants."""
    assert hasattr(social, "GOOGLE_SEARCH")
    assert hasattr(social, "FB_SEARCH")
    assert hasattr(social, "IG_SEARCH")
    assert hasattr(social, "WHOSCALL")
    assert hasattr(social, "TRUECALLER")
    assert isinstance(social.BUCKET_KEYWORDS, dict)
    assert set(social.BUCKET_KEYWORDS.keys()) == {"company", "award", "negative"}
    # 至少各有 4 個 keyword（防退化）
    assert all(len(v) >= 4 for v in social.BUCKET_KEYWORDS.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app_unified && pytest tests/test_vetting_social.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vetting.social'`

- [ ] **Step 3: Create `vetting/social.py` with skeleton**

```python
"""Pure URL builder for vetting social/Google link-out search.

No outbound HTTP. No DB. Pure deterministic functions.
See docs/superpowers/specs/2026-05-13-vetting-social-search-design.md
"""

GOOGLE_SEARCH = "https://www.google.com/search?q={}"
FB_SEARCH     = "https://www.facebook.com/search/people/?q={}"
IG_SEARCH     = "https://www.instagram.com/explore/tags/{}"
WHOSCALL      = "https://whoscall.com/zh-TW/number/886-{}"
TRUECALLER    = "https://www.truecaller.com/search/tw/{}"

BUCKET_KEYWORDS = {
    "company":  ["公司", "商行", "負責人", "董事", "決標", "招標"],
    "award":    ["獲獎", "得獎", "冠軍", "優勝", "公會", "證照"],
    "negative": ["警察", "偵辦", "起訴", "逮捕", "通緝", "詐欺", "詐騙", "判決"],
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app_unified && pytest tests/test_vetting_social.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add app_unified/vetting/social.py app_unified/tests/test_vetting_social.py
git commit -m "feat(vetting/social): scaffold module with URL templates + bucket keywords"
```

---

### Task 2: Implement `normalize_phone()`

**Files:**
- Modify: `app_unified/vetting/social.py`
- Test: `app_unified/tests/test_vetting_social.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vetting_social.py`:

```python
import pytest

@pytest.mark.parametrize("raw,expected", [
    ("0912345678",        "0912345678"),
    ("0912-345-678",      "0912345678"),
    ("0912 345 678",      "0912345678"),
    ("(0912)345-678",     "0912345678"),
    ("+886912345678",     "0912345678"),
    ("+886-912-345-678",  "0912345678"),
    ("886-912-345-678",   "0912345678"),
    ("886912345678",      "0912345678"),
])
def test_normalize_phone_variants(raw, expected):
    """6+ written formats should all collapse to canonical 0XXXXXXXXX."""
    assert social.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    "12345",          # 太短
    "12345678901234", # 太長
    "abc-def-ghij",   # 非數字
    "0912345abc",     # 混雜
    None,             # 也要不爆
])
def test_normalize_phone_invalid_returns_empty(raw):
    """Bad input returns empty string (caller filters on truthy check)."""
    assert social.normalize_phone(raw) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vetting_social.py -v`
Expected: FAIL with `AttributeError: module 'vetting.social' has no attribute 'normalize_phone'`

- [ ] **Step 3: Implement `normalize_phone()`**

Append to `vetting/social.py`:

```python
import re

_PHONE_DIGITS_RE = re.compile(r"\D+")


def normalize_phone(raw) -> str:
    """Normalize TW mobile/landline to canonical 0XXXXXXXXX (10 digits).

    Handles: `0912345678`, `0912-345-678`, `+886912345678`,
             `886-912-345-678`, `(0912)345-678`, etc.
    Returns `""` if input cannot be normalized.
    """
    if not raw or not isinstance(raw, str):
        return ""
    digits = _PHONE_DIGITS_RE.sub("", raw)
    if digits.startswith("886"):
        digits = "0" + digits[3:]
    # TW phone canonical = leading 0 + 9 more digits = 10 total
    if len(digits) != 10 or not digits.startswith("0"):
        return ""
    return digits
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_vetting_social.py::test_normalize_phone_variants tests/test_vetting_social.py::test_normalize_phone_invalid_returns_empty -v`
Expected: PASS (8 + 6 = 14 parametrized cases)

- [ ] **Step 5: Commit**

```bash
git add app_unified/vetting/social.py app_unified/tests/test_vetting_social.py
git commit -m "feat(vetting/social): normalize_phone handles 8 TW phone formats"
```

---

### Task 3: Implement `phone_query_variants()`

**Files:** same as Task 2

- [ ] **Step 1: Write failing test**

Append to `tests/test_vetting_social.py`:

```python
def test_phone_query_variants():
    """Canonical 0912345678 → 3 search variants (no-sep / hyphenated / +886)."""
    variants = social.phone_query_variants("0912345678")
    assert variants == [
        "0912345678",
        "0912-345-678",
        "+886912345678",
    ]


def test_phone_query_variants_empty_input():
    """Empty/invalid canonical returns empty list."""
    assert social.phone_query_variants("") == []
    assert social.phone_query_variants("invalid") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vetting_social.py::test_phone_query_variants tests/test_vetting_social.py::test_phone_query_variants_empty_input -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement**

Append to `vetting/social.py`:

```python
def phone_query_variants(canonical: str) -> list:
    """Canonical 10-digit (e.g. '0912345678') → 3 variants for OR query.

    Variants cover the most common public-records formats:
      - no separator:  0912345678
      - hyphenated:    0912-345-678
      - international: +886912345678
    """
    if not canonical or len(canonical) != 10 or not canonical.startswith("0"):
        return []
    return [
        canonical,                                            # 0912345678
        f"{canonical[:4]}-{canonical[4:7]}-{canonical[7:]}",  # 0912-345-678
        f"+886{canonical[1:]}",                               # +886912345678
    ]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_vetting_social.py -v -k phone`
Expected: PASS (all phone-related tests including normalize from Task 2)

- [ ] **Step 5: Commit**

```bash
git add app_unified/vetting/social.py app_unified/tests/test_vetting_social.py
git commit -m "feat(vetting/social): phone_query_variants expands canonical to 3 formats"
```

---

### Task 4: Implement `google_search_url()`

**Files:** same as Task 2

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vetting_social.py`:

```python
from urllib.parse import unquote_plus


def _decode_q(url: str) -> str:
    """Extract the decoded `q=` parameter from a Google search URL for assertion."""
    assert url.startswith("https://www.google.com/search?q=")
    return unquote_plus(url.split("q=", 1)[1])


def test_google_search_url_basic():
    """Name only → URL contains quoted name, nothing else."""
    url = social.google_search_url("王小明")
    assert _decode_q(url) == '"王小明"'


def test_google_search_url_with_company_bucket():
    """bucket=company → URL has name + OR of company tokens."""
    url = social.google_search_url("王小明", bucket="company")
    q = _decode_q(url)
    assert '"王小明"' in q
    assert "公司" in q and "商行" in q and "負責人" in q
    assert " OR " in q


def test_google_search_url_with_award_bucket():
    url = social.google_search_url("王小明", bucket="award")
    q = _decode_q(url)
    assert '"王小明"' in q
    assert "獲獎" in q and "公會" in q


def test_google_search_url_with_negative_bucket():
    url = social.google_search_url("王小明", bucket="negative")
    q = _decode_q(url)
    assert '"王小明"' in q
    assert "警察" in q and "詐欺" in q and "判決" in q


def test_google_search_url_with_disambiguators():
    """General (no bucket) + disambiguators → URL has name + each disambig quoted."""
    url = social.google_search_url(
        "王小明",
        disambiguators=["0912-345-678", "李美麗", "新北市"]
    )
    q = _decode_q(url)
    assert '"王小明"' in q
    assert '"0912-345-678"' in q
    assert '"李美麗"' in q
    assert '"新北市"' in q


def test_google_search_url_bucket_and_disambiguators_together():
    """bucket + disambiguators → both appear in query."""
    url = social.google_search_url(
        "王小明",
        bucket="company",
        disambiguators=["李美麗"]
    )
    q = _decode_q(url)
    assert '"王小明"' in q
    assert '"李美麗"' in q
    assert "公司" in q  # bucket OR group present


def test_google_search_url_invalid_bucket_raises():
    """Unknown bucket name raises ValueError (catch typos early)."""
    with pytest.raises(ValueError):
        social.google_search_url("王", bucket="unknown")


def test_google_search_url_special_chars_in_name():
    """Names with quotes, ampersands, Chinese — URL must be properly encoded."""
    url = social.google_search_url('A&B "Studio"')
    # We just check the URL is valid + can be decoded back to recognizable text
    q = _decode_q(url)
    assert 'A&B' in q or 'A%26B' in q  # depending on how & is encoded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vetting_social.py -v -k google_search_url`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement `google_search_url()`**

Append to `vetting/social.py`:

```python
from urllib.parse import quote_plus


def google_search_url(name: str, *, bucket=None, disambiguators=None) -> str:
    """Build a Google search URL for `name`, optionally narrowed by a keyword
    bucket and disambiguator tokens.

    Args:
        name: Person's name (will be quoted in the query).
        bucket: One of 'company' / 'award' / 'negative' / None.
                None = general search (no keyword filter).
        disambiguators: Extra tokens to AND-narrow the search (phone variants,
                relative names, feature words). Each gets quote-wrapped.

    Returns:
        Full https URL string with q= encoded.

    Raises:
        ValueError: bucket is not in BUCKET_KEYWORDS keys.
    """
    parts = [f'"{name}"']

    if bucket is not None:
        if bucket not in BUCKET_KEYWORDS:
            raise ValueError(f"unknown bucket: {bucket!r}")
        bucket_or = " OR ".join(BUCKET_KEYWORDS[bucket])
        parts.append(f"({bucket_or})")

    if disambiguators:
        for tok in disambiguators:
            if tok:  # skip empty strings
                parts.append(f'"{tok}"')

    q = " ".join(parts)
    return GOOGLE_SEARCH.format(quote_plus(q))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_vetting_social.py -v -k google_search_url`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add app_unified/vetting/social.py app_unified/tests/test_vetting_social.py
git commit -m "feat(vetting/social): google_search_url with bucket + disambiguator support"
```

---

### Task 5: Implement `build_url_bundle()`

**Files:** same as Task 2

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vetting_social.py`:

```python
def test_build_url_bundle_employee_only():
    """Minimum: only name provided. phone=null, people has 1 employee entry."""
    bundle = social.build_url_bundle("王小明")
    assert bundle["phone"] is None
    assert len(bundle["people"]) == 1
    emp = bundle["people"][0]
    assert emp["name"] == "王小明"
    assert emp["role"] == "employee"
    assert emp["conjunction_with_employee_url"] is None
    assert emp["google_general_url"].startswith("https://www.google.com/search?q=")
    assert emp["google_company_url"]
    assert emp["google_award_url"]
    assert emp["google_negative_url"]
    assert emp["fb_search_url"].startswith("https://www.facebook.com/search/")
    assert emp["ig_search_url"].startswith("https://www.instagram.com/explore/")


def test_build_url_bundle_with_phone():
    """Phone provided → phone dict populated with 3 link-out URLs."""
    bundle = social.build_url_bundle("王小明", phone="0912-345-678")
    p = bundle["phone"]
    assert p is not None
    assert p["normalized"] == "0912-345-678"  # display format
    assert p["whoscall_url"].startswith("https://whoscall.com/")
    assert p["truecaller_url"].startswith("https://www.truecaller.com/")
    assert p["google_phone_url"].startswith("https://www.google.com/search?q=")
    # employee URLs should also bake in phone as disambiguator
    emp = bundle["people"][0]
    decoded = unquote_plus(emp["google_general_url"].split("q=", 1)[1])
    assert "0912-345-678" in decoded or "0912345678" in decoded


def test_build_url_bundle_with_2_relatives():
    """2 relatives → people has 3 entries, each relative has conjunction URL."""
    bundle = social.build_url_bundle(
        "王小明",
        relatives=["李美麗", "張三"]
    )
    assert len(bundle["people"]) == 3
    names = [p["name"] for p in bundle["people"]]
    roles = [p["role"] for p in bundle["people"]]
    assert names == ["王小明", "李美麗", "張三"]
    assert roles == ["employee", "relative", "relative"]
    # Employee block has no conjunction URL
    assert bundle["people"][0]["conjunction_with_employee_url"] is None
    # Each relative has conjunction URL containing both names
    for rel in bundle["people"][1:]:
        url = rel["conjunction_with_employee_url"]
        assert url and url.startswith("https://www.google.com/")
        q = unquote_plus(url.split("q=", 1)[1])
        assert '"王小明"' in q
        assert f'"{rel["name"]}"' in q


def test_build_url_bundle_relative_count_cap():
    """Pass 3+ relatives → trimmed to first 2 (matches spec §2.1 cap)."""
    bundle = social.build_url_bundle(
        "王小明",
        relatives=["A", "B", "C", "D", "E"]
    )
    assert len(bundle["people"]) == 3  # 1 employee + max 2 relatives
    assert [p["name"] for p in bundle["people"][1:]] == ["A", "B"]


def test_build_url_bundle_feature_baked_into_general_search():
    """Feature ('新北市 ABC') tokens appear in general search disambiguators."""
    bundle = social.build_url_bundle(
        "王小明",
        feature="新北市 ABC餐廳"
    )
    emp = bundle["people"][0]
    q = unquote_plus(emp["google_general_url"].split("q=", 1)[1])
    assert "新北市" in q and "ABC餐廳" in q


def test_build_url_bundle_idempotent():
    """Same input → identical output (no random ordering, no time-based fields)."""
    args = {
        "name": "王小明",
        "phone": "0912-345-678",
        "relatives": ["李美麗", "張三"],
        "feature": "新北市",
    }
    b1 = social.build_url_bundle(**args)
    b2 = social.build_url_bundle(**args)
    assert b1 == b2


def test_build_url_bundle_relatives_disambiguate_each_other():
    """For relative 李美麗, the general search uses 王小明 + other relative + feature
    as disambiguators (so Google narrows on the family network)."""
    bundle = social.build_url_bundle(
        "王小明",
        relatives=["李美麗", "張三"],
        feature="新北市"
    )
    li = bundle["people"][1]  # 李美麗
    q = unquote_plus(li["google_general_url"].split("q=", 1)[1])
    assert '"李美麗"' in q
    assert '"王小明"' in q   # employee is a disambiguator for relative
    assert '"張三"' in q     # other relative is a disambiguator
    assert "新北市" in q     # feature is a disambiguator


def test_build_url_bundle_empty_phone_string_treated_as_no_phone():
    """phone="" → phone dict is None (not crashing)."""
    bundle = social.build_url_bundle("王小明", phone="")
    assert bundle["phone"] is None


def test_build_url_bundle_invalid_phone_treated_as_no_phone():
    """phone="abc" → phone dict is None (normalize returns "")."""
    bundle = social.build_url_bundle("王小明", phone="not-a-phone")
    assert bundle["phone"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vetting_social.py -v -k build_url_bundle`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement `build_url_bundle()`**

Append to `vetting/social.py`:

```python
def _phone_display_format(canonical: str) -> str:
    """0912345678 → 0912-345-678 for showing to humans."""
    return f"{canonical[:4]}-{canonical[4:7]}-{canonical[7:]}"


def _build_person(name, *, role, disambiguators, conjunction_with=None):
    """Build a single person's URL block (employee or relative)."""
    return {
        "name": name,
        "role": role,
        "google_general_url":  google_search_url(name, disambiguators=disambiguators),
        "google_company_url":  google_search_url(name, bucket="company"),
        "google_award_url":    google_search_url(name, bucket="award"),
        "google_negative_url": google_search_url(name, bucket="negative"),
        "fb_search_url": FB_SEARCH.format(quote_plus(name)),
        "ig_search_url": IG_SEARCH.format(quote_plus(name.replace(" ", ""))),
        "conjunction_with_employee_url": (
            google_search_url(conjunction_with, disambiguators=[name])
            if conjunction_with else None
        ),
    }


def build_url_bundle(name, phone="", relatives=None, feature=""):
    """Produce the full URL bundle for the result page.

    Deterministic, no IO, no DB.

    Args:
        name:       Employee name (required).
        phone:      Optional phone (any format normalize_phone accepts).
        relatives:  Optional list of relative names (max 2 used, rest dropped).
        feature:    Optional free-text feature (split on whitespace for tokens).

    Returns:
        dict with keys 'phone' and 'people' as documented in spec §3.2.
    """
    relatives = list(relatives or [])[:2]  # cap to 2
    feature_tokens = feature.split() if feature else []

    # Phone block
    canonical = normalize_phone(phone)
    phone_block = None
    if canonical:
        variants = phone_query_variants(canonical)
        or_query = " OR ".join(f'"{v}"' for v in variants)
        phone_block = {
            "normalized":      _phone_display_format(canonical),
            "whoscall_url":    WHOSCALL.format(_phone_display_format(canonical).replace("-", "-")),
            "truecaller_url": TRUECALLER.format(canonical),
            "google_phone_url": GOOGLE_SEARCH.format(quote_plus(or_query)),
        }

    # People list
    people = []

    # Employee: disambiguators = phone variants + relatives + feature
    emp_disambig = (
        phone_query_variants(canonical) if canonical else []
    ) + relatives + feature_tokens
    people.append(_build_person(
        name, role="employee",
        disambiguators=emp_disambig,
        conjunction_with=None,
    ))

    # Relatives: disambiguators = employee name + other relatives + feature
    for i, rel_name in enumerate(relatives):
        other_relatives = [r for j, r in enumerate(relatives) if j != i]
        rel_disambig = [name] + other_relatives + feature_tokens
        people.append(_build_person(
            rel_name, role="relative",
            disambiguators=rel_disambig,
            conjunction_with=name,
        ))

    return {"phone": phone_block, "people": people}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_vetting_social.py -v`
Expected: PASS (all unit tests so far)

- [ ] **Step 5: Commit**

```bash
git add app_unified/vetting/social.py app_unified/tests/test_vetting_social.py
git commit -m "feat(vetting/social): build_url_bundle composes complete URL bundle"
```

---

### Task 6: Modify `/vetting/start` to accept new fields

**Files:**
- Modify: `app_unified/vetting/routes.py:64-157` (the `start()` function)
- Modify: `app_unified/tests/test_vetting_routes.py` (append new tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vetting_routes.py` (after existing tests, before line ~239):

```python
# ────────────────────────────────────────────────────────────────
# Social / Google link-out (spec 2026-05-13)
# ────────────────────────────────────────────────────────────────

def _mock_npa_judicial_mjib(monkeypatch):
    """Common monkeypatch helper: stub external scraping/captcha calls."""
    monkeypatch.setattr("vetting.routes.npa.fetch_home_state",
                        lambda sess=None: ({"_csrf": "x", "_csrf_header": "h", "_key": "k"}, MagicMock()))
    monkeypatch.setattr("vetting.routes.npa.fetch_auth_token", lambda sess, tok: "JWT")
    monkeypatch.setattr("vetting.routes.npa.fetch_captcha_bytes", lambda sess: b"\xff\xd8FAKE")
    monkeypatch.setattr("vetting.routes.judicial.fetch_home_state",
                        lambda sess=None: ({"form_action": "x", "hidden_fields": {}}, MagicMock()))
    monkeypatch.setattr("vetting.routes.judicial.submit_search", lambda sess, state, name: [])
    monkeypatch.setattr("vetting.routes.mjib.search_by_name", lambda name, session=None: [])
    monkeypatch.setattr("vetting.routes.throttle_sleep", lambda: None)


def test_start_accepts_new_social_fields(app, client, db_session, monkeypatch):
    """POST /start with phone/relatives/feature → 200, state has 'social' key."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)

    res = client.post("/vetting/start", json={
        "name": "王小明",
        "id": "A123456789",
        "phone": "0912-345-678",
        "relatives": ["李美麗", "張三"],
        "feature": "新北市",
    })
    assert res.status_code == 200
    data = res.get_json()
    assert "query_id" in data

    # Inspect state to confirm social bundle is stashed
    from vetting.routes import _take_state
    state = _take_state(data["query_id"])
    assert "social" in state
    assert state["social"]["phone"] is not None
    assert len(state["social"]["people"]) == 3  # 1 employee + 2 relatives


def test_start_social_optional_fields_default_to_no_phone_no_relatives(
        app, client, db_session, monkeypatch):
    """Omitting new fields → social bundle still produced, phone null, employee only."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)

    res = client.post("/vetting/start", json={"name": "王小明"})
    assert res.status_code == 200
    from vetting.routes import _take_state
    state = _take_state(res.get_json()["query_id"])
    assert state["social"]["phone"] is None
    assert len(state["social"]["people"]) == 1


def test_start_relatives_trimmed_to_2(app, client, db_session, monkeypatch):
    """Sending 4 relatives → backend caps at 2 (defense in depth, UI also caps)."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    _mock_npa_judicial_mjib(monkeypatch)

    res = client.post("/vetting/start", json={
        "name": "王",
        "relatives": ["A", "B", "C", "D"],
    })
    assert res.status_code == 200
    from vetting.routes import _take_state
    state = _take_state(res.get_json()["query_id"])
    assert len(state["social"]["people"]) == 3  # 1 + 2 (trimmed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && pytest tests/test_vetting_routes.py -v -k social`
Expected: FAIL — `state` won't have "social" key yet.

- [ ] **Step 3: Modify `vetting/routes.py` — `/start` endpoint**

In `vetting/routes.py`, at top imports add:

```python
from vetting import judicial, mjib, npa, social
```

(Currently line 12 imports `judicial, mjib, npa` — extend to also import `social`.)

In `start()` function, **at the very top after parsing `data`**, parse the new fields:

```python
@vetting_bp.route("/start", methods=["POST"])
@login_required
@limiter.limit("3/minute", key_func=lambda: f"vet-start:{current_user.id}")
def start():
    """1) 取 NPA 驗證碼   2) 同步先打司法院 + MJIB   3) 暫存 + 回 captcha image。"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    id_no = (data.get("id") or "").strip()
    # NEW: social fields (all optional)
    phone = (data.get("phone") or "").strip()
    relatives = [r.strip() for r in (data.get("relatives") or []) if r and r.strip()]
    feature = (data.get("feature") or "").strip()
    if not name:
        return jsonify({"error": "missing_params", "message": "姓名必填"}), 400
    # ... existing body ...
```

**At the `_put_state(...)` call near the end**, add a `social` key built from the new fields:

```python
    qid = _put_state({
        "npa_session": npa_session,
        "npa_tokens": npa_tokens,
        "npa_jwt": npa_jwt,
        "name": name,
        "id_no": id_no,
        "judicial": judicial_results,
        "mjib": mjib_results,
        "social": social.build_url_bundle(   # ← NEW
            name, phone=phone, relatives=relatives, feature=feature
        ),
    })
```

- [ ] **Step 4: Run tests**

Run: `cd app_unified && pytest tests/test_vetting_routes.py -v -k social`
Expected: PASS (3 social tests)

- [ ] **Step 5: Run full vetting test suite to verify no regression**

Run: `cd app_unified && pytest tests/test_vetting_routes.py -v`
Expected: PASS (all existing tests still green + new social tests).

- [ ] **Step 6: Commit**

```bash
git add app_unified/vetting/routes.py app_unified/tests/test_vetting_routes.py
git commit -m "feat(vetting): /start accepts phone/relatives/feature, stashes URL bundle"
```

---

### Task 7: Modify `/vetting/submit` to return `social` in response

**Files:**
- Modify: `app_unified/vetting/routes.py:160-202` (the `submit()` function)
- Modify: `app_unified/tests/test_vetting_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vetting_routes.py`:

```python
def test_submit_response_includes_social_key(app, client, db_session, monkeypatch):
    """End-to-end: /submit response must include 'social' alongside judicial/mjib/npa."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    from vetting import social as social_mod
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "",
        "judicial": [],
        "mjib": [],
        "social": social_mod.build_url_bundle(
            "王", phone="0912345678", relatives=["李"]
        ),
    })
    monkeypatch.setattr("vetting.routes.npa.submit_query", lambda *a, **kw: [])

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "ABCD"})
    assert res.status_code == 200
    data = res.get_json()
    assert "judicial" in data
    assert "mjib" in data
    assert "npa" in data
    assert "social" in data                          # ← regression-proof for spec §9
    assert data["social"]["phone"] is not None
    assert len(data["social"]["people"]) == 2  # 1 emp + 1 rel


def test_submit_response_shape_unchanged_for_existing_keys(
        app, client, db_session, monkeypatch):
    """Regression: existing judicial/mjib/npa shapes are byte-identical to pre-spec."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    from vetting.routes import _put_state
    from vetting import social as social_mod
    qid = _put_state({
        "npa_session": MagicMock(),
        "npa_tokens": {"_csrf": "x", "_csrf_header": "h", "_key": "k"},
        "npa_jwt": "jwt",
        "name": "王", "id_no": "A123",
        "judicial": [{"subject": "x", "case_no": "n", "date": "d",
                      "court": "c", "detail_url": "u",
                      "keyword_hits": [], "masked_names": []}],
        "mjib": [{"name": "王", "eng_name": "", "masked_id": "A***",
                  "birth": "1990", "photo_url": "", "detail_url": ""}],
        "social": social_mod.build_url_bundle("王"),
    })
    monkeypatch.setattr("vetting.routes.npa.submit_query",
                        lambda *a, **kw: [{"name": "王", "crime": "竊盜", "detail_url": ""}])

    res = client.post("/vetting/submit",
                      json={"query_id": qid, "captcha": "ABCD"})
    data = res.get_json()
    # judicial entry shape
    assert data["judicial"][0]["subject"] == "x"
    assert "keyword_hits" in data["judicial"][0]
    # mjib entry shape
    assert data["mjib"][0]["name"] == "王"
    assert "masked_id" in data["mjib"][0]
    # npa entry shape
    assert data["npa"][0]["name"] == "王"
    assert data["npa"][0]["crime"] == "竊盜"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vetting_routes.py -v -k submit_response`
Expected: FAIL — response won't have "social" key yet.

- [ ] **Step 3: Modify `vetting/routes.py` — `/submit` endpoint**

In `submit()`, at the final `return jsonify(...)` call (currently line ~198):

```python
    # 一次性使用：清掉 state
    _QUERY_STATE.pop(qid, None)

    return jsonify({
        "judicial": entry["judicial"],
        "mjib": entry["mjib"],
        "npa": npa_results,
        "social": entry["social"],   # ← NEW (always present, may have phone=null + 1-element people list)
    })
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_vetting_routes.py -v -k submit`
Expected: PASS (new tests + existing submit tests).

- [ ] **Step 5: Run FULL test suite to confirm no regression anywhere**

Run: `cd app_unified && pytest tests/ -v`
Expected: PASS (entire suite green).

- [ ] **Step 6: Commit**

```bash
git add app_unified/vetting/routes.py app_unified/tests/test_vetting_routes.py
git commit -m "feat(vetting): /submit returns 'social' URL bundle in response"
```

---

## Task Breakdown — Frontend (PR 2 scope)

### Task 8: Add 3 new input fields + relatives 2-row progressive UI

**Files:**
- Modify: `app_unified/templates/vetting/index.html:7-18` (the `<form id="vet-name-form">` block)

- [ ] **Step 1: Modify the input form**

Replace the current `<div id="step-name">` block (lines 7-18) with the new form including 3 optional fields. The new form keeps the existing 姓名 + 身分證 + 開始查詢 button at the top, then adds collapsible "可選填欄位" section below:

```html
  <!-- Step 1: 姓名 + 身分證 + 選填欄位 -->
  <div id="step-name" style="margin-top:1rem;">
    <form id="vet-name-form">
      <label style="display:block; margin-bottom:.4rem;">姓名</label>
      <input type="text" id="vet-name" required
             style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.75rem;">

      <label style="display:block; margin-bottom:.4rem;">身分證 / 居留證 <span style="color:#888;font-weight:400;font-size:.85rem;">（選填）</span></label>
      <input type="text" id="vet-id" maxlength="12"
             style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.75rem; text-transform:uppercase;">

      <details style="margin-bottom:.75rem;">
        <summary style="cursor:pointer; padding:.4rem 0; color:#555; font-size:.92rem;">▸ 選填：電話 / 關係人 / 其他特徵（增加搜尋準確度）</summary>

        <label style="display:block; margin:.6rem 0 .4rem;">📞 電話 <span style="color:#888;font-weight:400;font-size:.85rem;">（多種格式都接受）</span></label>
        <input type="tel" id="vet-phone"
               placeholder="例：0912-345-678 / +886912345678"
               style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.75rem;">

        <label style="display:block; margin-bottom:.4rem;">👥 關係人 <span style="color:#888;font-weight:400;font-size:.85rem;">（最多 2 人，純姓名）</span></label>
        <div id="vet-relatives">
          <input type="text" class="vet-rel-input" placeholder="關係人 1"
                 style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.4rem;">
        </div>
        <button type="button" id="vet-add-rel"
                style="background:none; border:1px dashed #888; padding:.4rem .8rem; border-radius:6px; cursor:pointer; font-size:.88rem; margin-bottom:.75rem;">
          + 新增關係人
        </button>

        <label style="display:block; margin-bottom:.4rem;">🏷 其他特徵 <span style="color:#888;font-weight:400;font-size:.85rem;">（縣市、公司、學校等）</span></label>
        <input type="text" id="vet-feature"
               placeholder="例：新北市 ABC餐廳"
               style="width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.75rem;">
      </details>

      <button type="submit" class="btn btn-primary" style="width:100%; padding:.6rem;">開始查詢</button>
    </form>
  </div>
```

- [ ] **Step 2: Add JS to handle "+ 新增關係人" (progressive 2nd row)**

In the existing `<script>` block of `index.html`, after the `function escapeHtml(...)` definition (around line 74), insert:

```javascript
// 關係人「+ 新增關係人」progressive UI (max 2 rows, then lock the button)
document.getElementById('vet-add-rel').addEventListener('click', function() {
  var container = document.getElementById('vet-relatives');
  if (container.querySelectorAll('.vet-rel-input').length >= 2) return;
  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'vet-rel-input';
  input.placeholder = '關係人 2';
  input.style.cssText = 'width:100%; padding:.55rem; border:1px solid #ddd; border-radius:6px; font-size:1rem; margin-bottom:.4rem;';
  container.appendChild(input);
  // Lock the button after reaching 2
  if (container.querySelectorAll('.vet-rel-input').length >= 2) {
    document.getElementById('vet-add-rel').style.display = 'none';
  }
});
```

- [ ] **Step 3: Modify the submit handler to send new fields**

Find the existing `document.getElementById('vet-name-form').addEventListener('submit', ...)` block (around line 76). Replace its `body: JSON.stringify({name: _name, id: _id})` with:

```javascript
  // Collect relatives (max 2, filter empties)
  var _relatives = Array.from(document.querySelectorAll('.vet-rel-input'))
    .map(function(i) { return i.value.trim(); })
    .filter(function(v) { return v !== ''; })
    .slice(0, 2);
  var _phone = document.getElementById('vet-phone').value.trim();
  var _feature = document.getElementById('vet-feature').value.trim();
  // Stash for reloadCaptcha re-use (same fields on second /start call)
  window._socialPayload = {phone: _phone, relatives: _relatives, feature: _feature};

  startTick('🔍 查詢中：警政署取驗證碼 → 司法院刑事判決 → 法務部通緝');
  fetch('/vetting/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: _name, id: _id,
      phone: _phone, relatives: _relatives, feature: _feature
    })
  }).then(...)
```

And in `reloadCaptcha()` (around line 101), update its body to include the stashed payload so re-fetch uses same fields:

```javascript
function reloadCaptcha() {
  var sp = window._socialPayload || {phone: '', relatives: [], feature: ''};
  startTick('🔄 重整驗證碼中（會重跑三家查詢）');
  fetch('/vetting/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: _name, id: _id,
      phone: sp.phone, relatives: sp.relatives, feature: sp.feature
    })
  }).then(...)
  // ... rest unchanged ...
}
```

- [ ] **Step 4: Test in browser — confirm form renders + relatives row toggles**

Run: `bash scripts/run_local.sh`
Open: `http://localhost:5000/dev` → login as a vetting-enabled user → go to `/vetting/`
Manually verify:
- 「選填」details collapsed by default
- Click expand → 4 fields show: 電話 / 關係人 1 / 其他特徵
- Click「+ 新增關係人」→ 「關係人 2」 row appears, button hides
- Refresh page → state resets

- [ ] **Step 5: Commit**

```bash
git add app_unified/templates/vetting/index.html
git commit -m "feat(vetting): add phone/relatives/feature input fields + progressive 2-row UI"
```

---

### Task 9: Render D (電話標記) + E (Google 搜尋) + F (FB/IG) sections

**Files:**
- Modify: `app_unified/templates/vetting/index.html` (extend `renderResult()` function around line 150)

- [ ] **Step 1: Append D/E/F render logic to `renderResult()`**

In `templates/vetting/index.html`, in the `renderResult(d)` function (currently lines 150-236), **before the final** `html += '<div style="margin-top:1rem;"><a href="/vetting/" ...';` (the 「重新查詢」 link), add the new sections.

Insert this block after the existing `// ── C. NPA ──` rendering block ends (around line 231):

```javascript
  // ── Social warning banner + D/E/F ──
  if (d.social && d.social.people && d.social.people.length > 0) {

    // Banner: incognito-mode guide
    html += '<details open style="margin-top:1.5rem; border:1px solid #fcd34d; background:#fef3c7; border-radius:6px; padding:.5rem .75rem;">';
    html += '<summary style="cursor:pointer; font-weight:600;">ℹ️ 點連結前請先看 — 如何用無痕模式開啟</summary>';
    html += '<div style="margin-top:.5rem; font-size:.88rem; line-height:1.6; color:#444;">';
    html += '直接點連結會在當前瀏覽器開啟、登入的 FB / Google 帳號會記錄你查詢過誰。<br>強烈建議用「無痕視窗」開：<br>';
    html += '• 桌機 Chrome / Edge：右鍵連結 → 「在無痕視窗中開啟連結」<br>';
    html += '• 桌機 Safari（Mac）：右鍵連結 → 「在新的私密視窗中開啟連結」<br>';
    html += '• 桌機 Firefox：右鍵連結 → 「在隱私視窗中開啟連結」<br>';
    html += '• 手機 Chrome（Android）：長按連結 → 「在無痕分頁中開啟」<br>';
    html += '• 手機 Safari（iPhone / iPad）：長按連結 → 「在私密瀏覽中開啟」<br>';
    html += '</div></details>';

    // ── D. 電話標記 ──
    if (d.social.phone) {
      var p = d.social.phone;
      html += '<h2 style="margin-top:1rem;">D. 📞 電話標記</h2>';
      html += '<div style="border:1px solid #e0e0e0; border-radius:6px; padding:.6rem; background:#f9fafb;">';
      html += '<div style="font-family:monospace; font-size:1.05rem; margin-bottom:.4rem;">' + escapeHtml(p.normalized) + '</div>';
      html += '<a href="' + escapeHtml(p.whoscall_url) + '" target="_blank" rel="noopener" class="btn btn-secondary" style="margin-right:.4rem; margin-bottom:.3rem; display:inline-block; padding:.4rem .7rem;">→ Whoscall</a>';
      html += '<a href="' + escapeHtml(p.truecaller_url) + '" target="_blank" rel="noopener" class="btn btn-secondary" style="margin-right:.4rem; margin-bottom:.3rem; display:inline-block; padding:.4rem .7rem;">→ TrueCaller</a>';
      html += '<a href="' + escapeHtml(p.google_phone_url) + '" target="_blank" rel="noopener" class="btn btn-secondary" style="display:inline-block; padding:.4rem .7rem;">→ Google 電話搜尋</a>';
      html += '</div>';
    }

    // ── E + F: per-person blocks (Google + FB/IG link-out, Option C collapsible) ──
    html += '<h2 style="margin-top:1rem;">E + F. 🌐 社群 / Google 搜尋連結</h2>';
    d.social.people.forEach(function(person, idx) {
      var isEmployee = (person.role === 'employee');
      var openAttr = isEmployee ? ' open' : '';
      var icon = isEmployee ? '👤' : '👥';
      var label = isEmployee ? '員工本人' : ('關係人 ' + idx);
      html += '<details' + openAttr + ' class="vet-person-block" style="border:1px solid #e0e0e0; border-radius:6px; padding:.5rem .75rem; margin-bottom:.5rem;">';
      html += '<summary style="cursor:pointer; font-weight:600;">' + icon + ' ' + escapeHtml(person.name) + '（' + label + '）</summary>';
      html += '<div style="margin-top:.5rem;">';

      // Google sub-menu (collapsible inside)
      html += '<details style="margin-bottom:.4rem;">';
      html += '<summary style="cursor:pointer; color:#0e7490;">🌐 Google 搜尋 ▾</summary>';
      html += '<div style="margin-top:.3rem; padding-left:.8rem; display:flex; flex-direction:column; gap:.3rem;">';
      html += '<a href="' + escapeHtml(person.google_general_url) + '" target="_blank" rel="noopener">→ 一般搜尋（含已填條件）</a>';
      html += '<a href="' + escapeHtml(person.google_company_url) + '" target="_blank" rel="noopener">🏢 公司/採購相關</a>';
      html += '<a href="' + escapeHtml(person.google_award_url) + '" target="_blank" rel="noopener">🏆 獎項/證照相關</a>';
      html += '<a href="' + escapeHtml(person.google_negative_url) + '" target="_blank" rel="noopener" style="color:#b91c1c;">🚨 警方/負面相關</a>';
      html += '</div></details>';

      // FB / IG buttons
      html += '<div style="display:flex; flex-wrap:wrap; gap:.4rem;">';
      html += '<a href="' + escapeHtml(person.fb_search_url) + '" target="_blank" rel="noopener" class="btn btn-secondary" style="padding:.4rem .7rem;">📱 FB 搜尋</a>';
      html += '<a href="' + escapeHtml(person.ig_search_url) + '" target="_blank" rel="noopener" class="btn btn-secondary" style="padding:.4rem .7rem;">📱 IG 搜尋</a>';
      if (person.conjunction_with_employee_url) {
        html += '<a href="' + escapeHtml(person.conjunction_with_employee_url) + '" target="_blank" rel="noopener" class="btn btn-secondary" style="padding:.4rem .7rem;">🔗 與員工共同搜尋</a>';
      }
      html += '</div>';
      html += '</div></details>';
    });
  }
```

(Then the existing `html += '<div style="margin-top:1rem;"><a href="/vetting/" ...';` stays as the final element.)

- [ ] **Step 2: Local browser smoke test**

Run: `bash scripts/run_local.sh`
- Open `http://localhost:5000/dev`, switch to a vetting-enabled user
- Go to `/vetting/`
- Fill: 姓名=王小明, 電話=0912345678, 關係人 1=李美麗, 關係人 2=張三, 特徵=新北市
- Click 開始查詢 → captcha 顯示後輸入 (mock dev 環境的 NPA call 會跑、但 captcha 可以亂填)

> ⚠ Dev 環境 NPA 是真的會打外部 (outbound_guard 白名單已含 npa.gov.tw)、可能會被擋。
> 如果 NPA 不通可以暫時 monkeypatch / 用 stub 跑、focus 在驗證 D/E/F 渲染。

Manually verify:
- 警語 banner 出現在 C 段下方、預設展開
- D 段「📞 電話標記」出現、3 個 link button 顯示電話
- E+F 段：員工區塊預設展開、2 個關係人區塊預設摺疊
- 點員工 Google ▾ → 4 個 sub-link 出現
- 點關係人 header → 區塊展開、有「🔗 與員工共同搜尋」按鈕
- 右鍵任一連結 → 瀏覽器原生 menu 顯示「在無痕視窗開啟連結」選項

- [ ] **Step 3: Commit**

```bash
git add app_unified/templates/vetting/index.html
git commit -m "feat(vetting): render D/E/F result sections (phone/Google/FB-IG link-out)"
```

---

### Task 10: Frontend smoke tests (DOM-level)

**Files:**
- Modify: `app_unified/tests/test_vetting_routes.py`

- [ ] **Step 1: Write smoke tests for index page (form fields present)**

Append to `tests/test_vetting_routes.py`:

```python
def test_vetting_index_has_new_input_fields(app, client, db_session):
    """GET /vetting/ renders the new optional fields in the form."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/vetting/")
    html = res.get_data(as_text=True)
    # Expand panel summary
    assert "選填：電話 / 關係人 / 其他特徵" in html
    # Input field IDs
    assert 'id="vet-phone"' in html
    assert 'class="vet-rel-input"' in html
    assert 'id="vet-feature"' in html
    # The + button
    assert 'id="vet-add-rel"' in html


def test_vetting_index_has_social_render_logic(app, client, db_session):
    """index.html includes the renderResult logic for D/E/F sections."""
    u = _mk_user(db_session, root_admin=True)
    _login(client, u)
    res = client.get("/vetting/")
    html = res.get_data(as_text=True)
    # Banner text
    assert "如何用無痕模式開啟" in html
    # Section headers (in the JS, will be rendered when social data present)
    assert "D. 📞 電話標記" in html
    assert "E + F. 🌐 社群 / Google 搜尋連結" in html
    # All 5 platform incognito guides present
    assert "Chrome / Edge" in html
    assert "Safari（Mac）" in html
    assert "Firefox" in html
    assert "Chrome（Android）" in html
    assert "Safari（iPhone" in html
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_vetting_routes.py -v -k "index_has"`
Expected: PASS (2 new smoke tests).

- [ ] **Step 3: Run full vetting test suite**

Run: `pytest tests/test_vetting_*.py -v`
Expected: PASS (all unit + route + smoke tests).

- [ ] **Step 4: Run FULL project test suite (regression sweep)**

Run: `cd app_unified && pytest tests/ -v --tb=short 2>&1 | tail -40`
Expected: same pass/fail count as before this branch (no new failures).

- [ ] **Step 5: Commit**

```bash
git add app_unified/tests/test_vetting_routes.py
git commit -m "test(vetting): smoke tests for new input fields + render logic in index.html"
```

---

### Task 11: End-to-end local verification (workflow step, not auto-testable)

**Files:** none (manual verification step)

- [ ] **Step 1: Start local dev server**

Run: `cd app_unified && bash scripts/run_local.sh`
- Wait for "Running on http://127.0.0.1:5000"
- Confirm `dev/outbound_guard` warning visible (means dev mode active)

- [ ] **Step 2: Test full flow in Chrome**

- Open `http://localhost:5000/dev` → click an admin user (e.g. seed `admin`)
- Navigate to `http://localhost:5000/vetting/`
- Fill the form:
  - 姓名：`王小明` (any test name; this is a real query so won't return harmful results for a common test name)
  - 身分證：留空
  - 點 details 展開 → 電話 `0912-345-678` / 關係人 1 `李美麗` / 點 + 新增 → 關係人 2 `張三` / 特徵 `新北市`
- 開始查詢 → 等 captcha (可能 fail，因為 dev 環境的 outbound_guard 應允 npa.gov.tw、但 NPA 可能有 IP 風控、不一定通)
- 如果 captcha 拿到 → 隨便輸入 4 個字送出（會被 NPA 拒、回 captcha_invalid）→ 重整一次

> 對於本地測試，**這步主要驗證的是前端 render**，不是 NPA 真的查到結果。如果 NPA 卡關，可以在另一個 terminal 直接 `curl` 模擬。

- [ ] **Step 3: Alternative — mock the entire flow via direct DB / curl injection**

Open a Python shell with the app loaded:

```bash
cd app_unified
python -c "
from app import app
from vetting import social
b = social.build_url_bundle('王小明', phone='0912345678', relatives=['李美麗','張三'], feature='新北市')
import json; print(json.dumps(b, ensure_ascii=False, indent=2))
"
```

Verify the printed bundle has:
- `phone.normalized == "0912-345-678"`
- `len(people) == 3`
- 各 URL 都是 `https://` 開頭
- 點任一 URL（複製到瀏覽器）→ 應該打開有意義的 Google / FB / IG / Whoscall / TrueCaller 結果頁

- [ ] **Step 4: Stop dev server**

Press `Ctrl+C` (or in another terminal: `fuser -k 5000/tcp`).

- [ ] **Step 5: Mark this task complete in the plan**

No commit (no code change). Move on to the next.

---

## Post-Implementation: Verification before push

After all tasks done:

```bash
# 1. Make sure branch is clean
git status
# expected: nothing to commit, working tree clean

# 2. Full test suite green
cd app_unified
pytest tests/ -v --tb=short 2>&1 | tail -10
# expected: N passed, 0 failed, 0 errors

# 3. View the branch commits
git log --oneline main..HEAD
# expected: ~10 commits matching the tasks above

# 4. Diff summary vs main
git diff --stat main..HEAD
# expected: 5 files changed (vetting/social.py + tests/test_vetting_social.py + vetting/routes.py + tests/test_vetting_routes.py + templates/vetting/index.html)
```

**Do NOT push.** Per [[feedback_push_policy]] / user's session preference. Wait for user to say "OK push".

---

## Self-Review Checklist (run after writing this plan)

**1. Spec coverage** — Each spec section traced to a task:
- §2.1 Input fields (5 欄位) → T8
- §2.2 警語 banner → T9 (Step 1)
- §2.2 D/E/F section ordering → T9
- §2.2 員工展開 / 關係人摺疊 → T9 `<details open>` vs `<details>`
- §2.2 Google ▾ 子選單 → T9 nested `<details>` inside person block
- §3.2 `normalize_phone` / `phone_query_variants` / `google_search_url` / `build_url_bundle` → T2/T3/T4/T5
- §3.3 `/start` 接新欄位 → T6
- §3.3 `/submit` 回 social → T7
- §6.1 VettingQueryLog unchanged → T6 doesn't touch model (implicit guarantee)
- §9 既有 shape regression → T7 Step 1 second test
- §10.1 Unit tests (12) → T1-T5 produce 14 parametrize cases for normalize + 6 for build_url_bundle (covers spec count) ✓
- §10.2 Route tests (6) → T6 (3) + T7 (2) + T10 (2) = 7 ✓
- §10.3 Frontend smoke (3) → T10 (2 smokes for index.html) + T11 (manual E2E) ✓
- §11 部署 (純 image refresh) → no env var added; PR notes in §12 of spec hold

**2. Placeholder scan** — no "TBD", "TODO", "fill in later" in plan ✓

**3. Type consistency**:
- `build_url_bundle()` returns `dict` with `phone` (dict or None) + `people` (list of dicts) — used in T6/T7/T9 consistently ✓
- Each `person` has stable keys (name, role, google_general_url, google_company_url, google_award_url, google_negative_url, fb_search_url, ig_search_url, conjunction_with_employee_url) — used in T9 ✓
- `phone` block keys (normalized, whoscall_url, truecaller_url, google_phone_url) — used in T9 ✓
- `bucket` values: `"company"` / `"award"` / `"negative"` — same in T4 + T1 + T5 ✓

**4. Conventions**:
- Test files: unit tests in `test_vetting_social.py` (new), route tests appended to existing `test_vetting_routes.py` (matches per-source-file convention; spec said "test_vetting_routes_social.py" but project convention overrides) — noted in File Structure section ✓
- Commit message style: matches recent commits (e.g. `feat(vetting): ...`) ✓
