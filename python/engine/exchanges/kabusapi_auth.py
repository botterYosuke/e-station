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
