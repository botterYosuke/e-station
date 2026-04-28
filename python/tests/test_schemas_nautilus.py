"""N1.1 (Python): pydantic round-trip + Literal 制約のテスト.

- Command 系: StartEngine / StopEngine / LoadReplayData
- Event  系: EngineStarted / EngineStopped / ReplayDataLoaded /
             PositionOpened / PositionClosed
- Literal 制約違反 (engine="Bogus" / granularity="Bogus") で ValidationError
- SCHEMA_MINOR == 4
"""

from __future__ import annotations

import orjson
import pytest
from pydantic import ValidationError

from engine import schemas as s


def _roundtrip(model_cls, data: dict) -> dict:
    obj = model_cls.model_validate(data)
    return orjson.loads(orjson.dumps(obj.model_dump(mode="json")))


# ── Schema version ──────────────────────────────────────────────────────────


def test_schema_minor_is_4_for_nautilus() -> None:
    assert s.SCHEMA_MINOR == 4
    assert s.SCHEMA_MAJOR == 2


# ── Sub-models ──────────────────────────────────────────────────────────────


def test_engine_start_config_roundtrip() -> None:
    data = {
        "instrument_id": "1301.TSE",
        "start_date": "2024-01-04",
        "end_date": "2024-01-31",
        "initial_cash": "1000000",
        "granularity": "Trade",
    }
    out = _roundtrip(s.EngineStartConfig, data)
    assert out["granularity"] == "Trade"
    assert out["instrument_id"] == "1301.TSE"


def test_engine_start_config_rejects_unknown_granularity() -> None:
    data = {
        "instrument_id": "1301.TSE",
        "start_date": "2024-01-04",
        "end_date": "2024-01-31",
        "initial_cash": "1000000",
        "granularity": "Bogus",
    }
    with pytest.raises(ValidationError):
        s.EngineStartConfig.model_validate(data)


# ── Commands ────────────────────────────────────────────────────────────────


def test_start_engine_roundtrip() -> None:
    data = {
        "op": "StartEngine",
        "request_id": "req-1",
        "engine": "Backtest",
        "strategy_id": "strat-001",
        "config": {
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-31",
            "initial_cash": "1000000",
            "granularity": "Minute",
        },
    }
    out = _roundtrip(s.StartEngine, data)
    assert out["engine"] == "Backtest"
    assert out["config"]["granularity"] == "Minute"


def test_start_engine_rejects_unknown_engine_kind() -> None:
    data = {
        "op": "StartEngine",
        "request_id": "req-1",
        "engine": "Bogus",
        "strategy_id": "strat-001",
        "config": {
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-31",
            "initial_cash": "1000000",
            "granularity": "Trade",
        },
    }
    with pytest.raises(ValidationError):
        s.StartEngine.model_validate(data)


def test_stop_engine_roundtrip() -> None:
    data = {"op": "StopEngine", "request_id": "req-2", "strategy_id": "strat-001"}
    out = _roundtrip(s.StopEngine, data)
    assert out["strategy_id"] == "strat-001"


def test_load_replay_data_roundtrip() -> None:
    data = {
        "op": "LoadReplayData",
        "request_id": "req-3",
        "instrument_id": "1301.TSE",
        "start_date": "2024-01-04",
        "end_date": "2024-01-31",
        "granularity": "Daily",
    }
    out = _roundtrip(s.LoadReplayData, data)
    assert out["granularity"] == "Daily"


def test_load_replay_data_rejects_unknown_granularity() -> None:
    data = {
        "op": "LoadReplayData",
        "request_id": "req-3",
        "instrument_id": "1301.TSE",
        "start_date": "2024-01-04",
        "end_date": "2024-01-31",
        "granularity": "Hourly",
    }
    with pytest.raises(ValidationError):
        s.LoadReplayData.model_validate(data)


# ── Events ──────────────────────────────────────────────────────────────────


def test_engine_started_roundtrip() -> None:
    data = {
        "event": "EngineStarted",
        "strategy_id": "strat-001",
        "account_id": "SIM-001",
        "ts_event_ms": 1700000000000,
    }
    out = _roundtrip(s.EngineStarted, data)
    assert out["account_id"] == "SIM-001"


def test_engine_stopped_roundtrip() -> None:
    data = {
        "event": "EngineStopped",
        "strategy_id": "strat-001",
        "final_equity": "1050000.50",
        "ts_event_ms": 1700000000001,
    }
    out = _roundtrip(s.EngineStopped, data)
    assert out["final_equity"] == "1050000.50"


def test_replay_data_loaded_roundtrip() -> None:
    data = {
        "event": "ReplayDataLoaded",
        "strategy_id": "strat-001",
        "bars_loaded": 1234,
        "trades_loaded": 56789,
        "ts_event_ms": 1700000000002,
    }
    out = _roundtrip(s.ReplayDataLoaded, data)
    assert out["bars_loaded"] == 1234
    assert out["trades_loaded"] == 56789


def test_position_opened_roundtrip() -> None:
    data = {
        "event": "PositionOpened",
        "strategy_id": "strat-001",
        "venue": "SIM",
        "instrument_id": "1301.TSE",
        "position_id": "P-1",
        "side": "LONG",
        "opened_qty": "100",
        "avg_open_price": "1500.5",
        "ts_event_ms": 1700000000003,
    }
    out = _roundtrip(s.PositionOpened, data)
    assert out["side"] == "LONG"
    assert out["avg_open_price"] == "1500.5"


def test_position_closed_roundtrip() -> None:
    data = {
        "event": "PositionClosed",
        "strategy_id": "strat-001",
        "venue": "SIM",
        "instrument_id": "1301.TSE",
        "position_id": "P-1",
        "realized_pnl": "5000.0",
        "ts_event_ms": 1700000000004,
    }
    out = _roundtrip(s.PositionClosed, data)
    assert out["realized_pnl"] == "5000.0"


# ── Hello.mode (N1.13) ──────────────────────────────────────────────────────


def test_hello_accepts_mode_field() -> None:
    data = {
        "op": "Hello",
        "schema_major": 2,
        "schema_minor": 4,
        "client_version": "test-0.0.0",
        "token": "tok",
        "mode": "replay",
    }
    out = _roundtrip(s.Hello, data)
    assert out["mode"] == "replay"


def test_hello_defaults_mode_to_live_when_absent() -> None:
    """Backward compat: older clients that don't set mode default to "live"."""
    data = {
        "op": "Hello",
        "schema_major": 2,
        "schema_minor": 4,
        "client_version": "test-0.0.0",
        "token": "tok",
    }
    obj = s.Hello.model_validate(data)
    assert obj.mode == "live"
