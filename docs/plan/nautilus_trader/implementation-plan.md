# nautilus_trader 統合: 実装計画

## マイルストーン一覧

| Phase | ゴール | 依存 |
|---|---|---|
| **N-pre** | feasibility / 配布形態 / pin 戦略 / 既存 Rust 発注経路の有無を確定（実装ゼロ）| python-data-engine 完了済 |
| N0 | nautilus 同梱・サンプル戦略 headless で PnL が出る | N-pre |
| N1 | `/api/replay/*` を nautilus で実装、Gymnasium env が回る、REPLAY 仮想注文を SimulatedExchange に流す、`POST /api/agent/narrative` を新設 | N0、order/ Phase O0 完了 |
| N2 | 立花 `LiveExecutionClient` adapter（デモ）で実弾相当の発注往復が通る | N1、tachibana Phase 1 完了（T7 受け入れ緑）、order/ Phase O0〜O2 完了。**現状: T4 まで完了・T5〜T7 未着手** |
| N3 | 暗号資産 venue を nautilus 側に移植 or 新規実装、Rust 発注コード撤去 | N2 |

**Phase N1 の依存補正（C2）**: 旧版で「Phase 2（観測 API）完了済」を依存に挙げていたが、その「Phase 2」は本計画 N1 で置換・破棄する対象（[architecture.md §6](./architecture.md#6-既存計画との衝突点と整理)）。本計画では N1 の依存を「N0 + order/ Phase O0」に書き換え、自作 Virtual Exchange Engine の完成を依存条件にしない。

## Phase N-pre: feasibility と前提固め（実装ゼロ）

[spec.md §2.0](./spec.md#20-phase-n-pre--feasibility-確認と前提固め実装ゼロ) に対応するタスク列。

### Tpre.1 clock 注入 feasibility プロトタイプ（H4）
- [ ] `tests/spike/nautilus_clock_injection/` に捨てコード spike を作る
- [ ] 案 A（外部 clock 駆動・`AdvanceClock` Command 想定）を nautilus 1.211 の API で組めるか検証
- [ ] 案 B（`BacktestEngine.run()` 自走）の場合に StepForward UX が捨てられるかを評価
- [ ] 結果を [architecture.md §3](./architecture.md#3-新規-ipc-メッセージ) と [open-questions.md Q3](./open-questions.md#q3) に追記して resolve

### Tpre.2 nautilus_trader バージョン pin 確定（H6 / Q1）
- [ ] [open-questions.md Q1](./open-questions.md#q1) を resolve（SemVer 案 / 厳密 pin 案のいずれか）
- [ ] `pyproject.toml` の暫定値を [spec.md §5](./spec.md#5-依存方針) に反映

### Tpre.3 配布形態と LGPL-3.0（M8 / Q5）
- [ ] [open-questions.md Q5](./open-questions.md#q5) を resolve（venv / PyInstaller / インストーラ同梱のどれか）
- [ ] PyInstaller 採用時は nautilus 差し替え可能性の確保案を spec に追記

### Tpre.4 既存 Rust 発注経路の有無確認（L1 / Q6）
- [ ] `git grep -nE "(place_order|cancel_order|modify_order|submit_order)" exchange/src/` で Rust 側発注経路の grep
- [ ] 0 hit なら N3 を「新規実装」にラベル変更、ありなら「移植」のまま
- [ ] [open-questions.md Q6](./open-questions.md#q6) を resolve

### Tpre.5 動的呼値テーブル方針（C6 / 新規 Q8）
- [ ] [open-questions.md Q8](./open-questions.md#q8) を起票・resolve（Instrument を価格帯ごとに切る / Custom precondition で skip / 呼値テーブル前倒し のいずれか）
- [ ] 結論を [data-mapping.md §3](./data-mapping.md#3-instrument-価格帯と呼値テーブル) に反映

### Tpre.6 発注 UI の所在統一（L7 / Q7）
- [ ] [open-questions.md Q7](./open-questions.md#q7) を resolve（全 venue Python 側 UI に統一する方針確定）
- [ ] [spec.md §4 公開 API 表](./spec.md#4-公開-api不変条件) と [order/](../order/) の UI 設計が新方針と一致することを確認

### Tpre.7 wheel 入手性確認
- [ ] Windows 11 / macOS arm64 / Linux x86_64 の 3 環境で `uv add nautilus_trader` が wheel を取得できることを確認
- [ ] ソースビルドが必要な環境があれば Tpre.3 の判断材料に戻す

**Exit 条件**: Tpre.1〜Tpre.7 すべて DONE、open-questions.md の Q1/Q3/Q5/Q6/Q7/Q8 が `Resolved` ラベル付きで spec / architecture / data-mapping に反映済み。

---

## Phase N0: 同梱と最小バックテスト

### N0.1 依存追加
- `pyproject.toml` に `nautilus_trader` を追加（pin 値は Tpre.2 の決定に従う）
- `uv lock` で `uv.lock` を更新
- CI（`.github/workflows/`）で `uv sync` 後の import スモークテストを 1 本追加

### N0.2 ワーカー骨格
- `python/engine/nautilus/__init__.py`
- `python/engine/nautilus/engine_runner.py`
  - `class NautilusRunner` を新設（`start_backtest()` / `start_live()` / `stop()`）
  - 既存の `EngineClientBackend` ループから呼び出される
- `python/engine/server.py` のディスパッチに `Command::StartEngine` ハンドラを追加（schema 1.4、[architecture.md §3](./architecture.md#3-新規-ipc-メッセージ)）
- **N0 では live execution を呼ばない**（`start_live()` は stub、`Ready.capabilities.nautilus.live = false`）

### N0.3 EventStore → nautilus DataLoader
- `python/engine/nautilus/data_loader.py`
  - 入力: `{ticker, timeframe, range_start_ms, range_end_ms}`
  - 出力: nautilus `Bar` の iterable
  - **N0 では既存 `Klines` IPC イベントの `klines` 配列を nautilus `Bar` に変換する**（EventStore 直読み IPC は N1 で必要なら追加）。M4 文言修正

### N0.4 サンプル戦略
- `python/engine/nautilus/strategies/buy_and_hold.py`
- ユニットテスト 1 本: 1 年分 BTC 日足を投入 → 最終 equity が初期資金より大きい（または NaN でない）
- **`--strategy-file` 経路は実装しない**（M3、Q2 解決まで組み込み Strategy のみ）

### N0.5 headless スモーク
- `tests/python/test_nautilus_smoke.py`
  - `NautilusRunner.start_backtest(...)` を 1 回呼び、`Event::EngineStopped` が返る
- 既存 E2E に `s60_nautilus_backtest_smoke.py` を追加（`IS_HEADLESS=true` 必須）。smoke.sh 末尾から `uv run python tests/e2e/s60_nautilus_backtest_smoke.py` で呼び出す形式で既存 smoke.sh に追記する

### N0.6 決定論性テスト（M6 / spec §3.1）
- [ ] `tests/python/test_nautilus_determinism.py`
  - 同一 seed・同一データセットで `start_backtest` を 2 回回し、最終 equity / 全約定タイムスタンプ / 全 OrderFilled `last_price` が**ビット一致**することを検証
  - `pytest --count=2` 等の繰返し実行ではなく、明示的に 2 回 run して両結果を assert
- [ ] `tests/python/test_nautilus_wallclock_independence.py`
  - `unittest.mock.patch("time.time")` / `patch("time.monotonic")` / `patch("datetime.datetime")` を固定値に差し替えても backtest 結果が変わらないことを検証
  - nautilus 内部で wall clock 参照箇所が見つかった場合は spike として記録し本テストで把握

**Exit 条件**: 上記スモーク + 決定論性テストが `python-test.yml`（新設）の `uv run pytest tests/python/test_nautilus_determinism.py tests/python/test_nautilus_smoke.py` ジョブで緑になること

---

## Phase N1: リプレイ HTTP API の差し替え + REPLAY 仮想注文 + ナラティブ API 新設

**PR 切り方の規約（M7）**: N1 の `/api/replay/order` 自作 → nautilus 経由の置換は **1 PR でアトミック**にマージする。互換シムを残さないため、置換期間中の窓を作らない。Gym env / iced UI が壊れる可能性のあるリファクタは N1 内で別 PR に切らない。

### N1.1 IPC schema 1.4
- [ ] [engine-client/src/dto.rs](../../../engine-client/src/dto.rs) に `StartEngine` / `StopEngine` / `EngineStarted` / `EngineStopped` / `PositionOpened` / `PositionClosed`（venue / instrument_id / 文字列精度）を追加（[architecture.md §3](./architecture.md#3-新規-ipc-メッセージ)）
- [ ] **`Order*` 系 / `SubmitOrder` 系は order/ Phase O-pre PR のマージを確認してから着手すること。schema 1.3 が dto.rs に存在しない場合は N1.1 着手前ブロック**（C4 / order の整合）
- [ ] `schema_minor` を `4` に上げる（C1 修正、`schema_version` の表記は使わない）。Hello / Ready capabilities に `nautilus.backtest=true, nautilus.live=false` を載せる
- [ ] N-pre Tpre.1 の結論次第で `AdvanceClock` Command を追加 or 不要化
- [ ] 既存テストが落ちないことを `cargo test -p engine-client` で確認、Python 側 `python/engine/schemas.py` も同 PR で同期

### N1.2 Rust 側 replay_api 差し替え（C3 修正）
- [ ] `git grep -nE "VirtualExchangeEngine|replay/order"` で**現リポジトリに自作 Virtual Exchange Engine の Rust 実装が存在しないことを確認**してから着手（旧 plan の「あれば削除」hedge は廃止）
- [ ] `src/replay_api.rs`（または該当ファイル）の `/api/replay/order` ハンドラを「`engine_client.send(SubmitOrder { venue: "replay", ... })` → `OrderFilled` を待つ」フローに書き換え
- [ ] `/api/replay/portfolio` も `engine_client.send(GetPortfolio)`（新設）に置換
- [ ] 自作 Rust 実装が見つかった場合は同 PR で削除、互換シムは残さない

### N1.3 nautilus 側 SubmitOrder ハンドラ
- [ ] `engine_runner.py` で `BacktestEngine.submit_order` を呼び、約定後に `OrderFilled` を IPC で返送
- [ ] 仮想時刻同期: N-pre Tpre.1 の決定に従う（案 A=`AdvanceClock` 駆動 / 案 B=自走）

### N1.4 REPLAY 仮想注文ディスパッチャ（README §REPLAY モード仮想注文の取り込み）

**前提**: `python/engine/exchanges/tachibana_orders.py` が存在し `submit_order` / `NautilusOrderEnvelope` が実装済みであること（order/ Phase O0 以上完了）

- [ ] `python/engine/order_router.py` 新設
- [ ] live モード時 → `tachibana_orders.submit_order(...)` に委譲（order/ 計画の関数を呼ぶ）
- [ ] replay モード時 → `BacktestExecutionEngine.process_order(...)` に委譲
- [ ] 監査ログ WAL は `tachibana_orders.jsonl`（live）と `tachibana_orders_replay.jsonl`（replay）の 2 系統に分離
- [ ] `client_order_id` 名前空間も live / replay で分離（同一 ID 投入時の干渉なし）
- [ ] 第二暗証番号 modal は REPLAY ガードで skip
- [ ] iced UI 差分（バナー「⏪ REPLAYモード中」、ボタンラベル「仮想注文確認」）の実装を含む
- [ ] `tests/python/test_order_router_dispatch.py` を追加: live 時は `tachibana_orders.submit_order` が呼ばれること、replay 時は `tachibana_orders_replay.jsonl` に書き込まれることを mock で検証

### N1.5 ナラティブ API 新設（H5）
- [ ] `POST /api/agent/narrative` を `src/api/agent_api.rs`（新設）に実装
- [ ] 既存リポジトリには未実装のため、**本タスクが「初実装」**（旧 spec の「既存実装のまま」表記は誤り、本計画で訂正）
- [ ] `python/engine/nautilus/narrative_hook.py` を新設し、`Strategy.on_event` で `OrderFilled` を捕捉 → POST（`linked_order_id` を埋める）
- [ ] 文書間整合: [docs/plan/README.md](../README.md) Phase 4a の概念定義と矛盾しないこと

### N1.6 Gymnasium 互換性確認
- [ ] `FlowsurfaceEnv.step()` が変わらず動くこと
- [ ] `tests/python/test_flowsurface_env_with_nautilus.py`
- [ ] **追加テスト（M6）**: 部分約定 / cancel-after-fill レース / EC frame 重複受信のシナリオを mock で再現し、nautilus `OrderFilled` の累積と `leaves_qty` 整合をテスト

### N1.7 性能ベンチマーク
- [ ] [docs/plan/✅python-data-engine/benchmarks/](../✅python-data-engine/benchmarks/) に `nautilus_replay_baseline.py` 等を追加
- [ ] [spec.md §3.3](./spec.md#33-パフォーマンス) の計測対象定義に従い「`start_backtest` 呼出 → `EngineStopped` IPC 受領」までの wall clock を計測
- [ ] 30 秒 SLA を実測値で確定し spec を更新

**Exit 条件**: `s51`〜`s53` ナラティブ系 E2E が全部緑のまま、`/api/replay/*` と `/api/order/*`（REPLAY モード）の挙動が nautilus 経由で同じ、1 年バックテスト SLA 確定、決定論性テスト（N0.6 を N1 入力に対して再走）が緑（`uv run pytest tests/python/test_nautilus_determinism.py -k n1_dataset` が pass すること）

---

## Phase N2: 立花 ExecutionClient（デモ）

**前提**: order/ 計画の Phase O0〜O2 が完了し、`tachibana_orders.submit_order` / `modify_order` / `cancel_order` / EC frame パーサ / 第二暗証番号 UI / 監査ログ WAL がすべて稼働している。本フェーズは **nautilus への薄い adapter のみ**を書く。

### N2.1 nautilus `LiveExecutionClient` adapter

**前提**: `python/engine/exchanges/tachibana_orders.py` が存在し `submit_order` / `NautilusOrderEnvelope` が実装済みであること（order/ Phase O0 以上完了）

- [ ] `python/engine/nautilus/clients/tachibana.py` 新設
- [ ] `LiveExecutionClient` を継承し、以下を **order/ の関数に委譲**:
  - `submit_order(Order)` → `tachibana_orders.submit_order(session, second_password, NautilusOrderEnvelope.from_nautilus(order))`
  - `modify_order` → `tachibana_orders.modify_order(...)`
  - `cancel_order` → `tachibana_orders.cancel_order(...)`
- [ ] 立花 API 写像（`OrderType` / `TimeInForce` / `cash_margin` / `account_type`）は **[order/spec.md §6](../order/spec.md#6-nautilus_trader-互換要件不変条件) と [data-mapping.md](./data-mapping.md) に従う**。本ファイル内に重複定義しない

### N2.2 EC frame → nautilus イベント変換
- [ ] `python/engine/nautilus/clients/tachibana_event_bridge.py` 新設
- [ ] order/ の `tachibana_event._parse_ec_frame` の戻り値（`OrderEcEvent`）を nautilus `OrderFilled` / `OrderCanceled` / `OrderRejected` に変換
- [ ] `LiveExecutionEngine.process_event(...)` に流す
- [ ] **冪等化（M5）**: 同一 `p_eda_no` の EC が再送された場合に nautilus 側で 2 重 `OrderFilled` を発火しないよう、ClientOrderId 単位の seen-set を adapter 内に持つ。order/ の Python 側 seen-set と二重ガードになるが、IPC 経路を跨ぐ再起動時に守りを兼ねる
- [ ] 検証テスト: N2.6 の同一 `p_eda_no` 重複受信テスト（`test_ec_idempotency`）が pass すること

### N2.3 注文 ID マッピングと再起動復元
- [ ] nautilus `ClientOrderId` ⇔ 立花 `sOrderNumber` の双方向写像（order/ の `OrderSessionState` を流用）
- [ ] プロセス再起動時: 立花 `CLMOrderList` を引き、未決注文を nautilus `Cache` に warm-up（[spec.md §3.2](./spec.md#32-セキュリティ) の persistence 無効・Cache warm-up 規約）
- [ ] **persistence 設定（H3）**: `engine_runner.py` の `NautilusEngineConfig` 組み立て箇所で `database` を `None` にハードコードし、直後に `assert config.database is None` を入れる。Parquet/SQLite 永続化を OFF にしたまま warm-up を毎回行う。テストで `CacheConfig` の設定値を assert

### N2.4 市場時間帯ガード（M2）
- [ ] 立花 venue が `Disconnected{reason:"market_closed"}` の間は `LiveExecutionClient.start()` を保留する
- [ ] HTTP API 層 (`order_api.rs`) で `MARKET_CLOSED` を先行 reject（[order/spec.md §5.2](../order/spec.md#52-reason_code-体系観測性)）
- [ ] nautilus 内部で reject されてナラティブが汚染されないよう、ExecutionClient `start()` 前に `RiskEngine` への渡し前段で stop する経路を確認

### N2.5 セーフティ（order/ と二重ガード）
- [ ] デモ環境強制（`TACHIBANA_ALLOW_PROD=1` 未設定なら本番 URL を選んでも reject）
- [ ] 数量上限・1 注文金額上限を起動 config で必ず指定（未指定なら起動拒否）
- [ ] 発注ログ追記は order/ の WAL を使う（重複ファイルを増やさない）

### N2.6 E2E と単体テスト
- [ ] `s70_tachibana_nautilus_demo_order.py`（CI には載せない、ローカル手動。デモ環境クレデンシャルが必要）
- [ ] ユニットテスト: nautilus `OrderFactory` から発注 → adapter → mock `tachibana_orders` の往復で order/ の **`OrderType` 全 6 種 + `TimeInForce` 全 7 種**（[order/spec.md §6.1](../order/spec.md#61-用語型の整合必須)）を検証
- [ ] **追加テスト**: 部分約定 EC を 2 件流して `cumulative_qty` / `leaves_qty` が nautilus `OrderFilled` で正しく累積すること
- [ ] **追加テスト**: cancel リクエスト送信中に EC fill が来るレースで `OrderStatus` が壊れないこと
- [ ] **追加テスト**: 同一 `p_eda_no` の EC 重複受信で `OrderFilled` が 1 回しか発火しないこと

**Exit 条件**: デモ環境で「成行買い → 約定通知受信 → nautilus Portfolio に反映 → ナラティブに `outcome` が入る」往復が手動で確認、N2.6 の追加テストすべて緑

---

## Phase N3: 暗号資産 venue 移植（任意）

割愛（N2 完了時に詳細化）。Rust 側 `exchange/` の `place_order` / `cancel_order` 経路を削除し、nautilus 側 `HyperliquidExecutionClient` 等に置き換える。データ取得経路は触らない。

---

## 削除リスト（N1 完了時点）

- `docs/plan/README.md` Phase 2 の「自作」前提の文言
- 自作 Virtual Exchange Engine の Rust 実装は **N1.2 着手前の grep で存在確認** し、見つかった場合のみ削除（C3 修正、`(あれば)` hedge は廃止）

## 削除リスト（N3 完了時点）

- Rust `exchange/src/adapter/*` の発注関連メソッド（**N-pre Tpre.4 の grep で存在が確認できた場合のみ**）
- `engine-client` の venue 別発注 IPC の crypto 分岐（venue 列挙から `<crypto>` を落とす。`"tachibana"` と `"replay"` は残る）

**`venue` 列挙の長期形（C-ack）**: N3 完了後も `SubmitOrder.venue` は `"tachibana"` / `"replay"` の 2 値が残る。`"replay"` は架空 venue として恒久的に残す（README §既存計画との関係 で明示）。

## 横断タスク

- [ ] `docs/plan/README.md` の Phase 2 セクションを「nautilus 統合」に書き換え（N1 完了直後）
- [ ] `docs/plan/tachibana/implementation-plan.md` の Phase 2（発注）タスクが [docs/plan/order/](../order/) と本計画 N2 に分離されていることを再確認し、引退表示を update（N2 着手時）
- [ ] CLAUDE.md / SKILL.md（tachibana）に nautilus 経由の発注フローを追記:
  - SKILL.md L8 警告ブロック近傍に「N2 以降は `tachibana_orders.*` が `LiveExecutionClient` adapter 経由で nautilus から呼ばれる」追記
  - SKILL.md R10 に nautilus persistence 無効方針の参照リンク
  - SKILL.md S6 表に「nautilus 経由の発注時も `p_no` 採番ガードが効くこと」追記
- [ ] LGPL-3.0 の同梱表示（README + 配布アーティファクトの NOTICE）。配布形態は N-pre Tpre.3 の決定に従う

## ラベル規約（L2 修正）

旧版で N0/N1/N2 直下のタスクを `T0.1`〜`T2.5` と命名していたが、立花 plan の `T0`〜`T7` と紛らわしいため、本計画ではすべて **`N0.1`〜`N3.x`** に統一した。横参照する側（CLAUDE.md / SKILL.md / 他 plan）も `N{phase}.{n}` 形式で参照すること。
