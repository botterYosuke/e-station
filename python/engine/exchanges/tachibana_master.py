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

import codecs
import json
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

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
    # Per-stock yobine (tick-band) table — referenced by
    # CLMIssueSizyouMstKabu.sYobineTaniNumber. Decoded by
    # `decode_clm_yobine_record` below.
    "CLMYobine",
})


# Price-endpoint sCLMID set. These REQUEST endpoints must be sent against
# `sUrlPrice`, not `sUrlRequest` / `sUrlMaster`. Confirmed against the
# official samples ``e_api_get_market_price_tel.py`` and
# ``e_api_get_market_price_history_tel.py``.
PRICE_CLMIDS: frozenset[str] = frozenset({
    "CLMMfdsGetMarketPrice",
    "CLMMfdsGetMarketPriceHistory",
})


# ---------------------------------------------------------------------------
# CLMYobine — per-stock tick band table (B1)
# ---------------------------------------------------------------------------
#
# The §2-12 section of api_request_if_master_v4r5.pdf describes the
# *structure* of the tick table (max 20 ``(sKizunPrice_N, sYobineTanka_N,
# sDecimal_N)`` triples per ``sYobineTaniNumber``); the actual price→tick
# values come from the runtime ``CLMYobine`` master stream, NOT a single
# hardcoded table. ``CLMIssueSizyouMstKabu.sYobineTaniNumber`` references
# the row that applies to a given issue.
#
# Spec invariant from the PDF screenshot: at least one of the 20 columns
# always carries the sentinel ``999999999`` as ``sKizunPrice_N``. We use
# that as the table cap — bands at/after the first sentinel are kept once
# (so ``price <= cap`` always matches a legal price) and trailing duplicate
# sentinels are dropped.

YOBINE_PRICE_SENTINEL: Decimal = Decimal("999999999")


@dataclass(frozen=True, slots=True)
class YobineBand:
    """One ``(基準値段, 呼値単価, 小数桁数)`` row from a CLMYobine record."""

    kizun_price: Decimal
    yobine_tanka: Decimal
    decimals: int


@dataclass(frozen=True, slots=True)
class CLMYobineRecord:
    """Decoded CLMYobine record. ``bands`` is order-preserving (slot 1..20)."""

    sYobineTaniNumber: str
    bands: list[YobineBand]


def decode_clm_yobine_record(record: dict[str, Any]) -> CLMYobineRecord:
    """Decode a raw CLMYobine record (one of the CLMEventDownload children).

    Reads slots 1..20 as ``(sKizunPrice_N, sYobineTanka_N, sDecimal_N)``.
    Trailing all-sentinel padding (``999999999`` cap repeated) is collapsed
    so the returned ``bands`` list ends with exactly one cap row (the first
    occurrence of the sentinel). This relies on the PDF invariant that the
    sentinel is always present somewhere in the row.
    """
    if record.get("sCLMID") != "CLMYobine":
        raise ValueError(
            f"decode_clm_yobine_record: expected sCLMID='CLMYobine', "
            f"got {record.get('sCLMID')!r}"
        )

    raw_bands: list[YobineBand] = []
    for i in range(1, 21):
        kizun_raw = record.get(f"sKizunPrice_{i}")
        tanka_raw = record.get(f"sYobineTanka_{i}")
        dec_raw = record.get(f"sDecimal_{i}")
        if kizun_raw is None or tanka_raw is None or dec_raw is None:
            # Missing slot — treat as end-of-table (defensive; well-formed
            # CLMYobine always carries all 20).
            break
        raw_bands.append(
            YobineBand(
                kizun_price=Decimal(str(kizun_raw)),
                yobine_tanka=Decimal(str(tanka_raw)),
                decimals=int(dec_raw),
            )
        )

    bands: list[YobineBand] = []
    for band in raw_bands:
        bands.append(band)
        if band.kizun_price >= YOBINE_PRICE_SENTINEL:
            # First sentinel = table cap; drop everything after.
            break

    return CLMYobineRecord(
        sYobineTaniNumber=str(record["sYobineTaniNumber"]),
        bands=bands,
    )


def tick_size_for_price(
    price: Decimal,
    yobine_code: str,
    yobine_table: dict[str, list[YobineBand]],
) -> Decimal:
    """Return the tick size (呼値単価) that applies to ``price`` under
    ``yobine_table[yobine_code]``.

    Selection rule: the first band whose ``kizun_price >= price`` (i.e.
    ``price <= kizun_price``) wins. ``bands`` is assumed to be in slot
    order (1..20) which is also ascending by ``kizun_price`` per the
    Tachibana master schema.

    Args:
        price: Must be a ``Decimal``. ``float`` / ``int`` are rejected to
            avoid binary-float drift at tick boundaries.
        yobine_code: ``sYobineTaniNumber`` from
            ``CLMIssueSizyouMstKabu``.
        yobine_table: ``{yobine_code: bands}`` dict, typically built from
            decoded CLMYobine records.

    Raises:
        TypeError: if ``price`` is not a ``Decimal``.
        KeyError: if ``yobine_code`` is not in ``yobine_table``.
        ValueError: if no band matches (should not happen in practice
            because the 999999999 sentinel caps every legal table).
    """
    if not isinstance(price, Decimal) or isinstance(price, bool):
        raise TypeError(
            f"tick_size_for_price: price must be Decimal, got {type(price).__name__}"
        )
    bands = yobine_table[yobine_code]  # KeyError on miss — by contract
    for band in bands:
        if price <= band.kizun_price:
            return band.yobine_tanka
    raise ValueError(
        f"tick_size_for_price: no band matched price={price} for "
        f"yobine_code={yobine_code!r} (table missing 999999999 cap?)"
    )


def resolve_min_ticksize_for_issue(
    issue_record: dict[str, Any],
    yobine_table: dict[str, list[YobineBand]],
    snapshot_price: Decimal | None,
) -> Decimal:
    """Resolve the tick size that applies to a given issue.

    Glues ``CLMIssueSizyouMstKabu.sYobineTaniNumber`` to the live
    ``yobine_table`` built from decoded CLMYobine records and returns the
    tick size at ``snapshot_price``.

    When ``snapshot_price`` is ``None`` (e.g. at startup before any quote
    has arrived) we fall back to the **first band's tick** — i.e. the
    finest tick in the table for that issue's yobine code. This is
    conservative for the use we care about (display precision / quote
    rounding before a real price is known): a too-fine tick will still
    align with all coarser tiers, while a too-coarse tick at a low price
    tier would round legal quotes onto illegal grids.

    Args:
        issue_record: A ``CLMIssueSizyouMstKabu`` row. Must carry
            ``sYobineTaniNumber``.
        yobine_table: ``{yobine_code: bands}`` typically built from
            decoded CLMYobine records by the master loader.
        snapshot_price: Latest price for the issue, or ``None`` when no
            quote has been observed yet.

    Raises:
        KeyError: if ``issue_record["sYobineTaniNumber"]`` is missing
            from ``yobine_table``. Callers must treat this as a master
            integrity error (CLMYobine and CLMIssueSizyouMstKabu out of
            sync) rather than silently fall through.
    """
    yobine_code = str(issue_record["sYobineTaniNumber"])
    bands = yobine_table[yobine_code]  # KeyError on miss — by contract
    if snapshot_price is None:
        # First band carries the smallest kizun_price ⇒ the finest tick.
        return bands[0].yobine_tanka
    return tick_size_for_price(snapshot_price, yobine_code, yobine_table)


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

    __slots__ = ("_decoder", "_buf", "_records", "_complete", "_json")

    def __init__(self) -> None:
        # IncrementalDecoder safely holds partial multibyte sequences across
        # chunk boundaries — fixes data corruption when a 2-byte SJIS char is
        # split between two chunks (would otherwise become U+FFFD).
        self._decoder = codecs.getincrementaldecoder("shift_jis")(errors="replace")
        self._buf: str = ""
        self._records: list[dict[str, Any]] = []
        self._complete: bool = False
        # raw_decode is structure-aware: it ignores braces / quotes inside
        # JSON string values, so an `sIssueName` like ``"abc}def"`` no longer
        # truncates the surrounding record.
        self._json = json.JSONDecoder()

    @property
    def is_complete(self) -> bool:
        return self._complete

    def feed(self, chunk: bytes) -> None:
        if self._complete:
            return
        self._buf += self._decoder.decode(chunk)
        self._drain()

    def records(self) -> list[dict[str, Any]]:
        return self._records

    def _drain(self) -> None:
        while True:
            start = self._buf.find("{")
            if start < 0:
                # Discard noise before any record.
                self._buf = ""
                return

            try:
                record, end_idx = self._json.raw_decode(self._buf, start)
            except json.JSONDecodeError:
                # Either the record is incomplete (need more bytes) or it's
                # genuinely malformed. We can't tell from raw_decode alone, so
                # leave the partial record in the buffer and wait for more.
                self._buf = self._buf[start:]
                return

            self._buf = self._buf[end_idx:]

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
