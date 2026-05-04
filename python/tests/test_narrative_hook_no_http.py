"""R2-H4: `narrative_hook` does not perform HTTP after Phase 8.3.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_narrative_hook_no_http_call():
    """R2-H4: NarrativeHook を作成して on_order_filled を呼んでも
    HTTP 呼び出しが行われないこと。
    """
    from engine.nautilus.narrative_hook import NarrativeHook

    collected: list[dict] = []
    hook = NarrativeHook(strategy_id="test-strategy", on_event=collected.append)

    order_filled_event = {
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "3775.0",
        "ts_event_ms": 1714123456789,
    }

    # httpx.AsyncClient が呼ばれないことを確認
    with patch("httpx.AsyncClient") as mock_client_cls:
        await hook.on_order_filled(order_filled_event)
        mock_client_cls.assert_not_called()

    # ExecutionMarker は emit される（N1.12 は維持）
    assert len(collected) == 1
    assert collected[0]["event"] == "ExecutionMarker"
