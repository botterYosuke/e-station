# 引継ぎプロンプト — Replay Market Data 配線（§4 Rust 側）

## あなたへの依頼

`docs/✅nautilus_trader/replay-market-data-emit.md` の **§4. Rust 側 — 自動生成
CandlestickChart の購読配線** を実装してください。

§1〜§3（Python 側 emit / server.py 振り分け / docstring 整理）は既に完了しています。
§4 のみが残作業です。背景・調査結果・既知の不変条件・分解（4a / 4b / 4c）・スコープ外
は plan ドキュメントに集約してあります。**先に plan を最後まで読んでから着手してください。**

参照すべき plan / spec：

- `docs/✅nautilus_trader/replay-market-data-emit.md`（本作業の Single Source of Truth）
- `docs/✅nautilus_trader/spec.md` §3.2（CacheConfig 不変条件・mode 起動時固定）
- `docs/✅nautilus_trader/architecture.md`（IPC 境界・venue タグの D5 解釈）
- `CLAUDE.md` ルート（圧縮・schema バージョン規約・テスト構成）

## Goal

`bash scripts/run-replay-debug.sh docs/example/sma_cross.py` 実行時に、
auto-generated CandlestickChart ペインに `1301.TSE` の Daily ローソクが順次描画され、
TimeAndSales ペインに `Trades` がストリームされる状態にする。

## Constraints

1. **段階実装 (A)**: 4a → 4b → 4c の順で進める。各ステップ完了時に独立コミット。
   1 PR にまとめない（レビュー粒度を保つため）。
2. **既存 venue / Exchange の挙動を破壊しない**: Bybit / Binance / Hyperliquid /
   Okex / Mexc / Tachibana の network smoke と既存テストはすべて保持する。
3. **schema 互換**: `KlineUpdate` / `Trades` の wire は変更しない。
   `engine-client/src/lib.rs` の `SCHEMA_MAJOR` / `SCHEMA_MINOR` は触らない。
4. **`start_backtest_replay()` (run-once 版) は触らない**:
   決定論性テスト・gym_env が依存している。
5. **silent failure の禁止**: `exchange_for("replay", ...)` の Binance フォールバック
   warn が消えること（plan §4 調査結果参照）。Replay venue の正規ルートを通す。
6. **テスト先行 (TDD)**: 各 Rust 変更は failing test → 実装 → green の順。
   詳細は `.claude/skills/tdd-workflow/SKILL.md` 参照。
7. **計画書の生きた更新**: 進捗・新たな知見・設計判断・落とし穴を
   `replay-market-data-emit.md` §4 に追記する（後述「進捗共有」参照）。

## Acceptance criteria

### 4a. 基盤（Venue::Replay / Exchange::ReplayStock）

- `cargo build --workspace` 成功
- `cargo test --workspace` 全 pass（既存テスト 0 件破壊）
- `cargo clippy -- -D warnings` 通過
- 新規テストで `Venue::from_str("replay")` → `Venue::Replay`、
  `Exchange::from_venue_and_market(Venue::Replay, MarketKind::Stock)` →
  `Exchange::ReplayStock` が返ることを pin
- `Exchange::ReplayStock.supports_kline_timeframe(Timeframe::D1) == true`、
  `M1 == true`、`H1 == false`（Daily/Minute のみ）

### 4b. replay モード時の `EngineClientBackend` 登録

- `--mode replay` 起動時、起動ログに全 7 venue 分の `EngineClientBackend` が registered と出る
- `--mode live` を回しても 4a 既存テストおよび tachibana smoke が破壊されない
- 受け入れ test: `VENUE_NAMES` に `(Venue::Replay, "replay")` が含まれることを
  source pin する（既存の `venue_names_includes_tachibana.rs` パターン踏襲）

### 4c. 自動生成 CandlestickChart の auto-bind

- `bash scripts/run-replay-debug.sh docs/example/sma_cross.py` を実行し、
  CandlestickChart ペインに `1301.TSE` Daily ローソクが描画される
- `[replay-load] start OK` 後、Rust ログに `engine ws read error` /
  `kline_stream: lagged` / `falling back to Binance` が **出ない**
- TimeAndSales ペインに `Trades` イベントが受信される（granularity=Trade のとき）
- granularity=Daily のとき `Timeframe::D1` で購読、Minute のとき `Timeframe::M1`
- 単体テスト: `auto_generate_replay_panes("1301.TSE", "Daily")` 後に
  `Kline { ticker.ticker == "1301", timeframe == D1 }` の StreamKind が pane state に登録される
- granularity=Trade のとき CandlestickChart は生成しない（Bar 無し）

### 全体

- 既存テスト 1323 件（Python）+ 既存 cargo test workspace すべて pass
- E2E 手動: `bash scripts/run-replay-debug.sh docs/example/sma_cross.py` で
  ローソクが順次表示される（観測：30 秒以上）

## 実装スキル

- **並行実装**: `.claude/skills/parallel-agent-dev/SKILL.md` を読み、
  4a 内の独立タスク（enum 拡張・match 漏れ修正・VENUE_NAMES 更新・各 capability test）
  を依存グラフ化して並列起動。直列ステップは 4a → 4b → 4c の境界で切る。
- **TDD**: `.claude/skills/tdd-workflow/SKILL.md` を読み、各 enum バリアント追加 /
  match arm / auto-bind ロジックは failing test 先行 → green の順で進める。
- **完了後レビュー**: 全 4a-4c 完了後、`.claude/skills/review-fix-loop/SKILL.md`
  を起動し MEDIUM 以上の指摘がゼロになるまで loop する。
  e-station-review スキルが review 段で使われることを確認すること。

## 進捗共有のルール

`docs/✅nautilus_trader/replay-market-data-emit.md` §4 を生きた作業ノートとして使う。

- ステップを完了するたびに見出しに `✅` を付ける（例: `##### 4a. 基盤 ✅`）
- 着手中で未完了は `🚧` を付ける
- ブロッカーや想定外の事象が出たら **新しい subsection** を §4 末尾に追記する:
  - 「### 4a 実装メモ（YYYY-MM-DD）」のような日付付き見出し
  - 何を試したか、何が動かなかったか、なぜそう判断したか
  - 後続作業者が同じ落とし穴を踏まないための Tips
- 設計判断（API 形・型シグネチャ・既存 helper の流用判断）は理由つきで残す
- スコープ外と判断したものは「### スコープ外（次フェーズ）」セクションに追加

例:
```markdown
##### 4a. 基盤 ✅ (2026-04-30)

実装完了。Exchange::ReplayStock の supports_heatmap_timeframe は
default の `_ => true` に乗せた（既存パターンと一致）。

###### 設計メモ
- Venue::Replay の default_quote_currency は Tachibana と同じ Jpy にした。
  替えるとしたら強行通貨非依存だが、既存 enum signature が QuoteCurrency
  必須なので将来 enum を Option 化する必要がある（Q-V2 として記録）

###### 落とし穴
- `Exchange::ALL` の長さを 16 に変更したが、tests/exchange_all_count.rs
  でハードコードされていてテスト 1 件 fail した。pin の方を 16 に更新済み。
```

## 始める前に

1. `git status --short` で現在の uncommitted を確認（クリーンであることを期待）
2. `docs/✅nautilus_trader/replay-market-data-emit.md` を最後まで読む
3. 4a の対象ファイルを `Read` で確認:
   - `exchange/src/adapter.rs`（Venue / Exchange）
   - `src/main.rs:182-189`（VENUE_NAMES）
   - `tests/venue_names_includes_tachibana.rs`（既存 pin パターン）
4. 既存テストの現状を `cargo test --workspace --no-run` で baseline 取得

## 作業外

- §1〜§3（Python 側 emit / server.py 振り分け / docstring）は完了済みなので触らない
- `scripts/run-replay-debug.sh` のデフォルト削除（MED-2）も完了済み
- replay 専用 venue UI フィルタボタンは plan §4「スコープ外（次フェーズ）」のため触らない
