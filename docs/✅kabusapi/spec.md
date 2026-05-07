# kabuステーション venue 統合: 仕様 (Phase 1)

## 1. ゴール

本アプリの venue として三菱UFJ eスマート証券（旧 auカブコム）**kabuステーション API（v1.5）** を追加し、
**検証環境 `localhost:18081` に接続して kabu の板情報・気配・直近約定をチャートに表示できる状態**
を成立させる。Phase 1 は**リードオンリー（閲覧のみ）**。

## 2. スコープ

### 2.1 含めるもの（Phase 1 = MVP）

- **新 venue `Venue::KabuStation`** および `Exchange::KabuStationStock`（東証のみ、Phase 1）
- **IPC venue キー `"kabu_station"`** で `RequestVenueLogin` / `VenueReady` / `VenueError` を受理
- **Python モジュール群** `python/engine/exchanges/kabusapi*.py`（詳細は plan.md §1.2）
  - `kabusapi_url.py` — URL 定数（BASE_URL_PROD / BASE_URL_VERIFY）の唯一の所在地
  - `kabusapi_auth.py` — `POST /token` トークン取得・エラー判定
  - `kabusapi_codec.py` — UTF-8 JSON encode/decode（Shift-JIS 拒否 assert）
  - `kabusapi_login_flow.py` — startup_login、debug env 自動ログイン、本体疎通チェック
  - `kabusapi_login_dialog.py` — tkinter サブプロセス（API パスワード収集）
  - `kabusapi_ratelimit.py` — token-bucket（発注 5/s、余力 10/s、情報 10/s）
  - `kabusapi_register.py` — RegisterSet LRU 管理（50 銘柄上限）
  - `kabusapi_ws.py` — WebSocket 接続・板パース・DepthSnapshot IPC 送出
  - `kabusapi_rest.py` — REST 読取（`/board` / `/symbol` / `/orders` / `/positions` / `/wallet/*`）
  - `kabusapi.py` — KabuStationVenue ファサード
- **PUSH WebSocket 再接続**: 5s × 5 回打ち切り、再接続後は RegisterSet 全件 re-register
- **capabilities 追加**: `Ready.capabilities.venue_capabilities["kabu_station"]` キー
- **テスト**: `pytest-httpx` (HTTPXMock) + WebSocket mock で全 9 件のテストファイル（plan.md §1.3）
- **CI**: `.github/workflows/kabu-mock.yml`（`pytest -m demo_kabu`、HTTPXMock のみ）
- **URL lint**: Rust/Python の `kabusapi_url.py` 以外への URL リテラル漏出を CI で阻止

### 2.2 含めないもの（Phase 1 非ゴール）

- 発注・取消（`POST /sendorder` / `PUT /cancelorder`）
- 訂正（kabu API に訂正エンドポイントは無い。取消→再発注で代替するが Phase 2）
- 先物・OP・OCO
- 本番接続（`KABU_ALLOW_PROD=1` ガードのみ用意）
- 24h 連続稼働の安定性検証（kabuステーション本体の早朝強制ログアウト仕様による中断は許容）
- 早朝強制ログアウトのバナー文言確定（invariant-tests.md の分岐定義で暫定窓を定義）
- 市場細分化（東証 / 名証 / 福証 / 札証の区別は Phase 3 以降）
- `kabusapi_master.py` — 銘柄マスタは `/symbol/{key}` で都度取得、一括 DL 不要
- `kabusapi_file_store.py` — トークン短命のためファイルキャッシュ無し

### 2.3 安全不変条件（必ず守る）

1. **本番接続は `KABU_ALLOW_PROD=1` opt-in 必須**。デフォルトは検証環境（`localhost:18081`）固定。
   Python 側で多層ガードを敷き、Phase 1 では実弾発注経路を一切作らない。
2. **クレデンシャル・トークンは Python メモリのみ保持**。Rust 経路に流さない。ファイル永続化しない。
3. **URL リテラルは `kabusapi_url.py` 1 箇所**。Rust / engine-client / その他 Python ファイルに書かない。
4. **ログイン UI は Python tkinter サブプロセス**。Rust にダイアログコードを書かない。

## 3. 非機能要件

### 3.1 セキュリティ

- API パスワード・トークンはログに生で出力しない（`***` マスクまたは末尾 4 文字のみ）
- `caplog` に token / API パスワード / 取引パスワードが出力されないことをテストで確認
- `DEV_KABU_API_PASSWORD` は Python 側 `kabusapi_login_flow.py` のみが読む
- release ビルドでは自動ログインを禁止（Python 側でガード）

### 3.2 接続・再接続

- kabuステーション本体（Windows）が起動していることが前提（TCP 接続失敗 = `ConnectionRefusedError`）
- 本体プロセス落ち: `ConnectionRefusedError` → 5s backoff × 3 回 → `VenueError{code:"local_app_down"}`
- WebSocket 再接続失敗: 5s × 5 回連続失敗 → `VenueError{code:"local_app_down"}` 再発火
- 再接続後は RegisterSet 全件を `PUT /register` で再登録（サーバ側保持に依存しない）

### 3.3 PUSH 銘柄登録上限

- PUSH 銘柄は REST/PUSH 合算 **50 銘柄上限**（comparison.md §7 が数値の一次ソース）
- RegisterSet の LRU で枠管理。51 件目は `KabuRegisterFullError` を投げユーザーに通知
- `GET /board` が自動的に PUSH 登録を発火するため、`fetch_board()` 内で `RegisterSet.touch()` を必ず呼ぶ

### 3.4 流量制限

- 発注系: 5 req/sec (`OrderBucket`)
- 余力系: 10 req/sec (`WalletBucket`)
- 情報系: 10 req/sec (`InfoBucket`)
- `async with bucket:` で事前抑制が原則。サーバから `4002006` が返ったらバックオフリトライ

## 4. 受け入れ条件（Phase 1 完了の定義）

1. `cargo check --workspace` / `cargo clippy --workspace -- -D warnings` / `cargo fmt --check` 全通過
2. `cargo test --workspace` 全通過（kabu 関連 Rust test を含む）
3. `uv run pytest python/tests/test_kabusapi_*.py python/tests/test_live_session_kabu.py` 全通過
4. plan.md §1.3 で列挙された 9 つのテストファイルが実在し、各代表 assert を満たす
5. K8.5 URL リテラル lint が CI（`.github/workflows/kabu-mock.yml`）で強制されている
6. `LiveSession.login(venue="kabu_station", ...)` が `VenueReady` を発火する E2E が pass
7. WS 再接続 5s × 5 回打ち切り / RegisterSet 全件 re-register / SubscriptionEvicted 通知等の
   不変条件テストが pass
