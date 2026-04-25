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
| UI（チャート / ticker selector / バナー） | **Rust** | 既存仕組みをそのまま流用 |

**Rust 直結（NativeBackend）は実装しない**。立花統合は最初から `EngineClientBackend` のみで成立させる。これにより [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §7.1 案 A（完全撤去）の方針と一貫する。

> **補足**: SKILL.md は `exchange/src/adapter/tachibana.rs` を実在する参考実装として記述しているが、本リポジトリには存在しない（git 履歴上も未確認）。本計画は Rust adapter を**新設しない**（Python 側に集約）方針なので、SKILL.md L41/L431 の Rust ヘルパー参照は**仕様の抽象記述として読み替える**こと。

## 2. クレデンシャル受け渡しのプロトコル拡張

既存 IPC（[engine-client/src/dto.rs](../../../engine-client/src/dto.rs)）には `SetProxy` しかない。立花のクレデンシャルを安全に渡すため、**`SetVenueCredentials` コマンドを新設**する。

### 2.1 新設 IPC コマンド

```rust
// engine-client/src/dto.rs
pub enum Command {
    // ... 既存 ...
    SetVenueCredentials {
        venue: String,           // "tachibana"
        credentials: serde_json::Value,  // venue 固有 shape
    },
}
```

立花の `credentials` shape:

```json
{
  "user_id": "...",
  "password": "...",
  "second_password": "...",
  "is_demo": true,
  "session": {
    "url_request": "...",
    "url_master": "...",
    "url_price": "...",
    "url_event": "...",
    "url_event_ws": "...",
    "expires_at_ms": 1735689600000,
    "session_token": "..."
  }
}
```

- `session` は **Rust が keyring から復元できた場合のみ** 含める。Python はまず `session` を試し、`p_errno="2"` で失敗したら `user_id/password` で再ログインする
  - この再ログインは **起動直後の `SetVenueCredentials` 処理中に限る**。購読開始後の runtime で session expiry を検知した場合は再ログインせず `tachibana_session_expired` を返す
- `second_password` は **Phase 2 以降の発注機能で使う**。Phase 1 では受け取って Python メモリに保持するだけ（漏らさない）
- このコマンドは **`Ready` 受信後・任意の `Subscribe` 前** に送る（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.5 起動ハンドシェイク）

### 2.1.1 起動パラメータとの責務分離

- 現行の Python engine 起動パス（[engine-client/src/process.rs](../../../engine-client/src/process.rs)）は `stdin` で `port` / `token` しか渡していない
- そのため **クレデンシャルや session は起動引数ではなく IPC コマンドで渡す** 方針を維持する
- 一方でマスタキャッシュ保存先だけは Python 側単独では決められないため、T0 で次のどちらかを追加する
  - `stdin` 初期 payload に `config_dir` / `cache_dir` を追加
  - `SetEnginePaths` 相当の軽量コマンドを新設

### 2.2 ログ・テレメトリでのマスク

- Rust 側の `Debug` 派生で `password` / `second_password` / `session.url_*` をマスクする `SecretString` 型を導入
- Python 側は `engine.exchanges.tachibana.SessionState` を `__repr__` でマスク（pydantic の `SecretStr` を採用）
- IPC のシリアライズ時にマスクは行わない（loopback + token 認証で守る）

### 2.3 セッション再永続化

Python が**起動時の session 検証または初回ログイン**に成功し新仮想 URL を取得したら、**`VenueCredentialsRefreshed` イベントを Rust に逆送**して Rust が keyring を更新する:

```rust
pub enum EngineEvent {
    // ...
    VenueCredentialsRefreshed {
        venue: String,
        session: serde_json::Value,
    },
}
```

これにより Python 単独再起動 → Rust が keyring の最新 session を投入 → Python が validation 実行、というループが閉じる。

### 2.4 再起動時の source of truth

- **managed mode の再起動導線は `ProcessManager` が source of truth**。再接続時に `src/main.rs` がその場しのぎで `SetVenueCredentials` を送るのではなく、`ProcessManager` が proxy と同様に venue credentials も保持・再送する
- 再起動後の正式シーケンスは次の通り:
  1. `Hello -> Ready`
  2. `SetProxy`
  3. `SetVenueCredentials`
  4. `VenueReady`
  5. metadata fetch 再開
  6. active subscriptions 再送
- これにより [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 の「recovery handshake 後に購読再送」という既存契約に、立花の認証状態を安全に差し込める

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

- `SetVenueCredentials` 受領時、Python は **同期的に session validation を実施し、必要なら 1 回だけ再ログインして、結果を `VenueReady{venue:"tachibana"}` か `EngineError` で返す**
- `VenueReady` は **冪等イベント**。Python 単独再起動 → `SetVenueCredentials` 再注入 → `VenueReady` 再送、というサイクルを毎回踏む。Rust 側はこれを**最終受信状態**として保持し、`Disconnected{venue}`（プロセス再起動・WS 全切断など）を受けたらリセットする
- **`VenueReady` 再受信時の重複防止**: active subscriptions の resubscribe は `ProcessManager`（[engine-client/src/process.rs](../../../engine-client/src/process.rs)）が **1 度だけ** 行う。UI 側の view code は `VenueReady` イベントに反応して新規 subscribe を発行しないこと（既存購読の参照カウントは ProcessManager 経由でのみ維持）
- Rust 側は `VenueReady` 受領前は立花 ticker の `ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe` を送らない。UI では venue 単位のローディング表示を出す
- 既存 sidebar は起動直後に metadata fetch を自動発火するため、立花追加時は **venue-ready ゲート** を `AdapterHandles` 呼び出し前に差し込む必要がある

## 4. Python 側ファイル構成

```
python/engine/
├── exchanges/
│   ├── tachibana.py          # ExchangeWorker 実装
│   ├── tachibana_auth.py     # ログイン・session validation
│   ├── tachibana_url.py      # build_request_url（REQUEST 用 JSON クエリ）/ build_event_url（EVENT 用 key=value 形式）/ func_replace_urlecnode（SKILL.md R2/R9）
│   ├── tachibana_codec.py    # Shift-JIS デコード + parse_event_frame + deserialize_tachibana_list（空配列="" 正規化、SKILL.md R8）
│   ├── tachibana_master.py   # CLMEventDownload ストリームパース
│   └── tachibana_ws.py       # EVENT WebSocket クライアント（FD frame 中心、KP frame で死活監視）
└── tests/
    ├── test_tachibana_url.py        # REQUEST と EVENT で URL 形式が違うこと（R2）も検証
    ├── test_tachibana_codec.py      # Shift-JIS 往復 + 空配列 "" → [] 正規化（R8）
    ├── test_tachibana_event_parse.py
    ├── test_tachibana_login.py      # mock サーバ（pytest-httpx / respx）
    └── test_tachibana_e2e.py        # demo 環境を踏むのは @pytest.mark.demo_tachibana のみ
```

- 依存追加: 立花 API は標準 HTTP/WS なので新規依存ゼロ。Shift-JIS は Python 標準 `bytes.decode("shift-jis")` で足りる
- HTTP クライアントは既存 [python/engine/exchanges/binance.py](../../../python/engine/exchanges/binance.py) と同じく **`httpx`** に揃える。WS は同じく `websockets` を採用
- mock サーバは既存 [python/tests/](../../../python/tests/) と同一ツールチェーン（`pytest-httpx` の `HTTPXMock` フィクスチャ）に揃える。`respx` は採用しない（混在を避ける）。WS は `websockets.serve` でローカルサーバを立てて FD/KP frame を再生

## 5. Rust 側の変更箇所

| ファイル | 変更内容 |
| :--- | :--- |
| [exchange/src/adapter.rs](../../../exchange/src/adapter.rs) | `Venue::Tachibana` / `MarketKind::Stock` / `Exchange::TachibanaStock` 追加。`FromStr` / `Display` / `ALL` 配列更新、および `MarketKind` を網羅する既存 match の修正 |
| [engine-client/src/dto.rs](../../../engine-client/src/dto.rs) | `Command::SetVenueCredentials` / `EngineEvent::VenueReady` / `EngineEvent::VenueCredentialsRefreshed` 追加。`schema_minor` を bump |
| [engine-client/src/process.rs](../../../engine-client/src/process.rs) | `ProcessManager` が Tachibana credentials を保持し、再起動時に `SetProxy` の後で `SetVenueCredentials` を再送する |
| `data/src/config/tachibana.rs`（新設） | `TachibanaCredentials` 型 + keyring 読み書き。SKILL.md R10 に従う。`data/src/config/proxy.rs` の暗号資産プロキシ keyring 実装を参考にする |
| [src/main.rs](../../../src/main.rs) | 起動時に keyring から立花 creds を復元し `SetVenueCredentials` 投入 |
| `src/screen/login.rs` の拡張または新画面 | 立花ログイン UI（user_id / password / second_password / is_demo チェックボックス） |
| `src/screen/dashboard/tickers_table.rs` ほか UI | `VenueReady` 前の metadata fetch を抑止し、`MarketKind::Stock` に応じた market filter / indicator / timeframe / 表示文言を調整 |
| [docs/plan/✅python-data-engine/](../✅python-data-engine/) `schemas/` | `commands.json` / `events.json` に新コマンド・イベントを記載、`CHANGELOG.md` 更新（※親計画ディレクトリ内のスキーマ。本計画 T0 で同期） |

## 6. 失敗モードと UI 表現

| 状態 | UI 表現 | きっかけ |
| :--- | :--- | :--- |
| クレデンシャル未設定 | ログイン画面誘導 | keyring に何もない |
| 電話認証未済 | エラーバナー「先に電話認証を完了してください」 | login で `sResultCode=10031` 等 |
| 仮想 URL 期限切れ | バナー「立花のセッションが切れました（夜間閉局）」 | `p_errno="2"` |
| ザラ場時間外 | チャート上に「市場時間外」オーバーレイ | 時刻判定（Python 側） |
| Python 再起動中 | 既存の `EngineRestarting` ステータスを流用 | [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §5.3 |
| 銘柄コード未存在 | ticker selector で候補出ない | マスタに無い |

## 7. テスト戦略

### 7.1 単体（Python）

- URL 組立 — REQUEST 用 `build_request_url`（`?{JSON 文字列}` 形式、SKILL.md R2）と EVENT 用 `build_event_url`（`?key=value&...` 形式、R2 例外）を**別関数として**実装し、サンプル `e_api_login_tel.py` / `e_api_event_receive_tel.py` の出力とバイト一致確認
- `func_replace_urlecnode` の置換 30 文字（SKILL.md R9）— サンプルと 1 対 1 一致テスト
- Shift-JIS デコード往復（銘柄名・エラーメッセージの代表サンプル含む）
- `parse_event_frame` — 制御文字 `^A^B^C` 分解と `<型>_<行>_<情報コード>` キー抽出
- 空配列 `""` → `[]` 正規化（SKILL.md R8）— 注文ゼロ件レスポンスのフィクスチャで検証
- `p_errno` / `sResultCode` 二段判定（`p_errno` 空文字＝正常も含む、SKILL.md R6）
- マスタ `CLMEventDownload` ストリームパース（チャンク境界 / `CLMEventDownloadComplete` 終端検知）

### 7.2 結合（Python + mock サーバ）

- **`pytest-httpx`**（`HTTPXMock` フィクスチャ）でデモサーバを擬似化。既存 [python/tests/test_binance_rest.py](../../../python/tests/test_binance_rest.py) のパターンを踏襲
  - `e_api_login_tel.py/e_api_login_response.txt` を fixture として再利用
  - 異常系: `p_errno=-62` (時間外) / `p_errno=2` (セッション切れ) / `sKinsyouhouMidokuFlg=1` (未読通知)
  - WebSocket は `websockets` の `serve` でローカルサーバを立てて FD/KP frame を再生

### 7.3 結合（Rust + Python）

- `tests/integration/tachibana_handshake.rs`: `SetVenueCredentials` → `VenueReady` の往復
- `tests/integration/tachibana_subscribe.rs`: trade / depth subscribe → mock の FD frame → `Trades` / `DepthSnapshot` 受信
- 既存の Rust 単体は mockito を使う（プロジェクト共通方針）

### 7.4 E2E（demo 環境）

- `pytest -m demo_tachibana` でのみ実行（CI 既定では skip）
- 実 demo 環境 → ログイン → マスタ取得 → 任意銘柄 subscribe → 10 件以上の trade を受信して切断、までを 1 分以内に完了

### 7.5 シークレット流出ガード

- リポジトリ全体に対する pre-commit `grep -E "(kabuka\.e-shiten|sUserId.*=.*['\"][^'\"]+['\"])"` で本番 URL・ハードコードクレデンシャルを禁止
- ログキャプチャテストで `sPassword` / `sSecondPassword` / 仮想 URL ホスト部分が出ないこと
