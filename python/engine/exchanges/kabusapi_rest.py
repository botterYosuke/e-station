"""kabuステーション REST API ラッパー（読取専用）。

R4: Symbol は {symbol}@{exchange} 複合キー形式。
R5: 流量制限 bucket 経由でリクエスト。
R6: GET /board は内部的に PUSH 登録を自動発火するため、fetch_board() で
    RegisterSet.touch() を必ず呼ぶ（新規 + 満杯時は KabuRegisterFullError）。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from engine.exchanges.kabusapi_auth import KabuApiError, check_response
from engine.exchanges.kabusapi_register import RegisterSet
from engine.exchanges.kabusapi_ratelimit import InfoBucket, WalletBucket, TokenBucket
from engine.exchanges.kabusapi_url import KabuEnv, endpoint, symbol_key

logger = logging.getLogger(__name__)


class KabuRestClient:
    """kabuステーション REST クライアント（読取専用）。

    Args:
        token: X-API-KEY ヘッダに使う API トークン
        env: "prod" or "verify"
        register_set: PUSH 銘柄登録セット（fetch_board で touch() を呼ぶ）
        info_bucket: 情報系流量制限 bucket（10 req/sec）
        wallet_bucket: 余力系流量制限 bucket（10 req/sec）
    """

    def __init__(
        self,
        *,
        token: str,
        env: KabuEnv,
        register_set: RegisterSet,
        info_bucket: TokenBucket | None = None,
        wallet_bucket: TokenBucket | None = None,
    ) -> None:
        self._token = token
        self._env = env
        self._register_set = register_set
        self._info_bucket = info_bucket or InfoBucket()
        self._wallet_bucket = wallet_bucket or WalletBucket()

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self._token}

    async def fetch_board(self, symbol: str, exchange: int = 1) -> dict[str, Any]:
        """GET /board/{symbol}@{exchange} — 板情報を取得する。

        R6: GET /board は内部的に PUSH 登録を自動発火する。
        既存登録 hit なら touch() のみ（サーバー既登録なので HTTP 前に呼んでよい）。
        新規銘柄は HTTP 成功後に register()（失敗時はローカルに登録しない）。
        (INV-K6-TOUCH, INV-K6-REST-FULL)
        """
        key = (symbol, exchange)
        is_new = key not in self._register_set
        if not is_new:
            # 既存登録: サーバー側は既に PUSH 登録済みなので touch() を先に呼んでよい
            self._register_set.touch(symbol, exchange)

        async with self._info_bucket:
            url = endpoint(f"board/{symbol_key(symbol, exchange)}", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu board: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"board non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        # HTTP 成功後に新規銘柄を RegisterSet に追加（失敗時は登録しない）
        if is_new:
            self._register_set.register(symbol, exchange)
        return body

    async def fetch_symbol(self, symbol: str, exchange: int = 1) -> dict[str, Any]:
        """GET /symbol/{symbol}@{exchange} — 銘柄情報を取得する。"""
        async with self._info_bucket:
            url = endpoint(f"symbol/{symbol_key(symbol, exchange)}", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu symbol: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"symbol non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body

    async def fetch_orders(self, **params: Any) -> list[dict[str, Any]]:
        """GET /orders — 注文一覧を取得する。"""
        async with self._info_bucket:
            url = endpoint("orders", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers(), params=params)

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu orders: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"orders non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body if isinstance(body, list) else []

    async def fetch_positions(self, **params: Any) -> list[dict[str, Any]]:
        """GET /positions — 残高一覧を取得する。"""
        async with self._info_bucket:
            url = endpoint("positions", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers(), params=params)

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu positions: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"positions non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body if isinstance(body, list) else []

    async def fetch_wallet_cash(self) -> dict[str, Any]:
        """GET /wallet/cash — 現物余力を取得する。"""
        async with self._wallet_bucket:
            url = endpoint("wallet/cash", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu wallet/cash: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"wallet/cash non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body

    async def fetch_wallet_margin(self) -> dict[str, Any]:
        """GET /wallet/margin — 信用余力を取得する。"""
        async with self._wallet_bucket:
            url = endpoint("wallet/margin", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu wallet/margin: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"wallet/margin non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body

    async def fetch_wallet_future(self) -> dict[str, Any]:
        """GET /wallet/future — 先物余力を取得する。"""
        async with self._wallet_bucket:
            url = endpoint("wallet/future", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu wallet/future: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"wallet/future non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body

    async def fetch_wallet_option(self) -> dict[str, Any]:
        """GET /wallet/option — OP 余力を取得する。"""
        async with self._wallet_bucket:
            url = endpoint("wallet/option", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu wallet/option: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"wallet/option non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body

    async def fetch_symbolname_future(
        self,
        future_code: str,
        deriv_month: int,
    ) -> dict[str, Any]:
        """GET /symbolname/future — 先物銘柄コードを取得する。

        Args:
            future_code: 先物コード (例: "NK225")
            deriv_month: 限月 (yyyymm, 0=直近限月)
        """
        async with self._info_bucket:
            url = endpoint("symbolname/future", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url, headers=self._headers(),
                    params={"FutureCode": future_code, "DerivMonth": deriv_month},
                )

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu symbolname/future: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"symbolname/future non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body

    async def fetch_symbolname_option(
        self,
        option_code: str,
        deriv_month: int,
        put_or_call: str,
        strike_price: int,
    ) -> dict[str, Any]:
        """GET /symbolname/option — OP 銘柄コードを取得する。

        Args:
            option_code: OP コード (例: "NK225op")
            deriv_month: 限月 (yyyymm)
            put_or_call: "C" or "P"
            strike_price: 権利行使価格
        """
        async with self._info_bucket:
            url = endpoint("symbolname/option", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url, headers=self._headers(),
                    params={
                        "OptionCode": option_code,
                        "DerivMonth": deriv_month,
                        "PutOrCall": put_or_call,
                        "StrikePrice": strike_price,
                    },
                )

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu symbolname/option: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"symbolname/option non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body

    async def put_register(self, symbols: list[tuple[str, int]]) -> dict[str, Any]:
        """PUT /register — PUSH 銘柄登録リストを全件置換する。

        kabuStation の PUT /register は additive ではなく全件置換。
        空リストの場合は HTTP を送らずに即リターンする。
        """
        if not symbols:
            return {}
        payload = {"Symbols": [{"Symbol": s, "Exchange": e} for s, e in symbols]}
        async with self._info_bucket:
            url = endpoint("register", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.put(url, headers=self._headers(), json=payload)
        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu register: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"register non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        return body
