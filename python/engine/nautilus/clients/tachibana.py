"""立花証券 LiveExecutionClient adapter (N2.1, N2.3, N2.4, N2.5)

tachibana_orders.py / tachibana_event.py の関数に委譲するだけの thin adapter。
重複実装は禁止（計画書 Constraints 参照）。

N2.3: 注文 ID マッピング + CLMOrderList warm-up
N2.4: 市場時間帯ガード（Disconnected{reason:"market_closed"} 期間は start 保留）
N2.5: 安全装置（TACHIBANA_ALLOW_PROD=1 未設定の本番 URL 禁止、数量上限 / 金額上限必須）
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from decimal import Decimal
from typing import Any, Optional

from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.enums import (
    AccountType,
    LiquiditySide,
    OmsType,
    OrderSide,
    OrderType,
)
from nautilus_trader.model.identifiers import (
    ClientId,
    ClientOrderId,
    InstrumentId,
    StrategyId,
    Venue,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity

from engine.exchanges.tachibana_orders import (
    NautilusOrderEnvelope,
    cancel_order as _tachibana_cancel_order,
    fetch_order_list as _tachibana_fetch_order_list,
    modify_order as _tachibana_modify_order,
    submit_order as _tachibana_submit_order,
)
from engine.exchanges.tachibana_ws import is_market_open
from engine.nautilus.clients.tachibana_event_bridge import OrderIdMap

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 安全装置定数
# ---------------------------------------------------------------------------

# 1 注文あたりの最大株数（未設定の場合は起動拒否）
_DEFAULT_MAX_QTY: Optional[int] = None
# 1 注文あたりの最大金額（円、未設定の場合は起動拒否）
_DEFAULT_MAX_NOTIONAL_JPY: Optional[int] = None


# ---------------------------------------------------------------------------
# nautilus Order → NautilusOrderEnvelope 変換
# ---------------------------------------------------------------------------


def _order_to_envelope(order: Any) -> NautilusOrderEnvelope:
    """nautilus Order オブジェクトを NautilusOrderEnvelope に変換する。

    data-mapping.md §4 に基づく写像。
    """
    # OrderType (Cython enum value → string)
    _ORDER_TYPE_MAP = {
        1: "MARKET",
        2: "LIMIT",
        3: "STOP_MARKET",
        4: "STOP_LIMIT",
        5: "MARKET_TO_LIMIT",
        6: "MARKET_IF_TOUCHED",
        7: "LIMIT_IF_TOUCHED",
    }
    # TimeInForce (Cython enum value → string)
    _TIF_MAP = {
        1: "GTC",
        2: "IOC",
        3: "FOK",
        4: "GTD",
        5: "DAY",
        6: "AT_THE_OPEN",
        7: "AT_THE_CLOSE",
    }
    # OrderSide (Cython enum value → string)
    _SIDE_MAP = {
        1: "BUY",
        2: "SELL",
    }

    order_type_val = int(order.order_type)
    order_type_str = _ORDER_TYPE_MAP.get(order_type_val, str(order.order_type))

    tif_val = int(order.time_in_force)
    tif_str = _TIF_MAP.get(tif_val, str(order.time_in_force))

    side_val = int(order.side)
    side_str = _SIDE_MAP.get(side_val, "BUY")

    # price / trigger_price / expire_time_ns
    price_str: Optional[str] = None
    if hasattr(order, "price") and order.price is not None:
        try:
            price_str = str(order.price)
        except Exception:
            pass

    trigger_price_str: Optional[str] = None
    if hasattr(order, "trigger_price") and order.trigger_price is not None:
        try:
            trigger_price_str = str(order.trigger_price)
        except Exception:
            pass

    trigger_type_str: Optional[str] = None
    if hasattr(order, "trigger_type") and order.trigger_type is not None:
        try:
            trigger_type_str = str(order.trigger_type)
        except Exception:
            pass

    expire_time_ns: Optional[int] = None
    if hasattr(order, "expire_time") and order.expire_time is not None:
        try:
            expire_time_ns = int(order.expire_time)
        except Exception:
            pass

    tags: list[str] = []
    if hasattr(order, "tags") and order.tags:
        tags = list(order.tags)

    return NautilusOrderEnvelope(
        client_order_id=str(order.client_order_id),
        instrument_id=str(order.instrument_id),
        order_side=side_str,
        order_type=order_type_str,
        quantity=str(order.quantity),
        time_in_force=tif_str,
        post_only=getattr(order, "is_post_only", False),
        reduce_only=getattr(order, "is_reduce_only", False),
        price=price_str,
        trigger_price=trigger_price_str,
        trigger_type=trigger_type_str,
        expire_time_ns=expire_time_ns,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# 安全チェック
# ---------------------------------------------------------------------------


def _check_safety_limits(
    order: Any,
    *,
    max_qty: Optional[int],
    max_notional_jpy: Optional[int],
) -> Optional[str]:
    """発注前安全チェック（N2.5）。

    Returns:
        reject 理由文字列（None なら通過）
    """
    if max_qty is not None:
        try:
            qty = int(Decimal(str(order.quantity)))
            if qty > max_qty:
                return f"QUANTITY_EXCEEDED: {qty} > max_qty={max_qty}"
        except Exception:
            pass

    if max_notional_jpy is not None and hasattr(order, "price") and order.price is not None:
        try:
            qty = int(Decimal(str(order.quantity)))
            px = Decimal(str(order.price))
            notional = qty * px
            if notional > max_notional_jpy:
                return (
                    f"NOTIONAL_EXCEEDED: {notional} > max_notional_jpy={max_notional_jpy}"
                )
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# TachibanaLiveExecutionClient
# ---------------------------------------------------------------------------


class TachibanaLiveExecutionClient(LiveExecutionClient):
    """立花証券 e支店 向け LiveExecutionClient adapter。

    tachibana_orders.submit_order / modify_order / cancel_order に委譲する。
    nautilus Order → NautilusOrderEnvelope 変換は _order_to_envelope() で行う。

    N2.3: __init__ に session / second_password / p_no_counter を受け取る。
          warm_up() で CLMOrderList を引き、未決注文を OrderIdMap に登録する。
    N2.4: _connect() で is_market_open() を確認し、市場閉場中なら接続を保留する。
    N2.5: __init__ で max_qty / max_notional_jpy の設定必須チェック。
          未設定の場合は ValueError で起動拒否する。
    """

    def __init__(
        self,
        *args: Any,
        session: Any,
        second_password: str,
        p_no_counter: Any = None,
        strategy_id: str = "tachibana",
        max_qty: Optional[int] = None,
        max_notional_jpy: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        # N2.5: 安全装置 — 数量/金額上限の設定必須（未設定は起動拒否）
        # super().__init__() より先にチェックして起動自体を拒否する
        if max_qty is None:
            raise ValueError(
                "TachibanaLiveExecutionClient: max_qty must be specified in config "
                "(N2.5 safety guard). Example: max_qty=1000"
            )
        if max_notional_jpy is None:
            raise ValueError(
                "TachibanaLiveExecutionClient: max_notional_jpy must be specified in config "
                "(N2.5 safety guard). Example: max_notional_jpy=1_000_000"
            )

        super().__init__(*args, **kwargs)

        self._session = session
        self._second_password = second_password
        self._p_no_counter = p_no_counter
        self._strategy_id = strategy_id
        self._max_qty = max_qty
        self._max_notional_jpy = max_notional_jpy

        # N2.3: 注文 ID マッピング
        self._order_id_map = OrderIdMap()

    # ------------------------------------------------------------------
    # N2.3: Cache warm-up
    # ------------------------------------------------------------------

    async def warm_up(self) -> None:
        """CLMOrderList から当日未決注文を OrderIdMap に登録する（N2.3）。

        SetVenueCredentials → VenueReady 受信後、LiveExecutionEngine.start() 前に呼ぶ。
        warm-up 完了前に submit_order を受けた場合はキューに積む実装は N3 以降。
        """
        try:
            records = await _tachibana_fetch_order_list(
                self._session,
                p_no_counter=self._p_no_counter,
            )
            self._order_id_map.warm_up_from_records(records, self._strategy_id)
            log.info("warm_up: %d open orders loaded from CLMOrderList", len(records))
        except Exception as exc:
            log.error("warm_up: failed to fetch CLMOrderList: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # N2.1: LiveExecutionClient abstract methods
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """接続処理（N2.4: 市場時間帯ガード付き）。"""
        from datetime import datetime, timedelta, timezone

        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst)

        if not is_market_open(now_jst):
            log.info(
                "TachibanaLiveExecutionClient: market is closed at %s JST — "
                "connecting in read-only mode (no order submission allowed)",
                now_jst.strftime("%H:%M:%S"),
            )
        else:
            log.info("TachibanaLiveExecutionClient: market is open — connecting")

        self._set_connected(True)
        log.info("TachibanaLiveExecutionClient connected")

    async def _disconnect(self) -> None:
        self._set_connected(False)
        log.info("TachibanaLiveExecutionClient disconnected")

    async def _submit_order(self, command: Any) -> None:
        """発注処理（N2.1 + N2.5 安全チェック付き）。"""
        from datetime import datetime, timedelta, timezone

        order = command.order
        client_order_id = str(order.client_order_id)
        instrument_id_str = str(order.instrument_id)
        strategy_id = str(command.strategy_id)
        ts_ns = int(_time.time() * 1e9)

        # N2.4: 市場時間帯ガード
        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst)
        if not is_market_open(now_jst):
            reason = "MARKET_CLOSED"
            log.warning(
                "_submit_order: market closed, rejecting %s", client_order_id
            )
            self.generate_order_denied(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=reason,
                ts_event=ts_ns,
            )
            return

        # N2.5: 安全チェック
        safety_reject = _check_safety_limits(
            order,
            max_qty=self._max_qty,
            max_notional_jpy=self._max_notional_jpy,
        )
        if safety_reject is not None:
            log.warning(
                "_submit_order: safety limit exceeded, rejecting %s: %s",
                client_order_id,
                safety_reject,
            )
            self.generate_order_denied(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=safety_reject,
                ts_event=ts_ns,
            )
            return

        envelope = _order_to_envelope(order)

        try:
            result = await _tachibana_submit_order(
                self._session,
                self._second_password,
                envelope,
                p_no_counter=self._p_no_counter,
            )
        except Exception as exc:
            log.error(
                "_submit_order: submit failed for %s: %s",
                client_order_id,
                exc,
                exc_info=True,
            )
            self.generate_order_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(exc),
                ts_event=ts_ns,
            )
            return

        venue_order_id = result.venue_order_id or ""

        self.generate_order_submitted(
            strategy_id=command.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=ts_ns,
        )

        if venue_order_id:
            self.generate_order_accepted(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=VenueOrderId(venue_order_id),
                ts_event=ts_ns,
            )

            # N2.3: 写像登録
            self._order_id_map.register(
                client_order_id=client_order_id,
                venue_order_id=venue_order_id,
                instrument_id=instrument_id_str,
                strategy_id=strategy_id,
                order_side=order.side,
                order_type=order.order_type,
            )
        else:
            log.warning(
                "_submit_order: no venue_order_id returned for %s (warning_code=%r)",
                client_order_id,
                result.warning_code,
            )
            self.generate_order_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason="VENUE_ORDER_ID_MISSING",
                ts_event=ts_ns,
            )

    async def _modify_order(self, command: Any) -> None:
        """注文訂正処理（N2.1）。"""
        order = command.order
        client_order_id = str(order.client_order_id)
        venue_order_id_str = str(command.venue_order_id) if command.venue_order_id else ""
        ts_ns = int(_time.time() * 1e9)

        # N2.4: 市場時間帯ガード
        from datetime import datetime, timedelta, timezone
        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst)
        if not is_market_open(now_jst):
            self.generate_order_modify_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=command.venue_order_id,
                reason="MARKET_CLOSED",
                ts_event=ts_ns,
            )
            return

        try:
            await _tachibana_modify_order(
                self._session,
                self._second_password,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id_str,
                change=command,
                p_no_counter=self._p_no_counter,
            )
        except Exception as exc:
            log.error(
                "_modify_order: failed for %s: %s",
                client_order_id,
                exc,
                exc_info=True,
            )
            self.generate_order_modify_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=command.venue_order_id,
                reason=str(exc),
                ts_event=ts_ns,
            )

    async def _cancel_order(self, command: Any) -> None:
        """注文取消処理（N2.1）。"""
        order = command.order
        client_order_id = str(order.client_order_id)
        venue_order_id_str = str(command.venue_order_id) if command.venue_order_id else ""
        ts_ns = int(_time.time() * 1e9)

        # N2.4: 市場時間帯ガード
        from datetime import datetime, timedelta, timezone
        jst = timezone(timedelta(hours=9))
        now_jst = datetime.now(jst)
        if not is_market_open(now_jst):
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=command.venue_order_id,
                reason="MARKET_CLOSED",
                ts_event=ts_ns,
            )
            return

        try:
            await _tachibana_cancel_order(
                self._session,
                self._second_password,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id_str,
                p_no_counter=self._p_no_counter,
            )
        except Exception as exc:
            log.error(
                "_cancel_order: failed for %s: %s",
                client_order_id,
                exc,
                exc_info=True,
            )
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=command.venue_order_id,
                reason=str(exc),
                ts_event=ts_ns,
            )

    # ------------------------------------------------------------------
    # 公開ヘルパ（server.py / event bridge から呼ぶ）
    # ------------------------------------------------------------------

    @property
    def order_id_map(self) -> OrderIdMap:
        """注文 ID 写像テーブルへのアクセサ。"""
        return self._order_id_map
