"""Integration test: kabu PUSH → adapter → mapper → wire DTO pipeline (C1 配線確認).

kabu PUSH → KabuStationAdapter.parse_board() → OrderBook → (ssid/seq 補充) →
order_book_to_wire() → DepthSnapshotMsg → wire dict のパスを通しで検証する。

kabu PUSH は stream_session_id / sequence_id を持たないため、
サーバー層が補充する責務を持つ（IPC 必須フィールドを満たすため）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine.exchanges.kabusapi_adapter import KabuStationAdapter
from engine.kabu_push_pipeline import kabu_board_to_wire_dict, kabu_execution_to_wire_dict


_RAW_BOARD: dict = {
    "Symbol": "1234",
    "CurrentPriceTime": "2024-01-01T15:00:00+09:00",
    "CurrentPrice": 3000.0,
    "TradingVolume": 50000.0,
    "Buy1": {"Price": 3000.0, "Qty": 100.0, "Time": "15:00:00", "Sign": "0101"},
    "Buy2": {"Price": 2999.0, "Qty": 200.0, "Time": "15:00:00", "Sign": "0101"},
    "Sell1": {"Price": 3001.0, "Qty": 50.0, "Time": "15:00:00", "Sign": "0101"},
    "Sell2": {"Price": 3002.0, "Qty": 150.0, "Time": "15:00:00", "Sign": "0101"},
}


@pytest.fixture
def adapter() -> KabuStationAdapter:
    return KabuStationAdapter(["1234"])


class TestKabuBoardToWireDict:
    """kabu_board_to_wire_dict() — adapter→mapper pipeline の統合テスト。"""

    def test_event_and_venue(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="engine:1", seq=1, market="stock"
        )
        assert payload["event"] == "DepthSnapshot"
        assert payload["venue"] == "kabu_station"

    def test_ticker_and_market(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="engine:1", seq=1, market="stock"
        )
        assert payload["ticker"] == "1234"
        assert payload["market"] == "stock"

    def test_ssid_and_seq_injected(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="engine:99", seq=42, market="stock"
        )
        assert payload["stream_session_id"] == "engine:99"
        assert payload["sequence_id"] == 42

    def test_bids_count_matches_raw(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=1, market="stock"
        )
        assert len(payload["bids"]) == 2
        assert len(payload["asks"]) == 2

    def test_price_is_string_without_exponent(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=1, market="stock"
        )
        for level in payload["bids"] + payload["asks"]:
            price = level["price"]
            assert isinstance(price, str)
            assert "E" not in price and "e" not in price

    def test_sequence_is_independent_per_call(self, adapter: KabuStationAdapter) -> None:
        p1 = kabu_board_to_wire_dict(_RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=1, market="stock")
        p2 = kabu_board_to_wire_dict(_RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=2, market="stock")
        assert p1["sequence_id"] == 1
        assert p2["sequence_id"] == 2

    def test_no_none_in_top_level_values(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=1, market="stock"
        )
        for key, val in payload.items():
            assert val is not None, f"unexpected None at key={key!r}"

    def test_default_market_is_stock(self, adapter: KabuStationAdapter) -> None:
        """market="stock" を明示したとき "stock" が返ることを確認する（Bug C リグレッション）。

        kabu_board_to_wire_dict の market パラメータが "spot" に戻ると
        Rust depth_stream が ev_market!="stock" でスキップしラダーが Waiting for data... になる。
        market は必須引数化されたため、省略は TypeError になりこれ自体がリグレッションガード。
        """
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="engine:1", seq=1, market="stock"
        )
        assert payload["market"] == "stock", (
            f"market は 'stock' である必要があります（Bug C リグレッション）。"
            f"実際: {payload['market']!r}"
        )

    def test_custom_market_propagated(self, adapter: KabuStationAdapter) -> None:
        # NOTE: "topix" は Rust 側フィルタ (market_kind_to_ipc) に存在しない値であり、
        # この値を kabu_station に渡すと全イベントがスキップされる。
        # このテストは純粋関数の伝播性のみを検証する（Rust 側の受理可否は検証しない）。
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=1, market="topix"
        )
        assert payload["market"] == "topix"

    def test_bid_best_is_highest_buy(self, adapter: KabuStationAdapter) -> None:
        # Buy1 (3000) is the best bid (highest); Buy2 (2999) is second
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=1, market="stock"
        )
        bid_prices = [float(lvl["price"]) for lvl in payload["bids"]]
        assert bid_prices[0] > bid_prices[1]

    def test_ask_best_is_lowest_sell(self, adapter: KabuStationAdapter) -> None:
        # Sell1 (3001) is the best ask (lowest); Sell2 (3002) is second
        payload = kabu_board_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=1, market="stock"
        )
        ask_prices = [float(lvl["price"]) for lvl in payload["asks"]]
        assert ask_prices[0] < ask_prices[1]


class TestKabuExecutionToWireDict:
    """kabu_execution_to_wire_dict() — adapter→mapper pipeline の統合テスト。"""

    def test_event_and_venue(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_execution_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="engine:1", market="stock"
        )
        assert payload["event"] == "Trades"
        assert payload["venue"] == "kabu_station"

    def test_ticker_and_ssid(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_execution_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="engine:1", market="stock"
        )
        assert payload["ticker"] == "1234"
        assert payload["stream_session_id"] == "engine:1"

    def test_single_trade_in_batch(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_execution_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", market="stock"
        )
        assert len(payload["trades"]) == 1
        trade = payload["trades"][0]
        assert trade["side"] == "unknown"
        assert isinstance(trade["ts_ms"], int)
        assert trade["ts_ms"] > 0

    def test_trade_price_is_current_price(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_execution_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", market="stock"
        )
        trade = payload["trades"][0]
        # CurrentPrice=3000.0 → Decimal("3000.0") → "3000.0"
        assert trade["price"] == "3000.0"

    def test_trade_ts_ms_is_utc(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_execution_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", market="stock"
        )
        trade = payload["trades"][0]
        # 2024-01-01T15:00:00+09:00 = 2024-01-01T06:00:00Z
        expected_ms = int(
            datetime(2024, 1, 1, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert trade["ts_ms"] == expected_ms

    def test_trade_qty_normalized_to_zero(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_execution_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", market="stock"
        )
        # kabu PUSH qty は累積値のため 0 で正規化
        assert payload["trades"][0]["qty"] == "0"

    def test_market_is_stock(self, adapter: KabuStationAdapter) -> None:
        payload = kabu_execution_to_wire_dict(
            _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", market="stock"
        )
        assert payload["market"] == "stock"


# ---------------------------------------------------------------------------
# H-R2-3: market 必須引数の negative テスト
# ---------------------------------------------------------------------------


class TestMarketRequiredNegative:
    """kabu_board_to_wire_dict / kabu_execution_to_wire_dict の market 必須化 negative テスト（H-R2-3）。

    market が必須キーワード引数になっているため、省略すると TypeError が発生することを確認する。
    これにより将来のデフォルト値リグレッション（例: market="spot" に戻る）を防ぐ。
    """

    def test_market_required_raises_type_error_for_board(self, adapter: KabuStationAdapter) -> None:
        """kabu_board_to_wire_dict で market を省略すると TypeError になることを確認する（H-R2-3）。"""
        with pytest.raises(TypeError):
            kabu_board_to_wire_dict(  # type: ignore[call-arg]
                _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s", seq=1
                # market を意図的に省略
            )

    def test_market_required_raises_type_error_for_execution(self, adapter: KabuStationAdapter) -> None:
        """kabu_execution_to_wire_dict で market を省略すると TypeError になることを確認する（H-R2-3）。"""
        with pytest.raises(TypeError):
            kabu_execution_to_wire_dict(  # type: ignore[call-arg]
                _RAW_BOARD, adapter=adapter, ticker="1234", ssid="s"
                # market を意図的に省略
            )
