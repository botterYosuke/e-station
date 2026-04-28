#!/usr/bin/env python
"""nautilus_replay_baseline.py — N1.10 性能ベンチマークスクリプト。

実 J-Quants ファイル (S:\\j-quants\\) がある環境で手動実行する。
CI には含めない。

使い方:
    uv run python scripts/nautilus_replay_baseline.py
    uv run python scripts/nautilus_replay_baseline.py --instrument 9984.TSE --month 202402
    uv run python scripts/nautilus_replay_baseline.py --instrument 1301.TSE --month 202401 --granularity Trade
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.nautilus.engine_runner import NautilusRunner  # noqa: E402

SLA_SECONDS = 60.0  # spec.md §3.3
JQUANTS_DIR = Path("S:/j-quants")


def _month_to_date_range(month: str) -> tuple[str, str]:
    """'YYYYMM' を (start_date, end_date) に変換する。

    例: '202401' -> ('2024-01-01', '2024-01-31')
    """
    import calendar

    year = int(month[:4])
    mon = int(month[4:6])
    last_day = calendar.monthrange(year, mon)[1]
    start = f"{year:04d}-{mon:02d}-01"
    end = f"{year:04d}-{mon:02d}-{last_day:02d}"
    return start, end


def _check_data_file(instrument_id: str, month: str, granularity: str) -> Path | None:
    """該当の J-Quants ファイルが存在するか確認して返す。存在しない場合は None。"""
    if granularity == "Trade":
        fname = f"equities_trades_{month}.csv.gz"
    elif granularity == "Minute":
        # minute bars は日次ファイル — 月初のみ確認する
        date = f"{month[:4]}{month[4:6]}01"
        fname = f"equities_bars_minute_{date}.csv.gz"
    else:  # Daily
        fname = f"equities_bars_daily_{month}.csv.gz"

    path = JQUANTS_DIR / fname
    return path if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="N1.10 nautilus replay 性能ベンチマーク（実 J-Quants ファイル使用）"
    )
    parser.add_argument(
        "--instrument",
        default="1301.TSE",
        help="instrument_id (例: 1301.TSE, 9984.TSE)。デフォルト: 1301.TSE",
    )
    parser.add_argument(
        "--month",
        default="202401",
        help="対象月 YYYYMM。デフォルト: 202401",
    )
    parser.add_argument(
        "--granularity",
        choices=["Trade", "Minute", "Daily"],
        default="Trade",
        help="データ粒度。デフォルト: Trade",
    )
    parser.add_argument(
        "--initial-cash",
        type=int,
        default=1_000_000,
        help="初期資金（円）。デフォルト: 1,000,000",
    )
    args = parser.parse_args()

    print(f"[baseline] instrument={args.instrument} month={args.month} "
          f"granularity={args.granularity}")
    print(f"[baseline] J-Quants dir: {JQUANTS_DIR}")

    # データファイル存在確認
    data_file = _check_data_file(args.instrument, args.month, args.granularity)
    if data_file is None:
        print(f"[ERROR] J-Quants data file not found in {JQUANTS_DIR}")
        print(f"        granularity={args.granularity} month={args.month}")
        print("        実 J-Quants ファイルが利用できる環境で実行してください。")
        return 1

    print(f"[baseline] data file: {data_file.name}")
    start_date, end_date = _month_to_date_range(args.month)
    print(f"[baseline] date range: {start_date} .. {end_date}")
    print(f"[baseline] SLA: {SLA_SECONDS}s")
    print()

    # 計測
    events: list[dict] = []

    def on_event(evt: dict) -> None:
        events.append(evt)
        print(f"  [event] {evt['event']} ts={evt['ts_event_ms']}")

    runner = NautilusRunner()

    t0 = time.perf_counter()
    result = runner.start_backtest_replay(
        strategy_id="buy-and-hold",
        instrument_id=args.instrument,
        start_date=start_date,
        end_date=end_date,
        granularity=args.granularity,
        initial_cash=args.initial_cash,
        on_event=on_event,
    )
    elapsed = time.perf_counter() - t0

    print()
    print("=" * 60)
    print(f"[RESULT] instrument      : {args.instrument}")
    print(f"[RESULT] month           : {args.month}")
    print(f"[RESULT] granularity     : {args.granularity}")
    print(f"[RESULT] trades_loaded   : {result.trades_loaded}")
    print(f"[RESULT] bars_loaded     : {result.bars_loaded}")
    print(f"[RESULT] final_equity    : {result.final_equity}")
    print(f"[RESULT] wall_clock      : {elapsed:.3f}s")
    print(f"[RESULT] SLA             : {SLA_SECONDS}s")

    if elapsed < SLA_SECONDS:
        print(f"[RESULT] SLA             : PASS ({elapsed:.1f}s < {SLA_SECONDS}s)")
        return 0
    else:
        print(f"[RESULT] SLA             : FAIL ({elapsed:.1f}s >= {SLA_SECONDS}s)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
