"""Issue #43 リグレッションテスト: StrategyScenarioLoaded proto build。

scenario フィールドが dict のとき _dict_to_proto_event が proto build に失敗して
イベントを drop していた (silent failure)。修正後は proto build が成功すること。
"""
from __future__ import annotations

import json

import pytest

from engine.server_grpc import _dict_to_proto_event
from engine.proto import engine_pb2


def _make_loaded_payload(scenario) -> dict:
    return {
        "event": "StrategyScenarioLoaded",
        "request_id": "test-id",
        "path": "/some/path.py",
        "scenario": scenario,
        "resolved_instruments": None,
    }


class TestStrategyScenarioLoadedProtoBuild:
    """_dict_to_proto_event が StrategyScenarioLoaded を正しく proto build すること。"""

    def test_scenario_as_dict_builds_proto(self):
        """Regression #43: scenario が dict でも proto build が成功し None を返さないこと。"""
        payload = _make_loaded_payload(
            scenario={
                "schema_version": 1,
                "instrument": "1301.TSE",
                "start": "2025-01-06",
                "end": "2025-01-06",
                "granularity": "REPLAY_GRANULARITY_TRADE",
                "initial_cash": 1_000_000,
            }
        )
        result = _dict_to_proto_event(payload)
        assert result is not None, (
            "proto build に失敗して None が返された (silent failure)。"
            "scenario dict を JSON string に変換してから ParseDict に渡すこと。"
        )

    def test_scenario_as_dict_produces_json_string_in_proto(self):
        """proto の scenario フィールドが JSON-encoded string になっていること。"""
        scenario_dict = {
            "schema_version": 1,
            "instrument": "7203.TSE",
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "REPLAY_GRANULARITY_TRADE",
            "initial_cash": 500_000,
        }
        payload = _make_loaded_payload(scenario=scenario_dict)
        result = _dict_to_proto_event(payload)
        assert result is not None

        ssl = result.strategy_scenario_loaded
        assert ssl.scenario != "", "scenario フィールドが空"
        # proto scenario フィールドを JSON parse できること
        parsed = json.loads(ssl.scenario)
        assert parsed["instrument"] == "7203.TSE"
        assert parsed["schema_version"] == 1

    def test_scenario_none_builds_proto(self):
        """scenario=None (SCENARIO 定数なし) でも proto build が成功すること。"""
        payload = _make_loaded_payload(scenario=None)
        result = _dict_to_proto_event(payload)
        assert result is not None
        ssl = result.strategy_scenario_loaded
        assert not ssl.HasField("scenario"), "scenario=None は proto で absent になること"

    def test_scenario_as_string_builds_proto(self):
        """scenario が既に JSON string でも proto build が成功すること (二重変換なし)。"""
        scenario_str = json.dumps({"schema_version": 1, "instrument": "1301.TSE"})
        payload = _make_loaded_payload(scenario=scenario_str)
        result = _dict_to_proto_event(payload)
        assert result is not None
        ssl = result.strategy_scenario_loaded
        parsed = json.loads(ssl.scenario)
        assert parsed["instrument"] == "1301.TSE"
