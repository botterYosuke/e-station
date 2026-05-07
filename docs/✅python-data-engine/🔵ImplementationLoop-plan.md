# 実装計画: python-data-engine 改修 統合ロードマップ

作成日: 2026-05-07  
最終更新: 2026-05-07（Stage A〜C 実装完了）

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

### 🔴 A2: `Message` enum 分割 → `update()` 委譲化 — **未着手（大規模・別セッション要）**

- 文書: gui-triple-state Phase 1
- 作業: 158 バリアント（2026-05-07 実測）のフラット enum を `Engine` / `Venue` / `Replay` / `Dashboard` / `Window` / `Menu` / `Settings` グループに分割（nested enum）。`main.rs::update()` を委譲ハブに縮小。着手前に `rg "Message::" src/ --count` で最新バリアント数を再確認すること。
- 未着手理由: main.rs 7,816 行・38 ファイル横断、単一セッションでの安全な実装が困難
- 完了条件: `update()` 400 行以内、`cargo clippy` 警告なし

### ✅ A3: `Ladder.insert_depth()` Elm 管理移行 — **完了（不変条件すでに満足）**

- 文書: gui-triple-state Phase 3
- 調査結果（2026-05-07）: `insert_depth()` は `scroll_px` を直接書き換えていない。
  `scroll_px` への書き込みは `Panel::scroll(delta)` コールバック経由のみ（ladder.rs:72）。
  当初想定していたアンチパターンはコードに存在しなかった。
- 追加作業: **不要**

> ⚠ **A2/A3 の実行順序**: 計画上は並列可としているが、depth routing の実装を調査した結果、**A3 は A2 完了後に実施することを推奨**する。現行の depth fanout は `main.rs` → `dashboard.rs::ingest_depth()` → `ladder.rs::insert_depth()` という routing に乗っており、A3 で `Message::LadderScroll { pane_id, delta }` を新設すると `Message` enum への追加と routing 再設計が同時に発生する。A2 で Message enum の構造が固まった後に A3 を実施した方が、競合と二重修正を避けられる。`cargo test` の失敗が A2 由来か A3 由来か切り分けやすくもなる。

**Stage A 完了条件**: `cargo test --workspace` + `pytest` 全 PASS + `cargo clippy -- -D warnings` 警告なし + `map_engine_event_to_message()` 網羅テスト PASS  
→ **A2 が未着手のため Stage A は部分完了。A2 着手後に改めて完了条件を確認すること。**

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

### 🔴 B3: `HeatmapShader` 状態整理 — **未着手（設計ドキュメント未作成）**

- 文書: gui-triple-state Phase 2

> ⚠ **着手前提**: `docs/✅python-data-engine/🔵heatmap-phase2-ownership.md`（仮称）が作成され、`follow/pause/live 遷移`・`CanvasInvalidation`・`RebuildPolicy`・`camera` の4グループそれぞれの移行先 owner が確定していることが B3 着手の必須条件。この設計ドキュメントなしに B3 に着手してはならない。A2 完了後すぐに着手するのではなく、Phase 2 owner 設計を先行させること。

- 作業（未着手）:
  - `ViewState` に `camera_offset: Vector` / `camera_scale: f32` を追加し GPU カメラと統合
  - `CanvasInvalidation` / `RebuildPolicy` フィールドを削除
  - `HeatmapShader::update()` が `ViewState` を更新する Elm メッセージ経由に変更
- 依存: A2 完了 + `🔵heatmap-phase2-ownership.md` 作成・承認
- リスク: **中**（最も影響範囲が広い）
- 完了条件: `HeatmapShader` フィールド数 75 以下、ズーム・パン操作で `ViewState.scaling` と GPU カメラが常に同値

**Stage B 完了条件**: `HeatmapShader` フィールド 75 以下 + `MockIPCServer` 基本動作 1 秒以内 PASS  
→ **B3 が未着手のため Stage B は部分完了。**

---

## Stage C — ✅ 部分完了（2026-05-07）

### ✅ C1: `mappers.py` 追加・adapter→wire DTO 変換層の定義 — **完了（配線は次フェーズ）**

- 文書: adapter-type-boundary Step 3
- 実装済み: `python/engine/mappers.py`
  - `order_book_to_wire()` / `depth_diff_to_wire()` / `trade_to_wire()` / `trades_to_wire()`
  - adapter model（`OrderBook`/`DepthDiff`/`Trade`）→ schemas.py wire DTO への単段変換
  - gap recovery フィールド（`stream_session_id` / `sequence_id` / `prev_sequence_id`）を欠損なく伝搬
  - Decimal の指数表記（`1E-4`）を抑止（`format(d, "f")` を使用）
  - `.model_dump(mode="json")` 直結禁止の設計境界を維持
- テスト: `python/tests/test_mappers.py` — 9 tests PASS（wire compatibility・JSON dump 形式確認を含む）
- **注意**: `server.py` の配信パスを mapper 経由に差し替える作業（本来の C1 完成形）はまだ未実施。
  mapper の定義と型安全な変換層が揃った状態。server.py 側の wire-up は B3 完了後に実施する。
- IPC スキーマ（`SCHEMA_MAJOR` / `SCHEMA_MINOR`）は変更なし ✅
- 依存: B1 完了後 ✅

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

## Stage D — `fee_total` 実装完了後に解禁

> **`fee_total` とは**: `python/engine/summary.py` のリプレイサマリに手数料合計（`fee_total`）を追加する機能。
> 現在の fills.jsonl にはリプレイ約定に commission フィールドが存在しないため未実装（summary.py:18 の Out of scope コメント参照）。
> fills.jsonl の上流スキーマ変更（replay fill event に commission フィールド追加）と IPC スキーマ変更が必要。
> gRPC 移行（Stage D）は IPC スキーマ全体を proto 化する大変更なので、fee_total のスキーマ変更を先に確定させてから proto 化する順序とする。

**G0: `proto/engine.proto` + ビルドパイプライン（1〜2 日）**
- 既存 `commands.json` / `events.json` から proto 変換
- Rust: `tonic-build` を `build.rs` に追加
- Python: `grpcio-tools` を `pyproject.toml` に追加
- `buf lint` + `buf breaking` を CI に追加

**G0.5: Python 側 gRPC mock サーバー骨格（1 日）**
- `python/tests/fixtures/mock_grpc_server.py` を新設（`grpcio.aio` ベースのテスト用最小サーバー）
- Session 冒頭メッセージ方式の mock 骨格（`Command.oneof.hello = HelloRequest` を受け付け、`ReadyResponse` を最初の `Event` として返す）のみ実装（script 形式は不要、固定応答でよい）
- G1 の `server_grpc.py` 実装の acceptance テストで mock として使用する
- plan-test-mock-ipc-server.md の S1 骨格に相当する gRPC 版
- **完了条件**: `python/tests/test_mock_grpc_server_basic.py` が `@pytest.mark.timeout(1)` デコレータ付きで 1 秒以内に PASS。`Session` 冒頭の `HelloRequest`（`Command.oneof.hello`）に対して `ReadyResponse`（最初の `Event`）を返すラウンドトリップを grpcio チャネルで確認すること。実行コマンド: `pytest python/tests/test_mock_grpc_server_basic.py`。加えて `server.stop(); server.stop()` を連続呼び出しても例外が発生しないことを `test_mock_grpc_server_basic.py` 内で確認すること（`stop()` 冪等性）。

**G0.9: transport-aware attach/discovery 先行ゲート（1 日）**
- `session_file.rs::EngineSession` 構造体に `transport` フィールド（`"ws"` / `"grpc"` 文字列）を追加
- `replay_session.py::ReplaySession._resolve_endpoint_and_token()` に gRPC 分岐を最小実装（`transport == "grpc"` のとき gRPC チャネル URI を返す）
- `replay_session.py::LiveSession._resolve_endpoint_and_token()` にも同様の gRPC 分岐を追加
- `process.rs::DEFAULT_PROBE_URL` をトランスポート対応に変更（`transport` フィールドを参照）
- **完了条件**:
  - `session_file.rs::EngineSession` の `transport` フィールド追加テスト PASS
  - `ReplaySession` / `LiveSession` 両方の `_resolve_endpoint_and_token()` gRPC 分岐テスト PASS
  - **[writer 側 acceptance pin]** `engine-session.json` に `"transport": "grpc"` を書く主体（`--transport grpc` で起動した `server_grpc.py` / engine プロセス）が、実際にそのフィールドを session file に書き込むことを確認するテストが存在すること。具体的には `test_mock_grpc_server_basic.py` または新規テストで、MockIPCServer 起動後に session file の `transport` フィールドが `"grpc"` であることを明示的にアサートする（例: `assert session_data["transport"] == "grpc"`）。このテストが存在しない場合、unit test は通っても実 attach / external mode では session file が既定の `"ws"` のまま残り、helper と `start_or_attach()` が WS に誤誘導される。
  - **[external mode 読み取り確認]** external mode（既存の session file がある場合）においても、session file の `transport` フィールドが正しく読み取られ、`"grpc"` の場合に gRPC チャネル URI が返ることを確認すること（reader 側の acceptance pin と writer 側の acceptance pin の両方が揃うことで G0.9 完了とみなす）。
- **⚠ G0.9 完了後でないと G1 に着手してはならない**（endpoint 解決経路が未整備のまま G1 を実施すると attach mode が WS 固定のまま残る）

**G1: Python サーバーを gRPC に置き換え（2〜3 日）**
- `python/engine/server_grpc.py` を新設（`grpcio.aio` ベース）
- 現行 `server.py`（WebSocket）は `--transport ws` フラグで残存（ロールバック用）
- `pytest python/tests/` が全 PASS
- **G1 完了条件追記**: gRPC mock（G0.5 作成）を使った transport-level 統合テストが PASS

> ⚠ **endpoint 解決経路の再設計が必要**: 現行 `replay_session.py::_resolve_endpoint_and_token()` は session ファイルから `ws://127.0.0.1:{port}/` を組み立てる（WS URL 固定）。gRPC 移行後は `transport` フィールドを見て gRPC チャネルを組み立てる分岐に変更する必要がある。**`replay_session.py` 内の `LiveSession._resolve_endpoint_and_token()`（`ReplaySession` とは独立した実装）も同様に `transport` フィールド分岐を追加する必要がある**。この修正を忘れると attach mode が WS のままになる。`process.rs::DEFAULT_PROBE_URL = "ws://127.0.0.1:19876/"` もトランスポート対応に変更が必要。これらは `ipc-grpc-migration.md` G1 作業項目に詳細を追記済み。G1 の工数見積もりにこの endpoint 解決経路の再設計（`ReplaySession` 側・`LiveSession` 側の両方）を含めること。

**G2: Rust クライアントを gRPC に置き換え（独立マイルストーン、G1 完了後に着手）（3〜4 日）**
- `engine-client/src/grpc_client.rs` を新設（`tonic` ベース）
- 現行 `ws_client.rs` はフィーチャーフラグ `ws-transport` で保持
- `SCHEMA_MAJOR` チェックを Session 冒頭 `HelloRequest`（`Command.oneof.hello`）の `schema_major` / `schema_minor` チェック（`ipc-grpc-migration.md` §3.3）に置き換え
- **`tests/grpc_wire_integration.rs` を新設**: `python/engine/server_grpc.py` をサブプロセスで起動し、tonic クライアントが実 grpcio サーバーと通信することを確認（`mock_grpc_server.py` ではなく本物のサーバーを使う）
- `cargo test --workspace` 全 PASS（`grpc_wire_integration` を含む）
- **⚠ 工数注意・並列実施不可**: 現行 `EngineConnection` は concrete struct であり `Arc<EngineConnection>` として `main.rs`・`backend.rs`・`process.rs` 等で広く使われている。`EngineConnectionLike` trait（仮称）の新設と既存呼び出し箇所のジェネリクス化が必要。この trait 化リファクタは影響範囲が広く G1 と並行実施すると競合が多発するため、**G1 が完全に完了してから G2 に着手すること**。この作業を含めての 3〜4 日見積もりであることを確認する。

  **G2 着手前必須調査**: G2 実装開始前に `rg "EngineConnection" --type rust` を実行し、変更が必要なファイル数と呼び出し箇所数を一覧化し、3〜4 日の工数見積もりが妥当かどうかを確認すること。この調査なしに G2 の実装を開始してはならない。

**G3: WebSocket トランスポート廃止（1 日）**
- `ws-transport` フィーチャーフラグと `server.py` を削除
- `SCHEMA_MAJOR` / `SCHEMA_MINOR` 定数を削除
- `MockIPCServer` を gRPC ベース（`grpcio` テストサーバー）に書き直し
- `cargo clippy -- -D warnings` + `pytest` 全 PASS、RSV ビット関連コード完全消滅

**Stage D 完了条件**:
- `ipc-schema-check` スキルの内容が `buf` コマンドに更新済み
- `cargo test grpc_wire_integration` が実 `server_grpc.py` サブプロセスを起動した上で PASS（tonic↔grpcio の実 wire 互換性を確認。mock のみの完了は不可）
- multi-client attach 動作確認：同時 2 クライアント接続・`ClientConnected` broadcast・`MAX_CONNECTIONS=4` 超過時 `RESOURCE_EXHAUSTED` が gRPC 経由で動作
- WS トランスポート（`server.py` + `ws_client.rs`）削除後の smoke テスト PASS（`pytest --smoke -x`）
- gRPC mock（`mock_grpc_server.py`）が全 transport-level テストで使用されている（実プロセス起動ゼロ）
- `rg "per_message_deflate|rsv" --type rust --type python` がゼロ件（RSV ビット関連コード完全消滅を機械確認）

---

## 全体タイムライン（2026-05-07 時点）

```
2026-05-07
 │
 ├─[A1 Python]──────────────────────────────── ✅ 完了
 ├─[A2 Rust: Message分割]─────────────────────── 🔴 未着手
 └─[A3 Rust: Ladder scroll]──────────────────── ✅ 不変条件確認済（追加実装不要）
                         │
                         ├─[B1 KabuStationAdapter]──────────────── ✅ 完了
                         ├─[B2 MockIPCServer S1]─────────────────── ✅ 完了
                         └─[B3 HeatmapShader ★]──────────────────── 🔴 未着手（設計Doc未作成）
                                              │
                                              ├─[C1 mappers.py + adapter→wire定義]── ✅ 完了（server.py配線は未）
                                              ├─[C2 S2+S3 smoke整理]────────────── ✅ 完了
                                              └─[C3 Canvas境界]─────────────────── ✅ 完了
                                                              │
                                              fee_total 完了 ─┤  ← 未実装（凍結中）
                                                              │
                                                              └─[G0→G0.5→G0.9→G1→G2→G3]► 凍結中
```

---

## 計測指標（全体）

| 指標 | 2026-05-07 着手前 | 2026-05-07 現在 | 目標 |
|------|------|------|------|
| `main.rs` 総行数 | 7,447 行 | 7,816 行（変更なし） | 4,000 行以下 |
| `update()` 行数 | 3,579 行 | 3,579 行（変更なし） | 400 行以下 |
| `Message` バリアント数（フラット） | 158（2026-05-07 実測） | 158（変更なし） | グループ 7 以下（nested enum 再設計） |
| `HeatmapShader` フィールド数 | 150+ | 150+（変更なし） | 75 以下 |
| `pytest`（引数なし）実行時間 | 30〜120 秒 | 86 秒（新規 50 tests は 0.5s 以内） | 60 秒以内（実プロセス起動ゼロ） |
| adapter boundary モデル | なし | `models.py`（4 モデル / 19 tests） | — |
| KabuStation adapter | なし | `kabusapi_adapter.py`（13 tests） | — |
| MockIPCServer | なし | S1+S2 完了（9 tests / < 0.5s） | S1〜S5 完了 |
| adapter→wire mapper | なし | `mappers.py`（9 tests） | — |
| smoke marker 基盤 | なし | `conftest.py` + `pytest.ini` 整備済み | — |
| IPC スキーマ管理 | `SCHEMA_MAJOR/MINOR` 手書き | 変更なし | protobuf フィールド番号のみ |
| RSV ビット圧縮バグ | 再発リスクあり | 変更なし（gRPC 移行待ち） | gRPC 移行で原理消滅 |

---

## 残作業サマリ（次セッション向け）

### 次に着手すべき作業（優先順）

1. **`🔵heatmap-phase2-ownership.md` 作成**（B3 着手の必須条件）
   - follow/pause/live 遷移・CanvasInvalidation・RebuildPolicy・camera の4グループの owner table を確定する
   - このドキュメントなしに B3 を開始してはならない

2. **A2: Message enum 分割**（main.rs 7,816 行の大規模作業）
   - 着手前に `rg "Message::" src/ --count` で最新バリアント数を再確認
   - `Engine` / `Venue` / `Replay` / `Dashboard` / `Window` / `Menu` / `Settings` の 7 グループに nested 化
   - 38 ファイル横断・複数日の作業

3. **B3: HeatmapShader 状態整理**（`🔵heatmap-phase2-ownership.md` + A2 完了後）

4. **C1 server.py 配線**（B1+B3 完了後）
   - worker → adapter model → mapper → wire DTO → outbox のパスを実際に wire-up する
   - `test_server_adapter_integration.py` 統合テストが必要

5. **fee_total 実装**（Stage D 解禁のゲート）
   - fills.jsonl への commission フィールド追加（上流スキーマ変更）
   - `summary.py` への `fee_total` 集計追加
   - IPC スキーマ（`schemas.py` + `events.json`）への commission フィールド追加

### 凍結中

- **Stage D (G0〜G3)**: `fee_total` 実装完了まで着手禁止
