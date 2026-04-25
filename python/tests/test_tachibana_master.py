"""TDD: tachibana_master — CLMEventDownload streaming parser + ticker validate."""

from __future__ import annotations

import json
import logging

import pytest

from engine.exchanges.tachibana_master import (
    MASTER_CLMIDS,
    CLM_EVENT_DOWNLOAD_COMPLETE,
    MasterStreamParser,
    is_valid_issue_code,
    iter_records_from_chunks,
)


# ---------------------------------------------------------------------------
# MASTER_CLMIDS frozenset (MEDIUM-C7)
# ---------------------------------------------------------------------------


def test_master_clmids_contains_known_master_endpoints():
    """The frozenset must include sCLMID values that hit `sUrlMaster`."""
    expected_subset = {
        "CLMEventDownload",
        "CLMMfdsGetMasterData",
        "CLMMfdsGetIssueDetail",
        "CLMMfdsGetNewsHead",
        "CLMMfdsGetNewsBody",
        "CLMMfdsGetSyoukinZan",
        "CLMMfdsGetShinyouZan",
        "CLMMfdsGetHibuInfo",
    }
    assert expected_subset.issubset(MASTER_CLMIDS)


def test_master_clmids_is_frozen():
    assert isinstance(MASTER_CLMIDS, frozenset)


# ---------------------------------------------------------------------------
# is_valid_issue_code (HIGH-3, F-M11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,ok",
    [
        ("7203", True),
        ("130A0", True),
        ("Z" * 28, True),
        ("", False),
        ("Z" * 29, False),       # length cap
        ("7203|", False),        # pipe disallowed
        ("72-03", False),        # hyphen rejected (Phase 1 strict)
        ("７２０３", False),     # full-width digits non-ASCII
    ],
)
def test_is_valid_issue_code(code, ok):
    assert is_valid_issue_code(code) is ok


# ---------------------------------------------------------------------------
# Streaming parser — single-chunk happy path
# ---------------------------------------------------------------------------


def _make_record(d: dict) -> str:
    """Tachibana CLMEventDownload puts one JSON object per record terminated by `}`."""
    return json.dumps(d, ensure_ascii=False)


def _terminator() -> str:
    return _make_record({"sCLMID": CLM_EVENT_DOWNLOAD_COMPLETE})


def test_iter_records_single_chunk_with_terminator():
    body = _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "7203"})
    body += _terminator()
    records = list(iter_records_from_chunks([body.encode("shift_jis")]))
    assert len(records) == 1
    assert records[0]["sIssueCode"] == "7203"


def test_iter_records_stops_at_terminator_and_drops_remainder():
    """Anything after the terminator record must be ignored."""
    extra = _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "9999"})
    body = (
        _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "7203"})
        + _terminator()
        + extra
    )
    records = list(iter_records_from_chunks([body.encode("shift_jis")]))
    assert [r.get("sIssueCode") for r in records] == ["7203"]


# ---------------------------------------------------------------------------
# Chunk boundary edge cases (MEDIUM-C3-2)
# ---------------------------------------------------------------------------


def _split_at(b: bytes, idx: int) -> list[bytes]:
    return [b[:idx], b[idx:]]


def test_chunk_breaks_between_records_clean_boundary():
    rec_a = _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "7203"})
    rec_b = _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "9984"})
    body = (rec_a + rec_b + _terminator()).encode("shift_jis")
    cut = len(rec_a.encode("shift_jis"))  # exactly at `}` boundary of record A
    chunks = _split_at(body, cut)
    records = list(iter_records_from_chunks(chunks))
    assert [r.get("sIssueCode") for r in records] == ["7203", "9984"]


def test_chunk_breaks_just_before_terminator_brace():
    """Chunk split right before the closing `}` of the terminator record."""
    rec = _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "7203"})
    term = _terminator()
    body = (rec + term).encode("shift_jis")
    cut = len(body) - 1  # right before the very last `}`
    chunks = _split_at(body, cut)
    records = list(iter_records_from_chunks(chunks))
    assert [r.get("sIssueCode") for r in records] == ["7203"]


def test_chunk_breaks_in_middle_of_record():
    rec = _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "7203"})
    body = (rec + _terminator()).encode("shift_jis")
    cut = len(rec.encode("shift_jis")) - 5  # inside record A
    chunks = _split_at(body, cut)
    records = list(iter_records_from_chunks(chunks))
    assert [r.get("sIssueCode") for r in records] == ["7203"]


# ---------------------------------------------------------------------------
# Class-based parser API (mirror of iter_records_from_chunks for explicit feed)
# ---------------------------------------------------------------------------


def test_master_stream_parser_feed_and_done():
    parser = MasterStreamParser()
    parser.feed(_make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "7203"}).encode("shift_jis"))
    assert not parser.is_complete
    parser.feed(_terminator().encode("shift_jis"))
    assert parser.is_complete
    records = parser.records()
    assert [r.get("sIssueCode") for r in records] == ["7203"]


def test_master_stream_parser_skips_invalid_issue_code(caplog: pytest.LogCaptureFixture):
    """Records whose sIssueCode fails pre-validate are skipped with a warn log."""
    bad = _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "7203|BAD"})
    good = _make_record({"sCLMID": "CLMIssueMstKabu", "sIssueCode": "7203"})
    body = (bad + good + _terminator()).encode("shift_jis")

    parser = MasterStreamParser()
    with caplog.at_level(logging.WARNING):
        parser.feed(body)
    assert parser.is_complete
    issued = [r.get("sIssueCode") for r in parser.records()]
    assert issued == ["7203"]
    assert any("invalid issue code" in r.message for r in caplog.records)
