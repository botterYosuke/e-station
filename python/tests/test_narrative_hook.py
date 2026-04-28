"""N1.6: NarrativeHook のテスト。

pytest-httpx を使って HTTP リクエストをモックし、以下を検証する:
- on_order_filled が /api/agent/narrative に POST すること
- POST body に linked_order_id が含まれること
- HTTP エラー時に例外を raise しないこと（log.warning のみ）
"""

from __future__ import annotations

import pytest
import pytest_asyncio  # noqa: F401 — needed for pytest-asyncio discovery

from engine.nautilus.narrative_hook import NarrativeHook

# ── テスト用フィクスチャ ────────────────────────────────────────────────────────

_ENDPOINT = "http://localhost:9876"
_NARRATIVE_URL = f"{_ENDPOINT}/api/agent/narrative"

_SAMPLE_EVENT: dict = {
    "instrument_id": "1301.TSE",
    "linked_order_id": "O-20260428-000001",
    "outcome": "filled at 3775.0",
    "timestamp_ms": 1714123456789,
    "extra": {},
}


# ── テストケース ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_order_filled_posts_to_narrative_endpoint(httpx_mock) -> None:
    """on_order_filled が /api/agent/narrative に POST すること。"""
    httpx_mock.add_response(
        method="POST",
        url=_NARRATIVE_URL,
        status_code=201,
        json={"id": "test-uuid", "status": "stored"},
    )

    hook = NarrativeHook(strategy_id="buy-and-hold", endpoint=_ENDPOINT)
    await hook.on_order_filled(_SAMPLE_EVENT)

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    req = requests[0]
    assert req.method == "POST"
    assert str(req.url) == _NARRATIVE_URL


@pytest.mark.asyncio
async def test_on_order_filled_includes_linked_order_id(httpx_mock) -> None:
    """POST body に linked_order_id が含まれること。"""
    httpx_mock.add_response(
        method="POST",
        url=_NARRATIVE_URL,
        status_code=201,
        json={"id": "test-uuid", "status": "stored"},
    )

    hook = NarrativeHook(strategy_id="buy-and-hold", endpoint=_ENDPOINT)
    await hook.on_order_filled(_SAMPLE_EVENT)

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    import json

    body = json.loads(requests[0].content)
    assert body["linked_order_id"] == "O-20260428-000001"
    assert body["strategy_id"] == "buy-and-hold"
    assert body["event_type"] == "OrderFilled"
    assert body["instrument_id"] == "1301.TSE"


@pytest.mark.asyncio
async def test_on_order_filled_does_not_raise_on_http_error(httpx_mock) -> None:
    """HTTP エラー時に例外を raise しないこと（log.warning のみ）。"""
    httpx_mock.add_response(
        method="POST",
        url=_NARRATIVE_URL,
        status_code=500,
        json={"error": "internal server error"},
    )

    hook = NarrativeHook(strategy_id="buy-and-hold", endpoint=_ENDPOINT)
    # raise しないことを確認（例外があればここで失敗する）
    await hook.on_order_filled(_SAMPLE_EVENT)
