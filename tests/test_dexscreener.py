"""Dexscreener client tests — fake transport, no network."""
import pytest

from memebot.dexscreener import DexScreenerClient


def make_client(responses):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if isinstance(responses, Exception):
            raise responses
        return responses.get(url.split("dexscreener.com")[-1],
                             responses.get("*"))

    client = DexScreenerClient(fetch=fake_fetch, interval=0.0)
    return client, calls


def test_search_parses_pairs():
    client, calls = make_client(
        {"/latest/dex/search?q=MEME": {"pairs": [{"pairAddress": "P1"}]}})
    pairs = client.search("MEME")
    assert pairs == [{"pairAddress": "P1"}]
    assert "dexscreener.com/latest/dex/search?q=MEME" in calls[0]


def test_token_pairs_and_pair_and_boosts():
    client, _ = make_client({
        "/latest/dex/tokens/ADDR": {"pairs": [{"pairAddress": "P"}]},
        "/latest/dex/pairs/solana/PA": {"pairs": [{"pairAddress": "PA",
                                                  "priceUsd": "1"}]},
        "/token-boosts/latest/v1": [{"url": "x"}],
    })
    assert client.token_pairs("ADDR") == [{"pairAddress": "P"}]
    assert client.pair("solana", "PA")["priceUsd"] == "1"
    assert client.token_boosts() == [{"url": "x"}]


def test_pair_returns_none_for_missing():
    client, _ = make_client({"/latest/dex/pairs/solana/X": {"pairs": None}})
    assert client.pair("solana", "X") is None


def test_retries_on_429_then_succeeds(monkeypatch):
    import urllib.error
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(url, 429, "rate", {}, None)
        return {"pairs": []}

    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    client = DexScreenerClient(fetch=flaky, interval=0.0)
    assert client.search("q") == []
    assert calls["n"] == 3


def test_gives_up_after_max_retries(monkeypatch):
    import urllib.error
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)

    def always_429(url):
        raise urllib.error.HTTPError(url, 429, "rate", {}, None)

    client = DexScreenerClient(fetch=always_429, interval=0.0)
    with pytest.raises(RuntimeError):
        client.search("q")
