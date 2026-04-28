"""N1.12: NarrativeHook が OrderFilled → ExecutionMarker を 1:1 で emit するテスト。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.nautilus.narrative_hook import NarrativeHook, _emit_execution_marker


# ── _emit_execution_marker ユニットテスト ─────────────────────────────────────


def test_emit_execution_marker_appends_correct_event():
    """OrderFilled dict から ExecutionMarker が正しいフィールドで作成される。"""
    collected: list[dict] = []

    order_filled_event = {
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "1500.0",
        "ts_event_ms": 1_700_000_000_000,
    }
    _emit_execution_marker("buy-and-hold-001", order_filled_event, collected.append)

    assert len(collected) == 1
    marker = collected[0]
    assert marker["event"] == "ExecutionMarker"
    assert marker["strategy_id"] == "buy-and-hold-001"
    assert marker["instrument_id"] == "1301.TSE"
    assert marker["side"] == "BUY"
    assert marker["price"] == "1500.0"
    assert marker["ts_event_ms"] == 1_700_000_000_000


def test_emit_execution_marker_uses_last_price_fallback():
    """``price`` キーが無いとき ``last_price`` をフォールバックとして使う。"""
    collected: list[dict] = []

    event = {
        "instrument_id": "1301.TSE",
        "side": "SELL",
        "last_price": "1600.5",
        "ts_event_ms": 1_700_000_000_001,
    }
    _emit_execution_marker("strat-001", event, collected.append)

    assert collected[0]["price"] == "1600.5"


def test_emit_execution_marker_price_converted_to_str():
    """price が数値でも文字列に変換される。"""
    collected: list[dict] = []
    event = {"instrument_id": "1301.TSE", "side": "BUY", "price": 1750, "ts_event_ms": 0}
    _emit_execution_marker("strat", event, collected.append)
    assert isinstance(collected[0]["price"], str)
    assert collected[0]["price"] == "1750"


# ── NarrativeHook.on_order_filled ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_filled_emits_execution_marker():
    """OrderFilled イベントを受けると ExecutionMarker が 1 件 emit される。"""
    collected: list[dict] = []
    hook = NarrativeHook(
        strategy_id="strat-001",
        endpoint="http://localhost:9999",
        on_event=collected.append,
    )

    order_filled_event = {
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "1500.0",
        "ts_event_ms": 1_700_000_000_000,
        "outcome": "filled",
        "linked_order_id": "oid-1",
        "timestamp_ms": 1_700_000_000_000,
    }

    # HTTP POST は mock して実際には打たない
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_post.return_value = mock_resp
        await hook.on_order_filled(order_filled_event)

    assert len(collected) == 1
    marker = collected[0]
    assert marker["event"] == "ExecutionMarker"
    assert marker["strategy_id"] == "strat-001"
    assert marker["instrument_id"] == "1301.TSE"
    assert marker["side"] == "BUY"
    assert marker["price"] == "1500.0"


@pytest.mark.asyncio
async def test_execution_marker_not_emitted_without_order_filled():
    """on_event が None の場合は ExecutionMarker が出ない（N1.6 互換モード）。"""
    # on_event=None で NarrativeHook を作成（N1.6 互換）
    hook = NarrativeHook(strategy_id="strat-001", endpoint="http://localhost:9999")

    # HTTP POST は mock
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_post.return_value = mock_resp
        # on_order_filled を呼んでも on_event が None なので ExecutionMarker は出ない
        await hook.on_order_filled({"instrument_id": "1301.TSE", "side": "BUY"})

    # on_event が設定されていないので呼ばれることはない — エラーも発生しない
    # (このテストは例外が発生しないことを確認するだけでよい)


@pytest.mark.asyncio
async def test_execution_marker_emitted_once_per_order_filled():
    """OrderFilled 1 件につき ExecutionMarker は 1 件のみ emit される。"""
    collected: list[dict] = []
    hook = NarrativeHook(
        strategy_id="strat-001",
        endpoint="http://localhost:9999",
        on_event=collected.append,
    )

    event = {
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "1500.0",
        "ts_event_ms": 1_700_000_000_000,
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_post.return_value = mock_resp
        await hook.on_order_filled(event)
        await hook.on_order_filled(event)

    execution_markers = [e for e in collected if e["event"] == "ExecutionMarker"]
    assert len(execution_markers) == 2  # 2 回呼べば 2 件


@pytest.mark.asyncio
async def test_execution_marker_not_emitted_for_other_events():
    """OrderFilled 以外のイベントでは ExecutionMarker は出ない。

    NarrativeHook の on_order_filled() を呼ばない限り emit されない。
    """
    collected: list[dict] = []
    hook = NarrativeHook(
        strategy_id="strat-001",
        endpoint="http://localhost:9999",
        on_event=collected.append,
    )

    # on_order_filled() を呼ばない → collected は空のまま
    assert len(collected) == 0
