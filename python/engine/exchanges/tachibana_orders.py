"""立花証券 e支店 注文処理。

公開型:
    NautilusOrderEnvelope  — nautilus Order 互換のリクエスト純データクラス (Tpre.3)
    TachibanaWireOrderRequest  — 立花 HTTP 専用 wire 型（外部公開しない）
    SubmitOrderResult

Phase O-pre では型スケルトンのみ実装する。HTTP 送信・WAL・第二暗証番号 idle forget
等の実装は Phase O0 (T0.3〜T0.8) で行う。

立花固有の用語（sCLMID / p_no / sZyoutoekiKazeiC 等）はこのファイルの
_compose_request_payload 内にのみ存在する。外部（IPC / Rust UI 層）には出ない。

WAL フォーマット（T0.7）:
    各行は JSON オブジェクト + "\\n" 終端 (JSONL 形式)。
    末尾行に \\n が無い場合は truncated とみなしてスキップする（C-R5-H1）。

    submit 行:
        {"phase":"submit", "ts":<int ms>, "client_order_id":"<str>",
         "request_key":<int u64>, "instrument_id":"<str>",
         "order_side":"<BUY|SELL>", "order_type":"<MARKET|LIMIT|...>",
         "quantity":"<str>"}
    accepted 行:
        {"phase":"accepted", "ts":<int ms>, "client_order_id":"<str>",
         "venue_order_id":"<str|null>", "p_no":<int>,
         "warning_code":<str|null>, "warning_text":<str|null>}
    rejected 行:
        {"phase":"rejected", "ts":<int ms>, "client_order_id":"<str>",
         "reason_code":"<str>", "reason_text":"<str>"}

    不変条件:
        - second_password / sSecondPassword は絶対に含めない (D2-H2)
        - p_sd_date / p_no の実際の値はメタデータのみ (p_no は accepted 行に int で記録)
        - 仮想 URL は含めない (C-H1)
        - 各行は ASCII printable + UTF-8 に正規化済み (C-L4)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

logger = logging.getLogger(__name__)


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

    # 口座区分 (sZyoutoekiKazeiC): "1"=特定, "3"=一般, "5"=一般NISA, "6"=NISA成長投資枠
    account_type: str
    # 銘柄コード (sIssueCode): 例 "7203"
    issue_code: str
    # 市場コード (sSizyouC): "00"=東証
    market_code: str
    # 売買区分 (sBaibaiKubun): "3"=買, "1"=売
    side: str
    # 執行条件 (sCondition): "0"=指定なし, "2"=寄付, "4"=引け, "6"=不成
    condition: str
    # 注文値段 (sOrderPrice): "0"=成行, "*"=逆指値成行, 数値文字列=指値
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
    # 逆指値条件 (sGyakusasiZyouken): 逆指値トリガー価格。"0"=逆指値なし
    # Phase O3: STOP_MARKET / STOP_LIMIT で trigger_price の値が入る
    gyakusasi_zyouken: str = "0"
    # 逆指値値段 (sGyakusasiPrice): "0"=逆指値成行, 数値=逆指値指値, "*"=逆指値なし
    # Phase O3: STOP_MARKET → "0", STOP_LIMIT → <price>, 通常 → "*"
    gyakusasi_price: str = "*"
    # 逆指値注文種別 (sGyakusasiOrderType): "0"=逆指値なし, "1"=逆指値あり
    # Phase O3: STOP_* → "1"
    gyakusasi_order_type: str = "0"
    # 建日種類 (sTatebiType): "1"=個別指定, "*"=一括（デフォルト）
    # Phase O3: tategyoku tag がある場合 "1" になる
    tatebi_type: str = "*"
    # 建玉 ID (Phase O3: tategyoku tag の値)
    tategyoku_id: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"TachibanaWireOrderRequest("
            f"issue_code={self.issue_code!r}, side={self.side!r}, "
            f"price={self.price!r}, qty={self.qty!r}, "
            f"second_password=<redacted>)"
        )

    # C-3: __str__ も repr と同じマスク表現にする
    __str__ = __repr__

    # C-3: model_dump() / model_dump_json() でも second_password をマスク
    @field_serializer("second_password")
    def _mask_second_password(self, v: str, _info: Any) -> str:
        return "[REDACTED]"


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
    venue_order_id: Optional[str]  # 立花 sOrderNumber。欠落時は None
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
    # M-13: architecture.md §10.4 に基づき追加
    "close_action": "close_action",  # 現引/現渡（Phase O3 で写像実装予定）
    "tategyoku": "tategyoku",  # 信用返済の建玉個別指定（Phase O3 で写像実装予定）
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

# Phase O0 で許可する order_type（それ以外は即拒否）
# Phase O3 で STOP_MARKET / STOP_LIMIT を追加解禁
_ALLOWED_ORDER_TYPE = {"MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT"}
_ALLOWED_ORDER_SIDE = {"BUY", "SELL"}
# Phase O3 で GTD を追加解禁
_ALLOWED_TIME_IN_FORCE = {"DAY", "GTD", "AT_THE_OPEN", "AT_THE_CLOSE"}
# cash_margin=cash 以外も Phase O3 で解禁（_CASH_MARGIN_MAP で有効値チェック）
_REQUIRED_CASH_MARGIN_PREFIX = "cash_margin="

# Phase O3 で引き続き拒否する order_type（立花未対応）
_STILL_UNSUPPORTED_ORDER_TYPE = {"MARKET_IF_TOUCHED", "LIMIT_IF_TOUCHED"}
# Phase O3 で引き続き拒否する time_in_force（立花未対応）
_STILL_UNSUPPORTED_TIME_IN_FORCE = {"GTC", "IOC", "FOK"}


def check_phase_o0_order(order: Any) -> Optional[str]:
    """Phase O0/O3 制限チェック。

    拒否する場合は reason_code ("UNSUPPORTED_IN_PHASE_O0") を返す。
    通過する場合は None を返す。

    Phase O3 で解禁された種別:
      - order_type: STOP_MARKET, STOP_LIMIT（逆指値）
      - time_in_force: GTD（期日指定）
      - order_side: SELL
      - tags: margin_credit_new / margin_credit_repay / margin_general_new / margin_general_repay

    引き続き拒否（立花未対応）:
      - MARKET_IF_TOUCHED / LIMIT_IF_TOUCHED
      - GTC / IOC / FOK
      - trigger_type != LAST (逆指値で LAST 以外)
      - post_only=True / reduce_only=True
    """
    order_type = order.order_type
    if order_type in _STILL_UNSUPPORTED_ORDER_TYPE:
        return _PHASE_O0_CODE
    if order_type not in _ALLOWED_ORDER_TYPE:
        return _PHASE_O0_CODE

    if order.order_side not in _ALLOWED_ORDER_SIDE:
        return _PHASE_O0_CODE

    tif = order.time_in_force
    if tif in _STILL_UNSUPPORTED_TIME_IN_FORCE:
        return _PHASE_O0_CODE
    if tif not in _ALLOWED_TIME_IN_FORCE:
        return _PHASE_O0_CODE

    tags: list[str] = order.tags or []
    cash_margin_tag = next((t for t in tags if t.startswith(_REQUIRED_CASH_MARGIN_PREFIX)), None)
    if cash_margin_tag is None:
        return _PHASE_O0_CODE
    # 未知の cash_margin 値は _envelope_to_wire() で VENUE_UNSUPPORTED に写す
    if cash_margin_tag not in _CASH_MARGIN_MAP:
        return _PHASE_O0_CODE

    # trigger_type は STOP_MARKET/STOP_LIMIT 以外では null 必須（C1）
    trigger_type = getattr(order, "trigger_type", None)
    if trigger_type is not None and trigger_type != "LAST":
        return _PHASE_O0_CODE
    # 逆指値以外で trigger_type を設定するのは不正
    if trigger_type is not None and order_type not in ("STOP_MARKET", "STOP_LIMIT"):
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
    "account_type=specific":    "1",  # 特定口座
    "account_type=general":     "3",  # 一般口座
    "account_type=nisa":        "5",  # 一般NISA（2024年以降売却のみ可）
    "account_type=nisa_growth": "6",  # NISA成長投資枠（N成長）
}


def _parse_instrument_id(instrument_id: str) -> tuple[str, str]:
    """'7203.T/TSE' → ('7203', '00')。市場コードは TSE のみ Phase O0 でサポート。"""
    # instrument_id 形式: "<code>.<suffix>/<venue>"  例: "7203.T/TSE"
    issue_code = instrument_id.split(".")[0]
    return issue_code, "00"


def _expire_ns_to_jst_yyyymmdd(expire_time_ns: int) -> str:
    """UTC nanoseconds → JST YYYYMMDD 変換（architecture.md §10.2 GTD）。

    Args:
        expire_time_ns: nautilus expire_time（UTC nanoseconds）

    Returns:
        JST での日付文字列 "YYYYMMDD"

    Note:
        立花の CLMDateZyouhou (営業日カレンダー) マスタによる
        営業日チェックは Phase O4 以降で実装予定。
        Phase O3 では指定日をそのまま使う。
    """
    from datetime import datetime, timedelta, timezone

    jst = timezone(timedelta(hours=9))
    ts_sec = expire_time_ns / 1_000_000_000
    dt_utc = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    dt_jst = dt_utc.astimezone(jst)
    return dt_jst.strftime("%Y%m%d")


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

    # §10.1 OrderType 写像 → price / condition / 逆指値フィールド
    order_type = envelope.order_type
    # 逆指値フィールドのデフォルト値（Phase O3 以前の通常注文）
    wire_price: str
    condition = "0"
    gyakusasi_zyouken = "0"
    gyakusasi_price = "*"
    gyakusasi_order_type = "0"

    if order_type == "MARKET":
        wire_price = "0"
    elif order_type == "LIMIT":
        if not envelope.price:
            raise UnsupportedOrderError(
                "VENUE_UNSUPPORTED",
                "LIMIT 注文には price が必要です",
            )
        wire_price = envelope.price
    elif order_type == "STOP_MARKET":
        # 逆指値成行: sOrderPrice="*", sGyakusasiZyouken=<trigger>, sGyakusasiPrice="0"
        if not envelope.trigger_price:
            raise UnsupportedOrderError(
                "VENUE_UNSUPPORTED",
                "STOP_MARKET 注文には trigger_price が必要です",
            )
        if envelope.trigger_type and envelope.trigger_type != "LAST":
            raise UnsupportedOrderError(
                "VENUE_UNSUPPORTED",
                f"trigger_type={envelope.trigger_type!r} は立花では未対応（LAST のみ）",
            )
        wire_price = "*"
        gyakusasi_zyouken = envelope.trigger_price
        gyakusasi_price = "0"
        gyakusasi_order_type = "1"
    elif order_type == "STOP_LIMIT":
        # 逆指値指値: sOrderPrice=<price>, sGyakusasiZyouken=<trigger>, sGyakusasiPrice=<price>
        if not envelope.price:
            raise UnsupportedOrderError(
                "VENUE_UNSUPPORTED",
                "STOP_LIMIT 注文には price が必要です",
            )
        if not envelope.trigger_price:
            raise UnsupportedOrderError(
                "VENUE_UNSUPPORTED",
                "STOP_LIMIT 注文には trigger_price が必要です",
            )
        if envelope.trigger_type and envelope.trigger_type != "LAST":
            raise UnsupportedOrderError(
                "VENUE_UNSUPPORTED",
                f"trigger_type={envelope.trigger_type!r} は立花では未対応（LAST のみ）",
            )
        wire_price = envelope.price
        gyakusasi_zyouken = envelope.trigger_price
        gyakusasi_price = envelope.price
        gyakusasi_order_type = "1"
    elif order_type in ("MARKET_IF_TOUCHED", "LIMIT_IF_TOUCHED"):
        raise UnsupportedOrderError(
            "VENUE_UNSUPPORTED",
            f"order_type={order_type!r} は立花では未対応（STOP に変換してください）",
        )
    else:
        raise UnsupportedOrderError(
            "VENUE_UNSUPPORTED",
            f"order_type={order_type!r} は未対応",
        )

    # §10.2 TimeInForce 写像
    tif = envelope.time_in_force
    if tif == "DAY":
        expire_day = "0"
    elif tif == "GTD":
        # expire_time_ns (UTC nanoseconds) → JST YYYYMMDD
        if envelope.expire_time_ns is None:
            raise UnsupportedOrderError(
                "VENUE_UNSUPPORTED",
                "GTD 注文には expire_time_ns が必要です",
            )
        expire_day = _expire_ns_to_jst_yyyymmdd(envelope.expire_time_ns)
    elif tif in ("AT_THE_OPEN",):
        expire_day = "0"
        condition = "2"
    elif tif in ("AT_THE_CLOSE",):
        expire_day = "0"
        # D-3: close_strategy=funari tag → sCondition="6"（不成）
        # close_strategy タグなし or close_strategy=hikitsuke → sCondition="4"（引け）
        close_strategy_tag = next((t for t in (envelope.tags or []) if t.startswith("close_strategy=")), None)
        if close_strategy_tag == "close_strategy=funari":
            condition = "6"
        else:
            condition = "4"
    else:
        raise UnsupportedOrderError(
            "VENUE_UNSUPPORTED",
            f"time_in_force={tif!r} は立花では未対応",
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

    # §10.4 tags: tategyoku → sTatebiType + 建玉 ID
    tategyoku_tag = next((t for t in tags if t.startswith("tategyoku=")), None)
    if tategyoku_tag is not None:
        tatebi_type = "1"
        tategyoku_id = tategyoku_tag.split("=", 1)[1]
    else:
        tatebi_type = "*"
        tategyoku_id = None

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
        gyakusasi_zyouken=gyakusasi_zyouken,
        gyakusasi_price=gyakusasi_price,
        gyakusasi_order_type=gyakusasi_order_type,
        tatebi_type=tatebi_type,
        tategyoku_id=tategyoku_id,
    )


def _compose_request_payload(
    wire: TachibanaWireOrderRequest,
    p_no_counter: Any,
) -> dict[str, Any]:
    """TachibanaWireOrderRequest に p_no / p_sd_date / sCLMID 等を付与して
    HTTP リクエスト dict を構築する（T0.4）。

    立花固有キー（sCLMID / p_no / sJsonOfmt 等）はこの関数内に閉じ、
    外部（IPC / Rust UI 層）に漏らさない。

    NOTE: wire フィールドには直接アクセスすること（wire.model_dump() は使わない）。
    @field_serializer により model_dump() では second_password が "[REDACTED]" に置換されるため、
    実際の値が API payload に入らなくなる。
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
        # 逆指値フィールド（Phase O3: wire から取得）
        "sGyakusasiOrderType": wire.gyakusasi_order_type,
        "sGyakusasiZyouken": wire.gyakusasi_zyouken,
        "sGyakusasiPrice": wire.gyakusasi_price,
        # 建玉フィールド（Phase O3: wire から取得）
        "sTatebiType": wire.tatebi_type,
        "sTategyokuZyoutoekiKazeiC": "*",
    }
    # 建玉個別指定 (Phase O3)
    if wire.tategyoku_id is not None:
        payload["aCLMKabuHensaiData"] = [{"id": wire.tategyoku_id}]
    return payload


# ---------------------------------------------------------------------------
# WAL (Write-Ahead Log) helpers (T0.7)
# ---------------------------------------------------------------------------


def _current_ts_ms() -> int:
    """現在時刻を Unix ミリ秒で返す。"""
    return int(time.time() * 1000)


def _sanitize_for_wal(s: str) -> str:
    """WAL 書き込み用に文字列をサニタイズする（C-L4）。

    - C0 制御文字（\\x00-\\x1f, \\x7f）を除去する
    - JSON dumps で \\n / \\t はエスケープされるが、生の制御文字が混入しないよう事前除去する
    """
    return "".join(ch for ch in s if ord(ch) >= 0x20 and ord(ch) != 0x7F)


def _audit_log_submit(
    f: Any,
    client_order_id: str,
    request_key: int,
    instrument_id: str,
    order_side: str,
    order_type: str,
    quantity: str,
) -> None:
    """HTTP 送信直前に WAL に submit 行を書く（T0.7）。

    fsync 込みで書く（クラッシュ時の不整合最小化）。
    fsync 失敗時は OSError を raise して呼び出し元に伝え、HTTP 送信を行わせない。

    **第二暗証番号は絶対に書かない。**
    """
    record = {
        "phase": "submit",
        "ts": _current_ts_ms(),
        "client_order_id": client_order_id,
        "request_key": request_key,
        "instrument_id": instrument_id,
        "order_side": order_side,
        "order_type": order_type,
        "quantity": quantity,
    }
    line = json.dumps(record, ensure_ascii=True)  # ensure_ascii=True で制御文字も \\uXXXX エスケープ
    f.write(line + "\n")
    f.flush()
    os.fsync(f.fileno())  # クラッシュ安全性のため fsync 必須


def _audit_log_accepted(
    f: Any,
    client_order_id: str,
    venue_order_id: Optional[str],
    p_no: int,
    warning_code: Optional[str],
    warning_text: Optional[str],
) -> None:
    """応答受領後に WAL に accepted 行を書く（T0.7）。

    flush のみ（fsync 不要）。
    accepted が OS バッファ残りのままクラッシュした場合、起動時復元は unknown 状態になるが
    Phase O1 の GetOrderList で補完できる設計のため、同期 flush の遅延リスクを許容する。
    """
    record: dict[str, Any] = {
        "phase": "accepted",
        "ts": _current_ts_ms(),
        "client_order_id": client_order_id,
        "venue_order_id": venue_order_id,
        "p_no": p_no,
        "warning_code": warning_code,
        "warning_text": warning_text,
    }
    line = json.dumps(record, ensure_ascii=True)
    f.write(line + "\n")
    f.flush()


def _audit_log_rejected(
    f: Any,
    client_order_id: str,
    reason_code: str,
    reason_text: str,
) -> None:
    """応答受領後に WAL に rejected 行を書く（T0.7）。

    flush のみ（fsync 不要）。
    """
    record = {
        "phase": "rejected",
        "ts": _current_ts_ms(),
        "client_order_id": client_order_id,
        "reason_code": reason_code,
        "reason_text": reason_text,
    }
    line = json.dumps(record, ensure_ascii=True)
    f.write(line + "\n")
    f.flush()


def read_wal_records(wal_path: Path) -> list[dict[str, Any]]:
    """WAL ファイルを読み込み、有効なレコードのリストを返す（T0.7 起動時復元）。

    C-R5-H1 Truncation 復元規約:
        末尾行に \\n が無ければ truncated とみなしてスキップし、WARN ログを出す。
        partial 行は再生対象外で、対応する client_order_id は WAL 上「未送信」扱い。

    Args:
        wal_path: WAL ファイルのパス（存在しない場合は空リストを返す）

    Returns:
        有効なレコードの辞書リスト
    """
    if not wal_path.exists():
        return []

    raw = wal_path.read_text(encoding="utf-8")
    if not raw:
        return []

    # ファイル全体が \\n 終端かチェック（末尾行の truncation 判定）
    has_trailing_newline = raw.endswith("\n")

    # \\n で分割して空行を除く
    parts = raw.split("\n")

    records: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        if not part.strip():
            continue

        is_last_nonempty = all(not p.strip() for p in parts[i + 1 :])

        if is_last_nonempty and not has_trailing_newline:
            # 末尾行かつ \\n 欠落 → truncated
            logger.warning(
                "WAL truncated line detected at end of file, skipping: client_order_id may be "
                "lost. Line preview: %r",
                part[:80],
            )
            continue

        try:
            records.append(json.loads(part))
        except json.JSONDecodeError as exc:
            logger.warning("WAL invalid JSON line skipped: %r (%s)", part[:80], exc)

    return records


async def submit_order(
    session: Any,
    second_password: str,
    order: NautilusOrderEnvelope,
    *,
    p_no_counter: Optional[Any] = None,
    wal_path: Optional[Path] = None,
    request_key: int = 0,
) -> SubmitOrderResult:
    """CLMKabuNewOrder を立花 REQUEST API に送信する（T0.4 + T0.6 + T0.7）。

    シグネチャは nautilus LiveExecutionClient 互換（spec.md §6.3）。
    HTTP クライアントは httpx.AsyncClient で都度生成する。

    WAL 動作（T0.7）:
        - HTTP 送信直前に submit 行を WAL に fsync 込みで書く
        - fsync 失敗時は HTTP を送信しない（WAL 不変条件）
        - 応答受領後に accepted / rejected 行を flush で書く

    安全装置（T0.6）:
        - 本番 URL かつ TACHIBANA_ALLOW_PROD != "1" の場合は ValueError を raise

    Args:
        request_key: xxh3_64 hash computed by Rust (H-E / architecture.md §4.1).
            Value of 0 means "unknown" — WAL restore will skip this entry.
            Passed from IPC Command::SubmitOrder.request_key via server._do_submit_order.

    Raises:
        SessionExpiredError: p_errno=2 応答時
        TachibanaError: その他 API エラー時
        UnsupportedOrderError: 写像失敗時（Phase O0 では LIMIT 等）
        ValueError: 本番 URL かつ TACHIBANA_ALLOW_PROD 未設定時（T0.6）
        OSError: WAL fsync 失敗時（T0.7）
    """
    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    wire = _envelope_to_wire(order, session, second_password)
    payload = _compose_request_payload(wire, p_no_counter)
    url = build_request_url(session.url_request, payload, sJsonOfmt="5")

    # T0.6: 本番 URL ガード（HTTP 送信直前）
    guard_prod_url(url)

    # T0.7: WAL に submit 行を書く（HTTP 送信直前・fsync 必須）
    # p_no は _compose_request_payload() で既に確定済み
    submitted_p_no = int(payload["p_no"])

    if wal_path is not None:
        wal_path.parent.mkdir(parents=True, exist_ok=True)
        wal_file = open(wal_path, "a", encoding="utf-8")  # noqa: SIM115  (context manager は fsync 失敗時の制御に不向き)
        try:
            _audit_log_submit(
                wal_file,
                client_order_id=order.client_order_id,
                request_key=request_key,
                instrument_id=order.instrument_id,
                order_side=order.order_side,
                order_type=order.order_type,
                quantity=order.quantity,
            )
        except OSError:
            wal_file.close()
            raise  # fsync 失敗 → HTTP 送信しない（WAL 不変条件）
    else:
        wal_file = None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = decode_response_body(resp.content)

        data = json.loads(body)
        err = check_response(data)
        if err is not None:
            # T0.7: rejected 行を書く
            if wal_file is not None:
                _audit_log_rejected(
                    wal_file,
                    client_order_id=order.client_order_id,
                    reason_code=err.code,
                    reason_text=err.message,
                )
            raise err

        venue_order_id = data.get("sOrderNumber") or None
        warning_code = data.get("sWarningCode") or None
        warning_text = data.get("sWarningText") or None

        # T0.7: accepted 行を書く
        if wal_file is not None:
            _audit_log_accepted(
                wal_file,
                client_order_id=order.client_order_id,
                venue_order_id=venue_order_id,
                p_no=submitted_p_no,
                warning_code=warning_code,
                warning_text=warning_text,
            )

        return SubmitOrderResult(
            client_order_id=order.client_order_id,
            venue_order_id=venue_order_id,
            warning_code=warning_code,
            warning_text=warning_text,
        )
    finally:
        if wal_file is not None:
            wal_file.close()


# ---------------------------------------------------------------------------
# Phase O1 Wire 型（T1.1）
# ---------------------------------------------------------------------------


class TachibanaWireModifyRequest(BaseModel):
    """CLMKabuCorrectOrder リクエストの wire 専用 class（T1.1）。

    flowsurface `CorrectOrderRequest` を pydantic に 1:1 移植。
    立花固有キーは _compose_modify_payload() 内に閉じる。
    """

    model_config = ConfigDict(extra="forbid")

    # 注文番号 (sOrderNumber)
    order_number: str
    # 営業日 (sEigyouDay): YYYYMMDD
    eig_day: str
    # 執行条件 (sCondition): "*"=変更なし
    condition: str = "*"
    # 注文値段 (sOrderPrice): "*"=変更なし, "0"=成行, 数値=変更後値段
    price: str = "*"
    # 注文株数 (sOrderSuryou): "*"=変更なし, 数値=変更後株数
    qty: str = "*"
    # 期日 (sOrderExpireDay): "*"=変更なし
    expire_day: str = "*"
    # 第二パスワード — repr でマスク
    second_password: str

    def __repr__(self) -> str:
        return (
            f"TachibanaWireModifyRequest("
            f"order_number={self.order_number!r}, eig_day={self.eig_day!r}, "
            f"price={self.price!r}, qty={self.qty!r}, "
            f"second_password=[REDACTED])"
        )

    __str__ = __repr__

    @field_serializer("second_password")
    def _mask_second_password(self, v: str, _info: Any) -> str:
        return "[REDACTED]"


class TachibanaWireCancelRequest(BaseModel):
    """CLMKabuCancelOrder リクエストの wire 専用 class（T1.1）。

    flowsurface `CancelOrderRequest` を pydantic に 1:1 移植。
    """

    model_config = ConfigDict(extra="forbid")

    # 注文番号 (sOrderNumber)
    order_number: str
    # 営業日 (sEigyouDay): YYYYMMDD
    eig_day: str
    # 第二パスワード — repr でマスク
    second_password: str

    def __repr__(self) -> str:
        return (
            f"TachibanaWireCancelRequest("
            f"order_number={self.order_number!r}, eig_day={self.eig_day!r}, "
            f"second_password=[REDACTED])"
        )

    __str__ = __repr__

    @field_serializer("second_password")
    def _mask_second_password(self, v: str, _info: Any) -> str:
        return "[REDACTED]"


# ---------------------------------------------------------------------------
# Phase O1 結果型（T1.1）
# ---------------------------------------------------------------------------


@dataclass
class ModifyOrderResult:
    """modify_order() の戻り値。"""

    client_order_id: str
    venue_order_id: str
    eig_day: str = ""


@dataclass
class CancelOrderResult:
    """cancel_order() の戻り値。"""

    client_order_id: str
    venue_order_id: str
    eig_day: str = ""


@dataclass
class CancelAllResult:
    """cancel_all_orders() の戻り値。"""

    canceled_count: int
    failed_count: int = 0


@dataclass
class OrderRecordWire:
    """fetch_order_list() 戻り値の要素型。architecture.md §3 の OrderRecordWire に対応。"""

    client_order_id: Optional[str]
    venue_order_id: str
    instrument_id: str
    order_side: str
    order_type: str
    quantity: str
    filled_qty: str
    leaves_qty: str
    price: Optional[str]
    time_in_force: str
    status: str
    ts_event_ms: int
    trigger_price: Optional[str] = None
    expire_time_ns: Optional[int] = None
    # P-1: venue フィールド追加（dto.rs OrderRecordWire との IPC 契約一致）
    venue: str = "tachibana"


# ---------------------------------------------------------------------------
# 現在の営業日を取得するヘルパ（CLMOrderList の sSikkouDay に使用）
# ---------------------------------------------------------------------------


def _current_eig_day() -> str:
    """今日の JST 日付を YYYYMMDD 形式で返す。"""
    from datetime import datetime, timedelta, timezone

    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 立花 sCondition / sOrderStatus → nautilus 逆写像（architecture.md §10.2 / §10.5）
# ---------------------------------------------------------------------------

_CONDITION_TO_TIF: dict[tuple[str, str], str] = {
    ("0", "0"): "DAY",          # 当日
    ("2", "*"): "AT_THE_OPEN",  # 寄付
    ("4", "*"): "AT_THE_CLOSE", # 引け
    ("6", "*"): "AT_THE_CLOSE", # 不成
}

_STATUS_TEXT_MAP: dict[str, str] = {
    "受付中": "SUBMITTED",
    "注文中": "ACCEPTED",
    "一部約定": "ACCEPTED",   # nautilus 流: leaves_qty で判定
    "全部約定": "FILLED",
    "取消中": "PENDING_CANCEL",
    "取消済": "CANCELED",
    "失効": "EXPIRED",
    "失敗": "REJECTED",
    "却下": "REJECTED",
}


def _map_condition_to_tif(condition: str, expire_day: str) -> str:
    """立花 sCondition + sOrderExpireDay → nautilus TimeInForce 文字列。"""
    if condition == "0":
        if expire_day and expire_day != "0":
            return "GTD"
        return "DAY"
    elif condition == "2":
        return "AT_THE_OPEN"
    elif condition in ("4", "6"):
        return "AT_THE_CLOSE"
    return "DAY"  # fallback


def _map_status_text(status_text: str) -> str:
    """立花 sOrderStatus テキスト → nautilus OrderStatus 文字列。"""
    return _STATUS_TEXT_MAP.get(status_text, "ACCEPTED")


def _map_side(tachibana_side: str) -> str:
    """立花 sBaibaiKubun → nautilus OrderSide。"""
    # 立花: "3"=買, "1"=売 (architecture.md §10.3)
    return "BUY" if tachibana_side in ("3", "7") else "SELL"


def _map_order_type(order_price: str) -> str:
    """立花 sOrderPrice → nautilus OrderType の簡易逆写像。"""
    if order_price == "0" or order_price == "":
        return "MARKET"
    if order_price.startswith("*"):
        return "MARKET"
    return "LIMIT"


def _order_record_to_wire(
    record: dict[str, Any],
    client_order_id: Optional[str],
) -> "OrderRecordWire":
    """CLMOrderList の 1 件レコード → OrderRecordWire。"""
    import re

    issue_code = record.get("sOrderIssueCode", "")
    instrument_id = f"{issue_code}.TSE" if issue_code else ""

    order_qty = record.get("sOrderOrderSuryou", "0")
    executed_qty = record.get("sOrderYakuzyouSuryo", "0")
    current_qty = record.get("sOrderCurrentSuryou", "0")

    # 残量 = 注文数量 - 約定数量（current_qty でも可）
    try:
        leaves = int(order_qty) - int(executed_qty)
    except (ValueError, TypeError):
        leaves = 0

    order_price = record.get("sOrderOrderPrice", "0")
    # 成行 (order_price=="0") は price=None
    price_value: Optional[str] = None if order_price in ("0", "") else order_price

    status_text = record.get("sOrderStatus", "")
    status = _map_status_text(status_text)

    # 注文日時 (YYYYMMDDHHMMSS) → Unix ms UTC
    order_datetime_str = record.get("sOrderOrderDateTime", "")
    ts_event_ms = 0
    if order_datetime_str and len(order_datetime_str) >= 14:
        try:
            from datetime import datetime, timedelta, timezone

            jst = timezone(timedelta(hours=9))
            dt = datetime.strptime(order_datetime_str[:14], "%Y%m%d%H%M%S").replace(tzinfo=jst)
            ts_event_ms = int(dt.timestamp() * 1000)
        except ValueError:
            pass

    tachibana_side = record.get("sOrderBaibaiKubun", record.get("sBaibaiKubun", ""))
    order_side = _map_side(tachibana_side) if tachibana_side else "BUY"

    return OrderRecordWire(
        client_order_id=client_order_id,
        venue_order_id=record.get("sOrderOrderNumber", ""),
        instrument_id=instrument_id,
        order_side=order_side,
        order_type=_map_order_type(order_price),
        quantity=order_qty,
        filled_qty=executed_qty,
        leaves_qty=str(leaves),
        price=price_value,
        time_in_force="DAY",  # CLMOrderList は sCondition を返さない場合もある
        status=status,
        ts_event_ms=ts_event_ms,
        venue="tachibana",  # P-1: 明示設定
    )


# ---------------------------------------------------------------------------
# Phase O1 Payload helpers
# ---------------------------------------------------------------------------


def _compose_modify_payload(wire: TachibanaWireModifyRequest, p_no_counter: Any) -> dict[str, Any]:
    """TachibanaWireModifyRequest → CLMKabuCorrectOrder HTTP リクエスト dict。"""
    from engine.exchanges.tachibana_helpers import current_p_sd_date

    return {
        "sOrderNumber": wire.order_number,
        "sEigyouDay": wire.eig_day,
        "sCondition": wire.condition,
        "sOrderPrice": wire.price,
        "sOrderSuryou": wire.qty,
        "sOrderExpireDay": wire.expire_day,
        "sSecondPassword": wire.second_password,
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMKabuCorrectOrder",
        "sJsonOfmt": "5",
    }


def _compose_cancel_payload(wire: TachibanaWireCancelRequest, p_no_counter: Any) -> dict[str, Any]:
    """TachibanaWireCancelRequest → CLMKabuCancelOrder HTTP リクエスト dict。"""
    from engine.exchanges.tachibana_helpers import current_p_sd_date

    return {
        "sOrderNumber": wire.order_number,
        "sEigyouDay": wire.eig_day,
        "sSecondPassword": wire.second_password,
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMKabuCancelOrder",
        "sJsonOfmt": "5",
    }


def _compose_order_list_payload(
    p_no_counter: Any,
    eig_day: str = "",
    issue_code: str = "",
    status_filter: str = "",
) -> dict[str, Any]:
    """CLMOrderList HTTP リクエスト dict。"""
    from engine.exchanges.tachibana_helpers import current_p_sd_date

    return {
        "sIssueCode": issue_code,
        "sSikkouDay": eig_day,
        "sOrderSyoukaiStatus": status_filter,
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMOrderList",
        "sJsonOfmt": "5",
    }


# ---------------------------------------------------------------------------
# WAL helpers for O1
# ---------------------------------------------------------------------------


def _audit_log_modify(
    f: Any,
    client_order_id: str,
    venue_order_id: str,
    phase: str,
    reason_code: Optional[str] = None,
    reason_text: Optional[str] = None,
) -> None:
    """WAL に modify 関連行を書く。"""
    record: dict[str, Any] = {
        "phase": phase,
        "ts": _current_ts_ms(),
        "client_order_id": client_order_id,
        "venue_order_id": venue_order_id,
    }
    if reason_code is not None:
        record["reason_code"] = reason_code
    if reason_text is not None:
        record["reason_text"] = reason_text
    line = json.dumps(record, ensure_ascii=True)
    f.write(line + "\n")
    f.flush()


def _audit_log_cancel(
    f: Any,
    client_order_id: str,
    venue_order_id: str,
    phase: str,
) -> None:
    """WAL に cancel 関連行を書く。"""
    record: dict[str, Any] = {
        "phase": phase,
        "ts": _current_ts_ms(),
        "client_order_id": client_order_id,
        "venue_order_id": venue_order_id,
    }
    line = json.dumps(record, ensure_ascii=True)
    f.write(line + "\n")
    f.flush()


# ---------------------------------------------------------------------------
# Phase O1 公開関数（T1.1）
# ---------------------------------------------------------------------------


async def modify_order(
    session: Any,
    second_password: str,
    client_order_id: str,
    venue_order_id: str,
    change: Any,
    *,
    p_no_counter: Optional[Any] = None,
    wal_path: Optional[Path] = None,
) -> ModifyOrderResult:
    """注文訂正 CLMKabuCorrectOrder を送信する（T1.1）。

    Args:
        session: TachibanaSession（url_request 等を持つ）
        second_password: 第二暗証番号
        client_order_id: クライアント注文 ID
        venue_order_id: 立花注文番号 (sOrderNumber)
        change: OrderModifyChange（new_quantity / new_price / new_expire_time_ns 等）
        p_no_counter: PNoCounter（省略時は新規作成）
        wal_path: WAL ファイルパス（省略時は WAL 書き込みなし）

    Raises:
        SessionExpiredError: p_errno=2 時
        TachibanaError: その他 API エラー時
    """
    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    # change から wire フィールドへ写像
    new_qty = getattr(change, "new_quantity", None)
    new_price = getattr(change, "new_price", None)
    # expire_time_ns → YYYYMMDD 変換は Phase O3 で本格実装（現在は変換スキップ）
    # trigger_price の変更は Phase O3 まで未サポート（逆指値訂正 API）

    wire = TachibanaWireModifyRequest(
        order_number=venue_order_id,
        eig_day=_current_eig_day(),
        condition="*",
        price=new_price if new_price is not None else "*",
        qty=new_qty if new_qty is not None else "*",
        expire_day="*",
        second_password=second_password,
    )
    payload = _compose_modify_payload(wire, p_no_counter)
    url = build_request_url(session.url_request, payload, sJsonOfmt="5")

    guard_prod_url(url)

    # WAL: modify phase
    wal_file = None
    if wal_path is not None:
        wal_path.parent.mkdir(parents=True, exist_ok=True)
        wal_file = open(wal_path, "a", encoding="utf-8")  # noqa: SIM115
        try:
            _audit_log_modify(wal_file, client_order_id, venue_order_id, "modify")
        except OSError:
            wal_file.close()
            raise

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = decode_response_body(resp.content)

        data = json.loads(body)
        err = check_response(data)
        if err is not None:
            if wal_file is not None:
                _audit_log_modify(
                    wal_file, client_order_id, venue_order_id, "modify_rejected",
                    reason_code=err.code, reason_text=err.message,
                )
            raise err

        result_order_number = data.get("sOrderNumber") or venue_order_id
        result_eig_day = data.get("sEigyouDay", "")

        if wal_file is not None:
            _audit_log_modify(wal_file, client_order_id, venue_order_id, "modify_accepted")

        return ModifyOrderResult(
            client_order_id=client_order_id,
            venue_order_id=result_order_number,
            eig_day=result_eig_day,
        )
    finally:
        if wal_file is not None:
            wal_file.close()


async def cancel_order(
    session: Any,
    second_password: str,
    client_order_id: str,
    venue_order_id: str,
    *,
    p_no_counter: Optional[Any] = None,
    wal_path: Optional[Path] = None,
) -> CancelOrderResult:
    """注文取消 CLMKabuCancelOrder を送信する（T1.1）。

    Args:
        session: TachibanaSession
        second_password: 第二暗証番号
        client_order_id: クライアント注文 ID（WAL 記録用）
        venue_order_id: 立花注文番号 (sOrderNumber)
        p_no_counter: PNoCounter
        wal_path: WAL ファイルパス

    Raises:
        SessionExpiredError: p_errno=2 時
        TachibanaError: その他 API エラー時
    """
    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    wire = TachibanaWireCancelRequest(
        order_number=venue_order_id,
        eig_day=_current_eig_day(),
        second_password=second_password,
    )
    payload = _compose_cancel_payload(wire, p_no_counter)
    url = build_request_url(session.url_request, payload, sJsonOfmt="5")

    guard_prod_url(url)

    wal_file = None
    if wal_path is not None:
        wal_path.parent.mkdir(parents=True, exist_ok=True)
        wal_file = open(wal_path, "a", encoding="utf-8")  # noqa: SIM115
        try:
            _audit_log_cancel(wal_file, client_order_id, venue_order_id, "cancel")
        except OSError:
            wal_file.close()
            raise

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = decode_response_body(resp.content)

        data = json.loads(body)
        err = check_response(data)
        if err is not None:
            if wal_file is not None:
                _audit_log_cancel(wal_file, client_order_id, venue_order_id, "cancel_rejected")
            raise err

        result_order_number = data.get("sOrderNumber") or venue_order_id
        result_eig_day = data.get("sEigyouDay", "")

        if wal_file is not None:
            _audit_log_cancel(wal_file, client_order_id, venue_order_id, "cancel_accepted")

        return CancelOrderResult(
            client_order_id=client_order_id,
            venue_order_id=result_order_number,
            eig_day=result_eig_day,
        )
    finally:
        if wal_file is not None:
            wal_file.close()


async def cancel_all_orders(
    session: Any,
    second_password: str,
    instrument_id: Optional[str] = None,
    order_side: Optional[str] = None,
    *,
    p_no_counter: Optional[Any] = None,
    wal_path: Optional[Path] = None,
) -> CancelAllResult:
    """全注文取消: CLMOrderList で一覧取得後に個別 CLMKabuCancelOrder で取消す（T1.1）。

    Args:
        session: TachibanaSession
        second_password: 第二暗証番号
        instrument_id: 銘柄絞り込み（例 "7203.TSE"）。None=全銘柄
        order_side: サイド絞り込み（"BUY" / "SELL"）。None=両側
        p_no_counter: PNoCounter
        wal_path: WAL ファイルパス

    Returns:
        CancelAllResult（canceled_count / failed_count）
    """
    from engine.exchanges.tachibana_helpers import (
        PNoCounter,
        SecondPasswordInvalidError,
        SessionExpiredError,
    )

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    # issue_code 抽出（"7203.TSE" → "7203"）
    issue_code = ""
    if instrument_id:
        issue_code = instrument_id.split(".")[0]

    # 一覧取得
    records = await fetch_order_list(
        session=session,
        filter=None,
        _issue_code=issue_code,
        p_no_counter=p_no_counter,
    )

    canceled = 0
    failed = 0
    for record in records:
        # 取消可能ステータスのみ対象
        if record.status not in ("SUBMITTED", "ACCEPTED"):
            continue
        # サイドフィルタ
        if order_side and record.order_side != order_side:
            continue
        try:
            await cancel_order(
                session=session,
                second_password=second_password,
                client_order_id=record.client_order_id or record.venue_order_id,
                venue_order_id=record.venue_order_id,
                p_no_counter=p_no_counter,
                wal_path=wal_path,
            )
            canceled += 1
        except SecondPasswordInvalidError:
            logger.warning(
                "cancel_all_orders: second_password invalid for %s", record.venue_order_id
            )
            failed += 1
            raise  # _do_cancel_all_orders で on_invalid() を呼ばせる
        except SessionExpiredError:
            logger.error(
                "cancel_all_orders: session expired for %s", record.venue_order_id
            )
            raise  # セッション切れは全ループ中断
        except Exception as e:
            logger.warning(
                "cancel_all_orders: failed to cancel %s: %s", record.venue_order_id, e
            )
            failed += 1

    return CancelAllResult(canceled_count=canceled, failed_count=failed)


async def fetch_order_list(
    session: Any,
    filter: Optional[Any] = None,
    *,
    _issue_code: str = "",
    p_no_counter: Optional[Any] = None,
    client_order_id_lookup: Optional[dict[str, str]] = None,
) -> list[OrderRecordWire]:
    """注文一覧取得 CLMOrderList を呼ぶ（T1.1）。

    Args:
        session: TachibanaSession
        filter: OrderListFilter（status / instrument_id / date）
        _issue_code: 銘柄コード（内部 cancel_all_orders からの呼出用）
        p_no_counter: PNoCounter
        client_order_id_lookup: venue_order_id → client_order_id の逆引き辞書
            （WAL から構築。省略時は全レコード client_order_id=None）

    Returns:
        OrderRecordWire のリスト
    """
    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url
    from engine.exchanges.tachibana_codec import deserialize_tachibana_list

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    # フィルタから instrument_id を解決
    issue_code = _issue_code
    eig_day = ""
    if filter is not None:
        if getattr(filter, "instrument_id", None):
            issue_code = filter.instrument_id.split(".")[0]
        if getattr(filter, "date", None):
            # date フィールドは YYYYMMDD 形式で sSikkouDay に渡す
            eig_day = filter.date

    payload = _compose_order_list_payload(p_no_counter, eig_day=eig_day, issue_code=issue_code)
    url = build_request_url(session.url_request, payload, sJsonOfmt="5")

    guard_prod_url(url)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        body = decode_response_body(resp.content)

    data = json.loads(body)
    err = check_response(data)
    if err is not None:
        raise err

    # aOrderList: 空の場合は "" か [] として返る（deserialize_tachibana_list で正規化）
    raw_list = data.get("aOrderList", "")
    order_records = deserialize_tachibana_list(raw_list)

    lookup = client_order_id_lookup or {}

    result: list[OrderRecordWire] = []
    for raw in order_records:
        venue_order_id = raw.get("sOrderOrderNumber", "")
        client_order_id = lookup.get(venue_order_id)
        result.append(_order_record_to_wire(raw, client_order_id))

    # status フィルタ適用
    if filter is not None and getattr(filter, "status", None):
        result = [r for r in result if r.status == filter.status]

    return result


# ---------------------------------------------------------------------------
# Phase O3 — T3.2 余力・建玉 API
# ---------------------------------------------------------------------------


@dataclass
class BuyingPowerResult:
    """fetch_buying_power() の戻り値 (CLMZanKaiKanougaku)。"""

    available_amount: int  # 現物買付可能額合計 (sZanKaiKanougakuGoukei)
    shortfall: int = 0    # 余力不足額 (sZanKaiKanougakuHusoku)


@dataclass
class CreditBuyingPowerResult:
    """fetch_credit_buying_power() の戻り値 (CLMZanShinkiKanoIjiritu)。"""

    available_amount: int  # 信用新規可能額 (sZanShinkiKanoIjirituGoukei)
    shortfall: int = 0


@dataclass
class SellableQtyResult:
    """fetch_sellable_qty() の戻り値 (CLMZanUriKanousuu)。"""

    sellable_qty: int  # 売可能数量 (sZanUriKanouSuu)


@dataclass
class PositionRecord:
    """fetch_positions() の要素型。現物・建玉の両方を統一型で表現。"""

    instrument_id: str       # "<sIssueCode>.TSE"
    qty: int                 # 保有数量
    market_value: int = 0   # 評価額（円）
    position_type: str = "cash"  # "cash" | "margin_credit" | "margin_general"
    tategyoku_id: Optional[str] = None  # 建玉 ID（信用建玉のみ）


class InsufficientFundsError(Exception):
    """余力不足時に raise される例外（T3.3 で HTTP 403 に写される）。"""

    def __init__(self, message: str, *, shortfall: int = 0) -> None:
        self.reason_code = "INSUFFICIENT_FUNDS"
        self.shortfall = shortfall
        super().__init__(message)


async def fetch_buying_power(
    session: Any,
    *,
    p_no_counter: Optional[Any] = None,
) -> BuyingPowerResult:
    """CLMZanKaiKanougaku — 現物買付余力を取得する（T3.2）。

    Args:
        session: TachibanaSession
        p_no_counter: PNoCounter（省略時は新規作成）

    Returns:
        BuyingPowerResult（available_amount, shortfall）

    Raises:
        SessionExpiredError: p_errno=2 時
        TachibanaError: その他 API エラー時
    """
    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response, current_p_sd_date
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    payload: dict[str, Any] = {
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMZanKaiKanougaku",
        "sJsonOfmt": "5",
    }
    url = build_request_url(session.url_request, payload, sJsonOfmt="5")
    guard_prod_url(url)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        body = decode_response_body(resp.content)

    data = json.loads(body)
    err = check_response(data)
    if err is not None:
        raise err

    available = int(data.get("sSummaryGenkabuKaituke", "0") or "0")
    shortfall = 1 if data.get("sHusokukinHasseiFlg", "0") == "1" else 0
    return BuyingPowerResult(available_amount=available, shortfall=shortfall)


async def fetch_credit_buying_power(
    session: Any,
    *,
    p_no_counter: Optional[Any] = None,
) -> CreditBuyingPowerResult:
    """CLMZanShinkiKanoIjiritu — 信用新規可能額を取得する（T3.2）。

    Args:
        session: TachibanaSession
        p_no_counter: PNoCounter（省略時は新規作成）

    Returns:
        CreditBuyingPowerResult

    Raises:
        SessionExpiredError: p_errno=2 時
        TachibanaError: その他 API エラー時
    """
    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response, current_p_sd_date
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    payload: dict[str, Any] = {
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMZanShinkiKanoIjiritu",
        "sJsonOfmt": "5",
    }
    url = build_request_url(session.url_request, payload, sJsonOfmt="5")
    guard_prod_url(url)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        body = decode_response_body(resp.content)

    data = json.loads(body)
    err = check_response(data)
    if err is not None:
        raise err

    available = int(data.get("sSummarySinyouSinkidate", "0") or "0")
    shortfall = 0
    return CreditBuyingPowerResult(available_amount=available, shortfall=shortfall)


async def fetch_sellable_qty(
    session: Any,
    instrument_id: str,
    *,
    p_no_counter: Optional[Any] = None,
) -> SellableQtyResult:
    """CLMZanUriKanousuu — 売可能数量を取得する（T3.2）。

    Args:
        session: TachibanaSession
        instrument_id: 銘柄 ID（例 "7203.T/TSE"）
        p_no_counter: PNoCounter（省略時は新規作成）

    Returns:
        SellableQtyResult

    Raises:
        SessionExpiredError: p_errno=2 時
        TachibanaError: その他 API エラー時
    """
    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response, current_p_sd_date
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    issue_code = instrument_id.split(".")[0]

    payload: dict[str, Any] = {
        "sIssueCode": issue_code,
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMZanUriKanousuu",
        "sJsonOfmt": "5",
    }
    url = build_request_url(session.url_request, payload, sJsonOfmt="5")
    guard_prod_url(url)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        body = decode_response_body(resp.content)

    data = json.loads(body)
    err = check_response(data)
    if err is not None:
        raise err

    qty = int(data.get("sZanUriKanouSuu", "0") or "0")
    return SellableQtyResult(sellable_qty=qty)


async def fetch_positions(
    session: Any,
    *,
    p_no_counter: Optional[Any] = None,
) -> list[PositionRecord]:
    """CLMGenbutuKabuList + CLMShinyouTategyokuList — 現物・建玉一覧を取得する（T3.2）。

    Args:
        session: TachibanaSession
        p_no_counter: PNoCounter（省略時は新規作成）

    Returns:
        PositionRecord のリスト（現物 + 信用建玉）

    Raises:
        SessionExpiredError: p_errno=2 時
        TachibanaError: その他 API エラー時
    """
    import httpx

    from engine.exchanges.tachibana_codec import decode_response_body, deserialize_tachibana_list
    from engine.exchanges.tachibana_helpers import PNoCounter, check_response, current_p_sd_date
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url

    if p_no_counter is None:
        p_no_counter = PNoCounter()

    results: list[PositionRecord] = []

    # --- 現物残高 (CLMGenbutuKabuList) ---
    cash_payload: dict[str, Any] = {
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMGenbutuKabuList",
        "sJsonOfmt": "5",
    }
    cash_url = build_request_url(session.url_request, cash_payload, sJsonOfmt="5")
    guard_prod_url(cash_url)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(cash_url)
        resp.raise_for_status()
        body = decode_response_body(resp.content)

    data = json.loads(body)
    err = check_response(data)
    if err is not None:
        raise err

    cash_list = deserialize_tachibana_list(data.get("aGenbutuKabuList", ""))
    for rec in cash_list:
        issue_code = rec.get("sIssueCode", "")
        qty_str = rec.get("sGenbutuZanSuu", "0") or "0"
        val_str = rec.get("sGenbutuZanKingaku", "0") or "0"
        results.append(
            PositionRecord(
                instrument_id=f"{issue_code}.TSE",
                qty=int(qty_str),
                market_value=int(val_str),
                position_type="cash",
            )
        )

    # --- 信用建玉 (CLMShinyouTategyokuList) ---
    margin_payload: dict[str, Any] = {
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMShinyouTategyokuList",
        "sJsonOfmt": "5",
    }
    margin_url = build_request_url(session.url_request, margin_payload, sJsonOfmt="5")
    guard_prod_url(margin_url)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(margin_url)
        resp.raise_for_status()
        body = decode_response_body(resp.content)

    data = json.loads(body)
    err = check_response(data)
    if err is not None:
        raise err

    margin_list = deserialize_tachibana_list(data.get("aTategyokuList", ""))
    for rec in margin_list:
        issue_code = rec.get("sIssueCode", "")
        qty_str = rec.get("sTategyokuZanSuu", "0") or "0"
        tategyoku_id = rec.get("sTategyokuNumber")
        results.append(
            PositionRecord(
                instrument_id=f"{issue_code}.TSE",
                qty=int(qty_str),
                position_type="margin_credit",
                tategyoku_id=tategyoku_id,
            )
        )

    return results
