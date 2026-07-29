from __future__ import annotations

import logging
import math
import time
from typing import Any

import pandas as pd

from gold_bot.src.application.services.gold_runner import GoldRunner
from gold_bot.src.infrastructure.charting.live_plot import LiveChartRenderer
from gold_bot.src.infrastructure.config.settings import GoldSettings
from gold_bot.src.infrastructure.mt5.connector import GoldMt5Connector

logger = logging.getLogger(__name__)


class GoldBacktestService:
    def __init__(self, settings: GoldSettings, runner: GoldRunner, chart_renderer: LiveChartRenderer) -> None:
        self.settings = settings
        self.runner = runner
        self.chart_renderer = chart_renderer

    def run(self, lower_frame: pd.DataFrame, higher_frame: pd.DataFrame, source_name: str, artifact_stem: str) -> dict[str, Any]:
        working_lower = self._normalize_frame(lower_frame)
        working_higher = self._normalize_frame(higher_frame)
        working_lower, working_higher = self._apply_backtest_lookback(working_lower, working_higher)
        if working_lower.empty or working_higher.empty:
            self.chart_renderer.close()
            result = {
                "signals": [],
                "total_signals": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0.0,
                "start_balance": 0.0,
                "end_balance": 0.0,
                "balance_change": 0.0,
                "status": "completed_no_data",
                "processed_bars": 0,
            }
            self._log_backtest_exit_summary(result=result, source_name=source_name, artifact_stem=artifact_stem)
            return result

        lookback = max(300, int(getattr(self.settings, "ema_trend_period", 200)) * 3)
        higher_datetimes = pd.to_datetime(working_higher["datetime"], errors="coerce").tolist()
        higher_end = 0
        equity = float(getattr(self.settings, "backtest_initial_balance", 10000.0))
        initial_equity = equity
        equity_curve: list[dict[str, Any]] = []
        account_snapshot = {
            "login": "backtest",
            "balance": equity,
            "equity": equity,
            "margin": 0.0,
            "free_margin": equity,
            "currency": "USD",
        }
        account_change = {"delta_balance": 0.0, "delta_equity": 0.0}

        history: list[dict[str, Any]] = []
        markers: list[dict[str, Any]] = []
        wins = 0
        losses = 0
        breakeven = 0
        margin_rejections = 0
        volume_cap_events = 0
        sizing_rule = "risk_percent"
        volume_samples: list[float] = []
        processed_bars = 0
        status = "completed"
        warned_high_volume = False
        warned_high_equity = False
        profile = self._resolve_backtest_profile()
        try:
            for idx in range(len(working_lower)):
                processed_bars = idx + 1
                lower_start = max(0, idx - lookback + 1)
                lower_slab = working_lower.iloc[lower_start : idx + 1].copy()
                ts = lower_slab.iloc[-1]["datetime"] if "datetime" in lower_slab.columns else idx

                while higher_end < len(higher_datetimes) and higher_datetimes[higher_end] <= ts:
                    higher_end += 1
                higher_slab = self._higher_slab_by_index(working_higher, higher_end, lookback)

                candidates = self.runner.evaluate_candidates(lower_slab, higher_frame=higher_slab)
                for candidate in candidates:
                    raw_volume, sizing_rule = self._resolve_backtest_volume(equity)
                    volume, was_capped = self._normalize_backtest_volume(raw_volume, profile)
                    if was_capped:
                        volume_cap_events += 1
                    volume_samples.append(volume)

                    if not warned_high_volume and float(getattr(self.settings, "backtest_warn_volume_above", 0.0)) > 0 and volume >= float(getattr(self.settings, "backtest_warn_volume_above", 0.0)):
                        warned_high_volume = True
                        logger.warning(
                            "⚠️ Backtest high volume warning | volume=%.2f rule=%s threshold=%.2f equity=%.2f",
                            volume,
                            sizing_rule,
                            float(getattr(self.settings, "backtest_warn_volume_above", 0.0)),
                            equity,
                        )

                    equity_warning_multiple = max(1.0, float(getattr(self.settings, "backtest_warn_equity_multiplier", 20.0)))
                    if not warned_high_equity and initial_equity > 0 and equity >= (initial_equity * equity_warning_multiple):
                        warned_high_equity = True
                        logger.warning(
                            "⚠️ Backtest high equity growth warning | equity=%.2f start=%.2f multiple=%.2f",
                            equity,
                            initial_equity,
                            equity / initial_equity,
                        )

                    if self._should_reject_for_margin(
                        entry_price=float(candidate.price),
                        volume=volume,
                        equity=equity,
                        profile=profile,
                    ):
                        margin_rejections += 1
                        logger.info(
                            "⛔ Backtest margin reject simulated | strategy=%s direction=%s price=%.5f volume=%.2f equity=%.2f",
                            candidate.strategy,
                            candidate.direction,
                            float(candidate.price),
                            volume,
                            equity,
                        )
                        continue

                    pnl = self._estimate_trade_outcome(
                        lower_frame=working_lower,
                        idx=idx,
                        direction=candidate.direction,
                        entry_price=float(candidate.price),
                        volume=volume,
                    )
                    if pnl > 0:
                        wins += 1
                    elif pnl < 0:
                        losses += 1
                    else:
                        breakeven += 1
                    prev_equity = equity
                    equity += pnl
                    account_snapshot = {
                        "login": "backtest",
                        "balance": equity,
                        "equity": equity,
                        "margin": 0.0,
                        "free_margin": equity,
                        "currency": "USD",
                    }
                    account_change = {"delta_balance": equity - prev_equity, "delta_equity": equity - prev_equity}
                    signal = {
                        "strategy": candidate.strategy,
                        "direction": candidate.direction,
                        "reason": candidate.reason,
                        "entry_price": round(float(candidate.price), 5),
                        "take_profit_pips": round(self.settings.take_profit_pips, 2),
                        "stop_loss_pips": round(self.settings.stop_loss_pips, 2),
                        "volume": round(volume, 2),
                        "sizing_rule": sizing_rule,
                        "level": 1,
                        "datetime": str(ts),
                    }
                    history.append(signal)
                    markers.append({"datetime": ts, "price": float(candidate.price), "direction": candidate.direction, "type": "signal"})
                    markers.extend(self._build_trade_level_markers(ts=ts, direction=candidate.direction, entry_price=float(candidate.price)))

                equity_curve.append({"datetime": ts, "equity": equity, "balance": equity})

                if self.settings.plot_enabled and (idx % self.settings.refresh_candle_count == 0 or idx == len(working_lower) - 1):
                    self.chart_renderer.render_dual_timeframe(
                        lower_frame=lower_slab,
                        higher_frame=higher_slab,
                        symbol=self.settings.symbols[0] if self.settings.symbols else "XAUUSD",
                        lower_timeframe=self.settings.lower_timeframe,
                        higher_timeframe=self.settings.higher_timeframe,
                        lower_markers=markers,
                        higher_markers=markers,
                        account_snapshot=account_snapshot,
                        account_change=account_change,
                        open_positions_count=0,
                        equity_curve=equity_curve,
                        mode_label="backtest",
                        output_name=f"{artifact_stem}_backtest_heikinashi.png",
                    )
                    delay_seconds = max(0.0, float(self.settings.backtest_speed_ms) / 1000.0)
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
        except KeyboardInterrupt:
            status = "canceled_by_user"
            logger.info("⏹️ Backtest interrupted by user.")
        finally:
            self.chart_renderer.close()

        logger.info(
            "Backtest equity summary | start=%.2f end=%.2f change=%+.2f wins=%s losses=%s breakeven=%s",
            initial_equity,
            equity,
            equity - initial_equity,
            wins,
            losses,
            breakeven,
        )
        closed = wins + losses + breakeven
        win_rate = (wins / closed * 100.0) if closed > 0 else 0.0
        avg_volume = (sum(volume_samples) / len(volume_samples)) if volume_samples else 0.0
        result = {
            "signals": history,
            "total_signals": len(history),
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate": win_rate,
            "start_balance": initial_equity,
            "end_balance": equity,
            "balance_change": equity - initial_equity,
            "sizing_rule": sizing_rule,
            "avg_volume": avg_volume,
            "margin_rejections": margin_rejections,
            "volume_cap_events": volume_cap_events,
            "profile_source": str(profile.get("source", "defaults")),
            "status": status,
            "processed_bars": processed_bars,
        }
        self._log_backtest_exit_summary(result=result, source_name=source_name, artifact_stem=artifact_stem)
        return result

    def _log_backtest_exit_summary(self, result: dict[str, Any], source_name: str, artifact_stem: str) -> None:
        logger.info(
            "📊 Backtest run status | status=%s source=%s artifact=%s",
            result.get("status", "unknown"),
            source_name,
            artifact_stem,
        )
        table_rows = [
            {"metric": "status", "value": str(result.get("status", "unknown"))},
            {"metric": "profile_source", "value": str(result.get("profile_source", "defaults"))},
            {"metric": "sizing_rule", "value": str(result.get("sizing_rule", "risk_percent"))},
            {"metric": "avg_volume", "value": f"{float(result.get('avg_volume', 0.0)):.2f}"},
            {"metric": "volume_cap_events", "value": str(result.get("volume_cap_events", 0))},
            {"metric": "margin_rejections", "value": str(result.get("margin_rejections", 0))},
            {"metric": "processed_bars", "value": str(result.get("processed_bars", 0))},
            {"metric": "signals", "value": str(result.get("total_signals", 0))},
            {"metric": "wins", "value": str(result.get("wins", 0))},
            {"metric": "losses", "value": str(result.get("losses", 0))},
            {"metric": "breakeven", "value": str(result.get("breakeven", 0))},
            {"metric": "win_rate_pct", "value": f"{float(result.get('win_rate', 0.0)):.2f}"},
            {"metric": "start_balance", "value": f"{float(result.get('start_balance', 0.0)):.2f}"},
            {"metric": "end_balance", "value": f"{float(result.get('end_balance', 0.0)):.2f}"},
            {"metric": "balance_change", "value": f"{float(result.get('balance_change', 0.0)):+.2f}"},
            {
                "metric": "positions_passed_failed",
                "value": f"{int(result.get('wins', 0))}/{int(result.get('losses', 0))}",
            },
        ]
        logger.info("📋 Backtest summary table:\n%s", self._format_table(table_rows, ["metric", "value"]))

    def _format_table(self, rows: list[dict[str, Any]], columns: list[str]) -> str:
        normalized = []
        widths: dict[str, int] = {}
        for col in columns:
            widths[col] = len(col)
        for row in rows:
            normalized_row: dict[str, str] = {}
            for col in columns:
                value = row.get(col, "")
                text = f"{value}"
                normalized_row[col] = text
                widths[col] = max(widths[col], len(text))
            normalized.append(normalized_row)

        header = " | ".join(col.ljust(widths[col]) for col in columns)
        separator = "-+-".join("-" * widths[col] for col in columns)
        lines = [header, separator]
        for row in normalized:
            lines.append(" | ".join(row[col].ljust(widths[col]) for col in columns))
        return "\n".join(lines)

    def _normalize_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        working = frame.copy()
        if "datetime" in working.columns:
            working["datetime"] = pd.to_datetime(working["datetime"], errors="coerce")
            working = working.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
        return working

    def _higher_slab_by_index(self, higher_frame: pd.DataFrame, end_index: int, lookback: int) -> pd.DataFrame:
        if end_index <= 0:
            return higher_frame.iloc[:1].copy()
        start_index = max(0, end_index - lookback)
        return higher_frame.iloc[start_index:end_index].copy()

    def _apply_backtest_lookback(self, lower_frame: pd.DataFrame, higher_frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        lookback_value = int(getattr(self.settings, "backtest_lookback_value", 0))
        lookback_unit = str(getattr(self.settings, "backtest_lookback_unit", "weeks")).lower()
        if lookback_value <= 0:
            return lower_frame, higher_frame
        if "datetime" not in lower_frame.columns or "datetime" not in higher_frame.columns:
            logger.warning("Backtest lookback requested but datetime column is missing; skipping lookback filter")
            return lower_frame, higher_frame

        lower_max = pd.to_datetime(lower_frame["datetime"], errors="coerce").max()
        higher_max = pd.to_datetime(higher_frame["datetime"], errors="coerce").max()
        anchor = max(lower_max, higher_max)
        if pd.isna(anchor):
            return lower_frame, higher_frame

        if lookback_unit == "months":
            cutoff = anchor - pd.DateOffset(months=lookback_value)
        else:
            cutoff = anchor - pd.Timedelta(weeks=lookback_value)

        filtered_lower = lower_frame[pd.to_datetime(lower_frame["datetime"], errors="coerce") >= cutoff].copy()
        filtered_higher = higher_frame[pd.to_datetime(higher_frame["datetime"], errors="coerce") >= cutoff].copy()
        logger.info(
            "Backtest lookback applied | unit=%s value=%s cutoff=%s | lower_rows=%s->%s higher_rows=%s->%s",
            lookback_unit,
            lookback_value,
            cutoff,
            len(lower_frame),
            len(filtered_lower),
            len(higher_frame),
            len(filtered_higher),
        )
        return filtered_lower.reset_index(drop=True), filtered_higher.reset_index(drop=True)

    def _build_trade_level_markers(self, ts: Any, direction: str, entry_price: float) -> list[dict[str, Any]]:
        pip_size = max(0.00001, float(getattr(self.settings, "pip_size", 0.01)))
        sl_pips = max(1.0, float(getattr(self.settings, "stop_loss_pips", 120.0)))
        tp_pips = max(1.0, float(getattr(self.settings, "take_profit_pips", 250.0)))
        level_count = max(1, int(getattr(self.settings, "ladder_entries", 1)))
        rr_base = float(getattr(self.settings, "ladder_rr_ratio", 1.5))

        is_buy = direction.lower() == "buy"
        sl_price = entry_price - (sl_pips * pip_size) if is_buy else entry_price + (sl_pips * pip_size)

        markers: list[dict[str, Any]] = [
            {"datetime": ts, "price": entry_price, "direction": direction, "type": "entry", "label": "Entry"},
            {"datetime": ts, "price": sl_price, "direction": direction, "type": "sl", "label": "SL"},
        ]

        for level in range(1, level_count + 1):
            rr_ratio = rr_base if level == 1 else max(1.0, rr_base - (level - 1) * 0.1)
            level_tp_pips = tp_pips * rr_ratio
            tp_price = entry_price + (level_tp_pips * pip_size) if is_buy else entry_price - (level_tp_pips * pip_size)
            markers.append(
                {
                    "datetime": ts,
                    "price": tp_price,
                    "direction": direction,
                    "type": "tp",
                    "label": f"TP{level}",
                }
            )
        return markers

    def _resolve_backtest_volume(self, equity: float) -> tuple[float, str]:
        fixed_backtest_volume = max(0.0, float(getattr(self.settings, "backtest_fixed_volume", 0.0)))
        if fixed_backtest_volume > 0:
            return max(0.01, fixed_backtest_volume), "backtest_fixed_volume"

        fixed_lot_size = max(0.0, float(getattr(self.settings, "fixed_lot_size", 0.0)))
        if fixed_lot_size > 0:
            return max(0.01, fixed_lot_size), "gold_fixed_lot_size"

        risk_percent = max(0.0, float(getattr(self.settings, "risk_percent", 1.0)))
        stop_loss_pips = max(1.0, float(getattr(self.settings, "stop_loss_pips", 120.0)))
        risk_amount = max(0.0, float(equity) * (risk_percent / 100.0))
        # Backtest assumes 1.0 account-currency unit per pip per 1.0 lot.
        volume = risk_amount / stop_loss_pips if stop_loss_pips > 0 else 0.01
        return max(0.01, volume), "risk_percent"

    def _normalize_backtest_volume(self, raw_volume: float, profile: dict[str, float | str]) -> tuple[float, bool]:
        min_volume = max(0.01, float(profile.get("volume_min", 0.01) or 0.01))
        max_volume = max(min_volume, float(profile.get("volume_max", 50.0) or 50.0))
        hard_cap = max(0.0, float(getattr(self.settings, "backtest_max_volume_cap", 0.0)))
        if hard_cap > 0:
            max_volume = min(max_volume, hard_cap)
        step = max(0.0001, float(profile.get("volume_step", 0.01) or 0.01))

        stepped = math.floor(max(0.0, float(raw_volume)) / step) * step
        bounded = max(min_volume, min(max_volume, stepped))
        was_capped = bounded + 1e-9 < float(raw_volume)
        precision = self._step_precision(step)
        return round(bounded, precision), was_capped

    def _resolve_backtest_profile(self) -> dict[str, float | str]:
        profile: dict[str, float | str] = {
            "source": "defaults",
            "volume_min": max(0.01, float(getattr(self.settings, "backtest_volume_min", 0.01))),
            "volume_max": max(0.01, float(getattr(self.settings, "backtest_volume_max", 50.0))),
            "volume_step": max(0.0001, float(getattr(self.settings, "backtest_volume_step", 0.01))),
            "contract_size": max(0.0, float(getattr(self.settings, "backtest_default_contract_size", 100.0))),
            "leverage": max(1.0, float(getattr(self.settings, "backtest_default_leverage", 100.0))),
            "margin_initial": 0.0,
        }
        if not bool(getattr(self.settings, "backtest_use_mt5_profile", True)):
            logger.info("Backtest broker profile | source=defaults (mt5 profile disabled)")
            return profile

        connector = GoldMt5Connector(self.settings)
        if not connector.connect():
            logger.info("Backtest broker profile | source=defaults (mt5 unavailable)")
            return profile

        try:
            requested_symbol = self.settings.symbols[0] if self.settings.symbols else "XAUUSD"
            resolved_symbol = connector.resolve_symbol(requested_symbol) or requested_symbol
            symbol_meta = connector.symbol_info(resolved_symbol) or {}
            account_meta = connector.account_info() or {}
            if float(symbol_meta.get("volume_min", 0.0) or 0.0) > 0:
                profile["volume_min"] = float(symbol_meta.get("volume_min", profile["volume_min"]))
            if float(symbol_meta.get("volume_max", 0.0) or 0.0) > 0:
                profile["volume_max"] = float(symbol_meta.get("volume_max", profile["volume_max"]))
            if float(symbol_meta.get("volume_step", 0.0) or 0.0) > 0:
                profile["volume_step"] = float(symbol_meta.get("volume_step", profile["volume_step"]))
            if float(symbol_meta.get("trade_contract_size", 0.0) or 0.0) > 0:
                profile["contract_size"] = float(symbol_meta.get("trade_contract_size", profile["contract_size"]))
            if float(symbol_meta.get("margin_initial", 0.0) or 0.0) > 0:
                profile["margin_initial"] = float(symbol_meta.get("margin_initial", 0.0))
            if float(account_meta.get("leverage", 0.0) or 0.0) > 0:
                profile["leverage"] = float(account_meta.get("leverage", profile["leverage"]))
            profile["source"] = "mt5"
            logger.info(
                "Backtest broker profile | source=mt5 symbol=%s volume_min=%.2f volume_max=%.2f volume_step=%.4f contract_size=%.2f leverage=%.0f margin_initial=%.2f",
                resolved_symbol,
                float(profile.get("volume_min", 0.01)),
                float(profile.get("volume_max", 50.0)),
                float(profile.get("volume_step", 0.01)),
                float(profile.get("contract_size", 100.0)),
                float(profile.get("leverage", 100.0)),
                float(profile.get("margin_initial", 0.0)),
            )
        finally:
            connector.disconnect()
        return profile

    def _estimate_required_margin(self, entry_price: float, volume: float, profile: dict[str, float | str]) -> float:
        margin_initial = max(0.0, float(profile.get("margin_initial", 0.0) or 0.0))
        if margin_initial > 0:
            return margin_initial * max(0.0, volume)
        leverage = max(1.0, float(profile.get("leverage", 100.0) or 100.0))
        contract_size = max(0.0, float(profile.get("contract_size", 100.0) or 100.0))
        notional = max(0.0, entry_price) * max(0.0, volume) * contract_size
        return notional / leverage if leverage > 0 else 0.0

    def _should_reject_for_margin(self, entry_price: float, volume: float, equity: float, profile: dict[str, float | str]) -> bool:
        if not bool(getattr(self.settings, "backtest_simulate_margin_rejection", True)):
            return False
        required_margin = self._estimate_required_margin(entry_price=entry_price, volume=volume, profile=profile)
        if required_margin <= 0:
            return False
        margin_available_ratio = min(1.0, max(0.1, float(getattr(self.settings, "backtest_margin_available_ratio", 0.95))))
        available_margin = max(0.0, equity * margin_available_ratio)
        return required_margin > available_margin

    def _step_precision(self, step: float) -> int:
        text = f"{step:.8f}".rstrip("0")
        if "." not in text:
            return 2
        return max(2, len(text.split(".", 1)[1]))

    def _estimate_trade_outcome(
        self,
        lower_frame: pd.DataFrame,
        idx: int,
        direction: str,
        entry_price: float,
        volume: float,
    ) -> float:
        if idx + 1 >= len(lower_frame):
            return 0.0

        next_close = float(lower_frame.iloc[idx + 1]["close"])
        pip_size = max(0.00001, float(getattr(self.settings, "pip_size", 0.01)))
        sl_pips = max(1.0, float(getattr(self.settings, "stop_loss_pips", 120.0)))
        tp_pips = max(1.0, float(getattr(self.settings, "take_profit_pips", 250.0)))

        if direction.lower() == "buy":
            move = next_close - entry_price
        else:
            move = entry_price - next_close

        pips = move / pip_size
        bounded_pips = max(-sl_pips, min(tp_pips, pips))
        return bounded_pips * max(0.0, float(volume))
