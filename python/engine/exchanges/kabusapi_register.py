"""kabuステーション PUSH 銘柄登録セット — 50 銘柄上限 LRU 管理。

R6: REST/PUSH を合算して 50 銘柄まで。
Q-K5 決定: 暗黙 evict は行わず、満杯時は KabuRegisterFullError を投げる。

用語:
- register: 銘柄を登録（PUT /register は呼び出し元が行う）
- touch: LRU 位置更新（GET /board が呼ぶ）
- evict_lru: 最古の銘柄を登録解除して IPC SubscriptionEvicted を送出
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable

from engine.exchanges.kabusapi_auth import KabuRegisterFullError

# PUSH 銘柄数の上限 (comparison.md §7 が数値の一次ソース, INV-K1-CAP)
MAX_SYMBOLS: int = 50


class RegisterSet:
    """LRU ベースの銘柄登録セット。

    満杯時の暗黙 evict は行わない（Q-K5 決定、U24）。
    代わりに KabuRegisterFullError を投げてユーザーに登録解除を促す。
    """

    MAX: int = MAX_SYMBOLS  # capabilities test と一致させる (INV-K1-CAP)

    def __init__(
        self,
        on_evict: Callable[[str, int], None] | None = None,
        max_symbols: int = MAX_SYMBOLS,
    ) -> None:
        self._symbols: OrderedDict[tuple[str, int], None] = OrderedDict()
        self._on_evict = on_evict
        self._max = max_symbols

    def register(self, symbol: str, exchange: int) -> None:
        """銘柄を登録する。

        Raises:
            KabuRegisterFullError: 上限に達していて新規銘柄の場合
        """
        key = (symbol, exchange)
        if key in self._symbols:
            self.touch(symbol, exchange)
            return
        if len(self._symbols) >= self._max:
            raise KabuRegisterFullError(4002001, f"Register full ({self._max} symbols)")
        self._symbols[key] = None

    def unregister(self, symbol: str, exchange: int) -> bool:
        """銘柄を登録解除する。存在しない場合は False を返す。"""
        key = (symbol, exchange)
        if key in self._symbols:
            del self._symbols[key]
            return True
        return False

    def unregister_all(self) -> None:
        """全銘柄を登録解除する。"""
        self._symbols.clear()

    def touch(self, symbol: str, exchange: int = 1) -> None:
        """LRU 位置を最新に更新する（GET /board が呼ぶ）。"""
        key = (symbol, exchange)
        if key in self._symbols:
            self._symbols.move_to_end(key)

    def evict_lru(self) -> tuple[str, int] | None:
        """最古の銘柄を evict して IPC SubscriptionEvicted を送出する。

        Returns:
            evict した (symbol, exchange) のタプル、または空の場合 None
        """
        if not self._symbols:
            return None
        key, _ = self._symbols.popitem(last=False)  # FIFO: 最古を先頭から取り出す
        symbol, exchange = key
        if self._on_evict is not None:
            self._on_evict(symbol, exchange)
        return key

    def __len__(self) -> int:
        return len(self._symbols)

    def __contains__(self, item: tuple[str, int]) -> bool:
        return item in self._symbols

    def all_symbols(self) -> list[tuple[str, int]]:
        """登録中の全銘柄を LRU 順（古い順）で返す。"""
        return list(self._symbols.keys())
