"""Tachibana e-shiten REQUEST/EVENT URL builders.

This module is the **single source** for Tachibana URL construction. Standard
library URL encoders (`urllib.parse.quote`, `urlencode`, `httpx.URL(...)`'s
auto-query-encoding) MUST NOT be used: Tachibana mandates the bespoke 30-ish
character replacement table from SKILL.md R9 (`func_replace_urlecnode`), and
delegating to standard encoders breaks the contract.

NewType-style wrappers tag each virtual URL by purpose so that builders can
refuse the wrong endpoint at function boundaries (MEDIUM-C4):

* `RequestUrl` / `MasterUrl` / `PriceUrl`  — accepted by `build_request_url`
* `EventUrl`                               — accepted by `build_event_url`

The HTTP/REST scheme of the four virtual URLs is `https://`; only
`sUrlEventWebSocket` is `wss://` (validated in `tachibana_auth`, not here).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

# ---------------------------------------------------------------------------
# URL NewType wrappers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _BaseUrl:
    value: str

    def __str__(self) -> str:  # convenience for `f"{url}"`
        return self.value


class RequestUrl(_BaseUrl):
    """`sUrlRequest` — business REQUEST endpoint."""


class MasterUrl(_BaseUrl):
    """`sUrlMaster` — master data REQUEST endpoint."""


class PriceUrl(_BaseUrl):
    """`sUrlPrice` — quote-snapshot REQUEST endpoint."""


class EventUrl(_BaseUrl):
    """`sUrlEvent` / `sUrlEventWebSocket` — EVENT push endpoint."""


class AuthUrl(_BaseUrl):
    """`{BASE_URL}` — pre-login auth endpoint base.

    Distinct from the four virtual URLs above: it is **not** issued by the
    server but is the static base host string. The login builder appends
    ``auth/?{percent-encoded-JSON}`` to it.
    """


# ---------------------------------------------------------------------------
# Static base URLs (F-L1, single-source rule)
# ---------------------------------------------------------------------------
#
# These two literals are the ONLY place in the repository where the
# ``kabuka.e-shiten.jp`` host may appear (SKILL.md F-L1). The Phase 1 secret
# scanner (T7) allowlists this file by name; any other module touching the
# host string will fail pre-commit / CI. Demo and prod both ride the
# ``e_api_v4r8`` path — Phase 1 is API-version compatible with v4r7 docs.

BASE_URL_PROD: AuthUrl = AuthUrl("https://kabuka.e-shiten.jp/e_api_v4r8/")
BASE_URL_DEMO: AuthUrl = AuthUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/")


# Allowed `sJsonOfmt` values (R5). "5" = browser-friendly + named keys (default
# for REQUEST); "4" = one-record-per-line (only for `CLMEventDownload`).
_ALLOWED_OFMT = frozenset({"4", "5"})

# C0 control characters (U+0000..U+001F) must never appear inside a URL we
# send. Tachibana's server rejects them, and `\n` / `\t` in particular have
# caused production incidents before (SKILL.md EVENT 規約 / F-M6b). We reject
# the entire C0 block — listing only `\n\t\r\x01..\x03` would let `\x00` and
# `\x04..\x1F` slip through with no benefit to legitimate callers.
_FORBIDDEN_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20))


# ---------------------------------------------------------------------------
# func_replace_urlecnode (SKILL.md R9)
# ---------------------------------------------------------------------------

# 29-char replacement table copied verbatim from
# `samples/e_api_login_tel.py/e_api_login_tel.py:func_replace_urlecnode`.
_REPLACE_TABLE: dict[str, str] = {
    " ": "%20",
    "!": "%21",
    '"': "%22",
    "#": "%23",
    "$": "%24",
    "%": "%25",
    "&": "%26",
    "'": "%27",
    "(": "%28",
    ")": "%29",
    "*": "%2A",
    "+": "%2B",
    ",": "%2C",
    "/": "%2F",
    ":": "%3A",
    ";": "%3B",
    "<": "%3C",
    "=": "%3D",
    ">": "%3E",
    "?": "%3F",
    "@": "%40",
    "[": "%5B",
    "]": "%5D",
    "^": "%5E",
    "`": "%60",
    "{": "%7B",
    "|": "%7C",
    "}": "%7D",
    "~": "%7E",
}


def func_replace_urlecnode(s: str) -> str:
    """Apply the Tachibana percent-encoding table to `s`.

    Multibyte chars are passed through unchanged — the table only contains
    ASCII targets. SKILL.md R9 notes that production multibyte URLs are not
    yet exercised; if needed, callers should encode to Shift-JIS upstream.
    Standard library URL encoders MUST NOT be used in their place.
    """
    return "".join(_REPLACE_TABLE.get(ch, ch) for ch in s)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _check_no_control_chars(values: list[str]) -> None:
    for v in values:
        for ch in v:
            if ch in _FORBIDDEN_CONTROL_CHARS:
                raise ValueError(
                    f"control character {ch!r} is forbidden inside Tachibana "
                    "URLs (SKILL.md EVENT 規約 / F-M6b)"
                )


def build_request_url(
    base: RequestUrl | MasterUrl | PriceUrl,
    json_obj: Mapping[str, object],
    *,
    sJsonOfmt: str,
) -> str:
    """Build a REQUEST URL: ``{base}?{percent-encoded JSON}``.

    Standard library URL encoders MUST NOT be used to assemble the query.
    Tachibana's bespoke replacement table (SKILL.md R9) is applied to the
    serialized JSON via `func_replace_urlecnode`.

    `sJsonOfmt` is a required keyword argument (HIGH-C1, R5 enforcement):
    callers pass `"5"` for normal REQUEST/master-fetch flows and `"4"` only
    for `CLMEventDownload` (one-record-per-line streaming).

    Raises:
        TypeError: if `base` is not a request/master/price URL wrapper.
        ValueError: if `sJsonOfmt` is not in {"4", "5"} or any value contains
            a forbidden control character.
    """
    if not isinstance(base, (RequestUrl, MasterUrl, PriceUrl)):
        raise TypeError(
            f"build_request_url expects RequestUrl/MasterUrl/PriceUrl, got {type(base).__name__}"
        )
    if sJsonOfmt not in _ALLOWED_OFMT:
        raise ValueError(
            f"sJsonOfmt must be '4' or '5' (R5), got {sJsonOfmt!r}"
        )

    payload: dict[str, object] = {**dict(json_obj), "sJsonOfmt": sJsonOfmt}

    # Accept only str / numeric scalars at the JSON value position. Booleans
    # (an int subclass) ride along harmlessly; lists / dicts / None would
    # bypass the control-char check, so they're rejected outright.
    for key, value in payload.items():
        if not isinstance(value, (str, int, float)):
            raise TypeError(
                f"build_request_url: value for {key!r} must be str/int/float "
                f"(got {type(value).__name__}); nested types are not supported"
            )
    string_values = [str(v) for v in payload.values()]
    string_values += [str(k) for k in payload.keys()]
    _check_no_control_chars(string_values)

    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{base.value}?{func_replace_urlecnode(serialized)}"


def build_auth_url(
    base: AuthUrl,
    json_obj: Mapping[str, object],
    *,
    sJsonOfmt: str = "5",
) -> str:
    """Build the pre-login auth URL: ``{base}auth/?{percent-encoded JSON}``.

    Distinct from `build_request_url` because the auth endpoint:

    * is reached via the static base URL rather than a virtual URL, and
    * adds the literal ``auth/`` path segment between the base and the query.

    `sJsonOfmt` defaults to ``"5"`` (R5 — browser-friendly, named keys) which
    is the only value the auth endpoint accepts. ``"4"`` is rejected because
    the login response must be a single JSON object, not one record per line.
    """
    if not isinstance(base, AuthUrl):
        raise TypeError(
            f"build_auth_url expects AuthUrl, got {type(base).__name__}"
        )
    if sJsonOfmt != "5":
        raise ValueError(
            f"auth endpoint requires sJsonOfmt='5' (R5), got {sJsonOfmt!r}"
        )

    payload: dict[str, object] = {**dict(json_obj), "sJsonOfmt": sJsonOfmt}

    for key, value in payload.items():
        if not isinstance(value, (str, int, float)):
            raise TypeError(
                f"build_auth_url: value for {key!r} must be str/int/float "
                f"(got {type(value).__name__}); nested types are not supported"
            )
    string_values = [str(v) for v in payload.values()]
    string_values += [str(k) for k in payload.keys()]
    _check_no_control_chars(string_values)

    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{base.value}auth/?{func_replace_urlecnode(serialized)}"


def build_event_url(base: EventUrl, params: Mapping[str, str]) -> str:
    """Build an EVENT URL: ``{base}?key=value&key=value``.

    The EVENT endpoint is the lone REQUEST/JSON exception (R2): Tachibana
    expects a conventional ``key=value`` query string with each value passed
    through `func_replace_urlecnode`. Control characters in any value are
    rejected (F-M6b).
    """
    if not isinstance(base, EventUrl):
        raise TypeError(
            f"build_event_url expects EventUrl, got {type(base).__name__}"
        )

    keys = [str(k) for k in params.keys()]
    values = [str(v) for v in params.values()]
    _check_no_control_chars(keys + values)

    parts = [f"{func_replace_urlecnode(k)}={func_replace_urlecnode(v)}"
             for k, v in zip(keys, values)]
    return f"{base.value}?{'&'.join(parts)}"
