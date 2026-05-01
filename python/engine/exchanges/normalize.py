"""Price and quantity normalization for IPC boundary data.

All functions work on the dict-based event payloads sent to the IPC outbox.
Prices are rounded to the nearest min_ticksize multiple using ROUND_HALF_UP,
which matches the Rust ``Price::round_to_min_tick`` integer-arithmetic behavior
(``(units + half) / tick_units * tick_units`` where half = tick_units // 2).

Design notes (Phase C):
- Phase C adds Python-side normalization while Rust still normalizes as a backup.
  Price normalization is idempotent (round(already-rounded) = unchanged), so
  applying it in both Python and Rust is safe.
- Qty normalization functions are implemented here but wired into adapter streams
  only after Phase E (Rust-side normalization becomes debug_assert-only), to avoid
  double-normalization of quantities.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def normalize_price(price_str: str, min_ticksize: Decimal) -> str:
    """Round price string to the nearest min_ticksize multiple.

    Returns the original string unchanged when min_ticksize <= 0 or when
    ``price_str`` is not parseable as a Decimal.
    """
    if min_ticksize <= 0:
        return price_str
    try:
        price = Decimal(price_str)
    except InvalidOperation:
        return price_str
    normalized = (price / min_ticksize).to_integral_value(ROUND_HALF_UP) * min_ticksize
    return str(normalized)


def normalize_depth_levels(
    levels: list[dict],
    min_ticksize: Decimal,
) -> list[dict]:
    """Return a new list of depth levels with prices rounded to min_ticksize."""
    return [
        {"price": normalize_price(lv["price"], min_ticksize), "qty": lv["qty"]}
        for lv in levels
    ]


def normalize_depth(depth_event: dict, min_ticksize: Decimal) -> dict:
    """Return a new depth event dict with all bid/ask prices normalized.

    Suitable for both ``DepthSnapshot`` and ``DepthDiff`` events.
    """
    result = dict(depth_event)
    result["bids"] = normalize_depth_levels(depth_event.get("bids", []), min_ticksize)
    result["asks"] = normalize_depth_levels(depth_event.get("asks", []), min_ticksize)
    return result


def normalize_trade(trade: dict, min_ticksize: Decimal) -> dict:
    """Return a new trade dict with price normalized. Skips if price key absent."""
    if "price" not in trade:
        return trade
    result = dict(trade)
    result["price"] = normalize_price(trade["price"], min_ticksize)
    return result


def normalize_trades_event(trades_event: dict, min_ticksize: Decimal) -> dict:
    """Return a new Trades event dict with all trade prices normalized."""
    result = dict(trades_event)
    result["trades"] = [
        normalize_trade(t, min_ticksize) for t in trades_event.get("trades", [])
    ]
    return result


def normalize_kline(kline_event: dict, min_ticksize: Decimal) -> dict:
    """Return a new KlineUpdate event dict with OHLC prices normalized."""
    kline = kline_event.get("kline")
    if kline is None:
        return kline_event
    result_kline = dict(kline)
    for field in ("open", "high", "low", "close"):
        if field in result_kline:
            result_kline[field] = normalize_price(result_kline[field], min_ticksize)
    result = dict(kline_event)
    result["kline"] = result_kline
    return result


def normalize_qty_contract(qty_str: str, contract_size: Decimal) -> str:
    """Normalize qty in contract units to base-asset units by multiplying by contract_size.

    Used by crypto venues where the exchange sends quantities in contracts rather
    than base-asset units (qty_norm_kind == "contract").
    """
    if contract_size <= 0:
        return qty_str
    try:
        qty = Decimal(qty_str)
    except InvalidOperation:
        return qty_str
    return str(qty * contract_size)
