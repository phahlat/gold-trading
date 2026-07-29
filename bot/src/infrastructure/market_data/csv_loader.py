from __future__ import annotations

from pathlib import Path

import pandas as pd


_TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}

_SYMBOL_EQUIVALENTS = {
    "GOLD": ["GOLD", "XAUUSD"],
    "XAUUSD": ["XAUUSD", "GOLD"],
}


def load_ohlc_frame(path: str | Path) -> pd.DataFrame:
    data_path = Path(path)
    if data_path.is_dir():
        csv_files = sorted(data_path.glob("*.csv"))
        if not csv_files:
            raise FileNotFoundError(f"No CSV files found in {data_path}")
        data_path = csv_files[0]

    if not data_path.exists():
        raise FileNotFoundError(f"CSV data file not found: {data_path}")

    def _from_dense_columns(raw_frame: pd.DataFrame) -> pd.DataFrame:
        if len(raw_frame.columns) >= 7:
            datetime_col = raw_frame.iloc[:, 0].astype(str).str.strip() + " " + raw_frame.iloc[:, 1].astype(str).str.strip()
            return pd.DataFrame(
                {
                    "datetime": datetime_col,
                    "open": raw_frame.iloc[:, 2],
                    "high": raw_frame.iloc[:, 3],
                    "low": raw_frame.iloc[:, 4],
                    "close": raw_frame.iloc[:, 5],
                    "volume": raw_frame.iloc[:, 6],
                }
            )
        return pd.DataFrame(
            {
                "datetime": raw_frame.iloc[:, 0],
                "open": raw_frame.iloc[:, 1],
                "high": raw_frame.iloc[:, 2],
                "low": raw_frame.iloc[:, 3],
                "close": raw_frame.iloc[:, 4],
                "volume": raw_frame.iloc[:, 5] if len(raw_frame.columns) > 5 else 0,
            }
        )

    frame: pd.DataFrame
    try:
        frame = pd.read_csv(data_path)
    except Exception:
        raw = pd.read_csv(data_path, sep=r"\s+", header=None, engine="python")
        if len(raw.columns) < 5:
            raise
        frame = _from_dense_columns(raw)

    expected_cols = {"open", "high", "low", "close"}
    if not expected_cols.issubset(set(str(col).lower() for col in frame.columns)):
        raw = pd.read_csv(data_path, sep=r"[\t,;\s]+", header=None, engine="python")
        if len(raw.columns) < 5:
            raise ValueError(f"Unable to parse OHLC columns from {data_path}")
        frame = _from_dense_columns(raw)

    if "datetime" not in frame.columns:
        for candidate in ["time", "date", "timestamp", "dt"]:
            if candidate in frame.columns:
                frame = frame.rename(columns={candidate: "datetime"})
                break

    if "datetime" in frame.columns:
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
        frame = frame.dropna(subset=["datetime"]).sort_values("datetime")
    else:
        frame = frame.reset_index(drop=True)

    for column in ["open", "high", "low", "close", "volume"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        else:
            frame[column] = pd.NA

    frame = frame.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return frame


def timeframe_to_minutes(timeframe: str) -> int:
    key = timeframe.strip().upper()
    if key not in _TIMEFRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return _TIMEFRAME_MINUTES[key]


def _resolve_existing_path(candidate: str | Path) -> Path | None:
    path = Path(candidate)
    if path.exists():
        return path
    return None


def resolve_backtest_timeframe_file(
    data_source: str | Path,
    symbol: str,
    timeframe: str,
) -> Path:
    source = Path(data_source)
    search_dir = source if source.is_dir() else source.parent
    if not search_dir.exists():
        for candidate in [
            Path("bot/backtest/data"),
            Path("gold_bot/backtest/data"),
            Path("backtest/data"),
        ]:
            if candidate.exists():
                search_dir = candidate
                break
        else:
            raise FileNotFoundError(f"Backtest data directory not found: {search_dir}")

    tf_minutes = timeframe_to_minutes(timeframe)
    tf_token = str(tf_minutes)
    candidates = sorted(search_dir.glob(f"*{tf_token}.csv"))
    if not candidates:
        raise FileNotFoundError(f"No CSV found for timeframe {timeframe} ({tf_minutes} minutes) in {search_dir}")

    symbol_upper = symbol.strip().upper()
    equivalents = [item.upper() for item in _SYMBOL_EQUIVALENTS.get(symbol_upper, [symbol_upper])]

    ranked: list[tuple[int, Path]] = []
    for file_path in candidates:
        stem_upper = file_path.stem.upper()
        score = 100
        if any(stem_upper.startswith(eq) for eq in equivalents):
            score = 0
        elif any(eq in stem_upper for eq in equivalents):
            score = 1
        # penalize longer/less-direct names
        score += len(file_path.name)
        ranked.append((score, file_path))

    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def load_backtest_ltf_htf_frames(
    data_source: str | Path,
    symbol: str,
    lower_timeframe: str,
    higher_timeframe: str,
) -> tuple[pd.DataFrame, pd.DataFrame, Path, Path]:
    lower_path = resolve_backtest_timeframe_file(data_source=data_source, symbol=symbol, timeframe=lower_timeframe)
    higher_path = resolve_backtest_timeframe_file(data_source=data_source, symbol=symbol, timeframe=higher_timeframe)
    lower_frame = load_ohlc_frame(lower_path)
    higher_frame = load_ohlc_frame(higher_path)
    return lower_frame, higher_frame, lower_path, higher_path
