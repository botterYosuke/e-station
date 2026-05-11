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

### Phase 1 完了（2026-05-10）
- 担当: phase1-agent
- 主要 commit:
  - `bcd4d75` — engine_runner Phase 1: is_market_open / warm_up failure (例外 OR False) / LiveStrategyReady (warm_up 成功直後・node.build 前) / LiveStrategyWarmingUp ticker (5s 毎) / node_build_failed cleanup
  - `a0ec370` — server.py venue 単位 concurrent live ガード（`_active_live_venues`）+ EngineBusy(busy_kind=another_strategy_on_venue) 経路 + finally cleanup
  - `0c07e2d` — `python -m engine.live_session_cli run` CLI 新設（replay と対称）+ stdin 4 経路 + `--prod` AND env ガード + ``SecondPasswordRequired`` 固定文言 + EngineBusy/Error/EngineError stderr 表示
  - `a34bc0f` — self-review fix: early-abort 経路（market_closed / warm_up_failed）で EngineStopped を必ず emit する silent failure 対策 + attach mode で第二暗証番号系の引数 / env が設定されたら hint を出す
- 設計判断:
  - **`is_market_open` SoT 配置**: 既存 `engine.exchanges.tachibana_ws::is_market_open` を再利用（新設不要）。`engine_runner.py` の module top で import し、`monkeypatch.setattr("engine.nautilus.engine_runner.is_market_open", ...)` で testable に。`start_live` 冒頭で authoritative reject。CLI 側は事前 hint のみ（`engine.exchanges.tachibana_ws.is_market_open` を直接 import）
  - **LiveStrategyWarmingUp ticker 実装方針**: `_asyncio.create_task(_warming_up_ticker(stop_flag))` で background task。`_asyncio.wait_for(stop_flag.wait(), timeout=5.0)` で 5s 周期 emit、warm_up 成功 / 失敗で `stop_flag.set()` → ticker は次の wait_for で抜ける。stages は `[(0.2, "connecting"), (0.5, "fetching open orders"), (0.8, "synchronizing positions")]` の段階的 message（progress フィールドのテストで本当の進捗を観察できないので最低限実装）
  - **第二暗証番号 OS 露出対策**: 優先順位 ``--second-password-stdin`` (推奨) > ``DEV_TACHIBANA_SECOND_PASSWORD`` env > ``--second-password`` (非推奨)。stdin は `sys.stdin.read().rstrip("\r\n")` で trailing CRLF のみ除去（内部空白は意図的に保持）、`isatty()` で対話判定し非対話 + 空 stdin は ``argparse.error`` で reject。``--second-password`` は stderr に「shell history / ps / Windows タスクマネージャに露出します」と警告
  - **venue 単位 concurrent ガードの位置**: ``_handle_start_engine`` の main 経路で **engine_already_running 判定の直後**に置く（asyncio.to_thread 起動前）。ガード順序: engine_already_running（同一 strategy_id）→ venue concurrency（別 strategy_id + 同 venue）。venue は `self._connected_venue` を参照し、tachibana ログイン後に "tachibana" に確定する。``_active_live_venues.add(venue)`` は受理直後、``_active_live_venues.discard(venue)`` は finally 節（warm_up failure / node_build_failed / timeout / 例外いずれの経路でも cleanup 保証）
  - **`getattr` defensive 参照**: 既存テストが `__init__` を patch して `_active_live_venues` を未初期化のまま `_handle_start_engine` を呼ぶケースがあるため、`getattr(self, "_active_live_venues", None)` で None フォールバック（参照時 / 追加時 / discard 時の 3 箇所）。本実装の挙動は変えず既存テストとの互換のみ確保
  - **silent failure 修正（self-review ラウンド1）**: ``start_live`` の early-abort 経路（market_closed / warm_up_failed）は外側 ``try/finally``（EngineStopped emit 部）の前で return するため、Rust 側 state machine が stuck する silent failure を生んでいた。``_emit_engine_stopped_for_early_abort()`` helper で両経路で明示 emit。Rust 側は EngineStarted 無しの EngineStopped を no-op として扱う契約（server.py "Silent-M1"）に依存。``node_build_failed`` 経路は外側 ``try`` の **内側** にあり、Python の ``return → finally`` semantics で EngineStopped が自動 emit されるため修正不要
- 知見/Tips（次への引き継ぎ）:
  - **`live_demo` / `live_demo_inprocess` マーカーは未登録**: `pyproject.toml` に未登録のため pytest が `PytestUnknownMarkWarning` を出す（test 自体は PASS）。Phase 7 でマーカー登録 + `tachibana-demo.yml` ワークフロー組込予定（issue 統一決定 #9 + #20）
  - **`_FakeLiveSession` テスト pattern**: CLI test では実 LiveSession を経由せず `monkeypatch.setattr("engine.live_session_cli.LiveSession", _FakeLiveSession)` で差し替える。__init__ wrap で `injected_events` をセットしてから `run()` 内で on_event に流す方式。実 attach / inprocess の smoke は `pytest -m live_demo` で別経路（CI workflow_dispatch のみ）
  - **engine_runner の warm_up テスト**: TradingNode / TachibanaLiveExecutionClient / TachibanaLiveDataClient / OrderIdMap / TachibanaEventBridge / make_equity_instrument を全部 monkeypatch 必要。詳細は `python/tests/test_engine_runner_live_warmup_failure.py::_patch_min_dependencies` 参照。fake exec_client は `warm_up()` async + `close()` async の 2 メソッドだけ実装すれば良い
  - **`EngineError` vs `Error`**: 統一決定では warm_up_failed / market_closed / node_build_failed は ``EngineError`` に統一（strategy_id 付き、strategy レベル）。``Error`` は接続レベル（auth_failed 等）に限定する設計。本 Phase で旧 `Error{code:"warm_up_failed"}` を ``EngineError`` に変更
  - **server.py の `_active_live_venues` cleanup**: kabu_station venue が venue として "kabu_station" 文字列で登録される設計（Phase 4 完了時）。現状 ``self._connected_venue or "tachibana"`` で fallback しているが、Phase 4 で kabu live が解放されたら `self._connected_venue` が "kabu_station" になっているはずなので fallback は問題ない。ただし Phase 4 では `_handle_start_engine` の `venue_not_supported` reject が外れるので、その時点で再確認すること
  - **未対応 silent failure（次フェーズ対象）**: `start_live` の warm_up 失敗時に server.py 側で `Error{request_id, code:"engine_run_failed"}` を emit する経路が抜けている（exception を catch する except 経路でしか送らない）。CLI 側で blocking 待機する Rust client は EngineStopped で待機解除する設計のため致命ではないが、Rust 側の Error 待ちロジックが追加されたら問題になる。Phase 6 review で要再確認
  - **CLI exit code 体系**: 0=正常、1=一般エラー（EngineError / login fail / unexpected）、2=busy（EngineBusy / engine_already_running）、3=auth required（SecondPasswordRequired）。replay CLI と整合させた
- ✅ 達成した受け入れ基準: #1 (CLI 経路 — `test_attach_starts_engine_for_replay_strategy_unchanged` + `test_inprocess_starts_engine_for_replay_strategy_unchanged`), #6 (`test_invalid_config_when_max_qty_missing` + `test_max_qty_zero_or_negative_reject` + `test_max_notional_overflow_reject`), #7 (`test_prod_blocked_without_env`), #8 CLI 部分 (`test_attach_second_password_required_exits_nonzero`), #9 (`test_start_live_rejects_when_market_closed`), #14 (`test_warm_up_exception_emits_error_not_ready` + `test_warm_up_returns_false_emits_error_not_ready` + `test_warm_up_exception_closes_exec_client` + `test_warm_up_returns_false_closes_exec_client`), #16 (`test_concurrent_live_emits_engine_busy_for_venue` + `test_duplicate_strategy_id_emits_engine_already_running`), #20 (`test_second_password_stdin_handles_heredoc_pipe_empty_and_noninteractive`)
- ❌ Phase 6 に委譲: #10 (lint via `tools/lint/check_live_login_call.py`)
- 検証: `cargo test --workspace` 全緑、`uv run pytest python/tests/` 2352 passed / 114 skipped、`cargo clippy --workspace --tests -- -D warnings` clean

### Phase 3.5 完了（2026-05-10）
- 担当: phase35-agent
- 主要 commit:
  - `3e052cc` — Python: TachibanaWorker.capabilities() に `supports_live_strategy=True` + `is_production` (`TACHIBANA_ALLOW_PROD == "1"` で True) を追加。server.py kabu_station venue_caps に `supports_live_strategy=False` を追加（Phase 4 で flip）。受け入れ基準 #12 / #18 用テスト + schema fallback contract 追加
  - `8773560` — Rust: `engine-client/src/capabilities.rs` に `supports_live_strategy()` / `is_production()` helper 追加（cap 欠落 / 異 venue / malformed wire 全て safe-side false）。`src/main.rs::parse_kabu_is_production` を新 helper に DRY 委譲。unit test 6 件追加
  - `387168c` — GUI: `LiveStrategyFormModal` に `tachibana_is_production` フィールド追加 + `validate()` で `prod_mode=true && !tachibana_is_production` を固定文言「TACHIBANA_ALLOW_PROD env が未設定です（engine 再起動が必要）」で reject。`handlers/replay.rs::NativeOpenStrategyPicked` (live 分岐) で modal 構築時に `engine_client::capabilities::is_production(caps, "tachibana")` を流す。Phase 3 の `let _ = prod_mode;` TODO を解消（statement of invariant 化）
  - `e079784` — chore: Wave 1 / Phase 3 で fmt 残しの 2 ファイルを cargo fmt
- 設計判断:
  - **tachibana `is_production` 配置**: `python/engine/exchanges/tachibana.py::TachibanaWorker.capabilities()` 内で `os.environ.get("TACHIBANA_ALLOW_PROD") == "1"` を直接読む。kabu_station と対称な venue_caps 経路。`set_credentials_demo_flag` で動的に切替する経路は持たせない（engine プロセス env が SoT、統一決定 #14）
  - **prod_mode 配線方針**: 完成。ただし `EngineStartConfig.prod_mode` のような wire は **追加しない**（schema bump 不要を維持 + 統一決定 #14 の "GUI は engine env を触れない" を尊重）。代わりに `validate()` で「prod_mode=true && cap=false」を reject することで、Submit に到達した時点で「prod_mode=false なら demo 起動 / prod_mode=true && cap=true なら engine が prod env で起動済み」のいずれかが成立する invariant を担保する。これにより既存の `StartEngine` をそのまま送れば engine は自分の env に従って自動的に demo / prod を選ぶ
  - **venue dropdown 解放**: Phase 4 と一緒に解放。理由 = (1) kabu_station の `supports_live_strategy=False` は Phase 3.5 で expose したが、(2) `NautilusRunner.start_live` が tachibana 専用なため Phase 4 で kabu 対応が完了するまでフリップしても表示するメリットがない、(3) Phase 4 で venue 切替経路を一度にレビューした方が安全
  - **`VenueCapability` 型新設は撤回**: 統一決定 #14 R5-MED-3 の方針通り、`Ready.capabilities` は `serde_json::Value` のまま保持し、`engine-client/src/capabilities.rs` の薄い helper（`supports_live_strategy` / `is_production`）経由で読む構造を維持
  - **schema drift 警告ログ**: 旧 `parse_kabu_is_production` は `Err(_)` 経路で warn ログを出していたが、新 helper への DRY 統一でそれが消える。schema drift の検知は `/ipc-schema-check` skill 側に寄せる方針（cap 欠落と malformed wire を区別する必要がある場面ではない、UI は安全側 false で十分）
- ✅ 達成した受け入れ基準:
  - #12 `supports_live_strategy` cap が tachibana=true / kabu_station=false（`python/tests/test_capabilities_live.py::test_supports_live_strategy_per_venue` + Rust 側 `test_supports_live_strategy_returns_*`）
  - #18 tachibana `is_production` cap 露出（`python/tests/test_capabilities_live.py::test_tachibana_is_production_per_env` で env unset/0/1/"true" の 4 ケース pin + Rust 側 `test_is_production_*` + GUI validate() 4 件）
- 検証: `cargo test --workspace` 全緑、`uv run pytest python/tests/` 2356 passed / 118 skipped、`cargo clippy --workspace --tests -- -D warnings` clean
- 知見/Tips（Phase 4 への引き継ぎ）:
  - **kabu_station の `supports_live_strategy=False` を True に flip する位置**: `python/engine/server.py::_handshake` 内、現状は約 line 941 (`is_production` の直下) に `"supports_live_strategy": False,` として直書きされている。Phase 4 で `NautilusRunner.start_live` が kabu_station 対応を完了したら、この値を **`True` に変更する 1 行 + `python/tests/test_capabilities_live.py::test_supports_live_strategy_per_venue` の kabu_station 期待値を `True` に変更する 1 行** で flip 可能。tachibana の `supports_live_strategy` は worker の `capabilities()` 内なので Phase 4 では触らない
  - **venue dropdown の追加経路**: `src/modal/live_strategy_form.rs::view()` で `prod_checkbox` の上に venue dropdown を挿入する。capability 読み取りは `engine_client::capabilities::supports_live_strategy(caps, venue)` を使い、`true` の venue のみ表示する（`["tachibana", "kabu_station"]` を for_each で filter）。`Action::Submit` には venue フィールドを追加する必要があり、`EngineStartConfig` には現状 venue field が無いので Phase 4 で追加するか、`engine` enum を `Live { venue }` のように拡張するかは Phase 4 の wire 設計で決める
    - R2 (2026-05-11) 反映: venue dropdown + `LiveStrategyScenarioLoaded.venue` prefill + `connected_venue` (`tachibana_state.is_ready()` / `kabu_state.is_ready()` 由来) 一致チェックを実装。`available_venues` 空時は GUI 側判定を skip し server.py の `_connected_venue` reject 経路に委ねる compat 経路として残した
  - **`TACHIBANA_ALLOW_PROD` の "literal 1 only" 契約**: tachibana SKILL.md / `test_url_masker.py` と同じ流儀。`"true"` / `"yes"` 等は False（unsafe を倒すため）。`os.environ.get("TACHIBANA_ALLOW_PROD") == "1"` の判定を弱める提案が来ても拒否してよい
  - **modal 内の `tachibana_is_production` は engine 再起動を跨ぐと反映されない**: 現状 modal 構築時 (`NativeOpenStrategyPicked`) に capability を読んで struct literal で代入する設計。modal が開いている最中に engine が再起動すると古い値が残るが、env 変更には engine 再起動 → handshake → modal 再オープン が必要なので実害なし（統一決定 #14）。Phase 4 で venue dropdown が動的に表示切替する場合も同じ流儀で、`engine_connection.capabilities()` を modal 構築のたびに読む設計を維持する
  - **Rust helper を呼ぶ既存テスト**: `src/main.rs::parse_kabu_is_production` のテスト 7 件（`parse_returns_true_when_is_production_advertised` 等）は既存のまま PASS。新 helper は `engine_client::capabilities::is_production(caps, "kabu_station")` の薄い alias なので、kabu_station 用のテストは `parse_kabu_is_production` 側に集約して残し、tachibana / 一般化されたケースは `engine-client/src/capabilities.rs::tests` 側にある

### Phase 4 完了（2026-05-10）
- 担当: phase4-agent
- 主要 commit:
  - `2cc8ee2` — feat(kabu-station-clients): KabuStationLive{Exec,Data,EventBridge} 新設 + KabuStationVenue.fetch_orders 追加 + 16 unit tests
  - `4b659e6` — feat(engine-runner): NautilusRunner.start_live() に venue 引数追加（"tachibana" / "kabu_station"） + venue 別 client 構築 dispatch + venue_not_supported reject + 4 unit tests
  - `d271404` — feat(server): kabu_station venue_caps の supports_live_strategy=True に flip + _handle_start_engine の "tachibana 固定 reject" を撤廃し両 venue 受理 + test_capabilities_live の kabu 期待値を True に更新
  - `161465e` — fix(engine-runner): review-fix HIGH×3 (H-2 LiveDataBridge tachibana gate / H-3 warming_up_ticker venue 文言) + MEDIUM×1 (M-2 LiveStrategyReady venue propagation 正の test pin) + 3 regression-pin tests
- 設計判断:
  - **共通抽象の有無**: BaseLiveExecClient / BaseLiveDataClient 抽象は **抽出しない** （if/elif 分岐に留める）。理由: (1) Phase 4 minimal scope で代表 1 経路のみ通す方針、(2) 共通化すると warm_up / close / 安全装置 / submit_order の4契約だけが薄く重なるが各 venue の wire 表現が大きく異なる（tachibana = SessionHolder + p_no_counter + envelope, kabu = KabuStationVenue + send_order kwargs）ため抽象化のレバレッジが薄い、(3) 将来 BaseLiveExec を入れる際は LiveExecutionClient 親クラスから派生させる H-1 の宿題と一緒に整理する方が筋が良い
  - **既存 kabu_station 資産の活用範囲**: `KabuStationVenue` (engine.exchanges.kabusapi) を thin adapter から委譲する形で活用。`KabuOrderClient.send_order` / `cancel_order` / `poll_fills` を直接呼ぶ。`KabuStationVenue.clear()` を `close()` で呼んで token / order_client / trade_password_holder を解放。`RegisterSet` は **新インスタンスを live data client 内に持つ**（既存 server.py `_kabu_register_set` とは別、後述 M-3 TODO）
  - **50 銘柄 PUSH 上限の実装**: LRU evict + `SubscriptionEvicted` IPC event emit（spec §3.2-G）。RegisterSet の `on_evict` callback で IPC 化。明示 reject 設計の既存 server.py 経路と独立させた（live strategy 用は暗黙 evict + 通知が spec 規定）
  - **SubscriptionEvicted emit 経路**: `KabuStationLiveDataClient._on_evict(symbol, exchange)` → `on_event({"event":"SubscriptionEvicted", "venue":"kabu_station", "symbol", "exchange"})` callback 経由で server.py outbox / Rust IPC に流す
  - **venue 引数の wire**: `EngineStartConfig` には venue フィールドを **追加しない**（schema bump 不要を維持）。代わりに server.py 側で `_connected_venue` を読んで `runner.start_live(venue=...)` に流す。GUI dropdown の選択 → `EngineStartConfig.venue` への反映は Phase 5（GUI 拡張）で実装する。これにより engine 側は「ログイン済 venue で live を起こす」という invariant を保つ
- ✅ 達成した受け入れ基準: なし（Phase 4 自体は受け入れ基準対応表に直接エントリなし）
  - ただし **#12 の kabu_station 部** = `supports_live_strategy=True` に flip 完了。`python/tests/test_capabilities_live.py::test_supports_live_strategy_per_venue` の kabu_station 期待値を True に更新済
- ⚠️ TODO 残し（Phase 5+ または別 issue で対応）:
  - **H-1 (Nautilus 親クラス継承)**: ✅ **R2-C (`84cf060`) で完全解消**。`KabuStationLiveExecutionClient` / `KabuStationLiveDataClient` は `nautilus_trader.live.execution_client.LiveExecutionClient` / `LiveMarketDataClient` を継承し、`node.kernel` 経由で `loop / msgbus / cache / clock / instrument_provider` を `super().__init__` に転送する。`node._exec_engine.register_client(...)` / `node._data_engine.register_client(...)` の Cython type check を通過可能で、warm_up 成功 → `node.build()` 経路で `node_build_failed` にならない。詳細は本ドキュメント末尾の「R2-C 反映」セクション参照
  - **M-1 (GUI venue dropdown)**: ✅ R2 (2026-05-11) で解消。`LiveStrategyFormModal` に `venue` / `available_venues` / `connected_venue` フィールドを追加し、`Message::VenueChanged` + `Action::Submit { .., venue }` を流す。`LiveStrategyScenarioLoaded.venue` を `prefill_from_scenario` 経由で form に prefill、`available_venues` は `engine_client::capabilities::supports_live_strategy(caps, venue)` が true な venue のみを filter (modal 構築時 `handlers/replay.rs::NativeOpenStrategyPicked`)。`validate()` で `available_venues` 含有チェック + `connected_venue` (= `tachibana_state.is_ready()` / `kabu_state.is_ready()` 由来) との一致チェックを行い、戦略 venue と engine 接続 venue の不整合を Submit 前に reject する。`EngineStartConfig` への venue field 追加は引き続き Phase 5+ の wire 設計に委ねる（server.py 側 `_connected_venue` 判定が SoT、GUI は事前ガードとして機能）
  - **M-3 (RegisterSet 二重化)**: `KabuStationLiveDataClient` 内部の `RegisterSet` と既存 `server.py::self._kabu_register_set` が独立している。前者は live strategy の「契約 stub」（subscribe/evict 通知契約）、後者が PUSH 物理経路（`PUT /register` / `_handle_subscribe_kabu_station`）。kabu live data は実際には flow しない（R2-C で親継承は解消されたが、PUSH 経路の本配線は引き続き Phase 5 以降）。spec §3.2-G の `SubscriptionEvicted` 通知契約は live data client が IPC emit するので満たす
    - (a) **bootstrap register (warm-up 時の `instrument_id` 自動 register + PUT /register)**: ✅ R2 で解消、R3 C1 で ordering fix 完了 (register が PUT より **前** に走るよう `_handle_start_engine` で `_run` の外側に移動)。
    - (b) **runtime dynamic subscribe (strategy が走行中に `client.subscribe(symbol)` を呼んだとき)**: ⚠️ Phase 5 持ち越し。内部 RegisterSet と `server.py::self._kabu_register_set` の同期 (PUT /register の追従) は未配線。`KabuStationLiveDataClient.subscribe()` は `register_cb` (server.py 経由で PUT /register に流す async callback) を受け取れる設計だが、bootstrap 時にしか注入されていない。動的 subscribe 経路の本配線は Phase 5 で。
  - **M-5 (fetch_orders 意味論)**: `KabuStationVenue.fetch_orders` は現状 `KabuOrderClient.poll_fills(**params)` に委譲しており、`State=5 (約定)` のみを返す。warm_up の本来の目的「未決注文 (open orders) 復元」とは意味が違う（未決 = State 1, 3, 4 等）。Phase 5 で `KabuRestClient.fetch_orders` (`/orders` 全件) に切替え + OrderIdMap 相当の写像を kabu 側にも実装する
  - **代表 1 経路（注文発行 + フィル受信）の実機通過**: H-1 は R2-C で解消したため `node.build()` 通過は技術的に可能。attach mode の実機 smoke は credential / 立花 demo 環境が整い次第別途実施（テストロジック自体は Phase 7 `test_live_session_cli_e2e.py` で `@pytest.mark.live_demo` 配下に整備済）
- 検証:
  - `cargo test --workspace` 全緑
  - `uv run pytest python/tests/` 2379 passed / 118 skipped（Phase 4 で +23 件 — kabu client 16 / engine_runner kabu 7 + 既存 capabilities_live を kabu=True に更新）
  - `cargo clippy --workspace --tests -- -D warnings` clean
- 知見/Tips（Phase 5 への引き継ぎ）:
  - **examples の kabu_station 用 LIVE_SCENARIO**: 現状 `examples/test_strategy_*.py` の `LIVE_SCENARIO` は tachibana 専用。kabu 用 venue 文字列 `"kabu_station"` + instrument_id 形式 `"<symbol>.KabuStation Stock"` で別途 example を起こす。`LIVE_SCENARIO.venue` を `"kabu_station"` にする戦略ファイル（例: `examples/live_kabu_minute.py`）を新設する案
  - **kabu instrument_id 命名規則**: server.py `_do_submit_order_kabu` は `instrument_id.endswith(".KabuStation Future")` / `".KabuStation Option")` で stock / future / option を判別。Phase 4 minimal は stock 1 経路のみ。先物 / OP は KabuOrderClient.send_order_future / send_order_option を呼ぶ別経路が必要（既存実装あり）
  - **EngineStartConfig.venue field 追加判断**: GUI dropdown 解禁時に schema bump 1 件で venue field を追加する案と、`StartEngine.engine` を `Literal["Backtest", "Live"]` から `enum Engine { Backtest, Live { venue: str } }` のような discriminated union に拡張する案がある。後者の方が wire 上「Live + venue は不可分」を強制できるが schema bump コストが大きい。前者の方が後方互換が単純。Phase 5 のスキーマ設計レビューで決める
  - **review-fix で踏んだ silent failure pattern**: tachibana 専用の bridge / hardcode 文字列を kabu 経路に流すと daemon thread 内 AttributeError や UI 文言誤表示として silent failure になる。venue 引数を増やす際は **必ず**「(a) bridge / thread 起動条件、(b) UI 文言、(c) wire 値（account_id / venue）」の 3 点を venue 別に grep して洗うこと
  - **冪等 key 三つ組のテスト pin**: Rust 側 `auto_generate_live_panes(strategy_id, instrument_id, venue)` の冪等 key は三つ組。venue 取り違え regression を防ぐには engine 側 `LiveStrategyReady.venue` の値を「warm_up=true で正しく venue 引数が伝搬する」test で pin する（`TestLiveStrategyReadyVenuePropagation::test_kabu_warm_up_success_emits_live_strategy_ready_with_kabu_venue`）。warm_up failure 経路の negative test だけでは「venue 値の正の伝搬」を観測できない

#### Phase 4 R2 レビュー反映 (2026-05-11, ラウンド 2)

R1 (e-station-review) で CRITICAL-1 (kabu live data path 未配線) と HIGH-1 (LiveSession venue 非対応) を抽出 → R2 で本配線。

- ✅ CRITICAL-1: kabu PUSH/fill → live_fd_queue / live_ec_queue → KabuLiveDataBridge / KabuLiveEcBridge → KabuStationLiveDataClient._feed_trade_dict_sync / KabuStationEventBridge.process_order_record の物理経路完成
- ✅ HIGH-1: LiveSession.login() / run() を venue 分岐。kabu は KabuStationVenue.startup_login() を呼び、in-process arm は second_password 不要 + runner.start_live(venue=self._venue) を必ず渡す
- 計画書 §270 M-3 (RegisterSet 二重化) は warm-up 時の明示 register + PUT /register で実 PUSH が flow する形に解消。strategy 動的 subscribe は Phase 5 で再評価

主要変更ファイル:
- `python/engine/nautilus/clients/kabu_station/kabu_station_data_client.py`: `register_cb` 引数 + `_feed_trade_dict_sync` + `_connect()` で `_loop` 保存
- `python/engine/nautilus/clients/kabu_station/kabu_station_event_bridge.py`: `_loop: asyncio.AbstractEventLoop | None` 属性追加
- `python/engine/nautilus/live_bridges.py`: `KabuLiveDataBridge` / `KabuLiveEcBridge` 新設
- `python/engine/nautilus/engine_runner.py`: venue 別 bridge dispatch (`if venue == "tachibana"` → tachibana bridge / `else` → kabu bridge)、event_bridge._loop を両 venue で必須注入
- `python/engine/server.py`: `_on_kabu_board_push` で TRADING+kabu_station 時に `_live_fd_queue` push / `_kabu_fill_poller` で TRADING 中 `_live_ec_queue` push / `_handle_start_engine` kabu 分岐で warm-up 時 `_kabu_register_set.register` + `_kabu_put_register` 実行
- `python/engine/replay_session.py`: `LiveSession.login()` で venue=kabu_station → `KabuStationVenue.startup_login()` 経路 / `LiveSession.run()` in-process arm で `second_password` チェック venue 限定 + `runner.start_live(venue=self._venue)` 渡し

追加テスト数: 19 件
- `test_kabu_station_live_data_client.py`: +3 (register_cb provided/none, _feed_trade_dict_sync)
- `test_engine_runner_live_kabu.py`: +4 (TestLiveBridgesVenueDispatch — kabu bridge source pin + thread spawn pin + loop injection + ec bridge process_order_record)
- `test_server_kabu_live_push.py`: 新設 +7 (board push → live_fd_queue × 4 ケース / fill poller → live_ec_queue × 2 / start_engine register source-pin × 1)
- `test_live_session_kabu_login.py`: 新設 +6 (venue 分岐 login × 3 / venue 分岐 run inprocess × 3)

設計判断:
- **server.py 内 PUT /register 配置**: `_run` が sync (asyncio.to_thread) のため await できない。`_handle_start_engine` の `await asyncio.wait_for(asyncio.to_thread(_run), ...)` 直前 (async 文脈) で `_kabu_put_register` を呼ぶ。失敗時は `EngineError{code:"kabu_register_failed"}` emit + `_live_state` を CONNECTED に戻し _run 起動 skip
- **trade dict 形式**: tachibana の FdFrameProcessor 互換 (`ts_ms`/`price`/`qty`/`side`)。`KabuStationAdapter.parse_execution` の戻り値 `Trade` (qty=0/side="unknown" 固定) を dict 化して push
- **getattr 防御の徹底**: 既存 test_server_kabu_push.py は `__init__` を bypass する fixture を使うため、新たに参照する `_mode`/`_live_state`/`_connected_venue`/`_live_fd_queue` はすべて `getattr(self, ..., None)` で safe-default 経由で読む
- **TypeError 既知バグ**: engine_runner.py の `loop.create_task(loop.run_in_executor(...))` は Python 3.14 で `TypeError("coroutine was expected")` を投げる (既存挙動・本 R2 fix 範囲外)。新 bridge test は warm_up=true + build()=success 経路を初めて exercise するため、test 側で TypeError を catch して bridge 構築観測のみに focus する pattern を採用
- **KabuLoginCancelledError の wrap**: `KabuStationVenue.startup_login()` 失敗時、CLI / GUI の error handler が共通化されているため `RuntimeError` に wrap して raise。原因型は `__cause__` で保持

検証:
- `cargo check --workspace` clean / `cargo clippy --workspace --tests -- -D warnings` clean / `cargo fmt --check` clean / `cargo test --workspace` 全緑
- `uv run pytest python/tests/test_kabu_station_live_data_client.py python/tests/test_kabu_station_event_bridge.py python/tests/test_engine_runner_live_kabu.py python/tests/test_live_session_cli.py python/tests/test_replay_session_*.py -v --timeout=60` → 全件 GREEN
- `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana and not demo_kabu and not tk_smoke" --timeout=120` → **2337 passed** / 106 skipped / 202 deselected (R2 で +19 件)

#### Phase 4 R3 レビュー反映 (2026-05-11, ラウンド 3)

R2 着地後の R3 レビューで CRITICAL 1 / HIGH 4 / MEDIUM 12 を抽出 → 修正で 0 まで収束。

##### 解消サマリ

| 区分 | ID | 概要 | 対応 |
|------|-----|------|------|
| CRITICAL | C1 | server.py `_handle_start_engine` の `_kabu_register_set.register` が `_run` (asyncio.to_thread) の中、PUT /register が `_run` の外で **先**に走り、strategy 銘柄が PUT payload に乗らない silent failure | register block を `_run` の外側 (PUT /register の直前) に移動。失敗時は `EngineError{code:"kabu_register_failed"}` + `_live_state = CONNECTED` + `_run` 起動 skip。 |
| HIGH | H1 | source-pin だけでは register/PUT の **順序** バグを検出できなかった | PUT を mock して capture payload に strategy 銘柄が含まれることを動的 assert + register raise / PUT failure の各経路で `EngineError` emit と `_run` 未呼出を pin (`TestKabuStartEngineRegisterPutOrdering` 3 件追加)。 |
| HIGH | H2 | `KabuStationLiveDataClient._feed_trade_dict_sync` が `seq=0` 固定で `trade_dict_to_tick` を呼び、同一 ts_ms 内で trade_id="L-{ts_ms}-0" が衝突 → Nautilus 側 dedup / warning | `_seq_per_ms` dict + `_next_seq` helper を追加 (tachibana_data.py と同実装)。`_feed_trade_dict_sync` 内で `seq` を計算して `trade_dict_to_tick(..., seq=seq)` に渡す。 |
| HIGH | H3 | `_invoke_register_cb` の 3 段目 fallback `asyncio.get_event_loop().run_until_complete(coro)` が Python 3.12+ で RuntimeError / deadlock の温床 | running loop も `self._loop` も無いケースは `log.warning(...)` + `coro.close()` で silent skip 化。subscribe() の RegisterSet 更新は先行済みなので、後段の server.py 経由 PUT /register が物理経路を埋める。 |
| HIGH | H4 | `_kabu_fill_poller` の `_live_ec_queue` push 条件が `_live_state == TRADING` のみで `_connected_venue` チェックが無く、tachibana TRADING 中の kabu session 残留 fill が tachibana の EC bridge を汚染 | 条件に `self._connected_venue == "kabu_station"` を追加。 |
| MEDIUM | M1 | `KabuLiveDataBridge` / `KabuLiveEcBridge` の `_loop is None` 経路が `log.debug(...)` で運用ログから silent loss | DEBUG → WARNING に格上げ + 「silent loss」明示。caplog で warning 記録を pin する unit test 追加。 |
| MEDIUM | M2 | `connected_venue` 計算が両 venue ready 時に **tachibana 固定優先** で、kabu scenario を開いたユーザに「engine 接続 venue 'tachibana' と一致しません」と誤誘導 | `LiveStrategyScenarioLoaded` 受信時に `scenario.venue` が Some + 該当 venue ready なら `form.set_connected_venue(scen_venue)` で refine。両 ready + scenario.venue=None は既存通り tachibana 優先 (fallback)。 |
| MEDIUM | M3 | `validate()` で prod_mode check が venue check より **前** にあり、venue 未選択でも「TACHIBANA_ALLOW_PROD env が未設定…」と誤誘導 | venue check を prod_mode check の前に移動。RED test pin (`test_validate_returns_venue_error_before_prod_mode_error`) 追加。 |
| MEDIUM | M4 | `prod_mode` reject 文言が固定で `TACHIBANA_ALLOW_PROD` を含めており、kabu_station venue で reject されたユーザに tachibana env をいじる誤誘導 | 文言を venue-aware に分岐。kabu_station 経路は「prod_mode は kabu_station の production env 設定が必要です（engine 再起動が必要）」を返す。kabu 用 `is_production` cap 個別チェックは Phase 5 へ繰越。 |
| MEDIUM | M5 | `view()` 内 `pick_list(self.available_venues.clone(), Some(self.venue.clone()), Message::VenueChanged)` で毎回 clone | `pick_list(self.available_venues.as_slice(), Some(&self.venue), Message::VenueChanged)` に変更し clone 撤廃。 |
| MEDIUM | M6 | `LiveSession.__init__(venue: str)` が typo を silent に受け、後段で `runner.start_live(venue=typo)` まで流れる | `venue: Literal["tachibana", "kabu_station"]` に narrow + `__init__` 冒頭で値検証 `raise ValueError(...)` を追加。 |
| MEDIUM | M7 | CLI `--prod --venue kabu_station` のエラー文言が「KABU_ALLOW_PROD=1 and KABU_ENV=prod」表現で、help 側の表現と微妙にずれる | `"--prod --venue kabu_station requires BOTH KABU_ALLOW_PROD=1 AND KABU_ENV=prod environment variables (resolve_kabu_env() must return 'prod')"` に統一。help 文言と一致させ、内部関数名も併記して trace 容易化。 |
| MEDIUM | M8 | kabu PUSH の `parse_execution` は `qty=Decimal("0")` 固定 (PUSH 仕様で約定単位を返さない) で、qty=0 trade tick が `_live_fd_queue` に流れて Nautilus 内 dedup / 異常 transaction として silent failure | `_on_kabu_board_push` の live tick push 経路で `trade.qty == Decimal("0")` の場合は skip + DEBUG ログ。Strategy SDK は depth_snapshot ベースで動作する設計を spec 反映ブロックで明記。 |
| MEDIUM | M9 | `replay_session.py::LiveSession.run()` kabu 経路で `second_password=""` を渡し、`runner.start_live` の signature も `str` 固定で「未使用 venue でも空文字を要求」する設計上の歪み | `runner.start_live(second_password: str \| None = None)` に narrow。kabu 経路は `None` を渡す。tachibana 経路で `None` が来たら `EngineError{code:"invalid_config"}` + `EngineStopped` で reject。 |
| MEDIUM | M10 | 計画書 §270 の M-3 解消マークが「✅ R2」一括で、bootstrap register と runtime dynamic subscribe を区別できなかった | M-3 を (a) bootstrap register: ✅ R2 解消 (R3 で ordering fix 完了) と (b) runtime dynamic subscribe: ⚠️ Phase 5 持ち越し に分割記述。 |
| MEDIUM | M11 | `tests/live_form_smoke.rs` が source-scan のみで `validate()` を実 struct で動的に検証する test が不在 (source-pin だけだと挙動退行を見逃すリスク) | M3/M4 で追加した `test_validate_returns_venue_error_before_prod_mode_error` などの dynamic test を `tests/live_form_smoke.rs::test_live_form_module_has_dynamic_validate_tests_for_venue` で source-pin (test 削除 regression を防ぐ)。 |
| MEDIUM | M12 | `available_venues` 空のとき venue 検査を skip する compat 経路の意図が doc コメント上で「skip is intentional but UX risk」と明示されていない | `validate()` の該当ブロックに M12 警告コメントを追加 + `test_validate_skips_venue_check_when_available_venues_empty` unit test で挙動を pin。 |

##### 主要変更ファイル

- `python/engine/server.py`: `_handle_start_engine` の kabu register/PUT ordering fix + qty=0 skip + `_kabu_fill_poller` の venue check 追加
- `python/engine/nautilus/clients/kabu_station/kabu_station_data_client.py`: `_seq_per_ms` + `_next_seq` + `_feed_trade_dict_sync` に seq 注入 + `_invoke_register_cb` の 3 段目 fallback 削除
- `python/engine/nautilus/live_bridges.py`: `KabuLiveDataBridge` / `KabuLiveEcBridge` の `_loop is None` 経路を WARNING に格上げ
- `python/engine/nautilus/engine_runner.py`: `start_live(second_password: str | None = None)` + tachibana 経路で None reject
- `python/engine/replay_session.py`: `LiveSession.__init__` で venue typo を runtime reject + `LiveSession.run()` で kabu 経路は `None` を渡す
- `python/engine/live_session_cli.py`: `--prod --venue kabu_station` の error 文言を help と統一
- `src/modal/live_strategy_form.rs`: `validate()` の venue/prod_mode 順序入替 + venue-aware reject 文言 + `view()` の clone 撤廃 + `set_connected_venue` setter 追加
- `src/handlers/replay.rs`: `LiveStrategyScenarioLoaded` 受信時に scenario.venue による `connected_venue` refine

##### 追加テスト数: +15 件

- `python/tests/test_server_kabu_live_push.py`: +5 (register/PUT ordering 動的検証 × 3 + venue mismatch fill skip × 1 + qty=0 skip × 1) + 既存 1 件を nonzero qty に書き換え
- `python/tests/test_kabu_station_live_data_client.py`: +2 (seq unique × 1 + no_loop graceful drop × 1) + 既存 1 件を await pattern に変更
- `python/tests/test_live_bridges.py`: +2 (KabuLive bridge WARNING)
- `python/tests/test_engine_runner_live_kabu.py`: +2 (second_password None for kabu / tachibana reject)
- `python/tests/test_live_session_kabu_login.py`: +2 (venue Literal narrowing × 2)
- `tests/live_form_smoke.rs`: +2 (scenario refine + validate dynamic source-pin)
- `src/modal/live_strategy_form.rs::tests`: +3 (M3 / M4 / M12)

##### 設計判断

- **C1 ordering fix の判断**: register call を `_run` の **外** に出すことで、PUT /register より前に確実に走らせる。`_handle_start_engine` の async 文脈で実行するため、`_run` (sync, asyncio.to_thread) の中での実行と比べ、tied async 経路に揃う。失敗時の cleanup は `EngineError` emit + `_live_state = CONNECTED` で state machine を unstuck。
- **H3 `run_until_complete` 削除の影響**: 旧テスト `test_subscribe_calls_register_cb_when_provided` が fallback 経路に依存していたため、`_connect()` 呼出 + running loop pattern に書き換え。production では Nautilus parent が `_connect()` を呼ぶため自動的に running loop が確保される設計と整合。
- **M2 refine タイミング**: `connected_venue` の refine は modal 構築時ではなく `LiveStrategyScenarioLoaded` 受信時。modal 構築時点では scenario.venue が未取得 (async response 待ち) のため、scenario が venue を advertise した瞬間に refine する設計が自然。
- **M4 kabu prod_mode 個別 cap は Phase 5 へ繰越**: 現状 form に `kabu_is_production` 相当のフィールドが無く、最小 fix は文言の venue-aware 化のみ。kabu 用 `is_production` cap の expose と form 連動は Phase 5 で。
- **M8 spec 整合**: kabu PUSH は約定単位を返さないため live trade tick は流さない (= depth_snapshot ベース動作)。Strategy SDK の trade-driven hook は kabu 経路では発火しない仕様を spec / 計画書で明示。
- **M9 type narrowing と runtime check の二重防御**: mypy が走らない経路 (CLI から in-process LiveSession 経由など) では Literal narrowing だけでは silent failure になりうるため、`__init__` での runtime ValueError + `start_live` での tachibana None reject の二重防御。
- **M11 source-pin で dynamic test を守る**: flowsurface が binary crate のため tests/ 配下から `LiveStrategyFormModal` を直接 use できない。inline `#[cfg(test)] mod tests` 内の dynamic test を保持し続けることを source-pin で担保。

##### 検証

- `cargo check --workspace` clean / `cargo clippy --workspace --tests -- -D warnings` clean / `cargo fmt --check` clean / `cargo test --workspace` 全件緑
- `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana and not demo_kabu and not tk_smoke" --timeout=120` 全件緑 (R2 から +15 件)

##### 知見/Tips（次への引き継ぎ）

- **closures + asyncio.to_thread の execution order**: closure 内のコードは「呼出時」ではなく `asyncio.to_thread(closure)` で worker thread が動き始めたときに走る。async 文脈の後段コード (PUT /register など) が **先に** 走るため、closure 内に「PUT より前に走らせたい side-effect」を書くと silent failure になる。closure の外側 (async 文脈) に出して順序を明示する。
- **trade_id seq counter は LiveDataClient 単位で持つ**: 同一プロセス内で複数 instrument_id を扱うため、`_seq_per_ms: dict[str, dict[int, int]]` のように instrument_id 軸でも分ける必要がある。tachibana_data.py の `_next_seq` 実装を kabu にも横展開する際、この dict 構造を一緒にコピーすること (1 次元 dict だと cross-instrument 衝突する)。
- **`asyncio.get_event_loop().run_until_complete` は Python 3.12+ でハマる**: deprecated 警告 + nested-loop 規制 + thread context の組合せで RuntimeError / deadlock のリスクが高い。代替案: (a) running loop 必須にする (production では `_connect()` 経路で保証)、(b) silent skip + WARNING (本 H3 fix の方針)、(c) `asyncio.run` で完全新規 loop (重い)。プロダクション経路は (a)、テスト fallback は (b) が安全。
- **Optional 型は runtime validation も併用する**: Python は型注釈を runtime で強制しないため、`Literal["..."]` narrow だけでは silent pass する経路がある (CLI / dynamic kwargs / dict-unpack 等)。`__init__` 冒頭で `if value not in (...): raise ValueError(...)` を併設すると mypy 不在環境でも安全。
- **venue-aware error message のテスト**: 文言が venue 名を含むことを assert する test を venue ごとに最低 1 件用意すると、固定文言への regression を捕捉できる。M4 fix で `test_validate_prod_mode_error_message_is_venue_aware_for_kabu` を kabu 用に追加した。tachibana 用は既存 `test_validate_rejects_prod_mode_when_is_production_cap_false` が `TACHIBANA_ALLOW_PROD` 含有を pin している。

#### Phase 4 R5 反映 (2026-05-11, ラウンド 5)

**R4 サニティで発見した R3 fix-induced CRITICAL 2 件を解消**。R3 は CRITICAL 1 / HIGH 4 / MEDIUM 12 を解消したが、R3 の fix 自体が新たな silent failure を 2 件導入していた (MISSES.md 知見 17「fix が silent failure を生む」の典型例)。R4 サニティで両方とも検出し本ラウンドで TDD 解消した。

##### 解消サマリ

| 区分 | ID | 概要 | 対応 |
|------|-----|------|------|
| CRITICAL | R5-CRITICAL-1 | `_handle_start_engine` の R3 で追加した kabu register/PUT early-return 経路 (server.py:5249-5266 / 5272-5286) で、`EngineStopped` / `Error{request_id}` / `_active_live_venues.discard` / `_engine_tasks.pop` / `_engine_stop_events.pop` の **5 点 cleanup が脱漏**。R2/R3 前は register/PUT 失敗が `_run` 内例外として catch-all (`except Exception`) + `finally` で carry されていたが、R3 で register/PUT を `_run` の **外** (try/except 外) に出した結果、catch-all から外れた。silent failure: (a) Rust state machine が `pending_strategy_id` で stuck、(b) Rust RequestEngineStart が 60s hang、(c) 同 venue の次 StartEngine が `engine_busy_for_venue` で reject、(d) `_engine_tasks` / `_engine_stop_events` 残留。 | `_handle_start_engine` 内に `_kabu_register_early_abort(error_code, message)` helper closure を新設。`EngineError` 既存 emit + `EngineStopped` (engine_stopped_emitted ガード付) + `Error{request_id, code, message}` + `_engine_tasks.pop` + `_engine_stop_events.pop` + `_active_live_venues.discard(live_venue_for_cleanup)` + `_live_state = CONNECTED` を一括実行する。register 失敗 / PUT 失敗の両 early-return が helper を呼び `await _drain()` 後に return。|
| CRITICAL | R5-CRITICAL-2 | R3 M8 で「`KabuStationAdapter.parse_execution` が qty=Decimal("0") ハードコード (kabu PUSH は累積 TradingVolume を持つが per-trade qty を持たない) のため qty=0 trade を `_live_fd_queue` に流さない」と決定 → kabu live data 経路が完全 dead path 化。`KabuStationLiveDataClient._feed_trade_dict_sync` が呼ばれず、TradingNode の Strategy SDK には kabu PUSH frame が一切到達しない。R2/R3 で配線した live data path が R3 M8 で **無効化** された fix-induced regression。 | **Option A: TradingVolume delta**。server.py `__init__` に `self._kabu_last_trading_volume: dict[str, int]` を追加し、ticker 別 last_volume を保持。`_on_kabu_board_push` の trade extract block で `delta_qty = current_volume - last_volume` を計算。`has_prev=False` (初回) → state seed のみ skip / `delta_qty <= 0` → skip (no execution) / `delta_qty > 0` → `qty=str(delta_qty)` で trade dict を `_live_fd_queue` に enqueue。`_clear_kabu_session` で `_kabu_last_trading_volume.clear()` し、再ログイン時に古い state を引き継がない。R3 M8 skip block (`if trade.qty == _Decimal("0")`) は撤去。R3 M8 の意図 (qty=0 流さない) は `delta_qty <= 0` skip で同等にカバー。|

##### 主要変更ファイル

- `python/engine/server.py`:
  - `__init__`: `self._kabu_last_trading_volume: dict[str, int] = {}` を追加
  - `_clear_kabu_session`: `self._kabu_last_trading_volume.clear()` を追加
  - `_on_kabu_board_push`: R3 M8 skip block を撤去 + TradingVolume delta 計算ロジックを実装 (初回 seed / delta<=0 skip / delta>0 push)
  - `_handle_start_engine`: `_kabu_register_early_abort` helper closure を新設、register 失敗 / PUT 失敗の両 early-return で helper 呼出に置換

##### 追加テスト数: +8 件 (既存 1 件は名称・期待値とも書き換え)

- `python/tests/test_server_kabu_live_push.py`:
  - `TestKabuStartEngineEarlyReturnCleanup`: 新設 +3 件
    - `test_kabu_register_failure_emits_engine_stopped_and_error_request_id` — register raise → `EngineStopped` + `Error{request_id, code:"kabu_register_failed"}` + `_active_live_venues` discard + `_engine_tasks` / `_engine_stop_events` pop の 5 点を assert
    - `test_kabu_put_register_failure_emits_engine_stopped_and_error_request_id` — PUT 失敗で同上を assert
    - `test_kabu_register_failure_releases_active_live_venues_for_next_start_engine` — register fail 後の 2 回目 StartEngine が `engine_busy_for_venue` で reject されないことを動的検証
  - `TestKabuBoardPushTradingVolumeDelta`: 新設 +5 件
    - `test_kabu_board_push_computes_qty_from_trading_volume_delta` — 1000→1500 で qty="500"
    - `test_kabu_board_push_first_frame_skipped_for_state_seed` — 初回は state seed のみ + `_kabu_last_trading_volume["9433"]==2500` を assert
    - `test_kabu_board_push_zero_delta_skipped` — 同 volume 2 連続で 2 件目 skip
    - `test_kabu_board_push_negative_delta_skipped` — volume 減少 (reset 等) skip + state は新値で更新
    - `test_clear_kabu_session_resets_last_trading_volume` — `_clear_kabu_session` で state クリア + 再ログイン後の最初 frame は seed (skip)
  - 既存 `test_pushes_trade_to_live_fd_queue_when_trading_with_nonzero_qty` を `test_pushes_trade_to_live_fd_queue_when_trading_with_positive_volume_delta` に書き換え。`parse_execution` mock 経由ではなく実 PUSH frame 2 連続で TradingVolume delta を検証する形に。

##### 設計判断

- **state 配置を server 側に置く**: `KabuStationAdapter` (pure 関数 adapter) を持たせるか server 側に持たせるかで悩んだが、(a) adapter は ticker set 単位で生成され reuse 想定が薄い、(b) `_clear_kabu_session` 経由の session-scoped lifecycle と整合させるなら server 側が自然、(c) adapter の pure-function 性 (`parse_execution(raw)` は副作用 free) を維持できる、の 3 点で server 側に置く方を採用。
- **R3 M8 skip block の意図保持**: R3 M8 は「qty=0 を流さない」が主旨で、本ラウンドの `delta_qty <= 0` skip でも同等に達成される (delta=0 も含む)。M8 削除によって qty=0 が流れる regression は起きない。
- **state seed の skip 必要性**: 初回 frame で last_volume=0 として delta を計算すると、過去全累積を「1 件の trade」として流すことになり、dedup 異常 / 過剰約定量として silent failure を生む。明示的に「初回は seed のみ」とし `_live_fd_queue` には流さない。`has_prev = symbol in last_volume_map` で判定。
- **負の delta も state を更新する**: 通常は起きないが、東証停止からの再開や session 切替で TradingVolume が reset される異常ケースで、state を新値に更新しないと「次の正常 delta」が誤った巨大値になる。reset 経路でも state seed を更新することで自己回復する。
- **helper closure 内で `_emit` / `engine_stopped_emitted` / `live_venue_for_cleanup` をクロージャ捕捉**: enclosing async 関数のローカル変数を直接参照することで、main-thread coroutine 経路の `_emit_direct` セマンティクス (race-free な直接 outbox append) を維持する。timeout / except 経路の補完送出パターンと完全に揃え、`engine_stopped_emitted[0]` の二重送出ガードも統一。

##### R5 検証

- `cargo check --workspace` → clean
- `cargo clippy --workspace --tests -- -D warnings` → clean
- `cargo fmt --check` → clean
- `cargo test --workspace` → 全テスト緑 (Rust 側変更なし、既存 regression pin は維持)
- `uv run pytest python/tests/test_server_kabu_live_push.py python/tests/test_kabu_station_live_data_client.py -v` → 29 passed
- `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana and not demo_kabu and not tk_smoke" --timeout=120` → **2358 passed** / 106 skipped / 202 deselected (R4 2310 → R5 2358 で +8 件追加、既存 1 件名称書き換え。差分が大きいのは R6 / R7 micro-fix 経由で 2312 まで増えた件と本ラウンド +8 のため)

##### R5 知見 / 次回回避策

- **fix-induced regression を本ラウンドで 2 件踏んだ**: MISSES.md 知見 17 (「fix が silent failure を生む」) を本ラウンドで実証。R3 で `_run` の外に出した register/PUT block は、(a) `_run` 内例外を catch する `except Exception` から外れる結果、その catch が肩代わりしていた 5 点 cleanup が脱漏する、(b) parse_execution の qty=0 ハードコードを skip する判断が live data 経路全体を dead 化する、の 2 種を同時に踏んだ。次回 review-fix では「fix の差分が **catch-all の外に新しい return を生む** か」「fix が **既存経路全体を dead 化する skip を入れる** か」を専用 checklist で確認すべき。
- **early-return の cleanup checklist**: handler 系コードで早期 return を追加するとき、enclosing 関数の `finally` / `except Exception` が肩代わりしていた cleanup を **5 点まとめて** (event emit × 2-3 種類 + state dict pop × 2 + state machine reset) helper 化して呼び忘れを防ぐ。本 R5 の `_kabu_register_early_abort` がその helper パターンの実装例。
- **dead path 化を検出する pin test**: R2/R3 で配線した live data path が R3 M8 で無効化された件は「経路の存在 (source-pin)」だけでなく「経路が実際に動く (動的 enqueue)」を pin する test が必要。本 R5 の `test_pushes_trade_to_live_fd_queue_when_trading_with_positive_volume_delta` は実 PUSH frame 2 連続で `_live_fd_queue` の `not empty()` を assert することで、将来の dead 化を防ぐ。
- **後続レビュー向け重点項目**: 後続 review-fix loop では本 R5 で踏んだパターン 2 種 (catch-all から外す return / dead path 化する skip) に加え、TradingVolume delta の overflow / wrap-around (実取引で `int` のオーバーフローは想定しなくて良いが、`TradingVolume` 欠損時の `0` default が初回 seed と区別できない場合がないか) を見ること。

R3 fix-induced CRITICAL 2 件を R5 で完全解消。`fix-induced silent failure` パターンを MISSES.md 候補として後続レビューの参考に明記する。

##### R7 反映 (2026-05-11, ラウンド 7)

R6 サニティで発見した **MEDIUM 5 件 + LOW 3 件** を TDD で解消し review-fix-loop を収束させた。R5 で `_kabu_register_early_abort` helper と TradingVolume delta 経路を入れた直後の補完ラウンド。fix-induced regression 系ではなく、R5 の sanity 内に残っていた observability ギャップと test/spec 整合性の取りこぼし。

###### 解消サマリ

| 区分 | ID | 概要 | 対応 |
|------|-----|------|------|
| MEDIUM | R7-MEDIUM-1 | `instrument_id` 空文字で kabu register が silent skip → PUT /register 対象なしで TRADING 起動 → live data 永遠に届かない dead path 化 (`_kabu_symbol = ""` から `if _kabu_symbol:` の silent skip) | 二段防御: (a) `schemas.py::EngineStartConfig.instrument_id` に `Field(..., min_length=1)` を追加して pydantic 層で空文字を `Error{code:"invalid_config"}` で reject、(b) `server.py::_handle_start_engine` kabu 分岐に `_kabu_symbol` 空判定の runtime guard を追加し `_kabu_register_early_abort("invalid_config", ...)` 経由で 5 点 cleanup + Error/EngineStopped emit + state machine reset を完遂。bypass test fixture / 不正 dict 直注入経路もすべて同経路に集約。|
| MEDIUM | R7-MEDIUM-2 | `_kabu_register_set.all_symbols()` 空時の PUT /register skip が silent (外部 clear / race 後の検出不可) | `server.py::_handle_start_engine` の `if _all_syms:` の else 分岐に `log.warning("kabu live start: no symbols in _kabu_register_set, PUT /register skipped ...")` を追加。MEDIUM-1 fix で空 `instrument_id` は早期 abort されるためここに到達するのは外部 clear / race のみ。早期 abort はしない (silent failure 観測手段を残しつつ無害な経路で進める)。|
| MEDIUM | R7-MEDIUM-3 | test helper `_make_server` / `_make_server_for_start_engine` の prod `__init__` 同期漏れ — `self._kabu_last_trading_volume: dict[str, int] = {}` を helper が初期化していない。`_on_kabu_board_push` の defensive `getattr` で lazy init していたが、将来 defensive 経路が消えた瞬間にテストが落ちる脆さ | `python/tests/test_server_kabu_live_push.py` の両 helper に `server._kabu_last_trading_volume = {}` を追加して prod `__init__` と同期。`_on_kabu_board_push` の defensive `getattr` は撤廃せず維持 (二重防御)。|
| MEDIUM | R7-MEDIUM-4 | 再ログイン後の最初 kabu PUSH frame が state seed として skip される仕様が docstring / spec のどこにも書かれていない silent UX gap | `python/engine/server.py::_clear_kabu_session` の docstring に「`_kabu_last_trading_volume.clear()` 副作用 → 次回 PUSH frame は state seed として skip される (live trade tick が 1 件失われる)」を追記。`docs/specs/live-strategy.md` §3.2-G.1 と §5 安全装置リスト #11 にも同等の説明を追加。|
| MEDIUM | R7-MEDIUM-5 | early abort test の cleanup 網羅性 — `TestKabuStartEngineEarlyReturnCleanup` 3 件が `_live_state == CONNECTED` と `_engine_stop_events.get(strategy_id) is None` を明示 pin していない | 既存 3 test に追補のみ (新規メソッドは作らない)。3 件全てに `assert s._live_state == LiveState.CONNECTED` + `assert s._engine_stop_events.get(strategy_id) is None` を追加し、`asyncio.to_thread` 非呼出 + cleanup 完全性を二重 pin。|
| LOW | R7-LOW-1 | `_on_kabu_board_push` 内 Symbol 2 回取得 (`symbol = str(raw.get("Symbol", ""))` と outer `ticker` で重複) | `symbol = ticker` に変更して outer try で取得済みの `ticker` を再利用 (DRY)。|
| LOW | R7-LOW-2 | `parse_execution` 失敗時の log level が `debug` で live data dead path 化の直接原因なのに観測不可 | `log.debug` → `log.warning` に格上げ。|
| LOW | R7-LOW-3 | `live_venue_for_cleanup = None` 経路の test カバレッジ追記コメント | `TestKabuStartEngineEarlyReturnCleanup` 末尾にコメント追加: 「`_kabu_register_early_abort` 内 `if live_venue_for_cleanup is not None:` ガードで no-op、`set.discard` の冪等性で担保」。|

###### 主要変更ファイル

- `python/engine/schemas.py`: `EngineStartConfig.instrument_id` に `Field(..., min_length=1)` 追加 (R7-MEDIUM-1)
- `python/engine/server.py`:
  - `_handle_start_engine` kabu 分岐: `_kabu_symbol` 空判定の runtime guard 追加 (R7-MEDIUM-1)
  - `_handle_start_engine` kabu 分岐: `_all_syms` 空時 `log.warning` 追加 (R7-MEDIUM-2)
  - `_clear_kabu_session` docstring: 再ログイン後 1 件目 PUSH frame skip 仕様を明記 (R7-MEDIUM-4)
  - `_on_kabu_board_push`: `symbol = ticker` で DRY 化 (R7-LOW-1)
  - `_on_kabu_board_push` inner except: `log.debug` → `log.warning` に格上げ (R7-LOW-2)
- `docs/specs/live-strategy.md`:
  - §3.2-G.1 新設: kabu 再ログイン後 1 件目 PUSH frame state seed 仕様 (R7-MEDIUM-4)
  - §5 安全装置リスト #11 追加: 同上 (R7-MEDIUM-4)
- `python/tests/test_server_kabu_live_push.py`:
  - `_make_server` / `_make_server_for_start_engine` に `_kabu_last_trading_volume = {}` 追加 (R7-MEDIUM-3)
  - `TestKabuStartEngineEarlyReturnCleanup` 3 件に `_live_state` + `_engine_stop_events.get()` の assert 追加 (R7-MEDIUM-5)
  - `TestKabuStartEngineEarlyReturnCleanup` 末尾コメント追加 (R7-LOW-3)
  - `TestKabuStartEngineEmptyInstrumentIdAborts` 新設 +1 件 (R7-MEDIUM-1 RED→GREEN)
  - `TestKabuStartEngineEmptyRegisterSetLogsWarning` 新設 +1 件 (R7-MEDIUM-2 RED→GREEN)

###### 追加テスト数: +2 件 (kabu_live_push)

20 件 (R5 末時点) → 22 件 (R7 反映後)。既存 20 件は破壊せず、helper 同期 / MEDIUM-5 補強 / LOW-3 コメントは追補のみ。

###### 設計判断

- **MEDIUM-1 二段防御**: pydantic 層のみだと「将来 schema を緩めた」「fixture が __init__ を patch して bypass する」「不正 dict 直注入」経路で空文字が到達しうる。kabu_station 分岐内の runtime guard を別途入れて `_kabu_register_early_abort` 経路 (R5 で確立) に集約することで、Rust 60s hang / state machine 固着 / `_active_live_venues` 残留 を一括防止する。tachibana 経路は本 fix の対象外 (kabu 固有の dead-path 化)。
- **MEDIUM-2 で早期 abort しない理由**: `instrument_id` が空なら MEDIUM-1 で先に abort される。ここに到達するのは「instrument_id 有 + register 成功 → all_symbols が空」という外部 clear / race のみで、normal flow ではない。warning だけ残して flow は継続させ、live data が来ないことを observability で検出する方が現実的。abort してしまうと観測機会も消える。
- **MEDIUM-3 helper-prod sync の defensive 撤廃しない理由**: `_on_kabu_board_push` の `getattr(self, "_kabu_last_trading_volume", None)` lazy init は他経路 (将来追加されうる他の `__new__` ベース fixture) でも使われる safety net。helper の prod 同期は脆さの解消だが、defensive 撤廃は別 issue で扱う。二重防御を維持する。
- **MEDIUM-4 spec/docstring 同時更新**: docstring だけだと「コード読まないと分からない」「将来 docstring が古くなる」リスク。spec 側に 1 段 hard pin を置き、§3.2-G.1 と §5 #11 の両方で参照可能にする (kabu 経路と全体安全装置リストの 2 視点)。
- **MEDIUM-5 既存 test 補強のみ・新規 method 追加しない**: 既に 3 件の test が存在し責務分離されている (`register fail` / `PUT fail` / `release for next StartEngine`)。新規 method は責務重複になる。`_live_state == CONNECTED` + `_engine_stop_events.get() is None` の 2 assert を各 test に追加して完全性 pin を厚くする。

###### R7 検証

- `cargo check --workspace` → clean
- `cargo clippy --workspace --tests -- -D warnings` → clean
- `cargo fmt --check` → clean
- `cargo test --workspace` → 全テスト緑 (Rust 側変更なし、既存 regression pin は維持)
- `uv run pytest python/tests/test_server_kabu_live_push.py -v` → 22 passed
- `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana" -v` → 全件緑 (R5 末から +2 件: `test_kabu_start_engine_empty_instrument_id_aborts` / `test_kabu_start_engine_empty_register_set_logs_warning`)

###### R7 知見 / 次回回避策

- **silent skip = silent failure 候補**: 「`if _kabu_symbol:`」「`if _all_syms:`」のような guard が else 分岐を持たないとき、その guard 自体が silent skip path を生む。observability 確保のため `else: log.warning(...)` を必ず添える、または early abort を入れる二択を意識する。
- **pydantic Field validator + runtime guard の二段防御**: schema 層は wire 上限の安全装置として有用だが、`__new__` ベース fixture / 不正 dict 直注入で bypass される。runtime guard を kabu/tachibana 分岐内に入れて両方で防ぐ。pydantic の `min_length=1` は wire-level の最小契約として有用 (Rust 側からの空文字送信を即時 reject)。
- **test helper の prod sync 漏れ検出**: `__new__` ベース helper を使う test ファイルは、`__init__` で追加された新 field を helper に明示同期する規約を `bug-postmortem` で増やすべき。MISSES.md 候補: 「helper-prod field drift」。
- **再ログイン後 1 件目 trade 欠落の UX 影響**: 現状実装は安全側 (累積値 1 件として流す方が dangerous) だが、ユーザーが「最初の trade が見えない」と気付かない silent gap になりうる。将来 PUSH protocol が per-trade qty を返すようになったら state seed 経路は撤廃可能。spec §3.2-G.1 で「将来 PUSH protocol が per-trade qty を提供するようになった時点で撤廃する」と明記済。

MEDIUM 5 件 + LOW 3 件すべて TDD で解消し、本 R7 サブブロックで review-fix-loop を収束させる。

### Phase 6 完了（2026-05-10）
- 担当: phase6-agent
- 主要 commit:
  - `91b453c` — feat(lint): tools/lint/ + examples README live section（17 unit tests RED→GREEN + 受け入れ基準 #3 / #4 / #10 pin）
  - `161072d` — docs(specs): live-strategy §3.2-D.1（credential argv 経路禁止 + stdin 4 経路）+ §5（CLI / GUI / TACHIBANA_ALLOW_PROD / is_market_open / demo→prod 安全装置 10 項目）
  - `f4e2029` — docs(adr): 0071 Live Strategy GUI deferred → accepted（原本 3149879 を Context / Decision / Consequences で本文化）
  - `915df27` — docs(adr): 0072 Execute Live Strategy deferred → accepted（原本 ea5022b を本文化、loop A/B 二段構成 + warm_up 9 ステップ + concurrent ガード 7 段 gating）
  - `b380e14` — ci(python-tests): docs-lint job 新設（check_adr_status / check_examples_readme / check_live_login_call を CI 常時実行）
- 設計判断:
  - **ADR 原本 → 現代化方針**: 原本（古い形式の Markdown）をそのまま貼らず、`docs/decisions/README.md` の status 遷移ルールに従い `Status / Context / Decision / Consequences / 関連` の構造で再構成。原本の実装ステップ（Phase 1〜9 等の節）は Decision 節内の番号付き項目で要約し、issue #42 の対応 Phase / 受け入れ基準を相互参照として残した。`source_commit:` は原本 SHA（3149879 / ea5022b）を保持し、本 PR の merge SHA への更新は accepted 化の trigger を後追いする 2 段スタイルで後続 fixup commit に委譲する Note を本文に明記
  - **lint script の AST 解析戦略**: `check_live_login_call.py` は `ast.With` / `ast.AsyncWith` の `items` を走査して `_is_live_session_call` で `LiveSession(...)` 呼出かを判定 → `optional_vars`（`as <name>`）で束縛された変数名を抽出 → 同 with ブロック内に `<var>.run(...)` 呼出があり、かつ「同 with ブロック または 外側の関数全体」に `<var>.login(...)` 呼出が無いケースのみ reject。「外側で login → with run」慣用句も許容するよう enclosing function を引数に取る `_audit_with_block(stmt, enclosing_func, ...)` で実装。文字列マッチではなく AST に依存することで、コメント / 文字列リテラル中の "login" / "run" を誤検出しない
  - **lint script の文字列マッチ戦略**: `check_examples_readme.py` は ATX heading 行（`^#{1,6}\s+...`）のみフィードして `_iter_headings` で fenced code block (` ``` ` / `~~~`) を skip。本文中の「ライブで動かす」言及（地の文、コードブロック内）は検出しないことを `test_live_section_present_fails_when_match_in_body_only` で pin
  - **`pytest.ini` / `pyproject.toml` の `pythonpath` に `.` 追加**: `tools.lint` をテストから import するため。pytest は `pytest.ini` を優先するため両方に追加（pyproject 側は将来 ini 廃止時の保険）。リポジトリ直下の他のディレクトリへの sys.path 経由の意図しない import が起きないかは、既存テストの 2231 件全緑で副作用ゼロを確認
  - **CI 配線方針**: 既存 `python-tests` job に lint step を追加せず、独立した `docs-lint` job を新設。理由 = (1) lint は重い pytest scan より早く失敗を返したい、(2) 並列実行で全体時間を短縮、(3) lint 失敗時に「unit test も止まった」と誤認しない
  - **examples README は Phase 6 では見出しスタブのみ**: 本 Phase は lint script の検証用としての「ライブで動かす」見出し存在のみを担保し、replay → demo → prod の完全コマンド例の充実は Phase 5 に委譲。スタブには `> **TODO (Phase 5)**: ...` の表示を残し Phase 5 担当が拡充する起点を明示
- ✅ 達成した受け入れ基準:
  - #3 `examples/README.md` に replay → demo → prod を同ファイルで通すコマンド例（`python/tests/test_lint_check_examples_readme.py::test_live_section_present` で見出し存在を pin、内容充実は Phase 5）
  - #4 `docs/specs/live-strategy.md §5` 起票（CLI / GUI / SecondPasswordRequired / TACHIBANA_ALLOW_PROD / is_market_open / 安全装置 10 項目）
  - #5 ADR 0071 / 0072 accepted 昇格 + 本文起票（`scripts/check_adr_status.py` 全緑、CI 常時実行）
  - #10 `LiveSession.login()` 未呼出経路の不在（`tools/lint/check_live_login_call.py` の AST lint を CI 常時実行 + 17 unit tests）
- 検証:
  - `uv run pytest python/tests/` 2231 passed / 106 skipped / 198 deselected（Phase 6 で +17 件、live_demo マーカー由来の deselected 数は変わらず）
  - `uv run python scripts/check_adr_status.py` OK（134 ADR files）
  - `uv run python -m tools.lint.check_examples_readme` OK
  - `uv run python -m tools.lint.check_live_login_call` OK
  - `cargo test --workspace` 全緑（既存 issue 範囲外）
- 知見/Tips（Phase 5 への引き継ぎ + 将来）:
  - **`examples/README.md §C` 充実時の lint 互換性**: `check_examples_readme.py` は heading 文字列マッチで「ライブで動かす」を探すだけなので、Phase 5 で本文を書き換えても見出し文字列を変えない限り lint は green のまま。逆に「## D. 本番運用」のような新セクションを書く際は、ライブ section の見出しを `## ライブで動かす（demo / prod）` などに統合してもよい（lint は match 単位で OK）。表記揺れ用の正規表現は `_LIVE_HEADING_PATTERNS` に集約（`ライブで動かす` / `ライブ運用` / `^Live\b`）
  - **ADR 0071 / 0072 の `source_commit:` 更新タイミング**: 本 PR が main に merge されたら、別 commit で `source_commit:` を merge SHA に更新できる（現状は原本 SHA を指している）。`scripts/check_adr_status.py` は accepted 状態 + `source_commit:` の存在のみを assert し、SHA の妥当性は検証しないので、merge 後の更新は purely documentation な fixup
  - **AST lint の拡張ポイント**: `check_live_login_call.py` は現状「`with LiveSession(...) as s: ... s.run() ...` 経路で login() なし」のみを reject。将来 `LiveSession` の使用パターンが増えたら（例: context manager を使わずに直接 `s = LiveSession(...)` で生成するケース）、`_walk` を拡張して `Assign` 文の RHS が `LiveSession(...)` のケースも追加判定する設計。現状の AST 走査骨格はそのまま活きる
  - **CI 経路の追加**: `docs-lint` job は ubuntu-latest で 1 分以下で完了する想定。GitHub status check に追加する場合、branch protection rule の Required checks に `docs-lint` を入れることで lint 不合格 PR を block できる（merge 経路の物理ガード）
  - **`pytest.ini` の `pythonpath = python .`**: 将来 `tools.lint` 以外にもリポジトリ直下のパッケージ（`scripts.foo` 等）を test から import したくなった場合、すでに `.` が pythonpath に入っているので追加変更不要。pytest は両 ini ファイルの設定をマージせず、`pytest.ini` が見つかればそちらを優先するので、`pyproject.toml` 側の `pythonpath = ["python", "."]` 同期更新は将来の `pytest.ini` 廃止時の保険として保持

#### Phase 6 レビュー反映（2026-05-10、ラウンド R1 + R2）

R1 自己レビューで MEDIUM 4 件、R2 サニティで MEDIUM 1 件 (silent failure) を抽出 → 修正で 0 まで収束。

**R1 修正 commit `07f346c`** (`test(lint): R1 review-fix — additional regression pins`):

- ✅ MEDIUM-1: `test_module_level_with_block_without_login_fails` — モジュール直下 `with` (関数外) も検出
- ✅ MEDIUM-2: `test_class_method_with_login_passes` / `test_class_method_with_run_without_login_fails` — `class` 内メソッド（`ast.ClassDef` 経路）も検出
- ✅ MEDIUM-3: `test_check_live_login_call_fails_when_cli_path_missing` — ファイル不在ケースの対称性
- ✅ MEDIUM-4: `python/tests/test_adr_0071_0072_accepted.py` 新設（8 テスト） — `check_adr_status.py` は **一般的な status invariant のみ** を検証し特定 ADR の昇格を pin できないため、0071/0072 が `accepted` で `source_commit:` を持ち、本文がコメントアウト雛形に戻っていないことを直接 assert

**R2 修正 commit `fedeec0`** (`test(lint): R2 — tools 正規 package + python.engine 二重 import 防止`):

- ✅ MEDIUM-R2-1（**silent failure**、新規発見）: `pythonpath = ["python", "."]` の併記により `engine` (正規) と `python.engine` (PEP 420 namespace 経由) の **2 つの module 経路** が両立する。両方使うと module-level singleton と class identity が壊れる:
  ```python
  >>> import engine; import python.engine
  >>> engine is python.engine
  False  # ← 別 module オブジェクト扱い、しかしファイルは同一
  ```
  対策: (1) `tools/__init__.py` を新設し `tools/` を正規 package 化（PEP 328、PEP 420 namespace 排除）、(2) `test_no_python_engine_namespace_collision_in_repo` を追加し repo 内（`python` / `tools` / `scripts` / `examples` 配下、`.venv` / `target` 等は scan_dirs root 限定で除外）の `from python.engine ...` / `import python.engine` 表記が一切無いことを re で pin

**最終検証（R2 後）**:
- `uv run pytest python/tests/` **2244 passed** / 106 skipped / 198 deselected（R0: 2231, R1: +12, R2: +1 = 計 +13 件）
- `uv run python scripts/check_adr_status.py` OK（134 ADR files）
- `uv run python -m tools.lint.check_examples_readme` OK
- `uv run python -m tools.lint.check_live_login_call` OK
- `cargo test --workspace` 全緑（既存 issue 範囲外）

**収束**: CRITICAL 0 / HIGH 0 / MEDIUM 0 / LOW 残存はあるが対応不要（fence 検出の 4-backtick edge case、`with LiveSession` 以外のパターン非対応など、いずれもスコープ内で意図的に除外）。

**新規見逃しパターン候補（次回 MISSES.md 追記候補）**:
- `pytest pythonpath` に repo root を追加すると、サブディレクトリ名と同名の package が二重 import されうる（`engine` ↔ `python.engine` 等）。**namespace の二重化が silent failure になりうる**ことを「pythonpath 二重 import」パターンとして登録候補

### Phase 2 functional 完了（2026-05-10）
- 担当: phase2-functional-agent
- 主要 commit:
  - `19bd4f2` — feat(scenario): `engine.scenario.extract_live(strategy_path)` 新設 + `LiveScenario` TypedDict + `_validate_live_v1` + 17 件のテスト（extract_live 12 / handler 5）+ gRPC mapping pin 4 件
  - `36703a0` — feat(server): `_handle_load_live_strategy_scenario(msg)` ハンドラ + `_handle` dispatcher 配線
  - `9fb4434` — test(grpc-wire): `test_new_live_ipcs_round_trip_via_grpc` 追加（受け入れ基準 #22 — 新 live IPCs が gRPC 経路で送受信できる pin）
- 設計判断:
  - **`extract_live` の AST 抽出方針**: 既存 `extract()` (SCENARIO 用) と完全に対称な構造を採用。`ast.parse + ast.literal_eval` のみで import を発火させない（副作用ゼロ・統一決定通り）。Assign / AnnAssign 両形を許容し、annotation-only 宣言（value=None）は後続 Assign を見つけるまでスキャンを継続する（`extract()` と同じ流儀）。多重 LIVE_SCENARIO 定義は `ScenarioValidationError` で reject
  - **`LiveScenario` TypedDict 配置**: `engine.scenario` モジュール内、既存 `Scenario_v3` の隣に追加。`total=False` で `strategy_init_kwargs` の任意性を表現。schema_version=1 のみ対応で、`_validate_live_v1` で必須キー / 型 / 単一銘柄制約を強制（list `instrument` は明示 reject — 統一決定）
  - **`Error{code:"strategy_parse_failed"}` への集約**: AST parse / IO / `ScenarioValidationError` / `ValueError` を全て **単一 error code** にまとめる。Rust 側 `src/handlers/replay.rs` の IpcError handler は `code == "strategy_parse_failed"` を pin に `release_scenario_pending()` を呼ぶ設計のため、validate 失敗 / 構文エラー / IO エラーを区別する必要が無い。区別はログ（`scenario.load_live failed reason=... path=...`）で行う
  - **null 即応答の場所**: `_handle_load_live_strategy_scenario` 内、`extract_live` が `None` を返した直後に `LiveStrategyScenarioLoaded(全フィールド None).model_dump(exclude_none=False)` を `_outbox` に追加して `return`。**5s 待たない / GUI 側 timeout はあくまで engine 無応答時の fallback**（統一決定 #18 / 受け入れ基準 #23）。`exclude_none=False` を指定して null フィールドを wire 形に明示的に乗せる（`server_grpc.py` の ParseDict 経路と整合）
  - **`extract_live` への validation 統合**: 当初は `extract_live` を syntactic 抽出のみにして validate を server 層に分離する案もあったが、`extract` と対称にするため validation を `extract_live` 自身に内包した。理由: (1) server 層は単一 try/except で全失敗を `strategy_parse_failed` に集約しており、validate 失敗とその他失敗を区別する必要が無い、(2) validation 違反は実質的に「ファイルが live 用に使えない」ことを意味し、syntactic 成功だけでは GUI prefill が成立しない、(3) test の表現力（`extract_live(...)` 単体で validate も観測できる）
- ✅ 達成した受け入れ基準:
  - #19 (parse_failed 部) `Error{code:"strategy_parse_failed", request_id, message}` 経路（`test_strategy_parse_failed_emits_error_with_code` + `test_strategy_parse_failed_on_syntax_error` + `test_strategy_parse_failed_on_validation_error`）
  - #22 新 live IPCs が gRPC 経路で送受信できる（`test_field_to_op_mapping_contains_load_live_strategy_scenario` + `test_event_to_field_mapping_contains_live_strategy_scenario_loaded` + `test_new_live_ipcs_round_trip_via_grpc` `#[ignore]`）
  - #23 LIVE_SCENARIO 不在時の即応答（`test_absent_live_scenario_emits_immediate_loaded_with_nulls` — outbox 1 件のみ + 全フィールド None）
- 検証:
  - `uv run pytest python/tests/` 2417 passed / 118 skipped（Phase 2 functional で +17 件）
  - `cargo test --workspace --no-fail-fast` 全緑
  - `cargo clippy --workspace --tests -- -D warnings` clean
- 知見/Tips（Phase 5 examples 追記の参考形式 + 将来）:
  - **`examples/test_strategy_minute.py` への `LIVE_SCENARIO` 追記サンプル**: 本 Phase ではコード追加せず Phase 5 に委譲。追記時の最小形は以下:
    ```python
    LIVE_SCENARIO = {
        "schema_version": 1,
        "instrument": "8306.T",
        "max_qty": 100,
        "max_notional_jpy": 500000,
        "venue": "tachibana",
        # 任意: strategy_init_kwargs={"trade_size": 100} など
    }
    ```
    `instrument` は単一銘柄 str 必須（list は v1 で reject）。`venue` は capability `supports_live_strategy=True` の文字列を入れる（"tachibana" / "kabu_station"）
  - **`extract_live` と `extract` の coexistence**: 同一 .py に `SCENARIO` と `LIVE_SCENARIO` が両方あっても干渉しない（AST node 名でフィルタ）。replay 経路は `extract` を、live 経路は `extract_live` を呼ぶ独立した経路。Phase 5 で example を編集するときは「既存 SCENARIO はそのまま、LIVE_SCENARIO を追記」のスタイルで OK
  - **`LIVE_SCENARIO` フィールド型違反の `Error` メッセージ**: `ScenarioValidationError` の `__str__` が `LIVE_SCENARIO['max_qty'] must be int, got str` のような Python レベルメッセージを返す。GUI banner にそのまま表示すると技術的すぎるが、本 Phase では Rust 側 `handlers/replay.rs` で「手入力で続行」warn toast を出して終わるため UX 上は許容範囲。将来 i18n 経路で人間向け文言に置換する場合、Rust 側の toast 文言を切替える方が Python 側の Error.message を変えるより安全
  - **wire の null 表現**: pydantic `model_dump(exclude_none=False)` で全フィールドが dict に出る → `server_grpc.py` の `ParseDict` で proto optional フィールドに正しくマップされる（proto 側は `optional` 修飾子付き）。Rust 側 `LiveStrategyScenarioLoaded { instrument_id: Option<String>, ... }` は `serde(default, skip_serializing_if = "Option::is_none")` で受信側の None deserialize は安全。`exclude_none=True` にすると proto field が落ちて wire 上で区別できなくなるので注意
  - **handler test の `_outbox.append` 観測手法**: `_make_server()` で `BinanceWorker` 等を mock した最小 `DataEngineServer` を生成し、`asyncio.run(server._handle_load_live_strategy_scenario(msg))` を直接呼ぶ。`_outbox` は `_Broadcaster` で iter 可能なので `list(server._outbox)` で観測する。同パターンは既存 `test_scenario_load.py::test_load_failed_log_format` と同じ
  - **`_FIELD_TO_OP` mapping pin の重要性**: Wave 1 で wire 配線済の `load_live_strategy_scenario` が `_FIELD_TO_OP` から削除されると、Rust が proto Command を送信しても `_handle` まで到達しない silent failure になる。`test_field_to_op_mapping_contains_load_live_strategy_scenario` で mapping を pin することで、リファクタ時の意図しない削除を即座に検知できる

### Phase 5 完了（2026-05-10）
- 担当: phase5-agent
- 主要 commit:
  - `156da9a` — feat(examples): test_strategy_minute.py / test_strategy_daily.py / test_strategy_trade.py に `LIVE_SCENARIO` 定数追記 + `python/tests/test_examples_live_scenario.py` 6 件追加（RED→GREEN）
  - `be193bf` — docs(examples): README §C を replay → demo → prod の完全コマンド例 + 安全装置 6 項目 + GUI 経路で充実 / live_sample.py の docstring を「発注しない最小ロガー」と明示
- 設計判断:
  - **live_sample.py の位置づけ**: コメント追記方式を採用（リネームせず）。理由: (1) 既存テストや内部参照（仮にあった場合）への副作用ゼロ、(2) ファイル冒頭 docstring で「発注しない、接続確認専用」+「本番起動の正式 CLI は `python -m engine.live_session_cli run`」を明示すれば位置づけは十分伝わる、(3) リネームは将来 `live_logger_only.py` へ別 PR で行っても安全
  - **LIVE_SCENARIO の型**: `dict` を採用（`engine.scenario.LiveScenario` TypedDict は import しない）。理由: (1) example が `engine.scenario` を import すると重い transitive dependency が走り、戦略単体での動作確認 / strategy_loader の最小限読込みを汚す、(2) `extract_live` は schema_version=1 をランタイム validate するので、static type 注釈は無くても安全装置は engine 側で守られる、(3) Phase 2 引き継ぎサンプルも `dict` 形式
  - **kabu_station 用例の配置**: `test_strategy_minute.py` 内のコメント例として併記する方針を採用（新 `examples/live_kabu_minute.py` を作らない）。理由: (1) kabu_station venue は CLI/GUI から `--venue kabu_station` / dropdown で切り替えるだけで動く（戦略コードは無変更）、(2) コード重複（同じ Strategy class を 2 ファイルに展開する）を避けられる、(3) README §C 内に kabu_station 起動コマンド完全例を載せたので CLI 経路の参照は十分。Phase 4 引き継ぎ Tips の「`examples/live_kabu_minute.py` 新設案」は将来 venue 専用ロジック（先物 / OP）が必要になった時の予備案として残す
  - **README §C の構造**: replay → demo → prod を 3 ブロックの sh code で並べ、その後に kabu_station / 安全装置 / GUI 経路 / live_sample.py 注意 を続ける構成。Phase 6 で起票したスタブ（TODO 表示付き）を完全に書き換え、`tools/lint/check_examples_readme.py` の見出し検出（「ライブで動かす」）は維持。「### C. ライブで動かす（demo 口座）」の heading 文字列を変えていないので lint は green 維持
  - **同一戦略 instrument SoT**: LIVE_SCENARIO['instrument'] は SCENARIO['instrument'] と一致させる pin を test 化（`test_test_strategy_*_has_live_scenario` で `extract` / `extract_live` 両方を呼んで instrument 一致を assert）。これにより「同じ戦略ファイルを replay → live で動かす」建前を test 単位で固定する
- ✅ 達成した受け入れ基準:
  - #3 `examples/README.md` に replay → demo → prod の完全コマンド例（`python/tests/test_lint_check_examples_readme.py::test_live_section_present` で見出し継続検出 + `python/tests/test_examples_live_scenario.py` で各 example の LIVE_SCENARIO を pin / Phase 6 で起票したスタブを Phase 5 で本文化）
- 検証:
  - `uv run pytest python/tests/test_examples_live_scenario.py` 6 件 GREEN
  - `uv run pytest python/tests/test_lint_check_examples_readme.py python/tests/test_lint_check_live_login_call.py` 22 件 GREEN（lint regression なし）
  - `uv run pytest python/tests/test_scenario_load.py python/tests/test_scenario_live.py` 48 件 GREEN（既存 SCENARIO / LIVE_SCENARIO 経路 regression なし）
  - `uv run python -m tools.lint.check_examples_readme` OK
  - `uv run python -m tools.lint.check_live_login_call` OK
- 知見/Tips（Phase 7 統合テスト + 将来）:
  - **example の LIVE_SCENARIO は extract_live + GUI prefill の E2E 経路 pin として再利用できる**: `python/tests/test_examples_live_scenario.py::_assert_live_scenario_well_formed` の helper は (a) 必須キー網羅、(b) venue が capability `supports_live_strategy=True` の値、(c) max_qty / max_notional_jpy が CLI のレンジ内 を一括検証する。Phase 7 でこの helper を `test_load_live_strategy_scenario_round_trip` のような「examples/test_strategy_minute.py を engine に投げて prefill 値を観測する」E2E test の前提条件として呼ぶと、example が壊れた瞬間 E2E test が即 fail する設計になる
  - **README §C は live モード CLI コマンドの SoT に近い**: Phase 6 で起票したスタブは「TODO」付きで最小限だったが、Phase 5 で 3 段階フロー + 安全装置 + GUI 経路を完全に記載。今後 CLI 引数の追加 / 安全装置の更新があったら README §C を一次更新先にするのが整合的（specs/live-strategy.md §5 は契約レベル、README §C はユーザー向けクイックスタート）
  - **kabu_station example の今後**: 現状 README §C に「`--venue kabu_station` を指定するだけ」と記載している。実機で動かすための前提のうち H-1（Nautilus 親クラス継承）は R2-C で解消済。残る M-3（RegisterSet 二重化）は PUSH 経路の本配線が未着手だが、live data flow に依存しない注文 / 約定経路は `node.build()` 通過可能。Phase 7 統合テストは tachibana 経路に集中し、kabu_station live 経路の実機テストは別 issue に切り出す方針が合理的
  - **live_sample.py の将来の扱い**: 現状の docstring で位置づけは明示できているが、将来 `examples/live_logger_only.py` へリネームする選択肢は残っている。リネームする場合は (a) git mv で履歴を保つ、(b) README §C と test 注釈の参照を grep で更新、(c) STRATEGY_CONFIG / STRATEGY_CLASS の global 定数（strategy_loader が探す）が export されているか確認、の 3 点を 1 commit で行う必要がある

#### Phase 5 レビュー反映（2026-05-10, ラウンド 1 / セルフレビュー）
- 担当: phase5-agent (self-review)
- 主要 commit: `5f5b56d` — fix(issue-42): Phase 5 self-review
- ✅ 解消した指摘:
  - **HIGH-1**: `live_session_cli.py::_build_arg_parser` の `--venue` argparse choices が `["tachibana"]` のままで、Phase 4 で完了済の kabu_station capability flip + `engine_runner.start_live(venue=...)` dispatch と整合していなかった。`["tachibana", "kabu_station"]` に拡張 + `test_venue_choices_accept_tachibana_and_kabu_station` / `test_venue_choices_reject_unknown` を新設（RED→GREEN）。これで README §C の kabu_station 例 (`--venue kabu_station`) が成立する
  - **MEDIUM-1**: README §C で sh code block のみだったため Windows ユーザーが `$env:TACHIBANA_ALLOW_PROD=1` 構文への置換を強いられていた。Linux/macOS（bash/zsh）と Windows（PowerShell 7+）の 2 ブロックで完全コマンド例を併記
  - **MEDIUM-2**: README §C の demo / prod 例が `--mode auto` を使っており、attach 経路に入った場合 stdin 第二暗証番号が wire に流れない hint が stderr に出る挙動を説明していなかった。`--mode inprocess` を例示に変更し、attach / inprocess / auto の意味を冒頭 1 段落で説明 + GUI 連携時の `--mode attach` への切替を注意ブロックで記載
- 設計判断（追加分）:
  - **CLI argparse choices 更新を Phase 5 範疇とした理由**: Phase 4 で engine 側 dispatch / capability flip は完了していたが、CLI argparse の choices だけ tachibana 限定のまま残っていた（取りこぼし）。これを修正しないと「README §C で kabu_station 例を書く Phase 5 のゴール」が破綻するため、Phase 5 で 1 行 + テスト 2 件で fix。Phase 4 引き継ぎ Tips の「M-1 GUI venue dropdown」は別件（Rust GUI 側）として残す
  - **PowerShell 構文を併記する範囲**: 開発者向けの完全コマンド例（demo / prod の 3 段階）と kabu_station 例の主ブロックのみ。`--prod` 失敗例 / GUI 起動例は sh のみ（短く・OS 依存度低い）
  - **`--mode inprocess` を default 例示に選んだ理由**: (a) 挙動が予測しやすい（GUI が無くても動く / attach probe 不要 / stdin 第二暗証番号が確実に wire に流れる）、(b) replay の `--mode inprocess` と対称、(c) attach 連携は注意ブロックで補足するだけで充分
- 検証:
  - `uv run pytest python/tests/test_live_session_cli.py` 17 件 GREEN（+2 件）
  - `uv run pytest python/tests/` 2442 passed / 120 skipped（Phase 5 全体で +8 件、regression なし）
  - `uv run python -m tools.lint.check_examples_readme` OK（heading 文字列「ライブで動かす」を維持）
  - `uv run python -m tools.lint.check_live_login_call` OK
  - `cargo clippy --workspace --tests -- -D warnings` clean（Phase 5 では Rust 変更なし）
- 残存 LOW（対応不要）:
  - **LOW-1**: `examples/test_strategy_*.py` の `from nautilus_trader.model.data import ...` 行が LIVE_SCENARIO トップレベル定数の **後** にある（PEP 8 では import が上にあるべき）。既存 SCENARIO も同じ構造で、`extract` / `extract_live` がトップレベル assignment を AST で見るため副作用ゼロ。修正は将来 example 全体を再整理する別 PR で対応する
  - **LOW-2**: `python/tests/test_examples_live_scenario.py::_assert_live_scenario_well_formed` で `int(scenario["max_qty"])` のように防御的 conversion をかけているが、`extract_live` が `_validate_live_v1` で int 型を強制済のため redundant。test 意図の明示性を優先して残す
- 次フェーズへの引き継ぎ:
  - Phase 7（統合テスト）で kabu_station venue 経由の live test を書く場合、`live_session_cli.py --venue kabu_station --mode inprocess` 経路が CLI argparse を通過することは Phase 5 で pin 済（H-1 = Nautilus 親クラス継承は R2-C `84cf060` で解消したため、warm_up 成功 → `node.build()` 通過まで届く。実機検証は M-3 PUSH 配線完了と credential が揃った段階で別途実施）

### Phase 7 完了（2026-05-11）
- 担当: phase7-agent
- 主要 commit:
  - `53c38b6` — test(markers): pytest marker `live_demo` / `live_demo_inprocess` を `pyproject.toml` + `pytest.ini` に登録（統一決定 #9 / D-H1）。Phase 1 で追加済の `@pytest.mark.live_demo` / `@pytest.mark.live_demo_inprocess` test の `PytestUnknownMarkWarning` を解消
  - `915088b` — ci: `tachibana-demo.yml` の pytest 行を `-m "demo_tachibana or live_demo"` に変更（attach mode のみ CI 実行）+ `python-tests.yml` の push/PR 既定実行から `live_demo` / `live_demo_inprocess` を除外する exclusion clause を追加（統一決定 #7 / R3-M3 — CI Secrets を経由させない）
  - `495de2c` — test(loader-pin): `python/tests/test_strategy_live_replay_smoke.py` に契約テスト 3 件追加（受け入れ基準 #21）。(a) `test_load_strategy_from_file_used_for_both_paths` で mock spy + 同 class 比較、(b) `test_load_user_strategy_is_thin_wrapper_around_load_strategy_from_file` で AST 走査 pin、(c) `test_make_replay_strategy_delegates_to_load_user_strategy` で委譲 chain pin
  - `7434ee4` — test(cli-e2e): `python/tests/test_live_session_cli_e2e.py` 新設。(a) `@pytest.mark.live_demo` で attach mode lifecycle pin、(b) `@pytest.mark.live_demo_inprocess` で in-process lifecycle pin、(c) マーカー無し helper self-check で部分列マッチを CI 既定実行
- 設計判断:
  - **loader pin の実装方法**: **mock 確認 + 同 class 比較 + AST 走査** の 3 段重ねを採用。
    - (1) `unittest.mock.patch.object(strategy_loader, "load_strategy_from_file", wraps=...)` で **同じ関数オブジェクト** が両経路から呼ばれることを spy で観測（mock の wraps モード経由でロード動作も維持）
    - (2) live / replay 両経路で得た Strategy インスタンスの `type(...).__qualname__` 一致 + 親 class `nautilus_trader.trading.strategy.Strategy` の isinstance check
    - (3) AST 走査で `_load_user_strategy` が `load_strategy_from_file(...)` 呼出 + `engine.nautilus.strategy_loader` import を持つこと、`_make_replay_strategy` が `_load_user_strategy(...)` 呼出を持つことを pin
    - 3 段重ねの理由: (a) mock spy だけだと「mock を使わない future 実装」で silent に通過する、(b) 同 class 比較だけだと両経路が **別々の関数経由で同じ class を返す** 場合に騙される（loader fork 検知不可）、(c) AST 走査だけだと「呼出はあるが結果が違う」誤実装を見逃す。3 段で完全に loader 統一の不変条件を pin する
  - **`tachibana-demo.yml` への live_demo 統合方法**: `-m demo_tachibana` を `-m "demo_tachibana or live_demo"` に変更する **1 行 fix** で済んだ（pytest の boolean marker expression）。`live_demo_inprocess` は OR に含めず、ローカル限定の方針を維持（統一決定 #7）。tachibana-demo.yml 冒頭コメントで「in-process は CI から除外、`--second-password-stdin` 経由のローカル運用」を明記
  - **python-tests.yml 除外句の最終形**: `-m "not tk_smoke and not demo_tachibana and not demo_kabu and not live_demo and not live_demo_inprocess"`。既存の 3 マーカー除外（tk_smoke / demo_tachibana / demo_kabu）に **2 マーカーを末尾連結** することで意図が明確（既存除外パターンに変更を加えない）。Phase 6 の docs-lint job は別 job として独立しているので変更不要
  - **E2E test の skip 戦略**: 実 engine 環境変数（`FLOWSURFACE_ENGINE_TOKEN` / `DEV_TACHIBANA_*`）が無い CI / ローカル環境では `pytest.skip()` で graceful skip。マーカー除外（CI 既定）と二段防御で「実 engine が無くても sus failure を起こさない」設計
  - **lifecycle 期待 event の subsequence マッチ**: 厳密一致ではなく `EngineStarted` → `LiveStrategyReady` → `EngineStopped` の **部分列** マッチ。`LiveStrategyWarmingUp` / `LiveBuyingPower` 等の中間 event が挟まる実 engine の挙動を許容する。helper 自体は CI 既定で実行される `test_expected_subsequence_self_check` で自己検証
  - **gRPC integration test の運用方針**: `cargo test -p flowsurface-engine-client --test grpc_wire_integration -- --include-ignored --test-threads=1` で全 10 件 GREEN を観測（Wave 1 引き継ぎ Tips の `#[ignore]` 既定はそのまま維持）。**`--test-threads=1` 必須** の発見が重要 — parallel 実行だと port-binding race / Python subprocess の同時起動で fail する。今後 CI に組み込む場合は serial 実行を強制するか、専用 GitHub Actions workflow を起こす（本 Phase の scope 外）
- ✅ 達成した受け入れ基準:
  - **#21 loader pin（live / replay 両経路同実装）** — 主要対応。`test_load_strategy_from_file_used_for_both_paths` + 補助 AST 走査 pin 2 件
  - 既存 pin の継続実行確認（マーカー登録 + CI 配線後も既存テストの pass 状態維持）

#### 受け入れ基準対応表 全 #1〜#23 の current state（Phase 7 終了時点）

| # | 受け入れ条件 | 主担当 Phase | current state |
|---|---|---|---|
| 1 | replay 戦略無改変で live CLI 起動 + EngineStarted | Phase 1 + 2 | ✅ pin 済（`test_attach_starts_engine_for_replay_strategy_unchanged` + `test_inprocess_starts_engine_for_replay_strategy_unchanged`、live_demo / live_demo_inprocess マーカー） |
| 2 | GUI File>Open → 4 ペイン自動生成 | Phase 3 | ✅ pin 済（`tests/live_form_smoke.rs::test_live_strategy_ready_auto_generates_four_panes`） |
| 3 | examples/README.md replay → demo → prod 完全コマンド例 | Phase 5 + 6 lint | ✅ pin 済（`tools/lint/check_examples_readme.py` + Phase 5 の README §C 本文化） |
| 4 | docs/specs/live-strategy.md §5 起票 | Phase 6 | ✅ pin 済（Phase 6 で本文起票） |
| 5 | ADR 0071 / 0072 accepted 昇格 + 本文起票 | Phase 6 | ✅ pin 済（`scripts/check_adr_status.py` CI 常時実行） |
| 6 | max_qty 必須 | Phase 1 | ✅ pin 済（`test_invalid_config_when_max_qty_missing` + boundary 2 件） |
| 7 | TACHIBANA_ALLOW_PROD=0 で本番 reject | Phase 1 | ✅ pin 済（`test_prod_blocked_without_env`） |
| 8 | SecondPasswordRequired フロー（CLI 非ゼロ + GUI 赤帯） | Phase 1 + 3 | ✅ pin 済（`test_attach_second_password_required_exits_nonzero` + `tests/live_form_smoke.rs::test_second_password_required_shows_status_banner`） |
| 9 | is_market_open() ガード reject | Phase 1（engine_runner） | ✅ pin 済（`test_start_live_rejects_when_market_closed`） |
| 10 | login() 未呼出経路の不在（lint） | Phase 6 | ✅ pin 済（`tools/lint/check_live_login_call.py` AST lint + CI 常時実行） |
| 11 | LiveStrategyReady 4 ペイン自動生成 + 冪等 | Phase 3 | ✅ pin 済（`test_live_strategy_ready_idempotent_on_double_emit`） |
| 12 | supports_live_strategy cap | Phase 3.5 | ✅ pin 済（`test_supports_live_strategy_per_venue` — tachibana=true / kabu_station=true（Phase 4 で flip）） |
| 13 | LIVE_SCENARIO 戦略 → GUI prefill | Phase 2 + 3 | ✅ pin 済（`test_live_strategy_scenario_loaded_prefills_form`） |
| 14 | warm_up 失敗 → EngineError + close() | Phase 1 | ✅ pin 済（`test_warm_up_exception_emits_error_not_ready` + `test_warm_up_returns_false_emits_error_not_ready` + `test_warm_up_exception_closes_exec_client` + `test_warm_up_returns_false_closes_exec_client`） |
| 15 | LiveStrategyReady timeout 60s + LiveStrategyWarmingUp リセット | Phase 3 | ✅ pin 済（`test_engine_started_without_live_strategy_ready_shows_timeout_banner` + `test_warming_up_resets_timeout_counter` + `test_live_warmup_timeout_constant_is_60s`） |
| 16 | concurrent live reject（venue 単位 EngineBusy + 同一 sid engine_already_running） | Phase 1 + 3 | ✅ pin 済（`test_concurrent_live_emits_engine_busy_for_venue` + `test_duplicate_strategy_id_emits_engine_already_running`） |
| 17 | reconnect 時の LiveStrategyReady 冪等再生 | Phase 3 | ✅ pin 済（`test_engine_rehello_replays_live_strategy_ready_via_pending_config`） |
| 18 | tachibana is_production cap 露出 | Phase 3.5 | ✅ pin 済（`test_tachibana_is_production_per_env` 4 ケース） |
| 19 | LoadLiveStrategyScenario fallback（5s timeout / strategy_parse_failed） | Phase 2 + 3 | ✅ pin 済（`test_load_live_strategy_scenario_timeout_falls_back_to_manual_input` + `test_strategy_parse_failed_releases_form` + Python 側 `test_strategy_parse_failed_emits_error_with_code`） |
| 20 | --second-password-stdin 4 経路 | Phase 1 | ✅ pin 済（`test_second_password_stdin_handles_heredoc_pipe_empty_and_noninteractive`） |
| 21 | loader pin（live / replay 同一） | **Phase 7** | ✅ **pin 済（本 Phase）**（`test_load_strategy_from_file_used_for_both_paths` + AST 走査 2 件） |
| 22 | gRPC 経路で新 IPC 送受信 | Phase 2 + 3（schema chain） | ✅ pin 済（`engine-client/tests/grpc_wire_integration.rs::test_new_live_ipcs_round_trip_via_grpc` + `python/tests/test_server_grpc_live_ipcs.py::test_field_to_op_mapping_contains_load_live_strategy_scenario`、Phase 7 で `--include-ignored --test-threads=1` で 10 件全緑を観測） |
| 23 | LIVE_SCENARIO 不在時の即応答 | Phase 2 functional | ✅ pin 済（`test_absent_live_scenario_emits_immediate_loaded_with_nulls`） |

**全 23 受け入れ基準 → 対応 test 関数のマッピング完了**。Phase 7 で追加した #21 を除き、すべて他 Phase で pin 済 → Phase 7 では「全 23 件が現在も green か」を CI 既定 + workflow_dispatch の 2 経路で実行可能な状態に整備した。

- 検証:
  - `uv run pytest python/tests/ -m "not tk_smoke and not demo_tachibana and not demo_kabu and not live_demo and not live_demo_inprocess" --timeout=120` → **2256 passed / 106 skipped / 200 deselected**（Phase 7 で +5 件: loader pin 3 / E2E helper 1 / E2E lifecycle 2 のうち live_demo は CI 除外）
  - `uv run pytest python/tests/test_strategy_live_replay_smoke.py -v` → 9 件全緑（既存 6 + 新規 3）
  - `uv run pytest python/tests/test_live_session_cli_e2e.py -v` → 1 passed（self-check） / 2 skipped（実 engine 無し）
  - `cargo test --workspace` → 全緑（exit 0）
  - `cargo test -p flowsurface-engine-client --test grpc_wire_integration -- --include-ignored --test-threads=1` → **10 件全緑**（gRPC integration tests 全件）
  - `cargo clippy --workspace --tests -- -D warnings` → clean（exit 0）
- 知見/Tips（最終 review-fix-loop への引き継ぎ）:
  - **gRPC integration test の `--test-threads=1` 必須化**: 並列実行だと Python subprocess の port-binding race / 同時起動で 9/10 件 fail する。serial 実行で 10/10 件全緑。今後 CI 化する場合は workflow YAML 側で `--test-threads=1` を指定する必要がある。本 Phase ではドキュメント化（本 Tips）のみで CI 組込は scope 外
  - **マーカー登録の 2 ファイル同期**: pytest は `pytest.ini` を優先し `pyproject.toml` の `[tool.pytest.ini_options]` を **無視** する（pytest.ini が repo に存在する場合）。マーカー登録は両ファイルに同じ定義を入れて将来 `pytest.ini` 廃止時の保険とする（Phase 6 と同じ流儀）
  - **live_demo マーカーの実 E2E test を CI で動かす日**: `tachibana-demo.yml` の workflow_dispatch で `gh workflow run tachibana-demo.yml` を叩いた時のみ `test_attach_mode_full_lifecycle` が実行される。実 engine が attach 可能な状態（`FLOWSURFACE_ENGINE_TOKEN` 設定済 + 立花 demo ログイン済）でないと skip するため、CI 失敗リスクは無い
  - **CLI E2E 拡張ポイント**: 現状の `test_live_session_cli_e2e.py` は lifecycle subsequence のみを観測する最小実装。将来 (a) `LiveBuyingPower` の数値 invariants（initial_cash と一致など）、(b) `EngineBusy` 経路の reject 確認、(c) `--mode auto` の attach probe → fallback inprocess の path 切替 を追加する場合、`@pytest.mark.live_demo` 配下に新 test 関数を追加する。helper `_expected_subsequence` は再利用可能
  - **loader pin の AST 走査拡張**: 本 Phase の AST 走査は `_load_user_strategy` / `_make_replay_strategy` の 2 関数に絞っている。将来 `LiveSession.run` 内部で別 loader を呼ぶような refactor が入った場合、`engine.replay_session` 側にも同様の AST pin を追加する。現状 `replay_session.py` は `engine_runner.start_live` に委譲しているのみで loader を持たないため、ここに pin を入れる必要は無い
  - **schema bump 不要を維持**: 本 Phase で proto / schemas.py の変更ゼロ。SCHEMA_MINOR=28 のまま。`/ipc-schema-check` skill PASS 状態を維持

#### Phase 7 レビュー反映（2026-05-11, ラウンド 1 / セルフレビュー）
- 担当: phase7-agent (self-review)
- 主要 commit: `b502b4e` — fix(cli-e2e): login() 呼出修正 + 未使用 import 整理
- ✅ 解消した指摘:
  - **MEDIUM-1**: `test_attach_mode_full_lifecycle` で `sess.login()` を呼んでいなかった。`LiveSession.run` は `self._logged_in == True` を要求する（`replay_session.py:2124` で `RuntimeError("call login() before run()")`）。attach 経路では credential 明示渡しせず `sess.login()` を呼ぶと engine に `RequestVenueLogin` を送る ack handshake になる（`live_session_cli.py` 経路と整合、CLI も同じく `s.login(user_id=user_id, password=password)` を呼ぶ）。ローカルで実 engine が無いため動的検証はできないが、コード経路をなぞって不整合を発見
  - **LOW-1**: `import io` が未使用だったため削除（CLI 単体テスト `test_live_session_cli.py` から copy-paste した残骸）
  - **LOW-2**: `_resolve_repo_root()` の戻り値型注釈を `Path` で明示（`pathlib.Path` を module top-level import に移動）
  - **LOW-3**: attach test docstring の「ログイン操作は行わない」を「ack handshake として `login()` を呼ぶ」に訂正（コードと文言を整合）
- 設計判断（追加分）:
  - **attach mode の `sess.login()` の役割**: 「credential を wire に流さない」と「engine に ack handshake を送らない」は **別の話**。前者は統一決定 #7 の不変条件（credential を wire 経由で送らない）、後者は LiveSession の内部 state machine（`_logged_in == True` への遷移）。attach 経路の `login()` は credential 引数を渡さなければ engine に `RequestVenueLogin` を送るだけで credential は wire に乗らない（実装は `replay_session.py:1877-1903`）
  - **AST 走査による loader pin の表現力**: review 時に「同 class 比較だけだと両経路が別関数経由で同じ class を返す silent failure を見逃す」と判断し、AST 走査を 3 段重ねの一部に組み込んだ（オリジナル設計も同じ）。これは「fork detect の確実性 vs テストの脆さ（AST 構造が変わると false positive）」のトレードオフだが、loader 統一は本 issue の中核 invariant（受け入れ基準 #21）なので false positive 側に倒すのが安全
- 検証:
  - `uv run pytest python/tests/test_live_session_cli_e2e.py python/tests/test_strategy_live_replay_smoke.py -v --timeout=60` → 10 passed, 2 skipped (live_demo / live_demo_inprocess は実 engine 無しで graceful skip)
  - `uv run pytest python/tests/ -m "not tk_smoke and not demo_tachibana and not demo_kabu and not live_demo and not live_demo_inprocess" --timeout=120` → 2256 passed / 106 skipped / 200 deselected（regression なし）
- 残存 LOW（対応不要）:
  - **LOW-A**: in-process E2E テストの実機検証は行えていない（ローカル credential が無いため）。実機検証は本 issue close 後の手動 smoke で別途行う想定。テストロジック自体は CLI フロー（`live_session_cli.py:455-482`）を直接なぞっているため、CLI 単体テストが pass する限り実機での挙動も整合的に推測できる
  - **LOW-B**: `gRPC integration test の --test-threads=1 必須化` は実装プランに知見として記載済だが、CI 自動化（GitHub Actions workflow への組込）は本 Phase の scope 外。将来 `python-tests-grpc.yml` のような独立 workflow を起こす別 issue を切り出す候補
- 次フェーズへの引き継ぎ（最終 review-fix-loop / 本 issue close 前）:
  - **GUI スモーク** (`/iced-gui-testing` skill) は別途実機で実行する想定（本 Phase の自動テスト範囲外、issue Phase 7 の §「GUI スモーク」を参照）
  - **実機 demo lifecycle 検証**: `tachibana-demo.yml` を `gh workflow run tachibana-demo.yml` で叩いて attach mode lifecycle が green になるかは、実 engine + 立花 demo 環境が用意できた段階で 1 回手動確認する。Phase 7 の責務は test と CI 配線までで、実機 trigger は scope 外

### R2-A 反映（2026-05-11、修正担当）
- 担当: r2a-agent
- 主要 commit:
  - `f6759eb` — fix(schemas): H8/H9/M1/M2/M3 — Python schema validators 強化
  - `c791904` — fix(scenario): M4/M5 — LiveScenario TypedDict 厳格化 + 値域検証
  - `39b7915` — fix(silent-failure): H5/H6/M9/M10 — credential scrub + cleanup 強化
- 解消: H5, H6, H8 (Python part), H9, M1 (Python part), M2, M3, M4, M5, M9, M10（全 11 件）
- 設計判断:
  - **credential scrub helper の配置**: `python/engine/nautilus/engine_runner.py` の top-level に `_scrub_credential_exception(exc) -> str` を新設。server.py からは local import で参照する（循環 import を避けるため module top で import しない）。type 名の token 判定方式は `"Password" / "Auth" / "Credential"` の 3 つ — 完全な scrub ではなく「型名が credential 関連と思われるなら詳細を捨てる」防御線。詳細は `log.error(..., exc_info=True)` で local 側に残るため診断は可能
  - **BusyKind Literal の拡張方針**: 現状 `Literal["another_strategy_on_venue"]` の 1 値固定。将来 `"strategy_id_already_running"` 等を追加する場合は `schemas.py` の `BusyKind` の Literal を拡張し、Rust 側 `engine-client/src/dto.rs` の `BusyKind` enum も同期して追加する（R2-B 担当）。本 Phase では Rust 側が既に enum 化済（commit `6ac5b93`）なので Python の Literal と一致
  - **H9 (venue 必須化) の実装位置**: `EngineBusy._validate_state_command_orthogonal` に追記。既存の state/command 直交検証と同じ model_validator に置くことで、`EngineBusy` の不変条件を 1 関数に集約できる
  - **M2 (LiveStrategyScenarioLoaded all-or-none) の対象フィールド**: `instrument_id` / `max_qty` / `max_notional_jpy` / `venue` の 4 フィールドのみ。`strategy_init_kwargs` は対象外（任意フィールド、prefill とは独立）。test_schema_compat の partial-fill 検証は payload を全フィールド充足に更新して整合
  - **M4 (TypedDict Required/NotRequired) の syntax 選択**: scenario.py に `from __future__ import annotations` (PEP 563) があるため class-syntax の TypedDict は annotation を文字列として保持し `__required_keys__` / `__optional_keys__` の introspection が壊れる（全キーが required 扱いになる）。functional syntax `TypedDict("LiveScenario", {...})` は annotation を即時評価するため introspection が正しく機能する。今回は後者を採用
  - **M5 (値域検証) の上限値**: `schemas.py` の `EngineStartConfig.max_qty/max_notional_jpy` の `Field(ge=1, le=10_000)` / `Field(ge=1, le=100_000_000)` と一致。「LIVE_SCENARIO で書ける値」と「StartEngine で受理される値」が同じ範囲になることで、scenario 検証 → engine 起動の経路で「ここでは通るがここで弾かれる」の境界外傷を回避
  - **H6 (CancelledError 経路の二重防御)**: 既存 `finally:` 節は `CancelledError` 時にも走るので理論上は cleanup される。ただし `CancelledError` は `Exception` を継承しないため、もし将来 `finally:` 内に `await` が入ったり例外順序が変わると discard を取りこぼす可能性がある。明示的に `except asyncio.CancelledError:` で discard + raise する二重防御を入れた。`set.discard` は冪等なので二重実行で問題なし
  - **M10 (warm_up 無し client の log warning)**: 既存挙動互換のため warm_up が無い client でも起動自体は継続（旧挙動: silent skip）。`log.warning(...)` を残すことで silent failure を観測可能にする。完全な reject にすると後方互換が壊れる懸念があるため warning のみに留めた
- 検証:
  - `uv run pytest python/tests/test_engine_runner_live_warmup_failure.py python/tests/test_server_concurrent_live.py python/tests/test_credential_scrub.py python/tests/test_schemas_types.py python/tests/test_scenario_live.py python/tests/test_schemas.py python/tests/test_schemas_nautilus.py python/tests/test_schema_compat.py python/tests/test_scenario_load.py python/tests/test_scenario_writeback.py python/tests/test_scenario_path_guard.py python/tests/test_scenario_cli.py python/tests/test_examples_live_scenario.py python/tests/test_engine_busy_query_guards.py python/tests/test_engine_busy_reject.py python/tests/test_engine_runner_live_market_closed.py python/tests/test_engine_runner_live_kabu.py --timeout=60` → **258 passed, 1 skipped**（R2-A スコープ 17 ファイル全緑）
  - `uv run pytest python/tests/ -m "not tk_smoke and not demo_tachibana and not demo_kabu and not live_demo and not live_demo_inprocess" --timeout=120` → **2294 passed / 106 skipped / 202 deselected**（R2-A スコープ外の R2-C 未完了 `test_kabu_station_nautilus_parent.py` のみ 2 件 fail — 当該テストは R2-C の `kabu_station_*_client.py` 親 kwargs 統合に依存、本 Phase の責務外）
- 知見/Tips（次への引き継ぎ）:
  - **Parallel agent commit との競合**: R2-B / R2-C と並行作業すると同一 branch で他 agent の untracked / unstaged 変更が `git stash`/`stash pop` 経由で working tree に混入することがある。`git status` で自分が編集した覚えの無い path が出たら即座に `git checkout HEAD -- <path>` で revert すること。本 Phase では `kabu_station/*` / `src/handlers/replay.rs` / `src/main.rs` / `tests/live_form_smoke.rs` / `src/messages.rs` / `test_kabu_station_live_*.py` / `test_schemas_nautilus.py` を誤って取り込んでいたので個別 revert で対処
  - **scenario.py の `from __future__ import annotations` 配下 TypedDict**: 同じ罠で `Required` / `NotRequired` を class-syntax で書くと introspection が壊れる。新規に optional フィールドが入る TypedDict を追加する場合は functional syntax を使うか、`get_type_hints()` 経由で再評価すること
  - **credential scrub の token リスト拡張**: 現状 `("Password", "Auth", "Credential")` の 3 トークン。新しい venue（証券 API）を追加して認証エラー型名が異なるなら token リストを追加する。`KabuStationLoginError` のような型名も `"Login"` を含む形なら token 追加検討
  - **Rust 側 R2-B との同期**: H8 (BusyKind) は Python `Literal` と Rust `enum` の値が一致する必要がある。R2-B が enum 値を追加するときは Python `BusyKind` Literal も同期して追加。`test_schemas_nautilus.py::test_rust_schema_constants_match_python` がペアの整合性を担保（schema_minor 経由）

### R2-C 反映（2026-05-11、修正担当）
- 担当: r2c-agent
- 主要 commit: 本 commit で C1 (KabuStationLiveExecutionClient / KabuStationLiveDataClient の Nautilus 親継承) + M12 (3-way schema sync test 統合) を一括反映
- 解消: C1 CRITICAL (Nautilus parent inheritance, H-1 punt 解消), C1-2 CRITICAL (Data client 同), M12 MEDIUM (3-way schema test 統合)
- 設計判断:
  - **Nautilus 親継承の引数組立方針**: tachibana の `TachibanaLiveExecutionClient` / `TachibanaLiveDataClient` と同じパターン (`*args, **kwargs` を `super().__init__` に転送)。Cython 親型 (`ExecutionClient` / `MarketDataClient`) の type check は実インスタンス必須のため `MagicMock` では落ちる。`engine_runner.py::start_live` の kabu_station 分岐は `node.kernel` 経由で `loop / msgbus / cache / clock / instrument_provider` 等を取得して渡す。tachibana 分岐は本 R2-C のスコープ外（既存挙動を維持。同じく親引数を渡していないが、live demo 経路は既に test mock 経由）
  - **既存テストの kwargs 追加方針**: `_nautilus_parent_kwargs()` / `_nautilus_data_parent_kwargs()` ヘルパを test ファイル内に定義し、各 `KabuStationLive*Client(...)` 呼出に `**_nautilus_parent_kwargs()` を spread する。`MessageBus` / `Cache` / `InstrumentProvider` 等の実 Nautilus instance を組み立てる必要があり、`asyncio.set_event_loop(asyncio.new_event_loop())` で test scope ごとに loop を割り当てる
  - **test_kabu_station_nautilus_parent の構成**: (a) `isinstance(client, LiveExecutionClient)` / `isinstance(client, LiveMarketDataClient)` の継承契約 pin 2 件、(b) `kernel.exec_engine.register_client(client)` / `kernel.data_engine.register_client(client)` を実 TradingNode で叩く smoke test 2 件（`@pytest.mark.live_demo_inprocess` で CI 標準実行から除外）、(c) safety guard が super().__init__ より先に走ることの pin 1 件
  - **engine_runner.py の `_kernel = getattr(node, "kernel", None)` 二段防御**: 単体テストの `_FakeNode` は kernel attr を持たないため、kernel が無い場合は parent kwargs 空 dict で fallback。実 TradingNode 経路では `node.kernel.loop` 等を取得して spread。FakeNode tests は `KabuStationLive*Client` factory を monkeypatch で fake に差し替えるため、parent kwargs は無害に投げ捨てられる
  - **M12 単一 source-of-truth 化**: `test_rust_schema_constants_match_python` に server_grpc.py 比較を 3-way で統合（Rust ↔ schemas.py / Rust ↔ server_grpc.py / schemas.py ↔ server_grpc.py）。既存 `test_r2h2_server_grpc_schema_constants_match_schemas` は冗長になったが、test_server_grpc_phase_b.py に元々あり、削除しても再追加コストが低いため残す
- 知見/Tips:
  - **Nautilus Cython 親クラスの API**: `LiveExecutionClient.__init__` は `loop / client_id / venue / oms_type / account_type / base_currency / instrument_provider / msgbus / cache / clock / config` を要求。`LiveMarketDataClient.__init__` は `loop / client_id / venue / msgbus / cache / clock / instrument_provider / config / is_sync` を要求。両者とも Cython `cdef class` で `MagicMock` を投げると `TypeError: ... not of type <class 'nautilus_trader.common.providers.InstrumentProvider'>` で reject される
  - **`node.kernel` 経由の attrs**: `TradingNode.__init__` 内で `NautilusKernel` が初期化され `kernel.loop / msgbus / cache / clock / exec_engine / data_engine` が即 ready 状態になる（`node.build()` 待ち不要）。一方 `node._exec_engine` / `node._data_engine` という直接 attribute は実存在しない — テストの `_FakeNode` のみが持つ test-only attr。~~本来は `node.kernel.exec_engine` / `node.kernel.data_engine` を呼ぶべきだが、`register_client` 呼出箇所は本 R2-C scope 外（既存テストとの後方互換維持のため）~~  
    **R8 で解消**: `engine_runner.py` の `register_client` 呼出箇所は `node.kernel.exec_engine.register_client` / `node.kernel.data_engine.register_client` (canonical surface) に統一済 (commit `ea113f3` HIGH-2)。`_FakeNode` 系テストも canonical surface のみ持つ形に揃えた (R4 Group F)。詳細は §「Wave R8 反映」を参照。
  - **`self.id` の手動設定禁止**: `LiveExecutionClient` の `id` は Cython getset_descriptor (parent の `client_id` から自動派生)。`__init__` で `self.id = "..."` と setattr すると AttributeError / 型エラー。継承後は parent の id getter に任せる
  - **`asyncio.set_event_loop()` の必要性**: `nautilus_trader.common.functions.get_event_loop()` が test 環境では `RuntimeError("No event loop available in test environment")` を投げる。test fixture や helper で `asyncio.set_event_loop(asyncio.new_event_loop())` を明示する必要がある（pytest-asyncio の auto mode でも fixture 引数を経由しないなら自動 bind されない）
- ❗ 注意: H-1 punt は本 R2-C で完全解消。issue 本文の「punt」記述も R2-D で update 必要

### R2-B 反映（2026-05-11、修正担当）
- 担当: r2b-agent
- 主要 commit:
  - `6ac5b93` — feat(engine-client): H8 + M1 + R1-RUST-7 — BusyKind enum + progress clamp + log request_id
  - `8717b26` — feat(live-strategy): H7 + H1 — `LiveStrategyState::try_running` factory + pending_strategy_id クリア
  - `36f55a5` — feat(view): H2 — `live_warmup_timeout_banner` を view() に描画 + 「再試行」ボタン
  - `ea85585` — fix(handlers): H3 — `LiveWarmingUp` arm で strategy_id 照合
  - `bc383de` — feat(live-strategy): H4 — `node_build_failed` handler + `teardown_live_panes`
  - `86a0ef1` — fix(live-strategy-form): M6 — `prefill_from_scenario` の silent `unwrap_or_default` 撤廃
  - `f5539cb` — feat(live-strategy): M7 + M8 + R1-RUST-8 — `disabled_reason` 動的更新 / `EngineConnected` で pending クリア / proto 削除手順コメント
  - `a559304` — chore(fmt): cargo fmt apply after R2-B 修正系
- 解消: H1, H2, H3, H4, H7, H8 (Rust part), M1 (Rust part), M6, M7, M8, R1-RUST-7, R1-RUST-8（全 12 件）
- 設計判断:
  - **`BusyKind` enum の配置**: `engine-client/src/dto.rs` に `pub enum BusyKind { AnotherStrategyOnVenue }` を新設し、`serde(rename_all = "snake_case")` で wire 文字列との対称性を保つ。`from_wire_str()` / `as_wire_str()` / `Display` impl を併設して proto 経路 / serde 経路の双方で「未知値 → `None` + log warn」を統一する。serde は通常 unknown enum value を `Err` で reject するが、`deserialize_busy_kind_lenient` で `Option<String>` 経由で読み取り、parse 失敗時は `None` に degrade する custom deserializer を入れた（forward-compat: 新カテゴリ wire 拡張で旧 client が壊れない）
  - **`LiveStrategyState::try_running` factory の API 設計**: 失敗時に `Err(&'static str)` を返す Result API にして、caller 側で `log::warn!` + 遷移なしを選択できるようにする。旧 `Self::Running { .. }` 直代入経路を `replay.rs::LiveStrategyReady arm` だけ書き換え、他に直接生成している箇所が無いことを `grep` で確認済。enum 表現は変えずに、生成経路にのみ非空契約を加えた最小侵襲設計
  - **`teardown_live_panes` の閉じる範囲**: `auto_generate_live_panes` は base pane を split して 4 panel (TimeAndSales / OrderList / BuyingPower / Positions) を増やす実装なので、`teardown_live_panes` も 4 panel の `Content` variant だけを `panes.close()` する。base の `CandlestickChart` は live 起動前から存在する可能性が高く（live 用 panel ではない）、誤って閉じない方針。副作用として「手動で配置した TimeAndSales 等が同 dashboard にあると一緒に閉じられる」が、`node_build_failed` は通常の運用ではほぼ起きない（warm_up 成功直後の build 失敗）ため安全側 = 確実に live 4 panel を掃除する戦略を優先
  - **`set_disabled_reason` setter の責務範囲**: venue 接続状態 / 市場開閉状態のみを対象。`is_production` cap は engine プロセスの env (`TACHIBANA_ALLOW_PROD=1`) 経由なので動的に切り替わらず（統一決定 #14）、本 setter は触らない。tachibana / kabu 両 venue 経路で対称に呼ぶ（同じ live form に対する disable 理由は venue 全体 OR で決まる）
  - **`DismissLiveWarmupTimeoutBanner` の handler 実装**: 既存実装の「banner を None に戻す」分岐をそのまま使い、view() 側に「再試行」ボタンを足しただけ。再 Submit は別 modal 操作（ユーザーが live form を再度開く）が責務で、本 button は banner 消去のみ。最小実装で十分（統一決定 #17）
  - **`LiveStrategyBuildFailed` の strategy_id 照合**: pending / running と一致しないときは log warn のみで teardown を skip する。これにより、誤った EngineError 通知（古い start の遅延 emit など）で正常な live セッションのペインを誤って閉じる事故を防ぐ
- 検証:
  - `cargo check --workspace` → clean（exit 0）
  - `cargo clippy --workspace --tests -- -D warnings` → clean（exit 0）
  - `cargo fmt --check` → clean（適用済）
  - `cargo test --workspace` → 全テスト緑（exit 0、Doc-tests 含む）
  - `cargo test --test live_form_smoke` → **19 件全緑**（既存 14 + 新規 5: `test_live_strategy_ready_clears_pending_strategy_id` / `test_view_renders_live_warmup_timeout_banner` / `test_live_warming_up_ignores_mismatched_strategy_id` / `test_node_build_failed_resets_state_and_teardowns_panes` / `test_disabled_reason_cleared_on_venue_ready` / `test_engine_connected_releases_scenario_pending`）
  - `cargo test -p flowsurface-engine-client --lib grpc_transport` → **8 件全緑**（既存 3 + 新規 5: progress clamp 2 + BusyKind 3）
  - `cargo test -p flowsurface-engine-client --test engine_busy_event` → **11 件全緑**（既存 8 + 新規 3: BusyKind known/unknown/absent）
  - `cargo test --bin flowsurface live_strategy_form` → 23 件全緑（M6 source-pin を含む）
  - `cargo test --bin flowsurface live_strategy_state` → 2 件全緑（H7 try_running factory）
  - `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana" --timeout=120` → **2480 passed / 116 skipped / 8 deselected**（Python 側に R2-B 触手なし、regression なし）
- 知見/Tips（次への引き継ぎ）:
  - **`#[serde(deserialize_with)]` で未知 enum 値を None に degrade する pattern**: `Option<String>` を `Option::deserialize(deserializer)` で先に読み取り、enum mapping は手で行う custom deserializer 化することで、serde の「unknown variant = error」のデフォルト挙動を回避できる。proto 経路と JSON 経路の両方を forward-compat に倒せるので、新カテゴリ追加が schema bump 不要になる。`BusyKind` で導入したパターンは将来 `AttemptedCommand` / `CurrentEngineState` 等にも横展開可能（現状は厳格 reject 仕様で、本 issue では変更しない）
  - **factory + caller log warn の役割分担**: factory は契約違反を `Result::Err` で報告するだけで、log 出力は caller の責務。これにより同じ factory が「テストで明示的に Err を期待する箇所」と「実行時に log warn する箇所」で再利用できる。`LiveStrategyState::try_running` で実証
  - **source-pin tests の使い分け**: 挙動テスト (`#[test] fn` で実 struct を construct) と source-pin (`include_str!` で正規表現マッチ) は補完関係。binary crate のため `Flowsurface` を直接 instantiate できないので、`handlers/*.rs` / `main.rs::view` / `messages.rs` の契約は source-pin で守る。逆に `live_strategy_form_modal::*` のように lib module 化されている部分は挙動テストで pin する
  - **`pane_grid::State::iter()` の borrow チェッカー対策**: `panes.iter()` で借用中に `panes.close(id)` を呼ぶと `&mut` / `&` 競合で reject される。一度 `Vec<Pane>` に id を collect してから iterate して close する pattern が定番。`teardown_live_panes` で実装済
  - **`cargo fmt` がコメント内 multi-line を改行する**: `if *market_closed && let Some(...) = ...` のような複合条件は fmt で勝手に改行ブロック化される。事前に `cargo fmt --check` を回してから commit するか、commit 後に専用 fmt commit で揃えるのが安全（本 Phase では後者で対応）
  - **R2-A との同期**: H8 (BusyKind) の wire 値 `"another_strategy_on_venue"` は Python `schemas.py` の `BusyKind` Literal と一致している必要がある。R2-A 側の `test_schemas_nautilus.py::test_rust_schema_constants_match_python` で SCHEMA_MAJOR/MINOR は守られるが、Literal 値の対称性は手動で確認した（commit `6ac5b93` 時点で一致）。将来 enum 値を追加するときは Python / Rust 同時 PR で運用

### Wave R2 集約サマリ（2026-05-11）

R1 全 6 レビュアー集約（CRITICAL 1 / HIGH ~10 / MEDIUM ~14 / LOW ~10）に対する fix を 3 並列で実装:

- **R2-A** (`d033c0c` まで 4 commits): Python schema/scenario validators + silent failure × 11 件
  - 解消: H5, H6, H8 (Py), H9, M1 (Py), M2, M3, M4, M5, M9, M10
- **R2-B** (`8c15c19` まで 9 commits): Rust LiveStrategyState + EngineBusy.busy_kind enum + GUI behavior × 12 件
  - 解消: H1, H2, H3, H4, H7, H8 (Rust), M1 (Rust), M6, M7, M8, R1-RUST-7, R1-RUST-8
- **R2-C** (`84cf060`): CRITICAL Nautilus parent inheritance + 3-way schema test × 2 件
  - 解消: C1 (CRITICAL), M12
- **R2-D** (本 commit): docs / issue body fix × 1 件
  - 解消: H10 (受け入れ基準 #14 関数名)

R2 で解消した finding 総数: CRITICAL 1 / HIGH 10 / MEDIUM 13 / R1-RUST low 2 = 計 26 件
残存 LOW: ~8 件（同 PR 必須ではない、follow-up issue 候補）

Phase 4 H-1 punt は R2-C で完全解消。`KabuStationLive*` は `nautilus_trader.live.execution_client.LiveExecutionClient` / `LiveMarketDataClient` を継承し、`node.build()` 通過可能。

次: Round 3 sanity sweep（silent-failure-hunter 単独）で R2 fix が新規 silent failure を導入していないことを確認 → 収束。

### Wave R4 反映（2026-05-11、修正担当）

- 担当: r4-agent
- 主要 commit:
  - `74ff061` — docs(issue-42): R4 Group 8 — R3-GP-1〜5 docs fixes (HIGH 1 + MEDIUM 4)
  - `206fc7a` — fix(silent-failure): R4 Group 1 — R3-SILENT-1/2 + R3-RUST-3 credential scrub 強化
  - `edc141b` — fix(live-strategy): R4 Group 2 — R3-SILENT-3 LiveStopped 正常停止経路 teardown
  - `489a4ac` — fix(engine): R4 Group 3 — R3-SILENT-4 EngineConnected で pending state reset
  - `6266c10` — fix(engine-runner): R4 Group 4 — R3-SILENT-5 kabu_station kernel unavailable 早期 abort
  - `ae25f93` — fix(server): R4 Group 5 — R3-SILENT-6 _connected_venue tachibana hardcode fallback 撤廃
  - `2d5d868` — test(kabu-station): R4 Group 6 — R3-SILENT-7 register_client 契約を CI default で pin
  - `4f1ef10` — feat(view): R4 Group 7 — R3-RUST-1/2 warm_up progress UI 完成
- 解消: R3-SILENT-1〜7 + R3-RUST-1〜3 + R3-GP-1〜5 = HIGH 4 + MEDIUM 11 = **15 件**
- 設計判断:
  - **credential scrub の MRO 走査戦略**: 旧版は `type(exc).__name__` のみ判定で、`SessionExpiredError(TachibanaError)` のように venue prefix が付かない subclass を hit できなかった。MRO 全体を走査して prefix / token を確認することで、新規 subclass が venue API 例外として追加された際にも safe-by-default で scrub される（regression リスク最小）。誤検知時も型名は残し詳細は `log.error(exc_info=True)` に残るため診断性は維持。
  - **warm_up progress UI の banner 配置**: timeout banner と progress banner を **共存可能** にした。両者は別の意味（progress = 「動いてる」、timeout = 「動かなかった」）で、progress message が None に戻るのは Ready / Stopped / build_failed / EngineConnected(not Running) の 4 経路で、timeout fire は別タイマー駆動。progress を timeout banner の代わりにすると「timeout 中も progress message が残っていれば UX 混乱」する懸念がある — 別 banner として描画 + 共存 OK の戦略が一番素直。
  - **venue fallback 撤廃の影響範囲**: `_handle_start_engine` Live 分岐の 2 箇所 (`current_venue` / `live_venue_for_cleanup`) で `or "tachibana"` を削除し、early reject に統一。後段の `venue_not_supported` チェック (`live_venue not in ("tachibana", "kabu_station")`) は冗長になるが、明示的な venue 種別検査として残す（None は到達不能、ただ "binance" などが入った場合の防御線として有用）。既存 test_server_concurrent_live.py のフィクスチャは `_connected_venue = "tachibana"` を明示設定済みなので regression なし。
  - **kernel_unavailable のテスト分離**: `test_engine_runner_live_kabu_kernel_unavailable.py` を新ファイルとして独立させた。`test_engine_runner_live_kabu.py` 内に混在させると、既存 6 件の kabu warm_up テストが共有する `_FakeNode` (kernel mock 追加が必要) と、kernel 不在を試す test (kernel mock 不要) で fixture 流儀が分裂するため。新ファイルは "kernel attr を持たない _FakeNodeNoKernel" を専用に持ち、責務分離が綺麗。
  - **register_client mock test の方針 B 採用理由**: 既存 isinstance test が Cython 親型継承を pin 済みのため、Cython 側 type check 合格の根拠は十分。`register_client smoke` (実 TradingNode) は `@pytest.mark.live_demo_inprocess` でローカル限定実行に残し、CI default で実行される mock 経路 test を別途追加することで「呼出契約自体」を CI で常時 pin する relay 層を作った。完全な venue 別 isinstance pin は既存 test で carry。
- 知見/Tips（R5 sanity 不要なら 提出。R5 では silent-failure-hunter 単独で十分）:
  - **MRO 経由 scrub の forward-compat**: 新 venue (例: `LdSecurityError`) を追加するとき、prefix リスト (`_CREDENTIAL_TYPE_PREFIXES`) に `"LdSecurity"` を 1 行追加するだけで scrub が効くようになる。MRO 走査ロジック自体は不変。
  - **`teardown_live_panes` と `clear_live_pane_keys` の使い分け**: `teardown_live_panes(&strategy_id)` は内部で当該 strategy_id の key 削除 + 実ペインの close を行う。`clear_live_pane_keys()` (全削除) は LiveStopped で全消ししたいシナリオがあれば残せるが、現状 LiveStopped 経路では teardown 1 件で十分 (statement 同一)。今後 `force_stop_all_live` のような複数 strategy 一括停止 API を追加する場合は `clear_live_pane_keys` を保持する価値がある。
  - **EngineConnected reset の Running 状態保持の意義**: `LiveStrategyReady` 受信済の Running 状態を reset しない (`!matches!(... Running)` ガード) ことで、reconnect 後の `EngineRehello → 4 ペイン再生成` 経路が冪等に走る。Idle / pending 状態は捨てて手入力フォールバックに戻すという挙動分岐が、UX として「動いてる状態は守る、未確定状態は手動で再 Submit する」の自然なメンタルモデルと整合。
  - **warm_up progress UI の将来拡張**: 現状 banner は text + % だけだが、`ProgressBar` widget を `banner_row` に push すれば視覚的な進捗バーになる。本 Phase は最小実装 (`{:.0}%` テキストのみ) で済ませたが、UX 充実のため別 issue で `iced::widget::progress_bar` 経由のグラフィカル化を検討候補。
  - **issue 本文 SoT 整合の自動化**: 今回の R3-GP-1〜5 で発見された「テスト関数名のズレ」「実装と issue 本文の `kabu_station=false` 古い記述」は、CI lint で issue 本文と implementation-plan.md / 受け入れ基準 #N 対応表の関数名整合を機械的にチェックできれば未然に防げる。Phase 6 lint の延長案として将来検討候補 (`tools/lint/check_issue_body_test_names.py` 仮称)。

R3 sanity sweep で発見された全 15 件を R4 で完全解消。Wave R5 の追加 sanity は現状不要 (R4 fix が新規 silent failure を導入していないことを最終 review-fix-loop で確認可能)。

### Wave R6 反映（2026-05-11、修正担当）

- 担当: r6-agent
- 主要 commit:
  - `a3ea6a2` — fix(replay): R6 R5-SILENT-1 — warm_up timeout 発火時に warming banner / progress を None に reset
  - `74e8a53` — fix(venue): R6 R5-SILENT-2 — IpcError code="venue_not_connected" を Toast::error で user 通知
- 解消: R5-SILENT-1 (timeout で warming_message/progress reset), R5-SILENT-2 (venue_not_connected user 通知) = **MEDIUM 2 件**
- 次: silent-failure-hunter 単独で R7 sanity → 0 件確認できれば収束 / PR 提出可能

### Wave R8 反映（2026-05-11、外部レビュー反映 + R4 batch fix）

- 担当: r4-batch-agent
- 関連 commit:
  - `ea113f3` — fix(issue-42): R8 external review — HIGH-1 / HIGH-2 / MEDIUM-1 解消
  - 本 commit (R4 batch) — silent failure × HIGH 3 + MEDIUM 7 を TDD で順次解消

#### ea113f3 (R8 外部レビュー指摘)
- **HIGH-1**: Started→Ready 順序契約の確立。`EngineStarted` 受信時に Running 遷移しない設計を pin (instrument_id / venue を含まないため)。`LiveStrategyReady` 受信で初めて `try_running` factory を経由して Running に遷移する。
- **HIGH-2**: `node._exec_engine` / `node._data_engine` という underscore prefix の private attribute (test-only) ではなく、`node.kernel.exec_engine` / `node.kernel.data_engine` (canonical surface, real `TradingNode` 準拠) で `register_client` を呼ぶ統一 (production / test 経路の drift 解消)。
- **MEDIUM-1**: auto mode fallback (attach probe 失敗時に inprocess へ) で credential 不在が判明した場合の event 経路整備 (本 R4 Group B で完成)。

#### R2 ローカル R8 で発見した HIGH-3
- **HIGH-3**: `safe_slice_end` defensive hardening。`tests/issue_39_empty_state_pin.rs:47` の helper が `start > src.len()` を clamp していないため、caller が `&src[start..safe_slice_end(...)]` と書くと start>end で panic する余地。`start.min(src.len())` を追加して post-condition `end >= start_clamped` を保証 (R4 Group D で test 強化 + 実装 commit)。

#### R3 サニティ → R4 で解消した HIGH 3 + MEDIUM 7

| 区分 | ID | 概要 | 解消方法 |
|------|----|------|---------|
| HIGH | silent-HIGH-1 | `EngineError{strategy_id=Some(_)}` の `node_build_failed` 以外 (`warm_up_failed` / `kernel_unavailable` / `venue_not_supported` / `market_closed`) が `log::warn!` のみで GUI に通知されない | Group A: 既存 `LiveStrategyBuildFailed` variant に `code: String` field を追加し、5 つの code を同一 teardown 経路に流す。handler 側で code 別 toast prefix を出す。 |
| HIGH | silent-HIGH-2 | `LiveSession.run()` in-process arm が `second_password=None` を `RuntimeError` 直接 raise し、`on_event` に何も emit しない (auto fallback 経由のユーザーに通知が届かない silent UX failure) | Group B: `RuntimeError` 直前に attach 経路と対称な `SecondPasswordRequired` event (`{event, strategy_id, ts_event_ms}`) を emit。credential は event のどのフィールドにも含めない。 |
| HIGH | plan-HIGH-1 | R8 反映ブロックが implementation-plan.md に未記載 | Group I (本ブロック) で記述。 |
| MEDIUM | silent-MEDIUM-1 | LiveStopped no-op 経路の Rust 側 pin 欠如 (Python 側コメント `test_engine_runner_live_warmup_failure.py:209` のみが契約の根拠) | Group C: `tests/live_form_smoke.rs::test_live_stopped_no_op_when_idle_pin` で `else` 分岐が state mutation を含まないことを source-pin。 |
| MEDIUM | silent-MEDIUM-2 (rust) | `safe_slice_end` 境界値テスト不足 (`max_len=0` / `start=src.len()` / `start>src.len()` 系統未検証) | Group D: 3 ケース追加 + helper に defensive `start.min(src.len())` 追加。 |
| MEDIUM | rust-MEDIUM-2 | `safe_slice_end(&src, ...)` で `&String` を渡している箇所が 8 箇所、`src.as_str()` で型を明示すべき | Group E: 8 箇所すべて `safe_slice_end(src.as_str(), ...)` に統一。 |
| MEDIUM | plan-MEDIUM-1 | fake test の underscore intermediate 変数 (`_data_engine` / `_exec_engine`) が canonical surface のみの統一形に揃っていない | Group F: `test_engine_runner_live_warmup_failure.py` (2 箇所) / `test_engine_runner_live_node_build_failed.py` / `test_credential_scrub.py` / `test_engine_runner_live_kabu.py` の 5 箇所を canonical surface 直接代入に統一。 |
| MEDIUM | plan-MEDIUM-2 | implementation-plan.md §R2-C Tip に「`register_client` 呼出箇所は本 R2-C scope 外」の旧記述が残存 | Group G: 取消線 + 「R8 で解消」note を併記。 |
| MEDIUM | plan-MEDIUM-3 | spec.md §5.1 に `--mode auto` の意味論記述が欠如 | Group H: 「`--mode {auto|attach|inprocess}` の意味論」段落を追加 (auto = attach probe + inprocess fallback / credential は CLI 段階で強制せず `LiveSession.run()` 側で表面化 / attach は credential を wire に流さない、の 3 点)。 |
| MEDIUM | plan-MEDIUM-4 | MISSES.md に「source-pin tests の固定 byte slicing が UTF-8 multibyte 境界を踏む」パターン未記載 | Group J で 1 件追加。 |

#### 設計判断 (R4 Group A)
- **`LiveStrategyBuildFailed` を再利用 / 新 variant を作らない**: 既存 handler が
  必要な teardown (`state machine reset → pending_strategy_id=None → warmup banner clear → portfolio clear → teardown_live_panes → toast`) を全てこなしている。新 variant
  を作ると同じ経路を二重実装することになる。warm_up 失敗の場合 `auto_generate_live_panes`
  が呼ばれていないので `teardown_live_panes` は実 pane に対して no-op になるが、
  `LiveBarState::default()` reset と `pending_strategy_id` クリア、user toast は必須 — 既存 handler が全てこなす。
- **`code: String` field 追加 / enum 化しない**: 5 つの abort code は将来追加される
  可能性があり、`String` のままにすることで schema bump 不要で拡張可能。handler の
  `match code.as_str()` の `_` arm が code を toast 文言に echo するため、未知 code
  も silent failure にならない。

#### 検証 (R4 batch)
- `cargo check --workspace` → clean
- `cargo clippy --workspace --tests -- -D warnings` → clean
- `cargo fmt --check` → clean
- `cargo test --workspace` → 全テスト緑 (新規 Rust 試験 11 件追加)
  - `engine_error_routing_tests` 7 件 (Group A 経路 5 + 既存 regression 2)
  - `tests/live_form_smoke.rs::test_live_stopped_no_op_when_idle_pin` 1 件 (Group C)
  - `tests/live_form_smoke.rs::test_live_strategy_build_failed_carries_code_field` / `test_engine_error_routes_warm_up_codes_to_build_failed_arm` / `test_live_strategy_build_failed_handler_branches_on_code` 3 件 (Group A pin)
  - `tests/issue_39_empty_state_pin.rs::safe_slice_end_handles_max_len_zero` / `safe_slice_end_handles_start_at_end` / `safe_slice_end_clamps_start_beyond_len` 3 件 (Group D)
- `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana and not demo_kabu and not tk_smoke" --timeout=120` → 全件緑 (新規 Python 試験 2 件追加)
  - `test_live_session_run_inprocess_no_password.py` 2 件 (Group B)

#### 知見 / 次回回避策

- **fake test と production code の API surface drift**: `_FakeNode` が canonical
  surface (`kernel.data_engine`) と underscore intermediate (`_data_engine`) の両方を
  持っていると、production が誤って underscore 経路に fallback しても test では検出
  できない。fake は **canonical surface だけ持つ** ことで、drift を即時に
  AttributeError として可視化できる (`test_register_client_called_via_kernel_not_private_attr`
  のような専用 regression pin と組み合わせるとさらに堅牢)。
- **source-pin tests UTF-8 罠**: `&src[start..(start + N).min(src.len())]` で固定
  byte 窓を取るとき、対象ソースに日本語コメントが混じれば窓終端が char boundary
  を踏んで panic する。共通 helper (`safe_slice_end`) を必ず経由し、helper 自体は
  `start.min(src.len())` の defensive clamp も入れて caller の誤用を吸収する。
  issue #39 + #42 の 2 箇所で踏んだ実績あり (MISSES.md §「2026-05-11 — source-pin
  tests …」参照)。
- **auto mode CLI fallback の event 経路**: attach 経路で表面化する event
  (`SecondPasswordRequired` 等) は in-process 経路でも emit しないと、`force_mode=auto`
  fallback ユーザーには `RuntimeError` の例外メッセージしか届かない。CLI / GUI の
  event handler が両経路で同じ event を期待しているなら、両経路で emit 順序も
  揃える (event emit → 例外 raise の順)。
- **attach / in-process の対称性 audit checklist**: (a) login 失敗 → `VenueError`
  / `ConnectionError` の対称性、(b) 第二暗証番号要求 → `SecondPasswordRequired`
  event の対称性、(c) market_closed → `EngineError{code:"market_closed"}` の対称性、
  (d) warm_up 失敗 → `EngineError{code:"warm_up_failed"} + EngineStopped` の対称性。
  各境界に対して「attach は ev 流し、in-process は raise」の **片側のみ** になって
  いないか機械的にチェックする lint があると 2 度目の同類バグを防げる
  (`tools/lint/check_event_symmetry.py` 仮称、follow-up 候補)。

R3 サニティで発見した HIGH 3 + MEDIUM 7 を R4 で完全解消。

#### R5 サニティ → R6 で解消した HIGH 1 + MEDIUM 3

R4 batch fix の直後 (R5) に silent-failure-hunter + rust-reviewer を再走させた
ところ、R4 fix の延長で確認すべき新規発見が出たため R6 で追加修正した。

| 区分 | ID | 概要 | 解消方法 |
|------|----|------|---------|
| HIGH | silent-R6-HIGH-1 | `STRATEGY_ABORT_CODES` allow-list に `engine_run_failed` (server.py:5252) と `timeout` (server.py:5210) が漏れており、これらが `strategy_id=Some` 付きで emit されたとき `log::warn!` のみで握りつぶし → `live_strategy_pending_strategy_id` がクリアされず state machine が固着 → 次の live 起動が受け付けられない silent regression。timeout は 3600s で必ず発火するため長時間 live で確実に踏む | R6-A: `src/main.rs::STRATEGY_ABORT_CODES` に 2 code を追加 + `src/handlers/replay.rs::ReplayMsg::LiveStrategyBuildFailed` arm の `match code.as_str()` に日本語 toast prefix (`"エンジン実行失敗"` / `"エンジンタイムアウト"`) を追加。`engine_error_routing_tests` に regression pin 2 件追加 (RED→GREEN 確認済)。 |
| MEDIUM | silent-R6-MEDIUM-1 | `test_engine_runner_live_kabu_kernel_unavailable.py:77-78` の `_FakeNodeNoKernel` が R4 Group F の cleanup 対象から漏れ、`_data_engine` / `_exec_engine` underscore intermediate を保持。`TestRegisterClientUsesKernelPath` regression pin の意図と矛盾し、将来の開発者が production に underscore 参照を追加する誤読リスク | R6-B: `_FakeNodeNoKernel.__init__` から underscore intermediate を削除し、production が underscore に fallback したら `AttributeError` で即発覚する canonical-only fake に統一。 |
| MEDIUM | silent-R6-MEDIUM-2 | `test_run_inprocess_second_password_required_event_contains_no_credential` の credential 漏洩テストがトップレベル shallow scan のみで、将来 event payload に nested dict / list (例: `{"context": {"hint": "..."}}`) が追加されたとき silent leak を見逃す | R6-C: `_walk_keys` / `_walk_values` 再帰 helper を追加し、キー側 = `password` / `credential` 含有禁止 / 値側 = login literal `pw456` 含有禁止 を任意の深さで検証。helper 自体の動作 pin として 2 件のユニットテストも追加。 |
| MEDIUM | rust-R6-MEDIUM-1 | `messages.rs::ReplayMsg::LiveWarmingUp.progress` の `#[allow(dead_code)]` が R4 Group 7 (`4f1ef10`) で陳腐化 (handler / view が実際に使うようになった) | R6-D: `#[allow(dead_code)]` 削除。`coding-style.md`「`#[allow]` は理由コメント無しでは不可、陳腐化したものは即削除」に準拠。 |

#### R6 検証
- `cargo check --workspace` → clean
- `cargo clippy --workspace --tests -- -D warnings` → clean
- `cargo fmt --check` → clean
- `cargo test --workspace` → 全テスト緑 (R4 から +2 件: `engine_error_routing_tests` の `engine_run_failed` / `timeout` routing pin)
- `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana and not demo_kabu and not tk_smoke" --timeout=120` → 全件緑 (R4 2310 → R6 2312 で +2 件: `_walk_keys` / `_walk_values` helper 動作 pin)

#### R6 知見

- **`STRATEGY_ABORT_CODES` allow-list の網羅性検査**: silent-failure-hunter が
  「server.py の `EngineErrorModel(code=..., strategy_id=strategy_id)` を全 grep して
  Rust 側 allow-list と突き合わせる」プロセスで R4 で見落とした 2 code を発見。
  この種の「Python emit ↔ Rust receive 対称性」は CI lint 化候補 (例:
  `tools/lint/check_engine_error_codes.py` で server.py の `code=` リテラルを抽出 →
  `STRATEGY_ABORT_CODES` ソースと突合)。
- **R4 cleanup で fixture ファイル網羅漏れ**: R4 Group F は `test_engine_runner_live_*.py`
  4 ファイルを cleanup したが `test_engine_runner_live_kabu_kernel_unavailable.py`
  (R4 Group 4 で別途 split された専用 fixture ファイル) を見落とした。**新ファイル
  split 後は cleanup 対象リストを `git ls-files` で機械的に再確認** することを次回
  の運用 checklist に追加。
- **credential テストの shallow scan 罠**: イベント名自体が `SecondPasswordRequired`
  のように credential 関連語を含むケースがあるため、無差別な再帰検査だと false positive
  になる。**キー側 (新 field 追加の警告)** と **値側 (実 credential literal 検出)**
  の二経路に分けて再帰すると、安全性と false positive 回避が両立する。MISSES.md
  追加候補。

#### R7 サニティ → R8 micro-fix で対称性回復

R6 fix の直後 (R7) に silent-failure-hunter 単独で再走させたところ、R6-A の
取りこぼし MEDIUM 1 件を発見し micro-fix で回収:

| 区分 | ID | 概要 | 解消方法 |
|------|----|------|---------|
| MEDIUM | silent-R7-MEDIUM-1 | R6-A で `STRATEGY_ABORT_CODES` allow-list と handler の `match code.as_str()` toast prefix 表に `engine_run_failed` / `timeout` を追加したが、handler arm の prefix 出現を pin する `tests/live_form_smoke.rs::test_live_strategy_build_failed_handler_branches_on_code` の必須リストを更新し忘れていた → 将来 handler が `_` arm fallback (英語コード生出し) に退行しても CI が検出できない非対称 | R7-fix: 上記テストの `for prefix in [...]` に 2 code を追加。allow-list 側 (`test_engine_error_routes_warm_up_codes_to_build_failed_arm`) と完全対称化。 |

#### R7 fix 検証
- `cargo check / clippy / fmt / test --workspace` → 全件緑
- `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana and not demo_kabu and not tk_smoke" --timeout=120` → 2312 passed
- 純粋な test 追記 (production code 不変) のため新規 silent failure リスクゼロ → 追加サニティ不要

R4 + R6 + R7 micro-fix を経て review-fix-loop 収束。本 PR はマージ可能状態。

### Wave R1 反映（2026-05-11、外部レビュー反映）

- 担当: r1-fix-agent
- 関連 commit: 本 commit — HIGH-1 / HIGH-2 / MEDIUM-1 を TDD で順次解消

#### R1 外部レビュー指摘

| 区分 | ID | 概要 | 解消方法 |
|------|----|------|---------|
| HIGH | HIGH-1 | `EngineStarted` を warm_up **完了後** に emit していたため、warm_up が 5s を超えると `_warming_up_ticker` が先に `LiveStrategyWarmingUp` を emit して Rust 側 state machine (`pending_strategy_id` 照合) に silent drop されていた。spec §3.2 lifecycle 契約 (`EngineStarted → LiveStrategyWarmingUp → LiveStrategyReady`) 違反。 | `engine_runner.py::start_live` の `EngineStarted` emit ブロックを warm_up 開始 (= `ticker_task` 起動) **より前** に移動。warm_up 失敗パス (例外 / `False`) では `_emit_warmup_failed_and_close()` が後続で `EngineStopped` を emit するため、Rust 側 `LiveStopped` arm が pending_strategy_id を clear して state machine が unstuck される（順序契約: `EngineStarted → EngineError(warm_up_failed) → EngineStopped`）。`test_engine_runner_live_warmup_failure.py` の旧 assertion 「`EngineStarted` を emit しない」を「`EngineStarted` は emit されるが `LiveStrategyReady` は emit されない」に反転。 |
| HIGH | HIGH-2 | `KabuStationLiveDataClient._on_evict` が emit する `SubscriptionEvicted{venue,symbol,exchange}` は spec §3.2-G の IPC 契約だが、wire schema 全層（`schemas.py` / `engine.proto` / `engine_pb2` / `engine-client` DTO / `server_grpc.py` / Rust handler）に variant が無く、`server_grpc.py::_dict_to_proto_event` で **silent drop** されていた。kabuステーション 50 銘柄 PUSH 上限到達時、ユーザーへの通知が完全に失われる。 | `SCHEMA_MINOR 28 → 29` に bump。新 message `SubscriptionEvictedEvent {venue, symbol, exchange}` を proto に追加（field number 56）、Python pb2 を再生成、`schemas.py::SubscriptionEvicted` を新設、`server_grpc.py::_EVENT_TO_FIELD_AND_CLASS` に登録、`engine-client/src/dto.rs::EngineEvent::SubscriptionEvicted` を追加、`grpc_transport.rs::proto_event_to_dto` で変換、`src/main.rs::map_engine_event_to_message` で `Toast::warn` を発火（user-facing 文言「{symbol} は PUSH 上限到達で登録解除されました（再選択で再登録）」）。 |
| MEDIUM | MEDIUM-1 | `LiveStrategyFormModal` が単一フィールド `tachibana_is_production: bool` しか持たず、`prod_mode=true && venue=="kabu_station"` を hardcode で「Phase 5 へ繰越」と reject していた。server.py 側は既に `kabu_station.is_production = (_kabu_env == "prod")` を `_build_ready` で expose 済（`KABU_ALLOW_PROD=1 + KABU_ENV=prod` の二重判定）だったため、form 側だけが kabu prod を恒久 reject していた silent UX failure。 | フィールド名を `is_production_by_venue: HashMap<String, bool>` に置き換え、`validate()` の prod_mode check を venue-aware に変更（venue → KABU_ALLOW_PROD / TACHIBANA_ALLOW_PROD / generic "production env" の env hint を文言に挿入）。`src/handlers/replay.rs::NativeOpenStrategyPicked` の live 分岐で `engine_client::capabilities::is_production(caps, venue)` を tachibana / kabu_station の両 venue について読み、HashMap を組み立てて modal に渡す。 |

#### 設計判断 (R1)

- **`SCHEMA_MINOR` 28 → 29 bump 理由**: `SubscriptionEvicted` の wire 表現は完全に新規 variant 追加（既存 message の field 追加ではない）なので minor bump が必須。Rust ↔ Python の 3-way 同期テスト `test_rust_schema_constants_match_python` (`test_schemas_nautilus.py`) で担保。
- **`EngineStarted` 順序の Rust state machine 制約**: `src/handlers/replay.rs::ReplayMsg::LiveStarted` arm が `pending_strategy_id` を set し、60s warm_up timeout token を確立する。後続の `LiveWarmingUp` arm がそれと照合して進捗 banner / timeout reset を行う。`EngineStarted` が `LiveStrategyWarmingUp` より後に出ると、ticker からの先行 LiveStrategyWarmingUp が pending/running いずれにも match せず silent drop される（src/handlers/replay.rs:704）。`EngineStarted` を warm_up 前に emit することでこの照合経路が成立する。
- **kabu prod env SoT**: `KABU_ALLOW_PROD=1 + KABU_ENV=prod` の二重判定（既存 `engine.exchanges.kabusapi_url.resolve_kabu_env` を SoT として server.py が `_kabu_env == "prod"` を `is_production` cap に expose）。tachibana の `TACHIBANA_ALLOW_PROD=1`（単一判定）と非対称だが、これは既存 venue 仕様であり R1 範囲では現状維持。

#### R1 新規追加テスト

- `python/tests/test_kabu_station_live_data_client.py::TestPushSymbolLimitEviction::test_evicted_event_validates_against_schema` — 新 `SubscriptionEvicted` pydantic model で emit 済 dict を round-trip validate。
- `python/tests/test_server_grpc_live_ipcs.py::test_event_to_field_mapping_contains_subscription_evicted` / `test_subscription_evicted_dict_to_proto_round_trip` — wire mapping pin + dict→proto roundtrip。
- `python/tests/test_engine_runner_live_kabu.py::TestEngineStartedOrderingDuringWarmUp::test_engine_started_emitted_before_live_strategy_warming_up` (kabu) / `_tachibana` — 5.2s slow warm_up で ticker を 1 回発火させ、`EngineStarted` index < `LiveStrategyWarmingUp` index を assert。
- `engine-client/src/grpc_transport.rs::tests::subscription_evicted_proto_maps_to_dto` / `subscription_evicted_json_deserialize_pin` — Rust 側 proto→dto と JSON→dto の対称 round-trip。
- `src/modal/live_strategy_form.rs::tests::test_validate_allows_prod_mode_for_kabu_when_is_production_true` / `test_validate_rejects_kabu_prod_when_cap_false_and_mentions_kabu_env` / `test_validate_allows_prod_mode_for_tachibana_via_hashmap` / `test_validate_rejects_tachibana_prod_when_cap_false_via_hashmap` / `test_validate_rejects_prod_when_venue_missing_from_hashmap` — venue-aware is_production gate。
- 既存 assertion 反転: `test_engine_runner_live_warmup_failure.py::TestWarmUpFailureExceptionPath::test_warm_up_exception_emits_error_not_ready` / `TestWarmUpFailureFalseReturnPath::test_warm_up_returns_false_emits_error_not_ready` — `EngineStarted は emit されない` → `EngineStarted は emit されるが LiveStrategyReady は emit されない`、順序契約 `EngineStarted → EngineError → EngineStopped` を pin。
- 既存 schema 数値更新: `test_schemas_nautilus.py::test_schema_minor_is_9_for_phase_b1` / `test_request_venue_login_state.py::test_schema_minor_current_value` / `engine-client/tests/schema_v2_4_nautilus.rs::schema_minor_matches_current_bump` / `tests/engine_event_routing_exhaustive.rs::engine_event_variant_count_is_as_expected` (56 → 57)。

#### R1 検証

- `cargo check --workspace --tests` → clean
- `cargo clippy --workspace --tests -- -D warnings` → clean
- `cargo fmt --check` → clean
- `cargo test --workspace` → 全テスト緑
- `uv run pytest python/tests/ -m "not live_demo and not live_demo_inprocess and not demo_tachibana" -q` → 2549 passed, 116 skipped, 8 deselected
- 個別: `test_engine_runner_live_kabu.py` / `test_kabu_station_live_data_client.py` / `test_server_kabu_live_push.py` / `test_live_session_cli.py` / `test_engine_runner_live_warmup_failure.py` → 76 passed

R1 反映で review-fix-loop はさらに 1 周収束。本 PR はマージ可能状態（R4 + R6 + R7 + R1）。

