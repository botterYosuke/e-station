# 複数銘柄リプレイ対応 修正プラン（提案）

> **ステータス**: 提案（未着手・未承認）
> **作成日**: 2026-05-05
> **対象**: `engine.replay_session` / `engine.scenario` / `engine.nautilus.engine_runner` / IPC schema / GUI replay panes

## 1. 背景・動機

現状、`docs/example/buy_and_hold_minute.py` などで使う `SCENARIO.instrument` は単一文字列で、
リプレイハーネスは end-to-end で「1 戦略 = 1 銘柄」を前提にしている。

しかし以下のユースケースでは複数銘柄を同一バックテストで扱いたい:

- **ペアトレード / 統計的裁定**: 2 銘柄以上の相関に基づく戦略
- **バスケット / インデックス連動**: TOPIX Core 30 のような銘柄群
- **クロスセクション分析**: 同セクター内の相対強弱

Nautilus Trader 自体は `BacktestEngine.add_instrument()` を複数回呼べばマルチインストルメントを
そのまま扱える。e-station のハーネス側で硬く 1 銘柄に縛っている箇所だけが障壁。

> **D1（不変条件・継続）**: replay モードでは OrderBook / QuoteTick を提供しない方針は本プランでも維持。
> 複数銘柄でも各銘柄が `TradeTick` / `Bar` のみを emit する点は変わらない。

## 2. 現状の制約（根拠）

| # | 場所 | 内容 |
|---|---|---|
| C1 | [python/engine/scenario.py:45-52](../../python/engine/scenario.py#L45-L52) | `Scenario` TypedDict の `instrument: str`。単数前提 |
| C2 | [python/engine/replay_session.py:1655](../../python/engine/replay_session.py#L1655) | CLI `--instrument` は `default=None` の単一値。`nargs` 指定なし |
| C3 | [python/engine/schemas.py:723](../../python/engine/schemas.py#L723), [:749](../../python/engine/schemas.py#L749), [:779](../../python/engine/schemas.py#L779) | IPC schema (`LoadReplayData`, `EngineStartConfig` 等) の `instrument_id: str` が単数 |
| C4 | [python/engine/nautilus/engine_runner.py:259-385](../../python/engine/nautilus/engine_runner.py#L259-L385) | `start_backtest_replay()` は単一 `instrument_id` を受け、`load_*_bars()` を 1 回しか呼ばない。`add_instrument()` も 1 回のみ |
| C5 | [python/engine/nautilus/engine_runner.py:480-](../../python/engine/nautilus/engine_runner.py#L480) | `start_backtest_replay_streaming()` も同形 |
| C6 | docs/example/*.py | `SCENARIO.instrument` を単一文字列前提でサンプル化 |
| C7 | GUI replay panes | `ReplayDataLoaded.instrument_id: str | None` を単数前提で受け取り、`auto_generate_replay_panes` も 1 銘柄想定 |

## 3. 設計方針

### 3.1 後方互換戦略

既存の単一銘柄 `SCENARIO`（`"instrument": "1301.TSE"`）は **schema_version=1 のまま動かし続ける**。
複数銘柄対応は **schema_version=2** で `instruments: list[str]` を新設し、`scenario.validate()` が両方を受ける形にする。

```python
# schema_version=1 (既存・後方互換)
SCENARIO = {"schema_version": 1, "instrument": "1301.TSE", ...}

# schema_version=2 (新)
SCENARIO = {"schema_version": 2, "instruments": ["1301.TSE", "7203.TSE"], ...}
```

> **判断の根拠**: 既存ユーザーの `.py` を 1 行も書き換えずに動かし続けたい。
> v1 → v2 強制マイグレーションは、リプレイ実験のレシピを破壊するためコストに見合わない。

### 3.2 IPC schema の拡張方針

`SCHEMA_MINOR` を bump して、既存の `instrument_id: str | None` フィールドはそのまま残し、
`instrument_ids: list[str] | None` を **オプショナル追加** する（`serde(default)` 互換）。

- 旧 GUI（minor 古い）→ `instrument_ids` を無視、`instrument_id` だけ読む（単一銘柄として動作）
- 新 GUI → `instrument_ids` が非空ならそれを使い、なければ `instrument_id` にフォールバック

> **D8 整合**: モード切替セマンティクスは不変。1 プロセス 1 セッション内で銘柄数が増えるだけ。

### 3.3 venue / account の扱い

- venue は引き続き `"REPLAY"` 固定。複数銘柄は同一 venue 内に並べる
- `add_instrument()` を銘柄ごとに 1 回ずつ呼ぶ
- `starting_balances` は単一通貨でまとめて 1 アカウント（PnL は合算）。複数通貨混在は **本プランの非ゴール**

### 3.4 データロードの並行性

`load_minute_bars()` / `load_daily_bars()` / `load_trades()` を銘柄ごとにループ呼び出しする。
`add_data()` で nautilus のイベント時系列マージは内部で処理されるため、こちらは順序を気にせず銘柄ごとに add すればよい。

> **メモリ注意**: 1 週間 × 1 銘柄で約 1,500 本の分足。10 銘柄なら 15,000 本。
> 大規模銘柄数 × 長期間の組み合わせはメモリ上限を引き上げないと OOM の可能性あり。
> 受け入れテスト T-AC-5（後述）でガードする。

## 4. 実装タスク（依存順）

### M1: scenario schema v2（基盤）

- [ ] M1.1 [scenario.py](../../python/engine/scenario.py) に schema_version=2 ブランチを追加
  - `Scenario_v2` TypedDict を新設し、`instruments: list[str]` を必須化
  - `validate()` を schema_version で分岐
  - `extract()` は両形式を受ける（変更不要だが回帰テスト追加）
- [ ] M1.2 [scenario.py](../../python/engine/scenario.py) `_dict_to_cst_expr` に `list[str]` 値の対応を追加（現在は str/int/bool のみ）
- [ ] M1.3 単位テスト: v1 / v2 双方で extract → validate → write_back round-trip

### M2: CLI / replay_session API

- [ ] M2.1 [replay_session.py:1655](../../python/engine/replay_session.py#L1655) `--instrument` を `nargs='+'` 対応にし、`--instruments` も別名で追加
- [ ] M2.2 `ReplaySession.load()` の引数を `instrument_id: str | list[str]` に拡張（単数も list[1] 扱いに正規化）
- [ ] M2.3 `SCENARIO` フォールバック解決経路を v1/v2 両対応に
- [ ] 単位テスト: CLI args の正規化、SCENARIO フォールバックの優先順位

### M3: IPC schema 拡張（GUI 配線）

- [ ] M3.1 [schemas.py](../../python/engine/schemas.py) `LoadReplayData` / `EngineStartConfig` / `ReplayDataLoaded` に `instrument_ids: list[str] | None = None` を追加
- [ ] M3.2 `SCHEMA_MINOR` を +1 bump（v1 schema 確認は `/ipc-schema-check` スキル）
- [ ] M3.3 Rust 側 `engine-client` の `#[serde(default)]` で旧 GUI 互換を維持
- [ ] M3.4 GUI `auto_generate_replay_panes` を複数銘柄対応（銘柄数 × pane を生成）

### M4: nautilus runner の複数銘柄ロード

- [ ] M4.1 [engine_runner.py:259](../../python/engine/nautilus/engine_runner.py#L259) `start_backtest_replay()` を `instrument_ids: list[str]` 対応に
  - `add_instrument()` をループ
  - `load_*_bars()` をループし、各銘柄分の bars/ticks を `add_data()` で投入
  - `ReplayDataLoaded` を銘柄ごとに emit するか一括かは spec 項目（決定: **銘柄ごとに emit**、GUI が pane を順次起こせるように）
- [ ] M4.2 [engine_runner.py:480](../../python/engine/nautilus/engine_runner.py#L480) `start_backtest_replay_streaming()` も同様に
- [ ] M4.3 単一銘柄 API（既存呼び出し）は `[instrument_id]` への正規化で吸収し、削除しない

### M5: ドキュメント / サンプル

- [ ] M5.1 [docs/example/README.md](../example/README.md) に複数銘柄サンプルの節を追加
- [ ] M5.2 新規サンプル `docs/example/pair_trade_minute.py` を追加（schema_version=2 + 2 銘柄 + 簡易ペアトレード）
- [ ] M5.3 [docs/wiki/backtest.md](../wiki/backtest.md) に複数銘柄起動例と制約（D1・メモリ）を追記
- [ ] M5.4 本ファイル（multi-instrument-replay-plan.md）を `archive/` 移送、`README.md` に「実装済み」リンクを追加

### M6: 受け入れテスト

| ID | 内容 |
|---|---|
| T-AC-1 | v1 SCENARIO（既存）が無修正で動くこと（回帰） |
| T-AC-2 | v2 SCENARIO（2 銘柄）で `replay_session run` が完走し、両銘柄の `Bar` が strategy `on_bar` に到達 |
| T-AC-3 | CLI `--instrument 1301.TSE 7203.TSE` で SCENARIO を上書きできる |
| T-AC-4 | 旧 GUI（古い SCHEMA_MINOR）が単一銘柄として fallback して動く |
| T-AC-5 | 10 銘柄 × 1 営業日（minute）でメモリ上限内に完走（OOM ガード） |
| T-AC-6 | データ未収録の銘柄が混ざった場合、明示的エラーで停止し、他銘柄に黙って fallback しない（silent failure ガード） |

## 5. 非ゴール / 先送り

- **複数通貨混在**: 全銘柄が同一通貨（JPY 想定）。USD/JPY 混在の cross-currency PnL 計算は別フェーズ
- **動的銘柄追加**: バックテスト走行中の銘柄追加は不可。起動時に確定
- **板（OrderBook）対応**: D1 不変条件により replay では OrderBook を出さない。複数銘柄でも維持
- **live モード**: 本プランは replay 専用。live 側の複数銘柄購読は既に Nautilus 標準で動くため別議論

## 6. リスク・未解決項目

| # | 項目 | 対応 |
|---|---|---|
| R1 | `schema_version` の自動マイグレーション提供 | 提供しない（v1 はそのまま動く方針） |
| R2 | GUI の chart pane が銘柄数に応じて自動レイアウト崩れ | M3.4 の `auto_generate_replay_panes` で 1 行レイアウト → 縦並びへ拡張。詳細 spec は M3 着手時に決める |
| R3 | `S:\j-quants\` の銘柄不在ファイル誤判定 | T-AC-6 で必ず明示エラー。`silent-failure-hunter` スキルでレビュー |
| R4 | 戦略 `on_bar` で銘柄ごと state を持たない実装が混入 | サンプル `pair_trade_minute.py` でベストプラクティス提示 |

## 7. 着手順序（推奨）

1. **M1（scenario v2）** を先に固める。ここが TypedDict の SoT で、他全部が依存
2. M3（IPC schema）と M4（runner）は並行可。両方とも M1 完了後
3. M2（CLI）は M1 完了後・M4 完了前でも着手可（単独テスト可能）
4. M5（docs）と M6（受け入れテスト）は M2-M4 着地後にまとめて

> **review-fix-loop 推奨**: M3-M4 着地時点で `review-fix-loop` スキルを必ず回す。
> IPC schema 変更 + runner 変更 + GUI 配線が同時に動くため、レビュー観点が広い。

## 8. 参考

- 単一銘柄前提の経緯: [archive/replay-script-cli-args.md](archive/replay-script-cli-args.md)
- 不変条件: [README.md §D1, §D8](README.md)
- スキーマ運用: `/ipc-schema-check` スキル
