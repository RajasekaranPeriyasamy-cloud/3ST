"""HTTP sessions for the news desk that bypass the Kite static-IP proxy.

This is not a micro-optimisation, it is a correctness fix, and it is invisible
until the desk runs inside the API process.

``settings.apply_kite_proxy_env()`` pins ``HTTP_PROXY``/``HTTPS_PROXY``
process-wide so no Kite call can escape the whitelisted static IP — and it
deliberately **deletes** ``NO_PROXY`` so nothing can opt out through the
environment. Every ``requests`` call in the process therefore inherits the
proxy, including ours. Observed 2026-09-02: all eleven news sources went from
healthy standalone to ``ProxyError`` the moment the desk started under uvicorn
with ``KITE_USE_STATICIP_PROXY`` on.

Routing publisher traffic through that proxy is wrong on three counts: it is
metered egress bought for order placement, the proxy is not there to fetch
Reuters copy, and CLAUDE.md states the rule directly — *"Don't route
quote/history calls through the static-IP proxy."* News is even further from
the order path than a quote is.

``trust_env = False`` is what makes requests ignore the environment's proxy
settings. It also disables ``REQUESTS_CA_BUNDLE`` and ``.netrc`` lookups, which
is fine here: these are anonymous GETs of public feeds validated against
certifi's default bundle.
"""

from __future__ import annotations

import requests

# A browser-ish UA. Several Indian publishers 403 the default python-requests
# and feedparser agents outright.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 3ST-NewsDesk/1.0"
)


def direct_session(**headers: str) -> requests.Session:
    """A session that ignores the process-wide Kite proxy."""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {}
    session.headers.update({"User-Agent": USER_AGENT, **headers})
    return session
