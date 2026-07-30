from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from bot.src.application.services.gold_runner import GoldRunner
from bot.src.infrastructure.charting.live_plot import LiveChartRenderer
from bot.src.infrastructure.config.settings import GoldSettings
from bot.src.infrastructure.mt5.connector import GoldMt5Connector
from bot.src.infrastructure.persistence.sqlite_store import GoldPositionStore

logger = logging.getLogger(__name__)


class GoldLiveService:
    def __init__(
        self,
        settings: GoldSettings,
        runner: GoldRunner,
        connector: GoldMt5Connector,
        position_store: GoldPositionStore,
        chart_renderer: LiveChartRenderer,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.connector = connector
        self.position_store = position_store
        self.chart_renderer = chart_renderer
        self._signal_markers_by_timeframe: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._last_signal_keys: set[str] = set()
        self._daily_trade_count: dict[str, int] = defaultdict(int)
        self._daily_risk_pct: dict[str, float] = defaultdict(float)
        self._last_account_snapshot: dict[str, Any] | None = None
        self._last_account_change: dict[str, Any] | None = None
        self._last_positions: list[dict[str, Any]] = []
        self._equity_curve: list[dict[str, Any]] = []
        self._tick_trail: list[dict[str, Any]] = []
        self._session_started_at: datetime | None = None
        self._session_start_balance: float = 0.0
        self._session_start_equity: float = 0.0
        self._orders_attempted: int = 0
        self._orders_filled: int = 0
        self._orders_rejected: int = 0

    def run(self) -> int:
        requested_symbol = self.settings.symbols[0] if self.settings.symbols else "XAUUSD"
        if not self.connector.connect():
            logger.error("❌ Live mode requires MT5 connectivity. Refusing CSV fallback.")
            return 1

        symbol = self.connector.resolve_symbol(requested_symbol)
        if not symbol:
            broker_sample = self.connector.broker_symbols()[:10]
            logger.error(
                "❌ Requested symbol '%s' was not found on broker symbols. Sample=%s",
                requested_symbol,
                broker_sample,
            )
            return 1

        resolution_mode = "alias" if symbol.upper() != requested_symbol.strip().upper() else "exact"
        logger.info(
            "🔎 Symbol verification | requested=%s resolved=%s mode=%s",
            requested_symbol,
            symbol,
            resolution_mode,
        )

        logger.info(
            "🚀 Live service started | symbol=%s lower_tf=%s higher_tf=%s poll=%.2fs",
            symbol,
            self.settings.lower_timeframe,
            self.settings.higher_timeframe,
            self.settings.poll_seconds,
        )
        logger.info(
            "🧮 Chart candle config | ltf_window=%s htf_window=%s base_candle_count=%s fetch_ltf=%s fetch_htf=%s",
            self.settings.plot_ltf_candles,
            self.settings.plot_htf_candles,
            self.settings.candle_count,
            self._bars_to_pull(self.settings.lower_timeframe),
            self._bars_to_pull(self.settings.higher_timeframe),
        )
        self._session_started_at = datetime.utcnow()
        opening_account = self.connector.account_info() or {}
        self._session_start_balance = float(opening_account.get("balance", 0.0))
        self._session_start_equity = float(opening_account.get("equity", self._session_start_balance))

        cycle = 0
        last_position_monitor = 0.0
        last_account_monitor = 0.0
        last_plot_update = 0.0
        run_status = "completed"

        try:
            while True:
                if self.settings.max_cycles > 0 and cycle >= self.settings.max_cycles:
                    run_status = "completed_max_cycles"
                    break

                lower_frame = self._pull_frame(symbol, self.settings.lower_timeframe)
                higher_frame = self._pull_frame(symbol, self.settings.higher_timeframe)
                if lower_frame.empty or higher_frame.empty:
                    logger.warning("⚠️ No MT5 bars available yet for %s. Retrying.", symbol)
                    time.sleep(max(0.2, self.settings.poll_seconds))
                    continue

                candidates = self.runner.evaluate_candidates(lower_frame, higher_frame=higher_frame)
                if candidates:
                    logger.info("📈 Cycle %s generated %s candidate signal(s)", cycle + 1, len(candidates))
                for candidate in candidates:
                    self._handle_candidate(symbol, candidate, lower_frame, higher_frame)

                now = time.monotonic()
                if now - last_position_monitor >= self.settings.position_monitor_seconds:
                    self._monitor_positions(symbol)
                    last_position_monitor = now

                if now - last_account_monitor >= self.settings.account_monitor_seconds:
                    self._monitor_account()
                    last_account_monitor = now

                if self.settings.plot_enabled and now - last_plot_update >= self.settings.chart_update_seconds:
                    tick_price = self.connector.current_price(symbol, "buy")
                    ticker_point = None
                    if tick_price is not None:
                        ticker_point = {
                            "datetime": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                            "price": float(tick_price),
                            "direction": "buy",
                        }
                        self._tick_trail.append(ticker_point)
                        if len(self._tick_trail) > 40:
                            self._tick_trail = self._tick_trail[-40:]
                    logger.info(
                        "🛰️ Plot update input | ltf_rows=%s htf_rows=%s",
                        len(lower_frame),
                        len(higher_frame),
                    )
                    chart_path = self.chart_renderer.render_dual_timeframe(
                        lower_frame=lower_frame,
                        higher_frame=higher_frame,
                        symbol=symbol,
                        lower_timeframe=self.settings.lower_timeframe,
                        higher_timeframe=self.settings.higher_timeframe,
                        lower_markers=self._signal_markers_by_timeframe[self.settings.lower_timeframe],
                        higher_markers=self._signal_markers_by_timeframe[self.settings.higher_timeframe],
                        account_snapshot=self._last_account_snapshot,
                        account_change=self._last_account_change,
                        open_positions_count=len(self._last_positions),
                        equity_curve=self._equity_curve,
                        ticker_point=ticker_point,
                        ticker_trail=self._tick_trail,
                        mode_label="live",
                        output_name=f"{symbol}_dual_live_heikinashi.png",
                    )
                    logger.info("🖼️ Live chart refreshed: %s", chart_path)
                    last_plot_update = now

                cycle += 1
                time.sleep(max(0.05, self.settings.poll_seconds))
        except KeyboardInterrupt:
            run_status = "canceled_by_user"
            logger.info("⏹️ Interrupted by user.")
        finally:
            self._log_exit_summary(symbol=symbol, run_status=run_status)
            self.chart_renderer.close()
            self.connector.disconnect()
        return 0

    def _pull_frame(self, symbol: str, timeframe: str) -> pd.DataFrame:
        bars = self.connector.get_rates(symbol=symbol, timeframe=timeframe, count=self._bars_to_pull(timeframe))
        if not bars:
            return pd.DataFrame()
        frame = pd.DataFrame(bars)
        frame["datetime"] = pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert(None)
        frame = frame.sort_values("datetime").reset_index(drop=True)
        required = ["datetime", "open", "high", "low", "close"]
        return frame[required]

    def _bars_to_pull(self, timeframe: str) -> int:
        base_count = max(1, int(self.settings.candle_count))
        timeframe_text = (timeframe or "").strip().upper()
        if timeframe_text == self.settings.lower_timeframe.upper():
            return max(base_count, int(self.settings.plot_ltf_candles))
        if timeframe_text == self.settings.higher_timeframe.upper():
            return max(base_count, int(self.settings.plot_htf_candles))
        return base_count

    def _handle_candidate(self, symbol: str, candidate: Any, lower_frame: pd.DataFrame, higher_frame: pd.DataFrame) -> None:
        ts = lower_frame.iloc[-1]["datetime"]
        strategy_name = str(getattr(candidate, "strategy", "")).strip().lower()
        signal_key = f"{symbol}:{candidate.strategy}:{candidate.direction}:{ts.isoformat()}"
        logger.info(
            "📣 Signal detected | key=%s symbol=%s strategy=%s direction=%s reason=%s candidate_price=%.5f ltf_ts=%s htf_ts=%s",
            signal_key,
            symbol,
            candidate.strategy,
            candidate.direction,
            candidate.reason,
            float(candidate.price),
            ts,
            higher_frame.iloc[-1]["datetime"] if not higher_frame.empty and "datetime" in higher_frame.columns else "n/a",
        )
        if signal_key in self._last_signal_keys:
            logger.debug("🔁 Duplicate signal skipped | key=%s", signal_key)
            return

        self._last_signal_keys.add(signal_key)
        self._append_marker(self.settings.lower_timeframe, ts, float(candidate.price), candidate.direction, "signal")

        if not self.settings.enable_trading:
            logger.info(
                "🧪 Signal confirmed (dry-run only) | key=%s symbol=%s strategy=%s direction=%s reason=%s candidate_price=%.5f",
                signal_key,
                symbol,
                candidate.strategy,
                candidate.direction,
                candidate.reason,
                float(candidate.price),
            )
            return

        today_key = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        open_positions = self.connector.open_positions(symbol=symbol)
        strategy_open_positions = self._count_open_positions_by_strategy(open_positions)
        ladder_target = max(1, int(self.settings.ladder_entries if self.settings.enable_multi_entry else 1))
        current_strategy_open = int(strategy_open_positions.get(strategy_name, 0))
        available_strategy_slots = max(0, ladder_target - current_strategy_open)
        if available_strategy_slots <= 0:
            logger.info(
                "⛔ Signal not confirmed (strategy ladder cap) | key=%s strategy=%s symbol=%s open=%s cap=%s direction=%s",
                signal_key,
                candidate.strategy,
                symbol,
                current_strategy_open,
                ladder_target,
                candidate.direction,
            )
            return

        ladder_entries = self.runner.trade_manager.build_ladder(candidate)
        daily_trade_slots = max(0, int(self.settings.max_daily_trades) - int(self._daily_trade_count[today_key]))
        risk_percent = float(self.settings.risk_percent)
        if risk_percent > 0:
            remaining_risk = max(0.0, float(self.settings.max_daily_risk_pct) - float(self._daily_risk_pct[today_key]))
            daily_risk_slots = int(math.floor(remaining_risk / risk_percent))
        else:
            daily_risk_slots = len(ladder_entries)

        executable_slots = min(len(ladder_entries), available_strategy_slots, daily_trade_slots, max(0, daily_risk_slots))
        if executable_slots <= 0:
            logger.info(
                "⛔ Signal not confirmed (no available execution slots) | key=%s strategy=%s direction=%s strategy_slots=%s daily_trade_slots=%s daily_risk_slots=%s",
                signal_key,
                candidate.strategy,
                candidate.direction,
                available_strategy_slots,
                daily_trade_slots,
                daily_risk_slots,
            )
            return

        if executable_slots < len(ladder_entries):
            logger.info(
                "⚠️ Ladder reduced by active limits | key=%s strategy=%s requested=%s executable=%s strategy_slots=%s daily_trade_slots=%s daily_risk_slots=%s",
                signal_key,
                candidate.strategy,
                len(ladder_entries),
                executable_slots,
                available_strategy_slots,
                daily_trade_slots,
                daily_risk_slots,
            )

        account_info = self.connector.account_info()
        symbol_info = self.connector.symbol_info(symbol)
        entry_price = self.connector.current_price(symbol, candidate.direction)
        if entry_price is None:
            logger.warning(
                "⚠️ Signal not confirmed (no live quote) | key=%s symbol=%s strategy=%s direction=%s",
                signal_key,
                symbol,
                candidate.strategy,
                candidate.direction,
            )
            return

        logger.info(
            "✅ Signal confirmed for execution | key=%s symbol=%s strategy=%s direction=%s reason=%s market_price=%.5f",
            signal_key,
            symbol,
            candidate.strategy,
            candidate.direction,
            candidate.reason,
            float(entry_price),
        )

        for ladder_trade in ladder_entries[:executable_slots]:
            order = self.runner.trade_manager.build_order_request(
                candidate=candidate,
                symbol=symbol,
                entry_price=entry_price,
                account_info=account_info,
                symbol_info=symbol_info,
                stop_loss_pips=float(ladder_trade.get("stop_loss_pips", self.settings.stop_loss_pips)),
                take_profit_pips=float(ladder_trade.get("take_profit_pips", self.settings.take_profit_pips)),
                level=int(ladder_trade.get("level", 1)),
            )
            exit_targets = self.runner.trade_manager.update_exit_targets(
                entry_price=float(order["entry_price"]),
                current_price=float(entry_price),
                direction=order["direction"],
                stop_loss_pips=float(self.settings.stop_loss_pips),
                take_profit_pips=float(self.settings.take_profit_pips),
                move_sl_pips=max(1.0, float(self.settings.stop_loss_pips) / 2.0),
                move_tp_pips=max(1.0, float(self.settings.take_profit_pips) / 2.0),
            )
            order["stop_loss"] = exit_targets["stop_loss"]
            order["take_profit"] = exit_targets["take_profit"]
            order_comment = f"{self.settings.trade_comment_prefix}:{candidate.strategy}:L{order['level']}"
            logger.info(
                "🧾 Trade execution request | key=%s symbol=%s strategy=%s level=%s direction=%s volume=%.2f market_price=%.5f request_entry=%.5f sl=%.5f tp=%.5f magic=%s comment=%s",
                signal_key,
                symbol,
                order["strategy"],
                order["level"],
                order["direction"],
                float(order["volume"]),
                float(entry_price),
                float(order["entry_price"]),
                float(order["stop_loss"]),
                float(order["take_profit"]),
                self.settings.trade_magic_number,
                order_comment,
            )

            order_result = self.connector.place_market_order(
                symbol=symbol,
                direction=order["direction"],
                volume=order["volume"],
                stop_loss=order["stop_loss"],
                take_profit=order["take_profit"],
                magic_number=self.settings.trade_magic_number,
                comment=order_comment,
            )
            self._orders_attempted += 1

            if not order_result.get("ok"):
                self._orders_rejected += 1
                logger.error(
                    "❌ Trade execution rejected | key=%s symbol=%s strategy=%s level=%s direction=%s reason=%s retcode=%s filling=%s details=%s",
                    signal_key,
                    symbol,
                    candidate.strategy,
                    order["level"],
                    candidate.direction,
                    order_result.get("reason"),
                    order_result.get("retcode"),
                    order_result.get("filling"),
                    order_result.get("details"),
                )
                continue

            now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            position_key = f"bot:ticket-{order_result.get('order', 0)}:{symbol}"
            self.position_store.upsert_position(
                {
                    "position_key": position_key,
                    "ticket": int(order_result.get("order", 0)),
                    "symbol": symbol,
                    "direction": order["direction"],
                    "volume": order["volume"],
                    "entry_price": float(order_result.get("price", order["entry_price"])),
                    "stop_loss": order["stop_loss"],
                    "take_profit": order["take_profit"],
                    "strategy": order["strategy"],
                    "source": "mt5",
                    "is_external": 0,
                    "status": "open",
                    "opened_at": now_iso,
                }
            )
            self._append_marker(self.settings.lower_timeframe, ts, float(order_result.get("price", order["entry_price"])), order["direction"], "entry")
            self._append_marker(
                self.settings.higher_timeframe,
                higher_frame.iloc[-1]["datetime"],
                float(order_result.get("price", order["entry_price"])),
                order["direction"],
                "entry",
            )
            self._daily_trade_count[today_key] += 1
            self._daily_risk_pct[today_key] += self.settings.risk_percent
            self._orders_filled += 1
            logger.info(
                "✅ Trade executed | key=%s symbol=%s strategy=%s level=%s ticket=%s direction=%s volume=%.2f entry=%.5f sl=%.5f tp=%.5f filling=%s",
                signal_key,
                symbol,
                order["strategy"],
                order["level"],
                order_result.get("order"),
                order["direction"],
                order["volume"],
                float(order_result.get("price", order["entry_price"])),
                float(order["stop_loss"]),
                float(order["take_profit"]),
                order_result.get("filling"),
            )

    def _count_open_positions_by_strategy(self, open_positions: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        prefix = f"{self.settings.trade_comment_prefix}:"
        for item in open_positions:
            comment = str(item.get("comment", "") or "")
            if not comment.startswith(prefix):
                continue
            tail = comment[len(prefix) :]
            if not tail:
                continue
            strategy_name = tail.split(":", 1)[0].strip().lower()
            if not strategy_name:
                continue
            counts[strategy_name] += 1
        return counts

    def _log_exit_summary(self, symbol: str, run_status: str) -> None:
        ended_at = datetime.utcnow()
        started_at = self._session_started_at or ended_at
        account = self.connector.account_info() or {}
        end_balance = float(account.get("balance", self._session_start_balance))
        end_equity = float(account.get("equity", end_balance))
        performance = self.connector.session_trade_performance(
            started_at=started_at,
            ended_at=ended_at,
            symbol=symbol,
            magic_number=self.settings.trade_magic_number,
            comment_prefix=self.settings.trade_comment_prefix,
        )
        wins = int(performance.get("wins", 0))
        losses = int(performance.get("losses", 0))
        closed = int(performance.get("closed_trades", 0))
        win_rate = (wins / closed * 100.0) if closed > 0 else 0.0
        logger.info("📊 Live run status | status=%s symbol=%s", run_status, symbol)
        logger.info(
            "📊 Live session summary | symbol=%s started=%s ended=%s orders_attempted=%s orders_filled=%s orders_rejected=%s closed_trades=%s wins=%s losses=%s breakeven=%s win_rate=%.2f%% balance_start=%.2f balance_end=%.2f balance_change=%+.2f equity_start=%.2f equity_end=%.2f equity_change=%+.2f net_trade_profit=%+.2f",
            symbol,
            started_at.strftime("%Y-%m-%dT%H:%M:%S"),
            ended_at.strftime("%Y-%m-%dT%H:%M:%S"),
            self._orders_attempted,
            self._orders_filled,
            self._orders_rejected,
            closed,
            wins,
            losses,
            int(performance.get("breakeven", 0)),
            win_rate,
            self._session_start_balance,
            end_balance,
            end_balance - self._session_start_balance,
            self._session_start_equity,
            end_equity,
            end_equity - self._session_start_equity,
            float(performance.get("net_profit", 0.0)),
        )
        summary_rows = [
            {"metric": "status", "value": run_status},
            {"metric": "orders_attempted", "value": self._orders_attempted},
            {"metric": "orders_filled", "value": self._orders_filled},
            {"metric": "orders_rejected", "value": self._orders_rejected},
            {"metric": "positions_passed_failed", "value": f"{wins}/{losses}"},
            {"metric": "closed_trades", "value": closed},
            {"metric": "win_rate_pct", "value": f"{win_rate:.2f}"},
            {"metric": "balance_start", "value": f"{self._session_start_balance:.2f}"},
            {"metric": "balance_end", "value": f"{end_balance:.2f}"},
            {"metric": "balance_change", "value": f"{(end_balance - self._session_start_balance):+.2f}"},
            {"metric": "equity_start", "value": f"{self._session_start_equity:.2f}"},
            {"metric": "equity_end", "value": f"{end_equity:.2f}"},
            {"metric": "equity_change", "value": f"{(end_equity - self._session_start_equity):+.2f}"},
            {"metric": "net_trade_profit", "value": f"{float(performance.get('net_profit', 0.0)):+.2f}"},
        ]
        logger.info("📋 Live summary table:\n%s", self._format_table(summary_rows, ["metric", "value"]))

    def _append_marker(self, timeframe: str, timestamp: Any, price: float, direction: str, marker_type: str) -> None:
        markers = self._signal_markers_by_timeframe[timeframe]
        markers.append(
            {
                "datetime": pd.to_datetime(timestamp),
                "price": price,
                "direction": direction,
                "type": marker_type,
            }
        )
        # Keep memory bounded for long sessions.
        if len(markers) > 300:
            self._signal_markers_by_timeframe[timeframe] = markers[-300:]

    def _monitor_positions(self, symbol: str) -> None:
        positions = self.connector.open_positions(symbol=symbol)
        self._last_positions = positions
        logger.info("📌 Position monitor | symbol=%s open_positions=%s", symbol, len(positions))
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        for item in positions:
            position_key = f"mt5:ticket-{item['ticket']}:{item['symbol']}"
            direction = "buy" if int(item.get("type", 0)) == 0 else "sell"
            self.position_store.upsert_position(
                {
                    "position_key": position_key,
                    "ticket": item["ticket"],
                    "symbol": item["symbol"],
                    "direction": direction,
                    "volume": item["volume"],
                    "entry_price": item["price_open"],
                    "stop_loss": item["sl"],
                    "take_profit": item["tp"],
                    "strategy": "external",
                    "source": "mt5",
                    "is_external": 1,
                    "status": "open",
                    "opened_at": now_iso,
                }
            )
        position_rows = [
            {
                "ticket": int(item.get("ticket", 0)),
                "symbol": str(item.get("symbol", "")),
                "direction": "buy" if int(item.get("type", 0)) == 0 else "sell",
                "volume": float(item.get("volume", 0.0)),
                "entry": float(item.get("price_open", 0.0)),
                "sl": float(item.get("sl", 0.0)),
                "tp": float(item.get("tp", 0.0)),
                "profit": float(item.get("profit", 0.0)),
            }
            for item in positions
        ]
        if not position_rows:
            position_rows = [{"ticket": "-", "symbol": "-", "direction": "-", "volume": 0.0, "entry": 0.0, "sl": 0.0, "tp": 0.0, "profit": 0.0}]
        logger.info("📋 Open positions table:\n%s", self._format_table(position_rows, ["ticket", "symbol", "direction", "volume", "entry", "sl", "tp", "profit"]))

    def _monitor_account(self) -> None:
        account_info = self.connector.account_info()
        if not account_info:
            logger.warning("⚠️ Account monitor | account info unavailable")
            return
        previous = self._last_account_snapshot
        delta_balance = 0.0
        delta_equity = 0.0
        if previous:
            delta_balance = float(account_info.get("balance", 0.0)) - float(previous.get("balance", 0.0))
            delta_equity = float(account_info.get("equity", 0.0)) - float(previous.get("equity", 0.0))
        self._last_account_snapshot = account_info
        self._last_account_change = {"delta_balance": delta_balance, "delta_equity": delta_equity}
        self._equity_curve.append(
            {
                "datetime": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                "equity": float(account_info.get("equity", 0.0)),
                "balance": float(account_info.get("balance", 0.0)),
            }
        )
        if len(self._equity_curve) > 2000:
            self._equity_curve = self._equity_curve[-2000:]

        logger.info(
            "💰 Account monitor | login=%s balance=%.2f equity=%.2f margin=%.2f free_margin=%.2f",
            account_info.get("login"),
            float(account_info.get("balance", 0.0)),
            float(account_info.get("equity", 0.0)),
            float(account_info.get("margin", 0.0)),
            float(account_info.get("free_margin", 0.0)),
        )
        account_row = {
            "login": account_info.get("login", "-"),
            "balance": round(float(account_info.get("balance", 0.0)), 2),
            "equity": round(float(account_info.get("equity", 0.0)), 2),
            "d_balance": round(delta_balance, 2),
            "d_equity": round(delta_equity, 2),
            "margin": round(float(account_info.get("margin", 0.0)), 2),
            "free_margin": round(float(account_info.get("free_margin", 0.0)), 2),
            "open_positions": len(self._last_positions),
        }
        logger.info(
            "🧾 Account table:\n%s",
            self._format_table([account_row], ["login", "balance", "equity", "d_balance", "d_equity", "margin", "free_margin", "open_positions"]),
        )

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
