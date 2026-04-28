"""N3.A: HyperliquidExecutionClient テスト

検証項目:
- TestClientInitSafety: セッションなし / max_qty=0 で ValueError
- TestSubmitOrder: mock HTTP レスポンスで成行・指値注文の往復確認
  - submit_order が正しい HTTP body で呼ばれること
  - OrderAccepted が生成されること（venue_order_id が正しいこと）
- TestCancelOrder: キャンセルリクエストの往復確認
- TestModifyOrder: modify は OrderModifyRejected が返ること
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import ClientId, Venue
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from engine.exchanges.hyperliquid_orders import (
    HyperliquidSession,
    OrderResult,
    cancel_order as _hl_cancel_order,
    submit_order as _hl_submit_order,
)
from engine.nautilus.clients.hyperliquid import HyperliquidExecutionClient


# ---------------------------------------------------------------------------
# ヘルパ: モック署名関数
# ---------------------------------------------------------------------------


def _mock_signer(data: bytes) -> dict:
    return {"r": "0x" + "a" * 64, "s": "0x" + "b" * 64, "v": 27}


def _make_session() -> HyperliquidSession:
    return HyperliquidSession(
        address="0x1234567890123456789012345678901234567890",
        signer=_mock_signer,
    )


def _make_client(max_qty_per_order: float = 1.0) -> HyperliquidExecutionClient:
    """functional テスト用クライアントを作成する。nautilus TestStubs を使う。"""
    client = HyperliquidExecutionClient(
        loop=asyncio.new_event_loop(),
        client_id=ClientId("HYPERLIQUID"),
        venue=Venue("HYPERLIQUID"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        instrument_provider=InstrumentProvider.__new__(InstrumentProvider),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
        session=_make_session(),
        max_qty_per_order=max_qty_per_order,
    )
    # generate_* をモック化して呼出確認できるようにする
    client.generate_order_submitted = MagicMock()
    client.generate_order_accepted = MagicMock()
    client.generate_order_denied = MagicMock()
    client.generate_order_rejected = MagicMock()
    client.generate_order_cancel_rejected = MagicMock()
    client.generate_order_modify_rejected = MagicMock()
    return client


# ---------------------------------------------------------------------------
# ヘルパ: フェイク Order / Command
# ---------------------------------------------------------------------------


class _FakeOrder:
    def __init__(
        self,
        client_order_id: str = "HL-001",
        instrument_id: str = "BTC.HYPERLIQUID",
        side: int = 1,   # BUY
        order_type: int = 1,  # MARKET
        quantity: str = "0.1",
        time_in_force: int = 5,  # DAY/GTC
        price: str | None = None,
        tags: list[str] | None = None,
    ):
        self.client_order_id = client_order_id
        self.instrument_id = instrument_id
        self.side = side
        self.order_type = order_type
        self.quantity = quantity
        self.time_in_force = time_in_force
        self.price = price
        self.trigger_price = None
        self.trigger_type = None
        self.expire_time = None
        self.tags = tags or []
        self.is_post_only = False
        self.is_reduce_only = False


class _FakeSubmitCommand:
    def __init__(self, order: _FakeOrder, strategy_id: str = "strat-hl"):
        self.order = order
        self.strategy_id = strategy_id
        self.venue_order_id = None


class _FakeCancelCommand:
    def __init__(
        self,
        order: _FakeOrder,
        venue_order_id: str = "12345",
        strategy_id: str = "strat-hl",
    ):
        self.order = order
        self.venue_order_id = venue_order_id
        self.strategy_id = strategy_id


class _FakeModifyCommand:
    def __init__(self, order: _FakeOrder, strategy_id: str = "strat-hl"):
        self.order = order
        self.strategy_id = strategy_id
        self.venue_order_id = "12345"
        self.new_quantity = None
        self.new_price = None


# ---------------------------------------------------------------------------
# TestClientInitSafety
# ---------------------------------------------------------------------------


class TestClientInitSafety:
    """セッション未設定 / max_qty=0 で ValueError が上がること（N3.A 安全装置）。

    安全ガードは super().__init__() より前で発動するため、MagicMock() で OK。
    """

    def test_missing_session_raises_value_error(self):
        with pytest.raises(ValueError, match="session"):
            HyperliquidExecutionClient(
                loop=asyncio.new_event_loop(),
                client_id=MagicMock(),
                venue=MagicMock(),
                oms_type=MagicMock(),
                account_type=MagicMock(),
                base_currency=None,
                instrument_provider=create_autospec(InstrumentProvider, instance=True),
                msgbus=MagicMock(),
                cache=MagicMock(),
                clock=MagicMock(),
                session=None,
                max_qty_per_order=0.1,
            )

    def test_max_qty_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="max_qty_per_order"):
            HyperliquidExecutionClient(
                loop=asyncio.new_event_loop(),
                client_id=MagicMock(),
                venue=MagicMock(),
                oms_type=MagicMock(),
                account_type=MagicMock(),
                base_currency=None,
                instrument_provider=create_autospec(InstrumentProvider, instance=True),
                msgbus=MagicMock(),
                cache=MagicMock(),
                clock=MagicMock(),
                session=_make_session(),
                max_qty_per_order=0.0,
            )

    def test_negative_max_qty_raises_value_error(self):
        with pytest.raises(ValueError, match="max_qty_per_order"):
            HyperliquidExecutionClient(
                loop=asyncio.new_event_loop(),
                client_id=MagicMock(),
                venue=MagicMock(),
                oms_type=MagicMock(),
                account_type=MagicMock(),
                base_currency=None,
                instrument_provider=create_autospec(InstrumentProvider, instance=True),
                msgbus=MagicMock(),
                cache=MagicMock(),
                clock=MagicMock(),
                session=_make_session(),
                max_qty_per_order=-1.0,
            )


# ---------------------------------------------------------------------------
# TestSubmitOrder
# ---------------------------------------------------------------------------


class TestSubmitOrder:
    """_submit_order → submit_order 委譲 + OrderAccepted 生成確認。"""

    @pytest.mark.asyncio
    async def test_market_order_calls_submit_and_generates_accepted(self):
        """成行注文: submit_order が呼ばれ OrderAccepted が生成されること。"""
        client = _make_client()
        order = _FakeOrder(order_type=1, quantity="0.1")  # MARKET
        cmd = _FakeSubmitCommand(order)

        ok_result = OrderResult(venue_order_id="99999", status="ok")
        with patch(
            "engine.nautilus.clients.hyperliquid._hl_submit_order",
            new=AsyncMock(return_value=ok_result),
        ):
            await client._submit_order(cmd)

        client.generate_order_submitted.assert_called_once()
        client.generate_order_accepted.assert_called_once()

        # venue_order_id が "99999" で渡されていること
        call_kwargs = client.generate_order_accepted.call_args
        from nautilus_trader.model.identifiers import VenueOrderId
        assert call_kwargs.kwargs["venue_order_id"] == VenueOrderId("99999")

    @pytest.mark.asyncio
    async def test_limit_order_calls_submit_and_generates_accepted(self):
        """指値注文: submit_order が呼ばれ OrderAccepted が生成されること。"""
        client = _make_client()
        order = _FakeOrder(order_type=2, quantity="0.1", price="50000")  # LIMIT
        cmd = _FakeSubmitCommand(order)

        ok_result = OrderResult(venue_order_id="88888", status="ok")
        with patch(
            "engine.nautilus.clients.hyperliquid._hl_submit_order",
            new=AsyncMock(return_value=ok_result),
        ):
            await client._submit_order(cmd)

        client.generate_order_submitted.assert_called_once()
        client.generate_order_accepted.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_failure_generates_denied(self):
        """submit_order が例外を上げた場合 OrderDenied が生成されること。"""
        client = _make_client()
        order = _FakeOrder(order_type=1, quantity="0.1")
        cmd = _FakeSubmitCommand(order)

        with patch(
            "engine.nautilus.clients.hyperliquid._hl_submit_order",
            new=AsyncMock(side_effect=RuntimeError("network error")),
        ):
            await client._submit_order(cmd)

        client.generate_order_denied.assert_called_once()
        client.generate_order_accepted.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_error_status_generates_rejected(self):
        """submit_order が status='error' を返した場合 OrderRejected が生成されること。"""
        client = _make_client()
        order = _FakeOrder(order_type=1, quantity="0.1")
        cmd = _FakeSubmitCommand(order)

        err_result = OrderResult(venue_order_id="", status="error", message="insufficient margin")
        with patch(
            "engine.nautilus.clients.hyperliquid._hl_submit_order",
            new=AsyncMock(return_value=err_result),
        ):
            await client._submit_order(cmd)

        # error status → rejected
        client.generate_order_rejected.assert_called_once()
        client.generate_order_accepted.assert_not_called()


# ---------------------------------------------------------------------------
# TestCancelOrder
# ---------------------------------------------------------------------------


class TestCancelOrder:
    """_cancel_order → cancel_order 委譲確認。"""

    @pytest.mark.asyncio
    async def test_cancel_order_calls_hl_cancel(self):
        """cancel_order が委譲先を呼び、成功時に generate_order_canceled() を呼ぶこと。"""
        client = _make_client()
        client.generate_order_canceled = MagicMock()
        order = _FakeOrder()
        cmd = _FakeCancelCommand(order, venue_order_id="12345")

        with patch(
            "engine.nautilus.clients.hyperliquid._hl_cancel_order",
            new=AsyncMock(return_value=None),
        ) as mock_cancel:
            await client._cancel_order(cmd)

        mock_cancel.assert_called_once()
        client.generate_order_cancel_rejected.assert_not_called()
        # MEDIUM-1 fix: success path must call generate_order_canceled
        client.generate_order_canceled.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_failure_generates_cancel_rejected(self):
        """cancel_order が例外を上げた場合 OrderCancelRejected が生成されること。"""
        client = _make_client()
        order = _FakeOrder()
        cmd = _FakeCancelCommand(order, venue_order_id="12345")

        with patch(
            "engine.nautilus.clients.hyperliquid._hl_cancel_order",
            new=AsyncMock(side_effect=RuntimeError("cancel failed")),
        ):
            await client._cancel_order(cmd)

        client.generate_order_cancel_rejected.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_id", [None, "", "None", "0"])
    async def test_cancel_order_with_invalid_venue_order_id_calls_cancel_rejected(self, bad_id):
        """venue_order_id が None / "" / "None" / "0" の場合に generate_order_cancel_rejected が
        呼ばれること（R3: "None" 文字列すり抜け修正を含む）。
        generate_order_canceled は呼ばれないこと。
        reason に INVALID_VENUE_ORDER_ID が含まれること。"""
        client = _make_client()
        client.generate_order_canceled = MagicMock()
        order = _FakeOrder()
        cmd = _FakeCancelCommand(order, venue_order_id=bad_id)

        with patch(
            "engine.nautilus.clients.hyperliquid._hl_cancel_order",
            new=AsyncMock(return_value=None),
        ) as mock_cancel:
            await client._cancel_order(cmd)

        client.generate_order_cancel_rejected.assert_called_once()
        rejected_kwargs = client.generate_order_cancel_rejected.call_args.kwargs
        assert "INVALID_VENUE_ORDER_ID" in rejected_kwargs.get("reason", ""), (
            f"reason should mention INVALID_VENUE_ORDER_ID, got: {rejected_kwargs.get('reason')!r}"
        )
        # _hl_cancel_order は呼ばれないこと（早期リターン）
        mock_cancel.assert_not_called()
        # generate_order_canceled は呼ばれないこと
        client.generate_order_canceled.assert_not_called()


# ---------------------------------------------------------------------------
# TestModifyOrder
# ---------------------------------------------------------------------------


class TestModifyOrder:
    """_modify_order → OrderModifyRejected (NOT_SUPPORTED) が返ること。"""

    @pytest.mark.asyncio
    async def test_modify_always_rejected_with_not_supported(self):
        """Hyperliquid は modify 未対応 → OrderModifyRejected(NOT_SUPPORTED) が返ること。"""
        client = _make_client()
        order = _FakeOrder()
        cmd = _FakeModifyCommand(order)

        await client._modify_order(cmd)

        client.generate_order_modify_rejected.assert_called_once()
        call_kwargs = client.generate_order_modify_rejected.call_args
        reason = call_kwargs.kwargs.get("reason", "")
        assert "NOT_SUPPORTED" in reason


# ---------------------------------------------------------------------------
# TestHyperliquidOrders (hyperliquid_orders.py の直接テスト)
# ---------------------------------------------------------------------------


class TestHyperliquidOrdersSubmit:
    """hyperliquid_orders.submit_order の HTTP body 検証。"""

    @pytest.mark.asyncio
    async def test_submit_order_posts_correct_json_structure(self):
        """submit_order が POST する JSON body に action.type='order' が含まれること。"""
        session = _make_session()

        class _MockEnvelope:
            order_side = "BUY"
            order_type = "LIMIT"
            quantity = "0.1"
            price = "50000"
            instrument_id = "BTC.HYPERLIQUID"
            asset_index = 0

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [{"resting": {"oid": 12345}}]
                }
            }
        }

        posted_bodies: list[dict] = []

        class _MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url: str, json: dict, **kwargs) -> Any:
                posted_bodies.append(json)
                return mock_response

        with patch("httpx.AsyncClient", return_value=_MockAsyncClient()):
            result = await _hl_submit_order(session, _MockEnvelope())

        assert len(posted_bodies) == 1
        body = posted_bodies[0]
        assert "action" in body
        assert body["action"]["type"] == "order"
        assert "nonce" in body
        assert "signature" in body

    @pytest.mark.asyncio
    async def test_submit_order_returns_order_result_ok(self):
        """submit_order が OrderResult(status='ok') を返すこと。"""
        session = _make_session()

        class _MockEnvelope:
            order_side = "SELL"
            order_type = "MARKET"
            quantity = "0.05"
            price = None
            instrument_id = "ETH.HYPERLIQUID"
            asset_index = 1

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [{"resting": {"oid": 67890}}]
                }
            }
        }

        class _MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url: str, json: dict, **kwargs) -> Any:
                return mock_response

        with patch("httpx.AsyncClient", return_value=_MockAsyncClient()):
            result = await _hl_submit_order(session, _MockEnvelope())

        assert result.status == "ok"
        assert result.venue_order_id == "67890"

    @pytest.mark.asyncio
    async def test_submit_order_returns_error_on_api_error(self):
        """API が error を返した場合 OrderResult(status='error') になること。"""
        session = _make_session()

        class _MockEnvelope:
            order_side = "BUY"
            order_type = "MARKET"
            quantity = "0.1"
            price = None
            instrument_id = "BTC.HYPERLIQUID"
            asset_index = 0

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "err", "response": "Insufficient margin"}

        class _MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url: str, json: dict, **kwargs) -> Any:
                return mock_response

        with patch("httpx.AsyncClient", return_value=_MockAsyncClient()):
            result = await _hl_submit_order(session, _MockEnvelope())

        assert result.status == "error"


# ---------------------------------------------------------------------------
# TestOrderToHlEnvelope (H4: NautilusTrader Order → envelope 変換)
# ---------------------------------------------------------------------------


class TestOrderToHlEnvelope:
    """_order_to_hl_envelope が NautilusTrader enum 値を文字列に変換すること。"""

    def test_buy_market_order_converts_correctly(self):
        """OrderSide.BUY / OrderType.MARKET → order_side='BUY', order_type='MARKET'。"""
        from nautilus_trader.model.enums import OrderSide, OrderType

        from engine.nautilus.clients.hyperliquid import _order_to_hl_envelope

        order = _FakeOrder(
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="0.1",
        )
        envelope = _order_to_hl_envelope(order)
        assert envelope.order_side == "BUY"
        assert envelope.order_type == "MARKET"
        assert envelope.quantity == "0.1"
        assert envelope.price is None

    def test_sell_limit_order_converts_correctly(self):
        """OrderSide.SELL / OrderType.LIMIT → order_side='SELL', order_type='LIMIT', price 設定。"""
        from nautilus_trader.model.enums import OrderSide, OrderType

        from engine.nautilus.clients.hyperliquid import _order_to_hl_envelope

        order = _FakeOrder(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity="0.05",
            price="50000",
        )
        envelope = _order_to_hl_envelope(order)
        assert envelope.order_side == "SELL"
        assert envelope.order_type == "LIMIT"
        assert envelope.quantity == "0.05"
        assert envelope.price == "50000"

    @pytest.mark.asyncio
    async def test_submit_order_passes_envelope_to_hl_submit_order(self):
        """_submit_order が _hl_submit_order に envelope（文字列フィールド）を渡すこと。
        NautilusTrader の OrderSide.BUY enum 値を持つ _FakeOrder を使う。"""
        from nautilus_trader.model.enums import OrderSide, OrderType

        client = _make_client()
        order = _FakeOrder(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity="0.1",
            price="50000",
        )
        cmd = _FakeSubmitCommand(order)

        captured_envelopes: list = []

        async def _capture_envelope(session, envelope):
            captured_envelopes.append(envelope)
            return OrderResult(venue_order_id="77777", status="ok")

        with patch(
            "engine.nautilus.clients.hyperliquid._hl_submit_order",
            new=_capture_envelope,
        ):
            await client._submit_order(cmd)

        assert len(captured_envelopes) == 1
        env = captured_envelopes[0]
        # envelope には文字列フィールドが設定されていること
        assert env.order_side == "BUY", f"Expected 'BUY', got {env.order_side!r}"
        assert env.order_type == "LIMIT", f"Expected 'LIMIT', got {env.order_type!r}"


# ---------------------------------------------------------------------------
# TestMaxQtyGuard (M1: max_qty_per_order による発注拒否)
# ---------------------------------------------------------------------------


class TestMaxQtyGuard:
    """max_qty_per_order を超える注文が generate_order_denied で拒否されること。"""

    @pytest.mark.asyncio
    async def test_submit_order_exceeding_max_qty_calls_generate_order_denied(self):
        """qty=1.0 > max_qty_per_order=0.5 → generate_order_denied が呼ばれること。"""
        from nautilus_trader.model.enums import OrderSide, OrderType

        client = _make_client(max_qty_per_order=0.5)
        order = _FakeOrder(
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="1.0",
        )
        cmd = _FakeSubmitCommand(order)

        with patch(
            "engine.nautilus.clients.hyperliquid._hl_submit_order",
            new=AsyncMock(return_value=OrderResult(venue_order_id="12345", status="ok")),
        ):
            await client._submit_order(cmd)

        client.generate_order_denied.assert_called_once()
        denied_kwargs = client.generate_order_denied.call_args.kwargs
        assert "EXCEEDS_MAX_QTY" in denied_kwargs.get("reason", ""), (
            f"reason should mention EXCEEDS_MAX_QTY, got: {denied_kwargs.get('reason')!r}"
        )
        client.generate_order_submitted.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_order_within_max_qty_proceeds_normally(self):
        """qty=0.3 <= max_qty_per_order=0.5 → 拒否せず generate_order_submitted が呼ばれること。"""
        from nautilus_trader.model.enums import OrderSide, OrderType

        client = _make_client(max_qty_per_order=0.5)
        order = _FakeOrder(
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="0.3",
        )
        cmd = _FakeSubmitCommand(order)

        ok_result = OrderResult(venue_order_id="54321", status="ok")
        with patch(
            "engine.nautilus.clients.hyperliquid._hl_submit_order",
            new=AsyncMock(return_value=ok_result),
        ):
            await client._submit_order(cmd)

        client.generate_order_denied.assert_not_called()
        client.generate_order_submitted.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_order_with_invalid_qty_calls_generate_order_denied(self):
        """quantity="invalid" の場合 generate_order_denied が呼ばれること（R2-M1）。
        0.0 フォールバックで max_qty ガードをバイパスせず、早期リターンすること。"""
        from nautilus_trader.model.enums import OrderSide, OrderType

        client = _make_client(max_qty_per_order=1.0)
        order = _FakeOrder(
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity="invalid",
        )
        cmd = _FakeSubmitCommand(order)

        with patch(
            "engine.nautilus.clients.hyperliquid._hl_submit_order",
            new=AsyncMock(return_value=OrderResult(venue_order_id="12345", status="ok")),
        ) as mock_submit:
            await client._submit_order(cmd)

        client.generate_order_denied.assert_called_once()
        denied_kwargs = client.generate_order_denied.call_args.kwargs
        assert "INVALID_QTY" in denied_kwargs.get("reason", ""), (
            f"reason should mention INVALID_QTY, got: {denied_kwargs.get('reason')!r}"
        )
        # _hl_submit_order は呼ばれないこと
        mock_submit.assert_not_called()
        client.generate_order_submitted.assert_not_called()


# ---------------------------------------------------------------------------
# M7: _extract_venue_order_id の "filled" パステスト
# ---------------------------------------------------------------------------


def test_extract_venue_order_id_from_filled_status():
    """成行注文の即時約定 (filled) パスから oid を取り出せること。"""
    from engine.exchanges.hyperliquid_orders import _extract_venue_order_id

    response = {
        "status": "ok",
        "response": {
            "type": "order",
            "data": {
                "statuses": [{"filled": {"totalSz": "0.1", "avgPx": "30000", "oid": 99999}}]
            },
        },
    }
    assert _extract_venue_order_id(response) == "99999"


def test_extract_venue_order_id_from_resting_status():
    """指値注文の resting パスから oid を取り出せること（既存動作の保護）。"""
    from engine.exchanges.hyperliquid_orders import _extract_venue_order_id

    response = {
        "status": "ok",
        "response": {
            "type": "order",
            "data": {
                "statuses": [{"resting": {"oid": 12345}}]
            },
        },
    }
    assert _extract_venue_order_id(response) == "12345"


class TestHyperliquidOrdersCancel:
    """hyperliquid_orders.cancel_order の HTTP body 検証。"""

    @pytest.mark.asyncio
    async def test_cancel_order_posts_correct_json(self):
        """cancel_order が POST する JSON body に action.type='cancel' が含まれること。"""
        session = _make_session()
        venue_order_id = "12345"
        asset_index = 0

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "response": {"type": "cancel", "data": {"statuses": ["success"]}}
        }

        posted_bodies: list[dict] = []

        class _MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url: str, json: dict, **kwargs) -> Any:
                posted_bodies.append(json)
                return mock_response

        with patch("httpx.AsyncClient", return_value=_MockAsyncClient()):
            await _hl_cancel_order(session, venue_order_id=venue_order_id, asset_index=asset_index)

        assert len(posted_bodies) == 1
        body = posted_bodies[0]
        assert "action" in body
        assert body["action"]["type"] == "cancel"
        cancels = body["action"]["cancels"]
        assert len(cancels) == 1
        assert cancels[0]["a"] == asset_index
        assert cancels[0]["o"] == int(venue_order_id)
