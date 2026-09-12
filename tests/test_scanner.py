"""Scanner tests — reference pair records with hand-known outcomes."""
import time

from memebot.config import ScannerConfig
from memebot.scanner import ScanResult, parse_pair, passes_filters, scan_pairs


def _pair(**over):
    now = time.time() * 1000
    base = {
        "chainId": "solana",
        "dexId": "rayfield",
        "pairAddress": "PAIR1",
        "baseToken": {"symbol": "MEME", "address": "M"},
        "quoteToken": {"symbol": "USDC", "address": "U"},
        "priceUsd": "0.00001",
        "liquidity": {"usd": "50000"},
        "volume": {"h24": "250000"},
        "pairCreatedAt": str(now - 3_600_000),   # 1h old
        "txns": {"h24": {"buys": 150, "sells": 100}},
        "url": "https://dexscreener.com/solana/PAIR1",
    }
    base.update(over)
    return base


CFG = ScannerConfig(min_liquidity_usd=25000, min_volume24h_usd=100000,
                    max_age_hours=72, min_txns24h=100)


def test_parse_pair_maps_fields():
    r = parse_pair(_pair())
    assert r is not None
    assert r.base_token_symbol == "MEME"
    assert r.liquidity_usd == 50000.0
    assert r.volume24h_usd == 250000.0
    assert r.txns24h == 250
    assert 0.9 < r.age_hours < 1.2


def test_parse_pair_survives_missing_optional_fields():
    r = parse_pair({"pairAddress": "X", "chainId": "solana"})
    assert r is not None and r.liquidity_usd == 0.0


def test_parse_pair_rejects_broken_records():
    assert parse_pair({"no": "address"}) is None
    assert parse_pair(None) is None


def test_filters_pass_reference_case():
    ok, reason = passes_filters(parse_pair(_pair()), CFG)
    assert ok and reason is None


def test_filters_reject_each_dimension():
    for over, frag in (
        ({"liquidity": {"usd": "10000"}}, "liquidity"),
        ({"volume": {"h24": "50000"}}, "volume"),
        ({"txns": {"h24": {"buys": 10, "sells": 5}}}, "txns"),
    ):
        ok, reason = passes_filters(parse_pair(_pair(**over)), CFG)
        assert not ok and frag in reason, (over, reason)


def test_filters_reject_old_pairs():
    now = time.time() * 1000
    r = parse_pair(_pair(pairCreatedAt=str(now - 200 * 3_600_000)))
    ok, reason = passes_filters(r, CFG)
    assert not ok and "age" in reason


def test_scan_pairs_sorts_by_volume_desc_and_is_deterministic():
    pairs = [_pair(pairAddress="A", volume={"h24": "300000"}),
             _pair(pairAddress="B", volume={"h24": "500000"}),
             _pair(pairAddress="C", volume={"h24": "10000"})]   # filtered out
    out = scan_pairs(pairs, CFG)
    assert [r.pair_address for r in out] == ["B", "A"]
    out2 = scan_pairs(pairs, CFG)
    assert [r.pair_address for r in out2] == ["B", "A"]
