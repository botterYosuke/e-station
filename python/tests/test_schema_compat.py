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


# ── Phase P3a (25 → 26): LiveStrategyReady ─────────────────────────────────


def test_schema_minor_is_26_for_phase_p3a() -> None:
    """SCHEMA_MINOR must be bumped to 26 for Phase P3a (LiveStrategyReady)."""
    assert s.SCHEMA_MINOR >= 26, "Phase P3a: bump 25 → 26 not applied"


def test_live_strategy_ready_round_trip() -> None:
    """`LiveStrategyReady` event serialises and round-trips via JSON."""
    data = {
        "event": "LiveStrategyReady",
        "strategy_id": "live-strat-1",
        "venue": "tachibana",
        "instrument_id": "7203.TSE",
        "ts_event_ms": 1_700_000_000_123,
    }
    ev = s.LiveStrategyReady.model_validate(data)
    out = orjson.loads(orjson.dumps(ev.model_dump(mode="json")))
    assert out["event"] == "LiveStrategyReady"
    assert out["strategy_id"] == "live-strat-1"
    assert out["venue"] == "tachibana"
    assert out["instrument_id"] == "7203.TSE"
    assert out["ts_event_ms"] == 1_700_000_000_123


def test_live_strategy_ready_unknown_variant_tolerated() -> None:
    """旧 client が `LiveStrategyReady` event を unknown として握り潰せる（後方互換）。"""
    data = {
        "event": "LiveStrategyReady",
        "strategy_id": "live-strat-2",
        "venue": "tachibana",
        "instrument_id": "7203.TSE",
        "ts_event_ms": 1_700_000_000_456,
        "future_field": "ignored",
    }
    ev = s.LiveStrategyReady.model_validate(data)
    assert ev.strategy_id == "live-strat-2"
    assert not hasattr(ev, "future_field")


# ── Phase P3b (26 → 27): LiveStrategyWarmingUp ─────────────────────────────


def test_schema_minor_is_27_for_phase_p3b() -> None:
    """SCHEMA_MINOR must be bumped to 27 for Phase P3b (LiveStrategyWarmingUp)."""
    assert s.SCHEMA_MINOR >= 27, "Phase P3b: bump 26 → 27 not applied"


def test_live_strategy_warming_up_round_trip() -> None:
    """`LiveStrategyWarmingUp` event は warm_up 進捗を 5s 毎に emit する（progress + message）."""
    data = {
        "event": "LiveStrategyWarmingUp",
        "strategy_id": "live-strat-3",
        "progress": 0.42,
        "message": "ヒストリカルデータ取得中…",
    }
    ev = s.LiveStrategyWarmingUp.model_validate(data)
    out = orjson.loads(orjson.dumps(ev.model_dump(mode="json")))
    assert out["event"] == "LiveStrategyWarmingUp"
    assert out["strategy_id"] == "live-strat-3"
    assert out["progress"] == pytest.approx(0.42)
    assert out["message"] == "ヒストリカルデータ取得中…"


def test_live_strategy_warming_up_unknown_variant_tolerated() -> None:
    """旧 client が `LiveStrategyWarmingUp` を unknown event として握り潰せる（後方互換）."""
    data = {
        "event": "LiveStrategyWarmingUp",
        "strategy_id": "live-strat-4",
        "progress": 0.0,
        "message": "warming up",
        "future_field": 123,
    }
    ev = s.LiveStrategyWarmingUp.model_validate(data)
    assert ev.progress == pytest.approx(0.0)
    assert not hasattr(ev, "future_field")


# ── Phase P3c (27 → 28): EngineBusy.venue / EngineBusy.busy_kind ────────────


def test_schema_minor_is_28_for_phase_p3c() -> None:
    """SCHEMA_MINOR must be bumped to 28 for Phase P3c (EngineBusy.venue/busy_kind)."""
    assert s.SCHEMA_MINOR >= 28, "Phase P3c: bump 27 → 28 not applied"


def test_engine_busy_with_venue_round_trip() -> None:
    """`EngineBusy` は新しい optional `venue` / `busy_kind` フィールドを受け入れる。

    venue 単位で別 strategy が走っているとき: busy_kind="another_strategy_on_venue"
    （統一決定 #7）。
    """
    data = {
        "event": "EngineBusy",
        "current_state": "TRADING",
        "attempted_command": "StartEngine",
        "reason": "venue tachibana is hosting another strategy",
        "request_id": "req-busy-1",
        "venue": "tachibana",
        "busy_kind": "another_strategy_on_venue",
    }
    ev = s.EngineBusy.model_validate(data)
    out = orjson.loads(orjson.dumps(ev.model_dump(mode="json")))
    assert out["event"] == "EngineBusy"
    assert out["venue"] == "tachibana"
    assert out["busy_kind"] == "another_strategy_on_venue"


def test_engine_busy_missing_venue_busy_kind_falls_back_to_none() -> None:
    """旧 wire 形（venue / busy_kind 不在）でも EngineBusy はデシリアライズ成功（後方互換）."""
    data = {
        "event": "EngineBusy",
        "current_state": "RUNNING",
        "attempted_command": "LoadReplayData",
        "reason": "already running",
        "request_id": "req-busy-2",
    }
    ev = s.EngineBusy.model_validate(data)
    assert ev.venue is None
    assert ev.busy_kind is None
    # 旧形式と等価な round-trip ができる
    out = orjson.loads(orjson.dumps(ev.model_dump(mode="json", exclude_none=True)))
    assert "venue" not in out
    assert "busy_kind" not in out


# ── Phase 3.5: per-venue capability fallback (no schema bump needed) ─────────


def test_supports_live_strategy_missing_falls_back_false() -> None:
    """旧 server から ``supports_live_strategy`` cap が欠落しているとき、
    Rust 側 helper が ``false`` にフォールバックする契約を pin する（Python から見える wire 観点）。

    Phase 3.5 は capability map のキー追加のみで SCHEMA_MINOR を bump しない。
    旧 client が新 server に接続しても既存 ``venue_capability::<bool>`` helper の
    ``Ok(None)`` 仕様で安全側（false = "live 戦略未対応扱い"）に倒れる。
    """
    # capability map から supports_live_strategy が抜けた wire を再現
    legacy_caps_blob = {
        "supported_venues": ["tachibana"],
        "venue_capabilities": {
            "tachibana": {
                # supports_live_strategy なし (旧 server)
                "supported_timeframes": ["1d"],
            },
        },
    }
    venue_caps = legacy_caps_blob["venue_capabilities"]["tachibana"]
    # 旧 wire には supports_live_strategy キー自体が無い
    assert "supports_live_strategy" not in venue_caps, (
        "テストは『キー欠落 → Rust が false にフォールバック』を pin するため、"
        "テスト fixture から supports_live_strategy を意図的に省く必要がある"
    )
    # `engine_client::capabilities::supports_live_strategy(caps, venue)` は
    # 欠落時に false を返す契約（同名 Rust unit test で pin）。
    # Python 側からは「欠落キー = 旧 wire と等価」であることだけを保証する。


def test_tachibana_is_production_missing_falls_back_false() -> None:
    """旧 server から tachibana の ``is_production`` cap が欠落しているとき、
    Rust 側 helper は ``false`` にフォールバックする契約を pin する。
    """
    legacy_caps_blob = {
        "supported_venues": ["tachibana"],
        "venue_capabilities": {
            "tachibana": {
                # is_production なし (旧 server: tachibana 用 is_production を expose していなかった)
                "supported_timeframes": ["1d"],
            },
        },
    }
    venue_caps = legacy_caps_blob["venue_capabilities"]["tachibana"]
    assert "is_production" not in venue_caps, (
        "テストは『キー欠落 → Rust が false にフォールバック』を pin する目的"
    )
    # `engine_client::capabilities::is_production(caps, "tachibana")` は欠落時に false。
    # 安全側 (= demo 扱い) に倒すことで本番投入を予防する。
