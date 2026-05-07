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

    def reset(self, initial_cash: Decimal) -> None:
        """reload 時に initial_cash からリセット。"""
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._positions = {}

    def on_fill(
        self, instrument_id: str, side: str, qty: Decimal, price: Decimal
    ) -> None:
        """約定イベントを処理して cash / position を更新する。"""
        if qty <= 0 or price <= 0:
            return
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
        """equity = cash + position MTM."""
        if not last_prices or not self._positions:
            return self._cash
        mtm = sum(
            pos["qty"] * last_prices.get(inst, Decimal(0))
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
        """ReplayBuyingPower IPC event dict を返す。"""
        if self._positions and not last_prices:
            log.warning(
                "PortfolioView.to_ipc_dict: %d position(s) held but last_prices=None, MTM=0",
                len(self._positions),
            )
        eq = self.equity(last_prices)
        return {
            "event": "ReplayBuyingPower",
            "strategy_id": strategy_id,
            "cash": str(self._cash),
            "buying_power": str(self.buying_power),
            "equity": str(eq),
            "ts_event_ms": int(time.time() * 1000),
        }
