# 立花証券統合: 実装計画

親計画 [docs/plan/✅python-data-engine/implementation-plan.md](../✅python-data-engine/implementation-plan.md) のフェーズ 6 完了後、または並行で着手する追加トラックとして位置づける。

## フェーズ T0: 仕様凍結とスキーマ拡張（1〜2 日）

**ゴール**: IPC スキーマに立花対応の差分を入れ、Rust / Python 両側で型ビルドが通る。

- [ ] `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` を [exchange/src/adapter.rs](../../../exchange/src/adapter.rs) に追加
- [ ] `TickerInfo` に `lot_size: Option<u32>` と `quote_currency: &'static str` を追加（既存 venue は `None` / `"USDT"` などにマッピング）
- [ ] **既存 ticker バリデーションの確認**: `Ticker::new` 等が `130A0` のような英字混在 5 桁コードを許容するか検査。許容しない場合は本フェーズで緩和する（`MarketKind::Stock` のみ英数字許容など）
- [ ] `engine-client` DTO に下記を追加し `schema_minor` を bump:
  - `Command::SetVenueCredentials`
  - `EngineEvent::VenueReady` / `EngineEvent::VenueCredentialsRefreshed`
  - `Ready.capabilities.venue_capabilities` のサブ構造
- [ ] [docs/plan/✅python-data-engine/schemas/commands.json](../✅python-data-engine/schemas/commands.json) / `events.json` / `CHANGELOG.md` 更新
- [ ] **SKILL.md の同期**: `.claude/skills/tachibana/SKILL.md` の R3/R4/R6/R10 / §Rust 実装の既存ヘルパー が架空のファイル参照（`exchange/src/adapter/tachibana.rs` 等）を含むため、本フェーズで決まった**実在パスへ**置換または「将来実装予定」と但し書き
- [ ] **受け入れ**: `cargo check --workspace` 成功、Python `pytest` の既存スイート緑

## フェーズ T1: Python ユーティリティ（2〜3 日）

**ゴール**: 立花 API を叩く下回りが単体で揃う。サーバ通信なしの単体テストでカバレッジ 80%。

- [ ] `python/engine/exchanges/tachibana_url.py`:
  - `build_request_url(base, json_obj)` — REQUEST 用、`?{JSON 文字列}` 形式（SKILL.md R2）
  - `build_event_url(base, params: dict)` — EVENT 用、`?key=value&...` 形式（R2 例外、`p_evt_cmd`/`p_eno`/`p_rid`/`p_board_no`/`p_gyou_no`/`p_issue_code`/`p_mkt_code`）
  - `func_replace_urlecnode(s)` — 30 文字置換（R9、`e_api_login_tel.py` サンプル出力と一致）
- [ ] `python/engine/exchanges/tachibana_codec.py`:
  - Shift-JIS デコード（`decode_response_body`）
  - `parse_event_frame(data: str) -> list[tuple[str, str]]`（`^A^B^C` / `\n` 分解）
  - `deserialize_tachibana_list(value)` — 空配列が `""` で返るケースの正規化（SKILL.md R8）
- [ ] `python/engine/exchanges/tachibana_master.py` — `CLMEventDownload` ストリームパーサ（チャンク境界・`CLMEventDownloadComplete` 終端）
- [ ] `p_no` 採番ヘルパ（asyncio Lock + Unix 秒初期化の単調 int、SKILL.md R4）と `current_p_sd_date()`（JST 固定）
- [ ] エラー判定ヘルパ `check_response(payload) -> None | TachibanaError`（[SKILL.md R6](../../../.claude/skills/tachibana/SKILL.md)、`p_errno` 空文字＝正常を含む）
- [ ] **受け入れ**: 上記モジュールを単体テストでカバー、サンプルレスポンス（`samples/e_api_login_tel.py/e_api_login_response.txt` ほか）から期待値抽出ができる。REQUEST URL と EVENT URL の差を別テストで検証

## フェーズ T2: 認証フローと session 管理（2 日）

**ゴール**: `CLMAuthLoginRequest` 経由でデモ環境に対しログインできる。

- [ ] `python/engine/exchanges/tachibana_auth.py`
  - `login(user_id, password, is_demo) -> TachibanaSession`
  - `validate_session(session) -> bool`（`CLMMfdsGetMasterData` 軽量 1 件で生存確認）
  - 二段エラー判定 + `sKinsyouhouMidokuFlg=="1"` で `UnreadNoticesError`
  - レスポンスから `sZyoutoekiKazeiC`（譲渡益課税区分）を `TachibanaSession` に保持（Phase 2 発注時に流用）
- [ ] mock サーバテスト（`pytest-httpx` または `respx`）で正常系・異常系（`p_errno=-62` / `=2` / 認証失敗）
- [ ] **受け入れ**: `pytest -m demo_tachibana` で実 demo 環境ログイン成功（手動電話認証済みアカウント前提）

## フェーズ T3: クレデンシャル受け渡し配線（2 日）

**ゴール**: Rust が keyring からクレデンシャルを取り出し、Python が `VenueReady` を返すまで往復する。

- [ ] `data/src/config/tachibana.rs` 新設（**現リポジトリには存在しないことを確認済み**。`data/src/config/proxy.rs` の keyring 実装パターンを参考にする）:
  - `TachibanaCredentials { user_id, password: SecretString, second_password: SecretString, is_demo }`
  - `TachibanaSession { url_request, url_master, url_price, url_event, url_event_ws, expires_at_ms, zyoutoeki_kazei_c }`
  - keyring 読み書き
- [ ] [src/screen/login.rs](../../../src/screen/login.rs) に立花フォーム追加（または別タブ）。`#[cfg(debug_assertions)]` で `DEV_TACHIBANA_*` env 自動入力
- [ ] [engine-client/src/backend.rs](../../../engine-client/src/backend.rs) で `SetVenueCredentials` 送信パスを実装
- [ ] [src/main.rs](../../../src/main.rs) 起動シーケンスに「keyring 読込 → SetVenueCredentials → VenueReady 待ち」を追加
- [ ] **受け入れ**: debug ビルドで `.env` 設定 → 起動 → ログ「Tachibana session validated successfully」確認、再起動で keyring 復元動作

## フェーズ T4: マスタ・銘柄一覧・履歴 kline（2〜3 日）

**ゴール**: 起動後に銘柄を選び、日足チャートが表示される（trade/depth はまだ無い）。

- [ ] `tachibana.py::TachibanaWorker.list_tickers(market="stock")` — マスタ起動時 1 回ダウンロード→キャッシュ→`CLMIssueMstKabu` から ticker 配列を返す
- [ ] `TachibanaWorker.fetch_klines(timeframe="D1")` — `CLMMfdsGetMarketPriceHistory` 経由
- [ ] `TachibanaWorker.fetch_ticker_stats` — `CLMMfdsGetMarketPrice` から派生
- [ ] capabilities で `supported_timeframes=["D1"]` を Rust に伝え、UI で M1/M5/H1 等の選択を立花選択時に非活性化
- [ ] マスタキャッシュ（`<config_dir>/tachibana/master_<YYYYMMDD>.jsonl`）— 当日分があれば再ダウンロードしない（Q5 推奨方針）
- [ ] **受け入れ**: `7203` の日足 1 年分が表示される、銘柄セレクタに数千件のリストが出る、`130A0` 等英字混在 ticker もリストに含まれる（あるいは安全に除外される）

## フェーズ T5: trade / depth ストリーム（3〜4 日）

**ゴール**: ザラ場時間中、現値変化と 5 本気配がリアルタイムで更新される。

- [ ] `tachibana_ws.py` — EVENT WebSocket クライアント（`p_evt_cmd=FD,KP,ST,SS,US,EC`、購読は最低でも `FD,KP,ST`）
  - WebSocket URL は `build_event_url(session.url_event_ws, params)` で構築（R2 例外）
  - 自動 ping 無効化、手動 pong（[SKILL.md ストリーム規約](../../../.claude/skills/tachibana/SKILL.md)）
  - **KP（KeepAlive）frame の処理**: 5 秒周期で届く `p_evt_cmd=KP` を受信タイマーのリセットに使う。15 秒以上 KP も含めて全 frame が来なければ切断とみなして再接続（指数バックオフ）
  - **ST（エラーステータス）frame の処理**: 受信したら `EngineError` に変換、深刻なら subscribe 全停止
  - 受信バッファは `\n` または `^A` 区切りで蓄積分割（一塊チャンクに複数メッセージあり）
  - 切断 → `Disconnected` イベント、再接続は指数バックオフ
- [ ] `TachibanaWorker.stream_trades` — FD frame → 出来高差分から `TradeMsg` 合成
- [ ] `TachibanaWorker.stream_depth` — FD frame → 5 本気配 → `DepthSnapshot`（`DepthDiff` は生成しない）
- [ ] `TachibanaWorker.fetch_depth_snapshot` — `CLMMfdsGetMarketPrice` ベースの初回 snapshot
- [ ] ザラ場時間判定（**JST 9:00–11:30 / 12:30–15:30**、東証 2024-11-05 以降の現行時間。クロージング・オークション 15:25–15:30 を含む）— 時間外は subscribe を `Disconnected{reason:"market_closed"}` で即返す
- [ ] **受け入れ**: ザラ場中 10 分間 7203 を購読し続けて drop 0、UI で trade ティッカーと板が動く。KP frame 受信ログがあること

## フェーズ T6: 復旧・耐久・観測性（2 日）

**ゴール**: Python 異常終了・session 切れ・ザラ場跨ぎでも UI が破綻しない。

- [ ] `EngineError{code:"tachibana_session_expired"}` → Rust UI バナー
- [ ] `VenueCredentialsRefreshed` 経由で再ログイン後の session を Rust が keyring 更新
- [ ] Python 再起動シナリオの自動テスト（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 Python プロセス復旧プロトコル 流用）
- [ ] ログにシークレット非漏洩テスト
- [ ] capabilities ハンドシェイクで OI / fetch_trades / 分足の非対応を Rust に伝え UI を非活性化
- [ ] **受け入れ**: [spec.md §4 受け入れ条件](./spec.md#4-受け入れ条件phase-1-完了の定義) 全て緑

## フェーズ T7: 仕上げ・配布準備（1〜2 日）

- [ ] README / SKILL.md に「立花 venue 利用の前提（電話認証済み口座が必要）」追記
- [ ] release ビルドで env 自動ログインが完全に除外されていること（コンパイルエラー or 空関数）の検証
- [ ] 本番 URL 設定の隠しフラグ（`TACHIBANA_ALLOW_PROD=1`）を実装、デフォルトは demo 強制
- [ ] CI に `pytest -m demo_tachibana` を **手動トリガジョブ** として追加（毎 PR では走らせない）

## Phase 2 以降（参考、計画外）

- 発注機能（`CLMKabuNewOrder` ほか）。第二暗証番号 UI、注文台帳、約定通知 (EC)、現引現渡
- 信用建玉一覧、余力照会
- 分足のクライアント側集計
- 本番環境の正式サポートと UI からの切替
- 呼値テーブル動的反映（[data-mapping.md §5](./data-mapping.md#5-ticker-metadata呼値売買単位) (B) or (C) 案）
- 先物・OP（`CLMIssueMstSak` / `CLMIssueMstOp`）
- ニュース表示

## リスクと緩和

| リスク | 緩和 |
| :--- | :--- |
| 仮想 URL の取り扱いミスでセッションリーク | `SecretString` 型で wrap、`Debug` 派生でマスク。pre-commit で `kabuka.e-shiten` リテラルを検出 |
| 本番 URL を踏んで実弾 | `TACHIBANA_ALLOW_PROD=1` がない限り Python 側でデモ強制、Rust 側でも assertion |
| 立花仕様変更（v4r9 等への移行） | URL ベースを config 化、IPC `capabilities` で venue 側バージョンを Rust に伝える |
| 電話認証の手動性 | アプリは関与しない。ドキュメントで明示し、UI バナーで誘導 |
| 立花の API レート制限 | サンプル `e_api_get_master_tel.py` のリトライ間隔を尊重（3 秒）、`limiter.py` に `TachibanaLimiter` を追加 |
| ザラ場跨ぎでセッション切れ気付かない | `validate_session` を 5 分周期で実行、失敗で即座に `tachibana_session_expired` を発出 |

## 工数概算

| フェーズ | 概算 |
| :--- | :--- |
| T0 | 1〜2 日 |
| T1 | 2〜3 日 |
| T2 | 2 日 |
| T3 | 2 日 |
| T4 | 2〜3 日 |
| T5 | 3〜4 日 |
| T6 | 2 日 |
| T7 | 1〜2 日 |
| **合計** | **15〜20 日**（1 人換算、デモ環境動作確認込み） |
