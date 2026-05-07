# kabuステーション venue: 何をどこに追加するか

立花 venue（[../../✅tachibana/README.md](../../✅tachibana/README.md)）と**同じ Python autonomous アーキテクチャ**を踏襲する。Rust 側は IPC enum 拡張のみで、I/O・パース・認証・UI（ログインダイアログ）はすべて Python に閉じる。

## 設計原則（補足）

- venue 文字列キーは IPC 上 `"kabu_station"` で統一（Rust `Venue::KabuStation` と整合）。
- ログイン画面・取引パスワード収集 UI はすべて **Python tkinter subprocess** に統一する（iced modal は使わない）。
- localhost URL は本番 `localhost:18080` / 検証 `localhost:18081` の表記に揃える。
- venue 固有 URL リテラルは `kabusapi_url.py` 1 箇所限定。Rust／`engine-client` には書かない。
- capabilities サマリ（kabu_station）: `requires_local_app=true` / `max_push_symbols=50` / `supports_amend=false` / `requires_trade_password_for_cancel=true`（詳細は §1.1 表、PUSH 50 銘柄の数値ソースは [comparison.md §7 PUSH 配信](./comparison.md#7-push-配信時価ストリーム) に集約 ・ U38）。

## 0. 配置原則（立花計画 [architecture.md §1 配置原則](../../✅tachibana/architecture.md#1-配置原則) と同じ）

| 責務 | 所在 | 備考 |
| :--- | :--- | :--- |
| API パスワード保持 | **Python メモリのみ** | tkinter ダイアログ入力 or `DEV_KABU_API_PASSWORD` env |
| トークン取得 (`POST /token`) | **Python** (`kabusapi_auth.py`) | Rust 関与ゼロ |
| トークン保持 | **Python メモリのみ** | ファイル永続化しない（短命のため） |
| `X-API-KEY` ヘッダ付与 | **Python** | Rust HTTP クライアント不使用 |
| 板パース (`PushBoardSuccess` JSON) | **Python** (`kabusapi_ws.py`) | 既存 `DepthSnapshot` IPC へ詰め替え |
| PUSH 銘柄登録 50 上限管理 | **Python** (`kabusapi_register.py`) | LRU で枠管理、evict 時は IPC `SubscriptionEvicted{symbol}` 送出 |
| 流量制限 token-bucket | **Python** (`kabusapi_ratelimit.py`) | 発注 5/s, 余力 10/s, 情報 10/s |
| エラー判定（HTTP + body `Code`） | **Python** (`kabusapi_auth.check_response`) | `KabuApiError` 派生例外 |
| ログイン画面（tkinter） | **Python** (`kabusapi_login_dialog.py`) | subprocess 隔離、立花と同じ流儀 |
| 取引パスワード（取消／発注）UI | **Python tkinter subprocess** | 都度入力、メモリのみ保持・即削除（U4） |
| ログイン発火判定 | **Python** (`kabusapi_login_flow.py`) | `startup_login` で本体疎通 → トークン取得 |
| バナー文言 | **Python** | Rust UI は `VenueError.message` をそのまま描画 |
| UI フレーム / チャート描画 | **Rust**（既存 iced 流用） | venue 固有コードゼロ |

## 1. ファイル別追加計画

### 1.1 Rust 側（**新設は最小限**）

| ファイル | 変更内容 | 影響範囲 |
| :--- | :--- | :--- |
| [exchange/src/adapter.rs](../../../exchange/src/adapter.rs) | `Venue::KabuStation` バリアント追加。`Exchange::KabuStationStock` 1 バリアントのみ追加（市場細分化＝東証/名証/福証/札証＝は **Phase 3 以降**、現時点では Q-K4 に open-question として残す）。**先物・OP は Phase 3 以降** | enum 網羅 match 全箇所に分岐追加。`Venue::from_str` / `AdapterHandles` dispatch も合わせて更新 |
| [exchange/src/adapter/client.rs](../../../exchange/src/adapter/client.rs) | `AdapterHandles` 構造体に **`kabu_station: Option<Arc<dyn VenueBackend>>` フィールド追加が必須**（U39 / R2-B-L2）。Phase 1 では Python autonomous 経路のため `None` 初期化のみで OK | enum match に並ぶ handles map に kabu キーを追加 |
| [engine-client/src/dto.rs](../../../engine-client/src/dto.rs) | **`venue` フィールドの shape 自体は変更不要**。SCHEMA_MINOR bump の真の変更点は `exchange/src/adapter.rs` の `Venue` enum + `Venue::from_str` + `AdapterHandles` dispatch（U9）。`RequestVenueLogin` / `VenueReady` / `VenueError` / `VenueLoginStarted` / `VenueLoginCancelled` は既存 DTO をそのまま流用し、IPC 文字列として `"kabu_station"` を受理する | schemas.py と同期して SCHEMA_MINOR bump |
| [engine-client/src/capabilities.rs](../../../engine-client/src/capabilities.rs) | `Ready.capabilities.venue_capabilities["kabu_station"]` キー追加。shape は次表参照 | 既存 `venue_capability` ヘルパーで抽出可、変更最小 |

`venue_capabilities["kabu_station"]` キー一覧（C-M4）:

| キー | 型 | 値 | 備考 |
| :--- | :--- | :--- | :--- |
| `requires_local_app` | bool | `true` | kabuステーション本体プロセス必須 |
| `max_push_symbols` | int | `50` | RegisterSet LRU 上限 |
| `supports_amend` | bool | `false` | 訂正は「取消→再発注」シーケンス |
| `requires_trade_password_for_cancel` | bool | `true` | 取消時 `Password` 必須 |

**Rust 側に書かないもの**（立花と同じ禁則）:

- ❌ `data/src/config/kabu.rs` — **作らない**（クレデンシャル永続化は Python 側、トークンは永続化しない）
- ❌ `exchange/src/adapter/kabu.rs` — **作らない**（API 呼出は Python）
- ❌ `src/screen/login_kabu.rs` — **作らない**（tkinter で開く）
- ❌ `localhost:18080` / `localhost:18081` / `kabusapi/websocket` などの URL リテラル — **書かない**（`kabusapi_url.py` 1 箇所限定、F-L1 / U12）
- ❌ `#[cfg(debug_assertions)]` での env 読込 — **書かない**（Python 側で読む）

### 1.2 Python 側（**新規モジュール群**）

すべて [`python/engine/exchanges/`](../../../python/engine/exchanges/) 配下に新設。立花の `tachibana_*.py` と命名・責務分割を 1:1 で揃える。

| ファイル | 責務 | 立花対応 | Phase |
| :--- | :--- | :--- | :--- |
| `kabusapi_url.py` | `BASE_URL_PROD = "http://localhost:18080"` / `BASE_URL_VERIFY = "http://localhost:18081"`（**唯一の所在地**）。`endpoint(path, env)` / `symbol_key(symbol, exchange)` | `tachibana_url.py` | P1 |
| `kabusapi_codec.py` | UTF-8 JSON エンコード/デコード（Shift-JIS 不要なので極小）。`PushBoardSuccess` パース。Shift-JIS バイト列が来た場合は `UnicodeDecodeError` を即時 raise（assert で混入を弾く） | `tachibana_codec.py` | P1 |
| `kabusapi_auth.py` | `fetch_token(api_password, env) -> str`、`check_response(payload, http_status)`、エラー型 (`KabuApiError`, `KabuTokenExpiredError`, `KabuRateLimitError`, `KabuRegisterFullError`, `KabuConnectionError`) | `tachibana_auth.py` | P1 |
| `kabusapi_login_flow.py` | `startup_login()`、debug env 読込、tkinter ヘルパー spawn、本体疎通チェック（`ConnectionRefused` 検知 → 5s backoff × 3 回 → `VenueError{code:"local_app_down"}`、U17） | `tachibana_login_flow.py` | P1 |
| `kabusapi_login_dialog.py` | tkinter サブプロセス（API パスワード 1 フィールド、`prod`/`verify` ラジオ）。stdout に JSON で結果返却 | `tachibana_login_dialog.py` | P1 |
| `kabusapi_ratelimit.py` | `OrderBucket(5)` / `WalletBucket(10)` / `InfoBucket(10)` の token-bucket。`async with bucket:` で取得 | （立花に対応モジュール無し、kabu 固有） | P1 |
| `kabusapi_register.py` | `RegisterSet`：LRU 管理（上限値の数値ソースは [comparison.md §7 PUSH 配信](./comparison.md#7-push-配信時価ストリーム) ・ U38）。`register(symbol, exchange)` / `unregister(...)` / `unregister_all()` / `touch(symbol)`。LRU evict 時は IPC `SubscriptionEvicted{symbol}` 送出（U15） | （立花に対応無し、kabu 固有） | P1 |
| `kabusapi_ws.py` | `connect(env, token, on_message)`：WebSocket 接続、`ping_interval=20`。**再接続後は常に `RegisterSet` 全件を `PUT /register` で再登録**（U6、Q-K1 の検証結果に依らないデフォルト挙動）。**再接続連続失敗の打ち切りは 5s × 5 回**（U22）、上限到達で reconnect ループを抜け `VenueError{code:"local_app_down"}` を再発火 | `tachibana_ws.py` | P1 |
| `kabusapi_rest.py` | REST ラッパー（`board` / `symbol` / `orders` / `positions` / `wallet/*`）。token-bucket 経由。`fetch_board()` 内では `RegisterSet.touch()` を必ず呼ぶ（GET /board は SKILL R6 により自動 PUSH 登録するため、上限集計とズレないように同期）。**満杯時挙動**: 既存登録 hit の場合は touch のみ、新規 + 満杯時は `KabuRegisterFullError` を投げユーザーに登録解除を促す（暗黙 evict しない、U24） | `tachibana.py` + `tachibana_orders.py`（REQUEST 系） | P1（読取） / P2（発注） |
| `kabusapi.py` | venue ファサード。`KabuStationVenue` クラス、IPC `RequestVenueLogin` ハンドラ | `tachibana.py` | P1 |
| `kabusapi_master.py` | （**作らない**）銘柄マスタは `/symbol/{key}` で都度取得、立花の 21MB 一括 DL に相当する処理は kabu には不要 | `tachibana_master.py` | — |
| `kabusapi_file_store.py` | （**作らない**）トークン短命のためファイルキャッシュ無し | `tachibana_file_store.py` | — |

### 1.3 既存ファイルへの組込み

| ファイル | 変更 |
| :--- | :--- |
| [python/engine/__main__.py](../../../python/engine/__main__.py) | venue 起動分岐に kabu を追加。`config_dir` / `cache_dir` は受け取るが kabu はファイル永続化しないため未使用 |
| [python/engine/schemas.py](../../../python/engine/schemas.py) | SCHEMA_MINOR bump、`venue` フィールドに `"kabu_station"` を許可。`VenueError.code` に `"token_expired"` / `"local_app_down"` を予約。`OrderAmendFailed.original_cancelled` を `Option<bool>` 化（`None` = 取消結果不確定: 取消失敗 / ack 待ち中ネットワーク中断 / プロセス停止、U23）。`token_expired` は retry 1 回失敗で発火し、tkinter 再ログインへ誘導する（**自動再ログインは禁止**、U16）。SCHEMA_MINOR bump test には **`VenueError.code` 既存値の venue 横断列挙 assert と、`token_expired` / `local_app_down` の名前空間衝突確認 assert を追加**（U30） |
| [python/engine/replay_session.py](../../../python/engine/replay_session.py) | `LiveSession.login` を **破壊的拡張**: 既存シグネチャ `*, user_id: str \| None, password: str \| None` を `*, venue: str, ...` 形に変更（U32 / R2-B-M1）。**互換策**は次のいずれかを Phase 0 出口時点で確定: (a) 既存立花経路は `venue="tachibana"` を明示的に渡す、または (b) `login_tachibana()` / `login_kabu_station()` の別メソッド分離。Phase 1 では引数追加に留め、立花経路の挙動は不変。`LiveSession` の所在は `replay_session.py`（U3） |
| [python/tests/](../../../python/tests/) | 以下 9 件を `pytest-httpx` (`HTTPXMock`) で実装（U34 / R2-A-M1 / R2-A-L1）。SCHEMA_MINOR test (`test_schema_compat.py`) + `cargo test -p engine-client schema_minor_kabu` も並行で追加（D-M5）。<br>1. `test_kabusapi_auth.py` — `Code=4001005` から `KabuTokenExpiredError` を assert<br>2. `test_kabusapi_auth_logging.py` — `caplog` に **token / API パスワード / 取引パスワード** の 3 種が出力されないことを assert（U26）<br>3. `test_kabusapi_register.py` — 上限 + 1 件目で `KabuRegisterFullError`、LRU evict 時に `SubscriptionEvicted` 送出を assert<br>4. `test_kabusapi_ws.py` — 再接続後の全件 re-register、SJIS bytes 拒否、**5s × 5 回連続失敗で reconnect ループ打ち切り → `VenueError{code:"local_app_down"}`** (U22) を assert<br>5. `test_kabusapi_ratelimit.py` — `OrderBucket(5)` が 6 件目でブロックすることを assert<br>6. `test_kabusapi_login_flow.py` — `local_app_down` 5s × 3 回 retry、`DEV_KABU_API_PASSWORD` 設定時の tkinter 非起動<br>7. `test_kabusapi_codec.py` — UTF-8 JSON encode/decode、SJIS バイト混入で `UnicodeDecodeError`<br>8. `test_kabusapi_rest.py` — `fetch_board()` が `RegisterSet.touch()` を呼ぶこと、満杯時の `KabuRegisterFullError`<br>9. `test_live_session_kabu.py` — `LiveSession.login(venue="kabu_station")` が `VenueReady{venue:"kabu_station"}` を発火<br>**capabilities invariant test**: `test_kabusapi_capabilities.py::test_capabilities_max_push_symbols_matches_register_set`（`max_push_symbols=50` と `RegisterSet.MAX = 50` の一致 assert、K1 / K4 横断、U27 / R2-A-M5 / R2-C-L1） |

### 1.4 ドキュメント

| ファイル | 内容 |
| :--- | :--- |
| `docs//✅kabusapi/spec.md`（後続） | ゴール・非ゴール・スコープ。立花 spec.md を雛形にする |
| `docs//✅kabusapi/architecture.md`（後続） | プロセス境界・起動シーケンス。立花 architecture.md を雛形にする |
| `docs//✅kabusapi/data-mapping.md`（後続） | kabu の `PushBoardSuccess` ↔ 既存 `DepthSnapshot` IPC マッピング。立花計画には対応文書なし、kabu 固有の新規ドキュメント。雛形は立花 [architecture.md §7 PUSH 配信（時価ストリーム）](../../✅tachibana/architecture.md#7-push-配信時価ストリーム) から流用 |
| `docs//✅kabusapi/implementation-plan.md`（後続） | Phase 0/1/2 の受入条件・テスト戦略 |
| `docs//✅kabusapi/open-questions.md`（後続） | 再接続後の銘柄再登録要否（仕様確認）、`/orders` polling vs PUSH の選択、ほか |
| `docs//✅kabusapi/invariant-tests.md` | **Phase 0 では雛形（K-task ↔ 不変条件 ID の対応表のみ）を作成**、各 K-task 詳細は Phase 1 内で追記（U33 / R2-A-H1 / R2-A-H3 / U13）。`local_app_down` の早朝強制ログアウト時刻帯の分岐定義（リトライ後 `local_app_down` を ERROR でなく INFO 扱いとする時刻帯、U25 / R2-C-M2）も本ファイルに記述 |
| `docs//✅kabusapi/runbook.md`（**Phase 4 着手時作成**、U37 / R2-A-L3） | 本番事故対応・取消手順・kabuステーション本体ダウン時のオペレーション |

## 2. フェーズ分割

### Phase 0（計画フェーズ、本ドキュメント群）

- [x] 立花 SKILL / kabu SKILL の対比（[comparison.md](./comparison.md)）
- [x] 追加計画（本ファイル）
- [ ] **Phase 0 出口条件**: spec.md / architecture.md / data-mapping.md / implementation-plan.md / open-questions.md の **5 文書を Phase 1 着手前に必ず追補**（U7 / U33 / R2-A-H1 / R2-A-H3）。`invariant-tests.md` は Phase 0 では **雛形（K-task ↔ 不変条件 ID の対応表のみ）** を作成し、各 K-task の詳細 assert は Phase 1 内で追記する（§5 checklist と整合、U33）

### Phase 1（リードオンリー統合、検証環境のみ）

**ゴール**: 検証 `localhost:18081` に接続し、kabu の板情報・気配・直近約定をチャートに表示できる。発注は不可。

| Task | 内容 | 完了条件（テストファイル名 + 代表 assert を併記） |
| :--- | :--- | :--- |
| ✅ K1 | Rust enum 拡張（`Venue::KabuStation` / `Exchange::KabuStationStock`）+ `Venue::from_str` + `AdapterHandles` dispatch + **`AdapterHandles.kabu_station: Option<Arc<dyn VenueBackend>>` フィールド追加**（U39 / R2-B-L2、Phase 1 では `None` 初期化のみ） | `cargo check` 通過、enum 網羅 match 全箇所が compile。`test_kabusapi_capabilities.py::test_capabilities_max_push_symbols_matches_register_set` で `max_push_symbols=50` と `RegisterSet.MAX = 50` の一致を assert（U27 / R2-C-L1） |
| ✅ K2 | `kabusapi_url.py` / `kabusapi_codec.py` / `kabusapi_auth.py` 着地 + 単体テスト。**ログマスクは token + API パスワード + 取引パスワードの 3 種**（U26 / R2-C-M3） | `pytest python/tests/test_kabusapi_auth.py` グリーン。`test_kabusapi_auth.py` で `Code=4001005` **および `Code=4001001`** の両方から `KabuTokenExpiredError` を assert（U35 / R2-A-M2）。`test_kabusapi_auth_logging.py` で `caplog` に **token / API パスワード / 取引パスワード** 3 種いずれの文字列も出力されないことを assert（U26） |
| ✅ K3 | `kabusapi_login_flow.py` + tkinter ダイアログ。debug env 自動ログイン経路。本体プロセス落ち（TCP refused）の検知・復帰。**早朝強制ログアウト時刻帯（Q-K3 確定までの暫定窓）はリトライ後の `local_app_down` を ERROR でなく INFO 扱いとし、`invariant-tests.md` の分岐定義に従う**（U25 / R2-C-M2） | flowsurface debug 起動 → `DEV_KABU_API_PASSWORD` 設定 → `/token` 取得ログ確認。`test_kabusapi_login_flow.py::test_tcp_refused_three_retries_then_local_app_down` で 5s × 3 回後の `VenueError{code:"local_app_down"}` を assert（U17）。同 test で `DEV_KABU_API_PASSWORD` 設定時に tkinter ダイアログを spawn しないことも assert。早朝時刻帯分岐の test も同ファイルに追加 |
| ✅ K4 | `kabusapi_ratelimit.py` + `kabusapi_register.py` 単体テスト。**capabilities invariant test を K1 と横断で共有**（U27） | `test_kabusapi_register.py::test_register_51st_raises_full` で `KabuRegisterFullError`、`test_kabusapi_register.py::test_lru_evict_emits_subscription_evicted` で IPC `SubscriptionEvicted{symbol}` 送出を assert（U15）。`test_kabusapi_ratelimit.py::test_order_bucket_blocks_at_6th_req`。`test_kabusapi_capabilities.py::test_capabilities_max_push_symbols_matches_register_set`（K1 と共有、U27 / R2-C-L1） |
| ✅ K5 | `kabusapi_ws.py`：WebSocket 接続・板パース・`DepthSnapshot` IPC 送出。再接続後は **常に `RegisterSet` 全件 re-register**（U6）。**再接続連続失敗の打ち切り条件は 5s × 5 回**（U22 / R2-C-H1）、上限到達で reconnect ループを抜け `VenueError{code:"local_app_down"}` 再発火 | `test_kabusapi_ws.py::test_reconnect_reregisters_all_symbols` / `test_kabusapi_ws.py::test_decode_rejects_sjis_bytes` / `test_kabusapi_ws.py::test_reconnect_aborts_after_5_consecutive_failures`（U22）。チャートに板表示が出ること |
| ✅ K6 | `kabusapi_rest.py`（読取のみ）：`/board` / `/symbol` / `/orders` / `/positions` / `/wallet/*`。`fetch_board()` で `RegisterSet.touch()` 呼出 | レスポンスを既存 IPC 型へマッピング。`test_kabusapi_rest.py::test_fetch_board_touches_register_set` |
| K7 | IPC `Venue::KabuStation` ライフサイクル（`RequestVenueLogin` / `VenueReady` / `VenueError`）の E2E | `test_live_session_kabu.py::test_login_kabu_station_emits_venue_ready` で `LiveSession.login(venue="kabu_station")` が `VenueReady{venue:"kabu_station"}` を受信することを assert（U1） |
| K8 | URL リテラル lint タスク（U12 / U28 / R2-A-M4 / R2-C-L2）：正規表現を **`(http\|ws)://localhost:1808[01]\|/kabusapi/(websocket\|register\|sendorder\|board)`** に拡張し、検査範囲を `src/` / `exchange/` / `engine-client/` / **`python/engine/`**（ただし `python/engine/exchanges/kabusapi_url.py` は除外）に拡大。CI で空一致を検証 | CI 上で zero-match を assert する step を追加。除外対象 1 ファイル以外で 1 件でも検出されたら fail |
| K8.5 | `pytest -m demo_kabu` ジョブ（HTTPXMock のみ、本物は叩かない）を `.github/workflows/kabu-mock.yml` に追加 | CI グリーン。job 名 `pytest-kabu-mock`、コマンド `pytest -m demo_kabu python/tests/test_kabusapi_*.py` |

<!-- K1 実装メモ（2026-05-07） -->
> **K1 実装メモ**: `Venue::KabuStation` / `Exchange::KabuStationStock` を追加。
> 網羅 match の更新箇所は合計 **6 箇所**:
> 1. `exchange/src/adapter.rs` — `Venue::Display`
> 2. `exchange/src/adapter.rs` — `Exchange::market_type()`
> 3. `exchange/src/adapter.rs` — `Exchange::venue()`
> 4. `exchange/src/adapter.rs` — `Exchange::default_quote_currency()`（venue match）
> 5. `exchange/src/adapter.rs` — `Exchange::supports_kline_timeframe()`（venue match）
> 6. `exchange/src/adapter/client.rs` — `AdapterHandles::set_backend()` / `get_backend_arc()`
> 加えて `src/screen/dashboard/tickers_table.rs` と `src/style.rs` の main crate 側 match 2 箇所を更新。
> `SCHEMA_MINOR` を 17 → 18 に bump（`python/engine/schemas.py` と `engine-client/src/lib.rs` 同期）。
> `VenueError.code` docstring に `"token_expired"` / `"local_app_down"` を追記（型制約なし）。
> `engine-client/src/capabilities.rs` に `test_kabu_station_max_push_symbols` テスト追加。
> `engine-client/tests/schema_v2_4_nautilus.rs` の stale な `assert_eq!(SCHEMA_MINOR, 14)` を 18 に更新（元々 17 で既に失敗していた）。
> `cargo check --workspace` / `cargo clippy --workspace -- -D warnings` / `cargo fmt --check` / `cargo test --workspace` 全通過。

<!-- K2 実装メモ（2026-05-07） -->
> **K2 実装メモ**: `kabusapi_url.py` / `kabusapi_codec.py` / `kabusapi_auth.py` の 3 モジュールを新設。
> 新設ファイル:
> - `python/engine/exchanges/kabusapi_url.py` — `BASE_URL_PROD` / `BASE_URL_VERIFY` / `endpoint()` / `symbol_key()` / `ws_url()`。URL リテラルの唯一の所在地（R1）。
> - `python/engine/exchanges/kabusapi_codec.py` — UTF-8 JSON `encode()` / `decode()`。SJIS バイト列は `UnicodeDecodeError`（INV-K2-SJIS-REJECT）。`SIDE_BUY="2"` / `SIDE_SELL="1"` の regression guard（INV-K2-SIDE-BUY-2）。
> - `python/engine/exchanges/kabusapi_auth.py` — `KabuApiError` 派生 5 種 + `fetch_token()` + `check_response()`。ログマスク: token 末尾 4 文字のみ出力（INV-K2-NO-LOG-SECRET）。
> テストファイル 3 件（`test_kabusapi_auth.py` / `test_kabusapi_auth_logging.py` / `test_kabusapi_codec.py`）を新設。
> `pytest.ini` に `demo_kabu` マーカーを追加。
> `uv run pytest python/tests/test_kabusapi_auth.py python/tests/test_kabusapi_auth_logging.py python/tests/test_kabusapi_codec.py -v` → 14 passed。

<!-- K3 実装メモ（2026-05-07） -->
> **K3 実装メモ**: `kabusapi_login_flow.py` / `kabusapi_login_dialog.py` の 2 モジュールを新設。
> 新設ファイル:
> - `python/engine/exchanges/kabusapi_login_flow.py` — `startup_login()` / `_spawn_dialog()` / `_is_morning_logout_window()`。TCP 拒否時 `_RETRY_DELAY_S=5.0` × `_MAX_RETRIES=3` でリトライ後 `KabuConnectionError` を raise。早朝時刻帯（JST 4〜9 時）は `logger.info`、それ以外は `logger.error` で記録（INV-K3-MORNING）。`DEV_KABU_API_PASSWORD` 設定 + `dev_login_allowed=True` で tkinter ダイアログをスキップ（INV-K3-NO-TKINTER）。
> - `python/engine/exchanges/kabusapi_login_dialog.py` — tkinter サブプロセス。API パスワード入力フィールド + `verify`/`prod` ラジオボタン。stdout に JSON で結果返却。`python -m engine.exchanges.kabusapi_login_dialog` で起動可能。
> テストファイル 1 件（`test_kabusapi_login_flow.py`）を新設。
> `uv run pytest python/tests/test_kabusapi_login_flow.py -v` → 4 passed。

<!-- K4 実装メモ（2026-05-07） -->
> **K4 実装メモ**: `kabusapi_ratelimit.py` / `kabusapi_register.py` の 2 モジュールを新設。
> 新設ファイル:
> - `python/engine/exchanges/kabusapi_ratelimit.py` — `TokenBucket` クラス（`async with bucket:` で 1 トークン消費）。`OrderBucket()` = 5 req/sec、`WalletBucket()` = 10 req/sec、`InfoBucket()` = 10 req/sec（INV-K4-RATELIMIT / SKILL R5）。
> - `python/engine/exchanges/kabusapi_register.py` — `RegisterSet` クラス（`OrderedDict` ベース LRU）。`MAX_SYMBOLS = 50`（comparison.md §7 一次ソース）。`register()` で 51 件目に `KabuRegisterFullError`（暗黙 evict なし、Q-K5 決定 / U24）。`evict_lru()` で `on_evict` コールバック呼出（INV-K4-LRU-EVICT）。`RegisterSet.MAX = 50` でクラス属性公開（INV-K1-CAP）。
> テストファイル 3 件（`test_kabusapi_ratelimit.py` / `test_kabusapi_register.py` / `test_kabusapi_capabilities.py`）を新設。
> `uv run pytest python/tests/test_kabusapi_ratelimit.py python/tests/test_kabusapi_register.py python/tests/test_kabusapi_capabilities.py -v` → 9 passed。

<!-- K5 実装メモ（2026-05-07） -->
> **K5 実装メモ**: `kabusapi_ws.py` を新設。
> 新設ファイル:
> - `python/engine/exchanges/kabusapi_ws.py` — `connect()` 非同期関数。`websockets.connect(ping_interval=20, ping_timeout=10)` でループ。再接続後は `RegisterSet.all_symbols()` を `put_register` で全件再登録（U6 / INV-K5-RECONNECT-REREG）。`bytes` フレームは `decode("utf-8")` で SJIS 拒否（INV-K2-SJIS-REJECT 整合）。`ConnectionRefusedError` / `OSError` / 汎用例外いずれも連続失敗カウントに加算し、`_MAX_RECONNECT_ATTEMPTS=5` 到達で `KabuConnectionError` を raise（U22 / INV-K5-ABORT）。sleep 前に `asyncio.sleep(_RECONNECT_DELAY_S)` 呼出（モックでテスト可）。
> テストファイル 1 件（`test_kabusapi_ws.py`）を新設。
> `uv run pytest python/tests/test_kabusapi_ws.py -v` → 4 passed。

<!-- K6 実装メモ（2026-05-07） -->
> **K6 実装メモ**: `kabusapi_rest.py` を新設（読取専用）。
> 新設ファイル:
> - `python/engine/exchanges/kabusapi_rest.py` — `KabuRestClient` クラス。`fetch_board()` / `fetch_symbol()` / `fetch_orders()` / `fetch_positions()` / `fetch_wallet_cash()` / `fetch_wallet_margin()` を実装。
> - `fetch_board()` 内で `RegisterSet.touch()` / `RegisterSet.register()` を呼び、GET /board の自動 PUSH 登録と同期（R6 / INV-K6-TOUCH）。
> - 既存銘柄 hit なら `touch()` のみ、新規 + 満杯時は `KabuRegisterFullError`（INV-K6-REST-FULL / U24）。
> - 情報系は `InfoBucket`（10 req/sec）、余力系は `WalletBucket`（10 req/sec）経由（R5）。
> - 全リクエストに `X-API-KEY` ヘッダを付与（R3）。
> - URL リテラルは `kabusapi_url.py` の `endpoint()` / `symbol_key()` のみ使用（R1）。
> テストファイル: `python/tests/test_kabusapi_rest.py` — 3 件 passed。
> `uv run pytest python/tests/test_kabusapi_rest.py -v` → 3 passed。

**Phase 1 の非ゴール**:

- 発注・取消・訂正
- 先物・OP・OCO
- 本番接続（`KABU_ALLOW_PROD=1` ガードのみ用意）
- 24h 連続稼働の安定性検証（kabuステーション本体の早朝強制ログアウト仕様による中断は許容）
- 早朝強制ログアウトのバナー文言は spec.md（後続）で確定

### Phase 2（発注、検証環境のみ） ※`DEV_KABU_TRADE_PASSWORD` 等の取引パスワード env を Phase 2 着手前に予約決定

| Task | 内容 |
| :--- | :--- |
| K9 | `POST /sendorder`（株式現物・信用）。第二パスワード（取引パスワード）は **Python tkinter subprocess** で都度取得、メモリのみ保持・即削除（U4） |
| K10 | `PUT /cancelorder`。取消は `Password` フィールド必須。取引パスワードは tkinter subprocess で都度取得 |
| K11 | 訂正は **「取消 → 再発注」シーケンス**で実装（`kabusapi_rest.amend_order(order_id, ...)` ヘルパー内で 2 ステップ）。**`OrderAmendFailed.original_cancelled` は `Option<bool>` の三値**（U23 / R2-C-H2）: `Some(true)`=取消成功 + 再発注失敗、`Some(false)`=取消失敗（原注文継続）、`None`=取消結果不確定（取消失敗 / ack 待ち中ネットワーク中断 / プロセス停止）。自動再試行はしない（U14）。完了条件: `test_amend_order_rolls_back_on_resend_failure`（`Some(true)`）/ `test_amend_order_cancel_failed_keeps_original`（`Some(false)`）/ `test_amend_order_indeterminate_on_network_drop`（`None`） |
| K12 | `POST /sendoco`（OCO）|
| K13 | 発注 E2E（検証環境のみ）。`KABU_ALLOW_PROD=1` でも検証ポートを優先する誤爆ガードを Python で多層化。完了条件: `test_prod_guard_requires_both_envs` |

### Phase 3（先物・OP・市場細分化）

`/sendorder/future` / `/sendorder/option` / `/wallet/future` / `/wallet/option` / `/symbolname/*` の対応。`Exchange::KabuStation*` 列挙子を市場細分化（東証 = 1 / 名証 = 3 / 福証 = 5 / 札証 = 6）および先物 OP 系に拡張。**先物・OP は Phase 3 以降**（U18）。

### Phase 4（本番接続）

`KABU_ALLOW_PROD=1` 解禁。本番 `localhost:18080` で実弾発注テスト（最小 1 単元）。`docs//✅kabusapi/runbook.md`（事故対応・取消手順）を整備。

## 3. 立花計画から流用する既存資産

| 既存資産 | 流用方法 |
| :--- | :--- |
| `engine_client::dto::RequestVenueLogin` 等 | venue 文字列を `"kabu_station"` にするだけで再利用（DTO 型変更なし、U1/U9）[^cmd-variant] |
| `engine_client::capabilities::venue_capability` | `venue_capabilities["kabu_station"]` をパースする経路は既存ヘルパーをそのまま使う（U8: crate dir = `engine-client/`、Rust use path = `engine_client::...`） |
| `VenueState` FSM ([src/venue_state.rs](../../../src/venue_state.rs)) | venue 別 state machine。kabu 用 state を 1 セット追加 |
| `ProcessManager.apply_after_handshake` | **venue-agnostic（既存の ready cache クリア処理）。`Venue::KabuStation` も既存経路を素通し、追加コード不要**（U10）[^pm-inner] |
| `LiveSession.login()` ([python/engine/replay_session.py](../../../python/engine/replay_session.py)) | venue パラメータ追加で kabu 対応（U3）。**シグネチャは破壊的拡張（U32 / R2-B-M1）**: 既存 `*, user_id, password` → `*, venue: str, ...` 形へ。互換策は §1.3 参照 |

[^cmd-variant]: `engine_client::dto::RequestVenueLogin` は **`Command` 列挙子のバリアント**（`Command::RequestVenueLogin { request_id, venue }`）として定義されている（U40 / R2-B-L3）。
[^pm-inner]: `ProcessManager.apply_after_handshake` の **内部実装は `apply_after_handshake_inner`**、`apply_after_handshake_with_timeout` は **test feature gate 配下**（U41 / R2-B-L1）。
| iced UI バナー枠（`VenueError.message` 描画） | 文言ソースを Python 側に置く運用は同じ |
| `pytest-httpx` (`HTTPXMock`) パターン | kabu テストもそのまま流用 |

## 4. リスクと未確定事項

実装着手前に [open-questions.md](./open-questions.md) へ起票して解消する:

- **Q-K1**（サーバ仕様の確認質問として残す）: PUSH WebSocket 切断後、`PUT /register` 銘柄リストはサーバ側で保持されるか？（[ptal/push.html](../../../.claude/skills/kabusapi/ptal/push.html) 及び [comparison.md §7. PUSH 配信](./comparison.md#7-push-配信) で要確認）。**実装側のデフォルト挙動は U6 により「再接続後は常に `RegisterSet` 全件を再登録」で固定**（Q-K1 の検証結果に依らないフォールバック）
- **Q-K2**: `/orders` の約定通知は polling と PUSH のどちらが正か？ kabu の WebSocket は登録銘柄の時価のみで、約定は `/orders` polling かもしれない（OpenAPI 確認）
- **Q-K3**: kabuステーション本体の早朝強制ログアウト時刻は `ptal/howto.html` 記載か？ 24h E2E では再ログイン誘導の自動化は禁止し、ユーザー誘導バナーで返す
- **Q-K4**（open-question 維持、Phase 1 では作らない）: `Exchange` enum で「東証 = 1 / 名証 = 3 / 福証 = 5 / 札証 = 6」の市場細分化が必要か？（kabu OpenAPI は粒度なし）。Phase 1 は `Exchange::KabuStationStock` 1 バリアントのみで進行（U2）。細分化は Phase 3 で対応
- **Q-K5**【決定済み・U24 / R2-C-M1】: 板情報 GET (`/board`) が**自動的に PUSH 登録を発火する**仕様（SKILL R6）と `RegisterSet` 集計の整合 → `kabusapi_rest.fetch_board()` 内で **既存登録 hit なら `touch()` のみ、新規 + 満杯時は `KabuRegisterFullError` を投げユーザーに登録解除を促す**（暗黙 evict しない、U24）。§1.2 `kabusapi_rest.py` 行に明記済
- **Q-K6**: Windows 限定なので CI demo ジョブは構築不可。代わりに **HTTPXMock + WebSocket mock** での疑似 E2E をどこまで作るか
- **Q-K7**: ファイルキャッシュを作らない方針だが、ユーザー操作テンポ（毎回 tkinter）を考えるとプロセスメモリ寿命中（flowsurface 起動中）はトークンを保持する必要あり。プロセス再起動跨ぎは諦める（毎回ログイン）
- **Q-K8**: 立花の `MarketKind::Stock` を流用するか、kabu 用に別 `MarketKind::JPStock` を作るか（東証だけなら共有可能）

## 5. Phase 1 着手前のチェックリスト

- [ ] 立花計画 [architecture.md §2 Python 自律ログイン方式](../../✅tachibana/architecture.md#2-python-自律ログイン方式session-file-cache-適用後) を読み、Python autonomous の起動シーケンスを把握
- [ ] [/.claude/skills/kabusapi/SKILL.md](../../../.claude/skills/kabusapi/SKILL.md) の R1〜R10 を再読
- [ ] [comparison.md](./comparison.md) §10 sanity check 表 10 項目を頭に入れる
- [ ] kabuステーション本体（Windows）の API オプション申込・本体ログイン・API パスワード設定が完了している環境を 1 台用意
- [ ] `pytest-httpx` パターンを既存 [python/tests/test_binance_rest.py](../../../python/tests/test_binance_rest.py) で確認
- [ ] IPC SCHEMA_MINOR bump の手順（schemas.py + engine-client/src/lib.rs 同期）を `/ipc-schema-check` で確認
- [ ] `invariant-tests.md` を新規作成（U13）
