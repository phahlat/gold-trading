from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from gold_bot.src.application.services.gold_backtest_service import GoldBacktestService


class _NoopRunner:
    pass


class _NoopChart:
    def close(self) -> None:
        return None


def _service(settings: SimpleNamespace) -> GoldBacktestService:
    return GoldBacktestService(settings=settings, runner=_NoopRunner(), chart_renderer=_NoopChart())


def test_backtest_volume_prefers_backtest_fixed_volume() -> None:
    settings = SimpleNamespace(backtest_fixed_volume=0.03, fixed_lot_size=0.07, risk_percent=1.0, stop_loss_pips=120.0)
    service = _service(settings)

    volume, rule = service._resolve_backtest_volume(1000.0)

    assert volume == 0.03
    assert rule == "backtest_fixed_volume"


def test_backtest_volume_uses_fixed_lot_when_backtest_fixed_not_set() -> None:
    settings = SimpleNamespace(backtest_fixed_volume=0.0, fixed_lot_size=0.07, risk_percent=1.0, stop_loss_pips=120.0)
    service = _service(settings)

    volume, rule = service._resolve_backtest_volume(1000.0)

    assert volume == 0.07
    assert rule == "gold_fixed_lot_size"


def test_backtest_volume_falls_back_to_risk_percent() -> None:
    settings = SimpleNamespace(backtest_fixed_volume=0.0, fixed_lot_size=0.0, risk_percent=1.0, stop_loss_pips=100.0)
    service = _service(settings)

    volume, rule = service._resolve_backtest_volume(1000.0)

    assert round(volume, 2) == 0.1
    assert rule == "risk_percent"


def test_backtest_volume_normalization_caps_to_profile_max() -> None:
    settings = SimpleNamespace(backtest_max_volume_cap=0.0)
    service = _service(settings)

    normalized, was_capped = service._normalize_backtest_volume(
        raw_volume=12.73,
        profile={"volume_min": 0.01, "volume_max": 2.0, "volume_step": 0.01},
    )

    assert normalized == 2.0
    assert was_capped is True


def test_backtest_margin_rejection_triggers_when_required_margin_exceeds_equity() -> None:
    settings = SimpleNamespace(backtest_simulate_margin_rejection=True, backtest_margin_available_ratio=0.95)
    service = _service(settings)

    should_reject = service._should_reject_for_margin(
        entry_price=3000.0,
        volume=1.0,
        equity=1000.0,
        profile={"contract_size": 100.0, "leverage": 100.0, "margin_initial": 0.0},
    )

    assert should_reject is True


def test_backtest_outcome_scales_with_volume() -> None:
    settings = SimpleNamespace(pip_size=0.01, stop_loss_pips=120.0, take_profit_pips=250.0)
    service = _service(settings)
    frame = pd.DataFrame(
        {
            "close": [2000.00, 2001.00],
        }
    )

    pnl = service._estimate_trade_outcome(
        lower_frame=frame,
        idx=0,
        direction="buy",
        entry_price=2000.00,
        volume=0.10,
    )

    # 1.00 move at pip_size 0.01 -> 100 pips, multiplied by 0.10 lots.
    assert pnl == 10.0
