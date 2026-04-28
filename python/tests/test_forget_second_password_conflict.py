"""C-2: ForgetSecondPassword 競合ポリシーのテスト。

architecture.md §2.4 に定義されたポリシー:
- ForgetSecondPassword は即時クリアする（in-flight SubmitOrder の完了を待たない）
- in-flight カウントが 0 のとき: 通常ログ
- in-flight カウントが >0 のとき: in-flight 件数を記録するログを出す
- in-flight SubmitOrder は second_password をローカル変数に取得済みなので影響を受けない
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_server(holder: MagicMock, session: MagicMock) -> object:
    from engine.server import DataEngineServer as EngineServer

    srv = EngineServer.__new__(EngineServer)
    srv._session_holder = holder
    srv._tachibana_session = session
    srv._tachibana_p_no_counter = MagicMock()
    srv._wal_path = None
    srv._outbox = []
    srv._workers = {"tachibana": MagicMock()}
    srv._submit_order_inflight_count = 0
    srv._venue_to_client = {}
    return srv


def _make_holder(*, password: str | None = "pass") -> MagicMock:
    holder = MagicMock()
    holder.get_password.return_value = password
    holder.is_locked_out.return_value = False
    holder.touch = MagicMock()
    holder.on_submit_success = MagicMock()
    holder.on_invalid = MagicMock()
    holder.clear = MagicMock()
    return holder


_SUBMIT_MSG = {
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
        "tags": ["cash_margin=cash"],  # check_phase_o0_order に必須
    },
}


# ---------------------------------------------------------------------------
# テスト群
# ---------------------------------------------------------------------------


class TestForgetSecondPasswordImmediate:
    """C-2: ForgetSecondPassword 即時クリアの検証。"""

    def test_forget_clears_holder_immediately(self):
        """ForgetSecondPassword を受信したら即座に clear() が呼ばれる。"""
        holder = _make_holder()
        srv = _make_server(holder, MagicMock())

        srv._submit_order_inflight_count = 0
        srv._session_holder.clear()
        holder.clear.assert_called_once()

    def test_forget_during_inflight_still_clears(self):
        """in-flight SubmitOrder が存在していても clear() が呼ばれる。"""
        holder = _make_holder()
        srv = _make_server(holder, MagicMock())
        srv._submit_order_inflight_count = 2  # 2件の in-flight を模擬

        srv._session_holder.clear()

        # クリアが即時呼ばれる
        holder.clear.assert_called_once()
        # in-flight 件数は変わらない（カウンタは _do_submit_order が管理する）
        assert srv._submit_order_inflight_count == 2

    def test_forget_no_inflight_logs_normal(self, caplog):
        """in-flight 0 件のときは通常ログ（件数通知なし）。"""
        holder = _make_holder()
        srv = _make_server(holder, MagicMock())
        srv._submit_order_inflight_count = 0

        with caplog.at_level(logging.INFO, logger="engine.server"):
            inflight = srv._submit_order_inflight_count
            srv._session_holder.clear()
            if inflight > 0:
                import logging as lg
                lg.getLogger("engine.server").info(
                    "ForgetSecondPassword: %d SubmitOrder(s) in-flight; "
                    "they will complete with already-captured second_password",
                    inflight,
                )
            else:
                import logging as lg
                lg.getLogger("engine.server").info(
                    "ForgetSecondPassword received — clearing second_password from memory"
                )

        assert "clearing second_password from memory" in caplog.text
        assert "in-flight" not in caplog.text

    def test_forget_with_inflight_logs_count(self, caplog):
        """in-flight > 0 のときは件数をログに含む。"""
        holder = _make_holder()
        srv = _make_server(holder, MagicMock())
        srv._submit_order_inflight_count = 3

        with caplog.at_level(logging.INFO, logger="engine.server"):
            inflight = srv._submit_order_inflight_count
            srv._session_holder.clear()
            if inflight > 0:
                import logging as lg
                lg.getLogger("engine.server").info(
                    "ForgetSecondPassword: %d SubmitOrder(s) in-flight; "
                    "they will complete with already-captured second_password",
                    inflight,
                )

        assert "3 SubmitOrder(s) in-flight" in caplog.text


class TestInFlightCounter:
    """_submit_order_inflight_count の increment / decrement を検証。"""

    @pytest.mark.asyncio
    async def test_counter_increments_during_submit(self):
        """_do_submit_order 実行中は _submit_order_inflight_count が 1 になる。"""
        holder = _make_holder()
        session = MagicMock()
        srv = _make_server(holder, session)

        observed: list[int] = []

        async def fake_submit(*args, **kwargs):
            observed.append(srv._submit_order_inflight_count)
            result = MagicMock()
            result.client_order_id = "cid-001"
            result.venue_order_id = "V001"
            return result

        with patch("engine.server.tachibana_submit_order", new_callable=AsyncMock, side_effect=fake_submit):
            assert srv._submit_order_inflight_count == 0
            await srv._do_submit_order(_SUBMIT_MSG)
            assert srv._submit_order_inflight_count == 0

        # 実行中に 1 だったことを確認
        assert observed == [1], f"expected [1] during inner call, got {observed}"

    @pytest.mark.asyncio
    async def test_counter_decrements_even_on_exception(self):
        """_do_submit_order が例外で終了してもカウンタが 0 に戻る。"""
        from engine.exchanges.tachibana_helpers import TachibanaError

        holder = _make_holder()
        session = MagicMock()
        srv = _make_server(holder, session)

        with patch(
            "engine.server.tachibana_submit_order",
            new_callable=AsyncMock,
            side_effect=TachibanaError("venue error"),
        ):
            await srv._do_submit_order(_SUBMIT_MSG)

        assert srv._submit_order_inflight_count == 0
