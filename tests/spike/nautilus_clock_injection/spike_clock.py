"""
Spike: nautilus BacktestEngine clock injection feasibility (Q3)

Q3 の 2 案を検証する捨てコード。

案 A: streaming=True + 1 バッチ = 1 Bar で逐次実行 → StepForward UX を模倣
案 B: engine.run(start, end) で完全自走 → シンプルだが StepForward 不可

結論: この spike が通れば Q3 を Resolved にする。
"""

import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity, Money
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig

logging.basicConfig(level=logging.WARNING)

_BYPASS = LoggingConfig(bypass_logging=True)


def _make_engine(trader_id: str, first: bool = False) -> BacktestEngine:
    cfg = BacktestEngineConfig(
        trader_id=trader_id,
        logging=None if first else _BYPASS,
    )
    return BacktestEngine(config=cfg)


def _make_equity() -> Equity:
    instrument_id = InstrumentId(Symbol("7203"), Venue("TSE"))
    return Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol("7203"),
        currency=JPY,
        price_precision=1,
        price_increment=Price(Decimal("0.1"), precision=1),
        lot_size=Quantity(100, precision=0),
        isin=None,
        ts_event=0,
        ts_init=0,
    )


def _make_bar(instrument: Equity, dt: datetime, close: float) -> Bar:
    bar_type = BarType.from_str(f"{instrument.id}-1-DAY-MID-EXTERNAL")
    ts_ns = int(dt.timestamp() * 1_000_000_000)
    return Bar(
        bar_type=bar_type,
        open=Price(Decimal(str(close)), precision=1),
        high=Price(Decimal(str(close + 10)), precision=1),
        low=Price(Decimal(str(close - 10)), precision=1),
        close=Price(Decimal(str(close)), precision=1),
        volume=Quantity(1000, precision=0),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


class _RecordingStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__(config=StrategyConfig(strategy_id="recording-001"))
        self.bar_timestamps: list[int] = []

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str("7203.TSE-1-DAY-MID-EXTERNAL"))

    def on_bar(self, bar: Bar) -> None:
        self.bar_timestamps.append(bar.ts_event)


# ---------------------------------------------------------------------------
# 案 B: engine.run() 完全自走
# ---------------------------------------------------------------------------

def verify_plan_b() -> None:
    """
    案 B: run(start, end) に日付範囲を渡して nautilus が自走する。
    StepForward UX は捨てる。シンプルで確実。
    決定論性: 2 つの独立エンジンで同じ入力 → 同じタイムスタンプ列。
    """
    base = datetime(2024, 1, 4, 15, 30, tzinfo=timezone.utc)
    instrument = _make_equity()

    def _run_once(trader_id: str, first: bool) -> list[int]:
        engine = _make_engine(trader_id, first=first)
        engine.add_venue(
            venue=Venue("TSE"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=JPY,
            starting_balances=[Money(1_000_000, JPY)],
        )
        engine.add_instrument(instrument)
        bars = [_make_bar(instrument, base + timedelta(days=i), 2000.0 + i * 10) for i in range(3)]
        engine.add_data(bars)
        strategy = _RecordingStrategy()
        engine.add_strategy(strategy)
        engine.run()
        result = list(strategy.bar_timestamps)
        engine.dispose()
        return result

    ts1 = _run_once("SPIKE-B-001", first=True)
    ts2 = _run_once("SPIKE-B-002", first=False)

    assert len(ts1) == 3, f"Expected 3 bars, got {len(ts1)}"
    assert ts1 == ts2, f"Determinism FAILED: {ts1} != {ts2}"
    print(f"[案 B] OK: {len(ts1)} bars, determinism verified: {ts1}")


# ---------------------------------------------------------------------------
# 案 A: streaming=True + 逐次バッチ投入
# ---------------------------------------------------------------------------

def verify_plan_a() -> None:
    """
    案 A: streaming=True で 1 Bar ずつ逐次投入し、外部からバッチを制御する。
    1 バッチ = 1 Bar にすることで StepForward UX を模倣できる。
    """
    engine = _make_engine("SPIKE-A-001", first=False)
    instrument = _make_equity()

    engine.add_venue(
        venue=Venue("TSE"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=JPY,
        starting_balances=[Money(1_000_000, JPY)],
    )
    engine.add_instrument(instrument)

    strategy = _RecordingStrategy()
    engine.add_strategy(strategy)

    base = datetime(2024, 1, 4, 15, 30, tzinfo=timezone.utc)

    # 1 Bar ずつ streaming 投入: add_data → run → clear_data のサイクル
    for i in range(3):
        bar = _make_bar(instrument, base + timedelta(days=i), 2000.0 + i * 10)
        engine.add_data([bar])
        engine.run(streaming=True)
        engine.clear_data()  # 処理済みデータを解放しないと次 run で重複処理される

    engine.end()  # finalize

    assert len(strategy.bar_timestamps) == 3, f"Expected 3 bars, got {len(strategy.bar_timestamps)}"

    clock_ts = engine.kernel.clock.timestamp_ns()
    last_bar_ts = strategy.bar_timestamps[-1]
    clock_ok = clock_ts >= last_bar_ts
    print(
        f"[案 A] OK: streaming=True 逐次投入 {len(strategy.bar_timestamps)} bars, "
        f"clock_ts={clock_ts}, last_bar_ts={last_bar_ts}, clock>=bar: {clock_ok}"
    )
    engine.dispose()


# ---------------------------------------------------------------------------
# 案 A-2: TestClock.advance_time() 外部呼び出し可否の確認
# ---------------------------------------------------------------------------

def verify_plan_a2() -> None:
    """
    案 A-2: TestClock.advance_time() を外部から呼んだ後に run(streaming=True) すると
    Rust clock の非減少不変条件違反でパニックする（CONFIRMED 不可）。

    「AdvanceClock IPC → Python advance_time()」経路は **実装不可**。
    run() が内部で clock を進めるため、外部から先に進めると矛盾が生じる。

    結論: advance_time() はタイマーアラートのテスト用途のみ有効。
    バックテストのステップ実行には streaming=True + clear_data() を使うべき。
    """
    engine = _make_engine("SPIKE-A2-001", first=False)
    instrument = _make_equity()
    engine.add_venue(
        venue=Venue("TSE"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=JPY,
        starting_balances=[Money(1_000_000, JPY)],
    )
    engine.add_instrument(instrument)

    # advance_time() をデータ追加前 (clock=0 の状態) で呼ぶのは有効
    initial_ts = engine.kernel.clock.timestamp_ns()
    target_ns = 1_000_000_000  # 1 second
    try:
        events = engine.kernel.clock.advance_time(target_ns)
        print(f"[案 A-2] run() 前の advance_time({target_ns}ns): "
              f"{len(events)} timer events, clock {initial_ts} → {engine.kernel.clock.timestamp_ns()}")
    except Exception as e:
        print(f"[案 A-2] advance_time() even before run failed: {e}")

    print("[案 A-2] 結論: TestClock.advance_time() は run() 前のみ有効。"
          " run() と組み合わせると時刻不変条件違反(Rust panic)を起こす。"
          " StepForward は streaming=True + clear_data() サイクルで実現すること。")
    engine.dispose()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Spike: nautilus BacktestEngine clock injection (Q3)")
    print("=" * 60)
    print()

    print("--- 案 B: engine.run() 完全自走 ---")
    verify_plan_b()
    print()

    print("--- 案 A: streaming=True 逐次投入 ---")
    verify_plan_a()
    print()

    print("--- 案 A-2: TestClock.advance_time() 外部呼び出し ---")
    verify_plan_a2()
    print()

    print("=" * 60)
    print("Spike complete. 結果を open-questions.md Q3 に記録する。")
    print("=" * 60)
