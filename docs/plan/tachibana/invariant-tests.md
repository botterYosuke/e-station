# 不変条件 ↔ テスト対応表（単一正本）

**目的**: 立花証券アダプター実装の各不変条件 ID（spec.md / SKILL.md / data-mapping.md / open-questions.md 由来）と、それを CI で pin するテスト関数名を 1:1（または 1:n）で対応付ける**単一正本ファイル**。本表が存在しない不変条件 ID は「未対応」と扱い、CI grep ガード `test_invariant_table_covers_all_ids` により**未対応 ID = 0** を収束条件として保証する。

**更新規約**:
1. 不変条件を追加・改廃したら、同 PR で必ず本表を更新する。
2. テスト関数を rename する PR では、本表の同行も同時に更新する（ドリフト防止）。
3. `pin する test ファイル::関数名` 列が空欄 / TBD のまま `Tx` 列の対応タスクが `[x]` 化されることは禁止。
4. `Tx タスク` 列は `implementation-plan.md` のタスク ID（T0/T1/T3/T5/T7 等）を参照する。

**CI ガード仕様（`test_invariant_table_covers_all_ids`）**:
- 本ファイルを Markdown 表としてパースし、`不変条件 ID` 列を抽出する。
- spec.md / SKILL.md / data-mapping.md / open-questions.md / review-fixes-*.md を grep し、`F-[A-Z0-9-]+` および `R[0-9]+` パターンの ID 一覧を生成する。
- ソース側 ID 集合 ⊖ 本表 ID 集合 ＝ ∅ を assert する（差分が出たら CI 失敗）。
- 加えて `pin する test ファイル::関数名` 列が空 / `TBD` のまま残る行があれば warning（`Tx` 列が `[x]` 状態なら error）。

---

## 表

| 不変条件 ID | 一次資料節 | pin する test ファイル::関数名 | Tx タスク |
| :--- | :--- | :--- | :--- |
| F-H5 | spec.md §2.2 / architecture.md §7.4 | `data/tests/tachibana_second_password_guard.rs::test_phase1_second_password_guard_panics_in_debug` | T3 |
| F-B1 | architecture.md §7.2 / data-mapping.md §2 | `data/tests/tachibana_dto_secrecy.rs::test_credentials_roundtrip_with_zeroize_and_masked_debug` | T3 |
| F-B2 | architecture.md §7.2 | `data/tests/tachibana_wire_dto.rs::test_wire_dto_serialize_derives_present` | T3 |
| F-L1 | SKILL.md R1 / spec.md §3.2 | `python/tests/test_tachibana_url_single_source.rs::test_base_url_literal_appears_only_in_tachibana_url_py` | T1 |
| F-L5 | SKILL.md R1 補遺 | TBD（Tx で確定） | T1 |
| F-M4 | data-mapping.md §4 | TBD（Tx で確定） | T5 |
| F-M4b | data-mapping.md §4 注記 | TBD（Tx で確定） | T5 |
| F-M5a | data-mapping.md §5 | TBD（Tx で確定） | T5 |
| F-M6a | data-mapping.md §6 | `python/tests/test_tachibana_event_url.py::test_login_rejects_non_wss_event_url` | T5 |
| F-M8 | data-mapping.md §8 | TBD（Tx で確定） | T5 |
| F-H1 | spec.md §2.1 | `data/tests/tachibana_session_validate.rs::test_validate_session_uses_get_issue_detail_with_pinned_payload` | T3 |
| F-H2 | spec.md §2.1 / architecture.md §7.4 | `data/tests/tachibana_runtime_error.rs::test_runtime_error_from_validate_terminates_process_with_log` | T3 |
| F-H6 | spec.md §2.2 | `data/tests/tachibana_login_flow.rs::test_login_raises_unread_notices_when_kinsyouhou_flag_set` | T3 |
| F-Default-Demo | spec.md §3.1 / open-questions.md Q21 | TBD（Tx で確定） | T2 |
| F-Banner1 | spec.md §3.3 | TBD（Tx で確定） | T7 |
| F-Login1 | spec.md §2.2 / architecture.md §7.4 | `data/tests/tachibana_login_flow.rs::test_login_request_uses_json_ofmt_five` | T3 |
| SKILL R1 | SKILL.md R1（実弾保護 / Demo 既定） | TBD（Tx で確定） | T2 |
| SKILL R2 | SKILL.md R2（EVENT URL wss 強制） | `python/tests/test_tachibana_event_url.py::test_login_rejects_non_wss_event_url` | T5 |
| SKILL R3 | SKILL.md R3（永続化禁止対象） | `data/tests/tachibana_log_redaction.rs::test_runtime_logs_do_not_contain_credentials_or_virtual_urls` | T3 |
| SKILL R4 | SKILL.md R4（p_no 採番） | `python/tests/test_tachibana_pno_counter.py::test_pno_monotonic_under_concurrency` | T3 |
| SKILL R5 | SKILL.md R5（sJsonOfmt=5） | `data/tests/tachibana_login_flow.rs::test_login_request_uses_json_ofmt_five` | T3 |
| SKILL R6 | SKILL.md R6（業務エラー判定 / sResultCode=0 で subscription 維持） | `python/tests/test_tachibana_st_frame.py::test_st_frame_with_zero_result_code_does_not_stop_subscriptions` | T5 |
| SKILL R7 | SKILL.md R7（Shift-JIS 入出力） | `python/tests/test_tachibana_encoding.py::test_shift_jis_request_response_pipeline` | T1 |
| SKILL R8 | SKILL.md R8（マスタファイル運用） | TBD（Tx で確定） | T4 |
| SKILL R9 | SKILL.md R9（URL エンコード規約） | `python/tests/test_tachibana_urlencode.py::test_replace_urlecnode_empty` | T5 |
| SKILL R10 | SKILL.md R10（仮想 URL 秘匿） | `data/tests/tachibana_log_redaction.rs::test_runtime_logs_do_not_contain_credentials_or_virtual_urls` | T3 |
| F-Process-Restart | architecture.md §7.4 / spec.md §3.1 | `engine-client/tests/process_lifecycle.rs::test_credentials_resent_in_order_after_restart` | T3 |
| HIGH-U-9 | implementation-plan.md T4（Rust 側 `TickerInfo` 受信マッピング配線、Q16） | `engine-client/tests/ticker_info_tachibana_mapping.rs::test_tachibana_ticker_info_carries_display_name_ja_and_lot_size` | T4 |
| HIGH-U-10a | implementation-plan.md T4（マスタ invalidation: `is_demo` 切替） | `python/tests/test_tachibana_master_invalidation.py::test_master_reloaded_when_is_demo_flips` | T4 |
| HIGH-U-10b | implementation-plan.md T4（マスタ invalidation: JST 日跨ぎ） | `python/tests/test_tachibana_master_invalidation.py::test_master_reloaded_after_jst_rollover_in_running_process` | T4 |
| HIGH-U-10c | implementation-plan.md T4（マスタ invalidation: `__init__` 再生成） | `python/tests/test_tachibana_master_invalidation.py::test_master_event_is_fresh_per_worker_init` | T4 |
| HIGH-U-11p | implementation-plan.md T4（非 `"1d"` kline 拒否 / Python 側） | `python/tests/test_tachibana_fetch_klines_reject.py::test_fetch_klines_rejects_non_d1_timeframes` | T4 |
| HIGH-U-11r | implementation-plan.md T4（非 `"1d"` kline 拒否 / Rust 復元 fail-safe） | `engine-client/tests/tachibana_kline_capability_gate.rs::test_restored_pane_with_non_d1_timeframe_does_not_crash` | T4 |
| HIGH-D2-1-B1a | data-mapping.md §5.2 / implementation-plan.md T4 B1（`CLMYobine` decoder 20 スロット読出し） | `python/tests/test_tachibana_yobine.py::test_clm_yobine_decoder_collects_20_bands` | T4 |
| HIGH-D2-1-B1b | data-mapping.md §5.2（`999999999` sentinel truncate） | `python/tests/test_tachibana_yobine.py::test_clm_yobine_decoder_truncates_at_999999999_sentinel` | T4 |
| HIGH-D2-1-B1c | data-mapping.md §5.3（`tick_size_for_price` 代表 yobine_code 境界値 ±1 銭） | `python/tests/test_tachibana_yobine.py::test_tick_size_for_price_uses_first_band_le_price` | T4 |
| HIGH-D2-1-B1d | data-mapping.md §5.3（未知 `yobine_code` で `KeyError`） | `python/tests/test_tachibana_yobine.py::test_tick_size_for_price_unknown_yobine_code_raises_keyerror` | T4 |
| HIGH-D2-1-B1e | data-mapping.md §5.3（`price` は `Decimal` 限定、int/float 拒否） | `python/tests/test_tachibana_yobine.py::test_tick_size_for_price_decimal_only` | T4 |
| HIGH-D2-1-B2a | data-mapping.md §5.4 / implementation-plan.md T4 B2（銘柄→ yobine_code → tick 解決） | `python/tests/test_tachibana_master_yobine_resolve.py::test_resolve_tick_size_for_issue_uses_clm_yobine_lookup` | T4 |
| HIGH-D2-1-B2b | implementation-plan.md T4 B2（`yobine_table` invalidation: is_demo / JST / `__init__`） | `python/tests/test_tachibana_master_yobine_invalidation.py::test_yobine_table_reloaded_on_invalidation_triggers` | T4 |

---

## 未対応 ID（収束条件）

CI ガードが検知した未対応 ID は本セクションに自動列挙される（PR 着地までに 0 行に縮退させる）。

- （現在: 0 件 / TBD は対応 Tx タスク内で確定）
