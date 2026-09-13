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

   `status` also re-fetches live prices for every held position via
   Dexscreener and shows each position's current P&L, distance to its
   stop, and a `[STOP HIT]` flag (executed on the next run — status is
   read-only).
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
python -m memebot.main web                        # LAN dashboard (read-only)
```

## Web dashboard on your LAN

`python -m memebot.main web` serves a terminal-style dashboard —
black background, green for profit, red for loss, blue for
information, live status dot, 5-second auto-refresh — from a
stdlib-only server (no extra dependencies):

```
this machine:  http://127.0.0.1:8788
on your LAN:   http://192.168.x.x:8788    <- open from your phone
```

Live prices come from Dexscreener's keyless public API with a
60-second server-side cache, so browser polling stays polite. When
prices are unavailable, positions are marked at entry price and the
dashboard says so — never invented numbers.

It is READ-ONLY by design: no buy/sell buttons exist on the web
surface. Anyone on your Wi-Fi can view it; nobody can trade from
it. To bind to this machine only, pass `--host 127.0.0.1`.

Numbers are simulated — memecoins can and do go to zero. This
skeleton has no live execution adapter by design.```

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

## Morning WhatsApp report (status beacon)

The agent (Base44 Superagent) sends a morning WhatsApp report with
mode, equity, total PnL, open positions and a watchdog alert if this
bot stops checking in. This bot feeds it via the agent's external API
(exact curl examples are in the agent editor's Developer / API Docs
panel):

1. Set env vars (or pass flags): `AGENT_API_BASE` (the agent's API
   root, e.g. https://<host>/api/agents/<agent_id>) and
   `AGENT_API_KEY` (from the editor's Developer panel).
2. `python -m memebot.main report` — prints the snapshot; with
   AGENT_API_BASE/AGENT_API_KEY set it also sends a STATUS BEACON
   message to the agent, which stores it. Offline-safe: if
   DexScreener is unreachable, positions are marked at entry price
   and the snapshot says so.
3. Schedule it before the agent's 7:30am ET run, e.g. cron at 07:15
   America/New_York:
   `15 7 * * * cd /path/to/dexscreener-trading-bot && python -m memebot.main report`

The snapshot is honest about the skeleton's limits: the RiskManager
gates entries only (no daily loss limit yet), and the PnL reported is
total-since-inception, not daily. Strictly read-only and advisory —
nothing here places, approves or alters a trade.

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
