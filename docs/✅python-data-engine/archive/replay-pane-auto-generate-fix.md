# Replay 中にチャート/注文一覧が更新されない不具合 — 修正計画

- **作成日**: 2026-05-05
- **状態**: ✅ 実装完了 (2026-05-05)
- **優先度**: HIGH（replay モードのコア機能が不通）

## 実装サマリ (2026-05-05)

Step 1〜4 を直列実装で完了。タスクグラフが schema 変更 → emit 側 → 受信側 → テストの線形依存だったため、
parallel-agent-dev (worktree 並列) ではなく直接実装 — マージコンフリクトを避けるため。

✅ **完了した作業**:
- `engine-client/src/dto.rs`: `ReplayDataLoaded` に optional `instrument_id` / `granularity` 追加
  (`#[serde(default)]` で旧 engine 互換)
- `engine-client/src/lib.rs`: SCHEMA_MINOR 11 → 12 + 履歴コメント追記
- `python/engine/schemas.py`: SCHEMA_MINOR 12 + `ReplayDataLoaded` モデルに 2 フィールド追加
  (`Literal["Trade", "Minute", "Daily"] | None`)
- `python/engine/server.py`: `_handle_load_replay_data` の emit に `instrument_id` / `granularity` 同梱
- `python/engine/nautilus/engine_runner.py`: `start_backtest_replay` / `start_backtest_replay_streaming`
  の 2 emit 箇所に同梱
- `src/main.rs`:
  - `map_engine_event_to_tachibana` → `map_engine_event_to_message` リネーム + `pub(crate)` 化
  - `EngineEvent::ReplayDataLoaded` arm 追加 → `Message::ReplayDataLoaded` 変換
  - `Message::ReplayDataLoaded` variant 追加
  - `update()` で `instrument_id` 検証 → `ReplayGranularity` を `exchange::Timeframe` に変換 →
    `active_dashboard_mut().auto_generate_replay_panes()` 呼出
  - `_ => None` のコメント更新（再発防止フットプリント明記）
- `tests/engine_event_replay_data_loaded_routing.rs`: 新規 source-inspection リグレッションガード
  (2 テスト: リネーム検出 + ReplayDataLoaded arm 構造検証)。`flowsurface` が bin-only crate の
  ため、`syn::parse_file` + 文字列スキャンで dispatcher の存在/形状を検証する。
  `quote`/`proc-macro2` はワークスペース未導入のため不要なアプローチを選択。
- `engine-client/tests/schema_v2_4_nautilus.rs`: SCHEMA_MINOR 期待値を 12 に更新 +
  既存 `replay_data_loaded_round_trip` テストの destructure に `instrument_id` / `granularity`
  追加 (旧 fixture では `None` を assert)
- `python/tests/test_engine_runner_replay.py`: `test_replay_data_loaded_trades_count_matches_fixture`
  に `instrument_id == "1301.TSE"` / `granularity == "Trade"` の assert を追加
- `python/tests/test_schemas_nautilus.py`: SCHEMA_MINOR 期待値を 12 に更新

## 検証結果

```
cargo test -p flowsurface-engine-client       → 全 PASS
cargo test -p flowsurface                     → 全 39 binary PASS（pre-existing wandb 失敗は無関係）
cargo test --test engine_event_replay_data_loaded_routing → 2 PASS
uv run pytest python/tests/                   → 1864 passed, 4 skipped (132s)
```

> **Pre-existing failures** (本 PR と無関係): `tests/wandb_signin_flow.rs` の 2 件 (`login_failed_message_routes_through_update` / `main_rs_routes_login_failure_via_message`) は本ブランチに残っている in-progress wandb 作業由来で、本 fix の変更を入れる前から失敗している（git stash で main.rs をベースラインに戻しても同じく失敗を確認済み）。

## 設計判断ノート (作業中の知見)

### なぜ `pending_replay_instrument` フィールドを GUI に持たせなかったか

計画 §3 Step 3 のとおり、engine と GUI を同時配布する単一リポジトリでは
`instrument_id` は常に engine から到着する。フォーム経由 / attach mode で同じ
`Message::ReplayDataLoaded` 経路を通すことで、状態の二重持ちを避け対称性を保った。

### dispatcher テストの実装選択

当初 `syn::Block` の Debug 文字列を読む案を検討したが、`syn` の `extra-traits`
feature がワークスペースに無く `Block: Debug` が使えない。`quote`/`proc-macro2` も
未導入のため、`include_str!` した main.rs ソースに対して関数シグネチャの位置から
ブレース対応で関数本体を切り出し、`String::contains` で arm の構造を assert する
シンプルなアプローチに着地した。リファクタへの brittleness は意図的（リグレッション
ガードの目的に合致）。

### Other workers への申し送り

- 今後 `EngineEvent` に新バリアントを追加するときは、`map_engine_event_to_message`
  に arm を追加するか、`_ => None` で意図的に握り潰す根拠をコメントで明示すること。
  `engine_event_replay_data_loaded_routing` テストは現時点では `ReplayDataLoaded`
  だけをカバーしているが、新バリアント追加時には同様の guard を追加することを
  検討する（特に GUI 上の挙動を変えるイベントに対して）。
- helper attach mode を使う E2E は今後追加。本 fix では計画通り unit/integration
  テストでカバーし、手動 GUI 検証手順を §5 に残してある。

## 1. 不具合サマリ

`cargo run -- --mode replay` で GUI を起動し、外部 helper（`uv run python -m engine.replay_session run ...`）から
attach mode で `LoadReplayData` + `StartEngine` を投入すると、

- engine からは `ReplayDataLoaded` → `KlineUpdate` × N → `EngineStopped` が WS 経由で正しく broadcast される
- helper 側 stdout には全イベントが届く
- **GUI 側はチャートも注文一覧も生成されず、"Choose a view to get started" のまま**
- 注文一覧トースト「注文一覧を更新しました」だけ点灯する（`OrderListUpdated` ハンドラは生きているため）

## 2. 根本原因

[src/main.rs:1704](../../src/main.rs#L1704) の
`map_engine_event_to_tachibana`（イベントストリームの唯一のディスパッチ関数。
[main.rs:1670-1677](../../src/main.rs#L1670-L1677) でこの 1 関数だけを通す）に
**`EngineEvent::ReplayDataLoaded` の match arm が存在しない**。
末尾 `_ => None`（line 1908）で握り潰されており、設計上の起点である
[`Dashboard::auto_generate_replay_panes`](../../src/screen/dashboard.rs#L1019) が一度も呼ばれない。

[CLAUDE.md:211](../../.claude/CLAUDE.md#L211)・
[docs/example/README.md](../example/README.md) には
「`ReplayDataLoaded` 受信後に `auto_generate_replay_panes` が
TimeAndSales / CandlestickChart / OrderList / BuyingPower を自動生成する」と
明記されているが、実装が追従していない。

### 副次的問題: 関数名と責務の乖離

`map_engine_event_to_tachibana` という名前に反し、実体は
`OrderListUpdated` / `BuyingPowerUpdated` / `ExecutionMarker` /
`ReplayBuyingPower` / `ClientConnected` 等、Tachibana 以外のイベントも処理している。
コメント（line 1700-1703, 1904-1907）も「Tachibana 関連だけ」と書いており、
今回の握り潰しを誘発した構造的要因になっている。
責務を実態に合わせるため **関数名を `map_engine_event_to_message` にリネーム**する。

### 経路上の更なる欠落

[engine-client/src/dto.rs:1160](../../engine-client/src/dto.rs#L1160) の
`ReplayDataLoaded` 構造体は `bars_loaded` / `trades_loaded` / `strategy_id?` のみで
**`instrument_id` と `granularity` を運ばない**。GUI 内フォーム経由なら
`replay_form_modal` から自前で覚えておけるが、外部 helper attach mode では
GUI は `instrument_id` を知る術が無い。`auto_generate_replay_panes` は
`instrument_id: &str` と `Option<Timeframe>` を要求するため、これらを補わないと
attach mode で機能を復活させられない。

## 3. 修正方針

### Step 1: IPC スキーマ拡張（schema 3.11 → 3.12 minor bump）

`EngineEvent::ReplayDataLoaded` に optional フィールドを追加し、
古い送信側でも壊れないよう `#[serde(default)]` を付ける。

**Rust** ([engine-client/src/dto.rs:1160](../../engine-client/src/dto.rs#L1160)):

```rust
ReplayDataLoaded {
    #[serde(default)]
    strategy_id: Option<String>,
    bars_loaded: u64,
    trades_loaded: u64,
    #[serde(default)]
    instrument_id: Option<String>,        // 追加
    #[serde(default)]
    granularity: Option<GranularityDto>,  // 追加（Daily / M1 / Trade）
    ts_event_ms: i64,
},
```

**Python** ([python/engine/schemas.py](../../python/engine/schemas.py)):

- `SCHEMA_MINOR` を 11 → 12 へ bump
- `replay_data_loaded(...)` ヘルパに `instrument_id` / `granularity` を追加
- `NautilusRunner` ([python/engine/nautilus/engine_runner.py]) が
  `Command::LoadReplayData.instrument_id` / `granularity` を保持して
  emit 時に同梱する

**Rust** ([engine-client/src/lib.rs:36](../../engine-client/src/lib.rs#L36)):

- `SCHEMA_MINOR = 12` へ bump し履歴コメントに追記:
  `12: ReplayDataLoaded.instrument_id / granularity を追加（auto_generate_replay_panes 復活用）`

> 互換性: `SCHEMA_MAJOR` は変えないため古い engine と新 GUI の組み合わせでも
> handshake は成功する。本リポジトリは engine と GUI を同時配布するため、
> `instrument_id` 不在ケースは通常運用で発生しない（不在時は防御的に `log::error!` で return）。

### Step 2: 関数リネーム + ReplayDataLoaded ハンドラ追加

[src/main.rs:1704](../../src/main.rs#L1704) の `map_engine_event_to_tachibana` を
`map_engine_event_to_message` へリネームし（呼び出し元 [main.rs:1674](../../src/main.rs#L1674) も追従）、
match arm を追加して新 `Message` バリアントへ橋渡しする。

```rust
EngineEvent::ReplayDataLoaded {
    instrument_id,
    granularity,
    bars_loaded,
    trades_loaded,
    ..
} => Some(Message::ReplayDataLoaded {
    instrument_id,
    granularity,
    bars_loaded,
    trades_loaded,
}),
```

`Message::ReplayDataLoaded` を `src/screen.rs` か `src/main.rs` 上部の
`Message` 列挙体に追加する。

### Step 3: Flowsurface::update() で auto_generate_replay_panes を呼ぶ

`Message::ReplayDataLoaded` を受けたら：

1. `instrument_id` が `None` または空なら `log::error!` を出して return（防御的）
2. `granularity` を `Option<Timeframe>` へ変換
3. `dashboard.auto_generate_replay_panes(main_window_id, &instrument_id, timeframe)` を呼ぶ
4. 戻り値の `Task<Message>` をそのまま return する

> GUI 側に独自の `pending_replay_instrument` フィールドは持たない。
> engine と GUI が同時配布される単一リポジトリ構成では `instrument_id` は常に
> engine から到着する。フォーム経由 / attach mode で同じ経路を使うほうが対称的。

### Step 4: テスト追加（リグレッションガード）

#### 4.1 Rust: イベントマッパ網羅テスト

[tests/](../../tests/) に
`test_engine_event_replay_data_loaded_routing.rs` を新規追加。
`map_engine_event_to_message`（リネーム後）を `pub(crate)` で公開し直接呼ぶ：

- `EngineEvent::ReplayDataLoaded { instrument_id: Some("1301.TSE"), granularity: Some(_), .. }` を渡し、
  戻り値が `Some(Message::ReplayDataLoaded { .. })` で
  `instrument_id` / `granularity` が保持されていることを assert
- `instrument_id: None` の旧形式でも握り潰さず `Message::ReplayDataLoaded` を返すこと（`update()` 側で防御的に弾く前提）

> Step 5.2（source 文字列で arm 存在確認）は本テストで構造的にカバーできるため統合する。

#### 4.2 Python: schema integration

[python/tests/test_engine_runner_replay.py](../../python/tests/test_engine_runner_replay.py) を拡張し、
`ReplayDataLoaded` イベントに `instrument_id` / `granularity` が含まれることを assert する。

#### 4.3 手動 GUI 検証

`python -m engine.replay_session run` を `--mode attach` で叩いた直後の GUI で：

- `auto_generate_replay_panes` を呼んだ INFO ログが出る
- TimeAndSales / CandlestickChart / OrderList / BuyingPower の 4 ペインが pane_grid に並ぶ
- チャートに bar が積まれ、ExecutionMarker dot が表示される

> `bash tests/e2e/smoke.sh` は live ハンドシェイク観測用で replay attach はカバーしない。
> attach replay 用 smoke の追加は別件として切り出す。

## 4. 影響範囲

| ファイル | 変更内容 | リスク |
|---------|---------|-------|
| `engine-client/src/dto.rs` | `ReplayDataLoaded` フィールド 2 個追加（optional） | 低（後方互換） |
| `engine-client/src/lib.rs` | SCHEMA_MINOR 11 → 12 + コメント | 低 |
| `python/engine/schemas.py` | 同上 + helper 関数引数追加 | 低 |
| `python/engine/nautilus/engine_runner.py` | `instrument_id` / `granularity` を emit | 中（runner state 追加） |
| `src/main.rs` | `map_engine_event_to_tachibana` → `map_engine_event_to_message` リネーム + `Message::ReplayDataLoaded` arm 追加 + `update()` 側 arm 追加 | 中 |
| `src/screen/dashboard.rs` | 変更なし（既存 `auto_generate_replay_panes` を流用） | — |
| テスト 2 件 | 新規追加（Rust マッパ網羅 + Python schema） | — |

## 5. 検証手順

```bash
# Rust
cargo fmt
cargo clippy -- -D warnings
cargo test --workspace

# Python
uv run pytest python/tests/ -v

# 手動 GUI 検証
rm -f "$APPDATA/flowsurface/engine-session.json"
cargo run -- --mode replay &
# engine-session.json が生成されるまで待機
uv run python -m engine.replay_session run \
    --strategy docs/example/buy_and_hold.py \
    --instrument 1301.TSE \
    --start 2025-01-06 --end 2025-03-31 \
    --mode attach
# GUI に TimeAndSales / CandlestickChart / OrderList / BuyingPower が出ること
# チャートに bar が積まれ、ExecutionMarker dot が表示されること
```

## 6. bug-postmortem 連携

修正完了後 `/bug-postmortem` を起動し、以下を `MISSES.md` に追記する：

- **見逃しクラス**: 「IPC イベントの新バリアント追加時に Rust 側 match arm の
  網羅検査が無い」「ディスパッチ関数の名前が責務を反映しておらず、
  Tachibana 以外のイベントを足すべき場所だと気付きにくかった」
- **再発防止**: `dto.rs` の `EngineEvent` に新バリアントを足したら
  必ず `map_engine_event_to_message` のテストで網羅されることを確認する
  contract test を追加すること（Step 4.1）

## 7. リリース方針

Step 1〜4 を **単一 PR にまとめて merge** する。engine と GUI を同時配布する
単一リポジトリ構成では段階 merge の互換性メリットは無く、rebase コストとレビュー断片化のデメリットのほうが大きい。

PR の green 条件：

- `cargo fmt` / `cargo clippy -- -D warnings`
- `cargo test --workspace`
- `uv run pytest python/tests/ -v`
- 第 5 章の手動 GUI 検証手順
