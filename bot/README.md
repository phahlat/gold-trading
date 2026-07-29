# Gold Bot

Gold Bot is a lightweight strategy runner for XAUUSD that packages multiple trading ideas into a single workflow with configurable laddered entries.

## What the bot does

The bot evaluates OHLC market data, emits directional signals, and can turn each signal into one or more laddered trade plans. It supports backtesting from historical CSV files and MT5-only live execution.

## Strategies

The available strategies are documented in [docs/STRATEGIES.md](../docs/STRATEGIES.md). They are:

- `trend_following`: EMA trend + pullback breakout logic
- `price_action`: structural rejection logic around recent highs/lows
- `scalping`: fast EMA crossover logic
- `news`: volatility spike / recovery logic
- `session_breakout`: breakout above/below the recent session range

## Usage

```bat
cd /d c:\Users\PHAHLA\Documents\GitHub\phahla-trading-bot\ema-bot
copy gold_bot\.env.example gold_bot\.env
.\.venv\Scripts\python.exe -m gold_bot.src.interfaces.cli.main --env gold_bot\.env --backtest --data gold_bot\backtest\data\XAUUSD15.csv
```

## Configuration

Update gold_bot/.env to change strategy names, ladder size, risk settings, and backtest data location. Detailed comments for each variable are included in [gold_bot/.env.example](.env.example) and the full guide lives in [docs/CONFIGURATION.md](../docs/CONFIGURATION.md).

## Live behavior

- Live mode pulls candles from MT5 only and will stop if MT5 is unavailable.
- Live plotting shows side-by-side lower and higher timeframe Heikin-Ashi charts.
- Trade signal and live entry markers are drawn on charts.
- Position monitoring updates every 5 seconds by default; account monitoring updates every 30 seconds by default.
