"""engine.pii_scrub -- PII allow-list scrubber."""
from __future__ import annotations
from typing import Optional

FILLS_ALLOWED_KEYS: frozenset = frozenset({
    "symbol", "side", "qty", "price", "ts", "ts_event_ms", "pnl", "instrument_id",
})
EQUITY_ALLOWED_KEYS: frozenset = frozenset({
    "ts", "ts_event_ms", "equity", "cash", "buying_power", "position", "pnl", "strategy_id",
})
NARRATIVE_ALLOWED_KEYS: frozenset = frozenset({
    "ts", "ts_event_ms", "message", "tags", "signal_kind", "side", "price",
    "tag", "note", "instrument_id", "strategy_id",
})
FORBIDDEN_KEYS: frozenset = frozenset({
    "account_id", "token", "raw", "raw_data", "payload",
    "venue_order_id", "client_order_id", "order_id",
    "venue_token", "credential", "password", "user_id",
    "session_id", "secret",
})

def pii_scrub(event_dict: dict, allowed_keys: frozenset) -> Optional[dict]:
    """Return scrubbed dict, or None if forbidden keys are present."""
    if not isinstance(event_dict, dict):
        return None
    for key in FORBIDDEN_KEYS:
        if key in event_dict:
            return None
    result = {k: v for k, v in event_dict.items() if k in allowed_keys and k != "event"}
    return result
