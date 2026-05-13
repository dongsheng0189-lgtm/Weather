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
