"""Beacon tests: snapshot honesty + agent-API round trip.

Hand-verified expectations:
- snapshot is dry_run, project memebot, honest notes (no daily limit
  in the skeleton, pnl is total not daily)
- positions are marked at entry price when no client is given
- positions are marked at live price when the client provides one
- send_beacon fetches conversations with the api_key header, posts a
  STATUS BEACON message to the DEFAULT conversation, payload
  round-trips
- errors are reported as (False, detail), never raised
"""
import io
import json
import os
import socket
import sys
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memebot import beacon
from memebot.beacon import (_pick_conversation_id, build_status,
                            send_beacon)
from memebot.config import load_config


def _fake_client(prices):
    class C:
        def pair_prices(self, chain, addrs):
            return {a: prices[a] for a in addrs}
    return C()


def _write_state(tmp_path, state):
    (tmp_path / "memebot_state.json").write_text(json.dumps(state))


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class BuildStatusTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self._orig_cwd)
        import memebot
        cfg_path = (Path(memebot.__file__).resolve().parent.parent
                    / "config" / "config.example.yaml")
        self.cfg = load_config(str(cfg_path))

    def test_empty_portfolio_snapshot(self):
        status = build_status(self.cfg)
        self.assertEqual(status["project"], "memebot")
        self.assertEqual(status["mode"], "dry_run")
        self.assertEqual(status["balance"], 1000.0)
        self.assertEqual(status["daily_pnl"], 0.0)
        self.assertEqual(status["open_positions"], [])
        self.assertFalse(status["live"])
        # honesty: the skeleton's limits are stated in notes
        self.assertIn("no daily loss limit yet", status["notes"])
        self.assertIn("total since inception", status["notes"])

    def test_marks_at_entry_without_client(self):
        _write_state(self.tmp, {
            "starting_balance": 1000.0, "cash": 0.0,
            "positions": [{"pair_address": "a", "token_symbol": "TOK",
                           "entry_price": 2.0, "tokens": 100.0,
                           "cost_usd": 200.0, "entry_fee_usd": 2.0,
                           "stop_price": 1.8, "chain_id": "solana",
                           "opened_at": 1.0}]})
        status = build_status(self.cfg)
        self.assertEqual(len(status["open_positions"]), 1)
        self.assertEqual(status["open_positions"][0]["symbol"], "TOK")
        self.assertIn("marked at entry prices", status["notes"])

    def test_marks_at_live_with_client(self):
        _write_state(self.tmp, {
            "starting_balance": 1000.0, "cash": 0.0,
            "positions": [{"pair_address": "a", "token_symbol": "TOK",
                           "entry_price": 2.0, "tokens": 100.0,
                           "cost_usd": 200.0, "entry_fee_usd": 2.0,
                           "stop_price": 1.8, "chain_id": "solana",
                           "opened_at": 1.0}]})
        client = _fake_client({"a": 3.0})
        status = build_status(self.cfg, client=client)
        # 100 tokens at 3.0 = 300 equity
        self.assertEqual(status["balance"], 300.0)
        self.assertEqual(status["daily_pnl"], -700.0)
        self.assertIn("marked at live prices", status["notes"])


class PickConversationTests(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(_pick_conversation_id(
            [{"id": "a"}, {"id": "b"}]), "a")
        self.assertEqual(_pick_conversation_id(
            [{"id": "a"}, {"id": "b", "is_default": True}]), "b")
        self.assertEqual(_pick_conversation_id(
            {"conversations": [{"id": "c"}]}), "c")
        self.assertEqual(_pick_conversation_id({"id": "e"}), "e")
        self.assertIsNone(_pick_conversation_id({}))
        self.assertIsNone(_pick_conversation_id([]))


class SendBeaconTests(unittest.TestCase):
    def test_round_trip_default_conversation(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req.method, req.full_url, req.data,
                          req.get_header("Api_key")))
            if req.full_url.endswith("/conversations"):
                return FakeResponse(200, json.dumps(
                    [{"id": "conv-1", "is_default": True},
                     {"id": "conv-2"}]))
            return FakeResponse(200, '{"message": {"content": "stored"}}')

        with mock.patch.object(beacon.urllib.request, "urlopen",
                               fake_urlopen):
            ok, detail = send_beacon({"project": "memebot"},
                                     "https://h/api/agents/A1", "k1")
        self.assertTrue(ok)
        m1, u1, d1, k1 = calls[0]
        self.assertEqual(m1, "GET")
        self.assertEqual(u1, "https://h/api/agents/A1/conversations")
        self.assertEqual(k1, "k1")
        self.assertIsNone(d1)
        m2, u2, d2, k2 = calls[1]
        self.assertEqual(m2, "POST")
        self.assertEqual(u2,
                         "https://h/api/agents/A1/conversations/conv-1/messages")
        sent = json.loads(d2.decode())
        self.assertTrue(sent["message"].startswith("STATUS BEACON "))
        inner = json.loads(sent["message"][len("STATUS BEACON "):])
        self.assertEqual(inner["project"], "memebot")

    def test_conversation_fetch_failure_reported(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {},
                io.BytesIO(b'{"message": "bad key"}'))
        with mock.patch.object(beacon.urllib.request, "urlopen",
                               fake_urlopen):
            ok, detail = send_beacon({"x": 1}, "https://h/api/agents/A1",
                                     "bad")
        self.assertFalse(ok)
        self.assertIn("401", detail)

    def test_no_conversation_reported(self):
        with mock.patch.object(beacon.urllib.request, "urlopen",
                              lambda req, timeout=None:
                              FakeResponse(200, '{"unexpected": 1}')):
            ok, detail = send_beacon({"x": 1}, "https://h/api/agents/A1",
                                     "k")
        self.assertFalse(ok)
        self.assertIn("no conversation", detail)


if __name__ == "__main__":
    unittest.main()
