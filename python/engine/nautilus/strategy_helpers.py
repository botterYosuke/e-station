"""N1.12: Strategy mixin — emit_signal() ユーティリティ.

``StrategySignalMixin`` は nautilus Strategy に mixin して使う。
``emit_signal()`` を呼ぶと ``StrategySignal`` IPC event dict が
``on_event`` callback 経由で outbox に積まれる。

約定（OrderFilled）とは独立して呼べる。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# N1.12 暫定語彙（Q13 確定まで）
_VALID_SIGNAL_KINDS = frozenset({"EntryLong", "EntryShort", "Exit", "Annotate"})


class StrategySignalMixin:
    """``emit_signal()`` ユーティリティを提供する mixin。

    利用側は以下の属性を事前に設定すること:

    - ``_signal_strategy_id: str`` — 戦略 ID（例: ``"buy-and-hold-001"``）
    - ``_signal_instrument_id: str`` — 銘柄 ID（例: ``"1301.TSE"``）
    - ``_signal_on_event: Callable[[dict], None]`` — outbox callback

    ``setup_signal_mixin()`` ヘルパーメソッドで一括設定できる。
    """

    # ── セットアップ ──────────────────────────────────────────────────────────

    def setup_signal_mixin(
        self,
        strategy_id: str,
        instrument_id: str,
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        """mixin の必須属性をまとめて設定する。

        Parameters
        ----------
        strategy_id:
            戦略 ID（例: ``"buy-and-hold-001"``）。
        instrument_id:
            銘柄 ID（例: ``"1301.TSE"``）。
        on_event:
            ``StrategySignal`` event dict を受け取る callback。
            outbox への ``deque.append`` 等を渡す。
        """
        self._signal_strategy_id: str = strategy_id
        self._signal_instrument_id: str = instrument_id
        self._signal_on_event: Callable[[dict[str, Any]], None] = on_event

    # ── メインAPI ────────────────────────────────────────────────────────────

    def emit_signal(
        self,
        kind: str,
        *,
        side: str | None = None,
        price: str | None = None,
        tag: str | None = None,
        note: str | None = None,
        ts_event_ms: int | None = None,
    ) -> None:
        """``StrategySignal`` IPC を送出する。

        ``on_event`` callback 経由で outbox に積む。
        約定（OrderFilled）とは独立して呼べる。

        Parameters
        ----------
        kind:
            シグナル種別。``"EntryLong"`` / ``"EntryShort"`` / ``"Exit"`` /
            ``"Annotate"`` のいずれか（Q13 確定まで暫定語彙）。
        side:
            ``"BUY"`` | ``"SELL"`` | ``None``。
        price:
            価格（decimal 文字列）または ``None``。
        tag:
            短い機械可読ラベル（例: ``"entry"``）または ``None``。
        note:
            人間可読な注記または ``None``。
        ts_event_ms:
            イベント時刻（Unix ミリ秒）。``None`` の場合は現在時刻を使う。

        Raises
        ------
        ValueError
            ``kind`` が不正な値の場合。
        AttributeError
            ``setup_signal_mixin()`` が呼ばれていない場合。
        """
        if kind not in _VALID_SIGNAL_KINDS:
            raise ValueError(
                f"invalid signal_kind {kind!r}; must be one of {sorted(_VALID_SIGNAL_KINDS)}"
            )

        strategy_id: str = getattr(self, "_signal_strategy_id", "")
        instrument_id: str = getattr(self, "_signal_instrument_id", "")
        on_event: Callable[[dict[str, Any]], None] | None = getattr(
            self, "_signal_on_event", None
        )

        if on_event is None:
            log.warning(
                "emit_signal: _signal_on_event not configured; signal %r dropped",
                kind,
            )
            return

        if ts_event_ms is None:
            ts_event_ms = int(time.time() * 1000)

        event: dict[str, Any] = {
            "event": "StrategySignal",
            "strategy_id": strategy_id,
            "instrument_id": instrument_id,
            "signal_kind": kind,
            "ts_event_ms": ts_event_ms,
        }
        if side is not None:
            event["side"] = side
        if price is not None:
            event["price"] = str(price)
        if tag is not None:
            event["tag"] = tag
        if note is not None:
            event["note"] = note

        try:
            on_event(event)
        except Exception as exc:  # noqa: BLE001
            log.warning("emit_signal: on_event callback failed — %s", exc)
