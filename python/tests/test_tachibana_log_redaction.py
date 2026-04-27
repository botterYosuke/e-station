"""T6: Verify secrets never appear in log output (spec.md §3.1 / architecture.md §2.2).

spec.md §3.1 mandates:
  "ログ出力時は仮想 URL のホスト部分まで *** マスク"
  user_id / password / 仮想 URL は全て機密

Checks that tachibana_auth logger records do NOT contain:
  - user_id sentinel
  - password sentinel
  - session virtual-URL token sentinel (the path component that changes per session)

sentinel design: high-entropy strings that cannot collide with normal log
output (same pattern as test_tachibana_startup_supervisor.py, MEDIUM-3 ラウンド 7).

Coverage:
  1. login() happy path — user_id / password / virtual URL token absent from logs
  2. login() error path (login_failed) — credentials absent from error logs
  3. validate_session_on_startup() happy path — session URL token absent
  4. validate_session_on_startup() error path (session_expired) — token absent
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from pytest_httpx import HTTPXMock

from engine.exchanges.tachibana_auth import (
    StartupLatch,
    TachibanaSession,
    login,
    validate_session_on_startup,
)
from engine.exchanges.tachibana_helpers import PNoCounter
from engine.exchanges.tachibana_url import (
    BASE_URL_DEMO,
    EventUrl,
    MasterUrl,
    PriceUrl,
    RequestUrl,
)

# ---------------------------------------------------------------------------
# Sentinel values — must NOT appear in any log record
# ---------------------------------------------------------------------------

_USER_ID = "REDACT_TEST_USER_8f3a2e9d"
_PASSWORD = "REDACT_TEST_PWD_4c7b1f6a"
_SESSION_TOKEN = "REDACT_SESSION_TOKEN_9e2d5c8b"

_DEMO_BASE = BASE_URL_DEMO.value  # ends with "/"
_DEMO_HOST = _DEMO_BASE.removeprefix("https://").removesuffix("/")

_AUTH_URL_RE = re.compile(rf"^{re.escape(_DEMO_BASE)}auth/\?")
_MASTER_URL_RE = re.compile(
    rf"^https://{re.escape(_DEMO_HOST)}/price/{re.escape(_SESSION_TOKEN)}/\?"
)

_VIRTUAL_REQUEST = f"https://{_DEMO_HOST}/request/{_SESSION_TOKEN}/"
_VIRTUAL_MASTER = f"https://{_DEMO_HOST}/master/{_SESSION_TOKEN}/"
_VIRTUAL_PRICE = f"https://{_DEMO_HOST}/price/{_SESSION_TOKEN}/"
_VIRTUAL_EVENT = f"https://{_DEMO_HOST}/event/{_SESSION_TOKEN}/"
_VIRTUAL_EVENT_WS = f"wss://{_DEMO_HOST}/event_ws/{_SESSION_TOKEN}/"

_SECRETS = (_USER_ID, _PASSWORD, _SESSION_TOKEN)


def _ok_login_payload(**overrides: object) -> dict:
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
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    httpx_mock.add_response(
        url=_AUTH_URL_RE,
        method="GET",
        content=body.encode("shift_jis"),
    )


def _ok_validate_payload(**overrides: object) -> dict:
    base = {
        "p_no": "2",
        "p_sd_date": "2026.04.25-10:00:00.000",
        "p_errno": "0",
        "p_err": "",
        "sCLMID": "CLMMfdsGetIssueDetail",
        "sResultCode": "0",
        "sResultText": "",
    }
    base.update(overrides)
    return base


def _add_validate_response(httpx_mock: HTTPXMock, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("shift_jis")
    httpx_mock.add_response(
        url=re.compile(rf"https://{re.escape(_DEMO_HOST)}/master/{re.escape(_SESSION_TOKEN)}/\?"),
        method="GET",
        content=body,
    )


def _make_session() -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl(_VIRTUAL_REQUEST),
        url_master=MasterUrl(_VIRTUAL_MASTER),
        url_price=PriceUrl(_VIRTUAL_PRICE),
        url_event=EventUrl(_VIRTUAL_EVENT),
        url_event_ws=_VIRTUAL_EVENT_WS,
        zyoutoeki_kazei_c="1",
        expires_at_ms=None,
    )


def _assert_no_secrets_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Fail if any sentinel secret appears in any log record."""
    all_text = "\n".join(
        f"{r.name} | {r.levelname} | {r.getMessage()}" for r in caplog.records
    )
    for secret in _SECRETS:
        assert secret not in all_text, (
            f"Secret sentinel {secret!r} found in log output!\n"
            f"Log dump:\n{all_text}"
        )


# ---------------------------------------------------------------------------
# 1. login() happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_happy_path_no_secrets_in_logs(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Successful login must not log user_id, password, or virtual URL token."""
    _add_login_response(httpx_mock, _ok_login_payload())

    with caplog.at_level(logging.DEBUG, logger="engine.exchanges.tachibana_auth"):
        session = await login(
            _USER_ID, _PASSWORD, is_demo=True, p_no_counter=PNoCounter()
        )

    assert session.url_request.value == _VIRTUAL_REQUEST
    _assert_no_secrets_in_logs(caplog)


# ---------------------------------------------------------------------------
# 2. login() error path — login_failed (p_errno non-2 non-empty)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_error_no_credentials_in_logs(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Auth failure log must not contain user_id or password."""
    _add_login_response(
        httpx_mock,
        _ok_login_payload(
            p_errno="-62",
            p_err="取引時間外",
            sResultCode="0",
            # No virtual URLs on error
            sUrlRequest="",
            sUrlMaster="",
            sUrlPrice="",
            sUrlEvent="",
            sUrlEventWebSocket="",
        ),
    )

    with caplog.at_level(logging.DEBUG, logger="engine.exchanges.tachibana_auth"):
        with pytest.raises(Exception):  # LoginError or SessionExpiredError
            await login(_USER_ID, _PASSWORD, is_demo=True, p_no_counter=PNoCounter())

    _assert_no_secrets_in_logs(caplog)


# ---------------------------------------------------------------------------
# 3. validate_session_on_startup() happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_session_happy_path_no_url_token_in_logs(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Session validation must not log the virtual URL session token."""
    _add_validate_response(httpx_mock, _ok_validate_payload())
    session = _make_session()

    with caplog.at_level(logging.DEBUG, logger="engine.exchanges.tachibana_auth"):
        ok = await validate_session_on_startup(
            session, latch=StartupLatch(), p_no_counter=PNoCounter()
        )

    assert ok is True
    _assert_no_secrets_in_logs(caplog)


# ---------------------------------------------------------------------------
# 4. validate_session_on_startup() error — session_expired (p_errno==2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_session_expired_no_url_token_in_logs(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    """session_expired error path must not log the virtual URL session token."""
    _add_validate_response(
        httpx_mock,
        _ok_validate_payload(p_errno="2", p_err="セッションが切れています"),
    )
    session = _make_session()

    with caplog.at_level(logging.DEBUG, logger="engine.exchanges.tachibana_auth"):
        with pytest.raises(Exception):  # SessionExpiredError
            await validate_session_on_startup(
                session, latch=StartupLatch(), p_no_counter=PNoCounter()
            )

    _assert_no_secrets_in_logs(caplog)


# ---------------------------------------------------------------------------
# 5. Broad logger sweep — check ALL engine.exchanges.tachibana* loggers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_happy_path_no_secrets_in_any_tachibana_logger(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Secrets must not appear in any logger under engine.exchanges.tachibana."""
    _add_login_response(httpx_mock, _ok_login_payload())

    # Capture all tachibana-related loggers
    with caplog.at_level(logging.DEBUG, logger="engine.exchanges"):
        await login(_USER_ID, _PASSWORD, is_demo=True, p_no_counter=PNoCounter())

    _assert_no_secrets_in_logs(caplog)
