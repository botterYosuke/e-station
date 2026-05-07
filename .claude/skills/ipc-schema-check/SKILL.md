---
name: ipc-schema-check
description: Rust engine-client と Python engine の IPC スキーマ版数（SCHEMA_MAJOR/MINOR）と protobuf 定義の整合を即座に検証する。schemas.py や engine-client/src/lib.rs, engine.proto を編集したときに `/ipc-schema-check` で呼び出す。
---

# IPC Schema Check

Rust ↔ Python gRPC IPC の互換性を即座に検証するスキル。
スキーマ版数の不一致と proto フィールド番号の欠落を一度に確認します。

> **[G3 アーカイブ]** WebSocket トランスポートは 2026-05-08 に廃止されました。
> `compression=None` チェックおよび `test_server_ws_compat.py` は削除済みです。
> RSV ビット問題は gRPC 移行により原理的に消滅しました。

## When to Use

- `engine-client/src/lib.rs` の `SCHEMA_MAJOR` / `SCHEMA_MINOR` を変更したとき
- `python/engine/schemas.py` のスキーマ定義を変更したとき
- `python/engine/proto/engine.proto` を編集したとき
- 新しい IPC コマンド/イベントを追加したとき
- gRPC ハンドシェイクエラー（FAILED_PRECONDITION, UNAUTHENTICATED）が発生したとき

## How It Works

### Step 1: SCHEMA_MAJOR / SCHEMA_MINOR の整合確認

```bash
# Rust 側
grep -n "pub const SCHEMA_MAJOR\|pub const SCHEMA_MINOR" engine-client/src/lib.rs

# Python 側
grep -n "^SCHEMA_MAJOR\|^SCHEMA_MINOR" python/engine/schemas.py
```

判定:
- **major が一致しないと致命的** — gRPC ハンドシェイク HelloRequest が FAILED_PRECONDITION で拒否される
- **minor は不一致を許容** — 後方互換変更のみ

### Step 2: protobuf 定義の確認

```bash
# proto ファイルの存在確認
ls engine-client/proto/engine.proto python/engine/proto/engine.proto

# フィールド番号の対称性確認（主要メッセージ）
grep -n "^message\|= [0-9]\+;" engine-client/proto/engine.proto | head -50
```

### Step 3: スキーマ定義の対称性確認

```bash
# Rust 側のメッセージ型（dto.rs）
grep -n "^pub enum\|^pub struct" engine-client/src/dto.rs 2>/dev/null | head -30

# Python 側のメッセージ型
grep -n "^class\|@dataclass" python/engine/schemas.py | head -20
```

両側で対応するメッセージ型が定義されているか確認。

### Step 4: リグレッションテストの実行

```bash
# Python 側 — スキーマテスト
uv run pytest python/tests/test_schemas.py -v

# Python 側 — gRPC スモークテスト
uv run pytest python/tests/test_grpc_smoke.py -v

# Rust 側 — engine-client ユニットテスト
cargo test -p flowsurface-engine-client

# Rust 側 — gRPC wire 統合テスト（Python 環境が必要）
# cargo test -p flowsurface-engine-client --test grpc_wire_integration -- --include-ignored --nocapture
```

### Step 5: 結果レポート

以下のフォーマットで報告:

```
[IPC Schema Check]

SCHEMA_MAJOR: Rust=2 Python=2  → OK
SCHEMA_MINOR: Rust=3 Python=3  → OK

Proto: engine.proto の主要フィールド番号 → OK

Tests:
  test_schemas:          PASS (N tests)
  test_grpc_smoke:       PASS (N tests)
  engine-client (Rust):  PASS (N tests)

総合判定: ✓ IPC 互換性に問題なし
```

問題があれば:

```
[IPC Schema Check]

SCHEMA_MAJOR: Rust=3 Python=2  → CRITICAL: 不一致
  → gRPC ハンドシェイクが FAILED_PRECONDITION で拒否される。
  → 両側を同じ値に揃える（python/engine/schemas.py または engine-client/src/lib.rs）。
```

## 関連ファイル

| ファイル | 役割 |
|---------|------|
| `engine-client/src/lib.rs` | Rust 側スキーマ版数定数 |
| `python/engine/schemas.py` | Python 側スキーマ版数定数 |
| `engine-client/proto/engine.proto` | Rust 側 protobuf 定義 |
| `python/engine/proto/engine.proto` | Python 側 protobuf 定義 |
| `engine-client/src/grpc_transport.rs` | Rust gRPC クライアント（ハンドシェイク実装） |
| `python/engine/server_grpc.py` | Python gRPC サーバー |
| `python/tests/test_grpc_smoke.py` | gRPC スモークテスト |
| `python/tests/test_schemas.py` | スキーマシリアライゼーションテスト |
| `.claude/skills/bug-postmortem/MISSES.md` | 過去の見逃しパターン（RSV ビットバグは G3 で原理消滅） |

## 関連エージェント

より深い検査が必要なときは `ws-compatibility-auditor` エージェントを起動してください。
（G3 以降は gRPC 専用ですが、RSV ビット・Token 漏洩・framing 問題の過去事例を参照するために有用）
