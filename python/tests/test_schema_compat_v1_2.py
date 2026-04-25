"""HIGH-D2-2: schema 1.2 cross-language compat for the 7 Tachibana variants.

For each of the 7 variants introduced in schema 1.2 (2 commands + 5 events),
serialize a representative payload from one side (or pin the JSON shape) and
verify the pydantic model on the Python side accepts it via
``model_validate_json``. The companion Rust test
``engine-client/tests/schema_v1_2_roundtrip.rs`` checks the reverse direction.

Variants (per implementation-plan T0.2 stage B):

1. ``SetVenueCredentials`` (command)
2. ``RequestVenueLogin`` (command)
3. ``VenueReady`` (event)
4. ``VenueError`` (event)
5. ``VenueCredentialsRefreshed`` (event)
6. ``VenueLoginStarted`` (event)
7. ``VenueLoginCancelled`` (event)
"""

from __future__ import annotations

import json

import pytest

from engine.schemas import (
    RequestVenueLogin,
    SetVenueCredentials,
    TachibanaSessionWire,
    VenueCredentialsPayload,
    VenueCredentialsRefreshed,
    VenueError,
    VenueLoginCancelled,
    VenueLoginStarted,
    VenueReady,
)


VALID_UUID = "11111111-2222-4333-8444-555555555555"


def _rust_serialized(variant: str) -> str:
    """JSON shape produced by the Rust DTO ``serde_json::to_string`` output.

    Field order and names match ``engine-client/src/dto.rs``.
    """
    if variant == "SetVenueCredentials":
        return json.dumps({
            "op": "SetVenueCredentials",
            "request_id": VALID_UUID,
            "payload": {
                "venue": "tachibana",
                "user_id": "alice",
                "password": "p4ss",
                "second_password": None,
                "is_demo": True,
                "session": None,
            },
        })
    if variant == "RequestVenueLogin":
        return json.dumps({
            "op": "RequestVenueLogin",
            "request_id": VALID_UUID,
            "venue": "tachibana",
        })
    if variant == "VenueReady":
        return json.dumps({
            "event": "VenueReady",
            "venue": "tachibana",
            "request_id": VALID_UUID,
        })
    if variant == "VenueError":
        return json.dumps({
            "event": "VenueError",
            "venue": "tachibana",
            "request_id": VALID_UUID,
            "code": "session_expired",
            "message": "再ログインしてください",
        })
    if variant == "VenueCredentialsRefreshed":
        return json.dumps({
            "event": "VenueCredentialsRefreshed",
            "venue": "tachibana",
            "session": {
                "url_request": "https://example.invalid/req",
                "url_master": "https://example.invalid/m",
                "url_price": "https://example.invalid/p",
                "url_event": "https://example.invalid/e",
                "url_event_ws": "wss://example.invalid/ws",
                "expires_at_ms": 1700000000000,
                "zyoutoeki_kazei_c": "0",
            },
        })
    if variant == "VenueLoginStarted":
        return json.dumps({
            "event": "VenueLoginStarted",
            "venue": "tachibana",
            "request_id": VALID_UUID,
        })
    if variant == "VenueLoginCancelled":
        return json.dumps({
            "event": "VenueLoginCancelled",
            "venue": "tachibana",
            "request_id": None,
        })
    raise AssertionError(f"unknown variant {variant!r}")


VARIANT_TO_MODEL = {
    "SetVenueCredentials": SetVenueCredentials,
    "RequestVenueLogin": RequestVenueLogin,
    "VenueReady": VenueReady,
    "VenueError": VenueError,
    "VenueCredentialsRefreshed": VenueCredentialsRefreshed,
    "VenueLoginStarted": VenueLoginStarted,
    "VenueLoginCancelled": VenueLoginCancelled,
}


@pytest.mark.parametrize("variant", list(VARIANT_TO_MODEL.keys()))
def test_pydantic_accepts_rust_serialized_v1_2_variants(variant: str) -> None:
    """Rust-side serialization → Python pydantic deserialization."""
    model = VARIANT_TO_MODEL[variant]
    raw = _rust_serialized(variant)
    obj = model.model_validate_json(raw)
    # spot-check: the discriminator must round-trip
    if "op" in raw:
        assert obj.op == variant
    else:
        assert obj.event == variant


def test_pydantic_payload_has_tachibana_tag() -> None:
    """The ``venue`` discriminator on the payload must round-trip exactly
    so the Rust side can ``serde(tag = "venue")`` route it back."""
    raw = _rust_serialized("SetVenueCredentials")
    obj = SetVenueCredentials.model_validate_json(raw)
    assert obj.payload.venue == "tachibana"


def test_python_dump_matches_rust_expected_shape() -> None:
    """Reverse direction (Python → JSON) for the event side. We pin
    representative payloads and verify ``model_dump`` produces the keys
    Rust expects to deserialize.
    """
    ready = VenueReady(venue="tachibana", request_id=VALID_UUID)
    dumped = ready.model_dump()
    assert dumped == {
        "event": "VenueReady",
        "venue": "tachibana",
        "request_id": VALID_UUID,
    }

    err = VenueError(
        venue="tachibana",
        request_id=None,
        code="login_failed",
        message="認証失敗",
    )
    assert err.model_dump()["request_id"] is None
    assert err.model_dump()["code"] == "login_failed"


def test_session_wire_python_dump_uses_string_urls() -> None:
    """The ``TachibanaSessionWire`` payload must serialize URL fields as
    bare strings (not objects), because the Rust ``Zeroizing<String>``
    deserializer expects plain JSON strings.
    """
    s = TachibanaSessionWire(
        url_request="https://example.invalid/req",
        url_master="https://example.invalid/m",
        url_price="https://example.invalid/p",
        url_event="https://example.invalid/e",
        url_event_ws="wss://example.invalid/ws",
        expires_at_ms=None,
        zyoutoeki_kazei_c="0",
    )
    dumped = s.model_dump()
    for key in ("url_request", "url_master", "url_price", "url_event", "url_event_ws"):
        assert isinstance(dumped[key], str)


def test_payload_venue_tag_string_value_matches_rust() -> None:
    """``VenueCredentialsPayload.venue`` is the stable retain-tag (M2) and
    must equal the string Rust returns from ``venue_tag()``."""
    payload = VenueCredentialsPayload(
        venue="tachibana",
        user_id="alice",
        password="p4ss",
        second_password=None,
        is_demo=True,
        session=None,
    )
    assert payload.venue == "tachibana"
