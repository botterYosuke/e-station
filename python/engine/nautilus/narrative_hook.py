"""N1.6 / N1.12: narrative_hook — OrderFilled → POST /api/agent/narrative
                               + OrderFilled → ExecutionMarker IPC.

Strategy の on_event(event) から呼ぶ mixin または関数。
IPC EngineEvent::OrderFilled 受領時に:
  1. HTTP で narrative store に記録する（N1.6 機能）
  2. ExecutionMarker IPC イベントを outbox 経由で Rust UI に送出する（N1.12 機能）

HTTP エンドポイント: POST http://localhost:9876/api/agent/narrative
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

_NARRATIVE_PATH = "/api/agent/narrative"


class NarrativeHook:
    """OrderFilled イベントを narrative store に HTTP POST し、
    ExecutionMarker IPC を outbox に積むフック（N1.6 + N1.12）。

    Strategy の ``on_event`` から呼ぶか、スタンドアローン関数として使う。

    Parameters
    ----------
    strategy_id:
        記録に埋め込む戦略 ID（例: ``"buy-and-hold"``）。
    endpoint:
        narrative API のベース URL。デフォルト ``http://localhost:9876``。
    on_event:
        N1.12: ExecutionMarker IPC を outbox に積む callback。
        ``on_event(event_dict: dict) -> None`` のシグネチャ。
        ``None`` の場合は ExecutionMarker は送出されない（N1.6 互換モード）。
    """

    def __init__(
        self,
        strategy_id: str,
        endpoint: str = "http://localhost:9876",
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._strategy_id = strategy_id
        self._endpoint = endpoint.rstrip("/")
        self._on_event = on_event

    # ── Async API ──────────────────────────────────────────────────────────────

    async def on_order_filled(self, order_filled_event: dict) -> None:
        """OrderFilled イベントを処理する。

        1. narrative store に HTTP POST する。
        2. ExecutionMarker IPC を outbox 経由で送出する（``on_event`` が設定されている場合）。

        エラーは握り潰さず log.warning で記録（メイン戦略ループを止めない）。

        Parameters
        ----------
        order_filled_event:
            OrderFilled イベントの dict 表現。``instrument_id`` ``side``
            ``price`` (または ``last_price``) ``ts_event_ms`` を含むことを期待する。
        """
        # ① narrative store への HTTP POST（N1.6）
        payload = _build_payload(self._strategy_id, order_filled_event)
        url = self._endpoint + _NARRATIVE_PATH
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=5.0)
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "narrative_hook: POST %s failed — %s",
                url,
                exc,
            )

        # ② ExecutionMarker IPC 送出（N1.12）
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
        try:
            asyncio.run(self.on_order_filled(order_filled_event))
        except Exception as exc:  # noqa: BLE001
            log.warning("narrative_hook: sync wrapper failed — %s", exc)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _build_payload(strategy_id: str, event: dict) -> dict:
    """OrderFilled event dict から POST body を構築する。"""
    return {
        "strategy_id": strategy_id,
        "event_type": "OrderFilled",
        "instrument_id": event.get("instrument_id", ""),
        "linked_order_id": event.get("linked_order_id", ""),
        "outcome": event.get("outcome", ""),
        "timestamp_ms": event.get("timestamp_ms", 0),
        "extra": event.get("extra", {}),
    }


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
