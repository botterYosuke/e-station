"""N1.12: NarrativeHook のテスト。

N1.6（HTTP POST /api/agent/narrative）は Phase 8.3 で廃止。
HTTP API（ポート 9876）が削除されたため、narrative_hook は
N1.12 の ExecutionMarker IPC のみを提供する。
"""

from __future__ import annotations

import pytest

from engine.nautilus.narrative_hook import NarrativeHook

# ── テスト用フィクスチャ ────────────────────────────────────────────────────────

_SAMPLE_EVENT: dict = {
    "instrument_id": "1301.TSE",
    "linked_order_id": "O-20260428-000001",
    "outcome": "filled at 3775.0",
    "timestamp_ms": 1714123456789,
    "extra": {},
}


# ── テストケース ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_order_filled_does_not_make_http_call() -> None:
    """R2-H4: on_order_filled は HTTP 呼び出しを行わないこと（N1.6 廃止）。"""
    hook = NarrativeHook(strategy_id="buy-and-hold")
    # httpx を使わずに呼べること（例外が出ないこと）を確認
    await hook.on_order_filled(_SAMPLE_EVENT)
    # HTTP 呼び出しが行われないため、httpx を import しなくても動作する


@pytest.mark.asyncio
async def test_on_order_filled_does_not_raise() -> None:
    """on_order_filled は例外を raise しないこと。"""
    hook = NarrativeHook(strategy_id="buy-and-hold")
    # 例外があればここで失敗する
    await hook.on_order_filled(_SAMPLE_EVENT)


@pytest.mark.asyncio
async def test_on_order_filled_emits_execution_marker_when_on_event_set() -> None:
    """N1.12: on_event が設定されている場合は ExecutionMarker が emit されること。"""
    collected: list[dict] = []
    event = {
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "3775.0",
        "ts_event_ms": 1714123456789,
        "outcome": "filled at 3775.0",
        "linked_order_id": "O-20260428-000001",
        "timestamp_ms": 1714123456789,
        "extra": {},
    }
    hook = NarrativeHook(strategy_id="buy-and-hold", on_event=collected.append)
    await hook.on_order_filled(event)

    assert len(collected) == 1
    marker = collected[0]
    assert marker["event"] == "ExecutionMarker"
    assert marker["strategy_id"] == "buy-and-hold"
    assert marker["instrument_id"] == "1301.TSE"
