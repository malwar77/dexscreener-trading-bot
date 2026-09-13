"""Web UI tests — stdlib dashboard, design system, read-only stance.

Hand-verified expectations:
- build_status reports the paper portfolio honestly: entry-price
  fallback when Dexscreener is unavailable, stop-hit flags, disclaimer
  always present
- live prices go through the 60s TTL cache: a second call within the
  TTL must not re-fetch (verified with a counting fake client)
- the page ships the terminal design system (black bg, green/red/blue
  palette, pulse animation, 5s auto-refresh) and has no trade controls
- GET / and GET /api/status serve; unknown paths 404
- lan_ip() never raises
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


class FakeClient:
    def __init__(self, prices=None):
        self.prices = prices or {}
        self.fetch_calls = 0

    def pair_prices(self, chain, addrs):
        self.fetch_calls += 1
        return {a: self.prices[a] for a in addrs if a in self.prices}


def _portfolio_with_position():
    p = PaperPortfolio(1000.0, 0.01)
    entry = 0.0001
    ok, _ = p.buy("0xabc", "TEST", price=entry, cost_usd=100.0,
                  stop_price=entry * 0.9, chain_id="solana")
    assert ok
    return p


class BuildStatusTests(unittest.TestCase):
    STATE = "memebot_state.json"

    def setUp(self):
        # isolate: back up any existing state, write our own
        webui._price_cache.clear()
        self._had_state = os.path.exists(self.STATE)
        if self._had_state:
            os.rename(self.STATE, self.STATE + ".bak")
        p = _portfolio_with_position()
        save_state(p)  # round-trip through the real state format
        self.cfg = load_config("config/config.example.yaml")

    def tearDown(self):
        if os.path.exists(self.STATE):
            os.remove(self.STATE)
        if self._had_state:
            os.rename(self.STATE + ".bak", self.STATE)

    def test_offline_marks_at_entry_and_says_so(self):
        s = webui.build_status(self.cfg, client=FakeClient(prices={}))
        self.assertEqual(s["positions_open"], 1)
        pos = s["positions"][0]
        self.assertFalse(pos["priced_live"])
        self.assertEqual(pos["current_price"], pos["entry_price"])
        self.assertIn("simulated", s["disclaimer"])

    def test_live_price_marks_change(self):
        entry = 0.0001
        client = FakeClient(prices={"0xabc": entry * 2})
        s = webui.build_status(self.cfg, client=client)
        pos = s["positions"][0]
        self.assertTrue(pos["priced_live"])
        self.assertAlmostEqual(pos["change_pct"], 100.0, places=4)

    def test_price_cache_prevents_refetch(self):
        webui._price_cache.clear()
        client = FakeClient(prices={"0xabc": 0.0001})
        webui.build_status(self.cfg, client=client)
        webui.build_status(self.cfg, client=client)
        self.assertEqual(client.fetch_calls, 1)

    def test_empty_portfolio(self):
        os.remove(self.STATE)  # no state file at all
        s = webui.build_status(self.cfg, client=FakeClient())
        self.assertEqual(s["positions_open"], 0)
        self.assertEqual(s["equity"], self.cfg.starting_balance_usd)
        self.assertEqual(s["total_pnl"], 0)


class PageDesignTests(unittest.TestCase):
    def test_design_tokens(self):
        for token in ("--bg:#04070a", "--green:#00e68a", "--red:#ff4d5e",
                      "--blue:#4da3ff"):
            self.assertIn(token, webui.PAGE)
        self.assertIn("@keyframes pulse", webui.PAGE)
        self.assertIn("setInterval(refresh, 5000)", webui.PAGE)

    def test_read_only_stance(self):
        self.assertIn("read-only", webui.PAGE)
        self.assertIn("no buy/sell buttons", webui.PAGE)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def test_index_served(self):
        html = urllib.request.urlopen(
            "http://127.0.0.1:8892/").read().decode()
        self.assertIn("MEME", html)

    def test_status_json(self):
        data = json.load(urllib.request.urlopen(
            "http://127.0.0.1:8892/api/status"))
        self.assertIn("disclaimer", data)
        self.assertEqual(data["mode"], "PAPER (dry-run)")

    def test_404_for_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen("http://127.0.0.1:8892/api/buy")
        self.assertEqual(ctx.exception.code, 404)


class LanIpTests(unittest.TestCase):
    def test_never_raises(self):
        for _ in range(3):
            self.assertTrue(len(webui.lan_ip()) > 0)


if __name__ == "__main__":
    unittest.main()
