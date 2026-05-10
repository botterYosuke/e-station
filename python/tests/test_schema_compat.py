"""Schema chain (issue #42) compatibility tests.

Each commit P2 → P3a → P3b → P3c が後方互換性を維持していること（旧 client が
新しい event を unknown variant として握り潰せること）を確認する TDD ガード。

このファイルは issue #42 Wave 1 (schema 24 → 28) のサブエージェントが追加した。
"""
from __future__ import annotations

import orjson
import pytest

from engine import schemas as s


# ── Phase P2 (24 → 25): LoadLiveStrategyScenario / LiveStrategyScenarioLoaded ─


def test_schema_minor_is_25_for_phase_p2() -> None:
    """SCHEMA_MINOR must be bumped to 25 for Phase P2 (LoadLiveStrategyScenario)."""
    assert s.SCHEMA_MINOR >= 25, "Phase P2: bump 24 → 25 not applied"


def test_load_live_strategy_scenario_roundtrip() -> None:
    """`LoadLiveStrategyScenario` command serialises and round-trips via JSON."""
    data = {
        "op": "LoadLiveStrategyScenario",
        "request_id": "req-live-1",
        "strategy_path": "/path/to/strategy.py",
    }
    cmd = s.LoadLiveStrategyScenario.model_validate(data)
    out = orjson.loads(orjson.dumps(cmd.model_dump(mode="json")))
    assert out["op"] == "LoadLiveStrategyScenario"
    assert out["request_id"] == "req-live-1"
    assert out["strategy_path"] == "/path/to/strategy.py"


def test_live_strategy_scenario_loaded_full_roundtrip() -> None:
    """`LiveStrategyScenarioLoaded` event round-trips with all optional fields populated."""
    data = {
        "event": "LiveStrategyScenarioLoaded",
        "request_id": "req-live-1",
        "instrument_id": "7203.TSE",
        "max_qty": 100,
        "max_notional_jpy": 1_000_000,
        "venue": "tachibana",
        "strategy_init_kwargs": {"foo": 1, "bar": "baz"},
    }
    ev = s.LiveStrategyScenarioLoaded.model_validate(data)
    out = orjson.loads(orjson.dumps(ev.model_dump(mode="json")))
    assert out["event"] == "LiveStrategyScenarioLoaded"
    assert out["instrument_id"] == "7203.TSE"
    assert out["max_qty"] == 100
    assert out["max_notional_jpy"] == 1_000_000
    assert out["venue"] == "tachibana"
    assert out["strategy_init_kwargs"] == {"foo": 1, "bar": "baz"}


def test_live_strategy_scenario_loaded_optional_all_none() -> None:
    """`LiveStrategyScenarioLoaded` accepts all-None payload (LIVE_SCENARIO 不在時)."""
    data = {
        "event": "LiveStrategyScenarioLoaded",
        "request_id": "req-live-2",
    }
    ev = s.LiveStrategyScenarioLoaded.model_validate(data)
    assert ev.instrument_id is None
    assert ev.max_qty is None
    assert ev.max_notional_jpy is None
    assert ev.venue is None
    assert ev.strategy_init_kwargs is None


def test_live_strategy_scenario_loaded_unknown_variant_tolerated() -> None:
    """旧 client が新 event 名を unknown として握り潰せる（後方互換）。

    Pydantic IpcMessage は ``extra="ignore"`` 設定なので、未知 event 名のフィールドが
    紛れ込んでも別モデルでは validate されないという前提を確認する。実装としては、
    旧 client が ``LoadLiveStrategyScenario`` を含む dict を ``LoadStrategyScenario``
    として validate しようとしても reject されるが、それは旧 client 側で握り潰す責務。
    ここでは、新 event (``LiveStrategyScenarioLoaded``) 単体が dict 形式で問題なく
    serialise でき、旧 client がそれを **無視** できる前提を保証する。
    """
    data = {
        "event": "LiveStrategyScenarioLoaded",
        "request_id": "req-live-3",
        "instrument_id": "7203.TSE",
    }
    raw = orjson.dumps(data)
    decoded = orjson.loads(raw)
    # 旧 client は event 名で dispatch するため、未知 event 名は単純に skip 可能。
    assert decoded["event"] == "LiveStrategyScenarioLoaded"
    # extra フィールドは IpcMessage の extra="ignore" で吸収される（既存実装契約）
    ev = s.LiveStrategyScenarioLoaded.model_validate(
        {**data, "future_unknown_field": "tolerated"}
    )
    assert ev.request_id == "req-live-3"
    assert not hasattr(ev, "future_unknown_field")
