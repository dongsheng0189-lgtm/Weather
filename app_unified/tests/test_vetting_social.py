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
