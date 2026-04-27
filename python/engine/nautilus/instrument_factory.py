"""nautilus Instrument 生成ファクトリ (N0)

N0: price_precision=1 / price_increment=0.1 をハードコード（data-mapping.md §1, §3 案 A 決定）。
N1 以降: Instrument 経由で精度を切り替える。
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity

_DEFAULT_PRICE_PRECISION = 1
_DEFAULT_PRICE_INCREMENT = Decimal("0.1")
_DEFAULT_LOT_SIZE = 100


def make_equity_instrument(
    symbol: str,
    venue: str,
    *,
    price_precision: int = _DEFAULT_PRICE_PRECISION,
    price_increment: Decimal = _DEFAULT_PRICE_INCREMENT,
    lot_size: int = _DEFAULT_LOT_SIZE,
) -> Equity:
    """立花株式 Equity を生成する。

    Q8 決定: price_increment は 0.1 円固定（案 A）。
    実際の呼値丸めは _compose_request_payload で行う。

    ts_event / ts_init: N0 仮置きで 0 をハードコード（data-mapping.md §2 "N0 仮置き"）。
        N2 LiveExecutionClient では起動時刻(nanoseconds)を渡すこと。
    """
    return Equity(
        instrument_id=InstrumentId(Symbol(symbol), Venue(venue)),
        raw_symbol=Symbol(symbol),
        currency=JPY,
        price_precision=price_precision,
        price_increment=Price(price_increment, precision=price_precision),
        lot_size=Quantity(lot_size, precision=0),
        isin=None,
        ts_event=0,
        ts_init=0,
    )
