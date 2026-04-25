"""Tachibana CLMEventDownload streaming parser + master-endpoint registry.

CLMEventDownload returns a stream of one-JSON-object-per-record (`sJsonOfmt="4"`),
each record terminated by ``}`` and the entire stream terminated by a record
whose ``sCLMID == "CLMEventDownloadComplete"``. Chunks may break anywhere —
mid-record, between records, or right before the final ``}`` — so the parser
buffers raw bytes and only attempts JSON decode on a complete record.

We keep the parser self-contained so the same code path serves both:
* `iter_records_from_chunks(chunks)` — generator/list-driven (test helper +
  small offline fixtures), and
* `MasterStreamParser.feed(bytes)`   — incremental for the live HTTP body.

Both surfaces apply the `is_valid_issue_code` pre-validate (HIGH-3 / F-M11)
to drop malformed ``sIssueCode`` rows with a warn log, so downstream Rust
``Ticker::new`` never panics.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Iterator
from typing import Any

from .tachibana_codec import decode_response_body

log = logging.getLogger(__name__)


CLM_EVENT_DOWNLOAD_COMPLETE = "CLMEventDownloadComplete"


# Master-endpoint sCLMID set (MEDIUM-C7). When `build_request_url` is called
# with one of these the caller must hand a `MasterUrl`; non-master sCLMIDs go
# to `RequestUrl` (or `PriceUrl` for price snapshots). The list is the union
# of SKILL.md ComT4 endpoints and the master-stream identifiers; extending
# the set is fine but removing entries requires a Phase-spec review.
MASTER_CLMIDS: frozenset[str] = frozenset({
    # ComT4 — REQUEST endpoints reachable only via sUrlMaster
    "CLMEventDownload",
    "CLMMfdsGetMasterData",
    "CLMMfdsGetIssueDetail",
    "CLMMfdsGetNewsHead",
    "CLMMfdsGetNewsBody",
    "CLMMfdsGetSyoukinZan",
    "CLMMfdsGetShinyouZan",
    "CLMMfdsGetHibuInfo",
    # sTargetCLMID values that appear inside CLMEventDownload — kept here so
    # callers can reuse the same set when filtering parsed master records.
    "CLMIssueMstKabu",
    "CLMIssueSizyouMstKabu",
    "CLMIssueMstSak",
    "CLMIssueMstOp",
    "CLMIssueMstOther",
    "CLMOrderErrReason",
    "CLMDateZyouhou",
})


# ASCII alnum, 1..28 chars. Tighter than `Ticker::new` (which only forbids
# `|` and non-ASCII), because Phase 1 stock master never legitimately uses
# punctuation. Phase 2 (futures/options) will need to relax this — see
# implementation-plan.md "MEDIUM-6 注記".
_ISSUE_CODE_RE = re.compile(r"[A-Za-z0-9]{1,28}")


def is_valid_issue_code(code: str) -> bool:
    """Return True if `code` is safe to hand to Rust ``Ticker::new``."""
    return bool(_ISSUE_CODE_RE.fullmatch(code))


# ---------------------------------------------------------------------------
# Streaming parser
# ---------------------------------------------------------------------------


class MasterStreamParser:
    """Incremental parser for the CLMEventDownload byte stream.

    Usage::

        parser = MasterStreamParser()
        async for chunk in response.aiter_bytes():
            parser.feed(chunk)
            if parser.is_complete:
                break
        records = parser.records()
    """

    __slots__ = ("_buf", "_records", "_complete")

    def __init__(self) -> None:
        # The buffer is a *string* because Shift-JIS multibyte runs can span
        # chunk boundaries; we let `decode_response_body` use 'replace' to
        # avoid raising mid-stream.
        self._buf: str = ""
        self._records: list[dict[str, Any]] = []
        self._complete: bool = False

    @property
    def is_complete(self) -> bool:
        return self._complete

    def feed(self, chunk: bytes) -> None:
        if self._complete:
            return
        self._buf += decode_response_body(chunk)
        self._drain()

    def records(self) -> list[dict[str, Any]]:
        return self._records

    def _drain(self) -> None:
        # A record ends at the next `}`. We look for `{ ... }` pairs starting
        # from the buffer head. Because each record is a flat JSON object
        # without nested braces, a simple scan for the next `}` is sufficient
        # — but we count `{` to be defensive in case future master records
        # gain nested arrays of objects.
        while True:
            start = self._buf.find("{")
            if start < 0:
                # Discard noise before any record.
                self._buf = ""
                return

            depth = 0
            end = -1
            for i in range(start, len(self._buf)):
                ch = self._buf[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end < 0:
                # Incomplete — leave the partial record in the buffer.
                self._buf = self._buf[start:]
                return

            raw = self._buf[start : end + 1]
            self._buf = self._buf[end + 1 :]

            try:
                record = json.loads(raw)
            except json.JSONDecodeError as e:
                log.warning("tachibana: malformed master record dropped: %s", e)
                continue

            if not isinstance(record, dict):
                continue

            if record.get("sCLMID") == CLM_EVENT_DOWNLOAD_COMPLETE:
                self._complete = True
                self._buf = ""
                return

            issue = record.get("sIssueCode")
            if isinstance(issue, str) and not is_valid_issue_code(issue):
                log.warning("tachibana: skipping invalid issue code: %r", issue)
                continue

            self._records.append(record)


def iter_records_from_chunks(chunks: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    """Generator wrapper — feed an iterable of byte chunks, yield each record.

    Stops as soon as the terminator record is seen. Anything that arrives in
    chunks past the terminator is silently dropped (matches `MasterStreamParser`).
    """
    parser = MasterStreamParser()
    for chunk in chunks:
        parser.feed(chunk)
        if parser.is_complete:
            break
    yield from parser.records()
