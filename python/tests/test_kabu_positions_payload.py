import pytest
from unittest.mock import AsyncMock

from engine.server import DataEngineServer


@pytest.fixture
def dummy_server():
    server = DataEngineServer(port=0, token="test")
    server._outbox = AsyncMock()
    server._kabu_venue = AsyncMock()
    server._kabu_venue._token = "mock-token"
    return server


@pytest.mark.asyncio
async def test_kabu_positions_produces_valid_protobuf_enum_variants(dummy_server):
    # kabu REST client mock
    kabu_rest_client_mock = AsyncMock()
    kabu_rest_client_mock.fetch_positions.return_value = [
        # Cash long position
        {"ExecutionID": "1", "Symbol": "7203", "Leaves": 100, "Price": 1500, "Side": "2", "MarginTradeType": 0},
        # Margin credit short position
        {"ExecutionID": "2", "Symbol": "9984", "Leaves": 200, "Price": 5000, "Side": "1", "MarginTradeType": 1},
        # Margin general long position
        {"ExecutionID": "3", "Symbol": "6758", "Leaves": 300, "Price": 8000, "Side": "2", "MarginTradeType": 2},
    ]
    dummy_server._make_kabu_rest_client = lambda: kabu_rest_client_mock

    await dummy_server._do_get_positions_kabu({"request_id": "req-1"})

    dummy_server._outbox.append.assert_called_once()
    event = dummy_server._outbox.append.call_args[0][0]

    assert event["event"] == "PositionsUpdated"
    assert event["venue"] == "kabu_station"

    positions = event["positions"]
    assert len(positions) == 3

    # Cash long
    assert positions[0]["instrument_id"] == "7203.KabuStation Stock"
    assert positions[0]["qty"] == "100"
    assert positions[0]["position_type"] == "cash"
    assert positions[0]["tategyoku_id"] == "1"

    # Margin credit short
    assert positions[1]["instrument_id"] == "9984.KabuStation Stock"
    assert positions[1]["qty"] == "-200"  # short position gets negative qty
    assert positions[1]["position_type"] == "margin_credit"
    assert positions[1]["tategyoku_id"] == "2"

    # Margin general long
    assert positions[2]["instrument_id"] == "6758.KabuStation Stock"
    assert positions[2]["qty"] == "300"
    assert positions[2]["position_type"] == "margin_general"
    assert positions[2]["tategyoku_id"] == "3"

