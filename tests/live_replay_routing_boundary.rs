//! H-2: Live/replay 境界テスト 3 シナリオ。
//!
//! `src/handlers/replay.rs` のソースをスキャンして、replay/live 境界処理の
//! 実装が存在することを静的に確認する。
//!
//! 1. `test_replay_finished_arm_exists_in_handlers` — ReplayMsg::Finished arm と
//!    replay 終了後の処理コード（live routing 切り替えに相当する `replay_running = false`
//!    または `GetOrderList` の発行）が含まれていることを確認。
//!
//! 2. `test_session_epoch_state_transition_is_pinned` — `session_epoch` または
//!    `last_replay_session_epoch` の参照が存在し、replay/live 境界での
//!    epoch 管理が実装されていることを確認。
//!
//! 3. `test_pending_replay_state_handling` — `pending` または `RestoreSnapshotPending`
//!    の arm が存在し、pending replay 中の live reconnect に対応する分岐があることを確認。

const HANDLER_REPLAY: &str = include_str!("../src/handlers/replay.rs");

// ── シナリオ 1: ReplayMsg::Finished arm と replay 終了後処理 ─────────────────

#[test]
fn test_replay_finished_arm_exists_in_handlers() {
    assert!(
        HANDLER_REPLAY.contains("ReplayMsg::Finished"),
        "src/handlers/replay.rs に `ReplayMsg::Finished` の arm が見つからない。\
         replay 終了後に live routing へ切り替えるための Finished ハンドラが必要。"
    );

    // Finished ハンドラは replay_running を false にセットする必要がある（実コード確認済み）。
    assert!(
        HANDLER_REPLAY.contains("self.replay_running = false"),
        "ReplayMsg::Finished ハンドラが `self.replay_running = false` を含まない。\
         replay 完了後に live routing に切り替わる保証が失われている。"
    );
}

// ── シナリオ 2: session_epoch による replay/live 境界管理 ─────────────────────

#[test]
fn test_session_epoch_state_transition_is_pinned() {
    // epoch は DataLoaded ハンドラで `self.last_replay_session_epoch = session_epoch` として
    // 更新される（実コード確認済み）。
    assert!(
        HANDLER_REPLAY.contains("self.last_replay_session_epoch = session_epoch"),
        "src/handlers/replay.rs に `self.last_replay_session_epoch = session_epoch` の\
         代入が見つからない。replay セッション epoch の追跡が失われている。"
    );
}

// ── シナリオ 3: pending replay 中の live reconnect 対応分岐 ──────────────────

#[test]
fn test_pending_replay_state_handling() {
    // pending replay 中の live reconnect は `replay_stop_only_pending` フラグと
    // `pending_scenario_request_id` フィールドで管理される（実コード確認済み）。
    assert!(
        HANDLER_REPLAY.contains("self.replay_stop_only_pending"),
        "src/handlers/replay.rs に `self.replay_stop_only_pending` が見つからない。\
         pending replay 中の live reconnect ガードが失われている。"
    );
    assert!(
        HANDLER_REPLAY.contains("pending_scenario_request_id"),
        "src/handlers/replay.rs に `pending_scenario_request_id` が見つからない。\
         pending scenario request の追跡が失われている。"
    );
}
