# nautilus_trader 統合: データマッピング

nautilus の型 ↔ 立花 API / EventStore の対応表。
写像の実装は以下のファイルに集約する:

| 種別 | 実装ファイル |
|---|---|
| Bar / OHLCV | `python/engine/nautilus/data_loader.py` |
| Instrument | `python/engine/nautilus/instrument_factory.py`（N1 で新設） |
| OrderType / TimeInForce ↔ 立花 | `python/engine/exchanges/tachibana_orders._compose_request_payload` |
| EC frame → nautilus OrderEvent | `python/engine/nautilus/clients/tachibana_event_bridge.py` |
| AccountState / Position | `python/engine/nautilus/clients/tachibana.py` |
| Cache warm-up | `python/engine/nautilus/clients/tachibana.py::_warm_up_cache` |

---

## 1. Bar（OHLCV）

立花の `CLMMfdsGetMarketPriceHistory` レスポンス → nautilus `Bar`

| 立花フィールド | nautilus `Bar` フィールド | 型 | 備考 |
|---|---|---|---|
| `sDate` (YYYYMMDD) | `ts_event` | nanoseconds (`int`) | JST → UTC 変換必須。`datetime(Y,M,D,15,30, tz=JST).timestamp_ns()` を使う（立花日足は終値時刻=15:30 JST が慣習） |
| `sDate` | `ts_init` | nanoseconds | `ts_event` と同値 |
| `sOpen` | `open` | `Price` | 文字列 → `Price(value, precision=1)` |
| `sHigh` | `high` | `Price` | 同上 |
| `sLow` | `low` | `Price` | 同上 |
| `sClose` | `close` | `Price` | 同上 |
| `sVolume` | `volume` | `Quantity` | 文字列 → `Quantity(value, precision=0)`（株数は整数） |
| — | `bar_type` | `BarType` | `"{instrument_id}-1-DAY-MID-EXTERNAL"` |

**精度（H2）**: `Price` / `Quantity` の `precision` は `Instrument.price_precision` / `size_precision` から取る（Instrument が先に確定している必要あり）。N0 ではハードコード（price=1, size=0）で始め、N1 で Instrument 経由に切り替える。

**テスト**: `tests/python/test_data_mapping_bar.py` — `Bar.ts_event` の JST→UTC ns 変換境界値・`open`/`high`/`low`/`close`/`volume` の精度（文字列 → Decimal → nautilus Quantity）を検証。

---

## 2. Instrument（銘柄）

nautilus の `Equity` / `CurrencyPair` のうち、立花株式は **`Equity` 型**を使う。

| nautilus `Equity` フィールド | 立花マスタソース | 値 / 備考 |
|---|---|---|
| `instrument_id` | `sIssueCode` + `"TSE"` | `"7203.TSE"` 形式（[order/spec.md §6.1](../order/spec.md#61-用語型の整合必須)） |
| `raw_symbol` | `sIssueCode` | `"7203"` |
| `currency` | 固定 | `Currency.from_str("JPY")` |
| `price_precision` | 呼値テーブル（Q8 の決定に従う） | N0 仮置き: `1`（0.1 円刻み。Q8 確定後に修正） |
| `price_increment` | 呼値テーブル | N0 仮置き: `Price(0.1, precision=1)` |
| `size_precision` | 固定 | `0`（株数は整数） |
| `size_increment` | `sHikaku`（売買単位） | `Quantity(sHikaku, precision=0)` |
| `lot_size` | `sHikaku` | `Quantity(sHikaku, precision=0)` |
| `max_quantity` | 起動 config の上限 | [order/spec.md §3.2](../order/spec.md#32-安全装置誤発注防止) |
| `min_quantity` | `sHikaku` | `Quantity(sHikaku, precision=0)` |
| `maker_fee` | — | `Decimal("0")` |
| `taker_fee` | — | `Decimal("0")` |
| `ts_event` | — | 起動時刻（nanoseconds）|
| `ts_init` | — | 起動時刻 |

**テスト**: `tests/python/test_data_mapping_instrument.py` — `sHikaku` → `lot_size`・`sIssueCode` → `InstrumentId` の写像、および `price_precision` / `size_precision` の計算ロジックを検証。

## 3. Instrument: 価格帯と呼値テーブル

立花の呼値テーブルは東証規程（2023-04-04 適用）に準拠:

| 価格帯 | 呼値（円） |
|---|---|
| ≤ 1,000 | 0.1 |
| > 1,000 ≤ 3,000 | 0.5 |
| > 3,000 ≤ 10,000 | 1 |
| > 10,000 ≤ 30,000 | 5 |
| > 30,000 ≤ 100,000 | 10 |
| > 100,000 ≤ 300,000 | 50 |
| > 300,000 ≤ 1,000,000 | 100 |
| > 1,000,000 | 1,000 |

**Q8 決定（2026-04-26）: 案 A 採用**

- `price_increment = Price(0.1, precision=1)` で固定（N0〜N2 全期間）
- 実際の呼値丸めは `_compose_request_payload` の Python 写像層で行う
- nautilus 内部の `price_increment` 超過 reject は `RiskEngine` の `max_order_price` / `min_order_price` で吸収
- 案 B（Instrument 複数管理）は nautilus 非標準で実用困難のため不採用
- 案 C（動的反映前倒し）は N0 スコープ外・工数過大のため N3 以降に先送り

---

## 4. OrderType / TimeInForce ↔ 立花

**写像の正本**: [order/spec.md §5.1](../order/spec.md#51-nautilus-互換のリクエストシェイプ) および `tachibana_orders._compose_request_payload`。本表はその**参照コピー**（実装変更時は order/ 側が正本）。

### 4.1 OrderType → 立花 `sOrderPrice` / `sCondition`

| nautilus `OrderType` | 立花 `sOrderPrice` | 立花 `sCondition` | 備考 |
|---|---|---|---|
| `MARKET` | `""` (空文字) | `"0"` | 成行 |
| `LIMIT` | 指値価格文字列 | `"0"` | 指値 |
| `STOP_MARKET` | `""` | `"0"` + `sGyakusasiOrderType="2"` | 逆指値（成行） |
| `STOP_LIMIT` | 指値価格文字列 | `"0"` + `sGyakusasiOrderType="1"` | 逆指値（指値） |
| `MARKET_IF_TOUCHED` | `""` | `"0"` + `sGyakusasiOrderType="2"` | `STOP_MARKET` と同写像（立花未区別） |
| `LIMIT_IF_TOUCHED` | 指値価格文字列 | `"0"` + `sGyakusasiOrderType="1"` | `STOP_LIMIT` と同写像 |

### 4.2 TimeInForce → 立花 `sCondition` / `sOrderExpireDay`

| nautilus `TimeInForce` | 立花 `sCondition` | 立花 `sOrderExpireDay` | 備考 |
|---|---|---|---|
| `DAY` | `"0"` | `""` | 当日有効 |
| `GTC` | `"0"` | `"99991231"` | 立花最長期間固定（要確認） |
| `GTD` | `"0"` | YYYYMMDD（`expire_time_ms` から変換、JST） | 期日指定。10 営業日上限チェックは Python 層 |
| `AT_THE_OPEN` | `"2"` | `""` | 寄付 |
| `AT_THE_CLOSE` | `"4"` | `""` | 引け |
| `IOC` | N/A | N/A | **立花未対応。`VENUE_UNSUPPORTED` で reject**（[order/spec.md §5.2](../order/spec.md#52-reason_code-体系観測性)） |
| `FOK` | `"6"` | `""` | 不成（成行不成立時取消）に写像。意味が完全一致しないため `tags=["fok_as_funari"]` の明示が必要 |

**FOK 注意**: nautilus の `FOK`（一括約定 or 全取消）と立花の「不成（`sCondition=6`）」は意味が異なる（不成は「成行で発注し全部約定しなければ取消」に近いが仕様詳細は PDF 確認要）。立花 API 仕様書（api_web_access.xlsx）での FOK 対応確認後に正式写像を確定する（[order/spec.md §6.1](../order/spec.md#61-用語型の整合必須) 参照）。

**テスト**: implementation-plan.md N2.6 の往復テスト（OrderType 全 6 種 + TimeInForce 全 7 種）で兼用（[implementation-plan.md N2.6](./implementation-plan.md#n26)）。

### 4.3 `cash_margin` / `account_type` タグ → 立花 `sGenkinShinyouKubun` / `sZyoutoekiKazeiC`

[order/spec.md §5.1](../order/spec.md#51-nautilus-互換のリクエストシェイプ) の表と完全同一。重複掲載しない。

---

## 5. EC frame → nautilus OrderEvent

立花 EVENT WebSocket の `p_evt_cmd=EC` フレームを nautilus の注文イベントに変換する。
`tachibana_event_bridge.py` が担う（[implementation-plan.md N2.2](./implementation-plan.md)）。

### 5.1 主要フィールド写像

| 立花 EC フィールド | nautilus イベント | フィールド | 備考 |
|---|---|---|---|
| `p_eda_no` | `OrderFilled` | `trade_id` | 重複検知キー（seen-set）。`p_eda_no` は枝番であり 1 約定 = 1 EC |
| `sOrderNumber` | `OrderFilled` | `venue_order_id` | 立花注文番号（IPC `venue_order_id` と一致） |
| — | `OrderFilled` | `client_order_id` | `OrderSessionState` の双方向写像から逆引き |
| `sYakuDai` | `OrderFilled` | `last_price` | 約定価格（文字列） |
| `sYakuSuu` | `OrderFilled` | `last_qty` | 当該 EC 分の約定株数（文字列） |
| `sYakuDaiGokei` | — | — | 累積約定代金（参考値、`cumulative_qty` は `sZanSuu` から計算） |
| `sZanSuu` | `OrderFilled` | `leaves_qty` | 残株数。`0` なら全約定 |
| `sZanSuu > 0` でも最終 EC | `OrderFilled` | `leaves_qty` | 正値なら `PARTIALLY_FILLED` 相当（nautilus は `OrderFilled` で `leaves_qty` で判定） |
| `sKKKubun` | `OrderStatus` | `status` | 取引区分。`"1"=現物`、`"2"=信用`（参考情報）|

**`cumulative_qty` の計算**: 立花は累積約定株数を 1 フィールドで返さない。`cumulative_qty = lot_size_at_fill - leaves_qty` で計算する（`sOrderSuryou` - `sZanSuu`）。

**テスト**: `tests/python/test_ec_frame_bridge.py` — `OrderFilled`/`OrderCanceled`/`OrderRejected` 変換・`cumulative_qty` / `leaves_qty` 整合・partial fill 累積を検証。

### 5.2 注文ステータス写像

立花の注文ステータス（`sOrderState` / `sKKKubun` 等の組み合わせ）から nautilus `OrderStatus` への写像は `tachibana_orders._map_tachibana_state_to_nautilus()` に集約（[order/spec.md §6.2](../order/spec.md#62-イベントタクソノミー必須)）。EC bridge は純粋に EC frame のパースのみを担う。

---

## 6. Cache warm-up vs persistence

nautilus の persistence（ディスク Parquet / SQLite）は **N2 では無効化**（`CacheConfig.database = None`）。in-memory `Cache` は毎回 warm-up する。

### 6.1 warm-up フロー（N2 起動時）

```
NautilusRunner.start_live()
  │
  ├─ CacheConfig.database = None（永続化 OFF）
  │
  ├─ 立花 CLMOrderList 取得（tachibana_orders.fetch_order_list()）
  │     ↓
  │  未決注文（ACCEPTED / PARTIALLY_FILLED）ごとに:
  │     cache.add_order(Order.from_order_record_wire(rec))
  │     cache.update_order(order_id, status=ACCEPTED or PARTIALLY_FILLED)
  │
  └─ LiveExecutionEngine.start()
```

**注意点**:
- `CLMOrderList` は当日分のみ返す（立花仕様）。前日以前の未決は存在しない前提
- warm-up 完了前に `submit_order` を受けた場合は queue に積んで warm-up 後に発火（競合防止）
- warm-up は `SetVenueCredentials` → `VenueReady` 受信後に非同期で 1 回だけ実行（[tachibana/implementation-plan.md T4 の `_ensure_master_loaded` パターン](../tachibana/implementation-plan.md#フェーズ-t4-マスタ銘柄一覧履歴-kline2〜3-日) を流用）

### 6.2 `CacheConfig.database = None` のテスト

N2.3 の受け入れ条件: 以下の assert が pass すること。

```python
from nautilus_trader.cache.cache import CacheConfig
config = runner.get_engine_config()
assert config.cache.database is None
```

---

## 7. AccountState / Position

nautilus `AccountState` および `Position` を立花 API から組み立てる方法。

| nautilus フィールド | 立花 API ソース | 備考 |
|---|---|---|
| `account_id` | `sUserId + ".TACHIBANA"` | 固定プレフィックス |
| `account_type` | `"CASH"` / `"MARGIN"` | 現物 = CASH、信用あり = MARGIN |
| `balances[JPY].total` | `CLMZanKaiKanougaku.sKaiKanouGaku` | 現物余力 |
| `unrealized_pnl` | — | N2 では実装しない（Phase O3 以降） |
| `Position.instrument_id` | `sIssueCode + ".TSE"` | 現物建玉 |
| `Position.side` | `"LONG"` | 現物は常に LONG |
| `Position.quantity` | `sZanKabuSuu` | 残株数（文字列） |
| `Position.avg_open_price` | `sTakaneNedan` or `sYakuDanHeiken` | 平均取得単価（文字列） |

**Phase O3 前は** `/api/order/positions` が stub を返してよい。nautilus `Portfolio` の unrealized PnL 計算は live price feed（立花 FD stream）からの `Bar` 更新で自動計算される。
