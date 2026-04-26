"""立花証券 e支店 注文処理。

公開型:
    NautilusOrderEnvelope  — nautilus Order 互換のリクエスト純データクラス (Tpre.3)
    TachibanaWireOrderRequest  — 立花 HTTP 専用 wire 型（外部公開しない）
    SubmitOrderResult

Phase O-pre では型スケルトンのみ実装する。HTTP 送信・WAL・第二暗証番号 idle forget
等の実装は Phase O0 (T0.3〜T0.8) で行う。

立花固有の用語（sCLMID / p_no / sZyoutoekiKazeiC 等）はこのファイルの
_compose_request_payload 内にのみ存在する。外部（IPC / Rust UI 層）には出ない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# nautilus OrderEnvelope (Tpre.3)
# ---------------------------------------------------------------------------


class NautilusOrderEnvelope(BaseModel):
    """nautilus_trader.model.orders.Order 互換のフィールド構成を持つ純データクラス。

    N2 で nautilus 本体を導入したとき、このクラスを
    ``from nautilus_trader.model.orders import Order`` で置き換えるだけで
    済むよう field アクセスを互換に保つ。
    """

    model_config = ConfigDict(extra="ignore")

    client_order_id: str
    instrument_id: str
    order_side: str
    order_type: str
    quantity: str
    time_in_force: str
    post_only: bool
    reduce_only: bool
    price: Optional[str] = None
    trigger_price: Optional[str] = None
    trigger_type: Optional[str] = None
    expire_time_ns: Optional[int] = None
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 立花 Wire 型（外部に露出しない。Wire prefix で内部専用を明示）
# ---------------------------------------------------------------------------


class TachibanaWireOrderRequest(BaseModel):
    """CLMKabuNewOrder リクエストの wire 専用 class（T0.4 で実装）。

    `_envelope_to_wire()` 経由でしか生成できない設計（写像集約）。
    flowsurface `NewOrderRequest` を pydantic に 1:1 移植。
    フィールド名は立花固有（sXxx 形式）— 外部 IPC/UI 層には出ない。
    """

    model_config = ConfigDict(extra="forbid")

    # 口座区分 (sZyoutoekiKazeiC): "1"=特定源泉, "3"=特定非源泉, "0"=一般 etc.
    account_type: str
    # 銘柄コード (sIssueCode): 例 "7203"
    issue_code: str
    # 市場コード (sSizyouC): "00"=東証
    market_code: str
    # 売買区分 (sBaibaiKubun): "3"=買, "1"=売
    side: str
    # 執行条件 (sCondition): "0"=指定なし, "2"=寄付, "4"=引け, "6"=不成
    condition: str
    # 注文値段 (sOrderPrice): "0"=成行, 数値文字列=指値
    price: str
    # 注文株数 (sOrderSuryou): 例 "100"
    qty: str
    # 現物/信用区分 (sGenkinShinyouKubun):
    #   "0"=現物, "2"=制度信用新規, "4"=制度信用返済, "6"=一般信用新規, "8"=一般信用返済
    cash_margin: str
    # 注文期日 (sOrderExpireDay): "0"=当日, YYYYMMDD=期日指定
    expire_day: str
    # 第二パスワード (sSecondPassword) — repr でマスク
    second_password: str

    def __repr__(self) -> str:
        return (
            f"TachibanaWireOrderRequest("
            f"issue_code={self.issue_code!r}, side={self.side!r}, "
            f"price={self.price!r}, qty={self.qty!r}, "
            f"second_password=<redacted>)"
        )


class TachibanaWireOrderResponse(BaseModel):
    """CLMKabuNewOrder レスポンスの wire 専用 class。Phase O0 (T0.4) で実装。"""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# SubmitOrderResult
# ---------------------------------------------------------------------------


@dataclass
class SubmitOrderResult:
    """submit_order() の戻り値。"""

    client_order_id: str
    venue_order_id: str  # 立花 sOrderNumber
    warning_code: Optional[str] = None
    warning_text: Optional[str] = None


# ---------------------------------------------------------------------------
# TAGS レジストリ（architecture.md §10.4）
# ---------------------------------------------------------------------------
# Phase O0 で _compose_request_payload() 内で参照する。
# ここでは定義のみ。

TAGS_REGISTRY: dict[str, str] = {
    "cash_margin": "cash_margin",
    "account_type": "account_type",
    "close_strategy": "close_strategy",
}


# ---------------------------------------------------------------------------
# 写像関数（Phase O0 で実装。現在は UnsupportedOrderError を上げる）
# ---------------------------------------------------------------------------


class UnsupportedOrderError(Exception):
    """立花が対応していない注文パラメータ。"""

    def __init__(self, reason_code: str, reason_text: str = "") -> None:
        self.reason_code = reason_code
        self.reason_text = reason_text
        super().__init__(f"{reason_code}: {reason_text}")


# ---------------------------------------------------------------------------
# Phase O0 制限チェック（T0.3）
# ---------------------------------------------------------------------------

_PHASE_O0_CODE = "UNSUPPORTED_IN_PHASE_O0"

# Phase O0 で許可する値（それ以外は即拒否）
_ALLOWED_ORDER_TYPE = {"MARKET"}
_ALLOWED_ORDER_SIDE = {"BUY"}
_ALLOWED_TIME_IN_FORCE = {"DAY"}
_REQUIRED_TAG_PREFIX = "cash_margin=cash"


def check_phase_o0_order(order: Any) -> Optional[str]:
    """Phase O0 制限チェック。

    拒否する場合は reason_code ("UNSUPPORTED_IN_PHASE_O0") を返す。
    通過する場合は None を返す。

    条件 (a)-(g) は T0.3 受け入れテスト D3-2 に対応する。
    """
    if order.order_type not in _ALLOWED_ORDER_TYPE:
        return _PHASE_O0_CODE
    if order.order_side not in _ALLOWED_ORDER_SIDE:
        return _PHASE_O0_CODE
    if order.time_in_force not in _ALLOWED_TIME_IN_FORCE:
        return _PHASE_O0_CODE
    tags: list[str] = order.tags or []
    if _REQUIRED_TAG_PREFIX not in tags:
        return _PHASE_O0_CODE
    if getattr(order, "trigger_type", None) is not None:
        return _PHASE_O0_CODE
    if order.post_only:
        return _PHASE_O0_CODE
    if order.reduce_only:
        return _PHASE_O0_CODE
    return None


_ORDER_SIDE_MAP: dict[str, str] = {"BUY": "3", "SELL": "1"}
_CASH_MARGIN_MAP: dict[str, str] = {
    "cash_margin=cash": "0",
    "cash_margin=margin_credit_new": "2",
    "cash_margin=margin_credit_repay": "4",
    "cash_margin=margin_general_new": "6",
    "cash_margin=margin_general_repay": "8",
}
_ACCOUNT_TYPE_MAP: dict[str, str] = {
    "account_type=specific_with_withholding": "1",
    "account_type=specific_without_withholding": "3",
    "account_type=general": "0",
    "account_type=nisa_growth": "5",
    "account_type=nisa_tsumitate": "6",
}


def _parse_instrument_id(instrument_id: str) -> tuple[str, str]:
    """'7203.T/TSE' → ('7203', '00')。市場コードは TSE のみ Phase O0 でサポート。"""
    # instrument_id 形式: "<code>.<suffix>/<venue>"  例: "7203.T/TSE"
    issue_code = instrument_id.split(".")[0]
    return issue_code, "00"


def _envelope_to_wire(
    envelope: NautilusOrderEnvelope,
    session: Any,
    second_password: str,
) -> TachibanaWireOrderRequest:
    """NautilusOrderEnvelope → TachibanaWireOrderRequest 写像（T0.4）。

    architecture.md §10.1〜§10.4 の写像表に従う。立花固有フィールド名は
    このクラス内に閉じ、外部（IPC / Rust UI 層）には出さない。
    """
    issue_code, market_code = _parse_instrument_id(envelope.instrument_id)

    # §10.3 OrderSide 写像
    side = _ORDER_SIDE_MAP.get(envelope.order_side)
    if side is None:
        raise UnsupportedOrderError(
            "VENUE_UNSUPPORTED",
            f"order_side={envelope.order_side!r} は立花では未対応",
        )

    # §10.1 OrderType 写像 → price / condition
    order_type = envelope.order_type
    if order_type == "MARKET":
        wire_price = "0"
        condition = "0"
    elif order_type == "LIMIT":
        if not envelope.price:
            raise UnsupportedOrderError(
                "VENUE_UNSUPPORTED",
                "LIMIT 注文には price が必要です",
            )
        wire_price = envelope.price
        condition = "0"
    else:
        raise UnsupportedOrderError(
            "VENUE_UNSUPPORTED",
            f"order_type={order_type!r} は Phase O0 では未対応",
        )

    # §10.2 TimeInForce 写像
    tif = envelope.time_in_force
    if tif == "DAY":
        expire_day = "0"
    else:
        raise UnsupportedOrderError(
            "VENUE_UNSUPPORTED",
            f"time_in_force={tif!r} は Phase O0 では未対応",
        )

    # §10.4 tags: cash_margin → sGenkinShinyouKubun
    tags = envelope.tags or []
    cash_margin_tag = next((t for t in tags if t.startswith("cash_margin=")), None)
    if cash_margin_tag is None or cash_margin_tag not in _CASH_MARGIN_MAP:
        raise UnsupportedOrderError(
            "VENUE_UNSUPPORTED",
            f"cash_margin tag が未指定または不正: tags={tags!r}",
        )
    cash_margin = _CASH_MARGIN_MAP[cash_margin_tag]

    # §10.4 tags: account_type → sZyoutoekiKazeiC（省略時はセッションの値をパススルー）
    account_type_tag = next((t for t in tags if t.startswith("account_type=")), None)
    if account_type_tag is not None and account_type_tag in _ACCOUNT_TYPE_MAP:
        account_type = _ACCOUNT_TYPE_MAP[account_type_tag]
    else:
        account_type = session.zyoutoeki_kazei_c

    return TachibanaWireOrderRequest(
        account_type=account_type,
        issue_code=issue_code,
        market_code=market_code,
        side=side,
        condition=condition,
        price=wire_price,
        qty=envelope.quantity,
        cash_margin=cash_margin,
        expire_day=expire_day,
        second_password=second_password,
    )


def _compose_request_payload(
    wire: TachibanaWireOrderRequest,
    p_no_counter: Any,
) -> dict[str, Any]:
    """TachibanaWireOrderRequest に p_no / p_sd_date / sCLMID 等を付与して
    HTTP リクエスト dict を構築する（T0.4）。

    立花固有キー（sCLMID / p_no / sJsonOfmt 等）はこの関数内に閉じ、
    外部（IPC / Rust UI 層）に漏らさない。
    """
    from engine.exchanges.tachibana_helpers import current_p_sd_date

    payload: dict[str, Any] = {
        # IPC フィールドを立花 wire キーに rename
        "sZyoutoekiKazeiC": wire.account_type,
        "sIssueCode": wire.issue_code,
        "sSizyouC": wire.market_code,
        "sBaibaiKubun": wire.side,
        "sCondition": wire.condition,
        "sOrderPrice": wire.price,
        "sOrderSuryou": wire.qty,
        "sGenkinShinyouKubun": wire.cash_margin,
        "sOrderExpireDay": wire.expire_day,
        "sSecondPassword": wire.second_password,
        # 共通フィールド（全 REQUEST API 必須）
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMKabuNewOrder",
        "sJsonOfmt": "5",
        # 逆指値・建日種類デフォルト（Phase O0 は固定値、Phase O3 で拡張）
        "sGyakusasiOrderType": "0",
        "sGyakusasiZyouken": "0",
        "sGyakusasiPrice": "*",
        "sTatebiType": "*",
        "sTategyokuZyoutoekiKazeiC": "*",
    }
    return payload


async def submit_order(
    session: Any,
    second_password: str,
    order: NautilusOrderEnvelope,
    *,
    p_no_counter: Optional[Any] = None,
) -> SubmitOrderResult:
    """CLMKabuNewOrder を立花 REQUEST API に送信する（T0.4）。

    シグネチャは nautilus LiveExecutionClient 互換（spec.md §6.3）。
    HTTP クライアントは httpx.AsyncClient で都度生成する。
    WAL は Phase O0 T0.7 で追加。

    Raises:
        SessionExpiredError: p_errno=2 応答時
        TachibanaError: その他 API エラー時
        UnsupportedOrderError: 写像失敗時（Phase O0 では LIMIT 等）
    """
    import json

    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response
    from engine.exchanges.tachibana_url import build_request_url

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    wire = _envelope_to_wire(order, session, second_password)
    payload = _compose_request_payload(wire, p_no_counter)
    url = build_request_url(session.url_request, payload, sJsonOfmt="5")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        body = decode_response_body(resp.content)

    data = json.loads(body)
    err = check_response(data)
    if err is not None:
        raise err

    return SubmitOrderResult(
        client_order_id=order.client_order_id,
        venue_order_id=data.get("sOrderNumber", ""),
        warning_code=data.get("sWarningCode") or None,
        warning_text=data.get("sWarningText") or None,
    )


async def modify_order(
    session: Any,
    second_password: Any,
    client_order_id: str,
    new_quantity: Optional[str] = None,
    new_price: Optional[str] = None,
    new_trigger_price: Optional[str] = None,
    new_expire_time: Any = None,
) -> Any:
    """注文訂正。Phase O1 (T1.1) で実装。"""
    raise NotImplementedError("Phase O1 で実装予定")


async def cancel_order(
    session: Any,
    second_password: Any,
    client_order_id: str,
    venue_order_id: str,
) -> Any:
    """注文取消。Phase O1 (T1.1) で実装。"""
    raise NotImplementedError("Phase O1 で実装予定")


async def cancel_all_orders(
    session: Any,
    second_password: Any,
    instrument_id: Optional[str] = None,
    order_side: Optional[str] = None,
) -> Any:
    """全注文取消。Phase O1 (T1.1) で実装。"""
    raise NotImplementedError("Phase O1 で実装予定")


async def fetch_order_list(
    session: Any,
    filter: Optional[Any] = None,
) -> Any:
    """注文一覧取得。Phase O1 (T1.1) で実装。"""
    raise NotImplementedError("Phase O1 で実装予定")
