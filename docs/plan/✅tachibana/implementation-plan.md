# 立花証券統合: 実装計画

親計画 [docs/plan/✅python-data-engine/implementation-plan.md](../✅python-data-engine/implementation-plan.md) のフェーズ 6 完了後、または並行で着手する追加トラックとして位置づける。

> **不変条件 ID ↔ test 関数名対応は [`docs/plan/✅tachibana/invariant-tests.md`](./invariant-tests.md) を正本とする**（本ファイル内の各章で言及される不変条件 ID の test 紐付けは同表を参照）。

## フェーズ T0: 既存型棚卸し + 仕様凍結 + スキーマ拡張（2〜3 日）

**ゴール**: IPC スキーマに立花対応の差分を入れ、Rust / Python 両側で型ビルドが通る。**着手前に既存型の影響範囲を grep で表に書き出す**。

### T0.1 既存コード棚卸し（先に必ず実施）

- [x] `git grep -n "TickerInfo"` / `HashMap.*TickerInfo` / `HashSet.*TickerInfo` の参照箇所を全数表化。`#[derive(Hash, Eq)]` 入りでフィールドを増やす影響を見積もる
- [x] `git grep -nE "MarketKind::(Spot|LinearPerps|InversePerps)"` で網羅 match の箇所を全部リストアップ（`exchange` / `engine-client` / `data` / `src` 配下）
- [x] `Ticker::new` (`exchange/src/lib.rs::Ticker::new`) の `assert!(ticker.is_ascii())` を確認し、`130A0` 等が通ることをユニットテストで実機確認
- [x] **既存 `Timeframe` の serde 形式は `"D1"`（変種名）であることを確認済み**（F-m2、F-H1）。`exchange/src/lib.rs::Timeframe` は `#[derive(Serialize, Deserialize)]` のみで `#[serde(rename = ...)]` 無し。`Display` は `"1d"` を返すが serde は別系統。**T0.2 で `#[serde(rename = "1d")]` 等の rename 属性を全変種に追加する必要がある**（既存暗号資産 venue 経路で IPC を通っている場合は変換層の有無を grep で先に棚卸し）
- [x] **`qty_in_quote_value` 呼出箇所の棚卸し**（F-H4、H1 修正）: [`exchange/src/adapter.rs::qty_in_quote_value`](../../../exchange/src/adapter.rs) が正本。呼出は **9 箇所**（path::symbol 全数表は [inventory-T0.md §4](./inventory-T0.md#4-qty_in_quote_value-呼出箇所f-h4)）。`MarketKind::Stock => price * qty` を enum 内部分岐で強制すれば呼出側コード変更不要
- [x] **`EngineEvent::Disconnected` の shape は確認済み**（F-H2）: `engine-client/src/dto.rs::EngineEvent::Disconnected` で既に `{ venue, ticker, stream, market, reason: Option<String> }`。**DTO 追加は不要**、`reason: "market_closed"` は文字列規約として `events.json` schema に記載するだけで足りる
- [x] `ProcessManager` ([engine-client/src/process.rs](../../../engine-client/src/process.rs)) の proxy 保持パターンを読み、credentials 保持の **mutex / Arc 戦略を T0.2 のうちに確定**（F-m4）。proxy が `Arc<Mutex<Option<Proxy>>>` ならそれに揃える、`watch::channel` ならそれに揃える、と決め切る
- [x] `src/screen/` の現在構造を確認し、立花ログイン UI の追加先（既存 `login.rs` 拡張 or 新ファイル）を T0 のうちに暫定確定（F-m3）
- [x] `python/tests/test_*_rest.py` のモック方式（`pytest-httpx` / `HTTPXMock`）が他 venue で稼働中であることを確認
- [x] [docs/plan/✅python-data-engine/schemas/](../✅python-data-engine/schemas/) の `commands.json` / `events.json` が実在することを確認（実在を確認済み）
- [x] ✅ **FD 情報コード一覧確定（F-M2a、F-H3、B3 クローズ、2026-04-26）**: `.claude/skills/tachibana/manual_files/api_web_access.xlsx` 内の実 FD frame サンプル（2022-03-15）から全キー名を実値で確認。旧暫定名 `GAK/GBK/GAS/GBS`（→ 実: `GAP/GBP/GAV/GBV`）・`DPP_TIME`（→ 実: `DPP:T`）・`DDT`（→ 実: 共通ヘッダ `p_date`）はすべて誤りだったため訂正済み。気配本数は旧想定 5 本 → **実際は 10 本**。確定コード一覧: [inventory-T0.md §11.2.b](./inventory-T0.md#112b-fd-frame-data-key)・data-mapping.md §3/§4 を同日更新済み。**T5 着手ブロッカー解除**。

  > ~~**明示ゲート規約（HIGH、ユーザー指摘ラウンド 7）**~~: ✅ 解消済み（2026-04-26）。T5 着手禁止は解除。

### T0.2 型・スキーマ追加

- [x] `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` を [exchange/src/adapter.rs](../../../exchange/src/adapter.rs) に追加
- [x] **`MarketKind::Stock` の `qty_in_quote_value` は enum 内部分岐で `price * qty` 強制**（F-M3b）。`size_in_quote_ccy` 引数を見ない実装にし、`Stock` 用ユニットテストで誤呼出（`size_in_quote_ccy=true`）でも常に `price*qty` になることを確認
- [x] **`secrecy = "0.8"` を `engine-client` / `data` の Cargo.toml に追加**（F-B1）。`SecretString` は **Rust 内部保持型**でのみ使い、IPC 送出時は `expose_secret()` 経由でプレーン `String` 化した送出専用 DTO（後述 `*Wire`）に写像する
- [x] **`zeroize = "1"` を `engine-client` の `Cargo.toml` に追加し、Wire 型 secret フィールドを `Zeroizing<String>` で保持（M4、MEDIUM-B2-2）**: **現状の `engine-client/src/dto.rs::TachibanaCredentialsWire` / `engine-client/src/dto.rs::TachibanaSessionWire` は共にプレーン `String` でマージ済み**。`Zeroizing<String>` 化は型置換だけで足りる。**実装時の修正（M4-impl）**: zeroize 1.8 の `Zeroizing<T>` は `Serialize`/`Deserialize` を `Deref` 透過では提供しない（`zeroize` クレートの `serde` feature が必須）。本リポジトリでは workspace `Cargo.toml` で `zeroize = { version = "1.8", features = ["serde"] }` を有効化して採用する（旧記述の「serde feature 不要」は誤り）。Wire DTO の field 型を `String` → `Zeroizing<String>` に置換するだけで、JSON 出力フォーマットは不変。 `TachibanaCredentialsWire.password` / `TachibanaSessionWire.url_*` を `Zeroizing<String>` で持ち `Drop` 時のゼロ化を保証。Wire 値はスコープ最小化（serialize 直後に明示 drop）の規約を `engine-client/src/backend.rs` の `SetVenueCredentials` 送信パスに `// SAFETY-LITE: Wire は serialize 後即 drop — Zeroizing が Drop 時にゼロ化する` コメントで記す。テスト: `engine-client/tests/wire_dto_drop_scope.rs` に (a) `std::mem::needs_drop::<TachibanaCredentialsWire>()` が `true`、(b) `SetVenueCredentials` 送信関数が Wire 値を値渡し（move）で受け取ること（`&` 参照渡し禁止）を確認。ヒープ実メモリのゼロ化検証は OS 依存で不安定なため省略
- [x] `QuoteCurrency` enum を新設（`Usdt`/`Usdc`/`Usd`/`Jpy`、`Copy + Hash + Eq + Serialize + Deserialize`）。**`Default` は実装しない**（F-M6a）。`&'static str` は使わない（serde ラウンドトリップ不可）
- [x] `TickerInfo` に `#[serde(default)]` 付きで `lot_size: Option<u32>` と `quote_currency: Option<QuoteCurrency>` を追加（F13/F-M6a）。`TickerInfo` の `Copy` 制約を壊さない（`String` 追加禁止）。**`None` 復元時は読み込み層で `Exchange::default_quote_currency()` を使って `Some(_)` に正規化**し、UI フォーマッタへは常に `Some` で渡す
- [x] `Exchange::default_quote_currency(&self) -> QuoteCurrency` を `exchange/src/adapter.rs` に実装（暗号資産 venue は USDT/USDC、`TachibanaStock` は `Jpy`）
- [x] **既存永続 state の serde 互換性確認**（F13/F-M4）— [exchange/tests/ticker_info_state_migration.rs](../../../exchange/tests/ticker_info_state_migration.rs) で旧 `TickerInfo` payload (lot_size / quote_currency 欠如) が `serde(default)` 経由で読めることを検証。Hash 影響範囲は inventory-T0.md §1.2 にて「永続化されているのは `data/src/layout/pane.rs` の `ticker_info` フィールドのみ、`HashMap` キーは in-memory のみ」と確定済み: dashboard 設定ファイル / `state.rs` に `TickerInfo` が保存されているか `git grep` で特定。`#[serde(default)]` で missing field が読めることに加え、**`Hash` 値変化により既存 `HashMap<TickerInfo, _>` のキー突合が壊れないか**を実機テスト。受け入れ条件に「旧 `state.json` を起動 → pane 復元 → ticker 表示」を追加
- [x] **日本語銘柄名の運搬経路を確定**: `EngineEvent::TickerInfo.tickers[*]` は `Vec<serde_json::Value>` のまま（`engine-client/src/dto.rs::EngineEvent::TickerInfo`）であり、Python 側が `display_name_ja` キーを各 ticker dict に詰めれば追加 schema 不要で運搬可能。Rust UI 側は将来 `HashMap<Ticker, TickerDisplayMeta>` で別管理する方針を inventory に確定（実 UI 配線は T4 で実装）
- [x] ✅ **類似プロジェクト `C:\Users\sasai\Documents\flowsurface` の先行実装を参考にする（M9 決定）** — **本タスクは設計確定が deliverable**であり、実装配線は T4 に委譲する規約として T0.2 で閉じる:
  - `flowsurface/exchange/src/adapter/tachibana.rs::MasterRecord` 型を踏襲し、Python 側 `tachibana_master.py` のレコード型に **`sIssueName` / `sIssueNameRyaku` / `sIssueNameKana` / `sIssueNameEizi` の 4 種**を全て保持する（Phase 1 で全部使わなくても、後続フェーズの検索 UI で活きる）
  - `display_symbol` には **`sIssueNameEizi`（英語名 ASCII）を採用**。28 文字を超える場合は切詰め、空または非 ASCII なら `None` フォールバックして `Ticker::new_with_display` にデフォルト動作させる（`Ticker` の ASCII 制約を回避）
  - `display_name_ja` には `sIssueName` を入れる。flowsurface 側はまだ `display_name_ja` 経路を持たない（英語名の display_symbol で済ませている）ため、本計画はそこから一歩進む。`Ticker` には英語名・別管理の `TickerDisplayMeta` には日本語名、というルーティング
  - Rust 側 UI ラベルのフォールバック順序: `display_name_ja` → `display_symbol`（英語名）→ `ticker.symbol`（4 桁コード）。3 段フォールバックは flowsurface 側にも明示的にはないので本計画で新規規約として固定
  - **`display_name_ja` の events.json schema 明記**: 「Python 側 typo（`display_name_jp` 等）でサイレント失敗」を防ぐため、`docs/plan/✅python-data-engine/schemas/events.json` の `TickerInfo` entry の各 ticker オブジェクト形に `display_name_ja: string?` を追記し、Python 単体テストで「key 名が `display_name_ja` であること」を assert（M9 / 元 M9 ペンディング解消）
  - **完了根拠 (2026-04-25)**: `MasterStreamParser` は dict ベースで全 sIssueName* キーを保持するため、レコード型上での先取り作業は不要（T4 で `list_tickers` を書く際に `record["sIssueName"]` 等を直接参照する）。display_symbol / display_name_ja のマッピング規約は本箇条書きで確定済み。実装配線は T4 のタスク扱い。
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
- [x] **`VenueCredentialsPayload::venue_tag()` メソッド化（M2 再オープン、HIGH-B2-2）**: 現状 `engine-client/src/process.rs::set_venue_credentials` の retain ロジックが variant 列挙ベースで、将来 venue 追加時にコンパイル網羅 OK のまま論理破綻する。**緊急度**: 現実装は variant 列挙ベースで、第 2 venue（例えば SBI / 楽天等）を追加した瞬間に retain 述語が「同 variant 以外を全部 drop」する論理バグへ転落する予備軍。Phase 1 の Tachibana 単一 venue では症状が出ないが、設計上の地雷であり**先行修正必須**。`impl VenueCredentialsPayload { pub fn venue_tag(&self) -> &'static str }` を追加し、`set_venue_credentials` を `store.retain(|p| p.venue_tag() != payload.venue_tag())` に書換。同時に Wire 構造に対する `Hash + Eq` は不要（venue 名 1 文字列で識別）。**受け入れ**: `engine-client/src/process.rs` 単体テストで (a) 2 種類の venue payload（`Tachibana` + 仮の 2 つ目 variant、テスト用 `#[cfg(test)]` で stub variant を追加してよい）を順に setter に渡すと `store.len() == 2` になること、(b) 同一 venue payload を 2 回投入すると `store.len() == 1` のまま（最後勝ち）であることを assert
- [x] **`python/engine/schemas.py` の同期確認（L8、M6 修正）**: `commands.json` / `events.json` 更新と同期して、Python 側 pydantic モデルが追加済みであること。実測: `grep -c "VenueReady\|VenueError\|VenueCredentialsRefreshed\|VenueLoginStarted\|VenueLoginCancelled\|RequestVenueLogin\|VenueCredentialsPayload" python/engine/schemas.py` → **14** (閾値 7 以上を満足)。tag フィールド `venue` の文字列値が両側で一致するテスト追加は T3 の `test_tachibana_login.py` に含める

---

### T0.2 受け入れ（2 段構造）

**個別作業の完了 ≠ フェーズ完了。以下を全て満たして初めて T0.2 完了とする。**

#### ステージ A: 個別作業（上記 `[x]` / `[ ]` で追跡）

上記の各行の `[x]` / `[ ]` で追跡する。

#### ステージ B: フェーズ完了ゲート（ステージ A 全 `[x]` 後に実施）

> **ゲート通過記録 (2026-04-25)**: 下記の全項目を実機で検証して通過。残るは B3（FD 情報コード §11）のみで、これは spec.md / data-mapping §3 / T5 の前提ゲートとして**意図的に未通過**のまま据え置く（T5 着手と同 PR で解決する規約、line 21 参照）。B3 を除く全 ゲートをクリアしたことで T0.2 ステージ B は **T5 直前まで進める状態**に到達。

- [x] ✅ `cargo check --workspace` 成功 — `dev profile` で 1.08s 正常完了
- [x] ✅ Python `pytest` 既存スイート緑 — `uv run pytest python/tests/` で **288 passed** (T2 追加分含む)
- [x] ✅ `quote_currency` 正規化テスト緑（M1 / `exchange/tests/ticker_info_state_migration.rs` に 2 件） — `normalize_quote_currency_fills_in_default_for_tachibana` / `normalize_quote_currency_preserves_existing_value` の 2 件 ok
- [x] ✅ `venue_tag()` リファクタ後の `set_venue_credentials` 単体テスト緑（M2） — `engine-client/src/process.rs` の `venue_tag_returns_tachibana_for_tachibana_variant` / `set_venue_credentials_replaces_same_venue_last_wins` の 2 件 ok
- [x] ✅ Python pydantic / Rust DTO ラウンドトリップテスト緑（L8） — `python/tests/test_schema_compat_v1_2.py` 11 passed、`engine-client/tests/schema_v1_2_roundtrip.rs` 8 passed
- [x] ✅ `TickerInfo` serde 互換性テスト緑（`exchange/tests/ticker_info_state_migration.rs`） — 全 10 件 ok（`ticker_accepts_alphanumeric_5char_codes` / `timeframe_serde_*` / `normalize_*` 等）
- [ ] 🔴 FD 情報コード §11（B3）: 案 1/2/3 いずれかを選択し PR 説明文に解決証跡を記載。案 3（縮退）を選んだ場合は §11.3「縮退時の計画更新リスト」を全実施済みであること
  - **据え置き理由**: line 21 参照。`api_event_if_v4r7.pdf` の入手 / 実 frame キャプチャ / Phase 縮退の 3 案を T5 着手 PR と同タイミングで解決する規約。本ゲートはステージ B の他項目とは独立に T5 着手の前提条件として機能し、それまでは `[ ]` 🔴 のまま据え置く。**T0.2 ステージ B のクローズ条件は B3 を除く全項目を満たすこと**として運用する。
- [x] ✅ **`request_id` 規約確定（LOW-1）**: `commands.json`/`events.json` の `request_id` フィールドに UUIDv4 の `pattern` 正規表現が記載済みであること — schema 1.2 で `$defs/RequestId` / `$defs/RequestIdNullable` に `pattern: ^[0-9a-f]{8}-...$` を記載済み（line 69 参照）
- [x] ✅ **マスタキャッシュ path 確定（MEDIUM-4）**: `stdin` 初期 payload 拡張（`{port, token, config_dir, cache_dir, dev_tachibana_login_allowed}`）の文言が「暫定固定」から「確定」に書き換え済み（line 71）。`dev_tachibana_login_allowed` は T3、`config_dir`/`cache_dir` は T4 で実装する規約も同行に明記済み
- [x] ✅ **`quote_currency` 正規化実装位置確定（M1 再オープン）**: `TickerInfo::normalize_after_load()` に集約する方針を line 79 に確定記載済み。`pane.rs` ロード経路のみで fold、IPC 受信側は `TickerInfo::new()` で既に default 埋め込み済みのため fold 不要、という根拠付き
- [x] ✅ **`zeroize` 完了（M4）**: `engine-client/tests/wire_dto_drop_scope.rs` 3 件 ok（`credentials_wire_serializes_as_plain_strings` / `session_wire_roundtrips` / `wire_dtos_need_drop_for_zeroize`）。`TachibanaCredentialsWire` / `TachibanaSessionWire` の secret フィールドは `Zeroizing<String>` で保持
- [x] ✅ **`TachibanaSessionWire` が `Serialize + Deserialize` を両方 derive（C2 修正）**: `schema_v1_2_roundtrip.rs::rust_deserializes_python_venue_ready` / `rust_deserializes_python_venue_error` ok。`VenueCredentialsRefreshed` の Python→Rust デシリアライズ経路を実機で確認
- [x] ✅ **schema_minor 1.1→1.2 双方向 IPC ラウンドトリップテスト（HIGH-D2-2）**: 両ファイル実装済み・全件緑:
  - `python/tests/test_schema_compat_v1_2.py` 11 passed — Rust serialize → pydantic `model_validate_json` を 7 variant 網羅
  - `engine-client/tests/schema_v1_2_roundtrip.rs` 8 passed — pydantic `model_dump_json` → `serde_json::from_str` を 7 variant 網羅

## フェーズ T1: Python ユーティリティ（2〜3 日）

**ゴール**: 立花 API を叩く下回りが単体で揃う。サーバ通信なしの単体テストでカバレッジ 80%。

> **進捗 (2026-04-25)**: T1 はコミット `8bc6ca8` + `1338c76` で実装・レビュー反映済み。`uv run pytest python/tests/test_tachibana_*.py` で **75 件すべて緑**（auth 含む）。`cargo check --workspace` 緑。**Phase 1 で実装する Python ユーティリティ層は完了**。一部の項目は「Python 側は完了 / Rust 受信側または下層 worker が後続フェーズ依存」のため、本文ではその区別を箇条書きで明示する。
> 設計判断・知見は各箇条のサブ項目とコミットメッセージに記録。**T2 で利用しているのは本フェーズで揃ったユーティリティ群のみ**で、interface に追加修正は発生していない。

- [x] ✅ `python/engine/exchanges/tachibana_url.py`:
  - `build_request_url(base, json_obj, *, sJsonOfmt)` — REQUEST 用、`?{JSON 文字列}` 形式（SKILL.md R2）。**`sJsonOfmt` は必須キーワード引数**（HIGH-C1、R5 強制）。マスタ系 sCLMID（後述 `MASTER_CLMIDS`）は `"4"`、それ以外は `"5"` を呼出側が指定する。引数省略は `ValueError` を投げる。テストで `sJsonOfmt="4"` / `"5"` の両ケースおよび省略時 `ValueError` を検証
  - `build_event_url(base, params: dict)` — EVENT 用、`?key=value&...` 形式（R2 例外、`p_evt_cmd`/`p_eno`/`p_rid`/`p_board_no`/`p_gyou_no`/`p_issue_code`/`p_mkt_code`）
  - `func_replace_urlecnode(s)` — 30 文字置換（R9、`e_api_login_tel.py` サンプル出力と一致）。**エンコード方式（C-H1）**: JSON 文字列全体を一度 `func_replace_urlecnode` に通す（構造記号 `{` `}` `"` `:` `,` も変換テーブルに含まれ、立花サーバ側が期待する形式）。key / value 単位への個別適用は `{` `}` 等が `%7B`/`%7D` に化けずパースエラーになる可能性があるため採用しない。
  - **`func_replace_urlecnode` の追加テスト（MEDIUM-D4）**: `test_replace_urlecnode_empty`（空文字 `""` 入力で `""` を返す）、`test_replace_urlecnode_full_roundtrip`（30 文字全置換対象を含む文字列で encode/decode のラウンドトリップが完全一致。**受け入れ条件**: `{` `}` `"` `:` `,` 等の JSON 構造記号を含む完全な REQUEST ペイロードで期待バイト列と一致することを含める）、`test_replace_urlecnode_passthrough_alnum`（英数字のみ入力は素通り）の 3 ケースを `python/tests/test_tachibana_url.py` に追加
  - **builder 誤用ガード（MEDIUM-C4 / R2）**: `TachibanaSession` の URL を `RequestUrl` / `MasterUrl` / `PriceUrl` / `EventUrl` 等の NewType（`typing.NewType` または `dataclass(frozen=True)` 1 フィールド ラッパ）でラップし、`build_request_url` は `RequestUrl | MasterUrl` のみ、`build_event_url` は `EventUrl` のみ受理する型安全化を `tachibana_url.py` に実装。型不一致は `TypeError`。テスト 1 件追加
  - **多バイト fixture を必ず 1 ケース含める（M7 決定）**: `func_replace_urlecnode` 単体テストに「日本語 1 文字（例 `"あ"`）」「カナ 1 文字（例 `"ア"`）」「混在文字列（例 `"トヨタ自動車 7203"`）」のいずれか最低 1 ケースを追加し、Shift-JIS バイト列 → `%xx` 化のラウンドトリップを検証する。Phase 1 では multibyte query 送信を**実運用で**は発生させない方針だが、`func_replace_urlecnode` の正本実装は将来拡張に備えて先取りする。期待値はサンプルの規約（Shift-JIS エンコード後にバイト単位で `%xx`）に従い、`api_web_access.xlsx` の事例があれば優先採用
- [x] ✅ `python/engine/exchanges/tachibana_codec.py`:
  - Shift-JIS デコード（`decode_response_body`）
  - `parse_event_frame(data: str) -> list[tuple[str, str]]`（`^A^B^C` / `\n` 分解）
  - `deserialize_tachibana_list(value)` — 空配列が `""` で返るケースの正規化（SKILL.md R8）
  - **Phase 1 で `deserialize_tachibana_list` 適用必須となる List 形状フィールド（MEDIUM-C2-1、R8）**: 下表の各 dataclass 該当フィールドで decode 直後に `deserialize_tachibana_list` を呼び、`""` が来たら `[]` に正規化する実装規約とする。**T1 受け入れに該当 dataclass の単体テストを 1 件ずつ追加**し、各フィールドに `""` を流しても `[]` で正規化されることを assert:

    | sCLMID | dataclass / レスポンス | List 形状フィールド |
    | :--- | :--- | :--- |
    | `CLMMfdsGetMarketPrice` | `MarketPriceResponse` | `aCLMMfdsMarketPriceData` |
    | `CLMMfdsGetMarketPriceHistory` | `MarketPriceHistoryResponse` | `aCLMMfdsMarketPriceHistoryData` |
    | `CLMAuthLoginRequest` | ログイン応答 | List 系フィールド全般（warning list / notice list 等、サンプル `e_api_login_response.txt` で List shape のものを T2 着手時に最終列挙し本表を更新する） |
- [x] ✅ `python/engine/exchanges/tachibana_master.py` — `CLMEventDownload` ストリームパーサ（チャンク境界・`CLMEventDownloadComplete` 終端）
- [x] ✅ **`CLMEventDownload` 終端 + chunk 境界エッジケーステスト（MEDIUM-C3-2）**: `test_chunk_breaks_between_records_clean_boundary` / `test_chunk_breaks_just_before_terminator_brace` / `test_chunk_breaks_in_middle_of_record` の 3 件を実装。いずれもパース結果が同一の完全レコード列になり、`CLMEventDownloadComplete` 観測時点で終了と判定されることを assert。
- [x] ✅ **ticker pre-validate（HIGH-3、F-M11、L1 修正、MEDIUM-6 注記）— Python 側完了 / Rust 受信側は T4**: `tachibana_master.py` の `_ISSUE_CODE_RE = r"[A-Za-z0-9]{1,28}"` で pre-validate、`is_valid_issue_code` で T1 段階の Python 側責務を完了。**Rust IPC 受信側 (`engine-client/src/backend.rs`) の再 validate は `TickerInfo` 受信経路を実装する T4 のタスクに繰越**（現リポジトリにはまだ受信ハンドラ自体がない）。
  > **Phase 2 拡張注意（MEDIUM-6）**: `[A-Za-z0-9]` は `Ticker::new` の実制約（ASCII / `|` 不含）より**厳しい**。Phase 1 立花株式マスタでは英数字のみのため実害なし。ただし Phase 2 で先物・OP マスタ（`CLMIssueMstSak` / `CLMIssueMstOp`）を追加する際に限月コード等でハイフン・スラッシュが来ると**サイレント skip**する。Phase 2 着手時にこの正規表現を `Ticker::new` の実制約（ASCII 制御文字・`|` のみ除外）に緩和することを Phase 2 タスクに記載すること

- [ ] **`EngineEvent::TickerInfo.tickers[*]` dict の Rust 受信側 warn 規約（MEDIUM-7）**: `engine-client/src/backend.rs` の `TickerInfo` 受信経路で、各 ticker dict から `display_name_ja` キーを取り出すとき、キーが存在しない場合は `tracing::debug!("tachibana ticker dict missing display_name_ja: {}", ticker_symbol)` を出す（warn ではなく debug — 暗号資産 venue は常に欠落するため常時 warn だとノイズ）。`display_name_ja` が存在するが `null` の場合は `None` として扱う（正常系）。Python 側のタイポ（`display_name_jp` 等）による全件 debug ログ噴出でキー名誤りを早期発見できる
  - **据え置き理由**: Rust 側 `TickerInfo` 受信ハンドラ実装が T4 タスク。本ガード単体で先行実装すると配線が空振る。
- [x] ✅ `p_no` 採番ヘルパ（**asyncio 単一スレッド前提の単純カウンタ**、Unix 秒初期化、Lock 不要、F18）と `current_p_sd_date()`（JST 固定、SKILL.md R4） — `tachibana_helpers.PNoCounter` / `current_p_sd_date()` 実装済み、テスト緑。
  - **既知バグ回避**: SKILL.md S6 表に「セッション復元と並行で走る history fetch が逆転して `p_no <= 前要求.p_no` エラー」が記載されている。Python 移植版では **session 復元（`SetVenueCredentials` 処理）の完了前に他リクエストを発行しない**直列化を `TachibanaWorker` 内で強制し、起動レース回帰テストを 1 件追加する
  - **直列化ゲートは T3 繰越**: `TachibanaWorker` クラス自体が T3 で `tachibana.py` に新設されるため、ゲート実装はそこで一緒に書く。T2 では `StartupLatch` で同等の単一実行保証を `validate_session_on_startup` に対して既に入れている。
- [x] ✅ エラー判定ヘルパ `check_response(payload) -> None | TachibanaError`（[SKILL.md R6](../../../.claude/skills/tachibana/SKILL.md)、`p_errno` 空文字＝正常を含む） — 実装済み、`p_errno=""`/`"0"` 両ケースのテスト緑。
- [x] ✅ **制御文字 reject（F-M6b）**: `tachibana_url._FORBIDDEN_CONTROL_CHARS`（U+0000..U+001F）で `build_request_url` / `build_event_url` 双方が値文字列を pre-check。テスト緑。
- [x] ✅ **`p_no` 採番の整理（F-L5）— カウンタ層完了 / 直列化ゲートは T3**: 採番カウンタ自体（`PNoCounter`）は T1 で完了。`SetVenueCredentials` 処理中の他リクエスト抑止ゲートは `TachibanaWorker` 新設タイミング（T3）で実装する。本フェーズの責務は「カウンタは Lock 不要であることの規約化」までで完了。
- [x] ✅ **受け入れ**: 上記モジュールを単体テストでカバー、サンプルレスポンス（`samples/e_api_login_tel.py/e_api_login_response.txt` ほか）から期待値抽出ができる。REQUEST URL と EVENT URL の差を別テストで検証。`conftest.py` 共通フィクスチャ（HTTPXMock 共通 base URL / WS server fixture）を整備（F-L3）。**実測**: `python/tests/test_tachibana_*.py` 75 件緑（url 16 / codec 14 / helpers 12 / master 19 / auth 14）。
  - **Shift-JIS decode 全経路必須（HIGH-C2 / R7）**: 全 REQUEST レスポンスは `httpx.Response.content` を `decode_response_body` に通すこと。`response.text` / `response.json()` の直叩きを禁止する実装規約とし、`tachibana_auth.py` / `tachibana.py` / `tachibana_master.py` の全 REQUEST 経路で遵守。CI ガード: `grep -rnE "\.text\b|\.json\(\)" python/engine/exchanges/tachibana*.py` の出現が 0（または allowlist コメント付きのみ）であることをチェック
  - **`urllib.parse` / `httpx.URL` 標準 encoder 使用禁止 lint ガード（MEDIUM-C2-2 / R9）**: 立花 API は SKILL.md R9 の独自 30 文字置換 (`func_replace_urlecnode`) が必須で、標準 URL encoder（`urllib.parse.quote` / `urlencode` / `quote_plus` / `httpx.URL(...)` の query 自動エンコード）を経由すると規約破綻する。CI lint ガードとして `grep -rnE 'from urllib\.parse|urllib\.parse\.(quote|urlencode|quote_plus)|httpx\.URL\(' python/engine/exchanges/tachibana*.py` の出現が 0（または明示的な allowlist コメント付きのみ）であることをチェック。さらに `build_request_url` / `build_event_url` の docstring に「**標準ライブラリ URL encoder への委譲は禁止。立花は SKILL.md R9 の独自置換テーブルを使う**」を 1 行明記する
    - **CI lint ガード自体の実装は T7**（`tools/secret_scan*` と同じ pre-commit / CI ジョブで束ねる）。docstring 明記は `tachibana_url.py` の `build_request_url` / `build_event_url` で完了済み。
  - **`check_response` 単体テスト（MEDIUM-C5 / R6）**: `p_errno=""`（空文字）と `p_errno="0"` の 2 ケースをいずれも正常扱い（`None` を返す）として assert。`p_errno="2"` 等は `TachibanaError` を返すことも併せて検証 — `test_tachibana_helpers.py` で実装済み。
  - **`sWarningCode` 対応方針（C-M1）**: `sWarningCode != ""` のとき、Phase 1 では値を読み取って `logging.warning`（Python 側）に流すが戻り値には影響しない（正常扱い）。受け入れテストに `sWarningCode` 付き正常レスポンスのケースを追加し、`check_response` が `None` を返すこと（`TachibanaError` を返さないこと）を assert する。
  - **`p_sd_date` JST 単一化 CI ガード（MEDIUM-C8 / R4）**: `grep -rnE 'datetime\.now|time\.time' python/engine/exchanges/tachibana*.py` の出現が `current_p_sd_date` 内部以外で 0 であることを CI（lint ジョブ）でガード。`tachibana_master.py` のキャッシュ JST 日付生成等は `current_p_sd_date` 経由か allowlist コメント付き
    - **CI lint ガード本体の実装は T7**。`PNoCounter.__init__` の `time.time()` には allowlist コメントを既に記載（commit `1338c76`）。

## フェーズ T2: 認証フローと session 管理（2 日）

**ゴール**: `CLMAuthLoginRequest` 経由でデモ環境に対しログインできる。

> **進捗 (2026-04-25)**: モジュール本体・StartupLatch・URL スキーム検証・両 ピン留めテスト群を完了。`python/tests/test_tachibana_auth.py` 14 件すべて緑、`cargo check --workspace` 緑。
> **設計判断（実装）**:
> - `tachibana_auth.login()` の HTTP 入口は `httpx.AsyncClient` を**呼出側から DI 可能**（テストで `pytest-httpx` の `httpx_mock` が捕まえられる、本番は `TachibanaWorker` の共有 client を渡す前提）。
> - `BASE_URL_PROD` / `BASE_URL_DEMO` を `tachibana_url.py` に**唯一の出現箇所**として配置（F-L1, T7 secret_scan の allowlist 対象）。`AuthUrl` newtype を新設し、`build_auth_url()` で `auth/` セグメント付加と sJsonOfmt=5 強制を行う（auth は Master/Request/Price と URL 形が違うため `build_request_url` を流用しない）。
> - **login_path フラグ**: `_raise_for_error(data, login_path=True)` のとき、`SessionExpiredError` / `UnreadNoticesError` 以外の API エラーは `LoginError(code=元 code, message=元 message)` に**bucket** する。Rust 側 `VenueError.code` 列が enum 化される T0.2 設計と整合させるため、auth-time の generic な `p_errno` / `sResultCode` 値はそのまま `code` に流す（`login_failed` への画一的な書き換えはしない）。受け入れ条件側の "code='login_failed'" 期待は architecture.md §6 の「認証失敗」行が指す広義のバケットであり、未読通知・session_expired を除く全エラーが `LoginError` 系になる、という規約として実装している。
> - **StartupLatch.run_once**: 引数として渡された未 await コルーチンが **2 回目以降で `RuntimeWarning: coroutine was never awaited`** を出さないよう、early-return 時に `coro.close()` を明示的に呼ぶ。`asyncio.iscoroutine` で `Awaitable` 全般（Task など）と区別する。
> - **F-B3 expires_at_ms=None**: ログイン応答に明示期限がないため Phase 1 は `None` 固定。`validate_session_on_startup` を必ず通す safe path 専用の値。Phase 2 で `CLMDateZyouhou` の閉局時刻を入れる。
>
> **Tips**:
> - `pytest-httpx` の `add_response(url=re.compile(...))` で R9 の bespoke percent-encoded クエリを正規表現マッチングできる。クエリ内容は `urllib.parse.unquote(url.split("?",1)[1])` → `json.loads()` で復号。
> - レスポンス body は **必ず `decode_response_body` 経由（Shift-JIS）**。`Response.text` / `Response.json()` を直接呼ぶと R7 / HIGH-C2 違反。テスト fixture も `payload.encode("shift_jis")` で構築している。
> - 立花の login response 実例は [`samples/e_api_login_tel.py/e_api_login_response.txt`](../../../.claude/skills/tachibana/samples/e_api_login_tel.py/e_api_login_response.txt)。テスト固定値はこれを下敷きに `e_api_v4r8` パスへ置換。
>
> **レビュー反映 (2026-04-25)**:
> - **HIGH (p_no 単調性)**: `login()` / `validate_session_on_startup()` の `p_no: int` パラメータを廃止し、`p_no_counter: PNoCounter` を**必須キーワード**化。各呼出で `.next()` を 1 回消費するため、起動時再ログインや retry で `p_no` を再送する事故を構造的に排除（R4）。回帰防止テスト `test_login_consumes_p_no_counter_so_retries_are_monotonic` 追加。
> - **MEDIUM (single-source ホスト)**: `test_tachibana_auth.py` 内の `kabuka.e-shiten.jp` 直書き（テスト本体・コメントとも）を完全に削除し、`BASE_URL_DEMO.value` から派生させて URL を構築。これで T7 の `tools/secret_scan_patterns.txt` (`kabuka\.e-shiten\.jp`) は `tachibana_url.py` ファイル単位 allowlist だけで通る。
> - **MEDIUM (LoginError pin)**: `test_login_p_errno_minus_62_raises_login_error` / `test_login_authentication_failure_raises_login_error` の期待型を `TachibanaError` から `LoginError` に締め直し。`_raise_for_error(login_path=True)` の bucket 動作が将来素通しに戻ったときに即検知できる。
>
> **レビュー反映 第 2 ラウンド (2026-04-25)**:
> - **MEDIUM (バナー文言の Python 集中化、F-Banner1)**: T3/T6 まで先送りせず本フェーズで確定。`tachibana_auth.py` 冒頭に `_MSG_LOGIN_FAILED` / `_MSG_SESSION_EXPIRED_STARTUP` / `_MSG_TRANSPORT_ERROR` / `_MSG_LOGIN_PARSE_FAILED` / `_MSG_VIRTUAL_URL_INVALID` を**固定日本語文字列**として宣言。`_raise_for_error` は `LoginError(code, _MSG_LOGIN_FAILED)` の形で生成し、サーバ由来の `p_err` / `sResultText` は `log.error(...)` でのみ残す（UI には流さない）。`SessionExpiredError` も login path / runtime path の両方で `_MSG_SESSION_EXPIRED_STARTUP` を使う（`tachibana_helpers.SessionExpiredError` のデフォルト文字列はテスト fixture / 例外クラス互換のため残置するが、auth 経路は必ず override する）。回帰テスト `test_login_failure_message_uses_fixed_japanese_banner` / `test_session_expired_message_is_python_composed` を追加し、サーバ文字列が `.message` に混入しないことを assert。
> - **MEDIUM (`raise_for_status` 欠落)**: `login()` / `_do_validate()` の HTTP 呼出を `_safe_get(client, url)` に集約し、`resp.raise_for_status()` + `httpx.HTTPError` 全般を catch して `LoginError(code="transport_error", message=_MSG_TRANSPORT_ERROR)` に写像。これで 502 / 503 や proxy の HTML 応答が「JSON parse failed」に化けることを排除。`code="transport_error"` は architecture.md §6 の transport 障害バナー経路と整合。回帰テスト `test_login_http_502_maps_to_transport_error` / `test_validate_session_http_503_maps_to_transport_error` を追加。

- [x] ✅ `python/engine/exchanges/tachibana_auth.py`
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

    - **`session=None` の cold start 分岐（C-M2）**: `validate_session_on_startup` の呼出元は `session=None` のとき（cold start / keyring なし）validation をスキップしてログインフローに直進する。関数内部では `session is None` チェックを行わず、呼出側（`TachibanaWorker` の `SetVenueCredentials` 処理）で `if session is None: await login_flow()` の分岐を持つ。`test_tachibana_auth.py` の受け入れ条件に「`session=None` の cold start で `validate_session_on_startup` が呼ばれずログインフローに直進することを確認するテスト（`test_cold_start_without_session_skips_validation`）」を追加すること。
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
- [x] ✅ **起動時のみ再ログイン**のガードを実装: `SetVenueCredentials` の session validation 中に限り `user_id/password` fallback を許可し、購読開始後の `p_errno="2"` は再ログインせず `VenueError{code:"session_expired"}` を返す
  - 実装ノート: `tachibana_auth.login()` と `validate_session_on_startup()` を関数として分離。runtime 経路から `StartupLatch.run_once` 2 回目を呼ぶと `RuntimeError`（L6）。runtime 中の `p_errno=2` 検知 → `SessionExpiredError` 直送（`_raise_for_error(login_path=False)`）。これらの**配線**（`SetVenueCredentials` ハンドラ、再ログイン許可フラグ、`VenueError{code:"session_expired"}` への変換）は T3 で `tachibana_login_flow.py` / dispatch 側に実装する。本タスクは「再ログインは validate 中だけ」のための関数境界を確定させる責務まで。
- [x] ✅ mock サーバテスト（`pytest-httpx` の `HTTPXMock`、`python/tests/test_binance_rest.py` パターン踏襲）で正常系・異常系（`p_errno=-62` / `=2` / 認証失敗 / `sKinsyouhouMidokuFlg=1`）
- [x] ✅ **`CLMAuthLoginRequest` の `sJsonOfmt="5"` 固定テスト（MEDIUM-C3-1）**: `test_login_request_uses_json_ofmt_five` 実装。`build_auth_url` が auth エンドポイントでは `sJsonOfmt="5"` 以外を `ValueError` で reject する型レベル強制も追加（`build_request_url` の MASTER_CLMIDS 分岐とは独立）。
- [x] ✅ **仮想 URL スキーム検証（MEDIUM-C3-3）**: `_validate_virtual_urls` を `login()` 内で呼出。`test_login_rejects_non_wss_event_url` + `test_login_rejects_non_https_request_url` の 2 件で 4 URL + WS をカバー。
- [x] ✅ **`sKinsyouhouMidokuFlg` 未読通知の固定テスト名（HIGH-C2-1、R3）**: `test_login_raises_unread_notices_when_kinsyouhou_flag_set` 実装。`UnreadNoticesError.code == "unread_notices"` を assert。後続の IPC 経路 (`VenueError.code`) への変換は T3 dispatch 層の責務（本テストは Python 単体での発生を pin）。
- [x] ✅ **`validate_session` 実機リクエスト形式の固定テスト（HIGH-D2）**: `test_validate_session_uses_get_issue_detail_with_pinned_payload` 実装。(a) `sUrlMaster` プレフィックス、(b) GET、(c) sCLMID / sIssueCode / sSizyouC、(d) sJsonOfmt="4" の 4 点を assert。
- [x] ✅ **`validate_session_on_startup` の `RuntimeError` → supervisor 統合テスト（MEDIUM-D2-1、L6 修正の検証）** — T3 で実装済 (`python/tests/test_tachibana_startup_supervisor.py`、subprocess 経由で実 `_do_set_venue_credentials` を 2 度叩き、`os._exit(2)` 経路 + 全 secrets 非漏洩を pin): `python/tests/test_tachibana_startup_supervisor.py::test_runtime_error_from_validate_terminates_process_with_log` を新設。`subprocess` 経由で `python -m engine` を起動し、`StartupLatch.run_once` を 2 回呼ばせるテスト fixture を経由して 2 回目の `RuntimeError` を発生させ、(a) `engine/server.py` トップレベル supervisor で catch されてプロセスが exit code 非ゼロで終了、(b) stderr に `tracing::error!` 相当の 1 行が出ていること、(c) その error 行に `user_id` / `password` / session token などの creds 文字列が**含まれていない**こと、を assert
  - **未着手の理由**: `engine/server.py` のトップレベル supervisor が `RuntimeError` を catch してプロセス終了させる経路自体が現リポジトリにまだ存在しない（T3 の `SetVenueCredentials` ハンドラ実装と同時に追加するのが自然）。先行して subprocess テストだけ書くと supervisor 側のスタブ実装に引きずられて test-first ができない。**T3 着手と同タイミングで本タスクを実装する**ことに決定。T2 では `StartupLatch` の `RuntimeError` 発生条件（成功後 / 失敗後 / 並行）を Python 単体テスト 4 件で完全にカバーしているため、unit レベルの保証は揃っている。
- [x] ✅ **受け入れ**: `pytest -m demo_tachibana` で実 demo 環境ログイン成功（手動電話認証済みアカウント前提）
  - **実施日**: 2026-04-27
  - **ハング原因の確定した根本原因**: `httpx.AsyncClient(timeout=15.0)` のスカラー値は Windows において TCP connect フェーズに適用されず、仮想 URL 期限切れ時（DNS は解決するが TCP SYN に応答なし）に無期限でブロックする silent hang を引き起こす。`httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)` のコンポーネント指定形式に変更することで回避。
  - **修正箇所**:
    - `python/engine/__main__.py`: `main()` 冒頭に `logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)` を追加（Python ログが NullHandler に吸われ Rust 側に転送されていなかった問題を解消）
    - `python/engine/exchanges/tachibana_auth.py`: `login()` 内および `_do_validate()` 内の `httpx.AsyncClient(timeout=15.0)` を `httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)` に変更
    - `python/tests/test_tachibana_demo_login.py`: `pytest -m demo_tachibana` で実行するデモログインテスト 2 件を新規作成（`test_demo_login_returns_valid_session` / `test_demo_session_validates_on_startup`）
  - **再現・回避 Tips**:
    - Windows で silent hang が疑われる場合はまず httpx の timeout を `httpx.Timeout(connect=N, ...)` 形式に変更すること
    - 環境変数引き渡しは `set -a && source .env && set +a` を使う（`source .env` のみでは Python subprocess に変数が引き継がれない）
    - デバッグビルドで `cargo run` したとき `dev_tachibana_login_allowed=true` になる（release ビルドでは `false`）
    - `uv run pytest python/tests/ -m demo_tachibana -v` の前に `.env` を読み込むこと

- [x] ✅ **demo CI レーン方式の早期決定（MEDIUM、ユーザー指摘ラウンド 7）**: 案 **(B) manual lane only** を T2 暫定確定として採用する:
  - 理由: [open-questions.md Q21](./open-questions.md#q21--demo-環境の運用時間) の demo 運用時間が未確定の段階で PR チェック（ブロッキング / non-blocking 問わず）に組み込むと、閉局帯ヒットで開発者が偽陽性失敗を踏む。`workflow_dispatch` のみ許可なら閉局帯リスクが起動者に閉じる。
  - 実装場所: T7 で `.github/workflows/tachibana-demo.yml`（仮）を新設し、`on: workflow_dispatch:` のみで `uv run pytest -m demo_tachibana -v` を走らせる。PR / push トリガは載せない。
  - 再評価条件: Q21 の運用時間が T2 実機ログインで確定したら案 (A) への移行を再検討する（**T2 終了時点では実機ログイン未実施のため案 (B) で固定**）。
  - 旧 3 案候補（A: non-blocking PR job / B: manual lane / C: CI 不採用）の比較は本タスク完了とともに本文から外す。

## フェーズ T3: クレデンシャル受け渡し配線（2 日）

**ゴール**: Rust が keyring からクレデンシャルを取り出し、Python が `VenueReady` を返すまで往復する。

> **進捗 (2026-04-25)**: バックエンド配線完了・実機 demo ログイン成功。`pytest` 292 件 / `cargo test --workspace` 全件緑、`cargo clippy --workspace -- -D warnings` 緑。実機ログイン smoke で「Tachibana session validated successfully」確認済 (`scripts/smoke_tachibana_login.py` 経由)。Rust UI バナー / sidebar ボタン拡張 (F-M1a, F-L9 のフロント側) と E2E shell スクリプト (MEDIUM-D3) は次イテレーションへ繰越。env fast path（dev_login_allowed + dev env）→ `tachibana_auth.login` → `validate_session_on_startup` の経路は本フェーズで完成。
>
> **設計判断（実装）**:
> - **stdin payload (HIGH-B2-1)**: `engine-client/src/process.rs::spawn_with` を `serde_json::json!({...}).to_string()` に置換。`dev_tachibana_login_allowed` を `cfg!(debug_assertions)` に同期し、release ビルドで env fast path を無効化（H-2、R10）。`config_dir` / `cache_dir` 追加は T4 で同 JSON ビルダーへ追記する形で設計。
> - **`__main__.py` 後方互換**: stdin payload に未指定キーがあっても `setdefault` で `False` / `None` にフォールバックし、旧 Rust バイナリと新 Python の組合せでも起動する。
> - **`StartupLatch` supervisor 終端 (L6, MEDIUM-D2-1)**: `_do_set_venue_credentials` 内で `RuntimeError` を catch → stderr に固定 banner 文言 + `os._exit(2)` でプロセス強制終了。`tracing::error!` 相当 1 行を残し、creds 文字列は混入しない（テスト pin 済）。
> - **dev env エイリアス**: `tachibana_login_flow._load_dev_env()` は canonical `DEV_TACHIBANA_*` を優先しつつ、本リポジトリ既存 `.env` の `DEV_USER_ID` / `DEV_PASSWORD` / `DEV_IS_DEMO` も legacy alias として受理する（SKILL.md S2 の運用クイックスタートと整合）。`DEV_TACHIBANA_DEMO` 既定 `True`（F-Default-Demo）。
> - **second_password Phase 1 ガード (H2)**: `From<&TachibanaCredentials> for TachibanaCredentialsWire` 冒頭に `debug_assert!(c.second_password.is_none(), ...)` を配置。debug ビルドで panic、release は noop。`data/tests/tachibana_keyring_roundtrip.rs::test_phase1_second_password_guard_panics_in_debug` で pin。
> - **`classify_venue_error` (MEDIUM-5, F-L9)**: `engine-client/src/error.rs` に `VenueErrorClass { severity, action }` テーブル駆動関数として追加。architecture.md §6 の 6 行（session_expired / login_failed / unread_notices / phone_auth_required / ticker_not_found / transport_error）を網羅、未知 code は `(Error, Hidden)` で fail-safe。Banner レンダラ (T3 UI 拡張時) はこの戻り値だけ参照する設計。
> - **keyring backend テスト (MEDIUM-D3-3)**: `keyring::mock::default_credential_builder()` は `Entry::new` ごとに独立した `MockData` を作るため `save → load` のラウンドトリップが観測できない。代わりに `data/tests/tachibana_keyring_roundtrip.rs` 内に **process-shared な `SharedBuilder`**（HashMap<(service,user), Vec<u8>> ベース）を定義し、`set_default_credential_builder(Box::new(SharedBuilder))` で差し替えて real round-trip + Debug マスク + Zeroize 経路を検証。OS 実 keyring を絶対に触らない。
> - **`_do_validate` 実機修正**: T2 で `sIssueCode="7203"` + `sSizyouC="00"` としていたが、demo 環境 smoke で `code=-1, sTargetIssueCode:[NULL]` エラーを観測。マニュアル `mfds_json_api_ref_text.html#CLMMfdsGetIssueDetail` を再確認し、正しいパラメータ名は `sTargetIssueCode`（カンマ区切りの銘柄コードリスト）であることを確定。`tachibana_auth._do_validate` を修正、HIGH-D2 pinned テストも `sTargetIssueCode` に更新。実機で「Tachibana session validated successfully」ログ取得済。
>
> **Tips**:
> - 実機 demo 用 smoke スクリプト: `uv run python scripts/smoke_tachibana_login.py` — `.env` の `DEV_USER_ID` / `DEV_PASSWORD` / `DEV_IS_DEMO` を読み、`run_login` → `validate_session_on_startup` まで本番経路で実行する。手動電話認証済アカウント前提。
> - `tachibana_login_dialog.py` には `--headless` モードを実装。stdin の prefill JSON でバリデーション関数 (`validate_input`) を直叩きし、tkinter を出さずに `pytest -m gui` 不要で経路をカバーできる。
> - `tachibana_login_flow.run_login` の Cancel 経路は `_spawn_login_dialog` 戻り値 `None` で表現。retry ループ (3 回) は LoginError のみ周回、UnreadNoticesError / SessionExpiredError は即時返す。
>
> **レビュー反映 (2026-04-25, ラウンド 1)**:
> - **#1 `SetVenueCredentials` → `VenueReady` ゲート**: `ProcessManager::start()` 内で SetVenueCredentials 送信前に `connection.subscribe_events()` を取り、各 `request_id` に対応する `VenueReady`/`VenueError` を `VENUE_READY_TIMEOUT=60s` まで待ってから Subscribe を再送する経路に変更。タイムアウト時は warn ログを出して subscribe を続行（接続自体は維持）。`engine-client/tests/process_venue_ready_gate.rs` で SetVenueCredentials → VenueReady → Subscribe の順序契約を pin。
> - **#2 `VenueCredentialsRefreshed` 取りこぼし**: `ProcessManager.on_venue_credentials_refreshed: Arc<Mutex<Option<OnVenueCredentialsRefreshed>>>` フィールドを追加。`main.rs` は `manager.start()` 呼出**前**にコールバックを `set_on_venue_credentials_refreshed` で注入する。`start()` 内の `SetVenueCredentials` → `VenueReady` 待ちループは `VenueCredentialsRefreshed` を捕捉し、(a) `patch_in_memory_session` で `venue_credentials` の `session` を即座に差し替え（次回再起動の再注入素材を更新）、(b) 登録コールバックを呼び keyring 永続化を main.rs 側で実行する。post-start のランタイム listener も維持（`RequestVenueLogin` 由来の runtime refresh をカバー）し、両者は idempotent な書込みなので重複しても収束する。`engine-client/tests/process_creds_refresh_hook.rs` で hook 発火と in-memory 更新を pin。
> - **#3 `UnreadNoticesError` を terminal `VenueError` 化**: `_do_set_venue_credentials` の例外ハンドラを `UnreadNoticesError` → `LoginError` → `TachibanaError` の順に並べ替え。未読通知は自動再ログインに落とさず、`VenueError{code:"unread_notices", message:...}` を 1 件発火して **そのまま return**（`tachibana_run_login` を呼ばないため `VenueLoginStarted` も発火しない）。`python/tests/test_tachibana_unread_notices_terminal.py` で「VenueError 1 件・VenueLoginStarted/VenueReady/VenueLoginCancelled 0 件・dialog spawn 0 件」を pin。
>
> **レビュー反映 (2026-04-25, ラウンド 3)**:
> - **#1 初回ログイン keyring 永続化**: `data/src/config/tachibana::update_session_in_keyring` が「既存エントリあり」のときしか上書きせず、初回成功時の `VenueCredentialsRefreshed` を捨てていた。`load → None` の経路で**session-only エントリを新規作成**するように変更（`is_demo` は session URL の `demo-kabuka.e-shiten.jp` 部分一致から推論、user_id/password は空文字で OK — Phase 1 の startup fallback は env/dialog なので keyring の creds は読まれない）。再起動 keyring 復元の連続性を保証。`data/tests/tachibana_keyring_roundtrip.rs::test_update_session_in_keyring_creates_entry_when_none_exists` / `test_update_session_in_keyring_preserves_existing_user_id` で pin。
> - **#2 `VenueLoginStarted` の意味論ずれ**: `run_login` 冒頭で無条件に発火していたため、env fast path や fallback creds 経路でもダイアログ未起動のまま UI に「別ウィンドウでログイン中」を表示できてしまっていた。**dialog spawn 直前のみ**発火する位置に移動。env fast path / 静かなフォールバック経路は `_try_silent_login` ヘルパーに集約し、成功で `VenueCredentialsRefreshed + VenueReady` のみ、失敗時に呼出側が VenueError を積む形にした。`python/tests/test_tachibana_login_started_semantics.py` で「fast path: VenueLoginStarted 未発火」「dialog path: 発火」「fallback path 静か / 失敗時のみ dialog で発火」「unread_notices で terminal」の 5 件を pin。
> - **#3 startup re-login で payload creds が unused だった件**: docstring と実装が乖離していた。`_do_set_venue_credentials` が `payload["user_id"] / "password" / "is_demo"` を `tachibana_run_login(..., fallback_user_id=, fallback_password=, fallback_is_demo=)` 経由で渡し、`run_login` は `is_startup=True` のときのみ silent fallback ログインを試みる。失敗種別を再分類し、`unread_notices`/`session_expired` は terminal、`login_failed`/`transport_error` は dialog にフォールスルー。dialog spawn 時は `prefill={"user_id": ..., "is_demo": ...}` で user_id を渡し再入力負担を軽減。
>
> **完了範囲の整理（plan の表記分離）**:
> - 上記は **バックエンド配線のみの完了**。 Rust UI の `RequestVenueLogin` 発火導線（sidebar ボタン）と Banner 拡張は未着手で **次イテレーション扱い**。本セクション末尾の「繰越 / 次イテレーション」一覧で明示している。
>
> **繰越 / 次イテレーション**:
> - **Rust UI sidebar ボタン + Banner 拡張 (F-M1a / H3 / F-Login1)**: 本フェーズ完成のためには `tickers_table::exchange_filter_btn` 経路に着地（T3.5 Step D）し、Tachibana 行のログインアイコン追加、`Banner` レンダラに `VenueLoginStarted` / `VenueLoginCancelled` 状態描画を追加する必要があるが、iced widget 構造の調整範囲が広いため別 PR で扱う。env fast path 実機テストは UI ボタン無しでも完了する。
> - **MEDIUM-D3 E2E shell**: `tests/e2e/tachibana_relogin_after_cancel.sh` は HTTP API 経由のキャンセル → 再ログイン経路を要求。Rust UI 側の `RequestVenueLogin` 発火経路（sidebar ボタン）が無い現状ではドライブできないため、UI 拡張と同 PR で実装する。
> - **H5 (Bundled to_str fallback)**: `EngineCommand::Bundled(p).program()` が `to_str().unwrap_or("flowsurface-engine")` で fallback している件。Windows 日本語 user パス等で潜在的な silent skip。**UI 拡張 T3.5 と同 PR** で `Path` をそのまま受ける版に直す。
> - **H6 (test mock 所有権)**: `data/tests/tachibana_keyring_roundtrip.rs` の SharedBuilder/SharedStore がプロセス共有 `OnceLock<Mutex<HashMap>>` で複数テスト間に状態漏洩する件。`#[serial_test::serial]` は導入したが、テスト ID + 各テスト先頭で `delete_credential().ok()` する pattern を T3.5 で整理する。
> - **H7 / H8 / H9 (iced 逸脱: ENGINE_CONNECTION static / block_on / callback と Subscription 二重経路)**: `src/main.rs` の `static ENGINE_CONNECTION: RwLock` + `rt.block_on` + 手動 reconnect callback は iced の Subscription / Task モデルから外れている。**UI 拡張 T3.5 と同 PR**（sidebar / banner と同じ層に閉じ込めて Subscription ベースに寄せる）。
> - **H12 / H13 (型負債)**: `VenueReady` を typestate で表現する案 / `second_password` Wire 残存（`Option<String>` のまま wire に乗る）— **Phase O1 (Phase 2 直前のリファクタ)** に持ち越し。
> - **W1 (handshake recv timeout)**: `EngineConnection::connect` の Hello / Ready 受信が timeout を持っていない件。HangしたPython を検知できない。**別 PR** で `tokio::time::timeout` を被せる。
> - **M-12 (StoredCredentials Debug derive コメント)**: `StoredCredentials` は `Deserialize / Serialize` だけで `Debug` は derive していないが、コメントでその理由を 1 行残すべき。次回触るときに同梱。
> - **M-19 (`VenueCredentialsRefreshed` Option<→ enum 化)**: 現在 `user_id` / `password` / `is_demo` を `Option<...>` で持っているが、本来 (a) all-Some（current emitter）か (b) all-None（legacy emitter）の 2 状態しか取らない。enum で表現すれば main.rs の `match (a, b, c)` から `(Some, Some, Some)` 残ケースが消える。次回 schema bump タイミング（Phase O1）で対応。
>
> **レビュー反映 (2026-04-25, ラウンド 4)**:
>
> Group A — docs only:
> - ✅ **H11 + 持ち越し追記**: `architecture.md` §2.3 の `VenueCredentialsRefreshed` DTO に `user_id / password / is_demo` を追記し、Phase 1 で plaintext を IPC に乗せる根拠（keyring drift 防止 + outbox 1 hop）と MEDIUM-C6 例外条項を明文化。本ファイル「繰越 / 次イテレーション」に H5/H6/H7/H8/H9/H12/H13/W1/M-12/M-19 を追加。
>
> Group B — Rust 単体:
> - ✅ **C1 (keyring 並列競合)**: `data/Cargo.toml [dev-dependencies]` に `serial_test = "3"` 追加、`data/tests/tachibana_keyring_roundtrip.rs` の 6 テスト全てに `#[serial_test::serial]` 付与。並列実行 5 連続グリーン。
> - ✅ **M-5 (load_tachibana_credentials silent parse failure)**: `serde_json::from_str.ok()?` を `match` 展開し、Err 時 `log::warn!("tachibana keyring entry is corrupt: {e}")`。回帰テスト `test_load_tachibana_credentials_warns_when_keyring_payload_is_corrupt`（mock keyring に `"{not-json"` を仕込み `None` 返却を pin）。
> - ✅ **H4 (save_refreshed_credentials password を `Zeroizing<String>` 化)**: シグネチャ変更、main.rs 側 `password.clone()` で `Zeroizing` のまま渡す。`data/Cargo.toml` に `zeroize` 本体依存追加。回帰テスト `test_save_refreshed_credentials_takes_zeroizing_password` でコンパイル契約を pin。
>
> Group C — Rust process.rs / error.rs:
> - ✅ **M-3 silent (SetVenueCredentials send 失敗握り潰し)**: `apply_after_handshake` の `let _ = connection.send(...)` を `if let Err(...)` で error 分岐、warn + pending 削除 + failed_venues 追加 + continue。回帰テスト `engine-client/tests/process_send_failure_skips_subscribe.rs::apply_after_handshake_skips_subscribe_when_set_creds_send_fails`（mock WS が handshake 直後に close → cmd_rx drop → send 失敗 → 5 秒以内に return することで 60 秒ハングを防止）。
> - ✅ **M10 (VenueReady タイムアウト時 failed_venues 更新)**: タイムアウト 2 箇所（外側 deadline + 内側 `Err(_elapsed)`）で残 pending を failed_venues に移動。`apply_after_handshake_with_timeout(connection, Duration)` テスト seam を追加（production は引数無し版が 60 秒固定）。回帰テスト `process_venue_ready_timeout_marks_failed.rs` で「200ms タイムアウト → SetVenueCredentials 送信あり / Subscribe 送信なし」を pin。
> - ✅ **M11 (process_creds_refresh_hook テストを実 API 経由に)**: `refresh_hook_callback_fires_with_session` を `ProcessManager::handle_credentials_refreshed` 直叩きに書き換え、in-memory store の patch 副作用も同テストで pin。
> - ✅ **M9 (classify_venue_error テスト exhaustive)**: `architecture_md_section_6_table_is_covered` のループを 6 個別 assert（session_expired / login_failed / unread_notices / phone_auth_required / ticker_not_found / transport_error）に分解。緩い `!= Hidden || == Error` 条件を排除。
>
> Group D — Python server.py:
> - ✅ **H1 (`_do_request_venue_login` の `except (KeyError, TypeError): pass`)**: `_do_set_venue_credentials` と同じ pattern に揃え、malformed VenueCredentialsRefreshed → `VenueError{code:"session_restore_failed"}`。
> - ✅ **H3 / M-14 (両 dispatcher の最外層 except Exception)**: 両関数で `tachibana_run_login` 呼出を try/except Exception で包み、`log.exception` 詳細 + `VenueError{code:"login_failed", message:_MSG_LOGIN_FAILED}`。新規テスト `test_tachibana_login_unexpected_error.py`（2 件）で「`RuntimeError("forced")` monkeypatch → VenueError 1 件 / banner 文言固定 / `'forced'` 非混入」を pin。
> - ✅ **M-7 (`_restore_session_from_payload` 例外幅)**: `(KeyError, TypeError, ValueError, AttributeError)` に拡張（`str()` への None 渡し等を含む幅広い malformed payload を catch）。
>
> Group E — Python login_flow / dialog / auth:
> - ✅ **H2 / M-3-py / M-15 (`_spawn_login_dialog` stdin BrokenPipe で即時 abort)**: `BrokenPipeError` / `ConnectionResetError` 検知時に `proc.terminate()` + `LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)` を即 raise。`test_tachibana_login_helper_broken_pipe.py` で「`asyncio.wait_for` 到達なし + helper terminated」を pin。
> - ✅ **H10 (`_load_dev_env` legacy alias 削除)**: `DEV_USER_ID` / `DEV_PASSWORD` / `DEV_IS_DEMO` を全廃。`DEV_TACHIBANA_*` のみ受理。`scripts/smoke_tachibana_login.py` docstring も追従。回帰テスト `test_legacy_dev_env_aliases_no_longer_trigger_fast_path` 追加（legacy 3 変数全部 set + canonical 3 変数全部 unset → fast path 起動せず dialog spawn）。**注意: 開発者の `.env` を `DEV_TACHIBANA_USER_ID/PASSWORD/DEMO` に rename する必要あり（本コミットでは `.env` 自体は触っていない）**。
> - ✅ **M-4 (`_read_stdin_payload` stdin EOF を {} 扱い)**: 空 stdin 時に `_emit_result({"status":"cancelled"})` + `sys.exit(2)`。回帰テスト `test_empty_stdin_exits_non_zero_with_cancelled_payload` で exit code != 0 を pin。
> - ✅ **M16 (headless `allow_prod_choice=False` で `is_demo=True` 強制)**: `_run_headless` で `allow_prod=False` のとき prefill 内容を無視して `is_demo=True`。回帰テスト 2 件（`test_headless_forces_is_demo_true_when_prod_choice_disallowed` / `test_headless_honours_is_demo_when_prod_choice_allowed`）で双方向 pin。
> - ✅ **M2 / M-5 / M-17 (`__main__.py` CLI/env-var モードでも fast path 制御)**: `_env_dev_login_allowed()` ヘルパで `FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED` env をチェック、CLI / env-var 経路でこれを参照。stdin 経路は Rust 制御である旨をコメント明記。`test_tachibana_main_dev_flag.py` で truthy / falsy / default の 3 ケース pin。
>
> Group F — REFACTOR-only:
> - ✅ **M1 / L-2 (`_raise_for_error` 重複コード)**: SessionExpiredError 分岐の `if login_path` 両分岐が同じ → 1 行化。既存テスト緑のまま維持。
> - ✅ **M-13 / L-1 / L-7 (docstring 更新 + 未使用 helper 削除 + `_latch` → `latch` rename)**: `tachibana_login_flow.py` docstring を H10 反映 + `fallback_*` 言及。`test_tachibana_dev_env_guard.py` の未使用 `asyncio.get_event_loop().run_until_complete` ヘルパを削除。`validate_session_on_startup` の `_latch` キーワード引数を `latch` に rename、callers (`server.py`, smoke スクリプト, supervisor テスト, `test_tachibana_auth.py` 4 箇所) を全て更新。
>
> **レビュー反映 (2026-04-25, ラウンド 5)**:
>
> Group A — Rust 単体:
> - ✅ **R4-1 (`src/main.rs` `password.clone()` → 移動)**: `VenueCredentialsRefreshed` ハンドラ内 `match (...) { (Some(_), Some(password), Some(_)) => save_refreshed_credentials(user_id, password.clone(), ...) }` の `.clone()` を削除し `password` を直接 move。`Zeroizing<String>` 二重ヒープコピーを排除（intermediate copy が keyring 書き込み後も生存して zeroize されない問題の構造的根絶）。clippy / cargo test 緑のまま振る舞い不変。
> - ✅ **R4-2 (`apply_after_handshake_with_timeout` の API surface 縮小)**: `pub` → `#[doc(hidden)] pub` に変更（rustdoc から非公開）。**注意**: 当初指示は `pub(crate)` だったが、Rust の integration tests (`engine-client/tests/`) は別 crate のため `pub(crate)` だとアクセス不可（コンパイルエラー）。`#[doc(hidden)]` で公開 API surface からは除外しつつ integration tests のコンパイルを保つ pragmatic な落とし所として採用。docstring に R4-2 の経緯と代替案を明記。
>
> Group B — Python login_flow (orphan reap):
> - ✅ **M-15 ラウンド 5 (BrokenPipe で孤児プロセス回収)**: `_spawn_login_dialog` の `except (BrokenPipeError, ConnectionResetError)` ブロックで `proc.terminate()` 後に `await asyncio.wait_for(proc.wait(), 5.0)` で reap。`TimeoutError` / `ProcessLookupError` 時は `proc.kill()` + 2 秒の最終 reap でエスカレーション。`test_tachibana_login_helper_broken_pipe.py::test_broken_pipe_on_stdin_aborts_immediately` を拡張し、`FakeProc.wait_calls >= 1` と `returncode is not None` を assert（`terminate()` だけのコードでは fail する形に変更し RED→GREEN を実機確認）。
>
> Group C — Python server.py / __main__.py / dialog (defensive hardening):
> - ✅ **M-LOG ラウンド 5 (log.exception ローカル変数経由の secrets 漏洩を構造的排除)**: `_do_set_venue_credentials` の最外層 `except Exception` 内で `log.exception(...)` を呼ぶ前に `fallback_password = None` / `fallback_user_id = None` / `fallback_is_demo = None` / `payload = None` / `msg = None` を実行し、frame locals から credential bearings を消去。`test_tachibana_login_unexpected_error.py` の secrets リテラルを `secret-password-UNIQUE-12345` / `user-id-UNIQUE-67890` にユニーク化し、`traceback.StackSummary.extract(..., capture_locals=True)` で `engine.server` frame に password 文字列が残らないことを assert（capture_locals 形式の verbose log formatter / better_exceptions スタックでも漏洩しないことを構造的に保証）。`_do_request_venue_login` 側は creds を frame に bind しないため scrub 不要だが、シンメトリ維持用のコメントを残置。
> - ✅ **M-CFG ラウンド 5 (`__main__.py` stdin payload bool 型アサーション)**: `_coerce_dev_login_allowed(value)` ヘルパを新設し、`isinstance(value, bool)` でない場合 `log.warning("non-bool ...")` + `False` フォールバック。stdin 経路の `dev_tachibana_login_allowed` 解釈をこの helper 経由に置換し、`bool("false") == True` 系の silent enable を構造的根絶。`test_tachibana_main_dev_flag.py` に `test_parse_stdin_config_warns_and_falls_back_when_dev_flag_is_not_bool` / `test_coerce_dev_login_allowed_passes_through_real_bools` を追加。
> - ✅ **M-IO ラウンド 5 (`_read_stdin_payload` `OSError` ガード)**: `tachibana_login_dialog._read_stdin_payload` の `sys.stdin.readline()` を `try/except OSError` で包み、Windows 切り離されコンソール / Linux pty tear-down 時に `_emit_result({"status":"cancelled"})` + `sys.exit(2)` で構造化終了。unhandled traceback による親側分類不能を回避。`test_tachibana_login_dialog_modes.py::test_oserror_on_stdin_exits_non_zero` で pin。
>
> Group D — テスト hardening (secrets ユニーク化):
> - ✅ **テストの secrets ユニーク化**: `test_tachibana_login_unexpected_error.py` の `password = "p"` / `user_id = "u"` を `_UNIQUE_PASSWORD = "secret-password-UNIQUE-12345"` / `_UNIQUE_USER_ID = "user-id-UNIQUE-67890"` に変更。event repr / log record 全体に対して `assert UNIQUE not in ...` の形でチェックすることで、たまたま `"p"` が message に含まれて偽陽性にならない / 真陽性が確実に検出されるよう改善。`test_tachibana_startup_supervisor.py` は既に `uxf05882` / `vw20sr9h` / `SESSION_TOKEN_SHOULD_NOT_LEAK` の十分にユニークな sentinel 群を使用済み（再変更不要）。`test_tachibana_login_helper_broken_pipe.py` は creds を扱わないため対象外。
>
> **レビュー反映 (2026-04-25, ラウンド 6)**:
>
> Group A — Critical:
> - ✅ **CRITICAL-1 (`.env` 整理 + `.env.example` 新設)**: `.env` を `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO` の 3 行のみに整形（legacy `DEV_USER_ID` / `DEV_PASSWORD` / `DEV_IS_DEMO` / `DEV_SECOND_PASSWORD` / 裸 `PASSWORD=` / `DEMO=` を全削除）。`.env` は既に `.gitignore` 対象 + `git ls-files .env` 結果空のため history 除去は不要。`.env.example` を新設し canonical な 3 変数のテンプレートのみ記載。**ラウンド 7 追記**: 残置していた legacy の `.env.sample` を削除し、テンプレートは `.env.example` に一本化（HIGH-3）。
> - ✅ **CRITICAL-2 (`cargo fmt --check` 緑化)**: `cargo fmt --all` 実行、`cargo fmt --check` 緑。
> - ✅ **CRITICAL-3 (`update()` 内 `try_send_now` のコメント明記)**: `src/main.rs::update::Message::EngineRestarting` アームに「H7/H8/H9 と同根 / Phase O1 で iced Subscription 化」コメント追記。本件は構造変更が広範のため Phase O1 へ繰越（実装変更なし）。
>
> Group B — High (Rust):
> - ✅ **HIGH-1 (`classify_venue_error` テーブルに `session_restore_failed` / `unsupported_venue` 追加)**: `engine-client/src/error.rs` に 2 コードを追加（前者 `(Error, Relogin)`、後者 `(Error, Hidden)`）。新規テスト `session_restore_failed_is_error_relogin` / `unsupported_venue_is_error_hidden` で個別 pin。
> - ✅ **HIGH-2 (`engine-client/src/process.rs::Subscribe 再送` silent failure 解消)**: `let _ = ...` を `if let Err(err) = ...` に置換し、`venue` / `ticker` / `stream` 込みの warn ログを出力。
> - ✅ **HIGH-3 (`engine-client/src/process.rs::SetProxy` silent failure 解消)**: 同上パターン。
> - ✅ **HIGH-4 (`save_refreshed_credentials` の中間 `String` clone 排除)**: `data/src/config/tachibana.rs` に private helper `zeroizing_to_secret(z: Zeroizing<String>) -> SecretString` を新設し、`std::mem::take(&mut *password)` で内部 `String` を move。`(*password).clone()` の中間 heap allocation を排除。回帰テスト `test_save_refreshed_credentials_round_trips_under_zeroizing_helper` 追加。
> - ✅ **HIGH-6 (`--token` CLI を argparse.SUPPRESS + deprecation warning)**: `python/engine/__main__.py` で `--token` を `argparse.SUPPRESS` 化し help 非表示、使用時に `log.warning("--token CLI is deprecated...")` を 1 度だけ出す。回帰テスト `test_token_cli_emits_deprecation_warning` で pin。
> - ✅ **HIGH-7 (`_do_set_venue_credentials` 正常経路 credential scrub)**: `try` ブロックを `try/finally` で囲い、成功・失敗両経路で `fallback_password = None` 等を実行。回帰テスト `test_set_venue_credentials_scrubs_locals_on_success_path`（ソースに `finally:` リテラルが残っていることを `inspect.getsource` で構造的に pin）。
>
> Group C — High (繰越):
> - ✅ **HIGH-5 (callback type を `Fn` に変更し refresh move 化)**: 既存 `Box<dyn Fn(...) + Send + Sync>` のまま維持。`patch_in_memory_credentials` 内で `refresh.session.clone()` 等が複数回参照する構造のため、move 化には DTO 型構造の変更を伴う。**Phase O1 繰越**（VenueCredentialsRefresh 構造の見直しと併せて対応）。
> - ✅ **HIGH-8 (`data → engine-client` 逆依存)**: `data/Cargo.toml` の `engine-client.workspace = true` 行に「Phase O1 繰越: Wire DTO を data または共有 engine-types クレートへ」のコメント追記。本セクション末尾「繰越 / 次イテレーション」一覧に追加。
>
> Group D — Medium:
> - ✅ **MEDIUM-7 (`_parse_stdin_config` JSONDecodeError ハンドリング)**: `python/engine/__main__.py` で `try/except json.JSONDecodeError` を追加、`FATAL: invalid stdin payload: <exc>` を stderr に出力 + `sys.exit(2)`。raw payload は echo しない（token 漏洩防止）。回帰テスト `test_parse_stdin_config_exits_with_fatal_on_invalid_json`。
> - ✅ **MEDIUM-9 (mock WS の compression 明示コメント)**: `engine-client/tests/process_send_failure_skips_subscribe.rs` / `process_venue_ready_timeout_marks_failed.rs` / `process_creds_refresh_hook.rs` に `tokio_tungstenite::WebSocketConfig::default()` の compression 状態を tungstenite 更新時に再監査する旨のコメント追加。
> - ✅ **MEDIUM-10 (`_restore_session_from_payload` scheme 検証)**: `python/engine/server.py` で 4 つの HTTP 仮想 URL に `https://` 検証、`url_event_ws` に `wss://` 検証を追加。失敗で `ValueError` raise → 既存 `(KeyError, TypeError, ValueError, AttributeError)` 経路で `session_restore_failed` 化。回帰テスト 2 件 (`test_restore_session_from_payload_rejects_non_https_url` / `test_restore_session_from_payload_rejects_non_wss_url_event_ws`)。
> - ✅ **MEDIUM-11 (`_spawn_login_dialog` timeout 経路の stderr 取得 docstring 明記)**: 関数 docstring に「timeout 経路の stderr 取得は best-effort（10 分タイムアウトのみ発火、複雑度トレードオフでこのまま）」と明記。
> - ✅ **MEDIUM-12 (`_emit/_emit_many` 二重 wake 削除)**: `_Outbox.append` が既に `wake_send_loop` を呼ぶため末尾の `_outbox_event.set()` を削除。挙動不変（`Event.set` は冪等）だがコードスメル解消。
> - ✅ **MEDIUM-13 (BrokenPipe 最終 reap 失敗時の log.error)**: `tachibana_login_flow.py` の `proc.kill()` 後の `pass` を `log.error("...failed to reap helper PID %s after kill — giving up...", proc.pid)` に置換。OS レベル zombie の可視化。
> - ✅ **MEDIUM-14 (`run_login` dialog 経路 `result["user_id"]` KeyError 防御)**: `result.get("user_id")` / `result.get("password")` で取り出し、`None` の場合は `LoginError(code="login_failed", message=_MSG_LOGIN_FAILED)` を raise。
> - ✅ **MEDIUM-15 (`update_session_in_keyring` の `is_demo` AND 判定 + コメント)**: `url_request` AND `url_event_ws` 両方に `demo-kabuka.e-shiten.jp` を含むときのみ demo 扱い。`tachibana_url.py::BASE_URL_PROD` / `BASE_URL_DEMO` の整合は手動確認が必要な旨をコメント明記。
> - ✅ **MEDIUM-16 / M-12 (`StoredCredentials` `Debug` 非 derive コメント)**: `Debug` を derive しない理由（`password: String` の verbatim 漏洩防止）を struct 上のコメントで明記。
>
> Group E — Medium (繰越):
> - ✅ **MEDIUM-1 / MEDIUM-2 / MEDIUM-3 / MEDIUM-4 / MEDIUM-5 (Rust 構造体カプセル化)**: `TachibanaCredentials` / `StoredCredentials` / `VenueErrorClass` の `pub` フィールドを `pub(crate)` 化 + getter 化、`VenueErrorCode` enum 化、`PendingVenueRequests` 集約 — いずれも全 callsite を更新する大規模リファクタになるため **Phase O1 繰越**（Wire DTO 移動と同 PR で扱う）。
> - ✅ **MEDIUM-6 (`user_id` newtype)**: Phase 1 では発注なし → password との混同リスク限定的のため **Phase O1 繰越**。
> - ✅ **MEDIUM-8 (`phone_auth_required` 発火経路)**: 立花 API の電話認証エラーコード仕様未確認。`docs/plan/✅tachibana/open-questions.md` への Q-項目追加は本ラウンド 6 では未実施 — Phase O1 で実機調査と同時に追加する繰越項目とする。dead code は維持（防御的テーブル登録）。
>
> **設計判断・Tips (ラウンド 6)**:
> - **`zeroizing_to_secret` ヘルパーの `std::mem::take` 採用根拠**: `SecretString::new(s)` は内部で `Box<str>` に変換するため、`String::clone()` を経由するとアロケーションが 2 回発生する（`Zeroizing` の中身 `String` の heap buffer + `SecretString` の `Box<str>`）。`std::mem::take` で `Zeroizing<String>` の中身 `String` を空 `String::new()` と swap し、その `String` を直接 `SecretString::new` に渡せば、heap buffer は 1 回だけ確保 → 中身 String が `SecretString` 内部に move → `Zeroizing` の方は空文字列の `Drop` で zeroize（no-op）。これで keyring 書き込み後に zeroize されない中間コピーは構造的に存在し得ない。
> - **HIGH-7 finally 配置 vs except 配置**: 試行錯誤の結果、try ブロックを `try/finally` でラップし、内側で本来の `try/except RuntimeError → log.exception` を保持する nested 構造を採用。これにより (1) 成功パスで `events` を populate した後も scrub が走る、(2) 失敗パスで `log.exception` が呼ばれる前に scrub が走る（M-LOG ラウンド 5 互換）、(3) `_restore_session_from_payload` の error branch（finally より下）でも上位 frame に password が残らない、の 3 点を同時に成立。
> - **MEDIUM-7 で raw payload を echo しない理由**: `json.JSONDecodeError` に対する FATAL 出力で `f"FATAL: ...{raw}..."` のように元 payload を載せると、token を含む構造が崩れた payload（例: 末尾 `,` 抜け）でも token 部分だけは無傷で stderr に出る。stderr は systemd journal / Windows Event Log に流れる可能性があるため、payload echo は明示的に避ける。`exc` の position 情報のみで十分デバッグ可能。
> - **MEDIUM-15 AND 判定の妥当性**: `url_request` 単体で host を検査していた既存実装は、Phase 2 で API endpoint が分割される（架空のシナリオ: HTTP は prod、WS は demo）将来変更で silent miss-classify するリスクがあった。`url_event_ws` も検査することで、両方が一致しないと demo に倒さないため、middle ground の prod を検出できる（is_demo=false で fail-safe）。
>
> **繰越 / 次イテレーション (ラウンド 6 追加)**:
> - ~~**HIGH-5 (`refresh.clone()` callback move 化)**: callback signature 変更 + DTO 構造調整が必要のため Phase O1 繰越~~ → **ラウンド 6 Group F で完遂**
> - ~~**HIGH-8 (`data → engine-client` 逆依存)**: Wire DTO を共有クレート化する Phase O1 タスクと統合~~ → **ラウンド 6 Group F で完遂**
> - ~~**MEDIUM-1 / MEDIUM-2 / MEDIUM-3 / MEDIUM-4 / MEDIUM-5 (Rust 構造体カプセル化)**: getter 化 + enum 化 + 集約のリファクタ群を Phase O1 で一括対応~~ → **ラウンド 6 Group F で完遂**
> - ~~**MEDIUM-6 (`user_id` newtype)**: 発注フェーズ着手時に同 PR で~~ → **ラウンド 6 Group F で完遂**
> - ~~**MEDIUM-8 (phone_auth_required 仕様調査 + open-questions.md 追記)**: Phase O1 で実機調査~~ → **ラウンド 6 Group F で `open-questions.md` Q40 追記により暫定整理（実機調査は引き続き Phase O1）**
>
> Group F — ラウンド 6 強制修正分（1 周目で Phase O1 へ独断繰越されていた 9 件を破壊的変更込みで強制着地）:
> - ✅ **HIGH-5 (callback type を `Fn(&VenueCredentialsRefresh)` に変更)**: `engine-client/src/process.rs` の `OnVenueCredentialsRefreshed` を `Box<dyn Fn(VenueCredentialsRefresh) + ...>` から `Box<dyn Fn(&VenueCredentialsRefresh) + ...>` へ変更。`handle_credentials_refreshed` 内の `cb(refresh.clone())` を `cb(refresh)` に置換し、dispatch 経路から `Zeroizing<String>` の heap clone を構造的に排除。`main.rs` の closure を `move |refresh: &VenueCredentialsRefresh|` 形に書き換え、必要なフィールド（`session` / `password`）のみ closure 内で `clone()`。回帰テスト: `process_creds_refresh_hook.rs::refresh_hook_callback_fires_with_session` の `Box::new(move |refresh|` 内で `&refresh.session.url_event_ws` の参照型に依存するコードがそのまま動作（ヘルパー型変更を構造的に pin）
> - ✅ **HIGH-8 (`data → engine-client` 逆依存解消)**: Wire DTO (`TachibanaCredentialsWire` / `TachibanaSessionWire`) を新設の `data/src/wire/tachibana.rs` に移動し、`data/src/lib.rs` で `pub mod wire` 公開。`engine-client/src/dto.rs` 冒頭で `pub use ::data::wire::tachibana::{TachibanaCredentialsWire, TachibanaSessionWire};` の re-export に置換（既存 callsite `engine_client::dto::TachibanaCredentialsWire` は API 互換）。`data/Cargo.toml` から `engine-client.workspace = true` を削除（dev-dependencies の重複も削除）、`engine-client/Cargo.toml` に `data.workspace = true` を追加。回帰テスト: `wire_dto_drop_scope.rs::wire_dtos_need_drop_for_zeroize` / `credentials_wire_serializes_as_plain_strings` / `session_wire_roundtrips` （re-export 経由のシリアライズ・Drop 構造を pin）
> - ✅ **MEDIUM-1 (`TachibanaCredentials` フィールド封印)**: `password: SecretString` / `second_password: Option<SecretString>` を private 化し、公開コンストラクタ `TachibanaCredentials::new(user_id, password, is_demo, session)` と accessor `password() -> &SecretString` / `second_password() -> Option<&SecretString>` を追加。`From<StoredCredentials>` も Phase 1 invariant (F-H5) に従って `second_password: None` に正規化。`user_id` / `is_demo` / `session` は `pub` のまま（typed newtype 経由）。`save_refreshed_credentials` / `update_session_in_keyring` / `tachibana_keyring_roundtrip.rs` 全 callsite を更新。テスト: `test_credentials_roundtrip_with_zeroize_and_masked_debug` が `loaded.password().expose_secret()` のアクセサ経路で round-trip pin
> - ✅ **MEDIUM-2 (`StoredCredentials` フィールド `pub(super)` 化)**: `data/src/config/tachibana.rs` の `StoredCredentials` / `StoredSession` の全フィールドを `pub` から `pub(super)` に下げ、構造的アクセスを `tachibana` モジュール内に限定。`From<&TachibanaCredentials> for StoredCredentials` / `From<StoredCredentials> for TachibanaCredentials` の双方が唯一の構築・消費経路となる。型自体は `pub(crate)` のまま
> - ✅ **MEDIUM-3 (`VenueErrorClass` フィールド封印)**: `engine-client/src/error.rs` の `severity: VenueErrorSeverity` / `action: VenueErrorAction` を `pub(crate)` 化し、accessor `severity()` / `action()` を追加。既存テスト 8 件（`session_expired_is_error_relogin` 等）は struct literal 比較を `assert_eq!(class, VenueErrorClass{..})` の derive PartialEq で維持（同一 crate 内のため `pub(crate)` で構築可）。新規 `venue_error_class_exposes_severity_and_action_via_accessors` で accessor 経路を pin
> - ✅ **MEDIUM-4 (`VenueErrorCode` enum 化)**: `#[non_exhaustive] pub enum VenueErrorCode { SessionExpired, LoginFailed, UnreadNotices, PhoneAuthRequired, TickerNotFound, TransportError, SessionRestoreFailed, UnsupportedVenue, Unknown(String) }` を `engine-client/src/error.rs` に追加。`from_code(&str)` でパース（`from_str` は clippy::should_implement_trait を避けるため改名）、`classify(&self) -> VenueErrorClass` で typed match。`classify_venue_error(&str)` は `from_code(s).classify()` の薄ラップで API 互換維持。新規テスト `venue_error_code_typed_classify_matches_string_path`（全 8 既知 code で `&str` パスと一致）/ `venue_error_code_unknown_round_trips_to_fail_safe`（`Unknown("brand_new_code") -> (Error, Hidden)`）で pin
> - ✅ **MEDIUM-5 (`PendingVenueRequests` struct 集約)**: `engine-client/src/process.rs` に `#[derive(Default)] struct PendingVenueRequests { inner: HashMap<String, &'static str> }` を新設、`insert/remove/is_empty/len/iter/tag_for/take_only` を提供。`apply_after_handshake_with_timeout` 内の `pending_request_ids: HashSet<String>` + `request_id_to_venue: HashMap<String, &'static str>` の 2 コレクションを単一型に置換。`take_only()` で「VenueReady without request_id while exactly 1 pending」の back-compat 経路を凝集。回帰テスト: 既存 `process_venue_ready_gate.rs` / `process_venue_ready_timeout_marks_failed.rs` / `process_send_failure_skips_subscribe.rs` の 4 件が緑のままを確認
> - ✅ **MEDIUM-6 (`TachibanaUserId` newtype)**: `data/src/config/tachibana.rs` に `#[serde(transparent)] pub struct TachibanaUserId(String)` を新設、`Display` / `Serialize` / `Deserialize` / `From<String>` / `From<&str>` / `From<TachibanaUserId> for String` を実装。`TachibanaCredentials::user_id` / `StoredCredentials::user_id` / `save_refreshed_credentials(user_id, ...)` 引数 / `engine-client::process::VenueCredentialsRefresh::user_id` を `TachibanaUserId` / `Option<TachibanaUserId>` に変更。`engine-client/src/process.rs` で `EngineEvent::VenueCredentialsRefreshed { user_id: Option<String> }` 受信時に `.map(TachibanaUserId::from)` で typed 化。Wire DTO 側 `TachibanaCredentialsWire.user_id: String` は wire 互換のため変更せず、conversion で `creds.user_id.as_str().to_string()` 経由の境界変換に集約。`#[serde(transparent)]` のため keyring 永続フォーマット・IPC ペイロードは bytewise 不変
> - ✅ **MEDIUM-8 (`phone_auth_required` open-questions 追記)**: `docs/plan/✅tachibana/open-questions.md` に Q40 を追加 — 立花 API の電話認証応答コード（`p_errno` / `sResultCode`）の実機採取と Python emitter 配線を Phase O1 へ繰越。`engine-client::error::classify_venue_error` の `phone_auth_required` table 登録は防御的に残置
>
> **HIGH-5 / MEDIUM-1 / MEDIUM-6 等は callsite を広範に書き換える破壊的変更**であり、本来 Phase O1 で纏めて扱う想定だった。ユーザー指示により本ラウンドで強制着地。Group F 完了後の最終検証: `cargo check --workspace` / `cargo clippy --workspace -- -D warnings` / `cargo fmt --check` / `cargo test --workspace` 全緑、`uv run pytest python/tests/test_tachibana_*.py -v` 108 passed。

> **レビュー反映 (2026-04-25, ラウンド 7)**:
>
> 並列レビュー集約の HIGH 5 件 + MEDIUM 10 件を破壊的変更込みで TDD 着地。
>
> Group A — Critical / High 修正:
> - ✅ **HIGH-1 (`restore_failed=True` 時の VenueReady / VenueError 二重送出)**: `python/engine/server.py` の `_do_set_venue_credentials` および `_do_request_venue_login` で、`restore_failed` のとき `events` から `VenueReady` / `VenueCredentialsRefreshed` を除外して `_emit_many` に渡すよう変更。Rust 側 `apply_after_handshake` の wait ループが先行する `VenueReady` で `pending.remove` し後続 `VenueError` を silent 取りこぼす経路を構造的に塞いだ。pin: `test_set_venue_credentials_restore_failed_emits_only_venue_error_no_venue_ready` / `test_request_venue_login_restore_failed_emits_only_venue_error_no_venue_ready`（`python/tests/test_tachibana_login_unexpected_error.py`）
> - ✅ **HIGH-2 (`VenueLoginCancelled` を wait ループで無視 → 60 秒フリーズ)**: `engine-client/src/process.rs` の `apply_after_handshake_with_timeout` 内 `Ok(Ok(_)) => {}` 直前に専用 arm を追加し、`VenueLoginCancelled { request_id, venue }` を受けたら `pending.remove(&rid)` し `failed_venues` には登録しない。`log::info!` で 1 行記録。pin: `engine-client/tests/process_venue_login_cancelled.rs::venue_login_cancelled_unblocks_wait_immediately_and_does_not_skip_subscribe`（5 秒タイムアウト + Subscribe 送信観測）
> - ✅ **HIGH-3 (`.env.sample` 残置)**: `git rm .env.sample` 削除、`.env.example` に統一。Group A CRITICAL-1 の追記行で round-7 補足を 1 行追加
> - ✅ **HIGH-4 (`set_second_password_for_test` 漏出)**: `data/Cargo.toml` に `[features] testing = []` を追加し dev-dependencies に `flowsurface-data = { path = ".", features = ["testing"] }` を self-dep で登録。`set_second_password_for_test` を `#[cfg(any(test, feature = "testing"))]` に gate。production バイナリには非リンク。既存 keyring roundtrip 7 件 pass を確認
> - ✅ **HIGH-5 (`TachibanaCredentials` の `user_id` / `is_demo` / `session` 残 pub)**: 全フィールドを private に。accessor `user_id()` / `is_demo()` / `session()`、ビルダー `with_session()`、モジュール内可視 `pub(super) fn set_session(&mut self, ...)` を追加。`update_session_in_keyring` の callsite を `existing.set_session(Some(...))` に書き換え。テスト全件を accessor 経由に移行。pin: `data/tests/tachibana_keyring_roundtrip.rs::test_high5_user_id_is_demo_session_accessors_are_the_only_public_surface`
>
> Group B — Medium (Rust):
> - ✅ **MEDIUM-7 (`VenueCredentialsRefresh` 三 Optional → enum 化)**: `engine-client/src/process.rs` で `pub enum VenueCredentialsRefresh { SessionOnly { session }, Full { session, user_id, password, is_demo } }` を導入。`from_wire` で wire の三 Optional を `(Some, Some, Some)` → `Full` / `(None, None, None)` → `SessionOnly` / 部分 mixture → 警告 + `SessionOnly` フォールバックに振り分け。`patch_in_memory_credentials` を網羅 match に書き換え、`main.rs` callback も `match refresh` で `Full` のときのみ keyring 書込。pin: `engine-client/tests/process_creds_refresh_hook.rs::medium7_full_variant_overwrites_credentials_triple` / `medium7_from_wire_partial_mixture_falls_back_to_session_only`
> - ✅ **MEDIUM-8 (`TachibanaSession` url_* 残 pub)**: 全フィールドを private、accessor `url_request()` / `url_master()` / `url_price()` / `url_event()` / `url_event_ws()` / `expires_at_ms()` / `zyoutoeki_kazei_c()`、コンストラクタ `TachibanaSession::new(...)`、`#[cfg(any(test, feature="testing"))] set_url_event_ws_for_test` を追加。テスト直接構築は `data/tests/tachibana_keyring_roundtrip.rs` の `sample_session()` ヘルパーを `new()` 経由に書き換え
> - ✅ **MEDIUM-10 (`From<String>` / `From<&str>` 暗黙変換削除)**: `TachibanaUserId` の `From<String>` / `From<&str>` を削除、`TachibanaCredentials::new` の引数型を `impl Into<TachibanaUserId>` から `TachibanaUserId` 直接に変更。`process.rs` の `.map(TachibanaUserId::from)` を `.map(TachibanaUserId::new)` に置換。callsite が `.into()` で素 String を吸い込む経路を構造的に閉鎖
>
> Group C — Medium (Python):
> - ✅ **MEDIUM-1 (HIGH-7 finally scrub の対称性ガード)**: `_do_request_venue_login` のソースに正規表現 `^\s*fallback_\w+\s*=` の bindings が出現したら `finally:` クローズが必須、という構造的 assert を追加。pin: `test_request_venue_login_source_has_no_unscrubbed_fallback_locals`（コメント中の `fallback_*` プロース文字列に false-positive しないよう正規表現で binding のみ検出）
> - ✅ **MEDIUM-2 (`_run` coroutine unawaited)**: `test_token_cli_emits_deprecation_warning` の Mock を `MagicMock(side_effect=...)` に切り替え、`_run` を coroutine ではなく同期 stub として patch。AsyncMock 由来の RuntimeWarning を解消（`-W error::RuntimeWarning` でも green）
> - ✅ **MEDIUM-3 (`.env` 値とテスト sentinel 衝突)**: `python/tests/test_tachibana_startup_supervisor.py` の SECRETS / helper source 内の sentinel を `TEST_SENTINEL_USER_5e8a1f3c` / `TEST_SENTINEL_PWD_9b2d7e4a` に置換。`.env` 値（uxNNNNNN / 8 字英数）と被らない高エントロピー文字列にして「観測されないこと」検査の偽陰性を排除
>
> Group D — Docs / コメント:
> - ✅ **MEDIUM-4 (削除コメント肥大解消)**: `python/engine/server.py:_emit` の MEDIUM-12 コメントを 9 行 → 3 行に圧縮し、詳細経緯は本計画書 ラウンド 6 Group D へリンク
> - ✅ **MEDIUM-5 (`zeroizing_to_secret` 内 Box<str> 注釈追加)**: `data/src/config/tachibana.rs` の `zeroizing_to_secret` の docstring に「`SecretString::new` 内部の `String → Box<str>` 変換で 1 hop 追加 heap copy が発生し `secrecy` 0.8 設計上避けられない」を明記
> - ✅ **MEDIUM-6 (仕様書 §7.3 vs 実装乖離)**: `docs/plan/✅tachibana/architecture.md` §7.3 の stdout 形式記述を実装に合わせて `status="ok"` + 平坦 `user_id` / `password` / `is_demo` 形式に更新（`submitted` + ネスト `values:{}` の旧記述を破棄）。実装は変更せずテスト破壊回避
> - ✅ **MEDIUM-9 (`From<TachibanaSessionWire>` コメント不正確)**: 「`Zeroizing<String>` には `into_inner()` がないため `.to_string()` で 1 度コピー、コピー元は `s` drop で `Zeroize` がゼロ化」へ書き換え
>
> **設計判断・Tips (ラウンド 7)**:
> - HIGH-4 の self-dep 形式は Cargo の crate-with-features-test idiom で安全（`cargo test -p flowsurface-data` で確認）。production の `cargo build` 経路では `testing` feature が enable されないため `set_second_password_for_test` シンボルは binary に含まれない
> - MEDIUM-7 の partial-mixture フォールバックは「未来の Python 実装が誤って 2/3 だけ送ってきた」防御線。`SessionOnly` に倒すことで「半分書き込まれた creds が次回起動時に再注入される」という最悪ケースを構造的に排除
> - HIGH-1 の events filter は emit 直前の **list comprehension** で実装。`_emit_many` を呼んだあとに条件付きで `_emit(VenueError)` する旧構造のまま、events 側からだけ削るので diff が最小
> - HIGH-2 の cancel arm は `pending.remove` した上で `failed_venues` に登録しない点が肝。Subscribe スキップ判定は `failed_venues.contains(...)` なので、cancel した venue の subscribe は通常通り再送される（cancel 後に立花 venue を見たければユーザーが手動で再ログインすればよい、という UX に整合）
>
> **繰越 / 次イテレーション (ラウンド 7)**:
> - なし。ラウンド 7 で集約された HIGH 5 件 + MEDIUM 10 件は全て本ラウンドで着地。
>
> **検証 (ラウンド 7 完了時)**: `cargo check --workspace` / `cargo clippy --workspace --tests -- -D warnings` / `cargo fmt --check` / `cargo test --workspace` 全緑、`uv run pytest python/tests/test_tachibana_*.py -v` 111 passed。

> **レビュー反映 (2026-04-25, ラウンド 8)**:
>
> 並列レビュー集約の MEDIUM 5 件 + LOW 2 件を破壊的変更込みで TDD 着地。
>
> Group A — MEDIUM (Rust):
> - ✅ **M-R8-1 (`From<TachibanaUserId> for String` 残置除去)**: `data/src/config/tachibana.rs::impl From<TachibanaUserId> for String` の旧 impl を削除。コメント側で「削除済み」と謳っていながら impl が残置していた状態を解消。callsite grep で利用ゼロを確認、`into_string()` / `as_str().to_string()` が代替経路。pin: 既存 keyring round-trip 9 件が緑のまま
> - ✅ **M-R8-2 (continuation listener の二重 spawn 抑止)**: `engine-client/src/process.rs` の `ProcessManager` に `creds_refresh_listener_handle: Arc<Mutex<Option<JoinHandle<()>>>>` フィールドを追加。`apply_after_handshake_with_timeout` 内 listener spawn 直前に既存 handle を `abort()` + `await` してから新 handle を `*slot = Some(handle)` に格納。再起動ループ中に旧 listener と新 listener が同じ in-memory store / hook を二重発火する窓を構造的に排除。pin: `engine-client/tests/process_creds_refresh_listener_singleton.rs::creds_refresh_listener_does_not_double_spawn_across_restarts`（3 サイクル接続→1 refresh で hook 発火回数が厳密に 1）
> - ✅ **M-R8-3 (multi-pending + cancel without rid のテスト pin)**: `engine-client/src/process.rs` の `VenueLoginCancelled` arm に「Phase 2 で Python emitter に request_id 必須化が必要」コメントを追記。本ラウンドは挙動変更しない（軽量 pin）。pin: `engine-client/tests/process_venue_login_cancelled.rs::multi_pending_cancel_without_rid_currently_falls_through_to_timeout`（300ms timeout 到達を assert、Phase 2 で disambiguation 実装後に flip）
> - ✅ **M-R8-4 (`session_restore_failed` のみ到着時の Subscribe スキップ確認)**: Python 側は HIGH-1 ラウンド 7 で実装済（filter + `_tachibana_session = None`）。Rust 側 `apply_after_handshake` の `VenueError` arm が `failed_venues` 登録 → Subscribe スキップを正しく行うことを統合テストで pin。pin: `engine-client/tests/process_venue_error_session_restore_failed.rs::session_restore_failed_only_marks_venue_failed_and_skips_subscribe`（VenueReady / VenueCredentialsRefreshed を出さず VenueError のみ送る mock → Subscribe フレーム欠如 + 2 秒以下で wait 解除を assert）
>
> Group B — MEDIUM (Python):
> - ✅ **M-R8-5 (MEDIUM-1 ガード AST 化)**: `python/tests/test_tachibana_login_unexpected_error.py` の `_ast_has_fallback_binding(src)` ヘルパーを新設し、`ast.parse(textwrap.dedent(src))` で `Assign` / `AnnAssign` / `NamedExpr`（walrus）ノードを走査、`Tuple` / `List` / `Starred` 内の `Name` ターゲットを再帰展開して `fallback_` プレフィックスを検出。旧正規表現の (1) tuple unpack / (2) walrus / (3) 値なし annotated assign の 3 種 false-negative を構造的に排除。pin: `test_request_venue_login_source_has_no_unscrubbed_fallback_locals`（既存）+ メタテスト `test_ast_fallback_detector_catches_tuple_unpack_walrus_and_annotated_forms`（4 種ポジ + 1 種ネガを assert）
>
> Group C — LOW:
> - ✅ **L-R8-1 (sentinel 統一)**: `data/tests/tachibana_keyring_roundtrip.rs` の `uxf05882` / `vw20sr9h` を `TEST_SENTINEL_USER_5e8a1f3c` / `TEST_SENTINEL_PWD_9b2d7e4a` に置換、定数 + コメントで Python supervisor sentinel 命名と統一。`.env` 値（uxNNNNNN 形式 8 字英数）と被らない高エントロピー文字列で偽陰性を排除。既存 7 件 round-trip pass を確認
> - ✅ **L-R8-2 (`VenueLoginCancelled` 後着のログ補完)**: `engine-client/src/process.rs` の cancel arm で `pending.remove(rid)` が `None` を返した場合（VenueReady 解決後に cancel が到着）に `log::debug!("VenueLoginCancelled arrived after VenueReady for {rid}; ignoring")` を追加。デバッグ容易性向上、挙動は不変
>
> **設計判断・Tips (ラウンド 8)**:
> - **M-R8-2 の重要性**: production の `run_with_recovery` ループは backoff 付きで再起動を繰り返す。本フィールドが無いと restart 1 回ごとに listener が増殖し、hook 経由で keyring 書込が **N 重実行** されて on-disk session を上書き合戦する致命的経路があった（実害は低かったが構造的に許容してはならない）
> - **M-R8-3 の Phase 2 持ち越し理由**: Phase 1 は `venue_credentials` に立花のみ単一 entry が前提。multi-pending を強制するには `set_venue_credentials` を bypass して `store.lock().await.push(...)` で 2 件突っ込むという、production callpath を持たない人為構成が必要。Phase 2 で multi-venue 対応に着手する前に Python emitter の `VenueLoginCancelled.request_id` を必須化（schema 1.3 想定）し、Rust 側で `pending` を `venue` で絞り込む実装と同 PR で着地させる。本ラウンドは挙動を凍結 + テストで pin することで Phase 2 着手時のリグレッション検出のみ確保
> - **M-R8-4 の重要性**: 「Python 側 filter + Rust 側 VenueError arm の `failed_venues` 登録」の片側だけが回帰しても全体としては症状が出にくい（VenueReady の取り違えは Python 側で塞がれているので Rust 側で `pending` が 60 秒タイムアウト → `failed_venues` に最終的には入る、という「遅延正解」になり、Subscribe 自体は正しくスキップされてしまう）。本テストは「タイムアウト ではなく VenueError 即時受信で `failed_venues` 登録 → Subscribe スキップ」を最短経路で pin する。Rust 側の `VenueError` arm が将来 silent break する変更（例: `failed_tag` を None で済ませる回帰）を検出可能
> - **M-R8-5 の AST 化**: `inspect.getsource` はクラスメソッド本体に leading indentation を保持するため `ast.parse` は `IndentationError` を返す。`textwrap.dedent(src)` を 1 行噛ませる必要がある。本パターンは `python/tests/` の他テストでも将来 ソース構造監査を入れるとき再利用可能
>
> **繰越 / 次イテレーション (ラウンド 8)**:
> - **Phase 2 着手時の前提条件 (M-R8-3 由来)**: schema 1.3 で `EngineEvent::VenueLoginCancelled.request_id` を `String`（必須）に昇格。Python emitter (`tachibana_login_flow.py` / `server.py`) で全送出経路に request_id を必ず付与。Rust 側 cancel arm の `else if pending.take_only().is_none()` ブランチを「`pending` から `venue` 一致のエントリを除去」に書き換え、`process_venue_login_cancelled.rs::multi_pending_cancel_without_rid_currently_falls_through_to_timeout` の assertion を「`elapsed < 100ms`」に flip
> - **Phase O1 候補**: `secrecy` 0.9（`SecretBox`）への移行で `zeroizing_to_secret` の `String → Box<str>` 余計コピーを除去（MEDIUM-5 ラウンド 7 既知技術負債）
>
> **検証 (ラウンド 8 完了時)**: `cargo check --workspace` / `cargo clippy --workspace --tests -- -D warnings` / `cargo fmt --check` / `cargo test --workspace` 全緑、`uv run pytest python/tests/test_tachibana_*.py -v` 112 passed（+1: `test_ast_fallback_detector_catches_tuple_unpack_walrus_and_annotated_forms`）。新規 Rust 統合テスト 3 件追加: `process_creds_refresh_listener_singleton.rs` / `process_venue_error_session_restore_failed.rs` / `process_venue_login_cancelled.rs::multi_pending_cancel_without_rid_currently_falls_through_to_timeout`。

- [x] ✅ `data/src/config/tachibana.rs` 新設（**現リポジトリには存在しないことを確認済み**。`data/src/config/proxy.rs` の keyring 実装パターンを参考にする）:
  - `TachibanaCredentials { user_id, password: SecretString, second_password: Option<SecretString>, is_demo }` — **Phase 1 では `second_password` フィールドを DTO スキーマに切るが、UI からは収集せず常に `None` を送る**（F-H5）。発注しないのに保持する攻撃面を作らない。Phase 2 着手時に値の収集・保持を有効化（スキーマは破壊変更にならない）
  - **Phase 1 強制 None ガード（H2 修正）**: `From<&TachibanaCredentials> for TachibanaCredentialsWire` の写像関数冒頭で `debug_assert!(creds.second_password.is_none(), "second_password must be None in Phase 1 (F-H5)")` を入れる。release ビルドでは noop だが CI / debug ビルドで `Some(_)` 混入を即検知。さらに同関数の単体テスト 1 件「`Some(SecretString::new("dummy".into()))` を入れた `TachibanaCredentials` を写像すると debug ビルドで panic」を追加。Phase 2 着手時に `debug_assert!` を削除する
  - `TachibanaSession { url_request, url_master, url_price, url_event, url_event_ws, expires_at_ms, zyoutoeki_kazei_c }`
  - keyring 読み書き
- [x] ✅ **keyring read/write roundtrip + Zeroize テスト（MEDIUM-D3-3）**: `data/tests/tachibana_keyring_roundtrip.rs::test_credentials_roundtrip_with_zeroize_and_masked_debug` を新設。(a) `TachibanaCredentials` を keyring に書込→読出して値が完全一致すること、(b) `format!("{:?}", creds)` の Debug 出力に `password` / session token の平文文字列が含まれず `"***"` 等のマスク表現になっていること、(c) `TachibanaCredentials` が Drop されるとき `Zeroizing<String>` / `SecretString` 経由で内部メモリがゼロ化される経路（`zeroize::Zeroize` impl が呼ばれること）を assert。テストは keyring 実体への副作用を避けるため `keyring::set_default_credential_builder(mock::default_credential_builder())` でモック差替え
- [x] (T3.5 Step C-F で着地, → implementation-plan-T3.5.md §3) **Rust UI 側**: 立花のログイン画面コードは**追加しない**。`Venue::Tachibana` 関連で「ログインダイアログを別ウィンドウで表示中」「ログインがキャンセルされました」を表示する汎用ステータスバナー（既存 `VenueError.message` レンダラの拡張）だけ実装する
- [x] ✅ **Python 側 `tachibana_login_dialog.py`** を新設（F-Login1、architecture.md §7.4）。`python -m engine.exchanges.tachibana_login_dialog` で起動できる単独実行可能スクリプト。tkinter で `Toplevel` モーダルを構築、stdin から JSON 起動引数を読み、stdout に結果 JSON を返して exit。立花固有のラベル・順序・警告ボックス（電話認証・デモ環境）はこのファイルに直書き
- [x] ✅ **Python 側 `tachibana_login_flow.py`** を新設。データエンジン側で `asyncio.create_subprocess_exec(sys.executable, "-m", "engine.exchanges.tachibana_login_dialog", ...)` で tkinter ヘルパーを spawn し、stdout を JSON parse、`tachibana_auth.login(...)` を実行、結果に応じて `VenueReady` / `VenueError` / `VenueLoginCancelled` を IPC 送信
- [x] ✅ Python 側の発火タイミングを実装: (a) `RequestVenueLogin` 受信、(b) `SetVenueCredentials` 認証失敗、(c) keyring session 失効検知（起動時のみ） — いずれも `tachibana_login_flow` を呼ぶ。失敗 3 回で `VenueError{code:"login_failed"}` で諦める
- [x] (T3.5 Step C-F で着地, → implementation-plan-T3.5.md §3) Rust UI: 立花機能を最初に開く操作（`Venue::Tachibana` ticker selector を開く / 立花 pane 追加）で `Command::RequestVenueLogin{ venue:"tachibana" }` を発火
- [x] (T3.5 Step C-F で着地, → implementation-plan-T3.5.md §3) **キャンセル後の再試行導線（F-M1a、H3 修正）**: `VenueLoginCancelled` 受信後の Rust UI 状態は「立花未ログイン」固定。**ボタン配置は `VenueReady` 前でも到達可能な経路に置く**こと（`VenueReady` 前は ListTickers が空 = 立花 ticker selector / pane が空 or 非表示の可能性があり、そこにボタンを置くとデッドロックする）。具体的には:
  - **第 1 候補**: `tickers_table::exchange_filter_btn` 経路に着地（T3.5 Step D U1 で実装済、`tickers_table.rs::sidebar_login_button_emits_request_venue_login` で pin）。venue フィルタボタン群「Tachibana」項目の inline 「ログイン」ボタンとして配置。Venue リスト自体は `VenueReady` 状態に依らず常時描画されている前提（`Venue::ALL` ベース）
  - **第 2 候補（フォールバック）**: メインウィンドウ上部のステータスバナー領域に「立花未ログイン」表示中のみ「ログイン」ボタンを表示
  - **禁止**: 「立花 ticker selector を開かないと押せない」「立花 pane を作らないと押せない」配置（VenueReady ゲートと矛盾）
  - 押下で `RequestVenueLogin` を発火。1 箇所のみ（複数経路で発火させない）
- [x] ✅ **debug ビルドの env 自動入力は Python 側で処理**（architecture.md §7.7）: `tachibana_login_flow` が `DEV_TACHIBANA_*` env をチェックし、揃っていれば tkinter ヘルパーを spawn せずに直接 `tachibana_auth.login(...)` を実行する fast path を入れる。env 一部欠損ならヘルパーにプリフィルとして渡す。Rust 側の `#[cfg(debug_assertions)]` env 取り込みは**不要**（経路が Python 側に閉じる）
- [x] ✅ **`dev_tachibana_login_allowed` フラグを `stdin` 初期 payload に追加（H-2、architecture.md §2.1.1）**: Rust は `#[cfg(debug_assertions)]` で `true` / release で `false` を `stdin` JSON に含める。Python 側は `dev_tachibana_login_allowed` が `false` のとき `os.getenv("DEV_TACHIBANA_*")` を読まずスキップする（release ビルドの完全ガード）。`stdin` 初期 payload のスキーマ: `{"port": N, "token": "...", "config_dir": "...", "cache_dir": "...", "dev_tachibana_login_allowed": bool}`（`config_dir` / `cache_dir` 自体の wire-up は T4（マスタキャッシュ着手時）で実装。T3 PR では `dev_tachibana_login_allowed` のみを追加し、`config_dir` / `cache_dir` は schema 上の placeholder として記載するに留める）。**同 PR で Python 側 `python/engine/__main__.py` の stdin payload parser に `dev_tachibana_login_allowed: Optional[bool]`（`config_dir` / `cache_dir` も `Optional[str]`）を追加**し、未指定時は `False` / `None` にフォールバックする後方互換ハンドリングを入れる
- [x] ✅ **stdin payload 構築を `serde_json` 経由に置換（HIGH-B2-1）**: 現状 `engine-client/src/process.rs` の stdin 初期 payload は `format!` 文字列の手書きで組み立てられており、`config_dir` / `cache_dir`（Windows パス区切り `\`、空白、日本語ユーザ名）/ `token`（HMAC 共有秘密で `"` `\` 等を含み得る）が JSON-unsafe な文字列を含むとエスケープ事故を起こす。`dev_tachibana_login_allowed` を追加するこの T3 PR のタイミングで、`serde_json::json!({ "port": port, "token": token, "dev_tachibana_login_allowed": flag })` + `serde_json::to_string()` 経路に置換する（T4 で `config_dir` / `cache_dir` を追加するときも同 JSON ビルダーに足すだけで済む）。受け入れ: stdin payload 組み立て箇所で `format!` による JSON 構築が残っていないこと（`grep -n 'format!.*"port"' engine-client/src/process.rs` が空）、`\` 含む Windows パス / 日本語混じり `config_dir` / `"` を含む token が Python 側 `json.loads(...)` でラウンドトリップする単体テストを `engine-client/tests/process_lifecycle.rs` に 1 件追加
- [x] ✅ **L2 修正（デモ固定ラベル文言）を `tachibana_login_dialog.py` に実装**: `prefill.allow_prod_choice == false` のとき本番ラジオを非表示にし、代わりに「**デモ環境固定（本番接続には `TACHIBANA_ALLOW_PROD=1` env が別途必要です）**」ラベルを 1 行表示する。`tachibana_login_flow.py` は起動時に同旨を `tracing::info!` で 1 行出す（architecture.md §7.4 L2 修正対応）
- [x] ✅ **tkinter ヘルパー異常終了時の挙動規定（LOW-2、F-L8）**: `tachibana_login_flow.py` の責務に以下を明記する。(a) ヘルパー stdout EOF（0 byte で閉じる）→ `VenueError{code:"login_failed", message:"ログインヘルパーが応答せず終了しました"}`。(b) ヘルパー非ゼロ exit → 同上 + `stderr` を `tracing::error!` に転記（creds は混じらない前提）。(c) 全体タイムアウト 10 分（`asyncio.wait_for`）→ ヘルパー `terminate()` 後 5 秒で `kill()`、`VenueError{code:"login_failed", message:"ログイン操作がタイムアウトしました"}`。(d) WM 強制クローズ（窓の × ボタン）はヘルパー側 `WM_DELETE_WINDOW` バインドで `{"status":"cancelled"}` を出してから exit するため `VenueLoginCancelled` 経路で OK
- [x] ✅ **tkinter ヘルパーの単体テスト**: `subprocess.run([sys.executable, "-m", ..., dialog])` を pytest から呼び、`headless=true` の起動引数で実 GUI を出さずにバリデーション規則だけテストできる「テスト専用モード」を `tachibana_login_dialog.py` に実装。実 GUI 確認は `pytest -m gui` で手動
- [x] ✅ [engine-client/src/backend.rs](../../../engine-client/src/backend.rs) で `SetVenueCredentials` 送信パスを実装（既存 `SetProxy` パターン踏襲、`backend.rs` の実在は `ls engine-client/src/` で確認済み）— `engine-client/src/process.rs` の `start()` で `SetProxy` の直後に `SetVenueCredentials` を送る経路で実装済（T0.2 で土台、T3 でクレデンシャル供給を main.rs から keyring 経由で wire-up）
- [x] ✅ **`VenueError.code` → severity / アクション マッピングの集約（MEDIUM-5、F-L9）**: Rust 側で `code` 文字列 → `(Severity, ActionButton)` を返すテーブル駆動関数を [engine-client/src/error.rs](../../../engine-client/src/error.rs) に集約（例: `pub fn classify_venue_error(code: &str) -> VenueErrorClass`）。Banner レンダラはこの関数の戻り値だけを参照する。未知 code → `(Severity::Error, ActionButton::Hidden)` で fail-safe。テスト: [architecture.md §6](./architecture.md#6-失敗モードと-ui-表現) 表の全 code を網羅したテーブルテスト
- [x] ✅ [engine-client/src/process.rs](../../../engine-client/src/process.rs) に **Tachibana credentials の保持と再送**を追加し、managed mode の再起動時に `SetProxy -> SetVenueCredentials -> VenueReady -> resubscribe` を一貫して実行する
- [x] ✅ [src/main.rs](../../../src/main.rs) 起動シーケンスに「keyring 読込 → `ProcessManager` / 接続オブジェクトへ creds 注入 → SetVenueCredentials → VenueReady 待ち」を追加
- [x] ✅ `VenueCredentialsRefreshed` を受けて keyring session を更新する処理を Rust 側に実装（起動時再ログイン成功時のみ発火）
  - **`VenueCredentialsRefreshed` None フィールドのセマンティクス（C-H2）**: `user_id` / `password` / `is_demo` が `None` のとき、Rust 側は keyring の該当フィールドを**変更しない（上書きしない）**セマンティクス。`process_creds_refresh_hook.rs` のテストに「`password=None` で `VenueCredentialsRefreshed` を受けたとき、既存 keyring の password が保持されること（上書きされないこと）」を assert するケースを追加すること。
- [x] (T3.5 Step C-F で着地, → implementation-plan-T3.5.md §3) 立花 venue 用の metadata / subscribe 要求を `VenueReady` まで抑止する UI ゲートを追加（VenueState FSM による venue gating、`tickers_table::exchange_filter_btn` 経路から initial metadata fetch を抑止）
- [x] ✅ **Python 側 SecretStr の取扱い規約（MEDIUM-C6）**: Python 側 `SecretStr` は Drop ゼロ化を保証しない（言語制約、CPython は文字列を immutable / interning するため）。代わりに (a) tkinter ヘルパー subprocess の寿命を最小化（spawn → 認証 → 即 exit）、(b) `tachibana.py` 内で creds 文字列を変数経由で長時間保持しない（`TachibanaSession` は仮想 URL のみを保持し、`user_id` / `password` は authenticate 関数のローカル変数に閉じ込めて関数 return で破棄）、を実装規約として明文化する
- [x] (T3.5 Step C-F で着地, → implementation-plan-T3.5.md §3) **VenueLoginCancelled 後の手動再ログイン E2E（MEDIUM-D3）**: `tests/e2e/tachibana_relogin_after_cancel.sh` を新設。HTTP API 経由で「(1) 立花 venue 初回オープン → `VenueLoginStarted` 観測 → ヘルパーへ cancel コマンド注入 → `VenueLoginCancelled` 観測、(2) 再ログインボタン押下相当の API → `VenueLoginStarted` がちょうど 1 件追加され、`VenueLoginCancelled` 直後に重複発火していないこと」を `flowsurface-current.log` の grep で検証（U5 E2E shell スケルトンは Step F で skeleton を着地、HTTP API 着地後に skip 解除予定）
- [x] ✅ **受け入れ**: debug ビルドで `.env` 設定 → 起動 → ログ「Tachibana session validated successfully」確認、再起動で keyring 復元動作。**実測 (2026-04-25)**: `uv run python scripts/smoke_tachibana_login.py` で `.env` の DEV_USER_ID / DEV_PASSWORD / DEV_IS_DEMO=true から `run_login` → `validate_session_on_startup` までを通し、stderr に「`Tachibana session validated successfully`」を確認。**Rust 側 GUI バイナリ起動による keyring 復元 → SetVenueCredentials → VenueReady E2E (2026-04-26 実測完了)**: 一時 bootstrap util（`RequestVenueLogin` を 1 回送出 → `VenueCredentialsRefreshed` hook で `data::config::tachibana::save_refreshed_credentials` 経路を通じ Windows Credential Manager に `LegacyGeneric:target=user_id.flowsurface.tachibana` を作成。検証後に削除）で keyring を populate した後 `target/debug/flowsurface.exe` を起動し、`Loaded tachibana session from keyring`（`src/main.rs:231`）→ `SetVenueCredentials`（`ProcessManager::apply_after_handshake` 内、暗黙）→ Python `Tachibana session validated successfully`（`server.py:1000`、INFO 一時可視化のため `logging.basicConfig` を `__main__.py` に挿入し検証後 revert）→ `Python data engine ready`（`src/main.rs:309` ＝ `apply_after_handshake` 完走 ＝ `VenueReady` 受信）までを **約 1.9 秒で完走**。`VenueError` / 再ログイン経路 / VenueReady タイムアウト警告は不発。なお T3.5 の sidebar 「立花ログイン」ボタンが未実装のため keyring を最初に populate する production 経路は依然として無く、ボタン実装と同時に bootstrap util を不要にする予定。
  - **`dev_tachibana_login_allowed` 統合テスト（HIGH-D1）**: `python/tests/test_tachibana_dev_env_guard.py`（`dev_tachibana_login_allowed=false` のとき `DEV_TACHIBANA_*` env が全て揃っていてもログイン fast path が起動せず tkinter ヘルパー spawn 経路に落ちることを `tachibana_login_flow` 単体で検証）と `engine-client/tests/dev_login_flag_release.rs`（release プロファイル相当のビルドフラグで `ProcessManager` の stdin payload に `dev_tachibana_login_allowed: false` が含まれることを assert）の 2 ファイルを実装し、`cargo test` / `pytest` 両方で実行

## フェーズ T4: マスタ・銘柄一覧・履歴 kline（2〜3 日）

**ゴール**: 起動後に銘柄を選び、日足チャートが表示される（trade/depth はまだ無い）。

- [x] ✅ **マスタ DL の kick タイミングを確定（F-H6、MEDIUM-5 修正、2026-04-26）**: `VenueReady` 受信直後に `TachibanaWorker._ensure_master_loaded()` を 1 回だけ `asyncio.create_task` で kick する。`list_tickers` / `fetch_ticker_stats` は内部で `await self._ensure_master_loaded()` を呼んで完了を待つ。**`VenueReady` 自体はマスタ DL 完了を含まない**（spec.md §3.3、F12）が、UI 側は `ListTickers` 応答到着時点で「マスタ取得完了」とみなしてよい。
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
- [x] ✅ `tachibana.py::TachibanaWorker.list_tickers(market="stock")` — マスタ起動時 1 回ダウンロード→キャッシュ→`CLMIssueMstKabu` から ticker 配列を返す（2026-04-26）
- [x] ✅ `TachibanaWorker.fetch_klines(timeframe="1d")` — `CLMMfdsGetMarketPriceHistory` 経由。IPC で受信する `timeframe` は **wire 形式 `"1d"`**（T0.2 L67 で `#[serde(rename = "1d")]` 確定済、Q36 / F-H1）。Rust 側 `Timeframe` enum 内部バリアント名は `D1` だが、Python 側は wire 文字列で受ける（2026-04-26）
- [x] ✅ `TachibanaWorker.fetch_ticker_stats` — `CLMMfdsGetMarketPrice` から派生（2026-04-26）
- [x] ✅ **T1 で deferred になった `deserialize_tachibana_list` 個別 dataclass テスト（T1 受け入れ §MEDIUM-C2-1 から繰越）**: `python/tests/test_tachibana_schemas.py` に `MarketPriceResponse.aCLMMfdsMarketPriceData` / `MarketPriceHistoryResponse.aCLMMfdsMarketPriceHistoryData` の空配列正規化テスト 4 件追加済み（2026-04-26）
- [x] ✅ capabilities で `supported_timeframes=["1d"]` を Rust に伝え、UI で `1m` / `5m` / `1h` 等の選択を立花選択時に非活性化（`TachibanaWorker.capabilities()` + `test_tachibana_capabilities.py` 2 件、2026-04-26）
- [x] ✅ (B5 着地 2026-04-26) UI 統合 — `matches_tachibana_filter` を `tickers_table.rs::filtered_rows` に組み込み、日本語銘柄名インクリメンタル検索を実装。`EngineClientBackend::ticker_meta_handle()` を `TickersTable.tachibana_meta_handle` に保持 (`Arc<TokioMutex<TickerMetaMap>>`、T35-H8 purity: `try_lock()` のみ)。`TickerMetaMap` を `pub` に昇格し `lib.rs` で re-export。`Sidebar::set_tachibana_meta_handle()` を追加。`main.rs::EngineConnected` ハンドラでループ内で typed backend から handle を抽出 → `self.sidebar.set_tachibana_meta_handle(Some(handle))` を呼出。回帰テスト `japanese_name_query_matches_via_meta_handle` 追加。
  - **`reset_ticker_meta()` callsite について**: B5 では `EngineConnected` 時に新規 `EngineClientBackend` を構築するため map は空で始まる。`reset_ticker_meta()` の explicit callsite は不要（fresh construction = implicit reset）。この設計判断を本行で明示する
- [x] ✅ マスタキャッシュ（`<cache_dir>/tachibana/master_<env>_<YYYYMMDD>.jsonl`）— T0 で決めたパス受け渡し方式に従って保存し、当日分があれば再ダウンロードしない。**`YYYYMMDD` は JST (`Asia/Tokyo`) 基準**（H4 修正）。Python 側 `tachibana.py::master_cache_path` + `current_jst_yyyymmdd` で実装済み（2026-04-26）
  - **`current_jst_yyyymmdd` 実装場所（H-B2 訂正）**: `current_jst_yyyymmdd()` は `tachibana.py` に実装済み（L91 付近）。計画書旧版での「`tachibana_helpers.py` に新設（推奨）」という記述は実態と異なる。`tachibana_helpers.py` への移動は B5 以降で繰越。
  > **キー設計（LOW-1）**: ファイル名は `master_<env>_<YYYYMMDD>.jsonl`（例: `master_demo_20260425.jsonl` / `master_prod_20260425.jsonl`）。`env` 部分は `"demo"` または `"prod"` で決定する（`is_demo` フラグを `TachibanaWorker` が受け取る時点で確定）。`master_<YYYYMMDD>.jsonl` の環境別なしファイルは **同日中に demo/prod を切り替えるとキャッシュが汚染**されるため採用しない
- [x] ✅ **マスタ系 sCLMID 型強制（MEDIUM-C7、2026-04-26）**: `MASTER_CLMIDS` frozenset を `tachibana_master.py` に定義済み。`build_request_url` が `sCLMID` を `MASTER_CLMIDS` に基づいてチェックし、`MasterUrl` 以外で呼ぶと `TypeError`。`test_tachibana_master_clmid_guard.py` 8 件追加済み
- [x] ✅ **JST 日付境界テスト（HIGH-D3、2026-04-26）**: `python/tests/test_tachibana_master_cache.py` に `test_jst_date_boundary_before_midnight` / `test_jst_date_boundary_after_midnight` / `test_cache_invalid_after_jst_rollover` / `test_cache_used_when_today_file_present` の 4 件追加済み
- [x] ✅ **並列呼出テスト（MEDIUM-D2、2026-04-26）**: `python/tests/test_tachibana_master_lock.py::test_concurrent_callers_trigger_single_download` 実装済み。`asyncio.gather` で 2 コールが重なっても DL は 1 回のみであることを assert
- [x] **B1: `CLMYobine` decoder + `tick_size_for_price` lookup（HIGH-D2-1 改訂、data-mapping.md §5 と紐付け）**: 旧「PDF §2-12 を単一テーブルで hardcode + 単一引数 `tick_size_for_price(price)` 境界値悉皆」前提は撤回。新 signature `tick_size_for_price(price: Decimal, yobine_code: str, yobine_table: dict[str, list[YobineBand]]) -> Decimal`（`price` は `Decimal` 限定で int/float は `TypeError`、未知 `yobine_code` は `KeyError`、最初に `price <= band.price_le` を満たす band の `tick` を返す）。`tachibana_master.py` に `YobineBand(price_le, tick, decimals)` / `decode_clm_yobine_record(record) -> YobineRecord`（20 スロット順読、`sKizunPrice_n == "999999999"` sentinel で truncate）を実装。`yobine_table` は実行時に `CLMYobine` レコードから構築する。境界値テストは `python/tests/test_tachibana_yobine.py::test_tick_size_for_price_uses_first_band_le_price` に PDF §2-12 スクリーンショット例由来 fixture（`101`/`103`/`418`）で実装済（B1 完了、各 band の境界・境界±`Decimal("0.01")` の 3 点を網羅）+ `test_clm_yobine_decoder_collects_20_bands` / `test_clm_yobine_decoder_truncates_at_999999999_sentinel` / `test_tick_size_for_price_unknown_yobine_code_raises_keyerror` / `test_tick_size_for_price_decimal_only`。**全価格帯の悉皆テストではなく代表 yobine_code の境界値**で足りる（テーブル本体は `CLMYobine` master download から取得され、Phase 1 では立花側のテーブル正しさを再検証する責務はない）
- [x] **B2: master 結合（銘柄→ yobine_code → tick の解決経路、data-mapping.md §5.4 と紐付け）** — T4-B2 branch で着地 (2026-04-26)。`resolve_min_ticksize_for_issue` 関数 + pin テスト 6 件追加 (`test_tachibana_master_yobine_resolve.py` 3 件 / `test_tachibana_master_yobine_invalidation.py` 3 件)。`_ensure_master_loaded` での CLMYobine 並行 download、`yobine_table` 保持、3 トリガ invalidation は既に着地済 (`tachibana.py::TachibanaWorker._ingest_master_records` / `invalidate_master`)。**T4-B2 着地時に `resolve_min_ticksize_for_issue` 呼出（`list_tickers` 内 `"min_ticksize": float(tick)` 詰め込み）と Rust 側 `TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32` フォールバックまで完了。**
  - `tachibana_master.py` に `resolve_min_ticksize_for_issue(issue_record: dict, yobine_table: dict[str, list[YobineBand]], snapshot_price: Decimal | None) -> Decimal` を追加。`issue_record["sYobineTaniNumber"]` で `yobine_table` を引き、`snapshot_price` が `None` のときは `sKizunPrice_1` 相当の保守的フォールバック値を使う
  - `_ensure_master_loaded()` 内で `CLMIssueSizyouMstKabu` と並行して `CLMYobine` を download し、メモリ上の `yobine_table: dict[str, list[YobineBand]]` を `TachibanaWorker` で保持する。is_demo 切替・JST 日跨ぎ・プロセス再起動の 3 トリガで invalidate（HIGH-U-10 規約と同一ライフサイクル）
  - `TickerInfo::new_stock(min_ticksize: f32, ...)` への詰め込みは `Decimal -> f32` で行う（既存シグネチャ不変、data-mapping.md §5.4 と整合）
  - **テスト pin**: (a) `python/tests/test_tachibana_master_yobine_resolve.py::test_resolve_tick_size_for_issue_uses_clm_yobine_lookup` を新設、`CLMIssueSizyouMstKabu` fixture（`sYobineTaniNumber` 含む）+ `CLMYobine` fixture を組合わせ、(i) 既知 `yobine_code` + 既知 `snapshot_price` で正しい tick が返る、(ii) 未知 `sYobineTaniNumber` で `KeyError`、(iii) `snapshot_price=None` で `sKizunPrice_1` フォールバックの 3 ケース。(b) `python/tests/test_tachibana_master_yobine_invalidation.py::test_yobine_table_reloaded_on_invalidation_triggers` を 3 トリガ（is_demo flip / JST rollover / `__init__` 再生成）で `pytest.mark.parametrize`
- [x] ✅ **Rust 側 `TickerInfo` 受信マッピング配線（HIGH-U-9）** — T4-B2 branch で着地 (2026-04-26)。`engine-client/src/backend.rs` に `TickerMetaMap` 型別名・`ticker_meta: Arc<Mutex<TickerMetaMap>>` フィールド・`ticker_meta_handle()`・`reset_ticker_meta()`・staged_meta バッファリング・`RecvError::Lagged` 明示エラー化を実装。`connection.rs` に `capabilities: Arc<Value>` フィールド・`capabilities()` メソッドを追加し `perform_handshake` が `(ws, Value)` を返すよう変更。`dto.rs` の `EngineEvent::Ready.capabilities` に `#[serde(default)]` を追加。`tachibana_meta.rs` の `TickerDisplayMeta` フィールドを `pub(crate)` 化し accessor メソッド・`for_test` コンストラクタ・`matches_tachibana_filter()` を追加。テスト: `engine-client/tests/ticker_meta_map_round_trip.rs`（roundtrip + reset pin）・`engine-client/tests/capabilities_no_secret_keys.rs`（secret-leak smoke）・`handshake.rs` に `capabilities_getter_exposes_ready_snapshot` を追加。**T4-ui から `process.rs` 差分（T3.5 不変条件削除）は意図的にポートしていない**。
  - 現状 `engine-client/src/backend.rs::TickerMetadataMap` 構築は `TickerInfo::new(...)` 経路で `display_name_ja` / `lot_size` / `quote_currency` 正規化が落ちる。本タスクで以下を実装:
  - `tickers[*]` dict から `display_name_ja: Option<String>` を読み、`HashMap<Ticker, TickerDisplayMeta>` の別管理 map に格納（Q16 決定: `TickerInfo` の Hash には含めない）。Rust UI ticker selector のインクリメンタル検索（L530 タスク）対象は **コード前方一致 + `display_name_ja`（日本語名） / `display_symbol`（英語名 = `sIssueNameEizi` 由来、T0.2 L50） 前方一致** の両方
  - 株式の `lot_size` を伝播するため、立花経路では `exchange/src/lib.rs::TickerInfo::new_stock(ticker, min_ticksize, min_qty, lot_size)` を使う（venue_capability で venue を識別して分岐、または stream payload に `lot_size` を必須含めて backend が判定）
  - **`quote_currency` 正規化は IPC 受信側では実行しない**（T0.2 L82 確定）。`new_stock(...)` で構築する時点で `quote_currency` は `Exchange::default_quote_currency()` 由来 = `Some(QuoteCurrency::Jpy)` が埋まるため、`normalize_after_load()` の呼出は saved-state deserialize 経路のみで足りる（F-M6a の規約と整合）。IPC 受信ハンドラで再 fold すると T0.2 の単一規約が崩れるため禁止
  - **テスト**: `engine-client/tests/ticker_info_tachibana_mapping.rs::test_tachibana_ticker_info_carries_display_name_ja_and_lot_size` を pin。`display_name_ja` が UI 検索用 map に格納されること、`lot_size: Some(100)` が保持されること、`new_stock` 経由構築直後に `quote_currency` が `Some(QuoteCurrency::Jpy)` であること（`normalize_after_load` を介さず）を assert
- [x] ✅ **`tachibana.py::TachibanaWorker` クラスと `set_credentials_demo_flag` / `set_session` setter の新設（R11-3、2026-04-26）**: `python/engine/exchanges/tachibana.py` に `TachibanaWorker` クラスを新設済み。`set_credentials_demo_flag(is_demo)` が is_demo 差分検知 + `invalidate_master()` を担い（元の「`set_credentials` setter」に相当）、`__init__` で `_master_loaded = asyncio.Event()` / `_master_lock = asyncio.Lock()` を初期化済み
- [x] ✅ **マスタ in-memory invalidation 規約（HIGH-U-10、2026-04-26）**: `_master_loaded` Event とメモリ上のマスタ内容を再初期化する経路を実装・テスト済み。3 トリガすべて対応:
  - **`is_demo` フラグ変更時**: `set_credentials_demo_flag(is_demo)` で差分検知 → `invalidate_master()` 呼出 (テスト: `test_is_demo_flip_triggers_master_reload`)
  - **JST 日跨ぎ検知時**: `_check_jst_rollover()` で `_master_loaded_jst_date != current_jst_yyyymmdd()` なら invalidate（`_ensure_master_loaded` 入口で毎回チェック、テスト: `test_jst_date_rollover_invalidates_in_memory_master`）
  - **Python サブプロセス再起動時**: `__init__` で `_master_loaded = asyncio.Event()` を新規生成（テスト: `test_worker_init_starts_with_fresh_event`）
- [x] ✅ **非 `"1d"` kline 要求の Python 側明示拒否（HIGH-U-11）** — 実装済み。`tachibana.py::TachibanaWorker.fetch_klines` L411 で `timeframe != "1d"` を `VenueCapabilityError(code="not_implemented")` で即返し。`test_tachibana_fetch_klines_reject.py` に 5 ケース + `tachibana_kline_capability_gate.rs` に Rust 側 pin テストあり。
- [x] ✅ **非 `"1d"` kline 要求の Python 側明示拒否（HIGH-U-11、2026-04-26）**: `tachibana.py::fetch_klines` 入口で `timeframe != "1d"` を `VenueCapabilityError(code="not_implemented")` で即返し実装済み。`test_tachibana_fetch_klines_reject.py` 6 件（5 ケース parametrize + 1d 通過）+ `engine-client/tests/tachibana_kline_capability_gate.rs` Rust 側 pin テストあり。すべて通過済み
- [ ] **受け入れ**: `7203` の日足 1 年分が表示される、銘柄セレクタに数千件のリストが出る、`130A0` 等英字混在 ticker もリストに含まれる、日本語銘柄名が別メタデータ経路で検索または表示に使える、非 `1d` 要求は Python が `not_implemented` で明示拒否し UI が復元時に落ちない、`is_demo` 切替・JST 日跨ぎでマスタが再ロードされる（デモ環境での実機確認が必要）
- [x] ✅ **B5: インクリメンタル検索 UI 配線（T4-B5、2026-04-26）**: 日本語銘柄名インクリメンタル検索 UI 配線着地済み（詳細は L523 の B5 完了行を参照）。`test_tachibana_worker_basic.py` 2 件 + `ticker_info_tachibana_mapping.rs` 2 件（min_ticksize 解決の検証は T4-B2 着地分）全テスト通過
- [x] ✅ **Phase 1 reconnect モデルの設計仮定（B4 R3 明記、H3）**: Phase 1 の reconnect モデルは `EngineClientBackend` 再構築前提とする。`EngineRehello` 由来の reset hook は **Phase 2 (T7) で追加**。本 Phase は新規構築モデルで silent gap が閉じることを設計仮定として本行で明記する（設計文書として完了）
- [x] ✅ **T3.5 不変条件 pin の非退行ガード（T35-* 全 13 件、2026-04-26）**: `cargo test --workspace` 全通過、`tools/iced_purity_grep.sh` OK、`bash tests/e2e/tachibana_relogin_after_cancel.sh` exit 77（skip 許容、`src/replay_api.rs` 未着地のため。T7 で解除）。T4 着地時点で全 13 件 pin テストが非退行であることを確認済み

## フェーズ T5: trade / depth ストリーム（3〜4 日）

**ゴール**: ザラ場時間中、現値変化と 10 本気配がリアルタイムで更新される。

- [x] ✅ `tachibana_ws.py` — EVENT WebSocket クライアント（`p_evt_cmd=FD,KP,ST,SS,US,EC`、購読は最低でも `FD,KP,ST`）（ソース実態確認 2026-04-26: `python/engine/exchanges/tachibana_ws.py` 445 行。`TachibanaEventWs` クラス・`FdFrameProcessor` クラス・`is_market_open` 関数が実装済み。タイムアウトテスト `test_tachibana_ws_timeout.py` 2 件・WS プロキシテスト `test_tachibana_ws_proxy.py` 3 件も緑）
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
- [x] `TachibanaWorker.stream_trades` — FD frame → 出来高差分から `TradeMsg` 合成（**前 frame 気配ベースの quote rule + 初回 frame 除外 + DV リセット検知**、data-mapping §3、F3/F4）
  - 受け入れテスト（`test_tachibana_fd_trade.py`）:
    1. 初回 frame では trade を発火しない（`prev_dv=None`）
    2. 2 件目以降で DV 差分 > 0 のとき trade を 1 件生成
    3. DV が前 frame より減少したら trade 発火せず `prev_dv` を再初期化
    4. side は前 frame の best_bid/best_ask に対して判定（当該 frame の気配は使わない）
- [x] `TachibanaWorker.stream_depth` — FD frame → 10 本気配 → `DepthSnapshot`（`DepthDiff` は生成しない）。`sequence_id` は Python プロセス内 `AtomicI64`、`stream_session_id` 切替時に消費側リセット（F7）
- [x] **`depth_unavailable` セーフティ（MEDIUM-6、F-M12）**: FD WS 受信開始から 30 秒以内に bid/ask キー（`GAP1` / `GBP1` 等（価格）、数量は `GAV1` / `GBV1`）が 1 件も含まれないまま KP/ST 以外の frame が来ない場合、`tachibana_ws.py` は `VenueError{code:"depth_unavailable", message:"立花の板情報が取得できません（FD frame に気配が含まれていません）。設定を確認してください"}` を発出し、当該銘柄の depth 購読を停止して `CLMMfdsGetMarketPrice` polling fallback（10 秒間隔、上限 5 分）に倒す。trade ストリームは継続。テスト: bid/ask キー欠落の FD frame fixture で fallback 経路が起動すること
- [x] `TachibanaWorker.fetch_depth_snapshot` — `CLMMfdsGetMarketPrice` ベースの初回 snapshot（ザラ場前後の 1 発、および FD WS が 12 秒以上無通信の再接続中フォールバック時のみ。**runtime の定期 polling は実装しない**、F-M1b）
- [x] ザラ場時間判定（**JST 9:00–11:30 前場 / 12:30–15:25 後場連続 / 15:25–15:30 クロージング・オークション**、東証 2024-11-05 以降の現行時間）— **9:00–15:30 の間は `Connected` 維持**。クロージング・オークション中は気配が動かなくても「市場時間外」UI を出さない。閉場帯（〜9:00 / 11:30〜12:30 / 15:30〜）でのみ subscribe を `Disconnected{reason:"market_closed"}` で即返し、Python 側で polling/streaming を停止
- [x] **祝日フェイルセーフ（F-M5a）**: Phase 1 は祝日カレンダー判定を持たないため、ザラ場時間内に subscribe → 立花から `p_errno!=0` または「市場休業」相当の取引所エラーが返ったら、`VenueError` ではなく **`Disconnected{reason:"market_closed"}` に倒す**フォールバック分岐を `tachibana_ws.py` に実装。エラー応答の判定パターンは T2 mock テストの拡張で固定。誤判定で平常時の API エラーを market_closed に倒さないよう、対象は明示的なエラーコード（`sResultCode` で「市場休業」「立会停止」相当）のみ
- [x] **祝日 market_closed 倒しの統合テスト（MEDIUM-D2-2、F-M5a の検証）**: 以下 2 ケースを `python/tests/test_tachibana_holiday_fallback.py` に追加 —
  - `test_subscribe_outside_market_hours_emits_disconnected`: ザラ場時間外（JST 8:00 等、`is_market_open` を False で mock）に `stream_trades` を呼んだとき → `Disconnected{reason:"market_closed"}` がちょうど 1 件発出されることを assert
  - `test_subscribe_inside_market_hours_does_not_emit_market_closed`（ネガティブ）: ザラ場時間内（`is_market_open` を True で mock）に `stream_trades` を呼んだとき → `Disconnected{reason:"market_closed"}` が発出されない（WS レイヤを即時クローズして正常終了）ことを assert。誤判定の早期検知用
- [x] **`SetProxy` と WS の整合（F-M3a）**: `SetProxy` が設定されている環境で立花 EVENT WebSocket (`wss://`) が proxy を通るかを T5 受け入れに含める（`HTTPS_PROXY` 経由で `websockets` ライブラリが CONNECT トンネルを張るかの検証）。proxy 未対応で WS が落ちる場合は `VenueError{code:"transport_error", message:"プロキシ経由の WebSocket に失敗しました"}` を返す。**テスト計画（L-3）**: ローカル CONNECT プロキシ（`pproxy` または `mitmproxy` を `subprocess` で起動）を `pytest-httpx` と組み合わせて立てた mock サーバに向け、`websockets` が CONNECT トンネルを張れるかを `python/tests/test_tachibana_ws_proxy.py` で検証。プロキシ不達の場合に `VenueError{code:"transport_error"}` が発出されることも確認
- [x] **`stream_session_id` 切替で gap-detector がリセットされる統合テスト（F-M4b）**: Python 再起動 → 新 `stream_session_id` 発行 → Rust 側 gap-detector の sequence 比較が新 ID 受信時にリセットされることを `engine-client/tests/tachibana_session_reset.rs` で検証
- [x] **`depth_unavailable` ネガティブテスト（HIGH-D4）**: `python/tests/test_tachibana_depth_safety.py::test_depth_safety_does_not_fire_when_keys_arrive_within_30s` を追加。29 秒で bid/ask キー (`GAP1`/`GBP1`（価格）、必要なら `GAV1`/`GBV1`（数量）を併記) を含む FD frame を投入 → `VenueError{code:"depth_unavailable"}` が発出されないこと、`fetch_depth_snapshot` polling コール回数が 0 であることを `unittest.mock.AsyncMock` でカウント
- [x] **ザラ場時間境界の単体テスト（HIGH-D5）**: `python/tests/test_tachibana_session_window.py` を新設し、JST `08:59:59` / `09:00:00` / `11:30:00` / `12:30:00` / `15:25:00` / `15:29:59` / `15:30:00` の 7 ケースを `pytest.mark.parametrize` で追加（呼出側からは 6 境界）。それぞれ「市場時間内 / 外」と「subscribe 即返 `Disconnected{reason:"market_closed"}` の有無」を assert
- [x] **SetProxy + WS 統合のポジティブパス（MEDIUM-D5）**: `python/tests/test_tachibana_ws_proxy.py::test_ws_connects_through_local_connect_proxy` を追加。`pproxy` または `mitmproxy` の CONNECT proxy を `subprocess` で起動 → `SetProxy` 設定下で `tachibana_ws.py` が WS 接続成功、最初の FD frame を受信できることを assert（プロキシ不達時の `transport_error` ネガティブパスとは別ケース）
- [x] **ST frame `sResultCode == "0"` で停止しないネガティブテスト（MEDIUM-D6）**: `python/tests/test_tachibana_ws.py::TestStFrame::test_st_zero_result_does_not_stop_callback` を追加。`p_evt_cmd=ST` かつ `sResultCode=="0"` の情報通知 frame を流し込み、購読が停止しない・`VenueError` が発出されない・コールバックが呼ばれたままであることを assert
- [ ] **受け入れ**: ザラ場中 10 分間 7203 を購読し続けて drop 0、UI で trade ティッカーと板が動く。KP frame 受信ログがあること、tick rule fallback テスト（中値ぴったりの trade で直前 trade との比較が効くこと、F-M8b）が緑であること

## フェーズ T6: 復旧・耐久・観測性（2 日）

**ゴール**: Python 異常終了・session 切れ・ザラ場跨ぎでも UI が破綻しない。

- [x] ✅ `VenueError{venue:"tachibana", code:"session_expired", message}` → Rust UI バナー（旧 `EngineError{code:"tachibana_session_expired"}` は廃止）。**バナー文言は Python が `message` に詰めて送る**（F-Banner1）。Rust 側は `message` をそのまま描画し、固定文言を持たない。`code` は severity（warning/error）とアクションボタン（再ログイン / 閉じる）の出し分けにのみ使う
- [x] ✅ **`VenueError.code` の enum 化（T0 schema 追加分の検証）**: Python 側の発出箇所（`tachibana_auth.py` / `tachibana_ws.py` / `tachibana.py`）で使う code 文字列が [architecture.md §6](./architecture.md#6-失敗モードと-ui-表現) の表と一致することを単体テストで検証。Rust 側 `engine-client/src/error.rs` の `VenueErrorCode` enum + `classify_venue_error` が各コードを網羅（全コード explicit test 済み）。未知 code は `(Error, Hidden)` fail-safe に倒す
- [x] ✅ **バナー文言テスト** `python/tests/test_tachibana_banner_messages.py`（snapshot test、locale=`ja_JP` 固定）: `_MSG_*` 定数の snapshot assert（5 定数）＋ 日本語文字列ガード＋ `UnreadNoticesError` / `depth_unavailable` key phrase ガード実装済み。23 件全 PASS（2026-04-26）
- [x] ✅ `VenueCredentialsRefreshed` 経由で**起動時再ログイン後**の session を Rust が keyring 更新。`engine-client/tests/process_creds_refresh_hook.rs` の 4 テスト（`patch_in_memory_session_replaces_session_field` / `refresh_hook_callback_fires_with_session` / `medium7_full_variant_overwrites_credentials_triple` / `medium7_from_wire_partial_mixture_falls_back_to_session_only`）で검증済み
- [x] ✅ Python 再起動シナリオの自動テスト。`engine-client/tests/process_lifecycle.rs` に `test_credentials_resent_in_order_after_restart` を追記済み。コマンド送信順序 `SetProxy → SetVenueCredentials → Subscribe` を assert（2026-04-26）
- [x] ✅ ログ redaction テスト `python/tests/test_tachibana_log_redaction.py`（user_id / password / session token / 仮想URLが全 log record に含まれないこと）: 5 テストケース（happy path / error path / validate_session happy / validate_session expired / 全 tachibana logger sweep）実装済み。全 PASS（2026-04-26）
- [x] ✅ capabilities ハンドシェイクで OI / fetch_trades / 分足の非対応を Rust に伝え UI を非活性化。`tachibana.py::capabilities()` が `{"supported_timeframes": ["1d"]}` を返し、Rust 側 `is_timeframe_enabled` が 1m/5m/1h を disabled 化
- [x] ✅ capabilities UI 非活性化テスト `engine-client/tests/capabilities_gate.rs`。`1m / 5m / 1h` が `enabled == false`、`1d` が `enabled == true` を assert（2026-04-26 に `1m` の assert を追加）
- [x] ✅ `NotImplementedError` → `Error{code:"not_implemented"}` 変換。`tachibana.py::VenueCapabilityError(code="not_implemented")` が server 側で `Error` イベントにマップされることを `test_tachibana_error_mapping.py` で검증済み
- [x] ✅ 「ProcessManager が credentials を保持していないため再起動後に立花だけ復旧しない」回帰防止統合テスト。`engine-client/tests/process_lifecycle.rs::venue_credentials_are_retained_after_handshake` 追加済み（2026-04-26）
- [x] ✅ **`VenueReady` 冪等性テスト**: `engine-client/tests/venue_ready_idempotent.rs` を新設（2026-04-26）。`second_venue_ready_does_not_trigger_extra_subscribe`（2 サブスクリプション×2 ready → Subscribe は 2 件のみ）＋ `apply_after_handshake_sends_subscribe_exactly_once_per_subscription` の 2 テスト全 PASS
- [ ] **受け入れ**: [spec.md §4 受け入れ条件](./spec.md#4-受け入れ条件phase-1-完了の定義) 全て緑（デモ環境での実機確認が必要）

### T6 実装サマリ（2026-04-26）

**実装済みテスト一覧**:
| ファイル | テスト数 | 状態 |
|---|---|---|
| `python/tests/test_tachibana_banner_messages.py` | 18 | ✅ 全 PASS |
| `python/tests/test_tachibana_log_redaction.py` | 5 | ✅ 全 PASS |
| `engine-client/tests/capabilities_gate.rs` | 3（`1m` 追加済み） | ✅ 全 PASS |
| `engine-client/tests/process_lifecycle.rs` | +2（resent_in_order / retained） | ✅ 全 PASS |
| `engine-client/tests/venue_ready_idempotent.rs` | 2（新設） | ✅ 全 PASS |

**設計メモ**:
- バナー文言は `_MSG_*` 定数（`tachibana_auth.py`）が sole source of truth。Rust は `message` をそのまま描画し文言生成しない（F-Banner1 遵守）
- ログ redaction テストは sentinel 方式（高エントロピー文字列、`test_tachibana_startup_supervisor.py` MEDIUM-3 ラウンド 7 と同設計）。`caplog.at_level(DEBUG)` で全 record を捕捉
- VenueReady 冪等性: `apply_after_handshake` の外から届く stray VenueReady は ProcessManager が resubscribe をトリガしない構造であることを mock double-ready サーバで実証
- credentials retention: `apply_after_handshake` は `venue_credentials` を `clone()` して使うため原本は消えない（mut borrow なし）。テストがこの不変条件を pin
- **T6 タスク実行コマンドと CI ゲート（D-M1）**:
  - `test_tachibana_banner_messages.py` / `test_tachibana_log_redaction.py`: `uv run pytest python/tests/test_tachibana_banner_messages.py python/tests/test_tachibana_log_redaction.py -v`
  - `capabilities_gate.rs` / `VenueReady` 冪等性テスト: `cargo test -p flowsurface-engine-client`
  - CI ジョブ名: `.github/workflows/rust.yml::ci-test`（既存 rust テストジョブに統合）
  - **F-Banner1 の Tx タスク帰属**: F-Banner1（バナー文言テスト）は T7 でなく **T6** に帰属する（`test_tachibana_banner_messages.py` は本フェーズのタスク `[ ] バナー文言テスト` で実装するため）。invariant-tests.md の F-Banner1 行は別エージェント担当で T6 に修正予定。

### レビュー反映 (2026-04-26, ラウンド 1)

以下の指摘を TDD（RED→GREEN）で解消した。

| ID | ファイル | 内容 | 状態 |
|---|---|---|---|
| M-A | `python/engine/nautilus/engine_runner.py:195` | `sorted(timestamps), sorted(last_prices)` の独立ソートによるデータ破壊バグを `zip` + `sorted` によるペア保持ソートに修正 | ✅ |
| M-D | `engine-client/tests/process_lifecycle.rs:319,323,327` | `.expect("... {ops:?}")` が補間されない問題を `.unwrap_or_else(\|\| panic!("... {ops:?}"))` に変更（3 箇所） | ✅ |
| M-B | `engine-client/tests/process_lifecycle.rs:307-312` | `sleep(150ms) + try_recv` パターンを `timeout_at` 付き drain ループに変更（CI race 修正） | ✅ |
| M-C | `engine-client/tests/venue_ready_idempotent.rs:181,230` | `sleep(300ms) + try_recv` パターンを `timeout_at` 付き drain ループに変更。`apply_after_handshake` を `apply_after_handshake_with_timeout(5s)` に変更（2 テスト）。`process_lifecycle.rs` の `apply_after_handshake` 呼び出し 2 箇所も同様に変更 | ✅ |
| M-E | `engine-client/tests/capabilities_gate.rs` | `is_timeframe_enabled` の Err バリアントテスト `test_malformed_venue_capabilities_returns_err` を追加 | ✅ |
| M-F | `python/tests/test_tachibana_banner_messages.py` | `depth_unavailable` の `VenueError.message` が `板情報` を含むことを実値解析で検証するテスト `test_depth_unavailable_venue_error_message_contains_ita_joho` を追加（既存 `inspect.getsource` テストは残存） | ✅ |

**回帰テスト結果 (2026-04-26)**:
- `uv run pytest python/tests/` → 490 passed, 1 failed（pre-existing 環境依存: `test_tachibana_worker_basic::test_unimplemented_streams_raise_not_implemented` — DNS エラー、本修正と無関係）
- `cargo test -p flowsurface-engine-client` → 全件 ok
- `cargo check --workspace` → Finished（エラーなし）
- `cargo clippy --workspace -- -D warnings` → Finished（警告なし）
- `cargo fmt --check` → 差分なし（全 Rust ファイル整形済み）

**新設テストファイル**:
- `python/tests/test_collect_fill_data_preserves_pairs.py` — 6 テスト（M-A 回帰ガード）

### レビュー反映 (2026-04-26, ラウンド 2-3)

R2・R3 で発見・解消した追加指摘。

| ID | ファイル | 内容 | 状態 |
|---|---|---|---|
| HIGH-P2 | `python/engine/exchanges/tachibana_helpers.py:52` | `UnreadNoticesError` デフォルト文言が architecture.md §6 と不一致。`"未読通知があるため仮想 URL が発行されません"` → `"立花からの未読通知があります。ブラウザで確認後に再ログインしてください"` に統一 | ✅ |
| HIGH-P3 | `python/tests/test_tachibana_banner_messages.py:19` | コメントの `virtual_url_invalid` coverage 表記を `code="login_failed"` で発出される旨に訂正 | ✅ |
| HIGH-SFH1 | `python/engine/nautilus/engine_runner.py:189-194` | ts/lp の独立 None ガードで zip サイレント切り捨てが発生するバグを `if ts is not None and lp is not None` の同時評価に修正 | ✅ |
| HIGH-SFH2 | `python/engine/exchanges/tachibana_login_flow.py` (7 箇所) | `str(exc)` が IPC バナーに送られ英語混じり内部文字列が露出していた問題を `exc.message` に統一 | ✅ |
| HIGH-RS1 | `engine-client/tests/venue_ready_idempotent.rs:117` | mock server `sleep(50ms)` に根拠コメント追記（CI 余裕 500ms 内に収まる旨を明記） | ✅ |

**R3 収束確認 (2026-04-26)**:
- MEDIUM 以上の指摘ゼロを確認
- `uv run pytest python/tests/test_tachibana_banner_messages.py python/tests/test_collect_fill_data_preserves_pairs.py` → 全 PASS
- `cargo test -p flowsurface-engine-client` → 全 PASS

**新たな知見 (MISSES.md 候補)**:
- `str(exc)` を IPC に渡すと内部エラー文字列が UI に露出する。`exc.message` を使うこと
- 並列リストの None ガードは個別でなく同時評価（`ts is not None and lp is not None`）でペア整合を保つ
- Rust の `.expect("... {var:?}")` はリテラル扱いで補間されない。`unwrap_or_else(|| panic!(...))` を使う

**持ち越し（LOW として次フェーズ）**:
- `process_lifecycle.rs::run_with_recovery_calls_on_ready_on_connect` — テストが `on_ready` コールバック経路を実際に検証していない（mock 自己発火の設計上の問題）。T7 で直すか別 PR で対処
- `SessionExpiredError` デフォルト文言が英語混じり（"Tachibana セッション..."）— T7 でメッセージ整備時に対処

## フェーズ T7: 仕上げ・配布準備（1〜2 日）

> **進捗 (2026-04-26)**: 全タスク着地。
> - `tools/secret_scan.sh` + `tools/secret_scan.ps1` + `tools/secret_scan_patterns.txt` + `tools/secret_scan_allowlist.txt` 新設
> - `tools/tests/test_secret_scan.sh` + `tools/tests/test_secret_scan.ps1` + fixtures 新設（HIGH-D6）
> - `.github/workflows/python-tests.yml`（pytest + secret_scan + secret_scan meta + tkinter smoke）
> - `.github/workflows/tachibana-demo.yml`（`workflow_dispatch` のみ）
> - `python/tests/test_invariant_table_covers_all_ids.py`（R8-D1 CI ガード）
> - `python/tests/test_tachibana_tkinter_smoke.py`（F-M2c、`--auto-cancel` フラグ追加）
> - `engine-client/tests/capabilities_changed_after_reconnect.rs`（B4 R3 M3 繰越）
> - `src/replay_api.rs` 新設 + `src/main.rs` wiring（E2E skip 第 1 ゲート解除）
> - README.md + SKILL.md に立花 venue 前提条件追記
> - `pytest.ini` に `demo_tachibana` / `tk_smoke` マーカー登録
>
> **設計判断**:
> - `secret_scan` の allowlist: `tools/secret_scan_allowlist.txt` 1 ファイルを正本として sh/ps1 両スクリプトが参照。`docs/` と `__pycache__` はデフォルト除外（文書・コンパイル成果物は scan 対象外）
> - `replay_api.rs`: 最小 raw-TCP HTTP/1.1 サーバー（axum/hyper 非追加）。Iced 統合（`ControlApiCommand` → `update()`）は Phase O1 繰越。`mod replay_api;` の main.rs 宣言でスクリプトの第 1 skip ゲートは解除済み
> - `capabilities_changed_after_reconnect`: 新旧 2 本の独立モックサーバーで capabilities snapshot の更新を確認（backend 使い回しテストは Phase O1 で追加）

- [x] ✅ **不変条件 ID ↔ test 関数名対応表の集約（R8-D1）**: `python/tests/test_invariant_table_covers_all_ids.py` で「完了済みタスクに test 関数名が設定されていること」と「ID の重複なし」を CI 確認
- [x] ✅ **Python テスト CI 組込**: `.github/workflows/python-tests.yml` に `uv run pytest python/tests/`、secret-scan、meta-test、tkinter-smoke ジョブを追加
- [x] ✅ **README / SKILL.md に「立花 venue 利用の前提（電話認証済み口座が必要）」追記**
- [x] ✅ **release ビルドで env 自動ログイン除外の統合テスト確認**: T3 で実装済みの `engine-client/tests/dev_login_flag_release.rs` が正本ガード。Python 側は `test_tachibana_dev_env_guard.py` が pin。新規追加テストは不要（既存カバー済み）
- [x] ✅ **本番 URL 隠しフラグ（`TACHIBANA_ALLOW_PROD=1`）**: T2 で実装済み（`tachibana_login_flow.py::_spawn_login_dialog` の `allow_prod_choice` 経路）。`test_tachibana_login_dialog_modes.py` で pin。新規追加なし
- [x] ✅ **demo_tachibana CI 統合**: `.github/workflows/tachibana-demo.yml`（`workflow_dispatch` のみ、T2 確定方式 B）
- [x] ✅ **tkinter スモークテスト（F-M2c）**: `tachibana_login_dialog.py` に `--auto-cancel` フラグ追加。`python/tests/test_tachibana_tkinter_smoke.py` 4 件緑。CI は `xvfb-run pytest -m tk_smoke` で実行（python-tests.yml 内）
- [x] ✅ **`tools/secret_scan.sh` + `tools/secret_scan.ps1` 新設**: `tools/secret_scan_patterns.txt`（5 パターン正本）+ `tools/secret_scan_allowlist.txt`（許可ファイル一覧）。`bash tools/secret_scan.sh` で OK（exit 0）確認済み
- [x] ✅ **secret_scan メタテスト（HIGH-D6）**: `tools/tests/test_secret_scan.sh`（3 件）+ フィクスチャ `should_fail/` / `should_pass/` 新設。bash での 3 件全通過確認済み。`tools/tests/test_secret_scan.ps1`（Windows 版）も新設
- [x] ✅ **`capabilities_changed_after_reconnect` pin test（B4 R3 M3 繰越）**: `engine-client/tests/capabilities_changed_after_reconnect.rs` 新設、1 件緑
- [x] ✅ **`src/replay_api.rs` 新設 + main.rs wiring**: `pub mod replay_api;` を main.rs に追加。`GET /api/replay/status`（200 JSON）/ `POST /api/sidebar/toggle-venue`（202）/ `POST /api/sidebar/tachibana/request-login`（202）/ `POST /api/test/tachibana/cancel-helper`（202）を提供。E2E script の第 1 skip ゲート（`mod replay_api;` 存在確認）は解除済み。第 2 ゲート（`/api/replay/status` 疎通）は HTTP サーバー起動後に解除。Iced 統合（Phase O1 繰越）

### レビュー反映 (2026-04-26, ラウンド R1)
- ✅ HIGH-2: set_nonblocking log::warn 追加
- ✅ HIGH-3: try_send に変更（channel 満杯でのブロック解消）
- ✅ HIGH-4: toggle-venue 空 venue → 400 Bad Request
- ✅ HIGH-5: runtime build/spawn 失敗 log::error 追加
- ✅ HIGH-6: should_fail fixture に sSecondPassword + BASE_URL_PROD 追加
- ✅ HIGH-7: secret_scan.ps1 に docs/__pycache__/.pytest_cache 除外追加
- ✅ MEDIUM-3: pub mod → mod replay_api
- ✅ MEDIUM-5: write_response log::debug 追加
- ✅ MEDIUM-7: accept() error sleep 100ms バックオフ追加
- ✅ MEDIUM-2: content_length コメント修正
- ✅ MEDIUM-4: capabilities test に TODO(O1) コメント追加
- ✅ MEDIUM-8: secret_scan.sh EXCLUDE_ARGS ノーオペ説明コメント追加
- ✅ MEDIUM-9: python-tests CI ジョブに tk_smoke/demo_tachibana 除外追加
- ✅ MEDIUM-10: tachibana-demo.yml timeout-minutes: 15 追加
- ✅ MEDIUM-11: _run_dialog JSON decode 失敗時 pytest.fail() に改善
- ✅ MEDIUM-12: test_headless 設計意図コメント追加
- **H8 繰越 (Phase O1)**: test_invariant_table にソースファイル ID ドリフト検知を追加するにはテーブル全未登録 ID の同時追加が必要。table 完成後に対応。

## 下流計画への影響

本計画の完了（特に T2・T7）は、以下の下流計画のブロッカーを解除する。作業者はフェーズ完了時に下記リンク先の前提条件欄を確認すること。

| 完了フェーズ | 解除されるブロッカー | 参照先 |
|---|---|---|
| **T2（認証実装）完了** | [order/ Phase O-pre](../✅order/implementation-plan.md) 着手可能。`tachibana_auth.py` / `tachibana_url.py` / `tachibana_codec.py` が order/ の前提依存ファイル | [order/implementation-plan.md 冒頭](../✅order/implementation-plan.md) |
| **T4（マスタキャッシュ）完了** | IPC `stdin` 初期 payload への `config_dir` / `cache_dir` 追加が完了し、Python 側 fast-path が使えるようになる | [architecture.md §2.1.1](./architecture.md) |
| **Phase 1 全完了（T7 受け入れ緑）** | nautilus N2（`LiveExecutionClient` デモ）の着手条件の一部を満たす | [nautilus_trader/implementation-plan.md Phase N2](../nautilus_trader/implementation-plan.md) |

> **IPC schema 連鎖**: 本計画の T0.2 で schema **1.1 → 1.2** に bump する。order/ の Tpre.2（schema 1.2 → 1.3）は本計画の schema 1.2 ラウンドトリップテストが緑になるまで着手しないこと。連鎖の全体像は [docs/plan/README.md §実装トラック詳細](../README.md) を参照。

---

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
| **FD 情報コード未確定で Phase 1 縮退（HIGH-2）** ✅ 解消済み（2026-04-26） | `DV` / `GAP*` / `GBP*` / `GAV*` / `GBV*` / `DPP:T` / `p_date` の正式コード名は [inventory-T0.md §11.3](./inventory-T0.md#113-ブロッカー解消記録b3-クローズ) のいずれか（PDF 同梱 / 実 frame キャプチャ / Phase 縮退）で**T5 着手前に必ず実体解決**（T0.1 ゲート規約 L23–L35 と整合）。T1 codec は確認済み data key (`DPP` のみ) の範囲で先行着手可。縮退案を取った場合は Phase 1 を「日足 kline + ticker stats のみ」に縮退し spec.md §2.1 を改訂。PR 説明文に解決証跡を必須記載（PR テンプレに gate 化） |
| **マスタからの異常 ticker で Rust panic（HIGH-3）** | `Ticker::new` (`exchange/src/lib.rs::Ticker::new`) は `assert!` で panic する。Python `tachibana_master.py` で「ASCII 28 文字以内・`\|` 不含」を pre-validate して逸脱は skip + warn ログ。Rust IPC 受信側は `EngineEvent::TickerInfo.tickers[*]` の各 ticker dict を `Ticker::new` 呼出前に同条件で再 validate し、不正値は drop（panic させない）|
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
