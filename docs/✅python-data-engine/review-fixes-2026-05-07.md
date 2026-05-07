# review-fixes 2026-05-07

対象: python-data-engine 改修計画群（5 ファイル）
- 🔵ImplementationLoop-plan.md
- 🔵adapter-type-boundary.md
- 🔵gui-triple-state-refactor.md
- 🔵ipc-grpc-migration.md
- 🔵plan-test-mock-ipc-server.md

---

## ラウンド 1（2026-05-07）

### 統一決定
1. update() 行数目標: 400行以内で統一（gui-triple-state 目的の「300行」→「400行」修正）
2. Stage A 完了条件: `pytest -k "not smoke"` → `pytest`（smoke タグは Stage C 以降）
3. import パス: exchanges/ サブパッケージからは `from ..models import` が正
4. engine_runner.py スコープ: C1 は server.py のみ。adapter-type-boundary Step 3 タイトルから削除
5. `_stop`: `asyncio.Event` → `threading.Event` に変更
6. engine_proto_revision: git hash ではなく monotonic uint32（1, 2, 3...手動）と明記
7. `.model_dump(mode="json")`: Step 3 に明示
8. `side` 型: `Literal["buy", "sell", "unknown"]` に変更（field_validator 削除）

### Findings 一覧

| Finding ID | 観点 | 対象ファイル | 行付近 | 修正概要 |
|---|---|---|---|---|
| A-1 | A | gui-triple-state-refactor.md | L74 | update() 目的「300行」→「400行」に統一 |
| A-2 | A | adapter-type-boundary.md | L128 | Step 3 タイトルから engine_runner.py 削除 |
| A-3 | A | ImplementationLoop-plan.md | L50 | Stage A 完了条件の `pytest -k "not smoke"` → `pytest` |
| A-4 | A | gui-triple-state-refactor.md | L217-226 | フェーズ依存図を並列表記に修正 |
| A-5 | A | plan-test-mock-ipc-server.md | 末尾 | G3 gRPC 移行での書き直し予告注記を追加 |
| A-6 | A | ipc-grpc-migration.md | L197 | メモリ相対パスリンクをインライン要約に変更 |
| A-7 | A | ImplementationLoop-plan.md | L64 | B2 完了条件に `< 1s assert` 言及追記 |
| B-1 | B | adapter-type-boundary.md | L124 | kabusapi_adapter.py は新規作成である旨を明記 |
| B-2 | B | adapter-type-boundary.md | L82 | `from .models` → `from ..models` に修正 |
| B-3 | B | ipc-grpc-migration.md | L161 | EngineConnection は struct。trait 化の影響範囲を明記 |
| B-4 | B | gui-triple-state-refactor.md | L238 | フィールド数「150+」に計測方法注記を追加 |
| B-5 | B | gui-triple-state-refactor.md | L14-16 | 行番号参照をシンボル名参照に変更 |
| C-1 | C | plan-test-mock-ipc-server.md | L40 | `_stop = asyncio.Event()` → `threading.Event()` |
| C-2 | C | ipc-grpc-migration.md | L130 | engine_proto_revision を monotonic uint32 に変更 |
| C-3 | C | adapter-type-boundary.md | L130 | `.model_dump()` → `.model_dump(mode="json")` 明示 |
| C-4 | C | adapter-type-boundary.md | L62 | timestamp timezone 確認注意事項追記 |
| C-5 | C | ipc-grpc-migration.md | L98-115 | Handshake 失敗後 Session 制御パス追記 |
| C-6 | C | gui-triple-state-refactor.md | L129 | ViewState 拡張 vs HeatmapViewState 分離の設計判断注記 |
| C-7 | C | adapter-type-boundary.md | L49 | `side: str` → `Literal["buy","sell","unknown"]`、field_validator 削除 |
| D-1 | D | adapter-type-boundary.md | L142 | immutability assert 種別（ValidationError）を明記 |
| D-2 | D | adapter-type-boundary.md | L141-147 | ネガティブテスト行（不正JSON・欠損フィールド）を追加 |
| D-3 | D | adapter-type-boundary.md | L145 | 統合テストのファイル名・実行コマンドを明記 |
| D-4 | D | gui-triple-state-refactor.md | L116-119 | フェーズ1完了条件に対象テストファイルを追記 |
| D-5 | D | gui-triple-state-refactor.md | L160-163 | ViewState ≡ GPU カメラ同値の Rust 単体テスト追加を明記 |
| D-6 | D | gui-triple-state-refactor.md | L187-189 | scroll_px 変更箇所の grep 検証コマンドを追記 |
| D-7 | D | gui-triple-state-refactor.md | L208-210 | フェーズ4 CI 確認方法追記 |
| D-8 | D | ipc-grpc-migration.md | L143-145 | G0 完了条件に buf lint コマンドと失敗基準追記 |
| D-9 | D | ipc-grpc-migration.md | L153-155 | G1 smoke テストの gRPC 対応方針を明記 |
| D-10 | D | ipc-grpc-migration.md | L163-165 | G2 フィーチャーフラグ別テストコマンドを追記 |
| D-11 | D | ipc-grpc-migration.md | L171-174 | G3 RSV 消滅検証コマンドを追記 |
| D-12 | D | plan-test-mock-ipc-server.md | L102 | S1 `< 1s assert` の実装方法（pytest.mark.timeout）を明記 |
| D-13 | D | plan-test-mock-ipc-server.md | L104-110 | S2 に pytest-timeout 設定を作業項目として追加 |
| D-14 | D | plan-test-mock-ipc-server.md | L112-118 | S3 に CI ワークフロー具体的ファイル名・ジョブ名追記 |

HIGH: 14件 / MEDIUM: 14件 / LOW: 5件

---

## ラウンド 2（2026-05-07）

### 統一決定
- G1 完了条件: `-k "not smoke"` を外し `pytest python/tests/` (引数なし) に統一
- camera_scale(): Phase 2 実装時に新設。完了条件に「現行未存在・Phase 2 で追加」を明記
- proto スニペット: HelloRequest / ReadyResponse の message 定義を補完

### Findings 一覧

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| R2-AB-2 | A | ipc-grpc-migration.md | G1 完了条件から `-k "not smoke"` を除去 |
| R2-CD-1 | C | gui-triple-state-refactor.md | camera_scale() 未存在の注記をフェーズ2完了条件に追加 |
| R2-CD-2 | C | ipc-grpc-migration.md | HandshakeRequest proto スニペットに engine_proto_revision フィールドを追記 |
| R2-AB-3 | A | ImplementationLoop-plan.md | C1 の `.model_dump()` → `.model_dump(mode="json")` に修正 |
| R2-CD-3 | C | adapter-type-boundary.md | サンプルコードに ConfigDict(frozen=True) を追加 |
| R2-CD-4 | C | plan-test-mock-ipc-server.md | asyncio.get_event_loop() → asyncio.get_running_loop() |

MEDIUM: 3件 / LOW: 3件

---

## ラウンド 3 — ユーザーレビュー（2026-05-07）

spec.md / 実装を source of truth として実施。AI レビュアーが見落とした深層 Finding。

### 統一決定
1. proto スニペット: HelloRequest/ReadyResponse を現行 Hello/Ready フィールド（schema_major, schema_minor, client_version, token, mode / engine_session_id, capabilities）に移植
2. engine_proto_revision（monotonic int）: 削除。schema_major/schema_minor に置換
3. バージョニング: schema_major exact match=FAILED_PRECONDITION、schema_minor=warning のみ継続（spec §4.5.1 忠実移植）
4. multi-client: 1 クライアント=1 Session RPC、サーバー内部 broadcaster、MAX_CONNECTIONS=4 を Session accept 前に検査
5. gRPC mock: G0 後・G1 前に G0.5 として mock 骨格を作成
6. depth fields: OrderBook に stream_session_id/sequence_id 追加、DepthDiff モデル新設
7. HelloReject: wire として存在しない → Error/EngineError に修正
8. scroll_px 検証: insert_depth() 内のゼロ件 + 状態遷移の明文化（件数基準を削除）
9. map_engine_event_to_message: exhaustive テストを Phase 1 acceptance に追加
10. Stage D 完了条件: wire テスト + multi-client + WS 削除後 smoke を追加

### Findings 一覧

| Finding ID | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|
| User-1 | HIGH | ipc-grpc-migration.md | proto スニペットに全フィールドを追加（engine_proto_revision 削除） |
| User-2 | HIGH | ipc-grpc-migration.md | versioning 方針を schema_major/minor 二層に修正（exact match 廃止） |
| User-3 | HIGH | ipc-grpc-migration.md | multi-client 契約（broadcaster/MAX_CONNECTIONS/ClientConnected）の gRPC 実装方針を追記 |
| User-4 | HIGH | ImplementationLoop-plan.md | G0.5 gRPC mock 骨格を G1 前に追加 |
| User-5 | HIGH | adapter-type-boundary.md | DepthDiff モデル新設、OrderBook に gap recovery フィールド追加 |
| User-6 | MEDIUM | plan-test-mock-ipc-server.md | HelloReject → Error/EngineError + ClientConnected に修正 |
| User-7 | MEDIUM | gui-triple-state-refactor.md | Phase 1 acceptance に map_engine_event_to_message exhaustive テストを追加 |
| User-8 | MEDIUM | gui-triple-state-refactor.md | scroll_px grep 基準を「insert_depth 内ゼロ件」+「状態遷移明文化」に修正 |
| User-9 | MEDIUM | ImplementationLoop-plan.md | G2 に EngineConnectionLike 工数（Arc<EngineConnection> 広範囲リファクタ）を明記 |
| User-10 | MEDIUM | ImplementationLoop-plan.md | Stage D 完了条件に wire テスト・multi-client・smoke を追加 |

HIGH: 5件 / MEDIUM: 5件

---

## ラウンド 4 — ユーザーレビュー（2026-05-07）

### 統一決定
1. G2 完了条件: `cargo test grpc_wire_integration` が実 `server_grpc.py` サブプロセスを起動した上で PASS すること（mock のみ完了不可）
2. `tests/grpc_wire_integration.rs` を G2 の必須成果物として追加
3. Stage D 完了条件の「Rust-Python 実 wire 統合テスト PASS」を `grpc_wire_integration` テスト名で具体化

### Findings 一覧

| Finding ID | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|
| User-11 | CRITICAL | ipc-grpc-migration.md / ImplementationLoop-plan.md | G2 に `tests/grpc_wire_integration.rs`（実 server_grpc.py サブプロセス起動）を追加。tonic↔grpcio 実 wire 検証なしの「完了」を防ぐ |

CRITICAL: 1件

---

## ラウンド 5 — ユーザーレビュー（2026-05-07）

### 統一決定
1. G2 に実 wire テスト（`grpc_wire_integration.rs`）追加（R4 からの継続）
2. rollback 非対称性（Python ランタイム切替可・Rust 再ビルド必要）を G1〜G2 ロールバック手順表として明記
3. WS/gRPC 併存期間は `check_schema_parity.py` で JSON↔proto drift を CI 検知（G3 廃止）
4. attach/discovery（`engine-session.json`/`ReplaySession`）の gRPC 移行パスを G1 作業項目に追加
5. gRPC smoke を G1 時点から nightly に追加（G3 削除まで WS smoke と並走）
6. MockIPCServer 計画の前提：既存テスト4ファイルを現状表に追加・計画を「移植・高速化」に修正
7. `compression=None` を MockIPCServer S1 実装項目に必須明記（RSV1 再発防止）
8. テストサンプルのライフサイクル修正：`pytest.raises` が `with ReplaySession(...)` を囲む形に
9. CI 参照を `python-tests.yml` に統一・`pytest.ini` smoke マーカー追記手順を明記
10. DepthDiff を adapter boundary の正式 4 型目として Step 1 から明記
11. stream continuity 検証の責務境界（Pydantic ≠ stream invariant）を設計注記として追加
12. `.model_dump(mode="json")` wire compatibility テストを Step 3 完了条件に追加
13. Heatmap Phase 2 の ownership 移譲リスク（follow/pause/live 遷移の owner 未定義）を注記追加
14. ViewState 汎用拡張より `HeatmapViewState { camera_offset, camera_scale, cell_size, anchor }` 独立型を推奨する実装調査メモを追加
15. Phase 1 着手前に Phase 2 の所有権設計ラフスケッチを推奨する注記を §4 に追加
16. `Message::LadderScroll` に `pane_id` を追加・`iter_all_panes_mut` との routing ロジック明記

### Findings 一覧

| Finding ID | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|
| User-12 | HIGH | ipc-grpc-migration.md | ロールバック手順表（G1〜G2 期間）追加：Python ランタイム切替可・Rust 再ビルド必要の非対称性を明記 |
| User-13 | HIGH | ipc-grpc-migration.md | G0 に `check_schema_parity.py` 追加：WS/gRPC 併存期間の JSON↔proto drift を CI 機械検知 |
| User-14 | HIGH | ipc-grpc-migration.md | G1 に attach/discovery 移行パスを追加：`ReplaySession` gRPC 接続・auto-probe 動作を完了条件に追加 |
| User-15 | HIGH | ipc-grpc-migration.md | G1 に gRPC smoke テスト追加（`test_grpc_smoke.py`）：G3 削除まで WS smoke と並走 |
| User-16 | MEDIUM | plan-test-mock-ipc-server.md | 現状表に既存テスト4ファイルを追加・計画目的を「移植・高速化」に修正 |
| User-17 | MEDIUM | plan-test-mock-ipc-server.md | `compression=None` を MockIPCServer S1 実装に必須明記（RSV1 ビット再発防止） |
| User-18 | MEDIUM | plan-test-mock-ipc-server.md | テストサンプルのライフサイクル修正：`pytest.raises` が `with ReplaySession(...)` を囲む形に |
| User-19 | MEDIUM | plan-test-mock-ipc-server.md | CI 参照を `python-tests.yml` に統一・`pytest.ini` smoke マーカー追記手順を明記 |
| User-20 | MEDIUM | adapter-type-boundary.md | DepthDiff を adapter boundary 正式 4 型目として Step 1 から明記（必須フィールド 7 項目列挙） |
| User-21 | MEDIUM | adapter-type-boundary.md | stream continuity は Pydantic 単体で保証不可（adapter state / 統合テストの責務）を設計注記追加 |
| User-22 | MEDIUM | adapter-type-boundary.md | `.model_dump(mode="json")` wire compatibility テストを Step 3 完了条件に追加 |
| User-23 | MEDIUM | gui-triple-state-refactor.md | Phase 2 に ownership 移譲リスク注記追加（follow/pause/live の遷移 owner を先に定義） |
| User-24 | MEDIUM | gui-triple-state-refactor.md | ViewState 拡張より `HeatmapViewState` 独立型推奨の実装調査メモを設計判断注記に追記 |
| User-25 | MEDIUM | gui-triple-state-refactor.md | §4 に Phase 2 owner 設計ラフスケッチ後に Phase 1 着手を推奨する注記追加 |
| User-26 | MEDIUM | gui-triple-state-refactor.md | `Message::LadderScroll` に `pane_id` 追加・`iter_all_panes_mut` routing ロジック明記 |

HIGH: 4件 / MEDIUM: 11件

---

## ラウンド 6 — ユーザーレビュー（2026-05-07）

### 統一決定
1. gRPC Handshake/Session 分離問題: `Session` 冒頭メッセージ方式を採用案として記載（`Handshake` RPC 廃止、`Command.oneof` に `HelloRequest` を含める）
2. boot path 前提追加: `--transport ws|grpc` フラグ新設・`EngineSession.transport` フィールド追加・`ws-transport` Cargo feature 新設を G1 作業項目に追加
3. adapter outbox 境界明確化: `ExchangeWorker` が outbox に直接 dict を書く現行構造を明示し、B1 の作業範囲（worker streaming コード変更が必要）を注記
4. HeatmapShader Phase 2 着手条件: `canvas_invalidation`・`rebuild_policy`・follow/pause/live の4グループの移行先 owner を設計ドキュメントに明記してから実装開始
5. A3 は A2 完了後に実施（並列不可）: depth fanout routing の二重修正を防ぐ
6. `HelloReject` を ImplementationLoop-plan.md S2 から削除（EngineError + SCHEMA_MAJOR_MISMATCH に修正）
7. `start_or_attach.rs` 5シナリオを G1 完了条件の regression pin として明示
8. `test_live_session_attach.py` を G2 完了条件の live attach regression として明示

### Findings 一覧

| Finding ID | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|
| User-27 | HIGH | ipc-grpc-migration.md | Handshake/Session RPC 分離が single-stream 認証契約を壊す問題を記載。採用案（Session 冒頭メッセージ方式）・却下案2つを根拠付きで §3.1 に追加 |
| User-28 | HIGH | adapter-type-boundary.md | ExchangeWorker が outbox に直接 wire-format dict を書く現行構造を Step 2/3 に注記。B1 の実際の作業範囲（worker streaming コード変更）と C1 の順序依存を明記 |
| User-29 | HIGH | gui-triple-state-refactor.md | HeatmapShader Phase 2 の着手条件として、密結合4グループ（camera/再描画/再構築ポリシー/follow-pause-live）の移行先 owner 定義を必須化 |
| User-30 | HIGH | ipc-grpc-migration.md / ImplementationLoop-plan.md | gRPC discovery/rollback が現行 boot path を取り落とし。`--transport` フラグ・`EngineSession.transport`・`ws-transport` feature を G1 に追加 |
| User-31 | MED-HIGH | ImplementationLoop-plan.md | `HelloReject` を S2 定義から除去（EngineError + SCHEMA_MAJOR_MISMATCH に修正） |
| User-32 | MED-HIGH | ImplementationLoop-plan.md | A2/A3 並列を「A3 は A2 完了後に推奨」に変更。depth fanout routing の二重修正リスクを根拠として記載 |
| User-33 | MEDIUM | ipc-grpc-migration.md | G1 完了条件に `tests/start_or_attach.rs` 5シナリオ regression pin を追加。G2 完了条件に `test_live_session_attach.py` live attach regression を追加 |

HIGH: 4件 / MED-HIGH: 2件 / MEDIUM: 1件

---

## ラウンド 7（2026-05-07）

### 統一決定
1. Handshake RPC 廃止の完全除去: proto スニペット・§3.3 表・G2 タスク・G0.5 注記から `rpc Handshake(...)` を削除。「Session 冒頭 `Command.oneof.hello = HelloRequest`」に統一
2. G0.5 完了条件: `pytest python/tests/test_mock_grpc_server_basic.py` が `@pytest.mark.timeout(1)` 付きで PASS
3. EngineSession.transport 後方互換: `#[serde(default = "default_transport")]` で `"ws"` デフォルト + Python `SessionFileData` に `transport: str` 追加を G1 タスクに明記
4. 2回目 HelloRequest: G1 完了条件に「INVALID_ARGUMENT でストリーム終了」テスト追加
5. start_or_attach.rs 書き直し: 「必要な場合は」→「必須・G0.5 と同時」
6. S2 完了条件: `pytest -k "not smoke"` → `pytest`（引数なし）
7. §3 節順序整理: §3.4 multi-client を §3.3 の後に移動
8. ExchangeWorker outbox 記述: 実コードに即した正確な説明に修正
9. G1 smoke nightly: `.github/workflows/python-tests.yml` nightly ジョブを明記
10. G2 CI 環境要件: `.github/workflows/rust-tests.yml` への Python setup ステップ追加を明記
11. Phase 2 設計ドキュメント: `docs/✅python-data-engine/🔵heatmap-phase2-ownership.md`（仮称）
12. G1 multi-client テスト: `python/tests/test_server_grpc_multi_client.py` を明記
13. --transport と SCHEMA_MAJOR: ws/grpc 両モードで不一致テスト PASS を G1 完了条件に追記
14. MockIPCServer lifecycle: S1 完了条件に stop() 冪等性テスト追加

### Findings 一覧

| Finding ID | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|
| R7-H1 | HIGH | ipc-grpc-migration.md | §3.1 proto スニペット・§3.3 表・G2 タスクの旧 Handshake RPC 記述を Session 冒頭方式に修正 |
| R7-H2 | HIGH | ipc-grpc-migration.md + ImplementationLoop-plan.md | ipc-grpc-migration.md §4 に G0.5 節追加。ImplementationLoop-plan.md G0.5 に完了条件・テストファイル名・実行コマンド追記 |
| R7-H3 | HIGH | ipc-grpc-migration.md | G1 タスクに EngineSession.transport の serde default + Python SessionFileData 後方互換を明記 |
| R7-H4 | HIGH | ipc-grpc-migration.md | G1 完了条件に「2回目 HelloRequest → INVALID_ARGUMENT」テスト追加 |
| R7-H5 | HIGH | ipc-grpc-migration.md | G1 完了条件に multi-client テストファイル名（test_server_grpc_multi_client.py）と実行コマンド追記 |
| R7-M1 | MEDIUM | plan-test-mock-ipc-server.md | S2 完了条件の `pytest -k "not smoke"` → `pytest`（引数なし）に修正 |
| R7-M2 | MEDIUM | ipc-grpc-migration.md | §3 サブ節を §3.1→§3.2→§3.3→§3.4 の自然順に整理 |
| R7-M3 | MEDIUM | ipc-grpc-migration.md | start_or_attach.rs 書き直しを必須確定タスクに変更 |
| R7-M4 | MEDIUM | adapter-type-boundary.md | Step 2 注記の ExchangeWorker outbox 構造を実コードに即して修正 |
| R7-M5 | MEDIUM | ipc-grpc-migration.md | G1 完了条件に ws/grpc 両モードの schema_major 不一致テスト PASS を追記 |
| R7-M6 | MEDIUM | ipc-grpc-migration.md | G1 smoke nightly の CI ワークフローファイル名（python-tests.yml）を明記 |
| R7-M7 | MEDIUM | ipc-grpc-migration.md | G2 CI 環境要件（rust-tests.yml への Python setup）を完了条件に追記 |
| R7-M8 | MEDIUM | gui-triple-state-refactor.md | Phase 2 着手条件に設計ドキュメントのファイル名・配置（heatmap-phase2-ownership.md）を追記 |
| R7-M9 | MEDIUM | plan-test-mock-ipc-server.md | S1 完了条件に stop() 冪等性テスト追加 |

HIGH: 5件 / MEDIUM: 9件 / LOW: 2件（対応不要）

---

## ラウンド 9 — ユーザーレビュー（2026-05-07）

補足: 3本とも「個別ドキュメント内では注意書きがあるが、統合ロードマップにその制約が十分反映されていない」のが共通パターン。

### 統一決定
1. endpoint 解決経路の再設計: G1「フォーマットはほぼ維持」を削除。`_resolve_endpoint_and_token()`・`DEFAULT_PROBE_URL`・`_AttachClient` の gRPC 対応を G1 明示作業項目として列挙
2. adapter B1/C1 二重境界リスク: 「kabu 先行後の models.py と旧 wire-format 層の並存」を作業前提として明記。全 venue 同時移行 vs 段階移行の方針選択を B1 前提タスクに追加
3. ImplementationLoop B3 着手条件追加: heatmap-phase2-ownership.md 存在確認を B3 前提に追加
4. MockIPCServer API サンプル修正: `port=srv.port, token=srv.token` → `attach_endpoint=f"ws://127.0.0.1:{srv.port}/"` + env var 方式。「拡張不要」削除
5. rollback 計画実態記述: `--transport` 自体が G1 新規実装であることを明記。現行 `__main__.py` に transport 分岐なし
6. SCHEMA_MINOR 修正: 9 → 18。`schemas.rs` → `dto.rs`/`connection.rs`

### Findings 一覧

| Finding ID | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|
| User-34 | HIGH | ipc-grpc-migration.md / ImplementationLoop-plan.md | G1 attach/discovery 記述を endpoint 解決経路の全面再設計として書き直し。`_resolve_endpoint_and_token()` / `DEFAULT_PROBE_URL` / `_AttachClient` の gRPC 対応を G1 作業項目に明記 |
| User-35 | HIGH | ipc-grpc-migration.md / ImplementationLoop-plan.md / adapter-type-boundary.md | rollback 計画実態記述追加（`--transport` 新規実装）。adapter B1/C1 の二重境界リスクを ImplementationLoop に追記 |
| User-36 | HIGH | ImplementationLoop-plan.md | B3（HeatmapShader）着手前提（heatmap-phase2-ownership.md 存在確認）を Stage B に昇格 |
| User-37 | HIGH | plan-test-mock-ipc-server.md | MockIPCServer サンプル API を実 ReplaySession シグネチャ（attach_endpoint + env var）に修正。「拡張不要」削除 |
| User-38 | MEDIUM | ipc-grpc-migration.md | SCHEMA_MINOR=9 → 18。schemas.rs → dto.rs/connection.rs に修正 |
| User-39 | MEDIUM-HIGH | ipc-grpc-migration.md | rollback 表に「--transport フラグが G1 新規実装」の前提注記を追加 |

HIGH: 4件 / MEDIUM-HIGH: 2件

---

## ラウンド 10（2026-05-07）

### 統一決定
1. Stage A 完了条件: `cargo clippy -- -D warnings` 警告なし + `map_engine_event_to_message()` 網羅テスト PASS を追記（ImplementationLoop-plan.md）
2. G0.5 完了条件: `mock_grpc_server` の `stop()` 冪等性テストを追記（ImplementationLoop-plan.md）
3. G1 作業項目: `LiveSession._resolve_endpoint_and_token()` も gRPC transport 分岐必要と明記（ImplementationLoop-plan.md）
4. B1 注記: `outbox: list[dict]` は runtime に `_Broadcaster` が渡される duck typing である旨を明記（adapter-type-boundary.md）

### Findings 一覧

| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| NEW-1 | A | ImplementationLoop-plan.md | Stage A 完了条件に `cargo clippy -- -D warnings` + `map_engine_event_to_message()` 網羅テストを追記 |
| NEW-2 | A | ImplementationLoop-plan.md | G0.5 完了条件に stop() 冪等性テスト（`server.stop(); server.stop()` 例外なし）を追記 |
| NB-2 | B | ImplementationLoop-plan.md | G1 作業項目に `LiveSession._resolve_endpoint_and_token()` の transport 分岐を明記 |
| NB-1 | B | adapter-type-boundary.md | Step 2 B1 注記に `_Broadcaster` / `list[dict]` duck typing の説明を追記 |

HIGH: 0件 / MEDIUM: 4件（全修正済み）/ LOW: 4件（対応不要）

### 残存 LOW（対応不要）
- NEW-3: ipc-grpc-migration.md §3.1 見出しに "Handshake" 語が残存
- NEW-4: grpc_wire_integration の G2/Stage D 重複定義
- NB-3: ipc-grpc-migration.md の EngineSession 構造体確認チェックリスト未記載
- C-NEW-1: Session 先頭が非-HelloRequest の場合のサーバー挙動テスト未記載（G1 完了条件への追加候補）

---

## ラウンド 11 — ユーザーレビュー（2026-05-07）

補足: 現行実装と計画の乖離を中心に指摘。transport-aware attach/discovery・adapter/server 真の境界・Heatmap ownership の 3 点が先決事項として確認された。

### 統一決定
1. attach/discovery 先行ゲート「G0.9」をG1前に切り出す（session_file.rs transport フィールド + probe 切替完了が G1 入口条件）
2. rollback exit criteria = G1 開始前の「entry criteria」に昇格（--transport ws 正常動作確認 → G1 入口条件）
3. adapter 境界 = 「adapter まで model 化」明確化。migration path（dict 直書き → model 化の段階移行）を Step 2/3 に明示
4. Heatmap ownership table = Phase 2 着手条件（必須）に格上げ。owner table なしでは実装開始不可
5. mock IPC server = handshake 専用から attach 解決・inprocess fallback・stale pid シナリオ（S4）へ拡張
6. ipc-grpc-migration.md 前半の別 Handshake RPC 記述を「却下案」ブロックへ移動、Session 冒頭方式に統一
7. G2 EngineConnection 抽象化 = Stage D 一タスクから独立マイルストーンに格上げ（G1 完了後・並列不可）

### Findings 一覧

| Finding ID | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|
| User-40 | HIGH | ipc-grpc-migration.md / ImplementationLoop-plan.md | G0.9 節新設（attach/discovery 先行ゲート）: session_file.rs transport フィールド追加 + 両 _resolve_endpoint_and_token() 分岐を G1 前提条件に |
| User-41 | HIGH | ipc-grpc-migration.md | G1 entry criteria 追加 + rollback 表タイトルを「rollback / exit criteria」に変更 |
| User-42 | HIGH | adapter-type-boundary.md | 「adapter まで model 化」目標明示 + migration path（dict 直書き → model 化段階移行）+ 本番経路統合テスト必須を追記 |
| User-43 | HIGH | gui-triple-state-refactor.md | Phase 2 着手条件を「owner table 必須」に格上げ。follow/pause/live/catch-up/rebuild の owner table が存在するまで実装開始不可 |
| User-44 | MED-HIGH | plan-test-mock-ipc-server.md | S4 節新設（attach 解決シナリオ: 正常 attach / stale pid / session-file なし の 3 ケース）。目標節に attach/fallback カバレッジ明記 |
| User-45 | MEDIUM | ipc-grpc-migration.md | §3.1 別 Handshake RPC 記述を「却下案（参照のみ）」ブロックに移動。Session 冒頭方式に統一 |
| User-46 | MEDIUM | ipc-grpc-migration.md / ImplementationLoop-plan.md | G2 を独立マイルストーンに格上げ。Arc<EngineConnection> リファクタ・trait 化を G1 並列不可ブロッカーとして明示 |

HIGH: 4件 / MED-HIGH: 1件 / MEDIUM: 2件（全修正済み）
