from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bot.src.application.services.gold_live_service import GoldLiveService
from bot.src.application.services.gold_runner import GoldRunner
from bot.src.infrastructure.charting.live_plot import LiveChartRenderer
from bot.src.infrastructure.config.settings import load_gold_settings
from bot.src.infrastructure.ctrader.connector import GoldCTraderConnector
from bot.src.infrastructure.persistence.sqlite_store import GoldPositionStore


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


def test_ctrader_connector_requires_credentials() -> None:
    settings = SimpleNamespace(
        ctrader_client_id="",
        ctrader_client_secret="",
        ctrader_access_token="",
        ctrader_refresh_token="",
        ctrader_account_id=0,
        ctrader_host="demo",
        ctrader_request_timeout_seconds=3.0,
        ctrader_connect_timeout_seconds=3.0,
    )
    connector = GoldCTraderConnector(settings)

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


def test_connector_get_rates_normalizes_trendbars(monkeypatch) -> None:
    settings = SimpleNamespace(
        ctrader_client_id="id",
        ctrader_client_secret="secret",
        ctrader_access_token="token",
        ctrader_refresh_token="refresh",
        ctrader_account_id=123,
        ctrader_host="demo",
        ctrader_request_timeout_seconds=3.0,
        ctrader_connect_timeout_seconds=3.0,
    )
    connector = GoldCTraderConnector(settings)
    connector._connected = True
    connector._account_id = 123
    connector._symbols_by_name = {"XAUUSD": {"symbolId": 987, "symbolName": "XAUUSD"}}

    class FakeTrendbar:
        def __init__(self) -> None:
            self.utcTimestampInMinutes = 1710000000 // 60
            self.low = 230000000
            self.deltaOpen = 100000
            self.deltaClose = 120000
            self.deltaHigh = 180000
            self.volume = 42

    class FakeResponse:
        def __init__(self) -> None:
            self.trendbar = [FakeTrendbar()]

    monkeypatch.setattr(connector, "_send_and_extract", lambda request, timeout=None: FakeResponse())

    rates = connector.get_rates("XAUUSD", "M15", count=1)

    assert len(rates) == 1
    assert rates[0]["symbol"] == "XAUUSD"
    assert rates[0]["open"] == 2301.0
    assert rates[0]["close"] == 2301.2
    assert rates[0]["high"] == 2301.8
    assert rates[0]["low"] == 2300.0
    assert rates[0]["tick_volume"] == 42


def test_connector_resolves_gold_alias_to_broker_symbol() -> None:
    settings = SimpleNamespace(
        ctrader_client_id="id",
        ctrader_client_secret="secret",
        ctrader_access_token="token",
        ctrader_refresh_token="refresh",
        ctrader_account_id=123,
        ctrader_host="demo",
        ctrader_request_timeout_seconds=3.0,
        ctrader_connect_timeout_seconds=3.0,
    )
    connector = GoldCTraderConnector(settings)
    connector._connected = True
    connector._symbols_by_name = {
        "EURUSD": {"symbolId": 1, "symbolName": "EURUSD"},
        "XAUUSD": {"symbolId": 2, "symbolName": "XAUUSD"},
        "GBPUSD": {"symbolId": 3, "symbolName": "GBPUSD"},
    }

    assert connector.resolve_symbol("GOLD") == "XAUUSD"


def test_connector_resolves_gold_alias_with_suffix() -> None:
    settings = SimpleNamespace(
        ctrader_client_id="id",
        ctrader_client_secret="secret",
        ctrader_access_token="token",
        ctrader_refresh_token="refresh",
        ctrader_account_id=123,
        ctrader_host="demo",
        ctrader_request_timeout_seconds=3.0,
        ctrader_connect_timeout_seconds=3.0,
    )
    connector = GoldCTraderConnector(settings)
    connector._connected = True
    connector._symbols_by_name = {
        "EURUSD": {"symbolId": 1, "symbolName": "EURUSD"},
        "XAUUSD.A": {"symbolId": 2, "symbolName": "XAUUSD.A"},
        "GBPUSD": {"symbolId": 3, "symbolName": "GBPUSD"},
    }

    assert connector.resolve_symbol("GOLD") == "XAUUSD.A"


def test_live_service_requires_ctrader_connection(tmp_path: Path) -> None:
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
