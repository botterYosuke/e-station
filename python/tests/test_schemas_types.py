"""Phase 8 review-fix-loop R1 — Phase 1 (型基盤) tests.

C-Type1 / H-Type2 / H-Type4 / M-Type3 で追加される type alias / Literal の存在と、
EngineBusy の illegal 組合せ拒否 (model_validator) を検証する。

これらは後続 Phase (2/3/4/5) が import する型基盤 — schema を破壊させないこと。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine import schemas


# ---------------------------------------------------------------------------
# C-Type1: AttemptedCommand / AppMode / state-name Literal の存在
# ---------------------------------------------------------------------------


class TestTypeAliasesExist:
    def test_attempted_command_alias_is_defined(self) -> None:
        assert hasattr(schemas, "AttemptedCommand")

    def test_app_mode_alias_is_defined(self) -> None:
        assert hasattr(schemas, "AppMode")

    def test_replay_state_name_alias_is_defined(self) -> None:
        assert hasattr(schemas, "ReplayStateName")

    def test_live_state_name_alias_is_defined(self) -> None:
        assert hasattr(schemas, "LiveStateName")

    def test_current_engine_state_alias_is_defined(self) -> None:
        assert hasattr(schemas, "CurrentEngineState")


# ---------------------------------------------------------------------------
# C-Type1: AttemptedCommand に GetBuyingPower / GetPositions / GetOrderList を含む (H-Type4)
# ---------------------------------------------------------------------------


class TestAttemptedCommandLiteralValues:
    @pytest.mark.parametrize(
        ("cmd", "state"),
        [
            ("LoadReplayData", "IDLE"),
            ("StartEngine", "LOADED"),
            ("StopEngine", "RUNNING"),
            ("SetReplaySpeed", "RUNNING"),
            ("SubmitOrder", "IDLE"),  # replay state で SubmitOrder も合法
            ("SubmitOrder", "DISCONNECTED"),  # live state でも合法
            ("ModifyOrder", "DISCONNECTED"),
            ("CancelOrder", "DISCONNECTED"),
            ("CancelAllOrders", "DISCONNECTED"),
            ("RequestVenueLogin", "CONNECTED"),
            ("GetBuyingPower", "DISCONNECTED"),
            ("GetPositions", "DISCONNECTED"),
            ("GetOrderList", "DISCONNECTED"),
        ],
    )
    def test_engine_busy_accepts_command(self, cmd: str, state: str) -> None:
        evt = schemas.EngineBusy(
            current_state=state,  # type: ignore[arg-type]
            attempted_command=cmd,  # type: ignore[arg-type]
            reason="test",
        )
        assert evt.attempted_command == cmd

    def test_engine_busy_rejects_unknown_command(self) -> None:
        with pytest.raises(ValidationError):
            schemas.EngineBusy(
                current_state="IDLE",
                attempted_command="NotARealCommand",  # type: ignore[arg-type]
                reason="x",
            )


# ---------------------------------------------------------------------------
# H-Type2: EngineBusy が illegal 組合せ (STOPPING + RequestVenueLogin など) を拒否する
# ---------------------------------------------------------------------------


class TestEngineBusyOrthogonalRejection:
    """Replay state と Live command, Live state と Replay command の混在を拒否。"""

    def test_replay_state_with_live_only_command_rejected(self) -> None:
        # STOPPING は ReplayStateName。RequestVenueLogin は Live コマンド。
        with pytest.raises(ValidationError):
            schemas.EngineBusy(
                current_state="STOPPING",
                attempted_command="RequestVenueLogin",
                reason="illegal cross",
            )

    def test_live_state_with_replay_only_command_rejected(self) -> None:
        # CONNECTED は LiveStateName。LoadReplayData は Replay コマンド。
        with pytest.raises(ValidationError):
            schemas.EngineBusy(
                current_state="CONNECTED",
                attempted_command="LoadReplayData",
                reason="illegal cross",
            )

    def test_replay_state_with_replay_command_accepted(self) -> None:
        evt = schemas.EngineBusy(
            current_state="LOADED",
            attempted_command="LoadReplayData",
            reason="ok",
        )
        assert evt.current_state == "LOADED"

    def test_live_state_with_live_command_accepted(self) -> None:
        evt = schemas.EngineBusy(
            current_state="CONNECTED",
            attempted_command="SubmitOrder",
            reason="ok",
        )
        assert evt.current_state == "CONNECTED"

    def test_get_buying_power_accepted_with_live_state(self) -> None:
        evt = schemas.EngineBusy(
            current_state="DISCONNECTED",
            attempted_command="GetBuyingPower",
            reason="not connected",
        )
        assert evt.attempted_command == "GetBuyingPower"


# ---------------------------------------------------------------------------
# R2-A H8 / H9: EngineBusy.busy_kind Literal + another_strategy_on_venue venue 必須
# ---------------------------------------------------------------------------


class TestEngineBusyBusyKindLiteral:
    """H8: ``busy_kind`` は ``BusyKind`` Literal で固定（typo / 未予約値を拒否）。"""

    def test_busy_kind_alias_is_defined(self) -> None:
        assert hasattr(schemas, "BusyKind")

    def test_busy_kind_accepts_another_strategy_on_venue(self) -> None:
        evt = schemas.EngineBusy(
            current_state="TRADING",
            attempted_command="StartEngine",
            reason="venue concurrency",
            venue="tachibana",
            busy_kind="another_strategy_on_venue",
        )
        assert evt.busy_kind == "another_strategy_on_venue"

    def test_busy_kind_accepts_none(self) -> None:
        evt = schemas.EngineBusy(
            current_state="LOADED",
            attempted_command="LoadReplayData",
            reason="replay busy",
        )
        assert evt.busy_kind is None

    def test_busy_kind_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError):
            schemas.EngineBusy(
                current_state="TRADING",
                attempted_command="StartEngine",
                reason="x",
                venue="tachibana",
                busy_kind="typo_value",  # type: ignore[arg-type]
            )


class TestEngineBusyAnotherStrategyRequiresVenue:
    """H9: ``busy_kind='another_strategy_on_venue'`` のとき ``venue`` が必須。"""

    def test_engine_busy_another_strategy_requires_venue(self) -> None:
        with pytest.raises(ValidationError):
            schemas.EngineBusy(
                current_state="TRADING",
                attempted_command="StartEngine",
                reason="missing venue",
                venue=None,
                busy_kind="another_strategy_on_venue",
            )

    def test_engine_busy_another_strategy_with_venue_accepted(self) -> None:
        evt = schemas.EngineBusy(
            current_state="TRADING",
            attempted_command="StartEngine",
            reason="ok",
            venue="tachibana",
            busy_kind="another_strategy_on_venue",
        )
        assert evt.venue == "tachibana"


# ---------------------------------------------------------------------------
# R2-A M1: LiveStrategyWarmingUp.progress 範囲制約 [0.0, 1.0]
# ---------------------------------------------------------------------------


class TestLiveStrategyWarmingUpProgressRange:
    """M1: ``progress`` は 0.0–1.0 の範囲外を拒否する。"""

    def test_progress_zero_accepted(self) -> None:
        evt = schemas.LiveStrategyWarmingUp(strategy_id="s", progress=0.0, message="m")
        assert evt.progress == 0.0

    def test_progress_one_accepted(self) -> None:
        evt = schemas.LiveStrategyWarmingUp(strategy_id="s", progress=1.0, message="m")
        assert evt.progress == 1.0

    def test_progress_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            schemas.LiveStrategyWarmingUp(strategy_id="s", progress=1.5, message="m")

    def test_progress_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            schemas.LiveStrategyWarmingUp(strategy_id="s", progress=-0.1, message="m")


# ---------------------------------------------------------------------------
# R2-A M2: LiveStrategyScenarioLoaded all-or-none model_validator
# ---------------------------------------------------------------------------


class TestLiveStrategyScenarioLoadedAllOrNone:
    """M2: ``instrument_id`` / ``max_qty`` / ``max_notional_jpy`` / ``venue`` は
    全 None または全 Some が要求される（部分 None は ValidationError）。
    ``strategy_init_kwargs`` は対象外（任意フィールド）。
    """

    def test_all_none_accepted(self) -> None:
        evt = schemas.LiveStrategyScenarioLoaded(request_id="r")
        assert evt.instrument_id is None
        assert evt.max_qty is None
        assert evt.max_notional_jpy is None
        assert evt.venue is None

    def test_all_set_accepted(self) -> None:
        evt = schemas.LiveStrategyScenarioLoaded(
            request_id="r",
            instrument_id="8306.T",
            max_qty=100,
            max_notional_jpy=500_000,
            venue="tachibana",
        )
        assert evt.instrument_id == "8306.T"

    def test_partial_set_rejected_missing_venue(self) -> None:
        with pytest.raises(ValidationError):
            schemas.LiveStrategyScenarioLoaded(
                request_id="r",
                instrument_id="8306.T",
                max_qty=100,
                max_notional_jpy=500_000,
                # venue=None — partial fill
            )

    def test_partial_set_rejected_missing_max_qty(self) -> None:
        with pytest.raises(ValidationError):
            schemas.LiveStrategyScenarioLoaded(
                request_id="r",
                instrument_id="8306.T",
                # max_qty=None — partial fill
                max_notional_jpy=500_000,
                venue="tachibana",
            )

    def test_strategy_init_kwargs_can_be_set_alone_without_other_fields(self) -> None:
        # strategy_init_kwargs は all-or-none の対象外（任意フィールド）。
        # 他フィールドが全 None でも strategy_init_kwargs が dict を持つ構成は不正。
        # → all-or-none は他 4 フィールドのみが対象なので、ここでは reject されない。
        # （仕様: kwargs だけ持って prefill フィールドは null 応答する想定は無いが、
        #  pydantic の型としては許容する）
        evt = schemas.LiveStrategyScenarioLoaded(
            request_id="r",
            strategy_init_kwargs={"x": 1},
        )
        assert evt.strategy_init_kwargs == {"x": 1}


# ---------------------------------------------------------------------------
# R2-A M3: StartEngine{Live} は max_qty / max_notional_jpy を必須化
# ---------------------------------------------------------------------------


class TestStartEngineLiveRequiresSafetyLimits:
    """M3: Live mode の StartEngine は ``max_qty`` / ``max_notional_jpy`` 必須。"""

    def _config(self, **overrides):
        base = dict(
            instrument_id="8306.T",
            max_qty=100,
            max_notional_jpy=500_000,
            strategy_file="dummy.py",
        )
        base.update(overrides)
        return schemas.EngineStartConfig(**base)

    def test_live_with_both_limits_accepted(self) -> None:
        cfg = self._config()
        msg = schemas.StartEngine(
            request_id="r",
            engine="Live",
            strategy_id="sid",
            config=cfg,
        )
        assert msg.engine == "Live"

    def test_start_engine_live_rejects_missing_max_qty(self) -> None:
        cfg = self._config(max_qty=None)
        with pytest.raises(ValidationError):
            schemas.StartEngine(
                request_id="r",
                engine="Live",
                strategy_id="sid",
                config=cfg,
            )

    def test_start_engine_live_rejects_missing_max_notional(self) -> None:
        cfg = self._config(max_notional_jpy=None)
        with pytest.raises(ValidationError):
            schemas.StartEngine(
                request_id="r",
                engine="Live",
                strategy_id="sid",
                config=cfg,
            )

    def test_start_engine_backtest_does_not_require_live_limits(self) -> None:
        # Backtest は max_qty / max_notional_jpy 不要（live 専用フィールド）
        cfg = self._config(max_qty=None, max_notional_jpy=None)
        msg = schemas.StartEngine(
            request_id="r",
            engine="Backtest",
            strategy_id="sid",
            config=cfg,
        )
        assert msg.engine == "Backtest"


# ---------------------------------------------------------------------------
# M-Type3: OrderListFilter.status は既知の OrderStatus 値のみ受理する
# ---------------------------------------------------------------------------


class TestOrderListFilterStatus:
    @pytest.mark.parametrize(
        "status",
        [
            "SUBMITTED",
            "ACCEPTED",
            "FILLED",
            "PENDING_CANCEL",
            "CANCELED",
            "EXPIRED",
            "REJECTED",
        ],
    )
    def test_known_status_accepted(self, status: str) -> None:
        f = schemas.OrderListFilter(status=status)  # type: ignore[arg-type]
        assert f.status == status

    def test_none_status_accepted(self) -> None:
        f = schemas.OrderListFilter()
        assert f.status is None

    def test_unknown_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            schemas.OrderListFilter(status="not-a-status")  # type: ignore[arg-type]
