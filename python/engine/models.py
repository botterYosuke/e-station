"""adapter boundary 共通モデル（pydantic v2, frozen）。

各 exchange adapter が venue 固有の生 JSON を変換して返す内部正規化型。
wire 責務（event / venue / market / request_id）はここに含めない。これらは
`python/engine/mappers.py` が wire DTO（schemas.py 定義）に変換する際に補充する。

設計方針:
- price / qty は `Decimal` 統一（float 禁止、価格計算誤差を排除）。
- timestamp は timezone-aware `datetime`（UTC）統一。venue が naive を返す場合は
  adapter 側で UTC 化する。
- frozen=True により immutable。変更は新インスタンスを生成する。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


TradeSide = Literal["buy", "sell", "unknown"]


class _BoundaryModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=False)


class Instrument(_BoundaryModel):
    """取引可能な銘柄の静的属性。"""

    symbol: str
    display_name: str
    price_tick: Decimal
    lot_size: int
    currency: str

    @field_validator("price_tick")
    @classmethod
    def _tick_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("price_tick must be > 0")
        return v

    @field_validator("lot_size")
    @classmethod
    def _lot_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("lot_size must be > 0")
        return v


class OrderBook(_BoundaryModel):
    """板情報スナップショット（DepthSnapshot に対応）。"""

    symbol: str
    timestamp: datetime
    bids: list[tuple[Decimal, Decimal]]
    asks: list[tuple[Decimal, Decimal]]
    stream_session_id: str | None = None
    sequence_id: int | None = None

    @field_validator("timestamp")
    @classmethod
    def _ts_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return v


class DepthDiff(_BoundaryModel):
    """板差分更新。gap recovery の不変条件を保持する。

    - `bids` / `asks` の qty=0 は当該価格レベルの削除を意味する。
    - `stream_session_id` は WS 再接続ごとに変化する識別子。
    - `prev_sequence_id` の連続性チェックで gap を検出する（adapter / server 側責務）。
    """

    symbol: str
    timestamp: datetime
    bids: list[tuple[Decimal, Decimal]]
    asks: list[tuple[Decimal, Decimal]]
    stream_session_id: str
    sequence_id: int
    prev_sequence_id: int

    @field_validator("timestamp")
    @classmethod
    def _ts_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return v


class Trade(_BoundaryModel):
    """約定履歴の 1 件。"""

    symbol: str
    timestamp: datetime
    price: Decimal
    qty: Decimal
    side: TradeSide

    @field_validator("timestamp")
    @classmethod
    def _ts_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return v

    @field_validator("price", "qty")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("price/qty must be >= 0")
        return v


__all__ = [
    "Instrument",
    "OrderBook",
    "DepthDiff",
    "Trade",
    "TradeSide",
]
