"""kabuステーション API 認証ヘルパー。

- fetch_token: POST /token でトークン取得
- check_response: HTTP status + body Code の 2 段判定 (R7)
- エラー型: KabuApiError / KabuTokenExpiredError / KabuRateLimitError /
            KabuRegisterFullError / KabuConnectionError

ログマスク: token / API パスワード / 取引パスワードは絶対に平文ログに出さない (R10, INV-K2-NO-LOG-SECRET)。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from engine.exchanges.kabusapi_url import KabuEnv, endpoint

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# エラー型
# ---------------------------------------------------------------------------

class KabuApiError(Exception):
    """kabuステーション API の業務エラー基底クラス。"""
    def __init__(self, code: int | str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"KabuApiError({code}): {message}")


class KabuTokenExpiredError(KabuApiError):
    """Code=4001001 (not logged in) / 4001005 (token expired)."""


class KabuRateLimitError(KabuApiError):
    """Code=4002006 (rate limit exceeded)."""


class KabuRegisterFullError(KabuApiError):
    """Code=4002001 (register full) / 51銘柄目."""


class KabuConnectionError(KabuApiError):
    """kabuステーション本体が起動していない (ConnectionRefusedError)."""


class KabuLoginCancelledError(KabuApiError):
    """ユーザーがログインダイアログをキャンセルした。"""


class KabuTradeCancelledError(KabuApiError):
    """ユーザーが取引パスワードダイアログをキャンセルした。"""


class KabuTradeLockedOutError(KabuApiError):
    """取引パスワード 3 回連続誤入力による lockout 中。"""


# ---------------------------------------------------------------------------
# 取引パスワード保持
# ---------------------------------------------------------------------------


class KabuTradePasswordHolder:
    """取引パスワードのメモリ保持 + idle forget タイマー + lockout 状態。

    TachibanaSessionHolder の kabu 版（architecture.md §2.2 Phase 2）。

    * idle timer: set_password() / touch() でリセット。idle_forget_minutes 経過で自動 None 化。
    * lockout: on_invalid() が max_retries 回連続した場合に lockout_secs 間は is_locked_out() が True。
    * ログマスク: 取引パスワードは絶対に平文ログに出さない (R10, INV-K2-NO-LOG-SECRET)。
    """

    def __init__(
        self,
        idle_forget_minutes: float = 30.0,
        max_retries: int = 3,
        lockout_secs: float = 1800.0,
    ) -> None:
        self._password: str | None = None
        self._idle_forget_secs = idle_forget_minutes * 60.0
        self._max_retries = max_retries
        self._lockout_secs = lockout_secs
        self._last_use_time: float | None = None
        self._invalid_count: int = 0
        self._lockout_until: float | None = None

    def _now(self) -> float:
        import asyncio
        import time

        try:
            return asyncio.get_running_loop().time()
        except RuntimeError:
            return time.monotonic()

    def set_password(self, value: str) -> None:
        """取引パスワードを保持し idle タイマーをリセットする。"""
        self._password = value
        self.touch()

    def touch(self, now: float | None = None) -> None:
        """idle タイマーをリセットする。発注・取消リクエスト時に呼ぶ。"""
        self._last_use_time = now if now is not None else self._now()

    def is_idle_expired(self, now: float | None = None) -> bool:
        """idle forget 閾値を超えていれば True。"""
        if self._last_use_time is None:
            return False
        t = now if now is not None else self._now()
        return (t - self._last_use_time) >= self._idle_forget_secs

    def is_locked_out(self, now: float | None = None) -> bool:
        """lockout 期間中かを返す。"""
        if self._lockout_until is None:
            return False
        t = now if now is not None else self._now()
        if t >= self._lockout_until:
            self._lockout_until = None
            return False
        return True

    def get_password(self, now: float | None = None) -> str | None:
        """発注・取消時に呼ぶ。idle 期限切れなら自動クリアして None を返す。"""
        if self.is_idle_expired(now):
            self._password = None
            self._last_use_time = None
        return self._password

    def clear(self) -> None:
        """パスワードをクリアする。セッション終了・エラー時に呼ぶ。"""
        self._password = None
        self._last_use_time = None

    def on_invalid(self, now: float | None = None) -> bool:
        """取引パスワード誤入力時に呼ぶ。
        Returns True ならば lockout 状態に入った（以降の発注をブロックすべき）。
        """
        self._password = None
        self._invalid_count += 1
        if self._invalid_count >= self._max_retries:
            t = now if now is not None else self._now()
            self._lockout_until = t + self._lockout_secs
            return True
        return False

    def on_submit_success(self) -> None:
        """発注成功時に invalid_count をリセット。"""
        self._invalid_count = 0


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

async def fetch_token(api_password: str, *, env: KabuEnv) -> str:
    """POST /token でトークンを取得して返す。

    トークンは戻り値として返すのみ。このモジュールは保持しない。
    ログ出力時はトークン末尾 4 文字のみ (R3, R10)。
    """
    url = endpoint("token", env=env)
    payload = {"APIPassword": api_password}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
    except httpx.ConnectError as exc:
        raise KabuConnectionError(0, str(exc)) from exc

    body = resp.json()
    check_response(body, resp.status_code)

    token: str = body["Token"]
    masked = f"***{token[-4:]}" if len(token) >= 4 else "***"
    logger.info("kabu /token: 200 OK, token=%s", masked)
    return token


def check_response(payload: Any, http_status: int) -> None:
    """HTTP status と body Code を 2 段チェックする (R7)。

    正常: HTTP 2xx かつ (Code == 0 or Code 不在)
    """
    code = payload.get("Code", 0) if isinstance(payload, dict) else 0
    message = payload.get("Message", "") if isinstance(payload, dict) else ""

    # トークン期限切れ / 未ログイン
    if code in (4001001, 4001005):
        raise KabuTokenExpiredError(code, message)

    # 流量制限
    if code == 4002006:
        raise KabuRateLimitError(code, message)

    # 銘柄登録上限
    if code in (4002001, 4002008):
        raise KabuRegisterFullError(code, message)

    # その他業務エラー
    if code != 0:
        raise KabuApiError(code, message)

    # HTTP エラー（Code が 0 でも HTTP 非 2xx は異常）
    if not (200 <= http_status < 300):
        raise KabuApiError(http_status, f"HTTP {http_status}")
