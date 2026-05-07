"""HIGH-1 / MEDIUM-2: server.py の kabu_station 発注・取消経路テスト。

R1 レビュー指摘修正 TDD:
- HIGH-1: _do_submit_order_inner / _do_cancel_order の kabu_station 経路追加
- MEDIUM-2: dev_kabu_trade_password_allowed が KabuStationVenue に伝播する
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.server import DataEngineServer, LiveState


# ---------------------------------------------------------------------------
# テストヘルパー
# ---------------------------------------------------------------------------


def _make_server() -> DataEngineServer:
    """DataEngineServer を __new__ + 最低限の属性で構築するヘルパー。"""
    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTED
    srv._connected_venue = "kabu_station"
    srv._kabu_venue = None
    srv._kabu_env = "verify"  # [R2-M1] _startup_kabu_station() 参照時の AttributeError 防止
    srv._submit_order_inflight_count = 0
    srv._kabu_fill_poller_task = None  # [H-1] _clear_kabu_session で参照される
    srv._venue_to_client = {}  # [C-3] _do_submit_order_kabu で参照される

    emitted: list[dict] = []

    class _FakeOutbox:
        def append(self, item):
            emitted.append(item)

        def count(self):
            return len(emitted)

    srv._outbox = _FakeOutbox()
    srv._emitted = emitted
    return srv


def _make_submit_msg(
    venue: str = "kabu_station",
    instrument_id: str = "9433.KabuStation Stock",
    order_side: str = "BUY",
    order_type: str = "MARKET",
    quantity: str = "100",
    price: str | None = None,
    client_order_id: str = "test-cid-001",
) -> dict:
    order = {
        "client_order_id": client_order_id,
        "instrument_id": instrument_id,
        "order_side": order_side,
        "order_type": order_type,
        "quantity": quantity,
        "time_in_force": "DAY",
        "post_only": False,
        "reduce_only": False,
    }
    if price is not None:
        order["price"] = price
    return {
        "op": "SubmitOrder",
        "request_id": "req-001",
        "venue": venue,
        "order": order,
    }


def _make_cancel_msg(
    venue: str = "kabu_station",
    client_order_id: str = "test-cid-001",
    venue_order_id: str = "20230101A01234567",
) -> dict:
    return {
        "op": "CancelOrder",
        "request_id": "req-002",
        "venue": venue,
        "client_order_id": client_order_id,
        "venue_order_id": venue_order_id,
    }


# ---------------------------------------------------------------------------
# HIGH-1 テスト: SubmitOrder の kabu_station 経路
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_routes_to_kabu_venue():
    """venue='kabu_station' の SubmitOrder が kabu_venue.send_order を呼ぶ。"""
    srv = _make_server()

    mock_venue = MagicMock()
    mock_venue.send_order = AsyncMock(return_value={"OrderID": "20230101A01234567"})
    mock_venue.is_trade_locked_out.return_value = False
    srv._kabu_venue = mock_venue

    msg = _make_submit_msg(venue="kabu_station")
    await srv._do_submit_order_inner(msg, ws=None)

    mock_venue.send_order.assert_called_once()
    events = [e.get("event") for e in srv._emitted]
    assert "OrderSubmitted" in events, f"OrderSubmitted が emit されていない: {srv._emitted}"
    assert "OrderAccepted" in events, f"OrderAccepted が emit されていない: {srv._emitted}"

    accepted = next(e for e in srv._emitted if e.get("event") == "OrderAccepted")
    assert accepted["venue_order_id"] == "20230101A01234567"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_not_logged_in_returns_rejected():
    """kabu_venue が None (未ログイン) の場合 OrderRejected{NOT_LOGGED_IN} が返る。"""
    srv = _make_server()
    srv._kabu_venue = None  # 未接続

    msg = _make_submit_msg(venue="kabu_station")
    await srv._do_submit_order_inner(msg, ws=None)

    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events, f"OrderRejected が emit されていない: {srv._emitted}"
    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "NOT_LOGGED_IN"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_trade_password_locked_returns_rejected():
    """取引パスワード lockout 中は OrderRejected{TRADE_PASSWORD_LOCKED} が返る。"""
    srv = _make_server()

    mock_venue = MagicMock()
    mock_venue.is_trade_locked_out.return_value = True
    srv._kabu_venue = mock_venue

    msg = _make_submit_msg(venue="kabu_station")
    await srv._do_submit_order_inner(msg, ws=None)

    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events
    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "TRADE_PASSWORD_LOCKED"


# ---------------------------------------------------------------------------
# HIGH-1 テスト: CancelOrder の kabu_station 経路
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_cancel_order_routes_to_kabu_venue():
    """venue='kabu_station' の CancelOrder が kabu_venue.cancel_order を呼ぶ。"""
    srv = _make_server()

    mock_venue = MagicMock()
    mock_venue.cancel_order = AsyncMock(return_value={"OrderID": "20230101A01234567"})
    mock_venue.is_trade_locked_out.return_value = False
    srv._kabu_venue = mock_venue

    msg = _make_cancel_msg(venue="kabu_station")
    await srv._do_cancel_order(msg, ws=None)

    mock_venue.cancel_order.assert_called_once_with(order_id="20230101A01234567")
    events = [e.get("event") for e in srv._emitted]
    assert "OrderCancelled" in events, f"OrderCancelled が emit されていない: {srv._emitted}"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_cancel_order_not_logged_in_returns_rejected():
    """kabu_venue が None (未ログイン) の場合 OrderRejected{NOT_LOGGED_IN} が返る。"""
    srv = _make_server()
    srv._kabu_venue = None

    msg = _make_cancel_msg(venue="kabu_station")
    await srv._do_cancel_order(msg, ws=None)

    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events
    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "NOT_LOGGED_IN"


# ---------------------------------------------------------------------------
# MEDIUM-2 テスト: dev_kabu_trade_password_allowed の伝播
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
def test_dev_trade_password_allowed_propagated_to_kabu_venue():
    """EngineServer(dev_kabu_trade_password_allowed=True) で作った KabuStationVenue の
    _dev_trade_password_allowed が True になること。"""
    from engine.exchanges.kabusapi import KabuStationVenue

    created_venues: list[KabuStationVenue] = []

    original_init = KabuStationVenue.__init__

    def _capture_init(self, *, env="verify", dev_login_allowed=False, dev_trade_password_allowed=False):
        original_init(
            self,
            env=env,
            dev_login_allowed=dev_login_allowed,
            dev_trade_password_allowed=dev_trade_password_allowed,
        )
        created_venues.append(self)

    with patch.object(KabuStationVenue, "__init__", _capture_init):
        srv = DataEngineServer.__new__(DataEngineServer)
        # _dev_kabu_trade_password_allowed を直接設定して _startup_kabu_station の挙動をテスト
        srv._dev_kabu_login_allowed = False
        srv._dev_kabu_trade_password_allowed = True
        srv._kabu_env = "verify"  # H-2: インスタンスキャッシュ
        srv._kabu_venue = None
        srv._kabu_login_inflight = asyncio.Lock()
        srv._kabu_startup_task = None
        srv._kabu_fill_poller_task = None  # [H-1] _startup_kabu_station で参照される
        srv._live_state = LiveState.DISCONNECTED
        # C1: PUSH ssid 採番に使うセッション ID（__new__ では設定されないため手動で補充）
        import uuid as _uuid
        srv._engine_session_id = _uuid.UUID("00000000-0000-0000-0000-000000000001")
        srv._kabu_push_ssid = None
        srv._kabu_push_seq = 0

        emitted: list[dict] = []

        class _FakeOutbox:
            def append(self, item):
                emitted.append(item)

        srv._outbox = _FakeOutbox()

        # startup_login をモックアウトして KabuStationVenue 構築だけを検証する
        async def _run():
            with patch(
                "engine.exchanges.kabusapi.KabuStationVenue.startup_login",
                new_callable=AsyncMock,
            ) as mock_login:
                mock_login.return_value = "dummy_token"
                # _startup_kabu_station を呼ぶためには _emit が必要
                srv._connected_venue = None

                def _emit(item):
                    emitted.append(item)

                srv._emit = _emit
                await srv._startup_kabu_station(request_id=None)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    assert len(created_venues) >= 1, "KabuStationVenue が構築されていない"
    assert created_venues[-1]._dev_trade_password_allowed is True, (
        f"dev_trade_password_allowed が True に伝播していない: "
        f"{created_venues[-1]._dev_trade_password_allowed!r}"
    )


# ---------------------------------------------------------------------------
# B-1 CRITICAL: result["OrderID"] KeyError → UI 永久フリーズ
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_missing_order_id_emits_rejected():
    """send_order が OrderID のないレスポンスを返したとき OrderRejected{MISSING_ORDER_ID} が emit される。

    result["OrderID"] で KeyError が起きて UI が永久フリーズする代わりに、
    OrderRejected を emit して graceful に処理する。
    """
    srv = _make_server()

    mock_venue = MagicMock()
    mock_venue.send_order = AsyncMock(return_value={"Result": 0})  # OrderID なし
    mock_venue.is_trade_locked_out.return_value = False
    srv._kabu_venue = mock_venue

    msg = _make_submit_msg(venue="kabu_station")
    await srv._do_submit_order_inner(msg, ws=None)

    events = [e.get("event") for e in srv._emitted]
    # nautilus 流: OrderSubmitted → OrderRejected のシーケンスが正しい（tachibana と対称）
    assert "OrderSubmitted" in events, f"OrderSubmitted が emit されていない: {srv._emitted}"
    assert "OrderRejected" in events, f"OrderRejected が emit されていない: {srv._emitted}"

    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "MISSING_ORDER_ID"
    # OrderAccepted は emit されてはならない
    assert "OrderAccepted" not in events, "OrderID なしで OrderAccepted が emit されてはならない"


# ---------------------------------------------------------------------------
# B-2 HIGH: LIMIT 注文で price=None のとき OrderRejected を emit
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_limit_order_without_price_returns_rejected():
    """LIMIT 注文で price が None のとき OrderRejected{INVALID_PRICE} が emit される。

    price=None のまま 0.0 を API に送信してはならない。
    """
    srv = _make_server()

    mock_venue = MagicMock()
    mock_venue.send_order = AsyncMock(return_value={"Result": 0, "OrderID": "20230101A01234567"})
    mock_venue.is_trade_locked_out.return_value = False
    srv._kabu_venue = mock_venue

    # price フィールドなし (None) の LIMIT 注文
    msg = _make_submit_msg(venue="kabu_station", order_type="LIMIT", price=None)
    await srv._do_submit_order_inner(msg, ws=None)

    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events, f"OrderRejected が emit されていない: {srv._emitted}"

    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "INVALID_PRICE"
    # send_order は呼ばれてはならない
    mock_venue.send_order.assert_not_called()


# ---------------------------------------------------------------------------
# B-3 MEDIUM: _get_order_client が token=None のとき KabuApiError を raise
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_kabu_venue_token_none_returns_rejected():
    """KabuStationVenue._token が None (未ログイン) の場合、OrderRejected{NOT_LOGGED_IN} が返る。

    _get_order_client() が token=None チェックで KabuApiError を raise し、
    server の except Exception ブランチが OrderRejected を emit することを確認する。
    """
    from engine.exchanges.kabusapi import KabuStationVenue

    srv = _make_server()

    # token=None のまま venue を構築する
    venue = KabuStationVenue(env="verify")
    # token を None に保ったまま（startup_login 未呼び出し）
    assert venue._token is None

    mock_holder = MagicMock()
    mock_holder.is_locked_out.return_value = False
    venue._trade_password_holder = mock_holder

    srv._kabu_venue = venue

    msg = _make_submit_msg(venue="kabu_station")
    await srv._do_submit_order_inner(msg, ws=None)

    events = [e.get("event") for e in srv._emitted]
    # token=None → KabuApiError → except Exception → OrderRejected
    assert "OrderRejected" in events, f"OrderRejected が emit されていない: {srv._emitted}"


# ---------------------------------------------------------------------------
# M-1 新規テスト: KabuTokenExpiredError / KabuConnectionError / KabuRateLimitError
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_token_expired_clears_session():
    """KabuTokenExpiredError で _clear_kabu_session() が呼ばれ FSM がリセットされる。"""
    from engine.exchanges.kabusapi_auth import KabuTokenExpiredError
    srv = _make_server()
    mock_venue = MagicMock()
    mock_venue.is_trade_locked_out.return_value = False
    mock_venue.send_order = AsyncMock(side_effect=KabuTokenExpiredError(401, "token expired"))
    srv._kabu_venue = mock_venue
    srv._connected_venue = "kabu_station"
    srv._live_state = LiveState.CONNECTED

    msg = _make_submit_msg(venue="kabu_station")
    await srv._do_submit_order_inner(msg, ws=None)

    assert srv._connected_venue is None
    assert srv._live_state == LiveState.DISCONNECTED
    mock_venue.clear.assert_called_once()
    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events
    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "NOT_LOGGED_IN"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_connection_error_clears_session():
    """KabuConnectionError で _clear_kabu_session() が呼ばれ FSM がリセットされる。"""
    from engine.exchanges.kabusapi_auth import KabuConnectionError as KabuConnErr
    srv = _make_server()
    mock_venue = MagicMock()
    mock_venue.is_trade_locked_out.return_value = False
    mock_venue.send_order = AsyncMock(side_effect=KabuConnErr(503, "connection refused"))
    srv._kabu_venue = mock_venue
    srv._connected_venue = "kabu_station"
    srv._live_state = LiveState.CONNECTED

    msg = _make_submit_msg(venue="kabu_station")
    await srv._do_submit_order_inner(msg, ws=None)

    assert srv._connected_venue is None
    assert srv._live_state == LiveState.DISCONNECTED
    mock_venue.clear.assert_called_once()
    # [M-5] OrderRejected が emit されることを確認
    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events, f"OrderRejected が emit されていない: {srv._emitted}"
    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "CONNECTION_ERROR"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_rate_limited_emits_rate_limited_reason_code():
    """流量制限エラーで OrderRejected{RATE_LIMITED} が emit される。"""
    from engine.exchanges.kabusapi_auth import KabuRateLimitError
    srv = _make_server()
    mock_venue = MagicMock()
    mock_venue.is_trade_locked_out.return_value = False
    mock_venue.send_order = AsyncMock(side_effect=KabuRateLimitError(429, "rate limited"))
    srv._kabu_venue = mock_venue

    msg = _make_submit_msg(venue="kabu_station")
    await srv._do_submit_order_inner(msg, ws=None)

    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events
    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "RATE_LIMITED"
    # rate_limited は _clear_kabu_session() を呼ばない設計（負の不変条件 pin）
    mock_venue.clear.assert_not_called()


# ---------------------------------------------------------------------------
# M-2 新規テスト: _do_cancel_order_kabu の対応 3 件
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_cancel_order_token_expired_clears_session():
    """KabuTokenExpiredError で _clear_kabu_session() が呼ばれ FSM がリセットされる（cancel 経路）。"""
    from engine.exchanges.kabusapi_auth import KabuTokenExpiredError
    srv = _make_server()
    mock_venue = MagicMock()
    mock_venue.is_trade_locked_out.return_value = False
    mock_venue.cancel_order = AsyncMock(side_effect=KabuTokenExpiredError(401, "token expired"))
    srv._kabu_venue = mock_venue
    srv._connected_venue = "kabu_station"
    srv._live_state = LiveState.CONNECTED

    msg = _make_cancel_msg(venue="kabu_station")
    await srv._do_cancel_order(msg, ws=None)

    assert srv._connected_venue is None
    assert srv._live_state == LiveState.DISCONNECTED
    mock_venue.clear.assert_called_once()
    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events
    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "NOT_LOGGED_IN"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_cancel_order_connection_error_clears_session():
    """KabuConnectionError で _clear_kabu_session() が呼ばれ FSM がリセットされる（cancel 経路）。"""
    from engine.exchanges.kabusapi_auth import KabuConnectionError as KabuConnErr
    srv = _make_server()
    mock_venue = MagicMock()
    mock_venue.is_trade_locked_out.return_value = False
    mock_venue.cancel_order = AsyncMock(side_effect=KabuConnErr(503, "connection refused"))
    srv._kabu_venue = mock_venue
    srv._connected_venue = "kabu_station"
    srv._live_state = LiveState.CONNECTED

    msg = _make_cancel_msg(venue="kabu_station")
    await srv._do_cancel_order(msg, ws=None)

    assert srv._connected_venue is None
    assert srv._live_state == LiveState.DISCONNECTED
    mock_venue.clear.assert_called_once()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_cancel_order_rate_limited_emits_rate_limited_reason_code():
    """流量制限エラーで OrderRejected{RATE_LIMITED} が emit される（cancel 経路）。"""
    from engine.exchanges.kabusapi_auth import KabuRateLimitError
    srv = _make_server()
    mock_venue = MagicMock()
    mock_venue.is_trade_locked_out.return_value = False
    mock_venue.cancel_order = AsyncMock(side_effect=KabuRateLimitError(429, "rate limited"))
    srv._kabu_venue = mock_venue

    msg = _make_cancel_msg(venue="kabu_station")
    await srv._do_cancel_order(msg, ws=None)

    events = [e.get("event") for e in srv._emitted]
    assert "OrderRejected" in events
    rejected = next(e for e in srv._emitted if e.get("event") == "OrderRejected")
    assert rejected.get("reason_code") == "RATE_LIMITED"
    # rate_limited は _clear_kabu_session() を呼ばない設計
    mock_venue.clear.assert_not_called()


# ---------------------------------------------------------------------------
# [H-1] 先物・OP dispatch テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_future_dispatches_to_send_order_future():
    """instrument_id が '.KabuStation Future' で終わる場合 send_order_future() を呼ぶ。"""
    srv = _make_server()

    mock_venue = MagicMock()
    mock_venue.send_order_future = AsyncMock(return_value={"OrderID": "20240101F01234567"})
    mock_venue.is_trade_locked_out.return_value = False
    srv._kabu_venue = mock_venue

    msg = _make_submit_msg(
        venue="kabu_station",
        instrument_id="169090018.KabuStation Future",
        order_side="BUY",
        order_type="MARKET",
        quantity="1",
    )
    await srv._do_submit_order_inner(msg, ws=None)

    mock_venue.send_order_future.assert_called_once()
    # send_order は呼ばれない
    mock_venue.send_order.assert_not_called()
    events = [e.get("event") for e in srv._emitted]
    assert "OrderAccepted" in events, f"OrderAccepted が emit されていない: {srv._emitted}"
    accepted = next(e for e in srv._emitted if e.get("event") == "OrderAccepted")
    assert accepted["venue_order_id"] == "20240101F01234567"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_option_dispatches_to_send_order_option():
    """instrument_id が '.KabuStation Option' で終わる場合 send_order_option() を呼ぶ。"""
    srv = _make_server()

    mock_venue = MagicMock()
    mock_venue.send_order_option = AsyncMock(return_value={"OrderID": "20240101O01234567"})
    mock_venue.is_trade_locked_out.return_value = False
    srv._kabu_venue = mock_venue

    msg = _make_submit_msg(
        venue="kabu_station",
        instrument_id="169090019.KabuStation Option",
        order_side="BUY",
        order_type="MARKET",
        quantity="1",
    )
    await srv._do_submit_order_inner(msg, ws=None)

    mock_venue.send_order_option.assert_called_once()
    mock_venue.send_order.assert_not_called()
    events = [e.get("event") for e in srv._emitted]
    assert "OrderAccepted" in events, f"OrderAccepted が emit されていない: {srv._emitted}"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_server_submit_order_future_limit_order():
    """先物 LIMIT 注文で front_order_type=20 + price が send_order_future に渡る。"""
    srv = _make_server()

    mock_venue = MagicMock()
    mock_venue.send_order_future = AsyncMock(return_value={"OrderID": "20240101F99999999"})
    mock_venue.is_trade_locked_out.return_value = False
    srv._kabu_venue = mock_venue

    msg = _make_submit_msg(
        venue="kabu_station",
        instrument_id="169090018.KabuStation Future",
        order_side="BUY",
        order_type="LIMIT",
        quantity="1",
        price="28500.0",
    )
    await srv._do_submit_order_inner(msg, ws=None)

    mock_venue.send_order_future.assert_called_once()
    call_kwargs = mock_venue.send_order_future.call_args.kwargs
    assert call_kwargs["front_order_type"] == 20, (
        f"LIMIT 注文の front_order_type は 20 であるべき; got: {call_kwargs}"
    )
    assert call_kwargs["price"] == 28500.0, (
        f"price が送られていない; got: {call_kwargs}"
    )
