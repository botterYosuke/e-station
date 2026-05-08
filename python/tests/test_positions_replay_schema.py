"""RED phase test: PositionRecord.model_validate rejects venue="replay".

`build_replay_positions_event` sets venue="replay" on every position row, but
`PositionRecord.venue` is currently typed as `Literal["tachibana"]`, so
`model_validate` must raise a Pydantic ValidationError.

This test is expected to FAIL until `schemas.py:966` is fixed to allow
`Literal["tachibana", "replay"]` (or equivalent).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.schemas import PositionRecord
from engine.server import build_replay_positions_event


def test_position_record_validates_replay_venue():
    """PositionRecord.model_validate must accept venue="replay" from build_replay_positions_event."""
    portfolio_state = {
        "cash": "1000000",
        "positions": {
            "7203.TSE": {"qty": "100", "cost": "2500"},
        },
        "last_prices": {"7203.TSE": "2600"},
    }

    ev = build_replay_positions_event(portfolio_state, ts_ms=1746000000000)

    assert len(ev["positions"]) == 1, "Expected one position row in the event"
    pos_dict = ev["positions"][0]

    # This must succeed once Literal["tachibana"] is widened to include "replay".
    # Currently it raises ValidationError because venue="replay" is not in Literal["tachibana"].
    record = PositionRecord.model_validate(pos_dict)

    assert record.venue == "replay"
    assert record.instrument_id == "7203.TSE"
    assert record.qty == "100"
    assert record.market_value == "260000"
    assert record.position_type == "cash"
    assert record.tategyoku_id is None
