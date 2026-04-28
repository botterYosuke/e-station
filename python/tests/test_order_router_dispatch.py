"""N1.5: order_router.py のディスパッチ動作テスト.

live / replay モードに応じた注文ルーティングを検証する:
- live  → tachibana_orders.submit_order 委譲
- replay → tachibana_orders_replay.jsonl WAL 書込
           + client_order_id に 'REPLAY-' プレフィックス付与
           + CLMZanKaiKanougaku / CLMZanShinkiKanoIjiritu HTTP 呼出し 0 件
"""

from __future__ import annotations

import json
import asyncio

import pytest

from engine.exchanges.tachibana_orders import NautilusOrderEnvelope
from engine.order_router import route_submit_order, submit_order_live, submit_order_replay


# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------


def _make_envelope(client_order_id: str = "CLI-001") -> NautilusOrderEnvelope:
    return NautilusOrderEnvelope(
        client_order_id=client_order_id,
        instrument_id="7203.T/TSE",
        order_side="BUY",
        order_type="MARKET",
        quantity="100",
        time_in_force="DAY",
        post_only=False,
        reduce_only=False,
        tags=["cash_margin=cash", "account_type=specific"],
    )


# ---------------------------------------------------------------------------
# live ディスパッチ
# ---------------------------------------------------------------------------


def test_live_dispatch_calls_tachibana_submit_order(monkeypatch):
    """live モードで submit_order_live が tachibana_orders.submit_order を呼ぶこと。"""
    calls: list[dict] = []

    async def _mock_submit(session, second_password, order, *, p_no_counter=None, wal_path=None, request_key=0):
        calls.append({"order": order, "wal_path": wal_path})
        from engine.exchanges.tachibana_orders import SubmitOrderResult
        return SubmitOrderResult(
            client_order_id=order.client_order_id,
            venue_order_id="VNO-001",
        )

    monkeypatch.setattr("engine.order_router._tachibana_submit_order", _mock_submit)

    session = object()
    envelope = _make_envelope()
    result = asyncio.run(
        submit_order_live(envelope, session=session, second_password="pw")
    )
    assert len(calls) == 1
    assert calls[0]["order"].client_order_id == "CLI-001"
    assert result.client_order_id == "CLI-001"


# ---------------------------------------------------------------------------
# replay WAL 書込
# ---------------------------------------------------------------------------


def test_replay_dispatch_writes_wal(tmp_path):
    """replay モードで submit_order_replay が tachibana_orders_replay.jsonl に WAL を書くこと。"""
    wal = tmp_path / "tachibana_orders_replay.jsonl"
    envelope = _make_envelope()
    result = submit_order_replay(envelope, wal_path=wal)

    assert wal.exists(), "WAL ファイルが作成されていない"
    lines = [l for l in wal.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 1, "WAL に 1 行以上書かれていること"

    record = json.loads(lines[0])
    assert record["phase"] == "submit"
    assert "ts" in record
    assert "client_order_id" in record


def test_replay_wal_has_replay_prefix_client_order_id(tmp_path):
    """replay WAL の client_order_id が 'REPLAY-' プレフィックスを持つこと。"""
    wal = tmp_path / "tachibana_orders_replay.jsonl"
    envelope = _make_envelope("CLI-002")
    result = submit_order_replay(envelope, wal_path=wal)

    # 戻り値にもプレフィックスが付く
    assert result["client_order_id"].startswith("REPLAY-"), (
        f"client_order_id が REPLAY- で始まっていない: {result['client_order_id']}"
    )
    assert result["client_order_id"] == "REPLAY-CLI-002"

    # WAL 内にも REPLAY- プレフィックスが書かれている
    records = [
        json.loads(l)
        for l in wal.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert records[0]["client_order_id"].startswith("REPLAY-")


def test_replay_dispatch_returns_accepted_status(tmp_path):
    """submit_order_replay の戻り値に status='accepted' と venue='replay' が含まれること。"""
    wal = tmp_path / "r.jsonl"
    result = submit_order_replay(_make_envelope(), wal_path=wal)
    assert result["status"] == "accepted"
    assert result["venue"] == "replay"


# ---------------------------------------------------------------------------
# CLMZanKaiKanougaku 呼出しガード
# ---------------------------------------------------------------------------


def test_replay_does_not_call_clm_zan_kai_kanougaku(monkeypatch, tmp_path):
    """replay モードで CLMZanKaiKanougaku / fetch_buying_power への HTTP 呼出しが 0 回であること。"""
    import engine.exchanges.tachibana_orders as torders

    fetch_buying_power_calls: list = []
    fetch_credit_calls: list = []

    async def _mock_fetch_buying_power(session, *, p_no_counter=None):
        fetch_buying_power_calls.append(1)
        from engine.exchanges.tachibana_orders import BuyingPowerResult
        return BuyingPowerResult(available_amount=0)

    async def _mock_fetch_credit(session, *, p_no_counter=None):
        fetch_credit_calls.append(1)
        from engine.exchanges.tachibana_orders import CreditBuyingPowerResult
        return CreditBuyingPowerResult(available_amount=0)

    monkeypatch.setattr(torders, "fetch_buying_power", _mock_fetch_buying_power)
    monkeypatch.setattr(torders, "fetch_credit_buying_power", _mock_fetch_credit)

    wal = tmp_path / "r.jsonl"
    submit_order_replay(_make_envelope(), wal_path=wal)

    assert len(fetch_buying_power_calls) == 0, "replay 中に CLMZanKaiKanougaku が呼ばれた"
    assert len(fetch_credit_calls) == 0, "replay 中に CLMZanShinkiKanoIjiritu が呼ばれた"


# ---------------------------------------------------------------------------
# route_submit_order のルーティング
# ---------------------------------------------------------------------------


def test_route_submit_order_live_dispatches_to_live(monkeypatch):
    """route_submit_order(mode='live', ...) が submit_order_live を呼ぶこと。"""
    calls: list[str] = []

    async def _mock_live(envelope, *, session, second_password, **kwargs):
        calls.append("live")
        from engine.exchanges.tachibana_orders import SubmitOrderResult
        return SubmitOrderResult(
            client_order_id=envelope.client_order_id,
            venue_order_id="VNO-999",
        )

    def _mock_replay(envelope, *, wal_path=None):
        calls.append("replay")
        return {"status": "accepted", "client_order_id": "REPLAY-X", "venue": "replay"}

    monkeypatch.setattr("engine.order_router.submit_order_live", _mock_live)
    monkeypatch.setattr("engine.order_router.submit_order_replay", _mock_replay)

    result = asyncio.run(
        route_submit_order(
            "live",
            _make_envelope(),
            session=object(),
            second_password="pw",
        )
    )
    assert calls == ["live"]


def test_route_submit_order_replay_dispatches_to_replay(tmp_path, monkeypatch):
    """route_submit_order(mode='replay', ...) が submit_order_replay を呼ぶこと。"""
    calls: list[str] = []

    async def _mock_live(envelope, *, session, second_password, **kwargs):
        calls.append("live")
        from engine.exchanges.tachibana_orders import SubmitOrderResult
        return SubmitOrderResult(
            client_order_id=envelope.client_order_id,
            venue_order_id="VNO-999",
        )

    def _mock_replay(envelope, *, wal_path=None):
        calls.append("replay")
        return {
            "status": "accepted",
            "client_order_id": f"REPLAY-{envelope.client_order_id}",
            "venue": "replay",
        }

    monkeypatch.setattr("engine.order_router.submit_order_live", _mock_live)
    monkeypatch.setattr("engine.order_router.submit_order_replay", _mock_replay)

    wal = tmp_path / "r.jsonl"
    result = asyncio.run(
        route_submit_order(
            "replay",
            _make_envelope(),
            session=None,
            second_password=None,
            wal_path=wal,
        )
    )
    assert calls == ["replay"]
    assert result["venue"] == "replay"


# ---------------------------------------------------------------------------
# TestServerReplayRouting
# ---------------------------------------------------------------------------


class TestServerReplayRouting:
    """server.py が replay venue の SubmitOrder を REPLAY_NOT_IMPLEMENTED で reject しないことを確認する。

    N1.5 配線: _do_submit_order_inner の M-7 早期 reject 解除。
    """

    def _make_submit_order_msg(self) -> dict:
        return {
            "op": "SubmitOrder",
            "request_id": "test-req-1",
            "venue": "replay",
            "order": {
                "client_order_id": "CID-001",
                "instrument_id": "1301.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "100",
                "price": None,
                "time_in_force": "DAY",
                "post_only": False,
                "reduce_only": False,
                "tags": [],
                "request_key": 0,
            },
        }

    def test_replay_submit_order_returns_accepted_not_rejected(self, tmp_path):
        """venue=replay の SubmitOrder が OrderAccepted を返す（REPLAY_NOT_IMPLEMENTED ではない）。"""
        from engine.server import DataEngineServer

        server = DataEngineServer.__new__(DataEngineServer)
        server._cache_dir = tmp_path
        server._outbox = []
        server._submit_order_inflight_count = 0

        import asyncio

        msg = self._make_submit_order_msg()
        asyncio.run(server._do_submit_order_inner(msg))

        events = [e["event"] for e in server._outbox]
        assert "OrderSubmitted" in events
        assert "OrderAccepted" in events
        assert "REPLAY_NOT_IMPLEMENTED" not in str(server._outbox)

    def test_replay_submit_order_writes_wal(self, tmp_path):
        """venue=replay の SubmitOrder が WAL ファイルに記録される。"""
        from engine.server import DataEngineServer
        import json

        server = DataEngineServer.__new__(DataEngineServer)
        server._cache_dir = tmp_path
        server._outbox = []
        server._submit_order_inflight_count = 0

        import asyncio

        msg = self._make_submit_order_msg()
        asyncio.run(server._do_submit_order_inner(msg))

        wal_path = tmp_path / "tachibana_orders_replay.jsonl"
        assert wal_path.exists()
        line = json.loads(wal_path.read_text().strip())
        assert line["phase"] == "submit"
        assert "REPLAY-" in line["client_order_id"]

    def test_replay_accepted_client_order_id_has_replay_prefix(self, tmp_path):
        """OrderAccepted の client_order_id が REPLAY- プレフィックスを持つ。"""
        from engine.server import DataEngineServer

        server = DataEngineServer.__new__(DataEngineServer)
        server._cache_dir = tmp_path
        server._outbox = []
        server._submit_order_inflight_count = 0

        import asyncio

        msg = self._make_submit_order_msg()
        asyncio.run(server._do_submit_order_inner(msg))

        accepted = next(e for e in server._outbox if e["event"] == "OrderAccepted")
        assert accepted["client_order_id"].startswith("REPLAY-")
