"""Hyperliquid 発注関数群（N3.A）

LiveExecutionClient から委譲される thin adapter。
重複実装は禁止。hyperliquid.py の HTTP クライアントパターンを再利用。

公開型:
    HyperliquidSession  — address と signer を保持する発注セッション
    OrderResult         — submit_order() の戻り値
    OrderEnvelope       — submit_order() に渡す注文データ（プロトコル）

公開関数:
    submit_order()  — Hyperliquid に成行/指値注文を送信する
    cancel_order()  — Hyperliquid の注文をキャンセルする
"""

from __future__ import annotations

import dataclasses
import logging
import time as _time
from typing import Any, Callable, Optional, Protocol

import httpx

log = logging.getLogger(__name__)

_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"


# ---------------------------------------------------------------------------
# セッション・結果型
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class HyperliquidSession:
    """Hyperliquid 発注セッション。address と signer を保持する。

    unit test では mock signer を注入できるため、ECDSA 秘密鍵は不要。
    production では eth-account 等で実装した signer を渡す。
    """

    address: str
    """EVM address (0x...)"""

    signer: Callable[[bytes], dict]
    """署名関数: bytes → {"r": "0x...", "s": "0x...", "v": 27}"""


@dataclasses.dataclass
class OrderResult:
    """submit_order() の戻り値。"""

    venue_order_id: str
    """Hyperliquid order ID（oid）。エラー時は空文字。"""

    status: str
    """'ok' | 'error'"""

    message: str = ""
    """エラーメッセージ（status='error' 時のみ）。"""


# ---------------------------------------------------------------------------
# 注文データプロトコル（duck typing / Any 受け入れ用）
# ---------------------------------------------------------------------------


class OrderEnvelopeProtocol(Protocol):
    """submit_order() に渡す注文データの最低要件。

    LiveExecutionClient は NautilusOrderEnvelope を渡す設計だが、
    テスト用モックも受け入れられるようプロトコルで定義する。
    """

    order_side: str       # "BUY" | "SELL"
    order_type: str       # "MARKET" | "LIMIT"
    quantity: str         # 例 "0.1"
    price: Optional[str]  # 指値価格。成行の場合は None
    asset_index: int      # Hyperliquid asset index (0 = BTC-PERP 等)


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------


def _build_order_action(envelope: Any) -> dict:
    """NautilusOrderEnvelope / OrderEnvelope → Hyperliquid action dict を構築する。

    Hyperliquid order body の action フィールド:
        {
          "type": "order",
          "orders": [{
            "a": <asset_index (int)>,
            "b": <is_buy (bool)>,
            "p": "<price_str>",
            "s": "<size_str>",
            "r": false,           # reduce_only
            "t": {"limit": {"tif": "Gtc"}} | {"market": {}}
          }],
          "grouping": "na"
        }

    成行注文は price="0" + tif="Ioc" に写す（Hyperliquid 慣例）。
    """
    is_buy = envelope.order_side == "BUY"
    asset_index = getattr(envelope, "asset_index", 0)

    if envelope.order_type == "MARKET":
        # 成行: price="0", tif=Ioc
        price_str = "0"
        order_type_field: dict = {"limit": {"tif": "Ioc"}}
    else:
        # 指値
        price_str = str(envelope.price) if envelope.price is not None else "0"
        order_type_field = {"limit": {"tif": "Gtc"}}

    order_spec = {
        "a": asset_index,
        "b": is_buy,
        "p": price_str,
        "s": str(envelope.quantity),
        "r": False,
        "t": order_type_field,
    }

    return {
        "type": "order",
        "orders": [order_spec],
        "grouping": "na",
    }


def _build_cancel_action(asset_index: int, oid: int) -> dict:
    """キャンセル action dict を構築する。

    Hyperliquid cancel body:
        {"type": "cancel", "cancels": [{"a": <asset_index>, "o": <oid>}]}
    """
    return {
        "type": "cancel",
        "cancels": [{"a": asset_index, "o": oid}],
    }


def _sign_action(session: HyperliquidSession, action: dict, nonce: int) -> dict:
    """action + nonce をシリアライズして signer に渡す。

    unit test では session.signer は mock で、実際の EIP-712 署名は行わない。

    IMPORTANT (production signer の実装者へ):
        この関数が渡す `payload_bytes` は JSON シリアライズした簡易表現です。
        実際の Hyperliquid API は EIP-712 typed data hash (phantom_agent) を要求します。
        production signer は `payload_bytes` を無視し、`action` と `nonce` から
        EIP-712 typed data を独自に計算して署名を返すこと。
        参照: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/signing
    """
    import json

    payload_bytes = json.dumps(
        {"action": action, "nonce": nonce}, separators=(",", ":")
    ).encode("utf-8")
    return session.signer(payload_bytes)


def _extract_venue_order_id(response_json: dict) -> str:
    """Hyperliquid レスポンスから venue_order_id (oid) を取り出す。

    成功時レスポンス例:
        {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 12345}}]}}}

    oid は int → str に変換して返す。見つからない場合は "" を返す。
    """
    try:
        statuses = response_json["response"]["data"]["statuses"]
        if not statuses:
            return ""
        status_entry = statuses[0]
        # "resting" (指値) または "filled" (成行) キー
        if "resting" in status_entry:
            return str(status_entry["resting"]["oid"])
        if "filled" in status_entry:
            return str(status_entry["filled"]["oid"])
    except (KeyError, IndexError, TypeError):
        pass
    return ""


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------


async def submit_order(session: HyperliquidSession, envelope: Any) -> OrderResult:
    """Hyperliquid に成行/指値注文を送信する。

    Args:
        session: HyperliquidSession（address + signer）
        envelope: OrderEnvelope プロトコルを満たすオブジェクト。
                  NautilusOrderEnvelope または duck-typing 互換 mock。

    Returns:
        OrderResult

    Raises:
        httpx.HTTPStatusError: HTTP エラー時
    """
    nonce = int(_time.time() * 1000)
    action = _build_order_action(envelope)
    signature = _sign_action(session, action, nonce)

    body = {
        "action": action,
        "nonce": nonce,
        "signature": signature,
    }

    log.debug(
        "submit_order: asset_index=%s side=%s type=%s qty=%s",
        getattr(envelope, "asset_index", 0),
        envelope.order_side,
        envelope.order_type,
        envelope.quantity,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(_EXCHANGE_URL, json=body)
        resp.raise_for_status()
        data = resp.json()

    top_status = data.get("status", "")
    if top_status != "ok":
        message = str(data.get("response", ""))
        log.warning("submit_order: API returned status=%r message=%r", top_status, message)
        return OrderResult(venue_order_id="", status="error", message=message)

    venue_order_id = _extract_venue_order_id(data)
    log.info("submit_order: accepted venue_order_id=%s", venue_order_id)
    return OrderResult(venue_order_id=venue_order_id, status="ok")


async def cancel_order(
    session: HyperliquidSession,
    *,
    venue_order_id: str,
    asset_index: int,
) -> None:
    """Hyperliquid の注文をキャンセルする。

    Args:
        session: HyperliquidSession
        venue_order_id: キャンセルする注文の oid（文字列）
        asset_index: 注文の asset index（int）

    Raises:
        httpx.HTTPStatusError: HTTP エラー時
        ValueError: レスポンスが error 時
    """
    nonce = int(_time.time() * 1000)
    oid = int(venue_order_id)
    action = _build_cancel_action(asset_index=asset_index, oid=oid)
    signature = _sign_action(session, action, nonce)

    body = {
        "action": action,
        "nonce": nonce,
        "signature": signature,
    }

    log.debug(
        "cancel_order: asset_index=%s oid=%s",
        asset_index,
        venue_order_id,
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(_EXCHANGE_URL, json=body)
        resp.raise_for_status()
        data = resp.json()

    top_status = data.get("status", "")
    if top_status != "ok":
        message = str(data.get("response", ""))
        log.warning("cancel_order: API returned status=%r message=%r", top_status, message)
        raise ValueError(f"cancel_order failed: status={top_status!r} message={message!r}")

    log.info("cancel_order: canceled oid=%s", venue_order_id)
