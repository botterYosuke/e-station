"""Phase 1 Python スキーマ拡張 テスト

TDD RED phase: 実装前に書いたテスト。
- LiveBuyingPower の Python ↔ JSON シリアライズ対称性確認
- extra="forbid" で未知フィールドを含む JSON → ValidationError テスト
- EngineStartConfig で max_qty/max_notional_jpy が正しくシリアライズ/デシリアライズされる確認
- LiveStateName に "TRADING" と "STOPPING" が含まれることの確認
"""

from __future__ import annotations

import orjson
import pytest
from pydantic import ValidationError

from engine import schemas as s
from engine.schemas import (
    EngineStartConfig,
    LiveBuyingPower,
    LiveStateName,
)
from typing import get_args


# ── LiveStateName に TRADING / STOPPING が含まれることを確認 ─────────────────


def test_live_state_name_contains_trading() -> None:
    """LiveStateName に "TRADING" が含まれること。"""
    assert "TRADING" in get_args(LiveStateName)


def test_live_state_name_contains_stopping() -> None:
    """LiveStateName に "STOPPING" が含まれること。"""
    assert "STOPPING" in get_args(LiveStateName)


def test_live_state_name_contains_all_expected() -> None:
    """LiveStateName が期待される全値を含むこと。"""
    expected = {"DISCONNECTED", "CONNECTING", "CONNECTED", "TRADING", "STOPPING"}
    actual = set(get_args(LiveStateName))
    assert expected == actual


# ── LiveBuyingPower シリアライズ ─────────────────────────────────────────────


def test_live_buying_power_roundtrip() -> None:
    """LiveBuyingPower の Python ↔ JSON シリアライズ対称性確認。"""
    payload = {
        "event": "LiveBuyingPower",
        "strategy_id": "strat-001",
        "cash": "1000000",
        "buying_power": "2000000",
        "equity": "3000000",
        "ts_event_ms": 1700000000000,
    }
    obj = LiveBuyingPower.model_validate(payload)

    assert obj.event == "LiveBuyingPower"
    assert obj.strategy_id == "strat-001"
    assert obj.cash == "1000000"
    assert obj.buying_power == "2000000"
    assert obj.equity == "3000000"
    assert obj.ts_event_ms == 1700000000000

    # JSON ラウンドトリップ
    serialized = orjson.loads(orjson.dumps(obj.model_dump(mode="json")))
    assert serialized == payload


def test_live_buying_power_extra_forbid() -> None:
    """LiveBuyingPower の extra="forbid" — 未知フィールドは ValidationError。"""
    with pytest.raises(ValidationError):
        LiveBuyingPower.model_validate(
            {
                "event": "LiveBuyingPower",
                "strategy_id": "strat-001",
                "cash": "1000000",
                "buying_power": "2000000",
                "equity": "3000000",
                "ts_event_ms": 1700000000000,
                "unknown_field": "should_be_rejected",
            }
        )


def test_live_buying_power_missing_required_field() -> None:
    """LiveBuyingPower の必須フィールドが欠けていると ValidationError。"""
    with pytest.raises(ValidationError):
        LiveBuyingPower.model_validate(
            {
                "event": "LiveBuyingPower",
                "strategy_id": "strat-001",
                # cash が欠けている
                "buying_power": "2000000",
                "equity": "3000000",
                "ts_event_ms": 1700000000000,
            }
        )


# ── EngineStartConfig で live 専用フィールドが使えること ─────────────────────


def test_engine_start_config_live_fields_roundtrip() -> None:
    """EngineStartConfig で max_qty / max_notional_jpy が正しくシリアライズ/デシリアライズされる。"""
    payload = {
        "instrument_id": "7203.T",
        "max_qty": 1000,
        "max_notional_jpy": 5000000,
        "strategy_file": "strategies/my_live_strategy.py",
    }
    obj = EngineStartConfig.model_validate(payload)

    assert obj.max_qty == 1000
    assert obj.max_notional_jpy == 5000000
    # replay 専用フィールドは None
    assert obj.start_date is None
    assert obj.end_date is None
    assert obj.initial_cash is None
    assert obj.granularity is None

    serialized = obj.model_dump(mode="json")
    assert serialized["max_qty"] == 1000
    assert serialized["max_notional_jpy"] == 5000000


def test_engine_start_config_replay_fields_roundtrip() -> None:
    """EngineStartConfig で replay 専用フィールドが正しくシリアライズ/デシリアライズされる。"""
    payload = {
        "instrument_id": "7203.T",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "initial_cash": "1000000",
        "granularity": "Daily",
    }
    obj = EngineStartConfig.model_validate(payload)

    assert obj.start_date == "2024-01-01"
    assert obj.end_date == "2024-12-31"
    assert obj.initial_cash == "1000000"
    assert obj.granularity == "Daily"
    # live 専用フィールドは None
    assert obj.max_qty is None
    assert obj.max_notional_jpy is None


def test_engine_start_config_extra_forbid() -> None:
    """EngineStartConfig の extra="forbid" — 未知フィールドは ValidationError。"""
    with pytest.raises(ValidationError):
        EngineStartConfig.model_validate(
            {
                "instrument_id": "7203.T",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "initial_cash": "1000000",
                "granularity": "Daily",
                "unknown_field": "should_be_rejected",
            }
        )


# ── SCHEMA_MINOR が 17 に bump されていること ────────────────────────────────


def test_schema_minor_bumped_for_live_buying_power() -> None:
    """N3 live strategy 実装で SCHEMA_MINOR が 17 になっていること。"""
    assert s.SCHEMA_MINOR == 17
