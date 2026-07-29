# Gold Bot Workspace

This repository now contains a single gold-focused trading bot.

## Primary Documentation

1. docs/RUNBOOK.md: operational runbook for backtest and live runs
2. docs/CONFIGURATION.md: environment variable setup and presets
3. bot/README.md: package-specific usage notes

## Quick Start

```bat
cd /d c:\CodePlay\gold-trading-bot
python -m venv .venv
.\.venv\Scripts\activate.bat
pip install -r requirements.txt
copy bot\.env.example bot\.env
```

## Documentation set

- [docs/STRATEGIES.md](docs/STRATEGIES.md): strategy behavior and recommended presets
- [docs/RUNBOOK.md](docs/RUNBOOK.md): backtest and live-run commands for each strategy
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): grouped environment variables and detailed comments
- [bot/README.md](bot/README.md): package-level overview and usage

## Root Launcher Examples

```bat
cd /d c:\CodePlay\gold-trading-bot

REM Backtest
.\.venv\Scripts\python.exe .\main.py --mode backtest --no-trade --symbols XAUUSD --strategy trend_following --backtest-data-dir bot\backtest\data

REM Dry run
.\.venv\Scripts\python.exe .\main.py --mode live --no-trade --symbols XAUUSD --strategy trend_following --no-plot --max-cycles 1
```

## Safety

1. Use --no-trade for first-time validation and logic checks.
2. Validate settings on small data first, then scale to full backtests.
3. Keep MT5 credentials private and never commit the local bot/.env file.
