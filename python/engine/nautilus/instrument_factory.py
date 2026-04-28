"""nautilus Instrument 生成ファクトリ (N0 / N1.2 拡張)

N0: price_precision=1 / price_increment=0.1 をハードコード（data-mapping.md §1, §3 案 A）。
N1.2: lot_size / price_precision を InstrumentCache（Q10 案 B + 案 A fallback）から取得。
      ``lot_size_override`` 引数で起動 config から個別上書きを許可する。

後方互換: 既存テストが渡している ``lot_size=100`` / ``price_precision=1`` の
キーワード引数は引き続き優先する（明示値はキャッシュより強い）。
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity

from engine.nautilus.instrument_cache import InstrumentCache

_DEFAULT_PRICE_INCREMENT = Decimal("0.1")


def make_equity_instrument(
    symbol: str,
    venue: str,
    *,
    price_precision: int | None = None,
    price_increment: Decimal = _DEFAULT_PRICE_INCREMENT,
    lot_size: int | None = None,
    lot_size_override: dict[str, int] | None = None,
) -> Equity:
    """立花株式 Equity を生成する。

    引数の優先順位（lot_size）:
        1. ``lot_size`` を明示渡し → そのまま使う（旧 N0 互換）
        2. ``lot_size_override[<id>]`` がある → 上書き
        3. ``InstrumentCache.shared()`` に登録済み → cache 値
        4. fallback=100 + ``log.warning``

    Q8 決定: price_increment は 0.1 円固定（案 A）。
    実際の呼値丸めは _compose_request_payload で行う。

    ts_event / ts_init: N0 仮置きで 0 をハードコード（data-mapping.md §2 "N0 仮置き"）。
        N2 LiveExecutionClient では起動時刻(nanoseconds)を渡すこと。
    """
    instrument_id_str = f"{symbol}.{venue}"
    cache = InstrumentCache.shared()

    if lot_size is None:
        resolved_lot_size = cache.get_lot_size(
            instrument_id_str, override=lot_size_override
        )
    else:
        resolved_lot_size = lot_size

    if price_precision is None:
        resolved_precision = cache.get_price_precision(instrument_id_str)
    else:
        resolved_precision = price_precision

    return Equity(
        instrument_id=InstrumentId(Symbol(symbol), Venue(venue)),
        raw_symbol=Symbol(symbol),
        currency=JPY,
        price_precision=resolved_precision,
        price_increment=Price(price_increment, precision=resolved_precision),
        lot_size=Quantity(resolved_lot_size, precision=0),
        isin=None,
        ts_event=0,
        ts_init=0,
    )
