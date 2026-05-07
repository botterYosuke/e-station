"""N1.12: narrative_hook — OrderFilled → ExecutionMarker IPC."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


class NarrativeHook:
    """OrderFilled を ExecutionMarker IPC として outbox に流す薄い hook."""

    def __init__(
        self,
        strategy_id: str,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._strategy_id = strategy_id
        self._on_event = on_event

    # ── Async API ──────────────────────────────────────────────────────────────

    async def on_order_filled(self, order_filled_event: dict) -> None:
        if self._on_event is not None:
            _emit_execution_marker(
                self._strategy_id,
                order_filled_event,
                self._on_event,
            )

    # ── Sync API (non-async context 用) ───────────────────────────────────────

    def on_order_filled_sync(self, order_filled_event: dict) -> bool:
        """同期版。成功なら ``True`` を返す。

        H-Silent4: 既存イベントループ中から呼ばれて ``asyncio.run`` がネスト不可で
        ``RuntimeError`` を投げる場合は別 thread で再実行する fallback を用意する。
        MEDIUM-R2-5: thread fallback が失敗したら ``log.error`` の上で例外を上流に
        伝播し、caller が ExecutionMarker drop を検知できるようにする。
        """
        import asyncio
        coro = self.on_order_filled(order_filled_event)
        try:
            asyncio.run(coro)
            return True
        except RuntimeError as exc:
            msg = str(exc)
            if (
                "asyncio.run() cannot be called" in msg
                or "running event loop" in msg
            ):
                try:
                    coro.close()
                except Exception:  # noqa: BLE001
                    pass

                import concurrent.futures

                def _runner() -> None:
                    asyncio.run(self.on_order_filled(order_filled_event))

                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        ex.submit(_runner).result(timeout=10.0)
                    return True
                except Exception as inner_exc:  # noqa: BLE001
                    log.error(
                        "narrative_hook: thread fallback failed — %s", inner_exc
                    )
                    raise
            log.error("narrative_hook: sync wrapper RuntimeError — %s", exc)
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("narrative_hook: sync wrapper failed — %s", exc)
            raise


# ── Internal helpers ──────────────────────────────────────────────────────────


def _emit_execution_marker(
    strategy_id: str,
    event: dict,
    on_event: Callable[[dict[str, Any]], None],
) -> None:
    """OrderFilled dict から ExecutionMarker IPC event dict を構築して on_event に渡す."""
    try:
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
        # schema 3.21: 上流 dict が commission を持っていれば伝搬する（live 経路）。
        # ただし現状 OrderFilled wire schema (schemas.py の OrderFilled) には commission
        # フィールドが無いため、live 経路の commission は **常に dict に入らない**。
        # 結果として live ExecutionMarker は commission キーを持たず、
        # live fee_total は 0 のまま。fee_total の live 対応は OrderFilled wire 拡張を
        # 伴う別チケット（Stage D 後）で対応する。replay summary 用途のみ動作する。
        commission = event.get("commission")
        if commission is not None:
            marker["commission"] = str(commission)
        on_event(marker)
    except Exception as exc:  # noqa: BLE001
        log.warning("narrative_hook: emit ExecutionMarker failed — %s", exc)
