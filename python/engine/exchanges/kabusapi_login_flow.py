"""kabuステーション startup_login ログインフロー。

DEV_KABU_API_PASSWORD 環境変数が設定されている場合は tkinter ダイアログをスキップし、
直接 fetch_token() を呼ぶ。

TCP 拒否時は 5s × 3 回 retry して KabuConnectionError を raise する。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import sys
from asyncio import subprocess as aio_subprocess

from engine.exchanges.kabusapi_auth import KabuConnectionError, KabuLoginCancelledError, fetch_token
from engine.exchanges.kabusapi_url import KabuEnv

logger = logging.getLogger(__name__)

_RETRY_DELAY_S = 5.0
_MAX_RETRIES = 3

# 早朝強制ログアウト時刻帯 (JST 4:00〜9:00): local_app_down を INFO 扱い
# ptal/howto.html 確認後に更新 (Q-K3)
_MORNING_LOGOUT_HOURS_JST = range(4, 9)  # [4, 9)


def _is_morning_logout_window() -> bool:
    """JST 早朝時刻帯かどうかを判定する (INV-K3-MORNING)."""
    jst = datetime.timezone(datetime.timedelta(hours=9))
    now_jst = datetime.datetime.now(jst)
    return now_jst.hour in _MORNING_LOGOUT_HOURS_JST


async def _spawn_dialog() -> dict:
    """tkinter ダイアログを subprocess で起動し、結果を返す。"""
    proc = await aio_subprocess.create_subprocess_exec(
        sys.executable, "-m", "engine.exchanges.kabusapi_login_dialog",
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return json.loads(stdout.decode("utf-8"))


async def startup_login(
    *,
    env: KabuEnv = "verify",
    dev_login_allowed: bool = False,
) -> str:
    """kabuステーション startup_login。

    Returns:
        取得したトークン文字列

    Raises:
        KabuConnectionError: 本体落ち / 3 回 retry 失敗
        KabuApiError: その他 API エラー
    """
    api_password: str | None = None

    # debug env からの自動ログイン (INV-K3-NO-TKINTER)
    if dev_login_allowed:
        api_password = os.environ.get("DEV_KABU_API_PASSWORD")

    if api_password is None:
        # tkinter ダイアログで収集
        result = await _spawn_dialog()
        if result.get("status") != "ok":
            raise KabuLoginCancelledError(0, "Login cancelled by user")
        api_password = result["api_password"]
        env = result.get("env", env)

    # /token 取得 (5s × 3 回 retry)
    for attempt in range(_MAX_RETRIES):
        try:
            token = await fetch_token(api_password, env=env)
            return token
        except KabuConnectionError:
            if attempt < _MAX_RETRIES - 1:
                is_morning = _is_morning_logout_window()
                log_fn = logger.info if is_morning else logger.warning
                log_fn(
                    "kabu: Connection refused (attempt %d/%d), retrying in %ss",
                    attempt + 1, _MAX_RETRIES, _RETRY_DELAY_S,
                )
                await asyncio.sleep(_RETRY_DELAY_S)
            else:
                is_morning = _is_morning_logout_window()
                log_fn = logger.info if is_morning else logger.error
                log_fn("kabu: local_app_down after %d retries", _MAX_RETRIES)
                raise

    raise KabuConnectionError(0, "unreachable")
