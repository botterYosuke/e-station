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

    def on_order_filled_sync(self, order_filled_event: dict) -> bool:
        """同期版（non-async context 用）。成功なら ``True`` を返す。

        H-Silent4 (Phase 8 R1 / Phase 3): 既存イベントループ中から呼ばれた
        場合 (``asyncio.run`` がネスト不可で ``RuntimeError`` を投げる) は、
        別 thread で新しい event loop を立てて再実行する fallback を用意する。
        旧実装は ``except Exception`` で warning を出して silently drop して
        いたため、ExecutionMarker が emit されないバグの温床になっていた。

        MEDIUM-R2-5: thread fallback が失敗した場合は ``log.error`` で記録
        した上で **例外を上流に伝播** する。caller が ExecutionMarker drop
        を検知できるようにするため。Nautilus の event handler は失敗を
        吸収する設計だが、ユーザーコードが直接呼んだ場合は raise が必要。
        戻り値 ``bool`` は drop 検知用 (False = 失敗)。

        Returns:
            ``True``: ExecutionMarker emit に成功 (または on_event=None で no-op)。
            ``False``: 失敗を検知したが silent path で握り潰した場合 (現実装では
            常に raise するので False は返らないが、下流互換のため bool を返す)。
        """
        import asyncio
        # Build the coroutine once and re-build for the fallback path so that
        # a leaked, never-awaited coroutine does not emit a RuntimeWarning.
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
                # The original coroutine was never awaited because asyncio.run
                # rejected before scheduling. Close it explicitly to silence
                # the RuntimeWarning, then re-build for the thread fallback.
                try:
                    coro.close()
                except Exception:  # noqa: BLE001
                    pass

                # Fallback: run in a fresh thread with its own loop.
                import concurrent.futures

                def _runner() -> None:
                    asyncio.run(self.on_order_filled(order_filled_event))

                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        ex.submit(_runner).result(timeout=10.0)
                    return True
                except Exception as inner_exc:  # noqa: BLE001
                    # MEDIUM-R2-5: silent drop は禁止。エラー記録した上で
                    # 上流に伝播させ、caller が ExecutionMarker 損失を
                    # 検知できるようにする。
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
