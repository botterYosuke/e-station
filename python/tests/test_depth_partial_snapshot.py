"""D-A: carry-forward for one-sided FD frames in stream_depth."""

from __future__ import annotations

import pytest

from engine.exchanges.tachibana import _apply_carry_forward


_BID_LEVELS: list[dict[str, str]] = [{"price": "3010.0", "qty": "100"}]
_ASK_LEVELS: list[dict[str, str]] = [{"price": "3012.0", "qty": "200"}]


class TestApplyCarryForward:
    """_apply_carry_forward 純粋関数のユニットテスト（D-A）。"""

    def test_both_sides_normal(self):
        """両側ありのとき入力がそのまま返る。"""
        bids, asks = _apply_carry_forward(
            _BID_LEVELS, _ASK_LEVELS, [], []
        )
        assert bids == _BID_LEVELS
        assert asks == _ASK_LEVELS

    def test_asks_empty_carries_forward_previous(self):
        """asks=[] が来たとき直前の asks が使われる。"""
        bids, asks = _apply_carry_forward(
            _BID_LEVELS, [], [], _ASK_LEVELS
        )
        assert asks == _ASK_LEVELS, "ask should be carried forward"
        assert bids == _BID_LEVELS

    def test_bids_empty_carries_forward_previous(self):
        """bids=[] が来たとき直前の bids が使われる。"""
        bids, asks = _apply_carry_forward(
            [], _ASK_LEVELS, _BID_LEVELS, []
        )
        assert bids == _BID_LEVELS, "bid should be carried forward"
        assert asks == _ASK_LEVELS

    def test_first_frame_asks_empty_stays_empty(self):
        """初回フレームで asks が空なら補完するものがなく空のまま。"""
        bids, asks = _apply_carry_forward(
            _BID_LEVELS, [], [], []
        )
        assert asks == [], "no previous asks to carry forward"
        assert bids == _BID_LEVELS

    def test_first_frame_bids_empty_stays_empty(self):
        """初回フレームで bids が空なら補完するものがなく空のまま。"""
        bids, asks = _apply_carry_forward(
            [], _ASK_LEVELS, [], []
        )
        assert bids == [], "no previous bids to carry forward"
        assert asks == _ASK_LEVELS

    def test_both_empty_returns_unchanged(self):
        """両方空のとき入力値がそのまま返る（空リストのまま）。"""
        bids, asks = _apply_carry_forward([], [], _BID_LEVELS, _ASK_LEVELS)
        assert bids == []
        assert asks == []

    def test_carry_forward_uses_copy_not_reference(self):
        """キャリーフォワードが参照共有でなくコピーであること（M-1）。"""
        last_asks: list[dict[str, str]] = [{"price": "3012.0", "qty": "200"}]
        _bids, asks = _apply_carry_forward(
            _BID_LEVELS, [], [], last_asks
        )
        # 返ってきたリストを変更しても last_asks に影響しない
        asks.append({"price": "9999.0", "qty": "1"})
        assert len(last_asks) == 1, "carry-forward must return a copy, not the original list"

    def test_cache_updates_on_full_snapshot(self):
        """両側あり → ask 欠落 → ask 更新 → ask 欠落 の順で最新キャッシュが使われる。"""
        ask_v1: list[dict[str, str]] = [{"price": "3012.0", "qty": "200"}]
        ask_v2: list[dict[str, str]] = [{"price": "3015.0", "qty": "500"}]

        # フレーム1: 両側正常
        bids1, asks1 = _apply_carry_forward(_BID_LEVELS, ask_v1, [], [])
        last_bids = list(bids1) if bids1 else []
        last_asks = list(asks1) if asks1 else []

        # フレーム2: ask 欠落 → ask_v1 でキャリーフォワード
        bids2, asks2 = _apply_carry_forward(_BID_LEVELS, [], last_bids, last_asks)
        if bids2:
            last_bids = list(bids2)
        if asks2:
            last_asks = list(asks2)
        assert asks2 == ask_v1

        # フレーム3: ask_v2 で正常
        bids3, asks3 = _apply_carry_forward(_BID_LEVELS, ask_v2, last_bids, last_asks)
        if bids3:
            last_bids = list(bids3)
        if asks3:
            last_asks = list(asks3)

        # フレーム4: ask 欠落 → ask_v2 でキャリーフォワード
        _bids4, asks4 = _apply_carry_forward(_BID_LEVELS, [], last_bids, last_asks)
        assert asks4 == ask_v2
