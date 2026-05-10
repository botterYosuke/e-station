"""KabuStationLiveExecutionClient — kabusapi venue 用 LiveExecutionClient adapter (issue #42 Phase 4)。

設計方針:
- 既存 ``engine.exchanges.kabusapi*`` モジュール（KabuStationVenue / KabuOrderClient /
  KabuRestClient）に委譲する thin adapter。重複実装は禁止。
- tachibana 同等の安全装置（``max_qty`` / ``max_notional_jpy`` 必須）を ``__init__`` で
  チェックし、未設定なら ``ValueError`` で起動拒否。
- ``warm_up()`` は ``KabuStationVenue.fetch_orders()`` を呼んで未決注文の存在を確認する。
  失敗時は **例外を catch して False を返す**（Phase 1 の判定経路と互換、tachibana 同等）。
- ``close()`` は ``KabuStationVenue.clear()`` を呼び、token / order_client /
  trade_password_holder を解放する。
- 注文発行は最小限: ``submit_order`` は ``KabuOrderClient.send_order()`` に委譲。
  modify は kabuStation API に存在しない（``supports_amend=False`` cap）ため
  reject を返す。cancel は ``KabuOrderClient.cancel_order()`` に委譲。

LiveExecutionClient 互換 (``nautilus_trader.live.execution_client.LiveExecutionClient``)
として実装するが、Phase 4 の最小スコープでは「warm_up / close / 安全装置 + 代表 1 経路
(submit_order)」のみを通す。残りは TODO で残す。
"""
from __future__ import annotations

import logging
import time as _time
from decimal import Decimal
from typing import Any, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 安全チェック（tachibana 側と同等。共通化は将来 BaseLiveExecClient で行う）
# ---------------------------------------------------------------------------


def _check_safety_limits(
    order: Any,
    *,
    max_qty: Optional[int],
    max_notional_jpy: Optional[int],
) -> Optional[str]:
    """発注前安全チェック。

    Returns:
        reject 理由文字列（None なら通過）
    """
    if max_qty is not None:
        try:
            qty = int(Decimal(str(order.quantity)))
            if qty > max_qty:
                return f"QUANTITY_EXCEEDED: {qty} > max_qty={max_qty}"
        except Exception as exc:
            log.error("_check_safety_limits: parse error: %s", exc)
            return f"SAFETY_CHECK_ERROR: {exc}"

    if (
        max_notional_jpy is not None
        and hasattr(order, "price")
        and order.price is not None
    ):
        try:
            qty = int(Decimal(str(order.quantity)))
            px = Decimal(str(order.price))
            notional = qty * px
            if notional > max_notional_jpy:
                return (
                    f"NOTIONAL_EXCEEDED: {notional} > max_notional_jpy={max_notional_jpy}"
                )
        except Exception as exc:
            log.error("_check_safety_limits: parse error: %s", exc)
            return f"SAFETY_CHECK_ERROR: {exc}"

    return None


# ---------------------------------------------------------------------------
# KabuStationLiveExecutionClient
# ---------------------------------------------------------------------------


class KabuStationLiveExecutionClient:
    """kabuステーション 向け LiveExecutionClient adapter。

    既存 ``KabuStationVenue`` (``engine.exchanges.kabusapi``) に委譲する。

    Args:
        kabu_venue: ``KabuStationVenue`` インスタンス（または互換 mock）。
        strategy_id: strategy 識別子。
        max_qty: 1 注文あたりの最大株数（必須、未設定は起動拒否）。
        max_notional_jpy: 1 注文あたりの最大金額（円、必須、未設定は起動拒否）。
    """

    def __init__(
        self,
        *,
        kabu_venue: Any,
        strategy_id: str = "kabu_station",
        max_qty: Optional[int] = None,
        max_notional_jpy: Optional[int] = None,
        **_kwargs: Any,
    ) -> None:
        # 安全装置 — 数量 / 金額上限の設定必須（tachibana 同等）。
        if max_qty is None:
            raise ValueError(
                "KabuStationLiveExecutionClient: max_qty must be specified "
                "(safety guard). Example: max_qty=1000"
            )
        if max_notional_jpy is None:
            raise ValueError(
                "KabuStationLiveExecutionClient: max_notional_jpy must be specified "
                "(safety guard). Example: max_notional_jpy=1_000_000"
            )

        self._kabu_venue = kabu_venue
        self._strategy_id = strategy_id
        self._max_qty = max_qty
        self._max_notional_jpy = max_notional_jpy
        self.id = f"kabu_station-exec-{strategy_id}"

    # ------------------------------------------------------------------
    # warm-up: fetch open orders from kabu /orders（失敗時 False、Phase 1 互換）
    # ------------------------------------------------------------------

    async def warm_up(self) -> bool:
        """``GET /orders`` で当日未決注文を取得し warm-up する。

        Returns:
            True on success (空 list でも True)、例外で False（Phase 1 判定経路と互換）。
        """
        try:
            fetcher = getattr(self._kabu_venue, "fetch_orders", None)
            if fetcher is None:
                log.warning(
                    "KabuStationLiveExecutionClient.warm_up: kabu_venue has no "
                    "fetch_orders() — skipping warm-up (treating as success)."
                )
                return True
            records = await fetcher()
            log.info(
                "KabuStationLiveExecutionClient.warm_up: %d orders loaded",
                len(records) if records is not None else 0,
            )
            return True
        except Exception as exc:
            log.error(
                "KabuStationLiveExecutionClient.warm_up: failed to fetch orders: %s",
                exc,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # close: HTTP / WebSocket / token 解放（tachibana 同等のリーク防止）
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """token / trade_password_holder / order_client を解放する。

        ``KabuStationVenue.clear()`` を呼ぶ。close 自体は同期 API のため await 不要だが、
        engine_runner 側が ``await exec_client.close()`` で呼ぶため async 関数として公開する。
        """
        try:
            clear = getattr(self._kabu_venue, "clear", None)
            if callable(clear):
                clear()
        except Exception as exc:
            log.warning(
                "KabuStationLiveExecutionClient.close: clear() raised: %s", exc
            )

    # ------------------------------------------------------------------
    # 注文発行（代表 1 経路: submit_order — KabuOrderClient.send_order に委譲）
    # ------------------------------------------------------------------

    async def submit_order(self, command: Any) -> None:
        """発注処理。``KabuOrderClient.send_order()`` に委譲する。

        Phase 4 の最小スコープでは現物 stock 1 経路のみを通す。
        Future / Option / 信用は TODO（既存 ``server.py::_do_submit_order_kabu`` の
        ロジックを将来移植する）。

        安全装置: ``max_qty`` / ``max_notional_jpy`` を超える注文は ``OrderDenied`` 経路
        （実装上は callback で記録）。
        """
        order = command.order if hasattr(command, "order") else command
        client_order_id = getattr(order, "client_order_id", None) or "?"

        # 安全チェック
        reason = _check_safety_limits(
            order,
            max_qty=self._max_qty,
            max_notional_jpy=self._max_notional_jpy,
        )
        if reason is not None:
            log.warning(
                "KabuStationLiveExecutionClient.submit_order: rejected %s: %s",
                client_order_id,
                reason,
            )
            self._record_order_denied(reason, client_order_id=client_order_id)
            return

        # KabuOrderClient.send_order() に委譲
        # NOTE(Phase 4 minimal): 現状 instrument_id / side mapping を最小化している。
        # 完全な envelope 変換は server.py::_do_submit_order_kabu と同等の処理が必要なため
        # TODO で残す（既存 server.py 実装を adapter に移植）。
        try:
            if hasattr(self._kabu_venue, "send_order"):
                # quantity / side / price / instrument などの最小マッピング
                instrument_id = str(getattr(order, "instrument_id", ""))
                symbol = instrument_id.split(".")[0] if "." in instrument_id else instrument_id
                side_str = self._extract_side(order)
                qty = int(Decimal(str(order.quantity)))
                price = (
                    float(Decimal(str(order.price)))
                    if getattr(order, "price", None) is not None
                    else None
                )
                await self._kabu_venue.send_order(
                    symbol=symbol,
                    side=side_str,
                    qty=qty,
                    price=price,
                )
        except Exception as exc:
            log.error(
                "KabuStationLiveExecutionClient.submit_order: send_order failed: %s",
                exc,
                exc_info=True,
            )
            self._record_order_denied(f"SUBMIT_FAILED: {exc}", client_order_id=client_order_id)

    @staticmethod
    def _extract_side(order: Any) -> str:
        """nautilus OrderSide → "buy" / "sell" の文字列に変換する。"""
        side = getattr(order, "side", None)
        if side is None:
            return "buy"
        try:
            from nautilus_trader.model.enums import OrderSide

            if side == OrderSide.SELL:
                return "sell"
            return "buy"
        except Exception:
            # Cython enum value がそのまま入っているケース
            try:
                return "sell" if int(side) == 2 else "buy"
            except Exception:
                return "buy"

    def _record_order_denied(self, reason: str, *, client_order_id: str) -> None:
        """OrderDenied 系のイベントを発火する hook。

        LiveExecutionClient 親クラスを継承していない最小実装のため、ここではログ記録
        のみ。Phase 4 完了後にフル統合する際は ``LiveExecutionClient.generate_order_denied``
        互換に差し替える。
        """
        log.info(
            "KabuStationLiveExecutionClient: OrderDenied client_order_id=%s reason=%s",
            client_order_id,
            reason,
        )
