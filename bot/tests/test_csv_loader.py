from pathlib import Path

from gold_bot.src.infrastructure.market_data.csv_loader import load_backtest_ltf_htf_frames, resolve_backtest_timeframe_file


def test_resolve_backtest_timeframe_file_prefers_matching_symbol() -> None:
    data_dir = Path("gold_bot/backtest/data")
    ltf = resolve_backtest_timeframe_file(data_source=data_dir, symbol="XAUUSD", timeframe="M15")
    htf = resolve_backtest_timeframe_file(data_source=data_dir, symbol="XAUUSD", timeframe="H1")

    assert ltf.name == "XAUUSD15.csv"
    assert htf.name == "XAUUSD60.csv"


def test_load_backtest_ltf_htf_frames_supports_gold_alias() -> None:
    data_dir = Path("gold_bot/backtest/data")
    lower, higher, lower_path, higher_path = load_backtest_ltf_htf_frames(
        data_source=data_dir,
        symbol="GOLD",
        lower_timeframe="M15",
        higher_timeframe="H1",
    )

    assert lower_path.name == "XAUUSD15.csv"
    assert higher_path.name == "XAUUSD60.csv"
    assert not lower.empty
    assert not higher.empty
