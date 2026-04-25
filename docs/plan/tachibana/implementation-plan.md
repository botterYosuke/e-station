# 立花証券統合: 実装計画

親計画 [docs/plan/✅python-data-engine/implementation-plan.md](../✅python-data-engine/implementation-plan.md) のフェーズ 6 完了後、または並行で着手する追加トラックとして位置づける。

## フェーズ T0: 既存型棚卸し + 仕様凍結 + スキーマ拡張（2〜3 日）

**ゴール**: IPC スキーマに立花対応の差分を入れ、Rust / Python 両側で型ビルドが通る。**着手前に既存型の影響範囲を grep で表に書き出す**。

### T0.1 既存コード棚卸し（先に必ず実施）

- [ ] `git grep -n "TickerInfo"` / `HashMap.*TickerInfo` / `HashSet.*TickerInfo` の参照箇所を全数表化。`#[derive(Hash, Eq)]` 入りでフィールドを増やす影響を見積もる
- [ ] `git grep -nE "MarketKind::(Spot|LinearPerps|InversePerps)"` で網羅 match の箇所を全部リストアップ（`exchange` / `engine-client` / `data` / `src` 配下）
- [ ] `Ticker::new` ([exchange/src/lib.rs:281-291](../../../exchange/src/lib.rs#L281)) の `assert!(ticker.is_ascii())` を確認し、`130A0` 等が通ることをユニットテストで実機確認
- [ ] `Timeframe::D1` ([exchange/src/lib.rs:83](../../../exchange/src/lib.rs#L83)) の **`Serialize` 実装が IPC で `"1d"` 文字列を返すこと**を `serde_json::to_string` でユニットテスト確認（F-m2、enum 既定の `"D1"` で出ているなら独自 `Serialize` 実装か `#[serde(rename = "1d")]` 追加が必要）
- [ ] `ProcessManager` ([engine-client/src/process.rs](../../../engine-client/src/process.rs)) の proxy 保持パターンを読み、credentials 保持の **mutex / Arc 戦略を T0.2 のうちに確定**（F-m4）。proxy が `Arc<Mutex<Option<Proxy>>>` ならそれに揃える、`watch::channel` ならそれに揃える、と決め切る
- [ ] `src/screen/` の現在構造を確認し、立花ログイン UI の追加先（既存 `login.rs` 拡張 or 新ファイル）を T0 のうちに暫定確定（F-m3）
- [ ] `python/tests/test_*_rest.py` のモック方式（`pytest-httpx` / `HTTPXMock`）が他 venue で稼働中であることを確認
- [ ] [docs/plan/✅python-data-engine/schemas/](../✅python-data-engine/schemas/) の `commands.json` / `events.json` が実在することを確認（実在を確認済み）
- [ ] **FD 情報コード一覧抽出（F-M2）**: Python サンプル [`e_api_websocket_receive_tel.py`](../../../.claude/skills/tachibana/samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py) と [`e_api_event_receive_tel.py`](../../../.claude/skills/tachibana/samples/e_api_event_receive_tel.py/e_api_event_receive_tel.py) のコメント／コード表から FD frame の情報コード（`DPP` / `DV` / `DPP_TIME` / `DDT` / `GAK1..5` / `GBK1..5` ほか）を抜き出し、[data-mapping.md §3-4](./data-mapping.md) の表に転記する。実コード名と一致しないものは「未確認」マークして T1 まで持ち越し

### T0.2 型・スキーマ追加

- [ ] `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` を [exchange/src/adapter.rs](../../../exchange/src/adapter.rs) に追加
- [ ] **`MarketKind::Stock` の `qty_in_quote_value` は enum 内部分岐で `price * qty` 強制**（F-M3）。`size_in_quote_ccy` 引数を見ない実装にし、`Stock` 用ユニットテストで誤呼出（`size_in_quote_ccy=true`）でも常に `price*qty` になることを確認
- [ ] **`secrecy = "0.8"` を `engine-client` / `data` の Cargo.toml に追加**（F-B1）。`SecretString` は **Rust 内部保持型**でのみ使い、IPC 送出時は `expose_secret()` 経由でプレーン `String` 化した送出専用 DTO（後述 `*Wire`）に写像する
- [ ] `QuoteCurrency` enum を新設（`Usdt`/`Usdc`/`Usd`/`Jpy`、`Copy + Hash + Eq + Serialize + Deserialize`）。**`Default` は実装しない**（F-M6）。`&'static str` は使わない（serde ラウンドトリップ不可）
- [ ] `TickerInfo` に `#[serde(default)]` 付きで `lot_size: Option<u32>` と `quote_currency: Option<QuoteCurrency>` を追加（F13/F-M6）。`TickerInfo` の `Copy` 制約を壊さない（`String` 追加禁止）。**`None` 復元時は読み込み層で `Exchange::default_quote_currency()` を使って `Some(_)` に正規化**し、UI フォーマッタへは常に `Some` で渡す
- [ ] `Exchange::default_quote_currency(&self) -> QuoteCurrency` を `exchange/src/adapter.rs` に実装（暗号資産 venue は USDT/USDC、`TachibanaStock` は `Jpy`）
- [ ] **既存永続 state の serde 互換性確認**（F13/F-M4）: dashboard 設定ファイル / `state.rs` に `TickerInfo` が保存されているか `git grep` で特定。`#[serde(default)]` で missing field が読めることに加え、**`Hash` 値変化により既存 `HashMap<TickerInfo, _>` のキー突合が壊れないか**を実機テスト。受け入れ条件に「旧 `state.json` を起動 → pane 復元 → ticker 表示」を追加
- [ ] **日本語銘柄名の運搬経路を確定**: `engine-client::dto::TickerListed` / `TickerMetadata` 応答に `display_name_ja: Option<String>` を追加。Rust UI 側は `HashMap<Ticker, TickerDisplayMeta>` で別管理（`TickerInfo` の Hash には含めない）
- [ ] `engine-client` DTO に下記を追加し `schema_minor` を bump（F1, F6, F-B1, F-B2）:
  - `Command::SetVenueCredentials { request_id: String, payload: VenueCredentialsPayload }` — `payload` は typed enum（`VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire)`）。`serde_json::Value` は使わない
  - **2 層 DTO 構造**（F-B2）: 内部保持型 `TachibanaCredentials`/`TachibanaSession`（`data` クレート、`SecretString` 保持、`Debug` 手実装マスク、`Serialize` 持たない、`Deserialize` のみ keyring 復元用に持つ） / 送出用 `TachibanaCredentialsWire`/`TachibanaSessionWire`（`engine-client` クレート、プレーン `String`、`Serialize` 派生、`Debug` 手実装マスク、`Deserialize` 持たない）。送信時 `From<&TachibanaCredentials> for TachibanaCredentialsWire` で `expose_secret()` 経由の写像を 1 箇所に集約し、`Wire` は serialize 直後に drop
  - `TachibanaSessionWire.expires_at_ms: Option<i64>`（F-B3）。立花 API は明示的な期限を返さないため `None` を許容、`None` のとき起動時 `validate_session_on_startup` 必須
  - `EngineEvent::VenueReady { venue: String, request_id: Option<String> }`（**冪等イベント**、`request_id` は `SetVenueCredentials` との相関用。UI は初回 / 再送を区別しない）
  - `EngineEvent::VenueError { venue: String, request_id: Option<String>, code: String, message: String }` — 旧 `EngineError{code:"tachibana_session_expired"}` は廃止、`VenueError{code:"session_expired"}` に統一。**`message` は Python 側が user-facing 文言として詰める**（Rust 側は描画のみ、F-Banner1）。`code` の許容値（`session_expired` / `unread_notices` / `phone_auth_required` / `login_failed` / `ticker_not_found` …）は [architecture.md §6](./architecture.md#6-失敗モードと-ui-表現) の表に従い、`events.json` schema にも enum で列挙する
  - `EngineEvent::VenueCredentialsRefreshed { venue: String, session: TachibanaSessionWire }`
  - `EngineEvent::VenueLoginStarted { venue: String, request_id: Option<String> }` — Python が tkinter ログインヘルパーを spawn したことを Rust に通知（F-Login1、architecture.md §7.5）
  - `EngineEvent::VenueLoginCancelled { venue: String, request_id: Option<String> }` — ユーザーがダイアログをキャンセルした
  - `Command::RequestVenueLogin { request_id: String, venue: String }` — Rust UI から立花ログインを明示要求（architecture.md §7.5）
  - **UI ツリー DSL 型（`VenueLoginForm` / `VenueUiNode` 等）は追加しない**。Python が独立 tkinter ウィンドウを持つため、Rust に UI 構造を渡す必要が無い
  - `Ready.capabilities.venue_capabilities` のサブ構造（**Phase 1 は `serde_json::Value` のまま追加し、schema は Python 側で生成・Rust 側はパスを deserialize で読み出す方針で固定**、F-M8。typed 化は Phase 2 以降に再検討）
- [ ] **venue-ready ゲート方針を固定**: `Ready` と `VenueReady` の役割を分離し、立花 venue の `ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe` を `VenueReady` 後まで待たせる。**`VenueReady` は「session 検証完了」のみを意味し、マスタ初期 DL 完了は含まない**（F12）。マスタ取得完了判定は `ListTickers` 応答到着で行う。`VenueReady` 再受信時に既存購読の重複再送が起きないよう `ProcessManager` 1 箇所で resubscribe を集約
- [ ] **Python の保存先パス受け渡し方法を決定**: `stdin` 初期 payload 拡張（`{port, token, config_dir, cache_dir}`）を採用方針として暫定固定（軽量・既存パスの拡張で済む）。最終 OK は T0 レビューで
- [ ] **env 変数名を venue prefix で確定**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_SECOND_PASSWORD` / `DEV_TACHIBANA_DEMO`。SKILL.md S2/S3 の旧 `DEV_USER_ID` 系（架空ファイル前提）は本フェーズで SKILL.md 側を書き換える
- [ ] [docs/plan/✅python-data-engine/schemas/commands.json](../✅python-data-engine/schemas/commands.json) / `events.json` / `CHANGELOG.md` 更新
- [ ] **SKILL.md の同期（F-m5、唯一の正本タスク）**: `.claude/skills/tachibana/SKILL.md` の以下を本計画ベースで書き換える。README.md / spec.md 側の同種記述は本タスクへリンクする形に簡約済み:
  - L8 警告ブロック（旧 env 名と架空ファイル参照）
  - R3/R4/R6/R10
  - §Rust 実装の既存ヘルパー（架空 `tachibana.rs` 参照）
  - S1〜S6（架空 `src/screen/login.rs` / `src/connector/auth.rs` / `src/replay_api.rs` 参照）
  - 環境変数名: `DEV_USER_ID` 系 → `DEV_TACHIBANA_*`
  - 実装未完の参照は「将来実装予定（T3 で新設）」と但し書き
- [ ] **受け入れ**: `cargo check --workspace` 成功、Python `pytest` の既存スイート緑、棚卸し表 + FD 情報コード一覧が plan/ に commit 済み、旧 `state.json` 起動テスト緑

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
- [ ] `p_no` 採番ヘルパ（**asyncio 単一スレッド前提の単純カウンタ**、Unix 秒初期化、Lock 不要、F18）と `current_p_sd_date()`（JST 固定、SKILL.md R4）
  - **既知バグ回避**: SKILL.md S6 表に「セッション復元と並行で走る history fetch が逆転して `p_no <= 前要求.p_no` エラー」が記載されている。Python 移植版では **session 復元（`SetVenueCredentials` 処理）の完了前に他リクエストを発行しない**直列化を `TachibanaWorker` 内で強制し、起動レース回帰テストを 1 件追加する
- [ ] エラー判定ヘルパ `check_response(payload) -> None | TachibanaError`（[SKILL.md R6](../../../.claude/skills/tachibana/SKILL.md)、`p_errno` 空文字＝正常を含む）
- [ ] **受け入れ**: 上記モジュールを単体テストでカバー、サンプルレスポンス（`samples/e_api_login_tel.py/e_api_login_response.txt` ほか）から期待値抽出ができる。REQUEST URL と EVENT URL の差を別テストで検証

## フェーズ T2: 認証フローと session 管理（2 日）

**ゴール**: `CLMAuthLoginRequest` 経由でデモ環境に対しログインできる。

- [ ] `python/engine/exchanges/tachibana_auth.py`
  - `login(user_id, password, is_demo) -> TachibanaSession`
  - `validate_session_on_startup(session) -> bool`（`CLMMfdsGetMasterData` 軽量 1 件で生存確認）— **関数名で「起動時専用」を縛る**。runtime からは呼ばない（F10）
  - 二段エラー判定 + `sKinsyouhouMidokuFlg=="1"` で `UnreadNoticesError`
  - レスポンスから `sZyoutoekiKazeiC`（譲渡益課税区分）を `TachibanaSession` に保持（Phase 2 発注時に流用）
  - **`expires_at_ms` は `Option<i64>` で持つ**（F-B3）。ログイン直後は `None` 固定（立花は明示期限を返さないため）。`None` のとき `validate_session_on_startup` は必ず叩く（safe path）。`Some(t)` で `now > t` なら復元せず再ログインへ（fast path）。閉局時刻を `CLMDateZyouhou` から取得できることが確認できたら値を入れる方針は Phase 2 へ繰越
- [ ] **起動時のみ再ログイン**のガードを実装: `SetVenueCredentials` の session validation 中に限り `user_id/password` fallback を許可し、購読開始後の `p_errno="2"` は再ログインせず `VenueError{code:"session_expired"}` を返す
- [ ] mock サーバテスト（`pytest-httpx` の `HTTPXMock`、`python/tests/test_binance_rest.py` パターン踏襲）で正常系・異常系（`p_errno=-62` / `=2` / 認証失敗 / `sKinsyouhouMidokuFlg=1`）
- [ ] **受け入れ**: `pytest -m demo_tachibana` で実 demo 環境ログイン成功（手動電話認証済みアカウント前提）

## フェーズ T3: クレデンシャル受け渡し配線（2 日）

**ゴール**: Rust が keyring からクレデンシャルを取り出し、Python が `VenueReady` を返すまで往復する。

- [ ] `data/src/config/tachibana.rs` 新設（**現リポジトリには存在しないことを確認済み**。`data/src/config/proxy.rs` の keyring 実装パターンを参考にする）:
  - `TachibanaCredentials { user_id, password: SecretString, second_password: SecretString, is_demo }`
  - `TachibanaSession { url_request, url_master, url_price, url_event, url_event_ws, expires_at_ms, zyoutoeki_kazei_c }`
  - keyring 読み書き
- [ ] **Rust UI 側**: 立花のログイン画面コードは**追加しない**。`Venue::Tachibana` 関連で「ログインダイアログを別ウィンドウで表示中」「ログインがキャンセルされました」を表示する汎用ステータスバナー（既存 `VenueError.message` レンダラの拡張）だけ実装する
- [ ] **Python 側 `tachibana_login_dialog.py`** を新設（F-Login1、architecture.md §7.4）。`python -m engine.exchanges.tachibana_login_dialog` で起動できる単独実行可能スクリプト。tkinter で `Toplevel` モーダルを構築、stdin から JSON 起動引数を読み、stdout に結果 JSON を返して exit。立花固有のラベル・順序・警告ボックス（電話認証・デモ環境）はこのファイルに直書き
- [ ] **Python 側 `tachibana_login_flow.py`** を新設。データエンジン側で `asyncio.create_subprocess_exec(sys.executable, "-m", "engine.exchanges.tachibana_login_dialog", ...)` で tkinter ヘルパーを spawn し、stdout を JSON parse、`tachibana_auth.login(...)` を実行、結果に応じて `VenueReady` / `VenueError` / `VenueLoginCancelled` を IPC 送信
- [ ] Python 側の発火タイミングを実装: (a) `RequestVenueLogin` 受信、(b) `SetVenueCredentials` 認証失敗、(c) keyring session 失効検知（起動時のみ） — いずれも `tachibana_login_flow` を呼ぶ。失敗 3 回で `VenueError{code:"login_failed"}` で諦める
- [ ] Rust UI: 立花機能を最初に開く操作（`Venue::Tachibana` ticker selector を開く / 立花 pane 追加）で `Command::RequestVenueLogin{ venue:"tachibana" }` を発火
- [ ] **debug ビルドの env 自動入力は Python 側で処理**（architecture.md §7.7）: `tachibana_login_flow` が `DEV_TACHIBANA_*` env をチェックし、揃っていれば tkinter ヘルパーを spawn せずに直接 `tachibana_auth.login(...)` を実行する fast path を入れる。env 一部欠損ならヘルパーにプリフィルとして渡す。Rust 側の `#[cfg(debug_assertions)]` env 取り込みは**不要**（経路が Python 側に閉じる）
- [ ] **tkinter ヘルパーの単体テスト**: `subprocess.run([sys.executable, "-m", ..., dialog])` を pytest から呼び、`headless=true` の起動引数で実 GUI を出さずにバリデーション規則だけテストできる「テスト専用モード」を `tachibana_login_dialog.py` に実装。実 GUI 確認は `pytest -m gui` で手動
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
- [ ] `TachibanaWorker.stream_trades` — FD frame → 出来高差分から `TradeMsg` 合成（**前 frame 気配ベースの quote rule + 初回 frame 除外 + DV リセット検知**、data-mapping §3、F3/F4）
  - 受け入れテスト（`test_tachibana_fd_trade.py`）:
    1. 初回 frame では trade を発火しない（`prev_dv=None`）
    2. 2 件目以降で DV 差分 > 0 のとき trade を 1 件生成
    3. DV が前 frame より減少したら trade 発火せず `prev_dv` を再初期化
    4. side は前 frame の best_bid/best_ask に対して判定（当該 frame の気配は使わない）
- [ ] `TachibanaWorker.stream_depth` — FD frame → 5 本気配 → `DepthSnapshot`（`DepthDiff` は生成しない）。`sequence_id` は Python プロセス内 `AtomicI64`、`stream_session_id` 切替時に消費側リセット（F7）
- [ ] `TachibanaWorker.fetch_depth_snapshot` — `CLMMfdsGetMarketPrice` ベースの初回 snapshot（ザラ場前後の 1 発、および FD WS が 12 秒以上無通信の再接続中フォールバック時のみ。**runtime の定期 polling は実装しない**、F-M1）
- [ ] ザラ場時間判定（**JST 9:00–11:30 前場 / 12:30–15:25 後場連続 / 15:25–15:30 クロージング・オークション**、東証 2024-11-05 以降の現行時間）— **9:00–15:30 の間は `Connected` 維持**。クロージング・オークション中は気配が動かなくても「市場時間外」UI を出さない。閉場帯（〜9:00 / 11:30〜12:30 / 15:30〜）でのみ subscribe を `Disconnected{reason:"market_closed"}` で即返し、Python 側で polling/streaming を停止
- [ ] **受け入れ**: ザラ場中 10 分間 7203 を購読し続けて drop 0、UI で trade ティッカーと板が動く。KP frame 受信ログがあること

## フェーズ T6: 復旧・耐久・観測性（2 日）

**ゴール**: Python 異常終了・session 切れ・ザラ場跨ぎでも UI が破綻しない。

- [ ] `VenueError{venue:"tachibana", code:"session_expired", message}` → Rust UI バナー（旧 `EngineError{code:"tachibana_session_expired"}` は廃止）。**バナー文言は Python が `message` に詰めて送る**（F-Banner1）。Rust 側は `message` をそのまま描画し、固定文言を持たない。`code` は severity（warning/error）とアクションボタン（再ログイン / 閉じる）の出し分けにのみ使う
- [ ] **`VenueError.code` の enum 化（T0 schema 追加分の検証）**: Python 側の発出箇所（`tachibana_auth.py` / `tachibana_ws.py` / `tachibana.py`）で使う code 文字列が [architecture.md §6](./architecture.md#6-失敗モードと-ui-表現) の表と一致することを単体テストで検証。未知 code が発出されたら Rust 側はデフォルト severity（error）+ 再ログインボタン非表示で fail-safe に倒す
- [ ] **バナー文言テスト**: `tachibana_auth.py` の各エラー分岐（`p_errno=2` / `sKinsyouhouMidokuFlg=1` / `sResultCode=10031` / 認証失敗）が Python 側で意図通りの `message` を生成することを `python/tests/test_tachibana_banner_messages.py` で固定（snapshot test）
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
- [ ] `tools/secret_scan.sh` 新設: `kabuka.e-shiten` / 仮想 URL ホスト / `sUserId` / `sPassword` / `sSecondPassword` を検出。pre-commit hook と CI ジョブの両方から同一スクリプトを呼ぶ（spec.md §4 受け入れ条件 6 と整合）。**`BASE_URL_PROD` を定義する 1 箇所（例: `python/engine/exchanges/tachibana_url.py`）はファイル単位で allowlist** し、それ以外のリテラル出現を全て fail させる（F11）。allowlist ファイルは冒頭コメントで理由を明示
- [ ] **Windows 開発環境での pre-commit 整合（F-M5）**: 開発環境は Windows 中心であり pre-commit が PowerShell から起動するケースがある。`tools/secret_scan.sh` は git-bash / WSL を要件として README に明記し、pre-commit 設定で `bash tools/secret_scan.sh` 形式で呼ぶ（PowerShell 直起動を許さない）。CI でも `runs-on: ubuntu-latest` で実行。Windows ネイティブ pre-commit のために `tools/secret_scan.ps1` を将来追加するなら、両方が同じパターンを参照するよう正規表現を別ファイル `tools/secret_scan_patterns.txt` に切り出す

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
