"""kabuステーション API URL ビルダー — URL リテラルの唯一の所在地 (R1)."""
# このファイル以外に localhost:18080 / localhost:18081 / /kabusapi/ を書かない

from __future__ import annotations

from typing import Literal

BASE_URL_PROD = "http://localhost:18080"
BASE_URL_VERIFY = "http://localhost:18081"

KabuEnv = Literal["prod", "verify"]


def base_url(env: KabuEnv) -> str:
    """Return the base URL for the given environment."""
    return BASE_URL_PROD if env == "prod" else BASE_URL_VERIFY


def endpoint(path: str, *, env: KabuEnv) -> str:
    """`{base}/kabusapi/{path}` を組み立てる。"""
    return f"{base_url(env)}/kabusapi/{path}"


def symbol_key(symbol: str, exchange: int) -> str:
    """`5401@1` 形式の複合キーを返す (R4)."""
    return f"{symbol}@{exchange}"


def ws_url(env: KabuEnv) -> str:
    """WebSocket エンドポイント URL。"""
    return f"{base_url(env).replace('http', 'ws')}/kabusapi/websocket"
