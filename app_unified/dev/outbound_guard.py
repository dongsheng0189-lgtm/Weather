"""Dev-mode outbound HTTP guard.

When activated (FLASK_DEV_MODE=1), patches `requests.Session.send` so any
non-loopback HTTP request raises immediately. This protects against:

  - vetting/* modules calling 司法院 / 法務部 / 警政署 with test data
  - notes AI summary hitting Anthropic / Gemini if a stale key leaked into env
  - weather routes hitting OpenWeatherMap with seed user IDs
  - Anything else added later that does requests.get(...) on the prod side

Allowed targets: 127.0.0.1, localhost, 0.0.0.0, ::1. Everything else raises
DevOutboundBlocked with the full URL so it's obvious which call slipped.

To disable for a one-off prod debug: set FLASK_DEV_MODE=0 (not recommended).
"""
import logging
from urllib.parse import urlparse

import requests


_LOG = logging.getLogger(__name__)

# 完整 host 比對（loopback）
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1", ""}

# Domain suffix 比對（host == X 或 host.endswith("." + X) 皆通過）
# 這裡列的網域代表「本機 dev 也可打真實服務」— 加新平台直接擴充這個 set
_ALLOWED_DOMAINS = {
    # ── 公務查詢 (vetting 模組) ──
    "judicial.gov.tw",    # 司法院判決書（含 judgment.judicial.gov.tw 等子網域）
    "mjib.gov.tw",        # 法務部調查局通緝公告
    "npa.gov.tw",         # 警政署（含 eze8.npa.gov.tw 等）

    # ── 將來：社群搜尋（user 2026-05-12 預先白名單） ──
    "google.com",         # www.google.com / Search
    "google.com.tw",      # google.com.tw（台灣分站）
    "googleapis.com",     # Custom Search / Knowledge Graph 等 API
    "gstatic.com",        # google 靜態資源
    "facebook.com",       # www / m / mbasic / graph.facebook.com
    "fbcdn.net",          # FB CDN
    "instagram.com",      # www / graph.instagram.com
    "cdninstagram.com",   # IG CDN
}


def _host_allowed(host: str) -> bool:
    if host in _ALLOWED_HOSTS:
        return True
    for dom in _ALLOWED_DOMAINS:
        if host == dom or host.endswith("." + dom):
            return True
    return False


class DevOutboundBlocked(RuntimeError):
    """Raised when dev mode blocks an outbound request."""


def _patched_send(self, request, **kw):
    host = urlparse(request.url).hostname or ""
    if not _host_allowed(host):
        raise DevOutboundBlocked(
            f"FLASK_DEV_MODE=1 blocked outbound HTTP to {request.url} "
            f"(host={host!r}). Edit dev/outbound_guard.py to allowlist if intentional."
        )
    return _original_send(self, request, **kw)


_original_send = requests.Session.send


def install():
    """Install the patch. Idempotent."""
    if getattr(requests.Session.send, "_dev_guard_installed", False):
        return
    _patched_send._dev_guard_installed = True
    requests.Session.send = _patched_send
    _LOG.warning("dev/outbound_guard: requests.Session.send patched — non-loopback HTTP blocked.")
