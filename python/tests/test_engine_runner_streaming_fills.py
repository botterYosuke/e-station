"""N1.13 Step A: streaming replay での fill → ExecutionMarker / ReplayBuyingPower 配線テスト。

FillTestStrategy（bar 1 BUY / bar 2 SELL）を使い、
- 1 OrderFilled につき ExecutionMarker が 1 件 emit されること
- fill ごとに ReplayBuyingPower が emit され残高が変動すること
- IPC wire schema（pydantic モデル）に準拠すること

fixture データ: python/tests/fixtures/equities_bars_daily_202401.csv.gz
  2024-01-04: close=3815, vol=21400
  2024-01-05: close=3825, vol=15000
→ bar 1 BUY 100株 @ 3815 → cash = 1,000,000 - 381,500 = 618,500
→ bar 2 SELL 100株 @ 3825 → cash = 618,500 + 382,500 = 1,001,000
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from engine.nautilus.engine_runner import NautilusRunner

FIXTURES = Path(__file__).parent / "fixtures"
_FILL_STRATEGY_FILE = str(FIXTURES / "fill_strategy.py")

_COMMON_KWARGS = dict(
    strategy_id="fill-test",
    instrument_id="1301.TSE",
    start_date="2024-01-04",
    end_date="2024-01-05",
    granularity="Daily",
    initial_cash=1_000_000,
    multiplier=10_000_000,
    base_dir=FIXTURES,
    strategy_file=_FILL_STRATEGY_FILE,
)


def _run_and_collect() -> list[dict]:
    events: list[dict] = []
    runner = NautilusRunner()
    runner.start_backtest_replay_streaming(
        **_COMMON_KWARGS,
        on_event=events.append,
    )
    return events


class TestStreamingFillsEmitExecutionMarker:
    """ExecutionMarker の emit 件数・フィールドを検証する。"""

    def test_emits_one_execution_marker_per_fill(self) -> None:
        """1 OrderFilled = 1 ExecutionMarker（1:1 契約）。

        FillTestStrategy は bar 1 BUY / bar 2 SELL で合計 2 fills。
        """
        events = _run_and_collect()
        markers = [e for e in events if e["event"] == "ExecutionMarker"]
        assert len(markers) == 2

    def test_execution_marker_side_is_uppercase(self) -> None:
        """side は "BUY" / "SELL" の大文字のみ（小文字は IPC スキーマ違反）。"""
        events = _run_and_collect()
        markers = [e for e in events if e["event"] == "ExecutionMarker"]
        assert len(markers) == 2
        assert markers[0]["side"] == "BUY"
        assert markers[1]["side"] == "SELL"

    def test_execution_marker_has_no_extra_fields(self) -> None:
        """ExecutionMarker に venue / quantity などの余分フィールドがない。"""
        events = _run_and_collect()
        markers = [e for e in events if e["event"] == "ExecutionMarker"]
        expected_keys = {"event", "strategy_id", "instrument_id", "side", "price", "ts_event_ms"}
        for m in markers:
            assert set(m.keys()) == expected_keys, f"unexpected keys: {set(m.keys()) - expected_keys}"

    def test_execution_marker_instrument_id(self) -> None:
        """instrument_id フィールドが正しい値。"""
        events = _run_and_collect()
        markers = [e for e in events if e["event"] == "ExecutionMarker"]
        for m in markers:
            assert m["instrument_id"] == "1301.TSE"

    def test_execution_marker_price_is_string(self) -> None:
        """price フィールドは文字列型（decimal str）。"""
        events = _run_and_collect()
        markers = [e for e in events if e["event"] == "ExecutionMarker"]
        for m in markers:
            assert isinstance(m["price"], str), "price must be a string"
            Decimal(m["price"])  # 変換できること（valid decimal str）

    def test_execution_marker_ts_event_ms_is_int(self) -> None:
        """ts_event_ms は int（ミリ秒）。"""
        events = _run_and_collect()
        markers = [e for e in events if e["event"] == "ExecutionMarker"]
        for m in markers:
            assert isinstance(m["ts_event_ms"], int)
            assert m["ts_event_ms"] > 0


class TestStreamingFillsEmitReplayBuyingPower:
    """ReplayBuyingPower の emit 件数・フィールド・残高変動を検証する。"""

    def test_emits_buying_power_per_fill(self) -> None:
        """fill ごとに ReplayBuyingPower が 1 件 emit される。"""
        events = _run_and_collect()
        bp_events = [e for e in events if e["event"] == "ReplayBuyingPower"]
        assert len(bp_events) == 2

    def test_buying_power_has_exact_schema_fields(self) -> None:
        """ReplayBuyingPower のフィールドが IPC スキーマに完全一致。

        extra="forbid" なので未知フィールドは pydantic が reject する。
        venue や cash_available など余分なフィールドがないことも確認する。
        """
        events = _run_and_collect()
        bp_events = [e for e in events if e["event"] == "ReplayBuyingPower"]
        expected_keys = {"event", "strategy_id", "cash", "buying_power", "equity", "ts_event_ms"}
        for e in bp_events:
            assert set(e.keys()) == expected_keys, (
                f"unexpected keys: {set(e.keys()) - expected_keys}"
            )

    def test_buying_power_cash_decreases_on_buy(self) -> None:
        """BUY fill で cash が initial_cash より減少する。"""
        events = _run_and_collect()
        bp_events = [e for e in events if e["event"] == "ReplayBuyingPower"]
        # 1 件目は BUY（bar 1）
        assert Decimal(bp_events[0]["cash"]) < Decimal("1000000")

    def test_buying_power_cash_increases_on_sell(self) -> None:
        """SELL fill で cash が BUY 後より増加する。"""
        events = _run_and_collect()
        bp_events = [e for e in events if e["event"] == "ReplayBuyingPower"]
        assert len(bp_events) == 2
        cash_after_buy = Decimal(bp_events[0]["cash"])
        cash_after_sell = Decimal(bp_events[1]["cash"])
        assert cash_after_sell > cash_after_buy

    def test_buying_power_strategy_id_matches(self) -> None:
        """strategy_id が呼び出し時の値と一致する。"""
        events = _run_and_collect()
        bp_events = [e for e in events if e["event"] == "ReplayBuyingPower"]
        for e in bp_events:
            assert e["strategy_id"] == "fill-test"

    def test_buying_power_ts_event_ms_is_from_order_filled(self) -> None:
        """ts_event_ms は time.time() でなく OrderFilled の ts_event 由来。

        決定論性テスト: 同入力でも time.time() は毎回変わるが
        OrderFilled.ts_event はデータ依存の固定値。
        2 回連続 run で同じ ts_event_ms が出ることを確認する。
        """
        events1: list[dict] = []
        NautilusRunner().start_backtest_replay_streaming(
            **_COMMON_KWARGS, on_event=events1.append
        )
        events2: list[dict] = []
        NautilusRunner().start_backtest_replay_streaming(
            **_COMMON_KWARGS, on_event=events2.append
        )
        bp1 = [e for e in events1 if e["event"] == "ReplayBuyingPower"]
        bp2 = [e for e in events2 if e["event"] == "ReplayBuyingPower"]
        assert len(bp1) == len(bp2)
        for a, b in zip(bp1, bp2):
            assert a["ts_event_ms"] == b["ts_event_ms"], (
                "ts_event_ms must be deterministic (from OrderFilled.ts_event, not time.time())"
            )

    def test_execution_marker_and_buying_power_share_ts_event_ms(self) -> None:
        """同一 fill の ExecutionMarker と ReplayBuyingPower が同じ ts_event_ms を持つ。

        両イベントとも OrderFilled.ts_event 由来であることを直接確認する。
        """
        events = _run_and_collect()
        markers = [e for e in events if e["event"] == "ExecutionMarker"]
        bp_events = [e for e in events if e["event"] == "ReplayBuyingPower"]
        assert len(markers) == len(bp_events) == 2
        for marker, bp in zip(markers, bp_events):
            assert marker["ts_event_ms"] == bp["ts_event_ms"], (
                "ExecutionMarker and ReplayBuyingPower from same fill must share ts_event_ms"
            )


class TestMsgbusTopic:
    """msgbus topic 文字列の形式が nautilus の publish topic と一致することを確認する。"""

    def test_fill_topic_format(self) -> None:
        """topic 文字列 'events.fills.<instrument_id>' の形式を pin する。

        nautilus execution/engine.pyx の _get_fill_events_topic() は
        f'events.fills.{instrument_id}' で topic を生成する（InstrumentId オブジェクトを文字列展開）。

        engine_runner.py は `_fill_topic = f"events.fills.{instrument_id}"` で構築する
        （instrument_id は str 引数）。この 2 つが同じ結果を返すことを確認する。

        Note: engine_runner.py の `_fill_topic` 計算式の正確性は
        `TestStreamingFillsEmitExecutionMarker::test_emits_one_execution_marker_per_fill`
        が integration テストとして保護している（topic が間違えば fill が 0 件になる）。
        このテストは InstrumentId.__str__ の形式変更を早期検知するための静的 pin。
        """
        from nautilus_trader.model.identifiers import InstrumentId

        instrument_id_str = "1301.TSE"
        iid = InstrumentId.from_str(instrument_id_str)

        # nautilus が publish する topic（InstrumentId オブジェクト展開）
        topic_from_iid = f"events.fills.{iid}"
        # engine_runner.py が subscribe する topic（str 引数直接展開）
        topic_from_str = f"events.fills.{instrument_id_str}"

        # 両者が一致し、かつ期待値と一致する
        assert topic_from_str == topic_from_iid == "events.fills.1301.TSE", (
            "nautilus fill topic format or InstrumentId.__str__ changed "
            "— update engine_runner.py subscribe topic accordingly"
        )


class TestStreamingFillsPassPydanticSchema:
    """emit されたイベントが pydantic IPC スキーマを通過することを検証する。"""

    def test_emitted_events_pass_pydantic_schema(self) -> None:
        """全 ExecutionMarker / ReplayBuyingPower を pydantic で検証する。"""
        from engine.schemas import ExecutionMarker, ReplayBuyingPower

        events = _run_and_collect()
        for e in events:
            if e["event"] == "ExecutionMarker":
                ExecutionMarker.model_validate(e)
            elif e["event"] == "ReplayBuyingPower":
                ReplayBuyingPower.model_validate(e)
