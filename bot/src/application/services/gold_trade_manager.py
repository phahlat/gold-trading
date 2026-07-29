from __future__ import annotations

import math
from typing import Any

from gold_bot.src.domain.services.gold_strategies import SignalCandidate
from gold_bot.src.infrastructure.config.settings import GoldSettings


class GoldTradeManager:
    def __init__(self, settings: GoldSettings) -> None:
        self.settings = settings

    def build_ladder(self, candidate: SignalCandidate) -> list[dict[str, Any]]:
        if not self.settings.enable_multi_entry:
            return [self._build_trade(candidate, 1)]

        entries: list[dict[str, Any]] = []
        for level in range(1, self.settings.ladder_entries + 1):
            entries.append(self._build_trade(candidate, level))
        return entries

    def _build_trade(self, candidate: SignalCandidate, level: int) -> dict[str, Any]:
        multiplier = self.settings.ladder_step_ratio ** (level - 1)
        price = candidate.price
        if candidate.direction == "buy":
            price = price + (self.settings.stop_loss_pips * self.settings.pip_size) * multiplier
        else:
            price = price - (self.settings.stop_loss_pips * self.settings.pip_size) * multiplier

        rr_ratio = self.settings.ladder_rr_ratio if level == 1 else max(1.0, self.settings.ladder_rr_ratio - (level - 1) * 0.1)
        take_profit_pips = self.settings.take_profit_pips * rr_ratio
        return {
            "strategy": candidate.strategy,
            "direction": candidate.direction,
            "reason": candidate.reason,
            "entry_price": round(price, 5),
            "take_profit_pips": round(take_profit_pips, 2),
            "stop_loss_pips": round(self.settings.stop_loss_pips, 2),
            "level": level,
        }

    def build_order_request(
        self,
        candidate: SignalCandidate,
        symbol: str,
        entry_price: float,
        account_info: dict[str, Any] | None,
        symbol_info: dict[str, Any] | None,
        stop_loss_pips: float | None = None,
        take_profit_pips: float | None = None,
        level: int = 1,
    ) -> dict[str, Any]:
        pip_size = float(getattr(self.settings, "pip_size", 0.01))
        stop_pips = float(stop_loss_pips) if stop_loss_pips is not None else float(self.settings.stop_loss_pips)
        take_pips = float(take_profit_pips) if take_profit_pips is not None else float(self.settings.take_profit_pips)
        stop_distance = stop_pips * pip_size
        take_distance = take_pips * pip_size
        if candidate.direction == "buy":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + take_distance
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - take_distance

        volume = self._calculate_volume(account_info, symbol_info, stop_distance)
        return {
            "symbol": symbol,
            "direction": candidate.direction,
            "volume": volume,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "strategy": candidate.strategy,
            "reason": candidate.reason,
            "level": int(level),
        }

    def _calculate_volume(self, account_info: dict[str, Any] | None, symbol_info: dict[str, Any] | None, stop_distance: float) -> float:
        default_volume = 0.1
        fixed_lot_size = float(getattr(self.settings, "fixed_lot_size", 0.0))
        if fixed_lot_size > 0:
            return self._normalize_volume(fixed_lot_size, symbol_info)

        if not account_info or not symbol_info or stop_distance <= 0:
            return default_volume

        balance = float(account_info.get("equity") or account_info.get("balance") or 0.0)
        if balance <= 0:
            return default_volume

        risk_amount = balance * (float(self.settings.risk_percent) / 100.0)
        tick_size = float(symbol_info.get("trade_tick_size") or 0.0)
        tick_value = float(symbol_info.get("trade_tick_value") or 0.0)
        if tick_size <= 0 or tick_value <= 0:
            return default_volume

        stop_ticks = stop_distance / tick_size
        if stop_ticks <= 0:
            return default_volume

        raw_volume = risk_amount / (stop_ticks * tick_value)
        return self._normalize_volume(raw_volume, symbol_info)

    def _normalize_volume(self, raw_volume: float, symbol_info: dict[str, Any] | None) -> float:
        if not symbol_info:
            return round(max(0.01, raw_volume), 2)
        min_volume = float(symbol_info.get("volume_min") or 0.01)
        max_volume = float(symbol_info.get("volume_max") or 100.0)
        step = float(symbol_info.get("volume_step") or 0.01)
        if step <= 0:
            step = 0.01
        stepped = math.floor(raw_volume / step) * step
        bounded = max(min_volume, min(max_volume, stepped))
        return round(bounded, 2)
