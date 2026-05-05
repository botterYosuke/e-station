# WAL `accepted` 滞留によるモード切替ブロック障害

## 症状

ライブモード → リプレイモードへ切り替えようとすると以下のダイアログが出てブロックされる:

> 未約定の注文があります。
> モードを切り替えることができません。

ユーザー視点では未約定注文は無いにも関わらずブロックされる。

## 関連コード

- 判定呼び出し: [`src/main.rs:3880-3892`](../../src/main.rs#L3880-L3892)（`Action::SwitchMode` Live→Replay）
- 判定本体: [`src/main.rs:271-321`](../../src/main.rs#L271-L321)（`has_wal_in_flight_orders` / `has_wal_in_flight_orders_at`）
- WAL writer: [`python/engine/exchanges/tachibana_orders.py:569-623`](../../python/engine/exchanges/tachibana_orders.py#L569-L623)
- WAL ファイル: `~/.cache/flowsurface/engine/tachibana_orders.jsonl`

## デバッグ結果

[`src/main.rs:271-321`](../../src/main.rs#L271-L321) に診断ログを追加して再現。実環境 (`flowsurface-current.log` `[DEBUG-WALCHK]` プレフィックス) の観測値:

```
total_lines=46 parse_fail=0 missing_field=0 unique_orders=23 in_flight_count=20
decision has_in_flight=true
```

20 件の "in-flight" 判定の内訳:

| 種別 | 件数 | 例 |
|---|---|---|
| E2E テスト残骸 (`e2e-*` プレフィックス) | 17 | `e2e-1777349715-4127`, `e2e-mc-1777353398-7520`, `e2e-crash-1777352747-5487` |
| UUID 形式の実注文 | 3+ | `7d5ace4c-...`, `f55b362f-...`, `e69b3fba-...`, `118057e1-...`, `82894b5d-...` |

すべて `latest_phase = "accepted"`。venue 側では既に約定または取消済みだが、WAL 上は `accepted` で滞留している。

## 仮説検証サマリ

| ID | 仮説 | 結果 |
|---|---|---|
| H1 | `accepted` のまま終端 phase が無い古い注文が残っている | **確定** |
| H2 | `engine_cache_dir()` の解決先が想定と違う | 否定（想定通り `~/.cache/flowsurface/engine/`）|
| H3 | `client_order_id` または `phase` 欠損行で終端 `rejected` が拾えていない | 否定（`parse_fail=0 missing_field=0`）|
| H4 | `phase` が `rejected` 以外の終端表記（`canceled` / `filled` 等）で書かれている | 否定（観測された終端は `rejected` のみ、滞留は全て `accepted`）|
| H5 | 逆順走査での `or_insert` ロジックが意図と違う phase を固定 | 想定通り動作（`accepted` が最新行として正しく固定されている）|

## 根本原因

writer ([`python/engine/exchanges/tachibana_orders.py`](../../python/engine/exchanges/tachibana_orders.py)) は注文ライフサイクルのうち以下の 3 phase しか WAL に書かない:

- `submit`（送信前）
- `accepted`（venue 受付）
- `rejected`（venue 拒否）

立花の発注 API は受付応答までしか同期で返さず、約定 (filled) / 取消 (canceled) は EVENT ストリーム経由で venue 側が状態管理する。WAL writer はこれらの非同期終端イベントを受けて WAL に終端 phase を追記する経路を持っていないため、accepted されたあと約定/取消された注文は **WAL 上は永久に `accepted` のまま** になる。

判定側 [`src/main.rs:320`](../../src/main.rs#L320) は terminal phase を `rejected` のみと定義しているため、これらの残骸を全て in-flight として扱ってしまう。

```rust
// 現状（バグ）
latest_phase.values().any(|ph| ph.as_str() != "rejected")
```

## 修正プラン

### 採用方針

**「`accepted` のうち `ts` が当日 0:00 JST より古いものは terminal とみなす」**

立花の通常注文は当日限り有効（持ち越しは「期間指定」など特殊指定のみで、今は未対応）。前営業日以前の `accepted` は venue 側で必ず確定しており、未約定の可能性はゼロ。`submit` も同様（`submit` のまま 1 日以上残っているのは IPC 障害でしか起こらず、それも当日中に解消する）。

### 不採用案と理由

| 案 | 理由 |
|---|---|
| EVENT 受信時に終端 phase を WAL に書く | writer 側に大規模変更。約定通知の確実な配送保証が別問題として浮上 |
| CLMOrderList で venue の有効注文を都度問い合わせる | 切替経路に新たな I/O を追加、ネットワーク失敗時の挙動設計が必要 |
| 強制切替ボタン | ユーザー責任化はリプレイ進路に対する安全弁を弱める |
| WAL を毎回ローテート / 削除 | 約定済み注文の監査ログを失う |

### 実装変更

[`src/main.rs:271-321`](../../src/main.rs#L271-L321) `has_wal_in_flight_orders_at`:

1. レコードから `ts` (epoch_ms, JSON number) を読む
2. 「当日 0:00 JST」以降のレコードのみ `latest_phase` に積む
3. それ以前のレコードは終端扱いとして無視
4. 既存の `phase != "rejected"` 判定はそのまま維持

擬似コード:

```rust
let today_start_ms = jst_today_midnight_ms();
for line in content.lines().rev() {
    let record = ...;
    let ts = record.get("ts").and_then(|v| v.as_i64()).unwrap_or(0);
    if ts < today_start_ms {
        continue; // 前日以前は terminal 扱い
    }
    // 既存ロジック
}
```

JST 当日 0:00 算出は `chrono` の `FixedOffset(9*3600)` で `today().and_hms_opt(0,0,0)`。プロジェクト既に `chrono` 依存（[`src/logger.rs:38`](../../src/logger.rs#L38)）。

### 互換性 / 副作用

- Python 側 `wal_in_flight.detect_in_flight_orders` との CONTRACT（[`tests/wal_writer_reader_contract.rs`](../../tests/wal_writer_reader_contract.rs), [`python/tests/test_wal_in_flight_detection.py::TestWalContract`](../../python/tests/test_wal_in_flight_detection.py)）にも同じ「当日カットオフ」を入れる必要あり。両側で同期させないと CONTRACT テストが落ちる。
- 単体テストは fixed clock を引数で受け取る形にして当日判定を決定論化する。
- 既存テスト（[`tests/mode_switch_in_flight_order.rs`](../../tests/mode_switch_in_flight_order.rs)）は `has_wal_in_flight_orders_at` のシグネチャ変更（clock 注入）に追従が必要。

### テストケース追加

| ケース | 期待 |
|---|---|
| 当日の `accepted` 1 件 | `true`（in-flight 検出維持）|
| 前日 23:59 の `accepted` 1 件 | `false`（修正の核心）|
| 当日の `submit` 1 件 + 前日の `accepted` 1 件 | `true`（当日のみで判定）|
| 前日のみの `e2e-*` 残骸 17 件 | `false`（実環境再現）|
| 当日 `submit` → 当日 `rejected` | `false`（既存動作）|

## 作業手順

1. [x] [`src/main.rs`](../../src/main.rs) `has_wal_in_flight_orders_at` に `today_start_ms: i64` パラメタを追加、JST 当日 0:00 でフィルタ
2. [x] [`src/main.rs`](../../src/main.rs) `has_wal_in_flight_orders` から `jst_today_midnight_ms()`（`chrono::Utc::now()` ベース）を渡す
3. [x] Python 側 [`python/engine/wal_in_flight.py`](../../python/engine/wal_in_flight.py) `detect_in_flight_orders` に `today_start_ms` パラメタを追加（None → `jst_today_midnight_ms()`）
4. [x] CONTRACT テスト ([`tests/wal_writer_reader_contract.rs`](../../tests/wal_writer_reader_contract.rs)) — 既存シグネチャ pin はそのまま緑（`phase` / `client_order_id` / `rejected` の 3 点）
5. [x] テストケース 5 件 + 後方互換 + 関数 pin を [`src/main.rs::wal_today_cutoff_tests`](../../src/main.rs)・[`tests/mode_switch_in_flight_order.rs`](../../tests/mode_switch_in_flight_order.rs)・[`python/tests/test_wal_in_flight_detection.py::TestTodayCutoff`](../../python/tests/test_wal_in_flight_detection.py) に追加
6. [x] デバッグログ削除（旧 `[DEBUG-WALCHK]` 全行）
7. [x] `cargo test` / `pytest python/tests/test_wal_in_flight_detection.py` 緑化確認（Rust 8 件 + Python 5 件 新規通過、既存 47 + 6 件も維持）
8. [ ] 実環境で再現操作 → ダイアログが出ないことを確認 ← **ユーザー確認待ち**
