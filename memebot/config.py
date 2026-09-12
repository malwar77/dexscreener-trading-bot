"""Memecoin trading bot skeleton — configuration with the LIVE-MODE GATE.

Same contract as AetherBot / RegimeDesk:
- dry_run (paper) is the hard default.
- Live mode requires manually editing the YAML file with ALL of:
    mode.dry_run: false
    mode.live_confirmation.confirmed_live: true
    mode.live_confirmation.risk_disclosure_accepted: true
    mode.live_confirmation.risk_disclosure_accepted_at: "2026-09-12T12:00:00Z"
  No code path, CLI flag, or LLM may set these. The bot refuses to run
  live otherwise (TradingModeError).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


class TradingModeError(RuntimeError):
    """Raised when live mode is requested without explicit human gates."""


class ConfigError(ValueError):
    """Raised for structurally invalid configuration."""


@dataclass
class LiveConfirmation:
    confirmed_live: bool = False
    risk_disclosure_accepted: bool = False
    risk_disclosure_accepted_at: str | None = None


@dataclass
class ModeConfig:
    dry_run: bool = True
    live_confirmation: LiveConfirmation = field(default_factory=LiveConfirmation)

    def validate(self) -> None:
        if self.dry_run:
            return
        lc = self.live_confirmation
        if not (lc.confirmed_live and lc.risk_disclosure_accepted
                and lc.risk_disclosure_accepted_at):
            raise TradingModeError(
                "LIVE MODE REFUSED: mode.dry_run=false requires "
                "live_confirmation.confirmed_live=true, "
                "risk_disclosure_accepted=true and a "
                "risk_disclosure_accepted_at timestamp, set by manually "
                "editing the config file. Memecoins can go to zero; "
                "stay in paper mode until you accept that.")


@dataclass
class RiskLimits:
    max_open_positions: int = 3
    max_position_pct: float = 5.0        # % of balance per position
    stop_loss_pct: float = 15.0           # memecoins are volatile; wide stop
    daily_loss_limit_pct: float = 10.0    # halt paper/live buys for the day


@dataclass
class ScannerConfig:
    min_liquidity_usd: float = 25000.0
    min_volume24h_usd: float = 100000.0
    max_age_hours: float = 72.0
    min_txns24h: int = 100


@dataclass
class BotConfig:
    mode: ModeConfig = field(default_factory=ModeConfig)
    risk: RiskLimits = field(default_factory=RiskLimits)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    starting_balance_usd: float = 1000.0
    paper_fee_pct: float = 1.0            # DEX fees + slippage allowance


def load_config(path: str) -> BotConfig:
    """Load and validate a YAML config. Refuses invalid live setups."""
    with open(path) as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    mode_raw = raw.get("mode", {}) or {}
    lc_raw = mode_raw.get("live_confirmation", {}) or {}
    cfg = BotConfig(
        mode=ModeConfig(
            dry_run=bool(mode_raw.get("dry_run", True)),
            live_confirmation=LiveConfirmation(
                confirmed_live=bool(lc_raw.get("confirmed_live", False)),
                risk_disclosure_accepted=bool(
                    lc_raw.get("risk_disclosure_accepted", False)),
                risk_disclosure_accepted_at=lc_raw.get(
                    "risk_disclosure_accepted_at"),
            ),
        ),
        risk=RiskLimits(**(raw.get("risk", {}) or {})),
        scanner=ScannerConfig(**(raw.get("scanner", {}) or {})),
        starting_balance_usd=float(
            raw.get("starting_balance_usd", 1000.0)),
        paper_fee_pct=float(raw.get("paper_fee_pct", 1.0)),
    )
    if cfg.risk.max_open_positions < 1:
        raise ConfigError("risk.max_open_positions must be >= 1")
    if not 0 < cfg.risk.max_position_pct <= 100:
        raise ConfigError("risk.max_position_pct must be in (0, 100]")
    if cfg.risk.stop_loss_pct <= 0:
        raise ConfigError("risk.stop_loss_pct must be > 0")
    cfg.mode.validate()
    return cfg
