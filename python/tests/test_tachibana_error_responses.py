"""T0.8: flowsurface 立花エラーレスポンステストの移植。

flowsurface `exchange/src/adapter/tachibana.rs` Phase 4-2 / 4-3 / 4-4 テストを
Python pytest に移植する。入力は `NautilusOrderEnvelope` 経由に置換。

テストケース:
  - test_submit_order_wrong_password_response      — p_errno=0, sResultCode="11304"
      → TachibanaError(code="11304") が raise される
  - test_submit_order_market_closed_response       — p_errno=0, sResultCode="-62"
      → TachibanaError(code="-62") が raise される
  - test_submit_order_invalid_issue_code_response  — p_errno=0, sResultCode="11104"
      → TachibanaError(code="11104") が raise される
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_helpers import TachibanaError
from engine.exchanges.tachibana_orders import NautilusOrderEnvelope, submit_order
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session() -> TachibanaSession:
    """デモ用テストセッション。"""
    return TachibanaSession(
        url_request=RequestUrl("https://demo.example/request/"),
        url_master=MasterUrl("https://demo.example/master/"),
        url_price=PriceUrl("https://demo.example/price/"),
        url_event=EventUrl("https://demo.example/event/"),
        url_event_ws="wss://demo.example/event/",
        zyoutoeki_kazei_c="1",
    )


def _market_buy(client_order_id: str = "cid-err-001") -> NautilusOrderEnvelope:
    """現物・成行・買の最小 Envelope。"""
    return NautilusOrderEnvelope(
        client_order_id=client_order_id,
        instrument_id="7203.T/TSE",
        order_side="BUY",
        order_type="MARKET",
        quantity="100",
        time_in_force="DAY",
        post_only=False,
        reduce_only=False,
        tags=["cash_margin=cash"],
    )


def _make_mock_client(response_body_bytes: bytes) -> AsyncMock:
    """httpx.AsyncClient モックを返す。"""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.content = response_body_bytes

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


def _encode_sjis(data: dict) -> bytes:
    """JSON dict を Shift-JIS バイト列にエンコードする（立花 API 形式）。"""
    return json.dumps(data, ensure_ascii=False).encode("shift_jis")


# ---------------------------------------------------------------------------
# Tests: flowsurface Phase 4-2 / 4-3 / 4-4 移植
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_order_wrong_password_response():
    """第二暗証番号誤り (sResultCode="11304") → TachibanaError(code="11304") が raise される。

    flowsurface tachibana.rs:4168
    `submit_new_order_returns_error_on_wrong_password_response` の移植。
    sResultCode="11304" は 2026-04-17 デモ環境実機確認値。
    """
    response_data = {
        "p_errno": "0",
        "p_err": "",
        "sResultCode": "11304",
        "sResultText": "第二暗証番号が誤っています",
        "sOrderNumber": "",
        "sEigyouDay": "20260426",
        "sWarningCode": "",
        "sWarningText": "",
    }
    mock_client = _make_mock_client(_encode_sjis(response_data))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(TachibanaError) as exc_info:
            await submit_order(_session(), "wrongpassword", _market_buy("cid-wp-001"))

    err = exc_info.value
    assert err.code == "11304", f"expected code='11304', got {err.code!r}"
    # flowsurface テストと同様: エラー文字列に "code=" が含まれること（E2E スクリプト互換）
    assert "code=" in str(err), f"error string must contain 'code=': {err}"


@pytest.mark.asyncio
async def test_submit_order_market_closed_response():
    """市場時間外 (sResultCode="-62") → TachibanaError(code="-62") が raise される。

    flowsurface tachibana.rs:4215
    `submit_new_order_returns_error_on_market_closed_response` の移植。
    """
    response_data = {
        "p_errno": "0",
        "p_err": "",
        "sResultCode": "-62",
        "sResultText": "稼働時間外です",
        "sOrderNumber": "",
        "sEigyouDay": "20260426",
        "sWarningCode": "",
        "sWarningText": "",
    }
    mock_client = _make_mock_client(_encode_sjis(response_data))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(TachibanaError) as exc_info:
            await submit_order(_session(), "password", _market_buy("cid-mc-001"))

    err = exc_info.value
    assert err.code == "-62", f"expected code='-62', got {err.code!r}"


@pytest.mark.asyncio
async def test_submit_order_invalid_issue_code_response():
    """存在しない銘柄コード (sResultCode="11104") → TachibanaError(code="11104") が raise される。

    flowsurface tachibana.rs:4256
    `submit_new_order_returns_error_on_invalid_issue_code_response` の移植。
    """
    response_data = {
        "p_errno": "0",
        "p_err": "",
        "sResultCode": "11104",
        "sResultText": "銘柄がありません",
        "sOrderNumber": "",
        "sEigyouDay": "20260426",
        "sWarningCode": "",
        "sWarningText": "",
    }
    mock_client = _make_mock_client(_encode_sjis(response_data))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(TachibanaError) as exc_info:
            await submit_order(_session(), "password", _market_buy("cid-ic-001"))

    err = exc_info.value
    assert err.code == "11104", f"expected code='11104', got {err.code!r}"
    # エラー文字列に "code=" が含まれること
    assert "code=" in str(err), f"error string must contain 'code=': {err}"
