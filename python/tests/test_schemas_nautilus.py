"""N1.1 (Python): pydantic round-trip + Literal 制約のテスト.

- Command 系: StartEngine / StopEngine / LoadReplayData
- Event  系: EngineStarted / EngineStopped / ReplayDataLoaded /
             PositionOpened / PositionClosed
- Literal 制約違反 (engine="Bogus" / granularity="Bogus") で ValidationError
- SCHEMA_MINOR == 8 (Phase A: kind フィールド追加)
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


def test_schema_minor_is_8_for_phase_a() -> None:
    # Phase A: SCHEMA_MINOR を 6 → 8 に bump (kind フィールド追加 + PositionsUpdated 追加)
    assert s.SCHEMA_MINOR == 8
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


def test_start_engine_with_strategy_file_roundtrip() -> None:
    data = {
        "op": "StartEngine",
        "request_id": "req-sf",
        "engine": "Backtest",
        "strategy_id": "user-defined",
        "config": {
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-03-31",
            "initial_cash": "1000000",
            "granularity": "Daily",
            "strategy_file": "examples/strategies/buy_and_hold.py",
            "strategy_init_kwargs": {"instrument_id": "1301.TSE", "lot_size": 100},
        },
    }
    out = _roundtrip(s.StartEngine, data)
    assert out["config"]["strategy_file"] == "examples/strategies/buy_and_hold.py"
    assert out["config"]["strategy_init_kwargs"]["lot_size"] == 100


def test_engine_start_config_rejects_non_object_strategy_init_kwargs() -> None:
    for bad_value in [[], "string", 42]:
        with pytest.raises(ValidationError):
            s.EngineStartConfig.model_validate(
                {
                    "instrument_id": "1301.TSE",
                    "start_date": "2024-01-04",
                    "end_date": "2024-03-31",
                    "initial_cash": "1000000",
                    "granularity": "Daily",
                    "strategy_init_kwargs": bad_value,
                }
            )


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
    assert out["strategy_id"] == "strat-001"


def test_replay_data_loaded_accepts_null_strategy_id() -> None:
    """M-8 (R1b / schema 2.5): 単独 LoadReplayData では strategy_id=None を受け付ける。"""
    data = {
        "event": "ReplayDataLoaded",
        "strategy_id": None,
        "bars_loaded": 0,
        "trades_loaded": 4,
        "ts_event_ms": 1700000000002,
    }
    obj = s.ReplayDataLoaded.model_validate(data)
    assert obj.strategy_id is None
    out = orjson.loads(orjson.dumps(obj.model_dump(mode="json")))
    assert out["strategy_id"] is None


def test_replay_data_loaded_default_strategy_id_when_field_absent() -> None:
    """M-8: strategy_id フィールド省略時もデフォルト None で deserialize できる。"""
    data = {
        "event": "ReplayDataLoaded",
        "bars_loaded": 0,
        "trades_loaded": 4,
        "ts_event_ms": 1700000000002,
    }
    obj = s.ReplayDataLoaded.model_validate(data)
    assert obj.strategy_id is None


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
    # M-5 (R2 review-fix R2): SCHEMA_MINOR を動的参照する (旧 client 互換は別テスト)
    data = {
        "op": "Hello",
        "schema_major": s.SCHEMA_MAJOR,
        "schema_minor": s.SCHEMA_MINOR,
        "client_version": "test-0.0.0",
        "token": "tok",
        "mode": "replay",
    }
    out = _roundtrip(s.Hello, data)
    assert out["mode"] == "replay"


def test_hello_defaults_mode_to_live_when_absent() -> None:
    """Backward compat: older clients that don't set mode default to "live"."""
    # M-5 (R2 review-fix R2): SCHEMA_MINOR を動的参照する
    data = {
        "op": "Hello",
        "schema_major": s.SCHEMA_MAJOR,
        "schema_minor": s.SCHEMA_MINOR,
        "client_version": "test-0.0.0",
        "token": "tok",
    }
    obj = s.Hello.model_validate(data)
    assert obj.mode == "live"


def test_old_client_minor_4_compatible() -> None:
    """旧 client (SCHEMA_MINOR=4) からの Hello もハンドシェイク成功する。

    SCHEMA_MAJOR の不一致のみが切断条件。MINOR 不一致は WARN のみで接続維持
    （`engine-client/src/connection.rs` の handshake 規約）。
    Hello deserialize 自体は MINOR 値に依存しないため、旧 minor=4 を投げても
    pydantic の validate は通る (M-5, R2 review-fix R2 で明示 pin)。
    """
    data = {
        "op": "Hello",
        "schema_major": 2,
        "schema_minor": 4,  # 旧 client (R1b 以前)
        "client_version": "old-client",
        "token": "tok",
    }
    obj = s.Hello.model_validate(data)
    assert obj.schema_minor == 4
    assert obj.mode == "live"


# ── M-4: EngineError.strategy_id="" 正規化 ─────────────────────────────────


def test_engine_error_normalizes_empty_strategy_id_to_none() -> None:
    """M-4: EngineError(strategy_id="") は .strategy_id is None になる。"""
    obj = s.EngineError(code="x", message="y", strategy_id="")
    assert obj.strategy_id is None


def test_engine_error_keeps_nonempty_strategy_id() -> None:
    """M-4: 非空 strategy_id はそのまま保持される。"""
    obj = s.EngineError(code="x", message="y", strategy_id="strat-1")
    assert obj.strategy_id == "strat-1"


def test_engine_error_normalizes_empty_strategy_id_via_validate() -> None:
    """M-4: model_validate 経由でも空文字 → None になる (wire 互換)。"""
    obj = s.EngineError.model_validate(
        {"event": "EngineError", "code": "x", "message": "y", "strategy_id": ""}
    )
    assert obj.strategy_id is None
