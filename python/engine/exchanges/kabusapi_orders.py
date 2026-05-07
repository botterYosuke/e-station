"""kabuステーション 発注・取消・約定 polling クライアント (Phase 2: 検証環境のみ)。

制約:
- 検証環境 (localhost:18081) 限定。本番 (18080) 経路は作らない
- 取引パスワードはセッション開始時 1 回のみ収集 (KabuTradePasswordHolder で管理)
- 発注流量制限 5/s: OrderBucket を async with で抑制
- URL リテラルは kabusapi_url.py のみ (K8 URL lint)
- 取引パスワード・クレデンシャルはログに絶対出力しない (R10)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from asyncio import subprocess as aio_subprocess
from typing import Any, Literal

import httpx

from engine.exchanges.kabusapi_auth import (
    KabuApiError,
    KabuTradeCancelledError,
    KabuTradeLockedOutError,
    KabuTradePasswordHolder,
    KabuTradePasswordInvalidError,
    check_response,
)
from engine.exchanges.kabusapi_ratelimit import InfoBucket, OrderBucket, TokenBucket
from engine.exchanges.kabusapi_url import KabuEnv, endpoint

logger = logging.getLogger(__name__)

# kabu order state: 5 = 約定
_STATE_FILLED = 5


async def _spawn_trade_dialog() -> dict:
    """取引パスワード収集ダイアログを subprocess で起動し結果を返す。"""
    proc = await aio_subprocess.create_subprocess_exec(
        sys.executable, "-m", "engine.exchanges.kabusapi_trade_dialog",
        stdout=aio_subprocess.PIPE,
        stderr=aio_subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise KabuTradeCancelledError(0, "Trade dialog subprocess timed out after 120s")
    if proc.returncode != 0 or not stdout.strip():
        logger.error(
            "kabu trade dialog subprocess failed: returncode=%s, stderr=%s",
            proc.returncode,
            (stderr.decode("utf-8", errors="replace")[:500]) if stderr else "(empty)",
        )
        raise KabuTradeCancelledError(0, "Trade dialog subprocess failed")
    try:
        return json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("kabu trade dialog: invalid JSON output: %s", exc)
        raise KabuTradeCancelledError(0, "Trade dialog returned invalid JSON") from exc


SideStr = Literal["buy", "sell"]


def _side_to_kabu(side: SideStr) -> str:
    """発注サイドを kabu API の Side 文字列に変換する。

    買い = "2"（立花は "3" — data-mapping.md §8 参照。混同厳禁）。
    """
    if side == "buy":
        return "2"
    if side == "sell":
        return "1"
    raise ValueError(f"Unknown side: {side!r}")


class KabuOrderClient:
    """kabuステーション 発注・取消・約定 polling クライアント。

    Args:
        token: X-API-KEY ヘッダ用トークン
        env: "verify" 固定（Phase 2 は検証環境のみ）
        trade_password_holder: KabuTradePasswordHolder インスタンス
        order_bucket: 発注系流量制限 bucket (デフォルト: OrderBucket())
        info_bucket: 情報系流量制限 bucket (poll_fills 用, デフォルト: InfoBucket())
        dev_trade_password_allowed: True の場合 DEV_KABU_TRADE_PASSWORD env を読む
    """

    def __init__(
        self,
        *,
        token: str,
        env: KabuEnv = "verify",
        trade_password_holder: KabuTradePasswordHolder,
        order_bucket: TokenBucket | None = None,
        info_bucket: TokenBucket | None = None,
        dev_trade_password_allowed: bool = False,
    ) -> None:
        self._token = token
        self._env = env
        self._holder = trade_password_holder
        self._order_bucket = order_bucket or OrderBucket()
        self._info_bucket = info_bucket or InfoBucket()
        self._dev_allowed = dev_trade_password_allowed
        self._dialog_lock: asyncio.Lock = asyncio.Lock()  # [C-4] 排他ロック

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self._token}

    async def _ensure_trade_password(self) -> str:
        """取引パスワードを取得する。未保持の場合はダイアログまたは env から収集する。"""
        if self._holder.is_locked_out():
            raise KabuTradeLockedOutError(0, "Trade password locked out (3 consecutive failures)")

        password = self._holder.get_password()
        if password is not None:
            self._holder.touch()
            return password

        # [C-4] 未保持 → 排他ロックを取ってから収集（並列発注でダイアログが多重起動しないよう保護）
        async with self._dialog_lock:
            # double-check: ロック取得後に再確認
            password = self._holder.get_password()
            if password is not None:
                self._holder.touch()
                return password

            # env or ダイアログで収集
            if self._dev_allowed:
                env_pass = os.environ.get("DEV_KABU_TRADE_PASSWORD")
                if env_pass:
                    self._holder.set_password(env_pass)
                    logger.debug("kabu: using DEV_KABU_TRADE_PASSWORD (dev mode)")
                    return env_pass

            # tkinter ダイアログ
            result = await _spawn_trade_dialog()
            if result.get("status") != "ok":
                raise KabuTradeCancelledError(0, "Trade password cancelled by user")
            trade_password = result.get("trade_password")
            if not trade_password:
                logger.error("kabu trade dialog: 'trade_password' key missing in dialog result")
                raise KabuTradeCancelledError(0, "Trade dialog returned no trade_password")
            self._holder.set_password(trade_password)
            return trade_password

    async def send_order(
        self,
        *,
        symbol: str,
        exchange: int = 1,
        side: str,
        qty: int,
        front_order_type: int = 10,
        price: float | None = None,
        expire_day: int = 0,
        cash_margin: int = 1,
        deliv_type: int = 2,
        fund_type: str = "AA",
        account_type: int = 4,
    ) -> dict[str, Any]:
        """POST /sendorder — 発注する（検証環境のみ）。

        Args:
            symbol: 銘柄コード ("9433" など)
            exchange: 市場コード (1=東証)
            side: "buy" or "sell"
            qty: 注文数量
            front_order_type: 執行条件 (10=成行, 20=指値 など) — OpenAPI FrontOrderType 参照
            price: 注文価格 (成行時は 0、指値時は指定値)
            expire_day: 有効期限 (0=当日)
            cash_margin: 1=現物, 2=信用買, 3=信用売
            deliv_type: 受渡区分 (2=NSCC)
            fund_type: 資金区分 ("AA" など)
            account_type: 口座種別 (4=特定)
        """
        trade_password = await self._ensure_trade_password()

        # [C-1] FrontOrderType/Price/ExpireDay を OpenAPI RequestSendOrder に合わせる
        body: dict[str, Any] = {
            "Password": trade_password,
            "Symbol": symbol,
            "Exchange": exchange,
            "SecurityType": 1,
            "Side": _side_to_kabu(side),
            "CashMargin": cash_margin,
            "DelivType": deliv_type,
            "FundType": fund_type,
            "AccountType": account_type,
            "Qty": qty,
            "FrontOrderType": front_order_type,
            "Price": price if price is not None else 0,
            "ExpireDay": expire_day,
        }

        async with self._order_bucket:
            url = endpoint("sendorder", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=self._headers(), json=body)

        try:
            resp_body = resp.json()
        except Exception as exc:
            logger.error("kabu sendorder: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"sendorder non-JSON response: {exc}") from exc
        try:
            check_response(resp_body, resp.status_code)
        except KabuTradePasswordInvalidError:
            self._holder.on_invalid()
            raise
        self._holder.on_submit_success()
        logger.info("kabu sendorder: OrderID=%s", resp_body.get("OrderID", "?"))
        return resp_body

    async def cancel_order(self, *, order_id: str) -> dict[str, Any]:
        """PUT /cancelorder — 取消する（検証環境のみ）。

        訂正エンドポイントは kabu API に存在しない。取消→再発注で代替 (supports_amend: false)。

        NOTE: OpenAPI RequestCancelOrder は OrderId（小文字 d）のみ必須。Password フィールドなし。
        """
        # [C-2] OpenAPI RequestCancelOrder に合わせて OrderId（小文字 d）を使用。Password は不要。
        body: dict[str, Any] = {
            "OrderId": order_id,
        }

        async with self._order_bucket:
            url = endpoint("cancelorder", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.put(url, headers=self._headers(), json=body)

        try:
            resp_body = resp.json()
        except Exception as exc:
            logger.error("kabu cancelorder: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"cancelorder non-JSON response: {exc}") from exc
        check_response(resp_body, resp.status_code)
        logger.info("kabu cancelorder: OrderID=%s", order_id)
        return resp_body

    async def send_order_future(
        self,
        *,
        symbol: str,
        exchange: int = 23,
        trade_type: int = 1,
        time_in_force: int = 1,
        side: str,
        qty: int,
        front_order_type: int = 120,  # 120=成行（マーケットオーダー）。OpenAPI 有効値: 18/20/28/30/120
        price: float = 0,
        expire_day: int = 0,
    ) -> dict[str, Any]:
        """POST /sendorder/future — 先物発注（検証環境のみ）。"""
        body: dict[str, Any] = {
            "Symbol": symbol,
            "Exchange": exchange,
            "TradeType": trade_type,
            "TimeInForce": time_in_force,
            "Side": _side_to_kabu(side),
            "Qty": qty,
            "FrontOrderType": front_order_type,
            "Price": price,
            "ExpireDay": expire_day,
        }
        async with self._order_bucket:
            url = endpoint("sendorder/future", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        try:
            resp_body = resp.json()
        except Exception as exc:
            logger.error("kabu sendorder/future: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"sendorder/future non-JSON response: {exc}") from exc
        check_response(resp_body, resp.status_code)
        logger.info("kabu sendorder/future: OrderID=%s", resp_body.get("OrderID", "?"))
        return resp_body

    async def send_order_option(
        self,
        *,
        symbol: str,
        exchange: int = 23,
        trade_type: int = 1,
        time_in_force: int = 1,
        side: str,
        qty: int,
        front_order_type: int = 120,  # 120=成行（マーケットオーダー）。OpenAPI 有効値: 18/20/28/30/120
        price: float = 0,
        expire_day: int = 0,
    ) -> dict[str, Any]:
        """POST /sendorder/option — OP 発注（検証環境のみ）。"""
        body: dict[str, Any] = {
            "Symbol": symbol,
            "Exchange": exchange,
            "TradeType": trade_type,
            "TimeInForce": time_in_force,
            "Side": _side_to_kabu(side),
            "Qty": qty,
            "FrontOrderType": front_order_type,
            "Price": price,
            "ExpireDay": expire_day,
        }
        async with self._order_bucket:
            url = endpoint("sendorder/option", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        try:
            resp_body = resp.json()
        except Exception as exc:
            logger.error("kabu sendorder/option: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"sendorder/option non-JSON response: {exc}") from exc
        check_response(resp_body, resp.status_code)
        logger.info("kabu sendorder/option: OrderID=%s", resp_body.get("OrderID", "?"))
        return resp_body

    async def poll_fills(self, **params: Any) -> list[dict[str, Any]]:
        """GET /orders — 約定済み注文を返す (Q-K2: WebSocket では来ない)。

        State=5 (約定) のレコードのみを返す。
        """
        async with self._info_bucket:
            url = endpoint("orders", env=self._env)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers(), params=params)

        try:
            body = resp.json()
        except Exception as exc:
            logger.error("kabu poll_fills: non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
            raise KabuApiError(resp.status_code, f"poll_fills non-JSON response: {exc}") from exc
        check_response(body, resp.status_code)
        if not isinstance(body, list):
            logger.error("kabu poll_fills: expected list response, got %s", type(body).__name__)
            raise KabuApiError(0, f"GET /orders returned non-list response: {type(body).__name__}")
        filled = [o for o in body if o.get("State") == _STATE_FILLED]
        logger.debug("kabu poll_fills: %d/%d records matched State=5", len(filled), len(body))  # [M-4]
        return filled
