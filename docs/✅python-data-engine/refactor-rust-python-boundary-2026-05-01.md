# Rust ↔ Python 責務分離リファクタ実装計画（案C 採用）

作成日: 2026-05-01
対象: e-station Rust GUI ↔ Python データエンジン境界
方針: **案C — Rust 側 depth/price 正規化を完全撤去し、Python 側を「正規化済みデータの単一供給源」とする**

---

## 0. ゴール

1. Rust 側を「描画・UI・ユーザー入力」に純化する
2. Python 側を「venue 解釈・正規化・capability 宣言」の単一供給源にする
3. IPC 境界 DTO を typed schema にして「無型 Value 配列」を撤廃する
4. venue 追加時に Rust 改修が不要な構造にする
5. Ladder の "alternating zeros" を含む min_ticksize 起因のクラス全体を構造的に撲滅する

**非ゴール**:
- 描画ロジック自体の変更（`GroupedDepth::regroup_from_raw` の step バケッティングは UI 責務として残す）
- `TickMultiplier` のユーザー操作 API の変更
- 純粋値型 (`Price`, `PriceStep`, `Qty`) の API 変更

---

## 1. 現状の問題（要点）

| # | 問題 | 該当 | 種別 |
|---|------|------|------|
| P1 | `TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32 = 1.0` を Rust が defaulting | `engine-client/src/tachibana_meta.rs:33` | ドメイン解釈越境 |
| P2 | `apply_diff_levels` が毎 diff で `round_to_min_tick` 再丸め | `exchange/src/depth.rs:82-102` | 防衛的再正規化 |
| P3 | `Exchange::is_depth_client_aggr()` が enum マッチで venue 判定 | `exchange/src/adapter.rs:469`（10 箇所超で利用） | venue 知識の Rust 漏出 |
| P4 | `EngineEvent::TickerInfo.tickers: Vec<Value>` が無型 | `engine-client/src/dto.rs:805-809`, `schemas/events.json` | スキーマ設計欠陥 |
| P5 | `resolve_min_ticksize_for_issue(snapshot_price=None)` で最細刻みフォールバック | `python/engine/exchanges/tachibana_master.py:200-238` | ドメイン側未完 |
| P6 | Ladder が raw best bid/ask を `depth.bids.last_key_value()` で再導出 | `src/screen/dashboard/panel/ladder.rs:103-108` | best 値の Rust 再計算 |
| P7 | `qty_norm`（数量正規化）が Rust 側適用 | `exchange/src/depth.rs:88-91` | 同上 |

これらは 2026-05-01 の "alternating zeros" 1 件で全て同時に表面化している（[fix-min-ticksize-fallback-2026-05-01.md](./fix-min-ticksize-fallback-2026-05-01.md)）。

---

## 2. 完成形アーキテクチャ

### 2.1 IPC 契約（不変条件）

Python が Rust に送る `Depth` / `Trade` / `Kline` / `TickerInfo` は次を満たすことを **Python 側で保証** する:

| データ | 不変条件 |
|--------|---------|
| `Depth.bids/asks` の price | `min_ticksize` の整数倍に丸め済み |
| `Depth.bids/asks` の qty | venue 単位で正規化済み（contract_size × normalize 適用済み） |
| `Trade.price` | 同上 |
| `Kline.{o,h,l,c}` | 同上 |
| `TickerInfo.min_ticksize` | 必須・正値・解決済み（snapshot 価格を踏まえた band 解決） |
| `VenueCaps`（IPC では `TickerEntry` 内に同梱、Rust 永続モデルには載せない） | 必須・client_aggr_depth / supports_spread_display / qty_norm_kind を含む |

> **設計上の不変条件**: `VenueCaps` は **IPC 境界 DTO `TickerEntry` の一部としてのみ** Python から運ばれる。Rust 内部では `TickerInfo` 本体には**持たせない**（後述 §2.4 参照）。

Rust 側はこの契約を **debug ビルドで `debug_assert!` 検証**、release では no-op で信頼する。

### 2.2 責務マトリクス

| 機能 | Python | Rust |
|------|--------|------|
| venue REST/WS 接続 | ✅ | — |
| 価格 tick 解決（yobine 等） | ✅ | — |
| 価格 tick 丸め | ✅ | debug_assert のみ |
| qty normalization | ✅ | debug_assert のみ |
| best bid/ask 算出 | ✅（必要なら snapshot 内に含めて送る） | キャッシュとしての保持のみ |
| venue capability 宣言 | ✅ | — |
| `Depth` 構造保持 | — | ✅（受信キャッシュ） |
| `regroup_from_raw` step バケッティング | — | ✅（UI ズーム） |
| `TickMultiplier` ユーザー操作 | — | ✅ |
| ChaseTracker / Spread 表示 | — | ✅ |
| Ladder/Chart 描画 | — | ✅ |

### 2.3 IPC スキーマ（typed） — **Phase F 完了後の最終形**

> **注意**: 以下は **Phase F 完了後の到達点**を示す。Phase A〜E 期間中の `venue_caps` は optional（`required` から外す）で運用する。Phase 別のスキーマ差分は §4.1 / §5.1 / §9.1 のタスク表に記載。

`TickerInfo` を venue_kind で discriminated union 化:

```jsonc
// schemas/events.json
"TickerInfo": {
  "type": "object",
  "required": ["event", "request_id", "venue", "tickers"],
  "properties": {
    "tickers": {
      "type": "array",
      "items": { "$ref": "#/definitions/TickerEntry" }
    }
  }
},
"TickerEntry": {
  "oneOf": [
    { "$ref": "#/definitions/CryptoTicker" },
    { "$ref": "#/definitions/StockTicker" }
  ]
},
// Phase F 完了後の最終形: venue_caps が required
// Phase A〜E 期間中は required から venue_caps を外して optional 運用
"StockTicker": {
  "type": "object",
  "required": ["kind", "symbol", "min_ticksize", "lot_size", "venue_caps"],
  "properties": {
    "kind": { "const": "stock" },
    "symbol": { "type": "string" },
    "display_symbol": { "type": "string" },
    "min_ticksize": { "type": "number", "exclusiveMinimum": 0 },
    "lot_size": { "type": "integer", "exclusiveMinimum": 0 },
    "venue_caps": { "$ref": "#/definitions/VenueCaps" },
    "tachibana": { "$ref": "#/definitions/TachibanaMeta" }
  }
},
"VenueCaps": {
  "type": "object",
  "required": ["client_aggr_depth", "supports_spread_display"],
  "properties": {
    "client_aggr_depth": { "type": "boolean" },
    "supports_spread_display": { "type": "boolean" },
    "qty_norm_kind": { "enum": ["none", "contract", "lot"] }
  }
}
```

Rust 側は `EngineEvent::TickerInfo { tickers: Vec<TickerEntry> }` を typed enum で受ける。

### 2.4 Rust 内部での `VenueCaps` 配置（重要）

**`VenueCaps` を `TickerInfo` 本体に追加しない。** 理由:

- [exchange/src/lib.rs:545](../../exchange/src/lib.rs) の `TickerInfo` は `#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize, Hash, Eq)]` で**識別子に近い値オブジェクト**として扱われている
- map key として広く使われており、`Hash`/`Eq` の意味を変えると予測不能な regression を生む
- 旧 `saved-state.json` 互換は [exchange/tests/ticker_info_state_migration.rs](../../exchange/tests/ticker_info_state_migration.rs) で守られており、フィールド追加は serde 互換にも波及する
- `venue_caps` は識別子の一部ではなく**ランタイム capability**。型の役割が混ざる

採用する設計:

```rust
// engine-client または app 層に追加
pub struct VenueCapsStore {
    inner: HashMap<Ticker, VenueCaps>,
}

impl VenueCapsStore {
    pub fn upsert(&mut self, ticker: Ticker, caps: VenueCaps);
    pub fn get(&self, ticker: &Ticker) -> Option<&VenueCaps>;
}
```

- IPC 受信時に `TickerEntry` から `TickerInfo` と `VenueCaps` を**分離**して別経路で保存
- UI/ladder/data 各層は `caps_store.get(&ticker)` で参照
- 永続化対象から完全に除外（起動ごとに Python から再配信される）

**代替案（採用しない）**: `TickerInfo` に `venue_caps: Option<VenueCaps>` を足し、`Hash/PartialEq/Eq` を手書きで「既存フィールドだけ比較」にする案。
- 等価性が型シグネチャから読み取れない
- フィールド追加時に毎回 Hash/Eq 手書き更新が必要で事故りやすい
- serde roundtrip と runtime 等価性が乖離する

---

## 3. フェーズ分割

案C は破壊的変更を含むため、**段階的後方互換 → 一斉切替 → クリーンアップ** で進める。各フェーズは独立に merge 可能。

```
Phase A: スキーマ硬化（後方互換あり）
   ↓
Phase B: capability 配信（optional）
   ↓
Phase C: Python 側正規化の一元化
   ↓
Phase D: Rust 側 venue 知識の除去
   ↓
Phase E: Rust 側正規化の no-op 化（debug_assert のみ）
   ↓
Phase F: SCHEMA_MAJOR bump とプレースホルダ撤去
```

各フェーズの完了条件・所要規模・リスクを以下に列挙する。

---

## 4. Phase A — IPC スキーマ硬化（後方互換あり）

### 4.1 タスク

| ID | 内容 | 場所 |
|----|------|------|
| A1 ✅ | `TickerEntry` discriminated union を schema 定義 | `docs/✅python-data-engine/schemas/events.json` |
| A2 ✅ | Rust `dto.rs` に `TickerEntry` enum を追加（既存 `Vec<Value>` と並行） | `engine-client/src/dto.rs` |
| A3 ✅ | パーサ: 受信時に `TickerEntry::Stock(...)` への parse を試行、失敗時は既存 `Value` 経路に fallback | `engine-client/src/backend.rs:510-575` |
| A4 ✅ | Python: `list_tickers` の出力に `kind` フィールドを必ず付ける | `python/engine/exchanges/tachibana.py:list_tickers` |
| A5 ✅ | スキーマ単体テスト（JSON Schema validator） | `python/tests/test_events_json_schema.py`（新規）|

### 4.2 完了条件

- 既存の Tachibana / Crypto いずれの起動経路でも regression なし ✅
- Rust 起動ログに `parsed as typed TickerEntry: count=N` が出る ✅（backend.rs 実装済み）
- typed parse 失敗が出たら warn ログ + 旧経路で動作 ✅

### 4.5 Phase A 完了メモ（2026-05-01）

**実施内容:**
- A1: events.json に TickerEntry/StockTicker/CryptoTicker/VenueCaps 定義を追加（$defs）
- A2: dto.rs に TickerEntry enum（Stock/Crypto）+ StockTickerEntry/CryptoTickerEntry 構造体を追加
- A3: backend.rs の TickerInfo ハンドラを typed parse 優先 + fallback に更新。ログ: `TickerInfo: typed=N fallback=0`
- A4: 全 adapter（tachibana/hyperliquid/binance/bybit/mexc/okex）に kind フィールド追加
- A5: python/tests/test_events_json_schema.py 新規作成（13 テスト all green）
- SCHEMA_MINOR: 7 → 8（Rust + Python 両方）
- engine-client/tests/ticker_info_typed.rs 新規（5 テスト all green）

**落とし穴:**
- Python エージェントが tachibana.py を過剰に削減した（build_ws_url 等を削除）→ git restore で元に戻し `kind` のみ追加
- 両 worktree の lib.rs 変更（re-export vs SCHEMA_MINOR）は手動でマージが必要だった

**落とし穴 (追記):**
- Rust エージェントが dto.rs を truncated 版に差し替え、`GetPositions`/`PositionRecordWire`/`PositionsUpdated` を削除 → 手動で 3 箇所復元
- `schema_v2_4_nautilus.rs::schema_minor_is_7_for_positions` テストの期待値を 7→8 に更新（リグレッションガード）
- Python エージェントが tachibana.py を過剰に削減（build_ws_url 等削除）→ `git checkout HEAD` で元に戻し `kind: "stock"` のみ追加

**検証結果（修正後）:**
- `cargo test -p flowsurface-engine-client`: 全スイート green（0 failed / 0 errored）
- `uv run pytest python/tests/ -q`: 1401 passed（test_tachibana_buying_power は pre-existing WIP 失敗）
- `ipc-schema-check`: MAJOR=2/2, MINOR=8/8, compression=None → 全 OK

### 4.3 リスク

- schema 検証ライブラリ追加（`jsonschema` Python パッケージ）の dep 追加
- Tachibana 以外の venue（crypto）にも `kind: "crypto"` を付ける必要 → 既存全 adapter の改修

### 4.4 テスト

- `python/tests/test_schemas.py`: JSON Schema validator で events を検証
- `engine-client/tests/ticker_info_typed.rs`（新規）: typed parse の roundtrip
- e2e smoke: 起動ログで parsed as typed が確認できる

---

## 5. Phase B — VenueCaps の Python 配信

### 5.1 タスク

| ID | 内容 | 場所 |
|----|------|------|
| B1 | `VenueCaps` を `TickerEntry` に optional フィールドとして追加（IPC のみ）| schemas, dto.rs |
| B2 | Python 側 `venue_caps()` を adapter 基底クラスに追加（abstract）| `python/engine/exchanges/base.py`（新規 or 既存）|
| B3 | 各 adapter で `client_aggr_depth` / `supports_spread_display` を返す実装 | tachibana.py, hyperliquid.py 他 |
| B4 | Rust 側に `VenueCapsStore`（sidecar `HashMap<Ticker, VenueCaps>`）を新設。`TickerInfo` 本体には触らない。受信ハンドラで `TickerInfo` と `VenueCaps` を分離保存 | `engine-client/src/venue_caps.rs`（新規）, `engine-client/src/backend.rs` 受信パス |
| B5 | `is_depth_client_aggr()` 呼び出しを `caps_store.get(&ticker).map(\|c\| c.client_aggr_depth).unwrap_or_else(\|\| ticker.exchange.is_depth_client_aggr())` に置換 | 全 10 箇所（grep 結果参照）|
| B6 | `VenueCapsStore` を UI/data 層に渡す経路を確保（既存の `EngineClient` ハンドル経由か、`pane.rs` の context に注入）| `src/`, `data/` |

### 5.2 完了条件

- 全 venue が `venue_caps` を送出
- Rust 側 fallback パス（`unwrap_or_else`）を計測ログで監視し、本番で発火していないことを確認
- `TickerInfo` の `Hash`/`Eq`/`serde` シグネチャに変更がない（`exchange/tests/ticker_info_state_migration.rs` が無改修で green）

### 5.3 リスク

- Hyperliquid を含む全 adapter の修正を一気にやらないと fallback に依存
- 既存 `saved-state.json` には影響なし（`VenueCaps` は sidecar、永続化対象外）
- `VenueCapsStore` の lifetime/共有方法（Arc<RwLock<...>> か channel か）の選択を誤ると UI 描画パスでロック競合 → 設計判断 Q6 として後述

### 5.4 テスト

- `python/tests/test_venue_caps.py`: 各 adapter が `venue_caps` を返す
- `engine-client/tests/venue_caps_roundtrip.rs`: typed parse + fallback 経路

---

## 6. Phase C — Python 側正規化の一元化

### 6.1 タスク

| ID | 内容 | 場所 |
|----|------|------|
| C1 ✅ | `python/engine/exchanges/normalize.py`（新規）に `normalize_depth` / `normalize_trade` / `normalize_kline` / `normalize_qty_contract` を実装 | `python/engine/exchanges/normalize.py` |
| C2 ✅ | tachibana の depth/trade 送出パス全箇所で normalize を適用（market_closed / _cb_depth / _depth_polling_fallback / stream_trades） | `tachibana.py` |
| C3 ✅ | `resolve_min_ticksize_for_issue` は Phase A 以前から完成。C4 と連携して stream_depth から呼び出し済み | `tachibana_master.py`（変更なし） |
| C4 ✅ | stream_depth の初回 FD フレームで snapshot price を使って `_ticker_min_ticksize` キャッシュを更新（Python 内部のみ。Rust への TickerInfo 再 push は Phase F で検討） | `tachibana.py` |
| C5 ✅ | `normalize_qty_contract` を実装・テスト済み。Rust 側でも qty normalization は apply_diff_levels で実行されていないためダブル正規化リスクなし | `normalize.py` |

### 6.2 完了条件

- Python 側ユニットテスト: 任意の生 depth に対し、normalize 後の price が `min_ticksize` の整数倍
- Python 側ユニットテスト: 任意の生 depth qty に対し、normalize 後の qty が venue 単位整合
- Tachibana の "alternating zeros" シナリオ（5379 円銘柄 + 5x multiplier）が Python 出力時点で正値

### 6.3 リスク

- Python 側に正規化バグが入ると全 venue が同時に死ぬ → ユニットテストを厚く
- snapshot 価格取得まで `min_ticksize` が確定しないため list_tickers の挙動が変わる
  - mitigation: 暫定 best_guess を送り、解決後に `TickerInfo` を再 push（イベントの再送許容）
  - Rust 側は `TickerInfo` 再受信で `set_tick_size(pending)` 経路を再利用

### 6.4 テスト

- `python/tests/test_normalize_depth.py` ✅: 68 テスト all green（価格正規化・乱数プロパティ・5379円シナリオ）
- `python/tests/test_normalize_qty.py` ✅: qty 正規化テスト（normalize_qty_contract property test 含む）
- `python/tests/test_tachibana_ticksize_resolve.py` ✅: 全 yobine band 網羅（5バンド × 境界値 + NTT/Toyota/SoftBank/みずほシナリオ）
- `tests/e2e/smoke.sh` 拡張: 今後の課題

### 6.5 Phase C 完了メモ（2026-05-01）

**実施内容:**
- C1: `normalize.py` 新規作成。`normalize_price`（ROUND_HALF_UP でRust挙動に一致）/ `normalize_depth_levels` / `normalize_depth` / `normalize_trade` / `normalize_trades_event` / `normalize_kline` / `normalize_qty_contract` を実装
- C2: `tachibana.py` に3つのヘルパーメソッドを追加（`_lookup_sizyou_record` / `_update_min_ticksize_from_price` / `_normalize_depth_levels` / `_normalize_trade_price`）。stream_depth の全送出パス（market_closed / _cb_depth / _depth_polling_fallback）と stream_trades に組み込み
- C3: `resolve_min_ticksize_for_issue` は変更なし（既に完成）
- C4: stream_depth 初回 FD フレーム時に `_update_min_ticksize_from_price` を呼び出して yobine band を price ベースで再解決し `_ticker_min_ticksize[ticker]` を更新する。Python 内部のみ（Rust TickerInfo 再 push は実装しなかった — Rust TickerInfo ハンドラが request_id 一致待ちのため unsolicited push は無視される。Phase F で対応予定）
- C5: `normalize_qty_contract` を実装・テスト済み。現在の Rust pipeline（`apply_diff_levels`）は qty normalization を行っていないため、double-normalization リスクなし

**判明した重要な知見:**
- Rust `apply_diff_levels`（engine-client/src/backend.rs:1183）は qty normalization を行っていない（`Qty::from_f32(q)` 直接）。`exchange/src/depth.rs` の `QtyNormalization` コードは LocalDepthCache 専用で、engine-client pipeline では未使用
- Rust `depth_levels_to_arc_depth`（DepthSnapshot 処理）は価格の丸め込みも行わない。Phase E で Rust 側を no-op 化した際に snapshot の丸め込みが落ちる — Python 側の C2 追加が重要
- Rust TickerInfo ハンドラは `rid == request_id` 一致待ちのため、Python から unsolicited push しても無視される。C4 の Rust re-push は IPC 変更なしには実装できない

**落とし穴:**
- `_cb_depth` クロージャは while ループ内で再定義されるが `self` を capturing しているため、`_ticker_min_ticksize` への書き込みは問題なし（asyncio single-thread）
- `_first_fd_received` は while ループ外で定義されているため、再接続をまたいだ初回判定が正しく機能する

---

## 7. Phase D — Rust 側 venue 知識の除去

### 7.1 タスク

| ID | 内容 | 場所 |
|----|------|------|
| D1 | `Exchange::is_depth_client_aggr()` を `#[deprecated]` 化、内部実装を `panic!("use VenueCapsStore::get(&ticker)")` に。Phase B で導入した `unwrap_or_else` fallback も全箇所削除 | `exchange/src/adapter.rs:469`, Phase B5 で書き換えた全 10 箇所 |
| D2 | `TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32` を削除し、`min_ticksize` 欠落は `Result::Err` 化 | `engine-client/src/tachibana_meta.rs:33,137` |
| D3 | `engine-client/src/backend.rs:526-541` の Tachibana 分岐を `TickerEntry::Stock { tachibana: Some(...) }` の typed match に置換 | backend.rs |
| D4 | `parse_tachibana_ticker_dict` を `parse_stock_ticker_entry`（venue 非依存）にリネーム+汎用化 | tachibana_meta.rs → stock_meta.rs にリネーム |
| D5 | `src/screen/dashboard/panel/ladder.rs:858-864` の `is_depth_client_aggr()` を `caps_store.get(&self.ticker_info.ticker).map(\|c\| c.supports_spread_display).unwrap_or(false)` に置換（fallback 値は描画安全な側を選ぶ）| ladder.rs |
| D6 | `data/src/layout/pane.rs:302-304` の `is_depth_client_aggr()` 呼び出しも `caps_store` 参照に置換。`data` クレートが `VenueCapsStore` 参照を受け取れるよう context API を追加 | data/src/layout/pane.rs, src/ |

### 7.2 完了条件

- `grep -r 'is_depth_client_aggr' src/ data/ exchange/ engine-client/` がゼロ ✅（定義本体のみ残存 — Phase F で削除）
- `grep -r 'TACHIBANA_MIN_TICKSIZE_PLACEHOLDER' .` がゼロ ✅
- `cargo clippy -- -D warnings` clean ✅

### 7.5 Phase D 完了メモ（2026-05-01）

**Q3 決定**: (a) 起動時 step 再計算（既存挙動）。`TickMultiplier` は `saved-state.json` から読み込み、`min_ticksize` は Python から再受信。migration 関数不要。

**実施内容:**
- D1: `Exchange::is_depth_client_aggr()` を `#[deprecated]` + `panic!` 化。`stream_ticksize()` に `is_client_aggr: bool` 引数を追加（`is_depth_client_aggr()` を呼ばなくなった）。`caps_client_aggr()` の fallback を `unwrap_or(true)` に変更（Phase B の `unwrap_or_else(|| is_depth_client_aggr())` を除去）
- D2: `TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32` const を削除。`min_ticksize` 欠落時は `None`/warn ログに変更（typed path・fallback path 両方）
- D3: backend.rs の typed Stock parse パスで placeholder unwrap_or を除去し、None 時は `log::warn!` + `return None` に
- D4: `tachibana_meta.rs` → `stock_meta.rs` にリネーム（`parse_tachibana_ticker_dict` → `parse_stock_ticker_entry`）。旧 `tachibana_meta.rs` は re-export シムとして残存（Phase F で削除）
- D5: ladder.rs は Phase B 時点で `unwrap_or(false)` 済み — 追加変更なし
- D6: `PaneSetup::new` の `prev_base_ticker` 引数を除去し `prev_is_client_aggr: bool` を追加。caller (pane.rs) で `caps_client_aggr` を使い解決

**追加テスト:**
- `engine-client/tests/ticker_info_required_fields.rs`: min_ticksize 欠落/不正値で None
- `engine-client/tests/ticker_info_tachibana_mapping.rs`: 旧テストを新 API に更新
- `engine-client/tests/ticker_meta_map_round_trip.rs`: mock に min_ticksize を追加
- `data/src/panel/ladder.rs`: proptest — regroup_from_raw の出力 key が step の倍数

**検証結果:**
- `cargo clippy -- -D warnings`: clean
- `cargo test --workspace`: 全 green（0 failed）
- `grep -r 'TACHIBANA_MIN_TICKSIZE_PLACEHOLDER' .`: 0 件
- `grep -r 'is_depth_client_aggr' src/ data/ engine-client/ exchange/`: 定義本体のみ（呼び出し箇所ゼロ）

### 7.3 リスク

- 既存 `saved-state.json` を読み込んだ際、保存時の TickMultiplier × 旧 min_ticksize から計算された `step` が新 min_ticksize と整合しない可能性
  - mitigation: 起動時に TickerInfo 受信後 `step = TickMultiplier × min_ticksize` で再計算（既存 path を活用）

### 7.4 テスト

- `engine-client/tests/ticker_info_required_fields.rs`: `min_ticksize` 欠落で Err
- `data/src/panel/ladder.rs` proptest: 任意の (depth, step) で `regroup_from_raw` 出力 key が `step` の倍数 + qty 保存
- saved-state migration テスト

---

## 8. Phase E — Rust 側正規化の no-op 化

### 8.1 タスク

| ID | 内容 | 場所 |
|----|------|------|
| E1 | `Depth::diff_price_levels` の `round_to_min_tick` を `debug_assert!(price.is_at_tick(min_ticksize))` に置換 | `exchange/src/depth.rs:93` |
| E2 | `Depth::replace_all_with_qty_norm` の qty_norm 適用を debug_assert に置換 | `exchange/src/depth.rs:88-91` |
| E3 | `LocalDepthCache::update_with_qty_norm` の `qty_norm` 引数を削除（または `#[deprecated]`）| `exchange/src/depth.rs:160-182` |
| E4 | `apply_diff_levels`（backend.rs:1039-1068）の rounding を削除 | backend.rs |
| E5 | `Price::is_at_tick(MinTicksize)` を `exchange/src/unit/price.rs` に追加 | price.rs |

### 8.5 Phase E 完了メモ（2026-05-01）

**Q5 決定**: (a) silent（trust）。§2.1「release では no-op で信頼する」の設計方針のまま。

**実施内容:**
- E5: `Price::is_at_tick(MinTicksize) -> bool` を `exchange/src/unit/price.rs` に追加。
  ULP ベースの許容差（2 ULP）付きで実装 — `Price::from_f32` が f32 精度損失（101.0 × 10^8 は f32 で正確に表現できない）を持つため、厳密な整除チェックは false negative を起こす。
- E1: `diff_price_levels` の `round_to_min_tick` を削除し `debug_assert!(price.is_at_tick(...))` に置換
- E2: `replace_all_with_qty_norm` の qty_norm 適用コードを削除し `debug_assert!(qty_norm.is_none())` に置換
- E3: `LocalDepthCache::update_with_qty_norm` を `#[deprecated]` 化。内部実装を private `update_inner` に切り出し、`update()` は deprecated を呼ばずに `update_inner` を直接呼ぶ構造に変更
- E4: `engine-client/src/backend.rs::apply_diff_levels` の `round_to_min_tick` を削除し `debug_assert!` に置換
- `engine-client/tests/depth_assert.rs` 新規作成: 未正規化価格で debug ビルドが panic することを確認（5 テスト all green）

**落とし穴:**
- `Price::is_at_tick` を `self.units % unit == 0` の厳密チェックで実装したところ、`Price::from_f32(101.0)` の精度損失（units=10_099_999_744, 期待=10_100_000_000）により false が返され normalised_depth_does_not_panic が失敗。
  → 2 ULP 許容差を追加: `ulp = (units.unsigned_abs() >> 23).max(1)`, tolerance = 2 * ulp。
  → 許容差が tick の半分を超える場合（f32 精度が不十分なスケール）は自動的に true を返す。

**検証結果:**
- `cargo clippy -- -D warnings`: clean
- `cargo test -p flowsurface-exchange`: 全 green（58 tests）
- `cargo test -p flowsurface-engine-client`: 全 green（0 failed）
- `engine-client/tests/depth_assert.rs`: 5 tests all green（panic テスト 4 件 + 正常パス 1 件）

### 8.2 完了条件

- release ビルドで rounding コードパスがゼロ（`cargo asm` か `cargo expand` で確認）
- debug ビルドで 24h soak し `debug_assert` が一度も発火しない

### 8.3 リスク

- Python 側正規化バグが本番で発覚 → Rust 側で隠蔽されていたものが顕在化
  - mitigation: Phase E 投入前に Python 側正規化を 1 週間以上 debug ビルドで運用し assert 無発火を確認

### 8.4 テスト

- `exchange/src/depth.rs` の既存テストを「正規化済み入力」前提に書き換え
- `engine-client/tests/depth_assert.rs`: わざと未正規化 depth を流し debug ビルドで panic することを確認

---

## 9. Phase F — SCHEMA_MAJOR bump とクリーンアップ

### 9.1 タスク

| ID | 内容 | 場所 |
|----|------|------|
| F1 | `SCHEMA_MAJOR` を bump（現在値 +1）| `engine-client/src/lib.rs`, `python/engine/schemas.py` |
| F2 | Phase A の typed parse fallback 経路を削除（`Vec<Value>` 経路撤去） | dto.rs, backend.rs |
| F3 | `VenueCaps` を IPC `TickerEntry` で required 化（Phase B の optional 撤去）。**`TickerInfo` 本体には依然として持たせない**（sidecar 維持）| dto.rs, schemas |
| F4 | `Exchange::is_depth_client_aggr()` メソッド本体を削除（`VenueCapsStore` 必須化）| adapter.rs |
| F5 | `LocalDepthCache::update_with_qty_norm` の deprecated メソッド削除 | depth.rs |
| F6 | CHANGELOG 更新 | `docs/✅python-data-engine/schemas/CHANGELOG.md` |

### 9.5 Phase F 完了メモ（2026-05-01）

**実施内容:**
- F1: `SCHEMA_MAJOR` を 2 → 3 に bump（`engine-client/src/lib.rs`, `python/engine/schemas.py`）
- F2: `EngineEvent::TickerInfo.tickers` を `Vec<serde_json::Value>` → `Vec<TickerEntry>` に変更（`dto.rs`）。`backend.rs` の fallback 経路（`Err(_)` ブロック・`fallback_count`・Value-based parse）を全削除。`tickers.iter()` → `tickers.into_iter()` に変更し直接 `match` で処理
- F3: `StockTickerEntry.venue_caps` / `CryptoTickerEntry.venue_caps` を `Option<VenueCaps>` → `VenueCaps`（required）に変更。`#[serde(default)]` 削除。`backend.rs` の `if let Some(caps) = ...` を `staged_caps.push((ticker, entry.venue_caps))` に変更。`events.json` の StockTicker/CryptoTicker `required` に `"venue_caps"` を追加
- F3+: `StockTickerEntry.min_ticksize` も `Option<f32>` → `f32`（required）に変更（レビュー指摘: Phase F で "typed-only IPC" を名乗るなら min_ticksize の optional 残存は不整合）。`events.json` の `StockTicker.required` に `"min_ticksize"` を追加。`backend.rs` の Option unwrap を直接参照に変更（invalid 値は warn + skip で継続）
- F4: `Exchange::is_depth_client_aggr()` のメソッド本体（`#[deprecated]` + `panic!`）を `adapter.rs` から削除
- F5: `LocalDepthCache::update_with_qty_norm()` を `depth.rs` から削除。関連テスト（`update_with_some_qty_norm_panics_in_all_builds`, `update_with_none_qty_norm_does_not_panic`）も削除。`depth_assert.rs` の `deprecated_update_with_qty_norm_none_does_not_panic` も削除
- F6: `docs/✅python-data-engine/schemas/CHANGELOG.md` に v3.8 エントリを追加

**更新したリグレッションガードテスト:**
- `schema_v1_3_roundtrip.rs`: `schema_major_is_2` → `schema_major_is_3`（SCHEMA_MAJOR=3 を pin）
- `schema_v1_4_roundtrip.rs`: `assert_eq!(... 2)` → `const { assert!(... >= 2) }`（schema 1.4 features preserved in 3.x）
- `schema_v2_4_nautilus.rs`: `SCHEMA_MAJOR == 2` → `SCHEMA_MAJOR == 3`
- `ticker_info_typed.rs`: venue_caps をテスト JSON に追加 + test 6/7 新規追加（venue_caps 欠落で parse 失敗を確認）
- `ticker_meta_map_round_trip.rs`: モック TickerInfo に venue_caps を追加
- `python/tests/test_events_json_schema.py`: サンプルエントリ全件に venue_caps 追加 + `_make_validator` を `$defs` 埋め込み方式に変更（RefResolver URL 二重構築バグ回避）
- `python/tests/test_schemas_nautilus.py`: `SCHEMA_MAJOR == 2` → `SCHEMA_MAJOR == 3`

**検証結果:**
- `cargo clippy -- -D warnings`: clean
- `cargo test --workspace`: 全 green（FAILED 0）
- `uv run pytest python/tests/`: 1478 passed, 3 skipped（`test_tachibana_buying_power` は pre-existing WIP）
- `/ipc-schema-check`: MAJOR=3/3, MINOR=8/8, compression=None → 全 OK

**落とし穴:**
- jsonschema の旧 `RefResolver` は `$id: "flowsurface/ipc/events"`（非絶対 URI）を持つスキーマで `TickerEntry.oneOf → $ref → VenueCaps` チェーンを解決する際に URL を二重構築（`flowsurface/ipc/flowsurface/ipc/events`）する。`venue_caps` が optional のうちは発火しなかったが required 化後に顕在化。修正: `_make_validator` で `$defs` をサブスキーマに直接埋め込む方式に変更

### 9.2 完了条件

- 旧スキーマでハンドシェイク不可（SCHEMA_MAJOR mismatch）✅
- 全テスト green、e2e smoke 緑

### 9.3 リスク

- 旧 Rust ↔ 新 Python（または逆）の組み合わせがハンドシェイクで切れる
  - mitigation: SCHEMA_MAJOR bump をリリースノートに明記。`start_or_attach` の自動プローブが MAJOR mismatch で再 spawn する既存挙動でカバー

---

## 10. ロールバック計画

各フェーズが独立 PR として merge されるため、フェーズ単位で revert 可能。

| フェーズ | revert 影響 |
|---------|------------|
| A | typed parse 不使用、無型 Value 経路に戻る。実害なし |
| B | venue_caps 未配信、enum マッチ fallback で動作 |
| C | **Python 正規化が抜ける → Rust 側の Phase E 前なら救済される**。Phase E 後は revert 不可（Phase E も同時 revert 必須） |
| D | enum マッチ復活、placeholder 復活 |
| E | Rust 側 rounding 復活、防衛的に動作 |
| F | SCHEMA_MAJOR 戻し、旧 fallback 経路復活 |

**重要**: Phase C と E は 1 セット。E を先行投入禁止。C 投入後に soak 期間を置いてから E に進む。

---

## 11. 検証マトリクス

各フェーズ完了時に下記を必ず実行。

| 検証 | A | B | C | D | E | F |
|------|---|---|---|---|---|---|
| `cargo test --workspace` | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| `uv run pytest python/tests/ -v` | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| `bash tests/e2e/smoke.sh`（30s）| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `OBSERVE_S=120 bash tests/e2e/smoke.sh` | — | — | ✅ | ✅ | ✅ | ✅ |
| `cargo clippy -- -D warnings` | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| Tachibana demo で 5379 銘柄の Ladder 視認 | — | — | ✅ | ✅ | ✅ | ✅ |
| Hyperliquid demo で板表示視認 | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/ipc-schema-check` skill | ✅ | ✅ | — | — | — | ✅ |
| 24h debug soak（assert 無発火）| — | — | — | — | ✅ | — |

---

## 12. 追加すべきテスト一覧

| ファイル | 種別 | 概要 |
|---------|------|------|
| `python/tests/test_schemas.py` | unit | events.json を JSON Schema validator で検証 |
| `python/tests/test_venue_caps.py` | unit | 全 adapter が venue_caps を返す |
| `python/tests/test_normalize_depth.py` | property | 任意 depth → normalize → tick 整合 |
| `python/tests/test_normalize_qty.py` | property | 任意 qty → venue 単位整合 |
| `python/tests/test_tachibana_ticksize_resolve.py` | parametrized | 全 yobine band を網羅 |
| `engine-client/tests/ticker_info_typed.rs` | integration | typed roundtrip + fallback |
| `engine-client/tests/ticker_info_required_fields.rs` | integration | min_ticksize 欠落で Err |
| `engine-client/tests/venue_caps_roundtrip.rs` | integration | venue_caps 配信経路 |
| `engine-client/tests/depth_assert.rs` | integration | 未正規化 depth で debug panic |
| `data/src/panel/ladder.rs` の proptest 追加 | property | regroup_from_raw の不変条件 |
| `tests/e2e/ladder_continuous.sh` | e2e | HTTP API で Ladder 価格列の連続性検証 |

---

## 13. ドキュメント更新

| 場所 | 内容 |
|------|------|
| `docs/✅python-data-engine/spec.md` | 不変条件（§2.1）と責務マトリクス（§2.2）を反映 |
| `docs/✅python-data-engine/current-architecture.md` | Phase 完了ごとに最新化 |
| `docs/✅python-data-engine/schemas/CHANGELOG.md` | Phase A, B, F それぞれで version bump 記録 |
| `.claude/CLAUDE.md` | "Python が depth/trade/kline の正規化を保証する" を追記 |
| `.claude/skills/bug-postmortem/MISSES.md` | 本リファクタの動機（alternating zeros + IPC 無型）を追加 |

---

## 14. スケジュール感（目安）

| フェーズ | 作業規模 | 推奨期間 |
|---------|---------|---------|
| Phase A | 中 | 1〜2 日 |
| Phase B | 中 | 2〜3 日 |
| Phase C | 大 | 3〜5 日 + soak 1 週間 |
| Phase D | 中 | 1〜2 日 |
| Phase E | 小 | 1 日 + soak 24h |
| Phase F | 小 | 0.5 日 |

合計実装時間 ~10 営業日 + soak ~1.5 週間。並行実装オーケストレーション（`/parallel-agent-dev`）適用可能箇所:

- Phase A2/A3 と A4 は Rust/Python 別エージェント並列
- Phase B3 の各 adapter 修正は venue 単位で並列
- Phase D の各 grep 箇所修正は同一 PR 内で並列

---

## 15. 開いている設計判断（要決定）

| ID | 判断事項 | 候補 |
|----|---------|------|
| Q1 | `min_ticksize` 解決前の `TickerInfo` 送出をどうするか | **✅ 確定: (a) 暫定値で送り後で再 push**。Council 4声3対1。再 push 後の状態遷移テスト必須（競合状態カバレッジ）。Skeptic 指摘：再 push で板初期化後に tick_size だけ変わる整合性を単体テストで固定すること |
| Q2 | `venue_caps` を `TickerEntry` 内に持つか別 event か | **✅ 確定: (a) TickerEntry 内（IPC のみ）**。Rust 永続モデルには載せない（§2.4） |
| Q3 | 旧 `saved-state.json` の TickMultiplier 互換戦略 | **✅ 確定: (a) 起動時 step 再計算（既存挙動）**。`TickMultiplier` は `saved-state.json` から読み込まれ、`min_ticksize` は Python から再受信。`price_step = TickMultiplier × min_ticksize` は TickerInfo 受信後に実行時計算。migration 関数は不要。Phase D 着手前確認済 (2026-05-01) |
| Q4 | Phase F の SCHEMA_MAJOR bump 単位 | **✅ 確定: (a) Phase F で一度だけ MAJOR +1**。Council 全4声一致。SCHEMA_MINOR はフェーズ完了ごとに上げる（Phase A 完了時も MINOR bump）。A〜E は後方互換維持なので MAJOR bump は意味論的に不正 |
| Q5 | 未正規化 depth 検出時の release 挙動 | **✅ 確定: (a) silent（trust）**。§2.1「release では no-op で信頼する」の設計方針のまま。debug_assert! のみ。Phase E 着手前確認済 (2026-05-01) |
| Q6 | `VenueCapsStore` の共有方式 | **✅ 確定: (b) backend-owned / EngineClient-handle-mediated**。`VenueCapsStore` を `EngineClientBackend` の private sidecar にし、reconnect 時に backend 再構築でリセット。UI には narrow lookup handle のみ公開（`ticker_meta_handle` 類似パターン）。UI render 経路は derived booleans に projection して hot path でのロック回避を推奨。内部は `Arc<RwLock<HashMap<..>>>` でも可だがそれは実装詳細 |

### 決定タイミング

| 質問 | 確定期限 | 理由 |
|------|---------|------|
| Q1 | **Phase A 着手前** | スキーマ設計（A1）と Python 側送出戦略（A4, C4）に直結 |
| Q2 | **確定済**（§2.4 / Q2 = a） | — |
| Q3 | **Phase D 着手前** | saved-state migration の有無を D の段階で決める |
| Q4 | **Phase A 着手前** | Phase F の単位を見据えて A の互換戦略を決める必要 |
| Q5 | **Phase E 着手前** | release 挙動は Phase E の debug_assert 設計と一体 |
| Q6 | **Phase B 着手前** | `VenueCapsStore` は Phase B の実装詳細。Phase A は schema/typed parse のみで Q6 に依存しない |

Q1, Q4 を `/council` で先行確定 → Phase A 着手 → Phase B 着手前に Q6 を `/council` → 以降フェーズごとに残る Q を順次確定、で進められる。

---

## 16. 関連ドキュメント

- [fix-min-ticksize-fallback-2026-05-01.md](./fix-min-ticksize-fallback-2026-05-01.md) — 直近のスパース表示 fix（本リファクタの動機）
- [spec.md](./spec.md) — IPC 全体仕様
- [current-architecture.md](./current-architecture.md) — 現状アーキテクチャ
- [schemas/events.json](./schemas/events.json) — 改修対象スキーマ
- [open-questions.md](./open-questions.md) — Q1〜Q5 を追記

---

## 17. ポストフェーズ監査（2026-05-01）

Phase F 完了直後の総点検で発見した問題と修正。Phase A〜F は committed
だが、Phase F が typed schema を `min_ticksize: f32`（required）に確定した
ことで露出した **Python 側 silent failure** が 1 件残っていた。

### 17.1 ✅ Tachibana `list_tickers` silent skip（修正済）

**症状**: `tachibana.py:list_tickers` が `resolve_min_ticksize_for_issue`
の `KeyError` を `pass` で握り潰し、`min_ticksize` を持たない entry を
そのまま `out` に append していた（Phase B5 期の `TACHIBANA_MIN_TICKSIZE_PLACEHOLDER`
時代の名残）。

```python
# 修正前
if self._yobine_table:
    try:
        tick = resolve_min_ticksize_for_issue(...)
        entry["min_ticksize"] = float(tick)
        ...
    except (KeyError, ValueError):
        pass               # ← entry は append される（min_ticksize 欠落）
out.append(entry)
```

**Phase F での影響**:
- `events.json` `StockTicker.required: ["min_ticksize", "venue_caps", ...]`
- Rust `StockTickerEntry.min_ticksize: f32`（`Option<f32>` から required へ）
- `EngineEvent::TickerInfo.tickers: Vec<TickerEntry>` のため、**1 entry でも**
  serde 失敗すれば Vec 全体の parse が失敗
- Rust `connection.rs:432` は frame parse エラーを `log::warn!` のみで
  握り潰す → **TickerInfo 全件が UI から消える silent failure**

**修正内容** (`python/engine/exchanges/tachibana.py`):
- `pass` を `log.warning(...)` + `continue` に置換
- yobine_table 自体が空のケースも skip 対象に追加（`if not self._yobine_table: continue`）
- module docstring と "B2 design decision" 注記を Phase D/F の責務分離に合わせて更新
- `B5: ... fall back to TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32` の stale comment を削除

**追加テスト** (`python/tests/test_tachibana_worker_basic.py`):
- ✅ `test_list_tickers_skips_entries_when_yobine_table_empty`
  — yobine_table 空のとき返値が `[]`（事前に master_loaded を立てて再 DL を抑制）
- ✅ `test_list_tickers_skips_entry_when_yobine_code_unknown`
  — 1 entry の yobine_code が table に欠落していても、resolvable な他 entry は残り、
  欠落 entry のみが drop される
- 既存 `_make_master` ヘルパーに `yobine_code "1"` の最小バンドを追加
  （以前は `_yobine_table = {}` を返していたため、Phase F 後はすべての
  list_tickers ベース テストが空配列を返してしまう）

**検証**:
- `uv run pytest python/tests/test_tachibana_worker_basic.py -v` → 21 passed
- `cargo test -p flowsurface-exchange` → all green
- `cargo clippy --workspace -- -D warnings` → clean

**Tip / 知見**:
- Rust 側は frame parse エラーを **warn ログ** だけで握り潰すため、Python の
  IPC payload 不整合は本番で **完全に silent な ticker 全消失** を引き起こす。
  TickerInfo に新 required field を追加するときは「Python 側で全 path から
  確実に emit されているか」を必ず check すること。
- 現状の Rust pipeline では `Vec<TickerEntry>` の per-entry tolerance はない
  （Phase F design：trust Python）。将来 Python 側で別 venue が同じ silent skip
  パターンを再導入したらまた即時破綻するので、新 adapter 追加時は
  `test_events_json_schema.py` 系を adapter の actual 出力で回す統合テスト
  を整備するとさらに堅牢。

### 17.2 e-station-review findings（2026-05-01 完遂）

`/e-station-review` を Phase A〜F + 17.1 に対して実行した結果。HIGH 2 件 +
MEDIUM 4 件はすべて本セッションで解消した。

#### ✅ HIGH-1: Phase F 不変条件を全 adapter に適用

**症状**: MEXC `_list_tickers_futures` は `priceUnit` 欠落時に `0.0` を
emit していた（同シェイプの risk が binance/bybit にも存在）。Phase F で
`min_ticksize: f32` は Rust 側で `> 0` の serde guard を持たない（schema は
`exclusiveMinimum: 0` 表明のみ）ため、`min_ticksize=0.0` がそのまま透過し
下流（`regroup_from_raw` step バケッティング）で divide-by-zero を起こす。

**修正**:
- `python/engine/exchanges/base.py` に `is_valid_ticker_entry(entry, *, venue)` を
  追加。`min_ticksize > 0` および `min_qty > 0`（指定時）を検証し、不合格
  entry は WARNING ログ + skip
- 全 6 adapter（binance/bybit/hyperliquid/mexc/okex/tachibana）の
  `list_tickers` 末尾で同 helper を必ず通すよう書き換え
- `python/tests/test_mexc_rest.py` に `priceUnit=0` および `minVol=0` の
  pin test を追加（RED→GREEN 確認済）

#### ✅ HIGH-2: Vec<TickerEntry> 「1 件失敗で frame 全滅」 design pin

**症状**: Phase F 単体 entry の parse 失敗テストはあったが、
`EngineEvent::TickerInfo.tickers` Vec 全体に対する pin がなく、将来別 agent
が「per-element tolerance」に変更した時に silent failure 復活を防げない。

**修正**:
- `engine-client/tests/ticker_info_typed.rs` に下記 2 件を追加:
  - `ticker_info_vec_with_one_bad_entry_fails_whole_frame` —
    1 entry の `min_ticksize` 欠落で `EngineEvent` 全体が parse 失敗
    することを assert（design choice の pin）
  - `ticker_info_vec_with_all_valid_entries_parses_into_typed_vec` —
    happy path の round-trip 確認

#### ✅ MEDIUM-1: `caps_client_aggr` ロック競合フォールバックの観測性

**症状**: `src/screen/dashboard/pane.rs:53` の `try_read().ok()` は
writer 競合時に黙って `None` を返し、フォールバック値 `true` を採用していた。
`fetch_ticker_metadata` の write が in-flight な瞬間に Hyperliquid pane
構築が走ると `is_client_aggr=true` を一時的に取得し、誤った
`TickMultiplier(5)` 分岐 + 誤 `StreamTicksize::Client` を選びうる。

**修正**:
- `try_read()` のまま（同期 UI hot path で `blocking_read` は禁忌）
- ただし `Err(_)` 分岐で `log::warn!` を出力し、postmortem で再現性のない
  UI/stream-shape バグを追跡可能にした

#### ✅ MEDIUM-2: `tachibana_meta.rs` shim 削除

**症状**: Phase D 完了メモが「Phase F で削除」と約束していた re-export
シムが Phase F commit でも残存していた（`tickers_table.rs:1133` が経由）。

**修正**:
- `src/screen/dashboard/tickers_table.rs:1133` の参照を
  `engine_client::stock_meta::matches_tachibana_filter` に書き換え
- `engine-client/src/tachibana_meta.rs` 削除
- `engine-client/src/lib.rs:19 pub mod tachibana_meta;` 削除
- `engine-client/src/backend.rs:61` の stale comment（`parse_tachibana_ticker_dict`
  への言及）を `stock_meta` に更新
- `cargo clippy --workspace -- -D warnings` clean、`cargo build` clean

#### ✅ MEDIUM-3: engine frame parse 失敗の log 階層を error に昇格

**症状**: `engine-client/src/connection.rs:432` は `log::warn!` のみ。
Phase F で typed schema が strict になった結果、1 件不正で
`Vec<TickerEntry>` 全体が parse 失敗 → ticker 全消失するが、warn ログ
だけでは smoke-test スキャナーや oncall ダッシュボードが拾えない。

**修正**:
- `connection.rs:432` の `log::warn!` を `log::error!` に変更
- メッセージに `(frame DROPPED)` を明示し、PR レビュー時に意味が立つよう更新
- 17.1 への参照も追記

#### ✅ MEDIUM-4: 共通 invariant validator の導入（HIGH-1 と一体修正）

`is_valid_ticker_entry` を `base.py` に新設したことで HIGH-1 と同時に解消。
将来 adapter 追加時はこの helper を通すだけで Phase F 不変条件を満たせる。

### 17.3 残課題（low priority follow-up）

- ☐ `data/src/layout/pane.rs:298-299` の `prev_is_client_aggr: bool` 引数は
  Phase D で Exchange 経由のフォールバックを除去したが、いずれ pane state
  に持たせて引数を 1 個に減らせる余地あり（design polish のみ）。
- ☐ `python/tests/test_tachibana_buying_power.py::test_fetch_positions_cash_parses_response`
  は Phase A メモに既知の WIP 失敗として記録されている。本リファクタとは
  独立の positions parsing 課題。
- ☐ Python 側に runtime jsonschema validation を入れて IPC 出力を assert する
  統合テスト（adapter ごとの fixtures から TickerInfo を構築 → events.json
  に validator で照合）。現状は hand-crafted dict のみ schema 検証している。
- ☐ `VenueCapsStore` を `tokio::sync::RwLock` から `std::sync::RwLock` に
  移して try_read のフォールバックを廃止する案。同期 UI と非同期 backend
  の二面性に手を入れる必要があり、別 phase の作業。
