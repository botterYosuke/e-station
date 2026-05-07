# 実装計画: python-data-engine 改修 統合ロードマップ

作成日: 2026-05-07  
最終更新: 2026-05-08（G0〜G3 完了。WS トランスポート廃止、gRPC 一本化完了。G1-G3 R1+R2 レビュー反映済み）

## 対象ドキュメント

| 文書 | 概要 |
|------|------|
| [🔵adapter-type-boundary.md](./🔵adapter-type-boundary.md) | exchange adapter を共通 pydantic v2 モデルで型安全化 |
| [🔵gui-triple-state-refactor.md](./🔵gui-triple-state-refactor.md) | GUI 三重状態ループの解消（Rust/Iced） |
| [🔵ipc-grpc-migration.md](./🔵ipc-grpc-migration.md) | IPC WebSocket+JSON → gRPC (tonic + grpcio) |
| [🔵plan-test-mock-ipc-server.md](./🔵plan-test-mock-ipc-server.md) | MockIPCServer フィクスチャ導入で E2E 短縮 |

---

## 依存関係マップ

```
adapter-type-boundary   Step1 → Step2 → Step3             独立（Python のみ）
gui-triple-state        Phase1+3 → Phase2 → Phase4        独立（Rust のみ）
plan-test-mock-ipc      S1 → S2 → S3                      WebSocket 存在中に完了必須
ipc-grpc-migration      G0 → G0.5 → G0.9 → G1 → G2 → G3   最後（fee_total 後の制約あり、G0.9 が G1 の前提ゲート、G2 は G1 完了後に着手）
```

**クリティカルな順序制約:**
1. `MockIPCServer`（WebSocket ベース）は gRPC 移行**前**に完成させる → 移行中の安全網になる
2. `ipc-grpc-migration` は `fee_total` 実装後まで凍結（メモリ制約）
3. gui Phase 2（HeatmapShader）は Phase 1（Message 分割）完了後に着手

---

## Stage A — ✅ 部分完了（2026-05-07）

**実施順序**: A1 と A2 は並列着手可。A3 は A2 完了後に実施（A1/A2 と並列不可）。

### ✅ A1: `python/engine/models.py` 追加 + 単体テスト — **完了**

- 文書: adapter-type-boundary Step 1
- 実装済み: `Instrument` / `OrderBook` / `DepthDiff` / `Trade` pydantic v2 モデル
  - `Decimal` 統一・timezone-aware datetime 強制・`frozen=True` immutable
  - wire 責務フィールド（event / venue / market / request_id）禁止を CI テストで保証
- テスト: `python/tests/test_models.py` — 19 tests PASS
- 既存コードへの変更: **なし**

### ✅ A2: `Message` enum 分割 → `update()` 委譲化 — **完了（2026-05-07）**

- 文書: gui-triple-state Phase 1
- 実施内容: `src/messages.rs` 新設、7 グループ nested enum 実装、`update()` を 11 行の dispatch hub に縮小、7 つの `handle_*` メソッドに委譲
- 完了条件達成: `update()` 11 行（400 行以内）、`cargo test` 全 PASS、`cargo clippy -- -D warnings` 警告なし

### ✅ A3: `Ladder.insert_depth()` Elm 管理移行 — **完了（不変条件すでに満足）**

- 文書: gui-triple-state Phase 3
- 調査結果（2026-05-07）: `insert_depth()` は `scroll_px` を直接書き換えていない。
  `scroll_px` への書き込みは `Panel::scroll(delta)` コールバック経由のみ（ladder.rs:72）。
  当初想定していたアンチパターンはコードに存在しなかった。
- 追加作業: **不要**

> ⚠ **A2/A3 の実行順序**: 計画上は並列可としているが、depth routing の実装を調査した結果、**A3 は A2 完了後に実施することを推奨**する。現行の depth fanout は `main.rs` → `dashboard.rs::ingest_depth()` → `ladder.rs::insert_depth()` という routing に乗っており、A3 で `Message::LadderScroll { pane_id, delta }` を新設すると `Message` enum への追加と routing 再設計が同時に発生する。A2 で Message enum の構造が固まった後に A3 を実施した方が、競合と二重修正を避けられる。`cargo test` の失敗が A2 由来か A3 由来か切り分けやすくもなる。

**Stage A 完了条件**: `cargo test --workspace` + `pytest` 全 PASS + `cargo clippy -- -D warnings` 警告なし + `map_engine_event_to_message()` 網羅テスト PASS  
→ **A2 完了。Stage A 完了条件（cargo test + clippy 全 PASS）達成。**

---

## Stage B — ✅ 部分完了（2026-05-07）

### ✅ B1: `KabuStationAdapter` をモデルに対応 — **完了**

- 文書: adapter-type-boundary Step 2
- 実装済み: `python/engine/exchanges/kabusapi_adapter.py`
  - コンストラクタで 50 銘柄チェック・重複シンボル拒否を即時検査
  - `parse_board()` → `OrderBook`（JST→UTC 変換、Sell1〜10/Buy1〜10 の欠損段スキップ）
  - `parse_execution()` → `Trade`（PUSH JSON に side 情報がないため `"unknown"` で正規化）
  - kabuStation PUSH には diff 形式が存在しないため `parse_board_diff()` は非実装（スナップショットのみ）
- テスト: `python/tests/test_kabusapi_adapter.py` — 13 tests PASS
- **注意**: KabuStationWorker → outbox の wire-format 直書きパスはまだ変更していない（C1 対象）。現状は adapter が型安全な変換を提供するが、worker は旧パスで動いている二重境界状態。

> ⚠ **二重境界リスク**: `kabusapi_adapter.py` を先に作っても、`KabuStationWorker` が `_Broadcaster.append()` に wire-format dict を直接書く構造は変わらない。B1 完了後に C1（server.py 差し替え）を経由しない限り、`models.py` の型変換層と既存の wire-format 直書き層が並存する二重境界になる。「kabu だけ先行」で完結するように見えるが、他 venue worker は B1 後も旧境界のまま残る。B1 着手前に「全 venue worker の同時移行」か「kabu 先行＋他 venue を C1 フェーズで一括」かの方針を決めること。

### ✅ B2: `MockIPCServer` 骨格 + Hello/Ready (S1) — **完了**

- 文書: plan-test-mock-ipc S1
- 実装済み: `python/tests/fixtures/mock_ipc_server.py`
  - `asyncio` + `websockets.serve`・ランダムポート（`port=0`）・`compression=None` 必須
  - `script` 形式（`[{"on": "Hello", "reply": [...]}]`）で応答シーケンスを宣言的に定義
  - `stop()` 冪等性保証（複数回呼んでも例外なし）
  - `received` リストでテスト側からの受信確認をサポート
- テスト: `python/tests/test_mock_ipc_server_basic.py` — 5 tests PASS、全体 0.27s（< 1s ✅）

### ✅ B3: `HeatmapShader` 状態整理 — **完了（2026-05-07）**

- 文書: `docs/✅python-data-engine/🔵heatmap-phase2-ownership.md`（作成済み）
- review-fix-loop: R1（4 reviewers）→ R2（rust-reviewer）、MEDIUM+ ゼロで収束

**完了した作業:**
- `CanvasInvalidation` struct 削除（`ui.rs`）→ 直接 `cache.clear()` に置き換え
- `RebuildPolicy` enum 削除（`view.rs`）→ `RebuildSignal` enum に置き換え
- `last_interaction`, `needs_immediate_rebuild`, `force_rebuild_from_historical` の3フィールド削除
- `RebuildSignal { Idle | Debouncing { since, force_historical } | Immediate { force_historical } }` 追加
- `RebuildDecision { Idle | OverlaysOnly | Full }` 追加（旧 `(bool,bool)` を型安全化）
- `rebuild_all_immediate()` ヘルパー追加（BoundsChanged・ybin_changed のフラグリセット漏れバグ修正）
- `camera_scale() -> f32` 公開メソッド追加
- `HeatmapViewState { camera_scale: f32 }` 公開 struct 追加
- 11 件の新テスト追加（RebuildSignal 境界条件 + acceptance test）

**Phase 2-A 完了確認（2026-05-07）:**
- `HeatmapViewState { camera_scale, camera_offset, cell_width_world, cell_height_world }` 定義済み（`heatmap.rs:161-166`）
- `view_state()` / `apply_view_state()` 実装済み（`heatmap.rs:477-493`）
- Elm 側 `Content::ShaderHeatmap.view_state: Option<HeatmapViewState>` 追加済み（`pane.rs:2287`）
- インタラクション後に `*view_state = Some(c.view_state())` で更新済み（`pane.rs:1844`）
- `HeatmapShader` フィールド数: **25**（目標 75 以下を大幅に達成）
- 委譲 API・Elm 統合とも完了。B3 Phase 2-A を ✅ 完了とする。

**Stage B 完了条件**: `HeatmapShader` フィールド 75 以下 + `MockIPCServer` 基本動作 1 秒以内 PASS  
→ **B2 ✅ + B3 ✅ + Phase 2-A ✅。フィールド数 25（達成）。Stage B 完全完了。**

---

## Stage C — ✅ 部分完了（2026-05-07）

### ✅ C1: `mappers.py` 追加・adapter→wire DTO 変換層 + server.py 配線 — **完了（2026-05-08）**

- 文書: adapter-type-boundary Step 3
- 実装済み: `python/engine/mappers.py`
  - `order_book_to_wire()` / `depth_diff_to_wire()` / `trade_to_wire()` / `trades_to_wire()`
  - adapter model（`OrderBook`/`DepthDiff`/`Trade`）→ schemas.py wire DTO への単段変換
  - gap recovery フィールド（`stream_session_id` / `sequence_id` / `prev_sequence_id`）を欠損なく伝搬
  - Decimal の指数表記（`1E-4`）を抑止（`format(d, "f")` を使用）
  - `.model_dump(mode="json")` 直結禁止の設計境界を維持
- テスト: `python/tests/test_mappers.py` — 9 tests PASS（wire compatibility・JSON dump 形式確認を含む）

**server.py 配線（2026-05-08 完了）:**
- `python/engine/kabu_push_pipeline.py` 新設（純粋関数、I/O なし・独立テスト可）
  - `kabu_board_to_wire_dict()` — Raw kabu PUSH 板 JSON → DepthSnapshot wire dict
  - `kabu_execution_to_wire_dict()` — Raw kabu PUSH JSON → Trades wire dict（1 件バッチ）
  - kabu PUSH には ssid/seq がないため server 層が補充する設計を明示
- `server.py` に追加:
  - `_kabu_adapter: KabuStationAdapter`（ログイン前から空で保持）
  - `_kabu_push_ssid: str | None`（VenueReady 後に `{engine_session_id}:kabu_push` で採番）
  - `_kabu_push_seq: int`（PUSH ごとに単調増加）
  - `_on_kabu_board_push(raw)` — 板スナップショット PUSH → pipeline → outbox
  - `_on_kabu_trade_push(raw)` — 約定 PUSH → pipeline → outbox
  - `_clear_kabu_session()` で `_kabu_push_ssid = None` にリセット
- テスト: `python/tests/test_server_adapter_integration.py` — 16 tests PASS
- IPC スキーマ（`SCHEMA_MAJOR` / `SCHEMA_MINOR`）は変更なし ✅

**残作業（次フェーズ C1-next）:**
- `Subscribe(venue="kabu_station")` コマンドを受け付け、`RegisterSet` 動的更新 + kabu WS PUSH ループ起動
- kabu PUSH WS（`kabusapi_ws.connect()`）を `_startup_kabu_station()` 内で開始する
- `_on_kabu_board_push` / `_on_kabu_trade_push` は既に実装済みなので WS ループ追加のみ

### ✅ C2: IPC プロトコル網羅 (S2) + smoke 整理 (S3) — **完了**

- 文書: plan-test-mock-ipc S2 / S3
- 実装済み:
  - S2: `python/tests/test_mock_ipc_server_protocol.py`（4 tests）
    - `Subscribe` → `DepthSnapshot` 応答
    - `FetchKlines` → `Klines` 応答
    - `SCHEMA_MAJOR_MISMATCH` → `EngineError` 応答（`HelloReject` wire は存在しない）
    - `Unsubscribe` + `Shutdown` の受信記録確認
  - S3: `python/tests/conftest.py` に `--smoke` フラグ未指定時スキップを実装
  - S3: `pytest.ini` に `smoke` マーカー定義を追加
- 完了条件チェック:
  - ✅ 実プロセス起動ゼロ（新規テストはすべて MockIPCServer 使用）
  - ⚠ `pytest`（引数なし）全体が 60 秒以内: 現状 86 秒（既存統合テストが重い）。新規 50 tests は 0.5s 以内。

### ✅ C3: Canvas-local 状態の境界明文化 — **完了**

- 文書: gui-triple-state Phase 4
- 実装済み: `src/chart.rs` の `pub enum Interaction` にドキュメントコメントを追加
  - **許容**（Canvas-local OK）: Interaction サブ状態（ドラッグ中・ズーム中）、ホバー座標
  - **禁止**（必ず Elm 管理）: スケール（translation/scaling/camera）・データ配列・再描画ポリシー
  - 新規 canvas widget 実装時のガイドとして機能
- 依存: B3 未完了だが、境界定義はドキュメントコメントのみのため先行実施

**Stage C 完了条件**:
- ✅ IPC ハンドシェイク・`EngineError` フォールバック・スキーマ不一致の各経路が MockIPCServer でカバー
- ⚠ `pytest --smoke -x` で既存 smoke テスト PASS: smoke マーカー基盤は整備済み（conftest.py）。既存テストへのマーカー付与は未実施
- ⚠ `pytest`（引数なし）実プロセス起動ゼロ・60 秒以内: 実プロセス起動ゼロは達成。60 秒は既存統合テストが重く現状 86 秒

---

## Stage D — ✅ `fee_total` 実装完了（2026-05-07）。gRPC 移行解禁

> **`fee_total` とは**: `python/engine/summary.py` のリプレイサマリに手数料合計（`fee_total`）を追加する機能。
> 2026-05-07 完了。実装スコープは **replay summary 用途のみ**。live 経路の commission end-to-end 配線
> （OrderFilled wire schema 拡張）は Stage D 後の別チケットへ繰越。

### ✅ fee_total 完了内容（2026-05-07, レビューループ R1-R3）

- **schema 3.20 → 3.21 bump** — `ExecutionMarker.commission: Optional[str]` 追加（schemas.py / dto.rs / lib.rs 同期）
- **replay 経路** — `engine_runner.py` で nautilus `OrderFilled.commission` (Money) を `as_decimal()` 経由で decimal 文字列化。抽出失敗時は WARN ログ + str() フォールバック多段ガード
- **live 経路** — `narrative_hook.py` で dict-form の commission を伝搬（実配線は `OrderFilled` wire 拡張待ち、scope-out として明記）
- **wire 契約** — replay/live とも commission 不明時は marker dict から key を省略（schema Optional に整合、`skip_serializing_if = "Option::is_none"`）
- **PII allow-list 同期** — `python/engine/pii_scrub.py` + `🐃_blacksheep/scripts/pii_scrub.py` の FILLS_ALLOWED_KEYS に commission 追加（parity test 通過）
- **集計** — `summary.py::compute_summary()` に `fee_total: float` 出力。空文字 commission は upstream missing sentinel として silent 0 扱い、非数値は WARN ログ + 0 扱い
- **docstring 契約** — fee_total の単位（account currency）/ 符号（rebate は負値保持）/ 欠落・非数値の扱い / 税は upstream-defined を明記
- **schema_minor 履歴整合** — Rust `lib.rs` 履歴コメントに 16 番欠番を明文化、Python `schemas.py` には Rust を source of truth とする参照コメント
- **新規/更新テスト** — pytest +12 件 (test_summary.py 5、test_execution_marker_emit.py 2、test_run_buffer_writer.py 1、Rust schema_v2_4_nautilus.rs 2、schema pin 緩和 2)。Python 全件 2185 passed / 5 skipped、Rust engine-client 全件緑

### レビュー反映 (2026-05-07, ラウンド 1-3)

**ラウンド 1（5 並列：silent / general / ws / rust + pytest 全体）**
- CRIT-1 (general): 🐃_blacksheep/scripts/pii_scrub.py の `FILLS_ALLOWED_KEYS` parity を同期
- CRIT-2 (pytest 発見): SCHEMA_MINOR pin 2 件（test_request_venue_login_state.py / test_schemas_nautilus.py）を `==` から `>=` 緩和
- HIGH-1 (silent): commission 抽出を portfolio 更新 outer except から独立化、`as_decimal()` 失敗時に汎用 Exception キャッチ + WARN + str() 多段フォールバック
- HIGH-2 (silent + general): replay の `"0"` デフォルト廃止 → live 経路と統一して **不明時は key 省略**（schema Optional 準拠）
- HIGH-3 (general): `test_engine_runner_streaming_fills` の `required_keys` から commission を分離し `optional_keys` で表現
- HIGH-4 (general): legacy fills.jsonl 後方互換テスト追加（全行 commission 無し → fee_total=0）
- HIGH-5 (general): summary.py docstring に fee_total の単位/符号契約を明文化
- MED 群: 非数値 WARN ログ追加 / Rust dto Some+Zero deserialize テスト / SCHEMA_MINOR 履歴 16 欠番コメント / negative・empty テスト追加 / pii_scrub コメント

**ラウンド 2（2 並列：silent + general crosscheck）**
- MED-1 (silent): `commission_str: str | None` をアノテのみ → `= None` で初期化必須化（UnboundLocalError silent drop ガード）
- MED-2 (silent + general): 空文字 commission の WARN ノイズを除外（`raw_commission not in (None, "")`）
- MED-3 (general): live 経路の commission 配線が wire schema 上で未配線である事実を docstring に明示し、本フェーズスコープを「replay summary」に限定
- MED-4 (general): 計画書 Stage D に本ブロック追記
- MED-5 (general): EngineEvent は Deserialize 専用のため Rust 側 serialize テストは不可。ロジックを deserialize 側で担保する旨をコメントで明記
- LOW-1 (silent): test_run_buffer_writer に commission e2e テスト追加

**ラウンド 3（1 体：silent サニティ）**
- MED-1: engine_runner の `if commission_raw is None: commission_str = None` 重複代入を `is not None` 単純条件に整理
- LOW (false positive 棄却): test_summary.py に `\` 構文エラー指摘 → 実コードは `tmp_path / "..."` で正しい（pytest 緑、CRLF 表示の誤読）
- 残存 LOW: なし

**繰越（Stage D 後の別チケット）**
- `OrderFilled` wire schema (schemas.py / dto.rs) への commission フィールド追加 → live 経路 fee_total 動作開始
- `_read_jsonl` の JSONDecodeError silent skip ログ化（fee_total 以前から効く既存挙動、cross-cutting）
- summary.py の float→Decimal 移行（total_pnl/max_drawdown も float、本変更スコープ外の cross-cutting design）

---

> ### gRPC 移行（解禁済み）

### ✅ G0: `proto/engine.proto` + ビルドパイプライン — **完了（2026-05-08）**

- `proto/engine.proto` 新設（35 Commands, 51 Events を oneof で網羅）
  - `HelloRequest` = `Command.oneof` field 1（handshake 先頭契約）
  - `ReadyResponse` = `Event.oneof` field 1（handshake 成功時の先頭 Event 契約）
  - 全 enum は `ENUM_NAME_UNSPECIFIED = 0` + prefix 付き値（buf lint ENUM_VALUE_PREFIX 準拠）
  - optional フィールドは proto3 `optional` キーワードで明示
- `buf.yaml` / `buf.gen.yaml` 新設（`buf lint` + `buf breaking` 設定）
- `engine-client/build.rs` 新設（`tonic-build` + `protoc-bin-vendored` — Windows 対応）
- `engine-client/Cargo.toml` 更新: `tonic 0.12` + `prost 0.13` + `tonic-build 0.12` + `protoc-bin-vendored 3`
- `pyproject.toml` 更新: `grpcio>=1.62` + `grpcio-tools>=1.62` + `protobuf>=4.25`
- `scripts/gen_proto_python.py` 新設（Python スタブ生成スクリプト）
- `scripts/check_schema_parity.py` 新設（JSON↔proto name 対応チェック）
- **完了条件**:
  - ✅ `cargo check -p flowsurface-engine-client` — proto コンパイル成功（protoc-bin-vendored 経由）
  - ✅ `python scripts/check_schema_parity.py` — 35 commands, 51 events parity OK
  - ✅ `pytest python/tests/` — 2200 passed（既存の pre-existing 1 failure のみ）
- buf lint / `buf breaking --against .git#branch=main` は buf CLI インストール後に CI で実行予定

### ✅ G0.5: Python 側 gRPC mock サーバー骨格 — **完了（2026-05-08）**

- `python/engine/proto/` 新設: `grpcio-tools` で生成した Python スタブ（engine_pb2.py / engine_pb2_grpc.py）
  - `scripts/gen_proto_python.py` で再生成可能、import パスを自動パッチ済み
- `python/tests/fixtures/mock_grpc_server.py` 新設（`grpcio.aio` ベース）
  - `_DataEngineServicer.Session()`: HelloRequest → ReadyResponse、schema_major 不一致で FAILED_PRECONDITION、非 Hello 先頭で INVALID_ARGUMENT
  - `MockGrpcServer`: `async with` / `start()` / `stop()` API、port=0 ランダムポート、stop() 冪等
- `python/tests/test_mock_grpc_server_basic.py` 新設: 9 tests / 0.26s PASS（1 秒制限大幅クリア）
  - lifecycle: 起動・停止・冪等性・context manager
  - handshake: ReadyResponse 先頭確認・schema echo・session_id 一意性・stream 維持
  - failures: INVALID_ARGUMENT / FAILED_PRECONDITION
- `pyproject.toml` dev deps に `pytest-timeout>=2.3` 追加

**[前フェーズ]** G0.5: Python 側 gRPC mock サーバー骨格（1 日）
- `python/tests/fixtures/mock_grpc_server.py` を新設（`grpcio.aio` ベースのテスト用最小サーバー）
- Session 冒頭メッセージ方式の mock 骨格（`Command.oneof.hello = HelloRequest` を受け付け、`ReadyResponse` を最初の `Event` として返す）のみ実装（script 形式は不要、固定応答でよい）
- G1 の `server_grpc.py` 実装の acceptance テストで mock として使用する
- plan-test-mock-ipc-server.md の S1 骨格に相当する gRPC 版
- **完了条件**: `python/tests/test_mock_grpc_server_basic.py` が `@pytest.mark.timeout(1)` デコレータ付きで 1 秒以内に PASS。`Session` 冒頭の `HelloRequest`（`Command.oneof.hello`）に対して `ReadyResponse`（最初の `Event`）を返すラウンドトリップを grpcio チャネルで確認すること。実行コマンド: `pytest python/tests/test_mock_grpc_server_basic.py`。加えて `server.stop(); server.stop()` を連続呼び出しても例外が発生しないことを `test_mock_grpc_server_basic.py` 内で確認すること（`stop()` 冪等性）。

### ✅ G0.9: transport-aware attach/discovery 先行ゲート — **完了（2026-05-08）**

**実装内容:**
- `engine-client/src/session_file.rs::EngineSession` に `pub transport: String` フィールド追加
  - `#[serde(default = "default_transport")]` で旧形式（フィールドなし）は `"ws"` にデフォルト（後方互換）
  - `new()` は `"ws"` 固定（既存呼び出し箇所を破壊しない）
  - `with_transport(port, token, pid, schema_major, transport)` を新設
  - `Debug` 実装に `transport` フィールドを追加
- `engine-client/src/process.rs`
  - `probe_url_from_session_or_default()` ヘルパー追加: session ファイルの `transport` フィールドを読んで適切な probe URL を返す
  - `start_or_attach()` が `DEFAULT_PROBE_URL` 直書きではなく `probe_url_from_session_or_default()` を使用するよう変更
  - `"grpc"` → `grpc://127.0.0.1:{port}`、`"ws"` → `ws://127.0.0.1:{port}/`
- `python/engine/replay_session.py`
  - `PartialSessionFileData` / `SessionFileData` TypedDict に `transport: str` フィールド追加
  - `ReplaySession._resolve_endpoint_and_token()` path (b) に gRPC 分岐: `transport == "grpc"` → `f"127.0.0.1:{port}"` (grpcio channel target 形式)
  - `LiveSession._resolve_endpoint_and_token()` にも同じ gRPC 分岐を追加
- `python/tests/test_engine_session_transport.py` 新設（8 tests）
  - reader 側 acceptance pin: `_resolve_endpoint_and_token()` が `transport="grpc"` のとき gRPC URI を返す (ReplaySession / LiveSession 両方)
  - writer 側 acceptance pin: session ファイルに `transport="grpc"` が書き込めること（`server_grpc.py` が G1 で書く契約を手動再現）
  - 後方互換: `transport` フィールドなし → `"ws"` 扱い
- `engine-client/tests/session_file.rs` に G0.9 テスト 4 件追加
  - `transport_field_defaults_to_ws` / `transport_field_grpc_serializes_to_json` / `transport_field_missing_in_old_format_defaults_to_ws` / `debug_shows_transport_field`

**完了確認:**
- ✅ `cargo test --workspace` 全 PASS
- ✅ `cargo clippy -p flowsurface-engine-client -- -D warnings` 警告なし
- ✅ `pytest python/tests/test_engine_session_transport.py` 8/8 PASS
- ✅ writer 側 acceptance pin: `test_writer_acceptance_pin_grpc_transport_in_session_file` PASS
- ✅ reader 側 acceptance pin: ReplaySession/LiveSession 両 gRPC 分岐テスト PASS

**⚠ G1 着手時の注意事項:**
- `_AttachClient` は WS 専用のため、gRPC セッションファイルがある場合の `__enter__` では WS handshake が失敗して inprocess にフォールバックする（G0.9 では意図的な動作）。G1 では `_GrpcAttachClient` を追加し、`endpoint.startswith("ws://")` vs gRPC で分岐する
- writer 側 acceptance pin は G1 で `server_grpc.py` が実際に session ファイルを書くようになったら置き換えること（現在は手動再現テスト）
- `probe_url_from_session_or_default()` の gRPC 分岐は G2 の tonic クライアントが実装されるまで実際には使われない（`EngineConnection::probe` が WS 固定のため）

- **⚠ G0.9 完了後でないと G1 に着手してはならない**（endpoint 解決経路が未整備のまま G1 を実施すると attach mode が WS 固定のまま残る）

### ✅ G1: Python サーバーを gRPC に置き換え — **完了（2026-05-08）**

**実装内容:**
- `python/engine/server_grpc.py` 新設（290 行、`grpcio.aio` ベース）
  - `_GrpcDataEngineServicer`: Session RPC 実装（handshake + recv/send ループ）
  - `_GrpcSessionKey`: `_Broadcaster` dict key 用の sentinel クラス
  - `_cmd_payload_to_dict()`: proto Command → dict（`MessageToDict` 利用）
  - `_dict_to_proto_event()`: dict → proto Event（`ParseDict` + 51 event 全マッピングテーブル）
  - `GrpcDataEngineServer`: ライフサイクル管理（`serve()` で gRPC サーバー起動）
  - gRPC status codes: RESOURCE_EXHAUSTED / UNAUTHENTICATED / FAILED_PRECONDITION / INVALID_ARGUMENT / DEADLINE_EXCEEDED
  - 既存 `DataEngineServer._dispatch()` / `_outbox` / `_connections` をそのまま再利用
- `python/engine/__main__.py` 更新: `--transport ws|grpc`（デフォルト `grpc`）フラグ追加
- `engine-client/Cargo.toml` 更新: `[features]` に `ws-transport = []` 追加
- `python/tests/test_grpc_smoke.py` 新設: 5 tests（handshake / 認証失敗 / schema mismatch / wrong first message / Ping→Pong）
- `python/tests/test_server_grpc_multi_client.py` 新設: 3 tests（2 クライアント ClientConnected / MAX_CONNECTIONS 超過 RESOURCE_EXHAUSTED / 2 回目 HelloRequest INVALID_ARGUMENT）

**完了確認:**
- ✅ `pytest python/tests/test_grpc_smoke.py python/tests/test_server_grpc_multi_client.py` 8/8 PASS（1.05s）
- ✅ `pytest python/tests/test_mock_grpc_server_basic.py` 既存テスト引き続き PASS
- ✅ `--transport ws` フラグで従来 WS server.py が起動（ロールバック手段確保）
- ✅ full suite: 2223 passed（pre-existing 1 failure は test_replay_snapshot.py::TestRestoreSnapshotUiRollback、本変更に無関係）

**設計上の知見:**
- grpcio サーバー側の async iterator は `__anext__()` を使う（クライアント側の `.read()` とは異なる API）
- `MessageToDict` のキーワード引数: `preserving_proto_field_name=True`（末尾の `s` なし）、`always_print_fields_with_no_presence=False`（`including_default_value_fields` は古い API）
- `_Broadcaster` の dict key は `ServerConnection` 型制約ではなく hashable 任意オブジェクト → `_GrpcSessionKey` インスタンスがそのまま使える
- クライアント側 `stream.cancel()` は coroutine ではない（`await` 不要）

### レビュー反映 (2026-05-08, G1 ラウンド 1)

**レビュー実施**: silent-failure-hunter + general-purpose の 2 エージェント並列  
**集約結果**: CRITICAL×2, HIGH×4, MEDIUM×5 → 全件修正済み。LOW のみ残留。

**修正内容:**

| 重要度 | 対象 | 修正内容 |
|--------|------|---------|
| CRITICAL | `server_grpc.py:245` | `except (asyncio.TimeoutError, Exception): pass` → `log.warning` を各例外に追加（WS server.py と同じパターンに統一） |
| CRITICAL | `server_grpc.py:259` | `_startup_tachibana` を全接続で呼んでいた → `len(self._server._connections) == 1` ガードで初回接続時のみ呼ぶように修正（多重 spawn 防止） |
| HIGH | `server_grpc.py:281` | `done` タスクの例外を未チェック → `t.exception()` + `log.error` で確認するよう修正 |
| HIGH | `server_grpc.py:293-313` | `except Exception` → `except (asyncio.CancelledError, Exception)` に修正（CancelledError が Exception を継承しないことがある Python バージョン対策） |
| HIGH | `server_grpc.py:127-128` | `_ENGINE_VERSION if hasattr(server, "_ENGINE_VERSION") else _ENGINE_VERSION`（両辺が同じ） → `engine_version = _ENGINE_VERSION` に単純化 |
| HIGH | `_recv_loop` | `_FIELD_TO_OP.get(which, which)` → `get()` が `None` を返した場合に `log.warning` を出してから `which` をフォールバックとして使用 |
| MEDIUM | `test_server_grpc_multi_client.py:77` | `ev1 is not None or ev2 is not None` → `ev1 is not None and ev2 is not None` に修正（両クライアントへの broadcast を正しく検証） |

**見送り（根拠あり）:**
- `test_grpc_smoke.py` への `@pytest.mark.smoke` 付与: conftest.py の定義上 `smoke` マーカーは「実プロセス起動を伴うテスト」専用。gRPC テストは in-process fixture を使うため、付与すると CI で不当にスキップされる。**付与しない**。
- `start_or_attach.rs` の gRPC mock 書き直し: G2（Rust クライアント gRPC 化）の一環として実施予定。G1 完了後の独立作業として繰り越し。
- stdin `transport` フィールド: `args.transport` は常に CLI から読まれ、default `"grpc"` が設定される。Rust supervisor は `--transport` CLI フラグ経由で制御可能。stdin cfg には不要。

### レビュー反映 (2026-05-08, G1 ラウンド 2)

**レビュー実施**: silent-failure-hunter + general-purpose の 2 エージェント並列  
**集約結果**: CRITICAL×1, HIGH×4, MEDIUM×3 → 全件修正済み。LOW のみ残留。

**修正内容:**

| 重要度 | 対象 | 修正内容 |
|--------|------|---------|
| CRITICAL | `server_grpc.py:295-331` | replay セッション終了後も `_mode="replay"` が残り次の live 接続が FAILED_PRECONDITION で弾かれるバグ → `_connections` が空になったときに `_mode = "live"` にリセット |
| HIGH | `server_grpc.py:262` | `_startup_tachibana` タスク例外が無音 → `add_done_callback` で `log.error` + `exc_info` 追加 |
| HIGH | `server_grpc.py:281` | `log.error` に `exc_info` なし → traceback 消える → `exc_info=exc` 追加 |
| HIGH | `server_grpc.py:357` | dispatch `log.error` に `exc_info` なし → `exc_info=True` 追加 |
| HIGH | `server_grpc.py:413` | `add_insecure_port` 返値 0（バインド失敗）を無チェック → `RuntimeError` raise 追加 |
| MEDIUM | `server_grpc.py:202` | 汎用ハンドシェイク例外を `DEADLINE_EXCEEDED` で返すのは誤り → `INTERNAL` + `log.warning` に修正 |
| MEDIUM | `server_grpc.py:325` | `_cancel_all_streams()` 例外で後続の state リセットがスキップ → `try/except log.error` で囲む |
| MEDIUM | `server_grpc.py:241` | `prepare()` を全接続ごとに呼ぶ → `not self._server._connections` ガードで初回のみに変更 |

**見送り（根拠あり）:**
- mode write race: asyncio は協調マルチタスク、`await` なしの同期ブロックで2コルーチン交差不可 → LOW
- stdin `transport` フィールド: 本番は常に gRPC、WS ロールバックは CLI `--transport` フラグで対応 → 設計上不要
- `_send_loop` return が `_recv_loop` を停止しない: `asyncio.wait(FIRST_COMPLETED)` が正しく処理する → 偽陽性

**LOW（残留）:**
- `server_grpc.py` に `_ENGINE_VERSION` が `engine.server` から関数内 import されている（モジュール結合）— G3 で server.py 廃止時に整理予定
- `_proto_mode_to_str` の未知 mode 値が無音で "live" フォールバック — G3 時に `log.warning` 追加予定

### レビュー反映 (2026-05-08, G1 ラウンド 3)

**レビュー実施**: silent-failure-hunter + general-purpose の 2 エージェント並列  
**集約結果**: CRITICAL×0, HIGH×4, MEDIUM×4 → 全件修正済み。LOW のみ残留。

**修正内容:**

| 重要度 | 対象 | 修正内容 |
|--------|------|---------|
| HIGH | `server_grpc.py:265-271` | done_callback ラムダで `t.exception()` を2回評価 → `_log_startup_tachibana_error` 名前付き関数に置き換え |
| HIGH | `server_grpc.py:391` | send 失敗ログにイベント名・`exc_info` なし → `event=%s` と `exc_info=True` 追加 |
| HIGH | `server_grpc.py:377` | `except asyncio.TimeoutError` が `except asyncio.CancelledError` より先 → 順序を入れ替え |
| HIGH | `server_grpc.py:230-258` | mode 設定と `_connections.add()` の間に await 境界2つ → `asyncio.Lock` で mode 確定・接続登録を atomic 化 |
| MEDIUM | `server_grpc.py:244` | `gather(return_exceptions=False)` → どのワーカーが落ちたか不明 → `return_exceptions=True` + worker 名付きログ |
| MEDIUM | `server_grpc.py:304` | pending タスクのキャンセル中例外を完全握り潰し → `CancelledError` と `Exception` を分けて後者は `log.debug` |
| MEDIUM | `test_server_grpc_multi_client.py` | mode mismatch (FAILED_PRECONDITION) テスト未存在 → `test_mode_mismatch_second_client_returns_failed_precondition` 追加 |
| MEDIUM | `server_grpc.py:263` | Tachibana ガードを `len(connections)==1` から Lock 内で確定した `is_first` フラグに変更（明確化） |

**見送り（根拠あり）:**
- `EngineCapabilities.venue_capabilities` 欠落（kabu 本番バナー）: proto 定義と Rust 実装を確認せずに修正不可 → 要調査 LOW として残留
- `yield` 前キュー登録: 設計上の制約（ReadyResponse → 登録の順序）、窓は無視可能
- ParseDict 変換失敗 WARNING → ERROR: 意図的 drop（ignore_unknown_fields=True のフォールバック）のため維持

**LOW（残留）:**
- `_ENGINE_VERSION` 関数内 import — G3 で整理
- `_proto_mode_to_str` 未知値の無音フォールバック — G3 で `log.warning` 追加予定
- `EngineCapabilities.venue_capabilities` 未設定 — proto/Rust 側の調査が必要（G2 着手前に確認）
- type annotation: `_Broadcaster` key 型が `ServerConnection` 前提 — gRPC では `_GrpcSessionKey` を渡しているが mypy では検知不可

**残作業（G1-next / G2 前に確認）:**
- `ReplaySession(force_mode="attach")` gRPC attach テスト（`test_replay_session_attach.py` gRPC 版）
- `tests/start_or_attach.rs` の WS mock → gRPC mock 書き直し（G0.5 spec に記載、G2 フェーズで実施）
- `server_grpc.py` が起動時に session ファイルへ `transport="grpc"` を書く（G2 で process.rs 連携時）

> ⚠ **endpoint 解決経路の再設計が必要**: 現行 `replay_session.py::_resolve_endpoint_and_token()` は session ファイルから `ws://127.0.0.1:{port}/` を組み立てる（WS URL 固定）。gRPC 移行後は `transport` フィールドを見て gRPC チャネルを組み立てる分岐に変更する必要がある。**`replay_session.py` 内の `LiveSession._resolve_endpoint_and_token()`（`ReplaySession` とは独立した実装）も同様に `transport` フィールド分岐を追加する必要がある**。この修正を忘れると attach mode が WS のままになる。`process.rs::DEFAULT_PROBE_URL = "ws://127.0.0.1:19876/"` もトランスポート対応に変更が必要。これらは `ipc-grpc-migration.md` G1 作業項目に詳細を追記済み。G1 の工数見積もりにこの endpoint 解決経路の再設計（`ReplaySession` 側・`LiveSession` 側の両方）を含めること。

**✅ G2: Rust クライアントを gRPC に置き換え（2026-05-08 完了）**

**完了内容:**
- `engine-client/src/grpc_transport.rs` 新設（約 700 行、`tonic` ベース gRPC クライアント）
- `engine-client/tests/grpc_wire_integration.rs` 新設（4 `#[ignore]` テスト、実 Python サブプロセスを使うワイヤー統合テスト）
- `engine-client/src/connection.rs`: `EngineConnection::connect_grpc()` コンストラクタ追加
- `engine-client/src/process.rs`: `grpc://` プローブ URL のハンドリング追加
- `python/engine/server_grpc.py`: 起動時に `engine-session.json` へ `transport="grpc"` を書き込む
- `engine-client/Cargo.toml`: `tokio-stream` 依存追加
- `cargo test --workspace` 全 PASS（`grpc_wire_integration` の 4 テストは `#[ignore]`、Python+grpcio 環境で明示実行）

**設計決定と背景:**

**① `EngineConnectionLike` trait 化を回避 → 第2コンストラクタ方式を採用**
- 当初計画では `Arc<EngineConnection>` を trait object に変える大規模リファクタを想定していた
- 実際には `connect_grpc()` を `EngineConnection` の第2コンストラクタとして追加し、戻り型は同一の `EngineConnection` にすることで全呼び出し箇所の変更ゼロを達成
- 教訓: concrete struct に複数コンストラクタを生やす方が trait 化より影響範囲が小さい場合が多い

**② HelloRequest の事前バッファリング**
- `tonic::client::session(stream)` を呼ぶ前に `proto_tx.send(hello_cmd)` で HelloRequest をチャネルに積む
- こうすると `ReceiverStream::new(proto_rx)` がストリームの先頭要素として HelloRequest を自動的に送信する
- `client.session()` 呼び出し後に送ると HelloRequest がサーバーに届く前に ReadyResponse を待ち始めてデッドロックする危険がある

**③ 2 段チャネルアーキテクチャ**
```
dto::Command  →  [converter task]  →  proto::Command  →  ReceiverStream  →  gRPC request stream
                                                                           ↓
dto::EngineEvent  ←  [reader task]  ←  proto::Event  ←  event_stream.message()
```
- `mpsc::Sender<dto::Command>` を外部に公開し、内部で converter task が `proto::Command` に変換
- これにより `grpc_transport.rs` の内部実装を変えても呼び出し側 (`connection.rs` 等) は変更不要

**④ grpc:// → http:// URL 変換**
- `engine-session.json` には `"transport":"grpc"` と `"port":50051` が書かれる
- `process.rs` の `try_attach_or_spawn_inner` が `grpc://127.0.0.1:50051` を組み立てて保持する
- tonic の `Endpoint::from_shared()` は `http://` スキームを要求するため、`grpc://` → `http://` に変換してから渡す

**⑤ prost optional enum フィールドの型**
- `.proto` の `optional ReplayGranularity granularity` は Rust 側で `Option<i32>` になる（`Option<ReplayGranularity>` ではない）
- 変換パターン: `.granularity.and_then(proto_optional_granularity_to_dto)`（helper が `i32 → Option<dto::ReplayGranularity>` を返す）

**Tips（次の作業者向け）:**
- gRPC ハンドシェイクエラー（FAILED_PRECONDITION, UNAUTHENTICATED）は `client.session().await` では出ない。`event_stream.message().await` （ReadyResponse 待ち）で出る。タイムアウトはここに設定する
- `grpc_status_to_error()` でステータスコード → `EngineClientError` のマッピングを一元管理している（`src/grpc_transport.rs` 内）
- proto enum の Rust 変換: `AppMode::try_from(i32_value).ok()` のパターン。`as i32` でプロトコル送信
- `capabilities_to_json()` で `engine::EngineCapabilities` → `serde_json::Value` 変換。NULL 安全に `Option<engine::EngineCapabilities>` を受け取る
- ワイヤー統合テスト実行: `cargo test -p flowsurface-engine-client --test grpc_wire_integration -- --include-ignored`（Python venv 要）

### レビュー反映 (2026-05-08, G2 ラウンド 1)

**レビュー実施**: rust-reviewer + silent-failure-hunter + type-design-analyzer + general-purpose の 4 エージェント並列  
**集約結果**: CRITICAL×3, HIGH×8, MEDIUM×9 → 全件修正済み。LOW のみ残留。

**修正内容:**

| 重要度 | ID | 対象 | 修正内容 |
|--------|----|----|---------|
| CRITICAL | CR-A | grpc_transport.rs:129 | collapsible_if clippy エラー → && で折り畳み |
| CRITICAL | CR-B | grpc_transport.rs:127,143 | reader task に NotifyOnDrop ガード追加（panic 時も wait_closed() を保証） |
| CRITICAL | CR-C | grpc_transport.rs:564 | KlineUpdate.kline? → log::warn + early return |
| HIGH | H-A | grpc_wire_integration.rs:132 | schema mismatch テストに実際の bad schema 送信を実装 |
| HIGH | H-B | grpc_transport.rs:932 | OrderSide::Unspecified → Buy フォールバック廃止、None を返してログ |
| HIGH | H-C | grpc_transport.rs:148 | events_tx.send() 失敗時に reader task を break |
| HIGH | H-D | grpc_transport.rs:444,508 | unwrap_or_default() → log::error + String::new() |
| HIGH | H-E | server_grpc.py:478 | _write_grpc_session_file が FLOWSURFACE_DATA_PATH を参照するように修正 |
| HIGH | H-F | test_engine_session_transport.py | writer acceptance pin を実際の _write_grpc_session_file 呼び出しに差し替え |
| HIGH | H-G | grpc_transport.rs:634 | TickerStats.stats parse failure に log::warn 追加 |
| HIGH | H-H | grpc_transport.rs:830 | StrategyScenarioLoaded.scenario parse failure に log::warn 追加 |
| MEDIUM | M-B | grpc_transport.rs:130 | converter task break 時に log::warn 追加 |
| MEDIUM | M-C | grpc_transport.rs:147 | proto_event_to_dto None に log::debug 追加 |
| MEDIUM | M-D | server_grpc.py:450 | _write_grpc_session_file 失敗を log.error に昇格 |
| MEDIUM | M-E | test_grpc_smoke.py | _write_grpc_session_file の smoke テスト追加 |
| MEDIUM | M-F | proto/engine.proto | venue_capabilities フィールド調査 → proto に存在しない → 対応不要（クローズ） |
| MEDIUM | M-G | grpc_transport.rs:start_grpc_session | http:// 前提の doc comment 追加（start_grpc_session / start_grpc_session_with_schema 両方） |
| MEDIUM | M-H | grpc_wire_integration.rs:44 | wait_for_port に tokio::time::timeout ガード追加（10s、メッセージ付き） |
| MEDIUM | M-I | connection.rs | module comment を gRPC/WS 両対応に更新 |

**見送り（根拠あり）:**
- M-A (capabilities_to_json 2回呼び出し): 機能的問題なし、実装コスト対効果低 → G3 整理時に対応
- capabilities_to_json の serde_json::Value ダウングレード: 計画書に設計意図を明記済み

**LOW（残留）:**
- EngineClientError::WebSocket の gRPC 流用命名 — G3 で Grpc variant 追加予定
- proto_order_record_to_dto が常に Some を返すが Option を返す型 — 次 PR でリファクタ
- AttemptedCommand::Unspecified → LoadReplayData フォールバック — UI 影響限定的
- proto enum 未知値ログなし (QtyNormKind, EngineState 等) — G3 で統一対応
- Python started_at マイクロ秒精度差 — serde chrono が両形式を受理、実害なし

### レビュー反映 (2026-05-08, G2 ラウンド 2)

**レビュー実施**: rust-reviewer + silent-failure-hunter の 2 エージェント並列（変更層のみ）  
**集約結果**: CRITICAL×0, HIGH×3, MEDIUM×4 → 全件修正済み。

**修正内容:**

| 重要度 | 対象 | 修正内容 |
|--------|------|---------|
| HIGH | connection.rs:168 | `connect_grpc_with_schema` を `#[cfg(feature="testing")]` でゲート（production 漏出防止） |
| HIGH | grpc_wire_integration.rs 全テスト | `KillOnDrop` RAII ガード追加（panic 時も Python 子プロセスを kill） |
| HIGH | grpc_transport.rs:Ok(None) arm | サーバー EOF 時の `log::info!` 追加（切断理由が診断できない問題を解消） |
| MEDIUM | grpc_wire_integration.rs:154 | schema mismatch テストにエラーメッセージ assert 追加 |
| MEDIUM | grpc_transport.rs:195 | subscriber ゼロ時の break を廃止（`debug!` + continue に変更。WS 実装と整合） |
| MEDIUM | grpc_transport.rs:163 | converter task break 時に廃棄コマンド数をログ |
| MEDIUM | grpc_transport.rs:500,573 | `unwrap_or_else(→ String::new)` を `return None` に変更（空文字送信を防止） |

**R3 サニティチェック**: silent-failure-hunter 単独 — CRITICAL 0 / HIGH 0 / MEDIUM 0 **収束**

**新たに判明した知見:**
- `broadcast::Sender::send()` が `Err` を返す（subscriber ゼロ）場合に reader task を break させると、接続直後のレース条件で reader が消える。WS 実装同様に warn+continue が正しい
- `#[cfg(feature = "testing")]` gate + dev-dependency `features = ["testing"]` の組み合わせで test-only API を integration test にのみ公開できる（release binary に漏出しない）

**LOW（残留 — R1 引き継ぎ）:**
- EngineClientError::WebSocket の gRPC 流用命名（G3 で対応）
- ping-pong テストの `events.recv() Err(Closed)` ループ（Low 優先度）
- CI で `python` コマンドが venv 外を指す可能性（`PYTHON` env 対応は後回し）

**✅ G3: WebSocket トランスポート廃止（2026-05-08 完了）**

### 実施内容

**Rust 側（engine-client）**
- `connection.rs` を 507 行 → 136 行に縮小。`connect()`/`connect_with_mode()`/`probe()` および WS IO タスク実装（fastwebsockets, hyper, http-body-util 依存）をすべて削除
- `Cargo.toml` から WS 依存クレート（fastwebsockets, http-body-util, hyper, hyper-util）、dev-dep（tokio-tungstenite, futures-util）、`ws-transport` フィーチャーフラグをすべて削除
- `src/main.rs` の `connect_with_mode()` 呼び出しを `connect_grpc()` に置き換え（`grpc://` → `http://` 変換を追加）
- `tests/grpc_wire_integration.rs` の `grpc_schema_major_mismatch_rejected` テストに `#[cfg(feature = "testing")]` を追加（セルフ dev-dep による feature 有効化がこの Cargo バージョンでは統合テストに伝播しないため）

**Python 側（server_grpc.py）**
- `_ENGINE_VERSION` の import を `_build_ready_event()` 関数内から モジュールトップレベルに移動（G1 LOW 修正の完了）
- `_proto_mode_to_str` の未知値 `log.warning` は前フェーズで実装済み

**Python テスト**
- `test_server_ws_compat.py`（RSV ビット / per_message_deflate テスト）を削除 → `rg "per_message_deflate|rsv"` ゼロ件を確認

### 検証結果（最終確認: 2026-05-08）
- `cargo test --workspace` ✅ 全 PASS（0 failed, 2 ignored = grpc_wire_integration の #[ignore] テスト）
- `cargo clippy --workspace -- -D warnings` ✅ 警告ゼロ
- `cargo fmt --check` ✅ 差分なし
- `uv run pytest python/tests/ --ignore=python/tests/test_replay_benchmark.py -q` ✅ 2145 passed, 97 skipped
- `rg "per_message_deflate|rsv" --type rust --type python` ✅ ゼロ件

### Rust 統合テストの gRPC 移行（G3 実装詳細）

WS 依存だった 10 個の統合テストファイルを tonic gRPC モックに移行:

**共通ヘルパー** (`tests/common/mod.rs`):
- `MockServicer` — `DataEngine` trait 実装。token 検証・schema major 確認・ReadyResponse 送信・追加イベントストリーミング・`cmd_sink` チャネルへのコマンド転送をサポート
- `MockGrpcEngine` — `start_basic` / `start_close_after_ready` / `start_with_events` / `start_with_capabilities` / `start` の 5 種のコンビニエンスコンストラクタ

**移行ファイル**:
- `wait_ready.rs` — `MockGrpcEngine::start_basic` + `connect_grpc` で `wait_ready()` 即時解決を確認
- `connection_closed.rs` — `start_close_after_ready` で `wait_closed()` を確認
- `handshake.rs` — Hello/Ready ハンドシェイク・schema mismatch 拒否・`capabilities()` getter
- `capabilities_no_secret_keys.rs` — 空 caps でのキー検査
- `capabilities_changed_after_reconnect.rs` — 2 つの MockGrpcEngine インスタンスで capabilities 更新を確認
- `depth_gap_recovery.rs` — `DepthGapEvent` proto → `RequestDepthSnapshot` コマンド送信・ストリーム継続確認
- `tachibana_session_reset.rs` — 新 `stream_session_id` で gap-detector がリセットされ `RequestDepthSnapshot` が送信されない確認
- `process_lifecycle.rs` — `on_ready` / `on_restart` コールバック確認（WS 非依存テストはそのまま維持）
- `ticker_meta_map_round_trip.rs` — インライン `TachibanaTickerMock` で `ListTickers` → `TickerInfoEvent` 応答 → `fetch_ticker_metadata` 動作確認
- `tachibana_kline_capability_gate.rs` — インライン `KlineGateMock` で `FetchKlines` → `ErrorEvent{code:"not_implemented"}` → `AdapterError::InvalidRequest` 変換確認

**Cargo.toml / build.rs 変更点**:
- tonic: `"server"` + `"router"` フィーチャー追加（tonic サーバースタブ生成のため）
- tokio-stream: `"net"` フィーチャー追加（`TcpListenerStream` のため）
- `build.rs`: `build_server(false)` → `build_server(true)` に変更して `DataEngine` trait / `DataEngineServer` を生成

### 設計上の注意点・Tips

**DataEngineServer を削除しなかった理由**: `server.py` には WS トランスポート（`serve()`/`_handle()`/`_recv_loop()`/`_send_loop()`）とビジネスロジック（`DataEngineServer`/`ReplayState`/`LiveState`/`_Broadcaster`）が共存している。gRPC サーバー (`GrpcDataEngineServer`) がビジネスロジックを委譲しているため、`DataEngineServer` クラスを削除するには大規模リファクタリングが必要。G3 では WS トランスポートメソッドのみ削除し、ビジネスロジックは保持した。

**`import websockets` を残した理由**: `server.py` 内で Tachibana の kabu EVENT WebSocket（**アウトバウンド** WS 接続）に `websockets.connect()` を使用している（IPC とは無関係）。`from websockets import ServerConnection`（IPC **インバウンド** 型注釈）は削除済み。

**`SCHEMA_MAJOR`/`SCHEMA_MINOR` を残した理由**: `grpc_transport.rs` の gRPC ハンドシェイク（`HelloRequest` の schema 番号フィールド）でまだ使用中。WS コンテキストから削除したが定数自体は維持。

**WS テストのスキップ方針**: WS トランスポート依存テスト 91 個にスキップマーカーを付与（削除でなくスキップにした理由：ビジネスロジックは健全で、テスト自体のロジックは gRPC 版への移植候補になりうるため）。

### レビュー反映 (2026-05-08, G1-G3 ラウンド 1-3)

**レビュー構成**: R1 (5並列: rust-reviewer / silent-failure-hunter / type-design-analyzer / ws-compatibility-auditor / general-purpose) → R2 修正 → R2 サニティ (silent + general) → R3 サニティ (silent)

**R1 修正 (CRITICAL 0 / HIGH 8 / MEDIUM 13 → 全件解消):**

| ID | 対象 | 修正内容 |
|----|------|---------|
| H1 | `process.rs:627` | `EngineSession::new()` → `with_transport(TransportKind::Grpc)` — attach mode 破損の修正 |
| H2 | `replay_session.py` | `_GrpcAttachClient` 新設 — G1 完了条件の未実装を解消 |
| H3 | `server_grpc.py:60` | `_EVENT_TO_FIELD_AND_CLASS` から `"Ready"` 削除 + `_dict_to_proto_event` に early-return ガード |
| H4 | `server_grpc.py:209` | `StopAsyncIteration` に `log.debug` 追加 |
| H5 | `error.rs:364` + 9箇所 | `EngineClientError::WebSocket` → `GrpcTransport` にリネーム |
| H6 | `session_file.rs:31` | `transport: String` → `TransportKind` enum（typo による silent failure 防止） |
| H7 | `test_live_session_attach.py` | `pytestmark = pytest.mark.skip` 追加（WS transport 廃止対応） |
| H8 | `test_live_session_kabu.py` | 同上 |
| M1-M13 | 各ファイル | MessageToDict 注釈 / buf CI / OrderType Unspecified → None / AttemptedCommand::Unknown / _proto_mode_to_str FAILED_PRECONDITION / tokio::spawn handle / 接続数 Lock / start_or_attach stub / Ready イベントログ / sleep(0.2)削除 / testing feature gate / pid_is_live async 化 / token sentinel |

**R2 修正 (CRITICAL 0 / HIGH 3 / MEDIUM 3 → 全件解消):**
- HIGH: `send` → `send_command` 改名、`wait_for(timeout=)` → `(timeout_s=)` 修正、`_recv_loop` import/dict をループ外移動 + `"ready"` フィルタ追加
- MEDIUM: `handshake()` エラーパステスト 3件追加（schema mismatch / wrong token / 正常）、`sleep(0.1)` 削除、`buf breaking` 初回実行ガード

**R3 サニティ**: CRITICAL 0 / HIGH 0 / MEDIUM 0 — **収束**

**新規追加テスト**: +9件（`test_engine_session_transport.py` +6, `mock_grpc_server.py` expected_token 対応 +3）
**繰越（LOW のみ）**: env-only gRPC attach フォールバック / `ConnectionDropped` send 失敗の診断性 / `AttemptedCommand::Unknown` UI 表示改善 / backend.rs/dto.rs コメント残骸

**Stage D 完了条件**:
- `ipc-schema-check` スキルの内容が `buf` コマンドに更新済み
- `cargo test grpc_wire_integration` が実 `server_grpc.py` サブプロセスを起動した上で PASS（tonic↔grpcio の実 wire 互換性を確認。mock のみの完了は不可）
- multi-client attach 動作確認：同時 2 クライアント接続・`ClientConnected` broadcast・`MAX_CONNECTIONS=4` 超過時 `RESOURCE_EXHAUSTED` が gRPC 経由で動作
- WS トランスポート（`server.py` + `ws_client.rs`）削除後の smoke テスト PASS（`pytest --smoke -x`）
- gRPC mock（`mock_grpc_server.py`）が全 transport-level テストで使用されている（実プロセス起動ゼロ）
- `rg "per_message_deflate|rsv" --type rust --type python` がゼロ件（RSV ビット関連コード完全消滅を機械確認）

---

## 全体タイムライン（2026-05-08 更新）

```
2026-05-07
 │
 ├─[A1 Python]──────────────────────────────── ✅ 完了
 ├─[A2 Rust: Message分割]─────────────────────── ✅ 完了
 └─[A3 Rust: Ladder scroll]──────────────────── ✅ 不変条件確認済（追加実装不要）
                         │
                         ├─[B1 KabuStationAdapter]──────────────── ✅ 完了
                         ├─[B2 MockIPCServer S1]─────────────────── ✅ 完了
                         └─[B3 HeatmapShader ★]──────────────────── ✅ 完了
                                              │
                                              ├─[C1 mappers.py + adapter→wire定義]── ✅ 完了（server.py配線は未）
                                              ├─[C2 S2+S3 smoke整理]────────────── ✅ 完了
                                              └─[C3 Canvas境界]─────────────────── ✅ 完了
                                                              │
                                              fee_total 完了 ─┤  ← ✅ 完了（2026-05-07, R1-R3 収束）
                                                              │
                                                              └─[G0✅→G0.5✅→G0.9✅→G1✅→G2✅→G3✅]► 全フェーズ完了
```

---

## 計測指標（全体）

| 指標 | 2026-05-07 着手前 | 2026-05-07 現在 | 目標 |
|------|------|------|------|
| `main.rs` 総行数 | 7,447 行 | 4,109 行（handle_* を src/handlers/ に抽出）✅ | 4,000 行以下 |
| `update()` 行数 | 3,579 行 | 11 行（dispatch hub）✅ | 400 行以下 |
| `Message` バリアント数（フラット） | 158（2026-05-07 実測） | 7 グループ nested enum ✅ | グループ 7 以下（nested enum 再設計） |
| `HeatmapShader` フィールド数 | 150+ | **25**（B3 Phase 2-A 完了 ✅） | 75 以下 |
| `pytest`（引数なし）実行時間 | 30〜120 秒 | **44.7s**（2145 passed / 99 skipped ✅） | 60 秒以内 ✅ |
| adapter boundary モデル | なし | `models.py`（4 モデル / 19 tests） | — |
| KabuStation adapter | なし | `kabusapi_adapter.py`（13 tests） | — |
| MockIPCServer (gRPC) | なし | S1〜S5 完了（S4: 9 tests / S5: 6 tests / < 1s ✅） | S1〜S5 完了 ✅ |
| adapter→wire mapper | なし | `mappers.py`（9 tests） | — |
| smoke marker 基盤 | なし | `conftest.py` + `pytest.ini` 整備済み | — |
| IPC スキーマ管理 | `SCHEMA_MAJOR/MINOR` 手書き | 変更なし | protobuf フィールド番号のみ |
| RSV ビット圧縮バグ | 再発リスクあり | ✅ 原理消滅（G3: gRPC 移行完了、WS コード全削除、rg ゼロ件確認済） | gRPC 移行で原理消滅 |

---

## 残作業サマリ（次セッション向け）

### 次に着手すべき作業（優先順）

1. ~~**A2 残作業: `update()` 委譲**~~ — ✅ 2026-05-08 完了
   - `handle_*` 7 メソッドを `src/handlers/` サブモジュール群に抽出（3,772 行削減）
   - `main.rs` 7,881 行 → 4,109 行。`cargo test --workspace` 全 PASS

2. ~~**B3 Phase 2-A: `scene.camera` → `HeatmapViewState` 委譲**~~ — ✅ 2026-05-07 完了確認
   - `HeatmapViewState` 定義・`view_state()`/`apply_view_state()` 実装・Elm 側 `view_state` フィールドすべて実装済み
   - `HeatmapShader` フィールド数 25（目標 75 以下）

3. ~~**C1 server.py 配線**~~ — ✅ 2026-05-08 完了
   - `kabu_push_pipeline.py` 新設 + server.py メソッド追加 + 16 tests PASS
   - worker → adapter model → mapper → wire DTO → outbox のパスを実際に wire-up する
   - `test_server_adapter_integration.py` 統合テストが必要

5. ~~**fee_total 実装**（Stage D 解禁のゲート）~~ — ✅ 2026-05-07 完了
   - ExecutionMarker.commission 追加（schema 3.20→3.21）
   - replay 経路で nautilus `OrderFilled.commission` を decimal 文字列化
   - `summary.py::compute_summary()` に `fee_total` 出力
   - blacksheep parity 同期（FILLS_ALLOWED_KEYS）

6. ~~**G2: Rust クライアントを gRPC に置き換え**~~ — ✅ 2026-05-08 完了

7. ~~**G3: WebSocket トランスポート廃止**~~ — ✅ 2026-05-08 完了
   - `connection.rs` WS コード全削除（507行→136行）
   - `Cargo.toml` から fastwebsockets/hyper 系依存を全削除
   - `server_grpc.py` の `_ENGINE_VERSION` モジュールレベル import 修正
   - `test_server_ws_compat.py` 削除 → `rg "per_message_deflate|rsv"` ゼロ件確認
   - `cargo clippy --workspace -- -D warnings` 警告ゼロ
   - `pytest` 1080 passed, 17 skipped（1 pre-existing 失敗は test_replay_snapshot.py の既存バグ）
   - `grpc_transport.rs` 新設（tonic ベース、~700 行）
   - `EngineConnection::connect_grpc()` 第2コンストラクタ追加（trait 化不要）
   - `process.rs` に `grpc://` プローブ URL ハンドリング追加
   - `server_grpc.py` が `engine-session.json` に `transport="grpc"` を書くよう更新
   - `tests/grpc_wire_integration.rs` 4 `#[ignore]` テスト追加
   - `cargo test --workspace` 全 PASS

### 解禁済み

- **Stage D (G0〜G3)**: 着手可能（fee_total 完了済み）
- **G3**: G2 完了済みのため着手可能

---

## レビュー反映 (2026-05-08, G1-G3 ラウンド 1)

実施日: 2026-05-08  
新規追加テスト数: 1（`start_or_attach.rs::probe_success_attaches_without_spawn` — `#[ignore]` スケルトン）

### 修正済み Finding 一覧

| Finding ID | 分類 | 内容（1 行サマリ） |
|------------|------|------------------|
| H1 | HIGH | `process.rs` — セッションファイル書き込みを `TransportKind::Grpc` で行うよう変更 |
| H2 | HIGH | `replay_session.py` — `_GrpcAttachClient` クラス追加、`_make_attach_client()` ファクトリで WS/gRPC を自動選択 |
| H3 | HIGH | `server_grpc.py` — `_EVENT_TO_FIELD_AND_CLASS` から `"Ready"` を削除し `_dict_to_proto_event()` に早期リターンガード追加 |
| H4 | HIGH | `server_grpc.py` — `StopAsyncIteration` に `log.debug` 追加 |
| H5 | HIGH | `error.rs` — `EngineClientError::WebSocket` を `GrpcTransport` に改名（全使用箇所更新） |
| H6 | HIGH | `session_file.rs` — `transport: String` を `TransportKind` enum に変更、テスト更新 |
| H7 | HIGH | `test_live_session_attach.py` — `pytestmark = pytest.mark.skip` 追加（WS transport 廃止） |
| H8 | HIGH | `test_live_session_kabu.py` — `pytestmark = pytest.mark.skip` 追加（WS transport 廃止） |
| M3 | MEDIUM | `grpc_transport.rs` — `OrderType/TIF/Status::Unspecified` を `log::warn + return None` で拒否 |
| M4 | MEDIUM | `grpc_transport.rs` + `dto.rs` — `AttemptedCommand::Unknown` variant 追加、`Unspecified` を `Unknown` にマップ |
| M5 | MEDIUM | `server_grpc.py` — `_proto_mode_to_str` が未知値で `ValueError` を raise、呼び出し元で `FAILED_PRECONDITION` に変換 |
| M6 | MEDIUM | `tests/common/mod.rs` — `tokio::spawn(server)` を `let _handle = tokio::spawn(server)` に変更 |
| M7 | MEDIUM | `server_grpc.py` — 接続数チェックを `handshake_lock` 内に移動し `len(_connections)` で確認 |
| M8 | MEDIUM | `tests/start_or_attach.rs` — `probe_success_attaches_without_spawn` スケルトンテスト追加（`#[ignore]`） |
| M9 | MEDIUM | `grpc_transport.rs` — Ready イベント送信失敗時に `log::debug` 追加 |
| M10 | MEDIUM | `test_server_grpc_multi_client.py` — `asyncio.sleep(0.2)` 削除、timeout 付きポーリングに変更 |
| M11 | MEDIUM | `connection.rs` — `connect_grpc_with_schema` は既存 `#[cfg(feature = "testing")]` ゲートで対応済み（追加変更不要） |
| M12 | MEDIUM | `session_file.rs` — `std::thread::sleep` → `tokio::time::sleep`、`pid_is_live` / `reap_stale` を `async fn` に変更 |
| M13 | MEDIUM | `test_grpc_smoke.py` — token フィールド検証 `assert data.get("token") == expected_token` 追加 |
| M1 | MEDIUM | `server_grpc.py` — `_cmd_payload_to_dict` に optional フィールド欠落の注記を追加 |
| M2 | MEDIUM | `.github/workflows/proto-lint.yml` 新設（buf lint + breaking change チェック） |

## レビュー反映 (2026-05-08, G1-G3 ラウンド 2)

実施日: 2026-05-08  
新規追加テスト数: 3（`test_engine_session_transport.py` — `_GrpcAttachClient.handshake()` 正常/schema mismatch/wrong token）

### 修正済み Finding 一覧

| Finding ID | 分類 | 内容（1 行サマリ） |
|------------|------|------------------|
| H-NEW-1 | HIGH | `replay_session.py` — `_GrpcAttachClient._recv_loop` の import と `_field_to_event` dict 構築をループ外に移動、`"ready"` フィールドを `log.debug + continue` でフィルタ |
| M-NEW-1 | MEDIUM | `test_engine_session_transport.py` + `mock_grpc_server.py` — `_GrpcAttachClient.handshake()` 正常/schema_mismatch/wrong_token の 3 テスト追加、MockGrpcServer に `expected_token` パラメータ追加、`_GrpcAttachClient` に `_schema_major_override` パラメータ追加 |
| M-NEW-2 | MEDIUM | `test_server_grpc_multi_client.py:104` — `asyncio.sleep(0.1)` を削除（handshake 完了は `wait_for` で保証済みのため sleep 不要） |
| M-NEW-3 | MEDIUM | `.github/workflows/proto-lint.yml` — `buf breaking` に `fetch-depth: 0` と初回実行ガード追加（main に `buf.yaml` 未存在時にスキップ） |

## レビュー反映 (2026-05-08, ラウンド 1)

実施日: 2026-05-08  
対象フェーズ: A2 / B3  
新規テストファイル: `tests/engine_event_routing_exhaustive.rs`（4 tests）/ `tests/live_replay_routing_boundary.rs`（3 tests）

### 修正済み Finding 一覧

| Finding ID | 分類 | 内容（1 行サマリ） |
|---|---|---|
| H-1 | HIGH | `map_engine_event_to_message` exhaustive テスト追加 + `EngineError` arm 明示（`strategy_id: None` で `log::error!`、`Some(_)` は silent None） |
| H-2 | HIGH | live/replay 境界テスト 3 件新設（`ReplayMsg::Finished` arm / `session_epoch` 管理 / `pending` 状態ハンドリング） |
| H-3 | HIGH | `pane.rs` 再構築パスに `apply_view_state()` 追加（`TicksizeSelected` / `BasisSelected` ハンドラの `HeatmapShader::new()` 直後） |
| H-4 | HIGH | Phase O1 繰越（Panning.translation canvas architecture 変更、ユーザー承認済み） |
| H-5 | HIGH | `DashboardMsg::Layout.layout_id` を `Option<LayoutId>` に修正 — **STOP+REPORT**: `DistributeFetchedData.layout_id` が `Uuid` 型であり `Option<LayoutId>` の構築時に `name` フィールドが不明。変更すると多数の呼び出し箇所が壊れる。型変更の代わりに `handlers/dashboard.rs` 消費側での `id.map(\|l\| l.unique)` 変換で対応するか、`DistributeFetchedData.layout_id` を `LayoutId` に変更する広範な変更が必要。ユーザー判断を仰ぐ |
| M-1 | MEDIUM | `src/chart.rs:926` — `partial_cmp().unwrap()` → `total_cmp()` に変更（NaN パニック防止） |
| M-2 | MEDIUM | `src/handlers/replay.rs:569` — `price.parse()` フォールバックに `log::warn!` 追加 |
| M-3 | MEDIUM | `src/widget/chart/heatmap.rs` — `rebuild_signal_debouncing_at_exact_boundary_is_full` テスト追加（境界値 `elapsed == REBUILD_DEBOUNCE_MS` が `Full` を返すことを確認） |
| M-4 | MEDIUM | `HeatmapShader::update()` に doc コメント追加（Effect を返さない設計の意図を明記） |
| M-5 | MEDIUM | `src/main.rs` — `EngineStopped` の `..` を `final_equity` / `ts_event_ms` 全フィールド明示に変更 |
| M-6 | MEDIUM | `src/main.rs` — `ReplayStopped` の `..` を `request_id` / `final_equity` 全フィールド明示に変更 |
| M-7 | MEDIUM | `heatmap.rs` — `RebuildSignal::set_immediate()` に「Only call via `rebuild_all_immediate()`」コメント追加（既に private `fn`） |
| M-8 | MEDIUM | `HeatmapViewState.camera_offset` に「GPU シェーダー内部形式」説明コメント追加 |
| M-9 | MEDIUM | `ReplayMsg::DataLoaded` に `instrument_id` / `instrument_ids` 排他性の doc コメント追加 |
| M-10 | MEDIUM | `Interaction::Ruler` に「`start: None` は未初期化ではなく有効・開始点未選択を意味する」doc コメント追加 |
| M-11 | MEDIUM | `EngineMsg::Noop` に「IPC コマンド送信成功時の sink variant / Task::none() 代替」doc コメント追加 |
| M-12 | MEDIUM | `RebuildSignal::Debouncing.since` に「過去時刻保証・saturating_duration_since による安全装置」コメント追加 |
| M-13 | MEDIUM | `heatmap-phase2-ownership.md` チェックリスト更新（`HeatmapViewState` 定義済み / `view_state()`/`apply_view_state()` 実装済み / Elm 統合済みに `[x]`） |

**繰越（Phase O1 / 別 PR）**:
- H-4: `Interaction::Panning { translation: Vector }` の canvas architecture 変更（ユーザー承認済み）
- H-5: `DashboardMsg::Layout.layout_id` の `Option<LayoutId>` 変更（`DistributeFetchedData.layout_id: Uuid` との型不整合のため、doc コメントで None の意味を明示して対応済み。`LayoutTarget` enum への置換は別 PR）

## レビュー反映 (2026-05-08, ラウンド 2)

実施日: 2026-05-08  
対象: R1 修正後のサニティチェック（silent-failure-hunter + iced-architecture-reviewer）  
新規修正: 3 件（R2-M1〜M3）

### 修正済み Finding 一覧

| Finding ID | 分類 | 内容（1 行サマリ） |
|---|---|---|
| R2-M1 | MEDIUM | `EngineError { strategy_id: Some(sid) }` arm に `log::warn!` 追加（バックテスト中の strategy 例外が無応答だった） |
| R2-M2 | MEDIUM | `engine_event_variant_count_is_as_expected` を `>= 50` → `assert_eq!(count, 52)` の完全一致に変更（新バリアント追加を検出できなかった） |
| R2-M3 | MEDIUM | `live_replay_routing_boundary.rs` の 3 テストを精密パターンに改善（`self.replay_running = false` / `self.last_replay_session_epoch = session_epoch` / `self.replay_stop_only_pending` + `pending_scenario_request_id` の実コード確認済みパターン） |

## レビュー反映 (2026-05-08, ラウンド 3)

実施日: 2026-05-08  
対象: R2 修正後の最終サニティチェック（silent-failure-hunter 単独）  
結果: **CRITICAL 0 / HIGH 0 / MEDIUM 0 / LOW 0 — 収束**

- R2 修正 3 件すべての正確性を確認（フィールドバインド、variant 数 52、精密パターン）
- 新たな silent failure なし
- `cargo test --workspace` 943 tests passed
