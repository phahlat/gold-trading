from __future__ import annotations

import argparse
from pathlib import Path

from bot.src.application.services.gold_runner import GoldRunner
from bot.src.infrastructure.config.settings import load_gold_settings
from bot.src.infrastructure.logging.runtime import GoldQueueLoggingManager
from bot.src.infrastructure.market_data.csv_loader import load_ohlc_frame


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the gold bot backtest")
    parser.add_argument("--env", default="gold_bot/.env")
    parser.add_argument("--data", default="backtest/data/XAUUSD15.csv")
    args = parser.parse_args()

    settings = load_gold_settings(args.env)
    GoldQueueLoggingManager.configure(Path("logs"), "info")
    runner = GoldRunner(settings)
    frame = load_ohlc_frame(args.data)
    result = runner.run_backtest(frame)
    print(f"total_signals={result['total_signals']}")
    if result["signals"]:
        print(f"first_signal={result['signals'][0]}")
    else:
        print("first_signal=none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
