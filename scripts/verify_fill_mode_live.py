from __future__ import annotations

import time
from datetime import datetime

import MetaTrader5 as mt5

from bot.src.infrastructure.config.settings import load_gold_settings
from bot.src.infrastructure.mt5.connector import GoldMt5Connector


def main() -> int:
    settings = load_gold_settings("gold_bot/.env")
    connector = GoldMt5Connector(settings)
    if not connector.connect():
        print("VERIFY_RESULT connect_failed")
        return 1

    requested = settings.symbols[0] if settings.symbols else "XAUUSD"
    symbol = connector.resolve_symbol(requested)
    if not symbol:
        print(f"VERIFY_RESULT symbol_resolution_failed requested={requested}")
        connector.disconnect()
        return 1

    meta = connector.symbol_info(symbol)
    if not meta:
        print(f"VERIFY_RESULT symbol_info_failed symbol={symbol}")
        connector.disconnect()
        return 1

    fill_mode, fill_label = connector._resolve_allowed_filling_mode(meta)
    print(
        f"SELECTED_FILLING symbol={symbol} raw_filling_mode={meta.get('filling_mode')} "
        f"trade_exemode={meta.get('trade_exemode')} selected={fill_label}"
    )

    volume = float(meta.get("volume_min", 0.01))
    entry = connector.current_price(symbol, "buy")
    if entry is None:
        print(f"VERIFY_RESULT no_entry_quote symbol={symbol}")
        connector.disconnect()
        return 1

    sl = float(entry) - (settings.stop_loss_pips * settings.pip_size)
    tp = float(entry) + (settings.take_profit_pips * settings.pip_size)
    open_comment = f"{settings.trade_comment_prefix}:fillmode-test:{datetime.utcnow().strftime('%H%M%S')}"
    open_result = connector.place_market_order(
        symbol=symbol,
        direction="buy",
        volume=volume,
        stop_loss=sl,
        take_profit=tp,
        magic_number=settings.trade_magic_number,
        comment=open_comment,
    )
    print(f"OPEN_RESULT {open_result}")

    # Try to close immediately so the verification does not leave exposure.
    time.sleep(1.0)
    positions = mt5.positions_get(symbol=symbol) or []
    target = None
    for pos in positions:
        values = pos._asdict() if hasattr(pos, "_asdict") else {}
        if int(values.get("magic", 0)) == int(settings.trade_magic_number) and str(values.get("comment", "")) == open_comment:
            target = values
            break

    if not target:
        print("CLOSE_RESULT no_matching_position_found")
        connector.disconnect()
        return 0

    close_direction = "sell" if int(target.get("type", 0)) == int(mt5.POSITION_TYPE_BUY) else "buy"
    close_price = connector.current_price(symbol, close_direction)
    if close_price is None:
        print("CLOSE_RESULT no_close_quote")
        connector.disconnect()
        return 1

    close_type = mt5.ORDER_TYPE_SELL if close_direction == "sell" else mt5.ORDER_TYPE_BUY
    close_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "position": int(target.get("ticket", 0)),
        "volume": float(target.get("volume", volume)),
        "type": close_type,
        "price": float(close_price),
        "deviation": 20,
        "magic": int(settings.trade_magic_number),
        "comment": f"{settings.trade_comment_prefix}:fillmode-close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": int(fill_mode),
    }
    close_result = mt5.order_send(close_request)
    close_payload = close_result._asdict() if hasattr(close_result, "_asdict") else close_result
    print(f"CLOSE_RESULT {close_payload}")

    connector.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
