# 立花 注文計画 レビュー修正ログ

## ラウンド 1（2026-04-25）

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| A-H1 | 文書間整合 | README.md:44 | `Command::CorrectOrder` → `ModifyOrder`、`Event::OrderCanceled` 追加 |
| A-H2 | 文書間整合 | implementation-plan.md:90 周辺 | reason_code を `SECOND_PASSWORD_REQUIRED` に統一、CancelOrder 用法修正 |
| A-H3 | 文書間整合 | implementation-plan.md Tpre.2 / T0.3 | enum variant 凍結を Tpre.2 に集約、T0.3 はディスパッチ実装のみ |
| A-L1 | 文書間整合 | README.md:24 | nautilus 抽象メソッド名 (`modify_order` / `cancel_order` / `cancel_all_orders`) に統一 |
| A-M1 | 文書間整合 | architecture.md §3 / SKILL 整合 | second_password 撤去タイミング双方向リンク |
| A-M2 | 文書間整合 | spec.md §6.1 | OrderType/TimeInForce 列挙数を「IPC 型保持・HTTP は §5 等参照」と注記 |
| A-M3 | 文書間整合 | spec.md §5.2 | reason_code 表に HTTP ステータス列を追加 |
| A-M5 | 文書間整合 | implementation-plan.md Tpre.5 | 実 frame キャプチャは Phase 1 T2 完了後と明示 |
| B-H1 | 既存実装ズレ | architecture.md / implementation-plan.md | `next_p_no()` → `PNoCounter.next()`、`current_p_sd_date()` を `tachibana_helpers` 所在に修正 |
| B-H2 | 既存実装ズレ | implementation-plan.md 冒頭 | 現状確認節を最新化 (`tachibana_login.py` 不在、`tachibana_event.py` 未実装) |
| B-M2 | 既存実装ズレ | architecture.md §8 | flowsurface `place_or_replay` 名称対応表追加 |
| B-M3 | 既存実装ズレ | architecture.md §10.4 | Debug マスクは `second_password` のみ（user_id/password は wire に載らない） |
| B-M4 | 既存実装ズレ | architecture.md §10.4 | `sZyoutoekiKazeiC` をマニュアルで pin 必須に変更 |
| B-M5 | 既存実装ズレ | implementation-plan.md Tpre.5 / T2.1 | `tachibana_event.py` 新規作成・受信ループ含む |
| B-M1 | 既存実装ズレ | implementation-plan.md T0 系 notes | second_password 撤去タイミング双方向リンク |
| C-H1 | 仕様漏れ | spec.md §3.1 / architecture.md §4.2 / §10 | 仮想 URL マスク規約を追加 |
| C-H2 | 仕様漏れ | spec.md §3 / architecture.md §10 | Shift-JIS リクエスト側パイプライン明示 |
| C-H3 | 仕様漏れ | spec.md §3.3 §6 / architecture.md §1 §6 §10 | EC 重複検知キーを `(venue_order_id, trade_id)` に統一 |
| C-H4 | 仕様漏れ | spec.md §3.2 §5.2 | REPLAY ガード `REPLAY_MODE_ACTIVE` を Phase O0 必須に追加 |
| C-M1 | 仕様漏れ | architecture.md §2 | `PNoCounter` の連続性・wall-clock 単調増加を明記 |
| C-M2 | 仕様漏れ | spec.md §3 | 第二暗証番号アイドル forget 規定追加 |
| C-M3 | 仕様漏れ | spec.md §3.2 | 連打抑止 `RATE_LIMITED` 追加 |
| C-M5 | 仕様漏れ | spec.md §3 / architecture.md §2 | session 切れ即停止伝播範囲を明示 |
| C-M6 | 仕様漏れ | architecture.md §3 / §10 | second_password.is_none() の serializer assert 追加 |
| C-L2 | 仕様漏れ | spec.md §3.3 / §2.3 | 立花用語 `p_eda_no` → `trade_id` に統一 |
| C-L4 | 仕様漏れ | architecture.md §4.2 | WAL 制御文字対策追加 |
| D-T0.6 | テスト不足 | implementation-plan.md T0.6 | 誤発注ガード回帰テスト・連打耐性 integration test |
| D-T0.4 | テスト不足 | implementation-plan.md T0.4 | 第二暗証番号 horizontal grep テスト |
| D-T2.3 | テスト不足 | implementation-plan.md T2.5 | EC 重複検知 fault-injection E2E |
| D-session | テスト不足 | implementation-plan.md T1.6 / T0.4 | session 切れ即停止テスト |
| D-Shift-JIS | テスト不足 | implementation-plan.md T0.4 | Shift-JIS ラウンドトリップテスト |
| D-nautilus 境界 | テスト不足 | implementation-plan.md Tpre.6 | 禁止語 grep CI lint |
| D-仮想 URL | テスト不足 | implementation-plan.md T0.4 / T0.6 | caplog で `p_no=` 等が出ないことを assert |
| D-REPLAY | テスト不足 | implementation-plan.md T0.6 | REPLAY モード時の 503 確認テスト |
| D-WAL idempotent | テスト不足 | implementation-plan.md T0.7 / T0.8 | tags 順序違い 2 連投で 1 Created + 1 IdempotentReplay |
| D-cancel-all | テスト不足 | implementation-plan.md T1.3 | confirm body のテーブルテスト化 |
| D-EC state | テスト不足 | implementation-plan.md T2.5 | 状態遷移順序の state-machine assert |
| B-L1 | 既存実装ズレ | architecture.md §4.2 / README.md | REPLAY 引き取り境界を N1 計画と相互参照 |
| B-L2 | 既存実装ズレ | architecture.md §8 | `OrderSubmitted` 即時発火は nautilus 流の新規追加と注記 |
| B-L3 | 既存実装ズレ | architecture.md §6 | EC IPC 名 `trade_id` 固定の方針注記 |
| A-L3 | 文書間整合 | open-questions.md Q9 | schemas ディレクトリ実在確認を Tpre.2 着手前必須に追加 |

## ラウンド 2（2026-04-25）

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| A-H1 R2 | 文書間整合 | spec.md §1 / architecture.md §1 §2.1 §2.2 §4 / implementation-plan.md T0.5 | `order_number` を全箇所 `venue_order_id` に置換、`update_order_number` → `update_venue_order_id` |
| A-H2 R2 | 文書間整合 | README.md:27 | `next_p_no()` → `tachibana_helpers.PNoCounter.next()`、所在を `tachibana_helpers` に修正 |
| A-M1 R2 | 文書間整合 | architecture.md:40 / §8 対応表 | `submit_new_order` → `submit_order` に統一 |
| A-M2 R2 | 文書間整合 | implementation-plan.md:223 T1.1 | `submit_modify_order` / `submit_cancel_all` 等を `modify_order` / `cancel_all_orders` に統一 |
| A-M3 R2 | 文書間整合 | implementation-plan.md T1.1 / architecture.md §8 | wire 型名 prefix を `TachibanaWire*` に統一 |
| A-M4 R2 | 文書間整合 | architecture.md:262 | `OrderFilled.trade_id` のコメントから立花 `p_eda_no` 用語を除去 |
| A-L4 R2 | 文書間整合 | README.md:9 | `spec.md §2.5` 参照を `spec.md §3.2` に修正 |
| B2-H2 R2 | 既存実装ズレ | architecture.md §8 対応表 | `PlaceOrderOutcome::Created` の意味反転（flowsurface = venue 採番、本計画 = client 採番）を対応表に明記 |
| B2-M1 R2 | 既存実装ズレ | architecture.md:493-494 | 移植元参照を `agent_session_state.rs::AgentSessionState` に修正 |
| B2-M2 R2 | 既存実装ズレ | architecture.md §8 / implementation-plan.md T0.5 | `try_insert` を 3 引数（事前採番 UUID 渡し）に統一 |
| B2-L1 R2 | 既存実装ズレ | nautilus_trader/spec.md §2.3（注記） | order/implementation-plan への逆リンクをコメントとして言及（実反映は N2 タスクの責務） |
| B2-L2 R2 | 既存実装ズレ | implementation-plan.md T0.4 | `peek()` をリクエスト経路で呼ばない CI grep を追加 |
| C-R2-H1 R2 | 仕様漏れ | architecture.md §7 / implementation-plan.md T0.6 | `[tachibana.order]` config に `rate_limit_window_secs=3` / `rate_limit_max_hits=2` を追加 |
| C-R2-H2 R2 | 仕様漏れ | architecture.md §7 §5.3 / implementation-plan.md T0.4 | `second_password_idle_forget_minutes=30` を config 化、`tachibana_session_holder` の idle timer を起票 |
| C-R2-M1 R2 | 仕様漏れ | spec.md §4 表 | `UNSUPPORTED_IN_PHASE_O0` の発火条件 set を脚注追加 |
| C-R2-M2 R2 | 仕様漏れ | spec.md §6.2 状態遷移図 | `SUBMITTED → REJECTED` ブランチを追加 |
| C-R2-M3 R2 | 仕様漏れ | architecture.md §10.0 | `SubmitOrderRequest` / `OrderModifyChange` に `deny_unknown_fields` + serde で第二暗証番号 fields の混入を弾く規約 |
| C-R2-L1 R2 | 仕様漏れ | architecture.md §6 / implementation-plan.md T2.1 | EVENT URL 構築時の制御文字（`\n`/`\t`/`\x01-\x03`）除去規約 |
| C-R2-L2 R2 | 仕様漏れ | spec.md §3.4 | `client_order_id` 1-36 ASCII printable の根拠を nautilus `ClientOrderId` ソース脚注に明示 |
| C-R2-L3 R2 | 仕様漏れ | architecture.md §4.2 | `p_errno=""` 正常時は WAL に rejected 行を書かない注記 |
| D2-H1 R2 | テスト不足 | implementation-plan.md 横断タスク | nautilus 互換境界 lint を CI ゲート（`.github/workflows/*.yml`）に組込明記 |
| D2-H2 R2 | テスト不足 | implementation-plan.md T0.7 | `python/tests/test_audit_log_no_secret.py` で WAL に第二暗証番号値が出ないことを assert |
| D2-M1 R2 | テスト不足 | implementation-plan.md Tpre.6 | `docs/plan/order/invariant-tests.md` に不変条件 ID ↔ test 関数名の対応表を作成 |
| D2-M2 R2 | テスト不足 | implementation-plan.md T0.6 | `cargo test --test order_rate_limit`（N+1 連打で 429 + `RATE_LIMITED`） |
| D2-M3 R2 | テスト不足 | implementation-plan.md T0.3 | `python/tests/test_second_password_idle_forget.py` |
| D2-M4 R2 | テスト不足 | implementation-plan.md T0.4 / T0.7 | `python/tests/test_p_no_counter_monotonic.py`（再起動・time freeze） |
| D2-M5 R2 | テスト不足 | implementation-plan.md Tpre.2 | `cargo test --test creds_no_second_password_on_wire` |
| D2-L1 R2 | テスト不足 | implementation-plan.md T0.4 | `python/tests/test_url_masker.py`（マスクヘルパ単体） |
| D2-L2 R2 | テスト不足 | implementation-plan.md T0.7 | `cargo test --test request_key_canonical`（tags 順序 / null↔"" / 制御文字） |
| D2-L3 R2 | テスト不足 | implementation-plan.md T2.5 | `uv run pytest python/tests/test_ec_state_machine.py -v` 観測点を明記 |

## ラウンド 3（2026-04-25）

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| A-R3-H1 R3 | 文書間整合 | architecture.md §2.1 / §2 | `PNoCounter` 所在を `tachibana_helpers` に統一 |
| A-R3-H2 R3 | 文書間整合 | spec.md §3.2 / §5.2 | `RATE_LIMITED` HTTP ステータスを 429 に統一（RFC 6585） |
| A-R3-M1 R3 | 文書間整合 | architecture.md §8 対応表 | `submit_new_order` 等の旧名を新名に置換 |
| A-R3-M2 R3 | 文書間整合 | spec.md §6.5 | `order_number` 禁止を脚注で過去経緯化 |
| A-R3-L1/L2 R3 | 文書間整合 | architecture.md §10 / §5.3 | `spec.md §6` 参照を §6.5 / §6.1 に細分化 |
| B3R3-1 R3 | 既存実装ズレ | architecture.md §5.3 §7 / implementation-plan.md T0.4 | `TachibanaSessionHolder` を `tachibana_auth.py` に新規追加と所在を明記 |
| B3R3-2 R3 | 既存実装ズレ | architecture.md §2 §2.1 / implementation-plan.md T0.4 | `PNoCounter` を Python `int`、初期値 Unix 秒（×1000 削除）、AtomicU64 表現撤回 |
| B3R3-3 R3 | 既存実装ズレ | architecture.md §8 対応表 | `PlaceOrderOutcome::Created` は意味反転ではなく rename と訂正 |
| B3R3-4 R3 | 既存実装ズレ | implementation-plan.md T0 系 | Phase 1 `second_password` ガード解除タスクを起票 |
| B3R3-5 R3 | 既存実装ズレ | open-questions.md | nautilus_trader 相互リンク整備を Tpre.1 で確定 |
| C1 R3 | 仕様漏れ | spec.md §6.1 §5.2 / architecture.md §3 | `TriggerType` Phase O0/O1 で null 必須、O3 まで LAST、他は `VENUE_UNSUPPORTED` |
| C2 R3 | 仕様漏れ | spec.md §6.2 §5.2 / architecture.md §6 | `SUBMITTED → REJECTED` の reason_code set 明示、`SECOND_PASSWORD_INVALID` / `VENUE_REJECTED` 追加 |
| C3 R3 | 仕様漏れ | architecture.md §5.3 | idle timer の monotonic / reset trigger / suspend resume 規約追記 |
| C4 R3 | 仕様漏れ | architecture.md §6 | EVENT URL sanitize を `tachibana_url.build_event_url` 内で reject に統一 |
| C5 R3 | 仕様漏れ | architecture.md §10.2 / implementation-plan.md | 営業日カレンダーは `CLMDateZyouhou` マスタ依存、未取得時 503 + `INTERNAL_ERROR` |
| C6 R3 | 仕様漏れ | architecture.md §3 | `ts_event_ms` コメントを Unix ms (UTC epoch) と明確化 |
| C8 R3 | 仕様漏れ | spec.md §5.2 脚注 / implementation-plan.md T0.3 | `order_side != BUY` / `post_only != false` / `reduce_only != false` を `UNSUPPORTED_IN_PHASE_O0` 条件に追加 |
| D3-1 R3 | テスト不足 | implementation-plan.md Tpre.2 | `cargo test --test dto_deny_unknown_fields` |
| D3-2 R3 | テスト不足 | implementation-plan.md T0.3 | `pytest test_unsupported_phase_o0.py` を 3 条件 × 境界 + 1 でパラメタライズ |
| D3-3 R3 | テスト不足 | implementation-plan.md T0.4 / T0.8 | `pytest test_submitted_to_rejected_immediate.py`（`p_errno=2` 即時 reject 経路） |
| D3-4 R3 | テスト不足 | implementation-plan.md T0.6 | `RATE_LIMITED` 境界テストを 4 ケースにパラメタライズ |
| D3-5 R3 | テスト不足 | implementation-plan.md 横断 | `pytest test_invariant_tests_doc.py`（不変条件表の存在＆紐付け検査） |

## ラウンド 4（2026-04-25）

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| A-R4-H1 R4 | 文書間整合 | architecture.md | spec.md §6.x アンカー死活を実見出しに合わせて修正（§6.2 イベントタクソノミー / §6.1 用語・型の整合 / §6.5 禁止事項） |
| A-R4-M1 R4 | 文書間整合 | architecture.md §8 対応表 | `submit_order` 戻り値型を `SubmitOrderResult` に統一、`OrderRejectedError` は別経路と注記 |
| A-R4-M2 R4 | 文書間整合 | architecture.md §5.3 周辺 | `TachibanaSessionHolder` 追加フェーズ表記を「Phase O0（T0.4）」に修正 |
| B1 R4 | 既存実装ズレ | implementation-plan.md T0.4（B3R3-4） | `with_second_password` builder default、`TachibanaCredentials::new` 引数追加禁止 |
| B2 R4 | 既存実装ズレ | implementation-plan.md | `data/src/config/tachibana.rs:374-380` 行番号参照をシンボル名参照に置換 |
| B3 R4 | 既存実装ズレ | implementation-plan.md T0.4（B3R3-4） | `set_second_password_for_test` 撤去 or `with_second_password` テスト転用 |
| B5 R4 | 既存実装ズレ | architecture.md §5.3 / §2.4 | `StartupLatch` と `TachibanaSessionHolder` を並列フィールドとして保持、相互依存しない |
| C-R4-M1 R4 | 仕様漏れ | architecture.md §10.2 | `trigger_type` 逆写像規約（Phase O0/O1 null、O2/O3 LAST 固定）追加 |
| C-R4-M2 R4 | 仕様漏れ | architecture.md §10.0 / §2.1 | SJIS パイプラインで JSON 構造文字 `{}":,` は非エンコード、値部分のみ 30 文字置換 |
| C-R4-L1 R4 | 仕様漏れ | architecture.md §2.2 | タイムアウト後 `sOrderNumber` の WAL `accepted` のみ書き込み、再起動時 WAL 復元で同期 |
| C-R4-L2 R4 | 仕様漏れ | architecture.md §5.3 | idle timer reset trigger を Modify/Cancel/CancelAll に拡張 |
| C-R4-L3 R4 | 仕様漏れ | architecture.md §4.2 | WAL fsync 失敗時は 500 + `INTERNAL_ERROR` で reject、WAL に書けない発注は送信しない |
| D4-1 R4 | テスト不足 | implementation-plan.md T0.3 | `trigger_type != null` dispatch 実装タスク化 |
| D4-2 R4 | テスト不足 | implementation-plan.md T0.4 | `VENUE_UNSUPPORTED` 写像テスト追加 |
| D4-3 R4 | テスト不足 | implementation-plan.md T0.4（B3R3-4） | Phase 1 ガード解除後ポジティブテスト `tachibana_credentials_wire_strips_second_password` |
| D4-4 R4 | テスト不足 | implementation-plan.md T2.1 | `pytest test_event_url_sanitize.py`（reject 前提を明示） |

## ラウンド 5（2026-04-25）

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| C-R5-H1 R5 | 仕様漏れ | architecture.md §4.2 / spec.md §3 | WAL partial 行（末尾改行欠落）は復元時スキップ + WARN ログ |
| C-R5-H2 R5 | 仕様漏れ | architecture.md §5.3 §7 / spec.md §5.2 | `SECOND_PASSWORD_INVALID` 連続 3 回で 30 分 lockout、`SECOND_PASSWORD_LOCKED \| 423` 追加 |

## ラウンド 6（2026-04-25）

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| HIGH-R6-B1 R6 | 既存実装ズレ | implementation-plan.md T0.4 | lockout state 実装タスク + dispatch 423 reject + WAL truncation 復元タスク追加 |
| HIGH-R6-D1 R6 | テスト不足 | implementation-plan.md T0.4 | `test_second_password_lockout.py` 追加（3 連投で 423、1800 秒で解除） |
| HIGH-R6-D2 R6 | テスト不足 | implementation-plan.md T0.7 | `wal_restore_truncated_line` cargo test 追加 |
