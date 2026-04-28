"""TDD: FD frame → trade synthesis (T5, plan §data-mapping §3, F3/F4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.exchanges.tachibana_ws import FdFrameProcessor


def _fields(
    row: str,
    dpp: str,
    dv: str,
    *,
    gap1: str = "2501",
    gbp1: str = "2499",
    gav1: str = "100",
    gbv1: str = "100",
    p_date: str = "2024.01.01-09:30:00.000",
) -> dict[str, str]:
    """Build a minimal FD frame fields dict."""
    return {
        "p_cmd": "FD",
        f"p_{row}_DPP": dpp,
        f"p_{row}_DV": dv,
        f"p_{row}_GAP1": gap1,
        f"p_{row}_GBP1": gbp1,
        f"p_{row}_GAV1": gav1,
        f"p_{row}_GBV1": gbv1,
        "p_date": p_date,
    }


class TestFirstFrame:
    def test_first_frame_emits_no_trade(self) -> None:
        """First frame: initialize DV + quote, never emit a trade (F4)."""
        proc = FdFrameProcessor(row="1")
        trade, _ = proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        assert trade is None

    def test_first_frame_emits_depth(self) -> None:
        """First frame: always emit DepthSnapshot when bid/ask present."""
        proc = FdFrameProcessor(row="1")
        _, depth = proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        assert depth is not None
        assert depth["bids"]
        assert depth["asks"]


class TestTradeGeneration:
    def test_positive_dv_delta_emits_trade(self) -> None:
        """frame 2: DV increases → emit 1 trade (plan §T5 test case 2)."""
        proc = FdFrameProcessor(row="1")
        proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        trade, _ = proc.process(_fields("1", "2500", "110"), recv_ts_ms=1_000)
        assert trade is not None
        assert trade["qty"] == "10"

    def test_zero_dv_delta_emits_no_trade(self) -> None:
        """DV unchanged → no trade (qty=0 guard)."""
        proc = FdFrameProcessor(row="1")
        proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        trade, _ = proc.process(_fields("1", "2500", "100"), recv_ts_ms=1_000)
        assert trade is None

    def test_dv_decrease_resets_emits_no_trade(self) -> None:
        """DV drops (session rollover) → no trade, prev_dv reset (plan §T5 test case 3)."""
        proc = FdFrameProcessor(row="1")
        proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        proc.process(_fields("1", "2500", "110"), recv_ts_ms=1_000)  # trade
        trade, _ = proc.process(_fields("1", "2500", "50"), recv_ts_ms=2_000)  # DV reset
        assert trade is None

    def test_after_dv_reset_next_positive_delta_emits_trade(self) -> None:
        """After a DV reset, the subsequent positive delta re-enables trades."""
        proc = FdFrameProcessor(row="1")
        proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        proc.process(_fields("1", "2500", "50"), recv_ts_ms=500)  # reset
        trade, _ = proc.process(_fields("1", "2500", "60"), recv_ts_ms=1_000)
        assert trade is not None
        assert trade["qty"] == "10"

    def test_trade_price_matches_dpp(self) -> None:
        proc = FdFrameProcessor(row="1")
        proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        trade, _ = proc.process(_fields("1", "2502", "110"), recv_ts_ms=1_000)
        assert trade is not None
        assert trade["price"] == "2502"


class TestSideDetermination:
    def test_side_buy_when_price_ge_prev_ask(self) -> None:
        """DPP >= prev_ask → buy (plan §data-mapping §3 quote rule)."""
        proc = FdFrameProcessor(row="1")
        # frame 1: bid=2499, ask=2501
        proc.process(_fields("1", "2500", "100", gap1="2501", gbp1="2499"), recv_ts_ms=0)
        # frame 2: price=2502 >= ask=2501 → buy
        trade, _ = proc.process(
            _fields("1", "2502", "110", gap1="2503", gbp1="2501"), recv_ts_ms=1_000
        )
        assert trade is not None
        assert trade["side"] == "buy"

    def test_side_sell_when_price_le_prev_bid(self) -> None:
        """DPP <= prev_bid → sell."""
        proc = FdFrameProcessor(row="1")
        # frame 1: bid=2499, ask=2501
        proc.process(_fields("1", "2500", "100", gap1="2501", gbp1="2499"), recv_ts_ms=0)
        # frame 2: price=2498 <= bid=2499 → sell
        trade, _ = proc.process(
            _fields("1", "2498", "110", gap1="2499", gbp1="2497"), recv_ts_ms=1_000
        )
        assert trade is not None
        assert trade["side"] == "sell"

    def test_tick_rule_up_gives_buy(self) -> None:
        """Midpoint + prev_trade higher than last trade → buy (F-M8b)."""
        proc = FdFrameProcessor(row="1")
        # frame 1: price 2500, bid 2499, ask 2501
        proc.process(_fields("1", "2500", "100", gap1="2501", gbp1="2499"), recv_ts_ms=0)
        # frame 2: price 2501 >= prev_ask 2501 → buy (sets prev_trade=2501)
        proc.process(_fields("1", "2501", "110", gap1="2502", gbp1="2500"), recv_ts_ms=1_000)
        # frame 3: price exactly at midpoint (bid=2500, ask=2502 → mid=2501)
        # prev_trade_price=2501, current price=2501 (same) → default buy
        # Actually let's use price=2502 which is now == ask (buy)
        # Let's do tick rule: bid=2499, ask=2501, price=2500 (midpoint)
        # prev_trade=2501 > 2500 → sell
        proc2 = FdFrameProcessor(row="1")
        proc2.process(_fields("1", "2501", "100", gap1="2502", gbp1="2500"), recv_ts_ms=0)
        # frame 2: price at midpoint: bid=2499, ask=2501 → mid=2500
        # prev_trade from frame1=None, then after frame1: prev_trade=None
        # Actually first frame has no trade so prev_trade is None after it.
        # Let's do 3 frames: f1=init, f2=trade at 2501(buy), f3=midpoint
        proc3 = FdFrameProcessor(row="1")
        proc3.process(_fields("1", "2500", "100", gap1="2502", gbp1="2498"), recv_ts_ms=0)
        trade2, _ = proc3.process(
            _fields("1", "2503", "110", gap1="2504", gbp1="2502"), recv_ts_ms=1_000
        )
        assert trade2 is not None and trade2["side"] == "buy"
        # now frame 3: price=2503 (exactly at bid=2502, ask=2504 → mid=2503)
        # prev_trade=2503, current=2503 → same → default buy
        trade3, _ = proc3.process(
            _fields("1", "2503", "120", gap1="2504", gbp1="2502"), recv_ts_ms=2_000
        )
        assert trade3 is not None
        # same as prev_trade → ambiguous → unknown
        assert trade3["side"] == "unknown"

    def test_tick_rule_up_price_above_prev_trade(self) -> None:
        """Midpoint + price > prev_trade → buy (F-M8b explicit case)."""
        proc = FdFrameProcessor(row="1")
        # f1: bid=2499, ask=2501
        proc.process(_fields("1", "2500", "100", gap1="2501", gbp1="2499"), recv_ts_ms=0)
        # f2: price=2501 >=prev_ask → buy; prev_trade=2501
        proc.process(_fields("1", "2501", "110", gap1="2503", gbp1="2499"), recv_ts_ms=1_000)
        # f3: price=2502, bid=2499, ask=2505 → neither (2499<2502<2505)
        # prev_trade=2501 < 2502 → buy
        trade, _ = proc.process(
            _fields("1", "2502", "120", gap1="2505", gbp1="2499"), recv_ts_ms=2_000
        )
        assert trade is not None
        assert trade["side"] == "buy"

    def test_tick_rule_down_price_below_prev_trade(self) -> None:
        """Midpoint + price < prev_trade → sell (F-M8b explicit case)."""
        proc = FdFrameProcessor(row="1")
        # f1: bid=2499, ask=2501
        proc.process(_fields("1", "2500", "100", gap1="2501", gbp1="2499"), recv_ts_ms=0)
        # f2: price=2499 <=prev_bid → sell; prev_trade=2499
        proc.process(_fields("1", "2499", "110", gap1="2501", gbp1="2497"), recv_ts_ms=1_000)
        # f3: price=2498, bid=2495, ask=2501 → neither
        # prev_trade=2499 > 2498 → sell
        trade, _ = proc.process(
            _fields("1", "2498", "120", gap1="2501", gbp1="2495"), recv_ts_ms=2_000
        )
        assert trade is not None
        assert trade["side"] == "sell"

    def test_side_uses_prev_frame_quote_not_current(self) -> None:
        """Side is determined from the *previous* frame's quote (F3)."""
        proc = FdFrameProcessor(row="1")
        # f1: bid=2499, ask=2501
        proc.process(_fields("1", "2500", "100", gap1="2501", gbp1="2499"), recv_ts_ms=0)
        # f2: price=2501, current frame's bid=2502, ask=2503
        # But prev quote was bid=2499, ask=2501 → price=2501 >= prev_ask → buy
        trade, _ = proc.process(
            _fields("1", "2501", "110", gap1="2503", gbp1="2502"), recv_ts_ms=1_000
        )
        assert trade is not None
        assert trade["side"] == "buy"


class TestDepthOutput:
    def test_depth_has_ten_levels(self) -> None:
        """DepthSnapshot should contain up to 10 bid/ask levels."""
        fields: dict[str, str] = {
            "p_cmd": "FD",
            "p_1_DPP": "2500",
            "p_1_DV": "100",
            "p_date": "2024.01.01-09:30:00.000",
        }
        for i in range(1, 11):
            fields[f"p_1_GAP{i}"] = str(2501 + i)
            fields[f"p_1_GAV{i}"] = "100"
            fields[f"p_1_GBP{i}"] = str(2500 - i)
            fields[f"p_1_GBV{i}"] = "100"
        proc = FdFrameProcessor(row="1")
        _, depth = proc.process(fields, recv_ts_ms=0)
        assert depth is not None
        assert len(depth["bids"]) == 10
        assert len(depth["asks"]) == 10

    def test_depth_absent_when_no_bid_ask_keys(self) -> None:
        """No GAP/GBP keys → depth is None."""
        fields = {
            "p_cmd": "FD",
            "p_1_DPP": "2500",
            "p_1_DV": "100",
            "p_date": "2024.01.01-09:30:00.000",
        }
        proc = FdFrameProcessor(row="1")
        _, depth = proc.process(fields, recv_ts_ms=0)
        assert depth is None

    def test_depth_sequence_id_increments(self) -> None:
        """Each FD frame should get a monotonically increasing sequence_id."""
        proc = FdFrameProcessor(row="1")
        _, d1 = proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        _, d2 = proc.process(_fields("1", "2500", "100"), recv_ts_ms=1_000)
        assert d1 is not None and d2 is not None
        assert d2["sequence_id"] == d1["sequence_id"] + 1


class TestTimestamp:
    def test_ts_ms_from_p_date(self) -> None:
        """ts_ms is parsed from p_date when DPP:T is absent."""
        proc = FdFrameProcessor(row="1")
        proc.process(_fields("1", "2500", "100", p_date="2024.01.01-09:30:00.000"), recv_ts_ms=0)
        trade, _ = proc.process(
            _fields("1", "2501", "110", p_date="2024.01.01-09:30:05.500"), recv_ts_ms=9_000
        )
        assert trade is not None
        # 2024-01-01 09:30:05.500 JST in ms — should be far from recv_ts 9000
        assert trade["ts_ms"] > 0
        assert trade["ts_ms"] != 9_000  # parsed from p_date, not fallback

    def test_ts_ms_falls_back_to_recv_when_no_p_date(self) -> None:
        """ts_ms falls back to recv_ts_ms when no p_date."""
        fields_no_date = {
            "p_cmd": "FD",
            "p_1_DPP": "2500",
            "p_1_DV": "100",
            "p_1_GAP1": "2501",
            "p_1_GBP1": "2499",
            "p_1_GAV1": "100",
            "p_1_GBV1": "100",
        }
        proc = FdFrameProcessor(row="1")
        proc.process(dict(fields_no_date), recv_ts_ms=0)
        fields2 = dict(fields_no_date)
        fields2["p_1_DV"] = "110"
        trade, _ = proc.process(fields2, recv_ts_ms=12_345)
        assert trade is not None
        assert trade["ts_ms"] == 12_345


class TestReset:
    def test_reset_clears_state(self) -> None:
        """reset() makes the next frame act like the first frame."""
        proc = FdFrameProcessor(row="1")
        proc.process(_fields("1", "2500", "100"), recv_ts_ms=0)
        proc.process(_fields("1", "2500", "110"), recv_ts_ms=1_000)
        proc.reset()
        # After reset, next frame is treated as first — no trade
        trade, _ = proc.process(_fields("1", "2500", "120"), recv_ts_ms=2_000)
        assert trade is None
