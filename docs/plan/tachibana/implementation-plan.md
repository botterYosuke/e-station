# 立花証券統合: 実装計画

親計画 [docs/plan/✅python-data-engine/implementation-plan.md](../✅python-data-engine/implementation-plan.md) のフェーズ 6 完了後、または並行で着手する追加トラックとして位置づける。

## フェーズ T0: 既存型棚卸し + 仕様凍結 + スキーマ拡張（2〜3 日）

**ゴール**: IPC スキーマに立花対応の差分を入れ、Rust / Python 両側で型ビルドが通る。**着手前に既存型の影響範囲を grep で表に書き出す**。

### T0.1 既存コード棚卸し（先に必ず実施）

- [ ] `git grep -n "TickerInfo"` / `HashMap.*TickerInfo` / `HashSet.*TickerInfo` の参照箇所を全数表化。`#[derive(Hash, Eq)]` 入りでフィールドを増やす影響を見積もる
- [ ] `git grep -nE "MarketKind::(Spot|LinearPerps|InversePerps)"` で網羅 match の箇所を全部リストアップ（`exchange` / `engine-client` / `data` / `src` 配下）
- [ ] `Ticker::new` ([exchange/src/lib.rs:281-291](../../../exchange/src/lib.rs#L281)) の `assert!(ticker.is_ascii())` を確認し、`130A0` 等が通ることをユニットテストで実機確認
- [ ] `Timeframe::D1` ([exchange/src/lib.rs:83](../../../exchange/src/lib.rs#L83)) が IPC で `"1d"` 文字列にシリアライズされることを確認（新規 timeframe 追加は不要）
- [ ] `ProcessManager` ([engine-client/src/process.rs](../../../engine-client/src/process.rs)) の proxy 保持パターンを読み、credentials 保持で必要な mutex / Arc 戦略を把握
- [ ] `python/tests/test_*_rest.py` のモック方式（`pytest-httpx` / `HTTPXMock`）が他 venue で稼働中であることを確認
- [ ] [docs/plan/✅python-data-engine/schemas/](../✅python-data-engine/schemas/) の `commands.json` / `events.json` が実在することを確認（実在を確認済み）

### T0.2 型・スキーマ追加

- [ ] `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` を [exchange/src/adapter.rs](../../../exchange/src/adapter.rs) に追加
- [ ] `QuoteCurrency` enum を新設（`Usdt`/`Usdc`/`Usd`/`Jpy`、`Hash + Eq + Serialize + Deserialize`）。`&'static str` は使わない（serde ラウンドトリップ不可）
- [ ] `TickerInfo` に `lot_size: Option<u32>` と `quote_currency: QuoteCurrency` を追加。既存 venue 全件で `quote_currency` の初期化漏れがないようコンパイラに検出させる（フィールド追加で全コンストラクタが影響する）
- [ ] **日本語銘柄名の運搬経路を確定**: `engine-client::dto::TickerListed` / `TickerMetadata` 応答に `display_name_ja: Option<String>` を追加。Rust UI 側は `HashMap<Ticker, TickerDisplayMeta>` で別管理（`TickerInfo` の Hash には含めない）
- [ ] `engine-client` DTO に下記を追加し `schema_minor` を bump:
  - `Command::SetVenueCredentials { venue: String, credentials: serde_json::Value }`
  - `EngineEvent::VenueReady { venue: String }`（**冪等イベント**）
  - `EngineEvent::VenueCredentialsRefreshed { venue: String, session: serde_json::Value }`
  - `Ready.capabilities.venue_capabilities` のサブ構造
- [ ] **venue-ready ゲート方針を固定**: `Ready` と `VenueReady` の役割を分離し、立花 venue の `ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe` を `VenueReady` 後まで待たせる。`VenueReady` 再受信時に既存購読の重複再送が起きないよう `ProcessManager` 1 箇所で resubscribe を集約
- [ ] **Python の保存先パス受け渡し方法を決定**: `stdin` 初期 payload 拡張（`{port, token, config_dir, cache_dir}`）を採用方針として暫定固定（軽量・既存パスの拡張で済む）。最終 OK は T0 レビューで
- [ ] **env 変数名を venue prefix で確定**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_SECOND_PASSWORD` / `DEV_TACHIBANA_DEMO`。SKILL.md S2/S3 の旧 `DEV_USER_ID` 系（架空ファイル前提）は本フェーズで SKILL.md 側を書き換える
- [ ] [docs/plan/✅python-data-engine/schemas/commands.json](../✅python-data-engine/schemas/commands.json) / `events.json` / `CHANGELOG.md` 更新
- [ ] **SKILL.md の同期**: `.claude/skills/tachibana/SKILL.md` の R3/R4/R6/R10 / §Rust 実装の既存ヘルパー / S1〜S6 が架空のファイル参照（`exchange/src/adapter/tachibana.rs` / `data/src/config/tachibana.rs` / `src/screen/login.rs` / `src/connector/auth.rs` / `src/replay_api.rs`）と旧 env 名を含むため、本計画で決まった**実在パスと新 env 名**へ置換。実装未完の参照は「将来実装予定（T3 で新設）」と但し書き
- [ ] **受け入れ**: `cargo check --workspace` 成功、Python `pytest` の既存スイート緑、棚卸し表が plan/ に commit 済み

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
  - **既知バグ回避**: SKILL.md S6 表に「セッション復元と並行で走る history fetch が逆転して `p_no <= 前要求.p_no` エラー」が記載されている。Python 移植版では **session 復元（`SetVenueCredentials` 処理）の完了前に他リクエストを発行しない**直列化を `TachibanaWorker` 内で強制し、起動レース回帰テストを 1 件追加する
- [ ] エラー判定ヘルパ `check_response(payload) -> None | TachibanaError`（[SKILL.md R6](../../../.claude/skills/tachibana/SKILL.md)、`p_errno` 空文字＝正常を含む）
- [ ] **受け入れ**: 上記モジュールを単体テストでカバー、サンプルレスポンス（`samples/e_api_login_tel.py/e_api_login_response.txt` ほか）から期待値抽出ができる。REQUEST URL と EVENT URL の差を別テストで検証

## フェーズ T2: 認証フローと session 管理（2 日）

**ゴール**: `CLMAuthLoginRequest` 経由でデモ環境に対しログインできる。

- [ ] `python/engine/exchanges/tachibana_auth.py`
  - `login(user_id, password, is_demo) -> TachibanaSession`
  - `validate_session(session) -> bool`（`CLMMfdsGetMasterData` 軽量 1 件で生存確認）
  - 二段エラー判定 + `sKinsyouhouMidokuFlg=="1"` で `UnreadNoticesError`
  - レスポンスから `sZyoutoekiKazeiC`（譲渡益課税区分）を `TachibanaSession` に保持（Phase 2 発注時に流用）
- [ ] **起動時のみ再ログイン**のガードを実装: `SetVenueCredentials` の session validation 中に限り `user_id/password` fallback を許可し、購読開始後の `p_errno="2"` は再ログインせず `tachibana_session_expired` を返す
- [ ] mock サーバテスト（`pytest-httpx` の `HTTPXMock`、`python/tests/test_binance_rest.py` パターン踏襲）で正常系・異常系（`p_errno=-62` / `=2` / 認証失敗 / `sKinsyouhouMidokuFlg=1`）
- [ ] **受け入れ**: `pytest -m demo_tachibana` で実 demo 環境ログイン成功（手動電話認証済みアカウント前提）

## フェーズ T3: クレデンシャル受け渡し配線（2 日）

**ゴール**: Rust が keyring からクレデンシャルを取り出し、Python が `VenueReady` を返すまで往復する。

- [ ] `data/src/config/tachibana.rs` 新設（**現リポジトリには存在しないことを確認済み**。`data/src/config/proxy.rs` の keyring 実装パターンを参考にする）:
  - `TachibanaCredentials { user_id, password: SecretString, second_password: SecretString, is_demo }`
  - `TachibanaSession { url_request, url_master, url_price, url_event, url_event_ws, expires_at_ms, zyoutoeki_kazei_c }`
  - keyring 読み書き
- [ ] 立花ログイン UI を追加（user_id / password / second_password / is_demo）。`src/screen/login.rs` は **現リポジトリには未実装**のため、本フェーズで対応するログイン画面ファイルを新設する（既存 `src/screen/` の構造に沿わせ、配置は T3 着手時に設計メモで確定）。`#[cfg(debug_assertions)]` で `DEV_TACHIBANA_*` env 自動入力
- [ ] [engine-client/src/backend.rs](../../../engine-client/src/backend.rs) で `SetVenueCredentials` 送信パスを実装（既存 `SetProxy` パターン踏襲、`backend.rs` の実在は `ls engine-client/src/` で確認済み）
- [ ] [engine-client/src/process.rs](../../../engine-client/src/process.rs) に **Tachibana credentials の保持と再送**を追加し、managed mode の再起動時に `SetProxy -> SetVenueCredentials -> VenueReady -> resubscribe` を一貫して実行する
- [ ] [src/main.rs](../../../src/main.rs) 起動シーケンスに「keyring 読込 → `ProcessManager` / 接続オブジェクトへ creds 注入 → SetVenueCredentials → VenueReady 待ち」を追加
- [ ] `VenueCredentialsRefreshed` を受けて keyring session を更新する処理を Rust 側に実装（起動時再ログイン成功時のみ発火）
- [ ] 立花 venue 用の metadata / subscribe 要求を `VenueReady` まで抑止する UI ゲートを追加（sidebar 初期 metadata fetch を含む）
- [ ] **受け入れ**: debug ビルドで `.env` 設定 → 起動 → ログ「Tachibana session validated successfully」確認、再起動で keyring 復元動作

## フェーズ T4: マスタ・銘柄一覧・履歴 kline（2〜3 日）

**ゴール**: 起動後に銘柄を選び、日足チャートが表示される（trade/depth はまだ無い）。

- [ ] `tachibana.py::TachibanaWorker.list_tickers(market="stock")` — マスタ起動時 1 回ダウンロード→キャッシュ→`CLMIssueMstKabu` から ticker 配列を返す
- [ ] `TachibanaWorker.fetch_klines(timeframe="D1")` — `CLMMfdsGetMarketPriceHistory` 経由
- [ ] `TachibanaWorker.fetch_ticker_stats` — `CLMMfdsGetMarketPrice` から派生
- [ ] capabilities で `supported_timeframes=["1d"]` を Rust に伝え、UI で `1m` / `5m` / `1h` 等の選択を立花選択時に非活性化
- [ ] マスタキャッシュ（`<config_dir>/tachibana/master_<YYYYMMDD>.jsonl` または `<cache_dir>/tachibana/...`）— T0 で決めたパス受け渡し方式に従って保存し、当日分があれば再ダウンロードしない
- [ ] **受け入れ**: `7203` の日足 1 年分が表示される、銘柄セレクタに数千件のリストが出る、`130A0` 等英字混在 ticker もリストに含まれる、日本語銘柄名が別メタデータ経路で検索または表示に使える

## フェーズ T5: trade / depth ストリーム（3〜4 日）

**ゴール**: ザラ場時間中、現値変化と 5 本気配がリアルタイムで更新される。

- [ ] `tachibana_ws.py` — EVENT WebSocket クライアント（`p_evt_cmd=FD,KP,ST,SS,US,EC`、購読は最低でも `FD,KP,ST`）
  - WebSocket URL は `build_event_url(session.url_event_ws, params)` で構築（R2 例外）
  - 自動 ping 無効化、手動 pong（[SKILL.md ストリーム規約](../../../.claude/skills/tachibana/SKILL.md)）
  - **KP（KeepAlive）frame の処理**: 5 秒周期で届く `p_evt_cmd=KP` を受信タイマーのリセットに使う。**12 秒**（KP 2 回欠損相当 + 2 秒 jitter、spec.md §3.2 と同値）以上 KP も含めて全 frame が来なければ切断とみなして再接続（指数バックオフ）
  - **HTTP long-poll (`sUrlEvent`) のフォールバック実装はしない**（open-questions Q4 決定: WS のみ）。閉鎖環境用の補助ルートが必要になったら Phase 2 で追加
  - **ST（エラーステータス）frame の処理**: 受信したら `EngineError` に変換、深刻なら subscribe 全停止
  - 受信バッファは `\n` または `^A` 区切りで蓄積分割（一塊チャンクに複数メッセージあり）
  - 切断 → `Disconnected` イベント、再接続は指数バックオフ
- [ ] `TachibanaWorker.stream_trades` — FD frame → 出来高差分から `TradeMsg` 合成
- [ ] `TachibanaWorker.stream_depth` — FD frame → 5 本気配 → `DepthSnapshot`（`DepthDiff` は生成しない）
- [ ] `TachibanaWorker.fetch_depth_snapshot` — `CLMMfdsGetMarketPrice` ベースの初回 snapshot
- [ ] ザラ場時間判定（**JST 9:00–11:30 前場 / 12:30–15:25 後場連続 / 15:25–15:30 クロージング・オークション**、東証 2024-11-05 以降の現行時間）— **9:00–15:30 の間は `Connected` 維持**。クロージング・オークション中は気配が動かなくても「市場時間外」UI を出さない。閉場帯（〜9:00 / 11:30〜12:30 / 15:30〜）でのみ subscribe を `Disconnected{reason:"market_closed"}` で即返し、Python 側で polling/streaming を停止
- [ ] **受け入れ**: ザラ場中 10 分間 7203 を購読し続けて drop 0、UI で trade ティッカーと板が動く。KP frame 受信ログがあること

## フェーズ T6: 復旧・耐久・観測性（2 日）

**ゴール**: Python 異常終了・session 切れ・ザラ場跨ぎでも UI が破綻しない。

- [ ] `EngineError{code:"tachibana_session_expired"}` → Rust UI バナー
- [ ] `VenueCredentialsRefreshed` 経由で**起動時再ログイン後**の session を Rust が keyring 更新
- [ ] Python 再起動シナリオの自動テスト（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 Python プロセス復旧プロトコル 流用）
- [ ] ログにシークレット非漏洩テスト
- [ ] capabilities ハンドシェイクで OI / fetch_trades / 分足の非対応を Rust に伝え UI を非活性化
- [ ] `NotImplementedError` が現行 server では `Error{code:"not_implemented"}` に変換されることを前提に、UI とテストを揃える。専用エラーコードが必要なら server 側例外マッピング追加を別PRに切り出す
- [ ] 「ProcessManager が credentials を保持していないため再起動後に立花だけ復旧しない」回帰を防ぐ統合テストを追加
- [ ] **`VenueReady` 冪等性テスト**: Python 再起動 → `SetVenueCredentials` 再注入 → `VenueReady` 再受信時に、Rust 側 UI が新規 subscribe を発行しないこと（resubscribe は `ProcessManager` 1 箇所のみ）を統合テストで検証
- [ ] **受け入れ**: [spec.md §4 受け入れ条件](./spec.md#4-受け入れ条件phase-1-完了の定義) 全て緑

## フェーズ T7: 仕上げ・配布準備（1〜2 日）

- [ ] README / SKILL.md に「立花 venue 利用の前提（電話認証済み口座が必要）」追記
- [ ] release ビルドで env 自動ログインが完全に除外されていること（コンパイルエラー or 空関数）の検証
- [ ] 本番 URL 設定の隠しフラグ（`TACHIBANA_ALLOW_PROD=1`）を実装、デフォルトは demo 強制
- [ ] CI に `pytest -m demo_tachibana` を **手動トリガジョブ** として追加（毎 PR では走らせない）。スケジュール起動する場合は demo の閉局帯（平日 8:00–18:00 JST 想定、T2 で実機確認）を避ける
- [ ] `tools/secret_scan.sh` 新設: `kabuka.e-shiten` / 仮想 URL ホスト / `sUserId` / `sPassword` / `sSecondPassword` を検出。pre-commit hook と CI ジョブの両方から同一スクリプトを呼ぶ（spec.md §4 受け入れ条件 6 と整合）

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
| 仮想 URL の取り扱いミスでセッションリーク | `SecretString` 型で wrap、`Debug` 派生でマスク。`tools/secret_scan.sh`（T7 で実装）を pre-commit と CI 双方から呼び、`kabuka.e-shiten` リテラル + 仮想 URL ホスト + `sUserId`/`sPassword`/`sSecondPassword` を検出 |
| 本番 URL を踏んで実弾 | `TACHIBANA_ALLOW_PROD=1` がない限り Python 側でデモ強制、Rust 側でも assertion |
| 立花仕様変更（v4r9 等への移行） | URL ベースを config 化、IPC `capabilities` で venue 側バージョンを Rust に伝える |
| 電話認証の手動性 | アプリは関与しない。ドキュメントで明示し、UI バナーで誘導 |
| 立花の API レート制限 | サンプル `e_api_get_master_tel.py` のリトライ間隔を尊重（3 秒）、`limiter.py` に `TachibanaLimiter` を追加 |
| ザラ場跨ぎでセッション切れ気付かない | **定期 `validate_session` ポーリングは実装しない**（runtime 中の自動再ログイン禁止と矛盾するため、spec.md §3.2 と整合）。検知は subscribe 経路で受ける `p_errno=2` のみに任せ、検知後は即 `tachibana_session_expired` を発出して UI を再ログイン誘導状態に遷移させる |

## 工数概算

| フェーズ | 概算 |
| :--- | :--- |
| T0 | 2〜3 日（既存型棚卸し追加分） |
| T1 | 2〜3 日 |
| T2 | 2 日 |
| T3 | 2 日 |
| T4 | 2〜3 日 |
| T5 | 3〜4 日 |
| T6 | 2 日 |
| T7 | 1〜2 日 |
| **合計** | **16〜21 日**（1 人換算、デモ環境動作確認込み） |
