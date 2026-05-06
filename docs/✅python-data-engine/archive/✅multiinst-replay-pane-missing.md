# 複数銘柄リプレイ時にチャートペインが1銘柄しか生成されない

## 症状

`SCENARIO["instruments"]` に複数銘柄（例: 10銘柄）を指定したストラテジーをリプレイモードで起動すると、
注文一覧には全銘柄の約定が表示されるにもかかわらず、チャートエリアには先頭の1銘柄のペインしか自動生成されない。

## 関連コード

- IPC スキーマ（Python）: [`python/engine/schemas.py:771-784`](../../python/engine/schemas.py#L771)（`ReplayDataLoaded.instrument_ids`）
- IPC スキーマ（Rust DTO）: [`engine-client/src/dto.rs:1167-1185`](../../engine-client/src/dto.rs#L1167)（`EngineEvent::ReplayDataLoaded.instrument_ids`）
- イベント変換: [`src/main.rs`](../../src/main.rs)（`map_engine_event_to_message`）
- UI メッセージ定義: [`src/main.rs`](../../src/main.rs)（`Message::ReplayDataLoaded`）
- ペイン自動生成ハンドラ: [`src/main.rs`](../../src/main.rs)（`Message::ReplayDataLoaded` arm in `update()`）
- ペイン生成ロジック: [`src/screen/dashboard.rs`](../../src/screen/dashboard.rs)（`auto_generate_replay_panes`）

## 調査・再現手順

1. **再現条件**: `SCENARIO["instruments"]` に複数銘柄（例: `["1301.TSE", "7203.TSE"]`）を指定したストラテジーをリプレイモードで起動する。
2. **確認方法**: ログで `ReplayDataLoaded: auto-generating replay panes` の件数が1件のみであることを確認する（正常なら銘柄数分のログが出るはず）。
3. **調査起点**: `EngineEvent::ReplayDataLoaded` の DTO と `Message::ReplayDataLoaded` の対応フィールドを grep で比較し、`instrument_ids` が `Message` 側に存在しないことを確認する。
4. **MISSES.md 参照**: 同種の「DTO フィールド `..` 暗黙破棄」パターンが過去に記録されているか確認する（`docs/✅python-data-engine/` 配下の既存 MISSES.md）。

## 根本原因

schema 3.13 で Python エンジンは `ReplayDataLoaded` に `instrument_ids: list[str]`（全銘柄リスト）を追加した。
Rust の `EngineEvent` DTO も対応済みだったが、**UI メッセージへの変換経路が `instrument_ids` を `..` で黙って破棄していた**。

```rust
// 修正前（src/main.rs — map_engine_event_to_message）
EngineEvent::ReplayDataLoaded {
    instrument_id,
    granularity,
    bars_loaded,
    trades_loaded,
    ..   // ← schema 3.13 で EngineEvent に追加されたが Message 側への転送コードが書かれず、`..` に飲み込まれた
} => Some(Message::ReplayDataLoaded {
    instrument_id,   // 先頭1銘柄しか渡らない
    granularity,
    bars_loaded,
    trades_loaded,
}),
```

`Message::ReplayDataLoaded` 自体にも `instrument_ids` フィールドが無く、
ハンドラは単一 `instrument_id` でのみ `auto_generate_replay_panes` を呼んでいたため、
先頭銘柄のペインしか生成されなかった。

## 修正内容

### 変更箇所（すべて [`src/main.rs`](../../src/main.rs)）

| 変更 | 内容 |
|---|---|
| `Message::ReplayDataLoaded` | `instrument_ids: Option<Vec<String>>` フィールドを追加 |
| `map_engine_event_to_message` | `instrument_ids` を `..` で捨てず `Message` に転送 |
| `update()` ハンドラ | `instrument_ids`（schema 3.13+）を優先、なければ `instrument_id` 単体へ後方互換フォールバック。全銘柄を for ループで `auto_generate_replay_panes` → `Task::batch` で一括返却 |

### 後方互換

`instrument_ids` が `None`（schema_minor < 13 の旧エンジン）の場合は `instrument_id` 単体リストにフォールバックするため、既存の単銘柄リプレイ動作は変わらない。

`LoadReplayData` コマンド送信側（`src/main.rs`）は既存実装で `instrument_ids` を Python に正しく送信済みのため、今回の修正は受信側のイベント変換のみ対象。

ペイン生成タスクはすべて `layout_id: None`（アクティブダッシュボード）に固定。

```rust
let ids: Vec<String> = instrument_ids
    .filter(|v| !v.is_empty())
    .unwrap_or_else(|| instrument_id.into_iter().collect());
```

## テストケース追加（完了 2026-05-06）

`tests/multiinst_replay_pane_routing.rs` を新規作成。bin-only クレートのため source-scan パターンで実装。7テスト全 PASS。

| ケース | テスト名 | 状態 |
|---|---|---|
| ディスパッチャが `instrument_ids` を転送する | `dispatcher_forwards_instrument_ids_to_message` | ✅ PASS |
| `Message::ReplayDataLoaded` に `instrument_ids` フィールドがある | `message_replay_data_loaded_has_instrument_ids_field` | ✅ PASS |
| ハンドラが `Task::batch` で複数ペインを返す | `handler_uses_task_batch_for_multi_instrument` | ✅ PASS |
| ハンドラが `for id in &ids` でループする | `handler_iterates_over_instrument_ids` | ✅ PASS |
| `instrument_ids=None` → `instrument_id` フォールバック（後方互換） | `handler_fallbacks_to_single_instrument_id_when_instrument_ids_is_none` | ✅ PASS |
| `ids.is_empty()` 時に `Task::none()` を返す | `handler_returns_task_none_when_ids_is_empty` | ✅ PASS |
| `instrument_ids=[]` が `filter` で除外されフォールバックへ | `handler_treats_empty_instrument_ids_vec_as_absent` | ✅ PASS |

## 作業手順

1. [x] `Message::ReplayDataLoaded` に `instrument_ids` フィールドを追加
2. [x] `map_engine_event_to_message` で `instrument_ids` を転送
3. [x] ハンドラを複数銘柄ループ + `Task::batch` に書き換え
4. [x] `cargo check` 通過確認
5. [ ] 実環境で `examples/multiinst_10pairs_minute.py` を起動し、10銘柄分のペインが自動生成されることを確認 ← **ユーザー確認待ち**
6. [x] テストケース追加（`tests/multiinst_replay_pane_routing.rs` に7テスト追加、全 PASS）
7. [x] bug-postmortem 実施（`MISSES.md` に新パターン「EngineEvent フィールド追加時の `..` 黙示破棄」を追記）

## 2026-05-06 追記（作業者: Claude）

### bug-postmortem の知見

**見逃しパターン**: EngineEvent フィールド追加時の `..` 黙示破棄

既存テスト `engine_event_replay_data_loaded_routing.rs` は「アームの存在」しか検証しておらず、「どのフィールドが転送されるか」を pin していなかった。`MISSES.md` に新パターンとして追記済み。

### Tips（次の作業者へ）

- `Message::ReplayDataLoaded` と `EngineEvent::ReplayDataLoaded` のフィールドを増やす場合は両方同時に更新し、`map_engine_event_to_message` の arm の `..` を必ず確認すること。
- source-scan テストの `extract_function_body` ヘルパーは複数テストファイルで重複しているが、bin-only クレートでは共通ライブラリに切り出せない（integration test から lib をインポートできない）。重複は現状許容。
- `tt6_switch_mode_handler_body_contains_dirty_check_flow`（`tests/mode_toggle_footer.rs`）が現在 FAIL している。このバグ修正とは無関係な既存の失敗テスト。

## 既知の制約／非目標

- `ReplayDataLoaded` イベントは `layout_id` を運ばない。`layout_id: None`（アクティブダッシュボード）に固定されているため、リクエスト送信後にアクティブレイアウトが切り替わると、意図しないレイアウトにペインが生成されうる。これは現行の仕様抜けであり、現フェーズでは非目標とする。`layout_id` を IPC で運ぶ設計変更が必要な場合は別 Issue で対処すること。
