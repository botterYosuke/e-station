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

ログの出力先はビルド種別で異なる：

- **release ビルド** (`cargo build --release`): `~/AppData/Roaming/flowsurface/flowsurface-current.log`
- **debug ビルド** (`cargo build`): ターミナルの stdout

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
| `test_tachibana_dev_env_guard.py` | release 時に dev 自動ログインが動かないことを保証 |
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
cargo run -- --mode live --data-engine-url ws://127.0.0.1:19876/

# Rust フォーマット
cargo fmt

# Rust lint
cargo clippy -- -D warnings
```

### 起動モード（`--mode` は必須）

N1.13 以降、`flowsurface` 起動時に `--mode {live|replay}` の指定が**必須**になった。
省略すると即座に終了する：

```
flowsurface: --mode is required (use 'live' or 'replay'); e.g. `flowsurface --mode replay`
```

| モード | 用途 | 起動例 |
|--------|------|--------|
| `live` | 取引所からのリアルタイムデータを購読する通常運用 | `cargo run -- --mode live` |
| `replay` | 録画済みデータの再生（`/replay/*` HTTP API が有効化される） | `cargo run -- --mode replay` |

VSCode から CodeLLDB でデバッグする場合は [.vscode/launch.json](.vscode/launch.json) に
`Rust: Debug (CodeLLDB) - live` / `Rust: Debug (CodeLLDB) - replay` の 2 構成を
用意してある。デバッグサイドバーの起動構成セレクタから選ぶこと。

新しい起動経路（CI スクリプト・ドキュメント・別の launch.json 等）を追加するときは
必ず `--mode` を渡すこと。忘れると上記メッセージで即終了する。

---

## 立花証券 安全装置

### dev ログイン（自動ログイン）はリリースビルドで動作しない

`DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO` の env var
による自動ログイン fast path が有効になる条件は起動経路によって異なる。

| 起動経路 | fast path が有効になる条件 |
|---------|--------------------------|
| `cargo run`（debug ビルド） | `DEV_TACHIBANA_*` env var が設定されていれば自動で有効 |
| `cargo run --release`（release ビルド） | 無効（`dev_tachibana_login_allowed=False` が渡される） |
| `uv run python -m engine ...`（手動起動） | `DEV_TACHIBANA_*` に加えて `FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1` も必要 |

release ビルドではたとえ env var が設定されていても自動ログインは行われずダイアログが表示される。
これは意図的な安全装置（`python/tests/test_tachibana_dev_env_guard.py` で保護）。

- dev creds の env var 名は `DEV_TACHIBANA_*` 形式のみ有効（旧 `DEV_USER_ID` 等は削除済み）

### 本番 URL への送信には `TACHIBANA_ALLOW_PROD=1` が必要

HTTP 送信直前に `guard_prod_url()` が呼ばれ、本番 URL かつ
`TACHIBANA_ALLOW_PROD != "1"` なら `ValueError` を raise して送信を遮断する。
デモ URL（`demo-kabuka.e-shiten.jp`）は本番扱いしない。

```bash
# 本番環境で実行する場合のみ設定する
TACHIBANA_ALLOW_PROD=1 cargo run --release
```

誤って設定しないこと。`python/engine/exchanges/tachibana_url.py:48` 参照。

---

## 立花証券 実機診断スクリプト

ログを追加する前に、まずこれらのスクリプトで接続経路を切り分けること。

```bash
# EVENT WebSocket への接続・FD フレーム受信を診断（板情報が届くか確認）
uv run python scripts/diagnose_tachibana_ws.py
uv run python scripts/diagnose_tachibana_ws.py --ticker 6758 --frames 5

# ログインフロー単体の smoke（Rust 不要・HTTP のみ）
uv run python scripts/smoke_tachibana_login.py
```

`diagnose_tachibana_ws.py` は REST snapshot → WS 接続 → KP/FD フレーム受信の
5 ステップを順に検証し、どこで切れているかを出力する。

`.env` に `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO=true`
が必要。デモ口座のみ対応。

---

## 永続状態ファイル

デバッグ時は以下のファイルが再現性に影響する。

| ファイル | 場所 | 役割 |
|---------|------|------|
| `saved-state.json` | `%APPDATA%\flowsurface\saved-state.json` | UI 状態（ペイン構成・ウィンドウサイズ）を起動時に復元する |
| `tachibana_orders.jsonl` | `~/.cache/flowsurface/engine/tachibana_orders.jsonl` | 発注 WAL。Python が書き、Rust が読む。重複発注防止に使う |

- `saved-state.json` が残っていると前回の UI レイアウトが復元される。
  再現手順を書くときは「初期状態か保存済み状態か」を明記すること
- `tachibana_orders.jsonl` の WAL パスは `data_path` に依存する。
  パスを変えると別の WAL を参照するため、重複発注防止が効かなくなる。変更しないこと

---

## アーキテクチャ上の注意点

### IPC 境界（Rust ↔ Python WebSocket）

- **スキーマバージョン**: ハンドシェイク失敗の条件は `SCHEMA_MAJOR` の不一致のみ。
  `SCHEMA_MINOR` はログに記録されるだけで接続を切らない。
  ただし `engine-client/src/lib.rs` と `python/engine/schemas.py` の両 `SCHEMA_MAJOR` は
  常に一致させること
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

---

## 並行実装オーケストレーション

大型フェーズ（タスク 5 件以上）の実装は `/parallel-agent-dev` スキルを使う。
依存グラフを把握し、独立タスクを同一メッセージで並列起動することで実装速度が 3〜5 倍になる。

- 計画書の読み込み → 依存グラフ構築 → 並列グループ分け → 完了後に次グループ起動
- 各エージェントは `isolation: "worktree"` で独立実行
- 直列ステップの境界で `cargo test --workspace` と `uv run pytest` を手動確認
- 全完了後は `/review-fix-loop` でレビューを走らせる

---

## レビュー時のスキル指定

このリポジトリでコードレビュー・差分レビュー・PR レビューを行うときは、
必ず `.claude/skills/e-station-review/SKILL.md` のスキルを Skill ツールで起動する。

- スキル一覧上の表示名は `e-station-review`
- frontmatter の `name:` が `review` なので組み込み `/review` と紛らわしいが、
  **組み込み `/review` は使わない**
- review-fix-loop のレビュー段でも、サブエージェントに e-station-review スキルを
  使わせること

