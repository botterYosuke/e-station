"""TDD: tachibana_codec — Shift-JIS decode, EVENT frame parser, list normalizer."""

from __future__ import annotations

import pytest

from engine.exchanges.tachibana_codec import (
    decode_response_body,
    deserialize_tachibana_list,
    parse_event_frame,
)


# ---------------------------------------------------------------------------
# decode_response_body (R7)
# ---------------------------------------------------------------------------


def test_decode_response_body_ascii():
    assert decode_response_body(b"hello") == "hello"


def test_decode_response_body_japanese_shift_jis():
    expected = "トヨタ自動車"
    encoded = expected.encode("shift_jis")
    assert decode_response_body(encoded) == expected


def test_decode_response_body_invalid_byte_is_lenient():
    """Lone invalid SJIS bytes must not raise (errors='replace' or 'ignore')."""
    # 0xFF is not valid in SJIS first-byte position.
    out = decode_response_body(b"abc\xffdef")
    # The valid surroundings must survive.
    assert "abc" in out and "def" in out


# ---------------------------------------------------------------------------
# parse_event_frame (^A^B^C / \n)
# ---------------------------------------------------------------------------


def test_parse_event_frame_basic_pairs():
    """Items separated by ^A (\\x01), key/value by ^B (\\x02)."""
    data = "p_1_DPP\x021000\x01p_2_DPP\x021001"
    pairs = parse_event_frame(data)
    assert pairs == [("p_1_DPP", "1000"), ("p_2_DPP", "1001")]


def test_parse_event_frame_handles_trailing_separators():
    data = "p_1_DPP\x021000\x01"
    assert parse_event_frame(data) == [("p_1_DPP", "1000")]


def test_parse_event_frame_handles_value_with_caret_c():
    """Values may have ^C (\\x03) as inner sub-separator; we keep the raw value."""
    data = "p_1_FOO\x02a\x03b\x03c"
    pairs = parse_event_frame(data)
    assert pairs == [("p_1_FOO", "a\x03b\x03c")]


def test_parse_event_frame_skips_blank_segments():
    data = "\x01p_1_DPP\x021000\x01\x01"
    assert parse_event_frame(data) == [("p_1_DPP", "1000")]


def test_parse_event_frame_value_with_inner_kv_separator():
    """A value that itself contains ^B is kept intact past the first split."""
    data = "p_1_FOO\x02alpha\x02beta"
    pairs = parse_event_frame(data)
    assert pairs == [("p_1_FOO", "alpha\x02beta")]


def test_parse_event_frame_skips_items_without_value_separator():
    data = "loose_item\x01p_1_DPP\x021000"
    assert parse_event_frame(data) == [("p_1_DPP", "1000")]


# ---------------------------------------------------------------------------
# deserialize_tachibana_list (R8)
# ---------------------------------------------------------------------------


def test_deserialize_list_passthrough_actual_list():
    assert deserialize_tachibana_list([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_deserialize_list_empty_string_becomes_empty_list():
    assert deserialize_tachibana_list("") == []


def test_deserialize_list_none_becomes_empty_list():
    assert deserialize_tachibana_list(None) == []


def test_deserialize_list_rejects_non_list_non_empty_string():
    with pytest.raises(TypeError):
        deserialize_tachibana_list("not empty string")


def test_deserialize_list_rejects_unexpected_type():
    with pytest.raises(TypeError):
        deserialize_tachibana_list(42)  # type: ignore[arg-type]
