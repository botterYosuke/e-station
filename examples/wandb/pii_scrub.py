"""PII scrubber for W&B submit -- examples/wandb side independent copy.

This module is intentionally independent from python/engine/ (no cross-import).
submit_run.py uses this for upload-time sanity checks (F9b).

NOTE: The engine-side RunBuffer writer (python/engine/run_buffer.py, F9a scope)
has its own pii_scrub module. This file is the examples-side copy that focuses
solely on the upload-time guard contract.

Allow-lists (unified decision 47):
    fills.jsonl    : symbol, side, qty, price, ts, pnl
    equity.jsonl   : ts, equity, cash, position, pnl
    narrative.jsonl: ts, message, tags

    Additional alias keys (ts_event_ms, buying_power, etc.) are included for
    compatibility with IPC field names used by the engine.

Usage:
    from pii_scrub import scrub, assert_no_forbidden_keys, FILLS_ALLOWED_KEYS

    # Strip any key not in the allow-list:
    clean = scrub(event_dict, FILLS_ALLOWED_KEYS)

    # Abort upload if any forbidden key is present (double-guard):
    assert_no_forbidden_keys(clean, FILLS_ALLOWED_KEYS)
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Upload-time allow-list constants
# ---------------------------------------------------------------------------

FILLS_ALLOWED_KEYS: frozenset[str] = frozenset(
    ["symbol", "side", "qty", "price", "ts", "ts_event_ms", "pnl"]
)
EQUITY_ALLOWED_KEYS: frozenset[str] = frozenset(
    ["ts", "ts_event_ms", "equity", "cash", "buying_power", "position", "pnl", "strategy_id"]
)
NARRATIVE_ALLOWED_KEYS: frozenset[str] = frozenset(
    ["ts", "ts_event_ms", "message", "tags", "signal_kind", "side", "price", "tag", "note"]
)


def scrub(event_dict: dict, allowed_keys: frozenset) -> dict:
    """Return a copy of *event_dict* containing only keys in *allowed_keys*.

    Any key not present in *allowed_keys* is silently dropped.
    Call this before writing to JSONL or uploading to W&B.
    """
    return {k: v for k, v in event_dict.items() if k in allowed_keys}


def assert_no_forbidden_keys(event_dict: dict, allowed_keys: frozenset) -> None:
    """Raise ValueError if *event_dict* contains keys outside *allowed_keys*.

    Call this immediately before uploading to W&B as a final upload-time guard.
    Raises:
        ValueError: with the set of offending keys listed in the message.
    """
    forbidden = set(event_dict.keys()) - allowed_keys
    if forbidden:
        raise ValueError(f"forbidden keys in event: {forbidden}")
