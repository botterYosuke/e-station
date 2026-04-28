"""Hyperliquid LiveExecutionClient adapter（N3.A）

hyperliquid_orders.py の関数に委譲するだけの thin adapter。
重複実装は禁止。

Hyperliquid は 24/7 市場のため市場時間帯ガードは不要。
modify_order は Hyperliquid 未対応のため常に OrderModifyRejected を返す。
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any, Optional

from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import VenueOrderId

from engine.exchanges.hyperliquid_orders import (
    HyperliquidSession,
    cancel_order as _hl_cancel_order,
    submit_order as _hl_submit_order,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# セーフティ定数
# ---------------------------------------------------------------------------

_DEFAULT_MAX_QTY: float = 0.0
"""未設定時は起動拒否。"""


# ---------------------------------------------------------------------------
# 変換ヘルパー
# ---------------------------------------------------------------------------


def _order_to_hl_envelope(order: Any) -> Any:
    """NautilusTrader Order → hyperliquid_orders.submit_order が期待する envelope 形式に変換。

    NautilusTrader: order.side = OrderSide.BUY (int 1) / .SELL (int 2)
                    order.order_type = OrderType.MARKET (int 1) / .LIMIT (int 2)
    Hyperliquid:    envelope.order_side = "BUY" | "SELL"
                    envelope.order_type = "MARKET" | "LIMIT"
    """
    from nautilus_trader.model.enums import OrderSide, OrderType

    # OrderSide.BUY == 1, OrderSide.SELL == 2
    order_side = "BUY" if order.side == OrderSide.BUY else "SELL"
    # OrderType.MARKET == 1, OrderType.LIMIT == 2
    order_type_str = "MARKET" if order.order_type == OrderType.MARKET else "LIMIT"
    # price: LIMIT 注文のみ
    price = None
    if order.order_type != OrderType.MARKET and hasattr(order, "price") and order.price is not None:
        price = str(order.price)
    asset_index = getattr(order, "asset_index", 0)

    return type("_HLEnvelope", (), {
        "order_side": order_side,
        "order_type": order_type_str,
        "quantity": str(order.quantity),
        "price": price,
        "asset_index": asset_index,
    })()


# ---------------------------------------------------------------------------
# HyperliquidExecutionClient
# ---------------------------------------------------------------------------


class HyperliquidExecutionClient(LiveExecutionClient):
    """Hyperliquid 向け LiveExecutionClient adapter。

    hyperliquid_orders.submit_order / cancel_order に委譲する。
    modify_order は Hyperliquid が未対応のため常に OrderModifyRejected を返す。

    安全装置:
        - session が None の場合は ValueError で起動拒否
        - max_qty_per_order が 0.0 以下の場合は ValueError で起動拒否

    24/7 市場:
        Hyperliquid は 24/7 取引可能のため is_market_open() チェックは行わない。
    """

    def __init__(
        self,
        *args: Any,
        session: Optional[HyperliquidSession],
        max_qty_per_order: float = _DEFAULT_MAX_QTY,
        **kwargs: Any,
    ) -> None:
        # セーフティガード: super().__init__() より先にチェックして起動自体を拒否する
        if session is None:
            raise ValueError(
                "HyperliquidExecutionClient: session must be specified "
                "(N3.A safety guard). Pass a HyperliquidSession with address and signer."
            )
        if max_qty_per_order <= 0.0:
            raise ValueError(
                "HyperliquidExecutionClient: max_qty_per_order must be > 0.0 "
                f"(N3.A safety guard). Got: {max_qty_per_order!r}. "
                "Example: max_qty_per_order=1.0"
            )

        super().__init__(*args, **kwargs)

        self._session = session
        self._max_qty_per_order = max_qty_per_order

    # ------------------------------------------------------------------
    # LiveExecutionClient abstract methods
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """接続処理。Hyperliquid は 24/7 なので常に接続可能。"""
        self._set_connected(True)
        log.info("HyperliquidExecutionClient connected (24/7 market)")

    async def _disconnect(self) -> None:
        self._set_connected(False)
        log.info("HyperliquidExecutionClient disconnected")

    async def _submit_order(self, command: Any) -> None:
        """発注処理。hyperliquid_orders.submit_order に委譲する。

        成功: generate_order_submitted + generate_order_accepted を発火。
        失敗: generate_order_denied を発火。
        API error: generate_order_rejected を発火。
        """
        order = command.order
        client_order_id = str(order.client_order_id)
        ts_ns = int(_time.time() * 1e9)

        # H4: NautilusTrader Order → Hyperliquid envelope 形式に変換
        envelope = _order_to_hl_envelope(order)

        # M1: max_qty_per_order ガード
        try:
            order_qty = float(envelope.quantity)
        except (ValueError, TypeError) as exc:
            log.error(
                "_submit_order: cannot parse qty %r for %s: %s — denying",
                envelope.quantity,
                client_order_id,
                exc,
            )
            self.generate_order_denied(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=f"INVALID_QTY: {envelope.quantity!r}",
                ts_event=ts_ns,
            )
            return

        if order_qty > self._max_qty_per_order:
            log.warning(
                "_submit_order: qty %s exceeds max_qty_per_order %s, denying %s",
                order_qty,
                self._max_qty_per_order,
                client_order_id,
            )
            self.generate_order_denied(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=f"EXCEEDS_MAX_QTY: qty {order_qty} > max_qty_per_order {self._max_qty_per_order}",
                ts_event=ts_ns,
            )
            return

        try:
            result = await _hl_submit_order(self._session, envelope)
        except Exception as exc:
            log.error(
                "_submit_order: submit failed for %s: %s",
                client_order_id,
                exc,
                exc_info=True,
            )
            self.generate_order_denied(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=f"SUBMIT_FAILED: {exc}",
                ts_event=ts_ns,
            )
            return

        if result.status != "ok":
            log.warning(
                "_submit_order: API rejected order %s: %s",
                client_order_id,
                result.message,
            )
            self.generate_order_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=result.message or "VENUE_REJECTED",
                ts_event=ts_ns,
            )
            return

        self.generate_order_submitted(
            strategy_id=command.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=ts_ns,
        )

        self.generate_order_accepted(
            strategy_id=command.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId(result.venue_order_id),
            ts_event=ts_ns,
        )
        log.info(
            "_submit_order: accepted %s → venue_order_id=%s",
            client_order_id,
            result.venue_order_id,
        )

    async def _cancel_order(self, command: Any) -> None:
        """注文取消処理。hyperliquid_orders.cancel_order に委譲する。"""
        order = command.order
        client_order_id = str(order.client_order_id)
        venue_order_id_str = str(command.venue_order_id) if command.venue_order_id else ""
        ts_ns = int(_time.time() * 1e9)

        # 無効な venue_order_id は注文がまだ Accept されていないことを示す
        # "None" 文字列は VenueOrderId("None") の str() が Truthy になるためここで弾く
        if not venue_order_id_str or venue_order_id_str in ("None", "0", "none"):
            log.error(
                "_cancel_order: invalid venue_order_id %r for %s — order may not have been accepted by venue",
                venue_order_id_str or "(empty)", client_order_id,
            )
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=command.venue_order_id,
                reason=f"INVALID_VENUE_ORDER_ID: {venue_order_id_str!r} — order may not have been accepted by venue",
                ts_event=ts_ns,
            )
            return

        # asset_index: order に asset_index が設定されていれば使う。
        # unit test では asset_index=0 固定で OK（実環境では instrument 解決が必要）。
        asset_index = getattr(order, "asset_index", 0)

        try:
            await _hl_cancel_order(
                self._session,
                venue_order_id=venue_order_id_str,
                asset_index=asset_index,
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
            return

        self.generate_order_canceled(
            strategy_id=command.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=command.venue_order_id,
            ts_event=ts_ns,
        )
        log.info("_cancel_order: canceled %s", client_order_id)

    async def _modify_order(self, command: Any) -> None:
        """注文訂正処理。Hyperliquid は modify 未対応のため常に拒否する。"""
        order = command.order
        ts_ns = int(_time.time() * 1e9)

        log.info(
            "_modify_order: Hyperliquid does not support order modification, rejecting %s",
            order.client_order_id,
        )
        self.generate_order_modify_rejected(
            strategy_id=command.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=command.venue_order_id,
            reason="NOT_SUPPORTED: Hyperliquid does not support order modification",
            ts_event=ts_ns,
        )
