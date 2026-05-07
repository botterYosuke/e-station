# 実装計画: python-data-engine 改修 統合ロードマップ

作成日: 2026-05-07

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

## Stage A — 今すぐ着手可（並列）

**実施順序**: A1 と A2 は並列着手可。A3 は A2 完了後に実施（A1/A2 と並列不可）。

**A1: `python/engine/models.py` 追加 + 単体テスト**
- 文書: adapter-type-boundary Step 1
- 作業: `Instrument` / `OrderBook` / `Trade` pydantic v2 モデルを新規ファイルに定義
- テスト: `python/tests/test_models.py` で Decimal 精度・side バリデーション・immutability を確認
- 既存コードへの変更: **なし**

**A2: `Message` enum 分割 → `update()` 委譲化**
- 文書: gui-triple-state Phase 1
- 作業: 158 バリアント（2026-05-07 実測）のフラット enum を `Engine` / `Venue` / `Replay` / `Dashboard` / `Window` / `Menu` / `Settings` グループに分割（nested enum）。`main.rs::update()` を委譲ハブに縮小。着手前に `rg "Message::" src/ --count` で最新バリアント数を再確認すること。
- 完了条件: `update()` 400 行以内、`cargo clippy` 警告なし

**A3: `Ladder.insert_depth()` Elm 管理移行**
- 文書: gui-triple-state Phase 3
- 作業: `insert_depth()` が `scroll_px` を直接書き換えないよう `Option<f32>` を返す形に変更。呼び出し側が `Message::LadderScroll(delta)` を発行。
- A2 と Rust 内で並列可

> ⚠ **A2/A3 の実行順序**: 計画上は並列可としているが、depth routing の実装を調査した結果、**A3 は A2 完了後に実施することを推奨**する。現行の depth fanout は `main.rs` → `dashboard.rs::ingest_depth()` → `ladder.rs::insert_depth()` という routing に乗っており、A3 で `Message::LadderScroll { pane_id, delta }` を新設すると `Message` enum への追加と routing 再設計が同時に発生する。A2 で Message enum の構造が固まった後に A3 を実施した方が、競合と二重修正を避けられる。`cargo test` の失敗が A2 由来か A3 由来か切り分けやすくもなる。

**Stage A 完了条件**: `cargo test --workspace` + `pytest` 全 PASS + `cargo clippy -- -D warnings` 警告なし + `map_engine_event_to_message()` 網羅テスト PASS

---

## Stage B — Stage A 完了後（並列）

**B1: `KabuStationAdapter` をモデルに対応**
- 文書: adapter-type-boundary Step 2
- 作業: コンストラクタに 50 銘柄チェック追加。`parse_board()` / `parse_execution()` の戻り値を `OrderBook` / `Trade` に変更。
- テスト: `python/tests/test_kabusapi_adapter.py` でラウンドトリップ（生 JSON → モデル）検証

> ⚠ **二重境界リスク**: `kabusapi_adapter.py` を先に作っても、`KabuStationWorker` が `_Broadcaster.append()` に wire-format dict を直接書く構造は変わらない。B1 完了後に C1（server.py 差し替え）を経由しない限り、`models.py` の型変換層と既存の wire-format 直書き層が並存する二重境界になる。「kabu だけ先行」で完結するように見えるが、他 venue worker は B1 後も旧境界のまま残る。B1 着手前に「全 venue worker の同時移行」か「kabu 先行＋他 venue を C1 フェーズで一括」かの方針を決めること。

**B2: `MockIPCServer` 骨格 + Hello/Ready (S1)**
- 文書: plan-test-mock-ipc S1
- 作業: `python/tests/fixtures/mock_ipc_server.py` を実装（`asyncio` + `websockets.serve`、ランダムポート）。`Hello` → `Ready` 最小ハンドシェイクのみ。
- テスト: `python/tests/test_mock_ipc_server_basic.py` が 1 秒以内 PASS（実行時 < 1s の assert を含む: `@pytest.mark.timeout(1)` デコレータ、または `time.monotonic()` 計測で 1.0 未満を明示的に assert する）

**B3: `HeatmapShader` 状態整理**
- 文書: gui-triple-state Phase 2

> ⚠ **着手前提**: `docs/✅python-data-engine/🔵heatmap-phase2-ownership.md`（仮称）が作成され、`follow/pause/live 遷移`・`CanvasInvalidation`・`RebuildPolicy`・`camera` の4グループそれぞれの移行先 owner が確定していることが B3 着手の必須条件。この設計ドキュメントなしに B3 に着手してはならない。A2 完了後すぐに着手するのではなく、Phase 2 owner 設計を先行させること。

- 作業:
  - `ViewState` に `camera_offset: Vector` / `camera_scale: f32` を追加し GPU カメラと統合
  - `CanvasInvalidation` / `RebuildPolicy` フィールドを削除
  - `HeatmapShader::update()` が `ViewState` を更新する Elm メッセージ経由に変更
- 依存: A2 完了後
- リスク: **中**（最も影響範囲が広い）
- 完了条件: `HeatmapShader` フィールド数 75 以下、ズーム・パン操作で `ViewState.scaling` と GPU カメラが常に同値

**Stage B 完了条件**: `HeatmapShader` フィールド 75 以下 + `MockIPCServer` 基本動作 1 秒以内 PASS

---

## Stage C — Stage B 完了後（順次）

**C1: `server.py` 配信パスを adapter 経由に差し替え**
- 文書: adapter-type-boundary Step 3
- 作業: `server.py` が adapter の変換結果（`OrderBook`・`Trade`・`DepthDiff` 等の pydantic モデル）を受け取り、`python/engine/mappers.py` の mapper 関数経由で schemas.py 定義の wire DTO 型に変換し、outbox に送出する（`.model_dump(mode="json")` での直結は禁止）
- IPC スキーマ（`SCHEMA_MAJOR` / `SCHEMA_MINOR`）は変更しない
- 依存: B1 完了後

**C2: IPC プロトコル網羅 (S2) + smoke 整理 (S3)**
- 文書: plan-test-mock-ipc S2 / S3
- 作業:
  - S2: `script` 形式で `Subscribe` / `FetchKlines` / `Unsubscribe` / `Shutdown`・スキーマ不一致（`EngineError` + `SCHEMA_MAJOR_MISMATCH`）応答を定義（`HelloReject` という wire は存在しない）
  - S3: 既存 smoke テストに `@pytest.mark.smoke` を付与。`conftest.py` に `--smoke` 未指定時スキップを実装
- 完了条件: `pytest`（引数なし）が実プロセス起動ゼロで 60 秒以内に完走

**C3: Canvas-local 状態の境界明文化**
- 文書: gui-triple-state Phase 4
- 作業: `canvas::Program::State` の型エイリアスに境界コメントを追加。許容（Interaction サブ状態・ホバー座標）と禁止（スケール・データ配列・再描画ポリシー）をドキュメント化。
- 依存: B3 + A3 完了後

**Stage C 完了条件**:
- `pytest`（引数なし）実プロセス起動ゼロ・60 秒以内
- `pytest --smoke -x` で既存 smoke テスト PASS
- IPC ハンドシェイク・`ConnectionRefusedError` フォールバック・スキーマ不一致の各経路が MockIPCServer でカバー

---

## Stage D — `fee_total` 実装完了後に解禁

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

## 全体タイムライン

```
今
 │
 ├─[A1 Python]──────────────────────────────────────────────────────►
 ├─[A2 Rust: Message分割]────────────────────────────────────────────►
 └─[A3 Rust: Ladder scroll]──────────────────────────────────────────►
                         │
                         ├─[B1 KabuStationAdapter]──────────────────►
                         ├─[B2 MockIPCServer S1]─────────────────────►
                         └─[B3 HeatmapShader ★]──────────────────────►
                                              │
                                              ├─[C1 server.py差し替え]►
                                              ├─[C2 S2+S3 smoke整理]──►
                                              └─[C3 Canvas境界]────────►
                                                              │
                                              fee_total 完了 ─┤
                                                              │
                                                              └─[G0→G0.5→G0.9→G1→G2→G3]►
```

---

## 計測指標（全体）

| 指標 | 現在 | 目標 |
|------|------|------|
| `main.rs` 総行数 | 7,447 行 | 4,000 行以下 |
| `update()` 行数 | 3,579 行 | 400 行以下 |
| `Message` バリアント数（フラット） | 158（2026-05-07 実測） | グループ 7 以下（nested enum 再設計） |
| `HeatmapShader` フィールド数 | 150+ | 75 以下 |
| `pytest`（引数なし）実行時間 | 30〜120 秒 | 60 秒以内（実プロセス起動ゼロ） |
| IPC スキーマ管理 | `SCHEMA_MAJOR/MINOR` 手書き | protobuf フィールド番号のみ |
| RSV ビット圧縮バグ | 再発リスクあり | gRPC 移行で原理消滅 |

---

## 開始推奨

**A1（`models.py` 追加）+ A2（Message 分割）の同時着手**が最もリターンが早い。  
A1 は既存コードへの変更ゼロ、A2 は `cargo test` が通れば安全確認できる。
