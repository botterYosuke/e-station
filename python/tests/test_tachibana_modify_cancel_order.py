"""T1.6 — modify_order / cancel_order / cancel_all_orders / fetch_order_list の
Python ユニットテスト（TDD / pytest-httpx を使用）。

テストケース:
    - modify_order: 正常系
    - cancel_order: 正常系
    - cancel_order: session 切れ (p_errno=2)
    - cancel_all_orders: 正常系
    - fetch_order_list: 正常系
    - fetch_order_list: フィルタ適用
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_httpx

from engine.exchanges.tachibana_helpers import SessionExpiredError
from engine.exchanges.tachibana_url import RequestUrl
from engine.exchanges.tachibana_orders import (
    ModifyOrderResult,
    CancelOrderResult,
    CancelAllResult,
    OrderRecordWire as PythonOrderRecordWire,
    TachibanaWireModifyRequest,
    TachibanaWireCancelRequest,
    modify_order,
    cancel_order,
    cancel_all_orders,
    fetch_order_list,
)
from engine.schemas import OrderListFilter, OrderModifyChange


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_session(url_request: str = "https://demo.example/request/") -> Any:
    session = MagicMock()
    session.url_request = RequestUrl(url_request)
    session.zyoutoeki_kazei_c = "1"
    return session


def _ok_modify_response(order_number: str = "ORD-001") -> dict:
    """CLMKabuCorrectOrder / CLMKabuCancelOrder 正常レスポンス。"""
    return {
        "p_errno": "",
        "p_err": "",
        "sCLMID": "CLMKabuCorrectOrder",
        "sResultCode": "0",
        "sOrderNumber": order_number,
        "sEigyouDay": "20260426",
        "sOrderDate": "20260426103000",
    }


def _ok_cancel_response(order_number: str = "ORD-001") -> dict:
    return {
        "p_errno": "",
        "p_err": "",
        "sCLMID": "CLMKabuCancelOrder",
        "sResultCode": "0",
        "sOrderNumber": order_number,
        "sEigyouDay": "20260426",
        "sOrderDate": "20260426103000",
    }


def _session_expired_response() -> dict:
    return {
        "p_errno": "2",
        "p_err": "セッション切れ",
        "sCLMID": "CLMKabuCancelOrder",
        "sResultCode": "1",
    }


def _ok_order_list_response() -> dict:
    return {
        "p_errno": "",
        "p_err": "",
        "sCLMID": "CLMOrderList",
        "sResultCode": "0",
        "aOrderList": [
            {
                "sOrderOrderNumber": "ORD-001",
                "sOrderIssueCode": "7203",
                "sOrderOrderSuryou": "100",
                "sOrderCurrentSuryou": "100",
                "sOrderOrderPrice": "0",
                "sOrderOrderDateTime": "20260426103000",
                "sOrderStatus": "注文中",
                "sOrderYakuzyouSuryo": "0",
                "sOrderYakuzyouPrice": "0",
                "sOrderEigyouDay": "20260426",
            },
        ],
    }


# ---------------------------------------------------------------------------
# T1.1 TachibanaWireModifyRequest / TachibanaWireCancelRequest 型テスト
# ---------------------------------------------------------------------------


class TestTachibanaWireModifyRequest:
    def test_instantiate_with_required_fields(self):
        req = TachibanaWireModifyRequest(
            order_number="ORD-001",
            eig_day="20260426",
            condition="*",
            price="*",
            qty="*",
            expire_day="*",
            second_password="secret",
        )
        assert req.order_number == "ORD-001"
        assert req.eig_day == "20260426"

    def test_repr_masks_second_password(self):
        req = TachibanaWireModifyRequest(
            order_number="ORD-001",
            eig_day="20260426",
            condition="*",
            price="*",
            qty="*",
            expire_day="*",
            second_password="mysecret",
        )
        r = repr(req)
        assert "mysecret" not in r
        assert "[REDACTED]" in r or "redacted" in r.lower()

    def test_model_dump_masks_second_password(self):
        req = TachibanaWireModifyRequest(
            order_number="ORD-001",
            eig_day="20260426",
            condition="*",
            price="*",
            qty="*",
            expire_day="*",
            second_password="mysecret",
        )
        d = req.model_dump()
        assert d["second_password"] != "mysecret"


class TestTachibanaWireCancelRequest:
    def test_instantiate_with_required_fields(self):
        req = TachibanaWireCancelRequest(
            order_number="ORD-001",
            eig_day="20260426",
            second_password="secret",
        )
        assert req.order_number == "ORD-001"

    def test_repr_masks_second_password(self):
        req = TachibanaWireCancelRequest(
            order_number="ORD-001",
            eig_day="20260426",
            second_password="mysecret",
        )
        r = repr(req)
        assert "mysecret" not in r


# ---------------------------------------------------------------------------
# T1.1 modify_order テスト
# ---------------------------------------------------------------------------


class TestModifyOrder:
    @pytest.mark.asyncio
    async def test_modify_order_success(self, httpx_mock: pytest_httpx.HTTPXMock):
        """modify_order 正常系: CLMKabuCorrectOrder が呼ばれ ModifyOrderResult が返る。"""
        from engine.exchanges.tachibana_url import build_request_url

        session = _make_session()
        httpx_mock.add_response(
            method="GET",
            json=_ok_modify_response("ORD-001"),
        )

        change = OrderModifyChange(new_quantity="200")
        result = await modify_order(
            session=session,
            second_password="secret",
            client_order_id="cid-001",
            venue_order_id="ORD-001",
            change=change,
        )

        assert isinstance(result, ModifyOrderResult)
        assert result.venue_order_id == "ORD-001"

    @pytest.mark.asyncio
    async def test_modify_order_session_expired(self, httpx_mock: pytest_httpx.HTTPXMock):
        """modify_order: p_errno=2 → SessionExpiredError が raise される。"""
        session = _make_session()
        httpx_mock.add_response(
            method="GET",
            json=_session_expired_response(),
        )

        change = OrderModifyChange(new_price="1500")
        with pytest.raises(SessionExpiredError):
            await modify_order(
                session=session,
                second_password="secret",
                client_order_id="cid-001",
                venue_order_id="ORD-001",
                change=change,
            )


# ---------------------------------------------------------------------------
# T1.1 cancel_order テスト
# ---------------------------------------------------------------------------


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_order_success(self, httpx_mock: pytest_httpx.HTTPXMock):
        """cancel_order 正常系: CLMKabuCancelOrder が呼ばれ CancelOrderResult が返る。"""
        session = _make_session()
        httpx_mock.add_response(
            method="GET",
            json=_ok_cancel_response("ORD-001"),
        )

        result = await cancel_order(
            session=session,
            second_password="secret",
            client_order_id="cid-001",
            venue_order_id="ORD-001",
        )

        assert isinstance(result, CancelOrderResult)
        assert result.venue_order_id == "ORD-001"

    @pytest.mark.asyncio
    async def test_cancel_order_session_expired(self, httpx_mock: pytest_httpx.HTTPXMock):
        """cancel_order: p_errno=2 → SessionExpiredError が raise される。"""
        session = _make_session()
        httpx_mock.add_response(
            method="GET",
            json=_session_expired_response(),
        )

        with pytest.raises(SessionExpiredError):
            await cancel_order(
                session=session,
                second_password="secret",
                client_order_id="cid-001",
                venue_order_id="ORD-001",
            )


# ---------------------------------------------------------------------------
# T1.1 cancel_all_orders テスト
# ---------------------------------------------------------------------------


class TestCancelAllOrders:
    @pytest.mark.asyncio
    async def test_cancel_all_orders_success(self, httpx_mock: pytest_httpx.HTTPXMock):
        """cancel_all_orders 正常系: 一覧取得後に全件取消し、CancelAllResult を返す。"""
        session = _make_session()
        # First call: CLMOrderList
        httpx_mock.add_response(method="GET", json=_ok_order_list_response())
        # Second call: CLMKabuCancelOrder for ORD-001
        httpx_mock.add_response(method="GET", json=_ok_cancel_response("ORD-001"))

        result = await cancel_all_orders(
            session=session,
            second_password="secret",
        )

        assert isinstance(result, CancelAllResult)
        assert result.canceled_count >= 0


# ---------------------------------------------------------------------------
# T1.1 fetch_order_list テスト
# ---------------------------------------------------------------------------


class TestFetchOrderList:
    @pytest.mark.asyncio
    async def test_fetch_order_list_success(self, httpx_mock: pytest_httpx.HTTPXMock):
        """fetch_order_list 正常系: CLMOrderList を呼び OrderRecordWire のリストが返る。"""
        session = _make_session()
        httpx_mock.add_response(method="GET", json=_ok_order_list_response())

        filter_ = OrderListFilter()
        records = await fetch_order_list(session=session, filter=filter_)

        assert isinstance(records, list)
        assert len(records) == 1
        assert isinstance(records[0], PythonOrderRecordWire)
        assert records[0].venue_order_id == "ORD-001"

    @pytest.mark.asyncio
    async def test_fetch_order_list_with_instrument_id_filter(
        self, httpx_mock: pytest_httpx.HTTPXMock
    ):
        """fetch_order_list: instrument_id フィルタ付きで CLMOrderList を呼ぶ。"""
        session = _make_session()
        httpx_mock.add_response(method="GET", json=_ok_order_list_response())

        filter_ = OrderListFilter(instrument_id="7203.TSE")
        records = await fetch_order_list(session=session, filter=filter_)

        # ORD-001 は 7203 で一致するのでリストに含まれる
        assert any(r.venue_order_id == "ORD-001" for r in records)

    @pytest.mark.asyncio
    async def test_fetch_order_list_empty(self, httpx_mock: pytest_httpx.HTTPXMock):
        """fetch_order_list: 注文なしの場合は空リストを返す。"""
        session = _make_session()
        httpx_mock.add_response(
            method="GET",
            json={
                "p_errno": "",
                "p_err": "",
                "sCLMID": "CLMOrderList",
                "sResultCode": "0",
                "aOrderList": "",  # 立花の空リスト表現
            },
        )

        filter_ = OrderListFilter()
        records = await fetch_order_list(session=session, filter=filter_)
        assert records == []
