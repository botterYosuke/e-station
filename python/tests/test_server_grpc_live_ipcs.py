"""issue #42 Phase 2 functional / 受け入れ基準 #22:

`server_grpc.py` の wire mapping が新 IPCs (LoadLiveStrategyScenario /
LiveStrategyScenarioLoaded) を含むことを pin する。

mapping が落ちると proto Command/Event 経路で payload が drop されるため、
mapping 削除を即座に検知できる。
"""

from __future__ import annotations


def test_field_to_op_mapping_contains_load_live_strategy_scenario() -> None:
    """gRPC inbound mapping に ``load_live_strategy_scenario`` が含まれること。"""
    from engine.server_grpc import _FIELD_TO_OP

    assert "load_live_strategy_scenario" in _FIELD_TO_OP, (
        f"_FIELD_TO_OP に load_live_strategy_scenario が登録されていない: "
        f"keys={sorted(_FIELD_TO_OP.keys())}"
    )
    assert _FIELD_TO_OP["load_live_strategy_scenario"] == "LoadLiveStrategyScenario"


def test_event_to_field_mapping_contains_live_strategy_scenario_loaded() -> None:
    """gRPC outbound mapping に ``LiveStrategyScenarioLoaded`` が含まれること。"""
    from engine.server_grpc import _EVENT_TO_FIELD_AND_CLASS

    assert "LiveStrategyScenarioLoaded" in _EVENT_TO_FIELD_AND_CLASS, (
        f"_EVENT_TO_FIELD_AND_CLASS に LiveStrategyScenarioLoaded が登録されていない"
    )
    field_name, _msg_class = _EVENT_TO_FIELD_AND_CLASS["LiveStrategyScenarioLoaded"]
    assert field_name == "live_strategy_scenario_loaded", (
        f"field_name が一致しない: {field_name!r}"
    )


def test_event_to_field_mapping_contains_live_strategy_ready() -> None:
    """gRPC outbound mapping に ``LiveStrategyReady`` が含まれること（schema chain pin）。"""
    from engine.server_grpc import _EVENT_TO_FIELD_AND_CLASS

    assert "LiveStrategyReady" in _EVENT_TO_FIELD_AND_CLASS
    field_name, _msg_class = _EVENT_TO_FIELD_AND_CLASS["LiveStrategyReady"]
    assert field_name == "live_strategy_ready"


def test_event_to_field_mapping_contains_live_strategy_warming_up() -> None:
    """gRPC outbound mapping に ``LiveStrategyWarmingUp`` が含まれること（schema chain pin）。"""
    from engine.server_grpc import _EVENT_TO_FIELD_AND_CLASS

    assert "LiveStrategyWarmingUp" in _EVENT_TO_FIELD_AND_CLASS
    field_name, _msg_class = _EVENT_TO_FIELD_AND_CLASS["LiveStrategyWarmingUp"]
    assert field_name == "live_strategy_warming_up"
