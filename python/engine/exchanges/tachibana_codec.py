"""Tachibana e-shiten response/EVENT-frame decoders.

* `decode_response_body` — Shift-JIS decode (SKILL.md R7). Used for **every**
  REQUEST/EVENT byte payload; never call `httpx.Response.text` /
  `Response.json()` directly on Tachibana responses.
* `parse_event_frame` — split a single EVENT chunk into ``(key, value)`` pairs
  using the ASCII control separators ^A (\\x01) and ^B (\\x02). ^C (\\x03)
  sub-separators inside a value are preserved as-is for the caller to handle.
* `deserialize_tachibana_list` — normalize the Tachibana ``""`` empty-list
  convention (R8) into a real Python ``list``.
"""

from __future__ import annotations

import re
from typing import Any

_ITEM_SEP = "\x01"   # ^A item separator
_KV_SEP = "\x02"     # ^B key/value separator

# e-shiten.jp hostnames are dynamically assigned virtual URLs returned after
# login (sUrlRequest / sUrlEvent). They must never appear in WAL/logs/reason_text.
_VIRTUAL_URL_RE = re.compile(r"https?://\S*e-shiten\.jp\S*", re.IGNORECASE)


def mask_virtual_url(s: str) -> str:
    """Replace Tachibana virtual URLs (e-shiten.jp) with ``[MASKED_URL]``.

    Call this before writing any string that originates from a login response
    to WAL entries, log messages, or error reason_text (architecture.md C-H1).
    """
    return _VIRTUAL_URL_RE.sub("[MASKED_URL]", s)


def decode_response_body(payload: bytes) -> str:
    """Decode a Tachibana HTTP/WS payload as Shift-JIS.

    `errors='replace'` keeps surrounding ASCII intact when a stray byte slips
    in (we'd rather see ``"abc�def"`` than blow up mid-frame).
    """
    return payload.decode("shift_jis", errors="replace")


def parse_event_frame(data: str) -> list[tuple[str, str]]:
    """Split an EVENT frame into ``(key, value)`` pairs.

    Items are separated by ^A and key/value by ^B. Items without a ^B are
    skipped (defensive against malformed frames). ^C sub-separators inside a
    value are preserved as-is — multi-value fields (e.g. five-tier quotes) are
    decoded by the caller.
    """
    pairs: list[tuple[str, str]] = []
    for item in data.split(_ITEM_SEP):
        if not item or _KV_SEP not in item:
            continue
        key, _, value = item.partition(_KV_SEP)
        pairs.append((key, value))
    return pairs


def deserialize_tachibana_list(value: Any) -> list:
    """Normalize Tachibana's empty-list convention (R8): ``""`` → ``[]``.

    Tachibana returns ``""`` (and occasionally ``null``) instead of ``[]`` for
    list-shaped fields with no rows. Wrap this around any deserialization path
    that expects a list to keep downstream code uniform.

    Raises:
        TypeError: if `value` is neither a `list`, `None`, nor an empty string.
    """
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, str):
        if value == "":
            return []
        raise TypeError(
            f"deserialize_tachibana_list: non-empty string is not a list: {value!r}"
        )
    raise TypeError(
        f"deserialize_tachibana_list: unexpected type {type(value).__name__}"
    )
