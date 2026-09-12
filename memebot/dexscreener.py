"""Dexscreener public API client — keyless, read-only market data.

Uses ONLY Dexscreener's documented public endpoints:
  GET /latest/dex/search?q={q}
  GET /latest/dex/tokens/{tokenAddress}
  GET /latest/dex/pairs/{chainId}/{pairAddress}
  GET /token-boosts/latest/v1

No authentication, no API keys. Be a good citizen: the docs ask for
<=300 requests/minute; this client rate-limits well below that and
retries with backoff on 429/5xx.

GMGN and PumpFun have NO official public trading APIs — this bot does
NOT invent endpoints for them. Execution integration, if ever added,
requires your own verified credentials and their platform terms.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

BASE_URL = "https://api.dexscreener.com"
REQUEST_INTERVAL = 0.25            # 4 req/s max — well under the 300/min limit
MAX_RETRIES = 4

Fetcher = Callable[[str], dict[str, Any]]


def default_fetch(url: str) -> dict[str, Any]:
    """HTTP GET returning parsed JSON (test seam: injectable)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "memebot-skeleton/0.1 (+paper-mode)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


class DexScreenerClient:
    """Rate-limited client over Dexscreener's documented public API."""

    def __init__(self, fetch: Fetcher = default_fetch,
                 base_url: str = BASE_URL,
                 interval: float = REQUEST_INTERVAL):
        self._fetch = fetch
        self._base = base_url.rstrip("/")
        self._interval = interval
        self._last_request = 0.0

    # -- internals ---------------------------------------------------------
    def _get(self, path: str) -> dict[str, Any] | list[Any]:
        url = f"{self._base}{path}"
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                return self._fetch(url)  # type: ignore[return-value]
            except urllib.error.HTTPError as err:
                last_error = err
                if err.code in (429, 500, 502, 503):
                    time.sleep(2 ** attempt)   # exponential backoff
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as err:
                last_error = err
                time.sleep(2 ** attempt)
                continue
        raise RuntimeError(f"dexscreener request failed: {last_error}")

    def _throttle(self) -> None:
        wait = self._interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    # -- documented endpoints ---------------------------------------------
    def search(self, query: str) -> list[dict[str, Any]]:
        data = self._get("/latest/dex/search?q="
                         + urllib.parse.quote(query))
        return list(data.get("pairs", []))

    def token_pairs(self, token_address: str) -> list[dict[str, Any]]:
        data = self._get(f"/latest/dex/tokens/{token_address}")
        return list(data.get("pairs", []))

    def pair(self, chain_id: str, pair_address: str) -> dict[str, Any] | None:
        data = self._get(f"/latest/dex/pairs/{chain_id}/{pair_address}")
        pairs = data.get("pairs") or []
        return pairs[0] if pairs else None

    def token_boosts(self) -> list[dict[str, Any]]:
        data = self._get("/token-boosts/latest/v1")
        return list(data if isinstance(data, list) else [])
