"""M-1: cancel_all の partial failure をクライアントに通知するテスト。

テストケース:
    - canceled=2, failed=1 を返すモックで log.warning が呼ばれること
    - failed=0 のとき warning が呼ばれないこと
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: DataEngineServer のミニマル構築
# ---------------------------------------------------------------------------


def _make_server():
    """テスト用に DataEngineServer を最小限の設定で構築する。"""
    from engine.server import DataEngineServer

    server = DataEngineServer.__new__(DataEngineServer)
    # 必要最低限の属性を手動設定
    import asyncio
    from collections import deque
    from engine.server import _Outbox

    server._outbox_event = asyncio.Event()
    server._outbox = _Outbox(server._outbox_event.set)
    server._workers = {"tachibana": MagicMock()}
    server._session_holder = MagicMock()
    server._session_holder.is_locked_out.return_value = False
    server._session_holder.get_password.return_value = "dummy_password"
    server._tachibana_session = MagicMock()
    server._tachibana_p_no_counter = MagicMock()
    return server


# ---------------------------------------------------------------------------
# M-1-A: failed_count > 0 のとき log.warning が呼ばれること
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_failure_emits_warning():
    """canceled=2, failed=1 のとき _do_cancel_all_orders が log.warning を呼ぶ。"""
    from engine.exchanges.tachibana_orders import CancelAllResult

    server = _make_server()

    msg = {
        "request_id": "req-001",
        "venue": "tachibana",
        "instrument_id": None,
        "order_side": None,
    }

    cancel_result = CancelAllResult(canceled_count=2, failed_count=1)

    with patch(
        "engine.server.tachibana_cancel_all_orders",
        new_callable=AsyncMock,
        return_value=cancel_result,
    ), patch("engine.server.log") as mock_log:
        await server._do_cancel_all_orders(msg)

    mock_log.warning.assert_called_once()
    call_args = mock_log.warning.call_args
    # フォーマット文字列と引数が partial failure に関するものであることを確認
    assert "partial" in call_args[0][0].lower() or "cancel" in call_args[0][0].lower()


# ---------------------------------------------------------------------------
# M-1-B: failed_count > 0 のとき PARTIAL_CANCEL_FAILURE コードで Error イベントが返ること
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_failure_emits_error_event():
    """canceled=2, failed=1 のとき outbox に PARTIAL_CANCEL_FAILURE の Error イベントが積まれる。"""
    from engine.exchanges.tachibana_orders import CancelAllResult

    server = _make_server()

    msg = {
        "request_id": "req-002",
        "venue": "tachibana",
        "instrument_id": None,
        "order_side": None,
    }

    cancel_result = CancelAllResult(canceled_count=2, failed_count=1)

    with patch(
        "engine.server.tachibana_cancel_all_orders",
        new_callable=AsyncMock,
        return_value=cancel_result,
    ):
        await server._do_cancel_all_orders(msg)

    # outbox のイベントを確認
    events = list(server._outbox._q)
    assert len(events) == 1, f"期待 1 件のイベント、実際: {len(events)} 件"
    event = events[0]
    assert event["event"] == "Error"
    assert event["code"] == "PARTIAL_CANCEL_FAILURE"
    assert "canceled=2" in event["message"]
    assert "failed=1" in event["message"]


# ---------------------------------------------------------------------------
# M-1-C: failed_count == 0 のとき warning が呼ばれないこと
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_warning_when_all_canceled():
    """canceled=3, failed=0 のとき log.warning が呼ばれないこと。"""
    from engine.exchanges.tachibana_orders import CancelAllResult

    server = _make_server()

    msg = {
        "request_id": "req-003",
        "venue": "tachibana",
        "instrument_id": None,
        "order_side": None,
    }

    cancel_result = CancelAllResult(canceled_count=3, failed_count=0)

    with patch(
        "engine.server.tachibana_cancel_all_orders",
        new_callable=AsyncMock,
        return_value=cancel_result,
    ), patch("engine.server.log") as mock_log:
        await server._do_cancel_all_orders(msg)

    # warning が呼ばれていないこと
    mock_log.warning.assert_not_called()
