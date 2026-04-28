# 立花証券統合: アーキテクチャ

## 1. 配置原則

[docs/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §2 の責務分割を踏襲:

| 責務 | 所在 | 備考 |
| :--- | :--- | :--- |
| ユーザー ID / is_demo の保存 | **Python**（`tachibana_account.json`） | `config_dir` 配下。password はファイルに書かない |
| パスワードの保持 | **Python メモリのみ**（tkinter ダイアログ入力 or debug env） | ディスクには書かない |
| 電話認証完了の前提条件 | ユーザー操作（手動） | アプリは関与しない |
| `CLMAuthLoginRequest` 実行と仮想 URL 5 種の取得 | **Python** | Rust の関与ゼロ |
| 仮想 URL（セッション）の保持 | **Python**（メモリ + `tachibana_session.json`） | `cache_dir` 配下。JST 当日のもののみ fresh、broker 真の有効性は API validate に委譲 |
| マスタダウンロード（21MB ストリーム） | **Python** | 起動時 1 回 + 日次。`sJsonOfmt="4"` |
| FD frame パース（Shift-JIS / 制御文字分解） | **Python** | `parse_event_frame` 相当を Python 実装 |
| 板生成（FD 駆動が正、REST は補助） | **Python** | FD frame ごとに `DepthSnapshot` を再生成。`CLMMfdsGetMarketPrice` polling は (a) ザラ場前後初回 / (b) FD WS 12 秒無通信時の再接続中フォールバック / (c) `depth_unavailable` セーフティ発動時の 3 ケースに限定（spec.md §2.1 / §3.3 と整合、runtime 定期 polling は不可） |
| `p_no` 採番 / `p_sd_date` 生成 | **Python** | プロセス内 `AtomicU64` 相当 + JST chrono |
| `p_errno` / `sResultCode` 二段判定 | **Python** | `EngineError` または `Error` イベントへマップ |
| **ログイン画面の描画**（独立ウィンドウ、tkinter） | **Python**（`tachibana_login_dialog.py`、subprocess 隔離） | F-Login1、§7。Rust は描画コードを持たない |
| **ログイン画面の発火タイミング判定** | **Python**（`tachibana_login_flow.py`、`startup_login`） | session 失効・ファイルキャッシュなし・debug env 検知時に Python ヘルパーを spawn |
| **ログイン入力値の収集** | **Python**（tkinter ヘルパー → stdout JSON） | creds は Rust 経路を通らない |
| バナー文言（`VenueError.message`） | **Python** | Rust UI は `message` をそのまま描画（F-Banner1、§6） |
| UI のフレーム（チャート / ticker selector / バナー枠 / ログインフォーム枠） | **Rust** | 既存 iced レイアウトを流用 |

**Rust 直結（NativeBackend）は実装しない**。立花統合は最初から `EngineClientBackend` のみで成立させる。これにより [docs/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §7.1 案 A（完全撤去）の方針と一貫する。

**長期方針（[README.md](./README.md) §「長期方針」と整合）**: 将来 Rust（iced）を使わない **Python 単独モード**を新設する予定。Phase 1 で Python 側に置く venue 固有実装（認証・パース・tkinter ログイン UI・バナー文言）は **Python-only モードでもそのまま再利用できる構造**にする。設計判断で迷ったら「Python 単独でも動くか？」を判断軸に使う。

> **補足**: SKILL.md は `exchange/src/adapter/tachibana.rs` を実在する参考実装として記述しているが、本リポジトリには存在しない（git 履歴上も未確認）。本計画は Rust adapter を**新設しない**（Python 側に集約）方針なので、SKILL.md L41/L431 の Rust ヘルパー参照は**仕様の抽象記述として読み替える**こと。

## 2. Python 自律ログイン方式（session-file-cache 適用後）

立花のクレデンシャル・セッション管理は **Python に完全に閉じる**。Rust は creds / session を一切保持しない。`SetVenueCredentials` IPC コマンドおよび `VenueCredentialsRefreshed` IPC イベントは削除済み。

### 2.1 ファイルキャッシュ構成

Python は OS ユーザーディレクトリ配下の 2 ファイルで認証情報を管理する:

| ファイル | 場所 | 内容 |
| :--- | :--- | :--- |
| `tachibana_account.json` | `config_dir/` | `user_id` + `is_demo`（password は書かない） |
| `tachibana_session.json` | `cache_dir/` | 仮想 URL 5 種 + `saved_at_ms` |

```json
// tachibana_account.json
{ "user_id": "12345678", "is_demo": true }

// tachibana_session.json
{
  "url_request":      "https://demo-kabuka.e-shiten.jp/e_api_v4r8/xxxxxx/",
  "url_master":       "...",
  "url_price":        "...",
  "url_event":        "...",
  "url_event_ws":     "...",
  "zyoutoeki_kazei_c": "1",
  "saved_at_ms":      1745712000000
}
```

**新鮮判定（`_is_session_fresh`）**: `saved_at_ms` が JST 当日のもののみ有効。`saved_at_ms > now_ms`（クロックスキュー）は保守的に無効扱い。broker 側の真の有効期限（夜間閉局など）は `validate_session_on_startup` の API 呼出に委ねる（spec L81: "session 検証が失敗した場合のみ再ログイン"）。**旧仕様の 15:30 JST cutoff は廃止**（夕方ログイン後の再起動でも再ログインを強要されないようにするため、2026-04-27 修正）。

**原子書き込み**: `tempfile.mkstemp` + `os.replace` でアトミックに書き込む（Windows/Unix 両対応）。

### 2.1.1 起動パラメータ

`stdin` 初期 payload 形式（Rust → Python）:

```json
{"port": N, "token": "...", "config_dir": "...", "cache_dir": "...", "dev_tachibana_login_allowed": bool}
```

- `config_dir`: `tachibana_account.json` の保存先
- `cache_dir`: `tachibana_session.json` の保存先
- `dev_tachibana_login_allowed`: `true`（debug ビルド）のときのみ Python が `DEV_TACHIBANA_*` env を読む。Rust 側は `#[cfg(debug_assertions)]` で制御し、release では必ず `false` を渡す

### 2.2 ログ・テレメトリでのマスク

- Python 側は `tachibana_session.json` の URL をログに出力しない（仮想 URL はホスト部まで `***` マスク）
- `tachibana_account.json` の `user_id` はログ出力しても可（公開情報に近い識別子）
- IPC のシリアライズ時にマスクは行わない（loopback + token 認証で守る）

**`EngineConnection: Debug` 規約（pin: T35-H7-DebugRedaction）**: `engine_client::EngineConnection` の `Debug` 実装は **`finish_non_exhaustive()` のみ**を使い、内部フィールドを直接書き出す `#[derive(Debug)]` 派生は禁止。リグレッションは `engine-client/tests/engine_connection_debug_redaction.rs` で pin 済み。

### 2.3 セッション永続化

Python が起動時の session 検証または初回ログインに成功したら、**`tachibana_session.json` をアトミック上書き**する。Rust への逆送（`VenueCredentialsRefreshed`）は不要。Python が source of truth であるため、Rust は session を持たない。

- `VenueError{code:"session_expired"}` は runtime の `p_errno="2"` 検知時に発出する
- 旧 `EngineError{code:"tachibana_session_expired"}` 表記は廃止。venue-scoped `VenueError` に一本化する

### 2.4 再起動時の source of truth

- **Python プロセスが自律的に `startup_login` を再実行**する。Rust は credentials を保持せず、再注入もしない
- 再起動後の正式シーケンスは次の通り:
  1. `Hello -> Ready`
  2. `SetProxy`（必要時）
  3. Python が自律的に `_startup_tachibana` → `startup_login` を実行
  4. Python が `VenueReady{venue:"tachibana"}` を送信（**同期点**）
  5. Rust が `VenueReady` を受信 → metadata fetch 再開
  6. active subscriptions 再送
- これにより [docs/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 の「recovery handshake 後に購読再送」という既存契約に、立花の認証状態を安全に差し込める

## 3. 起動シーケンス

```
Rust 起動
  ├─ Python サブプロセス spawn（既存 src/main.rs のフロー）
  ├─ Hello → Ready 受領
  ├─ SetProxy（必要時）
  ├─ [Python 自律] _startup_tachibana → startup_login
  │     ├─ tachibana_session.json を確認
  │     │     ├─ 有効 → validate_session_on_startup（API ping）→ 成功 → 以下へ
  │     │     └─ 無効 / なし → tachibana_account.json を確認 → tkinter ダイアログ
  │     │                     → ログイン → session/account ファイルを保存
  │     └─ VenueReady{venue:"tachibana"}  ← Python から Rust へ
  ├─ ListTickers{venue:"tachibana", market:"stock"}
  └─ Subscribe{venue:"tachibana", ticker:"7203", stream:"trade"|"depth", market:"stock"}
```

- Python は handshake 完了後に自律的に `_startup_tachibana` を開始する。Rust は `VenueReady` を venue 文字列 `"tachibana"` で待つ（`venue_ready_timeout` 60 秒以内）
- **`VenueReady` の意味論**: 「認証・session validation 完了」を意味する。**マスタ初期 DL の完了は含まない**。マスタ取得完了は `ListTickers` 応答の到着で判定する（F12）
- `VenueReady` は **冪等イベント**。Python 単独再起動 → `startup_login` 再実行 → `VenueReady` 再送、というサイクルを毎回踏む。UI は初回 / 再送を区別しない前提（差異が必要になれば `session_id` 同梱で拡張）（F8）。Rust 側はこれを**最終受信状態**として保持し、**`ProcessManager` が Python サブプロセスの再起動を検知した時点（次の `Hello` 受信時）にリセット**する。`EngineEvent::Disconnected` は ticker/stream 粒度（`{venue, ticker, stream, market, reason}`、`engine-client/src/dto.rs::EngineEvent::Disconnected`）であって venue 全体の disconnected ではない点に注意（C3 修正）。WebSocket 切断などで全 ticker の `Disconnected` を受信しても `VenueReady` 状態は維持し、Python プロセス自体が落ちた時のみリセットする
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
│   └── tachibana_ws.py       # EVENT WebSocket クライアント（FD frame 中心、KP frame で死活監視）— 実装済み（T5 で tachibana.py に配線）
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
| [engine-client/src/dto.rs](../../../engine-client/src/dto.rs) | `Command::RequestVenueLogin` / `EngineEvent::VenueReady` / `EngineEvent::VenueLoginStarted` / `EngineEvent::VenueLoginCancelled` / `EngineEvent::VenueError` 追加。`SCHEMA_MAJOR = 2`（`SetVenueCredentials` / `VenueCredentialsRefreshed` 削除は破壊的変更のため major を bump） |
| [engine-client/src/process.rs](../../../engine-client/src/process.rs) | `apply_after_handshake_with_timeout` から `SetVenueCredentials` 送信ステップを削除。`VenueReady` を venue 文字列 `"tachibana"` で待つ方式に変更。`credentials_by_venue` 保持フィールドを削除 |
| [src/main.rs](../../../src/main.rs) | keyring 復元・`SetVenueCredentials` 投入コードを削除。Python が自律起動するため Rust の関与不要 |
| Rust UI（`src/screen/`） | **ログイン画面コードを追加しない**。Python ヘルパー spawn 中は「ログインダイアログを別ウィンドウで表示中」のステータスバナーだけ出す（汎用 string、立花知識なし） |
| `src/screen/dashboard/tickers_table.rs` ほか UI | `VenueReady` 前の metadata fetch を抑止し、`MarketKind::Stock` に応じた market filter / indicator / timeframe / 表示文言を調整。**抑止は `src/venue_state.rs::VenueState` FSM が前提**（`Trigger::{Auto,Manual}` で auto-fire と手動再ログインを区別、`engine_status_stream` は `tokio::select!` 1 本に singleton 化）。pin: T35-U4-VenueReadyGate / T35-H9-SingleRecoveryPath（リグレッションは `tests/engine_status_subscription_is_singleton.rs` で固定） |
| `engine-client/src/tachibana_meta.rs`（新設） | `TickerDisplayMeta` 型・`parse_tachibana_ticker_dict`・`matches_tachibana_filter`（HIGH-U-9 / T4-B5 着地済み） |
| `engine-client/src/backend.rs`（既存） | `ticker_meta: Arc<Mutex<TickerMetaMap>>` フィールド・`ticker_meta_handle()`・`reset_ticker_meta()`（HIGH-U-9 着地済み） |
| `src/screen/dashboard/tickers_table.rs`（既存） | `filtered_rows` への `matches_tachibana_filter` 組込み（T4-B5 着地済み） |
| [docs/✅python-data-engine/](../✅python-data-engine/) `schemas/` | `commands.json` / `events.json` に新コマンド・イベントを記載、`CHANGELOG.md` 更新（※親計画ディレクトリ内のスキーマ。本計画 T0 で同期） |
| ~~`data/src/config/tachibana.rs`~~（削除済み） | `TachibanaCredentials` 型 + keyring 操作コードは Python 自律管理方式への移行で全削除 |
| ~~`data/src/wire/tachibana.rs`~~（削除済み） | `TachibanaCredentialsWire` / `TachibanaSessionWire` は IPC から creds 送受信を廃止したため全削除 |

## 6. 失敗モードと UI 表現

**文言の所在原則（F-Banner1）**: 立花起因のバナー文言は **Python 側の `VenueError.message` に込める**。Rust UI は受信した `message` をそのまま描画するだけで固定文言を持たない。`code` 値は severity 判定とアクションボタン（再ログイン / 閉じるのみ）の出し分けにのみ使う。

| 状態 | `VenueError.code` | バナー文言（Python が `message` に詰める例） | UI severity / アクション |
| :--- | :--- | :--- | :--- |
| クレデンシャル未設定 / 初回起動 | （`VenueError` ではなく Python が自律的に tkinter ダイアログを表示） | （Python が `VenueLoginStarted` を先送りし、ダイアログでユーザーに入力させる） | `VenueLoginStarted` → ダイアログ |
| 電話認証未済 | `phone_auth_required` | 「先に立花の電話認証を完了してください」 | error / 閉じる |
| 仮想 URL 期限切れ | `session_expired` | 「立花のセッションが切れました（夜間閉局）。再ログインしてください」 | error / 再ログイン |
| 未読通知あり | `unread_notices` | 「立花からの未読通知があります。ブラウザで確認後に再ログインしてください」 | warning / 再ログイン |
| 認証失敗 | `login_failed` | 「ログインに失敗しました。ID / パスワードを確認してください」 | error / 再ログイン |
| 銘柄コード未存在 | `ticker_not_found` | 「銘柄が見つかりません: 7203」 | warning / 閉じる |
| ザラ場時間外 | （`VenueError` ではなく `Disconnected{reason:"market_closed"}`） | — | チャートに「市場時間外」オーバーレイ |
| Python 再起動中 | （既存の `EngineRestarting` ステータスを流用） | — | [docs/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 |

- Python は `message` を Shift-JIS デコード後の文字列で組み立てる。立花 API の `sResultText` / `sWarningText` が含まれる場合はそれをそのまま文末に括弧書き付与してよい
- Rust UI は **`message` をエスケープして 1 行で描画**（改行 / HTML タグは混入させない、CSS 側で折返し制御）
- `code` の一覧は [docs/✅python-data-engine/schemas/events.json](../✅python-data-engine/schemas/events.json) の `VenueError.code` 列に enum として明記する（T0.2 で追記）

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
# MEDIUM-6 (ラウンド 7): 実装は v0.8.7 時点で `status="ok"` + フラット
# な user_id / password / is_demo 形式に統一されている（second_password
# は Phase 1 では収集しない／F-H5 不変条件に従う）。仕様書も実装に
# 合わせる — テスト群（`test_tachibana_login_dialog_modes.py`、
# `test_tachibana_login_helper_broken_pipe.py` 他）は平坦形式に固定
# されており、`submitted` + ネスト `values:{}` 形式は v0.7.x 互換層
# として残置していない。
# 成功
{"status": "ok", "user_id": "...", "password": "...", "is_demo": true}
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
- ログインが必要になった条件を判定。**起動条件は spec.md §3.2 LOW-3 と整合する**:
  - (a) アプリ起動直後の session 検証フェーズで `tachibana_session.json` が無い / 復元 session の validate が失敗した場合（fast-path、ユーザー操作なしで起動して可）
  - (b) Rust UI が `Command::RequestVenueLogin` を送信した場合（ユーザー明示の再ログイン）
  - (c) debug ビルドで `DEV_TACHIBANA_*` env が揃っている場合の fast-path（ヘルパー spawn せず直接 `tachibana_auth.login(...)`）
  - **runtime 中に `p_errno=2` を検知してもこのフローは起動しない**（`VenueError{code:"session_expired"}` を返すのみ。Rust UI が `RequestVenueLogin` を送ってきたら (b) 経路に合流）
- 起動 JSON を組立てて `asyncio.create_subprocess_exec(sys.executable, "-m", "engine.exchanges.tachibana_login_dialog", stdin=PIPE, stdout=PIPE)`
- stdin 書込み・close → stdout 読込み・JSON parse
- `status == "ok"` なら `tachibana_auth.login(...)` を呼び、結果に応じて `VenueReady` または再度ヘルパー spawn（最大 3 回）
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
    /// ログイン成功時に発火
    VenueReady { venue: String, request_id: Option<String> },
    VenueError { venue: String, request_id: Option<String>, code: String, message: String },
    // VenueCredentialsRefreshed は削除済み（Python が tachibana_session.json で自前永続化）
}

pub enum Command {
    // ...
    /// Rust UI が「立花にログインしたい」と表明する。Python はセッションをクリアして startup_login を再実行する
    RequestVenueLogin { request_id: String, venue: String },
    // SetVenueCredentials は削除済み（Python が自律起動するため Rust からの creds 注入は不要）
}
```

**Python 自律ログイン方式の IPC コントラクト**:
- Rust が creds / session を保持・送信することはない
- Python は handshake 後に自律的に `startup_login` を実行し、結果を `VenueReady` または `VenueError` で返す
- ユーザーが再ログインを要求した場合は `Command::RequestVenueLogin` のみを使う。Python はこれを受けてセッションをクリアし `startup_login` を再実行する
- `SCHEMA_MAJOR = 2`（`SetVenueCredentials` / `VenueCredentialsRefreshed` 削除は破壊的変更）

#### 7.5.1 Rust UI bridge（DTO ↔ Iced Message ↔ FSM ↔ view）

DTO 列挙（`engine_client::dto::EngineEvent::{VenueReady,VenueLoginStarted,VenueLoginCancelled,VenueError}`）は Rust UI 入口に **`Message::TachibanaVenueEvent` として 1 本化**して入る。**T4 着手者が辿るブリッジ層は以下の path::symbol で固定**:

| 層 | path::symbol | 役割 |
| :--- | :--- | :--- |
| 受信ストリーム | `src/main.rs::Flowsurface::engine_status_stream`（`tokio::select!` 1 箇所、pin: T35-H9-SingleRecoveryPath） | engine status と venue event を単一 stream で受け、`Message::TachibanaVenueEvent(VenueEvent)` を発火 |
| Iced Message 入口 | `src/main.rs::Flowsurface::Message::TachibanaVenueEvent` / `Message::RequestTachibanaLogin` / `Message::DismissTachibanaBanner` | UI 起点（ログインボタン押下 → `RequestTachibanaLogin` → `Command::RequestVenueLogin`、バナー閉じる → `DismissTachibanaBanner`） |
| FSM | `src/venue_state.rs::{VenueState, Trigger, VenueEvent}` | DTO → Trigger 変換 + 状態遷移（`Idle → LoggingIn → Ready` / `Failed`）。`Trigger::{Auto,Manual}` で auto-fire と手動再ログインを区別 |
| view | `src/widget/venue_banner.rs::view` / `src/screen/dashboard/tickers_table.rs::exchange_filter_btn` | バナー描画と venue filter ボタン。`VenueState::Ready` 前は exchange_filter_btn の立花選択肢を disabled 表示 |

**流路（不変）**: Python `EngineEvent` → `engine_status_stream`（`tokio::select!`）→ `Message::TachibanaVenueEvent` → `VenueState` FSM → `venue_banner::view` / `tickers_table::exchange_filter_btn`。逆方向は `view` の on_press → `Message::RequestTachibanaLogin` → `Command::RequestVenueLogin` のみ（pin: T35-U1-LoginButton / T35-U3-AutoRequestLogin / T35-U2-Banner / T35-U4-VenueReadyGate）。

### 7.6 起動シーケンス（更新版）

```
Rust 起動
  ├─ Python サブプロセス spawn → Hello → Ready
  ├─ SetProxy（必要時）
  └─ [Python 自律] _startup_tachibana → startup_login
        ├─ tachibana_session.json 確認 → 有効 → validate_session_on_startup → 成功 → VenueReady
        └─ 無効 / なし → tachibana_account.json 確認 → tkinter ヘルパー spawn
                 → 認証成功 → account/session ファイル保存 → VenueReady
                 → キャンセル → VenueLoginCancelled
       （Rust は VenueReady を venue:"tachibana" で最大 60 秒待つ）
     ListTickers / Subscribe（VenueReady 後）

再ログイン時
  Rust UI → RequestVenueLogin → Python: session クリア → startup_login 再実行 → 同上フロー
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
- **採用する env 名は 3 つのみ**: `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO`（venue prefix 付き）。**`DEV_TACHIBANA_SECOND_PASSWORD` はログイン時（Phase 1 / Phase O0 以降とも）採用しない**（F-H5: **Phase O0 でも解除しない**。発注時は iced modal で取得・メモリのみ保持する方式に変更。env 経路は採用しない）。`tachibana_login_flow` 内で `os.getenv("DEV_TACHIBANA_SECOND_PASSWORD")` 等の呼出を書かないことを実装規約とする

**プリフィルの出典は env のみ（M5 決定）**:
- ヘルパー `prefill.user_id` は `DEV_TACHIBANA_USER_ID` の値だけを反映する。**直前 spawn でユーザーが入力した user_id は次の spawn に持ち回らない**
- 理由は (a) creds をデータエンジン側のメモリに長時間滞在させない、(b) 「失敗時の再 spawn」と「キャンセル後の手動再ログイン」で挙動を分岐させない単純化、(c) Python 単独モード移行時にも同じ規約で済むため
- 結果として認証 3 回失敗時のリトライ UX は「user_id を毎回 env から再投入（または手で再入力）」となる。env を設定済みのユーザーは fast path で抜けるため再 spawn 自体ほぼ走らない
- パスワードは **prefill に絶対載せない**（env fast path で消費するか、ヘルパー初期表示は空欄）。第二暗証番号はログインダイアログに入力欄を追加しない（F-H5: **Phase O0 以降も解除しない**。発注時は iced modal で取得・メモリのみ保持）。Rust は modal の入力値を `Command::SetSecondPassword` として Python に送信し、Python 側は値を `SecretStr` でラップしてメモリ保持する（idle forget タイマーで自動消去、architecture.md §5.3）。


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

- `engine-client/tests/handshake.rs` / `engine-client/tests/process_venue_ready_gate.rs` / `engine-client/tests/process_venue_ready_timeout_marks_failed.rs`: Python 自律 `startup_login` → `VenueReady` 受信、ゲート、タイムアウト失敗（実シンボル: `engine_client::process::ProcessManager`）
- `engine-client/tests/process_venue_login_cancelled.rs` / `engine-client/tests/process_venue_error_session_restore_failed.rs`: ログインキャンセル・session 復元失敗時の状態遷移
- trade / depth subscribe → mock の FD frame → `Trades` / `DepthSnapshot` 受信は Python 側 `python/tests/test_tachibana_e2e.py` および Rust 側 `engine-client/tests/depth_gap.rs` / `engine-client/tests/depth_gap_recovery.rs` で代替（Rust 単独の `tests/integration/tachibana_subscribe.rs` は新設しない）
- 既存の Rust 単体は mockito を使う（プロジェクト共通方針）
- ~~`engine-client/tests/process_creds_refresh_hook.rs`~~ / ~~`engine-client/tests/process_creds_refresh_listener_singleton.rs`~~（削除済み）: `VenueCredentialsRefreshed` は廃止済み
- ~~`engine-client/tests/schema_v1_2_roundtrip.rs`~~（`SetVenueCredentials` / `VenueCredentialsRefreshed` テスト関数のみ削除）: ファイル自体は v1.x 互換テストとして維持

### 8.4 E2E（demo 環境）

- `pytest -m demo_tachibana` でのみ実行（CI 既定では skip）
- 実 demo 環境 → ログイン → マスタ取得 → 任意銘柄 subscribe → 10 件以上の trade を受信して切断、までを 1 分以内に完了

### 8.5 シークレット流出ガード

- リポジトリ全体に対する pre-commit secret scan は `tools/secret_scan_patterns.txt` を正本とし、`tools/secret_scan.{sh,ps1}` から呼び出す。本ドキュメントでは grep リテラルを重複定義しない（重複による drift 防止）。詳細・正本パターンは [implementation-plan.md T7](./implementation-plan.md) 参照
- ログキャプチャテストで `sPassword` / `sSecondPassword` / 仮想 URL ホスト部分が出ないこと
