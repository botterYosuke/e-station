"""engine.pii_scrub -- PII allow-list scrubber.

This is the canonical (engine-side) source for PII allow-list constants.
The W&B upload responsibility has been migrated to the blacksheep repository;
this module owns the engine-side constants and scrubbing logic only.

Contract:

    pii_scrub(event_dict: dict, allowed_keys: frozenset) -> dict

    - Returns a new dict containing only keys in *allowed_keys*.
    - The "event" / "type" dispatch keys are always dropped from the result
      (they are not data, just routing fields).
    - If any key in FORBIDDEN_KEYS is present in the input, the offending
      keys are stripped (the event is NOT dropped) and a WARNING is logged
      so that developers can trace upstream leaks. This avoids silently
      losing real Nautilus Fill events that legitimately carry order_id.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

FILLS_ALLOWED_KEYS: frozenset = frozenset({
    "symbol", "side", "qty", "price", "ts", "pnl", "instrument_id",
})
EQUITY_ALLOWED_KEYS: frozenset = frozenset({
    "ts", "equity", "cash", "position", "buying_power", "strategy_id",
})
NARRATIVE_ALLOWED_KEYS: frozenset = frozenset({
    "ts", "message", "tags", "signal_kind", "side", "price",
    "tag", "note", "instrument_id", "strategy_id",
})
FORBIDDEN_KEYS: frozenset = frozenset({
    "account_id", "token", "raw", "raw_data", "payload",
    "venue_order_id", "client_order_id",
    "venue_token", "credential", "password", "user_id",
    "session_id", "secret",
})


def pii_scrub(event_dict: dict, allowed_keys: frozenset) -> dict:
    """Return scrubbed dict containing only *allowed_keys* (and never forbidden keys).

    If any key from FORBIDDEN_KEYS is present in *event_dict*, those keys are
    stripped and a WARNING is logged so developers can trace upstream leaks.
    The event itself is NOT dropped: this lets legitimate Nautilus Fill events
    (which always include an order_id-style field upstream) reach the JSONL
    files after being filtered through the allow-list.

    Returns:
        dict: filtered copy. Empty dict if *event_dict* is not a dict.
    """
    if not isinstance(event_dict, dict):
        return {}

    forbidden_present = [k for k in FORBIDDEN_KEYS if k in event_dict]
    if forbidden_present:
        log.warning(
            "pii_scrub: forbidden keys detected and stripped: %s",
            sorted(forbidden_present),
        )

    return {
        k: v
        for k, v in event_dict.items()
        if k in allowed_keys and k not in FORBIDDEN_KEYS and k != "event"
    }
