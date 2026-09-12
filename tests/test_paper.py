"""PaperPortfolio tests — reference math for fees, P&L, and stops."""
import pytest

from memebot.paper import PaperPortfolio, Position


def test_buy_charges_fee_and_records_tokens():
    pf = PaperPortfolio(1000.0, fee_pct=1.0)
    ok, why = pf.buy("P", "MEME", price=0.001, cost_usd=100.0,
                     stop_price=0.00085)
    assert ok
    pos = pf.positions["P"]
    # fee = 1% of 100 = 1; tokens = 99 USD worth at 0.001
    assert pos.entry_fee_usd == pytest.approx(1.0)
    assert pos.tokens == pytest.approx(99.0 / 0.001)
    assert pf.cash == pytest.approx(900.0)


def test_buy_rejects_overspend_and_invalid():
    pf = PaperPortfolio(100.0, fee_pct=1.0)
    assert pf.buy("P", "X", 1.0, cost_usd=200.0, stop_price=0.5)[0] is False
    assert pf.buy("P", "X", 0.0, cost_usd=10.0, stop_price=0.0)[0] is False


def test_sell_realizes_honest_pnl():
    pf = PaperPortfolio(1000.0, fee_pct=1.0)
    pf.buy("P", "MEME", 0.001, 100.0, 0.00085)
    ok, why, pnl = pf.sell("P", price=0.002)     # doubled
    assert ok
    # gross = 99000 tokens*0.002=198? tokens=99000? no: tokens=99/0.001=99000
    # gross = 99000*0.002=198; fee 1% -> 196.02; pnl = 196.02-100 = 96.02
    assert pnl == pytest.approx(96.02)
    assert pf.cash == pytest.approx(1000.0 - 100.0 + 196.02)
    assert "P" not in pf.positions


def test_sell_missing_position_fails_cleanly():
    pf = PaperPortfolio(1000.0, 1.0)
    ok, why, pnl = pf.sell("NOPE", 1.0)
    assert not ok and pnl == 0.0


def test_mark_to_market_and_unrealized():
    pf = PaperPortfolio(1000.0, fee_pct=1.0)
    pf.buy("P", "MEME", 0.001, 100.0, 0.00085)
    prices = {"P": 0.0005}                        # -50% move
    equity = pf.mark_to_market(prices)
    assert equity == pytest.approx(900.0 + 99_000 * 0.0005)
    assert pf.unrealized_pnl(prices) == pytest.approx(equity - 900.0 - 100.0)


def test_stops_hit():
    pf = PaperPortfolio(1000.0, 1.0)
    pf.buy("A", "X", 0.001, 100.0, stop_price=0.00085)
    pf.buy("B", "Y", 0.001, 100.0, stop_price=0.00085)
    prices = {"A": 0.0008, "B": 0.0012}
    assert pf.stops_hit(prices) == ["A"]


def test_invalid_starting_balance():
    with pytest.raises(ValueError):
        PaperPortfolio(0.0, 1.0)
