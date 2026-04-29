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
import os
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
# Evaluated once at import time. Set JQUANTS_DIR *before* importing this module
# (e.g. as a subprocess env var). Changing os.environ after import has no effect.
_DEFAULT_BASE_DIR = Path(os.environ.get("JQUANTS_DIR", "S:/j-quants"))
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
        - 先頭 4 桁は数字のみ
        - 末尾はチェックデジット ``"0"`` 必須
        - それ以外は ``ValueError``
    """
    if len(code) != 5:
        raise ValueError(f"unexpected J-Quants code length: {code!r}")
    if not code[:-1].isdigit():
        raise ValueError(f"J-Quants code prefix is not numeric: {code!r}")
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
    """期間に重なる年月を ``"YYYYMM"`` 文字列で順次生成。

    ``end_date < start_date`` は明示的な ``ValueError``（誤診断防止）。
    """
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    if end < start:
        raise ValueError(
            f"end_date ({end_date}) must be >= start_date ({start_date})"
        )
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield f"{year:04d}{month:02d}"
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _open_monthly(path: Path) -> Iterator[list[str]]:
    """月次 gzip CSV をストリームオープンし、ヘッダ後の行を ``list[str]`` で返す。

    呼出側がファイル存在を検証する責務（dead check 削減）。空ファイル / ヘッダのみは
    空 iterator として返す。CSV パースエラーや gzip 破損は raise する（silent failure 禁止）。
    """
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # header
        except StopIteration:
            return
        for row in reader:
            if row:
                yield row


def _warn_short_row_once(state: dict, path: Path, expected: int, actual: int) -> None:
    """同一ファイル内で短行 warning を一度だけ出す。

    CSV 列数仕様変更を運用で検知するためのガード。``state["warned"]`` を立てて
    flood を防ぐ（MISSES.md silent-failure 観点）。
    """
    if state.get("warned"):
        return
    state["warned"] = True
    logger.warning(
        "J-Quants CSV %s contains row with %d columns (expected >= %d); skipping",
        path.name,
        actual,
        expected,
    )


# ---------------------------------------------------------------------------
# ファイル存在確認（高速プリフライト）
# ---------------------------------------------------------------------------

_GRANULARITY_PREFIX: dict[str, str] = {
    "Trade": "equities_trades_",
    "Minute": "equities_bars_minute_",
    "Daily": "equities_bars_daily_",
}


def check_data_exists(
    _instrument_id: str,
    start_date: str,
    end_date: str,
    granularity: str = "Trade",
    *,
    base_dir: Path | str = _DEFAULT_BASE_DIR,
) -> None:
    """月次 CSV ファイルの存在だけを確認する（行を読まない）。

    ファイルが 1 件でも見つかれば即 return。
    見つからなければ ``FileNotFoundError`` を raise する。
    """
    prefix = _GRANULARITY_PREFIX.get(granularity)
    if prefix is None:
        raise ValueError(f"unknown granularity: {granularity!r}")
    base_dir = Path(base_dir)
    for yyyymm in _iter_yyyymm(start_date, end_date):
        if (base_dir / f"{prefix}{yyyymm}.csv.gz").exists():
            return
    raise FileNotFoundError(
        f"no J-Quants {granularity} files found under {base_dir} "
        f"for period {start_date}..{end_date}"
    )


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
        warn_state: dict = {}
        for row in _open_monthly(path):
            # CSV: Date,Code,Time,SessionDistinction,Price,TradingVolume,TransactionId
            if len(row) < 7:
                _warn_short_row_once(warn_state, path, expected=7, actual=len(row))
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

    JST は UTC+9 固定（夏時間・閏秒なし）。Python ``timestamp()`` は POSIX 時刻なので
    閏秒は考慮されない（J-Quants 仕様上問題なし）。``%f`` 無しの秒精度のみの行も
    fallback で受け付ける（仕様揺らぎ耐性）。
    """
    try:
        parsed = dt.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        parsed = dt.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
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
        warn_state: dict = {}
        for row in _open_monthly(path):
            # CSV: Date,Time,Code,O,H,L,C,Vo,Va
            if len(row) < 9:
                _warn_short_row_once(warn_state, path, expected=9, actual=len(row))
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
    """``"2024-01-04"`` + ``"09:00"`` → JST 09:00:59.999999999 → UTC ns。

    JST 固定（夏時間なし）。close 時刻揃えは Q9 の決定。
    """
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
        warn_state: dict = {}
        for row in _open_monthly(path):
            # CSV: Date,Code,O,H,L,C,UL,LL,Vo,Va,AdjFactor (UL/LL/AdjFactor は N1 では無視)
            if len(row) < 10:
                _warn_short_row_once(warn_state, path, expected=10, actual=len(row))
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
    """``"2024-01-04"`` → JST 15:30:00 → UTC ns。

    JST 固定（夏時間なし）。N0 の klines_to_bars と同じ慣習。
    """
    y, m, d = (int(p) for p in date_s.split("-"))
    parsed = dt.datetime(y, m, d, _DAILY_CLOSE_HOUR, _DAILY_CLOSE_MINUTE, tzinfo=_JST)
    return int(parsed.timestamp()) * 1_000_000_000
