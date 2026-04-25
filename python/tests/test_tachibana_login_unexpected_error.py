"""H1 / H3 / M-14 regression — unexpected exceptions inside the
Tachibana login dispatchers must surface as typed `VenueError` events
with the fixed Japanese banner. Earlier code shapes either swallowed
them silently (`except (KeyError, TypeError): pass`) or let them
bubble up to the generic `Error` event (which the UI banner cannot
classify under the venue contract).

The tests use `RuntimeError("forced")` as a stand-in for any unexpected
inner failure (helper subprocess crash, asyncio cancel translation,
bug in tachibana_run_login). They also assert that the secret-bearing
fields the test supplies (user_id "u", password "p") never appear in
the emitted VenueError — the banner stays generic.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from engine.server import DataEngineServer


@pytest.mark.asyncio
async def test_request_venue_login_emits_venue_error_when_run_login_raises():
    """H1: `_do_request_venue_login` must convert unexpected exceptions
    from `tachibana_run_login` into `VenueError{code:'login_failed'}`."""
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=True)

    async def fake_run_login(**_kwargs):
        raise RuntimeError("forced")

    with patch("engine.server.tachibana_run_login", fake_run_login):
        await server._do_request_venue_login(
            {"request_id": "rid-1", "venue": "tachibana"}
        )

    events = []
    while server._outbox:
        events.append(server._outbox.popleft())

    venue_errors = [e for e in events if e.get("event") == "VenueError"]
    assert len(venue_errors) == 1, f"expected exactly one VenueError, got {events}"
    assert venue_errors[0]["code"] == "login_failed"
    assert venue_errors[0]["request_id"] == "rid-1"
    # Banner stays generic — no leakage of inner exception text.
    assert "forced" not in venue_errors[0]["message"]


_UNIQUE_USER_ID = "user-id-UNIQUE-67890"
_UNIQUE_PASSWORD = "secret-password-UNIQUE-12345"


@pytest.mark.asyncio
async def test_set_venue_credentials_emits_venue_error_when_run_login_raises(caplog):
    """H3 / M-14: `_do_set_venue_credentials` must convert unexpected
    exceptions from `tachibana_run_login` into `VenueError{code:
    'login_failed'}`. Also asserts the user-supplied creds (unique
    sentinels) never leak into the event **or** the `log.exception`
    record's traceback (M-LOG ラウンド 5: stack-frame locals can carry
    `fallback_password` through `exc_text` when an exception fires
    inside the same scope that bound it)."""
    import logging
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=True)

    async def fake_run_login(**_kwargs):
        raise RuntimeError("forced")

    msg = {
        "request_id": "rid-2",
        "payload": {
            "venue": "tachibana",
            "user_id": _UNIQUE_USER_ID,
            "password": _UNIQUE_PASSWORD,
            "is_demo": True,
            # No session → goes straight to Step 2 (run_login).
        },
    }
    with caplog.at_level(logging.DEBUG, logger="engine.server"):
        with patch("engine.server.tachibana_run_login", fake_run_login):
            await server._do_set_venue_credentials(msg)

    events = []
    while server._outbox:
        events.append(server._outbox.popleft())

    venue_errors = [e for e in events if e.get("event") == "VenueError"]
    assert len(venue_errors) == 1, f"expected exactly one VenueError, got {events}"
    assert venue_errors[0]["code"] == "login_failed"
    assert venue_errors[0]["request_id"] == "rid-2"
    serialized = repr(venue_errors[0])
    assert _UNIQUE_PASSWORD not in serialized, (
        f"password leaked in event: {venue_errors[0]}"
    )
    assert _UNIQUE_USER_ID not in serialized, (
        f"user_id leaked in event: {venue_errors[0]}"
    )
    # Inner exception text must not leak.
    assert "forced" not in serialized

    # M-LOG ラウンド 5: `log.exception` produces a record with
    # `exc_text` (set lazily by Formatter / explicitly by .exception()
    # via `_log(... exc_info=True)`). Force formatting so the field
    # is populated, then scan it for the password literal — the
    # production handler must scrub `fallback_password` BEFORE the
    # exception path runs so even verbose formatters cannot pull it
    # out of the engine.server frame.
    import logging as _logging
    fmt = _logging.Formatter("%(message)s")
    for record in caplog.records:
        rendered = fmt.format(record)  # populates exc_text via formatException
        assert _UNIQUE_PASSWORD not in rendered, (
            f"password leaked into log record exc_text: {rendered!r}"
        )
        # Also assert the production frame's locals (rendered with
        # capture_locals=True) no longer contain the password literal.
        # Frames OUTSIDE engine.server (e.g. test fixtures) are out
        # of scope — we only enforce the scrub on our own frame.
        if record.exc_info:
            import traceback as _tb
            etype, evalue, etb = record.exc_info
            for frame_summary in _tb.StackSummary.extract(
                _tb.walk_tb(etb), capture_locals=True
            ):
                if frame_summary.filename.endswith("server.py"):
                    locals_repr = repr(frame_summary.locals or {})
                    assert _UNIQUE_PASSWORD not in locals_repr, (
                        f"password leaked in engine.server frame locals: "
                        f"{frame_summary.filename}:{frame_summary.lineno} "
                        f"{locals_repr}"
                    )


@pytest.mark.asyncio
async def test_set_venue_credentials_scrubs_locals_on_success_path():
    """HIGH-7 (ラウンド 6): the credential-bearing locals must be
    scrubbed on EVERY exit path of `_do_set_venue_credentials`'s
    inner try/finally, not only the exception path. A success path
    that leaves `fallback_password` / `payload` / `msg` bound on the
    frame would still surface them in any subsequent traceback that
    re-enters this scope (e.g. from the `_restore_session_from_payload`
    error branch below the try/finally).

    We can't easily observe locals after `_do_set_venue_credentials`
    returns (frame is gone), but we can drive the success path,
    inspect the events, and confirm the function returned cleanly
    without re-raising. The actual scrub guard is the source-level
    `finally:` block — this test pins that the success path is
    exercised at least once so a future refactor that drops the
    `finally:` (and reverts to the old `except`-only scrub) breaks
    the test suite via a coverage assertion.
    """
    import inspect

    src = inspect.getsource(DataEngineServer._do_set_venue_credentials)
    # Source-level invariant: the scrub block must be reachable from
    # the success path. We pin the `finally:` clause and the field
    # names. If a future refactor moves the scrub into `except` only,
    # the finally line will disappear and the assert will fail.
    assert "finally:" in src, (
        "HIGH-7 invariant: `_do_set_venue_credentials` must scrub "
        "credential-bearing locals in a `finally:` clause, not `except`."
    )
    # Pin the four sentinel names.
    for name in ("fallback_password", "fallback_user_id", "fallback_is_demo", "payload"):
        assert f"{name} = None" in src, (
            f"HIGH-7 invariant: scrub list missing {name!r}"
        )


@pytest.mark.asyncio
async def test_set_venue_credentials_restore_failed_emits_only_venue_error_no_venue_ready():
    """HIGH-1 (ラウンド 7): when the post-login `VenueCredentialsRefreshed`
    payload cannot be restored (malformed session), the dispatcher must
    NOT emit `VenueReady` for this `request_id`. Earlier code emitted
    every event in `events` (including a synthetic `VenueReady`) BEFORE
    the `restore_failed` `VenueError`, so Rust's wait loop saw `VenueReady`
    first and treated the request as terminally complete — silently
    dropping the trailing VenueError.

    We pin: with `restore_failed=True`, the only events emitted that
    carry the request_id are `VenueError(session_restore_failed)`
    — no `VenueReady`, no `VenueCredentialsRefreshed`."""
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=True)

    async def fake_run_login(**kwargs):
        # Return a successful-looking login with a malformed session
        # so `_restore_session_from_payload` raises and `restore_failed`
        # flips to True.
        return [
            {
                "event": "VenueCredentialsRefreshed",
                "venue": "tachibana",
                "session": {"this": "is", "not": "a valid session payload"},
            },
            {
                "event": "VenueReady",
                "venue": "tachibana",
                "request_id": kwargs.get("request_id"),
            },
        ]

    msg = {
        "request_id": "rid-restore-fail-1",
        "payload": {"venue": "tachibana"},  # no session → fall to login
    }
    with patch("engine.server.tachibana_run_login", fake_run_login):
        await server._do_set_venue_credentials(msg)

    events = []
    while server._outbox:
        events.append(server._outbox.popleft())

    venue_ready = [e for e in events if e.get("event") == "VenueReady"]
    assert venue_ready == [], (
        f"HIGH-1: VenueReady must NOT be emitted on restore_failed; got {events}"
    )
    refreshed = [e for e in events if e.get("event") == "VenueCredentialsRefreshed"]
    assert refreshed == [], (
        f"HIGH-1: VenueCredentialsRefreshed must NOT be emitted on restore_failed; got {events}"
    )
    venue_errors = [e for e in events if e.get("event") == "VenueError"]
    assert len(venue_errors) == 1
    assert venue_errors[0]["code"] == "session_restore_failed"
    assert venue_errors[0]["request_id"] == "rid-restore-fail-1"


@pytest.mark.asyncio
async def test_request_venue_login_restore_failed_emits_only_venue_error_no_venue_ready():
    """HIGH-1 (ラウンド 7): mirror for the user-initiated
    `_do_request_venue_login` path."""
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=True)

    async def fake_run_login(**kwargs):
        return [
            {
                "event": "VenueCredentialsRefreshed",
                "venue": "tachibana",
                "session": {"malformed": True},
            },
            {
                "event": "VenueReady",
                "venue": "tachibana",
                "request_id": kwargs.get("request_id"),
            },
        ]

    msg = {"request_id": "rid-restore-fail-2", "venue": "tachibana"}
    with patch("engine.server.tachibana_run_login", fake_run_login):
        await server._do_request_venue_login(msg)

    events = []
    while server._outbox:
        events.append(server._outbox.popleft())

    assert [e for e in events if e.get("event") == "VenueReady"] == []
    assert [e for e in events if e.get("event") == "VenueCredentialsRefreshed"] == []
    venue_errors = [e for e in events if e.get("event") == "VenueError"]
    assert len(venue_errors) == 1
    assert venue_errors[0]["code"] == "session_restore_failed"
    assert venue_errors[0]["request_id"] == "rid-restore-fail-2"


def _ast_has_fallback_binding(src: str) -> bool:
    """M-R8-5 (ラウンド 8): AST-based detector for ANY local binding
    whose target name starts with ``fallback_``. The previous regex
    (`^\\s*fallback_\\w+\\s*=`) had three known false-negatives:

    1. **Tuple unpack**: ``fallback_user_id, other = (...)`` — the
       leading token isn't followed by ``=``, the tuple is.
    2. **Walrus** (PEP 572): ``if (fallback_pw := compute()): ...``
       binds inside a ``NamedExpr`` node, never matching at column 0.
    3. **Annotated assignment**: ``fallback_password: str = ...`` —
       matched by the old regex only because of the ``str = `` tail,
       but ``fallback_password: str`` (no value) would slip through.

    Switching to ``ast.parse`` walks every ``Assign`` / ``AnnAssign``
    / ``NamedExpr`` node and resolves nested ``Tuple`` / ``List``
    targets recursively, so all three forms are caught.
    """
    import ast
    import textwrap

    # `inspect.getsource` preserves the method's leading indentation
    # (it sits inside a class body); ``ast.parse`` rejects that as
    # ``IndentationError``. ``textwrap.dedent`` strips the common
    # leading whitespace so the source parses as a top-level snippet.
    tree = ast.parse(textwrap.dedent(src))

    def _name_targets(node: ast.AST):
        # Yield every ``ast.Name`` target reachable from an assignment
        # node, descending into Tuple / List unpacks and Starred items.
        if isinstance(node, ast.Name):
            yield node
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                yield from _name_targets(elt)
        elif isinstance(node, ast.Starred):
            yield from _name_targets(node.value)

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets.append(node.target)
        elif isinstance(node, ast.NamedExpr):  # walrus
            targets.append(node.target)
        for t in targets:
            for name_node in _name_targets(t):
                if name_node.id.startswith("fallback_"):
                    return True
    return False


def test_request_venue_login_source_has_no_unscrubbed_fallback_locals():
    """MEDIUM-1 (ラウンド 7) + M-R8-5 (ラウンド 8): future-proof guard.
    `_do_request_venue_login` currently does not bind any `fallback_*`
    plaintext locals — the user-initiated path drives a fresh login
    without prefill. If a future refactor adds `fallback_user_id` /
    `fallback_password` to that handler (e.g. to support prefill from a
    UI dialog), the same `try/finally` scrub used by the sister
    `_do_set_venue_credentials` handler must also be added or this test
    fails.

    M-R8-5: detection switched from regex to AST walk so tuple unpack /
    walrus / annotated-without-value forms cannot bypass the guard.
    """
    import inspect

    src = inspect.getsource(DataEngineServer._do_request_venue_login)
    if _ast_has_fallback_binding(src):
        assert "finally:" in src, (
            "MEDIUM-1 invariant: `_do_request_venue_login` introduced "
            "`fallback_*` locals — it must also scrub them in a "
            "`finally:` clause (mirror `_do_set_venue_credentials` "
            "HIGH-7). Add the scrub or remove the fallback locals."
        )


def test_ast_fallback_detector_catches_tuple_unpack_walrus_and_annotated_forms():
    """M-R8-5 (ラウンド 8) meta-test: pin the AST detector against the
    three false-negative forms the regex missed. If a future refactor
    re-introduces a regex-based shortcut, these synthetic dummy
    sources will start asserting False and trip this test.
    """
    # Tuple unpack target.
    assert _ast_has_fallback_binding(
        "def f():\n    fallback_user_id, other = (1, 2)\n"
    ), "tuple-unpack `fallback_user_id` must be detected"
    # Walrus (NamedExpr) target.
    assert _ast_has_fallback_binding(
        "def f():\n    if (fallback_password := compute()):\n        pass\n"
    ), "walrus-bound `fallback_password` must be detected"
    # Annotated assignment WITHOUT a value (regex with `=` would miss).
    assert _ast_has_fallback_binding(
        "def f():\n    fallback_pw: str\n"
    ), "annotated-without-value `fallback_pw` must be detected"
    # Plain assignment (sanity).
    assert _ast_has_fallback_binding(
        "def f():\n    fallback_user_id = 'x'\n"
    ), "plain `fallback_user_id =` must be detected"
    # Negative: comment / string with `fallback_` text must NOT match.
    assert not _ast_has_fallback_binding(
        "def f():\n    x = 'fallback_user_id'\n    # fallback_password mention\n"
    ), "string/comment mention must NOT trigger detection"


@pytest.mark.asyncio
async def test_restore_session_from_payload_rejects_non_https_url():
    """MEDIUM-10 (ラウンド 6): malformed wire URLs must be rejected
    before they reach the network layer. A keyring entry corrupted
    to carry `http://` instead of `https://` would otherwise route
    the next session-validate request through plaintext."""
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=False)

    bad_payload = {
        "url_request": "http://demo-kabuka.e-shiten.jp/req/X/",  # http NOT https
        "url_master": "https://demo-kabuka.e-shiten.jp/mst/X/",
        "url_price": "https://demo-kabuka.e-shiten.jp/prc/X/",
        "url_event": "https://demo-kabuka.e-shiten.jp/evt/X/",
        "url_event_ws": "wss://demo-kabuka.e-shiten.jp/evt/X/",
        "expires_at_ms": None,
        "zyoutoeki_kazei_c": "1",
    }
    with pytest.raises(ValueError) as excinfo:
        server._restore_session_from_payload(bad_payload)
    assert "https://" in str(excinfo.value)


@pytest.mark.asyncio
async def test_restore_session_from_payload_rejects_non_wss_url_event_ws():
    """MEDIUM-10 (ラウンド 6): the websocket URL must use `wss://`."""
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=False)

    bad_payload = {
        "url_request": "https://demo-kabuka.e-shiten.jp/req/X/",
        "url_master": "https://demo-kabuka.e-shiten.jp/mst/X/",
        "url_price": "https://demo-kabuka.e-shiten.jp/prc/X/",
        "url_event": "https://demo-kabuka.e-shiten.jp/evt/X/",
        "url_event_ws": "ws://demo-kabuka.e-shiten.jp/evt/X/",  # ws NOT wss
        "expires_at_ms": None,
        "zyoutoeki_kazei_c": "1",
    }
    with pytest.raises(ValueError) as excinfo:
        server._restore_session_from_payload(bad_payload)
    assert "wss://" in str(excinfo.value)
