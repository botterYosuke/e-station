# 計画レビュー修正ログ（2026-04-25）

レビュー findings に基づき以下のファイルを修正した。

## ラウンド 1（当初レビュー分）

| ファイル | 修正 |
| :--- | :--- |
| `spec.md` | HIGH-C: ボタン配置をサイドバー固定に。HIGH-D/LOW-3: second_password env 削除・手動/自動再ログイン境界を明文化 |
| `architecture.md` | HIGH-D: 採用 env 名を 3 つに絞り SECOND_PASSWORD を Phase 1 不採用と明記 |
| `README.md` | HIGH-D: env 名一覧を修正。MEDIUM-1: TickerListed 架空型参照を削除 |
| `implementation-plan.md` | HIGH-D: T0.2 env 名タスクを修正。HIGH-B: T2 StartupLatch 設計を DI 方式へ書直し。MEDIUM-3: T0.2 受け入れを 2 段（個別/フェーズ）に分離。MEDIUM-4: request_id reject 責務を oneshot index 側に移動。MEDIUM-5: `_ensure_master_loaded` を Lock + Event 組合せに修正。MEDIUM-6: ticker pre-validate regex の Phase 2 拡張注記追加。MEDIUM-7: tickers dict キー欠落 debug ログ規約追加。LOW-1: cache key を `master_<env>_<YYYYMMDD>.jsonl` 形式に。LOW-4: tools/secret_scan.ps1 sibling を同時新設タスクに変更 |
| `data-mapping.md` | MEDIUM-1: TickerListed → `EngineEvent::TickerInfo.tickers[*]` dict 方式に訂正。MEDIUM-2: capabilities の `session_lifetime_seconds: 86400` を削除 |
| `inventory-T0.md` | HIGH-A: FD コードブロッカーに責任者・縮退影響・更新リストを追記。LOW-2: Timeframe serde migration テスト (`exchange/tests/timeframe_state_migration.rs`) を明示追加 |
| `.claude/skills/tachibana/SKILL.md` | LOW-5: L41 `BASE_URL_*` 旧表記を F-L1 方針（Python 1 ファイル限定）に沿って補正 |

## ラウンド 2（2026-04-25 深掘りレビュー分）

| 重要度 | Finding | 対応ファイル | 変更内容 |
| :--- | :--- | :--- | :--- |
| C-1 | `TachibanaSessionWire` の `Deserialize` 矛盾（architecture.md C2 修正が T0.2 に未反映） | `implementation-plan.md` T0.2 | 2 層 DTO 説明文を C2 修正に沿って書き直し。`TachibanaSessionWire` が `Serialize + Deserialize` 両派生と明記。「Deserialize 持たない」旧記述が誤りである旨を注記 |
| H-1 | `zeroize` 導入タスクが implementation-plan に未存在 | `implementation-plan.md` T0.2 | `[ ]` タスク新設（`Zeroizing<String>` 化・`wire_dto_drop_scope.rs` テスト・`SAFETY-LITE` コメント規約）。Stage B ゲートに完了条件を追加 |
| H-2 | `TACHIBANA_DEV_LOGIN_ALLOWED` 伝達経路が未設計 | `architecture.md` §2.1.1 / `implementation-plan.md` T3 | `stdin` 初期 payload に `dev_tachibana_login_allowed: bool` を追加する設計を §2.1.1 に明記。T3 に `[ ]` タスク（Rust 側 `#[cfg(debug_assertions)]` 分岐・Python 側ガード）を追加 |
| H-3 | SKILL.md トリガー条件が Rust `tachibana.rs` を前提とした旧設計 | `SKILL.md` §いつこのスキルを発動するか | Python モジュール名に書き換え（`tachibana_codec.py` 等）。Phase 2 以降の発注系は「Phase 2 以降」と明記 |
| H-4 | `validate_session_on_startup` の確認リクエスト未定義 | `implementation-plan.md` T2 | `CLMMfdsGetIssueDetail`（1 銘柄・`sUrlMaster`）を採用理由付きで明記。T2 実機確認で変更時の更新規約を追加 |
| M-1 | Stage B ゲートに未完了 `[ ]` タスク 4 件が未記載 | `implementation-plan.md` T0.2 Stage B | `request_id` 規約確定・マスタキャッシュ path 確定・`quote_currency` 正規化実装位置確定・`zeroize` 完了・`TachibanaSessionWire` C2 確認を Stage B に追加 |
| M-2 | `StartupLatch.run_once` の `finally` セマンティクス未文書化 | `implementation-plan.md` T2 | finally で `_done=True` になる意図（失敗後も再試行不可）を明記。`login()` は別関数で latch に影響しないことも明記 |
| M-3 | `asyncio.gather` テストの非決定性問題 | `implementation-plan.md` T2 | `return_exceptions=True` で集めて「RuntimeError が 1 件」を assert する形式に変更。特定コルーチンに `pytest.raises` を掛けない旨を明記 |
| M-5 | 呼値テーブル全量が data-mapping に記載されていない | `data-mapping.md` §5 | PDF §2-12 を直接参照する指示と境界値テストの追加を明記 |
| M-6 | ST フレームの「深刻」判定基準未定義 | `implementation-plan.md` T5 | `sResultCode != "0"` を Phase 1 保守的基準として明記し、PDF 確認後に更新する規約を追加 |
| L-1 | SKILL.md S2 の `DEV_TACHIBANA_SECOND_PASSWORD` 行が残存 | `SKILL.md` S2 | `#` コメント行に変換し「Phase 1 不採用、Phase 2 着手時に確定」と注記 |
| L-2 | L2 修正（デモ固定ラベル）が T3 タスクに未記載 | `implementation-plan.md` T3 | `[ ]` タスクとして `tachibana_login_dialog.py` への L2 ラベル実装を追加 |
| L-3 | プロキシ環境での WS 統合テストが計画不足 | `implementation-plan.md` T5 | `test_tachibana_ws_proxy.py` のテスト計画（ローカル CONNECT プロキシ起動方針）を追記 |
| L-4 | `tools/secret_scan_patterns.txt` の形式未定義 | `implementation-plan.md` T7 | ripgrep 正規表現・1 行 1 パターン・`#` コメント形式を明記。両スクリプトの読み込み方法を記載 |

## ラウンド 3（2026-04-25 第 3 巡レビュー分）

| 重要度 | Finding | 対応ファイル | 変更内容 |
| :--- | :--- | :--- | :--- |
| HIGH-A1 | env flag 名 `TACHIBANA_DEV_LOGIN_ALLOWED` の旧名残存（spec.md §3.1 のみ env 風表記） | `spec.md` §3.1（L67） | 「親プロセスから渡す `TACHIBANA_DEV_LOGIN_ALLOWED` 起動 flag」を削除し、「親プロセス（Rust）から `stdin` 初期 payload 内のフィールド `dev_tachibana_login_allowed: bool` として渡す（env 経路ではなく stdin payload で受け取る、architecture.md §2.1.1 H-2 修正と整合）」へ書き換え。env と stdin payload の混同を解消 |
| HIGH-C3 | WebSocket Pong handler のライブラリが Rust 名 `tokio-tungstenite` で誤記、Python `websockets` 用の `ping_interval=None` 指示が欠落 | `spec.md` §3.2（L85） | WS は Python 側 (`tachibana_ws.py`、`websockets` ライブラリ) が担当する旨を明示し、`websockets.connect(..., ping_interval=None, ping_timeout=None)` でライブラリ自動 Ping/Pong を完全に無効化したうえで手動 Pong handler から `Pong` を返す指示に書き直し。architecture.md §4 と整合 |
| HIGH-A2 | SKILL.md 状態ブロックに `DEV_TACHIBANA_SECOND_PASSWORD` env が残存 | `SKILL.md` L18 | env 列挙を 3 つに絞り「Phase 1 不採用、Phase 2 着手時に確定（F-H5）」とコメント注記 |
| HIGH-A3 | SKILL.md S1 節 env リストに `DEV_TACHIBANA_SECOND_PASSWORD` 残存（S2 修正と非対称） | `SKILL.md` S1（L81） | S1 の env 列挙からも SECOND_PASSWORD を除外し 3 つに統一 |
| HIGH-A4 | リスク表が旧コード名 `tachibana_session_expired` を使用 | `implementation-plan.md` L317 リスク表 | `VenueError{code:"session_expired"}` 発出に書き換え |
| HIGH-C1 | R5 `sJsonOfmt="5"` の強制ポイントが計画 T1 に未記載 | `implementation-plan.md` T1 | `build_request_url` で `sJsonOfmt` を必須キーワード引数化（マスタ系 `"4"`、それ以外 `"5"`、省略は `ValueError`）＋テスト追加 |
| HIGH-C2 | R7 Shift-JIS decode を全 REQUEST 経路で必須化する規約が未明記 | `implementation-plan.md` T1 受け入れ | 全 REQUEST レスポンスは `decode_response_body` 経由必須、`.text/.json()` 直叩き禁止＋CI grep ガード |
| HIGH-D1 | release ビルドで env 無視確認テストが具体名なし | `implementation-plan.md` T7/T3 | `python/tests/test_tachibana_dev_env_guard.py` と `engine-client/tests/dev_login_flag_release.rs` を具体名で明記 |
| HIGH-D2 | `validate_session_on_startup` の `CLMMfdsGetIssueDetail` リクエスト固定テスト未明記 | `implementation-plan.md` T2 | `test_validate_session_uses_get_issue_detail_with_pinned_payload` を URL/method/keys 固定で追加 |
| HIGH-D3 | T4 マスタキャッシュ JST 日付境界テストが境界ケース未明記 | `implementation-plan.md` T4 | `test_jst_date_boundary_around_midnight` / `test_cache_invalid_after_jst_rollover` を freezegun で追加 |
| HIGH-D4 | T5 `depth_unavailable` 30 秒タイムアウト ネガティブテスト未明記 | `implementation-plan.md` T5 | `test_depth_safety_does_not_fire_when_keys_arrive_within_30s` ネガティブテスト追加 |
| HIGH-D5 | T5 ザラ場時間境界（9:00/11:30/12:30/15:25/15:30）の単体テスト未明記 | `implementation-plan.md` T5 | `python/tests/test_tachibana_session_window.py` に 7 境界 parametrize テスト追加 |
| HIGH-D6 | T7 secret_scan メタテスト（ダミーリーク検出確認）未明記 | `implementation-plan.md` T7 | `tools/tests/test_secret_scan.sh` / `.ps1` をフィクスチャ付きで追加 |
| MEDIUM-A1 | architecture.md §5 表が `VenueLoginStarted` / `VenueLoginCancelled` / `RequestVenueLogin` を欠落 | `architecture.md` §5 | dto.rs 行に 3 イベント / 1 コマンドを追記し §7.5 と整合 |
| MEDIUM-A2 | SKILL.md 冒頭に `DEV_TACHIBANA_DEMO` 既定値（demo=true）の言及がない | `SKILL.md` L18 / S1 | 既定 `true`（demo、F-Default-Demo）併記 |
| MEDIUM-B1 | architecture.md §3 の `dto.rs:115` 参照が実コード（`Disconnected` は L201）と不一致 | `architecture.md` §3 L214 | 参照を `dto.rs:201`（アンカー `#L201`）に更新 |
| MEDIUM-B2 | `dto.rs:193` 参照が実コード（`EngineEvent::TickerInfo` は L279）と不一致 | `data-mapping.md` L35 / `implementation-plan.md` 全箇所 | `dto.rs:279`（アンカー `#L279`）に一括更新 |
| MEDIUM-C4 | R2 builder 誤用ガード（NewType ラップ）が未設計 | `implementation-plan.md` T1 | URL 型を `RequestUrl`/`MasterUrl`/`EventUrl` で NewType ラップ、各 builder が対応型のみ受理 |
| MEDIUM-C5 | R6 `p_errno` 空文字＝正常テストの明示 | `implementation-plan.md` T1 受け入れ | `check_response` で `p_errno=""` と `"0"` の 2 ケース正常 assert を明記 |
| MEDIUM-C6 | Python 側 `SecretStr` 平文残留トレードオフ未記載 | `implementation-plan.md` T3 | Python `SecretStr` の Drop ゼロ化非保証を明記、subprocess 寿命最小化＋変数長期保持禁止規約 |
| MEDIUM-C7 | マスタダウンロード `sUrlMaster` の型強制が未設計 | `implementation-plan.md` T4 | `MASTER_CLMIDS: frozenset[str]` で `MasterUrl` 強制の型ガード |
| MEDIUM-C8 | `p_sd_date` JST 固定の単一化規約が未明記 | `implementation-plan.md` T1 受け入れ | `datetime.now/time.time` の `current_p_sd_date` 外出現を CI grep ガード |
| MEDIUM-D1 | T6「Python 再起動シナリオ」具体テスト名未指定 | `implementation-plan.md` T6 | `engine-client/tests/process_restart_with_credentials.rs::test_credentials_resent_in_order_after_restart` 順序記録テスト |
| MEDIUM-D2 | T4 `_ensure_master_loaded` 並列呼出テスト未明記 | `implementation-plan.md` T4 | `python/tests/test_tachibana_master_lock.py::test_concurrent_callers_trigger_single_download` 追加 |
| MEDIUM-D3 | T3 `VenueLoginCancelled` 後の手動再ログイン経路 E2E テスト未明記 | `implementation-plan.md` T3 | `tests/e2e/tachibana_relogin_after_cancel.sh` E2E 追加 |
| MEDIUM-D4 | T1 `func_replace_urlecnode` 空文字・ラウンドトリップテスト未明記 | `implementation-plan.md` T1 | `test_replace_urlecnode_empty/full_roundtrip/passthrough_alnum` 追加 |
| MEDIUM-D5 | T5 `SetProxy` + 立花 WS 統合のポジティブパス未明記 | `implementation-plan.md` T5 | `test_ws_connects_through_local_connect_proxy` ポジティブパス追加 |
| MEDIUM-D6 | T5 ST frame `sResultCode == "0"` で停止しないネガティブテスト未明記 | `implementation-plan.md` T5 | `test_st_frame_with_zero_result_code_does_not_stop_subscriptions` 追加 |

## ラウンド 4（2026-04-25 第 4 巡レビュー反映）

| 重要度 | Finding | 対応ファイル | 変更内容 |
| :--- | :--- | :--- | :--- |
| HIGH-B2-1 | stdin payload が format! 手書きで JSON エスケープ事故リスク | `implementation-plan.md` T3 | `serde_json::json!` + `to_string()` 置換サブタスクを `[ ]` 追加。`process_lifecycle.rs` でラウンドトリップテスト |
| HIGH-B2-2 | venue_credentials 重複登録防止が弱い（venue_tag 緊急度上げ） | `implementation-plan.md` M2 | 「先行修正必須」緊急度注記。2 venue 投入で `store.len()` 期待値テスト追加 |
| HIGH-C2-1 | R3 `sKinsyouhouMidokuFlg` テスト名未明記 | `implementation-plan.md` T2 | `test_login_raises_unread_notices_when_kinsyouhou_flag_set` 追加（VenueError code=`unread_notices` 検証） |
| HIGH-C2-2 | secret_scan_patterns.txt の Phase 1 確定パターン未列挙 | `implementation-plan.md` T7 | 5 パターン逐語列挙（`kabuka\.e-shiten\.jp` ほか）、`tachibana_url.py` allowlist 設計を明記 |
| HIGH-D2-1 | tick_size_for_price 境界値テストの T4 紐付け欠落 | `implementation-plan.md` T4 | `test_tick_size_at_price_band_boundaries` を境界値 ±1 銭 parametrize で追加 |
| HIGH-D2-2 | schema_minor 1.2 双方向ラウンドトリップ具体名未指定 | `implementation-plan.md` T0.2 Stage B | `test_schema_compat_v1_2.py` / `schema_v1_2_roundtrip.rs` を 2 方向で明記、7 variant parametrize |
| MEDIUM-A2-1 | `dto.rs:115` 旧行番号残存 | `implementation-plan.md` / `spec.md` | `:201` / `#L201` に統一 |
| MEDIUM-A2-2 | SKILL.md L416 で `tokio-tungstenite` Rust 実装が残存 | `SKILL.md` L416 | 該当文を削除し Python `tachibana_ws.py` 集約に書き換え |
| MEDIUM-A2-3 / MEDIUM-B2-3 | stdin payload schema が T3 と他で不一致 | `implementation-plan.md` T3 | payload 例を 5 フィールドに統一、Python `__main__.py` 同 PR 更新を追記 |
| MEDIUM-B2-1 | テスト名 `process_restart_with_credentials.rs` が既存命名規則と非対称 | `implementation-plan.md` T6 | `process_lifecycle.rs::test_credentials_resent_in_order_after_restart` に変更 |
| MEDIUM-B2-2 | zeroize ゲートと dto.rs 現状の不一致が未明記 | `implementation-plan.md` T0.2 | 現状プレーン `String` でマージ済み、`Zeroizing` の `Serialize` 透過を確認すること追記 |
| MEDIUM-C2-1 | R8 空配列正規化の Phase 1 適用 sCLMID リスト未列挙 | `implementation-plan.md` T1 | `aCLMMfdsMarketPriceData` / 等を表で列挙、`deserialize_tachibana_list` 適用必須を明記 |
| MEDIUM-C2-2 | R9 `urllib.parse.quote` / `urlencode` 禁止 lint 未定義 | `implementation-plan.md` T1 | CI lint で標準 URL encoder 委譲禁止、docstring に同旨明記 |
| MEDIUM-D2-1 | `validate_session` RuntimeError supervisor E2E 未明記 | `implementation-plan.md` T2 | `test_runtime_error_from_validate_terminates_process_with_log` 追加 |
| MEDIUM-D2-2 | 祝日 market_closed 倒し統合テスト未明記 | `implementation-plan.md` T5 | `test_tachibana_holiday_fallback.py` に正/負 2 ケース追加 |

## ラウンド 5（2026-04-25 第 5 巡レビュー反映）

| 重要度 | Finding | 対応ファイル | 変更内容 |
| :--- | :--- | :--- | :--- |
| HIGH-A3-1 | architecture.md §8.5 の secret 流出ガード grep 表現が旧 2 パターン例で不一致 | `architecture.md` §8.5 | grep リテラルを削除し、`tools/secret_scan_patterns.txt` を正本とする旨と `implementation-plan.md T7` 参照に置換 |
| HIGH-C3-1 | WS フレーム本文の Shift-JIS デコード規約が T5 に未明記 | `implementation-plan.md` T5 | WS 受信 bytes は `parse_event_frame` 前に `decode_response_body` 経由必須、`tachibana_ws.py` を CI lint 対象に追加、Shift-JIS 漢字 fixture テスト追加 |
| MEDIUM-C3-1 | `CLMAuthLoginRequest` の `sJsonOfmt` 値が R5 分岐外 | `implementation-plan.md` T2 | `test_login_request_uses_json_ofmt_five` で "5" 選択を pin |
| MEDIUM-C3-2 | `CLMEventDownload` ストリーム終端のエッジケーステスト未明記 | `implementation-plan.md` T1 | `}` 直前/直後/レコード途中の 3 chunk 境界を parametrize 網羅 |
| MEDIUM-C3-3 | `sUrlEventWebSocket` / 他 URL のスキーム検証が T2 未明記 | `implementation-plan.md` T2 | `wss://` / `https://` assert と `LoginError`、`test_login_rejects_non_wss_event_url` 追加 |
| MEDIUM-D3-1 | ログにシークレット非漏洩テストの具体名・対象未指定 | `implementation-plan.md` T6 | `test_tachibana_log_redaction.py::test_runtime_logs_do_not_contain_credentials_or_virtual_urls` 追加 |
| MEDIUM-D3-2 | capabilities による UI 非活性化テスト未明記 | `implementation-plan.md` T6 | `engine-client/tests/capabilities_gate.rs::test_unsupported_timeframes_are_disabled_when_capabilities_received` 追加 |
| MEDIUM-D3-3 | keyring read/write roundtrip + Zeroize テスト未明記 | `implementation-plan.md` T3 | `data/tests/tachibana_keyring_roundtrip.rs::test_credentials_roundtrip_with_zeroize_and_masked_debug` 追加 |
