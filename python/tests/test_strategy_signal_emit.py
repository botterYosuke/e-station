"""N1.12: StrategySignalMixin.emit_signal() → StrategySignal IPC テスト。"""

from __future__ import annotations

import pytest

from engine.nautilus.strategy_helpers import StrategySignalMixin


# ── StrategySignalMixin ユニットテスト ────────────────────────────────────────


def _make_mixin(strategy_id: str = "strat-001", instrument_id: str = "1301.TSE"):
    """テスト用に setup 済みの StrategySignalMixin インスタンスを返す。"""
    collected: list[dict] = []
    mixin = StrategySignalMixin()
    mixin.setup_signal_mixin(
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        on_event=collected.append,
    )
    return mixin, collected


def test_emit_signal_sends_strategy_signal_ipc():
    """emit_signal() を呼ぶと StrategySignal IPC が 1 件 emit される。"""
    mixin, collected = _make_mixin()

    mixin.emit_signal("EntryLong", side="BUY", price="1500.0", tag="entry", note="first")

    assert len(collected) == 1
    sig = collected[0]
    assert sig["event"] == "StrategySignal"
    assert sig["strategy_id"] == "strat-001"
    assert sig["instrument_id"] == "1301.TSE"
    assert sig["signal_kind"] == "EntryLong"
    assert sig["side"] == "BUY"
    assert sig["price"] == "1500.0"
    assert sig["tag"] == "entry"
    assert sig["note"] == "first"
    assert "ts_event_ms" in sig


def test_emit_signal_independent_of_fills():
    """未約定でも emit_signal() は独立して出る。

    OrderFilled イベントを呼ばなくても emit_signal() だけで IPC が出ること。
    """
    mixin, collected = _make_mixin()

    # OrderFilled 系のイベントは一切呼ばない
    mixin.emit_signal("EntryShort", side="SELL")

    assert len(collected) == 1
    sig = collected[0]
    assert sig["signal_kind"] == "EntryShort"
    assert sig["side"] == "SELL"


def test_emit_signal_multiple_calls_produce_multiple_ipc():
    """emit_signal() を N 回呼ぶと N 件の IPC が emit される。"""
    mixin, collected = _make_mixin()

    for _ in range(3):
        mixin.emit_signal("Annotate")

    assert len(collected) == 3
    assert all(s["signal_kind"] == "Annotate" for s in collected)


def test_entry_long_signal_kind_serializes():
    """signal_kind=EntryLong が IPC wire で 'EntryLong' になる。"""
    mixin, collected = _make_mixin()
    mixin.emit_signal("EntryLong")
    assert collected[0]["signal_kind"] == "EntryLong"


def test_entry_short_signal_kind_serializes():
    """signal_kind=EntryShort が IPC wire で 'EntryShort' になる。"""
    mixin, collected = _make_mixin()
    mixin.emit_signal("EntryShort")
    assert collected[0]["signal_kind"] == "EntryShort"


def test_exit_signal_kind_serializes():
    """signal_kind=Exit が IPC wire で 'Exit' になる。"""
    mixin, collected = _make_mixin()
    mixin.emit_signal("Exit")
    assert collected[0]["signal_kind"] == "Exit"


def test_annotate_signal_kind_serializes():
    """signal_kind=Annotate が IPC wire で 'Annotate' になる。"""
    mixin, collected = _make_mixin()
    mixin.emit_signal("Annotate")
    assert collected[0]["signal_kind"] == "Annotate"


def test_emit_signal_optional_fields_absent_when_not_provided():
    """side/price/tag/note を省略すると IPC dict にキーが含まれない。"""
    mixin, collected = _make_mixin()
    mixin.emit_signal("Exit")

    sig = collected[0]
    # None フィールドはシリアライズ時にキーを省くことで wire をスリムに保つ
    assert "side" not in sig
    assert "price" not in sig
    assert "tag" not in sig
    assert "note" not in sig


def test_emit_signal_ts_event_ms_provided():
    """ts_event_ms を明示指定すると IPC に反映される。"""
    mixin, collected = _make_mixin()
    mixin.emit_signal("Annotate", ts_event_ms=1_700_000_000_999)
    assert collected[0]["ts_event_ms"] == 1_700_000_000_999


def test_emit_signal_invalid_kind_raises():
    """不正な signal_kind は ValueError を raise する。"""
    mixin, _ = _make_mixin()
    with pytest.raises(ValueError, match="invalid signal_kind"):
        mixin.emit_signal("UnknownSignal")


def test_emit_signal_without_setup_logs_warning(caplog):
    """setup_signal_mixin() を呼ばずに emit_signal() を呼ぶと IPC は出ず警告ログが出る。"""
    import logging

    mixin = StrategySignalMixin()

    with caplog.at_level(logging.WARNING):
        mixin.emit_signal("EntryLong")  # on_event が未設定 → 警告のみ

    assert any("_signal_on_event not configured" in r.message for r in caplog.records)


def test_emit_signal_price_cast_to_str():
    """price に数値を渡しても str に変換される。"""
    mixin, collected = _make_mixin()
    mixin.emit_signal("EntryLong", price=1500)
    assert isinstance(collected[0]["price"], str)
    assert collected[0]["price"] == "1500"
