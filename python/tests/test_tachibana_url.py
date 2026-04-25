"""TDD: tachibana_url module — REQUEST/EVENT URL builders and percent-encoding.

Covers SKILL.md R2 (URL form), R5 (sJsonOfmt required), R9 (30-char replacement),
F-M6b (control char reject), MEDIUM-C4 (NewType builder guard), MEDIUM-D4
(replace_urlecnode edge cases), and M7 (multibyte fixture).
"""

from __future__ import annotations

import json

import pytest

from engine.exchanges.tachibana_url import (
    EventUrl,
    MasterUrl,
    PriceUrl,
    RequestUrl,
    build_event_url,
    build_request_url,
    func_replace_urlecnode,
)


# ---------------------------------------------------------------------------
# func_replace_urlecnode (R9, MEDIUM-D4, M7)
# ---------------------------------------------------------------------------


def test_replace_urlecnode_each_target_char():
    """All 29 listed target chars are replaced to the documented %xx codes."""
    table = {
        " ": "%20", "!": "%21", '"': "%22", "#": "%23", "$": "%24",
        "%": "%25", "&": "%26", "'": "%27", "(": "%28", ")": "%29",
        "*": "%2A", "+": "%2B", ",": "%2C", "/": "%2F", ":": "%3A",
        ";": "%3B", "<": "%3C", "=": "%3D", ">": "%3E", "?": "%3F",
        "@": "%40", "[": "%5B", "]": "%5D", "^": "%5E", "`": "%60",
        "{": "%7B", "|": "%7C", "}": "%7D", "~": "%7E",
    }
    for ch, encoded in table.items():
        assert func_replace_urlecnode(ch) == encoded, f"failed for {ch!r}"


def test_replace_urlecnode_empty():
    assert func_replace_urlecnode("") == ""


def test_replace_urlecnode_passthrough_alnum():
    assert func_replace_urlecnode("abcXYZ0189") == "abcXYZ0189"


def test_replace_urlecnode_full_roundtrip():
    """A string containing every target char encodes losslessly via urllib unquote."""
    from urllib.parse import unquote

    src = " !\"#$%&'()*+,/:;<=>?@[]^`{|}~ABC123"
    encoded = func_replace_urlecnode(src)
    # urllib should decode our percent-encoding back to the original.
    assert unquote(encoded) == src


def test_replace_urlecnode_multibyte_shift_jis():
    """Multibyte: per SKILL.md R9, multibyte chars pass through `func_replace_urlecnode`
    untouched — there are no multibyte targets in the 29-char replacement table.
    """
    out = func_replace_urlecnode("トヨタ自動車 7203")
    # Only the ASCII space is in the table; multibyte stays intact.
    assert out == "トヨタ自動車%207203"


# ---------------------------------------------------------------------------
# build_request_url (R2, R5, HIGH-C1)
# ---------------------------------------------------------------------------


def test_build_request_url_requires_sJsonOfmt_kwarg():
    """`sJsonOfmt` must be a required keyword-only argument."""
    base = RequestUrl("https://example.invalid/v4r8/request/")
    with pytest.raises(TypeError):
        # missing sJsonOfmt entirely → TypeError from python signature
        build_request_url(base, {"sCLMID": "X"})  # type: ignore[call-arg]


def test_build_request_url_rejects_unknown_sJsonOfmt():
    base = RequestUrl("https://example.invalid/v4r8/request/")
    with pytest.raises(ValueError):
        build_request_url(base, {"sCLMID": "X"}, sJsonOfmt="9")


def test_build_request_url_format_5():
    base = RequestUrl("https://example.invalid/v4r8/request/")
    url = build_request_url(base, {"sCLMID": "CLMOrderList", "p_no": "1"}, sJsonOfmt="5")
    assert url.startswith("https://example.invalid/v4r8/request/?")
    # The query is a percent-encoded JSON object; sJsonOfmt was injected.
    query = url.split("?", 1)[1]
    # We can decode it back to JSON.
    from urllib.parse import unquote

    obj = json.loads(unquote(query))
    assert obj["sJsonOfmt"] == "5"
    assert obj["sCLMID"] == "CLMOrderList"


def test_build_request_url_format_4_for_master_download():
    base = MasterUrl("https://example.invalid/v4r8/master/")
    url = build_request_url(base, {"sCLMID": "CLMEventDownload"}, sJsonOfmt="4")
    from urllib.parse import unquote

    obj = json.loads(unquote(url.split("?", 1)[1]))
    assert obj["sJsonOfmt"] == "4"


def test_build_request_url_rejects_event_url_type():
    """EventUrl must not be accepted by build_request_url (MEDIUM-C4)."""
    bad = EventUrl("https://example.invalid/v4r8/event/")
    with pytest.raises(TypeError):
        build_request_url(bad, {"sCLMID": "X"}, sJsonOfmt="5")  # type: ignore[arg-type]


def test_build_request_url_accepts_price_url():
    base = PriceUrl("https://example.invalid/v4r8/price/")
    url = build_request_url(base, {"sCLMID": "CLMMfdsGetMarketPrice"}, sJsonOfmt="5")
    assert "CLMMfdsGetMarketPrice" in url


def test_build_request_url_rejects_control_chars_in_value():
    """Control chars must be rejected in JSON values (F-M6b)."""
    base = RequestUrl("https://example.invalid/v4r8/request/")
    for bad in ["\n", "\t", "\r", "\x01", "\x02", "\x03"]:
        with pytest.raises(ValueError):
            build_request_url(
                base, {"sCLMID": "X", "evil": f"a{bad}b"}, sJsonOfmt="5"
            )


# ---------------------------------------------------------------------------
# build_event_url (R2 example, F-M6b)
# ---------------------------------------------------------------------------


def test_build_event_url_keyvalue_form():
    base = EventUrl("https://example.invalid/v4r8/event/")
    url = build_event_url(
        base,
        {
            "p_evt_cmd": "FD,KP,ST",
            "p_eno": "0",
            "p_rid": "22",
            "p_board_no": "1000",
        },
    )
    # Must be key=value form, not JSON.
    assert url.startswith("https://example.invalid/v4r8/event/?")
    query = url.split("?", 1)[1]
    pairs = dict(p.split("=", 1) for p in query.split("&"))
    # Comma-containing values must be percent-encoded (',' → %2C).
    assert pairs["p_evt_cmd"] == "FD%2CKP%2CST"
    assert pairs["p_eno"] == "0"
    assert pairs["p_rid"] == "22"


def test_build_event_url_rejects_request_url_type():
    bad = RequestUrl("https://example.invalid/v4r8/request/")
    with pytest.raises(TypeError):
        build_event_url(bad, {"p_eno": "0"})  # type: ignore[arg-type]


def test_build_event_url_rejects_control_chars():
    base = EventUrl("https://example.invalid/v4r8/event/")
    for bad in ["\n", "\t", "\r", "\x01", "\x02", "\x03"]:
        with pytest.raises(ValueError):
            build_event_url(base, {"p_evt_cmd": f"FD{bad}KP"})
