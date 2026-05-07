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

    def test_equity_without_last_prices_uses_fill_price_fallback(self):
        """last_prices 省略時は on_fill で記録された価格にフォールバックする
        （StepBackward 後の pull 経路で MTM を保つため）。"""
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3000"))
        # last_prices 省略 → 内部の _last_prices (fill 価格 3000) にフォールバック
        equity = pv.equity()
        assert equity == Decimal("1000000"), (
            "equity must use stored fill price as last-known reference (700000 cash + 100*3000 MTM)"
        )

    def test_equity_no_positions_no_last_prices_returns_cash(self):
        """ポジションも last_prices もないとき equity は cash と等しい。"""
        pv = PortfolioView(Decimal("500000"))
        assert pv.equity() == Decimal("500000")

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

    def test_restore_from_dict_restores_cash(self):
        pv = PortfolioView(Decimal("2000000"))
        pv._restore_from_dict({"cash": "1500000", "ts_event_ms": 0})
        assert pv.cash == Decimal("1500000")

    def test_restore_from_dict_clears_positions(self):
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("10"), Decimal("100"))
        assert pv.has_open_positions
        pv._restore_from_dict({"cash": "900000", "ts_event_ms": 0})
        assert not pv.has_open_positions, "positions must be cleared after _restore_from_dict"

    def test_restore_from_dict_missing_cash_keeps_current(self):
        pv = PortfolioView(Decimal("1000000"))
        pv._restore_from_dict({"ts_event_ms": 0})
        assert pv.cash == Decimal("1000000")

    def test_restore_from_dict_with_positions_and_last_prices_preserves_mtm(self):
        """StepBackward 後の pull 経路 (GetBuyingPower) で equity が MTM 込みで返ること。

        ユーザー再現シナリオ: cash=700000, position=100, last_price=3100 →
        equity = 700000 + 100*3100 = 1010000 が返るべき。
        以前は to_ipc_dict が last_prices なしで呼ばれて equity=cash のみだった。
        """
        pv = PortfolioView(Decimal("1000000"))
        snapshot = {
            "cash": "700000",
            "positions": {"1301.TSE": {"qty": "100", "cost": "300000"}},
            "last_prices": {"1301.TSE": "3100"},
        }
        pv._restore_from_dict(snapshot)
        # equity() / to_ipc_dict() を last_prices 省略で呼んでも MTM が反映されること
        assert pv.equity() == Decimal("1010000")
        ipc = pv.to_ipc_dict("strategy-x")
        assert ipc["equity"] == "1010000", (
            f"to_ipc_dict must use restored _last_prices for MTM, got equity={ipc['equity']}"
        )


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
