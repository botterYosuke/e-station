"""PII scrubber for W&B submit -- examples/wandb side independent copy.

This module is intentionally independent from python/engine/ (no cross-import,
because examples/ runs under `uv run --with wandb` and must be importable
without depending on the engine package layout).

The CONSTANTS and FUNCTION CONTRACT must match python/engine/pii_scrub.py
exactly. Consistency between the two copies is asserted by
python/tests/test_run_buffer_writer.py::test_pii_allowlist_consistency_engine_and_examples.

Contract (unified with engine, M12):

    pii_scrub(event_dict: dict, allowed_keys: frozenset) -> dict

    - Returns a new dict containing only keys in *allowed_keys*.
    - The "event" / "type" dispatch keys are dropped from the result.
    - If any key in FORBIDDEN_KEYS is present in input, those keys are stripped
      and a WARNING is logged. The event is NOT dropped.

Usage:
    from pii_scrub import pii_scrub, assert_no_forbidden_keys, FILLS_ALLOWED_KEYS

    clean = pii_scrub(event_dict, FILLS_ALLOWED_KEYS)
    assert_no_forbidden_keys(clean, FILLS_ALLOWED_KEYS)  # double-guard
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allow-list constants -- MUST match python/engine/pii_scrub.py
# ---------------------------------------------------------------------------

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

    If any FORBIDDEN_KEYS member is present, those keys are stripped and a
    WARNING is logged. The event itself is NOT dropped.
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


def assert_no_forbidden_keys(event_dict: dict, allowed_keys: frozenset) -> None:
    """Raise ValueError if *event_dict* contains keys outside *allowed_keys*.

    Call this immediately before uploading to W&B as a final upload-time guard.

    F9 R1-M11: this function is a **double-guard**. The primary scrub happens in
    :func:`pii_scrub`, which already strips forbidden keys and any keys outside
    *allowed_keys*. ``assert_no_forbidden_keys`` then re-validates the final
    payload immediately before it crosses into ``wandb.log`` / ``wandb.Artifact``.
    The intent is to catch any third-party code path that constructed an event
    dict **without** going through :func:`pii_scrub` (e.g. a future caller that
    inlines its own filtering and accidentally drops the scrub step). Keeping
    the name unchanged avoids a breaking API rename; the contract is encoded in
    this docstring.

    Raises:
        ValueError: with the set of offending keys listed in the message.
    """
    forbidden = set(event_dict.keys()) - allowed_keys
    if forbidden:
        raise ValueError(f"forbidden keys in event: {forbidden}")
