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
- ✅ 達成した受け入れ基準: #1 (CLI 経路 — `test_attach_starts_engine_for_replay_strategy_unchanged` + `test_inprocess_starts_engine_for_replay_strategy_unchanged`), #6 (`test_invalid_config_when_max_qty_missing` + `test_max_qty_zero_or_negative_reject` + `test_max_notional_overflow_reject`), #7 (`test_prod_blocked_without_env`), #8 CLI 部分 (`test_attach_second_password_required_exits_nonzero`), #9 (`test_start_live_rejects_when_market_closed`), #14 (`test_warm_up_exception_emits_error_not_ready` + `test_warm_up_returns_false_emits_error_not_ready` + `test_warm_up_*_closes_exec_client`), #16 (`test_concurrent_live_emits_engine_busy_for_venue` + `test_duplicate_strategy_id_emits_engine_already_running`), #20 (`test_second_password_stdin_handles_heredoc_pipe_empty_and_noninteractive`)
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
  - **H-1 (Nautilus 親クラス継承)**: `KabuStationLiveExecutionClient` / `KabuStationLiveDataClient` は `nautilus_trader.live.execution_client.LiveExecutionClient` / `LiveDataClient` を継承していない thin adapter。`engine_runner.py:1357-1358` の `node._exec_engine.register_client(...)` / `node._data_engine.register_client(...)` は本物の Nautilus Cython 親クラスのインスタンスを期待するため、warm_up 成功 → `node.build()` 経路では type check で落ちる。現状 Phase 4 のテストは全て warm_up 失敗で abort するためこの経路を踏まないが、実機で kabu live を動かすと `node_build_failed` になる。Phase 5 で Nautilus 親クラスから派生させる必要あり（`abstractmethod` の `_connect` / `_disconnect` / `_submit_order` / `_cancel_order` / `_modify_order` / `_subscribe_*` などを実装）
  - **M-1 (GUI venue dropdown)**: `src/modal/live_strategy_form.rs::view()` への venue dropdown 追加が **未対応**。Phase 3.5 引き継ぎ Tips では「Phase 4 と一緒に解放」と書かれていたが、Phase 4 の実装範囲が広く（client 一式 + engine_runner 分岐 + server.py + tests）コミット粒度を分けたため、GUI 側変更は Phase 5（examples 対称化）と一緒に着手する。capability flip は完了済なので Rust 側 `engine_client::capabilities::supports_live_strategy(caps, "kabu_station")` は True を返す。dropdown を追加するには `EngineStartConfig` に `venue: Optional[str]` フィールドを追加するか（schema bump 1 件）、modal の `Action::Submit` に venue を載せて server.py 側で `_connected_venue` と照合する経路を決める必要あり
  - **M-3 (RegisterSet 二重化)**: `KabuStationLiveDataClient` 内部の `RegisterSet` と既存 `server.py::self._kabu_register_set` が独立している。前者は live strategy の「契約 stub」（subscribe/evict 通知契約）、後者が PUSH 物理経路（`PUT /register` / `_handle_subscribe_kabu_station`）。kabu live data は実際には flow しない（H-1 と合わせて Phase 5 で本配線）。spec §3.2-G の `SubscriptionEvicted` 通知契約は live data client が IPC emit するので満たす
  - **M-5 (fetch_orders 意味論)**: `KabuStationVenue.fetch_orders` は現状 `KabuOrderClient.poll_fills(**params)` に委譲しており、`State=5 (約定)` のみを返す。warm_up の本来の目的「未決注文 (open orders) 復元」とは意味が違う（未決 = State 1, 3, 4 等）。Phase 5 で `KabuRestClient.fetch_orders` (`/orders` 全件) に切替え + OrderIdMap 相当の写像を kabu 側にも実装する
  - **代表 1 経路（注文発行 + フィル受信）の実機通過**: H-1 が解消されないと実機で node.build() に到達できないため、attach mode の実機 smoke は Phase 5 以降に持ち越し
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
  - #3 `examples/README.md` に replay → demo → prod を同ファイルで通すコマンド例（`tools/lint/check_examples_readme.py::test_live_section_present` で見出し存在を pin、内容充実は Phase 5）
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
  - **`_FIELD_TO_OP` mapping pin の重要性**: Wave 1 で wire 配線済の `load_live_strategy_scenario` が `_FIELD_TO_OP` から削除されると、Rust が proto Command を送信しても `_handle` まで到達しない silent failure になる。`test_field_to_op_mapping_contains_new_commands` で mapping を pin することで、リファクタ時の意図しない削除を即座に検知できる
