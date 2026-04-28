"""N1.16: REPLAY 買付余力テスト — PortfolioView 単体 + server.py CLM ガード."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.nautilus.portfolio_view import PortfolioView


class TestPortfolioView:
    def test_buy_reduces_cash(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        assert pv.cash == Decimal("700000")

    def test_sell_increases_cash(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        pv.on_fill("1301.TSE", "SELL", Decimal("100"), Decimal("3100"))
        assert pv.cash == Decimal("1010000")

    def test_equity_includes_mtm(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        # position qty=100, cash=700000
        equity = pv.equity({"1301.TSE": Decimal("3100")})
        assert equity == Decimal("1010000")  # 700000 + 100*3100

    def test_equity_without_last_prices_returns_cash(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        # no last_prices provided
        equity = pv.equity()
        assert equity == Decimal("700000")  # cash only

    def test_reset_restores_initial_cash(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        pv.reset(Decimal("2000000"))
        assert pv.cash == Decimal("2000000")
        assert pv.equity() == Decimal("2000000")

    def test_reset_clears_positions(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        pv.reset(Decimal("1000000"))
        # After reset, equity with last prices should equal cash (no positions)
        assert pv.equity({"1301.TSE": Decimal("9999")}) == Decimal("1000000")

    def test_buying_power_equals_cash(self):
        pv = PortfolioView(Decimal("500000"))
        assert pv.buying_power == pv.cash

    def test_full_sell_removes_position(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        pv.on_fill("1301.TSE", "SELL", Decimal("100"), Decimal("3000"))
        # Position should be gone; equity = initial cash
        assert pv.equity({"1301.TSE": Decimal("9999")}) == Decimal("1000000")

    def test_to_ipc_dict_structure(self):
        pv = PortfolioView(Decimal("1000000"))
        d = pv.to_ipc_dict("buy-and-hold")
        assert d["event"] == "ReplayBuyingPower"
        assert d["strategy_id"] == "buy-and-hold"
        assert d["cash"] == "1000000"
        assert d["buying_power"] == "1000000"
        assert d["equity"] == "1000000"
        assert isinstance(d["ts_event_ms"], int)

    def test_multiple_instruments(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        pv.on_fill("6758.TSE", "BUY", Decimal("10"), Decimal("5000"))
        assert pv.cash == Decimal("1000000") - Decimal("300000") - Decimal("50000")
        assert pv.cash == Decimal("650000")


class TestReplayBuyingPowerClmGuard:
    """GetBuyingPower{venue="replay"} は CLMZanKaiKanougaku を呼ばない（D9.6 ガード）。"""

    @pytest.mark.asyncio
    async def test_clm_not_called_in_replay(self):
        """venue='replay' の GetBuyingPower は public _do_get_buying_power 経由で呼ぶ。

        H-E: ReplayBuyingPower は push event なので request_id フィールドを持たない。
        H-F: 内部メソッド _do_get_buying_power_replay を直接叩かず公開経路を使う。
        """
        from engine.server import DataEngineServer

        server = DataEngineServer(port=0, token="test")
        server._mode = "replay"
        server._outbox = MagicMock()
        server._outbox.append = MagicMock()

        msg = {"op": "GetBuyingPower", "request_id": "r-001", "venue": "replay"}

        with patch(
            "engine.server.tachibana_fetch_buying_power"
        ) as mock_clm, patch(
            "engine.server.tachibana_fetch_credit_buying_power"
        ) as mock_credit:
            await server._do_get_buying_power(msg)
            mock_clm.assert_not_called()
            mock_credit.assert_not_called()

        # outbox should have gotten a ReplayBuyingPower push event (no request_id)
        server._outbox.append.assert_called_once()
        args = server._outbox.append.call_args[0][0]
        assert args["event"] == "ReplayBuyingPower"
        assert "request_id" not in args, "ReplayBuyingPower must not include request_id"
