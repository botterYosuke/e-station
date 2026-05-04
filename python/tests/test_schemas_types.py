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
