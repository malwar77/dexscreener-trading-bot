"""Memecoin trading bot — tests for config gates and validation.

CRITICAL test: risk_disclosure_accepted=false must block live mode even
when mode.dry_run is false and confirmed_live is true (same contract as
RegimeDesk / AetherBot).
"""
import pytest

from memebot.config import (ConfigError, LiveConfirmation, ModeConfig,
                            TradingModeError, load_config)


def test_default_is_paper():
    m = ModeConfig()
    assert m.dry_run is True
    m.validate()  # paper mode must always validate


def test_live_mode_requires_all_three_gates():
    # mode: live but NO confirmation at all
    with pytest.raises(TradingModeError):
        ModeConfig(dry_run=False).validate()
    # confirmed_live but no risk disclosure
    with pytest.raises(TradingModeError):
        ModeConfig(dry_run=False, live_confirmation=LiveConfirmation(
            confirmed_live=True)).validate()
    # disclosure true but NO timestamp
    with pytest.raises(TradingModeError):
        ModeConfig(dry_run=False, live_confirmation=LiveConfirmation(
            confirmed_live=True, risk_disclosure_accepted=True)).validate()


def test_full_live_gate_passes_only_with_everything():
    m = ModeConfig(dry_run=False, live_confirmation=LiveConfirmation(
        confirmed_live=True, risk_disclosure_accepted=True,
        risk_disclosure_accepted_at="2026-09-12T12:00:00Z"))
    m.validate()  # explicit human opt-in accepted


def test_yaml_load_defaults_to_paper(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("mode:\n  dry_run: true\n")
    cfg = load_config(str(p))
    assert cfg.mode.dry_run is True
    assert cfg.risk.max_open_positions == 3


def test_yaml_live_refused_without_disclosure(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "mode:\n  dry_run: false\n"
        "  live_confirmation:\n"
        "    confirmed_live: true\n"
        "    risk_disclosure_accepted: false\n")
    with pytest.raises(TradingModeError):
        load_config(str(p))


def test_invalid_risk_limits_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("risk:\n  max_open_positions: 0\n")
    with pytest.raises(ConfigError):
        load_config(str(p))
