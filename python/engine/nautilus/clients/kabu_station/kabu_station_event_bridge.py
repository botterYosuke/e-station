"""KabuStationEventBridge — kabu 注文 PUSH / polling event を Nautilus events に変換する (issue #42 Phase 4)。

入力:
- kabu の ``GET /orders`` レコード（`{ID, OrderID, State, Symbol, Side, OrderQty, CumQty, ...}`）
  または PUSH JSON（同フィールド構成）。
- ``State`` フィールドで状態遷移を区別する:
  - 1: 受付（生 / 待機） → ``OrderAccepted``
  - 3: 取消済 → ``OrderCanceled``
  - 5: 約定済 → ``OrderFilled``
  - 4 / その他: 拒否（``DetailState`` で詳細判別だが Phase 4 では reject へフォールバック）

出力:
- ``client.generate_order_accepted`` / ``generate_order_filled`` / ``generate_order_canceled``
  / ``generate_order_rejected`` 互換 callback を呼ぶ。

冪等化:
- 同一 (OrderID, State) 組合せの 2 重投入を seen-set で握り潰す（tachibana 側と同様）。

Phase 4 の最小スコープ:
- 代表的な 4 経路（accept / fill / cancel / reject）の wire 表現を pin する。
- 詳細な ``DetailState`` 解釈や trade_id 採番は TODO で残す（既存 server.py polling
  経路がより詳細な変換を持っているので、将来 adapter に移植する）。
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# kabu State 値（OpenAPI GET /orders / PUSH JSON 共通）
_STATE_RECEIVED = 1  # 受付済 / 待機
_STATE_PROCESSED = 2  # 処理済（実際は handled by 5）
_STATE_CANCELED = 3
_STATE_REJECTED = 4
_STATE_FILLED = 5


class KabuStationEventBridge:
    """kabu 注文 record → Nautilus generate_* 呼び出しのブリッジ。

    Args:
        client: ``generate_order_*`` callback を持つ object（``LiveExecutionClient``
            互換または mock）。
    """

    def __init__(self, *, client: Any) -> None:
        self._client = client
        # 冪等化用 seen-set: (order_id, state)
        self._seen: set[tuple[str, int]] = set()

    def process_order_record(self, record: dict[str, Any]) -> None:
        """kabu 注文レコードを 1 件処理し、対応する Nautilus event を発火する。"""
        order_id = str(record.get("OrderID") or record.get("ID") or "")
        try:
            state = int(record.get("State", -1))
        except (TypeError, ValueError):
            log.warning(
                "KabuStationEventBridge: invalid State in record: %r", record
            )
            return

        # 冪等化
        key = (order_id, state)
        if key in self._seen:
            log.debug(
                "KabuStationEventBridge: duplicate record skipped order_id=%s state=%d",
                order_id,
                state,
            )
            return
        self._seen.add(key)

        if state == _STATE_RECEIVED:
            self._handle_accepted(record, order_id)
        elif state == _STATE_FILLED:
            self._handle_filled(record, order_id)
        elif state == _STATE_CANCELED:
            self._handle_canceled(record, order_id)
        elif state == _STATE_REJECTED:
            self._handle_rejected(record, order_id)
        else:
            log.debug(
                "KabuStationEventBridge: unhandled State=%d for order_id=%s",
                state,
                order_id,
            )

    # ------------------------------------------------------------------
    # internal handlers
    # ------------------------------------------------------------------

    def _handle_accepted(self, record: dict[str, Any], order_id: str) -> None:
        if not hasattr(self._client, "generate_order_accepted"):
            log.debug("client has no generate_order_accepted — skipping")
            return
        self._client.generate_order_accepted(
            order_id=order_id,
            symbol=record.get("Symbol"),
        )

    def _handle_filled(self, record: dict[str, Any], order_id: str) -> None:
        if not hasattr(self._client, "generate_order_filled"):
            log.debug("client has no generate_order_filled — skipping")
            return
        self._client.generate_order_filled(
            order_id=order_id,
            symbol=record.get("Symbol"),
            cum_qty=record.get("CumQty"),
            price=record.get("Price"),
        )

    def _handle_canceled(self, record: dict[str, Any], order_id: str) -> None:
        if not hasattr(self._client, "generate_order_canceled"):
            log.debug("client has no generate_order_canceled — skipping")
            return
        self._client.generate_order_canceled(
            order_id=order_id,
            symbol=record.get("Symbol"),
        )

    def _handle_rejected(self, record: dict[str, Any], order_id: str) -> None:
        if not hasattr(self._client, "generate_order_rejected"):
            log.debug("client has no generate_order_rejected — skipping")
            return
        self._client.generate_order_rejected(
            order_id=order_id,
            symbol=record.get("Symbol"),
            reason=record.get("DetailState"),
        )

    def reset_seen(self) -> None:
        """日次リセット。tachibana_event_bridge と同様。"""
        self._seen.clear()
