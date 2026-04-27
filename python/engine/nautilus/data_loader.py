"""nautilus DataLoader: 既存 Klines IPC データ → nautilus Bar 変換 (N0.3)

N0 では klines 配列（FetchKlines レスポンスの klines フィールド）を
nautilus Bar に変換する。精度は N0 仮置き: price_precision=1, size_precision=0。
N1 で Instrument 経由に切り替える（data-mapping.md §1）。

JST 変換規約: 立花日足の終値時刻 = 15:30 JST = 06:30 UTC
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity

_JST = timezone(timedelta(hours=9))
_CLOSE_HOUR_JST = 15
_CLOSE_MINUTE_JST = 30
_PRICE_PRECISION = 1
_SIZE_PRECISION = 0

_DATE_RE = re.compile(r"^\d{8}$")
_NUMERIC_FIELDS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class KlineRow:
    """立花 CLMMfdsGetMarketPriceHistory レスポンスの 1 本足."""

    date: str   # "YYYYMMDD"
    open: str
    high: str
    low: str
    close: str
    volume: str

    def __post_init__(self) -> None:
        if not _DATE_RE.match(self.date):
            raise ValueError(
                f"KlineRow.date: expected YYYYMMDD (8 digits), got {self.date!r}"
            )
        for field_name in _NUMERIC_FIELDS:
            value = getattr(self, field_name)
            try:
                Decimal(value)
            except (InvalidOperation, TypeError) as exc:
                raise ValueError(
                    f"KlineRow.{field_name}: not convertible to Decimal: {value!r}"
                ) from exc


def klines_to_bars(
    symbol: str,
    venue: str,
    klines: list[KlineRow],
) -> list[Bar]:
    """KlineRow のリストを nautilus Bar のリストに変換する。

    返す Bar は ts_event 昇順でソートされる。
    """
    if not klines:
        return []

    instrument_id = InstrumentId(Symbol(symbol), Venue(venue))
    bar_type = BarType.from_str(f"{instrument_id}-1-DAY-MID-EXTERNAL")
    bars = [_convert(bar_type, row) for row in klines]
    bars.sort(key=lambda b: b.ts_event)
    return bars


def _convert(bar_type: BarType, row: KlineRow) -> Bar:
    ts_ns = _date_to_ts_ns(row.date)
    return Bar(
        bar_type=bar_type,
        open=Price(Decimal(row.open), precision=_PRICE_PRECISION),
        high=Price(Decimal(row.high), precision=_PRICE_PRECISION),
        low=Price(Decimal(row.low), precision=_PRICE_PRECISION),
        close=Price(Decimal(row.close), precision=_PRICE_PRECISION),
        volume=Quantity(Decimal(row.volume), precision=_SIZE_PRECISION),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def _date_to_ts_ns(date_str: str) -> int:
    """YYYYMMDD を 15:30 JST の nanoseconds UTC タイムスタンプに変換する。"""
    y, m, d = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
    dt_jst = datetime(y, m, d, _CLOSE_HOUR_JST, _CLOSE_MINUTE_JST, tzinfo=_JST)
    return int(dt_jst.timestamp() * 1_000_000_000)
