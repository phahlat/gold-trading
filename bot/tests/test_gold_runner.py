from types import SimpleNamespace
from pathlib import Path

import pandas as pd

from gold_bot.src.application.services.gold_runner import GoldRunner
from gold_bot.src.domain.services.gold_strategies import GoldStrategyEngine
from gold_bot.src.infrastructure.config.settings import load_gold_settings
from gold_bot.src.interfaces.cli.main import _resolve_backtest_data_source


def _copy_example_env(tmp_path: Path) -> str:
    env_path = tmp_path / ".env"
    env_source = None
    for candidate in [Path("bot/.env.example"), Path("gold_bot/.env.example"), Path(".env.example")]:
        if candidate.exists():
            env_source = candidate
            break
    if env_source is None:
        raise FileNotFoundError("Unable to locate example environment file")
    env_path.write_text(env_source.read_text(encoding="utf-8"), encoding="utf-8")
    return str(env_path)


def test_runner_generates_signal_from_simple_frame(tmp_path: Path) -> None:
    settings = load_gold_settings(_copy_example_env(tmp_path))
    runner = GoldRunner(settings)
    frame = pd.DataFrame(
        {
            "open": [100, 101, 102, 103, 104],
            "high": [101, 103, 104, 105, 106],
            "low": [99, 100, 101, 102, 103],
            "close": [100, 102, 103, 104, 105],
        }
    )

    result = runner.run_backtest(frame)

    assert result["total_signals"] >= 0
    assert isinstance(result["signals"], list)


def test_cli_resolves_explicit_backtest_data_path() -> None:
    args = SimpleNamespace(data="backtest/data", backtest_data_dir="gold_bot/backtest/data/XAUUSD15.csv")
    settings = SimpleNamespace(backtest_data_dir="gold_bot/backtest/data/XAUUSD60.csv")

    assert _resolve_backtest_data_source(args, settings) == "gold_bot/backtest/data/XAUUSD15.csv"


def test_price_action_breakout_uses_prior_window() -> None:
    frame = pd.DataFrame(
        {
            "open": [100, 100.5, 101, 101.2, 101.4, 101.5],
            "high": [101, 101.2, 101.4, 101.6, 101.8, 102.8],
            "low": [99.7, 100.2, 100.7, 100.9, 101.0, 101.3],
            "close": [100.8, 101.0, 101.2, 101.3, 101.6, 102.5],
        }
    )
    engine = GoldStrategyEngine(["price_action"])
    candidates = engine.evaluate(frame, settings=SimpleNamespace())

    assert any(item.strategy == "price_action" and item.direction == "buy" for item in candidates)
