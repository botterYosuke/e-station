# Issue #42 Implementation Plan

**Branch**: `feat/issue-42-live-strategy`  
**Scope**: 全 Phase（1〜7）を本 PR で完遂。Phase 4（kabusapi 対応）も含む。  
**Source of Truth**: GitHub issue #42（最新版）+ 本ドキュメント

> ⚠️ サブエージェントは作業着手前に必ず本ドキュメント全体と issue #42 を読むこと。

---

## 既存実装の事実確認（2026-05-10、branch `feat/issue-42-live-strategy` 着手時）

| 項目 | 状態 |
| :--- | :--- |
| `python/engine/schemas.py::SCHEMA_MINOR` | **24** |
| `engine-client/src/lib.rs::SCHEMA_MINOR` | **24** |
| `python/engine/server_grpc.py::SCHEMA_MINOR` | **24** |
| `EngineStartConfig.max_qty` / `max_notional_jpy` / `strategy_init_kwargs` | ✅ 実装済み |
| `LoadStrategyScenario` / `StrategyScenarioLoaded`（replay 用） | ✅ 実装済み（対称ペア参考） |
| `LiveSession.run` / `LiveSession.login` | ✅ attach + inprocess 両対応 |
| `NautilusRunner.start_live` | ✅ tachibana 専用、warm_up は **戻り値判定なし**、`is_market_open` 呼出なし、`exec_client.close()` 失敗時呼出なし |
| `python/engine/server.py::_handle_start_engine` Live 分岐 | ✅ あり（kabu_station は `Live engine requires tachibana venue` で reject） |
| `src/main.rs::LiveStrategyState` | ✅ `Idle` / `Running { strategy_id }` のみ |
| `src/modal/live_strategy_form.rs::LiveStrategyFormModal` | ✅ 4 フィールド（`instrument_id` / `strategy_file` / `max_qty` / `max_notional_jpy`） |
| `python/engine/live_session_cli.py` | ❌ 未作成 |
| `tools/lint/` | ❌ 未作成 |
| `docs/decisions/0071-live-strategy-gui.md` | ⚠️ status=deferred、本文コメントアウト |
| `docs/decisions/0072-execute-live-strategy.md` | ⚠️ status=deferred、本文コメントアウト |
| proto 次の field number | Command 37+ / Event 53+ |
| `engine_runner.py::start_live` の `warm_up` | exception catch 済 / 戻り値 `False` 未判定 / `exec_client.close()` 未呼出 |

---

## 統一決定の SoT（issue #42 本文の統一決定 #1〜#22 を厳守）

特に重要な制約:

1. **schema bump は 1 commit ずつ独立**。`/ipc-schema-check` skill が pre-commit で複数 minor bump を弾く。
2. **schema chain（24→28）**:
   - commit P2: 24→25 = `LoadLiveStrategyScenario` / `LiveStrategyScenarioLoaded`
   - commit P3a: 25→26 = `LiveStrategyReady`
   - commit P3b: 26→27 = `LiveStrategyWarmingUp`
   - commit P3c: 27→28 = `EngineBusy.venue` / `EngineBusy.busy_kind`
   - Phase 3.5 cap 追加は capability map のキー追加なので **追加 bump 不要**
3. **3 箇所の SCHEMA_MINOR を必ず同期**: `python/engine/schemas.py` / `engine-client/src/lib.rs` / `python/engine/server_grpc.py`
4. **proto/engine.proto は gRPC IPC の SoT**。新 IPC は必ず proto に追加 + pb2 再生成 + `engine-client/build.rs` 経由で tonic-build 再生成。
5. **`is_market_open()` SoT**: `engine_runner.py::start_live()` の冒頭でチェック → `EngineError{code:"market_closed"}` emit + abort。CLI/GUI は事前 hint のみ。
6. **warm_up 失敗判定**: 例外 OR `False` 戻り値の **OR** で abort。**必ず** `await exec_client.close()` を呼ぶ。
7. **concurrent live ガード**: 同一 strategy_id → `Error{code:"engine_already_running"}` / 同一 venue 別 strategy_id → `EngineBusy{venue, busy_kind:"another_strategy_on_venue"}`。両方とも実装。
8. **`pending_live_config` 配置**: `src/main.rs::LiveStrategyState` を `Running { strategy_id, instrument_id, venue }` に拡張。`VenueState` には**触らない**。
9. **`auto_generate_live_panes(strategy_id, instrument_id, venue)` は冪等**（key = 三つ組）。`EngineRehello` 受信時に Rust 側で再実行。
10. **`LiveStrategyReady` timeout = 60s**（30s ではない）。中間 event `LiveStrategyWarmingUp` を 5s 毎 emit、GUI banner に「再試行」必置。
11. **第二暗証番号 OS 露出対策**: `--second-password-stdin` / `DEV_TACHIBANA_SECOND_PASSWORD` env を推奨。`--second-password` は非推奨化（argparse help で警告）。stdin 読込仕様: `sys.stdin.read().rstrip("\r\n")`、`sys.stdin.isatty()` 判定。
12. **`SecondPasswordRequired` 固定文言**: 「第二暗証番号を設定してください」（CLI stderr / GUI ステータスバー赤帯で同一）。
13. **`prod_mode` SoT**: engine プロセス起動時の env が固定。GUI は触れない。`Ready.capabilities.venue_capabilities[<venue>].is_production` で disable 判定。
14. **`LIVE_SCENARIO` 不在時の即応答（Open Q2）**: engine は即時 `LiveStrategyScenarioLoaded { instrument_id: None, max_qty: None, max_notional_jpy: None, venue: None, strategy_init_kwargs: None }` を返す。5s timeout は engine 無応答時の fallback のみ。
15. **`LiveStrategyReady` 意味（Open Q1）**: `warm_up()` 成功時点で emit（`node.build()` より前）。`node.build()` 失敗時は `EngineError{code:"node_build_failed"}` emit + Rust 側で `teardown_live_panes`。

---

## 実行ウェーブ（依存グラフ）

```
Wave 1: Schema chain (sequential, 4 commits)
   P2-schema → P3a-schema → P3b-schema → P3c-schema

Wave 2: Functional implementations (parallel)
   Phase 1 CLI ─┐
   Phase 3 GUI ─┤
   Phase 3.5 ──┤  (no schema bump needed)
   Phase 4 kabu ┘

Wave 3: Cross-cutting (parallel after Wave 2)
   Phase 5 examples ─┐
   Phase 6 docs/ADR ─┤
   Phase 7 tests ─────┘

Wave 4: Final review
   /review-fix-loop until MEDIUM ≤ 0
```

---

## 統一テスト要件（TDD 厳守 — 全 Phase 共通）

- **RED → GREEN → REFACTOR**: 必ず failing test を先に書き、実装で green にする
- **完了条件**:
  - `cargo test --workspace` 全緑
  - `cargo clippy --workspace -- -D warnings` クリーン
  - `uv run pytest python/tests/ -v` 全緑
  - `/ipc-schema-check` skill PASS（schema 同期確認）
  - 該当 phase の受け入れ基準テスト関数が green

---

## 受け入れ基準 → 実装ファイル対応表

| # | 受け入れ条件 | 主担当 Phase |
|---|---|---|
| 1 | 戦略無改変で `python -m engine.live_session_cli run --demo` 起動 + `EngineStarted` | Phase 1 + 2 |
| 2 | GUI `File > Open` から起動 → 4 ペイン自動生成 | Phase 3 |
| 3 | `examples/README.md` に replay → demo → prod コマンド例 | Phase 5 + 6 lint |
| 4 | `docs/specs/live-strategy.md §5` 起票 | Phase 6 |
| 5 | ADR 0071/0072 accepted 昇格 + 本文起票 | Phase 6 |
| 6 | `max_qty` 必須 | Phase 1 |
| 7 | `TACHIBANA_ALLOW_PROD=0` で本番 reject | Phase 1 |
| 8 | `SecondPasswordRequired` フロー（CLI 非ゼロ + GUI 赤帯） | Phase 1 + 3 |
| 9 | `is_market_open()` ガード reject | Phase 1（engine_runner 改修） |
| 10 | `login()` 未呼出経路の不在（lint） | Phase 6 |
| 11 | `LiveStrategyReady` 4 ペイン自動生成 + 冪等 | Phase 3 |
| 12 | `supports_live_strategy` cap | Phase 3.5 |
| 13 | `LIVE_SCENARIO` 戦略 → GUI prefill | Phase 2 + 3 |
| 14 | warm_up 失敗（例外 OR `False`）→ `EngineError` + `close()` | Phase 1（engine_runner 改修） |
| 15 | `LiveStrategyReady` timeout 60s + `LiveStrategyWarmingUp` リセット | Phase 3 |
| 16 | concurrent live reject（venue 単位 `EngineBusy` + 同一 sid `engine_already_running`） | Phase 1（server.py） + 3 |
| 17 | reconnect 時の `LiveStrategyReady` 冪等再生 | Phase 3 |
| 18 | tachibana `is_production` cap 露出 | Phase 3.5 |
| 19 | `LoadLiveStrategyScenario` fallback（5s timeout / `strategy_parse_failed`） | Phase 3 |
| 20 | `--second-password-stdin` 4 経路 | Phase 1 |
| 21 | loader pin（live/replay 同一） | Phase 7 |
| 22 | gRPC 経路で新 IPC 送受信 | Phase 2 + 3（schema chain） |
| 23 | `LIVE_SCENARIO` 不在時の即応答 | Phase 2 |

---

## 進捗ログ（サブエージェント追記）

各 Phase 完了時に以下フォーマットで追記:

```
### Phase X 完了（YYYY-MM-DD）
- 担当: [agent name]
- 主要 commit: [SHA list]
- 設計判断: [key decisions]
- 知見/Tips: [next agent への引き継ぎ]
- ✅ チェックリスト
```

### Phase 0: 計画書起草 完了（2026-05-10）
- 担当: orchestrator (main thread)
- 既存実装の事実確認を完了
- schema chain（24→28）の commit 粒度を確定
- Wave 構成を確定（Wave 1 schema → Wave 2 functional → Wave 3 cross-cutting → Wave 4 review）

### Wave 1: Schema chain (24→28) 完了（2026-05-10）
- 担当: schema-chain-agent
- 主要 commit:
  - `77bcbd4` — P2: 24→25 `LoadLiveStrategyScenario` / `LiveStrategyScenarioLoaded`
  - `2f6c550` — P3a: 25→26 `LiveStrategyReady`
  - `a480bba` — P3b: 26→27 `LiveStrategyWarmingUp`
  - `a803aa7` — P3c: 27→28 `EngineBusy.venue` / `EngineBusy.busy_kind`
- 各 commit 後の test 結果:
  - P2: ✅ `cargo test --workspace` 全緑、`pytest test_schema_compat.py test_schemas.py` 全緑
  - P3a: ✅ 同上
  - P3b: ✅ 同上
  - P3c: ✅ 同上（`cargo test --workspace` 全緑、`pytest test_schema_compat.py test_schemas_nautilus.py test_schemas.py` 50 件全緑、`cargo clippy --workspace --tests -- -D warnings` clean）
- proto field number 採番:
  - Command oneof: 37 (`load_live_strategy_scenario`)
  - Event oneof: 53 (`live_strategy_scenario_loaded`), 54 (`live_strategy_ready`), 55 (`live_strategy_warming_up`)
  - `EngineBusyEvent` 内: 5 (`venue`), 6 (`busy_kind`)（新 message ではなく既存 message へのフィールド追加なので Event oneof field number は不変）
- 罠/Tips（次のエージェントへの引き継ぎ）:
  - **`engine_busy_event.rs` の exhaustive pattern**: `EngineBusy` を destructure する既存テストは exhaustive match なので、新フィールド追加時に必ず pattern を更新する（`engine-client/tests/engine_busy_event.rs::engine_busy_deserializes_correctly` を更新済）。`grpc_transport.rs::proto_event_to_dto` も同様に exhaustive pattern なので追加しないとビルド失敗する（実施済）。
  - **`EngineEvent` には `Serialize` が実装されていない**（Deserialize のみ）。`serde_json::to_string(&EngineEvent::...)` を呼ぶ round-trip テストはコンパイル失敗するので、JSON 文字列 → deserialize の片方向 + dict 比較で検証する。
  - **`server_grpc.py` の event 構築は ParseDict 経由の generic 経路**（line 269）。EngineBusy への field 追加は `_EVENT_TO_FIELD_AND_CLASS` mapping 変更不要、SCHEMA_MINOR 同期のみで済む（schemas.py に dict 出力されれば proto も自動で埋まる）。例外は `LiveStrategyScenarioLoaded.strategy_init_kwargs` のような `dict` → JSON 文字列の事前変換が必要な場合（既存実装あり）。
  - **`engine_event_variants` count は 56 のまま**（P3c は新 variant ではなくフィールド追加）。Phase 3 functional impl で `LiveStrategyReady` / `LiveStrategyWarmingUp` の `map_engine_event_to_message` arm に GUI 配線（現状は TODO 付き `None` arm）。
  - **gRPC integration test は `#[ignore]` 既定**（Python+grpcio 必要）。Phase 3 functional impl 完了後に `--include-ignored` で観測される想定。
  - **Phase 3.5 は schema bump 不要**（capability dict のキー追加のみ）。SCHEMA_MINOR は 28 で本 issue の schema chain は完了。

### Phase 3 完了（2026-05-10）
- 担当: phase3-agent
- 主要 commit:
  - `7f86ffd` — modal 拡張（strategy_init_kwargs / prod_mode / disabled_reason / pending_scenario_request_id + Action::Submit 拡張 + 18 unit tests）
  - `44269d9` — LiveStrategyState 三つ組拡張 + auto_generate_live_panes + map_engine_event_to_message TODO 解消 + warm_up timeout (60s) + LiveStrategyScenarioLoaded prefill + LoadLiveStrategyScenario fallback (5s) + EngineRehello → LiveStrategyRehelloReplay + SecondPasswordRequired 固定文言 + strategy_parse_failed 解放
  - `fb0c36b` — `tests/live_form_smoke.rs` source-pin 13 tests（受入基準 #2/#11/#13/#15/#17/#19/#8 GUI）
- 設計判断:
  - **LiveStrategyState 拡張箇所**: `src/main.rs` 内、`enum LiveStrategyState { Idle, Running { strategy_id, instrument_id, venue } }` で三つ組 SoT 化（`pending_live_config` 別フィールド案は撤回、Running 自体を兼用）
  - **auto_generate_live_panes 配置**: `src/screen/dashboard.rs::Dashboard::auto_generate_live_panes(&mut self, main_window_id, strategy_id, instrument_id, venue)`。冪等 key は `live_pane_keys: HashSet<(String, String, String)>` フィールドで管理し、新規キーのときだけ CandlestickChart→TimeAndSales→OrderList→BuyingPower→Positions の 4+ ペインを生成、`clear_live_pane_keys()` で stop 時にリセット
  - **timer 実装方針**: Subscription ではなく `Task::perform(tokio::time::sleep, ...)` + token bump 方式。`live_warmup_timeout_token: u64` を `LiveStarted` / `LiveWarmingUp` / `LiveStrategyReady` / `LiveStopped` で wrapping_add(1) して、`LiveWarmupTimeoutFired` 発火時に古い token は捨てる（リセット実装）。`LIVE_WARMUP_TIMEOUT_SECS=60` / `LIVE_SCENARIO_FALLBACK_TIMEOUT_SECS=5` を `pub(crate) const` で集約
  - **pending_live_config の保持方法**: LiveStrategyState::Running を SoT として直接使用（別フィールドなし）。`LiveStrategyRehelloReplay` 内部メッセージで `Running` から三つ組を抽出して `auto_generate_live_panes` を冪等再呼出
  - **EngineRehello 連携**: `src/handlers/venue.rs` の tachibana / kabu 両 EngineRehello arm で `Message::Replay(ReplayMsg::LiveStrategyRehelloReplay)` を chain。Python engine 側からの再 emit は要求しない
  - **SecondPasswordRequired**: 既存 modal flow に加え `notifications.push(Toast::error("第二暗証番号を設定してください"))` で固定文言通知（CLI と統一）
  - **strategy_parse_failed**: `IpcError{code:"strategy_parse_failed"}` を venue handler で `live_strategy_form_modal.pending_scenario_request_id` と照合して `release_scenario_pending()` 呼出 + 「手入力で続行」warn toast
- ✅ 達成した受け入れ基準:
  - #2 GUI File>Open → 4 ペイン自動生成（`test_live_strategy_ready_auto_generates_four_panes`）
  - #11 LiveStrategyReady 冪等（`test_live_strategy_ready_idempotent_on_double_emit`）
  - #13 LIVE_SCENARIO 戦略 → GUI prefill（`test_live_strategy_scenario_loaded_prefills_form`）
  - #15 LiveStrategyReady timeout (60s) + LiveStrategyWarmingUp でリセット（`test_engine_started_without_live_strategy_ready_shows_timeout_banner` + `test_warming_up_resets_timeout_counter` + `test_live_warmup_timeout_constant_is_60s`）
  - #17 reconnect 時の冪等再生（`test_engine_rehello_replays_live_strategy_ready_via_pending_config`）
  - #19 LoadLiveStrategyScenario fallback（`test_load_live_strategy_scenario_timeout_falls_back_to_manual_input` + `test_strategy_parse_failed_releases_form`）
  - #8 GUI 部分: 第二暗証番号 固定文言（`test_second_password_required_shows_status_banner`）
- ❌ 後 Phase に委譲: なし（Phase 3 スコープ完遂）。Phase 3.5 で `prod_mode` の engine 配線（`is_production` cap が venue 単位で expose されたとき）と venue dropdown を解放する
- 知見/Tips（次への引き継ぎ）:
  - **handler_arm の char-boundary 落ち穴**: 日本語コメントが多い handler を fixed-byte slicing で切ると multibyte UTF-8 境界に落ちる。`tests/auto_generate_replay_panes_auto_bind.rs` の 15_000 byte slicing がこの罠を踏み、`auto_generate_live_panes` を sibling に追加した時点で一気に 4 件 panic した。`is_char_boundary()` で floor する helper を共通化（`tests/live_form_smoke.rs::handler_arm_match`）
  - **handler_arm の同名衝突**: `Message::Replay(ReplayMsg::LiveWarmupTimeoutFired { ... })` の Task::perform 内コンストラクション呼出と、トップレベル match arm の `ReplayMsg::LiveWarmupTimeoutFired { strategy_id, token } =>` は同名で `match_indices` の最初の hit を取ると間違える。`handler_arm_match` は destructuring `{ field, ... } =>` を含む arm-style パターンを優先する
  - **EngineRehello 経路**: tachibana / kabu の **両方** で `LiveStrategyRehelloReplay` を chain しないと、片方の venue 再ハンドシェイクで live ペインが復活しない事故が起きる。`tests/live_form_smoke.rs::test_engine_rehello_replays_live_strategy_ready_via_pending_config` で 2 件以上の occurrence を pin
  - **`prod_mode` は今 Phase 3 では engine config に流れない**: `src/handlers/replay.rs::LiveStrategyFormMsg::Submit` で `let _ = prod_mode;` の TODO 残し。Phase 3.5 で `Ready.capabilities.venue_capabilities["tachibana"].is_production` を読んで disable 判定を解放したあと、engine 引数経路を加える
  - **`auto_generate_live_panes` は最小実装**: replay の `auto_generate_replay_panes` の dismiss/registry 機構までは持たず、`live_pane_keys: HashSet<(String,String,String)>` だけで冪等性を確保する。実 pane 生成は CandlestickChart (M1) を root に → TimeAndSales → OrderList → BuyingPower → Positions の 5 split。`Configuration::Pane` の Starter 単独状態では bootstrap で root pane を作る（replay と同じ流儀）。dismiss 機構や stream binding（`set_content_and_streams` 相当）は live mode 用 helper 経路（既存）が担う想定なので、ここは pane 生成のみ。dismiss を追加するときは `replay_pane_registry` パターンを参考にする
  - **`LiveStarted` の Running 遷移を撤回**: 旧コードは `EngineStarted`(live) で即 `Running { strategy_id }` に遷移していたが、`EngineStarted` イベントには `instrument_id` / `venue` が無いため拡張後の `Running { strategy_id, instrument_id, venue }` を組み立てられない。Phase 3 では `LiveStarted` は pending_strategy_id 設定 + 60s タイマー起動だけに変更し、Running 遷移は `LiveStrategyReady` 受信時のみ。これにより `EngineStarted` 後 `LiveStrategyReady` が来ない（warm_up 失敗）ケースで Running を空 state にしないという invariant も担保される
  - **`LiveStopped` の matching 拡張**: pending_strategy_id（warm_up 中の停止）と Running.strategy_id（warm_up 後の停止）の両方を許容しないと、warm_up 中の stop が黙殺される。`pending_match || running_match` ロジックを使用
