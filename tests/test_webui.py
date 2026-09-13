"""Live web dashboard tests.

Hand-verified expectations:
- build_status reports the paper portfolio honestly: entry-price
  fallback when Dexscreener is unavailable, stop-hit flags,
  live/entry price source labeled, disclaimer always present
- direct price fetches go through the TTL cache (no refetch inside 60s)
- market_snapshot caches scan results (one client.search per TTL)
- sample_tick appends REAL prices to history and caps its length
- paper_trade: buy requires a live price (refuses invented prices),
  buys/sell through the same PaperPortfolio code as the CLI,
  enforces cash limits, closes at live/entry price, persists state
- serve() refuses non-dry-run configs (paper-only by design)
- page ships the live terminal design + Lightweight Charts + controls
- HTTP: / and /api/status and the vendored chart JS serve;
  unknown paths 404; lan_ip() never raises
"""
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memebot.webui as webui
from memebot.config import load_config
from memebot.main import load_state, save_state
from memebot.paper import PaperPortfolio

ADDR = "0xabc"


class FakeClient:
    def __init__(self, prices=None, search_pairs=None):
        self.prices = prices or {}
        self._search = search_pairs or []
        self.fetch_calls = 0
        self.search_calls = 0

    def pair_prices(self, chain, addrs):
        self.fetch_calls += 1
        return {a: self.prices[a] for a in addrs if a in self.prices}

    def search(self, query):
        self.search_calls += 1
        return self._search


class _StateFixture:
    """Isolate memebot_state.json and price history around a test."""
    def __enter__(self):
        for path in (webui.STATE_PATH, webui.HISTORY_PATH):
            bak = path + ".bak"
            self._had = os.path.exists(path)
            if self._had:
                os.rename(path, bak)
        return self

    def __exit__(self, *exc):
        for path in (webui.STATE_PATH, webui.HISTORY_PATH):
            if os.path.exists(path):
                os.remove(path)
            bak = path + ".bak"
            if os.path.exists(bak):
                os.rename(bak, path)


def _write_state(position=True):
    p = PaperPortfolio(1000.0, 1.0)
    if position:
        entry = 0.0001
        ok, _ = p.buy(ADDR, "TEST", price=entry, cost_usd=100.0,
                      stop_price=entry * 0.9, chain_id="solana")
        assert ok
    save_state(p)


class BuildStatusTests(unittest.TestCase):
    def setUp(self):
        _StateFixture.__enter__(self)
        webui._price_cache.clear()
        _write_state()
        self.cfg = load_config("config/config.example.yaml")

    def tearDown(self):
        _StateFixture.__exit__(self)

    def test_offline_marks_at_entry_and_says_so(self):
        s = webui.build_status(self.cfg, client=FakeClient(prices={}))
        self.assertEqual(s["positions_open"], 1)
        pos = s["positions"][0]
        self.assertFalse(pos["priced_live"])
        self.assertEqual(pos["current_price"], pos["entry_price"])
        self.assertIn("Paper trading only", s["disclaimer"])

    def test_live_price_marks_change(self):
        entry = 0.0001
        s = webui.build_status(self.cfg,
                               client=FakeClient(prices={ADDR: entry * 2}))
        pos = s["positions"][0]
        self.assertTrue(pos["priced_live"])
        self.assertAlmostEqual(pos["change_pct"], 100.0, places=4)

    def test_price_cache_prevents_refetch(self):
        webui._price_cache.clear()
        client = FakeClient(prices={ADDR: 0.0001})
        webui.build_status(self.cfg, client=client)
        webui.build_status(self.cfg, client=client)
        self.assertEqual(client.fetch_calls, 1)


class MarketTests(unittest.TestCase):
    def setUp(self):
        _StateFixture.__enter__(self)
        self.cfg = load_config("config/config.example.yaml")

    def tearDown(self):
        _StateFixture.__exit__(self)

    def test_market_fallback_labels_filtered_rows(self):
        """When few pairs pass the bot's own filters, the board still
        shows top pairs — each honestly labeled with its verdict."""
        webui._market_cache = (0.0, [])
        # a real SOL-like pair: huge volume but old -> fails max_age
        pair = {"chainId": "solana", "dexId": "raydium",
                "pairAddress": "0xsol",
                "baseToken": {"symbol": "SOL"},
                "quoteToken": {"symbol": "USDC"},
                "priceUsd": "100.0", "liquidity": {"usd": 900000},
                "volume": {"h24": 2000000},
                "txns": {"h24": {"buys": 900, "sells": 900}},
                "pairCreatedAt": (time.time() - 500 * 3600) * 1000}
        rows = webui.market_snapshot(
            self.cfg, FakeClient(search_pairs=[pair]), "SOL/USDC")
        self.assertTrue(rows)
        row = rows[0]
        self.assertFalse(row["passes_filters"])
        self.assertIn("age", row["filter_reason"])
        self.assertEqual(row["symbol"], "SOL")

    def test_market_junk_symbols_skipped(self):
        webui._market_cache = (0.0, [])
        junk = {"chainId": "solana", "dexId": "x",
                "pairAddress": "0xjunk",
                "baseToken": {"symbol": "A" * 40},
                "quoteToken": {"symbol": "USDC"},
                "priceUsd": "1", "liquidity": {"usd": 10**7},
                "volume": {"h24": 10**7},
                "txns": {"h24": {"buys": 1, "sells": 1}},
                "pairCreatedAt": (time.time() - 3600) * 1000}
        rows = webui.market_snapshot(
            self.cfg, FakeClient(search_pairs=[junk]), "SOL/USDC")
        self.assertEqual(rows, [])  # junk never reaches the board

    def test_market_snapshot_caches(self):
        webui._market_cache = (0.0, [])
        client = FakeClient(search_pairs=[
            {"chainId": "solana", "dexId": "raydium",
             "pairAddress": "0xxyz",
             "baseToken": {"symbol": "WIF", "name": "dogwifhat"},
             "quoteToken": {"symbol": "USDC"},
             "priceUsd": "0.5", "liquidity": {"usd": 900000},
             "volume": {"h24": 500000},
             "txns": {"h24": {"buys": 600, "sells": 600}},
             "pairCreatedAt": (time.time() - 7200) * 1000}])
        webui.market_snapshot(self.cfg, client, "SOL/USDC")
        webui.market_snapshot(self.cfg, client, "SOL/USDC")
        self.assertEqual(client.search_calls, 1)

    def test_sample_tick_appends_and_caps(self):
        webui._market_cache = (0.0, [])
        _write_state()
        client = FakeClient(prices={ADDR: 0.0001})
        n = webui.sample_tick(self.cfg, client)
        self.assertGreaterEqual(n, 1)
        pts = webui.price_history(ADDR)
        self.assertEqual(len(pts), 1)
        self.assertAlmostEqual(pts[0]["value"], 0.0001)
        # cap enforcement: prefill history beyond HISTORY_CAP
        webui._history[ADDR] = [(int(time.time()) - i, 0.0001)
                                for i in range(webui.HISTORY_CAP + 50)]
        webui.sample_tick(self.cfg, client)
        self.assertLessEqual(len(webui._history[ADDR]), webui.HISTORY_CAP)


class PaperTradeTests(unittest.TestCase):
    def setUp(self):
        _StateFixture.__enter__(self)
        webui._price_cache.clear()
        webui._market_cache = (0.0, [])
        _write_state(position=False)  # start flat
        self.cfg = load_config("config/config.example.yaml")

    def tearDown(self):
        _StateFixture.__exit__(self)

    def test_buy_requires_live_price(self):
        code, msg = webui.paper_trade(
            self.cfg, "buy", "0xdead", chain_id="solana",
            symbol="GHOST", cost_usd=50.0, client=FakeClient(prices={}))
        self.assertEqual(code, 503)
        self.assertIn("invented", msg["error"])

    def test_buy_over_cash_refused(self):
        code, msg = webui.paper_trade(
            self.cfg, "buy", ADDR, chain_id="solana", symbol="TEST",
            cost_usd=99999.0,
            client=FakeClient(prices={ADDR: 0.0001}))
        self.assertEqual(code, 400)

    def test_buy_then_close_roundtrip(self):
        client = FakeClient(prices={ADDR: 0.0001})
        code, msg = webui.paper_trade(
            self.cfg, "buy", ADDR, chain_id="solana", symbol="TEST",
            cost_usd=100.0, stop_pct=10.0, client=client)
        self.assertEqual(code, 200)
        p = PaperPortfolio(1000.0, 1.0)
        load_state(p)
        self.assertIn(ADDR, p.positions)
        self.assertLess(p.cash, 1000.0)  # cost deducted from cash
        self.assertAlmostEqual(p.cash, 900.0, places=6)
        code, msg = webui.paper_trade(
            self.cfg, "close", ADDR, symbol="TEST", client=client)
        self.assertEqual(code, 200)
        self.assertIn("PnL", msg["note"])
        p2 = PaperPortfolio(1000.0, 1.0)
        load_state(p2)
        self.assertNotIn(ADDR, p2.positions)

    def test_close_unknown_position_404(self):
        code, msg = webui.paper_trade(
            self.cfg, "close", "0xnope", client=FakeClient())
        self.assertEqual(code, 404)

    def test_invalid_action_400(self):
        code, _ = webui.paper_trade(self.cfg, "yolo", ADDR,
                                    client=FakeClient())
        self.assertEqual(code, 400)


class ServeGuardTests(unittest.TestCase):
    def test_refuses_non_dry_run(self):
        # a fully-human-gated live config still can't get web controls:
        # the dashboard is paper-only by design
        from memebot.config import ModeConfig, LiveConfirmation
        cfg = load_config("config/config.example.yaml")
        # the full human-edited live gates, as documented:
        cfg.mode = ModeConfig(
            dry_run=False,
            live_confirmation=LiveConfirmation(
                confirmed_live=True, risk_disclosure_accepted=True,
                risk_disclosure_accepted_at="2026-09-13T00:00:00Z"))
        cfg.mode.validate()  # human gates satisfied -> constructible
        with self.assertRaises(SystemExit):
            webui.serve(cfg, "127.0.0.1", 0)


class PageDesignTests(unittest.TestCase):
    def test_design_tokens(self):
        for token in ("--bg:#04070a", "--green:#00e68a", "--red:#ff4d5e",
                      "--blue:#4da3ff"):
            self.assertIn(token, webui.PAGE)
        self.assertIn("@keyframes pulse", webui.PAGE)
        self.assertIn("setInterval(refresh, 5000)", webui.PAGE)

    def test_live_market_and_charts(self):
        self.assertIn("lightweight-charts.standalone.production.js",
                      webui.PAGE)
        self.assertIn("addAreaSeries", webui.PAGE)
        self.assertIn("/api/market", webui.PAGE)
        self.assertIn("paper_trade", webui.PAGE)

    def test_paper_only_boundary_stated(self):
        self.assertIn("paper-only", webui.PAGE)
        self.assertIn("PAPER", webui.build_status(
            load_config("config/config.example.yaml"),
            client=FakeClient())["mode"])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = _StateFixture()
        cls.fixture.__enter__()
        webui._price_cache.clear()
        webui._market_cache = (0.0, [])
        _write_state()
        cls.server = webui.ThreadingHTTPServer(("127.0.0.1", 8892),
                                               webui._Handler)
        webui._Handler.cfg = load_config("config/config.example.yaml")
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.fixture.__exit__()

    def test_index_served(self):
        html = urllib.request.urlopen(
            "http://127.0.0.1:8892/").read().decode()
        self.assertIn("MEME", html)

    def test_chart_library_served(self):
        js = urllib.request.urlopen(
            "http://127.0.0.1:8892/static/"
            "lightweight-charts.standalone.production.js").read()
        self.assertGreater(len(js), 100000)  # real library, not a stub
        self.assertIn(b"TradingView", js)

    def test_status_json(self):
        data = json.load(urllib.request.urlopen(
            "http://127.0.0.1:8892/api/status"))
        self.assertIn("disclaimer", data)
        self.assertEqual(data["mode"], "PAPER (dry-run)")

    def test_404_for_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen("http://127.0.0.1:8892/api/live")
        self.assertEqual(ctx.exception.code, 404)


class LanIpTests(unittest.TestCase):
    def test_never_raises(self):
        for _ in range(3):
            self.assertTrue(len(webui.lan_ip()) > 0)


if __name__ == "__main__":
    unittest.main()
