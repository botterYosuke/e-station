# nautilus_trader 統合: データマッピング

nautilus の型 ↔ 立花 API / J-Quants / EventStore の対応表。
写像の実装は以下のファイルに集約する:

| 種別 | 実装ファイル |
|---|---|
| **TradeTick (live)** | `python/engine/nautilus/clients/tachibana_data.py`（N2 新設） |
| **TradeTick (replay)** | `python/engine/nautilus/jquants_loader.py`（N1 新設） |
| Bar (replay daily/minute) | `python/engine/nautilus/jquants_loader.py` |
| Bar (replay daily, EventStore 経由) | `python/engine/nautilus/data_loader.py`（N0 既存） |
| Instrument | `python/engine/nautilus/instrument_factory.py` |
| OrderType / TimeInForce ↔ 立花 | `python/engine/exchanges/tachibana_orders._compose_request_payload` |
| EC frame → nautilus OrderEvent | `python/engine/nautilus/clients/tachibana_event_bridge.py` |
| AccountState / Position | `python/engine/nautilus/clients/tachibana.py` |
| Cache warm-up | `python/engine/nautilus/clients/tachibana.py::_warm_up_cache` |

---

## 1. TradeTick（歩み値）⭐ Strategy 互換性の中核

ユーザー Strategy が触る一次データ。live / replay 共通インタフェース。

### 1.1 InstrumentId 写像

| ソース | コード形式 | InstrumentId |
|---|---|---|
| 立花 `sIssueCode` | 4 桁（`"7203"`） | `"7203.TSE"` |
| **J-Quants `Code`** | **5 桁（`"13010"`）** | **末尾 0 を切って `"1301.TSE"`**（confirmed 2026-04-28） |

J-Quants の 5 桁コードは「東証 4 桁コード + チェックデジット 0」の規則。`Code[:-1] + ".TSE"` で写像する。例外検出のため `Code[-1] != "0"` の場合は `ValueError` を raise（J-Quants 仕様確認用ログ）。

### 1.2 live: 立花 FD frame → TradeTick

`python/engine/exchanges/tachibana_ws._FdFrameProcessor` の出力 trade dict を nautilus `TradeTick` に変換する。

| FD frame trade dict | nautilus `TradeTick` | 備考 |
|---|---|---|
| `instrument_id`（`"7203.TSE"`） | `instrument_id` | 既存 venue 側で組み立て済み |
| `price`（`Decimal`） | `price: Price` | `Price(value, precision=Instrument.price_precision)` |
| `qty`（`Decimal`） | `size: Quantity` | `Quantity(value, precision=0)`（株数は整数） |
| `side`（`"buy"` / `"sell"` / 推定不能 → `None`） | `aggressor_side: AggressorSide` | `BUYER` / `SELLER` / `NO_AGGRESSOR`。**前提**: [tachibana_ws.py:190](../../../python/engine/exchanges/tachibana_ws.py#L190) の `_determine_side` を曖昧時 `None` 返却に修正済みであること（[Q11-pre](./open-questions.md#q11-pre-立花-live-モードの曖昧-side-が-buy-寄せになっている-bug-to-fix-first-2026-04-28-新設)） |
| `ts_ms` | `ts_event: int (ns)` | ms → ns（×1_000_000） |
| 採番（同一秒内連番） | `trade_id: TradeId` | live は連番文字列。`f"L-{ts_ms}-{seq}"` |

**Strategy 開発者向け注意**:
- live の `aggressor_side` は quote rule + tick rule 推定。曖昧時は `NO_AGGRESSOR`
- replay は J-Quants 仕様で常に `NO_AGGRESSOR`
- nautilus `SimulatedExchange` の fill 判定で `aggressor_side` が効く場面があるため、**意思決定の入力には使わない**（[spec.md §3.5.3](./spec.md#353-既知のlivereplay差分) / [Q11](./open-questions.md#q11)）

### 1.3 replay: J-Quants `equities_trades_*.csv.gz` → TradeTick

| CSV カラム | nautilus `TradeTick` | 備考 |
|---|---|---|
| `Code` | `instrument_id` | §1.1 の写像（5 桁 → 4 桁 + `.TSE`） |
| `Price` | `price: Price` | `Price(int_str, precision=...)`、整数 |
| `TradingVolume` | `size: Quantity` | `Quantity(int_str, precision=0)` |
| `Date` + `Time` | `ts_event: int (ns)` | `"2024-01-04"` + `"09:00:00.165806"` を JST → UTC ns。マイクロ秒精度 |
| — | `aggressor_side` | `NO_AGGRESSOR` 固定（J-Quants は情報なし） |
| `TransactionId` | `trade_id` | `f"R-{TransactionId}"` |
| `SessionDistinction` | （tags or 無視） | `"01"` = 前場、`"02"` = 後場。N1 では無視。立会外を除外する用途で N3+ |

**ts_event 変換**:
```python
# JST naive → UTC ns
import datetime as dt
JST = dt.timezone(dt.timedelta(hours=9))
ts = dt.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=JST)
ts_ns = int(ts.timestamp() * 1_000_000_000)
```

**テスト**: `python/tests/test_jquants_trade_loader.py`（N1 で追加）
- `Code "13010"` → `"1301.TSE"`
- `Code "12345"` → `ValueError`（末尾非 0）
- マイクロ秒精度 timestamp 復元
- `aggressor_side == NO_AGGRESSOR`

### 1.4 BarAggregator 経由の Bar 生成（live / replay 共通）

Strategy が `on_bar` を実装する場合、nautilus 標準の `TimeBarAggregator` / `TickBarAggregator` を使って `TradeTick` から Bar を内部生成する。これにより live / replay で同じ Bar 系列が得られる（live は Bar 履歴 API、replay は J-Quants Bar を使わなくても tick から生成可）。

---

## 2. Bar（OHLCV）

### 2.1 replay: J-Quants `equities_bars_minute_*.csv.gz` → Bar

| CSV カラム | nautilus `Bar` | 備考 |
|---|---|---|
| `Code` | `bar_type.instrument_id` | §1.1 の写像 |
| `Date` + `Time`（`"09:00"`） | `ts_event` | **bar close 時刻に揃える**（Q9 確定）。`"09:00"` → JST `09:00:59.999999999` → UTC ns。nautilus 既定 `time_bars_timestamp_on_close=True` と整合 |
| `O` | `open: Price` | |
| `H` | `high: Price` | |
| `L` | `low: Price` | |
| `C` | `close: Price` | |
| `Vo` | `volume: Quantity` | |
| `Va` | （tags） | 売買代金。nautilus Bar に対応フィールドなし |
| — | `bar_type` | `BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")` |

### 2.2 replay: J-Quants `equities_bars_daily_*.csv.gz` → Bar

N0 の `data_loader.klines_to_bars()` を流用するが、ソースは EventStore（立花）から **J-Quants ファイル**に切り替える（N1.3）。スキーマは N0 と同じ。

| ソース | timestamp | 備考 |
|---|---|---|
| `Date` | JST 15:30（終値時刻） → UTC ns | N0 と同じ慣習 |
| `O/H/L/C` | `Price`（precision=1） | |
| `Vo` | `Quantity`（precision=0） | |
| — | `bar_type` | `f"{instrument_id}-1-DAY-LAST-EXTERNAL"` |

### 2.3 立花の Bar 履歴（N2 で live モード補助）

立花 `CLMMfdsGetMarketPriceHistory` レスポンス → nautilus `Bar`（N0 互換）。詳細は旧 §1 と同じ（省略）。

---

## 3. Instrument（銘柄）

nautilus の `Equity` を使う。

| nautilus `Equity` フィールド | 立花マスタソース | 値 / 備考 |
|---|---|---|
| `instrument_id` | `sIssueCode` + `"TSE"` | `"7203.TSE"` |
| `raw_symbol` | `sIssueCode` | `"7203"` |
| `currency` | 固定 | `Currency.from_str("JPY")` |
| `price_precision` | 呼値テーブル | N0〜N2: `1`（0.1 円固定。Q8 案 A） |
| `price_increment` | 呼値テーブル | N0〜N2: `Price(0.1, precision=1)` |
| `size_precision` | 固定 | `0`（株数は整数） |
| `size_increment` | `sHikaku` | `Quantity(sHikaku, precision=0)` |
| `lot_size` | `sHikaku` | `Quantity(sHikaku, precision=0)` |
| `max_quantity` | 起動 config の上限 | |
| `min_quantity` | `sHikaku` | |
| `maker_fee` / `taker_fee` | — | `Decimal("0")` |
| `ts_event` / `ts_init` | — | 起動時刻 |

**replay モードの Instrument（Q10 確定 2026-04-28、案 B + 案 A fallback）**:

優先順位:
1. `~/.cache/flowsurface/instrument_master.json`（live モードで取得した立花 `sHikaku` を永続化したもの）にエントリがあれば使う
2. ミス時は `lot_size = 100` を fallback とし `log.warning("instrument master cache miss for {instrument_id}, using fallback lot_size=100")`
3. ユーザーは起動 config で `lot_size_override: {"1301.TSE": 1, ...}` で個別上書き可能（ETF / REIT 等）

実装: `python/engine/nautilus/instrument_cache.py`（N1.2 で新設）が live → cache 永続化、replay → cache 読込を担う。

**呼値テーブル（Q8 案 A 確定）**:

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

`price_increment = Price(0.1, precision=1)` で固定し、実際の呼値丸めは `_compose_request_payload` の Python 写像層で行う。

---

## 4. OrderType / TimeInForce ↔ 立花

**写像の正本**: [order/spec.md §5.1](../✅order/spec.md#51-nautilus-互換のリクエストシェイプ) および `tachibana_orders._compose_request_payload`。本表はその参照コピー。

### 4.1 OrderType → 立花 `sOrderPrice` / `sCondition`

| nautilus `OrderType` | 立花 `sOrderPrice` | 立花 `sCondition` | 備考 |
|---|---|---|---|
| `MARKET` | `""` | `"0"` | 成行 |
| `LIMIT` | 指値価格文字列 | `"0"` | 指値 |
| `STOP_MARKET` | `""` | `"0"` + `sGyakusasiOrderType="2"` | 逆指値（成行） |
| `STOP_LIMIT` | 指値価格文字列 | `"0"` + `sGyakusasiOrderType="1"` | 逆指値（指値） |
| `MARKET_IF_TOUCHED` | `""` | 同 STOP_MARKET | 立花未区別 |
| `LIMIT_IF_TOUCHED` | 指値価格文字列 | 同 STOP_LIMIT | 立花未区別 |

### 4.2 TimeInForce → 立花 `sCondition` / `sOrderExpireDay`

| nautilus `TimeInForce` | 立花 `sCondition` | 立花 `sOrderExpireDay` | 備考 |
|---|---|---|---|
| `DAY` | `"0"` | `""` | 当日有効 |
| `GTC` | `"0"` | `"99991231"` | 立花最長期間固定 |
| `GTD` | `"0"` | YYYYMMDD（JST） | 10 営業日上限チェックは Python 層 |
| `AT_THE_OPEN` | `"2"` | `""` | 寄付 |
| `AT_THE_CLOSE` | `"4"` | `""` | 引け |
| `IOC` | N/A | N/A | **立花未対応**。`VENUE_UNSUPPORTED` で reject |
| `FOK` | `"6"` | `""` | 不成（意味が完全一致せず）。`tags=["fok_as_funari"]` 明示 |

### 4.3 `cash_margin` / `account_type` タグ

[order/spec.md §5.1](../✅order/spec.md#51-nautilus-互換のリクエストシェイプ) と完全同一。重複掲載しない。

---

## 5. EC frame → nautilus OrderEvent

立花 EVENT WebSocket の `p_evt_cmd=EC` フレームを nautilus 注文イベントに変換する。`tachibana_event_bridge.py` が担う。

### 5.1 主要フィールド写像

| 立花 EC フィールド | nautilus イベント | フィールド | 備考 |
|---|---|---|---|
| `p_eda_no` | `OrderFilled` | `trade_id` | 重複検知キー |
| `sOrderNumber` | `OrderFilled` | `venue_order_id` | 立花注文番号 |
| — | `OrderFilled` | `client_order_id` | `OrderSessionState` から逆引き |
| `sYakuDai` | `OrderFilled` | `last_price` | 約定価格（文字列） |
| `sYakuSuu` | `OrderFilled` | `last_qty` | 当該 EC 分の約定株数 |
| `sZanSuu` | `OrderFilled` | `leaves_qty` | 残株数。`0` なら全約定 |

`cumulative_qty = sOrderSuryou - sZanSuu` で計算する。

### 5.2 注文ステータス写像

`tachibana_orders._map_tachibana_state_to_nautilus()` に集約。

---

## 6. Cache warm-up vs persistence

nautilus の persistence（ディスク Parquet / SQLite）は **N2 では無効化**（`CacheConfig.database = None`）。in-memory `Cache` は毎回 warm-up する。

### 6.1 warm-up フロー（N2 起動時）

```
NautilusRunner.start_live()
  ├─ CacheConfig.database = None
  ├─ 立花 CLMOrderList 取得
  │     ↓ 未決注文（ACCEPTED / PARTIALLY_FILLED）ごとに:
  │     cache.add_order(...) + cache.update_order(status=...)
  └─ LiveExecutionEngine.start()
```

- `CLMOrderList` は当日分のみ
- warm-up 完了前に `submit_order` を受けた場合は queue に積む
- warm-up は `SetVenueCredentials` → `VenueReady` 受信後に非同期で 1 回だけ実行

### 6.2 受け入れ条件

```python
config = runner.get_engine_config()
assert config.cache.database is None
```

---

## 7. AccountState / Position

| nautilus フィールド | 立花 API ソース | 備考 |
|---|---|---|
| `account_id` | `sUserId + ".TACHIBANA"` | |
| `account_type` | `"CASH"` / `"MARGIN"` | |
| `balances[JPY].total` | `CLMZanKaiKanougaku.sKaiKanouGaku` | |
| `unrealized_pnl` | — | N2 では実装しない |
| `Position.instrument_id` | `sIssueCode + ".TSE"` | |
| `Position.side` | `"LONG"` | 現物は常に LONG |
| `Position.quantity` | `sZanKabuSuu` | |
| `Position.avg_open_price` | `sTakaneNedan` or `sYakuDanHeiken` | |

**replay モードの AccountState**: nautilus `SimulatedExchange` が組み立てる。立花 API は呼ばない。`initial_cash` は `StartEngine.config` で指定。

---

## 8. J-Quants データソース ⭐ 2026-04-28 新設

### 8.1 配置と命名

```
S:\j-quants\
  equities_bars_daily_YYYYMM.csv.gz       (月次)
  equities_bars_minute_YYYYMMDD.csv.gz    (日次)
  equities_trades_YYYYMM.csv.gz           (月次)
```

### 8.2 期間選択ロジック

`POST /api/replay/load { start_date, end_date }` を受けたら:

| granularity | 開く必要のあるファイル |
|---|---|
| `Trade` | `equities_trades_{YYYYMM}.csv.gz` を期間に重なる月だけ順次 stream-open |
| `Minute` | `equities_bars_minute_{YYYYMMDD}.csv.gz` を期間内の各取引日について順次 |
| `Daily` | `equities_bars_daily_{YYYYMM}.csv.gz` を期間に重なる月だけ |

### 8.3 ローダ実装方針

- gzip ストリーム読込（メモリに全量展開しない）
- 銘柄フィルタを **ヘッダ行 + 行単位**で適用（pandas ロード前に csv module で間引く）
- 期間フィルタも行単位
- `BacktestEngine.add_data(ticks)` には instrument_id 単位でまとめて投入

### 8.4 リファレンス CSV スキーマ（confirmed 2026-04-28）

```
trades:       Date,Code,Time,SessionDistinction,Price,TradingVolume,TransactionId
              例: 2024-01-04,13010,09:00:00.165806,01,3775,1100,000000000010

minute_bars:  Date,Time,Code,O,H,L,C,Vo,Va
              例: 2026-02-18,09:00,13010,5180,5180,5180,5180,2100,10878000

daily_bars:   既存 N0 互換（実物確認時にカラム名を本表に固定する）
```

### 8.5 InstrumentId 例外検出

```python
def jquants_code_to_instrument_id(code: str) -> str:
    if len(code) != 5:
        raise ValueError(f"unexpected J-Quants code length: {code!r}")
    if code[-1] != "0":
        raise ValueError(f"J-Quants code does not end with 0: {code!r}")
    return f"{code[:-1]}.TSE"
```

不正コードは raise してログに残す（J-Quants の規約変更を即座に検知）。
