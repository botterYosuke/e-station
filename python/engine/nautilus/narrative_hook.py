"""N1.12: narrative_hook — OrderFilled → ExecutionMarker IPC.

注: N1.6（POST /api/agent/narrative）は Phase 8.3 で廃止。
HTTP API（ポート 9876）が削除されたため narrative store への HTTP 記録は機能しない。
現在は N1.12 の ExecutionMarker IPC のみを提供する。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


class NarrativeHook:
    """N1.12: narrative_hook — OrderFilled → ExecutionMarker IPC.

    Strategy の ``on_event`` から呼ぶか、スタンドアローン関数として使う。

    注: N1.6（POST /api/agent/narrative）は Phase 8.3 で廃止。
    HTTP API（ポート 9876）が削除されたため narrative store への HTTP 記録は機能しない。
    現在は N1.12 の ExecutionMarker IPC のみを提供する。

    Parameters
    ----------
    strategy_id:
        記録に埋め込む戦略 ID（例: ``"buy-and-hold"``）。
    endpoint:
        **Deprecated**: Phase 8.3 で HTTP API が廃止されたため、このパラメータは
        無視される。後方互換性のため残しているが、指定すると WARNING が出る。
    on_event:
        N1.12: ExecutionMarker IPC を outbox に積む callback。
        ``on_event(event_dict: dict) -> None`` のシグネチャ。
        ``None`` の場合は ExecutionMarker は送出されない。
    """

    def __init__(
        self,
        strategy_id: str,
        endpoint: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._strategy_id = strategy_id
        self._on_event = on_event
        if endpoint is not None:
            log.warning(
                "NarrativeHook: 'endpoint' パラメータは Phase 8.3 で廃止されました。"
                "HTTP API（ポート 9876）は削除済みのため、このパラメータは無視されます。"
            )

    # ── Async API ──────────────────────────────────────────────────────────────

    async def on_order_filled(self, order_filled_event: dict) -> None:
        """OrderFilled イベントを処理する。

        N1.12: ExecutionMarker IPC を outbox 経由で送出する（``on_event`` が設定されている場合）。

        注: N1.6 の HTTP POST は Phase 8.3 で廃止。HTTP 呼び出しは行わない。

        Parameters
        ----------
        order_filled_event:
            OrderFilled イベントの dict 表現。``instrument_id`` ``side``
            ``price`` (または ``last_price``) ``ts_event_ms`` を含むことを期待する。
        """
        # N1.12: ExecutionMarker IPC 送出
        if self._on_event is not None:
            _emit_execution_marker(
                self._strategy_id,
                order_filled_event,
                self._on_event,
            )

    # ── Sync API (non-async context 用) ───────────────────────────────────────

    def on_order_filled_sync(self, order_filled_event: dict) -> None:
        """同期版（non-async context 用）。

        既存のイベントループがある場合は ``asyncio.run_coroutine_threadsafe``
        などで適切に呼ぶこと。このメソッド自体は新しいイベントループを
        ``asyncio.run()`` で生成して実行する。
        """
        import asyncio
        try:
            asyncio.run(self.on_order_filled(order_filled_event))
        except Exception as exc:  # noqa: BLE001
            log.warning("narrative_hook: sync wrapper failed — %s", exc)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _emit_execution_marker(
    strategy_id: str,
    event: dict,
    on_event: Callable[[dict[str, Any]], None],
) -> None:
    """OrderFilled dict から ExecutionMarker IPC event dict を構築して on_event に渡す。

    ``on_event`` は outbox に積む処理を担う（例: ``deque.append``）。
    エラーは握り潰さず log.warning で記録する。

    Parameters
    ----------
    strategy_id:
        戦略 ID。
    event:
        OrderFilled イベントの dict。``instrument_id`` ``side``
        ``price`` (or ``last_price``) ``ts_event_ms`` を参照する。
    on_event:
        ExecutionMarker dict を受け取る callback。
    """
    try:
        # ``price`` → ``last_price`` の順でフォールバック
        price = event.get("price") or event.get("last_price", "0")
        ts_event_ms = event.get("ts_event_ms", int(time.time() * 1000))
        marker: dict[str, Any] = {
            "event": "ExecutionMarker",
            "strategy_id": strategy_id,
            "instrument_id": event.get("instrument_id", ""),
            "side": event.get("side", ""),
            "price": str(price),
            "ts_event_ms": ts_event_ms,
        }
        on_event(marker)
    except Exception as exc:  # noqa: BLE001
        log.warning("narrative_hook: emit ExecutionMarker failed — %s", exc)
