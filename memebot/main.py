"""Memecoin bot CLI — scan, paper-run, status.

    python -m memebot.main scan --config config/config.example.yaml --query "SOL"
    python -m memebot.main run --config config/config.example.yaml --once
    python -m memebot.main status --config config/config.example.yaml

`run` scans, applies risk gates, and PAPER-buys top candidates.
Nothing here ever places a real trade.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import (BotConfig, ConfigError, TradingModeError,
                      load_config)
from .dexscreener import DexScreenerClient
from .paper import PaperPortfolio
from .risk import EntryRequest, RiskManager
from .scanner import scan_pairs

STATE_PATH = Path("memebot_state.json")


def save_state(portfolio: PaperPortfolio) -> None:
    positions = [
        {
            "pair_address": p.pair_address,
            "token_symbol": p.token_symbol,
            "entry_price": p.entry_price,
            "tokens": p.tokens,
            "cost_usd": p.cost_usd,
            "entry_fee_usd": p.entry_fee_usd,
            "stop_price": p.stop_price,
            "opened_at": p.opened_at,
        }
        for p in portfolio.positions.values()
    ]
    STATE_PATH.write_text(json.dumps({
        "starting_balance": portfolio.starting_balance,
        "cash": portfolio.cash,
        "positions": positions,
    }, indent=2))


def load_state(portfolio: PaperPortfolio) -> None:
    if not STATE_PATH.exists():
        return
    from .paper import Position
    state = json.loads(STATE_PATH.read_text())
    portfolio.cash = float(state.get("cash", portfolio.starting_balance))
    for p in state.get("positions", []):
        portfolio.positions[p["pair_address"]] = Position(**p)


def cmd_scan(cfg: BotConfig, query: str) -> int:
    client = DexScreenerClient()
    pairs = client.search(query) if query else []
    survivors = scan_pairs(pairs, cfg.scanner)
    print(f"scanned {len(pairs)} pairs for '{query or '-'}'; "
          f"{len(survivors)} pass filters:")
    for r in survivors[:10]:
        print(f"  {r.base_token_symbol}/{r.quote_token_symbol} [{r.dex}] "
              f"price={r.price_usd:.8f} liq={r.liquidity_usd:.0f}USD "
              f"vol24h={r.volume24h_usd:.0f}USD txns={r.txns24h} "
              f"age={r.age_hours:.1f}h")
    print("candidates are facts, not trade recommendations")
    return 0


def cmd_run(cfg: BotConfig, query: str) -> int:
    client = DexScreenerClient()
    rm = RiskManager(cfg.risk)
    portfolio = PaperPortfolio(cfg.starting_balance_usd, cfg.paper_fee_pct)
    load_state(portfolio)
    prices = {}
    pairs = client.search(query) if query else []
    for p in pairs:
        base = (p.get("baseToken") or {}).get("symbol", "?")
        prices[p.get("pairAddress", "")] = float(p.get("priceUsd") or 0)

    # 1) respect stops on existing positions (paper)
    for addr in portfolio.stops_hit(prices):
        pos = portfolio.positions[addr]
        ok, _, pnl = portfolio.sell(addr, prices.get(addr, pos.entry_price))
        if ok:
            print(f"STOP HIT {pos.token_symbol}: realized pnl {pnl:+.2f} USD")

    # 2) scan + risk-gate NEW entries (paper only)
    day_pnl = portfolio.mark_to_market(prices) - portfolio.starting_balance
    survivors = scan_pairs(pairs, cfg.scanner)
    for r in survivors:
        if r.pair_address in portfolio.positions:
            continue
        balance = portfolio.mark_to_market(prices)
        cost = round(min(rm.position_cap_usd(balance), portfolio.cash), 2)
        req = EntryRequest(
            pair_address=r.pair_address,
            price_usd=r.price_usd,
            position_cost_usd=cost,
            account_balance_usd=portfolio.mark_to_market(prices),
            open_positions=len(portfolio.positions),
            day_pnl_usd=day_pnl,
        )
        ok, reasons = rm.check_entry(req)
        if not ok:
            print(f"VETO {r.base_token_symbol}: {'; '.join(reasons)}")
            continue
        stop = rm.stop_price(r.price_usd)
        filled, why = portfolio.buy(r.pair_address, r.base_token_symbol,
                                    r.price_usd, cost, stop)
        print(f"PAPER BUY {r.base_token_symbol} {cost:.2f}USD @ "
              f"{r.price_usd:.8f} stop={stop:.8f} ({why})")
        if len(portfolio.positions) >= cfg.risk.max_open_positions:
            break
    save_state(portfolio)
    return 0


def cmd_status(cfg: BotConfig) -> int:
    portfolio = PaperPortfolio(cfg.starting_balance_usd, cfg.paper_fee_pct)
    load_state(portfolio)
    equity = portfolio.mark_to_market(
        {a: p.entry_price for a, p in portfolio.positions.items()})
    pnl = equity - portfolio.starting_balance
    mode = "PAPER (dry-run)" if cfg.mode.dry_run else "LIVE"
    print(f"mode: {mode}")
    print(f"cash: {portfolio.cash:.2f} USD | equity(mark): {equity:.2f} USD")
    print(f"open positions: {len(portfolio.positions)}")
    print(f"total pnl (marked at entry): {pnl:+.2f} USD "
          f"({pnl / portfolio.starting_balance * 100:+.2f}%)")
    print("numbers are simulated — memecoins can and do go to zero")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memebot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    parser.add_argument("--config", default=None,
                        help=argparse.SUPPRESS)
    for name in ("scan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--query", default="SOL/USDC")
        p.add_argument("--config", default=None, help=argparse.SUPPRESS)
        p.add_argument("--once", action="store_true")
    p_status = sub.add_parser("status")
    p_status.add_argument("--config", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        cfg = load_config(getattr(args, "config", None)
                          or "config/config.example.yaml")
    except (TradingModeError, ConfigError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    if not cfg.mode.dry_run:
        print("WARNING: config requests LIVE mode; this skeleton has no "
              "live execution adapter — refusing to trade outside paper "
              "mode by design.", file=sys.stderr)
        return 1
    if args.cmd == "scan":
        return cmd_scan(cfg, args.query)
    if args.cmd == "run":
        return cmd_run(cfg, args.query)
    return cmd_status(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
