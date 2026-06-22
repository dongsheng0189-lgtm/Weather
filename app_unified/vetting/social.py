"""Pure URL builder for vetting social/Google link-out search.

No outbound HTTP. No DB. Pure deterministic functions.
See docs/superpowers/specs/2026-05-13-vetting-social-search-design.md
"""

GOOGLE_SEARCH = "https://www.google.com/search?q={}"
# FB 改走公開人名目錄 /public/<name>：登出也能列出同名的人，不必登入搜尋
# （search/top 登出會 not found）。名字放 path，用 quote（空白→%20）。
FB_SEARCH     = "https://www.facebook.com/public/{}"
# IG 改走 Google 包「"name" instagram」：site:instagram.com 對中文名常查無、
# IG 站內找人又需登入；Google 包繞開登入牆最穩。沿用 GOOGLE_SEARCH template。
IG_SEARCH     = "https://www.google.com/search?q={}"
# Whoscall 公開 number 頁需要登入；改走 Google 搜尋包 site:whoscall.com 抓得到該號碼公開頁
WHOSCALL      = 'https://www.google.com/search?q=%22{}%22+whoscall'
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


from urllib.parse import quote, quote_plus


def google_search_url(name: str, *, bucket=None, disambiguators=None,
                      quote_disambiguators=True) -> str:
    """Build a Google search URL for `name`, optionally narrowed by a keyword
    bucket and disambiguator tokens.

    Args:
        name: Person's name (always quoted = required exact phrase).
        bucket: One of 'company' / 'award' / 'negative' / None.
                None = general search (no keyword filter).
        disambiguators: Extra tokens to narrow the search (relative names,
                feature words). Quoted only when `quote_disambiguators` is True.
        quote_disambiguators: True → each disambiguator is quoted (exact-phrase
                AND, used by the「與員工共同搜尋」conjunction so both names must
                co-occur). False → disambiguators stay unquoted (soft ranking
                terms; used by the general person search so it narrows without
                over-AND-ing every token into a 0-result query).

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
                parts.append(f'"{tok}"' if quote_disambiguators else tok)

    q = " ".join(parts)
    return GOOGLE_SEARCH.format(quote_plus(q))


def _phone_display_format(canonical: str) -> str:
    """0912345678 → 0912-345-678 for showing to humans."""
    return f"{canonical[:4]}-{canonical[4:7]}-{canonical[7:]}"


def _build_person(name, *, role, disambiguators, conjunction_with=None):
    """Build a single person's URL block (employee or relative)."""
    return {
        "name": name,
        "role": role,
        "google_general_url":  google_search_url(
            name, disambiguators=disambiguators, quote_disambiguators=False),
        "google_company_url":  google_search_url(name, bucket="company"),
        "google_award_url":    google_search_url(name, bucket="award"),
        "google_negative_url": google_search_url(name, bucket="negative"),
        # FB 公開人名目錄：名字在 path，用 quote（空白→%20，不可用 + 號）
        "fb_search_url": FB_SEARCH.format(quote(name)),
        # IG 走 Google 包「"name" instagram」（繞開 IG 登入牆、中文名也查得到）
        "ig_search_url": IG_SEARCH.format(quote_plus(f'"{name}" instagram')),
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
            "whoscall_url":    WHOSCALL.format(_phone_display_format(canonical)),  # 保留開頭 0
            "truecaller_url": TRUECALLER.format(canonical),
            "google_phone_url": GOOGLE_SEARCH.format(quote_plus(or_query)),
        }

    # People list
    people = []

    # Employee: disambiguators = relatives + feature（電話有專屬 D 段，不再 bake 進
    # 一般搜尋——三個電話格式變體全 AND 進去會讓 Google 0 結果）
    emp_disambig = relatives + feature_tokens
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
