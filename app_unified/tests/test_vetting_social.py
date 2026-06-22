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


# ---------------------------------------------------------------------------
# Task 4: google_search_url
# ---------------------------------------------------------------------------
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


def test_google_search_url_disambiguators_unquoted_when_flagged():
    """quote_disambiguators=False → 主體姓名加引號、disambiguator 不加（軟性輔助詞，
    避免一般搜尋把一堆精確片語 AND 起來 → 0 結果）。"""
    url = social.google_search_url(
        "王小明",
        disambiguators=["李美麗", "新北市"],
        quote_disambiguators=False,
    )
    q = _decode_q(url)
    assert '"王小明"' in q        # 主體仍加引號
    assert '李美麗' in q and '"李美麗"' not in q   # 輔助詞不加引號
    assert '新北市' in q and '"新北市"' not in q


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


# ---------------------------------------------------------------------------
# Task 5: build_url_bundle
# ---------------------------------------------------------------------------

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
    # IG 個人頁改走 Google site 搜尋（站內找人需登入、人名當 hashtag 查不到本人）
    assert emp["ig_search_url"].startswith("https://www.google.com/search?q=")
    ig_q = unquote_plus(emp["ig_search_url"].split("q=", 1)[1])
    assert "site:instagram.com" in ig_q and "王小明" in ig_q


def test_build_url_bundle_with_phone():
    """Phone provided → phone dict populated with 3 link-out URLs."""
    bundle = social.build_url_bundle("王小明", phone="0912-345-678")
    p = bundle["phone"]
    assert p is not None
    assert p["normalized"] == "0912-345-678"  # display format
    # Whoscall 直連公開頁需登入；改走 Google 搜尋抓 Whoscall 該號碼公開頁
    assert p["whoscall_url"].startswith("https://www.google.com/search?q=")
    decoded_w = unquote_plus(p["whoscall_url"].split("q=", 1)[1])
    assert "whoscall" in decoded_w.lower()
    assert "0912-345-678" in decoded_w  # 保留開頭 0（跟一般人寫號碼一致）
    assert p["truecaller_url"].startswith("https://www.truecaller.com/")
    assert p["google_phone_url"].startswith("https://www.google.com/search?q=")
    # 電話有專屬 D 段；一般搜尋不再 bake 電話變體（避免過度 AND → 0 結果）
    emp = bundle["people"][0]
    decoded = unquote_plus(emp["google_general_url"].split("q=", 1)[1])
    assert "0912" not in decoded


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
    assert '"李美麗"' in q          # 主體姓名仍加引號（必要精確詞）
    assert '"王小明"' not in q      # disambiguator 不再加引號（避免過度 AND）
    assert '王小明' in q            # 但仍出現，當排序輔助詞
    assert '張三' in q
    assert "新北市" in q


def test_build_url_bundle_empty_phone_string_treated_as_no_phone():
    """phone="" → phone dict is None (not crashing)."""
    bundle = social.build_url_bundle("王小明", phone="")
    assert bundle["phone"] is None


def test_build_url_bundle_invalid_phone_treated_as_no_phone():
    """phone="abc" → phone dict is None (normalize returns "")."""
    bundle = social.build_url_bundle("王小明", phone="not-a-phone")
    assert bundle["phone"] is None
