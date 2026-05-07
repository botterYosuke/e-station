"""KabuStation adapter — venue 固有 PUSH JSON を共通 boundary モデルへ変換する。

責務:
- venue 固有制約の即時検査（コンストラクタで 50 銘柄上限など）
- PUSH 配信 JSON → `OrderBook` / `Trade` への単一責務変換

非責務:
- 永続化 / 配信（server.py + mappers.py が wire DTO へ変換する）
- wire 責務フィールド（event / venue / market / request_id）の保持

kabuStation PUSH メッセージは 1 銘柄スナップショット（`Sell1`〜`Sell10`・`Buy1`〜`Buy10`
の各 `{Price, Qty, Time, Sign}` と `CurrentPrice` / `CurrentPriceTime` /
`TradingVolume`）。差分配信（diff）形式は無いため `parse_board()` のみを提供する。

sequence_id 採番:
- kabuStation の PUSH JSON は `sequence_id` / `prev_sequence_id` を持たない。
- 本 adapter は OrderBook 用に snapshot を返すのみであり、DepthDiff の連番管理は
  実施しない（diff 配信が存在しないため）。サーバ側で gap recovery が必要な場合は
  別途 sequence machine を持たせる方針。

参考: ImplementationLoop-plan.md / 🔵adapter-type-boundary.md Step 2
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from engine.models import OrderBook, Trade

# /register で同時に維持できる銘柄数の上限（comparison.md §7 / INV-K1-CAP と同じ値）。
KABU_MAX_SYMBOLS: int = 50

# kabuStation PUSH は最大 10 段（Sell1〜Sell10 / Buy1〜Buy10）まで配信する。
_MAX_DEPTH_LEVELS: int = 10

# JST タイムゾーン（kabuStation の `CurrentPriceTime` / 各レベル `Time` は JST ISO8601）。
_JST = timezone(timedelta(hours=9))

# kabu PUSH `Sign` の解釈（comparison.md §4 / INV-K2-SIDE-BUY-2）:
# 立花の "1"=売 / "3"=買 とは互換性がない。kabu は CurrentPrice の符号フィールドが
# `0101`〜`0103` 等で表現されるが、ここでは PUSH に含まれる範囲では trade side を
# 確定できないため `"unknown"` で正規化する。サーバ側で `KabuExecution` 経路から
# side を埋める場合は別途上書き可能。


class KabuStationAdapter:
    """kabuStation PUSH 配信 JSON を boundary モデルへ変換する単一責務 adapter。"""

    def __init__(self, symbols: Iterable[str]) -> None:
        symbol_list = list(symbols)
        if len(symbol_list) > KABU_MAX_SYMBOLS:
            raise ValueError(
                f"kabuStation /register は最大 {KABU_MAX_SYMBOLS} 銘柄。"
                f"{len(symbol_list)} 件は超過。"
            )
        # 重複は許容しない（PUT /register が衝突するため）。
        seen: set[str] = set()
        for s in symbol_list:
            if s in seen:
                raise ValueError(f"duplicate symbol: {s}")
            seen.add(s)
        self._symbols: tuple[str, ...] = tuple(symbol_list)

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    # ------------------------------------------------------------------
    # parse helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_jst_to_utc(raw_ts: str | None) -> datetime:
        """`CurrentPriceTime` (`"2020-07-22T15:00:00+09:00"` 等) を UTC `datetime` へ。

        - tz 情報付き ISO8601 ならそのまま `datetime.fromisoformat` で解釈し UTC へ変換。
        - tz 情報なし（過去仕様の naive 文字列）の場合は JST と仮定して UTC 化する。
        - `None` / 空文字列の場合は `ValueError`。
        """
        if not raw_ts:
            raise ValueError("missing timestamp")
        ts = datetime.fromisoformat(raw_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_JST)
        return ts.astimezone(timezone.utc)

    @staticmethod
    def _collect_levels(raw: dict, prefix: str) -> list[tuple[Decimal, Decimal]]:
        """`Sell1`..`Sell10` / `Buy1`..`Buy10` を `(price, qty)` 列に揃える。

        欠損段は無視する。`Price` / `Qty` が `None` のレベルもスキップ。
        """
        levels: list[tuple[Decimal, Decimal]] = []
        for n in range(1, _MAX_DEPTH_LEVELS + 1):
            entry = raw.get(f"{prefix}{n}")
            if not isinstance(entry, dict):
                continue
            price = entry.get("Price")
            qty = entry.get("Qty")
            if price is None or qty is None:
                continue
            levels.append((Decimal(str(price)), Decimal(str(qty))))
        return levels

    # ------------------------------------------------------------------
    # public conversion API
    # ------------------------------------------------------------------

    def parse_board(self, raw: dict[str, Any]) -> OrderBook:
        """PUSH 板スナップショット JSON → `OrderBook`。

        - `bids` は Buy1（最良値）→ Buy10 順
        - `asks` は Sell1（最良値）→ Sell10 順
        - `timestamp` は `CurrentPriceTime` を UTC 化
        - `stream_session_id` / `sequence_id` は kabu PUSH に存在しないため `None`
        """
        symbol = str(raw["Symbol"])
        ts_utc = self._parse_jst_to_utc(raw.get("CurrentPriceTime"))
        bids = self._collect_levels(raw, "Buy")
        asks = self._collect_levels(raw, "Sell")
        return OrderBook(
            symbol=symbol,
            timestamp=ts_utc,
            bids=bids,
            asks=asks,
        )

    def parse_execution(self, raw: dict[str, Any]) -> Trade:
        """PUSH JSON の `CurrentPrice` 部分から最新約定の `Trade` を抽出する。

        kabuStation PUSH には独立した約定ストリームが無く、板スナップショットの
        `CurrentPrice` / `CurrentPriceTime` / 1 単位 lot を基にする。`side` は
        PUSH JSON だけでは確定できないため `"unknown"` で正規化する（必要に応じて
        サーバ側で上書きする）。
        """
        symbol = str(raw["Symbol"])
        ts_utc = self._parse_jst_to_utc(raw.get("CurrentPriceTime"))
        price_raw = raw.get("CurrentPrice")
        if price_raw is None:
            raise ValueError("CurrentPrice missing")
        # PUSH JSON は約定単位を返さないため qty=0 で正規化する（TradingVolume は累積で
        # 1 件分の数量ではないため誤用を避ける）。
        return Trade(
            symbol=symbol,
            timestamp=ts_utc,
            price=Decimal(str(price_raw)),
            qty=Decimal("0"),
            side="unknown",
        )


__all__ = ["KabuStationAdapter", "KABU_MAX_SYMBOLS"]
