"""Tests: live status re-check — price fetching, P&L marking, stop flags.

Reference numbers are hand-computed. The DexscreenerClient fetch is
injected (fake), so no network is touched.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memebot.config import load_config
from memebot.dexscreener import DexScreenerClient
from memebot.main import cmd_status, live_prices, save_state
from memebot.paper import PaperPortfolio, Position


CFG = load_config("config/config.example.yaml")


def fake_fetch(payload_by_url: dict):
    """Fetcher stub keyed by URL suffix; unlisted URLs 404-style fail."""
    def fetch(url):
        for suffix, body in payload_by_url.items():
            if url.endswith(suffix):
                return body
        raise RuntimeError("not found: %s" % url)
    return fetch


def make_portfolio():
    pf = PaperPortfolio(1000.0, 1.0)
    ok, why = pf.buy("addrA", "DOGE", 0.00010, 100.0, 0.00007,
                    chain_id="solana")
    assert ok
    ok, why = pf.buy("addrB", "WIF", 0.00010, 100.0, 0.00012,
                    chain_id="solana")
    assert ok
    return pf


# ---------------------------------------------------------------------------
# DexScreenerClient.pair_prices — endpoint parsing
# ---------------------------------------------------------------------------
def test_pair_prices_parses_documented_payload():
    client = DexScreenerClient(fetch=fake_fetch({
        "/latest/dex/pairs/solana/addrA":
            {"pair": {"pairAddress": "addrA", "priceUsd": "0.5"}},
    }))
    out = client.pair_prices("solana", ["addrA"])
    assert out == {"addrA": 0.5}


def test_pair_prices_missing_pair_is_absent_not_fatal():
    client = DexScreenerClient(fetch=fake_fetch({
        "/latest/dex/pairs/solana/addrA":
            {"pair": {"pairAddress": "addrA", "priceUsd": "0.5"}},
        "/latest/dex/pairs/solana/addrB": {"pair": None},
    }))
    out = client.pair_prices("solana", ["addrA", "addrB"])
    assert out == {"addrA": 0.5}


def test_pair_prices_dead_pair_swallowed():
    client = DexScreenerClient(fetch=fake_fetch({}))   # everything 404s
    assert client.pair_prices("solana", ["addrX"]) == {}


# ---------------------------------------------------------------------------
# live_prices — grouping by chain, old-state handling
# ---------------------------------------------------------------------------
class StubClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def pair_prices(self, chain, addrs):
        self.calls.append((chain, list(addrs)))
        return {a: self.mapping.get(a) for a in addrs
                if a in self.mapping}


def test_live_prices_groups_by_chain():
    pf = make_portfolio()
    stub = StubClient({"addrA": 0.00008, "addrB": 0.00011})
    out = live_prices(pf, stub)
    assert out == {"addrA": 0.00008, "addrB": 0.00011}
    # both positions are solana -> ONE batched call
    assert stub.calls == [("solana", ["addrA", "addrB"])]


def test_live_prices_skips_positions_without_chain():
    pf = make_portfolio()
    pf.positions["addrA"].chain_id = None       # simulate an old state file
    stub = StubClient({"addrB": 0.00011})
    out = live_prices(pf, stub)
    assert out == {"addrB": 0.00011}
    assert stub.calls == [("solana", ["addrB"])]


# ---------------------------------------------------------------------------
# cmd_status — live marking, stop flags (hand-computed)
# ---------------------------------------------------------------------------
def run_status_with_state(pf, capsys, tmp_path, stub=None):
    import memebot.main as m
    old = m.STATE_PATH
    m.STATE_PATH = tmp_path / "state.json"
    try:
        save_state(pf)
        # positions were saved by save_state under the patched path
        fresh = PaperPortfolio(pf.starting_balance, pf.fee_pct)
        m.load_state(fresh)
        rc = cmd_status(CFG, client=stub or DexScreenerClient(
            fetch=fake_fetch({})))
        out = capsys.readouterr().out
        return rc, out
    finally:
        m.STATE_PATH = old


def test_status_live_marking_hand_computed(capsys, tmp_path):
    pf = make_portfolio()
    # hand reference: DOGE entry 0.00010 -> live 0.00008 = -20.00%;
    # stop 0.00007 -> live is (0.00008/0.00007-1) = +14.3% above stop
    # WIF entry 0.00010 -> live 0.00015 = +50.00%, stop 0.00012 ->
    # (0.00015/0.00012-1) = +25.0% above stop
    stub = StubClient({"addrA": 0.00008, "addrB": 0.00015})
    rc, out = run_status_with_state(pf, capsys, tmp_path, stub)
    assert rc == 0
    assert "marked at live prices" in out
    assert "DOGE" in out and "-20.00%" in out and "+14.3% away" in out
    assert "WIF" in out and "+50.00%" in out and "+25.0% away" in out
    # DOGE is below stop? no: 0.00008 > 0.00007 -> NOT hit
    assert "STOP HIT" not in out


def test_status_flags_stop_hit(capsys, tmp_path):
    pf = make_portfolio()
    # DOGE live 0.00005 <= stop 0.00007 -> STOP HIT, closes on next run
    stub = StubClient({"addrA": 0.00005})
    rc, out = run_status_with_state(pf, capsys, tmp_path, stub)
    assert "DOGE" in out and "STOP HIT" in out
    # WIF has no live price -> unavailable, marked at entry, NOT flagged
    assert "WIF" in out and "price unavailable" in out


def test_status_old_state_without_chain_marks_at_entry(capsys, tmp_path):
    pf = make_portfolio()
    for p in pf.positions.values():
        p.chain_id = None
    state = tmp_path / "state.json"
    import memebot.main as m
    old = m.STATE_PATH
    m.STATE_PATH = state
    try:
        save_state(pf)
        fresh = PaperPortfolio(1000.0, 1.0)
        m.load_state(fresh)
        # old-format state must round-trip without chain_id
        assert all(p.chain_id is None for p in fresh.positions.values())
    finally:
        m.STATE_PATH = old
    rc, out = run_status_with_state(pf, capsys, tmp_path)
    assert rc == 0
    assert "marked at entry prices" in out
    assert "price unavailable" in out


def test_status_equity_hand_computed(capsys, tmp_path):
    pf = make_portfolio()
    stub = StubClient({"addrA": 0.00008, "addrB": 0.00015})
    rc, out = run_status_with_state(pf, capsys, tmp_path, stub)
    # hand: cash = 1000 - 200 = 800; DOGE tokens = 99/0.00010 = 990000
    #   at 0.00008 -> 79.20 ; WIF tokens 99/0.00010 = 990000 at
    #   0.00015 -> 148.50 ; equity = 800 + 79.20 + 148.50 = 1027.70
    assert "1027.70 USD" in out
