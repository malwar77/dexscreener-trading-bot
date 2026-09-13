"""Token scanner — deterministic, fact-based filters over Dexscreener pairs.

Every filter is a documented number from the pair record (liquidity,
volume24h USD, pair age, transaction count). No sentiment, no "hype"
heuristics, no LLM judgment. Output is a labeled candidate list —
a candidate is NOT a trade recommendation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import ScannerConfig


@dataclass
class ScanResult:
    pair_address: str
    chain_id: str
    base_token_symbol: str
    quote_token_symbol: str
    price_usd: float
    liquidity_usd: float
    volume24h_usd: float
    pair_created_at: float          # unix ms
    txns24h: int
    dex: str
    url: str
    rejected_reason: str | None = None
    txns24h_buys: int = 0
    txns24h_sells: int = 0

    @property
    def age_hours(self) -> float:
        return max(0.0, (time.time() * 1000 - self.pair_created_at) / 3_600_000)


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_pair(pair: dict[str, Any]) -> ScanResult | None:
    """Map a raw Dexscreener pair record to a ScanResult, or None."""
    if not isinstance(pair, dict):
        return None
    try:
        base = (pair.get("baseToken") or {})
        quote = (pair.get("quoteToken") or {})
        _b = _num((pair.get("txns") or {}).get("h24", {}).get("buys", 0))
        _s = _num((pair.get("txns") or {}).get("h24", {}).get("sells", 0))
        txns24 = _b + _s
        return ScanResult(
            pair_address=pair["pairAddress"],
            chain_id=pair.get("chainId", ""),
            base_token_symbol=base.get("symbol", "?"),
            quote_token_symbol=quote.get("symbol", "?"),
            price_usd=_num(pair.get("priceUsd")),
            liquidity_usd=_num((pair.get("liquidity") or {}).get("usd")),
            volume24h_usd=_num((pair.get("volume") or {}).get("h24")),
            pair_created_at=_num(pair.get("pairCreatedAt")),
            txns24h=int(txns24),
            txns24h_buys=int(_b),
            txns24h_sells=int(_s),
            dex=pair.get("dexId", "?"),
            url=pair.get("url", ""),
        )
    except (KeyError, TypeError):
        return None


def passes_filters(result: ScanResult,
                   cfg: ScannerConfig) -> tuple[bool, str | None]:
    """Deterministic filter gate. Returns (ok, reason_if_rejected)."""
    if result.liquidity_usd < cfg.min_liquidity_usd:
        return False, f"liquidity {result.liquidity_usd:.0f} < {cfg.min_liquidity_usd:.0f} USD"
    if result.volume24h_usd < cfg.min_volume24h_usd:
        return False, (f"volume24h {result.volume24h_usd:.0f} < "
                       f"{cfg.min_volume24h_usd:.0f} USD")
    if result.age_hours > cfg.max_age_hours:
        return False, f"age {result.age_hours:.1f}h > {cfg.max_age_hours:.0f}h"
    if result.txns24h < cfg.min_txns24h:
        return False, f"txns24h {result.txns24h} < {cfg.min_txns24h}"
    return True, None


def risk_flags(result: ScanResult) -> list[str]:
    """Honest rug-risk HEURISTICS from public Dexscreener data only.

    These are cheap sanity flags, not protection: honeypots, mint
    functions and hidden ownership CANNOT be detected from market data
    (that requires contract analysis, which this bot does not do).
    A pair with zero flags can still be a rug. Heuristic, advisory,
    never a green light.
    """
    flags: list[str] = []
    liq = result.liquidity_usd
    if liq < 10_000:
        flags.append(f"thin-liquidity ({liq:.0f} USD)")
    if result.volume24h_usd > 3 * max(liq, 1):
        flags.append("volume > 3x liquidity (churn/wash pattern)")
    b = max(result.txns24h_buys, 0) or 1
    s = max(result.txns24h_sells, 0)
    if s == 0 and b >= 20:
        flags.append("sells = 0 with active buys (exit-liquidity risk)")
    elif b / max(s, 1) > 10:
        flags.append("buys/sells > 10 (one-way flow, suspicious)")
    if result.age_hours < 6:
        flags.append(f"very fresh ({result.age_hours:.1f}h — most rugs "
                     "happen in the first hours)")
    return flags


def scan_pairs(pairs: list[dict[str, Any]],
               cfg: ScannerConfig) -> list[ScanResult]:
    """Filter+rank a list of raw pair records. Deterministic: survivors
    are sorted by volume24h descending, then by address for stability."""
    results = [r for r in (parse_pair(p) for p in pairs) if r]
    survivors = []
    for r in results:
        ok, reason = passes_filters(r, cfg)
        if ok:
            survivors.append(r)
        else:
            r.rejected_reason = reason
    survivors.sort(key=lambda r: (-r.volume24h_usd, r.pair_address))
    return survivors
