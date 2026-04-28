"""TDD Red → Green: HTTP 送信前にセッション不在等が判明した場合のイベントシーケンス検証。

テストの意図:
  - NOT_LOGGED_IN（セッション未設定）の場合、OrderSubmitted を**経由せず**
    OrderRejected{reason_code="NOT_LOGGED_IN"} が直接発火する
  - SECOND_PASSWORD_LOCKED の場合も同様に OrderSubmitted を**経由せず**
    OrderRejected{reason_code="SECOND_PASSWORD_LOCKED"} が直接発火する
  - 正常にセッションが揃った上で SessionExpiredError が送出された場合は
    OrderSubmitted → OrderRejected{reason_code="SESSION_EXPIRED"} の順序になる
    （OrderAccepted は含まれない）

サーバーインスタンスを直接操作して _do_submit_order を呼び出すパターン。
WebSocket を起動しないことで高速・独立なテストを実現する。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_auth import TachibanaSession, TachibanaSessionHolder
from engine.exchanges.tachibana_helpers import SessionExpiredError
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server():
    """DataEngineServer をワーカーなしで構築するヘルパー。

    WebSocket を起動せず、_do_submit_order を直接呼べる最小構成。
    _startup_tachibana は AsyncMock で置き換えてネットワーク呼び出しを排除する。
    """
    from engine.server import DataEngineServer

    with (
        patch("engine.server.BinanceWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.BybitWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.HyperliquidWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.MexcWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.OkexWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.TachibanaWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
    ):
        srv = DataEngineServer(port=19999, token="test-token", wal_path=Path("/tmp/test_wal.jsonl"))
    return srv


def _make_submit_msg(client_order_id: str = "cid-test-001") -> dict:
    return {
        "op": "SubmitOrder",
        "request_id": "req-test",
        "venue": "tachibana",
        "order": {
            "client_order_id": client_order_id,
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


def _make_demo_session() -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://demo.example/request/"),
        url_master=MasterUrl("https://demo.example/master/"),
        url_price=PriceUrl("https://demo.example/price/"),
        url_event=EventUrl("https://demo.example/event/"),
        url_event_ws="wss://demo.example/event/",
        zyoutoeki_kazei_c="1",
    )


def _collect_outbox(srv) -> list[dict]:
    """outbox に積まれたイベントをすべて取り出してリストにして返す。"""
    events = []
    while srv._outbox:
        events.append(srv._outbox.popleft())
    return events


# ---------------------------------------------------------------------------
# テスト 1: セッション未設定 → OrderSubmitted を経由せず OrderRejected(NOT_LOGGED_IN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_not_set_fires_rejected_no_submitted_no_accepted():
    """セッション未設定のとき OrderSubmitted を発火せずに OrderRejected(NOT_LOGGED_IN) が来る。

    _do_submit_order の M-2 ガード（line 729）が
    OrderSubmitted 発火前（line 745）に置かれているため、
    OrderSubmitted は outbox に積まれない。
    """
    srv = _make_server()
    # セッションを設定せず、第二暗証番号のみ設定する
    srv._session_holder.set_password("secret-pass")
    # セッションは None（デフォルト）

    msg = _make_submit_msg()
    await srv._do_submit_order(msg)

    events = _collect_outbox(srv)
    event_names = [e["event"] for e in events]

    assert "OrderSubmitted" not in event_names, (
        f"OrderSubmitted should NOT be emitted before M-2 guard, got events: {event_names}"
    )
    assert "OrderAccepted" not in event_names, (
        f"OrderAccepted should NOT be emitted, got events: {event_names}"
    )
    assert len(events) == 1, f"Expected exactly 1 event, got: {events}"
    assert events[0]["event"] == "OrderRejected"
    assert events[0]["reason_code"] == "NOT_LOGGED_IN"
    assert events[0]["client_order_id"] == "cid-test-001"


# ---------------------------------------------------------------------------
# テスト 2: lockout 中 → OrderSubmitted を経由せず OrderRejected(SECOND_PASSWORD_LOCKED)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_out_fires_rejected_no_submitted_no_accepted():
    """lockout 中は OrderSubmitted を発火せずに OrderRejected(SECOND_PASSWORD_LOCKED) が来る。

    _do_submit_order の lockout ガード（line 704）が
    OrderSubmitted 発火前（line 745）に置かれているため、
    OrderSubmitted は outbox に積まれない。
    """
    srv = _make_server()
    # lockout を手動で発動させる（max_retries=1）
    srv._session_holder = TachibanaSessionHolder(max_retries=1, lockout_secs=1800.0)
    srv._session_holder.set_password("secret-pass")
    srv._session_holder.on_invalid()  # 1 回で lockout 発動
    assert srv._session_holder.is_locked_out() is True

    msg = _make_submit_msg()
    await srv._do_submit_order(msg)

    events = _collect_outbox(srv)
    event_names = [e["event"] for e in events]

    assert "OrderSubmitted" not in event_names, (
        f"OrderSubmitted should NOT be emitted when locked out, got events: {event_names}"
    )
    assert "OrderAccepted" not in event_names
    assert len(events) == 1
    assert events[0]["event"] == "OrderRejected"
    assert events[0]["reason_code"] == "SECOND_PASSWORD_LOCKED"
    assert events[0]["client_order_id"] == "cid-test-001"


# ---------------------------------------------------------------------------
# テスト 3: SESSION_EXPIRED → OrderSubmitted → OrderRejected(SESSION_EXPIRED)（OrderAccepted なし）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_expired_fires_submitted_then_rejected_no_accepted():
    """SessionExpiredError が送出された場合は OrderSubmitted → OrderRejected の順序になる。

    M-2 ガードを通過した後（セッション設定済み）に SessionExpiredError が上がるため、
    OrderSubmitted は先行発火されるが OrderAccepted は発火されない。
    """
    srv = _make_server()
    srv._session_holder.set_password("secret-pass")
    srv._tachibana_session = _make_demo_session()

    msg = _make_submit_msg("cid-session-exp-001")

    # tachibana_submit_order が SessionExpiredError を raise するよう mock
    with patch("engine.server.tachibana_submit_order", side_effect=SessionExpiredError()):
        await srv._do_submit_order(msg)

    events = _collect_outbox(srv)
    event_names = [e["event"] for e in events]

    assert event_names == ["OrderSubmitted", "OrderRejected"], (
        f"Expected [OrderSubmitted, OrderRejected], got: {event_names}"
    )
    assert "OrderAccepted" not in event_names
    rejected = events[1]
    assert rejected["reason_code"] == "SESSION_EXPIRED"
    assert rejected["client_order_id"] == "cid-session-exp-001"
