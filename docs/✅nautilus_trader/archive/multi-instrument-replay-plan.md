# 複数銘柄リプレイ対応 修正プラン（提案）

> **ステータス**: ✅ 実装完了
> **作成日**: 2026-05-05
> **完了日**: 2026-05-05
> **対象**: `engine.replay_session` / `engine.scenario` / `engine.nautilus.engine_runner` / IPC schema / GUI replay panes

## 1. 背景・動機 {#background-motivation}

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

## 2. 現状の制約（根拠） {#current-constraints}

| # | 場所 | 内容 |
|---|---|---|
| C1 | [python/engine/scenario.py:45-52](../../python/engine/scenario.py#L45-L52) | `Scenario` TypedDict の `instrument: str`。単数前提 |
| C2 | [python/engine/replay_session.py:1655](../../python/engine/replay_session.py#L1655) | CLI `--instrument` は `default=None` の単一値。`nargs` 指定なし |
| C3 | [python/engine/schemas.py:723](../../python/engine/schemas.py#L723), [:749](../../python/engine/schemas.py#L749), [:779](../../python/engine/schemas.py#L779) | IPC schema (`LoadReplayData`, `EngineStartConfig` 等) の `instrument_id: str` が単数 |
| C4 | [python/engine/nautilus/engine_runner.py:259-385](../../python/engine/nautilus/engine_runner.py#L259-L385) | `start_backtest_replay()` は単一 `instrument_id` を受け、`load_*_bars()` を 1 回しか呼ばない。`add_instrument()` も 1 回のみ |
| C5 | [python/engine/nautilus/engine_runner.py:480-](../../python/engine/nautilus/engine_runner.py#L480) | `start_backtest_replay_streaming()` も同形 |
| C6 | docs/example/*.py | `SCENARIO.instrument` を単一文字列前提でサンプル化 |
| C7 | GUI replay panes | `ReplayDataLoaded.instrument_id: str \| None` を単数前提で受け取り、`auto_generate_replay_panes` も 1 銘柄想定 |

## 3. 設計方針 {#design-principles}

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

**M1.1 に追記する内容**（A-2 High Priority Fix）:
- `validate()` を schema_version で分岐: v1 は `schema_version==1 and "instrument" in d`、v2 は `schema_version==2 and "instruments" in d` で判定
- `extract()` は両形式を受けるが変換しない。ユーザーが v1 → v2 migration は手動で行う

### 3.2 IPC schema の拡張方針

`SCHEMA_MINOR` を bump して、既存の `instrument_id: str | None` フィールドはそのまま残し、
`instrument_ids: list[str] | None` を **オプショナル追加** する（`serde(default)` 互換）。

- 旧 GUI（minor 古い）→ `instrument_ids` を無視、`instrument_id` だけ読む（単一銘柄として動作）
- 新 GUI → 起動時に定まる venue（live/replay）に応じた mode で動作。`instrument_ids` が非空ならそれを使い、なければ `instrument_id` を無条件読み出し。**venue fallback（動的選別）は行わない**（D8 mode-fixed rule）

> **D8 整合**: モード（live/replay）は起動時 CLI で 1 回固定。runtime での venue fallback は行わない。1 プロセス 1 セッション内で銘柄数が増えるだけ。

**M3.1 に追記する内容**（A-3 Medium Priority Fix）:
- Rust enum の `LoadReplayData` / `EngineStartConfig` は SCHEMA_MINOR bump 後、`#[serde(default)]` で旧 GUI との互換性を保証
- Python schemas.py の `model_validate()` は `instrument_ids` が Some なら優先、None なら `instrument_id` にフォールバック

**M3.2 修正**（B-4 Medium Priority Fix）:
- `SCHEMA_MINOR` を +1 bump 後、必ず `/ipc-schema-check` スキルを実行。expect SCHEMA_VERSION: `(3, 13)` または `(3, [N])` （current 確認後に設定）

### 3.3 venue / account の扱い

- venue は引き続き `"REPLAY"` 固定。複数銘柄は同一 venue 内に並べる
- `add_instrument()` を銘柄ごとに 1 回ずつ呼ぶ
- `starting_balances` は単一通貨でまとめて 1 アカウント（PnL は合算）。複数通貨混在は **本プランの非ゴール**
- **position identity は `(venue, instrument_id)` で一意。account は 1 つで PnL 合算**

> **内部 venue 制約**: [engine_runner.py:304-306](../../python/engine/nautilus/engine_runner.py#L304) は `InstrumentId.from_str(instrument_id)` から venue 文字列を取り出して `add_venue()` を **1 回だけ** 呼んでいる。複数銘柄が異なる内部 venue を持つ場合（例 `["1301.TSE", "AAPL.XNAS"]`）は現状 `add_venue()` が 1 回しか呼ばれず未対応。本プランは **全銘柄が同一内部 venue（例: `TSE`）** に限定する。mixed-venue 対応は非ゴール。M4.1a 実装時に「venue が全銘柄で一致すること」を validate すること。

### 3.4 データロードの並行性

`load_minute_bars()` / `load_daily_bars()` / `load_trades()` を銘柄ごとにループ呼び出しする。
`add_data()` で nautilus のイベント時系列マージは内部で処理されるため、こちらは順序を気にせず銘柄ごとに add すればよい。

> **メモリ注意**: 1 営業日 × 1 銘柄で約 1,500 本の分足。10 銘柄なら 15,000 本。
> 推定メモリ 200-300 MB（開発環境標準）。上限確認は `python -m tracemalloc` で計測。
> 受け入れテスト T-AC-5（後述）でガードする。（A-6 Low Priority Fix）

## 4. 実装タスク（依存順） {#implementation-tasks}

### M1: scenario schema v2（基盤）

- [x] M1.1 [scenario.py](../../python/engine/scenario.py) に schema_version=2 ブランチを追加 ✅
  - `Scenario_v2` TypedDict を新設し、`instruments: list[str]` を必須化
  - **`validate()` を schema_version で分岐**: v1 は `schema_version==1 and "instrument" in d`、v2 は `schema_version==2 and "instruments" in d` で判定
  - **`extract()` は両形式を受けるが変換しない**。ユーザーが v1 → v2 migration は手動で行う
- [x] M1.2 [scenario.py](../../python/engine/scenario.py) `_dict_to_cst_expr` に `list[str]` 値の対応を追加（現在は str/int/bool のみ）✅
- [x] M1.3 単位テスト: v1 / v2 双方で extract → validate → write_back round-trip ✅

### M2: CLI / replay_session API

- [x] M2.1 [replay_session.py:1655](../../python/engine/replay_session.py#L1655) `--instrument` を `nargs='+'` 対応にし、`--instruments` も別名で追加 ✅
- [x] M2.2 `ReplaySession.load()` と **配線全体** を複数銘柄対応に拡張 ✅
  - `instrument_id: str | list[str]` で受け取り
  - `_load_params` に `instrument_ids` キーを追加（後方互換で `instrument_id` も保持）
  - attach / in-process モード両方で対応
  - RunBuffer の `_scenario` を v1/v2 で分岐
  - `StartEngine` / `EngineStartConfig` に `instrument_ids` を渡す
- [x] M2.3 `_resolve_cli_params` を v1/v2 両対応に拡張 ✅
- [x] 単位テスト: T-AC-1〜T-AC-6 で確認 ✅

### M3: IPC schema 拡張（GUI 配線）

- [x] **M3.1-M3.3（IPC schema base）** schemas.py / Rust enum に `instrument_ids: list[str] | None` 追加、backward-compat 確認 ✅
  - **M3.1** [schemas.py](../../python/engine/schemas.py) `LoadReplayData` / `EngineStartConfig` / `ReplayDataLoaded` に `instrument_ids: list[str] | None = None` を追加 ✅
  - **M3.2** `SCHEMA_MINOR` を 12 → 13 に bump。`engine-client/src/lib.rs` も同期更新 ✅
  - **M3.3** Rust 側 `engine-client` の `SCHEMA_MINOR` を 13 に更新 ✅

- [ ] **M3.4a（GUI logic 新設）** ReplayDataLoaded に複数 instrument_ids を含める場合の GUI 側処理
  - API: ReplayDataLoaded イベントから `instrument_ids` を読み出し（別 phase）

- [ ] **M3.4b（GUI layout）** Rust GUI pane layout 変更（縦積み / grid / tab 決定）
  - 別 phase 扱い → open-questions.md に繰越

### M4: nautilus runner の複数銘柄ロード

- [x] **M4.1a** [engine_runner.py:259](../../python/engine/nautilus/engine_runner.py#L259) `start_backtest_replay()` を `instrument_ids: list[str]` 対応に ✅
  - 全銘柄が同一内部 venue か validate（同一 venue 制約）
  - `add_venue()` は 1 回のみ（既存動作を維持）
  - `add_instrument()` をループ
  - `load_*_bars()` をループし、各銘柄分の bars/ticks を `add_data()` で投入
  - `ReplayDataLoaded` に `instrument_ids` を追加

- [x] **M4.1b** ReplayDataLoaded emit の error handling ✅
  - FileNotFoundError は伝播（silent fallback なし）（T-AC-6）

- [x] **M4.2** [engine_runner.py:480](../../python/engine/nautilus/engine_runner.py#L480) `start_backtest_replay_streaming()` も同様に ✅
  - `_fill_topic` / `_bar_topic` を銘柄ごとに分離（`_make_fill_handler` factory）
  - `_bar_topic` / `_on_bar` も銘柄ごと分離
  - unsubscribe のループ化

- [x] M4.3 単一銘柄 API（既存呼び出し）互換性 ✅
  - `instrument_ids=None` のとき `[instrument_id]` にフォールバック

### M5: ドキュメント / サンプル

- [ ] M5.1 [docs/example/README.md](../example/README.md) に複数銘柄サンプルの節を追加（TODO）

- [x] **M5.2a** 新規サンプル `docs/example/pair_trade_minute.py` を追加 ✅
  - schema_version=2: `SCENARIO.instruments = ["1301.TSE", "7203.TSE"]`
  - Strategy.on_bar(bar) で銘柄判定パターンを提示

- [x] **M5.2b** 新規サンプル `docs/example/multiinst_10pairs_minute.py` を追加（10銘柄、T-AC-5 用）✅
  - schema_version=2: `SCENARIO.instruments` 10銘柄
  - Strategy はシンプルに全銘柄を等重購読

- [ ] M5.3 [docs/wiki/backtest.md](../wiki/backtest.md) に複数銘柄起動例と制約（D1・メモリ）を追記（TODO）
- [ ] M5.4 本ファイルを `archive/` 移送（TODO）

### M6: 受け入れテスト ✅ 全件実装済み（`python/tests/test_multi_instrument_acceptance.py`）

| ID | 内容 | 実行コマンド |
|---|---|---|
| T-AC-1 | v1 SCENARIO（既存）が無修正で動くこと（回帰） | `uv run python -m engine.replay_session run --strategy docs/example/test_strategy_minute.py` |
| T-AC-2 | v2 SCENARIO（2 銘柄）で `replay_session run` が完走し、両銘柄の `Bar` が strategy `on_bar` に到達 | `uv run python -m engine.replay_session run --strategy docs/example/pair_trade_minute.py` |
| T-AC-3 | CLI `--instrument 1301.TSE 7203.TSE` で SCENARIO を上書きできる | `uv run python -m engine.replay_session run --strategy docs/example/test_strategy_minute.py --instrument 1301.TSE 7203.TSE` |
| T-AC-4 | 旧 GUI（古い SCHEMA_MINOR）が単一銘柄として fallback して動く | 旧 GUI simulator: SCHEMA_MINOR=N-1 で LoadReplayData `{instrument_id: "1301.TSE", instrument_ids: null}` を送信 → engine が `instrument_ids` を無視し `instrument_id` で単一銘柄処理。確認: GUI chart pane が 1 銘柄のみ表示 |
| T-AC-5 | 10 銘柄 × 1 営業日（minute=約 1,500 本/銘柄）で計 15,000 本。推定メモリ 200-300 MB（開発環境標準）。上限確認は `python -m tracemalloc` で計測 | `uv run python -m engine.replay_session run --strategy docs/example/multiinst_10pairs_minute.py` |
| T-AC-6 | データ未収録の銘柄が混ざった場合、明示的エラーで停止し、他銘柄に黙って fallback しない（C-4 Medium Priority Fix） | 期待動作: `jquants_loader.load_minute_bars("9999.TSE", ...)` が FileNotFoundError を raise → engine_runner が EngineFailure event を emit（status="data_load_failed"）。GUI が error dialog を表示。他銘柄は loaded されず、backtest は全体停止 |

## 5. 非ゴール / 先送り {#non-goals}

- **複数通貨混在**: 全銘柄が同一通貨（JPY 想定）。USD/JPY 混在の cross-currency PnL 計算は別フェーズ
- **動的銘柄追加**: バックテスト走行中の銘柄追加は不可。起動時に確定
- **板（OrderBook）対応**: D1 不変条件により replay では OrderBook を出さない。複数銘柄でも維持
- **live モード**: 本プランは replay 専用。live 側の複数銘柄購読は既に Nautilus 標準で動く（B-1 High Priority補足）

**補足** (B-1 High Priority Fix):
live モード側は既に複数銘柄対応の nautilus on_trade_tick / on_bar を受け取り可能。本計画の runner loop 化が live に backport される予定は、将来の Phase で live 側の複数銘柄対応を検討予定（スコープ外）。

## 6. リスク・未解決項目 {#risks-open-items}

| # | 項目 | 対応 |
|---|---|---|
| R1 | `schema_version` の自動マイグレーション提供 | 提供しない（v1 はそのまま動く方針） |
| R2 | GUI の chart pane が銘柄数に応じて自動レイアウト崩れ | M3.4a の `auto_generate_replay_panes` で 1 行レイアウト → 縦並びへ拡張。M3.4b（別 phase）で詳細 spec を決める |
| R3 | `S:\j-quants\` の銘柄不在ファイル誤判定 | T-AC-6 で必ず明示エラー。`silent-failure-hunter` スキルでレビュー |
| R4 | 戦略 `on_bar` で銘柄ごと state を持たない実装が混入 | サンプル `pair_trade_minute.py` でベストプラクティス提示 |

## 7. 着手順序（推奨） {#recommended-order}

1. **M1（scenario v2）** を先に固める。ここが TypedDict の SoT で、他全部が依存
2. M3（IPC schema）と M4（runner）は並行可。両方とも M1 完了後
3. M2（CLI）は M1 完了後・M4 完了前でも着手可（単独テスト可能）
4. M5（docs）と M6（受け入れテスト）は M2-M4 着地後にまとめて

**見積補足** (C-5 Medium Priority Fix):
本タスク一覧は粗見積（±50%）。実装着手後に各 M に対して 詳細 task breakdown（4-8 hour slice）を実施。特に M4（runner loop 化）は既存 load_*_bars() の並列化戦略によって 4h～1day に変動。

> **review-fix-loop 推奨**: M3-M4 着地時点で `review-fix-loop` スキルを必ず回す。
> IPC schema 変更 + runner 変更 + GUI 配線が同時に動くため、レビュー観点が広い。

## 8. 参考 {#references}

- 単一銘柄前提の経緯: [archive/replay-script-cli-args.md](archive/replay-script-cli-args.md)
- 不変条件: [README.md #d1-不変条件確定-2026-04-28](./README.md#d1-不変条件確定-2026-04-28), [README.md #d8-モード固定ルール](./README.md#d8-モード固定ルール)
- スキーマ運用: `/ipc-schema-check` スキル
- 既存 implementation-plan.md: [implementation-plan.md #phase-n0-n-pre-feasibility確認と前提固め実装ゼロ](./implementation-plan.md#phase-n0-n-pre-feasibility確認と前提固め実装ゼロ)
