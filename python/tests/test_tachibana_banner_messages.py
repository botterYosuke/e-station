"""T6: Snapshot tests for banner messages emitted via VenueError.message (F-Banner1).

architecture.md §6 mandates:
  "バナー文言は Python 側の VenueError.message に込める。Rust UI は受信した
   message をそのまま描画するだけで固定文言を持たない"

These tests act as a regression guard: if any message is accidentally changed
to English, shortened to an empty string, or loses the critical Japanese phrase
that tells the user what to do next, the test fails immediately.

locale=ja_JP: all messages must be non-empty Japanese strings.

Coverage (architecture.md §6 failure-mode table):
  session_expired   — p_errno==2 at startup validation
  unread_notices    — sKinsyouhouMidokuFlg=='1'
  login_failed      — general auth failure
  transport_error   — HTTP/network failure during login
  login_parse_failed — malformed JSON response
  virtual_url_invalid — response URLs fail scheme validation (raised as code="login_failed")
  depth_unavailable — no bid/ask keys in FD frames after 30 s (tachibana.py)
"""

from __future__ import annotations

import pytest

import engine.exchanges.tachibana_auth as auth_module
from engine.exchanges.tachibana_helpers import (
    LoginError,
    SessionExpiredError,
    TachibanaError,
    UnreadNoticesError,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _is_japanese(s: str) -> bool:
    """Return True if `s` contains at least one CJK / Hiragana / Katakana char."""
    for ch in s:
        cp = ord(ch)
        if (
            0x3040 <= cp <= 0x309F  # Hiragana
            or 0x30A0 <= cp <= 0x30FF  # Katakana
            or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs (common range)
            or 0x3000 <= cp <= 0x303F  # CJK Symbols and Punctuation
            or 0xFF00 <= cp <= 0xFFEF  # Halfwidth and Fullwidth Forms
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Snapshot: tachibana_auth.py message constants (F-Banner1)
# ---------------------------------------------------------------------------
# Access module-level private constants directly — Python does not enforce
# name-mangling on module attributes, and these constants are the sole source
# of truth for the Tachibana banner text (locale=ja_JP).


@pytest.mark.parametrize(
    "constant_name, expected_snapshot",
    [
        (
            "_MSG_LOGIN_FAILED",
            "ログインに失敗しました。ID / パスワードを確認してください",
        ),
        (
            "_MSG_SERVICE_OUT_OF_HOURS",
            "立花サーバーが現在サービス時間外です（デモ環境は平日 8:00–18:00 JST）。"
            "時間内に再ログインしてください",
        ),
        (
            "_MSG_SESSION_EXPIRED_STARTUP",
            "立花のセッションが切れました（夜間閉局）。再ログインしてください",
        ),
        (
            "_MSG_TRANSPORT_ERROR",
            "立花サーバとの通信に失敗しました。ネットワーク / プロキシ設定を確認してください",
        ),
        (
            "_MSG_LOGIN_PARSE_FAILED",
            "立花ログイン応答の形式が不正です。サポートに連絡してください",
        ),
        (
            "_MSG_VIRTUAL_URL_INVALID",
            "立花ログイン応答の URL が想定と異なります。サポートに連絡してください",
        ),
    ],
)
def test_auth_message_constant_snapshot(
    constant_name: str, expected_snapshot: str
) -> None:
    """Each message constant must equal the pinned Japanese snapshot."""
    actual = getattr(auth_module, constant_name)
    assert actual == expected_snapshot, (
        f"{constant_name} changed unexpectedly.\n"
        f"  expected: {expected_snapshot!r}\n"
        f"  got:      {actual!r}\n"
        "Update the snapshot if the wording was intentionally improved."
    )


@pytest.mark.parametrize(
    "constant_name",
    [
        "_MSG_LOGIN_FAILED",
        "_MSG_SESSION_EXPIRED_STARTUP",
        "_MSG_TRANSPORT_ERROR",
        "_MSG_LOGIN_PARSE_FAILED",
        "_MSG_VIRTUAL_URL_INVALID",
    ],
)
def test_auth_message_constant_is_japanese(constant_name: str) -> None:
    """Message constants must contain Japanese characters (locale=ja_JP)."""
    msg = getattr(auth_module, constant_name)
    assert isinstance(msg, str) and len(msg) > 0, f"{constant_name} must be non-empty"
    assert _is_japanese(msg), (
        f"{constant_name} must be Japanese but got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Snapshot: UnreadNoticesError default message (tachibana_helpers.py)
# ---------------------------------------------------------------------------


def test_unread_notices_error_default_message_snapshot() -> None:
    """UnreadNoticesError default message is the pinned Japanese string."""
    err = UnreadNoticesError()
    assert err.code == "unread_notices"
    assert err.message == "立花からの未読通知があります。ブラウザで確認後に再ログインしてください"
    assert _is_japanese(err.message)


def test_unread_notices_error_carries_correct_code() -> None:
    err = UnreadNoticesError()
    assert err.code == "unread_notices"


# ---------------------------------------------------------------------------
# Snapshot: depth_unavailable message (tachibana.py)
# ---------------------------------------------------------------------------
# The depth_unavailable message is assembled inline in stream_depth(); we
# verify the substring that must appear so minor rewording is allowed but
# the key phrase cannot be removed.


def test_depth_unavailable_message_contains_japanese_key_phrase() -> None:
    """depth_unavailable message must mention 板情報 in Japanese."""
    import engine.exchanges.tachibana as tachibana_module
    import inspect

    source = inspect.getsource(tachibana_module)
    # The message must contain the Japanese key phrase "板情報"
    assert "板情報" in source, (
        "depth_unavailable banner message lost the Japanese key phrase '板情報'. "
        "Update tachibana.py and this test together."
    )
    # Verify it's associated with depth_unavailable code (same block)
    idx_depth = source.find('"depth_unavailable"')
    idx_ita = source.find("板情報")
    assert idx_depth != -1, "depth_unavailable code missing from tachibana.py"
    assert idx_ita != -1, "板情報 phrase missing from tachibana.py"
    # They should be within 300 chars of each other (same dict literal)
    assert abs(idx_depth - idx_ita) < 300, (
        "'板情報' is not adjacent to the depth_unavailable code — "
        "the message may have been moved away from the code."
    )


# ---------------------------------------------------------------------------
# Functional: depth_unavailable VenueError message carries 板情報 (M-F)
# ---------------------------------------------------------------------------
# The depth_unavailable message is assembled inline in stream_depth().
# Rather than relying solely on inspect.getsource() proximity, we also parse
# the assembled string literal from the source and assert the field value
# directly — so minor source layout changes (e.g. parenthesis grouping) do
# not silently break the guard.


def test_depth_unavailable_venue_error_message_contains_ita_joho() -> None:
    """The depth_unavailable VenueError message must contain '板情報'.

    tachibana.py._safety_watchdog appends a dict with:
        {"event": "VenueError", "code": "depth_unavailable", "message": "..."}
    We verify the message by parsing the adjacent string literals from source
    and asserting the assembled value contains the key phrase.
    """
    import re
    import inspect
    import engine.exchanges.tachibana as tachibana_module

    source = inspect.getsource(tachibana_module)

    # Locate the depth_unavailable VenueError dict and extract the "message" value.
    # The watchdog builds the message as an implicit string concatenation:
    #   "message": (
    #       "立花の板情報が取得できません"
    #       "（FD frame に気配が含まれていません）。"
    #       "設定を確認してください"
    #   ),
    pattern = re.compile(
        r'"depth_unavailable".*?"message":\s*\(?\s*((?:"[^"]*"\s*)+)',
        re.DOTALL,
    )
    m = pattern.search(source)
    assert m is not None, (
        "Could not locate the depth_unavailable message block in tachibana.py. "
        "Update this test if the watchdog code structure changed."
    )

    parts = re.findall(r'"([^"]*)"', m.group(1))
    assembled_message = "".join(parts)

    assert "板情報" in assembled_message, (
        f"depth_unavailable VenueError message must contain '板情報', "
        f"got assembled message: {assembled_message!r}"
    )
    assert _is_japanese(assembled_message), (
        f"depth_unavailable VenueError message must be Japanese: {assembled_message!r}"
    )


# ---------------------------------------------------------------------------
# Integration: error classes produce correct code+message pairs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc, expected_code, required_phrase",
    [
        # login_failed — any auth failure not caused by session/notices
        (
            LoginError(
                code="login_failed",
                message="ログインに失敗しました。ID / パスワードを確認してください",
            ),
            "login_failed",
            "ログイン",
        ),
        # session_expired — p_errno==2
        (
            SessionExpiredError(
                message="立花のセッションが切れました（夜間閉局）。再ログインしてください"
            ),
            "session_expired",
            "セッション",
        ),
        # unread_notices — sKinsyouhouMidokuFlg=='1'
        (
            UnreadNoticesError(),
            "unread_notices",
            "未読通知",
        ),
        # transport_error
        (
            LoginError(
                code="transport_error",
                message="立花サーバとの通信に失敗しました。ネットワーク / プロキシ設定を確認してください",
            ),
            "transport_error",
            "通信",
        ),
    ],
)
def test_error_class_code_and_message(
    exc: TachibanaError, expected_code: str, required_phrase: str
) -> None:
    """Each error class must carry the correct code and a Japanese message with the key phrase."""
    assert exc.code == expected_code, f"code mismatch: {exc.code!r} != {expected_code!r}"
    assert isinstance(exc.message, str) and len(exc.message) > 0
    assert _is_japanese(exc.message), f"message must be Japanese: {exc.message!r}"
    assert required_phrase in exc.message, (
        f"Required phrase {required_phrase!r} missing from message {exc.message!r}"
    )


# ---------------------------------------------------------------------------
# Guard: no message constant is the empty string
# ---------------------------------------------------------------------------


def test_no_message_constant_is_empty() -> None:
    """Every _MSG_* module attribute must be a non-empty string."""
    for name in dir(auth_module):
        if name.startswith("_MSG_"):
            value = getattr(auth_module, name)
            assert isinstance(value, str), f"{name} must be str, got {type(value)}"
            assert value.strip(), f"{name} must not be empty or whitespace-only"
