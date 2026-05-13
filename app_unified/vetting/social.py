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
