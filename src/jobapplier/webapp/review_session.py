"""Short-lived hand-off store for the full-tab resume review page (see review.html.jinja).

The extension can't pass this data to a plain webapp tab via the URL (jd_text can be tens of
KB - too big for a query string) or via chrome.storage (a regular tab has no access to that),
so it POSTs the session once to get a token, then opens /review/<token> in a new tab, which
fetches the same data back by that token.

In-memory only, unlike analyze_cache.py/tailor_cache.py - this is a same-process hand-off, not
meant to survive a server restart, and every value passed through it is already durably cached
elsewhere (analyze_cache.json, tailor_cache.json) if a token is lost."""
from __future__ import annotations

import secrets
import time

# Generous for a single-user local tool - old sessions (closed tabs, abandoned hand-offs) are
# evicted oldest-created-first once this is exceeded.
MAX_SESSIONS = 50

_sessions: dict[str, dict] = {}


def create(data: dict) -> str:
    token = secrets.token_urlsafe(16)
    _sessions[token] = {**data, "created_at": time.time()}
    if len(_sessions) > MAX_SESSIONS:
        oldest_first = sorted(_sessions.items(), key=lambda kv: kv[1]["created_at"])
        for stale_token, _ in oldest_first[: len(_sessions) - MAX_SESSIONS]:
            _sessions.pop(stale_token, None)
    return token


def get(token: str) -> dict | None:
    return _sessions.get(token)
