"""Status beacon for the agent's morning report (read-only).

build_status() produces the snapshot (offline-safe: positions are
marked at live prices when the DexScreener client is available, at
entry price otherwise). send_beacon() posts it to the agent's external
API as a STATUS BEACON message. Strictly advisory: nothing here can
place, approve or alter a trade, and the snapshot is data only.
"""
from __future__ import annotations

import datetime
import json
import socket
import urllib.error
import urllib.request

BEACON_TIMEOUT_SECONDS = 120


def build_status(cfg, client=None):
    """Snapshot for the morning report. Offline-safe by design."""
    from .main import load_state
    from .paper import PaperPortfolio

    portfolio = PaperPortfolio(cfg.starting_balance_usd,
                               cfg.paper_fee_pct)
    load_state(portfolio)
    live = {}
    if client is not None and portfolio.positions:
        try:
            from .main import live_prices
            live = live_prices(portfolio, client)
        except Exception:  # noqa: BLE001 — beacon must never fail on data
            live = {}
    prices = {a: live.get(a, p.entry_price)
              for a, p in portfolio.positions.items()}
    equity = portfolio.mark_to_market(prices)
    pnl = equity - portfolio.starting_balance
    positions = [
        {"symbol": p.token_symbol,
         "entry_price": p.entry_price,
         "tokens": p.tokens,
         "cost_usd": p.cost_usd,
         "stop_price": p.stop_price}
        for p in portfolio.positions.values()
    ]
    return {
        "project": "memebot",
        "account": "paper",
        "mode": "dry_run" if cfg.mode.dry_run else "live",
        "generated_at":
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "balance": round(equity, 2),
        "daily_pnl": round(pnl, 2),
        "open_positions": positions,
        "kill_switch": {"daily_used_pct": None,
                        "weekly_used_pct": None,
                        "blocked": False},
        "live": False,
        "host": socket.gethostname(),
        "notes": ("paper skeleton: RiskManager gates entries only, no "
                  "daily loss limit yet; pnl is total since inception, "
                  "not daily; marked at %s prices"
                  % ("live" if live else "entry")),
    }


def _pick_conversation_id(convs):
    """Best-effort conversation picker. Handles a bare list, a wrapped
    dict ({conversations|data|items|results: [...]}) and a single
    conversation object. Prefers the default conversation."""
    items = None
    if isinstance(convs, list):
        items = convs
    elif isinstance(convs, dict):
        for key in ("conversations", "data", "items", "results"):
            if isinstance(convs.get(key), list):
                items = convs[key]
                break
        if items is None and isinstance(convs.get("id"), str):
            return convs["id"]
    if not items:
        return None
    for c in items:
        if isinstance(c, dict) and (c.get("is_default")
                                    or c.get("default")):
            return c.get("id")
    first = items[0]
    return first.get("id") if isinstance(first, dict) else None


def send_beacon(payload, api_base, api_key,
                timeout=BEACON_TIMEOUT_SECONDS):
    """Post the snapshot to the agent's external API as a STATUS BEACON
    message. api_base is the agent's API root, e.g.
    https://<host>/api/agents/<agent_id>. Returns (ok, detail)."""
    base = api_base.rstrip("/")
    req = urllib.request.Request(
        base + "/conversations", method="GET",
        headers={"api_key": api_key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            convs = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return (False, "conversation fetch failed: HTTP %s %s"
                % (e.code, e.read().decode("utf-8", "replace")[:200]))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, "conversation fetch failed: %s" % e
    conv_id = _pick_conversation_id(convs)
    if not conv_id:
        return False, ("no conversation found in API response: %s"
                       % json.dumps(convs)[:200])
    body = json.dumps({
        "message": "STATUS BEACON " + json.dumps(payload),
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/conversations/%s/messages" % conv_id, data=body,
        method="POST",
        headers={"api_key": api_key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.read().decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as e:
        return (False, "beacon POST failed: HTTP %s %s"
                % (e.code, e.read().decode("utf-8", "replace")[:200]))
    except (urllib.error.URLError, OSError) as e:
        return False, "beacon POST failed: %s" % e
