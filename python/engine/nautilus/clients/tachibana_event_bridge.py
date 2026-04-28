"""EC frame → nautilus OrderFilled / OrderCanceled / OrderExpired 変換ブリッジ (N2.2)

data-mapping.md §5 の写像仕様に従う。

不変条件:
    - 同一 (venue_order_id, trade_id) の EC が再送された場合に 2 重発火しない（M5）
    - order/ の TachibanaEventClient の seen-set と二重ガードになるが、IPC 経路を跨ぐ
      再起動時の守りを兼ねる
    - cumulative_qty = sOrderSuryou - sZanSuu で計算
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.enums import LiquiditySide, OrderSide, OrderType
from nautilus_trader.model.identifiers import (
    ClientOrderId,
    InstrumentId,
    PositionId,
    StrategyId,
    TradeId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity

if TYPE_CHECKING:
    from nautilus_trader.live.execution_client import LiveExecutionClient

from engine.exchanges.tachibana_event import OrderEcEvent

log = logging.getLogger(__name__)

# EC 通知種別定数（p_NT の値、tachibana_event.py と同じ）
_NT_RECEIVED = "1"
_NT_FILLED = "2"
_NT_CANCELED = "3"
_NT_EXPIRED = "4"


class TachibanaEventBridge:
    """立花 EC フレームを nautilus の generate_* メソッドに橋渡しする。

    冪等化 (M5):
        同一 (venue_order_id, trade_id) の EC を二重処理しないよう
        adapter 内部に seen-set を保持する。

    使い方:
        bridge = TachibanaEventBridge(client, order_id_map)
        bridge.process_ec_event(ec_event)
    """

    def __init__(
        self,
        client: LiveExecutionClient,
        order_id_map: OrderIdMap,
    ) -> None:
        self._client = client
        self._order_id_map = order_id_map
        # 冪等化用 seen-set: (venue_order_id, trade_id)
        self._seen: set[tuple[str, str]] = set()

    def process_ec_event(self, ec: OrderEcEvent) -> None:
        """EC フレーム由来の OrderEcEvent を nautilus イベントに変換して発火する。

        notification_type:
            "1" (受付)   → generate_order_submitted (no-op: submit 時に送出済み)
            "2" (約定)   → generate_order_filled
            "3" (取消)   → generate_order_canceled
            "4" (失効)   → generate_order_expired
        """
        # 冪等化ガード（M5）
        key = (ec.venue_order_id, ec.trade_id)
        if key in self._seen:
            log.debug(
                "EventBridge: duplicate EC skipped venue_order_id=%s trade_id=%s",
                ec.venue_order_id,
                ec.trade_id,
            )
            return
        self._seen.add(key)

        nt = ec.notification_type
        if nt == _NT_RECEIVED:
            # 受付は submit_order 完了時に generate_order_submitted 済み → no-op
            return
        elif nt == _NT_FILLED:
            self._handle_filled(ec)
        elif nt == _NT_CANCELED:
            self._handle_canceled(ec)
        elif nt == _NT_EXPIRED:
            self._handle_expired(ec)
        else:
            log.warning(
                "EventBridge: unknown notification_type %r for venue_order_id=%s",
                nt,
                ec.venue_order_id,
            )

    # TODO(N3): 毎日の市場クローズ後（立花 ST フレーム検知時）に server.py から呼ぶこと。
    # server.py 配線が未完了（N3 繰越）のため、現在は日次リセットが実行されない。
    def reset_seen(self) -> None:
        """日次リセット。夜間閉局後に呼ぶ。"""
        self._seen.clear()

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _handle_filled(self, ec: OrderEcEvent) -> None:
        client_order_id = self._order_id_map.get_client_order_id(ec.venue_order_id)
        if client_order_id is None:
            log.warning(
                "EventBridge: no ClientOrderId found for venue_order_id=%s — skipping fill",
                ec.venue_order_id,
            )
            return

        order_info = self._order_id_map.get_order_info(client_order_id)
        if order_info is None:
            log.warning(
                "EventBridge: no order info for client_order_id=%s — skipping fill",
                client_order_id,
            )
            return

        last_price_str = ec.last_price or "0"
        last_qty_str = ec.last_qty or "0"

        try:
            last_qty = Quantity(Decimal(last_qty_str), precision=0)
            last_px = Price(Decimal(last_price_str), precision=1)
        except Exception as exc:
            log.error(
                "EventBridge: failed to parse fill price/qty for venue_order_id=%s: %s",
                ec.venue_order_id,
                exc,
                exc_info=True,
            )
            return

        ts_ns = ec.ts_event_ms * 1_000_000

        self._client.generate_order_filled(
            strategy_id=order_info["strategy_id"],
            instrument_id=InstrumentId.from_str(order_info["instrument_id"]),
            client_order_id=ClientOrderId(client_order_id),
            venue_order_id=VenueOrderId(ec.venue_order_id),
            venue_position_id=None,
            trade_id=TradeId(ec.trade_id or f"EC-{ec.venue_order_id}"),
            order_side=order_info["order_side"],
            order_type=order_info["order_type"],
            last_qty=last_qty,
            last_px=last_px,
            quote_currency=JPY,
            commission=Money(0.0, JPY),
            liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
            ts_event=ts_ns,
        )

    def _handle_canceled(self, ec: OrderEcEvent) -> None:
        client_order_id = self._order_id_map.get_client_order_id(ec.venue_order_id)
        if client_order_id is None:
            log.warning(
                "EventBridge: no ClientOrderId for canceled venue_order_id=%s",
                ec.venue_order_id,
            )
            return

        order_info = self._order_id_map.get_order_info(client_order_id)
        if order_info is None:
            log.warning(
                "EventBridge: no order info for client_order_id=%s — skipping canceled/expired",
                client_order_id,
            )
            return

        ts_ns = ec.ts_event_ms * 1_000_000

        self._client.generate_order_canceled(
            strategy_id=order_info["strategy_id"],
            instrument_id=InstrumentId.from_str(order_info["instrument_id"]),
            client_order_id=ClientOrderId(client_order_id),
            venue_order_id=VenueOrderId(ec.venue_order_id),
            ts_event=ts_ns,
        )

    def _handle_expired(self, ec: OrderEcEvent) -> None:
        client_order_id = self._order_id_map.get_client_order_id(ec.venue_order_id)
        if client_order_id is None:
            log.warning(
                "EventBridge: no ClientOrderId for expired venue_order_id=%s",
                ec.venue_order_id,
            )
            return

        order_info = self._order_id_map.get_order_info(client_order_id)
        if order_info is None:
            log.warning(
                "EventBridge: no order info for client_order_id=%s — skipping canceled/expired",
                client_order_id,
            )
            return

        ts_ns = ec.ts_event_ms * 1_000_000

        # nautilus に OrderExpired はないため OrderCanceled として扱う
        # （失効 = 期日到来による自動取消）
        self._client.generate_order_canceled(
            strategy_id=order_info["strategy_id"],
            instrument_id=InstrumentId.from_str(order_info["instrument_id"]),
            client_order_id=ClientOrderId(client_order_id),
            venue_order_id=VenueOrderId(ec.venue_order_id),
            ts_event=ts_ns,
        )


# ---------------------------------------------------------------------------
# OrderIdMap — ClientOrderId ⇔ sOrderNumber 双方向写像 (N2.3)
# ---------------------------------------------------------------------------


class OrderIdMap:
    """nautilus ClientOrderId ⇔ 立花 sOrderNumber の双方向写像。

    N2.3: プロセス再起動時に CLMOrderList から warm-up する。
    tachibana_orders.OrderSessionState を参照するのではなく、
    adapter 内部に最小限の写像テーブルを持つ設計（IPC 境界を跨がない）。
    """

    def __init__(self) -> None:
        # client_order_id → {"venue_order_id": ..., "instrument_id": ..., ...}
        self._by_client: dict[str, dict[str, Any]] = {}
        # venue_order_id → client_order_id
        self._by_venue: dict[str, str] = {}

    def register(
        self,
        client_order_id: str,
        venue_order_id: str,
        instrument_id: str,
        strategy_id: str,
        order_side: OrderSide,
        order_type: OrderType,
    ) -> None:
        """注文登録（submit 完了後に呼ぶ）。"""
        info = {
            "venue_order_id": venue_order_id,
            "instrument_id": instrument_id,
            "strategy_id": StrategyId(strategy_id),
            "order_side": order_side,
            "order_type": order_type,
        }
        self._by_client[client_order_id] = info
        self._by_venue[venue_order_id] = client_order_id

    def get_client_order_id(self, venue_order_id: str) -> str | None:
        """venue_order_id から client_order_id を逆引きする。"""
        return self._by_venue.get(venue_order_id)

    def get_order_info(self, client_order_id: str) -> dict[str, Any] | None:
        """client_order_id から注文情報を取得する。"""
        return self._by_client.get(client_order_id)

    def remove(self, client_order_id: str) -> None:
        """注文を写像から除去する（約定完了・取消後）。"""
        info = self._by_client.pop(client_order_id, None)
        if info:
            self._by_venue.pop(info.get("venue_order_id", ""), None)

    def warm_up_from_records(
        self,
        records: list[Any],
        strategy_id: str,
    ) -> None:
        """CLMOrderList の OrderRecordWire リストから写像を初期化する（N2.3）。

        Args:
            records: tachibana_orders.fetch_order_list() の戻り値
            strategy_id: 現在実行中の strategy_id 文字列
        """
        from nautilus_trader.model.enums import OrderSide, OrderType

        _SIDE_MAP = {"BUY": OrderSide.BUY, "SELL": OrderSide.SELL}
        _TYPE_MAP = {
            "MARKET": OrderType.MARKET,
            "LIMIT": OrderType.LIMIT,
            "STOP_MARKET": OrderType.STOP_MARKET,
            "STOP_LIMIT": OrderType.STOP_LIMIT,
        }

        warmed_count = 0
        for rec in records:
            coid = getattr(rec, "client_order_id", None)
            void = getattr(rec, "venue_order_id", None)
            instrument_id = getattr(rec, "instrument_id", None)
            order_side_str = getattr(rec, "order_side", "BUY")
            order_type_str = getattr(rec, "order_type", "MARKET")
            status = getattr(rec, "status", "")

            if not void or status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                continue

            # P-11: instrument_id が None の場合はスキップ
            if not instrument_id:
                log.warning(
                    "OrderIdMap.warm_up: record has no instrument_id, venue_order_id=%s — skipping",
                    void,
                )
                continue

            if coid is None:
                coid = f"WARM-{void}"

            _mapped_side = _SIDE_MAP.get(order_side_str)
            if _mapped_side is None:
                log.warning(
                    "OrderIdMap.warm_up: unknown order_side %r for venue_order_id=%s — defaulting to BUY",
                    order_side_str,
                    void,
                )
                _mapped_side = OrderSide.BUY

            _mapped_type = _TYPE_MAP.get(order_type_str)
            if _mapped_type is None:
                log.warning(
                    "OrderIdMap.warm_up: unknown order_type %r for venue_order_id=%s — defaulting to MARKET",
                    order_type_str,
                    void,
                )
                _mapped_type = OrderType.MARKET

            self.register(
                client_order_id=coid,
                venue_order_id=void,
                instrument_id=instrument_id,
                strategy_id=strategy_id,
                order_side=_mapped_side,
                order_type=_mapped_type,
            )
            warmed_count += 1
        log.info(
            "OrderIdMap: warmed up %d/%d orders from CLMOrderList (skipped %d terminal)",
            warmed_count, len(records), len(records) - warmed_count,
        )
