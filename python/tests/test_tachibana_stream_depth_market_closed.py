"""M5: stream_depth の市場時間外パス（REST スナップショット → DepthSnapshot → VenueError → Disconnected）。

tachibana.py:782-823 のコードパスをカバーする。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import engine.exchanges.tachibana_ws as _ws_mod
from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


def _fake_session() -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws="wss://example.test/event/",
        zyoutoeki_kazei_c="",
    )


def _make_worker(tmp_path: Path) -> TachibanaWorker:
    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True, session=_fake_session())
    return worker


# ---------------------------------------------------------------------------
# M5-1: 市場時間外で DepthSnapshot → VenueError → Disconnected の順に積まれる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_depth_market_closed_emits_depth_snapshot_then_venue_error_then_disconnected(
    tmp_path: Path,
) -> None:
    """市場時間外で is_market_open=False のとき outbox に DepthSnapshot→VenueError→Disconnected が積まれる。"""
    worker = _make_worker(tmp_path)

    snapshot_data = {
        "last_update_id": 1_700_000_000_000,
        "bids": [{"price": "2878", "qty": "100"}],
        "asks": [{"price": "2882", "qty": "150"}],
        "recv_ts_ms": 1_700_000_000_000,
    }
    worker.fetch_depth_snapshot = AsyncMock(return_value=snapshot_data)  # type: ignore[method-assign]

    outbox: list[dict] = []
    stop = asyncio.Event()

    with patch.object(_ws_mod, "is_market_open", return_value=False):
        await worker.stream_depth("7203", "stock", "sess-1", outbox, stop)

    event_types = [e.get("event") for e in outbox]

    assert "DepthSnapshot" in event_types, (
        f"DepthSnapshot が outbox にない。event_types={event_types}"
    )
    assert "VenueError" in event_types, (
        f"VenueError が outbox にない。event_types={event_types}"
    )
    assert "Disconnected" in event_types, (
        f"Disconnected が outbox にない。event_types={event_types}"
    )

    # 順序確認: DepthSnapshot → VenueError → Disconnected
    idx_snap = next(i for i, e in enumerate(outbox) if e.get("event") == "DepthSnapshot")
    idx_venue = next(i for i, e in enumerate(outbox) if e.get("event") == "VenueError")
    idx_disc = next(i for i, e in enumerate(outbox) if e.get("event") == "Disconnected")
    assert idx_snap < idx_venue < idx_disc, (
        f"イベント順序が不正: DepthSnapshot@{idx_snap} VenueError@{idx_venue} Disconnected@{idx_disc}"
    )

    # VenueError の code が market_closed
    venue_err = outbox[idx_venue]
    assert venue_err.get("code") == "market_closed", (
        f"VenueError.code が market_closed でない: {venue_err}"
    )

    # Disconnected の reason が market_closed
    disc = outbox[idx_disc]
    assert disc.get("reason") == "market_closed", (
        f"Disconnected.reason が market_closed でない: {disc}"
    )


# ---------------------------------------------------------------------------
# M5-2: DepthSnapshot の bids/asks が dict 形式であることを pin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_depth_market_closed_depth_snapshot_uses_dict_levels(
    tmp_path: Path,
) -> None:
    """market_closed 時の DepthSnapshot.bids/asks が {"price": ..., "qty": ...} dict であること。"""
    worker = _make_worker(tmp_path)

    snapshot_data = {
        "last_update_id": 1_700_000_000_001,
        "bids": [
            {"price": "2878", "qty": "100"},
            {"price": "2877", "qty": "200"},
        ],
        "asks": [
            {"price": "2882", "qty": "150"},
            {"price": "2883", "qty": "300"},
        ],
        "recv_ts_ms": 1_700_000_000_001,
    }
    worker.fetch_depth_snapshot = AsyncMock(return_value=snapshot_data)  # type: ignore[method-assign]

    outbox: list[dict] = []
    stop = asyncio.Event()

    with patch.object(_ws_mod, "is_market_open", return_value=False):
        await worker.stream_depth("7203", "stock", "sess-2", outbox, stop)

    snap = next((e for e in outbox if e.get("event") == "DepthSnapshot"), None)
    assert snap is not None, "DepthSnapshot が outbox にない"

    for bid in snap["bids"]:
        assert "price" in bid and "qty" in bid, (
            f"bids の要素が dict{{price, qty}} 形式でない: {bid}"
        )
    for ask in snap["asks"]:
        assert "price" in ask and "qty" in ask, (
            f"asks の要素が dict{{price, qty}} 形式でない: {ask}"
        )

    # 具体値確認
    assert snap["bids"][0]["price"] == "2878"
    assert snap["asks"][0]["price"] == "2882"


# ---------------------------------------------------------------------------
# R2-M3: 空スナップショット（bids/asks 両方空）のとき DepthSnapshot を積まない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_depth_market_closed_empty_snapshot_skips_depth_snapshot(
    tmp_path: Path,
) -> None:
    """空スナップショット（bids/asks 両方空）のとき DepthSnapshot を積まない。
    outbox は [VenueError, Disconnected] の 2 件だけになる。
    """
    worker = _make_worker(tmp_path)

    async def _empty_snapshot(_ticker: str, _market: str) -> dict:
        # last_update_id=1, recv_ts_ms=1 は H1 修正後の「非ゼロ保証」と整合する最小値。
        # bids/asks が両方空なので DepthSnapshot は積まれないことが期待される。
        return {"last_update_id": 1, "bids": [], "asks": [], "recv_ts_ms": 1}

    worker.fetch_depth_snapshot = AsyncMock(side_effect=_empty_snapshot)  # type: ignore[method-assign]

    outbox: list[dict] = []
    stop = asyncio.Event()

    with patch.object(_ws_mod, "is_market_open", return_value=False):
        await worker.stream_depth("7203", "stock", "sess-3", outbox, stop)

    event_types = [e.get("event") for e in outbox]

    # 空スナップショットのとき DepthSnapshot は積まれない
    assert "DepthSnapshot" not in event_types, (
        f"空スナップショットなのに DepthSnapshot が outbox に存在する: {event_types}"
    )

    # VenueError(market_closed) と Disconnected は必ず積まれる
    assert "VenueError" in event_types, (
        f"VenueError が outbox にない: {event_types}"
    )
    assert "Disconnected" in event_types, (
        f"Disconnected が outbox にない: {event_types}"
    )

    # VenueError.code = market_closed
    venue_err = next(e for e in outbox if e.get("event") == "VenueError")
    assert venue_err.get("code") == "market_closed"

    # Disconnected.reason = market_closed
    disc = next(e for e in outbox if e.get("event") == "Disconnected")
    assert disc.get("reason") == "market_closed"
