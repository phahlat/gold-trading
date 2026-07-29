from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from gold_bot.src.application.services.gold_live_service import GoldLiveService
from gold_bot.src.application.services.gold_runner import GoldRunner
from gold_bot.src.infrastructure.charting.live_plot import LiveChartRenderer
from gold_bot.src.infrastructure.config.settings import load_gold_settings
from gold_bot.src.infrastructure.mt5 import connector as mt5_connector
from gold_bot.src.infrastructure.mt5.connector import GoldMt5Connector
from gold_bot.src.infrastructure.persistence.sqlite_store import GoldPositionStore


def _copy_example_env(tmp_path: Path) -> str:
    env_path = tmp_path / ".env"
    env_source = None
    for candidate in [Path("bot/.env.example"), Path("gold_bot/.env.example"), Path(".env.example")]:
        if candidate.exists():
            env_source = candidate
            break
    if env_source is None:
        raise FileNotFoundError("Unable to locate example environment file")
    env_path.write_text(env_source.read_text(encoding="utf-8"), encoding="utf-8")
    return str(env_path)


def test_mt5_connector_requires_credentials() -> None:
    settings = SimpleNamespace(mt5_login=0, mt5_password="", mt5_server="", mt5_path="")
    connector = GoldMt5Connector(settings)

    assert connector.connect() is False


def test_position_store_persists_and_closes_positions(tmp_path: Path) -> None:
    store = GoldPositionStore(tmp_path / "positions.sqlite3")

    store.upsert_position(
        {
            "position_key": "bot:ticket-1:XAUUSD",
            "ticket": 1,
            "symbol": "XAUUSD",
            "direction": "buy",
            "volume": 0.1,
            "entry_price": 2345.0,
            "stop_loss": 2335.0,
            "take_profit": 2365.0,
            "strategy": "trend_following",
            "source": "bot",
            "is_external": 0,
            "status": "open",
            "opened_at": "2026-07-28T12:00:00",
        }
    )

    open_positions = store.list_positions(status="open")
    assert len(open_positions) == 1

    store.mark_closed("bot:ticket-1:XAUUSD", 2350.0, "2026-07-28T12:05:00")
    closed_positions = store.list_positions(status="closed")
    assert len(closed_positions) == 1
    assert closed_positions[0]["close_price"] == 2350.0


def test_connector_get_rates_uses_python_datetime(monkeypatch) -> None:
    settings = SimpleNamespace(mt5_login=316016904, mt5_password="demo", mt5_server="demo", mt5_path="")
    connector = GoldMt5Connector(settings)
    connector._connected = True

    class FakeRate:
        def __init__(self) -> None:
            self.time = 1710000000
            self.open = 1.0
            self.high = 1.1
            self.low = 0.9
            self.close = 1.05
            self.tick_volume = 10

    fake_mt5 = SimpleNamespace(
        TIMEFRAME_M15=1,
        copy_rates_from=lambda symbol, timeframe, start, count: [FakeRate()],
    )
    monkeypatch.setattr(mt5_connector, "mt5", fake_mt5)

    rates = connector.get_rates("XAUUSD", "M15", count=1)

    assert len(rates) == 1
    assert rates[0]["symbol"] == "XAUUSD"


def test_connector_resolves_gold_alias_to_broker_symbol(monkeypatch) -> None:
    settings = SimpleNamespace(mt5_login=316016904, mt5_password="demo", mt5_server="demo", mt5_path="")
    connector = GoldMt5Connector(settings)
    connector._connected = True

    class FakeSymbol:
        def __init__(self, name: str) -> None:
            self.name = name

    fake_mt5 = SimpleNamespace(
        symbols_get=lambda: [FakeSymbol("EURUSD"), FakeSymbol("XAUUSD"), FakeSymbol("GBPUSD")],
    )
    monkeypatch.setattr(mt5_connector, "mt5", fake_mt5)

    assert connector.resolve_symbol("GOLD") == "XAUUSD"


def test_connector_resolves_gold_alias_with_suffix(monkeypatch) -> None:
    settings = SimpleNamespace(mt5_login=316016904, mt5_password="demo", mt5_server="demo", mt5_path="")
    connector = GoldMt5Connector(settings)
    connector._connected = True

    class FakeSymbol:
        def __init__(self, name: str) -> None:
            self.name = name

    fake_mt5 = SimpleNamespace(
        symbols_get=lambda: [FakeSymbol("EURUSD"), FakeSymbol("XAUUSD.a"), FakeSymbol("GBPUSD")],
    )
    monkeypatch.setattr(mt5_connector, "mt5", fake_mt5)

    assert connector.resolve_symbol("GOLD") == "XAUUSD.a"


def test_live_service_requires_mt5_connection(tmp_path: Path) -> None:
    settings = load_gold_settings(_copy_example_env(tmp_path))
    runner = GoldRunner(settings)

    class FakeConnector:
        def connect(self) -> bool:
            return False

        def disconnect(self) -> None:
            return None

    service = GoldLiveService(
        settings=settings,
        runner=runner,
        connector=FakeConnector(),
        position_store=GoldPositionStore(tmp_path / "positions.sqlite3"),
        chart_renderer=LiveChartRenderer(
            output_dir=tmp_path,
            interactive=False,
            chart_width=14.0,
            chart_height=8.0,
            max_lower_candles=120,
            max_higher_candles=90,
        ),
    )

    assert service.run() == 1
