"""RiskManager tests — hand-calculated reference values."""
import pytest

from memebot.config import RiskLimits
from memebot.risk import EntryRequest, RiskManager


LIMITS = RiskLimits(max_open_positions=3, max_position_pct=5.0,
                    stop_loss_pct=15.0, daily_loss_limit_pct=10.0)


def _rm():
    return RiskManager(LIMITS)


def test_position_cap():
    # 5% of 1000 = 50 USD
    assert _rm().position_cap_usd(1000.0) == pytest.approx(50.0)


def test_position_size_reference():
    # risk 2% of 1000 = 20 USD; stop distance 10% -> size 200 USD
    assert _rm().position_size(1000.0, 2.0, 10.0) == pytest.approx(200.0)
    # risk 1% of 500 = 5 USD; stop 25% -> 20 USD
    assert _rm().position_size(500.0, 1.0, 25.0) == pytest.approx(20.0)


def test_position_size_rejects_zero_stop():
    with pytest.raises(ValueError):
        _rm().position_size(1000.0, 2.0, 0.0)


def test_stop_price():
    # 15% stop below entry 100 -> 85
    assert _rm().stop_price(100.0) == pytest.approx(85.0)


def test_check_entry_allows_good_entry():
    ok, reasons = _rm().check_entry(EntryRequest(
        pair_address="P", price_usd=0.001, position_cost_usd=50.0,
        account_balance_usd=1000.0, open_positions=1, day_pnl_usd=0.0))
    assert ok and reasons == []


def test_check_entry_vetoes():
    rm = _rm()
    # too many positions
    ok, reasons = rm.check_entry(EntryRequest(
        "P", 0.001, 50.0, 1000.0, open_positions=3, day_pnl_usd=0.0))
    assert not ok and "max_open_positions" in " ".join(reasons)
    # oversized position
    ok, reasons = rm.check_entry(EntryRequest(
        "P", 0.001, 80.0, 1000.0, open_positions=0, day_pnl_usd=0.0))
    assert not ok and "exceeds cap" in " ".join(reasons)
    # daily loss halt: -100 on 1000 balance = -10%
    ok, reasons = rm.check_entry(EntryRequest(
        "P", 0.001, 50.0, 1000.0, open_positions=0, day_pnl_usd=-100.0))
    assert not ok and "daily loss" in " ".join(reasons)
    # invalid price
    ok, reasons = rm.check_entry(EntryRequest(
        "P", 0.0, 50.0, 1000.0, open_positions=0, day_pnl_usd=0.0))
    assert not ok and "invalid price" in " ".join(reasons)


def test_daily_loss_boundary():
    rm = _rm()
    # exactly at the limit -> halted (uses <=)
    assert rm.daily_loss_reached(1000.0, -100.0) is True
    assert rm.daily_loss_reached(1000.0, -99.0) is False
