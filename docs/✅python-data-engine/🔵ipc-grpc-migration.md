# 改修プラン: IPC WebSocket → gRPC（tonic + grpcio）

ステータス: **提案（未着手）**
作成日: 2026-05-07

---

## 1. 現状の問題

### 1.1 WebSocket + JSON の既知障害

| 問題 | 発生箇所 | 影響 |
|---|---|---|
| RSV ビット圧縮互換性 | `fastwebsockets`（Rust）↔ `websockets`（Python）| 何度も踏んだ。`per_message_deflate` 有効・無効の組み合わせで無言接続断 |
| SCHEMA_MAJOR/MINOR 手動管理 | `schemas/CHANGELOG.md` | 型追加・削除のたびに人が bump を判断。見落としが breaking になる |
| JSON パース失敗の silent drop | `server.py` の `except` ブロック | 不正フレームが握り潰され、UI 側に届かない |
| フレームサイズ上限なし | WebSocket | 巨大 `KlinesFetched` で OOM リスク |

### 1.2 SCHEMA_MAJOR/MINOR の限界

現行 `SCHEMA_MAJOR=3, SCHEMA_MINOR=18` まで成長した。バージョン管理は手書き
[CHANGELOG](./schemas/CHANGELOG.md) に依存しており、フィールド追加・削除・改名の後方互換性保証は
コードレビューのみに頼っている。protobuf の後方互換性ルール（フィールド番号保持）に移行すれば、
ルール自体がコンパイル時に強制される。

---

## 2. 移行先の選択肢

### 案 A: gRPC（tonic + grpcio）— **推奨**

```
共通 .proto ファイル
      │
      ├── Rust: tonic-build でコード生成 → tonic クライアント
      └── Python: grpc_tools.protoc でコード生成 → grpcio サーバー
```

**採用理由:**

- `.proto` がスキーマの単一ソース。今の `schemas/commands.json` / `events.json` / `schemas.py`
  の三重管理が消える。
- protobuf フィールド番号ルールにより、フィールド追加は無条件に後方互換。フィールド削除・改名は
  `reserved` キーワードで制約される。SCHEMA_MAJOR/MINOR 管理が不要になる。
- RSV ビット問題が原理的に発生しない（HTTP/2 フレーミング、gRPC は独自圧縮オプション）。
- bidirectional streaming RPC で現行 WS の双方向モデルをそのまま表現できる。
- Rust: `tonic` は tokio ネイティブ、async/await で既存コードと親和性が高い。
- Python: `grpcio-tools` + 生成コードは pydantic-protobuf（`betterproto`）で pydantic
  モデルに変換でき、既存 `schemas.py` との置き換えが自然。

**デメリット:**

- HTTP/2 依存。ループバック IPC では過剰とも言えるが許容範囲。
- proto 生成コードのビルドステップ追加（`build.rs` + `grpc_tools`）。
- `tonic` の TLS 設定（ループバックなので skip で問題なし）。
- `betterproto` は 2.0 系がまだ alpha（2026-05 時点）。`grpcio` 標準生成コードで代用可。

### 案 B: msgpack-rpc

```
MessagePack バイナリ over TCP ソケット
      │
      ├── Rust: rmp-serde + 手書きフレーマー
      └── Python: msgpack + 手書きフレーマー
```

**採用理由（相対優位）:**

- WebSocket の RSV ビット問題をそのまま回避できる最小コスト移行。
- JSON より小さい（バイナリ）。遅延が 20〜30% 改善する見込み（取引所 WS の高頻度パスで効く）。

**デメリット:**

- スキーマが proto より弱い（型がバイナリだが、フィールド定義は依然 Python/Rust で二重管理）。
- `rmp-rpc` クレートはメンテが止まっている。フレーマーを自前実装する必要がある。
- SCHEMA_MAJOR/MINOR 管理問題は解決しない。

### 推奨: 案 A（gRPC）

RSV ビット問題の根本解決とスキーマ管理の簡素化を両立できるのは案 A のみ。案 B は RSV 問題は
解消するが SCHEMA_MAJOR 管理問題が残る。

---

## 3. gRPC 移行後のアーキテクチャ

```
┌─────────────────────────────────┐         gRPC (HTTP/2 loopback)         ┌──────────────────────────────────┐
│  Rust Viewer (Iced)             │ ◄────────────────────────────────────► │  Python Data Engine              │
│  tonic::Client                  │   bidirectional streaming RPC          │  grpcio Server                   │
│  engine-client/src/grpc_client  │   engine.proto                         │  engine/server_grpc.py           │
└─────────────────────────────────┘                                         └──────────────────────────────────┘
```

### 3.1 proto サービス定義（概要）

```protobuf
// proto/engine.proto

syntax = "proto3";
package engine;

message HelloRequest {
  uint32 schema_major   = 1;  // breaking change の際に bump
  uint32 schema_minor   = 2;  // 後方互換追加の際に bump
  string client_version = 3;
  string token          = 4;  // stdin 渡しの HMAC トークン
  string mode           = 5;  // "live" | "replay"
}

message EngineCapabilities {
  repeated string supported_venues    = 1;
  bool            supports_bulk_trades = 2;
  bool            supports_depth_binary = 3;
}

message ReadyResponse {
  uint32            schema_major      = 1;
  uint32            schema_minor      = 2;
  string            engine_version    = 3;
  string            engine_session_id = 4;  // UUIDv4。再起動で必ず変わる
  EngineCapabilities capabilities     = 5;
}

// Command / Event は既存 commands.json / events.json から変換
message Command { /* oneof で各コマンドを表現（G0 で確定） */ }
message Event   { /* oneof で各イベントを表現（G0 で確定） */ }

service DataEngine {
  // コマンド送信 & イベント受信（双方向ストリーム。1 クライアント = 1 RPC）
  // Session 冒頭の Command.oneof.hello = HelloRequest でハンドシェイクを行う
  rpc Session(stream Command) returns (stream Event);
}
```

`Session` RPC の双方向ストリームが現行 WebSocket コネクションに対応する。
`Command` は現行 `op` フィールドを持つ JSON コマンド群（`Subscribe` / `Unsubscribe` /
`FetchKlines` 等）、`Event` は現行イベント群（`TradesBatch` / `KlineUpdate` 等）を
protobuf message に 1:1 対応させる。

**ハンドシェイク失敗契約（確定）**: Session 冒頭の `HelloRequest`（`Command.oneof.hello`）に対するサーバーの失敗応答は **stream status コードで完結する。`Event::EngineError` は出さない**。クライアント（Rust `tonic`）は stream status を受け取ると同時にストリームを終了し、Python fanout は stream close で検知する。

> **`Event::EngineError` の用途限定（確定）**: `Event::EngineError` は **ハンドシェイク成功後の Session 中エラー専用**である。ハンドシェイクフェーズ（`HelloRequest` 受信〜 `ReadyResponse` 送信まで）の失敗には `EngineError` を使用してはならない。proto 定義・Python サーバー実装・Rust クライアント実装・wire テスト・mock helper のすべてでこの区分を維持すること。

> **ReadyResponse 先頭 payload 契約（確定）**: ハンドシェイクが成功した場合、サーバーが最初に送信する `Event` の payload は必ず `ReadyResponse` でなければならない。proto 定義・G0 wire テスト・G0.5 mock helper・G2 実 wire テストのすべてがこの前提で統一されている。`ReadyResponse` より前に他の `Event` payload を送信することは禁止する。

失敗パターン別 status code 対応表:

| 失敗パターン | stream status code |
|---|---|
| `schema_major` 不一致 | `FAILED_PRECONDITION` |
| token 不正 / 認証失敗 | `UNAUTHENTICATED` |
| プロトコル違反（例: 最初のメッセージが `HelloRequest` でない） | `INVALID_ARGUMENT` |
| 接続数上限超過 | `RESOURCE_EXHAUSTED` |

Rust 側は status code で retry/fallback を分岐する。`Event::EngineError` を handshake 失敗パスに使用してはならない。G2 完了条件にこのパスの Rust 統合テスト（`HelloRequest` 送信後に各 status code を受けたとき以降のコマンドを送らずに stream が終了することを mock で確認）を追加する。

#### Session 冒頭ハンドシェイク方式（採用済み設計決定）

**採用方式: Session 冒頭メッセージ方式**（確定）
`HelloRequest` を `Session` RPC の最初の `Command` メッセージとして送信し、サーバーが `ReadyResponse` を最初の `Event` として返す。独立した `Handshake` RPC は存在しない。

```protobuf
service DataEngine {
  // Hello/Ready を Session stream の冒頭メッセージで行う（WS と同じ構造）
  rpc Session(stream Command) returns (stream Event);
}

// Command の oneof に HelloRequest を含める
message Command {
  oneof payload {
    HelloRequest hello = 1;
    Subscribe    subscribe = 2;
    // ... 他のコマンド
  }
}
```

**採用理由**: 現行 WS の「同一ストリーム上でのハンドシェイク」をそのまま表現できる。token は Session stream の開始時に一度だけ送られ、以降の Session が認証済みであることが implicit に保証される。

> **却下案（参照のみ）**
> - 却下案 A: gRPC metadata（Authorization ヘッダー）方式 — tonic と grpcio の両方で metadata 読み取りコードが必要で、現行 inline Hello/Ready フローと乖離する。
> - 却下案 B: 別 `Handshake` RPC → `ReadyResponse.session_token` → `Session` RPC metadata 方式 — 認証を二段階で行う分複雑で、gRPC mock で再現しにくい。独立した HTTP/2 ストリームとなるため状態管理も煩雑。

> ⚠ この設計は G0 着手前に確定済み。proto の Service 定義と Command/Event の oneof 構造はこの方式に統一されている。

### 3.2 スキーマ管理の変化

| 現行 | gRPC 移行後 |
|---|---|
| `schemas/commands.json` | `proto/engine.proto`（単一ソース） |
| `schemas/events.json` | 同上 |
| `python/engine/schemas.py`（Pydantic） | `grpc_tools.protoc` 生成（+ betterproto or 標準） |
| `engine-client/src/dto.rs` / `connection.rs`（`SCHEMA_MAJOR`/`MINOR` 定数は `lib.rs`）| `tonic-build` 生成 |
| `SCHEMA_MAJOR` / `SCHEMA_MINOR` 定数 | 廃止。proto フィールド番号のみで互換管理 |
| `ipc-schema-check` スキル | proto lint（`buf lint`）に置き換え |

### 3.3 スキーマバージョニング（gRPC 移行後）

| 項目 | 現行 WebSocket | gRPC 移行後 |
|---|---|---|
| breaking 変更の検出 | `schema_major` 不一致で接続拒否 | Session 冒頭 `HelloRequest.schema_major` が `ReadyResponse.schema_major` と不一致 → FAILED_PRECONDITION（接続拒否）|
| 後方互換追加の検出 | `schema_minor` 差は warning のみ・継続 | Session 冒頭 `HelloRequest.schema_minor` と `ReadyResponse.schema_minor` の差は warning ログのみ・接続継続 |
| フィールド追加の後方互換 | 手動で bump を判断 | protobuf フィールド番号ルール（番号保持）により無条件後方互換 |
| breaking 削除の検出 | 手動レビュー | `buf breaking --against .git#branch=main` が CI で自動検出 |
| バージョン定数 | `SCHEMA_MAJOR=3, SCHEMA_MINOR=18`（手書き） | `HelloRequest.schema_major/minor` として Rust 側コンパイル時定数で管理 |

### 3.4 multi-client 契約（gRPC 移行後）

現行 Phase 8 の multi-client 実装（`_Broadcaster`, `MAX_CONNECTIONS=4`, `ClientConnected`/`ClientDisconnected`, `EngineBusy`）は gRPC でも以下の方式で維持する:

- **1 クライアント = 1 `Session` RPC stream**: gRPC の bidirectional streaming は接続ごとに独立した stream になる。`ws_client.rs` の 1 WebSocket 接続に対応する。
- **サーバー内部 broadcaster**: `server_grpc.py` は `_Broadcaster` 相当の tokio broadcast channel（または asyncio Queue fanout）を持ち、全アクティブ `Session` stream に同じイベントを送出する。
- **MAX_CONNECTIONS 管理**: `Session` RPC の accept 直前に接続数を確認し、4 を超える場合は gRPC ステータス `RESOURCE_EXHAUSTED` で reject（現行の WebSocket 1008 Policy Violation に相当）。
- **ClientConnected/ClientDisconnected**: `Session` RPC の開始/終了を検知して全ストリームに broadcast する。gRPC の `Event` message に `client_connected`/`client_disconnected` の oneof バリアントとして追加する。
- **EngineBusy{request_id}**: `Command` に `request_id` フィールドを持たせ、Python がコマンドを処理できない状態のとき `EngineBusy` を送信元 stream に返す（broadcast ではなく unicast）。
- **G1 での検証**: `server_grpc.py` の multi-client 動作を統合テストで確認する（同時 2 クライアント接続、`ClientConnected` の受信、`MAX_CONNECTIONS` 超過時の `RESOURCE_EXHAUSTED` を確認）。テストファイル: `python/tests/test_server_grpc_multi_client.py`、実行コマンド: `pytest python/tests/test_server_grpc_multi_client.py`。

`engine_proto_revision`（monotonic int による exact match）は **採用しない**。exact match はフィールド追加でも全クライアント同時更新を強制し、protobuf 後方互換の旨味を失うため。

---

## 4. 移行フェーズ

### フェーズ G0: proto 定義 & ビルドパイプライン（1〜2 日）

- [ ] `proto/engine.proto` を書く（既存 `commands.json` / `events.json` から変換）。
- [ ] Rust: `tonic-build` を `build.rs` に追加。`engine-client/Cargo.toml` に `tonic` 依存追加。
- [ ] Python: `grpcio-tools` を `pyproject.toml` に追加。`Makefile` / `scripts/` に生成コマンド追加。
- [ ] `buf.yaml` + `buf.gen.yaml` で Buf CLI 管理（オプション、なければ `protoc` 直叩き）。
- [ ] CI に `buf lint` + `buf breaking --against .git#branch=main` を追加。
- [ ] `commands.json` / `events.json` と `engine.proto` の parity を CI で確認するスクリプトを追加（`scripts/check_schema_parity.py`）。コマンド名・イベント名の対応チェックに加え、**field-level の比較**を機械検証し、drift を PR 時点で検知する。G3（WS 廃止）時に削除。

  field-level チェック項目（`check_schema_parity.py` に実装必須）:
  - field 番号（proto field number と JSON スキーマの順序対応）
  - oneof 名と oneof 内の配置順序
  - optional/repeated の区別
  - reserved field 番号の存在確認（削除済みフィールドの番号再利用防止）
  - 新規追加 field の Rust/Python 両側への対称確認（片側のみ追加を検知）

**完了条件**: 両言語でコードが生成され、型エラーなくコンパイルできる。`buf lint` がエラーコード 0 で通過し、`buf breaking --against .git#branch=main` による後方互換チェックがエラーゼロであること（初回は main ブランチに proto がないため `--against` チェックは次コミット以降から有効）。
- [ ] `python scripts/check_schema_parity.py` がゼロエラーで完了（JSON↔proto の名前対応 + field-level チェック）
- [ ] proto の `Event` message の `oneof` 定義において、`ReadyResponse` が最初の payload（フィールド番号 1）として配置されていることを確認する。G0.5 mock helper および G2 wire テストはこの配置を前提として実装すること（§3.1「ReadyResponse 先頭 payload 契約」との整合）。

### フェーズ G0.5: Python 側 gRPC mock サーバー骨格（0.5〜1 日）

G0 完了後、G1 の統合テストを支えるための Python gRPC mock サーバーを先行作成する。

- [ ] `python/tests/fixtures/mock_grpc_server.py` を新設（`grpcio.aio` ベース）。
- [ ] `DataEngine.Session` RPC のみ実装（Hello/Ready ハンドシェイク骨格のみ）。
  - 最初の `Command` が `HelloRequest` であることを確認し、`ReadyResponse` を返す。
  - 以降の `Command` はすべて無視してストリームを保持する。
- [ ] `python/tests/test_mock_grpc_server_basic.py` に `@pytest.mark.timeout(1)` 付きで以下を実装:
  - mock サーバーが起動できる
  - `HelloRequest` を送ると `ReadyResponse` が返ってくる
  - ストリームが確立したままになる
- [ ] G1 の `start_or_attach.rs` テスト書き直しはこのフェーズと同時に完了させること（下記 G1 参照）。

**完了条件**: `pytest python/tests/test_mock_grpc_server_basic.py` が `@pytest.mark.timeout(1)` 付きで全 PASS。

### フェーズ G0.9: attach/discovery 先行ゲート（0.5 日）

> ⚠ **G1 の厳格な前提ゲート。G0.9 が PASS しない限り G1 に着手してはならない。**

G1 では `engine-client/src/session_file.rs` の `EngineSession` 構造体と Python 側の endpoint 解決ロジックを gRPC 対応させる必要がある。しかし現時点では `transport` フィールドが存在せず、G1/G2 を通常タスクとして並べると「一括置換」になってしまう。G0.9 ではこの基盤だけを先行して確立する。

**G0.9 作業項目:**

- [ ] `engine-client/src/session_file.rs::EngineSession` に `transport: String` フィールドを追加（`#[serde(default = "default_transport")]` で `"ws"` をデフォルト値とし、既存の session ファイルに `transport` フィールドがなくても読めること）。
- [ ] `python/engine/replay_session.py::_resolve_endpoint_and_token()` が session ファイルの `transport` フィールドを読み、`"grpc"` の場合は `ws://` URL ではなく `127.0.0.1:{port}` 形式の gRPC channel address を返す最小実装を追加する。`transport` フィールド不在時は既存の WS 動作を維持する（後方互換）。
- [ ] `python/engine/replay_session.py` 内の `LiveSession._resolve_endpoint_and_token()`（`LiveSession` は `replay_session.py` に `ReplaySession` と同居する helper クラス）にも同じ `transport` 分岐を追加する。
- [ ] `python/engine/replay_session.py::SessionFileData` に `transport: str` フィールドを追加（デフォルト `"ws"`）。

**G0.9 完了条件:**
- `engine-client/src/session_file.rs::EngineSession` に `transport: String`（serde default="ws"）が存在する。
- `replay_session.py::_resolve_endpoint_and_token()` および `replay_session.py` 内の `LiveSession._resolve_endpoint_and_token()` が `transport` 値に応じて ws/grpc endpoint を返す最小実装が存在する。
- 既存の全テスト（`cargo test --workspace` + `pytest python/tests/`）が G0.9 前後で同じ PASS/FAIL 状態を維持する（回帰なし）。

---

### フェーズ G1: Python サーバーを gRPC に置き換え（2〜3 日）

#### G1 開始条件（entry criteria）

> ⚠ **以下がすべて満たされていない限り G1 に着手してはならない。確認失敗時は G0.9 に戻ること。**

- [ ] G0.9 が完了しており、`EngineSession.transport` フィールドが `engine-client/src/session_file.rs` に存在する。
- [ ] `--transport ws` フラグを指定した場合（またはフラグ未実装のため ws が動作する現状）に、`pytest python/tests/` の全既存テストが PASS することを確認する。
- [ ] `cargo test --workspace` が全 PASS していることを確認する。
- [ ] 上記確認後、確認日時をコミットメッセージまたは PR に記録してから G1 着手する。

- [ ] `python/engine/server_grpc.py` を新設（`grpcio.aio` ベース）。
- [ ] 現行 `server.py`（WebSocket）は削除せず `--transport ws` フラグで残存（ロールバック用）。
- [ ] `Session` 双方向ストリームで `Command` / `Event` の送受信を実装。
- [ ] 起動引数は現行と互換（port / token は stdin 渡し継続）。
- [ ] 既存 E2E テスト（`python/tests/`）を gRPC トランスポートで通す。
- [ ] **endpoint 解決経路の再設計（attach/discovery パス移行）**: 現行の `replay_session.py::_resolve_endpoint_and_token()` は session ファイルの `port` フィールドから `ws://127.0.0.1:{port}/` を組み立て、`_AttachClient` が WebSocket で接続する。`process.rs` の `DEFAULT_PROBE_URL = "ws://127.0.0.1:19876/"` もハードコードされた WS URL であり、`EngineSession` 構造体に `transport` フィールドは存在しない。G1 では以下の変更が必要:
  - `replay_session.py::_resolve_endpoint_and_token()` が session ファイルの `transport` フィールドを読み、`"grpc"` の場合は `ws://` URL ではなく `127.0.0.1:{port}` 形式の gRPC channel address を返すよう変更する。`transport` フィールド不在時は既存の WS 動作を維持する（後方互換）。
  - `_AttachClient` が `transport="grpc"` のとき gRPC channel で接続できるよう変更する（WS ソケット接続の代わりに `grpc.insecure_channel` を使用）。
  - `process.rs::DEFAULT_PROBE_URL` のハードコードを撤廃し、`EngineSession.transport` を読んで WS/gRPC のどちらで probe するかを決める分岐に変更する（gRPC の場合は `127.0.0.1:{port}` 形式）。
  - auto-probe（プロセス未起動時の自動起動）パスも gRPC transport で動作することを G1 完了条件に追加する。
- [ ] **gRPC smoke テスト（G1 時点で追加）**: `@pytest.mark.smoke` を付けた `test_grpc_smoke.py` を追加し、`server_grpc.py` の起動・Hello/Ready ハンドシェイク・最低1イベント受信を E2E で確認する。G1 完了時点から gRPC smoke が CI nightly で実行されること。WS smoke と並行して実行（排他ではない）。`.github/workflows/python-tests.yml` の nightly ジョブ（`plan-test-mock-ipc-server.md` S3 で追加）に `pytest --smoke -x` を追加すること。
- [ ] **`--transport` フラグを新設**: `python/engine/__main__.py` の `_parse_args()` に `--transport ws|grpc`（デフォルト `grpc`）を追加。G1 では `--transport grpc` で `server_grpc.py` を起動、`--transport ws` で従来 `server.py` を起動する分岐を追加。
- [ ] **`EngineSession` に `transport` フィールドを追加**: `engine-client/src/session_file.rs` の `EngineSession` 構造体に `transport: String`（`"ws"` または `"grpc"`）を追加。`#[serde(default = "default_transport")]` で `"ws"` をデフォルト値とし（後方互換：既存の session ファイルに `transport` フィールドがなくても読めること）。Python helper が session ファイルを読んで正しいエンドポイント形式（`ws://` vs gRPC channel）を判断できるようにする。あわせて `replay_session.py::SessionFileData` に `transport: str` フィールドを追加（デフォルト `"ws"`）。
- [ ] **`ws-transport` Cargo feature を追加**: `engine-client/Cargo.toml` に `ws-transport = []` feature を追加。WS 関連コード（`ws_client.rs`・`perform_handshake()` 等）をこの feature でゲートする準備をする（G2 で実装）。
- [ ] **`start_or_attach()` の transport 分岐**: `engine-client/src/process.rs` の `start_or_attach()` が `EngineSession.transport` を読んで WS/gRPC のどちらで接続するかを決める分岐を G2 で追加することを、G1 完了条件として設計決定しておく。また gRPC smoke テストに「`--transport ws` / `--transport grpc` 両モードで schema_major 不一致時に接続拒否される」統合テストを追加し PASS させること。

**完了条件**: `--transport grpc` フラグ時に `pytest python/tests/` が全 PASS（smoke テストは `--transport ws` を使った従来のプロセス起動でも引き続き動作することを確認する）。multi-client 動作検証（同時 2 クライアント接続・`ClientConnected` broadcast・`MAX_CONNECTIONS` 超過 reject）が統合テストで PASS（テストファイル: `python/tests/test_server_grpc_multi_client.py`、実行コマンド: `pytest python/tests/test_server_grpc_multi_client.py`）。
- [ ] `ReplaySession(force_mode="attach")` が gRPC エンドポイントに接続できる（`test_replay_session_attach.py` の gRPC 版が PASS）
- [ ] **2回目 HelloRequest**: Session 中に 2 回目の `HelloRequest` を受信した場合、サーバーが `INVALID_ARGUMENT` でストリームを終了するテストが PASS すること。
- [ ] **SCHEMA_MAJOR 独立性**: `--transport ws` / `--transport grpc` 両モードで schema_major 不一致時に接続拒否される統合テストが PASS すること。
- [ ] **regression pin**: `tests/start_or_attach.rs` の全シナリオ（probe_success_attaches_without_spawn / probe_refused_falls_back_to_spawn / token_mismatch_falls_back_to_spawn / schema_major_mismatch_falls_back_to_spawn / empty_token_skips_probe）が `--transport grpc` で引き続き PASS すること。WS mock（tokio-tungstenite）から gRPC mock への書き直しは必須。G0.5 と同時に完了させること。

#### ロールバック / exit criteria（G1〜G2 期間）

> **G1 開始条件との関係**: ロールバック手段（`--transport ws` フラグ）は G1 で新規実装される。G1 着手時点ではまだ存在しないため、G1 開始条件（上記 entry criteria）で `--transport ws` での既存テスト PASS を確認した上で着手し、`--transport` フラグ追加を G1 最初のタスクとして実施すること。フラグ実装が完了するまでロールバック手段は「古いコミットに戻す」のみである。

> **exit criteria**: G1 を中断・中止する判断基準。以下のいずれかが発生した場合は G1 を止めて G0.9 に戻る。
> - `--transport ws` で既存テストが G1 着手前比で FAIL に転落した場合
> - `EngineSession.transport` の serde default が機能せず、既存 session ファイルの読み込みが壊れた場合

| 層 | cutover 方法 | rollback 方法 |
|---|---|---|
| Python server | `--transport grpc` フラグで `server_grpc.py` を起動 | `--transport ws` フラグで `server.py` を起動（ランタイム切替・再デプロイのみ） |
| Rust client | `ws-transport` feature OFF でビルド（デフォルト） | `--features ws-transport` で**再ビルドが必要**（ランタイム切替不可） |

**非対称性の明記**: Python 側は `--transport ws` フラグで即時ランタイム切替が可能。Rust 側は `ws-transport` feature 付き再ビルドが必要であり、ランタイム切替は不可。この非対称性により、rollback 手段は以下の 2 パターンに分かれる:

- **Python WS 戻し + Rust ws-transport 付きバイナリあり**: rollback 成立（双方を WS に戻せる）。
- **gRPC-only Rust バイナリ配布済み + Python を WS に戻したい**: rollback 不成立（Rust が gRPC-only のため、Python を WS に戻しても Rust 側が接続できない）。

**dual-transport artifact 常備方針**: G3（WS 廃止）直前まで、CI は `--features ws-transport` 付きの Rust バイナリ（dual-transport artifact）を gRPC-only バイナリと並行してビルド・保持すること。gRPC-only Rust バイナリのみを配布した後に Python 側を WS に戻す rollback は成立しないため、dual artifact が常備されている間のみ完全 rollback が保証される。G2 完了後は gRPC ビルドと ws-transport ビルドの両方を CI artifact として保持し、rollback 用バイナリを手元に置くこと。

### G2（独立マイルストーン）: EngineConnection 抽象化 + Rust クライアントを gRPC に置き換え（3〜4 日）

> **G1 完了後に独立して取り組むマイルストーン。G1 と並列実施は不可。**
>
> G2 の中核は `EngineConnection` の trait 抽象化であり、工数と詰まりやすさの点で Stage D の一タスクとして扱えるレベルを超えている。独立マイルストーンとして計画すること。
>
> **G2 ブロッカー候補:**
> - `Arc<EngineConnection>` が `main.rs`・`backend.rs`・`process.rs` 等で広範囲に使われており、trait 化の影響範囲特定だけで相応の調査コストがかかる。
> - `EngineConnectionLike` trait（仮称）の設計によっては、既存の async メソッドシグネチャとの整合に工夫が必要になる（`async_trait` crate の使用要否など）。
>
> G2 着手前に影響範囲を `rg "EngineConnection"` で全列挙し、変更ファイル数と変更箇所数を見積もること。

- [ ] `engine-client/src/grpc_client.rs` を新設（`tonic` ベース）。
- [ ] 現行 `engine-client/src/ws_client.rs` はフィーチャーフラグ `ws-transport` で保持。
- [ ] `EngineConnectionLike` trait（仮称）を新設し、`grpc_client` と `ws_client` の両方を実装させることで差し替え可能にする。**注意**: 現行 `EngineConnection` は concrete struct であり、`main.rs`・`backend.rs`・`process.rs` 等で `Arc<EngineConnection>` として広く使われている。trait 抽象化は既存呼び出し箇所の大規模変更を伴うため、G2 の工数見積もりに含めること（3〜4日の見積もりはこの作業を前提とする）。
- [ ] `SCHEMA_MAJOR` チェックを Session 冒頭 `HelloRequest.schema_major` / `schema_minor`（`Command.oneof.hello`）と `ReadyResponse.schema_major` / `schema_minor` の照合（§3.3 バージョニング方針）に置き換え。
- [ ] **`tests/grpc_wire_integration.rs` を新設し、実 Python サーバーとの wire 検証を行う**（下記「実 wire テスト」参照）。
- [ ] `tests/` Rust 統合テスト全 PASS（mock 完結テストと wire テストの両方を含む）。

#### 実 wire テスト（G2 必須）

`tests/grpc_wire_integration.rs` は以下を実施する:

```rust
// python/engine/server_grpc.py をサブプロセスで起動し、
// tonic クライアントが実際に grpcio サーバーと通信することを確認する。
// mock_grpc_server.py ではなく server_grpc.py を使う点が重要。
#[tokio::test]
async fn test_wire_handshake_with_real_python_server() {
    // 1. subprocess::Command で `python -m engine.server_grpc --port 0` を起動
    // 2. stdout から "Listening on port NNNNN" を読んで実ポートを取得
    // 3. tonic::transport::Channel でそのポートに接続
    // 4. HelloRequest を送信し ReadyResponse を受信・フィールド検証
    // 5. schema_major 不一致ケースで FAILED_PRECONDITION が返ることを確認
    // 6. サブプロセスを kill して終了
}
```

**なぜ mock では不十分か**: tonic（Rust）と grpcio（Python）は protobuf エンコーディングの実装が独立しているため、同じ `.proto` からコード生成しても wire 互換性が壊れることがある（フィールド番号のズレ・oneofエンコーディング差異・compression ネゴシエーションなど）。mock_grpc_server.py も grpcio で書かれているが、そこで通っても `server_grpc.py` の実装バグを隠す可能性がある。G2 では必ず本物のサーバーと通信させること。

**完了条件**: `cargo test --workspace`（gRPC transport デフォルト）と `cargo test --workspace --features ws-transport`（WebSocket フォールバック）の両方が全 PASS。**かつ** `cargo test grpc_wire_integration` が実 Python サーバーを起動した上で PASS すること（CI では `python/engine/server_grpc.py` が起動可能な環境で実行すること）。`.github/workflows/rust-tests.yml`（または相当ファイル）に Python セットアップと `grpcio-tools` インストールステップの追加が必要。
- [ ] `python/tests/test_live_session_attach.py` が gRPC transport で PASS すること（`replay_session.py` 内 `LiveSession` の live attach 回帰）。

### フェーズ G3: WebSocket トランスポート廃止（1 日）

- [ ] `ws-transport` フィーチャーフラグと `server.py`（WebSocket）を削除。
- [ ] `SCHEMA_MAJOR` / `SCHEMA_MINOR` 定数を削除。
- [ ] `ipc-schema-check` スキルの内容を `buf` コマンドに更新。
- [ ] `schemas/CHANGELOG.md` にアーカイブ注記を追加。

**完了条件**: `cargo clippy -- -D warnings` + `pytest` 全 PASS。`rg "per_message_deflate|rsv" --type rust --type python` のヒットがゼロ件（RSV ビット関連コードが完全消滅）。

---

## 5. リスクと対策

| リスク | 確率 | 対策 |
|---|---|---|
| `betterproto` alpha の API 不安定 | 中 | `grpcio` 標準生成コードで代用（pydantic 変換は手書き thin wrapper） |
| bidirectional streaming での背圧（backpressure）未実装 | 中 | `Session` RPC の送信側に `asyncio.Queue(maxsize=1000)` でバッファ制限 |
| Windows でのビルド（`protoc` バイナリ配置） | 低 | `protoc-bin-vendored` crate で Rust build.rs 内に同梱 |
| 移行中の二重メンテ | 中 | G1〜G2 でフィーチャーフラグを使い、本番は常に片方のみ有効 |
| RSV ビット問題が gRPC 側でも再発 | 極低 | HTTP/2 は独自フレーミング。WebSocket 圧縮ネゴシエーションがそもそも存在しない |

---

## 6. 前提・保留事項

- **対象外**: 取引所 ↔ Python の外向き WebSocket（これは取引所 API なので変更不可）。
  RSV ビット問題は「Rust↔Python ローカル IPC」側のみ発生していた。
- **Tachibana / kabuStation venue**: IPC 層が共通なので gRPC 移行でそのまま恩恵を受ける。
  venue 固有のコードへの影響はない。
- **Python 単独モード**（Python 単独モード: Rust なしで Python クライアント同士でも gRPC は動作するため、将来的な Python 単独モードとの親和性が高い）。
- **着手タイミング**: `fee_total` 実装完了後に着手を検討。IPC 移行は大きな変更なので、他の機能実装と並走させない。

---

## 7. 参考リンク

- [tonic (Rust gRPC)](https://github.com/hyperium/tonic)
- [grpcio (Python)](https://github.com/grpc/grpc/tree/master/src/python/grpcio)
- [betterproto](https://github.com/danielgtaylor/python-betterproto) — Pydantic-like dataclasses from proto
- [Buf CLI](https://buf.build/docs/introduction) — proto lint / breaking change detection
- [protoc-bin-vendored](https://github.com/neoeinstein/protoc-bin-vendored) — Windows での protoc 同梱
- [MISSES.md / WSコンパイル互換問題](../../MISSES.md) — RSV ビット過去事例
