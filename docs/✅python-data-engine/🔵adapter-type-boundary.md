# 改修プラン: 取引所 adapter — 型で境界を固める

作成日: 2026-05-07

## 背景と動機

現状は exchange ごとに変換ロジックが ad-hoc に分散しており、どのフィールドがどの型で返るかが adapter ファイルを読まないと分からない。新しい venue を追加するたびに「他の adapter は何を返していたっけ？」と各ファイルを横断確認する必要がある。

この改修は、共通 dataclass（pydantic v2）を **一か所** に定義し、各 adapter は「その型に変換するだけ」という単一責務にまとめることを目標とする。

---

## 共通モデル定義

`python/engine/models.py`（新規）に pydantic v2 BaseModel として定義する。

```python
# python/engine/models.py

from __future__ import annotations
from decimal import Decimal
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict


class Instrument(BaseModel):
    """取引可能な銘柄の静的属性。"""
    model_config = ConfigDict(frozen=True)

    symbol: str           # 取引所固有のシンボル文字列
    display_name: str
    price_tick: Decimal   # 最小呼値
    lot_size: int         # 最小売買単位（株 / 枚）
    currency: str         # 例: "JPY"


class OrderBook(BaseModel):
    """板情報スナップショット（DepthSnapshot に対応）。"""
    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime
    bids: list[tuple[Decimal, Decimal]]  # [(price, qty), ...]
    asks: list[tuple[Decimal, Decimal]]
    stream_session_id: str | None = None   # WS 接続ごとの ID（gap recovery 用）
    sequence_id: int | None = None          # 適用済みシーケンス番号


class DepthDiff(BaseModel):
    """板差分更新（DepthDiff に対応）。gap recovery の不変条件を保持する。"""
    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime
    bids: list[tuple[Decimal, Decimal]]  # [(price, qty), ...]（qty=0 は削除）
    asks: list[tuple[Decimal, Decimal]]
    stream_session_id: str              # WS 再接続ごとに変化
    sequence_id: int                    # このdiffのシーケンス番号
    prev_sequence_id: int               # 前の diff の sequence_id（連続性チェック用）


class Trade(BaseModel):
    """約定履歴の 1 件。"""
    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime
    price: Decimal
    qty: Decimal
    side: Literal["buy", "sell", "unknown"]
```

### 設計方針

- **Decimal** を使う。float は浮動小数点誤差が価格計算に混入するため使わない。
- `timestamp` は **timezone-aware** `datetime`（UTC）で統一する。adapter 側で naive datetime を受け取った場合は `replace(tzinfo=timezone.utc)` して変換する。⚠ kabuStation の timestamp が UTC か JST かを確認すること。JST の場合は `replace(tzinfo=timezone.utc)` ではなく `datetime.fromisoformat(raw_ts).replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc)` パターンを使う。Step 2 のテストに timezone roundtrip を必須項目として追加する。
- モデルは immutable（`model_config = ConfigDict(frozen=True)`）にする。変更は新インスタンスを作る。

---

## adapter の責務

各 adapter クラスは以下の 2 点だけを担う。

1. **コンストラクタでベニュー固有制約を検査して落とす**
2. **venue の生データを共通モデル (`Instrument` / `OrderBook` / `Trade` / `DepthDiff`) に変換して返す**

adapter boundary の型は `Instrument` / `OrderBook` / `Trade` / `DepthDiff` の 4 種類である。`DepthDiff` は gap recovery の要件を担うモデルであり、adapter contract に正式に含まれる。

ビジネスロジック・永続化・配信はしない。

### kabuStation adapter の例

```python
# python/engine/exchanges/kabusapi_adapter.py

from ..models import Instrument, OrderBook, DepthDiff, Trade

KABU_MAX_SYMBOLS = 50  # /register 銘柄上限

class KabuStationAdapter:
    def __init__(self, symbols: list[str]) -> None:
        if len(symbols) > KABU_MAX_SYMBOLS:
            raise ValueError(
                f"kabuStation /register は最大 {KABU_MAX_SYMBOLS} 銘柄。"
                f"{len(symbols)} 件は超過。"
            )
        self._symbols = symbols

    def parse_board(self, raw: dict) -> OrderBook:
        """PUSH 配信の板情報 JSON → OrderBook（DepthSnapshot）"""
        ...

    def parse_board_diff(self, raw: dict) -> DepthDiff:
        """PUSH 配信の差分更新 JSON → DepthDiff。
        stream_session_id / sequence_id / prev_sequence_id は raw データから取得。
        取引所が提供しない場合は adapter 内で採番する。"""
        ...

    def parse_execution(self, raw: dict) -> Trade:
        """PUSH 配信の約定 JSON → Trade"""
        ...
```

### 制約検査の原則

ベニュー固有の制約は **コンストラクタで即座に `raise`** する。呼び出し側は「adapter が返った = 制約チェック済み」と信頼できる。遅延検査（変換時に初めてエラー）は禁止。

| ベニュー | 制約 | 検査箇所 |
|---|---|---|
| kabuStation | `/register` 上限 50 銘柄 | `KabuStationAdapter.__init__` |
| （将来追加） | 任意 | 各 adapter の `__init__` |

---

## 移行ステップ

### Step 1: モデル定義と単体テスト（リスクなし）

> **本計画の目標**: 「adapter まで model 化」。adapter（`kabusapi.py` 等）が Pydantic model を返し、`server.py` がそれを受け取って `.model_dump(mode="json")` する形に変換する。現行の worker → `outbox.append({...})` で wire-format dict を直接書くパスは Step 2 の廃止対象であり、Step 3 完了時に完全削除する。

- `python/engine/models.py` を追加。`Instrument` / `OrderBook` / `Trade` / `DepthDiff` の 4 モデルを定義する。
- `DepthDiff` には必須フィールド `stream_session_id: str`・`sequence_id: int`・`prev_sequence_id: int`・`symbol: str`・`timestamp: datetime`・`bids: list[tuple[Decimal, Decimal]]`・`asks: list[tuple[Decimal, Decimal]]` を含めること。
- `python/tests/test_models.py` で pydantic バリデーション・Decimal 精度・side バリデーションを確認。`DepthDiff` のフィールド欠損時に `ValidationError` が出ることも対象とする。
- 既存コードへの変更なし。

### Step 2: kabuStation adapter をモデルに対応

> ⚠ **実装境界の確認**: 現行の `ExchangeWorker`（`base.py`）は **コンストラクタに `outbox` を受け取らない**。outbox は `stream_trades()` / `stream_depth()` / `stream_kline()` の **メソッド引数**として渡される（シグネチャ: `outbox: list[dict]`）。実行時に渡されるのは `server.py` の `_Broadcaster` インスタンスであり、各 venue worker は `outbox.append({...})` で wire-format dict を書き込む（`asyncio.Queue` ではなく `_Broadcaster.append()` を呼ぶ）。`server.py` はこの `_Broadcaster` インスタンス（`self._outbox`）を `worker.stream_*()` 呼び出し時に直接渡すだけで中継しない。この構造では、adapter のメソッド（`parse_board()`・`parse_board_diff()`）を追加するだけでは outbox へ書き込まれる wire-format を置き換えられない。
> なお `outbox` 引数の型ヒントは `list[dict]` だが、実行時は `server.py` の `_Broadcaster` インスタンスが渡される。`_Broadcaster` は `append()` を実装し list 互換として振る舞う（duck typing）。B1 実装時に型チェッカーが警告を出す場合は、この duck typing を意識すること。
>
> **B1 の実際の作業範囲**: `KabuStationAdapter` が pydantic モデルを返すメソッドを持つだけでなく、`KabuStationWorker` が outbox に書き込む前に adapter の変換を通すよう、worker の streaming コード（`stream_depth()`・`stream_trades()`）を変更する必要がある。この変更量は当初の想定より大きい可能性があるため、作業前に `python/engine/exchanges/kabusapi/` の worker 実装を確認すること。

`kabusapi_adapter.py` は新規作成ファイル。既存 `exchanges/kabusapi.py`（または `kabusapi_ws.py`）の板・約定パース部分を移植する。既存ファイルの削除・修正は Step 2 のスコープ外。

> ⚠ **他 venue との境界方針**: `kabusapi_adapter.py` を先に作っても、他の venue worker（Tachibana 等）は引き続き wire-format dict を `_Broadcaster.append()` に直接書く旧構造のまま残る。kabu 先行後に `models.py` の型変換層と wire-format 直書き層が並存する二重境界が発生する。この状態を許容して段階移行するか、全 venue worker を Step 2 で一括移行するかを事前に決めること。段階移行を選ぶ場合は Step 3 完了まで旧 venue は旧境界で動作する期間が生まれることをドキュメントに明記する。

- `KabuStationAdapter` のコンストラクタに 50 件チェックを追加。
- `parse_board` / `parse_execution` を実装し、戻り値を `OrderBook` / `Trade` に変更。
- `python/tests/test_kabusapi_adapter.py` でラウンドトリップ（生 JSON → モデル）を検証。

> **migration path（dict 直書きパスの段階移行）**: 現行の dict 直書きパス（`worker → outbox.append({...})`）を Pydantic model 化パス（`worker → model → outbox.append(model.model_dump(mode='json'))`）に段階移行する。この移行中は両パスが並存することを許容し、Step 3 完了時に dict 直書きパスを完全削除する。他 venue（Tachibana 等）は Step 3 完了まで旧境界（dict 直書き）のまま動作する。

### Step 3: server.py の配信パスを adapter 経由に差し替え

> ⚠ **C1 の適用範囲**: `server.py` が outbox を直接 worker に渡す構造を変えない限り、`server.py` 側だけを変更しても効果がない。C1 は「B1 で worker 側が pydantic モデルを outbox に入れるようになった後に、server.py がそれを `.model_dump(mode="json")` する」という順序依存がある。B1 完了前に C1 に着手しないこと。

- `server.py` が adapter の変換結果（pydantic モデル）を受け取り、`.model_dump(mode="json")` を使って IPC JSON に変換する（`mode="json"` により Decimal が str に変換される）。
- IPC スキーマ（`SCHEMA_MAJOR` / `SCHEMA_MINOR`）は **変更しない**。変換は adapter ↔ server の内部境界で完結する。
- **gap recovery フィールドの保持**: `DepthDiff.stream_session_id`・`sequence_id`・`prev_sequence_id` は `server.py` が `.model_dump(mode="json")` して IPC JSON に変換する際にそのまま渡す。これらが欠落すると Rust 側の gap recovery（`RequestDepthSnapshot` の自動発行）が機能しない。`SCHEMA_MAJOR/MINOR` は変更しないが、`DepthDiff` イベントの必須フィールドは現行 `schemas.py` の `DepthDiff` クラス定義と一致させること。
- `engine_runner.py` への変更は ImplementationLoop-plan.md C1 のスコープ外。
- [ ] **`.model_dump(mode="json")` wire compatibility テスト**: serialization 変更前後で生成 JSON の形式が変わらないことを確認するテストを追加する。`Decimal`（文字列 vs 数値）・`datetime`（タイムゾーン有無）・`None` フィールドの扱いを対象とする。IPC スキーマバージョン不変でも wire 互換性は別途確認が必要。
- [ ] **Step 3 完了条件（統合テスト必須）**: 本番経路（`worker → model → server.py → model_dump`）を通る統合テストが PASS すること。model 化の単体テストだけでは不十分であり、`test_server_adapter_integration.py` 等で実際の worker → outbox → server の経路を E2E で通すテストが必要。

### Step 4: 他 adapter（将来）

新 venue を追加するときは `models.py` のモデルに変換する `parse_*` メソッドを実装し、コンストラクタに venue 固有制約を書くだけ。共通モデルを変更する必要は原則ない。

---

## テスト方針

| テスト種別 | 対象 | 確認項目 |
|---|---|---|
| 単体 | `models.py` | Decimal 精度、side バリデーション、immutability（`frozen=True` により `with pytest.raises(ValidationError): obj.price = Decimal("0")` が通ること） |
| 単体 | 各 adapter | 生 JSON → 共通モデルへの変換精度、制約違反時の `ValueError` |
| 単体 | 各 adapter | 不正 JSON / 欠損フィールド / 型違反入力で `pytest.raises(ValidationError)` が送出される |
| 単体 | DepthDiff モデル | sequence_id の連続性・stream_session_id の一意性を確認。不正 raw JSON で ValidationError が出ることを確認 |
| 統合 | `server.py` + adapter | adapter 出力が IPC JSON に正しくシリアライズされる（Decimal → str 変換確認を含む）。テストファイル: `python/tests/test_server_adapter_integration.py`。実行: `pytest python/tests/test_server_adapter_integration.py` |

> ⚠ **設計境界**: Pydantic モデルが保証するのは「1 メッセージ内のフィールド正当性」のみ。
> stream の連続性（sequence_id ギャップ検知・stream_session_id 切替検知）は
> adapter の state machine または server 統合テスト（`test_server_multi_client.py` 等）の責務。
> モデル単体テストではカバーしない。

E2E（`ReplaySession` / `LiveSession`）は変更なし。IPC 境界を変えないため既存テストがそのまま通る想定。

---

## 非目標

- IPC スキーマ（`events.json` / `commands.json`）の変更
- Rust 側への影響
- adapter 内への永続化・配信ロジックの追加
