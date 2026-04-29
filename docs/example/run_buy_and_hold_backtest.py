"""実 J-Quants データで BuyAndHold 戦略を回す Python 単体バックテスト例。

前提:
    - `S:/j-quants/` に J-Quants 月次 CSV を保存済み
      (`equities_bars_daily_YYYYMM.csv.gz` / `equities_trades_YYYYMM.csv.gz`)
    - リポジトリルートで `uv sync` 済み

実行例:
    uv run python docs/example/run_buy_and_hold_backtest.py

切替パラメータ:
    INSTRUMENT_ID  J-Quants 5 桁 Code を末尾 0 を切って `{4 桁}.TSE` 形式に
                   写像した文字列 (data-mapping.md §1.1)
    GRANULARITY    "Daily" | "Minute" | "Trade"
    START_DATE     "YYYY-MM-DD"
    END_DATE       "YYYY-MM-DD"
    INITIAL_CASH   円建て初期資金

戦略:
    `engine.nautilus.strategies.buy_and_hold.BuyAndHoldStrategy`
    (最初の bar/tick で全資金を成行買いし保持するだけのリファレンス実装)

確認した実走結果 (2026-04-29):
    - 7203.TSE Daily 2024 通年: 245 bars / 5.03 秒 / final_equity=736,500
    - 7203.TSE Trade 2024-01-04: 20,382 ticks / 130.53 秒 / final_equity=739,500
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# python/ をモジュールパスに足してから engine.* を import する。
# (リポジトリ構造上 engine パッケージは python/engine/ にある)
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from engine.nautilus.engine_runner import NautilusRunner

INSTRUMENT_ID = "7203.TSE"
GRANULARITY = "Daily"
START_DATE = "2024-01-01"
END_DATE = "2024-12-31"
INITIAL_CASH = 1_000_000
BASE_DIR = Path("S:/j-quants")


def _on_event(evt: dict) -> None:
    rest = {k: v for k, v in evt.items() if k != "event"}
    print(f"[EVENT] {evt['event']} {rest}")


def main() -> int:
    runner = NautilusRunner()
    print(
        f"=== BuyAndHold backtest: {INSTRUMENT_ID} "
        f"{START_DATE}..{END_DATE} granularity={GRANULARITY} ==="
    )
    t0 = time.time()
    result = runner.start_backtest_replay(
        strategy_id="buy-and-hold",
        instrument_id=INSTRUMENT_ID,
        start_date=START_DATE,
        end_date=END_DATE,
        granularity=GRANULARITY,
        initial_cash=INITIAL_CASH,
        base_dir=BASE_DIR,
        on_event=_on_event,
    )
    elapsed = time.time() - t0
    pnl = result.final_equity - INITIAL_CASH
    pct = pnl / INITIAL_CASH * 100

    print()
    print("=== RESULT ===")
    print(f"  elapsed       : {elapsed:.2f} sec")
    print(f"  bars_loaded   : {result.bars_loaded}")
    print(f"  trades_loaded : {result.trades_loaded:,}")
    print(f"  initial_cash  : {INITIAL_CASH:,} JPY")
    print(f"  final_equity  : {result.final_equity:,} JPY")
    print(f"  PnL           : {pnl:+,} JPY ({pct:+.2f}%)")
    print(f"  fills         : {len(result.fill_timestamps)} 件 @ {result.fill_last_prices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
