"""J-Quants CSV ローダ → nautilus TradeTick / Bar (N1.2)

data-mapping.md §1.3 / §2 / §8 に従う。

入力ファイル（実態 confirmed 2026-04-28）:
    S:\\j-quants\\equities_trades_YYYYMM.csv.gz       (月次)
    S:\\j-quants\\equities_bars_minute_YYYYMM.csv.gz  (月次, data-mapping §8.1 の YYYYMMDD は誤記)
    S:\\j-quants\\equities_bars_daily_YYYYMM.csv.gz   (月次)

設計方針:
- pandas は使わない。gzip stream + csv.reader でメモリ効率を確保
- 期間に重なる月次ファイルだけを順次オープン
- 銘柄フィルタ・期間フィルタは行単位で適用
- price_precision は InstrumentCache から取得（cache miss なら 1 fallback）
- bar `ts_event` は bar close 時刻に揃える（Q9）
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import logging
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model.data import Bar, BarType, TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity

from engine.nautilus.instrument_cache import InstrumentCache

logger = logging.getLogger(__name__)

_JST = dt.timezone(dt.timedelta(hours=9))
_DEFAULT_BASE_DIR = Path("S:/j-quants")
_DEFAULT_PRICE_PRECISION = 1
_SIZE_PRECISION = 0
_DAILY_CLOSE_HOUR = 15
_DAILY_CLOSE_MINUTE = 30
# minute bar の close 時刻の sub-second 部分（59.999999999 秒）
_MINUTE_CLOSE_SUBSECOND_NS = 59 * 1_000_000_000 + 999_999_999


# ---------------------------------------------------------------------------
# InstrumentId 写像
# ---------------------------------------------------------------------------


def jquants_code_to_instrument_id(code: str) -> str:
    """J-Quants 5 桁 Code → ``"<4桁>.TSE"``。

    ルール:
        - 長さ 5 必須
        - 末尾はチェックデジット ``"0"`` 必須
        - それ以外は ``ValueError``
    """
    if len(code) != 5:
        raise ValueError(f"unexpected J-Quants code length: {code!r}")
    if code[-1] != "0":
        raise ValueError(f"J-Quants code does not end with 0: {code!r}")
    return f"{code[:-1]}.TSE"


def _instrument_id_to_jquants_code(instrument_id: str) -> str:
    """``"1301.TSE"`` → ``"13010"``."""
    symbol = instrument_id.split(".", 1)[0]
    return f"{symbol}0"


# ---------------------------------------------------------------------------
# 期間 → 月次ファイル名
# ---------------------------------------------------------------------------


def _iter_yyyymm(start_date: str, end_date: str) -> Iterator[str]:
    """期間に重なる年月を ``"YYYYMM"`` 文字列で順次生成。"""
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    if end < start:
        return
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield f"{year:04d}{month:02d}"
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _open_monthly(
    base_dir: Path, prefix: str, yyyymm: str
) -> Iterator[list[str]]:
    """月次 gzip CSV をストリームオープンし、ヘッダ後の行を ``list[str]`` で返す。

    ファイル不在は呼び出し側に空イテレータで通知（DEBUG ログのみ）。
    """
    path = base_dir / f"{prefix}_{yyyymm}.csv.gz"
    if not path.exists():
        logger.debug("J-Quants monthly file not found, skipping: %s", path)
        return
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # header
        except StopIteration:
            return
        for row in reader:
            if row:
                yield row


# ---------------------------------------------------------------------------
# TradeTick ローダ
# ---------------------------------------------------------------------------


def load_trades(
    instrument_id: str,
    start_date: str,
    end_date: str,
    *,
    base_dir: Path | str = _DEFAULT_BASE_DIR,
) -> Iterator[TradeTick]:
    """期間内 trades CSV を stream し、銘柄一致行を TradeTick として yield する。

    ts_event はマイクロ秒精度（JST → UTC ns）。aggressor_side は常に NO_AGGRESSOR。
    """
    base_dir = Path(base_dir)
    target_code = _instrument_id_to_jquants_code(instrument_id)
    nautilus_iid = InstrumentId.from_str(instrument_id)
    cache = InstrumentCache.shared()
    price_precision = cache.get_price_precision(instrument_id)

    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)

    any_file = False
    for yyyymm in _iter_yyyymm(start_date, end_date):
        path = base_dir / f"equities_trades_{yyyymm}.csv.gz"
        if not path.exists():
            logger.debug("J-Quants trades file not found, skipping: %s", path)
            continue
        any_file = True
        for row in _open_monthly(base_dir, "equities_trades", yyyymm):
            # CSV: Date,Code,Time,SessionDistinction,Price,TradingVolume,TransactionId
            if len(row) < 7:
                continue
            date_s, code, time_s, _session, price_s, vol_s, tx_id = row[:7]
            if code != target_code:
                continue
            row_date = dt.date.fromisoformat(date_s)
            if row_date < start or row_date > end:
                continue
            ts_ns = _trade_ts_to_ns(date_s, time_s)
            tick = TradeTick(
                instrument_id=nautilus_iid,
                price=Price(Decimal(price_s), precision=price_precision),
                size=Quantity(Decimal(vol_s), precision=_SIZE_PRECISION),
                aggressor_side=AggressorSide.NO_AGGRESSOR,
                trade_id=TradeId(f"R-{tx_id}"),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            yield tick

    if not any_file:
        raise FileNotFoundError(
            f"no J-Quants trades files found under {base_dir} "
            f"for period {start_date}..{end_date}"
        )


def _trade_ts_to_ns(date_s: str, time_s: str) -> int:
    """``"2024-01-04"`` + ``"09:00:00.165806"`` (JST naive) → UTC ns。

    マイクロ秒精度を保つため、``timestamp() * 1_000_000`` で μs 整数化してから ns に
    変換する（直接 ``* 1e9`` だと float 誤差で末尾の μs 桁が崩れることがある）。
    """
    parsed = dt.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S.%f")
    parsed = parsed.replace(tzinfo=_JST)
    us = int(parsed.timestamp() * 1_000_000)
    return us * 1000


# ---------------------------------------------------------------------------
# Bar ローダ (minute / daily)
# ---------------------------------------------------------------------------


def load_minute_bars(
    instrument_id: str,
    start_date: str,
    end_date: str,
    *,
    base_dir: Path | str = _DEFAULT_BASE_DIR,
) -> Iterator[Bar]:
    """期間内 minute bars を yield。``ts_event`` は close 時刻 (XX:XX:59.999999999 JST → UTC ns)。"""
    base_dir = Path(base_dir)
    target_code = _instrument_id_to_jquants_code(instrument_id)
    cache = InstrumentCache.shared()
    price_precision = cache.get_price_precision(instrument_id)
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")

    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)

    any_file = False
    for yyyymm in _iter_yyyymm(start_date, end_date):
        path = base_dir / f"equities_bars_minute_{yyyymm}.csv.gz"
        if not path.exists():
            logger.debug("J-Quants minute file not found, skipping: %s", path)
            continue
        any_file = True
        for row in _open_monthly(base_dir, "equities_bars_minute", yyyymm):
            # CSV: Date,Time,Code,O,H,L,C,Vo,Va
            if len(row) < 9:
                continue
            date_s, time_s, code, o, h, l, c, vo, _va = row[:9]
            if code != target_code:
                continue
            row_date = dt.date.fromisoformat(date_s)
            if row_date < start or row_date > end:
                continue
            ts_ns = _minute_close_ts_ns(date_s, time_s)
            bar = Bar(
                bar_type=bar_type,
                open=Price(Decimal(o), precision=price_precision),
                high=Price(Decimal(h), precision=price_precision),
                low=Price(Decimal(l), precision=price_precision),
                close=Price(Decimal(c), precision=price_precision),
                volume=Quantity(Decimal(vo), precision=_SIZE_PRECISION),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            yield bar

    if not any_file:
        raise FileNotFoundError(
            f"no J-Quants minute bar files found under {base_dir} "
            f"for period {start_date}..{end_date}"
        )


def _minute_close_ts_ns(date_s: str, time_s: str) -> int:
    """``"2024-01-04"`` + ``"09:00"`` → JST 09:00:59.999999999 → UTC ns."""
    hh, mm = time_s.split(":", 1)
    parsed = dt.datetime(
        *map(int, date_s.split("-")),
        int(hh),
        int(mm),
        0,
        tzinfo=_JST,
    )
    base_ns = int(parsed.timestamp()) * 1_000_000_000
    return base_ns + _MINUTE_CLOSE_SUBSECOND_NS


def load_daily_bars(
    instrument_id: str,
    start_date: str,
    end_date: str,
    *,
    base_dir: Path | str = _DEFAULT_BASE_DIR,
) -> Iterator[Bar]:
    """期間内 daily bars を yield。``ts_event`` は JST 15:30 → UTC ns。"""
    base_dir = Path(base_dir)
    target_code = _instrument_id_to_jquants_code(instrument_id)
    cache = InstrumentCache.shared()
    price_precision = cache.get_price_precision(instrument_id)
    bar_type = BarType.from_str(f"{instrument_id}-1-DAY-LAST-EXTERNAL")

    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)

    any_file = False
    for yyyymm in _iter_yyyymm(start_date, end_date):
        path = base_dir / f"equities_bars_daily_{yyyymm}.csv.gz"
        if not path.exists():
            logger.debug("J-Quants daily file not found, skipping: %s", path)
            continue
        any_file = True
        for row in _open_monthly(base_dir, "equities_bars_daily", yyyymm):
            # CSV: Date,Code,O,H,L,C,UL,LL,Vo,Va,AdjFactor (UL/LL/AdjFactor は N1 では無視)
            if len(row) < 10:
                continue
            date_s, code, o, h, l, c = row[0], row[1], row[2], row[3], row[4], row[5]
            vo = row[8]
            if code != target_code:
                continue
            row_date = dt.date.fromisoformat(date_s)
            if row_date < start or row_date > end:
                continue
            ts_ns = _daily_close_ts_ns(date_s)
            bar = Bar(
                bar_type=bar_type,
                open=Price(Decimal(o), precision=price_precision),
                high=Price(Decimal(h), precision=price_precision),
                low=Price(Decimal(l), precision=price_precision),
                close=Price(Decimal(c), precision=price_precision),
                volume=Quantity(Decimal(vo), precision=_SIZE_PRECISION),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            yield bar

    if not any_file:
        raise FileNotFoundError(
            f"no J-Quants daily bar files found under {base_dir} "
            f"for period {start_date}..{end_date}"
        )


def _daily_close_ts_ns(date_s: str) -> int:
    y, m, d = (int(p) for p in date_s.split("-"))
    parsed = dt.datetime(y, m, d, _DAILY_CLOSE_HOUR, _DAILY_CLOSE_MINUTE, tzinfo=_JST)
    return int(parsed.timestamp()) * 1_000_000_000
