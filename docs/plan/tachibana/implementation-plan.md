# 立花証券統合: 実装計画

親計画 [docs/plan/✅python-data-engine/implementation-plan.md](../✅python-data-engine/implementation-plan.md) のフェーズ 6 完了後、または並行で着手する追加トラックとして位置づける。

## フェーズ T0: 既存型棚卸し + 仕様凍結 + スキーマ拡張（2〜3 日）

**ゴール**: IPC スキーマに立花対応の差分を入れ、Rust / Python 両側で型ビルドが通る。**着手前に既存型の影響範囲を grep で表に書き出す**。

### T0.1 既存コード棚卸し（先に必ず実施）

- [x] `git grep -n "TickerInfo"` / `HashMap.*TickerInfo` / `HashSet.*TickerInfo` の参照箇所を全数表化。`#[derive(Hash, Eq)]` 入りでフィールドを増やす影響を見積もる
- [x] `git grep -nE "MarketKind::(Spot|LinearPerps|InversePerps)"` で網羅 match の箇所を全部リストアップ（`exchange` / `engine-client` / `data` / `src` 配下）
- [x] `Ticker::new` ([exchange/src/lib.rs:281-291](../../../exchange/src/lib.rs#L281)) の `assert!(ticker.is_ascii())` を確認し、`130A0` 等が通ることをユニットテストで実機確認
- [x] **既存 `Timeframe` の serde 形式は `"D1"`（変種名）であることを確認済み**（F-m2、F-H1）。[exchange/src/lib.rs:67-83](../../../exchange/src/lib.rs#L67-L83) は `#[derive(Serialize, Deserialize)]` のみで `#[serde(rename = ...)]` 無し。`Display` は `"1d"` を返すが serde は別系統。**T0.2 で `#[serde(rename = "1d")]` 等の rename 属性を全変種に追加する必要がある**（既存暗号資産 venue 経路で IPC を通っている場合は変換層の有無を grep で先に棚卸し）
- [x] **`qty_in_quote_value` 呼出箇所の棚卸し**（F-H4、H1 修正）: [exchange/src/adapter.rs:53](../../../exchange/src/adapter.rs#L53) が正本。呼出は **9 箇所**（行番号付き全数表は [inventory-T0.md §4](./inventory-T0.md#4-qty_in_quote_value-呼出箇所f-h4)）。`MarketKind::Stock => price * qty` を enum 内部分岐で強制すれば呼出側コード変更不要
- [x] **`EngineEvent::Disconnected` の shape は確認済み**（F-H2）: [engine-client/src/dto.rs:201-208](../../../engine-client/src/dto.rs#L201) で既に `{ venue, ticker, stream, market, reason: Option<String> }`。**DTO 追加は不要**、`reason: "market_closed"` は文字列規約として `events.json` schema に記載するだけで足りる
- [x] `ProcessManager` ([engine-client/src/process.rs](../../../engine-client/src/process.rs)) の proxy 保持パターンを読み、credentials 保持の **mutex / Arc 戦略を T0.2 のうちに確定**（F-m4）。proxy が `Arc<Mutex<Option<Proxy>>>` ならそれに揃える、`watch::channel` ならそれに揃える、と決め切る
- [x] `src/screen/` の現在構造を確認し、立花ログイン UI の追加先（既存 `login.rs` 拡張 or 新ファイル）を T0 のうちに暫定確定（F-m3）
- [x] `python/tests/test_*_rest.py` のモック方式（`pytest-httpx` / `HTTPXMock`）が他 venue で稼働中であることを確認
- [x] [docs/plan/✅python-data-engine/schemas/](../✅python-data-engine/schemas/) の `commands.json` / `events.json` が実在することを確認（実在を確認済み）
- [ ] 🔴 **FD 情報コード一覧抽出（F-M2a、F-H3、B3 再オープン、HIGH-2 ゲート、C1）— T0 完了マーカは `[ ]` のまま据え置く**。本項目を `[x]` にできるのは [inventory-T0.md §11.3](./inventory-T0.md#113-ブロッカー扱いと対応方針b3-再オープン) の 3 案（PDF 同梱 / 実 frame キャプチャ / Phase 縮退）のいずれかを実体解決した PR のみ。確定コードは `DPP` / `KP`(*) / `ST`(*) / `SS`(*) / `US`(*) / `EC`(*) のみで `DV` / `GAK1..5` / `GBK1..5` / `GAS1..5` / `GBS1..5` / `DPP_TIME` / `DDT` は **未確認**（`api_event_if_v4r7.pdf` が `manual_files/` に未同梱）。(*) は `p_evt_cmd` 値であって FD frame data key ではない（inventory §11.2 の表を data key と evt_cmd で分割すること、L5）。**T5 着手の前提として 3 案のいずれかを実体解決必須**。T1 codec は確認済み data key (`DPP` のみ) の範囲で先行着手可。PR 説明文に解決証跡を必須記載

  > **明示ゲート規約（HIGH、ユーザー指摘ラウンド 7）**: 本項目は spec.md §2.1（live trade / 5 本気配 board の MVP 採否）/ §4 受け入れ条件（A 系 / B 系の選択）/ data-mapping §3（side 判定・timestamp・depth 生成の最終化）の **前提ゲート**である。本項目が `[x]` になるまで以下を**禁止**:
  >   - T5（trade/depth ストリーム実装）への着手と PR マージ
  >   - data-mapping §3 の FD コード仮仕様部分への破壊変更（仮値前提のリファクタは可）
  >   - spec.md §4 を「A 系適用」と確定させる文書 PR
  >
  > 解決時のチェックリスト:
  > 1. 採用案（PDF 同梱 / 実 frame キャプチャ / Phase 縮退）を [inventory-T0.md §11.3](./inventory-T0.md#113-ブロッカー扱いと対応方針b3-再オープン) に明記
  > 2. data-mapping §3 の暫定コード（`DV` / `GAK*` / `GBK*` / `DPP_TIME` / `DDT`）を実値で確定 or 縮退案では当該節を「Phase 2 へ繰越」と書換
  > 3. spec.md §2.1 の MVP / §4 受け入れを A 系 / B 系のどちらで確定させるかを 1 文で固定
  > 4. 上記 PR 説明文に「採用案 / 確定コード / spec 改訂行」を 3 点セットで記載
  > 5. 本行を `[x]` に書換（reviewer は上記 4 点のリンクを確認してマージ）

### T0.2 型・スキーマ追加

- [x] `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` を [exchange/src/adapter.rs](../../../exchange/src/adapter.rs) に追加
- [x] **`MarketKind::Stock` の `qty_in_quote_value` は enum 内部分岐で `price * qty` 強制**（F-M3b）。`size_in_quote_ccy` 引数を見ない実装にし、`Stock` 用ユニットテストで誤呼出（`size_in_quote_ccy=true`）でも常に `price*qty` になることを確認
- [x] **`secrecy = "0.8"` を `engine-client` / `data` の Cargo.toml に追加**（F-B1）。`SecretString` は **Rust 内部保持型**でのみ使い、IPC 送出時は `expose_secret()` 経由でプレーン `String` 化した送出専用 DTO（後述 `*Wire`）に写像する
- [x] **`zeroize = "1"` を `engine-client` の `Cargo.toml` に追加し、Wire 型 secret フィールドを `Zeroizing<String>` で保持（M4、MEDIUM-B2-2）**: **現状の [engine-client/src/dto.rs:133-162](../../../engine-client/src/dto.rs#L133-L162) は `TachibanaCredentialsWire` / `TachibanaSessionWire` 共にプレーン `String` でマージ済み**。`Zeroizing<String>` 化は型置換だけで足りる。**実装時の修正（M4-impl）**: zeroize 1.8 の `Zeroizing<T>` は `Serialize`/`Deserialize` を `Deref` 透過では提供しない（`zeroize` クレートの `serde` feature が必須）。本リポジトリでは workspace `Cargo.toml` で `zeroize = { version = "1.8", features = ["serde"] }` を有効化して採用する（旧記述の「serde feature 不要」は誤り）。Wire DTO の field 型を `String` → `Zeroizing<String>` に置換するだけで、JSON 出力フォーマットは不変。 `TachibanaCredentialsWire.password` / `TachibanaSessionWire.url_*` を `Zeroizing<String>` で持ち `Drop` 時のゼロ化を保証。Wire 値はスコープ最小化（serialize 直後に明示 drop）の規約を `engine-client/src/backend.rs` の `SetVenueCredentials` 送信パスに `// SAFETY-LITE: Wire は serialize 後即 drop — Zeroizing が Drop 時にゼロ化する` コメントで記す。テスト: `engine-client/tests/wire_dto_drop_scope.rs` に (a) `std::mem::needs_drop::<TachibanaCredentialsWire>()` が `true`、(b) `SetVenueCredentials` 送信関数が Wire 値を値渡し（move）で受け取ること（`&` 参照渡し禁止）を確認。ヒープ実メモリのゼロ化検証は OS 依存で不安定なため省略
- [x] `QuoteCurrency` enum を新設（`Usdt`/`Usdc`/`Usd`/`Jpy`、`Copy + Hash + Eq + Serialize + Deserialize`）。**`Default` は実装しない**（F-M6a）。`&'static str` は使わない（serde ラウンドトリップ不可）
- [x] `TickerInfo` に `#[serde(default)]` 付きで `lot_size: Option<u32>` と `quote_currency: Option<QuoteCurrency>` を追加（F13/F-M6a）。`TickerInfo` の `Copy` 制約を壊さない（`String` 追加禁止）。**`None` 復元時は読み込み層で `Exchange::default_quote_currency()` を使って `Some(_)` に正規化**し、UI フォーマッタへは常に `Some` で渡す
- [x] `Exchange::default_quote_currency(&self) -> QuoteCurrency` を `exchange/src/adapter.rs` に実装（暗号資産 venue は USDT/USDC、`TachibanaStock` は `Jpy`）
- [x] **既存永続 state の serde 互換性確認**（F13/F-M4）— [exchange/tests/ticker_info_state_migration.rs](../../../exchange/tests/ticker_info_state_migration.rs) で旧 `TickerInfo` payload (lot_size / quote_currency 欠如) が `serde(default)` 経由で読めることを検証。Hash 影響範囲は inventory-T0.md §1.2 にて「永続化されているのは `data/src/layout/pane.rs` の `ticker_info` フィールドのみ、`HashMap` キーは in-memory のみ」と確定済み: dashboard 設定ファイル / `state.rs` に `TickerInfo` が保存されているか `git grep` で特定。`#[serde(default)]` で missing field が読めることに加え、**`Hash` 値変化により既存 `HashMap<TickerInfo, _>` のキー突合が壊れないか**を実機テスト。受け入れ条件に「旧 `state.json` を起動 → pane 復元 → ticker 表示」を追加
- [x] **日本語銘柄名の運搬経路を確定**: `EngineEvent::TickerInfo.tickers[*]` は `Vec<serde_json::Value>` のまま（[engine-client/src/dto.rs:279](../../../engine-client/src/dto.rs#L279)）であり、Python 側が `display_name_ja` キーを各 ticker dict に詰めれば追加 schema 不要で運搬可能。Rust UI 側は将来 `HashMap<Ticker, TickerDisplayMeta>` で別管理する方針を inventory に確定（実 UI 配線は T4 で実装）
- [ ] **類似プロジェクト `C:\Users\sasai\Documents\flowsurface` の先行実装を参考にする（M9 決定）**:
  - `flowsurface/exchange/src/adapter/tachibana.rs:625-684` の `MasterRecord` 型を踏襲し、Python 側 `tachibana_master.py` のレコード型に **`sIssueName` / `sIssueNameRyaku` / `sIssueNameKana` / `sIssueNameEizi` の 4 種**を全て保持する（Phase 1 で全部使わなくても、後続フェーズの検索 UI で活きる）
  - `display_symbol` には **`sIssueNameEizi`（英語名 ASCII）を採用**。28 文字を超える場合は切詰め、空または非 ASCII なら `None` フォールバックして `Ticker::new_with_display` にデフォルト動作させる（`Ticker` の ASCII 制約を回避）
  - `display_name_ja` には `sIssueName` を入れる。flowsurface 側はまだ `display_name_ja` 経路を持たない（英語名の display_symbol で済ませている）ため、本計画はそこから一歩進む。`Ticker` には英語名・別管理の `TickerDisplayMeta` には日本語名、というルーティング
  - Rust 側 UI ラベルのフォールバック順序: `display_name_ja` → `display_symbol`（英語名）→ `ticker.symbol`（4 桁コード）。3 段フォールバックは flowsurface 側にも明示的にはないので本計画で新規規約として固定
  - **`display_name_ja` の events.json schema 明記**: 「Python 側 typo（`display_name_jp` 等）でサイレント失敗」を防ぐため、`docs/plan/✅python-data-engine/schemas/events.json` の `TickerInfo` entry の各 ticker オブジェクト形に `display_name_ja: string?` を追記し、Python 単体テストで「key 名が `display_name_ja` であること」を assert（M9 / 元 M9 ペンディング解消）
- [x] `engine-client` DTO に下記を追加し `schema_minor` を bump（F1, F6, F-B1, F-B2）（schema 1.1 → 1.2）:
  - `Command::SetVenueCredentials { request_id: String, payload: VenueCredentialsPayload }` — `payload` は typed enum（`VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire)`）。`serde_json::Value` は使わない
  - **2 層 DTO 構造**（F-B2、**C2 修正反映**）: 内部保持型 `TachibanaCredentials`/`TachibanaSession`（`data` クレート、`SecretString` 保持、`Debug` 手実装マスク、`Serialize` 持たない、`Deserialize` のみ keyring 復元用に持つ） / 送出用 Wire DTO（`engine-client` クレート、プレーン `String`、`Debug` 手実装マスク）は **方向別に trait を分離**する: **`TachibanaCredentialsWire` は Rust→Python 一方向のため `Serialize` のみ**。**`TachibanaSessionWire` は `SetVenueCredentials`（Rust→Python）と `VenueCredentialsRefreshed`（Python→Rust）の双方向に出現するため `Serialize + Deserialize` の両方を派生**（architecture.md §2.1 C2 修正）。旧記述「Wire は `Deserialize` を持たない」は誤りであり、この行の旧表記を参照したコードに `Deserialize` を付け忘れないよう注意。送信時 `From<&TachibanaCredentials> for TachibanaCredentialsWire` で `expose_secret()` 経由の写像を 1 箇所に集約し、`Wire` は serialize 直後に drop
  - `TachibanaSessionWire.expires_at_ms: Option<i64>`（F-B3）。立花 API は明示的な期限を返さないため `None` を許容、`None` のとき起動時 `validate_session_on_startup` 必須
  - `EngineEvent::VenueReady { venue: String, request_id: Option<String> }`（**冪等イベント**、`request_id` は `SetVenueCredentials` との相関用。UI は初回 / 再送を区別しない）
  - `EngineEvent::VenueError { venue: String, request_id: Option<String>, code: String, message: String }` — 旧 `EngineError{code:"tachibana_session_expired"}` は廃止、`VenueError{code:"session_expired"}` に統一。**`message` は Python 側が user-facing 文言として詰める**（Rust 側は描画のみ、F-Banner1）。`code` の許容値（`session_expired` / `unread_notices` / `phone_auth_required` / `login_failed` / `ticker_not_found` …）は [architecture.md §6](./architecture.md#6-失敗モードと-ui-表現) の表に従い、`events.json` schema にも enum で列挙する
  - `EngineEvent::VenueCredentialsRefreshed { venue: String, session: TachibanaSessionWire }`
  - `EngineEvent::VenueLoginStarted { venue: String, request_id: Option<String> }` — Python が tkinter ログインヘルパーを spawn したことを Rust に通知（F-Login1、architecture.md §7.5）
  - `EngineEvent::VenueLoginCancelled { venue: String, request_id: Option<String> }` — ユーザーがダイアログをキャンセルした
  - `Command::RequestVenueLogin { request_id: String, venue: String }` — Rust UI から立花ログインを明示要求（architecture.md §7.5）
  - **UI ツリー DSL 型（`VenueLoginForm` / `VenueUiNode` 等）は追加しない**。Python が独立 tkinter ウィンドウを持つため、Rust に UI 構造を渡す必要が無い
  - `Ready.capabilities.venue_capabilities` のサブ構造（**Phase 1 は `serde_json::Value` のまま追加し、schema は Python 側で生成・Rust 側はパスを deserialize で読み出す方針で固定**、F-M8。typed 化は Phase 2 以降に再検討）。**capabilities 抽出ヘルパ `fn venue_capability<T: DeserializeOwned>(value: &Value, venue: &str, key: &str) -> Result<Option<T>, _>` を 1 箇所に集約**し、path 欠落 / 型不一致を `Result::Err` で返すユニットテストを T0.2 内で追加（F-M7）。silent false 倒れを禁止
  - `Timeframe` 全変種に `#[serde(rename = "...")]` を付与し IPC 形式を `"1m"`/`"1d"` 等の `Display` と一致させる（F-H1）。serde ラウンドトリップ・既存暗号資産 venue 経路の影響を `cargo test --workspace` で確認
- [x] **venue-ready ゲート方針を固定** — `VenueReady` イベントを `engine-client::dto::EngineEvent` に追加済み（idempotent、`request_id` 相関）。実 UI ゲートと resubscribe 集約は T3 で実装: `Ready` と `VenueReady` の役割を分離し、立花 venue の `ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe` を `VenueReady` 後まで待たせる。**`VenueReady` は「session 検証完了」のみを意味し、マスタ初期 DL 完了は含まない**（F12）。マスタ取得完了判定は `ListTickers` 応答到着で行う。`VenueReady` 再受信時に既存購読の重複再送が起きないよう `ProcessManager` 1 箇所で resubscribe を集約
- [x] **Python の保存先パス受け渡し方法を決定** — `stdin` 初期 payload に `config_dir` / `cache_dir` を追加する方針で暫定確定（軽量・既存 stdin payload `{port, token}` の自然な拡張）。実 wire-up は T4（マスタキャッシュ着手時）で実装: `stdin` 初期 payload 拡張（`{port, token, config_dir, cache_dir}`）を採用方針として暫定固定（軽量・既存パスの拡張で済む）。最終 OK は T0 レビューで
- [x] **env 変数名を venue prefix で確定（Phase 1 採用は 3 つ）**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO`。`DEV_TACHIBANA_SECOND_PASSWORD` は **Phase 1 では採用しない**（F-H5: 第二暗証番号は収集も保持もしない方針との整合）。Python 実装側でも `os.getenv("DEV_TACHIBANA_SECOND_PASSWORD")` 系の呼出を書かないことを規約とする。Phase 2 着手時に env 名を改めて確定する。SKILL.md S2/S3 の旧 `DEV_USER_ID` 系（架空ファイル前提）は本フェーズで SKILL.md 側を書き換える
- [x] [docs/plan/✅python-data-engine/schemas/commands.json](../✅python-data-engine/schemas/commands.json) / `events.json` / `CHANGELOG.md` 更新
- [x] **`request_id` の規約確定（LOW-1、F-L7、M1 修正、MEDIUM-4 修正）**: `Command::SetVenueCredentials` / `RequestVenueLogin` の `request_id` は **UUIDv4 文字列（RFC 4122）固定**。最大長 36 文字。Python 側は `uuid.uuid4().hex` ではなく `str(uuid.uuid4())` を使う（hyphen 入り）。`commands.json` / `events.json` の `$defs/RequestId`（および nullable 用 `RequestIdNullable`）に `pattern: ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` を記載済み（schema 1.2）。Rust 側の `oneshot::Sender` / `Notify` 索引は `request_id` 単位で行う。
  - **衝突時の挙動（MEDIUM-4 修正）**: 同一 request_id の二重送信は **`oneshot::Sender` を `HashMap<request_id, oneshot::Sender<()>>` に `try_insert` する箇所で reject** する（`ProcessManager` の credentials setter ではなく、`SetVenueCredentials` 送信後に waiter を登録するヘルパー関数内）。`try_insert` が `Err(OccupiedEntry)` を返したら caller が `request_id` を生成し直す（`Err(DuplicateRequestId)` を返す）。`set_venue_credentials` setter は credentials を `Vec` に書き込むだけであり、request_id の衝突判定責務を持たない（roles が異なる）。「最後送信が勝ち、古い waiter は drop」案は採用しない（`oneshot::Sender` drop 時の `RecvError` を `VenueError` に変換する経路まで設計しないと caller hang or panic を生むため）
- [x] **マスタキャッシュ路 path 受け渡し方式の確定（MEDIUM-4）**: `stdin` 初期 payload 拡張案を**確定**（暫定固定 → 確定済み）。commands.json には影響しない（stdin 形式は別領域）。[engine-client/src/process.rs](../../../engine-client/src/process.rs) の現行 stdin 書込み箇所に `config_dir` / `cache_dir` を追加するパッチは T4 着手時に書く。`dev_tachibana_login_allowed` は T3 で同 stdin payload に追加。本行は T0.2 の確定マークとして閉じ、後続 PR は確定済み方式に従う
- [x] **SKILL.md の同期（F-m5、唯一の正本タスク）**: `.claude/skills/tachibana/SKILL.md` の以下を本計画ベースで書き換える。README.md / spec.md 側の同種記述は本タスクへリンクする形に簡約済み:
  - L8 警告ブロック（旧 env 名と架空ファイル参照）
  - R3/R4/R6/R10
  - §Rust 実装の既存ヘルパー（架空 `tachibana.rs` 参照）
  - S1〜S6（架空 `src/screen/login.rs` / `src/connector/auth.rs` / `src/replay_api.rs` 参照）
  - 環境変数名: `DEV_USER_ID` 系 → `DEV_TACHIBANA_*`
  - 実装未完の参照は「将来実装予定（T3 で新設）」と但し書き
- [x] **`quote_currency` 正規化の実装位置を確定し、テスト追加（M1 再オープン）**: 正規化は **`TickerInfo::normalize_after_load()`（[exchange/src/lib.rs](../../../exchange/src/lib.rs)）に集約**。`Option<QuoteCurrency>` で `None` を返す deserialize 経路は (a) `data::layout::pane` の `saved-state.json` ロード時、(b) `engine_client::backend` で IPC 受信した `TickerInfo` を `exchange::TickerInfo` に詰め直す経路、の 2 箇所だが、(b) はすでに `TickerInfo::new()` 経由で `Some(default)` を埋めるため fold 不要。(a) のみ `normalize_after_load()` を呼ぶ規約とし、メソッドの doc コメントに 2 経路の関係を明示。受入: `exchange/tests/ticker_info_state_migration.rs` に「旧 payload (`quote_currency` キー欠落) → `normalize_after_load()` 後に `Some(Jpy)` / `Some(Usdt)` 等の venue 既定値が入っている」ケースを 2 件 + 既存値保持 1 件追加済み
- [x] **`VenueCredentialsPayload::venue_tag()` メソッド化（M2 再オープン、HIGH-B2-2）**: 現状 [engine-client/src/process.rs:247-257](../../../engine-client/src/process.rs#L247) の retain ロジックが variant 列挙ベースで、将来 venue 追加時にコンパイル網羅 OK のまま論理破綻する。**緊急度**: 現実装は variant 列挙ベースで、第 2 venue（例えば SBI / 楽天等）を追加した瞬間に retain 述語が「同 variant 以外を全部 drop」する論理バグへ転落する予備軍。Phase 1 の Tachibana 単一 venue では症状が出ないが、設計上の地雷であり**先行修正必須**。`impl VenueCredentialsPayload { pub fn venue_tag(&self) -> &'static str }` を追加し、`set_venue_credentials` を `store.retain(|p| p.venue_tag() != payload.venue_tag())` に書換。同時に Wire 構造に対する `Hash + Eq` は不要（venue 名 1 文字列で識別）。**受け入れ**: `engine-client/src/process.rs` 単体テストで (a) 2 種類の venue payload（`Tachibana` + 仮の 2 つ目 variant、テスト用 `#[cfg(test)]` で stub variant を追加してよい）を順に setter に渡すと `store.len() == 2` になること、(b) 同一 venue payload を 2 回投入すると `store.len() == 1` のまま（最後勝ち）であることを assert
- [x] **`python/engine/schemas.py` の同期確認（L8、M6 修正）**: `commands.json` / `events.json` 更新と同期して、Python 側 pydantic モデルが追加済みであること。実測: `grep -c "VenueReady\|VenueError\|VenueCredentialsRefreshed\|VenueLoginStarted\|VenueLoginCancelled\|RequestVenueLogin\|VenueCredentialsPayload" python/engine/schemas.py` → **14** (閾値 7 以上を満足)。tag フィールド `venue` の文字列値が両側で一致するテスト追加は T3 の `test_tachibana_login.py` に含める

---

### T0.2 受け入れ（2 段構造）

**個別作業の完了 ≠ フェーズ完了。以下を全て満たして初めて T0.2 完了とする。**

#### ステージ A: 個別作業（上記 `[x]` / `[ ]` で追跡）

上記の各行の `[x]` / `[ ]` で追跡する。

#### ステージ B: フェーズ完了ゲート（ステージ A 全 `[x]` 後に実施）

- [ ] `cargo check --workspace` 成功
- [ ] Python `pytest` 既存スイート緑
- [ ] `quote_currency` 正規化テスト緑（M1 / `exchange/tests/ticker_info_state_migration.rs` に 2 件）
- [ ] `venue_tag()` リファクタ後の `set_venue_credentials` 単体テスト緑（M2）
- [ ] Python pydantic / Rust DTO ラウンドトリップテスト緑（L8）
- [ ] `TickerInfo` serde 互換性テスト緑（`exchange/tests/ticker_info_state_migration.rs`）
- [ ] FD 情報コード §11（B3）: 案 1/2/3 いずれかを選択し PR 説明文に解決証跡を記載。案 3（縮退）を選んだ場合は §11.3「縮退時の計画更新リスト」を全実施済みであること
- [ ] **`request_id` 規約確定（LOW-1）**: `commands.json`/`events.json` の `request_id` フィールドに UUIDv4 の `pattern` 正規表現が記載済みであること
- [ ] **マスタキャッシュ path 確定（MEDIUM-4）**: `stdin` 初期 payload 拡張（`{port, token, config_dir, cache_dir, dev_tachibana_login_allowed}`）の文言が「暫定固定」から「確定」に書き換え済みであること（`dev_tachibana_login_allowed` は T3 で追加、`config_dir`/`cache_dir` は T4 で追加）
- [ ] **`quote_currency` 正規化実装位置確定（M1 再オープン）**: `pane.rs` ロード経路と IPC 受信経路の 2 箇所それぞれで `Exchange::default_quote_currency()` を folding する箇所がコードコメントで明示されているか、どちらか一方に集約する方針が本文に確定記載済みであること
- [ ] **`zeroize` 完了（M4）**: `engine-client/tests/wire_dto_drop_scope.rs` テスト緑、`TachibanaCredentialsWire`/`TachibanaSessionWire` secret フィールドが `Zeroizing<String>` であること
- [ ] **`TachibanaSessionWire` が `Serialize + Deserialize` を両方 derive（C2 修正）**: `VenueCredentialsRefreshed` の Python→Rust デシリアライズ経路の単体テスト緑であること
- [ ] **schema_minor 1.1→1.2 双方向 IPC ラウンドトリップテスト（HIGH-D2-2）**: 以下 2 ファイルを実装し、いずれも `pytest.mark.parametrize` / `#[rstest]` 等で `SetVenueCredentials` / `RequestVenueLogin` / `VenueReady` / `VenueError` / `VenueCredentialsRefreshed` / `VenueLoginStarted` / `VenueLoginCancelled` の 7 variant 全てを網羅すること:
  - `python/tests/test_schema_compat_v1_2.py::test_pydantic_accepts_rust_serialized_v1_2_variants` — Rust DTO（`engine-client/src/dto.rs`）で `serde_json::to_string` した出力 JSON 文字列を Python 側 `python/engine/schemas.py` の pydantic モデルが `model_validate_json` で受理できることを assert
  - `engine-client/tests/schema_v1_2_roundtrip.rs::test_rust_dto_roundtrips_v1_2_python_payloads` — pydantic モデルから `model_dump_json()` で生成した JSON 文字列を Rust 側 `serde_json::from_str` でパースでき、再 serialize しても意味的に等価（フィールド順序を除く）であることを assert

## フェーズ T1: Python ユーティリティ（2〜3 日）

**ゴール**: 立花 API を叩く下回りが単体で揃う。サーバ通信なしの単体テストでカバレッジ 80%。

- [ ] `python/engine/exchanges/tachibana_url.py`:
  - `build_request_url(base, json_obj, *, sJsonOfmt)` — REQUEST 用、`?{JSON 文字列}` 形式（SKILL.md R2）。**`sJsonOfmt` は必須キーワード引数**（HIGH-C1、R5 強制）。マスタ系 sCLMID（後述 `MASTER_CLMIDS`）は `"4"`、それ以外は `"5"` を呼出側が指定する。引数省略は `ValueError` を投げる。テストで `sJsonOfmt="4"` / `"5"` の両ケースおよび省略時 `ValueError` を検証
  - `build_event_url(base, params: dict)` — EVENT 用、`?key=value&...` 形式（R2 例外、`p_evt_cmd`/`p_eno`/`p_rid`/`p_board_no`/`p_gyou_no`/`p_issue_code`/`p_mkt_code`）
  - `func_replace_urlecnode(s)` — 30 文字置換（R9、`e_api_login_tel.py` サンプル出力と一致）
  - **`func_replace_urlecnode` の追加テスト（MEDIUM-D4）**: `test_replace_urlecnode_empty`（空文字 `""` 入力で `""` を返す）、`test_replace_urlecnode_full_roundtrip`（30 文字全置換対象を含む文字列で encode/decode のラウンドトリップが完全一致）、`test_replace_urlecnode_passthrough_alnum`（英数字のみ入力は素通り）の 3 ケースを `python/tests/test_tachibana_url.py` に追加
  - **builder 誤用ガード（MEDIUM-C4 / R2）**: `TachibanaSession` の URL を `RequestUrl` / `MasterUrl` / `PriceUrl` / `EventUrl` 等の NewType（`typing.NewType` または `dataclass(frozen=True)` 1 フィールド ラッパ）でラップし、`build_request_url` は `RequestUrl | MasterUrl` のみ、`build_event_url` は `EventUrl` のみ受理する型安全化を `tachibana_url.py` に実装。型不一致は `TypeError`。テスト 1 件追加
  - **多バイト fixture を必ず 1 ケース含める（M7 決定）**: `func_replace_urlecnode` 単体テストに「日本語 1 文字（例 `"あ"`）」「カナ 1 文字（例 `"ア"`）」「混在文字列（例 `"トヨタ自動車 7203"`）」のいずれか最低 1 ケースを追加し、Shift-JIS バイト列 → `%xx` 化のラウンドトリップを検証する。Phase 1 では multibyte query 送信を**実運用で**は発生させない方針だが、`func_replace_urlecnode` の正本実装は将来拡張に備えて先取りする。期待値はサンプルの規約（Shift-JIS エンコード後にバイト単位で `%xx`）に従い、`api_web_access.xlsx` の事例があれば優先採用
- [ ] `python/engine/exchanges/tachibana_codec.py`:
  - Shift-JIS デコード（`decode_response_body`）
  - `parse_event_frame(data: str) -> list[tuple[str, str]]`（`^A^B^C` / `\n` 分解）
  - `deserialize_tachibana_list(value)` — 空配列が `""` で返るケースの正規化（SKILL.md R8）
  - **Phase 1 で `deserialize_tachibana_list` 適用必須となる List 形状フィールド（MEDIUM-C2-1、R8）**: 下表の各 dataclass 該当フィールドで decode 直後に `deserialize_tachibana_list` を呼び、`""` が来たら `[]` に正規化する実装規約とする。**T1 受け入れに該当 dataclass の単体テストを 1 件ずつ追加**し、各フィールドに `""` を流しても `[]` で正規化されることを assert:

    | sCLMID | dataclass / レスポンス | List 形状フィールド |
    | :--- | :--- | :--- |
    | `CLMMfdsGetMarketPrice` | `MarketPriceResponse` | `aCLMMfdsMarketPriceData` |
    | `CLMMfdsGetMarketPriceHistory` | `MarketPriceHistoryResponse` | `aCLMMfdsMarketPriceHistoryData` |
    | `CLMAuthLoginRequest` | ログイン応答 | List 系フィールド全般（warning list / notice list 等、サンプル `e_api_login_response.txt` で List shape のものを T2 着手時に最終列挙し本表を更新する） |
- [ ] `python/engine/exchanges/tachibana_master.py` — `CLMEventDownload` ストリームパーサ（チャンク境界・`CLMEventDownloadComplete` 終端）
- [ ] **`CLMEventDownload` 終端 + chunk 境界エッジケーステスト（MEDIUM-C3-2）**: `python/tests/test_tachibana_master.py` に以下 3 ケースを `pytest.mark.parametrize` で網羅 — (a) `CLMEventDownloadComplete` 終端 frame の直前で chunk が `}` 直後に切れる（レコード単位できれいに切れる正常境界）、(b) 終端 frame の `}` 直前で切れる（閉じ括弧手前で chunk 分断）、(c) 通常レコードの途中で chunk が切れる（buffer に積まれて次 chunk と結合される）。いずれの場合もパース結果が同一の完全レコード列になり、`CLMEventDownloadComplete` を観測した時点でストリーム終了と判定されることを assert
- [ ] **ticker pre-validate（HIGH-3、F-M11、L1 修正、MEDIUM-6 注記）**: `tachibana_master.py` から取り出す `sIssueCode` を `re.fullmatch(r"[A-Za-z0-9]{1,28}", code)` で pre-validate し、逸脱したレコードは `warn!("tachibana: skipping invalid issue code: {!r}", code)` で skip。`[A-Za-z0-9]` で `|` は当然弾かれるため Rust `Ticker::new` ([exchange/src/lib.rs:281](../../../exchange/src/lib.rs#L281)) の 3 条件（length ≤ 28 / ASCII / `|` 不含）は全て先取りで満たす。Rust IPC 受信側も `engine-client/src/backend.rs` で `EngineEvent::TickerInfo.tickers[*]` の各 ticker dict を `Ticker::new` 呼出前に同条件で再 validate し、不正値は drop（panic させない）。テスト: 28 文字超 / 非 ASCII / `|` 含むケースをそれぞれ skip すること。
  > **Phase 2 拡張注意（MEDIUM-6）**: `[A-Za-z0-9]` は `Ticker::new` の実制約（ASCII / `|` 不含）より**厳しい**。Phase 1 立花株式マスタでは英数字のみのため実害なし。ただし Phase 2 で先物・OP マスタ（`CLMIssueMstSak` / `CLMIssueMstOp`）を追加する際に限月コード等でハイフン・スラッシュが来ると**サイレント skip**する。Phase 2 着手時にこの正規表現を `Ticker::new` の実制約（ASCII 制御文字・`|` のみ除外）に緩和することを Phase 2 タスクに記載すること

- [ ] **`EngineEvent::TickerInfo.tickers[*]` dict の Rust 受信側 warn 規約（MEDIUM-7）**: `engine-client/src/backend.rs` の `TickerInfo` 受信経路で、各 ticker dict から `display_name_ja` キーを取り出すとき、キーが存在しない場合は `tracing::debug!("tachibana ticker dict missing display_name_ja: {}", ticker_symbol)` を出す（warn ではなく debug — 暗号資産 venue は常に欠落するため常時 warn だとノイズ）。`display_name_ja` が存在するが `null` の場合は `None` として扱う（正常系）。Python 側のタイポ（`display_name_jp` 等）による全件 debug ログ噴出でキー名誤りを早期発見できる
- [ ] `p_no` 採番ヘルパ（**asyncio 単一スレッド前提の単純カウンタ**、Unix 秒初期化、Lock 不要、F18）と `current_p_sd_date()`（JST 固定、SKILL.md R4）
  - **既知バグ回避**: SKILL.md S6 表に「セッション復元と並行で走る history fetch が逆転して `p_no <= 前要求.p_no` エラー」が記載されている。Python 移植版では **session 復元（`SetVenueCredentials` 処理）の完了前に他リクエストを発行しない**直列化を `TachibanaWorker` 内で強制し、起動レース回帰テストを 1 件追加する
- [ ] エラー判定ヘルパ `check_response(payload) -> None | TachibanaError`（[SKILL.md R6](../../../.claude/skills/tachibana/SKILL.md)、`p_errno` 空文字＝正常を含む）
- [ ] **制御文字 reject（F-M6b）**: `build_event_url(base, params)` は値文字列に `\n` / `\t` / `\r` / `^A`〜`^C` を含む場合 `ValueError` を投げる。`build_request_url` も同様（JSON 値内の制御文字を pre-check）。SKILL.md 「EVENT URL に `\n` `\t` を入れない」の不変条件を呼出側ではなく builder 側で強制する。テストケース 1 件追加
- [ ] **`p_no` 採番の整理（F-L5）**: 採番カウンタ自体は `asyncio` 単一スレッド前提で Lock 不要。一方 SKILL.md S6 の「セッション復元と並行で走る history fetch が逆転」事案は別レイヤ（**`SetVenueCredentials` 処理中は他の業務リクエスト発出を抑止する直列化ゲート**）で解決する。両者を別関数に分離し、それぞれ単体テスト 1 件
- [ ] **受け入れ**: 上記モジュールを単体テストでカバー、サンプルレスポンス（`samples/e_api_login_tel.py/e_api_login_response.txt` ほか）から期待値抽出ができる。REQUEST URL と EVENT URL の差を別テストで検証。`conftest.py` 共通フィクスチャ（HTTPXMock 共通 base URL / WS server fixture）を整備（F-L3）。
  - **Shift-JIS decode 全経路必須（HIGH-C2 / R7）**: 全 REQUEST レスポンスは `httpx.Response.content` を `decode_response_body` に通すこと。`response.text` / `response.json()` の直叩きを禁止する実装規約とし、`tachibana_auth.py` / `tachibana.py` / `tachibana_master.py` の全 REQUEST 経路で遵守。CI ガード: `grep -rnE "\.text\b|\.json\(\)" python/engine/exchanges/tachibana*.py` の出現が 0（または allowlist コメント付きのみ）であることをチェック
  - **`urllib.parse` / `httpx.URL` 標準 encoder 使用禁止 lint ガード（MEDIUM-C2-2 / R9）**: 立花 API は SKILL.md R9 の独自 30 文字置換 (`func_replace_urlecnode`) が必須で、標準 URL encoder（`urllib.parse.quote` / `urlencode` / `quote_plus` / `httpx.URL(...)` の query 自動エンコード）を経由すると規約破綻する。CI lint ガードとして `grep -rnE 'from urllib\.parse|urllib\.parse\.(quote|urlencode|quote_plus)|httpx\.URL\(' python/engine/exchanges/tachibana*.py` の出現が 0（または明示的な allowlist コメント付きのみ）であることをチェック。さらに `build_request_url` / `build_event_url` の docstring に「**標準ライブラリ URL encoder への委譲は禁止。立花は SKILL.md R9 の独自置換テーブルを使う**」を 1 行明記する
  - **`check_response` 単体テスト（MEDIUM-C5 / R6）**: `p_errno=""`（空文字）と `p_errno="0"` の 2 ケースをいずれも正常扱い（`None` を返す）として assert。`p_errno="2"` 等は `TachibanaError` を返すことも併せて検証
  - **`p_sd_date` JST 単一化 CI ガード（MEDIUM-C8 / R4）**: `grep -rnE 'datetime\.now|time\.time' python/engine/exchanges/tachibana*.py` の出現が `current_p_sd_date` 内部以外で 0 であることを CI（lint ジョブ）でガード。`tachibana_master.py` のキャッシュ JST 日付生成等は `current_p_sd_date` 経由か allowlist コメント付き

## フェーズ T2: 認証フローと session 管理（2 日）

**ゴール**: `CLMAuthLoginRequest` 経由でデモ環境に対しログインできる。

- [ ] `python/engine/exchanges/tachibana_auth.py`
  - `login(user_id, password, is_demo) -> TachibanaSession`
  - `validate_session_on_startup(session, *, _latch: StartupLatch) -> bool`（**`CLMMfdsGetIssueDetail` で 1 銘柄（例: `sIssueCode="7203"`, `sSizyouC="00"`）を軽量リクエスト** — `sUrlMaster` に接続するマスタ系 API で最も引数が少ない。`CLMMfdsGetMasterData` は列指定が必要で返却量が多いため不採用。T2 実機確認で別 API が適切と判明した場合は本行を更新すること）— **「同時起動・重複起動を許さない」シングルフライト保証を `StartupLatch` 値渡しで実現**（M6 決定、HIGH-B 修正）:

    **設計変更（モジュールスコープ変数は採用しない）**: モジュールスコープの `_startup_validation_done: bool` は pytest が同一プロセスで複数テストを実行するため **テスト間で状態が漏洩**し、2 テスト目以降が必ず `RuntimeError` になる。さらに `TachibanaWorker` のライフサイクル（プロセス内で複数インスタンスを立てることは現在ないが将来の Python 単独モードで起こりうる）と噛み合わない。代わりに **インスタンスバウンドの `StartupLatch`** を採用する:

    ```python
    # tachibana_auth.py
    import asyncio

    class StartupLatch:
        """validate_session_on_startup が 1 度だけ実行されることを保証する latch。
        TachibanaWorker インスタンスごとに 1 つ持つ。pytest でも fixture reset で独立できる。"""
        def __init__(self) -> None:
            self._lock = asyncio.Lock()
            self._done = False

        async def run_once(self, coro):
            """coro を最初の呼出時のみ実行し、以降の呼出は RuntimeError で fail-fast する。
            並列呼出時はロックで直列化し、先行者が終わった後に後続は done=True を見て失敗する。"""
            async with self._lock:
                if self._done:
                    raise RuntimeError(
                        "validate_session_on_startup は 1 プロセスライフサイクル中に 1 度だけ呼べる。"
                        "runtime 経路から呼ばれた場合はプログラムのバグ（L6）。"
                    )
                try:
                    return await coro
                finally:
                    self._done = True

    async def validate_session_on_startup(session: TachibanaSession, *, _latch: StartupLatch) -> bool:
        return await _latch.run_once(_do_validate(session))
    ```

    - `TachibanaWorker.__init__` で `self._startup_latch = StartupLatch()` を持ち、`_latch=self._startup_latch` を渡して呼ぶ
    - **L6 修正（例外スコープ規約）**: この `RuntimeError` は内部不変条件違反（プログラマ向けクラッシュ）。上位 caller は catch せず、`engine/server.py` トップレベル supervisor で初めて catch して `tracing::error!` + プロセス終了させる。`VenueError.message` 経路には乗せない
    - **`finally: self._done = True` のセマンティクス（M2）**: validation コルーチンが例外を投げても `_done` が `True` になるため、**2 度目の `run_once` 呼出は常に `RuntimeError`**（失敗後の再試行も不可）。これは intentional — runtime 経路から再呼出しされること自体がプログラムのバグ（L6）。`login()` は `validate_session_on_startup` とは別関数なので latch に影響せず、起動時 fallback ログイン（`user_id/password` 再送）は別パスで実行される。
    - **テスト方針（M3）**: `StartupLatch` を直接 `conftest.py` フィクスチャで新規生成するため、テスト間の状態漏洩はゼロ。追加テスト:
      - `latch = StartupLatch()` を作り 2 回連続 `await latch.run_once(coro)` → 2 回目は `RuntimeError`
      - `asyncio.gather` 並列テスト: `return_exceptions=True` で 2 つの `run_once` を同時起動し、結果リストに `RuntimeError` が **ちょうど 1 件** 含まれることを assert（どちらが RuntimeError になるかは Lock 取得順次第のため、特定コルーチンの結果に `pytest.raises` を掛けない）
      - Mock サーバへの実 HTTP は 1 回のみ（`HTTPXMock.get_requests()` で確認）
      - 上記いずれも **モジュールスコープ変数を reset するフィクスチャ不要**
  - 二段エラー判定 + `sKinsyouhouMidokuFlg=="1"` で `UnreadNoticesError`
  - レスポンスから `sZyoutoekiKazeiC`（譲渡益課税区分）を `TachibanaSession` に保持（Phase 2 発注時に流用）
  - **`expires_at_ms` は `Option<i64>` で持つ**（F-B3）。ログイン直後は `None` 固定（立花は明示期限を返さないため）。`None` のとき `validate_session_on_startup` は必ず叩く（safe path）。`Some(t)` で `now > t` なら復元せず再ログインへ（fast path）。閉局時刻を `CLMDateZyouhou` から取得できることが確認できたら値を入れる方針は Phase 2 へ繰越
- [ ] **起動時のみ再ログイン**のガードを実装: `SetVenueCredentials` の session validation 中に限り `user_id/password` fallback を許可し、購読開始後の `p_errno="2"` は再ログインせず `VenueError{code:"session_expired"}` を返す
- [ ] mock サーバテスト（`pytest-httpx` の `HTTPXMock`、`python/tests/test_binance_rest.py` パターン踏襲）で正常系・異常系（`p_errno=-62` / `=2` / 認証失敗 / `sKinsyouhouMidokuFlg=1`）
- [ ] **`CLMAuthLoginRequest` の `sJsonOfmt="5"` 固定テスト（MEDIUM-C3-1）**: `python/tests/test_tachibana_auth.py::test_login_request_uses_json_ofmt_five` を追加。`HTTPXMock.get_request()` で `tachibana_auth.login(...)` 発行リクエストのクエリ JSON に `sJsonOfmt == "5"` が含まれることを pin（`CLMAuthLoginRequest` は `MASTER_CLMIDS` に含めず、`build_request_url` 呼出時に `sJsonOfmt="5"` を選択する前提を実機確認）。R5 強制ロジックの分岐（マスタ系 sCLMID = `"4"` / それ以外 = `"5"`）が login で正しく動くことを保証
- [ ] **仮想 URL スキーム検証（MEDIUM-C3-3）**: `tachibana_auth.py` のログイン応答パース時に `sUrlEventWebSocket.startswith("wss://")` を assert。`sUrlRequest` / `sUrlMaster` / `sUrlPrice` / `sUrlEvent` の 4 URL は `"https://"` を assert。違反時は `LoginError` を投げる。`python/tests/test_tachibana_auth.py::test_login_rejects_non_wss_event_url` 単体テストを追加（`sUrlEventWebSocket` が `ws://` の応答を返したとき `LoginError` で reject されること）
- [ ] **`sKinsyouhouMidokuFlg` 未読通知の固定テスト名（HIGH-C2-1、R3）**: `python/tests/test_tachibana_auth.py::test_login_raises_unread_notices_when_kinsyouhou_flag_set` を追加。mock 応答 `{"p_errno":"0", "sResultCode":"0", "sKinsyouhouMidokuFlg":"1"}` で `tachibana_auth.login(...)` が `UnreadNoticesError` を投げ、後続の IPC 経路で `VenueError.code == "unread_notices"` として発出されることを assert（architecture.md §6 失敗モード表の `unread_notices` 行と整合）
- [ ] **`validate_session` 実機リクエスト形式の固定テスト（HIGH-D2）**: `python/tests/test_tachibana_auth.py::test_validate_session_uses_get_issue_detail_with_pinned_payload` を追加。`HTTPXMock.get_request()` で **(a) URL が `sUrlMaster` ベースであること、(b) HTTP メソッドが GET、(c) クエリ JSON に `sCLMID="CLMMfdsGetIssueDetail"`/`sIssueCode="7203"`/`sSizyouC="00"` が含まれること、(d) `sJsonOfmt="4"` であること** を assert。リクエスト形式が将来書き変わったときに即検知できるピン留めテスト
- [ ] **`validate_session_on_startup` の `RuntimeError` → supervisor 統合テスト（MEDIUM-D2-1、L6 修正の検証）**: `python/tests/test_tachibana_startup_supervisor.py::test_runtime_error_from_validate_terminates_process_with_log` を新設。`subprocess` 経由で `python -m engine` を起動し、`StartupLatch.run_once` を 2 回呼ばせるテスト fixture を経由して 2 回目の `RuntimeError` を発生させ、(a) `engine/server.py` トップレベル supervisor で catch されてプロセスが exit code 非ゼロで終了、(b) stderr に `tracing::error!` 相当の 1 行が出ていること、(c) その error 行に `user_id` / `password` / session token などの creds 文字列が**含まれていない**こと、を assert
- [ ] **受け入れ**: `pytest -m demo_tachibana` で実 demo 環境ログイン成功（手動電話認証済みアカウント前提）

- [ ] **demo CI レーン方式の早期決定（MEDIUM、ユーザー指摘ラウンド 7）**: `pytest -m demo_tachibana` の CI 統合方式を **T2 着手時点**で以下の 3 案から選択し、本計画本文に確定記載（T7 まで先送りしない）:
  - **(A) non-blocking job**: PR チェックには加えるが緑必須にしない。落ちたら通知のみ。閉局帯（demo 運用時間外）は skip 判定が必要
  - **(B) manual lane only**: PR / push トリガから外し、`workflow_dispatch` で開発者明示起動のみ。最も保守的・推奨
  - **(C) CI 不採用**: ローカル実機検証のみ。`tests/e2e/tachibana_login_local.md` 手順書を整備
  
  決定根拠: [open-questions.md Q21](./open-questions.md#L25) で demo 環境の運用時間自体が未確定のため、ブロッキング CI 化はリリース終盤の不安定依存になる。Q21 の値が T2 で実機確認できるまでは案 (B) を暫定固定とし、T2 終了時に最終決定する。確定後は T7 のスケジュール起動有効化規約を本決定に揃える（手動トリガジョブのみ許可、または CI 不採用）

## フェーズ T3: クレデンシャル受け渡し配線（2 日）

**ゴール**: Rust が keyring からクレデンシャルを取り出し、Python が `VenueReady` を返すまで往復する。

- [ ] `data/src/config/tachibana.rs` 新設（**現リポジトリには存在しないことを確認済み**。`data/src/config/proxy.rs` の keyring 実装パターンを参考にする）:
  - `TachibanaCredentials { user_id, password: SecretString, second_password: Option<SecretString>, is_demo }` — **Phase 1 では `second_password` フィールドを DTO スキーマに切るが、UI からは収集せず常に `None` を送る**（F-H5）。発注しないのに保持する攻撃面を作らない。Phase 2 着手時に値の収集・保持を有効化（スキーマは破壊変更にならない）
  - **Phase 1 強制 None ガード（H2 修正）**: `From<&TachibanaCredentials> for TachibanaCredentialsWire` の写像関数冒頭で `debug_assert!(creds.second_password.is_none(), "second_password must be None in Phase 1 (F-H5)")` を入れる。release ビルドでは noop だが CI / debug ビルドで `Some(_)` 混入を即検知。さらに同関数の単体テスト 1 件「`Some(SecretString::new("dummy".into()))` を入れた `TachibanaCredentials` を写像すると debug ビルドで panic」を追加。Phase 2 着手時に `debug_assert!` を削除する
  - `TachibanaSession { url_request, url_master, url_price, url_event, url_event_ws, expires_at_ms, zyoutoeki_kazei_c }`
  - keyring 読み書き
- [ ] **keyring read/write roundtrip + Zeroize テスト（MEDIUM-D3-3）**: `data/tests/tachibana_keyring_roundtrip.rs::test_credentials_roundtrip_with_zeroize_and_masked_debug` を新設。(a) `TachibanaCredentials` を keyring に書込→読出して値が完全一致すること、(b) `format!("{:?}", creds)` の Debug 出力に `password` / session token の平文文字列が含まれず `"***"` 等のマスク表現になっていること、(c) `TachibanaCredentials` が Drop されるとき `Zeroizing<String>` / `SecretString` 経由で内部メモリがゼロ化される経路（`zeroize::Zeroize` impl が呼ばれること）を assert。テストは keyring 実体への副作用を避けるため `keyring::set_default_credential_builder(mock::default_credential_builder())` でモック差替え
- [ ] **Rust UI 側**: 立花のログイン画面コードは**追加しない**。`Venue::Tachibana` 関連で「ログインダイアログを別ウィンドウで表示中」「ログインがキャンセルされました」を表示する汎用ステータスバナー（既存 `VenueError.message` レンダラの拡張）だけ実装する
- [ ] **Python 側 `tachibana_login_dialog.py`** を新設（F-Login1、architecture.md §7.4）。`python -m engine.exchanges.tachibana_login_dialog` で起動できる単独実行可能スクリプト。tkinter で `Toplevel` モーダルを構築、stdin から JSON 起動引数を読み、stdout に結果 JSON を返して exit。立花固有のラベル・順序・警告ボックス（電話認証・デモ環境）はこのファイルに直書き
- [ ] **Python 側 `tachibana_login_flow.py`** を新設。データエンジン側で `asyncio.create_subprocess_exec(sys.executable, "-m", "engine.exchanges.tachibana_login_dialog", ...)` で tkinter ヘルパーを spawn し、stdout を JSON parse、`tachibana_auth.login(...)` を実行、結果に応じて `VenueReady` / `VenueError` / `VenueLoginCancelled` を IPC 送信
- [ ] Python 側の発火タイミングを実装: (a) `RequestVenueLogin` 受信、(b) `SetVenueCredentials` 認証失敗、(c) keyring session 失効検知（起動時のみ） — いずれも `tachibana_login_flow` を呼ぶ。失敗 3 回で `VenueError{code:"login_failed"}` で諦める
- [ ] Rust UI: 立花機能を最初に開く操作（`Venue::Tachibana` ticker selector を開く / 立花 pane 追加）で `Command::RequestVenueLogin{ venue:"tachibana" }` を発火
- [ ] **キャンセル後の再試行導線（F-M1a、H3 修正）**: `VenueLoginCancelled` 受信後の Rust UI 状態は「立花未ログイン」固定。**ボタン配置は `VenueReady` 前でも到達可能な経路に置く**こと（`VenueReady` 前は ListTickers が空 = 立花 ticker selector / pane が空 or 非表示の可能性があり、そこにボタンを置くとデッドロックする）。具体的には:
  - **第 1 候補**: サイドバー（[src/screen/dashboard/sidebar.rs](../../../src/screen/dashboard/sidebar.rs)）の venue リスト項目「Tachibana」のホバー時アクションまたは項目右端のアイコン。Venue リスト自体は `VenueReady` 状態に依らず常時描画されている前提（`Venue::ALL` ベース）
  - **第 2 候補（フォールバック）**: メインウィンドウ上部のステータスバナー領域に「立花未ログイン」表示中のみ「ログイン」ボタンを表示
  - **禁止**: 「立花 ticker selector を開かないと押せない」「立花 pane を作らないと押せない」配置（VenueReady ゲートと矛盾）
  - 押下で `RequestVenueLogin` を発火。1 箇所のみ（複数経路で発火させない）
- [ ] **debug ビルドの env 自動入力は Python 側で処理**（architecture.md §7.7）: `tachibana_login_flow` が `DEV_TACHIBANA_*` env をチェックし、揃っていれば tkinter ヘルパーを spawn せずに直接 `tachibana_auth.login(...)` を実行する fast path を入れる。env 一部欠損ならヘルパーにプリフィルとして渡す。Rust 側の `#[cfg(debug_assertions)]` env 取り込みは**不要**（経路が Python 側に閉じる）
- [ ] **`dev_tachibana_login_allowed` フラグを `stdin` 初期 payload に追加（H-2、architecture.md §2.1.1）**: Rust は `#[cfg(debug_assertions)]` で `true` / release で `false` を `stdin` JSON に含める。Python 側は `dev_tachibana_login_allowed` が `false` のとき `os.getenv("DEV_TACHIBANA_*")` を読まずスキップする（release ビルドの完全ガード）。`stdin` 初期 payload のスキーマ: `{"port": N, "token": "...", "config_dir": "...", "cache_dir": "...", "dev_tachibana_login_allowed": bool}`（`config_dir` / `cache_dir` 自体の wire-up は T4（マスタキャッシュ着手時）で実装。T3 PR では `dev_tachibana_login_allowed` のみを追加し、`config_dir` / `cache_dir` は schema 上の placeholder として記載するに留める）。**同 PR で Python 側 `python/engine/__main__.py` の stdin payload parser に `dev_tachibana_login_allowed: Optional[bool]`（`config_dir` / `cache_dir` も `Optional[str]`）を追加**し、未指定時は `False` / `None` にフォールバックする後方互換ハンドリングを入れる
- [ ] **stdin payload 構築を `serde_json` 経由に置換（HIGH-B2-1）**: 現状 `engine-client/src/process.rs` の stdin 初期 payload は `format!` 文字列の手書きで組み立てられており、`config_dir` / `cache_dir`（Windows パス区切り `\`、空白、日本語ユーザ名）/ `token`（HMAC 共有秘密で `"` `\` 等を含み得る）が JSON-unsafe な文字列を含むとエスケープ事故を起こす。`dev_tachibana_login_allowed` を追加するこの T3 PR のタイミングで、`serde_json::json!({ "port": port, "token": token, "dev_tachibana_login_allowed": flag })` + `serde_json::to_string()` 経路に置換する（T4 で `config_dir` / `cache_dir` を追加するときも同 JSON ビルダーに足すだけで済む）。受け入れ: stdin payload 組み立て箇所で `format!` による JSON 構築が残っていないこと（`grep -n 'format!.*"port"' engine-client/src/process.rs` が空）、`\` 含む Windows パス / 日本語混じり `config_dir` / `"` を含む token が Python 側 `json.loads(...)` でラウンドトリップする単体テストを `engine-client/tests/process_lifecycle.rs` に 1 件追加
- [ ] **L2 修正（デモ固定ラベル文言）を `tachibana_login_dialog.py` に実装**: `prefill.allow_prod_choice == false` のとき本番ラジオを非表示にし、代わりに「**デモ環境固定（本番接続には `TACHIBANA_ALLOW_PROD=1` env が別途必要です）**」ラベルを 1 行表示する。`tachibana_login_flow.py` は起動時に同旨を `tracing::info!` で 1 行出す（architecture.md §7.4 L2 修正対応）
- [ ] **tkinter ヘルパー異常終了時の挙動規定（LOW-2、F-L8）**: `tachibana_login_flow.py` の責務に以下を明記する。(a) ヘルパー stdout EOF（0 byte で閉じる）→ `VenueError{code:"login_failed", message:"ログインヘルパーが応答せず終了しました"}`。(b) ヘルパー非ゼロ exit → 同上 + `stderr` を `tracing::error!` に転記（creds は混じらない前提）。(c) 全体タイムアウト 10 分（`asyncio.wait_for`）→ ヘルパー `terminate()` 後 5 秒で `kill()`、`VenueError{code:"login_failed", message:"ログイン操作がタイムアウトしました"}`。(d) WM 強制クローズ（窓の × ボタン）はヘルパー側 `WM_DELETE_WINDOW` バインドで `{"status":"cancelled"}` を出してから exit するため `VenueLoginCancelled` 経路で OK
- [ ] **tkinter ヘルパーの単体テスト**: `subprocess.run([sys.executable, "-m", ..., dialog])` を pytest から呼び、`headless=true` の起動引数で実 GUI を出さずにバリデーション規則だけテストできる「テスト専用モード」を `tachibana_login_dialog.py` に実装。実 GUI 確認は `pytest -m gui` で手動
- [ ] [engine-client/src/backend.rs](../../../engine-client/src/backend.rs) で `SetVenueCredentials` 送信パスを実装（既存 `SetProxy` パターン踏襲、`backend.rs` の実在は `ls engine-client/src/` で確認済み）
- [ ] **`VenueError.code` → severity / アクション マッピングの集約（MEDIUM-5、F-L9）**: Rust 側で `code` 文字列 → `(Severity, ActionButton)` を返すテーブル駆動関数を [engine-client/src/error.rs](../../../engine-client/src/error.rs) に集約（例: `pub fn classify_venue_error(code: &str) -> VenueErrorClass`）。Banner レンダラはこの関数の戻り値だけを参照する。未知 code → `(Severity::Error, ActionButton::Hidden)` で fail-safe。テスト: [architecture.md §6](./architecture.md#6-失敗モードと-ui-表現) 表の全 code を網羅したテーブルテスト
- [ ] [engine-client/src/process.rs](../../../engine-client/src/process.rs) に **Tachibana credentials の保持と再送**を追加し、managed mode の再起動時に `SetProxy -> SetVenueCredentials -> VenueReady -> resubscribe` を一貫して実行する
- [ ] [src/main.rs](../../../src/main.rs) 起動シーケンスに「keyring 読込 → `ProcessManager` / 接続オブジェクトへ creds 注入 → SetVenueCredentials → VenueReady 待ち」を追加
- [ ] `VenueCredentialsRefreshed` を受けて keyring session を更新する処理を Rust 側に実装（起動時再ログイン成功時のみ発火）
- [ ] 立花 venue 用の metadata / subscribe 要求を `VenueReady` まで抑止する UI ゲートを追加（sidebar 初期 metadata fetch を含む）
- [ ] **Python 側 SecretStr の取扱い規約（MEDIUM-C6）**: Python 側 `SecretStr` は Drop ゼロ化を保証しない（言語制約、CPython は文字列を immutable / interning するため）。代わりに (a) tkinter ヘルパー subprocess の寿命を最小化（spawn → 認証 → 即 exit）、(b) `tachibana.py` 内で creds 文字列を変数経由で長時間保持しない（`TachibanaSession` は仮想 URL のみを保持し、`user_id` / `password` は authenticate 関数のローカル変数に閉じ込めて関数 return で破棄）、を実装規約として明文化する
- [ ] **VenueLoginCancelled 後の手動再ログイン E2E（MEDIUM-D3）**: `tests/e2e/tachibana_relogin_after_cancel.sh` を新設。HTTP API 経由で「(1) 立花 venue 初回オープン → `VenueLoginStarted` 観測 → ヘルパーへ cancel コマンド注入 → `VenueLoginCancelled` 観測、(2) 再ログインボタン押下相当の API → `VenueLoginStarted` がちょうど 1 件追加され、`VenueLoginCancelled` 直後に重複発火していないこと」を `flowsurface-current.log` の grep で検証
- [ ] **受け入れ**: debug ビルドで `.env` 設定 → 起動 → ログ「Tachibana session validated successfully」確認、再起動で keyring 復元動作。
  - **`dev_tachibana_login_allowed` 統合テスト（HIGH-D1）**: `python/tests/test_tachibana_dev_env_guard.py`（`dev_tachibana_login_allowed=false` のとき `DEV_TACHIBANA_*` env が全て揃っていてもログイン fast path が起動せず tkinter ヘルパー spawn 経路に落ちることを `tachibana_login_flow` 単体で検証）と `engine-client/tests/dev_login_flag_release.rs`（release プロファイル相当のビルドフラグで `ProcessManager` の stdin payload に `dev_tachibana_login_allowed: false` が含まれることを assert）の 2 ファイルを実装し、`cargo test` / `pytest` 両方で実行

## フェーズ T4: マスタ・銘柄一覧・履歴 kline（2〜3 日）

**ゴール**: 起動後に銘柄を選び、日足チャートが表示される（trade/depth はまだ無い）。

- [ ] **マスタ DL の kick タイミングを確定（F-H6、MEDIUM-5 修正）**: `VenueReady` 受信直後に `TachibanaWorker._ensure_master_loaded()` を 1 回だけ `asyncio.create_task` で kick する。`list_tickers` / `fetch_ticker_stats` は内部で `await self._ensure_master_loaded()` を呼んで完了を待つ。**`VenueReady` 自体はマスタ DL 完了を含まない**（spec.md §3.3、F12）が、UI 側は `ListTickers` 応答到着時点で「マスタ取得完了」とみなしてよい。
  - **重複 kick の race 防止（MEDIUM-5 修正）**: `asyncio.Event` だけでは「まだ `set()` 前 → 並列呼出が 2 本とも DL 開始」する race がある。正しい実装は `asyncio.Lock` + `asyncio.Event` の組合せ:
    ```python
    async def _ensure_master_loaded(self) -> None:
        if self._master_loaded.is_set():
            return                          # fast path: 完了済み
        async with self._master_lock:       # Lock で直列化
            if self._master_loaded.is_set():
                return                      # double-checked: 先行者が完了済み
            await self._download_master()
            self._master_loaded.set()
    ```
    `self._master_lock = asyncio.Lock()` / `self._master_loaded = asyncio.Event()` を `TachibanaWorker.__init__` で初期化する。これにより並列呼出が来ても DL は 1 回だけ実行され、後続は Event 待ちに倒れる
- [ ] `tachibana.py::TachibanaWorker.list_tickers(market="stock")` — マスタ起動時 1 回ダウンロード→キャッシュ→`CLMIssueMstKabu` から ticker 配列を返す
- [ ] `TachibanaWorker.fetch_klines(timeframe="D1")` — `CLMMfdsGetMarketPriceHistory` 経由
- [ ] `TachibanaWorker.fetch_ticker_stats` — `CLMMfdsGetMarketPrice` から派生
- [ ] capabilities で `supported_timeframes=["1d"]` を Rust に伝え、UI で `1m` / `5m` / `1h` 等の選択を立花選択時に非活性化
- [ ] **銘柄セレクタのインクリメンタル検索（L4 修正、Q9 決定の実装）**: 数千銘柄を一気に表示すると ticker selector の描画が重い。**コード前方一致 (`7203` で `7203*` ヒット) と表示名前方一致（`display_name_ja` / `display_name_en` 両方を対象）のインクリメンタル検索**を ticker selector に追加。実装位置は [src/screen/dashboard/tickers_table.rs](../../../src/screen/dashboard/tickers_table.rs) または立花専用フィルタ層。受け入れ条件 (項目「銘柄セレクタに数千件のリスト」) と合わせて検証する
- [ ] マスタキャッシュ（`<cache_dir>/tachibana/master_<env>_<YYYYMMDD>.jsonl`）— T0 で決めたパス受け渡し方式に従って保存し、当日分があれば再ダウンロードしない。**`YYYYMMDD` は JST (`Asia/Tokyo`) 基準**（H4 修正）。立花の営業日と夜間閉局が JST 定義のため、UTC 基準だと日本時間 0:00–9:00 の起動で前日キャッシュが「当日扱い」されない（または逆）事故が起きる。Python 側 `tachibana_master.py` で `datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d")` を使う。
  > **キー設計（LOW-1）**: ファイル名は `master_<env>_<YYYYMMDD>.jsonl`（例: `master_demo_20260425.jsonl` / `master_prod_20260425.jsonl`）。`env` 部分は `"demo"` または `"prod"` で決定する（`is_demo` フラグを `TachibanaWorker` が受け取る時点で確定）。`master_<YYYYMMDD>.jsonl` の環境別なしファイルは **同日中に demo/prod を切り替えるとキャッシュが汚染**されるため採用しない
- [ ] **マスタ系 sCLMID 型強制（MEDIUM-C7）**: マスタ系 sCLMID 一覧を Python 内 `MASTER_CLMIDS: frozenset[str] = frozenset({"CLMIssueMstKabu", "CLMIssueSizyouMst", "CLMIssueSizyouMstKabu", "CLMIssueMstKihon", "CLMMfdsGetMasterData", "CLMMfdsGetIssueDetail", ...})`（最終リストは spec.md / data-mapping.md と整合）として `tachibana_master.py` に定義し、`build_request_url` が `sCLMID` 値を見て `MASTER_CLMIDS` に含まれる場合のみ `MasterUrl` 型を要求する型ガードを実装。誤って `RequestUrl` を渡すと `TypeError`。テスト 1 件追加
- [ ] **JST 日付境界テスト（HIGH-D3）**: `python/tests/test_tachibana_master_cache.py` に `freezegun` を使った以下の 2 ケースを追加 — `test_jst_date_boundary_around_midnight`（UTC 14:30 / 15:30 起動 = JST 23:30 / 翌 00:30 でファイル名がそれぞれ当日 / 翌日の YYYYMMDD になること）、`test_cache_invalid_after_jst_rollover`（前日 JST 23:30 に作ったキャッシュが、JST 翌 00:30 起動で「当日キャッシュなし」と判定されること）
- [ ] **並列呼出テスト（MEDIUM-D2）**: `python/tests/test_tachibana_master_lock.py::test_concurrent_callers_trigger_single_download` を追加。`list_tickers` と `fetch_ticker_stats` を `asyncio.gather` で同時起動し、`_download_master` が呼ばれた回数を `unittest.mock.AsyncMock` でカウントしてちょうど 1 回のみであることを assert。さらに `_master_loaded.is_set()` が両 caller の終了後に `True` であることを確認
- [ ] **`tick_size_for_price` 価格帯境界値テスト（HIGH-D2-1、data-mapping.md §5 / M-5 と紐付け）**: `python/tests/test_tachibana_tick_size.py::test_tick_size_at_price_band_boundaries` を新設。`api_request_if_master_v4r5.pdf` §2-12 の各価格帯境界（例: 3000 / 3001 / 5000 / 5001 / 30000 / 30000000 等の全境界）について、境界値ちょうど・境界値 ±1 銭（`Decimal("0.01")` 単位）の 3 点を `pytest.mark.parametrize` で網羅し、`tick_size_for_price(price)` が PDF §2-12 の刻みと一致することを assert。価格帯テーブルの抜けや off-by-one を即検知できるピン留めテスト
- [ ] **受け入れ**: `7203` の日足 1 年分が表示される、銘柄セレクタに数千件のリストが出る、`130A0` 等英字混在 ticker もリストに含まれる、日本語銘柄名が別メタデータ経路で検索または表示に使える

## フェーズ T5: trade / depth ストリーム（3〜4 日）

**ゴール**: ザラ場時間中、現値変化と 5 本気配がリアルタイムで更新される。

- [ ] `tachibana_ws.py` — EVENT WebSocket クライアント（`p_evt_cmd=FD,KP,ST,SS,US,EC`、購読は最低でも `FD,KP,ST`）
  - WebSocket URL は `build_event_url(session.url_event_ws, params)` で構築（R2 例外）
  - 自動 ping 無効化、手動 pong（[SKILL.md ストリーム規約](../../../.claude/skills/tachibana/SKILL.md)）
  - **KP（KeepAlive）frame の処理**: 5 秒周期で届く `p_evt_cmd=KP` を受信タイマーのリセットに使う。**12 秒**（KP 2 回欠損相当 + 2 秒 jitter、spec.md §3.2 と同値）以上 KP も含めて全 frame が来なければ切断とみなして再接続（指数バックオフ）
  - **タイムアウト値の根拠（M2 修正）**: 12 秒 = 5 秒 × 2 + 2 秒（NIC・OS バッファ・GIL ワーストケースを 2 秒で見積もり）。実機計測で jitter 中央値が判明したら更新。Phase 1 では暫定値として固定し、変更時は本行を更新する
  - **タイムアウト発火テスト（M2 修正）**: `python/tests/test_tachibana_ws_timeout.py` に「11 秒沈黙 → 切断しない」「13 秒沈黙 → 切断 → 指数バックオフで再接続」の 2 ケースを追加（`websockets.serve` のローカル mock サーバで KP を任意間隔で投げる）
  - **WS フレーム本文 Shift-JIS decode 必須（HIGH-C3-1）**: WS 受信 `bytes` は `parse_event_frame` 呼出**前**に必ず `decode_response_body` を通すこと（REQUEST レスポンスと同じ規約、HIGH-C2 / R7 の WS 経路適用）。CI lint ガード（T1 で定義した `grep -rnE "\.text\b|\.json\(\)" python/engine/exchanges/tachibana*.py` の 0 出現チェック）の対象に **`tachibana_ws.py` も含める**ことを明記する。`python/tests/test_tachibana_ws.py` に Shift-JIS 漢字（例: 銘柄名「株式会社○○」相当のバイト列）を含む FD frame fixture を 1 件追加し、(a) `decode_response_body` を通した後に `parse_event_frame` でフィールド分解、(b) 漢字が文字化けせずに正しく取り出せること、を assert
  - **HTTP long-poll (`sUrlEvent`) のフォールバック実装はしない**（open-questions Q4 決定: WS のみ）。閉鎖環境用の補助ルートが必要になったら Phase 2 で追加
  - **ST（エラーステータス）frame の処理（M6）**: 受信したら内容を parse し、**`sResultCode != "0"` かつシステム停止相当（`api_event_if_v4r7.pdf` 別紙で「全銘柄停止」「回線切断」相当コードと確認済みのもの）なら全購読停止**して `VenueError{code:"transport_error"}` を発出する。`sResultCode == "0"` の ST（情報通知レベル）は `tracing::info!` でログして継続。T5 実装時に実機 / PDF で深刻コードの具体値を確認し、本行を更新すること。Phase 1 保守的フォールバック: 「`sResultCode != "0"` なら全停止」で問題なければそれで実装し、後で緩和する
  - 受信バッファは `\n` または `^A` 区切りで蓄積分割（一塊チャンクに複数メッセージあり）
  - 切断 → `Disconnected` イベント、再接続は指数バックオフ
- [ ] `TachibanaWorker.stream_trades` — FD frame → 出来高差分から `TradeMsg` 合成（**前 frame 気配ベースの quote rule + 初回 frame 除外 + DV リセット検知**、data-mapping §3、F3/F4）
  - 受け入れテスト（`test_tachibana_fd_trade.py`）:
    1. 初回 frame では trade を発火しない（`prev_dv=None`）
    2. 2 件目以降で DV 差分 > 0 のとき trade を 1 件生成
    3. DV が前 frame より減少したら trade 発火せず `prev_dv` を再初期化
    4. side は前 frame の best_bid/best_ask に対して判定（当該 frame の気配は使わない）
- [ ] `TachibanaWorker.stream_depth` — FD frame → 5 本気配 → `DepthSnapshot`（`DepthDiff` は生成しない）。`sequence_id` は Python プロセス内 `AtomicI64`、`stream_session_id` 切替時に消費側リセット（F7）
- [ ] **`depth_unavailable` セーフティ（MEDIUM-6、F-M12）**: FD WS 受信開始から 30 秒以内に bid/ask キー（`GAK1` / `GBK1` 等、コード名は §inventory-T0 §11 で確定したものを使う）が 1 件も含まれないまま KP/ST 以外の frame が来ない場合、`tachibana_ws.py` は `VenueError{code:"depth_unavailable", message:"立花の板情報が取得できません（FD frame に気配が含まれていません）。設定を確認してください"}` を発出し、当該銘柄の depth 購読を停止して `CLMMfdsGetMarketPrice` polling fallback（10 秒間隔、上限 5 分）に倒す。trade ストリームは継続。テスト: bid/ask キー欠落の FD frame fixture で fallback 経路が起動すること
- [ ] `TachibanaWorker.fetch_depth_snapshot` — `CLMMfdsGetMarketPrice` ベースの初回 snapshot（ザラ場前後の 1 発、および FD WS が 12 秒以上無通信の再接続中フォールバック時のみ。**runtime の定期 polling は実装しない**、F-M1b）
- [ ] ザラ場時間判定（**JST 9:00–11:30 前場 / 12:30–15:25 後場連続 / 15:25–15:30 クロージング・オークション**、東証 2024-11-05 以降の現行時間）— **9:00–15:30 の間は `Connected` 維持**。クロージング・オークション中は気配が動かなくても「市場時間外」UI を出さない。閉場帯（〜9:00 / 11:30〜12:30 / 15:30〜）でのみ subscribe を `Disconnected{reason:"market_closed"}` で即返し、Python 側で polling/streaming を停止
- [ ] **祝日フェイルセーフ（F-M5a）**: Phase 1 は祝日カレンダー判定を持たないため、ザラ場時間内に subscribe → 立花から `p_errno!=0` または「市場休業」相当の取引所エラーが返ったら、`VenueError` ではなく **`Disconnected{reason:"market_closed"}` に倒す**フォールバック分岐を `tachibana_ws.py` に実装。エラー応答の判定パターンは T2 mock テストの拡張で固定。誤判定で平常時の API エラーを market_closed に倒さないよう、対象は明示的なエラーコード（`sResultCode` で「市場休業」「立会停止」相当）のみ
- [ ] **祝日 market_closed 倒しの統合テスト（MEDIUM-D2-2、F-M5a の検証）**: 以下 2 ケースを `python/tests/test_tachibana_holiday_fallback.py` に追加 —
  - `test_market_closed_error_during_open_hours_emits_disconnected`: ザラ場時間内（JST 10:00 等を `freezegun` で固定）に mock WS で「市場休業」相当の `sResultCode` を持つ frame を投入 → `Disconnected{reason:"market_closed"}` がちょうど 1 件発出され、`VenueError` は発出されないこと、その後の subscribe が抑止されていることを assert
  - `test_unrelated_api_error_during_open_hours_does_not_emit_market_closed`（ネガティブ）: ザラ場時間内に「市場休業」とは無関係な `sResultCode`（例: 一時的な内部エラー / parse 失敗等）を投入 → `Disconnected{reason:"market_closed"}` が発出されない（`VenueError` または通常の再接続経路に倒れる）ことを assert。誤判定の早期検知用
- [ ] **`SetProxy` と WS の整合（F-M3a）**: `SetProxy` が設定されている環境で立花 EVENT WebSocket (`wss://`) が proxy を通るかを T5 受け入れに含める（`HTTPS_PROXY` 経由で `websockets` ライブラリが CONNECT トンネルを張るかの検証）。proxy 未対応で WS が落ちる場合は `VenueError{code:"transport_error", message:"プロキシ経由の WebSocket に失敗しました"}` を返す。**テスト計画（L-3）**: ローカル CONNECT プロキシ（`pproxy` または `mitmproxy` を `subprocess` で起動）を `pytest-httpx` と組み合わせて立てた mock サーバに向け、`websockets` が CONNECT トンネルを張れるかを `python/tests/test_tachibana_ws_proxy.py` で検証。プロキシ不達の場合に `VenueError{code:"transport_error"}` が発出されることも確認
- [ ] **`stream_session_id` 切替で gap-detector がリセットされる統合テスト（F-M4b）**: Python 再起動 → 新 `stream_session_id` 発行 → Rust 側 gap-detector の sequence 比較が新 ID 受信時にリセットされることを `tests/integration/tachibana_session_reset.rs` で検証
- [ ] **`depth_unavailable` ネガティブテスト（HIGH-D4）**: `python/tests/test_tachibana_depth_safety.py::test_depth_safety_does_not_fire_when_keys_arrive_within_30s` を追加。29 秒で bid/ask キー (`GAK1`/`GBK1`) を含む FD frame を投入 → `VenueError{code:"depth_unavailable"}` が発出されないこと、`fetch_depth_snapshot` polling コール回数が 0 であることを `unittest.mock.AsyncMock` でカウント
- [ ] **ザラ場時間境界の単体テスト（HIGH-D5）**: `python/tests/test_tachibana_session_window.py` を新設し、JST `08:59:59` / `09:00:00` / `11:30:00` / `12:30:00` / `15:25:00` / `15:29:59` / `15:30:00` の 7 ケースを `pytest.mark.parametrize` で追加（呼出側からは 6 境界）。それぞれ「市場時間内 / 外」と「subscribe 即返 `Disconnected{reason:"market_closed"}` の有無」を assert
- [ ] **SetProxy + WS 統合のポジティブパス（MEDIUM-D5）**: `python/tests/test_tachibana_ws_proxy.py::test_ws_connects_through_local_connect_proxy` を追加。`pproxy` または `mitmproxy` の CONNECT proxy を `subprocess` で起動 → `SetProxy` 設定下で `tachibana_ws.py` が WS 接続成功、最初の FD frame を受信できることを assert（プロキシ不達時の `transport_error` ネガティブパスとは別ケース）
- [ ] **ST frame `sResultCode == "0"` で停止しないネガティブテスト（MEDIUM-D6）**: `python/tests/test_tachibana_ws.py::test_st_frame_with_zero_result_code_does_not_stop_subscriptions` を追加。`p_evt_cmd=ST` かつ `sResultCode=="0"` の情報通知 frame を流し込み、購読が停止しない・`VenueError` が発出されない・`tracing::info!` 相当のログが 1 件出ることを assert
- [ ] **受け入れ**: ザラ場中 10 分間 7203 を購読し続けて drop 0、UI で trade ティッカーと板が動く。KP frame 受信ログがあること、tick rule fallback テスト（中値ぴったりの trade で直前 trade との比較が効くこと、F-M8b）が緑であること

## フェーズ T6: 復旧・耐久・観測性（2 日）

**ゴール**: Python 異常終了・session 切れ・ザラ場跨ぎでも UI が破綻しない。

- [ ] `VenueError{venue:"tachibana", code:"session_expired", message}` → Rust UI バナー（旧 `EngineError{code:"tachibana_session_expired"}` は廃止）。**バナー文言は Python が `message` に詰めて送る**（F-Banner1）。Rust 側は `message` をそのまま描画し、固定文言を持たない。`code` は severity（warning/error）とアクションボタン（再ログイン / 閉じる）の出し分けにのみ使う
- [ ] **`VenueError.code` の enum 化（T0 schema 追加分の検証）**: Python 側の発出箇所（`tachibana_auth.py` / `tachibana_ws.py` / `tachibana.py`）で使う code 文字列が [architecture.md §6](./architecture.md#6-失敗モードと-ui-表現) の表と一致することを単体テストで検証。未知 code が発出されたら Rust 側はデフォルト severity（error）+ 再ログインボタン非表示で fail-safe に倒す
- [ ] **バナー文言テスト**: `tachibana_auth.py` の各エラー分岐（`p_errno=2` / `sKinsyouhouMidokuFlg=1` / `sResultCode=10031` / 認証失敗）が Python 側で意図通りの `message` を生成することを `python/tests/test_tachibana_banner_messages.py` で固定（snapshot test）。**L3 修正**: 将来 i18n を入れたとき snapshot 全壊を防ぐため、Phase 1 の snapshot は **locale を `ja_JP` 固定** で取り、`pytest` fixture で `LANG=ja_JP.UTF-8` を強制する。i18n 導入時は locale 別 snapshot ファイルに分割する規約を本タスクのコメントに残す
- [ ] `VenueCredentialsRefreshed` 経由で**起動時再ログイン後**の session を Rust が keyring 更新
- [ ] Python 再起動シナリオの自動テスト（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 Python プロセス復旧プロトコル 流用）。**具体テスト名（MEDIUM-D1、MEDIUM-B2-1）**: 既存 `engine-client/tests/process_lifecycle.rs` に `#[tokio::test] async fn test_credentials_resent_in_order_after_restart()` を追記する（既存 integration test ファイルは `handshake.rs` / `connection_closed.rs` / `depth_gap.rs` / `process_lifecycle.rs` といった短い名詞形で揃っており、新規 `process_restart_with_credentials.rs` を作ると命名規則から逸脱する）。`ProcessManager` の再起動経路でコマンド送信順序を `Vec<&'static str>` に記録 → `["SetProxy", "SetVenueCredentials", "Subscribe", ...]` の順序を assert（`SetVenueCredentials` が `SetProxy` の後・`Subscribe` の前であること）
- [ ] ログにシークレット非漏洩テスト
- [ ] **ログ redaction 具体テスト（MEDIUM-D3-1）**: `python/tests/test_tachibana_log_redaction.py::test_runtime_logs_do_not_contain_credentials_or_virtual_urls` を新設。`caplog` fixture で `tachibana_auth.login` / `tachibana_ws` 接続経路 / `VenueError` 発出経路（`p_errno=2` / 認証失敗 / `unread_notices` / `transport_error` 等の各分岐）の全 log record を集め、各 record の `message` / `args` / formatted text に対して以下の文字列が**含まれていないこと**を assert: (a) `user_id` 値、(b) `password` 値、(c) session token、(d) `url_request` / `url_master` / `url_price` / `url_event` / `url_event_ws` の仮想 URL ホスト部分。違反があった record があれば assertion failure メッセージにどの文字列がどの record に出たかを明示する
- [ ] capabilities ハンドシェイクで OI / fetch_trades / 分足の非対応を Rust に伝え UI を非活性化
- [ ] **capabilities による UI 非活性化テスト（MEDIUM-D3-2）**: `engine-client/tests/capabilities_gate.rs::test_unsupported_timeframes_are_disabled_when_capabilities_received` を新設。`capabilities = {"supported_timeframes": ["1d"]}` を受信した状態で、UI 状態モデル（`engine-client` 側で公開している capabilities 適用後の state）に対して 1m / 5m / 1h の timeframe 選択が `enabled == false` になっていることを assert。1d は `enabled == true`。capabilities 未受信時は全 timeframe が `enabled == true`（fail-open ではなく初期状態）であることも併せて確認
- [ ] `NotImplementedError` が現行 server では `Error{code:"not_implemented"}` に変換されることを前提に、UI とテストを揃える。専用エラーコードが必要なら server 側例外マッピング追加を別PRに切り出す
- [ ] 「ProcessManager が credentials を保持していないため再起動後に立花だけ復旧しない」回帰を防ぐ統合テストを追加
- [ ] **`VenueReady` 冪等性テスト**: Python 再起動 → `SetVenueCredentials` 再注入 → `VenueReady` 再受信時に、Rust 側 UI が新規 subscribe を発行しないこと（resubscribe は `ProcessManager` 1 箇所のみ）を統合テストで検証
- [ ] **受け入れ**: [spec.md §4 受け入れ条件](./spec.md#4-受け入れ条件phase-1-完了の定義) 全て緑

## フェーズ T7: 仕上げ・配布準備（1〜2 日）

- [ ] README / SKILL.md に「立花 venue 利用の前提（電話認証済み口座が必要）」追記
- [ ] release ビルドで env 自動ログインが完全に除外されていること（**`dev_tachibana_login_allowed: false` が Rust から送られ、Python 側 `tachibana_login_flow` が `os.getenv("DEV_TACHIBANA_*")` を読まないことを統合テストで確認**。`#[cfg(debug_assertions)]` でのコンパイル除外は採用しない — T3 で実装した runtime ガード方式を前提とする）
- [ ] 本番 URL 設定の隠しフラグ（`TACHIBANA_ALLOW_PROD=1`）を実装、デフォルトは demo 強制
- [ ] CI に `pytest -m demo_tachibana` を **T2 で確定した方式（A/B/C）に従って統合**（毎 PR では走らせない原則は維持）。T2 で案 (B)（manual lane only）を採ったなら `workflow_dispatch` のみ実装、案 (C) なら CI 統合自体を行わない。スケジュール起動する場合は demo の閉局帯を避ける。**ゲート（H5 修正）**: スケジュール起動の有効化は [open-questions.md Q21](./open-questions.md#L25) の運用時間が T2 実機確認で確定してから。確定前は手動トリガのみ許可
- [ ] **tkinter スモークテスト（F-M2c）**: CI で `xvfb-run pytest -m tk_smoke` を回す。`tachibana_login_dialog.py` を起動して即座に `{"status":"cancelled"}` を返す経路を `--auto-cancel` フラグで実装し、import エラーや `Toplevel` 構築失敗を CI で検知する。実 GUI のバリデーション挙動は引き続き `pytest -m gui` の手動確認
- [ ] `tools/secret_scan.sh` 新設: `kabuka.e-shiten` / 仮想 URL ホスト / `sUserId` / `sPassword` / `sSecondPassword` を検出。pre-commit hook と CI ジョブの両方から同一スクリプトを呼ぶ（spec.md §4 受け入れ条件 6 と整合）。**`BASE_URL_PROD` を定義する 1 箇所（例: `python/engine/exchanges/tachibana_url.py`）はファイル単位で allowlist** し、それ以外のリテラル出現を全て fail させる（F11）。allowlist ファイルは冒頭コメントで理由を明示
- [ ] **Windows 開発環境での pre-commit 整合（F-M5b、LOW-4 修正）**: 本リポジトリの開発主体は Windows であり、git-bash / WSL が常に使える前提は持てない。以下の 2 ファイルを**同時に**新設する:
  - `tools/secret_scan.sh`（bash 版）— CI（`runs-on: ubuntu-latest`）と git-bash / WSL 環境の pre-commit で呼ぶ
  - `tools/secret_scan.ps1`（PowerShell 版）— Windows ネイティブ pre-commit（PowerShell から起動）で呼ぶ
  - パターン正規表現は `tools/secret_scan_patterns.txt` に 1 ファイルとして正本化し、両スクリプトがそれを読む。これにより「sh は更新したが ps1 は古い」ずれが起きない
  - `tools/secret_scan.sh` は git-bash / WSL を要件として `tools/README.md` に明記。pre-commit 設定で bash 版を呼ぶ際は `bash tools/secret_scan.sh` 形式とし PowerShell 直起動は認めない
  - CI では bash 版のみ使用（`runs-on: ubuntu-latest`）
  - **`tools/secret_scan_patterns.txt` のファイル形式（L-4）**: 1 行 1 パターンの **ripgrep 正規表現**（`-e` オプションに直接渡せる形式）。`#` で始まる行はコメント。`secret_scan.sh` は `grep -E -f tools/secret_scan_patterns.txt`、`secret_scan.ps1` は `Get-Content tools/secret_scan_patterns.txt | Where-Object {$_ -notmatch '^#'} | ForEach-Object { Select-String -Pattern $_ ... }` で読み込む。形式は両スクリプトの冒頭コメントにも明記する
  - **Phase 1 確定パターン正本（HIGH-C2-2）**: `tools/secret_scan_patterns.txt` には Phase 1 では以下の 5 パターンを逐語で正本登録する（コメント行含めそのまま）。`kabuka` ホストはドット escape 必須で **`demo-kabuka.e-shiten.jp` も同じ正規表現で対象**になる。allowlist は `python/engine/exchanges/tachibana_url.py` を **ファイル単位**で吸収する設計（pre-commit / CI 両方の呼出元で `--exclude python/engine/exchanges/tachibana_url.py` 相当を渡す）:
    ```
    # 立花本番ホスト（demo 含む全 URL を検出、allowlist で tachibana_url.py のみ吸収）
    kabuka\.e-shiten\.jp
    # ハードコードされた sCLMID 系秘密キー（定義部分以外で出現したら fail）
    \bsUserId\b\s*[:=]
    \bsPassword\b\s*[:=]
    \bsSecondPassword\b\s*[:=]
    # 本番 URL 定数の代入（tachibana_url.py 以外で出現したら fail）
    BASE_URL_PROD\s*=
    ```
- [ ] **secret_scan メタテスト（HIGH-D6）**: スキャナ自体のリグレッションを防ぐメタテストを追加する:
  - `tools/tests/test_secret_scan.sh`（bash）: `tools/tests/fixtures/should_fail/*`（仮想 URL ホスト・`sUserId=...` リテラル等を含むダミー）に対して `tools/secret_scan.sh` が exit 1 を返すこと、および `tools/tests/fixtures/should_pass/tachibana_url.py`（`BASE_URL_PROD` を含むが allowlist ファイルとして許可されている想定）に対して exit 0 を返すことを assert
  - `tools/tests/test_secret_scan.ps1`（PowerShell）: 同等のシナリオを `tools/secret_scan.ps1` に対して実行（Windows ネイティブ pre-commit のリグレッション防止）
  - CI（ubuntu-latest）で `tools/tests/test_secret_scan.sh`、Windows ランナーで `tools/tests/test_secret_scan.ps1` を実行

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
| **FD 情報コード未確定で Phase 1 縮退（HIGH-2）** | `DV` / `GAK*` / `GBK*` / `GAS*` / `GBS*` / `DPP_TIME` / `DDT` の正式コード名は [inventory-T0.md §11.3](./inventory-T0.md#113-ブロッカー扱いと対応方針b3-再オープン) のいずれか（PDF 同梱 / 実 frame キャプチャ / Phase 縮退）で**T1 着手前に必ず実体解決**。縮退案を取った場合は Phase 1 を「日足 kline + ticker stats のみ」に縮退し spec.md §2.1 を改訂。PR 説明文に解決証跡を必須記載（PR テンプレに gate 化） |
| **マスタからの異常 ticker で Rust panic（HIGH-3）** | `Ticker::new` ([exchange/src/lib.rs:281](../../../exchange/src/lib.rs#L281)) は `assert!` で panic する。Python `tachibana_master.py` で「ASCII 28 文字以内・`\|` 不含」を pre-validate して逸脱は skip + warn ログ。Rust IPC 受信側は `EngineEvent::TickerInfo.tickers[*]` の各 ticker dict を `Ticker::new` 呼出前に同条件で再 validate し、不正値は drop（panic させない）|
| **proxy 環境で `wss://` が張れず立花 venue 完全不可（MEDIUM-2）** | Phase 1 は WS のみ。`SetProxy` 設定時に WS が張れない場合は `VenueError{code:"transport_error"}` を返し、文言で「Phase 1 はプロキシ経由 WebSocket 未対応」を明示。HTTP long-poll fallback は Phase 2 で必須化（[Phase 2 以降](#phase-2-以降参考計画外) に追記） |
| **FD 板キーが永久に来ない（MEDIUM-6）** | FD 受信開始から 30 秒以内に bid/ask キーが 1 件も来なければ `VenueError{code:"depth_unavailable"}` を発出して polling fallback に倒す。spec.md §3.3 と T5 受け入れに条文を追加 |
| 本番 URL を踏んで実弾 | `TACHIBANA_ALLOW_PROD=1` がない限り Python 側でデモ強制、Rust 側でも assertion |
| 立花仕様変更（v4r9 等への移行） | URL ベースを config 化、IPC `capabilities` で venue 側バージョンを Rust に伝える |
| 電話認証の手動性 | アプリは関与しない。ドキュメントで明示し、UI バナーで誘導 |
| 立花の API レート制限 | サンプル `e_api_get_master_tel.py` のリトライ間隔を尊重（3 秒）、`limiter.py` に `TachibanaLimiter` を追加 |
| ザラ場跨ぎでセッション切れ気付かない | **定期 `validate_session` ポーリングは実装しない**（runtime 中の自動再ログイン禁止と矛盾するため、spec.md §3.2 と整合）。検知は subscribe 経路で受ける `p_errno=2` のみに任せ、検知後は即 `VenueError{code:"session_expired"}` を発出して UI を再ログイン誘導状態に遷移させる |

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
