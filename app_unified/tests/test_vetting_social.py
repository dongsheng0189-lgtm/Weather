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
