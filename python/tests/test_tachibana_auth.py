"""Tachibana auth flow tests (T2).

Pinned tests (per implementation-plan.md フェーズ T2):

* `test_login_request_uses_json_ofmt_five`           (MEDIUM-C3-1)
* `test_login_rejects_non_wss_event_url`             (MEDIUM-C3-3)
* `test_login_raises_unread_notices_when_kinsyouhou_flag_set` (HIGH-C2-1)
* `test_validate_session_uses_get_issue_detail_with_pinned_payload` (HIGH-D2)
* `test_session_expired_p_errno_2`                   (受け入れ ↑ + R6)
* `test_login_p_errno_minus_62_raises_login_error`
* `test_login_authentication_failure_raises_login_error`
* `test_startup_latch_*`                             (M3)

Mock URLs are matched with a regex so that `build_auth_url`'s bespoke
percent-encoded query (R9) does not have to be reproduced verbatim; the
asserts decode the captured `request.url` to inspect the JSON payload.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import unquote

import pytest
from pytest_httpx import HTTPXMock

from engine.exchanges.tachibana_auth import (
    StartupLatch,
    TachibanaSession,
    login,
    validate_session_on_startup,
)
from engine.exchanges.tachibana_helpers import (
    LoginError,
    PNoCounter,
    SessionExpiredError,
    TachibanaError,
    UnreadNoticesError,
)
from engine.exchanges.tachibana_url import (
    BASE_URL_DEMO,
    EventUrl,
    MasterUrl,
    PriceUrl,
    RequestUrl,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Test URLs are derived from BASE_URL_DEMO so the host literal lives only in
# `tachibana_url.py` (F-L1 / T7 secret_scan allowlist). Replicating the host
# string here would make the scanner have to allowlist test files too, which
# defeats the single-source guarantee.
_DEMO_BASE = BASE_URL_DEMO.value  # ends with "/"
_DEMO_HOST_PATH = _DEMO_BASE.removeprefix("https://").removesuffix("/")

_AUTH_URL_RE = re.compile(rf"^{re.escape(_DEMO_BASE)}auth/\?")
_VIRTUAL_REQUEST = f"{_DEMO_BASE}request/ND=/"
_VIRTUAL_MASTER = f"{_DEMO_BASE}master/ND=/"
_VIRTUAL_PRICE = f"{_DEMO_BASE}price/ND=/"
_VIRTUAL_EVENT = f"{_DEMO_BASE}event/ND=/"
_VIRTUAL_EVENT_WS = f"wss://{_DEMO_HOST_PATH}/event_ws/ND=/"
_MASTER_URL_RE = re.compile(rf"^{re.escape(_VIRTUAL_MASTER)}\?")


def _ok_login_payload(**overrides) -> dict:
    base = {
        "p_no": "1",
        "p_sd_date": "2026.04.25-10:00:00.000",
        "p_errno": "0",
        "p_err": "",
        "sCLMID": "CLMAuthLoginAck",
        "sResultCode": "0",
        "sResultText": "",
        "sZyoutoekiKazeiC": "1",
        "sKinsyouhouMidokuFlg": "0",
        "sUrlRequest": _VIRTUAL_REQUEST,
        "sUrlMaster": _VIRTUAL_MASTER,
        "sUrlPrice": _VIRTUAL_PRICE,
        "sUrlEvent": _VIRTUAL_EVENT,
        "sUrlEventWebSocket": _VIRTUAL_EVENT_WS,
    }
    base.update(overrides)
    return base


def _add_login_response(httpx_mock: HTTPXMock, payload: dict | str) -> None:
    """Tachibana returns Shift-JIS bytes; emulate that here."""
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    httpx_mock.add_response(
        url=_AUTH_URL_RE,
        method="GET",
        content=body.encode("shift_jis"),
    )


def _decode_query_json(url: str) -> dict:
    """Recover the JSON object from the bespoke percent-encoded query."""
    _, _, q = url.partition("?")
    return json.loads(unquote(q))


def _make_session() -> TachibanaSession:
    p = _ok_login_payload()
    return TachibanaSession(
        url_request=RequestUrl(p["sUrlRequest"]),
        url_master=MasterUrl(p["sUrlMaster"]),
        url_price=PriceUrl(p["sUrlPrice"]),
        url_event=EventUrl(p["sUrlEvent"]),
        url_event_ws=p["sUrlEventWebSocket"],
        zyoutoeki_kazei_c="1",
        expires_at_ms=None,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_login_returns_session_on_success(httpx_mock: HTTPXMock):
    _add_login_response(httpx_mock, _ok_login_payload())
    session = await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    assert isinstance(session, TachibanaSession)
    assert isinstance(session.url_master, MasterUrl)
    assert session.url_event_ws.startswith("wss://")
    assert session.zyoutoeki_kazei_c == "1"
    assert session.expires_at_ms is None  # F-B3


async def test_login_request_uses_json_ofmt_five(httpx_mock: HTTPXMock):
    """MEDIUM-C3-1 — auth endpoint requires sJsonOfmt='5'."""
    _add_login_response(httpx_mock, _ok_login_payload())
    await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    request = httpx_mock.get_request()
    assert request is not None
    query = _decode_query_json(str(request.url))
    assert query["sJsonOfmt"] == "5"
    assert query["sCLMID"] == "CLMAuthLoginRequest"
    assert query["sUserId"] == "uid"
    assert query["sPassword"] == "pwd"


async def test_login_consumes_p_no_counter_so_retries_are_monotonic(
    httpx_mock: HTTPXMock,
):
    """R4 — two `login()` calls on the same counter must send strictly
    increasing `p_no` values, so a startup retry never replays the prior
    request id."""
    _add_login_response(httpx_mock, _ok_login_payload())
    _add_login_response(httpx_mock, _ok_login_payload())
    counter = PNoCounter()
    await login("uid", "pwd", is_demo=True, p_no_counter=counter)
    await login("uid", "pwd", is_demo=True, p_no_counter=counter)
    requests = httpx_mock.get_requests()
    p_nos = [int(_decode_query_json(str(r.url))["p_no"]) for r in requests]
    assert p_nos[1] > p_nos[0]


# ---------------------------------------------------------------------------
# URL scheme validation (MEDIUM-C3-3)
# ---------------------------------------------------------------------------


async def test_login_rejects_non_wss_event_url(httpx_mock: HTTPXMock):
    # Force the WS scheme to ws:// while keeping the host derived from
    # BASE_URL_DEMO so the demo host literal stays out of the test file
    # (single-source rule, F-L1).
    payload = _ok_login_payload(
        sUrlEventWebSocket=_VIRTUAL_EVENT_WS.replace("wss://", "ws://", 1),
    )
    _add_login_response(httpx_mock, payload)
    with pytest.raises(LoginError):
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())


async def test_login_rejects_non_https_request_url(httpx_mock: HTTPXMock):
    payload = _ok_login_payload(
        sUrlRequest=_VIRTUAL_REQUEST.replace("https://", "http://", 1),
    )
    _add_login_response(httpx_mock, payload)
    with pytest.raises(LoginError):
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())


# ---------------------------------------------------------------------------
# Error mapping (R6 / two-stage check + sKinsyouhouMidokuFlg)
# ---------------------------------------------------------------------------


async def test_login_raises_unread_notices_when_kinsyouhou_flag_set(httpx_mock: HTTPXMock):
    """HIGH-C2-1: sKinsyouhouMidokuFlg='1' → UnreadNoticesError → unread_notices."""
    _add_login_response(
        httpx_mock,
        _ok_login_payload(sKinsyouhouMidokuFlg="1"),
    )
    with pytest.raises(UnreadNoticesError) as exc_info:
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    assert exc_info.value.code == "unread_notices"


async def test_session_expired_p_errno_2(httpx_mock: HTTPXMock):
    _add_login_response(
        httpx_mock,
        _ok_login_payload(p_errno="2", p_err="session expired"),
    )
    with pytest.raises(SessionExpiredError) as exc_info:
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    assert exc_info.value.code == "session_expired"


async def test_login_p_errno_minus_62_raises_login_error(httpx_mock: HTTPXMock):
    """Generic non-2 `p_errno` on the auth path must be bucketed as
    `LoginError` (login_path=True), not surface as a bare `TachibanaError`.
    Pinning the subclass guards against `_raise_for_error(login_path=True)`
    silently regressing to pass-through."""
    _add_login_response(
        httpx_mock,
        _ok_login_payload(p_errno="-62", p_err="auth-rate"),
    )
    with pytest.raises(LoginError) as exc_info:
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    # The original Tachibana code is preserved on LoginError.code.
    assert exc_info.value.code == "-62"


async def test_login_p_errno_minus_62_uses_service_out_of_hours_banner(
    httpx_mock: HTTPXMock,
):
    """`p_errno=-62` ("システムサービス時間外") MUST surface a dedicated banner
    instead of the misleading "ID / パスワードを確認してください" string. The
    server-side wording (`p_err`) still must not leak (F-Banner1)."""
    _add_login_response(
        httpx_mock,
        _ok_login_payload(p_errno="-62", p_err="システムサービス時間外。"),
    )
    with pytest.raises(LoginError) as exc_info:
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    msg = exc_info.value.message
    assert "サービス時間外" in msg
    assert "ID" not in msg and "パスワード" not in msg, (
        "service-hours banner must NOT recycle the credential-check wording"
    )
    assert "システムサービス時間外。" not in msg, (
        "raw server p_err text must not leak into the banner (F-Banner1)"
    )


async def test_login_authentication_failure_raises_login_error(httpx_mock: HTTPXMock):
    """Same guarantee for `sResultCode != "0"` on the auth path."""
    _add_login_response(
        httpx_mock,
        _ok_login_payload(
            sResultCode="10031",
            sResultText="invalid credentials",
        ),
    )
    with pytest.raises(LoginError) as exc_info:
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    assert exc_info.value.code == "10031"


# ---------------------------------------------------------------------------
# Banner-text contract (F-Banner1 / architecture.md §6)
# ---------------------------------------------------------------------------
#
# Server-side `p_err` / `sResultText` MUST NOT leak into VenueError.message
# — Python composes the entire banner string. These tests pin that.


async def test_login_failure_message_uses_fixed_japanese_banner(
    httpx_mock: HTTPXMock,
):
    _add_login_response(
        httpx_mock,
        _ok_login_payload(
            sResultCode="10031",
            sResultText="invalid credentials",
        ),
    )
    with pytest.raises(LoginError) as exc_info:
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    assert "invalid credentials" not in exc_info.value.message
    assert exc_info.value.message == (
        "ログインに失敗しました。ID / パスワードを確認してください"
    )


async def test_session_expired_message_is_python_composed(httpx_mock: HTTPXMock):
    _add_login_response(
        httpx_mock,
        _ok_login_payload(p_errno="2", p_err="session expired"),
    )
    with pytest.raises(SessionExpiredError) as exc_info:
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    assert "session expired" not in exc_info.value.message
    assert exc_info.value.message == (
        "立花のセッションが切れました（夜間閉局）。再ログインしてください"
    )


# ---------------------------------------------------------------------------
# HTTP / transport error mapping
# ---------------------------------------------------------------------------


async def test_login_http_502_maps_to_transport_error(httpx_mock: HTTPXMock):
    """5xx must surface as transport_error, not as a JSON parse failure."""
    httpx_mock.add_response(
        url=_AUTH_URL_RE,
        method="GET",
        status_code=502,
        content=b"<html>Bad Gateway</html>",
    )
    with pytest.raises(LoginError) as exc_info:
        await login("uid", "pwd", is_demo=True, p_no_counter=PNoCounter())
    assert exc_info.value.code == "transport_error"


async def test_validate_session_http_503_maps_to_transport_error(
    httpx_mock: HTTPXMock,
):
    httpx_mock.add_response(
        url=_MASTER_URL_RE,
        method="GET",
        status_code=503,
        content=b"<html>Service Unavailable</html>",
    )
    session = _make_session()
    latch = StartupLatch()
    with pytest.raises(LoginError) as exc_info:
        await validate_session_on_startup(
            session, latch=latch, p_no_counter=PNoCounter()
        )
    assert exc_info.value.code == "transport_error"


# ---------------------------------------------------------------------------
# validate_session_on_startup (HIGH-D2)
# ---------------------------------------------------------------------------


async def test_validate_session_uses_get_issue_detail_with_pinned_payload(
    httpx_mock: HTTPXMock,
):
    """HIGH-D2: assert (a) sUrlMaster base, (b) GET, (c) sCLMID/sTargetIssueCode, (d) sJsonOfmt='4'."""
    httpx_mock.add_response(
        url=_MASTER_URL_RE,
        method="GET",
        content=json.dumps({"p_errno": "0", "sResultCode": "0"}).encode("shift_jis"),
    )
    session = _make_session()
    latch = StartupLatch()
    ok = await validate_session_on_startup(session, latch=latch, p_no_counter=PNoCounter())
    assert ok is True

    request = httpx_mock.get_request()
    assert request is not None
    assert request.method == "GET"
    assert str(request.url).startswith(session.url_master.value)
    query = _decode_query_json(str(request.url))
    assert query["sCLMID"] == "CLMMfdsGetIssueDetail"
    assert query["sTargetIssueCode"] == "7203"
    assert "sIssueCode" not in query
    assert "sSizyouC" not in query
    assert query["sJsonOfmt"] == "4"


async def test_validate_session_propagates_session_expired(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=_MASTER_URL_RE,
        method="GET",
        content=json.dumps({"p_errno": "2", "p_err": "expired"}).encode("shift_jis"),
    )
    session = _make_session()
    latch = StartupLatch()
    with pytest.raises(SessionExpiredError):
        await validate_session_on_startup(session, latch=latch, p_no_counter=PNoCounter())


# ---------------------------------------------------------------------------
# StartupLatch (M3)
# ---------------------------------------------------------------------------


async def test_startup_latch_second_call_raises():
    """Second call must always RuntimeError, even after success."""
    latch = StartupLatch()

    async def coro_ok():
        return 42

    assert await latch.run_once(coro_ok()) == 42
    with pytest.raises(RuntimeError):
        await latch.run_once(coro_ok())


async def test_startup_latch_second_call_after_failure_still_raises():
    latch = StartupLatch()

    async def coro_fail():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await latch.run_once(coro_fail())
    with pytest.raises(RuntimeError):
        await latch.run_once(coro_fail())


async def test_startup_latch_concurrent_calls_one_raises_runtime_error():
    """asyncio.gather two run_once → exactly one RuntimeError."""
    latch = StartupLatch()

    async def coro():
        await asyncio.sleep(0)
        return "ok"

    results = await asyncio.gather(
        latch.run_once(coro()),
        latch.run_once(coro()),
        return_exceptions=True,
    )
    runtime_errors = [r for r in results if isinstance(r, RuntimeError)]
    successes = [r for r in results if r == "ok"]
    assert len(runtime_errors) == 1
    assert len(successes) == 1


async def test_validate_session_runs_real_http_only_once(httpx_mock: HTTPXMock):
    """Concurrent run_once → server is called at most once."""
    httpx_mock.add_response(
        url=_MASTER_URL_RE,
        method="GET",
        content=json.dumps({"p_errno": "0", "sResultCode": "0"}).encode("shift_jis"),
    )
    session = _make_session()
    latch = StartupLatch()

    results = await asyncio.gather(
        validate_session_on_startup(session, latch=latch, p_no_counter=PNoCounter()),
        validate_session_on_startup(session, latch=latch, p_no_counter=PNoCounter()),
        return_exceptions=True,
    )
    runtime_errors = [r for r in results if isinstance(r, RuntimeError)]
    assert len(runtime_errors) == 1
    # HTTPXMock.get_requests() returns every captured request.
    assert len(httpx_mock.get_requests()) == 1
