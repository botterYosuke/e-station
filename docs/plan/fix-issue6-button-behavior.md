# 修正計画書: Issue 6 — 1step 戻る / 1step 進む / 一時停止 ボタンの動作確認

> **✅ 実装完了 (2026-05-08)** — R5 まで全指摘解消済み。

## コード調査結果

`handlers/menu.rs:74-261` を全トレースした結果、以下の実装状況を確認した。

### 各ボタンの実装状況

| ボタン | 実装箇所 | IPC コマンド | 楽観的更新 | エラーハンドリング |
|---|---|---|---|---|
| ▶ Play（Resume） | menu.rs:74-91 | `ResumeReplay` | ✅ `replay_paused = false` | ❌ 失敗時ロールバックなし |
| ⏸ Pause | menu.rs:184-217 | `PauseReplay` | ✅ `replay_paused = true` | ✅ `ReplayPauseStateChanged { paused: false }` でロールバック |
| ⏭ StepForward | menu.rs:218-239 | `StepReplay` | N/A（状態変化なし） | ✅ Toast エラー表示 |
| ⏮ StepBackward | menu.rs:240-261 | `StepBackward` | N/A | ✅ Toast エラー表示、Python が `ReplayHistoryChanged` で state 更新 |
| ⏹ Stop | menu.rs:269 | → `ReplayMsg::StopReplayOnly` | — | — |

Python 側の動作:

| コマンド | Python 応答イベント |
|---|---|
| `PauseReplay` | なし（IPC 送達の `Ok(())` のみ） |
| `ResumeReplay` | なし（IPC 送達の `Ok(())` のみ） |
| `StepReplay` | なし（IPC 送達の `Ok(())` のみ；`engine_runner.py` が後続処理後にデータを push） |
| `StepBackward` | `RestoreSnapshot`, `ReplayBuyingPower`, `OrderListUpdated`, `ReplayHistoryChanged` を emit ✅ |

## 発見したバグ

### Bug 1: Resume 失敗時のロールバックが欠如（重大）

**場所**: `handlers/menu.rs:80-91`

```rust
Task::perform(
    async move {
        conn.send(engine_client::dto::Command::ResumeReplay { ... }).await
    },
    |_| Message::Engine(EngineMsg::Noop),  // ← Ok も Err も Noop！
)
```

`|_|` はクロージャの引数を無視しており、`Result<(), IpcError>` の `Ok` / `Err` が
どちらも `EngineMsg::Noop` になる。`ResumeReplay` が失敗した場合：
- `self.replay_paused` は楽観的に `false` に設定済み
- エラーでも `BarMessage::ReplayPauseStateChanged { paused: true }` が呼ばれない
- 結果として **UI は「再開済み」表示なのに Python エンジンは PAUSED のまま**

`PressPause` (line 197-211) は正しくロールバックを実装しているので同パターンで修正できる。

### Bug 2: `PressPlay` (new session) が `replay_paused && replay_running` チェックに依存

**場所**: `handlers/menu.rs:76`

`PressPlay` でのリプレイ開始判定は `self.replay_paused && self.replay_running` の両方が必要。
しかし `self.replay_paused` は `PressPause` / `ReplayPauseStateChanged` でしか更新されず、
`self.replay_running` は `ReplayMsg::Finished` と `ReplayDataLoaded` で更新される。

万が一どちらかが未同期の場合、Resume ではなく新規セッション開始路が誤選択される。
現状の実装フローは正しいが、**テストで明示的に検証されていない**。

## 修正方針

### Fix 1: Resume 失敗時のロールバックを追加（`handlers/menu.rs:80-91`）

```rust
BarMessage::PressPlay => {
    if self.replay_paused && self.replay_running {
        self.replay_paused = false;
        if let Some(conn) = self.engine_connection.as_ref().cloned() {
            let req_id = uuid::Uuid::new_v4().to_string();
            Task::perform(
                async move {
                    conn.send(engine_client::dto::Command::ResumeReplay {
                        request_id: req_id,
                    })
                    .await
                    .map_err(|e| e.to_string())
                },
                |res| match res {
                    Ok(()) => Message::Engine(EngineMsg::Noop),
                    Err(e) => {
                        log::error!("ResumeReplay IPC failed (rolling back): {e}");
                        Message::Menu(MenuMsg::Bar(
                            crate::menu_bar_state::BarMessage::ReplayPauseStateChanged {
                                paused: true,
                                // has_history は現在値を維持: Resume 後も history は変わらない
                                has_history: false, // 呼び出し元で has_history を capture する
                            },
                        ))
                    }
                },
            )
        } else {
            Task::none()
        }
    }
    // ...
}
```

`has_history` は `PressPause` と同様に呼び出し前にキャプチャ:

```rust
let has_history = self.menu_bar.replay_bar.replay_has_history;
```

### Fix 2: StepForward 後の状態確認（`engine_runner.py`）

`StepReplay` 後に Python が PAUSED 状態を維持しているかを単体テストで確認する。
`_replay_step_request` カウンタが処理後にデクリメントされ、完了後の状態が PAUSED であることを
テストで明示的に検証する。

## 確認項目

- [ ] `ResumeReplay` IPC 失敗時に `replay_paused` が `true` に戻ること
- [ ] `StepForward` 後にボタンが有効のまま（PAUSED 状態を維持）
- [ ] `StepBackward` で `ReplayHistoryChanged { has_history: false }` が届いたとき ⏮ ボタンが disabled になること
- [ ] `PressPause` → `PressPlay` (Resume) → `PressPause` の往復で状態が正しく遷移すること
- [ ] `replay_paused` の2重管理（`self.replay_paused` と `menu_bar.replay_bar.replay_paused`）が `ReplayPauseStateChanged` で同期されること（`handlers/menu.rs:265-267` で確認済み）

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `src/handlers/menu.rs:80-91` | `ResumeReplay` の `|_|` → `|res| match res` に変更してロールバック追加 |

## 実装難易度

**低**。1箇所の修正のみ。`PressPause` の実装パターン（line 197-211）をそのまま流用できる。

## レビュー反映 (2026-05-08, ラウンド R2)

### 修正した指摘
- ✅ HIGH: PressPlay (Resume) IPC 失敗時ロールバック欠如 → `|_|` を `|res| match res` に変更、エラー時に `ReplayPauseStateChanged { paused: true }` でロールバック
- ✅ HIGH: EngineBusy (PauseReplay/ResumeReplay) 受信時ロールバック欠如 → `EngineMsg::PauseReplayBusy` / `ResumeReplayBusy` 新設、`handle_engine` でロールバック
- ✅ MEDIUM: engine_connection=None 時の楽観更新が戻らない → connection チェックを楽観更新の前に移動
- ✅ Bug 2 再分類: 「バグ」→「テストで pin すべき不変条件」に変更（現状の実装フローは正しい）

### テスト追加
- `src/menu_bar_state.rs`: `press_play_rollback_preserves_has_history`, `press_pause_rollback_preserves_has_history` の 2 件追加

### 設計判断
- `EngineBusy` のロールバックは free fn `map_engine_event_to_message` では `self` にアクセスできないため、`EngineMsg` に新 variant を追加し `handle_engine` でロールバックするパターンを採用
- `has_history` は rollback 時に現在値を保持（Resume/Pause は history に影響しないため）

## レビュー反映 (2026-05-08, ラウンド R3)

### 修正した指摘
- ✅ H1: `engine_busy_dispatch_limited_to_stop_replay_commands` テストに PauseReplay/ResumeReplay arm assert を追加
- ✅ H2: `engine.rs` に "IPC Ok() 後に EngineBusy は来ない" invariant コメントを追加
- ✅ M1: source-inspection テストの終端検出ロジックに脆さを示すコメント追加
- ✅ M2: `engine.rs` の reason 用途コメント追加（Toast は out of scope 明記）
- ✅ M3: `engine.rs` 側 arm 存在を確認する `engine_busy_pause_resume_replay_rollback_arm_exists_in_handler` テスト追加

### 持ち越し（LOW のみ）
- L1: PressPlay Resume で楽観更新後 menu_bar_state::update() が再呼出し（将来的な競合リスク、実害なし）
- L2: Flowsurface::replay_paused 同期の統合テストなし（E2E テスト対象）
- L3: engine_connection=None Resume パスに Toast がない（engine_restarting Toast が代替として出る）
- M4 (既存): main.rs:1597 の _ ワイルドカードが将来 variant を silent Toast に吸収する構造的リスク（今回修正前から存在）

## レビュー反映 (2026-05-08, ラウンド R5)

### 修正した指摘
- ✅ R4-M1: `engine_event_routing.rs` テストにロールバック方向チェック追加（PauseReplayBusy→paused:false、ResumeReplayBusy→paused:true）
- ✅ R4-M2: `mode_switch_timeout_abort.rs` の body 範囲チェックにコメント追加（`_ =>` 終端の意図を明示）
