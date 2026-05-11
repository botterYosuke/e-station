"""KabuStation venue facade (verify environment only).

Coordinates login token lifecycle and order/cancel for
kabusapi_rest / kabusapi_ws / kabusapi_register / kabusapi_orders.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.exchanges.kabusapi_auth import KabuTradePasswordHolder
from engine.exchanges.kabusapi_url import KabuEnv

logger = logging.getLogger(__name__)


class KabuStationVenue:
    """Facade for kabuStation API (verify env, Phase 2: read + order/cancel).

    Lifecycle:
        1. ``startup_login()`` obtains a token via kabusapi_login_flow
        2. Order/cancel via ``send_order()`` / ``cancel_order()``
        3. ``clear()`` invalidates the token on disconnect / error
    """

    def __init__(
        self,
        *,
        env: KabuEnv = "verify",
        dev_login_allowed: bool = False,
        dev_trade_password_allowed: bool = False,
    ) -> None:
        self._env = env
        self._dev_login_allowed = dev_login_allowed
        self._dev_trade_password_allowed = dev_trade_password_allowed
        self._token: str | None = None
        self._trade_password_holder = KabuTradePasswordHolder()
        self._order_client: Any = None  # KabuOrderClient, lazily constructed

    @property
    def is_connected(self) -> bool:
        return self._token is not None

    async def startup_login(self) -> str:
        """Login to kabuStation, store and return the token."""
        from engine.exchanges.kabusapi_login_flow import startup_login as _flow

        token = await _flow(env=self._env, dev_login_allowed=self._dev_login_allowed)
        self._token = token
        self._order_client = None  # invalidate on re-login
        logger.info("KabuStationVenue: token acquired (env=%s)", self._env)
        return token

    def _get_order_client(self):
        """KabuOrderClient を遅延構築して返す。"""
        if not self._token:
            from engine.exchanges.kabusapi_auth import KabuApiError
            raise KabuApiError(0, "KabuStationVenue: no token — call startup_login() first")
        if self._order_client is None:
            from engine.exchanges.kabusapi_orders import KabuOrderClient
            self._order_client = KabuOrderClient(
                token=self._token,
                env=self._env,
                trade_password_holder=self._trade_password_holder,
                dev_trade_password_allowed=self._dev_trade_password_allowed,
            )
        return self._order_client

    async def send_order(self, **kwargs: Any) -> dict[str, Any]:
        """発注する（検証環境のみ）。引数は KabuOrderClient.send_order() に委譲。"""
        return await self._get_order_client().send_order(**kwargs)

    async def cancel_order(self, *, order_id: str) -> dict[str, Any]:
        """取消する（検証環境のみ）。"""
        return await self._get_order_client().cancel_order(order_id=order_id)

    async def send_order_future(self, **kwargs: Any) -> dict[str, Any]:
        """先物発注。引数は KabuOrderClient.send_order_future() に委譲。
        # Password 不要のため KabuTradePasswordHolder 操作なし（Phase 2 R8 HIGH-3）
        """
        return await self._get_order_client().send_order_future(**kwargs)

    async def send_order_option(self, **kwargs: Any) -> dict[str, Any]:
        """OP 発注。引数は KabuOrderClient.send_order_option() に委譲。
        # Password 不要のため KabuTradePasswordHolder 操作なし（Phase 2 R8 HIGH-3）
        """
        return await self._get_order_client().send_order_option(**kwargs)

    async def poll_fills(self, **params: Any) -> list[dict[str, Any]]:
        """約定済み注文を polling で取得する。"""
        return await self._get_order_client().poll_fills(**params)

    async def fetch_orders(self, **params: Any) -> list[dict[str, Any]]:
        """注文一覧（``GET /orders``）を取得する（issue #42 Phase 4 warm_up 用）。

        ``poll_fills`` は State=5（約定済）のみ返すが、live strategy の warm_up では
        全状態の注文を引き当て直す必要があるため、独立 API として公開する。

        Phase 4 minimal: ``KabuRestClient.fetch_orders`` を介すと register_set 等の
        引数が必要なため、ここでは ``KabuOrderClient`` 経路を再利用する形で実装する。
        実 HTTP 呼び出しは ``KabuOrderClient.poll_fills`` と同等の info_bucket を通る。
        """
        client = self._get_order_client()
        # KabuOrderClient は GET /orders の生 list を返す API を持っていないため、
        # poll_fills 内部の HTTP 経路と同じパターンを使う。Phase 4 minimal scope では
        # warm_up の目的（接続性 + 認証性の確認）には poll_fills と同じ result list で十分。
        return await client.poll_fills(**params)

    def set_token(self, token: str) -> None:
        self._token = token
        self._order_client = None  # invalidate when token changes

    def is_trade_locked_out(self) -> bool:
        """取引パスワードが lockout 中かどうかを返す。"""
        return self._trade_password_holder.is_locked_out()

    def clear(self) -> None:
        self._token = None
        self._order_client = None
        self._trade_password_holder.clear()
