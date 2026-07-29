from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from matplotlib.lines import Line2D

logger = logging.getLogger(__name__)


class LiveChartRenderer:
    def __init__(
        self,
        output_dir: str | Path | None,
        interactive: bool,
        chart_width: float,
        chart_height: float,
        max_lower_candles: int,
        max_higher_candles: int,
        strategy_names: list[str] | None = None,
        ema_fast: int = 9,
        ema_slow: int = 21,
        ema_trend_period: int = 200,
    ) -> None:
        self.output_dir = Path(output_dir or "logs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._interactive = interactive
        self._chart_width = chart_width
        self._chart_height = chart_height
        self._figure: plt.Figure | None = None
        self._lower_ax: Any | None = None
        self._higher_ax: Any | None = None
        self._equity_ax: Any | None = None
        self._max_lower_candles = max_lower_candles
        self._max_higher_candles = max_higher_candles
        normalized_strategies = {name.strip().lower() for name in (strategy_names or []) if str(name).strip()}
        self._show_ema_overlay = bool(normalized_strategies.intersection({"trend_following", "scalping"}))
        self._ema_fast = max(1, int(ema_fast))
        self._ema_slow = max(1, int(ema_slow))
        self._ema_trend_period = max(1, int(ema_trend_period))
        self._window_shown = False

    def _ensure_canvas(self) -> tuple[plt.Figure, Any, Any, Any]:
        if self._figure is not None and self._lower_ax is not None and self._higher_ax is not None and self._equity_ax is not None:
            return self._figure, self._lower_ax, self._higher_ax, self._equity_ax

        if self._interactive:
            try:
                plt.ion()
            except Exception:  # pragma: no cover - backend dependent
                logger.warning("Interactive backend unavailable. Falling back to snapshot plotting.")
                self._interactive = False

        fig = plt.figure(figsize=(self._chart_width, self._chart_height), constrained_layout=True)
        grid = fig.add_gridspec(2, 2, height_ratios=[3, 1])
        lower_ax = fig.add_subplot(grid[0, 0])
        higher_ax = fig.add_subplot(grid[0, 1])
        equity_ax = fig.add_subplot(grid[1, :])
        self._figure = fig
        self._lower_ax = lower_ax
        self._higher_ax = higher_ax
        self._equity_ax = equity_ax
        if self._interactive and not self._window_shown:
            try:
                plt.show(block=False)
                self._window_shown = True
            except Exception:  # pragma: no cover - backend dependent
                logger.warning("Interactive show call failed; continuing with file snapshots only.")
                self._interactive = False
        return fig, lower_ax, higher_ax, equity_ax

    @staticmethod
    def _clip_frame(frame: pd.DataFrame, max_points: int) -> pd.DataFrame:
        if frame.empty:
            return frame
        return frame.tail(max_points).copy()

    @staticmethod
    def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        normalized = frame.copy()
        if "datetime" in normalized.columns:
            normalized["datetime"] = pd.to_datetime(normalized["datetime"], errors="coerce")
            normalized = normalized.dropna(subset=["datetime"]).set_index("datetime")
        elif not isinstance(normalized.index, pd.DatetimeIndex):
            normalized.index = pd.RangeIndex(start=0, stop=len(normalized), step=1)
        normalized = normalized.sort_index()
        if isinstance(normalized.index, pd.DatetimeIndex):
            normalized = normalized[~normalized.index.duplicated(keep="last")]
        return normalized[["open", "high", "low", "close"]].astype(float)

    @staticmethod
    def _to_heikin_ashi(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        ha = frame.copy()
        ha_close = (ha["open"] + ha["high"] + ha["low"] + ha["close"]) / 4.0
        ha_open = ha_close.copy()
        ha_open.iloc[0] = (ha["open"].iloc[0] + ha["close"].iloc[0]) / 2.0
        for idx in range(1, len(ha)):
            ha_open.iloc[idx] = (ha_open.iloc[idx - 1] + ha_close.iloc[idx - 1]) / 2.0
        ha_high = pd.concat([ha["high"], ha_open, ha_close], axis=1).max(axis=1)
        ha_low = pd.concat([ha["low"], ha_open, ha_close], axis=1).min(axis=1)
        return pd.DataFrame({"Open": ha_open, "High": ha_high, "Low": ha_low, "Close": ha_close}, index=ha.index)

    @staticmethod
    def _plot_markers(ax: Any, markers: list[dict[str, Any]], index: pd.Index) -> None:
        if not markers or index.empty:
            return
        by_style: dict[tuple[str, str], dict[str, list[Any]]] = {}
        for marker in markers:
            ts = marker.get("datetime")
            price = marker.get("price")
            direction = str(marker.get("direction", "buy")).lower()
            marker_type = str(marker.get("type", "signal")).lower()
            label = str(marker.get("label", "")).strip()
            if ts is None or price is None:
                continue
            try:
                timestamp = pd.to_datetime(ts)
            except Exception:
                continue
            if isinstance(index, pd.DatetimeIndex):
                if timestamp not in index:
                    continue
                marker_x = int(index.get_loc(timestamp))
            else:
                marker_x = len(index) - 1

            if marker_type == "sl":
                color = "#ff7f0e"
                shape = "v"
            elif marker_type == "tp":
                color = "#1f77b4"
                shape = "^"
            elif marker_type == "entry":
                color = "#2ca02c" if direction == "buy" else "#d62728"
                shape = "D"
            else:
                color = "#1a7f37" if direction == "buy" else "#d1242f"
                shape = "o"

            bucket = by_style.setdefault((shape, color), {"x": [], "y": [], "labels": []})
            bucket["x"].append(marker_x)
            bucket["y"].append(float(price))
            bucket["labels"].append(label)

        for (shape, color), payload in by_style.items():
            ax.scatter(
                payload["x"],
                payload["y"],
                c=color,
                s=38,
                marker=shape,
                alpha=0.92,
                edgecolors="#111111",
                linewidths=0.5,
                zorder=6,
            )
            for x, y, text in zip(payload["x"], payload["y"], payload["labels"]):
                if not text:
                    continue
                ax.annotate(text, (x, y), textcoords="offset points", xytext=(4, 4), fontsize=7, color=color)

    def render_dual_timeframe(
        self,
        lower_frame: pd.DataFrame,
        higher_frame: pd.DataFrame,
        symbol: str,
        lower_timeframe: str,
        higher_timeframe: str,
        lower_markers: list[dict[str, Any]] | None = None,
        higher_markers: list[dict[str, Any]] | None = None,
        account_snapshot: dict[str, Any] | None = None,
        account_change: dict[str, Any] | None = None,
        open_positions_count: int | None = None,
        equity_curve: list[dict[str, Any]] | None = None,
        ticker_point: dict[str, Any] | None = None,
        ticker_trail: list[dict[str, Any]] | None = None,
        mode_label: str = "live",
        output_name: str = "live_dual_chart.png",
    ) -> Path:
        fig, lower_ax, higher_ax, equity_ax = self._ensure_canvas()

        lower_ax.clear()
        higher_ax.clear()
        equity_ax.clear()

        lower_raw = self._normalize_frame(lower_frame)
        higher_raw = self._normalize_frame(higher_frame)
        lower_clipped = self._clip_frame(lower_raw, self._max_lower_candles)
        higher_clipped = self._clip_frame(higher_raw, self._max_higher_candles)
        lower = self._to_heikin_ashi(lower_clipped)
        higher = self._to_heikin_ashi(higher_clipped)
        if not lower.empty:
            mpf.plot(
                lower,
                type="candle",
                style="charles",
                ax=lower_ax,
                volume=False,
                xrotation=15,
                datetime_format="%H:%M",
                warn_too_much_data=max(10000, len(lower) + 1),
            )
            self._draw_ema_overlays(lower_ax, lower_raw, lower.index)
        if not higher.empty:
            mpf.plot(
                higher,
                type="candle",
                style="charles",
                ax=higher_ax,
                volume=False,
                xrotation=15,
                datetime_format="%d %H:%M",
                warn_too_much_data=max(10000, len(higher) + 1),
            )
            self._draw_ema_overlays(higher_ax, higher_raw, higher.index)

        self._plot_markers(lower_ax, lower_markers or [], lower.index)
        self._plot_markers(higher_ax, higher_markers or [], higher.index)
        self._draw_ticker_trail(lower_ax, ticker_trail, lower.index)
        self._draw_ticker_trail(higher_ax, ticker_trail, higher.index)
        self._draw_ticker_dot(lower_ax, ticker_point, lower.index)
        self._draw_ticker_dot(higher_ax, ticker_point, higher.index)

        lower_ax.set_title(f"{symbol} {lower_timeframe} Heikin-Ashi (last {len(lower)} bars)")
        higher_ax.set_title(f"{symbol} {higher_timeframe} Heikin-Ashi (last {len(higher)} bars)")
        lower_ax.grid(True, alpha=0.2)
        higher_ax.grid(True, alpha=0.2)

        self._draw_equity_panel(equity_ax, equity_curve, mode_label)

        self._draw_account_legend(fig, equity_ax, account_snapshot, account_change, open_positions_count)

        if self._interactive:
            fig.canvas.draw()
            fig.canvas.flush_events()
            plt.pause(0.01)

        path = self.output_dir / output_name
        fig.savefig(path, dpi=150)
        return path

    def _draw_ema_overlays(self, ax: Any, raw_frame: pd.DataFrame, clipped_index: pd.Index) -> None:
        if not self._show_ema_overlay or raw_frame.empty or clipped_index.empty:
            return
        if "close" not in raw_frame.columns:
            return

        close = raw_frame["close"].astype(float)
        ema_fast = close.ewm(span=self._ema_fast, adjust=False).mean().reindex(clipped_index)
        ema_slow = close.ewm(span=self._ema_slow, adjust=False).mean().reindex(clipped_index)
        ema_trend = close.ewm(span=self._ema_trend_period, adjust=False).mean().reindex(clipped_index)

        x = list(range(len(clipped_index)))
        ax.plot(x, ema_fast.values, color="#ffb000", linewidth=1.1, alpha=0.95, label=f"EMA {self._ema_fast}", zorder=5)
        ax.plot(x, ema_slow.values, color="#00a1ff", linewidth=1.1, alpha=0.95, label=f"EMA {self._ema_slow}", zorder=5)
        ax.plot(x, ema_trend.values, color="#8e44ad", linewidth=1.0, alpha=0.9, label=f"EMA {self._ema_trend_period}", zorder=5)
        ax.legend(loc="upper left", fontsize=8)

    def _draw_equity_panel(self, ax: Any, equity_curve: list[dict[str, Any]] | None, mode_label: str) -> None:
        ax.grid(True, alpha=0.25)
        ax.set_title(f"Account Equity Curve ({mode_label})")
        ax.set_ylabel("equity")
        if not equity_curve:
            ax.text(0.5, 0.5, "No equity points yet", transform=ax.transAxes, ha="center", va="center", alpha=0.7)
            return

        curve = pd.DataFrame(equity_curve)
        if "datetime" in curve.columns:
            curve["datetime"] = pd.to_datetime(curve["datetime"], errors="coerce")
            curve = curve.dropna(subset=["datetime"]).sort_values("datetime")
            x = curve["datetime"]
        else:
            x = range(len(curve))

        if "equity" not in curve.columns:
            ax.text(0.5, 0.5, "Equity values unavailable", transform=ax.transAxes, ha="center", va="center", alpha=0.7)
            return

        y = curve["equity"].astype(float)
        ax.plot(x, y, color="#2d6cdf", linewidth=1.6, label="equity")
        start = float(y.iloc[0])
        end = float(y.iloc[-1])
        change = end - start
        ax.legend(loc="upper left", fontsize=9, title=f"Start {start:.2f} | End {end:.2f} | d {change:+.2f}")

    def _draw_ticker_dot(self, ax: Any, ticker_point: dict[str, Any] | None, index: pd.Index) -> None:
        if not ticker_point or index.empty:
            return
        price = ticker_point.get("price")
        timestamp = ticker_point.get("datetime")
        direction = str(ticker_point.get("direction", "buy")).lower()
        if price is None or timestamp is None:
            return

        ts = pd.to_datetime(timestamp, errors="coerce")
        if pd.isna(ts):
            return

        # mplfinance external-axes mode uses positional x coordinates.
        x = len(index) - 1

        color = "#00c853" if direction == "buy" else "#ff1744"
        ax.scatter([x], [float(price)], s=80, marker="o", c=color, edgecolors="#000000", linewidths=0.8, zorder=8)
        ax.annotate("tick", (x, float(price)), textcoords="offset points", xytext=(6, 5), fontsize=8, color=color)

    def _draw_ticker_trail(self, ax: Any, ticker_trail: list[dict[str, Any]] | None, index: pd.Index) -> None:
        if not ticker_trail or index.empty:
            return
        prices = [float(item.get("price", 0.0)) for item in ticker_trail if item.get("price") is not None]
        if len(prices) < 2:
            return

        count = min(len(prices), 20)
        prices = prices[-count:]
        right = len(index) - 1
        left = max(0, right - count + 1)
        xs = list(range(left, right + 1))[-count:]
        if len(xs) != len(prices):
            return
        ax.plot(xs, prices, color="#ff9800", linewidth=1.2, alpha=0.9, zorder=7)

    def _draw_account_legend(
        self,
        fig: plt.Figure,
        anchor_ax: Any,
        account_snapshot: dict[str, Any] | None,
        account_change: dict[str, Any] | None,
        open_positions_count: int | None,
    ) -> None:
        if account_snapshot is None:
            return
        balance = float(account_snapshot.get("balance", 0.0))
        equity = float(account_snapshot.get("equity", 0.0))
        d_balance = float((account_change or {}).get("delta_balance", 0.0))
        d_equity = float((account_change or {}).get("delta_equity", 0.0))
        margin = float(account_snapshot.get("margin", 0.0))
        free_margin = float(account_snapshot.get("free_margin", 0.0))
        positions = int(open_positions_count or 0)
        currency = str(account_snapshot.get("currency", ""))

        handles = [
            Line2D([], [], marker="o", linestyle="", markersize=7, color="#1a7f37", label="Buy marker"),
            Line2D([], [], marker="o", linestyle="", markersize=7, color="#d1242f", label="Sell marker"),
            Line2D([], [], color="none", label=f"Balance: {balance:.2f} {currency}"),
            Line2D([], [], color="none", label=f"Equity: {equity:.2f} {currency}"),
            Line2D([], [], color="none", label=f"dBalance: {d_balance:+.2f} | dEquity: {d_equity:+.2f}"),
            Line2D([], [], color="none", label=f"Margin: {margin:.2f} | Free: {free_margin:.2f} | Open: {positions}"),
        ]
        legend = anchor_ax.legend(handles=handles, loc="lower right", frameon=True, fontsize=8, title="Account State")
        legend.get_frame().set_alpha(0.88)
        # fig.suptitle("Dual Timeframe Execution View", fontsize=13, y=0.98)

    def close(self) -> None:
        if self._figure is not None:
            plt.close(self._figure)
        self._figure = None
        self._lower_ax = None
        self._higher_ax = None
        self._equity_ax = None
        self._window_shown = False
