"""kabu PUSH board/execution → wire DTO pipeline (C1 配線).

純粋関数として実装し、server.py から呼び出す。
I/O・サーバー状態なしで独立テスト可能。

kabu PUSH は stream_session_id / sequence_id を持たないため、
呼び出し側（server 層）が ssid / seq を補充して渡す。
"""
from __future__ import annotations

from engine.exchanges.kabusapi_adapter import KabuStationAdapter
from engine.mappers import order_book_to_wire, trades_to_wire


def kabu_board_to_wire_dict(
    raw: dict,
    *,
    adapter: KabuStationAdapter,
    ticker: str,
    ssid: str,
    seq: int,
    market: str,
) -> dict:
    """Raw kabu PUSH 板スナップショット JSON → DepthSnapshot wire dict.

    kabu PUSH には stream_session_id / sequence_id が存在しないため、
    server 層が ssid / seq を採番して渡す責務を持つ。

    Returns:
        DepthSnapshotMsg.model_dump(exclude_none=True) 相当の dict。
        outbox.append() にそのまま渡せる。
    """
    book = adapter.parse_board(raw)
    # model_copy は shallow copy: book.bids is book_with_ids.bids が True になる。
    # order_book_to_wire() 直後に book_with_ids を破棄するため現在は安全。
    # book を長期キャッシュする場合は deep=True を使うこと。
    book_with_ids = book.model_copy(update={
        "stream_session_id": ssid,
        "sequence_id": seq,
    })
    wire = order_book_to_wire(
        book_with_ids, venue="kabu_station", ticker=ticker, market=market
    )
    return wire.model_dump(exclude_none=True)


def kabu_execution_to_wire_dict(
    raw: dict,
    *,
    adapter: KabuStationAdapter,
    ticker: str,
    ssid: str,
    market: str,
) -> dict:
    """Raw kabu PUSH 約定 JSON → Trades wire dict（1 件バッチ）.

    kabu PUSH は独立した約定ストリームを持たないため、
    板スナップショットの CurrentPrice / CurrentPriceTime から抽出する。
    qty は累積値のため 0 で正規化（KabuStationAdapter.parse_execution() 仕様）。

    Returns:
        Trades.model_dump(exclude_none=True) 相当の dict。
    """
    trade = adapter.parse_execution(raw)
    wire = trades_to_wire(
        [trade],
        venue="kabu_station",
        ticker=ticker,
        market=market,
        stream_session_id=ssid,
    )
    return wire.model_dump(exclude_none=True)


__all__ = ["kabu_board_to_wire_dict", "kabu_execution_to_wire_dict"]
