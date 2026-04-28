"""J-Quants 風小フィクスチャ生成スクリプト (N1.2)

このスクリプトは python/tests/fixtures/equities_*.csv.gz を生成する。
実 J-Quants ファイル（S:/j-quants/）からの一部抽出ではなく、
テストに必要な最小行数を手書きで埋め込む。

実行方法:
    uv run python python/tests/fixtures/_build_jquants_fixtures.py

ファイルサイズはいずれも 1KB 未満。テストは各行を直接 assert するため
内容変更時はテストも合わせて更新すること。
"""

from __future__ import annotations

import gzip
from pathlib import Path

HERE = Path(__file__).parent

TRADES_202401 = (
    "Date,Code,Time,SessionDistinction,Price,TradingVolume,TransactionId\n"
    # 1301 (= 1301.TSE)
    "2024-01-04,13010,09:00:00.165806,01,3775,1100,000000000010\n"
    "2024-01-04,13010,09:00:12.384777,01,3775,100,000000000019\n"
    "2024-01-04,13010,12:30:00.000000,02,3780,200,000000000020\n"
    "2024-01-05,13010,09:00:00.500000,01,3790,300,000000000030\n"
    # 1305 (= 1305.TSE)
    "2024-01-04,13050,09:00:00.111111,01,2490,400,000000000050\n"
    "2024-01-05,13050,09:00:01.222222,01,2495,500,000000000051\n"
)

TRADES_202402 = (
    "Date,Code,Time,SessionDistinction,Price,TradingVolume,TransactionId\n"
    "2024-02-01,13010,09:00:00.000001,01,3800,150,000000000060\n"
    "2024-02-01,13010,09:00:00.000002,01,3801,250,000000000061\n"
    "2024-02-02,13050,09:00:00.000003,01,2500,100,000000000062\n"
)

MINUTE_202401 = (
    "Date,Time,Code,O,H,L,C,Vo,Va\n"
    "2024-01-04,09:00,13010,3775,3775,3760,3760,2400,9056500\n"
    "2024-01-04,09:01,13010,3765,3765,3755,3755,700,2632000\n"
    "2024-01-05,09:00,13010,3770,3775,3768,3772,1000,3770000\n"
    "2024-01-04,09:00,13050,2490,2495,2488,2492,800,1992000\n"
)

DAILY_202401 = (
    "Date,Code,O,H,L,C,UL,LL,Vo,Va,AdjFactor\n"
    "2024-01-04,13010,3775.0,3825.0,3755.0,3815.0,0,0,21400.0,81210000.0,1.0\n"
    "2024-01-05,13010,3815.0,3830.0,3800.0,3825.0,0,0,15000.0,57000000.0,1.0\n"
    "2024-01-04,13050,2490.5,2517.0,2469.0,2515.0,0,0,333530.0,829587495.0,1.0\n"
)


def _write(name: str, content: str) -> None:
    path = HERE / name
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        f.write(content)
    print(f"wrote {path} ({path.stat().st_size} bytes)")


def main() -> None:
    _write("equities_trades_202401.csv.gz", TRADES_202401)
    _write("equities_trades_202402.csv.gz", TRADES_202402)
    _write("equities_bars_minute_202401.csv.gz", MINUTE_202401)
    _write("equities_bars_daily_202401.csv.gz", DAILY_202401)


if __name__ == "__main__":
    main()
