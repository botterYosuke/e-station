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

# エンジンに接続してアプリ起動（トークンは環境変数で指定）
FLOWSURFACE_ENGINE_TOKEN=dev-token cargo run -- --mode live --data-engine-url ws://127.0.0.1:19876/

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
`live - Rust: Debug (CodeLLDB)` / `replay - Rust: Debug (CodeLLDB)` の 2 構成を
用意してある。デバッグサイドバーの起動構成セレクタから選ぶこと。

新しい起動経路（CI スクリプト・ドキュメント・別の launch.json 等）を追加するときは
必ず `--mode` を渡すこと。忘れると上記メッセージで即終了する。

### replay モードの使い方

#### サンプル戦略を流す最小コマンド

```bash
bash scripts/run-replay-debug.sh docs/example/buy_and_hold.py 1301.TSE 2025-01-06 2025-03-31
```

`run-replay-debug.sh` は以下を一気にやる：

1. `cargo build`（debug ビルド）
2. `replay_dev_load.sh` を background で起動（HTTP ポート 9876 を polling）
3. `flowsurface --mode replay` を foreground 起動
4. background スクリプトが `POST /api/replay/load` → `POST /api/replay/start` を順に投げる

サンプル戦略は `docs/example/` 配下：

| ファイル | 内容 |
|---------|------|
| `buy_and_hold.py` | 最初のバーで成行買いし、以降は保有し続ける最小戦略 |

#### 引数一覧

```
run-replay-debug.sh <strategy_file> <instrument_id> <start_date> <end_date> [granularity]
replay_dev_load.sh  <strategy_file> <instrument_id> <start_date> <end_date> [granularity]
```

| 位置 | 必須 | 例 |
|------|------|-----|
| `$1` strategy_file | ✅ | `docs/example/buy_and_hold.py` |
| `$2` instrument_id | ✅ | `1301.TSE` |
| `$3` start_date | ✅ | `2025-01-06` (ISO8601) |
| `$4` end_date | ✅ | `2025-03-31` (ISO8601) |
| `$5` granularity | 任意（既定 `Daily`） | `Daily` / `Minute` / `Trade` |

任意パラメータは引き続き env var で上書き可：

| 変数 | 既定値 |
|------|--------|
| `REPLAY_INITIAL_CASH` | `1000000`（円） |
| `REPLAY_STRATEGY_ID` | `user-strategy` |

#### J-Quants データの場所

`S:/j-quants/` 直下に月次 CSV.gz が必要：

- Daily: `equities_bars_daily_YYYYMM.csv.gz`
- Minute: `equities_bars_minute_YYYYMM.csv.gz`
- Trade: `equities_trades_YYYYMM.csv.gz`

env var `JQUANTS_DIR` で上書き可（既定 `S:/j-quants`）。
`POST /api/replay/load` は**ファイル存在確認のみ**で行を読まない。返値の
`bars_loaded:0` は仕様通り（実際のロード件数ではない）。

#### `saved-state.json` の扱い（D9）

replay モードでは `saved-state.json` を **load も save も行わない**：

- 起動時：常にデフォルト状態（空ペイングリッド）から始まる
- 終了時：live モードの設定を上書きしない

これにより replay セッションが live のレイアウト・ウィンドウ位置を汚染しない。
ペインは `ReplayDataLoaded` 受信後に `auto_generate_replay_panes` が
TimeAndSales / CandlestickChart / OrderList / BuyingPower を自動生成する。

#### IPC イベントの流れ（streaming replay）

```
POST /api/replay/load   → LoadReplayData IPC
                        → Python: check_data_exists() （ファイル存在のみ）
                        → ReplayDataLoaded
                        → Rust: AutoGenerateReplayPanes コマンド送信（ack 付き）
                        → Iced: pane 生成 + stream bind 完了で ack.notify_one()
                        → 200 OK = pane ready まで含む（pane_ready_timeout で 504）

POST /api/replay/start  → StartEngine IPC
                        → Python: NautilusRunner.start_backtest_replay_streaming()
                        → EngineStarted
                        → 1 バー / tick ずつ処理：
                          - DateChangeMarker（営業日跨ぎ）
                          - KlineUpdate / Trades（market data 複製）
                          - ExecutionMarker / StrategySignal（戦略の発注時）
                          - ReplayBuyingPower（ポートフォリオ更新）
                        → 全バー処理後 EngineStopped
```

`replay_speed.py` の `SLEEP_CAP_SEC=0.200` により tick 間隔は最大 200ms に
キャップされる。Daily バー 60本でも約 12 秒で完走する。

#### よくある落とし穴

- **`Subscribe: unknown venue 'replay'` ログ**：Python は replay を実 venue として
  登録していないため Subscribe / FetchKlines は拒否される。これは仕様通り。
  チャート表示は streaming で push される `KlineUpdate` を直接受信して描画する
- **チャートに初期履歴が無い**：`FetchKlines` は失敗するが streaming で 1 本ずつ
  bar が増える。replay 開始直後はチャート空、徐々に bar が積まれる
- **`saved-state.json` を消したら再現するバグ**：D9 で replay は常にこの状態で
  起動するため、空ペイングリッドからのフローを必ず動作確認すること
- **`/load` 後に sleep を入れる必要はない**：`/api/replay/load` は pane 生成
  完了まで blocking で 200 を返すので、`replay_dev_load.sh` で `/start` を投げる
  前に sleep する必要はない。debug+lldb で重い場合は `REPLAY_PANE_READY_TIMEOUT_S`
  で ack 待機を延長できる（既定 debug 30 s / release 10 s）。詳細は
  `docs/✅nautilus_trader/replay-launch-empty-pane-issue.md` 第五原因参照

### 外部エンジンに接続する際のトークン認証

`--data-engine-url` を指定してアプリを起動する際は、
**必ず環境変数 `FLOWSURFACE_ENGINE_TOKEN` をエンジンのトークンと一致させる必要がある**。

アプリ側（`src/main.rs` の `FLOWSURFACE_ENGINE_TOKEN` 取得箇所）は以下のように環境変数から取得している：
```rust
let token = std::env::var("FLOWSURFACE_ENGINE_TOKEN").unwrap_or_default();
```

エンジン起動時のトークン：
```bash
uv run python -m engine --port 19876 --token dev-token
```

アプリ起動時に一致するトークンを指定：
```bash
FLOWSURFACE_ENGINE_TOKEN=dev-token cargo run -- --mode live --data-engine-url ws://127.0.0.1:19876/
```

**トークンが一致しない場合、`error: data engine connection failed to initialise` で起動に失敗する。**

#### `--data-engine-url` 未指定時の自動プローブ（start_or_attach）

`--data-engine-url` を指定しなくても、`FLOWSURFACE_ENGINE_TOKEN` を設定して
`ws://127.0.0.1:19876/` で待ち受け中のエンジンがあれば自動接続する（TCP 2 秒タイムアウト）。

- env 未設定の場合は外部試行を skip して直接 spawn する（無駄な遅延と warn ログを避けるため）
- env 設定ありで接続できない場合（タイムアウト・token 不一致・SCHEMA_MAJOR 不一致）のみ Rust が新規 spawn する
- 手動起動エンジンを再利用したい場合は `FLOWSURFACE_ENGINE_TOKEN` を Python の `--token` と一致させること

```bash
# 手動起動エンジンを再利用する場合
uv run python -m engine --port 19876 --token dev-token
FLOWSURFACE_ENGINE_TOKEN=dev-token cargo run -- --mode live
# → 19876 へ自動プローブ → 成功すれば attach（spawn なし）
```

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
| `saved-state.json` | `%APPDATA%\flowsurface\saved-state.json` | 起動時に「前回の状態」を開く役割。終了時に自動で書き出され、次回起動時に自動で読み込まれる |
| `tachibana_orders.jsonl` | `~/.cache/flowsurface/engine/tachibana_orders.jsonl` | 発注 WAL。Python が書き、Rust が読む。重複発注防止に使う |

- `saved-state.json` が残っていると前回の UI レイアウトが復元される。
  再現手順を書くときは「初期状態か保存済み状態か」を明記すること
- 任意パスへの書き出し・読み込みは OS ネイティブメニューの `File > 名前を付けて保存...` /
  `File > 開く...` 経由で行う（live モードのみ）。`File > 開く...` で読み込んだ JSON は
  `saved-state.json` に上書き保存され、`self.restart()` で即座に反映される
  （`src/native_menu.rs` / `docs/✅menu-and-footer/native-menu-bar-impl.md` 参照）
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

- スキル一覧上の表示名は `e-station-review`（frontmatter の `name:` も `e-station-review`）
- 組み込みの `/review` とは別物。**組み込み `/review` は使わない**
- review-fix-loop のレビュー段でも、サブエージェントに e-station-review スキルを
  使わせること

