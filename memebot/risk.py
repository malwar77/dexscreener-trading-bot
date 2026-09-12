"""RiskManager — THE veto authority for memecoin positions.

Mirrors the RegimeDesk/AetherBot contract: risk lives in code, never in
prompts. Every paper (and any future live) buy must pass check_entry().
Memecoin-specific note: rug pulls and liquidity pulls can gap straight
through any stop-loss — a stop is a mitigation, not a guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import RiskLimits


@dataclass
class EntryRequest:
    pair_address: str
    price_usd: float
    position_cost_usd: float
    account_balance_usd: float
    open_positions: int
    day_pnl_usd: float


class RiskManager:
    """Deterministic gate with final veto over all entries."""

    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def position_cap_usd(self, balance: float) -> float:
        """Max USD per position (max_position_pct of balance)."""
        return balance * self.limits.max_position_pct / 100.0

    def daily_loss_reached(self, balance: float, day_pnl: float) -> bool:
        """True if today's loss exceeds the daily limit — buys halted."""
        return day_pnl <= -(balance * self.limits.daily_loss_limit_pct / 100.0)

    def position_size(self, balance: float, risk_pct: float,
                      stop_distance_pct: float) -> float:
        """Fixed-risk sizing: risk_pct% of balance / stop distance.
        Pure calculator — the limits still veto the final number."""
        if stop_distance_pct <= 0:
            raise ValueError("stop_distance_pct must be > 0")
        risk_usd = balance * risk_pct / 100.0
        return risk_usd / (stop_distance_pct / 100.0)

    def check_entry(self, req: EntryRequest) -> tuple[bool, list[str]]:
        """Veto gate. Returns (allowed, reasons). ALL conditions must pass."""
        reasons: list[str] = []
        if req.open_positions >= self.limits.max_open_positions:
            reasons.append(f"max_open_positions {self.limits.max_open_positions} reached")
        if req.position_cost_usd > self.position_cap_usd(req.account_balance_usd):
            reasons.append(
                f"position {req.position_cost_usd:.2f} USD exceeds cap "
                f"{self.position_cap_usd(req.account_balance_usd):.2f} USD")
        if self.daily_loss_reached(req.account_balance_usd, req.day_pnl_usd):
            reasons.append("daily loss limit reached — buying halted")
        if req.price_usd <= 0:
            reasons.append("invalid price")
        return (not reasons, reasons)

    def stop_price(self, entry: float) -> float:
        """Paper stop level: entry * (1 - stop_loss_pct/100)."""
        return entry * (1 - self.limits.stop_loss_pct / 100.0)
