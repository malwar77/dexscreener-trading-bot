"""Paper portfolio — simulated fills, real math, honest P&L.

This is the hard-default execution venue. Fees and slippage are
simulated conservatively (paper_fee_pct, default 1% round trip on
entry). Numbers shown are real simulated numbers, including losses —
no win-rate inflation, no hidden rounding up.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Position:
    pair_address: str
    token_symbol: str
    entry_price: float
    tokens: float
    cost_usd: float                 # including fee
    entry_fee_usd: float
    stop_price: float
    chain_id: str | None = None      # None in old state files: marked at entry
    opened_at: float = field(default_factory=time.time)


class PaperPortfolio:
    def __init__(self, starting_balance: float, fee_pct: float):
        if starting_balance <= 0:
            raise ValueError("starting_balance must be > 0")
        self.starting_balance = starting_balance
        self.cash = starting_balance
        self.fee_pct = fee_pct
        self.positions: dict[str, Position] = {}

    def buy(self, pair_address: str, token_symbol: str, price: float,
            cost_usd: float, stop_price: float,
            chain_id: str | None = None) -> tuple[bool, str]:
        """Simulate a market buy. Returns (ok, reason)."""
        if cost_usd > self.cash:
            return False, f"insufficient cash {self.cash:.2f} < {cost_usd:.2f}"
        if price <= 0 or cost_usd <= 0:
            return False, "invalid price or size"
        fee = cost_usd * self.fee_pct / 100.0
        self.cash -= cost_usd
        self.positions[pair_address] = Position(
            pair_address=pair_address,
            token_symbol=token_symbol,
            entry_price=price,
            tokens=(cost_usd - fee) / price,
            cost_usd=cost_usd,
            entry_fee_usd=fee,
            stop_price=stop_price,
            chain_id=chain_id,
        )
        return True, "filled"

    def sell(self, pair_address: str, price: float) -> tuple[bool, str, float]:
        """Simulate a market sell. Returns (ok, reason, realized_pnl)."""
        pos = self.positions.pop(pair_address, None)
        if pos is None:
            return False, "no position", 0.0
        gross = pos.tokens * price
        fee = gross * self.fee_pct / 100.0
        net = gross - fee
        self.cash += net
        return True, "filled", net - pos.cost_usd

    def mark_to_market(self, prices: dict[str, float]) -> float:
        """Total equity at the given prices (cash + positions at market)."""
        equity = self.cash
        for addr, pos in self.positions.items():
            equity += pos.tokens * prices.get(addr, pos.entry_price)
        return equity

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        return (self.mark_to_market(prices)
                - self.cash
                - sum(p.cost_usd for p in self.positions.values()))

    def stops_hit(self, prices: dict[str, float]) -> list[str]:
        """Position addresses whose stop is hit at the given prices."""
        return [a for a, p in self.positions.items()
                if prices.get(a, p.entry_price) <= p.stop_price]
