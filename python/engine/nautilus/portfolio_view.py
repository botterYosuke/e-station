"""REPLAY 仮想ポートフォリオ状態トラッカー。

CLMZanKaiKanougaku を一切呼ばない純粋 Python 実装。
Fill イベントを受け取り cash / equity をリアルタイムに追跡する。
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

log = logging.getLogger(__name__)


class PortfolioView:
    """仮想ポートフォリオ状態（fill ベース追跡）。

    nautilus Portfolio 内部に依存しない独立実装。
    """

    def __init__(self, initial_cash: Decimal) -> None:
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._positions: dict[str, dict] = {}  # instrument_id → {qty, cost}
        # 最新参照価格。on_fill / update_last_price で更新され、
        # equity() / to_ipc_dict() の last_prices 引数省略時のフォールバックに使う。
        # StepBackward 後の pull 経路（GetBuyingPower）でも MTM を保つため必要。
        self._last_prices: dict[str, Decimal] = {}

    def reset(self, initial_cash: Decimal) -> None:
        """reload 時に initial_cash からリセット。"""
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._positions = {}
        self._last_prices = {}

    def update_last_price(self, instrument_id: str, price: Decimal) -> None:
        """外部から最新参照価格を更新する（bar close など）。"""
        if price > 0:
            self._last_prices[instrument_id] = price

    def on_fill(
        self, instrument_id: str, side: str, qty: Decimal, price: Decimal
    ) -> None:
        """約定イベントを処理して cash / position を更新する。"""
        if qty <= 0 or price <= 0:
            return
        # fill 価格を最新参照価格として記録（pull 経路の equity 計算に使う）
        self._last_prices[instrument_id] = price
        amount = qty * price
        if side == "BUY":
            self._cash -= amount
            pos = self._positions.setdefault(
                instrument_id, {"qty": Decimal(0), "cost": Decimal(0)}
            )
            pos["qty"] += qty
            pos["cost"] += amount
        elif side == "SELL":
            self._cash += amount
            pos = self._positions.get(instrument_id)
            if pos is not None:
                pos["qty"] -= qty
                if pos["qty"] <= 0:
                    del self._positions[instrument_id]

    @property
    def has_open_positions(self) -> bool:
        return bool(self._positions)

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def buying_power(self) -> Decimal:
        return self._cash

    def equity(self, last_prices: dict[str, Decimal] | None = None) -> Decimal:
        """equity = cash + position MTM。

        last_prices 省略時は内部保持の self._last_prices にフォールバックする
        （StepBackward 後の pull 経路で MTM を維持するため）。
        """
        prices = last_prices if last_prices else self._last_prices
        if not prices or not self._positions:
            return self._cash
        mtm = sum(
            pos["qty"] * prices.get(inst, Decimal(0))
            for inst, pos in self._positions.items()
        )
        return self._cash + mtm

    def _restore_from_dict(self, d: dict) -> None:
        """Restore state from a snapshot dict.

        Handles two formats:
        - portfolio_state dict (full): {"cash": str, "positions": {...}, "last_prices": {...}}
        - IPC event dict (summary only): {"cash": str, ...} — positions are cleared.
        """
        self._cash = Decimal(str(d.get("cash", str(self._cash))))
        self._positions.clear()
        for inst, pos_data in d.get("positions", {}).items():
            self._positions[inst] = {
                "qty": Decimal(str(pos_data["qty"])),
                "cost": Decimal(str(pos_data["cost"])),
            }
        # last_prices を復元（pull 経路の equity 計算で MTM を維持するため）
        self._last_prices.clear()
        for inst, p in d.get("last_prices", {}).items():
            self._last_prices[inst] = Decimal(str(p))

    def to_snapshot_dict(self, last_prices: "dict | None" = None) -> dict:
        """Serialize full portfolio state for runner-side restoration.

        Format: {"cash": str, "positions": {inst: {qty, cost}}, "last_prices": {inst: str}}
        Different from to_ipc_dict() which produces the ReplayBuyingPower UI event.
        """
        return {
            "cash": str(self._cash),
            "positions": {
                inst: {"qty": str(pos["qty"]), "cost": str(pos["cost"])}
                for inst, pos in self._positions.items()
            },
            "last_prices": {k: str(v) for k, v in (last_prices or {}).items()},
        }

    def to_ipc_dict(
        self,
        strategy_id: str,
        last_prices: dict[str, Decimal] | None = None,
    ) -> dict:
        """ReplayBuyingPower IPC event dict を返す。

        last_prices 省略時は内部保持の self._last_prices にフォールバックする。
        """
        prices = last_prices if last_prices else self._last_prices
        if self._positions and not prices:
            log.warning(
                "PortfolioView.to_ipc_dict: %d position(s) held but last_prices=None, MTM=0",
                len(self._positions),
            )
        eq = self.equity(prices)
        return {
            "event": "ReplayBuyingPower",
            "strategy_id": strategy_id,
            "cash": str(self._cash),
            "buying_power": str(self.buying_power),
            "equity": str(eq),
            "ts_event_ms": int(time.time() * 1000),
        }
