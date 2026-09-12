# Memecoin Trading Bot (Dexscreener) — paper-mode skeleton

[![tests](https://github.com/malwar77/dexscreener-trading-bot/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/malwar77/dexscreener-trading-bot/actions/workflows/tests.yml)

A safety-first skeleton for scanning and paper-trading memecoins using
Dexscreener's keyless public API. Built on the same contract as
[AetherBot](https://github.com/malwar77/aetherbot) and
[RegimeDesk](https://github.com/malwar77/regimedesk): risk in code,
never in prompts; paper mode is the hard default; no invented endpoints.

> ## ⚠️ READ FIRST — MEMECOINS ARE THE HIGHEST-RISK CORNER OF CRYPTO
>
> - **Total loss of capital is a common, normal outcome.** Most memecoins
>   go to zero. Rug pulls, liquidity pulls, and honeypots are routine.
> - **A stop-loss is a mitigation, NOT a guarantee.** Liquidity can
>   vanish between blocks; your stop can fill far worse than the level.
> - **This skeleton has NO live execution path by design.** The CLI
>   refuses to trade outside paper mode. It scans, filters, and
>   paper-simulates. Nothing more.
> - **Nothing here is financial advice.** Candidates printed by the
>   scanner are filtered facts, not trade recommendations.
> - Bots trading on behalf of other people may require licenses in your
>   jurisdiction. Verify independently.

## What it does

1. `scan` — pulls pairs from Dexscreener's public search API and applies
   deterministic, fact-based filters: minimum liquidity, minimum 24h
   volume, maximum pair age, minimum 24h transaction count. Survivors
   are ranked by 24h volume, descending. No sentiment, no hype heuristics.
2. `run` — loads the paper portfolio, respects paper stops on existing
   positions, then risk-gates and paper-buys top candidates. Every entry
   must pass the RiskManager veto (see below).
3. `status` — honest numbers: cash, marked equity, open positions, and
   total P&L **including losses**, with the paper-mode reminder printed.

## Safety architecture (in code, not prompts)

- **Hard default: paper mode.** The config loader refuses anything else.
- **Live gates exist but live execution does not.** The config schema
  supports the AetherBot/RegimeDesk triple gate (`dry_run: false` +
  `confirmed_live: true` + `risk_disclosure_accepted: true` with
  timestamp, set only by manually editing the YAML). If you set them,
  `main.py` still refuses: this skeleton deliberately contains no live
  execution adapter. The gates are there so a future adapter cannot
  be turned on implicitly.
- **RiskManager veto on every paper entry:** max open positions, per-
  position cap (% of balance), daily loss halt, invalid-price rejection.
  Deterministic, unit-tested against hand-calculated reference values.
- **No API keys anywhere.** Dexscreener's API is public and keyless.
- **No invented endpoints.** GMGN and PumpFun have no official public
  trading APIs — this project does NOT fabricate them. Integrating real
  execution would require your own verified credentials and their
  platform terms.

## Usage

```bash
pip install -r requirements.txt   # pytest + PyYAML only

python -m memebot.main scan --query "SOL/USDC"
python -m memebot.main run  --query "SOL/USDC"     # paper only
python -m memebot.main status
```

Tune filters and risk limits in `config/config.example.yaml`:

```yaml
mode:
  dry_run: true            # hard default; leave it
scanner:
  min_liquidity_usd: 25000
  min_volume24h_usd: 100000
  max_age_hours: 72
  min_txns24h: 100
risk:
  max_open_positions: 3
  max_position_pct: 5.0
  stop_loss_pct: 15.0
  daily_loss_limit_pct: 10.0
starting_balance_usd: 1000.0
paper_fee_pct: 1.0         # simulated DEX fees + slippage per side
```

## Tests

```bash
python -m pytest tests/ -q
```

Covered: the live-mode gate (disclosure `false` blocks even with
`mode: live`), scanner reference cases for every filter dimension,
risk math against hand-calculated values, paper P&L round trips,
and the Dexscreener client (fake transport: parsing, 429 retries,
rate limiting).

## License

MIT — see LICENSE. Educational software, provided as-is, no warranty.
