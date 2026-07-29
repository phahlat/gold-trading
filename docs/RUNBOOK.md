# Gold Bot Runbook

## Scope

This runbook covers the single gold bot entrypoint used for backtest, dry-run, and live execution.

## Prerequisites

1. Open the repository root in a terminal.
2. Activate the workspace virtual environment before running anything: `.venv\Scripts\Activate.ps1` (PowerShell) or `.venv\Scripts\python.exe` for direct execution.
3. Copy bot/.env.example to bot/.env and update the MT5 credentials and strategy settings.
4. Choose a strategy preset from docs/STRATEGIES.md before you run the bot.

## Backtest Commands

Backtest timeframe loading behavior:
- Backtest now loads two CSV files from the data directory: one for `LOWER_TIMEFRAME` and one for `HIGHER_TIMEFRAME`.
- Example: `LOWER_TIMEFRAME=M15` loads `*15.csv`; `HIGHER_TIMEFRAME=H1` loads `*60.csv`.
- The higher timeframe is not resampled from the lower timeframe file.
- Optional lookback filter: use `--backtest-lookback-value` and `--backtest-lookback-unit weeks|months` to run only the most recent window.

### Trend-following backtest

```bat
cd /d c:\CodePlay\gold-trading-bot
.venv\Scripts\python.exe .\main.py --mode backtest --no-trade --symbols XAUUSD --strategy trend_following --backtest-data-dir bot\backtest\data --backtest-results-subdir trend_backtest --backtest-lookback-value 12 --backtest-lookback-unit weeks
```

### Price-action backtest

```bat
cd /d c:\CodePlay\gold-trading-bot
.venv\Scripts\python.exe .\main.py --mode backtest --no-trade --symbols XAUUSD --strategy price_action --backtest-data-dir bot\backtest\data\XAUUSD15.csv --backtest-results-subdir price_action_backtest
```

### Scalping backtest

```bat
cd /d c:\CodePlay\gold-trading-bot
.venv\Scripts\python.exe .\main.py --mode backtest --no-trade --symbols XAUUSD --strategy scalping --backtest-data-dir bot\backtest\data\XAUUSD15.csv --backtest-results-subdir scalping_backtest
```

### News and breakout backtests

```bat
cd /d c:\CodePlay\gold-trading-bot
.venv\Scripts\python.exe .\main.py --mode backtest --no-trade --symbols XAUUSD --strategy news --backtest-data-dir bot\backtest\data\XAUUSD15.csv --backtest-results-subdir news_backtest
.venv\Scripts\python.exe .\main.py --mode backtest --no-trade --symbols XAUUSD --strategy session_breakout --backtest-data-dir bot\backtest\data\XAUUSD15.csv --backtest-results-subdir breakout_backtest
```

## Live / Dry-Run Commands

```bat
cd /d c:\CodePlay\gold-trading-bot

REM Activate the environment once per terminal session
.\.venv\Scripts\Activate.ps1

REM Dry run with logging enabled
.\.venv\Scripts\python.exe .\main.py --mode live --no-trade --symbols XAUUSD --strategy trend_following --plot

REM Headless dry run
.\.venv\Scripts\python.exe .\main.py --mode live --no-trade --symbols XAUUSD --strategy price_action --no-plot

REM Live execution (orders allowed, MT5 credentials required)
.\.venv\Scripts\python.exe .\main.py --mode live --trade --symbols XAUUSD --strategy trend_following,price_action --plot

REM Broker symbol evaluation (GOLD alias)
.\.venv\Scripts\python.exe .\main.py --mode live --no-trade --symbols GOLD --strategy trend_following --no-plot --max-cycles 1

REM Broker symbol evaluation (XAUUSD alias)
.\.venv\Scripts\python.exe .\main.py --mode live --no-trade --symbols XAUUSD --strategy trend_following --no-plot --max-cycles 1
```

## Logging and plotting

- Logging writes to logs/gold-bot.log and also prints to the terminal.
- Log verbosity is controlled by `LOG_LEVEL` in `bot/.env` and defaults to `DEBUG`.
- Emoji log taxonomy in live mode:
	- `📈` cycle-level candidate signal counts
	- `📣` full signal details (strategy, direction, reason, price, timestamps)
	- `⛔` explicit skip reasons (daily trades, daily risk, open-position cap)
	- `🧾` entry request payload before order placement
	- `✅` accepted/filled entry outcomes
	- `❌` rejected/failed order outcomes
	- `⚠️` transient data/quote/account warnings
- Plotting is controlled by `--plot` or `--no-plot` and by `PLOT_ENABLED=true/false` in the env file.
- Live plotting renders side-by-side lower/higher timeframe Heikin-Ashi charts and overlays trade signal / entry markers.
- Backtest plotting also overlays labeled trade levels at entry timestamp: `Entry`, `SL`, and `TP1..TPx`.
- Live logs now include `🛰️ Plot update input | ltf_rows=... htf_rows=...` so you can verify candles are being fed to each panel.
- Live charts now overlay a ticker dot (`tick`) from current MT5 price, so movement remains visible even before a new candle closes.
- `POSITION_MONITOR_SECONDS` defaults to 5 seconds and `ACCOUNT_MONITOR_SECONDS` defaults to 30 seconds.
- When you run the bot in live mode, startup logs include configured `log_level`, trading mode, plotting mode, symbols, and strategies.

## Live-mode constraints

- Live mode is MT5-only and does not fall back to CSV candles.
- If MT5 cannot connect, the process exits with an error so no stale/offline data can trigger signals.

## Symbol verification

- On live startup, confirm `🔎 Symbol verification | requested=... resolved=... mode=...`.
- `mode=exact` means broker already has the requested symbol name.
- `mode=alias` means the bot mapped your request to a broker symbol variant.
- Use `--symbols GOLD` or `--symbols XAUUSD`; the bot selects the broker-available equivalent.

## Output locations

1. Backtest summaries: backtest/results/<subdir>
2. Backtest signal CSV: backtest/results/<subdir>/<source>__<strategies>__<timestamp>_signals.csv
3. Backtest chart snapshots: logs/<source>__<strategies>__<timestamp>_backtest_heikinashi.png
4. Runtime logs: logs/gold-bot.log
