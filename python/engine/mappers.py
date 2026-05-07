"""adapter model → wire DTO mapper（C1 / adapter-type-boundary Step 3）。

責務: `python/engine/models.py` の boundary モデル（venue 内部正規化型）を、
`python/engine/schemas.py` の wire DTO（IPC 配信形）に変換する単一段の mapper。

設計方針:
- adapter model は wire 責務（event / venue / market / request_id）を持たない。
  そのため mapper の引数で venue / ticker / market を補充する。
- 数値（Decimal）は wire DTO 上では str（IpcMessage 制約）。`schemas.DepthLevel` は
  `price: str` / `qty: str` であり、Decimal を str 化する。
- timestamp は wire 上では `ts_ms`（int milliseconds）または ISO 文字列。
  `Trade` の wire（`TradeMsg`）は `ts_ms: int` を持つため、UTC datetime → ms に変換する。
- gap recovery 不変条件: `DepthDiff` の `stream_session_id` / `sequence_id` /
  `prev_sequence_id` は loss なく wire DTO へ伝搬する（Rust 側の自動再要求が依存）。

非責務:
- 永続化 / outbox 送出（呼び出し側 server.py の責務）
- IPC スキーマバージョンの変更
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from engine.models import DepthDiff, OrderBook, Trade
from engine.schemas import (
    DepthDiffMsg,
    DepthLevel,
    DepthSnapshotMsg,
    TradeMsg,
    Trades,
)


if TYPE_CHECKING:
    pass


def _decimal_to_str(d: Decimal) -> str:
    """Decimal → 文字列。指数表記を避け wire 比較を安定させる。"""
    # `format(d, "f")` は `1E+2` を `100` に正規化する（trailing zero 維持）。
    return format(d, "f")


def _level(price: Decimal, qty: Decimal) -> DepthLevel:
    return DepthLevel(price=_decimal_to_str(price), qty=_decimal_to_str(qty))


def _ts_to_ms(ts: datetime) -> int:
    if ts.tzinfo is None:
        raise ValueError("ts must be timezone-aware")
    return int(ts.astimezone(timezone.utc).timestamp() * 1000)


def order_book_to_wire(
    model: OrderBook,
    *,
    venue: str,
    ticker: str,
    market: str,
    request_id: str | None = None,
) -> DepthSnapshotMsg:
    """`OrderBook` → `DepthSnapshotMsg`。

    `stream_session_id` / `sequence_id` が adapter model 上で `None` の場合は
    wire DTO の必須フィールドを満たせないため `ValueError` を raise する
    （呼び出し側で server 層が補充する責務）。
    """
    if model.stream_session_id is None:
        raise ValueError("OrderBook.stream_session_id required for wire DTO")
    if model.sequence_id is None:
        raise ValueError("OrderBook.sequence_id required for wire DTO")
    return DepthSnapshotMsg(
        request_id=request_id,
        venue=venue,
        ticker=ticker,
        market=market,
        stream_session_id=model.stream_session_id,
        sequence_id=model.sequence_id,
        bids=[_level(p, q) for p, q in model.bids],
        asks=[_level(p, q) for p, q in model.asks],
    )


def depth_diff_to_wire(
    model: DepthDiff,
    *,
    venue: str,
    ticker: str,
    market: str,
) -> DepthDiffMsg:
    """`DepthDiff` → `DepthDiffMsg`。gap recovery フィールドを保持する。"""
    return DepthDiffMsg(
        venue=venue,
        ticker=ticker,
        market=market,
        stream_session_id=model.stream_session_id,
        sequence_id=model.sequence_id,
        prev_sequence_id=model.prev_sequence_id,
        bids=[_level(p, q) for p, q in model.bids],
        asks=[_level(p, q) for p, q in model.asks],
    )


def trade_to_wire(model: Trade) -> TradeMsg:
    """`Trade` → `TradeMsg`（バッチに格納する単一要素）。"""
    return TradeMsg(
        price=_decimal_to_str(model.price),
        qty=_decimal_to_str(model.qty),
        side=model.side,
        ts_ms=_ts_to_ms(model.timestamp),
        is_liquidation=False,
    )


def trades_to_wire(
    models: list[Trade],
    *,
    venue: str,
    ticker: str,
    market: str,
    stream_session_id: str,
) -> Trades:
    """複数の `Trade` をバッチ wire DTO `Trades` に集約する。"""
    return Trades(
        venue=venue,
        ticker=ticker,
        market=market,
        stream_session_id=stream_session_id,
        trades=[trade_to_wire(m) for m in models],
    )


__all__ = [
    "order_book_to_wire",
    "depth_diff_to_wire",
    "trade_to_wire",
    "trades_to_wire",
]
