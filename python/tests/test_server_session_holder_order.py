"""Group D: server.py セッションホルダー呼び出し順序・on_submit_success テスト。

D-1: touch() が get_password() の前に呼ばれること。
D-2: modify / cancel / cancel-all 正常完了時に on_submit_success() が呼ばれること。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_holder(*, password: str = "pass", locked_out: bool = False) -> MagicMock:
    """TachibanaSessionHolder の最小モック。"""
    holder = MagicMock()
    holder.get_password.return_value = password
    holder.is_locked_out.return_value = locked_out
    holder.touch = MagicMock()
    holder.on_submit_success = MagicMock()
    holder.on_invalid = MagicMock()
    holder.clear = MagicMock()
    return holder


def make_tachibana_session() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# D-1: touch() が get_password() の前にある（submit / modify / cancel / cancel_all）
# ---------------------------------------------------------------------------


class TestTouchBeforeGetPassword:
    """touch() が get_password() より前に呼ばれること。

    call_args_list で呼び出し順序を確認する。
    """

    @pytest.mark.asyncio
    async def test_submit_touch_before_get_password(self):
        """_do_submit_order: touch() が get_password() の前にある。"""
        import sys
        import types

        # tachibana_submit_order を AsyncMock で置換
        fake_result = MagicMock()
        fake_result.client_order_id = "cid-001"
        fake_result.venue_order_id = "V001"

        holder = make_session_holder()
        session = make_tachibana_session()

        call_order: list[str] = []
        orig_get_password = holder.get_password

        def tracking_get_password():
            call_order.append("get_password")
            return orig_get_password()

        def tracking_touch():
            call_order.append("touch")

        holder.get_password = MagicMock(side_effect=tracking_get_password)
        holder.touch = MagicMock(side_effect=tracking_touch)

        with patch(
            "engine.server.tachibana_submit_order",
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            from engine.server import DataEngineServer as EngineServer

            srv = _make_server(holder, session)
            msg = {
                "op": "SubmitOrder",
                "request_id": "req-1",
                "venue": "tachibana",
                "order": {
                    "client_order_id": "cid-001",
                    "instrument_id": "7203.TSE",
                    "order_side": "BUY",
                    "order_type": "MARKET",
                    "quantity": "100",
                    "time_in_force": "DAY",
                    "post_only": False,
                    "reduce_only": False,
                    "tags": ["cash_margin=cash"],
                },
            }
            await srv._do_submit_order(msg)

        # touch が先、get_password が後
        touch_idx = call_order.index("touch")
        gp_idx = call_order.index("get_password")
        assert touch_idx < gp_idx, (
            f"touch() must be called before get_password(); order={call_order}"
        )

    @pytest.mark.asyncio
    async def test_modify_touch_before_get_password(self):
        """_do_modify_order: touch() が get_password() の前にある。"""
        holder = make_session_holder()
        session = make_tachibana_session()

        call_order: list[str] = []
        orig_gp = holder.get_password

        def tracking_get_password():
            call_order.append("get_password")
            return orig_gp()

        def tracking_touch():
            call_order.append("touch")

        holder.get_password = MagicMock(side_effect=tracking_get_password)
        holder.touch = MagicMock(side_effect=tracking_touch)

        with patch(
            "engine.server.tachibana_modify_order",
            new_callable=AsyncMock,
            return_value=None,
        ):
            srv = _make_server(holder, session)
            msg = {
                "op": "ModifyOrder",
                "request_id": "req-2",
                "venue": "tachibana",
                "client_order_id": "cid-002",
                "venue_order_id": "V002",
                "change": {"new_price": "3600"},
            }
            await srv._do_modify_order(msg)

        touch_idx = call_order.index("touch")
        gp_idx = call_order.index("get_password")
        assert touch_idx < gp_idx, (
            f"touch() must be called before get_password() in modify; order={call_order}"
        )

    @pytest.mark.asyncio
    async def test_cancel_touch_before_get_password(self):
        """_do_cancel_order: touch() が get_password() の前にある。"""
        holder = make_session_holder()
        session = make_tachibana_session()

        call_order: list[str] = []
        orig_gp = holder.get_password

        def tracking_get_password():
            call_order.append("get_password")
            return orig_gp()

        def tracking_touch():
            call_order.append("touch")

        holder.get_password = MagicMock(side_effect=tracking_get_password)
        holder.touch = MagicMock(side_effect=tracking_touch)

        with patch(
            "engine.server.tachibana_cancel_order",
            new_callable=AsyncMock,
            return_value=None,
        ):
            srv = _make_server(holder, session)
            msg = {
                "op": "CancelOrder",
                "request_id": "req-3",
                "venue": "tachibana",
                "client_order_id": "cid-003",
                "venue_order_id": "V003",
            }
            await srv._do_cancel_order(msg)

        touch_idx = call_order.index("touch")
        gp_idx = call_order.index("get_password")
        assert touch_idx < gp_idx, (
            f"touch() must be called before get_password() in cancel; order={call_order}"
        )


# ---------------------------------------------------------------------------
# D-2: on_submit_success() が modify / cancel / cancel_all の正常完了後に呼ばれること
# ---------------------------------------------------------------------------


class TestOnSubmitSuccessOnSuccess:
    @pytest.mark.asyncio
    async def test_modify_success_calls_on_submit_success(self):
        holder = make_session_holder()
        session = make_tachibana_session()

        with patch(
            "engine.server.tachibana_modify_order",
            new_callable=AsyncMock,
            return_value=None,
        ):
            srv = _make_server(holder, session)
            msg = {
                "op": "ModifyOrder",
                "request_id": "req-m",
                "venue": "tachibana",
                "client_order_id": "cid-m",
                "venue_order_id": "Vm",
                "change": {"new_price": "3500"},
            }
            await srv._do_modify_order(msg)

        holder.on_submit_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_success_calls_on_submit_success(self):
        holder = make_session_holder()
        session = make_tachibana_session()

        with patch(
            "engine.server.tachibana_cancel_order",
            new_callable=AsyncMock,
            return_value=None,
        ):
            srv = _make_server(holder, session)
            msg = {
                "op": "CancelOrder",
                "request_id": "req-c",
                "venue": "tachibana",
                "client_order_id": "cid-c",
                "venue_order_id": "Vc",
            }
            await srv._do_cancel_order(msg)

        holder.on_submit_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_all_success_calls_on_submit_success(self):
        holder = make_session_holder()
        session = make_tachibana_session()

        from engine.exchanges.tachibana_orders import CancelAllResult

        with patch(
            "engine.server.tachibana_cancel_all_orders",
            new_callable=AsyncMock,
            return_value=CancelAllResult(canceled_count=1, failed_count=0),
        ):
            srv = _make_server(holder, session)
            msg = {
                "op": "CancelAllOrders",
                "request_id": "req-ca",
                "venue": "tachibana",
            }
            await srv._do_cancel_all_orders(msg)

        holder.on_submit_success.assert_called_once()


# ---------------------------------------------------------------------------
# Helper: EngineServer の最小インスタンス化
# ---------------------------------------------------------------------------


def _make_server(holder: MagicMock, session: MagicMock) -> object:
    """EngineServer を最小構成で組み立てる。"""
    from engine.server import DataEngineServer as EngineServer

    srv = EngineServer.__new__(EngineServer)
    srv._session_holder = holder
    srv._tachibana_session = session
    srv._tachibana_p_no_counter = MagicMock()
    srv._wal_path = None
    srv._outbox = []
    # _workers に "tachibana" が含まれていないと early-return する
    srv._workers = {"tachibana": MagicMock()}
    # C-2: in-flight カウンタ（_do_submit_order で使用）
    srv._submit_order_inflight_count = 0
    # Phase O2: venue_order_id → client_order_id 逆引きマップ
    srv._venue_to_client = {}
    return srv
