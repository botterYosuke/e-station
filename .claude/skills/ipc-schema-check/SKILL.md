---
name: ipc-schema-check
description: Rust engine-client と Python engine の IPC スキーマ版数（SCHEMA_MAJOR/MINOR）と圧縮設定の整合を即座に検証する。schemas.py や engine-client/src/lib.rs を編集したときに `/ipc-schema-check` で呼び出す。
---

# IPC Schema Check

Rust ↔ Python WebSocket IPC の互換性を即座に検証するスキル。
スキーマ版数の不一致と WebSocket 圧縮設定の欠落を一度に確認します。

## When to Use

- `engine-client/src/lib.rs` の `SCHEMA_MAJOR` / `SCHEMA_MINOR` を変更したとき
- `python/engine/schemas.py` のスキーマ定義を変更したとき
- `python/engine/server.py` を編集したとき（compression 設定の確認）
- 新しい IPC コマンドを追加したとき
- ハンドシェイクエラーが発生したとき

## How It Works

### Step 1: SCHEMA_MAJOR / SCHEMA_MINOR の整合確認

```bash
# Rust 側
grep -n "pub const SCHEMA_MAJOR\|pub const SCHEMA_MINOR" engine-client/src/lib.rs

# Python 側
grep -n "^SCHEMA_MAJOR\|^SCHEMA_MINOR" python/engine/schemas.py
```

判定:
- **major が一致しないと致命的** — ハンドシェイクが拒否される
- **minor は不一致を許容** — 後方互換変更のみ

### Step 2: WebSocket 圧縮設定の確認

```bash
grep -n "compression" python/engine/server.py
```

`websockets.serve(..., compression=None)` が必ず存在することを確認。
欠落している場合は **MISSES.md 2026-04-25** の RSV1 バグが再発する。

### Step 3: スキーマ定義の対称性確認

```bash
# Rust 側のメッセージ型
grep -n "^pub enum\|^pub struct" engine-client/src/lib.rs engine-client/src/messages.rs 2>/dev/null

# Python 側のメッセージ型
grep -n "^class\|@dataclass" python/engine/schemas.py
```

両側で対応するメッセージ型が定義されているか確認。

### Step 4: リグレッションテストの実行

```bash
# Python 側 — WebSocket 互換性テスト
uv run pytest python/tests/test_server_ws_compat.py -v

# Python 側 — スキーマテスト
uv run pytest python/tests/test_schemas.py -v

# Rust 側 — ハンドシェイクテスト
cargo test -p flowsurface-engine-client --test handshake
cargo test -p flowsurface-engine-client --test wait_ready
```

### Step 5: 結果レポート

以下のフォーマットで報告:

```
[IPC Schema Check]

SCHEMA_MAJOR: Rust=1 Python=1  → OK
SCHEMA_MINOR: Rust=2 Python=2  → OK

Compression: python/engine/server.py に compression=None あり → OK

Tests:
  test_server_ws_compat: PASS (2 tests)
  test_schemas:          PASS (N tests)
  handshake (Rust):      PASS
  wait_ready (Rust):     PASS

総合判定: ✓ IPC 互換性に問題なし
```

問題があれば:

```
[IPC Schema Check]

SCHEMA_MAJOR: Rust=2 Python=1  → CRITICAL: 不一致
  → ハンドシェイクが拒否される。両側を 2 に揃えるか、Rust を 1 に戻す。

Compression: python/engine/server.py に compression=None なし → CRITICAL
  → MISSES.md 2026-04-25 の RSV1 圧縮バグが再発する。
  → `websockets.serve(..., compression=None)` を追加する。
```

## 関連ファイル

| ファイル | 役割 |
|---------|------|
| `engine-client/src/lib.rs` | Rust 側スキーマ版数定数 |
| `python/engine/schemas.py` | Python 側スキーマ版数定数 |
| `python/engine/server.py` | WebSocket サーバー（compression 設定） |
| `engine-client/src/connection.rs` | ハンドシェイク実装 |
| `python/tests/test_server_ws_compat.py` | 圧縮非互換のリグレッションテスト |
| `python/tests/test_schemas.py` | スキーマシリアライゼーションテスト |
| `.claude/skills/bug-postmortem/MISSES.md` | 過去の見逃しパターン |

## 関連エージェント

より深い検査が必要なときは `ws-compatibility-auditor` エージェントを起動してください。
RSV ビット・Token 漏洩・Close フレーム処理まで包括的に検査します。