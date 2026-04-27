# 立花証券統合: 仕様

## 1. ゴール

本アプリの venue として立花証券 e支店 API を追加し、**日本株のチャート閲覧体験を既存暗号資産 venue と同じ UI で提供する**。Phase 1 はリードオンリー（閲覧のみ）。

## 2. スコープ（Phase 1 = MVP）

### 2.1 含めるもの

- **新 venue `Venue::Tachibana`**（[exchange/src/adapter.rs::Exchange::TachibanaStock](../../../exchange/src/adapter.rs) に追加）
- **新 `MarketKind::Stock`**（株式現物市場。信用は内部的に同じ market でハンドル）。`qty_in_quote_value` は enum 内部分岐で `price * qty` を強制（呼出 9 箇所、[inventory-T0.md §4](./inventory-T0.md#4-qty_in_quote_value-呼出箇所f-h4)）
- **新 `Exchange::TachibanaStock`**
- **`Timeframe` の serde 形式統一（F-H1）**: 現状 `#[derive(Serialize)]` のみで `"D1"` / `"M1"` 等が出る既知の不整合がある。立花 capabilities (`["1d"]`) と既存 `Display` (`"1d"`) に合わせるため、`Timeframe` 全変種に `#[serde(rename = "...")]` を T0.2 で追加
- **Python 実装** `python/engine/exchanges/tachibana.py`（`ExchangeWorker` 実装、デモ環境のみ）
  - 認証フロー（`CLMAuthLoginRequest` → 5 つの仮想 URL を取得 → `TachibanaSession` 保持）
  - ティッカー一覧（`CLMEventDownload` の `CLMIssueMstKabu` から銘柄マスタを取り出す）
  - 銘柄コード・英語名（`display_symbol`）・日本語名（`display_name_ja`）の前方一致インクリメンタル検索（`matches_tachibana_filter`、T4-B5 着地済み）
  - ティッカーメタデータ（呼値単位・売買単位・銘柄名）— **本線は銘柄マスタ（`CLMIssueMstKabu` + `CLMIssueSizyouMstKabu` + `CLMYobine`）から合成**し、マスタ未掲載や追加情報が必要な場合のみ `CLMMfdsGetIssueDetail` をフォールバックで叩く（F9）。呼値は **per-stock 解決**（`CLMIssueSizyouMstKabu.sYobineTaniNumber` で `CLMYobine` 行を引いて band を選ぶ）であり、全銘柄共通の単一価格帯テーブルは存在しない（data-mapping.md §5）
  - 日足 kline 履歴（`CLMMfdsGetMarketPriceHistory`）
  - 24h ticker stats 相当（`CLMMfdsGetMarketPrice` のスナップショットから派生）
  - **取引（FD frame）ストリーム**: EVENT WebSocket (`sUrlEventWebSocket`) で `p_evt_cmd=FD` を購読 → 現値変化を 1 件 = 1 trade として配信（`p_*_DPP` フィールド）。**T0.1 FD 情報コード明示ゲート（implementation-plan.md L21）の通過が前提**。ゲート未通過のまま B 系縮退を採るなら本項目（trade ストリーム）と次項目（板スナップショット）は MVP から外す
  - **板スナップショット**: **FD frame 駆動が正**（FD frame ごとに 10 本気配を `DepthSnapshot` 化して配信、data-mapping §4）。`CLMMfdsGetMarketPrice` は (a) ザラ場前後の初回 snapshot、(b) FD WS 12 秒無通信の再接続中フォールバック、(c) `depth_unavailable` セーフティ発動時の polling fallback の **3 ケースに限定**（§3.3 と整合）。**runtime の定期 polling は実装しない**。`DepthDiff` / L2 はサポートしない
- **Rust 側の最小変更**:
  - `Venue` / `Exchange` / `MarketKind` 拡張
  - **ログイン関連の画面（フォーム・エラー表示・確認モーダル）は Python が tkinter で独立ウィンドウとして開く**（[architecture.md §7](./architecture.md#7-ログイン画面の-python-駆動f-login1)、F-Login1）。Rust 側に立花のログイン画面コード（フィールド名・ラベル・順序）を書かない
  - GUI ライブラリは **tkinter（Python 標準ライブラリ）** を採用（追加依存ゼロ、日本語 IME 対応、軽量）。tkinter の制約（メインスレッド要求）はログインヘルパー subprocess 隔離で回避
  - **tkinter ヘルパー spawn の起動条件（runtime 中の自動再ログイン禁止と整合、§3.2 LOW-3 参照）**: (a) アプリ起動直後の session 検証フェーズで `tachibana_session.json` が無い / 復元 session が validate に失敗した場合、(b) Rust UI が `Command::RequestVenueLogin` を発火した場合、の 2 経路のみ。**runtime 中に `p_errno=2` を検知しても Python は自発的にダイアログを spawn しない**（`VenueError{code:"session_expired"}` を返すだけ）。Rust UI には Python engine event DTO 名 `VenueLoginStarted` / `VenueLoginCancelled` / `VenueReady` / `VenueError` で状態を伝え、Rust UI 側の状態管理は `VenueState{Idle/LoginInFlight/Ready/Error}` 1 本化で受ける（用語使い分け: DTO = `VenueLogin*`、UI 状態 = `VenueState::*`）
  - **「立花にログイン」ボタンの常設（T35-U1、LOW-7、F-M1a、H3 修正）**: 再ログイン導線（Python engine event = `VenueLoginCancelled` / Rust UI 状態 = `VenueState::Idle` 後）として、**[src/screen/dashboard/tickers_table.rs::exchange_filter_btn](../../../src/screen/dashboard/tickers_table.rs) の Tachibana 行直下**に常設する（`Venue::ALL` ベースで `VenueState::Ready` 以外でも常時描画される領域、T3.5 Step D 着地済）。**禁止配置**: 立花 ticker selector / 立花 pane のヘッダ部に置くと `VenueState::Ready` 前は ticker selector / pane が空 or 非表示でデッドロックするため不可。フォールバックとしてメインウィンドウ上部のステータスバナー領域（T35-U2）に「立花未ログイン」表示中のみ補助ボタンを許容。押下で `Command::RequestVenueLogin` を発火（複数経路で発火させない、[implementation-plan.md T3 H3 修正](./implementation-plan.md)）
  - `TickerInfo`・`Exchange::price_step` 等で立花特有の呼値単位を反映（実装源は `CLMYobine` + `CLMIssueSizyouMstKabu.sYobineTaniNumber`、Phase 1 でも銘柄別呼値を使用、data-mapping.md §5）
  - `MarketKind::Stock` 追加に伴う UI / indicator / timeframe / market filter / 表示ラベルの網羅修正
- **デモ環境のみ**: `https://demo-kabuka.e-shiten.jp/e_api_v4r8/`
- **debug ビルドの env 自動ログイン**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO=true`（venue prefix を付けて将来の他 venue ID と衝突させない方針）。**`DEV_TACHIBANA_SECOND_PASSWORD` は Phase 1 では予約名として一覧化せず、計画文書からも削除する**（F-H5、第二暗証番号は収集も保持もしない方針との整合）。Phase 2 着手時に env 名を改めて確定する。SKILL.md 側の `DEV_USER_ID` 旧表記の書き換えは [implementation-plan.md T0.2 の SKILL.md 同期タスク](./implementation-plan.md) に集約

### 2.2 含めないもの（Phase 1 スコープ外）

- **発注・訂正・取消**（`CLMKabuNewOrder` / `CLMKabuCorrectOrder` / `CLMKabuCancelOrder*`）
  - 第二暗証番号の取り扱い、注文台帳 UI、約定通知 (`p_evt_cmd=EC`) の表示などは **[docs/plan/✅order/](../✅order/) で管理**（Order Phase O0〜O3 として実装済み）。Phase 1 本文書のスコープ外
- **本番環境（実弾）接続の常時 UI 露出** — `BASE_URL_PROD` の追加自体は許容するが、**通常起動時の UI（メイン画面・設定）には露出させない**。**`TACHIBANA_ALLOW_PROD=1` env が立っているときに限り、Python tkinter ログインダイアログにデモ/本番ラジオを描画**して都度選択させる（M8、architecture.md §7.4）。env が無いときはデモ固定。デフォルトは demo 強制
- **板差分（Depth Diff）/ L2 リアルタイム板** — 立花 FD frame は「現値・気配 10 本」程度で、L2 差分配信ではない。Phase 1 では **snapshot のみ** を一定間隔で配信し、`DepthDiff` イベントは生成しない
- **OI（建玉残）チャート** — 株式に概念がない。`fetch_open_interest` は `NotImplementedError` を返す
- **ヒストリカル trade（tick by tick 過去履歴）** — 立花は当日 FD frame の累積のみ、過去日のティック取得 API なし。`fetch_trades` は `NotImplementedError`
- **分足・秒足 kline** — 立花の REST 履歴は日足のみ（`CLMMfdsGetMarketPriceHistory`）。分足は FD frame からアプリ側で集計可能だが Phase 1 では未対応
- **ニュース** (`CLMMfdsGetNewsHead/Body`)
- **ザラ場外（PTS・夜間）銘柄**

### 2.3 MVP 必須に昇格した項目

- **ザラ場時間帯の判定（MVP 必須、T5 で実装）**: JST 9:00–11:30 前場 / 12:30–15:25 後場連続 / **15:25–15:30 クロージング・オークション**（**2024-11-05 以降の現行東証取引時間**）。`Connected` を維持するのは **9:00–15:30 全体**。クロージング・オークション中は気配がほぼ動かなくても「市場時間外」UI を出さない。閉場（〜9:00 / 11:30〜12:30 / 15:30〜）でのみ subscribe を `Disconnected{reason:"market_closed"}` で停止する。**Phase 1 はハードコード**（祝日カレンダー判定なし）。営業日カレンダー動的取得（`CLMDateZyouhou`）は Phase 2 送り
  - **発出粒度（M5 修正）**: `Disconnected` イベントは ticker/stream 粒度（`engine-client/src/dto.rs::EngineEvent::Disconnected`）のため、閉場帯に届いた立花 ticker subscribe ごとに 1 件返す。**Rust UI 側はバナー表示を venue 単位で de-dup**（複数銘柄購読中でも「市場時間外」バナーは 1 つ）。実装位置は `code` → severity マッピングと同じレイヤ（[engine-client/src/error.rs](../../../engine-client/src/error.rs) の `classify_*` 関数群、F-L9）に集約

### 2.4 ストレッチゴール（同フェーズ内で時間が許せば）

- マスタファイル（21MB ストリーム）のローカルキャッシュ（日次更新）

## 3. 非機能要件

### 3.1 セキュリティ

- ユーザー ID / パスワード / 第二暗証番号 / 仮想 URL 5 種は **すべて機密**（[SKILL.md R10](../../../.claude/skills/tachibana/SKILL.md)）
  - **Python が OS ファイルシステムで管理**（`tachibana_account.json` に user_id + is_demo、`tachibana_session.json` に仮想 URL 5 種 + `zyoutoeki_kazei_c`（課税区分コード）+ `saved_at_ms`）
  - **password はファイルに書かない**。tkinter ダイアログで毎回入力させるか、`DEV_TACHIBANA_PASSWORD` env（debug ビルドのみ）で供給する
  - Rust 側は creds / session を一切保持しない（keyring 不使用）。IPC で creds を送受信しない
- **第二暗証番号は Phase 1 では収集も保持もしない（F-H5）**: DTO スキーマ上は `second_password: Option<SecretString>` を切るが、Phase 1 では Rust 側の収集 UI も Python 側のメモリ保持も実装せず、常に `None` を送る。発注しないものを保持して攻撃面（コアダンプ・スワップ・GC 残存）を増やさない。Phase 2（発注機能）で値の収集・保持を有効化する。スキーマは破壊変更にならないため移行コストはない
- ログ出力時は仮想 URL のホスト部分まで `***` マスク（`tachibana_session.json` に保存される夜間閉局まで有効な 1 日券のため、URL がリークしても session 侵害にはなりうる）
- **`DEV_TACHIBANA_*` env を読むのは Python 側 `tachibana_login_flow.py` のみ**（B1）。Rust 側に `#[cfg(debug_assertions)]` の env 取込みコードは追加しない。release ビルドでは Python 側でも env を完全無視する（`os.getenv` 経路を `if not RELEASE_BUILD` でガード、判定は親プロセス（Rust）から `stdin` 初期 payload 内のフィールド `dev_tachibana_login_allowed: bool` として渡す（env 経路ではなく stdin payload で受け取る、architecture.md §2.1 H-2 修正と整合）。pin: invariant-tests.md `F-DevEnv-Release-Guard`（既存テスト `python/tests/test_tachibana_dev_env_guard.py` を release ビルドの完全ガード assert として登録、本体追記は別 implementer 担当）。**Phase 1 では `DEV_TACHIBANA_SECOND_PASSWORD` という env 名自体を採用しない**（F-H5: 第二暗証番号は Phase 1 で収集も保持もしないため、env 経路に存在させる必要がない。`os.getenv("DEV_TACHIBANA_SECOND_PASSWORD")` 等の呼出を Python 側に書かない）。Phase 2 着手時に env 名を改めて確定する
- **`DEV_TACHIBANA_DEMO` の既定値は `true`**（F-Default-Demo）。env 未設定でも demo URL を叩く。本番 URL を許可するには `TACHIBANA_ALLOW_PROD=1` を併用する必要があり、その判定は **`python/engine/exchanges/tachibana_url.py` 内 1 箇所だけ**で行う（F-L1）。SKILL.md S2 で旧表記されていた `DEV_IS_DEMO` / `TACHIBANA_USER_ID` / `TACHIBANA_PASSWORD` は採用しない
- **`BASE_URL_PROD` 定数の所在は 1 ファイル限定（F-L1）**: 本番 URL リテラル `kabuka.e-shiten.jp` を持てるのは `python/engine/exchanges/tachibana_url.py` の冒頭定義 1 箇所のみ。Rust 側は本番 URL を持たない（Python から venue 設定経由で受け取る）。`tools/secret_scan.sh` の allowlist もこの 1 ファイルのみとする

### 3.2 セッション寿命と復旧

- 仮想 URL は **夜間閉局までの 1 日券**。閉局後は電話認証からやり直し（自動化不可）
- セッション切れ（`p_errno="2"`）検知時:
  - Python 側は `VenueError{venue:"tachibana", code:"session_expired", message, request_id:None}` を発出して全立花購読を停止（旧 `EngineError{code:"tachibana_session_expired"}` 表記は廃止、F1）
  - **バナー文言は Python 側が `message` フィールドに込める**（F-Banner1）。Rust UI は受け取った `message` をそのまま表示するだけで、Rust 側に固定文言を持たない。これにより:
    - 文言の正本が立花アダプタ 1 箇所に集約される（venue 固有の事情を venue コードが持つ原則）
    - 状況別の細分化（夜間閉局 / マスタ未読通知 / 認証失敗 / レート制限など）を Rust UI 側のコード変更なしで Python から出し分けられる
    - 将来の i18n も Python 側の責務として一本化できる
  - Rust 側は **`VenueError` を「バナー表示 + 該当 venue の購読停止状態への遷移」というレンダラ的な扱い**にする。`code` で UI の severity（warning / error）と再ログインボタンの出し分けだけ判定し、文言生成はしない
  - **購読中の runtime では自動再ログインを試みない**（電話認証が前提のため）。定期的な `validate_session` ポーリングも**実装しない**（runtime 中に切れを検知した場合の対処が再ログイン禁止と矛盾するため、検知は subscribe 経路の `p_errno=2` だけに任せる）
  - ただし **アプリ起動直後の session 復元フェーズ**に限り、`tachibana_session.json` の session 検証が失敗した場合は `user_id/password` による再ログインを 1 回だけ試してよい。ここで成功した session を再永続化する（`tachibana_session.json` を上書き保存）
  - **夜間閉局またぎ運用（F-m1）**: アプリを起動しっぱなしで翌日のザラ場開始を迎えた場合、最初の subscribe で `p_errno=2` を踏むのは仕様通り。Python 側は `VenueError{code:"session_expired"}` を返し、Rust UI は再ログインバナーを表示する。**ここで自動再ログインはしない**（電話認証完了の確認が取れないため）。ユーザーがバナーから再ログイン操作を行うと、`Command::RequestVenueLogin` → Python が `tachibana_session.json` をクリアして `startup_login` を再実行する経路を辿る
  - **「自動」と「手動（ユーザー明示）」の境界（LOW-3）**: 「自動再ログイン禁止」とは *Python / Rust がユーザー操作なしにパスワードを再送する*ことを禁止する。ユーザーがバナーから「ログイン」ボタンを押して `Command::RequestVenueLogin` が発火する経路は「ユーザー明示の再ログイン」であり禁止しない。実装者向け判別基準: **`RequestVenueLogin` コマンドの受信を起点とする経路 → 許可**、**Python 側内部ロジックが `p_errno=2` 検知後に自発的に再ログインを開始する経路 → 禁止**
  - **バナー「閉じる」(`Message::DismissTachibanaBanner` / `VenueEvent::Dismissed`) の FSM 意味論（C-L1）**: `VenueState::Error{..} → Idle` の 1 遷移として `next()` テーブルに含まれる（`src/venue_state.rs::VenueState::next` の `(Error, Dismissed) → Idle` arm）。`Idle` / `LoginInFlight` / `Ready` で受けた場合は no-op（同状態を返す）であり副作用なし。Dismiss は keyring / Python セッション / 購読状態に触れないため、後続の `VenueError` 受信でバナーは再表示される（acknowledge セマンティクス）。既存 9 遷移にこの 1 遷移を加えた計 10 遷移が FSM 正本。
- **WebSocket 死活監視**: EVENT WS は **5 秒周期で `p_evt_cmd=KP`（KeepAlive）frame** を送ってくる。Python 側は KP 受信をタイマリセットに使い、**12 秒（KP 2 回欠損相当 + 2 秒 jitter）以上 KP も含めて全 frame が来なければ切断とみなして再接続**する（定数 `_DEAD_FRAME_TIMEOUT_S = 12.0`）。WS は Python 側 (`tachibana_ws.py`、`websockets` ライブラリ) が担当する設計（architecture.md §4）のため、`websockets.connect(..., ping_interval=None)` でライブラリ側の自動 Ping 送信を無効化する（`ping_timeout` は設定しない）。立花サーバ側が送ってくる websockets-level の `Ping` フレームにはライブラリが自動で `Pong` を返す（`ping_interval=None` のとき自動エコーは有効）
- `VenueReady{venue}` は **冪等イベント**として扱う。session が新たに validate / 再ログインされるたびに送ってよく、Rust 側は最後に受信した状態を保持する。**Python サブプロセス再起動検知時（次の `Hello` 受信時）に限り `VenueReady` 状態をリセット**し、再 ready 後に既存購読の重複再送を行わないこと（`ProcessManager` 側で active subscriptions を 1 度だけ resubscribe する）。`EngineEvent::Disconnected` は ticker/stream 粒度であり venue 全体の状態管理には使わない（C3 修正、architecture.md §3 と整合）
- Python 単独再起動時（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 Python プロセス復旧プロトコル）は、**Python プロセスが自律的に `startup_login` を再実行**し（`tachibana_session.json` / `tachibana_account.json` から復元 → 必要なら再ログイン → `VenueReady` 送信）、Rust は `VenueReady` を待ってから metadata fetch / resubscribe を再開する。`SetVenueCredentials` の再送は行わない

### 3.3 整合性 / レイテンシ

- FD frame 受信→ Rust 描画キュー投入: 中央値 < 50ms（株式は暗号資産より頻度が低いので緩め）
- **板更新は FD ストリーム駆動が正**（FD frame ごとに `DepthSnapshot` を再生成、data-mapping §4）。REST `CLMMfdsGetMarketPrice` polling は (a) ザラ場前後の初回 snapshot 1 発、(b) FD WS が一定時間（KP 含めて 12 秒）無通信で再接続中のフォールバック時、(c) **`depth_unavailable` セーフティ発動時の polling fallback（10 秒間隔・上限 5 分）** のみ。**runtime の定期 polling は実装しない**
- **`depth_unavailable` セーフティ（MEDIUM-6、F-M12）**: FD WS 受信開始 30 秒以内に bid/ask キーが 1 件も来ない場合、`VenueError{code:"depth_unavailable"}` を発出して当該銘柄 depth を polling fallback に倒す。FD 情報コード未確定（[inventory-T0.md §11](./inventory-T0.md#11-fd-情報コード一覧f-m2a--f-h3)）の影響で板キーが永久に来ない事故を防ぐ
- **proxy 環境での WebSocket 制約（MEDIUM-2）**: Phase 1 は WS のみで `SetProxy` 経由の CONNECT が張れない場合は `VenueError{code:"transport_error"}` を返して立花 venue を非活性化する。HTTP long-poll fallback は Phase 2 で必須化
- マスタダウンロードは **起動時 1 回 + 日次更新のみ**、各 ticker subscribe ごとには走らせない。**kick タイミングは `VenueReady` 受信直後**（`sUrlMaster` が必要なため）。完了は `ListTickers` 応答到着で判定（F-H6、`VenueReady` 自体には含めない）
- 立花 venue の `ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe` は **`VenueReady` 前に送らない**。`Ready` 直後に sidebar が自動 metadata fetch する既存導線があるため、Rust 側に venue-ready ゲートを追加する
- マスタキャッシュを永続化する場合は、Python が保存先を推測しない。**Rust から `config_dir` または `cache_dir` を起動時に明示的に渡す**

### 3.4 ベースライン計測

[docs/plan/✅python-data-engine/benchmarks/](../✅python-data-engine/benchmarks/) に立花用ベースラインを別ファイルで追加。暗号資産 venue と統合 CPU 比較は意味が薄いため、**立花単体での FD 受信スループット・メモリ使用量** を計測する。

## 4. 受け入れ条件（Phase 1 完了の定義）

> **二段階受け入れ（FD ブロッカー条件分岐）**: FD 情報コード（`DV` / `GAP*` / `GBP*` / `GAV*` / `GBV*` / `DPP:T` / `p_date` 等）が T1 着手前に [inventory-T0.md §11.3](./inventory-T0.md#113-ブロッカー解消記録b3-クローズ) のいずれかで実体解決した場合は **A 系（フル受け入れ）** を満たす。3 案で「Phase 縮退」を選んだ場合のみ **B 系（縮退受け入れ）** に切り替え、本節を改訂してから Phase 1 完了とする。data-mapping §3 注記、implementation-plan T0.1 ゲート、リスク表「FD 情報コード未確定」と整合。

### A 系（フル受け入れ、FD ブロッカー解決済み時）

1. デモ環境で `DEV_TACHIBANA_*` 設定 → debug ビルド起動 → `tachibana_account.json` / `tachibana_session.json` 保存 → 再起動でファイルキャッシュ復元、までが手動で確認できる（demo 環境にも夜間閉局があるため、CI demo ジョブは閉局帯（demo の運用時間 = 平日 8:00–18:00 JST 想定、確定値は T2 で実機確認）を避けてスケジュールする）
2. 任意の主要銘柄（例 `7203` トヨタ）を ticker selector から選び、日足チャート + 直近 trade（FD 由来）+ 10 本気配 snapshot が表示される
3. ザラ場時間中、FD frame ストリームが `Connected` → trade イベントを継続配信できる（10 分以上連続稼働、drop なし）
4. 閉場時間に subscribe しても `Disconnected` → 「市場時間外」状態で UI が破綻しない
5. Python が異常終了しても、Rust が指数バックオフで再起動しセッションを再注入、UI から見て自動復旧する
6. **本番 URL `kabuka.e-shiten.jp` がデフォルト設定では絶対に呼ばれないこと**（CI ジョブで `grep -E "kabuka\.e-shiten"` + ユニットテストで Python 側 URL 切替ロジックを検証。pre-commit / CI 双方で同一スクリプトを呼ぶ。重複定義を避けるため正本は `tools/secret_scan.sh` に置く — T7 で実装。**ただし `BASE_URL_PROD` 定数定義ファイル 1 箇所（`python/engine/exchanges/tachibana_url.py` の先頭数行）だけは allowlist で除外**し、それ以外からのリテラル出現を全て失敗させる、F11）
7. ログとエラー応答に `sUserId` / `sPassword` / `sSecondPassword` / 仮想 URL の生値が現れないことを `tests/secret_redaction.py` で検証
8. 起動時の session 復元に失敗した場合のみ再ログインが 1 回実行され、購読開始後の `p_errno="2"` では再ログインせず UI を明示エラーに遷移させる

### B 系（縮退受け入れ、FD ブロッカー未解決時のみ適用）

A 系の項目 2 / 3 を「日足 chart + ticker stats のみで表示が成立すること」に置換し、trade ストリーム / 10 本気配 / 10 分連続稼働は **Phase 1 完了条件から外す**（Phase 2 で FD コード確定後に復活）。項目 1 / 4 / 5 / 6 / 7 / 8 はそのまま適用。本節を「B 系適用」と書き換える PR が implementation-plan T0.1 ブロッカー解決 PR と紐付き必須。

> **T35-U5 E2E 注記**: U5 E2E (`tests/e2e/tachibana_relogin_after_cancel.sh`) は `src/replay_api.rs` 着地まで CI で `exit 77` skip 扱いとし、A 系受け入れ判定からは除外する（HTTP API 着地後に完走、T3.5 Step F 整合）。
