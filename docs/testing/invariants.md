---
title: e-station 不変条件カタログ (集約)
status: draft
---

# e-station 不変条件カタログ

> 本ドキュメントは Wave 4 で各 module の `_invariants-fragment.md` を統合した集約版。venue/モジュール横断の不変条件をひとつの索引から辿れるようにする。各章の本文は移送前 fragment の原文を保持している。

## data-engine

<!--
このファイルは Wave 4 で `docs/testing/invariants.md` に統合されることを前提とした
中間フラグメントです。現状、旧 `docs/specs/data-engine/` 配下には独立した
`invariant-tests.md` ファイルは存在せず、不変条件は spec.md / current-architecture.md
本文に散在する形で記述されているため、抽出候補となる項目を以下に列挙するに留めます。
正式な ID 付与（INV-DATA-ENGINE-NNN）と本文化は Wave 4 の testing/invariants.md
統合作業で行う予定です。
-->

# data-engine 不変条件 (抽出候補)

[`specs/data-engine.md`](../../specs/data-engine.md) 本文中で「必ず」「禁止」「不変」相当として記述されている事項を、Wave 4 の testing/invariants.md 統合に向けて列挙する。

## INV 候補

- **IPC アクセス制御**: WebSocket サーバは loopback (`127.0.0.1` / `::1`) のみ listen する。token 不一致接続は即切断。([data-engine.md §4.1.1](../../specs/data-engine.md#411-ローカル-ipc-のアクセス制御))
- **handshake 順序**: Rust は `Ready` 受領前にマーケットデータ系コマンドを送らない。([data-engine.md §4.5](../../specs/data-engine.md#45-起動ハンドシェイク))
- **schema_major 不一致は致命的**: 不一致時はハンドシェイク失敗で接続拒否。`schema_minor` 差は警告のみで接続継続。([data-engine.md §4.5.1](../../specs/data-engine.md#451-スキーマバージョニング運用))
- **depth diff は drop 不可**: 受信キューが詰まった場合でも depth 中間 diff は drop せず、coalesce か session 切り直しで対応。([data-engine.md §4.4](../../specs/data-engine.md#44-バックプレッシャと整合性保証))
- **stream_session_id 不一致時の板破棄**: `stream_session_id` 不一致または `prev_sequence_id != applied_seq` を検知したら板を破棄して `RequestDepthSnapshot` を送出。
- **engine_session_id 切替時の全破棄**: `engine_session_id` が変わったら Rust は全ての板・未確定 kline・進行中 fetch を破棄する。
- **trade dedup**: trade 重複配信は許容するが、`(venue, ticker, trade_id)` で Rust 側が dedup する。([data-engine.md §9.4](../../specs/data-engine.md#94-整合性))
- **depth gap 検知漏れ = 0**: 長時間稼働でも gap 検知漏れが発生しないこと。
- **MAX_CONNECTIONS=4** (Phase 8 attach mode): 同時接続上限を超えた場合は 1008 Policy Violation で reject。
- **mode 一致**: 外部エンジン attach 時、`mode` ("live" | "replay") が一致しないクライアントは拒否。([replay.md §1](../../specs/replay.md))
- **replay モードでの venue login 抑止**: `mode == "replay"` のとき Python は Tachibana startup login を skip し、`RequestVenueLogin` も `mode_mismatch` で拒否する。([replay.md §3](../../specs/replay.md))


## tachibana

# Tachibana 不変条件 fragment

> **統合先**: 本ファイルは将来 `docs/testing/invariants.md` に統合される fragment。`INV-TACHIBANA-NNN` ID で venue 横断の集約を可能にする。原文の ID（`F-*` / `T35-*` / `SKILL R*` / `HIGH-*` / `MEDIUM-*` / `F-SC-*`）は最右列「原 ID」で保持する。

**目的**: 立花証券アダプター実装の各不変条件 ID（spec.md / SKILL.md / data-mapping.md / open-questions.md 由来）と、それを CI で pin するテスト関数名を 1:1（または 1:n）で対応付ける**単一正本ファイル**。本表が存在しない不変条件 ID は「未対応」と扱い、CI grep ガード `test_invariant_table_covers_all_ids` により**未対応 ID = 0** を収束条件として保証する。

> **Phase 8（2026-05-03 完了）注記**: 表中の `T35-U5-RelogE2E`（旧 `tests/e2e/tachibana_relogin_after_cancel.sh`、HTTP API 経由）は Phase 8 で pytest + `engine.live_session.LiveSession.login()` ベースに移行した。`src/replay_api.rs` 着地待ちスキップゲートは HTTP 廃止により消滅。pin テストの実体はそれぞれ pytest 版へ移植済み。

**最終照合 (2026-04-30)**: 実装テスト群（`python/tests/test_tachibana_yobine.py` / `test_tachibana_file_store.py` / `test_tachibana_dev_env_guard.py`、`engine-client/tests/ticker_info_tachibana_mapping.rs` 等）と本表を突合し、CLMYobine tick size 解決（HIGH-D2-1-B1a〜e / B2a/b）、`display_name_ja` / `lot_size` / `quote_currency` 伝播（HIGH-U-9）、session file store JST freshness（F-SC-FreshJST）、dev env release guard（F-DevEnv-Release-Guard）が登録済みであることを確認。差分なし。

**ID prefix 規約**:
- `F-*`: 本体（Phase 1〜2）の不変条件。
- `T35-*`: T3.5（再ログイン UX / VenueState FSM）の不変条件。CI grep regex は `(F|T35)-[A-Z0-9-]+` 相当を拾う（下記 CI ガード仕様参照）。
- `SKILL R*` / `HIGH-*`: 既存 prefix（変更なし）。`R[0-9]+` 単独 prefix の参照は `SKILL R*` に一本化済（CI ガード収集には影響しない / 下記 regex は `SKILL R*` を `R[0-9]+` 部分一致で拾う）。

**更新規約**:
1. 不変条件を追加・改廃したら、同 PR で必ず本表を更新する。
2. テスト関数を rename する PR では、本表の同行も同時に更新する（ドリフト防止）。
3. `pin する test ファイル::関数名` 列が空欄 / TBD のまま `Tx` 列の対応タスクが `[x]` 化されることは禁止。
4. `Tx タスク` 列は `implementation-plan.md` のタスク ID（T0/T1/T3/T5/T7 等）を参照する。

**CI ガード仕様（`test_invariant_table_covers_all_ids`）**:
- 本ファイルを Markdown 表としてパースし、`不変条件 ID` 列を抽出する。
- spec.md / SKILL.md / data-mapping.md / open-questions.md / review-fixes-*.md / implementation-plan-T3.5.md を grep し、`F-[A-Z0-9-]+`、`T35-[A-Z0-9-]+`、`R[0-9]+` パターンの ID 一覧を生成する（regex は `(F|T35)-[A-Z0-9-]+` 相当に拡張、`F-` と `T35-` 両 prefix を拾う）。
- ソース側 ID 集合 ⊖ 本表 ID 集合 ＝ ∅ を assert する（差分が出たら CI 失敗）。
- 加えて `pin する test ファイル::関数名` 列が空 / `TBD` のまま残る行があれば warning（`Tx` 列が `[x]` 状態なら error）。

---

## 表

| INV ID | 原 ID | 一次資料節 | pin する test ファイル::関数名 | 実行コマンド | Tx タスク | 関連 SKILL ID |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| INV-TACHIBANA-001 | F-H5 | spec.md §2.2 / architecture.md §7.4 | `data/tests/tachibana_second_password_guard.rs::test_phase1_second_password_guard_panics_in_debug`（同 pin に `debug_assert!(second_password.is_none())` 行を含める。R3 C-L1 の `second_password.is_none()` pin は独立 ID を切らず本 pin に統合する） | `cargo test -p flowsurface-data --tests -- test_phase1_second_password_guard_panics_in_debug` | T3 | R10 |
| INV-TACHIBANA-002 | F-B1 | architecture.md §7.2 / data-mapping.md §2 | `data/tests/tachibana_dto_secrecy.rs::test_credentials_roundtrip_with_zeroize_and_masked_debug` | `cargo test -p flowsurface-data --tests -- test_credentials_roundtrip_with_zeroize_and_masked_debug` | T3 | R10 |
| INV-TACHIBANA-003 | F-B2 | architecture.md §7.2 | `data/tests/tachibana_wire_dto.rs::test_wire_dto_serialize_derives_present` | `cargo test -p flowsurface-data --tests -- test_wire_dto_serialize_derives_present` | T3 | — |
| INV-TACHIBANA-004 | F-L1 | SKILL.md R1 / spec.md §3.2 | `python/tests/test_tachibana_url_single_source.py::test_base_url_literal_appears_only_in_tachibana_url_py` | `uv run pytest python/tests/test_tachibana_url_single_source.py -v` | T1 | R1 |
| INV-TACHIBANA-005 | F-L5 | SKILL.md R1 補遺 | T1 未実装（テスト未追加）— `test_base_url_is_single_source` 相当の関数は python/tests/ に存在しない。F-L1 の `test_base_url_literal_appears_only_in_tachibana_url_py` が意味的に近いが、F-L5 固有のテストは未作成 | T1 未実装（テスト未追加） | T1 | R1 |
| INV-TACHIBANA-006 | F-M4 | data-mapping.md §4 | `engine-client/tests/tachibana_session_reset.rs::depth_snapshot_with_new_session_id_deserializes` | `cargo test -p flowsurface-engine-client --test tachibana_session_reset` | T5 | — |
| INV-TACHIBANA-007 | F-M4b | data-mapping.md §4 注記 | `engine-client/tests/tachibana_session_reset.rs::new_session_id_resets_gap_detector_and_accepts_diffs` | `cargo test -p flowsurface-engine-client --test tachibana_session_reset` | T5 | — |
| INV-TACHIBANA-008 | F-M5a | data-mapping.md §5 | `python/tests/test_tachibana_holiday_fallback.py::test_subscribe_outside_market_hours_emits_disconnected` | `uv run pytest python/tests/test_tachibana_holiday_fallback.py -v` | T5 | — |
| INV-TACHIBANA-009 | F-M6a | SKILL.md R2 | `python/tests/test_tachibana_auth.py::test_login_rejects_non_wss_event_url` | `uv run pytest python/tests/test_tachibana_auth.py::test_login_rejects_non_wss_event_url -v` | T5 | R2 |
| INV-TACHIBANA-010 | F-M8 | data-mapping.md §8 | TBD（Tx で確定） | TBD | T5 | — |
| INV-TACHIBANA-011 | F-M8b | data-mapping.md §3 | `python/tests/test_tachibana_fd_trade.py::test_tick_rule_fallback_*` (3件: DPP=中値かつ上昇→buy / 下落→sell / 同値→buy+warn) | `uv run pytest python/tests/test_tachibana_fd_trade.py -v` | T5 | — |
| INV-TACHIBANA-012 | F-H1 | spec.md §2.1 | `data/tests/tachibana_session_validate.rs::test_validate_session_uses_get_issue_detail_with_pinned_payload` | `cargo test -p flowsurface-data --tests -- test_validate_session_uses_get_issue_detail_with_pinned_payload` | T3 | R6 |
| INV-TACHIBANA-013 | F-H2 | spec.md §2.1 / architecture.md §7.4 | `data/tests/tachibana_runtime_error.rs::test_runtime_error_from_validate_terminates_process_with_log` | `cargo test -p flowsurface-data --tests -- test_runtime_error_from_validate_terminates_process_with_log` | T3 | R6 |
| INV-TACHIBANA-014 | F-H6 | spec.md §2.2 | `data/tests/tachibana_login_flow.rs::test_login_raises_unread_notices_when_kinsyouhou_flag_set` | `cargo test -p flowsurface-data --tests -- test_login_raises_unread_notices_when_kinsyouhou_flag_set` | T3 | R3 / R6 |
| INV-TACHIBANA-015 | F-Default-Demo | spec.md §3.1 / open-questions.md Q21 | T2 未実装（テスト未追加）— `test_default_demo_flag` 相当の関数は python/tests/ に存在しない。`test_tachibana_login_dialog_modes.py::test_headless_forces_is_demo_true_when_prod_choice_disallowed` が is_demo=True の振る舞いに部分的に触れているが、F-Default-Demo 固有の「is_demo=True がデフォルト」を直接 pin するテストは未作成 | T2 未実装（テスト未追加） | T2 | R1 |
| INV-TACHIBANA-016 | F-Banner1 | spec.md §3.3 | `python/tests/test_tachibana_banner_messages.py::test_banner_message_uses_python_supplied_text` | `uv run pytest python/tests/test_tachibana_banner_messages.py -v` | T6 | — |
| INV-TACHIBANA-017 | F-Login1 | spec.md §2.2 / architecture.md §7.4 | `data/tests/tachibana_login_flow.rs::test_login_request_uses_json_ofmt_five` | `cargo test -p flowsurface-data --tests -- test_login_request_uses_json_ofmt_five` | T3 | R5 |
| INV-TACHIBANA-018 | F-DevEnv-Release-Guard | spec.md §3.1 / open-questions.md（dev env 制約）/ SKILL.md R10 末尾 / R1 | `python/tests/test_tachibana_dev_env_guard.py::test_dev_login_disallowed_does_not_fast_path_even_with_full_env` <br>`python/tests/test_tachibana_dev_env_guard.py::test_dev_login_allowed_uses_env_without_spawning_dialog` <br>`python/tests/test_tachibana_dev_env_guard.py::test_legacy_dev_env_aliases_no_longer_trigger_fast_path` <br>`python/tests/test_tachibana_dev_env_guard.py::test_dev_login_allowed_falls_back_to_dialog_when_env_missing` | `uv run pytest python/tests/test_tachibana_dev_env_guard.py -v` | T2 | R1 / R10 |
| INV-TACHIBANA-019 | SKILL R1 | SKILL.md R1（実弾保護 / Demo 既定） | T2 未実装（テスト未追加）— `test_shift_jis_decode` 相当の関数は python/tests/ に存在しない（Shift-JIS テストは SKILL R7 / `test_tachibana_codec.py::test_decode_response_body_japanese_shift_jis` でカバー済み。実弾保護 / Demo 既定に特化したテストは未作成） | T2 未実装（テスト未追加） | T2 | R1 |
| INV-TACHIBANA-020 | SKILL R2 | SKILL.md R2（EVENT URL wss 強制） | `python/tests/test_tachibana_auth.py::test_login_rejects_non_wss_event_url` | `uv run pytest python/tests/test_tachibana_auth.py::test_login_rejects_non_wss_event_url -v` | T5 | R2 |
| INV-TACHIBANA-021 | SKILL R3 | SKILL.md R3（永続化禁止対象） | `data/tests/tachibana_log_redaction.rs::test_runtime_logs_do_not_contain_credentials_or_virtual_urls`（注: `venue_banner.rs` 側の redaction は T35-U2-BannerRedaction で別 pin。UI bridge 拡張観点での相互参照） | `cargo test -p flowsurface-data --tests -- test_runtime_logs_do_not_contain_credentials_or_virtual_urls` | T3 | R3 / R10 |
| INV-TACHIBANA-022 | SKILL R4 | SKILL.md R4（p_no 採番） | `python/tests/test_tachibana_pno_counter.py::test_pno_monotonic_under_concurrency` | `uv run pytest python/tests/test_tachibana_pno_counter.py -v` | T3 | R4 |
| INV-TACHIBANA-023 | SKILL R5 | SKILL.md R5（sJsonOfmt=5） | `data/tests/tachibana_login_flow.rs::test_login_request_uses_json_ofmt_five` | `cargo test -p flowsurface-data --tests -- test_login_request_uses_json_ofmt_five` | T3 | R5 |
| INV-TACHIBANA-024 | SKILL R6 | SKILL.md R6（業務エラー判定 / sResultCode=0 で subscription 維持） | `python/tests/test_tachibana_st_frame.py::test_st_frame_with_zero_result_code_does_not_stop_subscriptions` | `uv run pytest python/tests/test_tachibana_st_frame.py -v` | T5 | R6 |
| INV-TACHIBANA-025 | SKILL R7 | SKILL.md R7（Shift-JIS 入出力） | `python/tests/test_tachibana_encoding.py::test_shift_jis_request_response_pipeline` | `uv run pytest python/tests/test_tachibana_encoding.py -v` | T1 | R7 |
| INV-TACHIBANA-026 | SKILL R8 | SKILL.md R8（マスタファイル運用） | `python/tests/test_tachibana_yobine.py::test_clm_yobine_decoder_collects_20_bands` | `uv run pytest python/tests/test_tachibana_yobine.py -v` | T4 | R8 |
| INV-TACHIBANA-027 | SKILL R9 | SKILL.md R9（URL エンコード規約） | `python/tests/test_tachibana_urlencode.py::test_replace_urlecnode_empty` | `uv run pytest python/tests/test_tachibana_urlencode.py -v` | T5 | R9 |
| INV-TACHIBANA-028 | SKILL R10 | SKILL.md R10（仮想 URL 秘匿） | `data/tests/tachibana_log_redaction.rs::test_runtime_logs_do_not_contain_credentials_or_virtual_urls` | `cargo test -p flowsurface-data --tests -- test_runtime_logs_do_not_contain_credentials_or_virtual_urls` | T3 | R10 |
| INV-TACHIBANA-029 | F-Process-Restart | architecture.md §7.4 / spec.md §3.1 | `engine-client/tests/process_lifecycle.rs::run_with_recovery_calls_on_restart_after_connection_loss` | `cargo test -p flowsurface-engine-client --test process_lifecycle` | T3 | — |
| INV-TACHIBANA-030 | F-Process-VenueReadyGate | architecture.md §8.3 | `engine-client/tests/process_venue_ready_gate.rs`（全関数） | `cargo test -p flowsurface-engine-client --test process_venue_ready_gate` | T3 | — |
| INV-TACHIBANA-031 | F-Process-VenueReadyTimeout | architecture.md §8.3 | `engine-client/tests/process_venue_ready_timeout_marks_failed.rs`（全関数） | `cargo test -p flowsurface-engine-client --test process_venue_ready_timeout_marks_failed` | T3 | — |
| INV-TACHIBANA-032 | ~~F-Process-CredsRefreshHook~~ | ~~architecture.md §8.3~~ | **削除済み** — `VenueCredentialsRefreshed` イベント廃止に伴い `engine-client/tests/process_creds_refresh_hook.rs` ごと削除（architecture.md §8.3 参照） | — | T3 | — |
| INV-TACHIBANA-033 | ~~F-Process-CredsRefreshSingleton~~ | ~~architecture.md §8.3~~ | **削除済み** — `VenueCredentialsRefreshed` イベント廃止に伴い `engine-client/tests/process_creds_refresh_listener_singleton.rs` ごと削除（architecture.md §8.3 参照） | — | T3 | — |
| INV-TACHIBANA-034 | F-Process-VenueLoginCancelled | architecture.md §8.3 | `engine-client/tests/process_venue_login_cancelled.rs`（全関数） | `cargo test -p flowsurface-engine-client --test process_venue_login_cancelled` | T3 | — |
| INV-TACHIBANA-035 | F-Process-SessionRestoreFailed | architecture.md §8.3 | `engine-client/tests/process_venue_error_session_restore_failed.rs`（全関数） | `cargo test -p flowsurface-engine-client --test process_venue_error_session_restore_failed` | T3 | — |
| INV-TACHIBANA-036 | HIGH-U-9 | implementation-plan.md T4（Rust 側 `TickerInfo` 受信マッピング配線、Q16） | `engine-client/tests/ticker_info_tachibana_mapping.rs::test_tachibana_ticker_info_carries_display_name_ja_and_lot_size` | `cargo test -p flowsurface-engine-client --test ticker_info_tachibana_mapping` | T4 | R8 |
| INV-TACHIBANA-037 | HIGH-U-10a | implementation-plan.md T4（マスタ invalidation: `is_demo` 切替） | `python/tests/test_tachibana_master_invalidation.py::test_master_reloaded_when_is_demo_flips` | `uv run pytest python/tests/test_tachibana_master_invalidation.py -v` | T4 | R8 |
| INV-TACHIBANA-038 | HIGH-U-10b | implementation-plan.md T4（マスタ invalidation: JST 日跨ぎ） | `python/tests/test_tachibana_master_invalidation.py::test_master_reloaded_after_jst_rollover_in_running_process` | `uv run pytest python/tests/test_tachibana_master_invalidation.py -v` | T4 | R8 |
| INV-TACHIBANA-039 | HIGH-U-10c | implementation-plan.md T4（マスタ invalidation: `__init__` 再生成） | `python/tests/test_tachibana_master_invalidation.py::test_master_event_is_fresh_per_worker_init` | `uv run pytest python/tests/test_tachibana_master_invalidation.py -v` | T4 | R8 |
| INV-TACHIBANA-040 | HIGH-U-11p | implementation-plan.md T4（非 `"1d"` kline 拒否 / Python 側） | `python/tests/test_tachibana_fetch_klines_reject.py::test_fetch_klines_rejects_non_d1_timeframes` | `uv run pytest python/tests/test_tachibana_fetch_klines_reject.py -v` | T4 | — |
| INV-TACHIBANA-041 | HIGH-U-11r | implementation-plan.md T4（非 `"1d"` kline 拒否 / Rust 復元 fail-safe） | `engine-client/tests/tachibana_kline_capability_gate.rs::test_restored_pane_with_non_d1_timeframe_does_not_crash` | `cargo test -p flowsurface-engine-client --test tachibana_kline_capability_gate` | T4 | — |
| INV-TACHIBANA-042 | HIGH-D2-1-B1a | data-mapping.md §5.2 / implementation-plan.md T4 B1（`CLMYobine` decoder 20 スロット読出し） | `python/tests/test_tachibana_yobine.py::test_clm_yobine_decoder_collects_20_bands` | `uv run pytest python/tests/test_tachibana_yobine.py -v` | T4 | R8 |
| INV-TACHIBANA-043 | HIGH-D2-1-B1b | data-mapping.md §5.2（`999999999` sentinel truncate） | `python/tests/test_tachibana_yobine.py::test_clm_yobine_decoder_truncates_at_999999999_sentinel` | `uv run pytest python/tests/test_tachibana_yobine.py -v` | T4 | R8 |
| INV-TACHIBANA-044 | HIGH-D2-1-B1c | data-mapping.md §5.3（`tick_size_for_price` 代表 yobine_code 境界値 ±1 銭） | `python/tests/test_tachibana_yobine.py::test_tick_size_for_price_uses_first_band_le_price` | `uv run pytest python/tests/test_tachibana_yobine.py -v` | T4 | R8 |
| INV-TACHIBANA-045 | HIGH-D2-1-B1d | data-mapping.md §5.3（未知 `yobine_code` で `KeyError`） | `python/tests/test_tachibana_yobine.py::test_tick_size_for_price_unknown_yobine_code_raises_keyerror` | `uv run pytest python/tests/test_tachibana_yobine.py -v` | T4 | R8 |
| INV-TACHIBANA-046 | HIGH-D2-1-B1e | data-mapping.md §5.3（`price` は `Decimal` 限定、int/float 拒否） | `python/tests/test_tachibana_yobine.py::test_tick_size_for_price_decimal_only` | `uv run pytest python/tests/test_tachibana_yobine.py -v` | T4 | R8 |
| INV-TACHIBANA-047 | HIGH-D2-1-B2a | data-mapping.md §5.4 / implementation-plan.md T4 B2（銘柄→ yobine_code → tick 解決） | `python/tests/test_tachibana_master_yobine_resolve.py::test_resolve_tick_size_for_issue_uses_clm_yobine_lookup` | `uv run pytest python/tests/test_tachibana_master_yobine_resolve.py -v` | T4 | R8 |
| INV-TACHIBANA-048 | HIGH-D2-1-B2b | implementation-plan.md T4 B2（`yobine_table` invalidation: is_demo / JST / `__init__`） | `python/tests/test_tachibana_master_yobine_invalidation.py::test_yobine_table_reloaded_on_invalidation_triggers` | `uv run pytest python/tests/test_tachibana_master_yobine_invalidation.py -v` | T4 | R8 |
| INV-TACHIBANA-049 | HIGH-C3-1 | architecture.md §4（Shift-JIS decode: FD frame 内の漢字・仮名が文字化けしないこと） | `python/tests/test_tachibana_ws.py::TestShiftJisDecode::test_kanji_in_fd_frame_is_not_garbled` | `uv run pytest python/tests/test_tachibana_ws.py -v` | T5 | — |
| INV-TACHIBANA-050 | HIGH-D5 | spec.md §2.3（ザラ場時間境界: JST 9:00–11:30 / 12:30–15:30 を `is_market_open` で正確に判定） | `python/tests/test_tachibana_session_window.py::test_market_hours_boundary` | `uv run pytest python/tests/test_tachibana_session_window.py -v` | T5 | — |
| INV-TACHIBANA-051 | MEDIUM-D6 | spec.md §3.2 / architecture.md §4（ST sResultCode=0 は情報レベル: 購読を停止しない） | `python/tests/test_tachibana_ws.py::TestStFrame::test_st_zero_result_does_not_stop_callback` | `uv run pytest python/tests/test_tachibana_ws.py -v` | T5 | R6 |
| INV-TACHIBANA-052 | HIGH-D2-1-WsTimeout | spec.md §3.2（dead-frame タイムアウト: KP 含む全 frame が 12 秒来なければ再接続） | `python/tests/test_tachibana_ws_timeout.py::test_no_frame_within_timeout_triggers_reconnect` | `uv run pytest python/tests/test_tachibana_ws_timeout.py -v` | T5 | — |
| INV-TACHIBANA-053 | T35-H5-PathFidelity | implementation-plan-T3.5.md §3 Step B（H5: `EngineCommand::Bundled(p).program()` の `to_str().unwrap_or("flowsurface-engine")` fallback を `&OsStr` 直接受けに置換し、Unicode/非ASCII パスを silent skip しない） | `engine-client/tests/bundled_path_with_unicode.rs::bundled_program_preserves_unicode_path` | `cargo test -p flowsurface-engine-client --test bundled_path_with_unicode` | T3.5 | — |
| INV-TACHIBANA-054 | T35-H6-KeyringSlotIsolation | implementation-plan-T3.5.md §3 Step B（H6: 共有 `SharedStore` 上の production keyring slot を各テストが `fresh_keyring_slot` で明示リセットし、`#[serial]` 順依存の状態漏洩を排除）。**安定化基準: × 5 連続緑** | `data/tests/tachibana_keyring_roundtrip.rs::keyring_slot_is_isolated_per_test` | `cargo test -p flowsurface-data --tests -- --test-threads=4 keyring_roundtrip`（× 5 連続緑が安定化基準） | T3.5 | R3 |
| INV-TACHIBANA-055 | T35-H7-NoStaticInUpdate | implementation-plan-T3.5.md §3 Step A（H7: `Flowsurface::update()` から `static ENGINE_CONNECTION` の直接読み出しを排除し、Subscription/Task 経由に置換） | `tests/main_update_no_static_access.rs::update_body_has_no_engine_connection_read` + `tools/iced_purity_grep.sh`（assert 補助） | `cargo test -p flowsurface --test main_update_no_static_access` + `tools/iced_purity_grep.sh` | T3.5 | — |
| INV-TACHIBANA-056 | T35-H8-NoBlockOnInUpdate | implementation-plan-T3.5.md §3 Step A（H8: `update()` 内の `block_on(...)` を `Task::perform` 化、現状未出現の regression guard） | `tests/main_update_no_block_on.rs::update_body_has_no_block_on` + `tools/iced_purity_grep.sh`（assert 補助） | `cargo test -p flowsurface --test main_update_no_block_on` + `tools/iced_purity_grep.sh` | T3.5 | — |
| INV-TACHIBANA-057 | T35-H9-SingleRecoveryPath | implementation-plan-T3.5.md §3 Step A（H9: 手動 reconnect callback / 二重経路を `Subscription::run` 単一化） | `tests/engine_status_subscription_is_singleton.rs::engine_status_subscription_is_singleton` + `tools/iced_purity_grep.sh`（assert 補助） | `cargo test -p flowsurface --test engine_status_subscription_is_singleton` + `tools/iced_purity_grep.sh` | T3.5 | — |
| INV-TACHIBANA-058 | T35-H7-DebugRedaction | implementation-plan-T3.5.md §3 Step A REFACTOR（`EngineConnection: Debug` の secret 焼付きガード） | `engine-client/tests/engine_connection_debug_redaction.rs::engine_connection_debug_does_not_leak_credentials` | `cargo test -p flowsurface-engine-client --test engine_connection_debug_redaction` | T3.5 | R10 |
| INV-TACHIBANA-059 | T35-U4-VenueReadyGate | implementation-plan-T3.5.md §3 Step C（U4: 立花 metadata fetch を `VenueState::Ready` まで抑止し、pending fetch を `VenueState::Ready` 遷移時に再生する。旧 API `set_tachibana_ready(true)` は VenueState FSM へ統一済） | `src/screen/dashboard/tickers_table.rs::tests::metadata_fetch_blocked_until_venue_ready` および `src/screen/dashboard/tickers_table.rs::tests::pending_fetch_replays_on_venue_ready` | `cargo test -p flowsurface --lib -- screen::dashboard::tickers_table::tests::metadata_fetch_blocked_until_venue_ready screen::dashboard::tickers_table::tests::pending_fetch_replays_on_venue_ready` | T3.5 | — |
| INV-TACHIBANA-060 | T35-U4-StartupGate | implementation-plan-T3.5.md §レビュー修正 R2（U4 拡張: persisted Tachibana 選択や engine reconnect 経路でも Tachibana metadata fetch を `VenueState::Ready` まで抑止。`new_with_settings` / `update_handles` から Tachibana を初期 fetch リスト除外し pending=true へ） | `src/screen/dashboard/tickers_table.rs::tests::tachibana_in_initial_settings_defers_fetch_to_pending` および `..::update_handles_skips_tachibana_when_not_ready` | `cargo test -p flowsurface --lib -- screen::dashboard::tickers_table::tests::tachibana_in_initial_settings_defers_fetch_to_pending screen::dashboard::tickers_table::tests::update_handles_skips_tachibana_when_not_ready` | T3.5 | — |
| INV-TACHIBANA-061 | T35-VenueReadyCache | implementation-plan-T3.5.md §レビュー修正 R2（HIGH-1: `ProcessManager::venue_ready_state` で post-handshake VenueReady を caching し、broadcast::Receiver の non-replay 性ゆえ `Flowsurface::Message::EngineConnected` で query → 必要に応じて `VenueEvent::Ready` を synthesize する。subscribe-after-handshake の race を解消） | `engine-client/src/process.rs` の `try_is_venue_ready` 公開 API + `apply_after_handshake_with_timeout` の VenueReady/VenueError 経路で `venue_ready_state` を更新（FSM 直接 unit テストは未追加、構造的 review コメント + ビルド統合で守る） | `cargo test --workspace` | T3.5 | — |
| INV-TACHIBANA-062 | T35-RehelloOrder | implementation-plan-T3.5.md §レビュー修正 R3（HIGH-1: `engine_status_stream` の yield 順序を `EngineRehello → EngineConnected` に固定。EngineConnected ハンドラの `update_handles` が古い `tachibana_ready=true` で動かないようにする） | `tests/engine_rehello_yields_before_engine_connected.rs::engine_rehello_yields_before_engine_connected_in_both_branches`（source-level text scan で initial / changed 両分岐の yield 順を pin。`async_stream!` マクロボディゆえ syn AST visitor が使えず text スキャンとした） | `cargo test --test engine_rehello_yields_before_engine_connected` | T3.5 | — |
| INV-TACHIBANA-063 | T35-VenueReadyBridge | implementation-plan-T3.5.md §レビュー修正 R3（HIGH-2: 外部エンジンモード `--data-engine-url` でも `VenueReady` を取りこぼさないよう、両モードで `spawn_venue_ready_bridge` 相当のタスクを `connect()` 直後に spawn し、グローバル `VENUE_READY_CACHE` (`OnceLock<Arc<Mutex<FxHashSet<String>>>>`) に流し込む。`Flowsurface::Message::EngineConnected` ハンドラは ProcessManager cache と本キャッシュを **OR で query** する） | `src/main.rs::spawn_venue_ready_bridge` / `cached_venue_is_ready` ヘルパ + 外部モード recovery loop の bridge spawn / managed mode recovery loop の bridge spawn — 構造的 pin（Stream 起動と live broadcast を要するため unit テストは別 PR） | `cargo test --workspace` | T3.5 | — |
| INV-TACHIBANA-064 | T35-VenueNamesIncludesTachibana | implementation-plan-T3.5.md §レビュー修正 R4（HIGH-1: `VENUE_NAMES` に `Venue::Tachibana` が含まれていないと `EngineClientBackend` が登録されず、`fetch_ticker_metadata(Tachibana, …)` が `No adapter handle configured` で全失敗する。U4 ゲートは通っても下流が silent に死ぬ） | `tests/venue_names_includes_tachibana.rs::venue_names_includes_tachibana_backend`（text scan で `VENUE_NAMES` 定義中に `Venue::Tachibana` と wire id `"tachibana"` が含まれることを assert） | `cargo test --test venue_names_includes_tachibana` | T3.5 | — |
| INV-TACHIBANA-065 | T35-DupPressClaim | implementation-plan-T3.5.md §レビュー修正 R4（MEDIUM-2: `RequestTachibanaLogin` の dup 抑止が `VenueLoginStarted` 到着待ちで実装されており、その間に複数の IPC が走る race を `VenueState::try_claim_login_in_flight` の optimistic transition + `TachibanaLoginIpcResult(Err)` 時の rollback で閉じる） | `src/venue_state.rs::tests::try_claim_login_in_flight_succeeds_from_idle` / `..::try_claim_login_in_flight_succeeds_from_ready` / `..::try_claim_login_in_flight_succeeds_from_error` / `..::try_claim_login_in_flight_rejects_when_already_in_flight` | `cargo test -p flowsurface --lib -- venue_state::tests::try_claim_login_in_flight` | T3.5 | — |
| INV-TACHIBANA-066 | T35-CacheInvalidation | implementation-plan-T3.5.md §レビュー修正 R4（MEDIUM-3: 全 venue-ready bridge と `ProcessManager.venue_ready_state` が `VenueReady` / `VenueError` のみならず `VenueLoginStarted` / `VenueLoginCancelled` でもキャッシュを invalidate する。stale Ready が re-login dialog cancel と engine reconnect を跨いで生存しないことを担保） | `tests/venue_ready_bridge_invalidates_on_login_events.rs::every_venue_ready_bridge_handles_all_four_lifecycle_events`（main.rs 内の全 bridge body に `EngineEvent::Venue{Ready,Error,LoginStarted,LoginCancelled}` の 4 アームが揃っていることを text scan で pin） | `cargo test --test venue_ready_bridge_invalidates_on_login_events` | T3.5 | — |
| INV-TACHIBANA-067 | T35-PendingClearOnDeselect | implementation-plan-T3.5.md §レビュー修正 R5（MEDIUM-1: 未ログイン状態で Tachibana を ON→OFF した後にあとで `VenueReady` が来ても、ユーザーが解除した venue に対して metadata fetch が走らないことを担保。`ToggleExchangeFilter` の deselect 経路で `tachibana_fetch_pending=false` を立てる + `set_tachibana_ready` で `selected_exchanges.contains(&Venue::Tachibana)` を二重チェック） | `src/screen/dashboard/tickers_table.rs::tests::deselecting_tachibana_clears_pending_fetch_so_later_ready_does_not_replay` | `cargo test -p flowsurface --lib -- screen::dashboard::tickers_table::tests::deselecting_tachibana_clears_pending_fetch_so_later_ready_does_not_replay` | T3.5 | — |
| INV-TACHIBANA-068 | T35-BannerActionFallback | implementation-plan-T3.5.md §レビュー修正 R5（MEDIUM-2: Python が単一行 `VenueError.message` を出している現実に合わせ、`action_button_label` で `Relogin` / `Dismiss` 時に Rust 側 fallback ラベル（"再ログイン" / "閉じる"）を提供する。F-Banner1 の "Rust に文字列リテラルを持たない" 制約は Python が 3 行 message に揃うまでの暫定で部分緩和。`Hidden` は無条件にボタン非表示） | `src/widget/venue_banner.rs::tests::action_button_label_uses_python_supplied_label_when_present` / `..::action_button_label_falls_back_for_relogin_when_message_is_single_line` / `..::action_button_label_falls_back_for_dismiss_when_message_is_single_line` / `..::action_button_label_returns_none_for_hidden_even_with_third_line` | `cargo test -p flowsurface --lib -- widget::venue_banner::tests::action_button_label` | T3.5 | — |
| INV-TACHIBANA-069 | T35-VenueReadyStateCycleClear | implementation-plan-T3.5.md §レビュー修正 R6（HIGH: `ProcessManager.venue_ready_state` が recovery loop の cycle 跨ぎでクリアされず、credentials 無し cycle が stale `VenueReady` を保持して `Flowsurface::Message::EngineConnected` が phantom Ready を synthesize する silent failure を解消。`apply_after_handshake_with_timeout` 冒頭で `venue_ready_state.lock().await.clear()` を呼び、`main.rs` 側の `VENUE_READY_CACHE.clear()` と非対称性を解消） | `engine-client/tests/apply_after_handshake_clears_venue_ready_state.rs::apply_after_handshake_clears_stale_venue_ready_state`（text scan で関数本体冒頭に `venue_ready_state.lock().await.clear()` 呼び出しがあり、かつ `subscribe_events()` より前であることを pin） | `cargo test -p flowsurface-engine-client --test apply_after_handshake_clears_venue_ready_state` | T3.5 | — |
| INV-TACHIBANA-070 | T35-U4-FSM | implementation-plan-T3.5.md §3.2（VenueState 二重フラグ廃止と単一 enum 化、9 通り遷移） | `src/venue_state.rs::tests::fresh_state_is_idle_and_not_ready` / `..::login_started_transitions_idle_to_in_flight` / `..::ready_event_transitions_in_flight_to_ready` / `..::cancel_returns_to_idle_so_user_can_retry` / `..::error_carries_class_and_verbatim_message` / `..::login_started_can_recover_from_error` / `..::engine_rehello_always_resets_to_idle` / `..::ready_is_idempotent_under_repeated_ready_events` | `cargo test -p flowsurface --lib -- venue_state::tests` | T3.5 | — |
| INV-TACHIBANA-071 | T35-U2-BannerRedaction | implementation-plan-T3.5.md §3 Step E / SKILL.md R3 / R10（UI bridge 拡張）（`VenueState::Error.message` / `BannerMessage` payload / `Trigger::Manual` toast 文言が credentials / 仮想 URL を露出しないことを redaction pin） | `src/widget/venue_banner.rs::tests::banner_message_does_not_leak_credentials_or_virtual_urls`（テスト本体実装は本タスクのスコープ外、計画書側の登録のみ） | `cargo test -p flowsurface --lib -- widget::venue_banner::tests::banner_message_does_not_leak_credentials_or_virtual_urls` | T3.5 | R3 / R10 |
| INV-TACHIBANA-072 | T35-U1-LoginButton | implementation-plan-T3.5.md §3 Step D（U1: `tickers_table::exchange_filter_btn` の Tachibana 行直下に inline 「ログイン」ボタンを設け、押下で `Action::RequestTachibanaLogin(Trigger::Manual)` を bubble する。Flowsurface 側で `LoginInFlight` 中は Task::none() に倒れる） | `src/screen/dashboard/tickers_table.rs::tests::sidebar_login_button_emits_request_venue_login` および `src/screen/dashboard/tickers_table.rs::tests::duplicate_press_returns_task_none_while_login_in_flight` | `cargo test -p flowsurface --lib -- screen::dashboard::tickers_table::tests::sidebar_login_button_emits_request_venue_login screen::dashboard::tickers_table::tests::duplicate_press_returns_task_none_while_login_in_flight` | T3.5 | — |
| INV-TACHIBANA-073 | T35-U3-AutoRequestLogin | implementation-plan-T3.5.md §3 Step D（U3: `ToggleExchangeFilter(Tachibana)` がゲートで弾かれた瞬間に `Action::RequestTachibanaLogin(Trigger::Auto)` を auto-fire する。LOW-3 「ユーザー明示」分類） | `src/screen/dashboard/tickers_table.rs::tests::auto_request_login_on_first_open_classified_as_manual_trigger` | `cargo test -p flowsurface --lib -- screen::dashboard::tickers_table::tests::auto_request_login_on_first_open_classified_as_manual_trigger` | T3.5 | — |
| INV-TACHIBANA-074 | T35-U2-Banner | implementation-plan-T3.5.md §3 Step E（U2: `VenueState::Error` のときだけバナー描画。`classify_venue_error` の severity → palette role マッピングのみ Rust 側で保持し、ヘッダ/本文/ボタンラベルはすべて Python の `VenueError.message` 改行 3 行から抽出。Idle/LoginInFlight/Ready は無描画） | `src/widget/venue_banner.rs::tests::idle_state_yields_no_banner`（Idle で無描画）/ `..::ready_state_yields_no_banner`（Ready で無描画）/ `..::login_in_flight_yields_no_banner`（LoginInFlight で無描画）/ `..::error_state_yields_banner`（Error で描画）/ `..::parse_message_three_lines_extracts_header_body_label`（3 行 → header/body/label 抽出）/ `..::parse_message_two_lines_has_no_button_label`（2 行 → ボタン無し）/ `..::parse_message_single_line_goes_into_body`（1 行 → body のみ）/ `..::parse_message_empty_lines_are_dropped`（空行除外）/ `..::banner_transitions`（state 遷移時の描画切替）/ `..::dismiss_action_uses_dismiss_button`（dismiss button 経路）/ `..::hidden_action_renders_message_without_button`（button 無しで本文描画） | `cargo test -p flowsurface --lib -- widget::venue_banner::tests` | T3.5 | — |
| INV-TACHIBANA-075 | T35-U5-RelogE2E | implementation-plan-T3.5.md §3 Step F（U5: HTTP API 経由で「Tachibana 選択 → cancel → 再ログイン」を bash で end-to-end pin。VenueLoginStarted=2 / VenueLoginCancelled=1 をログ count で検証）。**現状はスケルトン**: `src/replay_api.rs` 未実装のため pre-flight gate で `exit 77` (skip)。HTTP API 着地後にスキップ条件を外す。**実行条件: nightly + `e2e` label, `OBSERVE_S=60`** | `tests/e2e/tachibana_relogin_after_cancel.sh` | `OBSERVE_S=60 bash tests/e2e/tachibana_relogin_after_cancel.sh`（nightly CI に `e2e` label 付きで配置） | T3.5 | — |
| INV-TACHIBANA-076 | T35-LoginUpdate | login-test-plan.md §Layer 3（RequestTachibanaLogin ハンドラの 4 不変条件を構造的ピンで固定: (1) `try_claim_login_in_flight()` 呼び出し, (2) `Command::RequestVenueLogin` IPC 送信, (3) `TachibanaLoginIpcResult` callback hook, (4) 未接続時 `Task::none()` 早期リターン）| `tests/tachibana_login_update.rs::request_login_calls_try_claim` / `::request_login_sends_request_venue_login_command` / `::request_login_hooks_tachibana_login_ipc_result` / `::request_login_returns_none_when_no_connection` | `cargo test --test tachibana_login_update` | T3.5 | — |
| INV-TACHIBANA-077 | F-SC-NoPassword | tachibana_file_store.py（T-SC1 F-SC-NoPassword）| `python/tests/test_tachibana_file_store.py::test_save_account_does_not_include_password` <br>`python/tests/test_tachibana_file_store.py::test_save_session_does_not_include_password` | `uv run pytest python/tests/test_tachibana_file_store.py -v` | T-SC5 | R3 |
| INV-TACHIBANA-078 | F-SC-Atomic | tachibana_file_store.py（T-SC1 F-SC-Atomic）| `python/tests/test_tachibana_file_store.py::test_atomic_write_leaves_no_tmp_on_success` <br>`python/tests/test_tachibana_file_store.py::test_atomic_write_preserves_original_on_exception` | `uv run pytest python/tests/test_tachibana_file_store.py -v` | T-SC5 | — |
| INV-TACHIBANA-079 | F-SC-FreshJST | tachibana_file_store.py（T-SC1 F-SC-FreshJST）| `python/tests/test_tachibana_file_store.py::test_is_session_fresh_same_day_before_cutoff` <br>`python/tests/test_tachibana_file_store.py::test_is_session_fresh_same_day_at_cutoff_is_stale` <br>`python/tests/test_tachibana_file_store.py::test_is_session_fresh_same_day_before_cutoff_minus_1s` <br>`python/tests/test_tachibana_file_store.py::test_is_session_fresh_different_day_is_stale` <br>`python/tests/test_tachibana_file_store.py::test_is_session_fresh_clock_skew_future_is_stale` | `uv run pytest python/tests/test_tachibana_file_store.py -v` | T-SC5 | — |

---

## 未対応 ID（収束条件）

CI ガードが検知した未対応 ID は本セクションに自動列挙される（PR 着地までに 0 行に縮退させる）。

- （現在: 0 件 / TBD は対応 Tx タスク内で確定）


## kabusapi

# kabusapi invariant 抽出断片

本ファイルは `docs/specs/venues/kabusapi/invariant-tests.md` の各不変条件項目を `INV-KABUSAPI-NNN` 体系で再番号付けしたもの。Wave 4 で `docs/testing/invariants.md` に統合される。元の K-task ID（INV-K1-* 等）は履歴互換のために併記する。

## INV-KABUSAPI-NNN 一覧

| 新 ID | 旧 ID | 不変条件の説明 | テストファイル | 代表 assert |
| :--- | :--- | :--- | :--- | :--- |
| INV-KABUSAPI-001 | INV-K1-ENUM | `Venue::KabuStation` / `Exchange::KabuStationStock` が enum 網羅 match 全箇所でコンパイルする | `cargo test -p engine-client schema_minor_kabu` | compile success |
| INV-KABUSAPI-002 | INV-K1-CAP | `max_push_symbols=50` と `RegisterSet.MAX=50` の一致 | `test_kabusapi_capabilities.py::test_capabilities_max_push_symbols_matches_register_set` | `assert capabilities["max_push_symbols"] == RegisterSet.MAX == 50` |
| INV-KABUSAPI-003 | INV-K2-TOKEN-ERR | `Code=4001005` から `KabuTokenExpiredError` が発火する | `test_kabusapi_auth.py` | `pytest.raises(KabuTokenExpiredError)` |
| INV-KABUSAPI-004 | INV-K2-TOKEN-ERR-2 | `Code=4001001` からも `KabuTokenExpiredError` が発火する | `test_kabusapi_auth.py` | `pytest.raises(KabuTokenExpiredError)` |
| INV-KABUSAPI-005 | INV-K2-NO-LOG-SECRET | caplog に token / API パスワード / 取引パスワードが出力されない | `test_kabusapi_auth_logging.py` | `assert TOKEN not in caplog.text` など |
| INV-KABUSAPI-006 | INV-K2-SJIS-REJECT | SJIS バイト列で `UnicodeDecodeError` が発火する | `test_kabusapi_codec.py::test_decode_rejects_sjis_bytes` | `pytest.raises(UnicodeDecodeError)` |
| INV-KABUSAPI-007 | INV-K2-SIDE-BUY-2 | kabu 買い区分は `"2"` であり `"3"` ではない | `test_kabusapi_codec.py::test_side_mapping_kabu_buy_is_2_not_3` | `assert buy_side == "2"` |
| INV-KABUSAPI-008 | INV-K3-TCP-RETRY | TCP 拒否時 5s × 3 回後に `VenueError{code:"local_app_down"}` | `test_kabusapi_login_flow.py::test_tcp_refused_three_retries_then_local_app_down` | `assert error.code == "local_app_down"` |
| INV-KABUSAPI-009 | INV-K3-NO-TKINTER | `DEV_KABU_API_PASSWORD` 設定時に tkinter を spawn しない | `test_kabusapi_login_flow.py` | `mock_spawn.assert_not_called()` |
| INV-KABUSAPI-010 | INV-K3-MORNING | 早朝時刻帯の `local_app_down` は INFO ログ扱い | `test_kabusapi_login_flow.py` | `assert "INFO" in caplog.records[...]` |
| INV-KABUSAPI-011 | INV-K4-REG-FULL | 51 件目の register で `KabuRegisterFullError` | `test_kabusapi_register.py::test_register_51st_raises_full` | `pytest.raises(KabuRegisterFullError)` |
| INV-KABUSAPI-012 | INV-K4-LRU-EVICT | LRU evict 時に `SubscriptionEvicted{symbol}` を IPC 送出 | `test_kabusapi_register.py::test_lru_evict_emits_subscription_evicted` | `assert evicted_symbol in ipc_events` |
| INV-KABUSAPI-013 | INV-K4-RATELIMIT | OrderBucket(5) が 6 件目でブロックする | `test_kabusapi_ratelimit.py::test_order_bucket_blocks_at_6th_req` | 6 件目が待機することを assert |
| INV-KABUSAPI-014 | INV-K5-RECONNECT-REREG | 再接続後に RegisterSet 全件を re-register | `test_kabusapi_ws.py::test_reconnect_reregisters_all_symbols` | `assert PUT /register calls == 全件` |
| INV-KABUSAPI-015 | INV-K5-ABORT | 5s × 5 回連続失敗で reconnect ループ打ち切り → `VenueError{code:"local_app_down"}` | `test_kabusapi_ws.py::test_reconnect_aborts_after_5_consecutive_failures` | `assert error.code == "local_app_down"` |
| INV-KABUSAPI-016 | INV-K6-TOUCH | `fetch_board()` が `RegisterSet.touch()` を呼ぶ | `test_kabusapi_rest.py::test_fetch_board_touches_register_set` | `register_set.touch.assert_called()` |
| INV-KABUSAPI-017 | INV-K6-REST-FULL | `fetch_board()` で新規 + 満杯時に `KabuRegisterFullError` | `test_kabusapi_rest.py` | `pytest.raises(KabuRegisterFullError)` |
| INV-KABUSAPI-018 | INV-K7-E2E | `LiveSession.login(venue="kabu_station")` が `VenueReady{venue:"kabu_station"}` を発火 | `test_live_session_kabu.py::test_login_kabu_station_emits_venue_ready` | `assert event.venue == "kabu_station"` |
| INV-KABUSAPI-019 | INV-K8-URL-LINT | `kabusapi_url.py` 以外に URL リテラルが存在しない | CI lint step | zero-match assertion |
| INV-KABUSAPI-020 | INV-K85-CI | `pytest -m demo_kabu` が CI でグリーン | `.github/workflows/kabu-mock.yml` | CI pass |

## 早朝強制ログアウト時刻帯の分岐定義 {#early-morning-logout}

kabuステーション本体は早朝に強制ログアウトする仕様。
ptal/howto.html の記載が確認できるまでは暫定として以下を適用:

- **暫定時刻帯**: JST 4:00〜9:00
- **ログレベル**: 当該時刻帯に `local_app_down` が発生した場合は `ERROR` でなく `INFO` で記録
- **Q-K3 確定後**: 正式な時刻帯に更新し、INV-KABUSAPI-010 を更新

## SCHEMA_MINOR bump 不変条件

`python/engine/schemas.py` の `SCHEMA_MINOR` 変更時は以下を合わせて更新する:

- `engine-client/src/lib.rs` の `SCHEMA_MINOR` 定数
- `test_schema_compat.py` の `VenueError.code` 既存値の venue 横断列挙 assert
- `token_expired` / `local_app_down` の名前空間衝突確認 assert

Rust test: `cargo test -p engine-client schema_minor_kabu`
Python test: `uv run pytest python/tests/test_schema_compat.py`


## order

# 立花注文 不変条件 fragment（INV-ORDER-*）

> このファイルは Wave 4 で `docs/testing/invariants.md` 本体に統合される fragment。
> INV-ORDER-NNN は本再編で付与した正規 ID。`元 ID` 列は移送前の表記を保持し、追跡可能性を確保する。
>
> 管理ポリシー: `docs/specs/order.md §6` の不変条件が増減したら本表を同時に更新すること。
> CI: `python/tests/test_invariant_tests_doc.py` で本ファイルの存在と不変条件 ID の網羅を assert する（旧 invariant-tests.md と同様の運用を継承）。
>
> **Phase 8（2026-05-03 完了）注記**: 表中の `src/api/order_api.rs` は Phase 8 で削除済み。Rust 側ガード（INV-ORDER-005 / INV-ORDER-007 / INV-ORDER-008 等）は HTTP path 専用の防壁だったため Phase 8 で消滅した（GUI 発注は元から HTTP を経由しないため挙動変更なし）。これらのテストはリポジトリ履歴上の「旧 HTTP path レビュー時の記録」として残置。replay モード reject は engine state machine の `EngineBusy` event に置き換わっている。

| INV ID | 元 ID | 説明 | テストファイル | 関数名 | ステータス |
|---|---|---|---|---|---|
| INV-ORDER-001 | A-H2 | `reason_code` は SCREAMING_SNAKE_CASE 固定文字列のみ（specs/order.md §5.2） | `python/tests/test_invariant_reason_code.py` | `test_canonical_codes_are_screaming_snake_case` / `test_all_reason_codes_in_source_are_canonical` / `test_all_reason_codes_in_source_are_screaming_snake_case` | 実装済み (2026-04-28) |
| INV-ORDER-002 | C-H1 | 仮想 URL（sUrlRequest / sUrlEvent / sUrlEventWebSocket）と p_no クエリを WAL・ログ・reason_text に出さない。`mask_virtual_url()` 必須（specs/order.md §3.1 / §3.4） | `python/tests/test_url_masker.py` | `test_mask_virtual_url` | 実装済み (2026-04-28) |
| INV-ORDER-003 | C-H2 | 立花 HTTP リクエストは Shift-JIS + `func_replace_urlecnode` パーセントエンコード必須（specs/order.md §3.0） | `python/tests/test_url_encode_pipeline.py` | `TestFuncReplaceUrlecnode` / `test_build_request_url_uses_tachibana_encoding` / `test_build_request_url_encodes_plus_not_as_space` | 実装済み (2026-04-28) |
| INV-ORDER-004 | C-H3 | 約定通知重複検知キーは `(venue_order_id, trade_id)` タプル。`trade_id` 単独では衝突しうるため `venue_order_id` と組で比較する（specs/order.md §3.3） | `python/tests/test_ec_dedup.py` | `test_dedup_same_key_returns_false_then_true` / `test_dedup_different_venue_order_id_is_independent` / `test_dedup_reset_clears_seen_keys` | 実装済み (2026-04-28) |
| INV-ORDER-005 | C-H4 | `replay_mode == true` のとき全 `/api/order/*` を 503 + `reason_code="REPLAY_MODE_ACTIVE"` で拒否。Rust HTTP 層最前段で判定し Python に到達させない（specs/order.md §3.2） | `src/api/order_api.rs` | `test_submit_order_replay_mode_returns_503` | Phase 8 廃止 (HTTP path 削除。replay reject は engine state machine の EngineBusy event に置換) |
| INV-ORDER-006 | C-M2 | 第二暗証番号は Python メモリのみ保持。アイドル N 分・夜間閉局・仮想 URL refresh のいずれかで自動 forget する（specs/order.md §3.1） | `python/tests/test_tachibana_session_holder.py` | `test_idle_forget_returns_none_after_expiry` / `test_clear_resets_password` | 実装済み (O0) |
| INV-ORDER-007 | C-M3 | 同一 `(instrument_id, order_side, quantity, price)` が N 秒以内に Y 回以上送られたら 429 + `reason_code="RATE_LIMITED"`（specs/order.md §3.2） | `src/api/order_api.rs` | `test_rate_limit_rejects_on_n_plus_1` / `test_rate_limit_resets_after_window` / `test_rate_limit_different_key_independent_counter` | Phase 8 廃止 (HTTP path 削除) |
| INV-ORDER-008 | C-M5 | `p_errno=2` 検知で `OrderSessionState` を frozen 遷移し、以降の全 `/api/order/*` を 503 + `reason_code="SESSION_EXPIRED"` で即時拒否（specs/order.md §3.3） | `src/api/order_api.rs` | `test_submit_after_session_frozen_returns_503` | Phase 8 廃止 (HTTP path 削除) |
| INV-ORDER-009 | C-R2-M3 | `SubmitOrderRequest` / `OrderModifyChange` は `deny_unknown_fields` を付与し、`second_password` / `secondPassword` / `p_no` 等の混入を serde 段で弾く（specs/order.md §10.0） | `engine-client/tests/dto_deny_unknown_fields.rs` | `submit_order_request_rejects_second_password_field` / `submit_order_request_rejects_camelcase_second_password` / `submit_order_request_rejects_p_no_field` / `submit_order_request_rejects_arbitrary_extra_field` / `order_modify_change_rejects_second_password` | 実装済み (Tpre.2) |
| INV-ORDER-010 | C-R2-H2 | 第二暗証番号 idle timer は monotonic clock で計測し、reset trigger は `SetSecondPassword` / `SubmitOrder` 受信時のみに限定する（specs/order.md §5.3） | `python/tests/test_tachibana_session_holder.py` | `test_touch_resets_idle_timer` / `test_touch_updates_last_use_time` | 実装済み (O0) |
| INV-ORDER-011 | C-R2-L1 | EVENT URL 構築時に `\n` / `\t` / `\x01-\x03` 等の制御文字を reject（除去ではなく reject に統一）（specs/order.md §6） | `python/tests/test_event_url_sanitize.py` | `test_build_event_url_rejects_control_char_in_value` / `test_build_event_url_rejects_control_char_in_key` / `test_build_event_url_does_not_silently_strip` | 実装済み (O0) |
| INV-ORDER-012 | C-R5-H2 | `SECOND_PASSWORD_INVALID` が連続 N 回（デフォルト 3 回）で lockout 状態に遷移し、`SubmitOrder` / `ModifyOrder` / `CancelOrder` を 423 + `reason_code="SECOND_PASSWORD_LOCKED"` で reject する（specs/order.md §5.2） | `python/tests/test_second_password_lockout.py` | `test_submit_rejects_when_locked_out` / `test_three_invalid_via_server_triggers_lockout_response` / `test_lockout_expires_after_1800_seconds` | 実装済み (O0) |
| INV-ORDER-013 | B-M4 | `_ACCOUNT_TYPE_MAP` の各キー・値がマニュアル確定値（`"1"`=特定, `"3"`=一般, `"5"`=一般NISA, `"6"`=NISA成長投資枠）と一致し、無効値 `"0"` および旧タグ名が存在しない | `python/tests/test_tachibana_order_mapping.py` | `test_account_type_map_matches_manual` | 実装済み (2026-04-28) |

## 備考

- 上記テスト関数名欄が「TBD」のものは対応テストが未実装。実装時に本表を更新すること。
- `test_invariant_tests_doc.py` は本ファイルに登場する不変条件 ID がすべて `docs/specs/order.md §6` の記述と一致することを assert し、陳腐化したら CI が落ちる運用にする。
- 本表は `docs/roadmap/order/implementation-plan.md` Tpre.6 受け入れ条件 D2-M1 R2 に対応する成果物。
- 旧 ID（`A-H2` / `C-H1` 等）は本フェーズ移送前の論文ID表記。新規参照は INV-ORDER-NNN を優先する。


