# 立花証券統合: アーキテクチャ

## 1. 配置原則

[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §2 の責務分割を踏襲:

| 責務 | 所在 | 備考 |
| :--- | :--- | :--- |
| ユーザー ID / パスワード / 第二暗証番号の保存 | **Rust（keyring）** | `data::config::tachibana`（新設） |
| 電話認証完了の前提条件 | ユーザー操作（手動） | アプリは関与しない |
| `CLMAuthLoginRequest` 実行と仮想 URL 5 種の取得 | **Python** | クレデンシャルは Rust から IPC で受領 |
| 仮想 URL（セッション）の保持 | **Python メモリ + Rust keyring** | Python は揮発、Rust は永続。再起動時 Rust → Python へ再注入 |
| マスタダウンロード（21MB ストリーム） | **Python** | 起動時 1 回 + 日次。`sJsonOfmt="4"` |
| FD frame パース（Shift-JIS / 制御文字分解） | **Python** | `parse_event_frame` 相当を Python 実装 |
| 板スナップショット polling | **Python** | フォーカス pane のみ高頻度。Rust 側で要求頻度を伝える |
| `p_no` 採番 / `p_sd_date` 生成 | **Python** | プロセス内 `AtomicU64` 相当 + JST chrono |
| `p_errno` / `sResultCode` 二段判定 | **Python** | `EngineError` または `Error` イベントへマップ |
| **ログイン画面の描画**（独立ウィンドウ、tkinter） | **Python**（`tachibana_login_dialog.py`、subprocess 隔離） | F-Login1、§7。Rust は描画コードを持たない |
| **ログイン画面の発火タイミング判定** | **Python**（`tachibana_login_flow.py`） | session 失効・creds 未注入・debug env 検知時に Python ヘルパーを spawn |
| **ログイン入力値の収集** | **Python**（tkinter ヘルパー → stdout JSON） | creds は Rust 経路を通らない（runtime ユーザー入力時） |
| 起動時の keyring 復元クレデンシャル注入 | **Rust → Python**（`SetVenueCredentials`） | typed payload は維持（`SecretString` セキュリティ、F-B1/F-B2） |
| バナー文言（`VenueError.message`） | **Python** | Rust UI は `message` をそのまま描画（F-Banner1、§6） |
| UI のフレーム（チャート / ticker selector / バナー枠 / ログインフォーム枠） | **Rust** | 既存 iced レイアウトを流用 |

**Rust 直結（NativeBackend）は実装しない**。立花統合は最初から `EngineClientBackend` のみで成立させる。これにより [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §7.1 案 A（完全撤去）の方針と一貫する。

**長期方針（[README.md](./README.md) §「長期方針」と整合）**: 将来 Rust（iced）を使わない **Python 単独モード**を新設する予定。Phase 1 で Python 側に置く venue 固有実装（認証・パース・tkinter ログイン UI・バナー文言）は **Python-only モードでもそのまま再利用できる構造**にする。設計判断で迷ったら「Python 単独でも動くか？」を判断軸に使う。

> **補足**: SKILL.md は `exchange/src/adapter/tachibana.rs` を実在する参考実装として記述しているが、本リポジトリには存在しない（git 履歴上も未確認）。本計画は Rust adapter を**新設しない**（Python 側に集約）方針なので、SKILL.md L41/L431 の Rust ヘルパー参照は**仕様の抽象記述として読み替える**こと。

## 2. クレデンシャル受け渡しのプロトコル拡張

既存 IPC（[engine-client/src/dto.rs](../../../engine-client/src/dto.rs)）には `SetProxy` しかない。立花のクレデンシャルを安全に渡すため、**`SetVenueCredentials` コマンドを新設**する。

### 2.1 新設 IPC コマンド

venue 固有のクレデンシャル shape は **`serde_json::Value` を渡さない**。Rust 側で `Debug` マスクを効かせるため、typed enum で受け渡す（F6）。

**`secrecy` クレート導入方針（F-B1）**: `engine-client` / `data` の `Cargo.toml` に `secrecy = "0.8"` を追加。`SecretString` は **Rust 内部の保持型**として使い、IPC へ送出する直前にだけ `expose_secret()` で `&str` を取り出して JSON 化する。

**`Serialize` の整合（F-B2）**: `secrecy::SecretString` は `Serialize` を実装しない。よって IPC 送出用 DTO は **2 層構造**にする:

- 内部保持型 `TachibanaCredentials` / `TachibanaSession`（`data` クレート、`SecretString` 保持、`Debug` 手実装でマスク、`Serialize` は持たせない、`Deserialize` は keyring 復元用にのみ持つ）
- 送出用 DTO `TachibanaCredentialsWire` / `TachibanaSessionWire`（`engine-client` クレート、フィールドはプレーン `String`、`Debug` は手実装で値マスク）

**Wire DTO の Serialize / Deserialize 方向（C2 修正）**: IPC は双方向のため、Wire 型の trait 実装は **流れる方向ごと** に決める:

| Wire 型 | 出現するメッセージ | Rust 視点の方向 | 実装 |
| :--- | :--- | :--- | :--- |
| `TachibanaCredentialsWire` | `Command::SetVenueCredentials` | Rust → Python | `Serialize` のみ |
| `TachibanaSessionWire` (送信側) | `Command::SetVenueCredentials.payload.session` | Rust → Python | `Serialize` |
| `TachibanaSessionWire` (受信側) | `EngineEvent::VenueCredentialsRefreshed.session` | Python → Rust | `Deserialize` |

`TachibanaSessionWire` は **両方向に出現する** ため `Serialize + Deserialize` の両方を派生する（命名は同一型を使い回す）。`TachibanaCredentialsWire` は Rust→Python 一方向のみなので `Serialize` のみで足りる。「Wire は Deserialize を持たない」という旧方針は誤り。

送信時は `From<&TachibanaCredentials> for TachibanaCredentialsWire` で `expose_secret()` 経由の写像を 1 箇所に集約し、`Wire` 値はその場で serialize → drop して長時間メモリに残さない。受信時は `TryFrom<TachibanaSessionWire> for TachibanaSession` で `SecretString::from()` 経由の写像を 1 箇所に集約。

**Wire の zeroize（M4 追記、M3 修正）**: プレーン `String` を直持ちすると `Drop` でゼロクリアされず、シリアライズバッファ・serde 中間 Cow・スワップアウト後のページ等に平文が残りうる。`engine-client` の `Cargo.toml` に `zeroize = "1"` を追加し、`TachibanaCredentialsWire` / `TachibanaSessionWire` の secret フィールドを `zeroize::Zeroizing<String>` で持つ（`Serialize` は `&str` を経由するので impl は維持できる）。`Drop` でメモリゼロ化される一方、関数引数 / クローンで複製されたバッファまでは追えないので、**Wire 値はスコープを最小化（serialize 直後に明示 drop）する規約**を `engine-client/src/backend.rs` の `SetVenueCredentials` 送信パスに `// SAFETY-LITE: ...` コメント付きで記す。

**回帰テスト（M3 修正）**: `engine-client/tests/wire_dto_drop_scope.rs` に下記を追加:
- (a) Wire 値を作って `serde_json::to_string` した後にスコープを抜けると、`Zeroizing` の `Drop` が呼ばれること（`Drop` 実装の存在を `std::mem::needs_drop::<TachibanaCredentialsWire>()` で確認）
- (b) `SetVenueCredentials` 送信関数のシグネチャが Wire 値を **値渡し（move）で受け取り関数内で drop される** ことを型レベルで保証（`fn send(_: TachibanaCredentialsWire)` 形式、`&` 参照渡しは禁止）
- ヒープ実メモリのゼロ化検証は OS 依存で flaky なので入れない（コードレビューで Wire 値の clone / Arc 包みを禁止する規約をコメントで残す）

```rust
// data/src/config/tachibana.rs（内部保持型）
use secrecy::SecretString;

pub struct TachibanaCredentials {
    pub user_id: String,
    pub password: SecretString,
    pub second_password: Option<SecretString>, // F-H5: Phase 1 では常に None
    pub is_demo: bool,
    pub session: Option<TachibanaSession>, // keyring 復元時のみ Some
}

pub struct TachibanaSession {
    pub url_request: SecretString,
    pub url_master: SecretString,
    pub url_price: SecretString,
    pub url_event: SecretString,
    pub url_event_ws: SecretString,
    pub expires_at_ms: Option<i64>, // None なら起動時 validation 必須（F-B3）
    pub zyoutoeki_kazei_c: String,  // 譲渡益課税区分（発注時に再利用）
}

// engine-client/src/dto.rs（送出用 Wire DTO）
pub enum Command {
    // ... 既存 ...
    SetVenueCredentials {
        request_id: String,
        payload: VenueCredentialsPayload,
    },
}

#[derive(Serialize)]
#[serde(tag = "venue", rename_all = "snake_case")]
pub enum VenueCredentialsPayload {
    Tachibana(TachibanaCredentialsWire),
}

// Debug は手実装でマスク。Derive(Debug) は使わない。
// Rust → Python 一方向なので Serialize のみ
#[derive(Serialize)]
pub struct TachibanaCredentialsWire {
    pub user_id: String,
    pub password: String,
    pub second_password: Option<String>, // F-H5: Phase 1 では常に None
    pub is_demo: bool,
    pub session: Option<TachibanaSessionWire>,
}

// Session は Rust↔Python 双方向（SetVenueCredentials 送信 + VenueCredentialsRefreshed 受信）
#[derive(Serialize, Deserialize)]
pub struct TachibanaSessionWire {
    pub url_request: String,
    pub url_master: String,
    pub url_price: String,
    pub url_event: String,
    pub url_event_ws: String,
    pub expires_at_ms: Option<i64>,
    pub zyoutoeki_kazei_c: String,
}
```

`expires_at_ms` の決定方針（F-B3）:
- 立花 API は session の正確な期限値を返さない（夜間閉局までという運用情報のみ、SKILL.md R3）
- **方針**: `Option<i64>` で持ち、ログイン直後は `None`（= 起動時 `validate_session_on_startup` が必須）。今後 `CLMDateZyouhou` から閉局時刻を取得できることが判明したら値を入れる
- keyring から復元する `expires_at_ms = Some(t)` のとき、`now > t` なら復元せず再ログイン経路へ進む（fast path）。`None` のときは validation を必ず叩く（safe path）

対応する Python 側 pydantic モデルは `pydantic.SecretStr` を使い、`__repr__` で `***` 化する。

- `session` は **Rust が keyring から復元できた場合のみ** 含める。Python はまず `session` を試し、`p_errno="2"` で失敗したら `user_id/password` で再ログインする
  - この再ログインは **起動直後の `SetVenueCredentials` 処理中に限る**。購読開始後の runtime で session expiry を検知した場合は再ログインせず `VenueError{venue:"tachibana", code:"session_expired"}` を返す
- `second_password` は **Phase 2 以降の発注機能で使う**。**Phase 1 では DTO スキーマ上 `Option<SecretString>` で持つが、収集も保持もせず常に `None` を送る**（F-H5）。発注しないものを Python メモリに保持して攻撃面を増やさない方針
- このコマンドは **`Ready` 受信後・任意の `Subscribe` 前** に送る（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.5 起動ハンドシェイク）
- **応答規約**: Python は 1 件の `SetVenueCredentials` に対し、成功時は `VenueReady{venue, request_id}` を、失敗時は `VenueError{venue, request_id, code, message}` を必ず 1 件だけ返す（F1）

### 2.1.1 起動パラメータとの責務分離

- 現行の Python engine 起動パス（[engine-client/src/process.rs](../../../engine-client/src/process.rs)）は `stdin` で `port` / `token` しか渡していない
- そのため **クレデンシャルや session は起動引数ではなく IPC コマンドで渡す** 方針を維持する
- 一方でマスタキャッシュ保存先だけは Python 側単独では決められないため、T0 で次のどちらかを追加する
  - `stdin` 初期 payload に `config_dir` / `cache_dir` を追加
  - `SetEnginePaths` 相当の軽量コマンドを新設
- **`dev_tachibana_login_allowed` フラグの追加（H-2、T3 で実装）**: Python が debug ビルド専用の `DEV_TACHIBANA_*` env を読んでよいかを Rust が明示的に許可するフラグ。`stdin` 初期 payload に **`dev_tachibana_login_allowed: bool`** を追加する（最終 payload 形式: `{"port": N, "token": "...", "config_dir": "...", "cache_dir": "...", "dev_tachibana_login_allowed": bool}`）。Rust 側は `#[cfg(debug_assertions)]` で `true`、release では `false` を渡す。Python 側 `tachibana_login_flow.py` は `dev_tachibana_login_allowed == false` のとき `os.getenv("DEV_TACHIBANA_*")` を読まずスキップし、release ビルドでの env 混入を完全にガードする。`spec.md §3.1` の「`TACHIBANA_DEV_LOGIN_ALLOWED` 起動 flag」はこの方式で実現する

### 2.2 ログ・テレメトリでのマスク

- Rust 側の `Debug` 派生で `password` / `second_password` / `session.url_*` をマスクする `SecretString` 型を導入
- Python 側は `engine.exchanges.tachibana.SessionState` を `__repr__` でマスク（pydantic の `SecretStr` を採用）
- IPC のシリアライズ時にマスクは行わない（loopback + token 認証で守る）

### 2.3 セッション再永続化

Python が**起動時の session 検証または初回ログイン**に成功し新仮想 URL を取得したら、**`VenueCredentialsRefreshed` イベントを Rust に逆送**して Rust が keyring を更新する:

```rust
pub enum EngineEvent {
    // ...
    VenueReady {
        venue: String,
        request_id: Option<String>, // SetVenueCredentials の request_id（再接続時 None 可）
    },
    VenueError {
        venue: String,
        request_id: Option<String>,
        code: String,      // "session_expired" | "unread_notices" | "login_failed" ...
        message: String,
    },
    VenueCredentialsRefreshed {
        venue: String,
        session: TachibanaSessionWire,  // typed、Debug は手実装でマスク
    },
}
```

- `VenueError{code:"session_expired"}` は runtime の `p_errno="2"` 検知時に、対応する `request_id` を持たない `SetVenueCredentials` 失敗時は起動シーケンスの `request_id` に紐付けて返す
- 旧 `EngineError{code:"tachibana_session_expired"}` 表記は廃止。venue-scoped `VenueError` に一本化する

これにより Python 単独再起動 → Rust が keyring の最新 session を投入 → Python が validation 実行、というループが閉じる。

### 2.4 再起動時の source of truth

- **managed mode の再起動導線は `ProcessManager` が source of truth**。再接続時に `src/main.rs` がその場しのぎで `SetVenueCredentials` を送るのではなく、`ProcessManager` が proxy と同様に venue credentials も保持・再送する
- 再起動後の正式シーケンスは次の通り:
  1. `Hello -> Ready`
  2. `SetProxy`
  3. `SetVenueCredentials`（venue ごとに 1 回。`ProcessManager.venue_credentials` を順に送る）
  4. `VenueReady` を **同期点** として待機（`tokio::sync::Notify` か `oneshot::channel` で `request_id` 単位で受信完了を await）
  5. metadata fetch 再開
  6. active subscriptions 再送
- これにより [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 の「recovery handshake 後に購読再送」という既存契約に、立花の認証状態を安全に差し込める
- **`engine-client/src/process.rs` の `ProcessManager::start()` 内コメント（M3 追記）**: 現状コメントは `Hello/Ready → SetProxy → Subscribe` のみで `SetVenueCredentials` ステップが欠落している。**T3 で実装する際に、コメントブロックに `(3) SetVenueCredentials (per venue) → wait VenueReady` を必ず差し込む**。さらに「`VenueReady` を待ってから resubscribe する同期点」を実装するチャネル / Notify の選択は T3 の作業項目とし、以下のいずれかで具体化:
  - `Arc<Mutex<HashMap<String /*venue*/, oneshot::Sender<()>>>>` を持ち、`SetVenueCredentials` 送信時に `oneshot` を登録、`VenueReady` 受信ハンドラで `send(())`、`start()` 側は `oneshot::Receiver::await`
  - もしくは venue ごとの `tokio::sync::Notify` を保持し、`VenueReady` 受信で `notify_one()`、`start()` 側は `notified().await`

## 3. 起動シーケンス

```
Rust 起動
  ├─ keyring から TachibanaCredentials を読む（任意）
  ├─ Python サブプロセス spawn（既存 src/main.rs のフロー）
  ├─ Hello → Ready 受領
  ├─ SetProxy（必要時）
  ├─ SetVenueCredentials{venue:"tachibana", ...}  ← 新設
  ├─ VenueReady{venue:"tachibana"}
  ├─ ListTickers{venue:"tachibana", market:"stock"}
  └─ Subscribe{venue:"tachibana", ticker:"7203", stream:"trade"|"depth", market:"stock"}
```

- `SetVenueCredentials` 受領時、Python は session validation を実施し、必要なら 1 回だけ再ログインして、結果を `VenueReady{venue, request_id}` か `VenueError{venue, request_id, code, message}` で返す（`request_id` 相関で Rust 側が応答を突き合わせる）
- **`VenueReady` の意味論**: 「認証・session validation 完了」を意味する。**マスタ初期 DL の完了は含まない**。マスタ取得完了は `ListTickers` 応答の到着で判定する（F12）
- `VenueReady` は **冪等イベント**。Python 単独再起動 → `SetVenueCredentials` 再注入 → `VenueReady` 再送、というサイクルを毎回踏む。UI は初回 / 再送を区別しない前提（差異が必要になれば `session_id` 同梱で拡張）（F8）。Rust 側はこれを**最終受信状態**として保持し、**`ProcessManager` が Python サブプロセスの再起動を検知した時点（次の `Hello` 受信時）にリセット**する。`EngineEvent::Disconnected` は ticker/stream 粒度（`{venue, ticker, stream, market, reason}`、[engine-client/src/dto.rs:201](../../../engine-client/src/dto.rs#L201)）であって venue 全体の disconnected ではない点に注意（C3 修正）。WebSocket 切断などで全 ticker の `Disconnected` を受信しても `VenueReady` 状態は維持し、Python プロセス自体が落ちた時のみリセットする
- **`VenueReady` 再受信時の重複防止**: active subscriptions の resubscribe は `ProcessManager`（[engine-client/src/process.rs](../../../engine-client/src/process.rs)）が **1 度だけ** 行う。UI 側の view code は `VenueReady` イベントに反応して新規 subscribe を発行しないこと（既存購読の参照カウントは ProcessManager 経由でのみ維持）
- Rust 側は `VenueReady` 受領前は立花 ticker の `ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe` を送らない。UI では venue 単位のローディング表示を出す
- 既存 sidebar は起動直後に metadata fetch を自動発火するため、立花追加時は **venue-ready ゲート** を `AdapterHandles` 呼び出し前に差し込む必要がある

## 4. Python 側ファイル構成

```
python/engine/
├── exchanges/
│   ├── tachibana.py          # ExchangeWorker 実装
│   ├── tachibana_auth.py     # ログイン・session validation（起動時のみ呼ぶ）
│   ├── tachibana_url.py      # build_request_url（REQUEST 用 JSON クエリ）/ build_event_url（EVENT 用 key=value 形式）/ func_replace_urlecnode（SKILL.md R2/R9）
│   ├── tachibana_codec.py    # Shift-JIS デコード + parse_event_frame + deserialize_tachibana_list（空配列="" 正規化、SKILL.md R8）
│   ├── tachibana_master.py   # CLMEventDownload ストリームパース
│   └── tachibana_ws.py       # EVENT WebSocket クライアント（FD frame 中心、KP frame で死活監視）
python/tests/                   # ← 既存テストと同じディレクトリに集約（F5）
├── test_tachibana_url.py        # REQUEST と EVENT で URL 形式が違うこと（R2）も検証
├── test_tachibana_codec.py      # Shift-JIS 往復 + 空配列 "" → [] 正規化（R8）
├── test_tachibana_event_parse.py
├── test_tachibana_fd_trade.py   # 前 frame bid/ask による quote rule と 初回 frame 除外（F3/F4）
├── test_tachibana_login.py      # mock サーバ（pytest-httpx の HTTPXMock）
└── test_tachibana_e2e.py        # demo 環境を踏むのは @pytest.mark.demo_tachibana のみ
```

- 依存追加: 立花 API は標準 HTTP/WS なので新規依存ゼロ。Shift-JIS は Python 標準 `bytes.decode("shift-jis")` で足りる
- HTTP クライアントは既存 [python/engine/exchanges/binance.py](../../../python/engine/exchanges/binance.py) と同じく **`httpx`** に揃える。WS は同じく `websockets` を採用
- mock サーバは既存 [python/tests/](../../../python/tests/) と同一ツールチェーン（`pytest-httpx` の `HTTPXMock` フィクスチャ）に揃える。**`respx` は採用しない**（F15）。WS は `websockets.serve` でローカルサーバを立てて FD/KP frame を再生

## 5. Rust 側の変更箇所

| ファイル | 変更内容 |
| :--- | :--- |
| [exchange/src/adapter.rs](../../../exchange/src/adapter.rs) | `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` 追加。`FromStr` / `Display` / `ALL` 配列更新、および `MarketKind` を網羅する既存 match の修正 |
| [engine-client/src/dto.rs](../../../engine-client/src/dto.rs) | `Command::SetVenueCredentials` / `Command::RequestVenueLogin` / `EngineEvent::VenueReady` / `EngineEvent::VenueCredentialsRefreshed` / `EngineEvent::VenueLoginStarted` / `EngineEvent::VenueLoginCancelled` 追加。`schema_minor` を bump |
| [engine-client/src/process.rs](../../../engine-client/src/process.rs) | `ProcessManager` が Tachibana credentials を保持し、再起動時に `SetProxy` の後で `SetVenueCredentials` を再送する |
| `data/src/config/tachibana.rs`（新設） | `TachibanaCredentials` 型 + keyring 読み書き。SKILL.md R10 に従う。`data/src/config/proxy.rs` の暗号資産プロキシ keyring 実装を参考にする |
| [src/main.rs](../../../src/main.rs) | 起動時に keyring から立花 creds を復元し `SetVenueCredentials` 投入 |
| Rust UI（`src/screen/`） | **ログイン画面コードを追加しない**。Python ヘルパー spawn 中は「ログインダイアログを別ウィンドウで表示中」のステータスバナーだけ出す（汎用 string、立花知識なし） |
| `src/screen/dashboard/tickers_table.rs` ほか UI | `VenueReady` 前の metadata fetch を抑止し、`MarketKind::Stock` に応じた market filter / indicator / timeframe / 表示文言を調整 |
| [docs/plan/✅python-data-engine/](../✅python-data-engine/) `schemas/` | `commands.json` / `events.json` に新コマンド・イベントを記載、`CHANGELOG.md` 更新（※親計画ディレクトリ内のスキーマ。本計画 T0 で同期） |

## 6. 失敗モードと UI 表現

**文言の所在原則（F-Banner1）**: 立花起因のバナー文言は **Python 側の `VenueError.message` に込める**。Rust UI は受信した `message` をそのまま描画するだけで固定文言を持たない。`code` 値は severity 判定とアクションボタン（再ログイン / 閉じるのみ）の出し分けにのみ使う。

| 状態 | `VenueError.code` | バナー文言（Python が `message` に詰める例） | UI severity / アクション |
| :--- | :--- | :--- | :--- |
| クレデンシャル未設定 | （`VenueError` ではなく Rust の keyring 不在検出） | （Rust が固定文言でログイン誘導） | login 画面遷移 |
| 電話認証未済 | `phone_auth_required` | 「先に立花の電話認証を完了してください」 | error / 閉じる |
| 仮想 URL 期限切れ | `session_expired` | 「立花のセッションが切れました（夜間閉局）。再ログインしてください」 | error / 再ログイン |
| 未読通知あり | `unread_notices` | 「立花からの未読通知があります。ブラウザで確認後に再ログインしてください」 | warning / 再ログイン |
| 認証失敗 | `login_failed` | 「ログインに失敗しました。ID / パスワードを確認してください」 | error / 再ログイン |
| 銘柄コード未存在 | `ticker_not_found` | 「銘柄が見つかりません: 7203」 | warning / 閉じる |
| ザラ場時間外 | （`VenueError` ではなく `Disconnected{reason:"market_closed"}`） | — | チャートに「市場時間外」オーバーレイ |
| Python 再起動中 | （既存の `EngineRestarting` ステータスを流用） | — | [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 |

- Python は `message` を Shift-JIS デコード後の文字列で組み立てる。立花 API の `sResultText` / `sWarningText` が含まれる場合はそれをそのまま文末に括弧書き付与してよい
- Rust UI は **`message` をエスケープして 1 行で描画**（改行 / HTML タグは混入させない、CSS 側で折返し制御）
- `code` の一覧は [docs/plan/✅python-data-engine/schemas/events.json](../✅python-data-engine/schemas/events.json) の `VenueError.code` 列に enum として明記する（T0.2 で追記）

## 7. ログイン画面の Python 駆動（F-Login1）

### 7.1 設計原則

**ログイン関連の画面は Python 側が独立した GUI ウィンドウとして開く**。Rust 側 UI は立花のログインフォームを描画しない（iced 側にログイン画面コードを持たない）。Rust が知るのは「ログインが成功したか / 失敗したか / キャンセルされたか」だけ。

これにより:
- 立花アダプタを追加・改修するときに Rust UI を一切触らない
- バリデーション文言・補助テキスト・i18n の正本が Python 側 venue コード 1 箇所に集約
- 将来 venue が増えても Rust に GUI コードを追加しなくてよい
- Python 側のテストで GUI 周りも完結する

トレードオフ（**ユーザーが明示的に許容している**）:
- iced（メインウィンドウ）と Python GUI（ログインウィンドウ）の **2 つの windowing system が同時に走ることを許容**する。GUI 一貫性より「venue 固有 UI を venue コードに閉じ込めること」「将来の Python 単独モードへの移行コスト低減」を優先
- DPI スケール / テーマ / フォーカス挙動 / キーボードフォーカスの一貫性は best-effort（OS ネイティブの widget に揃える）
- Python 側に GUI 依存が増える（ヘッドレス環境でデータエンジン単体起動するケースで GUI コードがロードされないようにする工夫が必要）— `tachibana_login_dialog.py` は `python -m` でのみ起動し、データエンジン本体の import グラフには載せない

**将来の Python 単独モードでの再利用**: tkinter ヘルパー (`tachibana_login_dialog.py`) と認証フロー (`tachibana_login_flow.py`) は **Rust に依存しない**実装に保つ。Python 単独モードでは `tachibana_login_flow` を直接呼び、得られた creds で `tachibana_auth.login(...)` を実行する経路がそのまま使える。

### 7.2 GUI ライブラリ選定

**選定基準**:
- 追加バイナリサイズが小さいこと（データエンジン subprocess は軽量に保ちたい）
- Windows / macOS / Linux で動く
- 日本語 IME が問題なく動く
- パスワード入力のマスク表示
- モーダルダイアログとして使える
- メンテナンス活発・FOSS ライセンス
- asyncio イベントループとの共存（後述の subprocess 隔離で回避可能）

**比較**:

| ライブラリ | 追加サイズ | 日本語 IME | 評価 |
| :--- | :--- | :--- | :--- |
| **tkinter（Python 標準）** | 0（stdlib） | ◎ Win/Mac/Linux すべて native IME | **採用** |
| CustomTkinter | +約 5MB | ◎（tk 上の theming 層） | **オプション採用**（モダン外観が必要なら） |
| PySide6 / PyQt6 | +約 100MB | ◎ | ❌ Phase 1 では過剰 |
| Kivy | +約 50MB（GL 依存） | △ Windows IME に既知問題 | ❌ |
| DearPyGui | +約 10MB（GL 依存） | △ IME 検証情報少 | ❌ |
| Toga / BeeWare | +約 20MB | ○ | ❌ プラットフォームごとの安定性が未検証 |
| Flet | 別 runtime 必要 | ○ | ❌ ランタイム配布が複雑 |

**決定: tkinter（Python 標準ライブラリ）を採用**。理由:
- 追加依存ゼロ。pip install 不要、ビルド成果物サイズに影響しない
- Windows 11 / macOS / Linux で日本語 IME が問題なく動く
- 立花のログインに必要な部品（`Entry`、`Entry(show='*')`、`Checkbutton`、モーダル `Toplevel`、`messagebox`）はすべて標準
- 立ち上がりが早い（< 100ms）。ログインのような短命ダイアログに最適
- 見た目は地味だが、ログインダイアログは数秒〜数十秒しか表示されないので外観の優先度は低い

外観をモダンにしたくなった場合は **CustomTkinter（オプション）** を後付けで導入できる。tkinter API 互換のため移行コストは小さい。Phase 1 では未採用。

**却下したもの**:
- **Kivy**: OpenGL 依存と Windows での IME 問題（過去報告例あり）。立花ユーザーは日本語入力が必須なのでリスクが大きい
- **PySide6 / PyQt6**: バイナリ ~100MB は CLI / データエンジン subprocess としては過大
- **Flet**: Flutter ランタイム配布が必要で、配布パイプラインが複雑化

### 7.3 プロセスモデル: ログインヘルパー subprocess

tkinter は **メインスレッドでイベントループを回す**設計のため、データエンジンの asyncio ループと同居させると相互ブロックや IME 不安定の原因になる。これを避けるため:

- データエンジン本体（asyncio）は GUI を**直接開かない**
- ログインが必要になったら、データエンジンが **小さなログインヘルパー subprocess（`python -m engine.exchanges.tachibana_login_dialog`）を spawn** する
- ヘルパーは tkinter ループだけを回し、ユーザー入力を受け取ったら **stdout に JSON 1 行**で結果を返して終了
- データエンジンはヘルパーの stdout を `asyncio.create_subprocess_exec` で待ち受ける（非同期ブロックなし）

```
[Rust iced UI] ── IPC ──> [Python data engine (asyncio)] ── spawn ──> [Python login helper (tkinter)]
                                                                              │
                                                                              ▼
                                                               (ユーザーが入力 → JSON で返却)
```

ヘルパー I/F（標準入出力プロトコル）:

```
# stdin（最大 64KB の JSON 1 行で起動引数を渡す）
{
  "venue": "tachibana",
  "title": "立花証券 e支店 ログイン",
  "fields": [...],            // ※ ヘルパー側で UI 構築に使う宣言（venue ごとに自由形式）
  "prefill": {                // **env のみ**を出典とする（M5 決定）。直前 attempt の値は再 spawn 時にも使わない（メモリ滞在を最小化するため）
    "user_id": "123456789",   // `DEV_TACHIBANA_USER_ID` があれば埋める。無ければ空文字 / キー省略
    "is_demo": true,          // `DEV_TACHIBANA_DEMO` を反映、未設定時は `true` 既定。ヘルパー UI ではこれを **ラジオボタンの初期選択**として使う（M8）
    "allow_prod_choice": false // `TACHIBANA_ALLOW_PROD=1` のときのみ `true`。`true` のときヘルパーは「本番」ラジオを有効化、`false` のときは「デモ」固定でラジオ自体を非表示
  },
  "last_error": {             // 直前認証失敗時のみ
    "message": "ログインに失敗しました。ID / パスワードを確認してください",
    "field_errors": [{"field_id": "password", "message": "パスワードを再入力してください"}]
  }
}

# stdout（ユーザー操作結果）
# 成功
{"status": "submitted", "values": {"user_id": "...", "password": "...", "second_password": "...", "is_demo": true}}
# キャンセル / 閉じる
{"status": "cancelled"}
```

セキュリティ:
- ヘルパー subprocess のメモリ寿命は数秒〜数分。終了時に OS がページを回収する
- stdout で creds を返すため、**ヘルパー → データエンジン間は OS パイプ（同一ユーザー権限）に閉じる**。ログ出力は stderr 側のみ、creds は混ぜない
- データエンジン受信後、ヘルパーが書いた stdout バッファをすぐクリアし、`SecretStr` に wrap
- ログインヘルパーは **shebang や独立 .exe にはしない**。`python -m` 経由で同じ Python 実行系を再利用し、配布物を増やさない

### 7.4 Python 側ファイル追加

```
python/engine/exchanges/
├── tachibana_login_dialog.py    # tkinter ベースのログインダイアログ（python -m で起動）
├── tachibana_login_flow.py      # データエンジン側ロジック: ヘルパー spawn + 結果受信 + 認証実行
```

`tachibana_login_dialog.py` の責務:
- `sys.stdin` から起動 JSON を読む（フィールド定義・プリフィル値・直前エラー）
- tkinter で `Toplevel` モーダルを構築
- 立花固有のラベル・順序・警告ボックス（「電話認証を完了してから」「デモ環境警告」など）はこのファイル内に**直接記述**してよい（venue コード = venue 固有 UI が許される唯一の場所）
- **環境ラジオボタン（M8 決定）**: 「○ デモ環境（demo-kabuka.e-shiten.jp）」「○ 本番環境（kabuka.e-shiten.jp）⚠️ 実弾」の 2 択ラジオを user_id / password 入力欄の上に配置
  - `prefill.allow_prod_choice == false` のときは **本番ラジオを描画しない**（デモ固定の旨ラベル 1 行を出す）。**L2 修正**: `DEV_TACHIBANA_DEMO=false` を env で立てたが `TACHIBANA_ALLOW_PROD=1` を立てていない debug ユーザー向けに、ラベルを「**デモ環境固定（本番接続には `TACHIBANA_ALLOW_PROD=1` env が別途必要です）**」と明示し、`tachibana_login_flow.py` が起動時に同旨を `tracing::info!` で 1 行出す
  - `prefill.allow_prod_choice == true` のときは両ラジオを描画。本番選択時は警告色（赤系）と「実取引が発生します」モーダル `messagebox.askyesno` を「ログイン」押下時に挟み、二段確認させる
  - `prefill.is_demo` で初期選択を決定（既定 demo）。submit 時は選択値を `values.is_demo` に詰める
- 「ログイン」押下 → 値を JSON で `sys.stdout` に書き、`exit(0)`
- 「キャンセル」押下 / ウィンドウ閉じる → `{"status":"cancelled"}` を出して `exit(0)`
- バリデーション失敗時は tkinter のラベル赤表示 + `messagebox` でユーザーに通知、submit させない（データエンジンに送り返さない）

`tachibana_login_flow.py` の責務:
- ログインが必要になった条件を判定（session expired / keyring に creds 無し / debug プリフィル env 存在）
- 起動 JSON を組立てて `asyncio.create_subprocess_exec(sys.executable, "-m", "engine.exchanges.tachibana_login_dialog", stdin=PIPE, stdout=PIPE)`
- stdin 書込み・close → stdout 読込み・JSON parse
- `submitted` なら `tachibana_auth.login(...)` を呼び、結果に応じて `VenueReady` または再度ヘルパー spawn（最大 3 回）
- `cancelled` なら IPC で **`VenueLoginCancelled { venue }`** イベントを Rust に送る

### 7.5 IPC イベント / コマンドの整理

UI ツリー DSL（前案の `VenueLoginForm` / `VenueUiNode`）は廃止。Python が自前ウィンドウを持つので Rust に UI 構造を渡す必要がない。

新規イベント / コマンド:

```rust
pub enum EngineEvent {
    // ...
    /// Python がログインヘルパーを起動した（Rust UI は「ログインダイアログを別ウィンドウで表示中」状態）
    VenueLoginStarted { venue: String, request_id: Option<String> },
    /// ユーザーがダイアログをキャンセルした
    VenueLoginCancelled { venue: String, request_id: Option<String> },
    /// 既存（§2）— ログイン成功時に発火
    VenueReady { venue: String, request_id: Option<String> },
    VenueError { venue: String, request_id: Option<String>, code: String, message: String },
    VenueCredentialsRefreshed { venue: String, session: TachibanaSessionWire },
}

pub enum Command {
    // ...
    /// Rust UI が「立花にログインしたい」と表明する。Python はヘルパーを spawn して応答する
    RequestVenueLogin { request_id: String, venue: String },
}
```

**`Command::SetVenueCredentials` の位置付け変更（重要）**:
- 旧計画では Rust UI が入力値を受け取って `SetVenueCredentials` に詰めて送っていた
- 新計画では **Python ヘルパーが直接 creds を受け取る**ため、Rust UI が creds を扱わない経路が増える
- ただし **keyring 復元経路は引き続き `SetVenueCredentials` を使う**（Rust が keyring から読んだ creds を Python に注入する起動時パスは変わらない）
- Python 単独再起動時の credentials 再注入も `SetVenueCredentials`。**ProcessManager は keyring 復元 creds を保持し再送する**（既存方針 [README.md](./README.md) 維持）

`SetVenueCredentials` の典型的な発火タイミングが「(a) 起動時に keyring から復元」「(b) Python 再起動後の再注入」の 2 つに集約される（runtime のユーザー入力経路は使わない）。

### 7.6 起動シーケンス（更新版）

```
Rust 起動
  ├─ keyring から TachibanaCredentials を読む（任意）
  ├─ Python サブプロセス spawn → Hello → Ready
  ├─ SetProxy（必要時）
  ├─ 分岐 ───
  │   ├─ keyring に creds あり: SetVenueCredentials 即送信
  │   │     ├─ Python: session validation 成功 → VenueReady
  │   │     └─ Python: session 失敗 → tachibana_login_flow を起動 → tkinter ヘルパー spawn
  │   │              → 認証成功 → VenueCredentialsRefreshed → keyring 更新 → VenueReady
  │   │              → キャンセル → VenueLoginCancelled
  │   └─ keyring に creds 無し: 何も送らない
  │         （Rust UI が立花機能に触れたとき RequestVenueLogin を送り、上のフローに合流）
  └─ ListTickers / Subscribe（VenueReady 後）
```

**ループ規約（再ログイン時）**:
1. 認証失敗 → `tachibana_login_flow` がヘルパーを再 spawn し直前エラー文言を渡す（最大 3 回）
2. 3 回失敗 → `VenueError{code:"login_failed"}` で諦める。Rust UI はバナー表示
3. ユーザーが任意のタイミングでキャンセル → `VenueLoginCancelled` → Rust UI は「立花未ログイン」状態を維持

### 7.7 debug ビルドの env 自動入力との整合

- `DEV_TACHIBANA_*` env は **Python 側 `tachibana_login_flow` が読む**（Rust 経由ではない）
- env が揃っている場合は **ヘルパーを spawn せず**、env 値で直接 `tachibana_auth.login(...)` を実行する fast path を入れる
- env が一部欠損なら、欠けた項目だけプリフィルされた状態でヘルパーを表示する
- `is_demo` 既定値は `True` 強制（env 未指定時）。SKILL.md R1 の実弾事故防止
- **Phase 1 で採用する env 名は 3 つのみ**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO`（venue prefix 付き）。**`DEV_TACHIBANA_SECOND_PASSWORD` は Phase 1 では採用しない**（F-H5: 第二暗証番号は収集も保持もしないため env 経路に存在させる必要がない）。`tachibana_login_flow` 内で `os.getenv("DEV_TACHIBANA_SECOND_PASSWORD")` 等の呼出を書かないことを実装規約とする。Phase 2（発注）着手時に env 名を改めて確定する

**プリフィルの出典は env のみ（M5 決定）**:
- ヘルパー `prefill.user_id` は `DEV_TACHIBANA_USER_ID` の値だけを反映する。**直前 spawn でユーザーが入力した user_id は次の spawn に持ち回らない**
- 理由は (a) creds をデータエンジン側のメモリに長時間滞在させない、(b) 「失敗時の再 spawn」と「キャンセル後の手動再ログイン」で挙動を分岐させない単純化、(c) Python 単独モード移行時にも同じ規約で済むため
- 結果として認証 3 回失敗時のリトライ UX は「user_id を毎回 env から再投入（または手で再入力）」となる。env を設定済みのユーザーは fast path で抜けるため再 spawn 自体ほぼ走らない
- パスワードは **prefill に絶対載せない**（env fast path で消費するか、ヘルパー初期表示は空欄）。第二暗証番号は Phase 1 では入力欄自体を表示しない（F-H5）


## 8. テスト戦略

### 8.1 単体（Python）

- URL 組立 — REQUEST 用 `build_request_url`（`?{JSON 文字列}` 形式、SKILL.md R2）と EVENT 用 `build_event_url`（`?key=value&...` 形式、R2 例外）を**別関数として**実装し、サンプル `e_api_login_tel.py` / `e_api_event_receive_tel.py` の出力とバイト一致確認
- `func_replace_urlecnode` の置換 30 文字（SKILL.md R9）— サンプルと 1 対 1 一致テスト
- Shift-JIS デコード往復（銘柄名・エラーメッセージの代表サンプル含む）
- `parse_event_frame` — 制御文字 `^A^B^C` 分解と `<型>_<行>_<情報コード>` キー抽出
- 空配列 `""` → `[]` 正規化（SKILL.md R8）— 注文ゼロ件レスポンスのフィクスチャで検証
- `p_errno` / `sResultCode` 二段判定（`p_errno` 空文字＝正常も含む、SKILL.md R6）
- マスタ `CLMEventDownload` ストリームパース（チャンク境界 / `CLMEventDownloadComplete` 終端検知）

### 8.2 結合（Python + mock サーバ）

- **`pytest-httpx`**（`HTTPXMock` フィクスチャ）でデモサーバを擬似化。既存 [python/tests/test_binance_rest.py](../../../python/tests/test_binance_rest.py) のパターンを踏襲
  - `e_api_login_tel.py/e_api_login_response.txt` を fixture として再利用
  - 異常系: `p_errno=-62` (時間外) / `p_errno=2` (セッション切れ) / `sKinsyouhouMidokuFlg=1` (未読通知)
  - WebSocket は `websockets` の `serve` でローカルサーバを立てて FD/KP frame を再生

### 8.3 結合（Rust + Python）

- `tests/integration/tachibana_handshake.rs`: `SetVenueCredentials` → `VenueReady` の往復
- `tests/integration/tachibana_subscribe.rs`: trade / depth subscribe → mock の FD frame → `Trades` / `DepthSnapshot` 受信
- 既存の Rust 単体は mockito を使う（プロジェクト共通方針）

### 8.4 E2E（demo 環境）

- `pytest -m demo_tachibana` でのみ実行（CI 既定では skip）
- 実 demo 環境 → ログイン → マスタ取得 → 任意銘柄 subscribe → 10 件以上の trade を受信して切断、までを 1 分以内に完了

### 8.5 シークレット流出ガード

- リポジトリ全体に対する pre-commit secret scan は `tools/secret_scan_patterns.txt` を正本とし、`tools/secret_scan.{sh,ps1}` から呼び出す。本ドキュメントでは grep リテラルを重複定義しない（重複による drift 防止）。詳細・正本パターンは [implementation-plan.md T7](./implementation-plan.md) 参照
- ログキャプチャテストで `sPassword` / `sSecondPassword` / 仮想 URL ホスト部分が出ないこと
