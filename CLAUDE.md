# e-station — Claude Code ガイド

## プロジェクト概要

e-station は Rust（Iced GUI）+ Python データエンジン（WebSocket IPC）で構成する
マーケットデータ可視化アプリ。

```
src/                  # Rust GUI（iced）
engine-client/        # Rust IPC クライアント（fastwebsockets）
exchange/             # 取引所アダプター（Rust）
python/engine/        # Python データエンジン（websockets サーバー）
tests/e2e/            # E2E スモークテスト（bash）
python/tests/         # Python ユニット・統合テスト（pytest）
engine-client/tests/  # Rust 統合テスト（tokio-tungstenite モック）
```

---

## 不具合が見つかった時の対処法

### ステップ 1: デバッグ

```
1. 現状のコードから仮説を 2〜8 個立てる
2. 各仮説を検証するログをコードに追加する
3. ログを確認して原因を特定する
4. 修正を行う
5. 追加したログをすべて削除する
```

ログは `~/AppData/Roaming/flowsurface/flowsurface-current.log` に出力される。

### ステップ 2: 修正後に `/bug-postmortem` を起動する

**不具合を修正したら必ずこのスキルを実行すること。**

```
/bug-postmortem
```

このスキルは 5 つのフェーズで動作する：

| フェーズ | 内容 |
|---------|------|
| Phase 1 分析 | なぜ既存テストで発見できなかったかを構造的に分析する |
| Phase 2 判断 | テストを追加すべきかどうかを判定する |
| Phase 3 実装 | テストを書く |
| Phase 4 検証 | 修正前→FAIL、修正後→PASS を実際に確認する |
| Phase 5 記録 | `.claude/skills/bug-postmortem/MISSES.md` に知見を追記する |

### ステップ 3: 過去の見逃しパターンを確認する

分析の前に必ず読む：

```
.claude/skills/bug-postmortem/MISSES.md
```

過去に記録された見逃しパターン（Mock 置換漏れ・同一言語テスト・ログ検査漏れ・
再接続隠蔽など）と照合することで、同じクラスの見逃しを防ぐ。

---

## テスト構成

### Python テスト

```bash
uv run pytest python/tests/ -v
```

| ファイル | 対象 |
|---------|------|
| `test_server_dispatch.py` | IPC コマンドのディスパッチ |
| `test_server_proxy.py` | SetProxy 後のストリーム再購読 |
| `test_server_ws_compat.py` | WebSocket フレーム互換性（圧縮・RSV ビット） |
| `test_*_rest.py` | 各取引所 REST クライアント |
| `test_*_depth_sync.py` | 各取引所 Depth 同期 |

### Rust テスト

```bash
cargo test -p flowsurface-engine-client
cargo test --workspace
```

| ファイル | 対象 |
|---------|------|
| `handshake.rs` | Hello/Ready ハンドシェイク |
| `connection_closed.rs` | wait_closed() 解決タイミング |
| `process_lifecycle.rs` | on_restart / on_ready コールバック |
| `depth_gap.rs` | DepthGap → 再同期 |

### E2E スモークテスト

```bash
bash tests/e2e/smoke.sh           # 30 秒観測（デフォルト）
OBSERVE_S=120 bash tests/e2e/smoke.sh  # 2 分観測
```

**検査項目**:
- ハンドシェイクが 15 秒以内に完了する
- `engine ws read error`（WebSocket プロトコルエラー）が出ない
- 観測ウィンドウ中の再接続が 2 回以下
- DepthGap・parse error・snapshot fetch failed が出ない

---

## 開発コマンド

```bash
# 開発用ビルド
cargo build

# リリースビルド
cargo build --release

# Python エンジンを手動起動（ポート 19876）
uv run python -m engine --port 19876 --token dev-token

# エンジンに接続してアプリ起動
cargo run -- --data-engine-url ws://127.0.0.1:19876/

# Rust フォーマット
cargo fmt

# Rust lint
cargo clippy -- -D warnings
```

---

## アーキテクチャ上の注意点

### IPC 境界（Rust ↔ Python WebSocket）

- **スキーマバージョン**: `engine-client/src/lib.rs` の `SCHEMA_MAJOR/MINOR` と
  `python/engine/schemas.py` の `SCHEMA_MAJOR/MINOR` を常に一致させる（major のみ検査）
- **圧縮設定**: `websockets.serve(compression=None)` は必須。
  fastwebsockets は permessage-deflate を実装しておらず、RSV1=1 フレームを受信すると
  接続を切断する（`MISSES.md` 2026-04-25 参照）
- **Token 認証**: `hmac.compare_digest` でタイミング攻撃を防いでいる。Token を
  ログに出力しないこと

### テスト設計の原則

- **言語境界**: Rust クライアント × Python サーバーの組み合わせは同一言語テストで
  再現できない挙動差異を持つ。`fastwebsockets` を使う場合は実際の Python サーバーと
  組み合わせたテストで補完すること
- **Mock の補完**: `tokio-tungstenite` モックは高速だが、Python `websockets` の
  デフォルト設定による挙動差異を再現できない。統合テストで補完する
- **リグレッションガード**: 設定値の削除で元に戻るタイプの修正には、
  その設定値の存在を assert するテストを追加すること
