"""KabuStation venue facade (Phase 1: read-only, verify environment only).

Coordinates login token lifecycle for kabusapi_rest / kabusapi_ws / kabusapi_register.
"""
from __future__ import annotations

import logging

from engine.exchanges.kabusapi_url import KabuEnv

logger = logging.getLogger(__name__)


class KabuStationVenue:
    """Phase 1 facade for kabuStation API (read-only, verify env).

    Lifecycle:
        1. ``startup_login()`` obtains a token via kabusapi_login_flow
        2. ``clear()`` invalidates the token on disconnect / error
    """

    def __init__(self, *, env: KabuEnv = "verify", dev_login_allowed: bool = False) -> None:
        self._env = env
        self._dev_login_allowed = dev_login_allowed
        self._token: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._token is not None

    async def startup_login(self) -> str:
        """Login to kabuStation, store and return the token."""
        from engine.exchanges.kabusapi_login_flow import startup_login as _flow

        token = await _flow(env=self._env, dev_login_allowed=self._dev_login_allowed)
        self._token = token
        logger.info("KabuStationVenue: token acquired (env=%s)", self._env)
        return token

    def set_token(self, token: str) -> None:
        self._token = token

    def clear(self) -> None:
        self._token = None
