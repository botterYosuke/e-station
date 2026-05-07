"""kabuステーション API URL ビルダー — URL リテラルの唯一の所在地 (R1).

P4-1: 本番 URL (`localhost:18080`) は `KABU_ALLOW_PROD=1` 必須の多層ガード付き。
- `is_production_url(url)`: prod URL か判定
- `guard_prod_url(url)`: prod URL かつ env 未設定なら ValueError
- `base_url("prod")` / `endpoint(..., env="prod")` / `ws_url("prod")` は内部で guard を呼ぶ
"""
# このファイル以外に localhost:18080 / localhost:18081 / /kabusapi/ を書かない

from __future__ import annotations

import logging
import os
from typing import Literal

BASE_URL_PROD = "http://localhost:18080"
BASE_URL_VERIFY = "http://localhost:18081"

KabuEnv = Literal["prod", "verify"]

_ALLOW_PROD_ENV = "KABU_ALLOW_PROD"
_ALLOW_PROD_VALUE = "1"
_KABU_ENV_VAR = "KABU_ENV"
_KABU_ENV_PROD = "prod"

_log = logging.getLogger(__name__)


def resolve_kabu_env() -> KabuEnv:
    """env 変数から接続先環境を解決する (P4-2 二重判定)。

    `KABU_ALLOW_PROD=1` **かつ** `KABU_ENV=prod` のとき `"prod"`。
    片方だけ設定されている場合は安全側に倒し `"verify"` を返し WARN ログを出す。
    両方未設定（デフォルト）は WARN なしで `"verify"`。
    """
    allow_prod_raw = os.environ.get(_ALLOW_PROD_ENV)
    kabu_env_raw = os.environ.get(_KABU_ENV_VAR)
    allow_prod = allow_prod_raw == _ALLOW_PROD_VALUE
    kabu_env_prod = kabu_env_raw == _KABU_ENV_PROD
    if allow_prod and kabu_env_prod:
        return "prod"
    # 片方だけ prod 寄りなら誤設定の可能性が高い → WARN
    if allow_prod and not kabu_env_prod:
        _log.warning(
            "KABU_ALLOW_PROD=1 だが KABU_ENV=prod が未設定のため verify にフォールバック (KABU_ENV=%r)",
            kabu_env_raw,
        )
    elif kabu_env_prod and not allow_prod:
        _log.warning(
            "KABU_ENV=prod だが KABU_ALLOW_PROD=1 が未設定のため verify にフォールバック (KABU_ALLOW_PROD=%r)",
            allow_prod_raw,
        )
    return "verify"


def is_production_url(url: str) -> bool:
    """`localhost:18080` または `127.0.0.1:18080` を含む URL を本番と判定する (http/ws どちらも)。"""
    if not url:
        return False
    return "localhost:18080" in url or "127.0.0.1:18080" in url


def guard_prod_url(url: str) -> None:
    """本番 URL かつ `KABU_ALLOW_PROD=1` 未設定なら ValueError を raise。

    `KABU_ALLOW_PROD` の許可値は厳密に文字列 `"1"` のみ。`"true"` や `"0"` は不可。
    verify URL は env なしで通る。
    """
    if not is_production_url(url):
        return
    if os.environ.get(_ALLOW_PROD_ENV) != _ALLOW_PROD_VALUE:
        raise ValueError(
            "KABU_ALLOW_PROD=1 が設定されていないため本番 URL へのアクセスはブロックされました: "
            f"{url}"
        )


def base_url(env: KabuEnv) -> str:
    """Return the base URL for the given environment.

    `env="prod"` の場合は `KABU_ALLOW_PROD=1` 必須（多層ガードの第一段）。
    """
    url = BASE_URL_PROD if env == "prod" else BASE_URL_VERIFY
    guard_prod_url(url)
    return url


def endpoint(path: str, *, env: KabuEnv) -> str:
    """`{base}/kabusapi/{path}` を組み立てる。"""
    return f"{base_url(env)}/kabusapi/{path}"


def symbol_key(symbol: str, exchange: int) -> str:
    """`5401@1` 形式の複合キーを返す (R4)."""
    return f"{symbol}@{exchange}"


def ws_url(env: KabuEnv) -> str:
    """WebSocket エンドポイント URL。"""
    base = base_url(env)
    return f"{base.replace('http', 'ws')}/kabusapi/websocket"
