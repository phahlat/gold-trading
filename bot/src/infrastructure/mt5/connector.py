from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


_TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

_SYMBOL_ALIASES = {
    "GOLD": ["XAUUSD", "XAUUSD.", "XAUUSDm", "XAUUSDmicro", "XAUUSDpro"],
    "XAUUSD": ["XAUUSD", "GOLD", "XAUUSD.", "XAUUSDm", "XAUUSDmicro", "XAUUSDpro"],
}


_FILLING_MODE_LABELS = {
    mt5.ORDER_FILLING_FOK: "FOK",
    getattr(mt5, "ORDER_FILLING_IOC", -1): "IOC",
    getattr(mt5, "ORDER_FILLING_RETURN", -1): "RETURN",
}

_SYMBOL_FILLING_TO_ORDER_FILLING = {
    1: mt5.ORDER_FILLING_FOK,
    2: getattr(mt5, "ORDER_FILLING_IOC", mt5.ORDER_FILLING_FOK),
    4: getattr(mt5, "ORDER_FILLING_RETURN", mt5.ORDER_FILLING_FOK),
}


@dataclass
class Mt5AccountInfo:
    login: int
    password: str
    server: str
    path: str


class GoldMt5Connector:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._initialized = False
        self._connected = False

    def connect(self) -> bool:
        if self._connected:
            return True

        if not self.settings.mt5_login or not self.settings.mt5_password or not self.settings.mt5_server:
            logger.warning("⚠️ MT5 credentials are incomplete; skipping live connection")
            return False

        try:
            initialized = mt5.initialize(path=self.settings.mt5_path or None, login=int(self.settings.mt5_login), password=self.settings.mt5_password, server=self.settings.mt5_server)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("❌ MT5 initialize failed: %s", exc)
            return False

        if not initialized:
            logger.error("❌ MT5 initialize returned false | last_error=%s", mt5.last_error())
            return False

        self._initialized = True
        self._connected = True
        logger.info("✅ MT5 connected | login=%s server=%s", self.settings.mt5_login, self.settings.mt5_server)
        return True

    def disconnect(self) -> None:
        if self._initialized:
            mt5.shutdown()
        self._initialized = False
        self._connected = False

    def account_info(self) -> dict[str, Any] | None:
        if not self._connected:
            return None
        try:
            info = mt5.account_info()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("MT5 account_info failed: %s", exc)
            return None
        if info is None:
            return None
        return {
            "login": info.login,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "leverage": float(getattr(info, "leverage", 0.0)),
            "currency": info.currency,
        }

    def broker_symbols(self) -> list[str]:
        if not self._connected:
            return []
        try:
            symbols = mt5.symbols_get()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("MT5 symbols_get failed: %s", exc)
            return []
        if not symbols:
            return []

        names: list[str] = []
        for item in symbols:
            name = str(getattr(item, "name", "")).strip()
            if name:
                names.append(name)
        return sorted(set(names))

    def resolve_symbol(self, requested_symbol: str) -> str | None:
        if not self._connected:
            return None

        requested = requested_symbol.strip().upper()
        available = self.broker_symbols()
        if not available:
            return None

        lookup = {name.upper(): name for name in available}
        alias_candidates = [requested]
        alias_candidates.extend(_SYMBOL_ALIASES.get(requested, []))
        normalized_candidates = [candidate.upper() for candidate in alias_candidates]

        # For GOLD aliases, prefer canonical XAUUSD variants when broker supports them.
        if requested == "GOLD":
            canonical_priority = ["XAUUSD", "XAUUSD.", "XAUUSDM", "XAUUSDMICRO", "XAUUSDPRO"]
            for candidate in canonical_priority:
                if candidate in lookup:
                    return lookup[candidate]

        if requested in lookup:
            return lookup[requested]

        for candidate in normalized_candidates:
            if candidate in lookup:
                return lookup[candidate]

        # Handle broker suffixes/prefixes like XAUUSD.a, XAUUSDm, etc.
        fuzzy_matches: list[str] = []
        for broker_symbol in available:
            broker_upper = broker_symbol.upper()
            if any(candidate in broker_upper for candidate in normalized_candidates):
                fuzzy_matches.append(broker_symbol)
        if fuzzy_matches:
            fuzzy_matches.sort(key=len)
            return fuzzy_matches[0]

        return None

    def symbol_info(self, symbol: str) -> dict[str, Any] | None:
        if not self._connected:
            return None
        try:
            if not mt5.symbol_select(symbol, True):
                logger.error("MT5 failed to select symbol %s | last_error=%s", symbol, mt5.last_error())
                return None
            info = mt5.symbol_info(symbol)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("MT5 symbol_info failed for %s: %s", symbol, exc)
            return None
        if info is None:
            return None
        return {
            "symbol": symbol,
            "digits": int(info.digits),
            "point": float(info.point),
            "trade_tick_size": float(getattr(info, "trade_tick_size", 0.0)),
            "trade_tick_value": float(getattr(info, "trade_tick_value", 0.0)),
            "trade_contract_size": float(getattr(info, "trade_contract_size", 0.0)),
            "margin_initial": float(getattr(info, "margin_initial", 0.0)),
            "margin_maintenance": float(getattr(info, "margin_maintenance", 0.0)),
            "volume_min": float(getattr(info, "volume_min", 0.01)),
            "volume_max": float(getattr(info, "volume_max", 100.0)),
            "volume_step": float(getattr(info, "volume_step", 0.01)),
            "filling_mode": int(getattr(info, "filling_mode", -1)),
            "trade_exemode": int(getattr(info, "trade_exemode", -1)),
        }

    def _resolve_allowed_filling_mode(self, symbol_meta: dict[str, Any]) -> tuple[int, str]:
        raw_mode = int(symbol_meta.get("filling_mode", -1))

        # Many brokers expose allowed filling as bit flags: 1=FOK, 2=IOC, 4=RETURN.
        preferred_symbol_flags = [2, 4, 1]
        if raw_mode > 0:
            for flag in preferred_symbol_flags:
                if raw_mode & flag:
                    order_filling = _SYMBOL_FILLING_TO_ORDER_FILLING.get(flag, mt5.ORDER_FILLING_FOK)
                    return int(order_filling), _FILLING_MODE_LABELS.get(int(order_filling), str(order_filling))

        # Some integrations may expose order filling enum directly.
        if raw_mode in _FILLING_MODE_LABELS:
            return raw_mode, _FILLING_MODE_LABELS.get(raw_mode, str(raw_mode))

        # Defensive default if broker metadata is missing or unexpected.
        return mt5.ORDER_FILLING_FOK, _FILLING_MODE_LABELS.get(mt5.ORDER_FILLING_FOK, str(mt5.ORDER_FILLING_FOK))

    def current_price(self, symbol: str, direction: str) -> float | None:
        if not self._connected:
            return None
        try:
            tick = mt5.symbol_info_tick(symbol)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("MT5 symbol_info_tick failed for %s: %s", symbol, exc)
            return None
        if tick is None:
            return None
        if direction.lower() == "buy":
            return float(tick.ask)
        return float(tick.bid)

    def open_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if not self._connected:
            return []
        try:
            positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("MT5 positions_get failed: %s", exc)
            return []
        if not positions:
            return []

        normalized: list[dict[str, Any]] = []
        for item in positions:
            values = item._asdict() if hasattr(item, "_asdict") else {}
            normalized.append(
                {
                    "ticket": int(values.get("ticket", getattr(item, "ticket", 0))),
                    "symbol": str(values.get("symbol", getattr(item, "symbol", ""))),
                    "type": int(values.get("type", getattr(item, "type", 0))),
                    "volume": float(values.get("volume", getattr(item, "volume", 0.0))),
                    "price_open": float(values.get("price_open", getattr(item, "price_open", 0.0))),
                    "sl": float(values.get("sl", getattr(item, "sl", 0.0))),
                    "tp": float(values.get("tp", getattr(item, "tp", 0.0))),
                    "profit": float(values.get("profit", getattr(item, "profit", 0.0))),
                    "time": int(values.get("time", getattr(item, "time", 0))),
                    "comment": str(values.get("comment", getattr(item, "comment", ""))),
                }
            )
        return normalized

    def place_market_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        magic_number: int,
        comment: str,
    ) -> dict[str, Any]:
        if not self._connected:
            return {"ok": False, "reason": "not_connected"}

        symbol_meta = self.symbol_info(symbol)
        if symbol_meta is None:
            return {"ok": False, "reason": "symbol_unavailable"}

        price = self.current_price(symbol, direction)
        if price is None:
            return {"ok": False, "reason": "tick_unavailable"}

        filling_mode, filling_label = self._resolve_allowed_filling_mode(symbol_meta)
        logger.info(
            "🧩 Filling mode selected | symbol=%s raw_filling_mode=%s trade_exemode=%s selected=%s",
            symbol,
            symbol_meta.get("filling_mode"),
            symbol_meta.get("trade_exemode"),
            filling_label,
        )

        order_type = mt5.ORDER_TYPE_BUY if direction.lower() == "buy" else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": float(price),
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "deviation": 20,
            "magic": int(magic_number),
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": int(filling_mode),
        }

        account = self.account_info() or {}
        leverage = float(account.get("leverage", 0.0) or 0.0)
        contract_size = float(symbol_meta.get("trade_contract_size", 0.0) or 0.0)
        notional = float(price) * float(volume) * contract_size if contract_size > 0 else 0.0
        approx_margin_from_leverage = (notional / leverage) if leverage > 0 and notional > 0 else 0.0
        logger.info(
            "📐 Margin estimate inputs | symbol=%s direction=%s volume=%.2f price=%.5f tick_size=%.5f tick_value=%.5f contract_size=%.2f leverage=%.0f notional=%.2f approx_margin=%.2f balance=%.2f equity=%.2f free_margin=%.2f margin_initial=%.2f margin_maintenance=%.2f vol_min=%.2f vol_max=%.2f vol_step=%.2f",
            symbol,
            direction,
            float(volume),
            float(price),
            float(symbol_meta.get("trade_tick_size", 0.0) or 0.0),
            float(symbol_meta.get("trade_tick_value", 0.0) or 0.0),
            contract_size,
            leverage,
            notional,
            approx_margin_from_leverage,
            float(account.get("balance", 0.0) or 0.0),
            float(account.get("equity", 0.0) or 0.0),
            float(account.get("free_margin", 0.0) or 0.0),
            float(symbol_meta.get("margin_initial", 0.0) or 0.0),
            float(symbol_meta.get("margin_maintenance", 0.0) or 0.0),
            float(symbol_meta.get("volume_min", 0.0) or 0.0),
            float(symbol_meta.get("volume_max", 0.0) or 0.0),
            float(symbol_meta.get("volume_step", 0.0) or 0.0),
        )
        logger.info(
            "📤 MT5 order_send request | symbol=%s direction=%s volume=%.2f price=%.5f sl=%.5f tp=%.5f filling=%s",
            symbol,
            direction,
            float(volume),
            float(price),
            float(stop_loss),
            float(take_profit),
            filling_label,
        )

        try:
            result = mt5.order_send(request)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("❌ MT5 order_send failed for %s: %s", symbol, exc)
            return {"ok": False, "reason": "exception", "error": str(exc)}

        if result is None:
            return {"ok": False, "reason": "no_result", "last_error": mt5.last_error()}

        result_dict = result._asdict() if hasattr(result, "_asdict") else {}
        retcode = int(result_dict.get("retcode", getattr(result, "retcode", 0)))
        order_ticket = int(result_dict.get("order", getattr(result, "order", 0)))
        deal_ticket = int(result_dict.get("deal", getattr(result, "deal", 0)))
        if retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                "❌ MT5 order rejected | symbol=%s direction=%s retcode=%s filling=%s details=%s",
                symbol,
                direction,
                retcode,
                filling_label,
                result_dict,
            )
            return {"ok": False, "reason": "rejected", "retcode": retcode, "details": result_dict, "filling": filling_label}

        logger.info(
            "✅ MT5 order accepted | symbol=%s direction=%s retcode=%s order=%s deal=%s filling=%s",
            symbol,
            direction,
            retcode,
            order_ticket,
            deal_ticket,
            filling_label,
        )

        return {
            "ok": True,
            "retcode": retcode,
            "order": order_ticket,
            "deal": deal_ticket,
            "price": float(result_dict.get("price", price)),
            "volume": float(result_dict.get("volume", volume)),
            "filling": filling_label,
        }

    def session_trade_performance(
        self,
        started_at: datetime,
        ended_at: datetime | None = None,
        symbol: str | None = None,
        magic_number: int | None = None,
        comment_prefix: str | None = None,
    ) -> dict[str, Any]:
        if not self._connected:
            return {"closed_trades": 0, "wins": 0, "losses": 0, "breakeven": 0, "net_profit": 0.0}

        window_end = ended_at or datetime.utcnow()
        try:
            deals = mt5.history_deals_get(started_at, window_end)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("MT5 history_deals_get failed: %s", exc)
            return {"closed_trades": 0, "wins": 0, "losses": 0, "breakeven": 0, "net_profit": 0.0}

        if not deals:
            return {"closed_trades": 0, "wins": 0, "losses": 0, "breakeven": 0, "net_profit": 0.0}

        out_entries = {
            int(getattr(mt5, "DEAL_ENTRY_OUT", 1)),
            int(getattr(mt5, "DEAL_ENTRY_OUT_BY", 3)),
        }
        closed_trades = 0
        wins = 0
        losses = 0
        breakeven = 0
        net_profit = 0.0

        for deal in deals:
            data = deal._asdict() if hasattr(deal, "_asdict") else {}
            if symbol and str(data.get("symbol", "")).upper() != symbol.upper():
                continue
            if magic_number is not None and int(data.get("magic", -1)) != int(magic_number):
                continue
            if comment_prefix and not str(data.get("comment", "")).startswith(comment_prefix):
                continue

            entry_kind = int(data.get("entry", -1))
            if entry_kind not in out_entries:
                continue

            profit = float(data.get("profit", 0.0))
            commission = float(data.get("commission", 0.0))
            swap = float(data.get("swap", 0.0))
            fee = float(data.get("fee", 0.0))
            total = profit + commission + swap + fee

            closed_trades += 1
            net_profit += total
            if total > 0:
                wins += 1
            elif total < 0:
                losses += 1
            else:
                breakeven += 1

        return {
            "closed_trades": closed_trades,
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "net_profit": net_profit,
        }

    def get_symbol_ticks(self, symbol: str) -> list[dict[str, Any]]:
        if not self._connected:
            return []
        try:
            ticks = mt5.copy_ticks_from(symbol, mt5.COPY_TICKS_INFO, 0, 1)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("MT5 ticks fetch failed for %s: %s", symbol, exc)
            return []
        return [
            {
                "time": int(item.time),
                "bid": float(item.bid),
                "ask": float(item.ask),
                "last": float(item.last),
            }
            for item in ticks
        ]

    def get_rates(self, symbol: str, timeframe: str, count: int = 200) -> list[dict[str, Any]]:
        if not self._connected:
            return []
        mt5_timeframe = _TIMEFRAME_MAP.get(timeframe.upper(), mt5.TIMEFRAME_M15)
        try:
            copy_from_pos = getattr(mt5, "copy_rates_from_pos", None)
            rates = None
            if callable(copy_from_pos):
                rates = copy_from_pos(symbol, mt5_timeframe, 0, count)

            if rates is None or len(rates) == 0:
                # Fallback for brokers/terminals where position-based pull is unavailable.
                copy_from = getattr(mt5, "copy_rates_from", None)
                if callable(copy_from):
                    rates = copy_from(symbol, mt5_timeframe, datetime.now(), count)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("MT5 rates fetch failed for %s: %s", symbol, exc)
            return []
        if rates is None:
            return []

        def _value(item: Any, key: str, fallback_index: int, default: Any) -> Any:
            try:
                if isinstance(item, dict):
                    return item.get(key, default)
                if hasattr(item, "dtype") and getattr(item.dtype, "names", None) and key in item.dtype.names:
                    return item[key]
                if hasattr(item, "_asdict"):
                    return item._asdict().get(key, default)
                if isinstance(item, tuple) and len(item) > fallback_index:
                    return item[fallback_index]
                return getattr(item, key, default)
            except Exception:
                return default

        normalized: list[dict[str, Any]] = []
        for item in rates:
            normalized.append(
                {
                    "time": int(_value(item, "time", 0, 0)),
                    "symbol": symbol,
                    "open": float(_value(item, "open", 1, 0.0)),
                    "high": float(_value(item, "high", 2, 0.0)),
                    "low": float(_value(item, "low", 3, 0.0)),
                    "close": float(_value(item, "close", 4, 0.0)),
                    "tick_volume": int(_value(item, "tick_volume", 5, 0)),
                }
            )
        return normalized
