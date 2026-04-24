# 新仕様: Rust ビュアー + Python データエンジン

## 1. ゴール

- **Rust** はビュアー専用。UI 描画・ユーザー操作・レイアウト永続化のみを担当する。
- **Python** が取引所 REST/WebSocket への接続、レート制限、データ正規化、配信を担当する。
- Rust は取引所 SDK を**直接呼ばない**。すべてのマーケットデータは Python サービス経由で受け取る。

## 2. 責務分割

| 機能 | 現状 | 新構成 |
|---|---|---|
| Iced GUI / 描画 / 入力 | Rust | **Rust** |
| レイアウト・テーマ永続化 | Rust | **Rust** |
| サウンド再生 | Rust | **Rust** |
| ティッカーメタデータ取得 | Rust (`exchange/`) | **Python** |
| Kline / OI / 24h 統計の REST 取得 | Rust | **Python** |
| trade / depth / kline の WebSocket 購読 | Rust | **Python** |
| 取引所別レート制限 | Rust (`limiter.rs`) | **Python** |
| プロキシ / 認証情報 | Rust (`keyring`) | **Rust が保持し Python に渡す**（または Python 側に環境変数で注入） |
| ヒストリカル trade（bulk download 含む） | Rust (`connector::fetcher`) | **Python** |
| Open Interest（REST: 履歴 / インジケータ継続要求） | Rust (`fetch_open_interest`) | **Python**（§4.2 に `FetchOpenInterest` / `OpenInterest` イベントとして明記） |
| インメモリ集計（チャート用バッファ） | Rust (`data/`) | **Rust に残す**（描画直近のものに限定） |

## 3. プロセスモデル

```
┌──────────────────────────┐        IPC         ┌──────────────────────────┐
│  Rust Viewer (Iced)      │ ◄────────────────► │  Python Data Engine      │
│  - UI / canvas           │   (本文 §4 参照)    │  - REST clients          │
│  - layout state          │                    │  - WS clients            │
│  - input handling        │                    │  - rate limiter          │
│  - in-memory chart bufs  │                    │  - normalization         │
└──────────────────────────┘                    └──────────────────────────┘
```

- Rust 起動時に Python サブプロセスを spawn（同梱バイナリ or `python -m data`）。
- 異常終了時は Rust が再起動を試行（指数バックオフ、最大 N 回）。
- 開発時は Python を独立起動して Rust から既存ポートに接続する形も許容（`--data-engine-url` フラグ）。

## 4. IPC プロトコル

### 4.1 トランスポート

**第一案: ローカル WebSocket + JSON**
- Python 側で `127.0.0.1:<port>` に WebSocket サーバを立てる（`websockets` or `fastapi`）。**必ず loopback にバインド**し、外部インタフェースでは listen しない。
- Rust 側は既存の `fastwebsockets` クライアントを再利用できる。
- 双方向（コマンド↔イベント）を 1 接続で扱える。
- ポートは Rust が空きを選び、**stdin 経由**で Python に渡す（`--port` のような CLI 引数は `ps` で露出するため使わない）。

**代替案**:
- gRPC（`tonic` + `grpcio`）: 型安全だが依存が重い。
- Unix Domain Socket / Named Pipe + length-prefixed JSON: OS 依存だが最速、認証不要（ファイルパーミッションで守れる）。
- ZeroMQ: 低レイテンシだがバイナリ依存追加。

**推奨**: まず WebSocket+JSON で開始し、ボトルネックが出たら MessagePack に切替。スキーマは [4.3](#43-メッセージスキーマ) を共通定義とする。

#### 4.1.1 ローカル IPC のアクセス制御

同一マシン上の別プロセスから loopback に接続されると、認証なしで `SetProxy` 等の制御面が叩かれてしまうため、WebSocket+JSON 案を採るなら最低限以下を満たす:

- **ランダム接続トークン（必須）**: Rust が起動ごとに 32 byte のトークンを生成し、stdin 経由で Python に渡す。WebSocket 接続時に `Sec-WebSocket-Protocol` もしくは最初のメッセージ（`Hello`、§4.5 参照）で提示させ、一致しなければ即切断。
- **単一クライアント制限**: Python サーバは既にクライアントが接続中なら新規接続を即拒否。`ready` 状態遷移後は追加接続を受けない。
- **loopback 専用**: `127.0.0.1` / `::1` 以外からの接続は listen しない／accept しない。
- **ポート秘匿**: ポート番号は Rust→Python 間の stdin だけで受け渡し、環境変数やファイルには書き出さない。

将来的にこれらを簡素化したい場合は UDS / Named Pipe に切り替え、ファイルパーミッション（ユーザー専用）で代替する。

### 4.2 メッセージ方向

| 方向 | 種類 | 例 |
|---|---|---|
| Rust → Python | `Hello` / `SetProxy` / `ListTickers` / `GetTickerMetadata` / `Subscribe` / `Unsubscribe` / `FetchKlines` / `FetchTrades` / `FetchOpenInterest` / `FetchTickerStats` / `RequestDepthSnapshot` / `Shutdown` | `{"op":"subscribe","venue":"binance","ticker":"BTCUSDT","stream":"trade"}` |
| Python → Rust | `Ready` / `EngineError` / `Connected` / `Disconnected` / `Tickers` / `TickerInfo` / `TickerStats` / `Klines` / `KlineUpdate` / `Trades`（バッチ） / `DepthSnapshot` / `DepthDiff` / `DepthGap` / `OpenInterest` / `Error` | `{"event":"trade_batch","venue":"binance","ticker":"BTCUSDT","trades":[{"p":"68000.1","q":"0.012","side":"buy","ts":...}, ...]}` |

※ `exchange::Event` 列挙体の各バリアント（`Connected` / `Disconnected` / `DepthReceived` / `TradesReceived` / `KlineReceived`）と一対一対応する形で Python→Rust イベントを定義する。OI インジケータが継続的に要求する `FetchRange::OpenInterest` も `FetchOpenInterest` コマンド + `OpenInterest` レスポンスで表現する（参考: [`exchange/src/adapter/client.rs`](../../exchange/src/adapter/client.rs) `fetch_open_interest`、[`src/chart/indicator/kline/open_interest.rs`](../../src/chart/indicator/kline/open_interest.rs)）。

ticker 一覧 / metadata / stats は現行 `AdapterHandles` が起動直後に取得している（[`exchange/src/adapter/client.rs`](../../exchange/src/adapter/client.rs) L200 付近・L269 付近）。新構成でも `ListTickers` / `GetTickerMetadata` / `FetchTickerStats` を trait と IPC の両方に明示し、`EngineClientBackend` が同じ API を提供することで UI 側の起動シーケンスを不変に保つ。

### 4.3 エンコーディング方針（per-channel）

すべての IPC を JSON 一律にはしない。メッセージ種別ごとに頻度とホットパスを見てエンコーディングを分ける。

| チャネル | 頻度 | エンコーディング（第一選択） | 備考 |
|---|---|---|---|
| ハンドシェイク / コマンド / エラー / メタデータ | 低頻度 | **JSON** | 可読性・デバッグ性重視 |
| `TickerStats` / `KlineUpdate` / `OpenInterest` | 中〜低頻度 | **JSON** | 人間が覗ける方が運用で楽 |
| `Trades`（バッチ） | 33ms バッチ、可変 | **JSON → MessagePack**（計測で超える場合） | フェーズ 2 で計測し判断 |
| `DepthDiff` / `DepthSnapshot` | 高頻度（BTCUSDT で秒あたり数百〜千メッセージ） | **バイナリ推奨**（MessagePack もしくは FlatBuffers + 固定小数 i64） | 下記 §4.3.1 参照 |

#### 4.3.1 depth チャネルのバイナリ化検討

現行 Rust は `sonic-rs` で高速 JSON parse しているため、IPC で JSON に戻すと CPU 面で後退する懸念がある（特に Binance perp の L2 は秒あたり数百〜千メッセージ、価格・数量を文字列化して Decimal 化するコストが直撃）。

- **第一候補**: `DepthDiff` / `DepthSnapshot` のみ **MessagePack + 固定小数 i64**（`Price` / `Qty` の min_ticksize 単位の整数表現）。他チャネルは JSON のまま。スキーマは §4.3.2 の DTO と同じ shape を使う。
- **代替**: FlatBuffers。ゼロコピー読み取りができるが、ビルド複雑化と Python 側の生産性低下。まず MessagePack を試して足りなければ検討。
- **決定タイミング**: フェーズ 2 で現行 Rust 直結とのベースライン計測を取り、IPC 追加レイテンシ目標（§10）を満たせない場合にバイナリ化へ切替。計測が済むまでは JSON で実装して開発効率を優先。

### 4.3.2 メッセージスキーマ

**方針: IPC 専用 DTO 層を別途定義する（既存 Rust 型をそのまま serde には流さない）。**

理由: 既存型は IPC 向けに serde-ready ではない。
- `Trade` と `TickerStats` のみ `Deserialize` 派生を持つが、`Kline`（[`exchange/src/lib.rs`](../../exchange/src/lib.rs) L544〜）・`OpenInterest`（同 L651〜）は serde 派生なし。
- `Depth`（[`exchange/src/depth.rs`](../../exchange/src/depth.rs)）は内部表現寄りで、そのままシリアライズすると Python 側が読みにくい。
- `exchange::Event`（[`exchange/src/adapter.rs`](../../exchange/src/adapter.rs) L535〜）は `Arc<Depth>` / `Box<[Trade]>` を含み、IPC で直接扱う shape ではない。

そこで:

- **`engine-client` crate 側に IPC 専用 DTO（例: `dto::TradeMsg`, `dto::KlineMsg`, `dto::DepthSnapshotMsg`, `dto::DepthDiffMsg`, `dto::OpenInterestMsg`）を新設**し、Rust 既存型 ⇔ DTO の変換関数を置く。
  - UI 側へは従来通り `exchange::Event`（または同等の enum）で返すため、UI コードは変更不要。
- 共通スキーマ定義は [`docs/plan/schemas/`](./schemas/) 配下に JSON Schema として配置し、Rust `serde` 派生 / Python `pydantic` モデルの両方を同スキーマから生成する（生成器の選定は [open-questions.md](./open-questions.md) 参照）。
- タイムスタンプは UNIX ms (i64)、価格・数量は `string`（精度損失防止、Rust 側で `Price`/`Qty` 相当へ復元）。
- 既存型に派生を足すだけで済むもの（`Kline`, `OpenInterest` 等）は素朴に `Serialize/Deserialize` を追加する選択肢もあるが、`Depth` の内部表現を外に出すのは避けたいため、全体として **DTO 層分離を原則**とする。

### 4.4 バックプレッシャと整合性保証

**trade**:
- Python は trade を 33ms（現行と同じ）でバッチ化して送信、1 メッセージ複数トレードの配列を許容。
- Rust 側 receive キューが詰まった場合、最古の trade バッチから drop し warning ログ。trade は累積が壊れないので drop 可。

**depth（壊れやすいので明示的に保護する）**:
- 各 `DepthDiff` には `{venue, ticker, stream_session_id, sequence_id, prev_sequence_id}` を必ず付与する。
- `DepthSnapshot` には `{venue, ticker, stream_session_id, sequence_id, checksum?}` を付与（checksum は取引所が提供する場合のみ）。
- Rust 側ハンドリング:
  1. `DepthSnapshot` 受信 → 新 `stream_session_id` で板を初期化し `applied_seq = snapshot.sequence_id` を保持。
  2. `DepthDiff` 受信 → `stream_session_id` 一致かつ `prev_sequence_id == applied_seq` なら適用、`applied_seq = diff.sequence_id`。
  3. 不一致（gap / session 変化）を検知したら板を破棄し、`RequestDepthSnapshot` を即時送信。以降の diff は snapshot 適用まで buffer する。
- Python 側も自前で gap を検知したら `DepthGap{venue, ticker, stream_session_id}` を送出し、自発的に再スナップショットを取得して `DepthSnapshot` を送り直す（Rust からの要求を待たない）。

session ID の用語と型（混同防止）:
- **`engine_session_id`**: Python **プロセス**のライフサイクルを表す ID。プロセス起動ごとに **UUIDv4** を発番。再起動をまたいで必ずユニーク。`Ready` メッセージに含めて Rust に通知。Rust は `engine_session_id` が変わったら **全ての** 板・未確定 kline・進行中 fetch を破棄する。
- **`stream_session_id`**: 特定 `(venue, ticker)` の **取引所 WS 接続**を表す ID。`engine_session_id` + 当該 stream のカウンタ（u32）の組で表現する（JSON 上は `"<uuid>:<u32>"` の文字列）。WS 再接続ごとに u32 を増やす。これを持てば「プロセスは生きているが一部 ticker だけ再接続した」ケースも正しく扱える。
- 仕様書内の過去版で `session_id` と単一語で書いた箇所はすべて上記 2 種のいずれかに置き換える方針。
- 受信キューが詰まった場合、**depth の中間 diff は drop せず**（drop するとサイレントに壊れる）、代わりに「最新 snapshot + 以降の差分」を coalesce して送る。Rust 側が追いつかない場合は session を切って snapshot から再同期する。
- 取引所が checksum を提供する venue では diff 適用後に checksum 検証、不一致なら強制再同期。

**kline / OI**:
- kline 更新は冪等（同一 open_time の上書き）なので drop 可。
- OI は時刻 + 値の列で差分整合性が不要、最新値と再フェッチで復旧可能。

### 4.5 起動ハンドシェイク

接続直後の race とバージョン不一致を防ぐため、接続直後は次の順で進む。Rust は `Ready` 受領までマーケットデータ系コマンドを送らない。

1. **Rust → Python: `Hello`**
   - フィールド: `{schema_major: u16, schema_minor: u16, client_version: str, token: str}`。
   - `token` は §4.1.1 で渡したランダム接続トークン。Python は不一致なら即切断。
2. **Python → Rust: `Ready` もしくは `EngineError`**
   - `Ready` フィールド: `{schema_major: u16, schema_minor: u16, engine_version: str, engine_session_id: uuid, capabilities: {supported_venues: [...], supports_bulk_trades: bool, supports_depth_binary: bool, ...}}`。
3. **Rust → Python: `SetProxy`（必要時のみ）**
   - `Ready` 受領後に送る。
4. **Rust → Python: マーケットデータ系コマンド**（`Subscribe` 等）。

#### 4.5.1 スキーマバージョニング運用
- **`schema_major`**: 既存フィールドの意味変更・削除、enum バリアントの削除、コマンド/イベント名の変更など互換性を破る変更で bump。不一致は **致命的エラー**としてハンドシェイクを失敗させ、UI にアップグレード誘導バナーを出す。
- **`schema_minor`**: 後方互換の追加（新フィールド・新 enum バリアント・新コマンド）で bump。**minor 差は警告ログのみ**で接続継続。受信側は未知フィールドを無視、未知バリアントは `Unknown` として扱う。
- 開発中は minor を頻繁に上げる運用で良い。major を触るのは DTO shape の破壊的変更時だけ。
- 計画ツリー配下の [`docs/plan/schemas/CHANGELOG.md`](./schemas/) に major/minor 変更履歴を記録する。

#### 4.5.2 既存接続の置換（半死接続対策）
Python プロセスは生きているが Rust が単独でクラッシュ / デバッガで落とされた場合、Python 側に半死の古い接続が残り、新しい Rust が単一クライアント制限で拒否される事故が起こる。これを避けるため:

- Python サーバは `Hello` 受領時に **トークンが一致すれば既存接続を強制切断して新規を受け入れる**（トークンは Rust プロセス固有なので、別の攻撃接続が勝手に引き継ぐことはない）。
- 加えて WebSocket の ping/pong を 15 秒間隔で実施し、連続 2 回応答なしで接続を破棄。
- 強制切断時は古い側に `Error{reason: "superseded"}` を送って閉じる。

`Connected` / `Disconnected` イベントは「取引所 WS の接続状態」を表す（エンジン自体の準備完了ではない）。エンジンの準備完了は `Ready` のみで表す。

## 5. Rust 側の変更概要

### 5.1 venue 単位の backend 抽象化（先行作業）

現状の `AdapterHandles` は venue 毎の具体ハンドルを直接フィールドに持ち、`spawn_all()` で一斉起動している（[`exchange/src/adapter/client.rs`](../../exchange/src/adapter/client.rs) L21〜、L30〜）。
「Binance だけ Python 経由／他は Rust 直結」を段階移行で成立させるには、まず **venue ごとに backend を選べる抽象化** を挟む必要がある。

- trait `VenueBackend` を定義。現行 `AdapterHandles` が担う全経路を覆う:
  - 初期化系: `list_tickers` / `get_ticker_metadata`（[`exchange/src/adapter/client.rs`](../../exchange/src/adapter/client.rs) L200 付近・L269 付近に対応）
  - ストリーム系: `subscribe` / `unsubscribe` / イベントストリーム取得
  - フェッチ系: `fetch_klines` / `fetch_open_interest` / `fetch_ticker_stats` / `fetch_trades`
  - 運用系: `request_depth_snapshot`（再同期用）/ `health`（エンジン状態の問い合わせ）
- `AdapterHandles` の各フィールドを `Box<dyn VenueBackend>` に変更（または enum でラップ）。
- 実装は 2 種類:
  - `NativeBackend`: 既存 `hub/{venue}` を呼ぶ（現行動作）。
  - `EngineClientBackend`: Python エンジンに IPC する新実装。
- 起動時設定（CLI フラグ or 設定ファイル）で venue 毎に backend を選ぶ。

この抽象化はフェーズ 1 の前提となるため、[implementation-plan.md](./implementation-plan.md) のフェーズ 0.5 として切り出す。

### 5.2 エンジンクライアント / Python 連携

- `engine-client` crate を新設（または `exchange` 配下に `engine_backend` モジュールとして追加）。
  - IPC DTO 定義（§4.3）と WebSocket クライアントを内包。
  - `VenueBackend` を実装し、内部では Python への IPC コマンド発行＋イベント購読を行う。
  - UI 側へ返すイベントは既存 `exchange::Event` 相当（`Arc<Depth>` / `Box<[Trade]>` への変換をここで行う）。
- `connector::fetcher` も `VenueBackend` 経由に置換。
- `data/` crate のチャートロジックはそのまま流用。
- 最終的に `limiter.rs`, `hub/*` は削除（Python 側に移管）。`proxy.rs` は「Rust が資格情報を保持し Python に渡す」責務だけ残す（§6 参照）。
- 起動時の Python プロセス管理は **`engine-client` crate 内（例: `engine_client::process`）** に置く。`src/` バイナリ側からは薄い fascade 呼び出しのみとし、バイナリと crate の境界を汚さない。crate 名・モジュール配置の最終決定はフェーズ 0.5 の抽象化設計レビュー時に確定する。

### 5.3 Python プロセス復旧プロトコル

Python の異常終了・再起動は「必ず起こる」前提で、Rust 側で状態を再構築できるようにする。Rust は自身を **source of truth** として以下を保持し、新プロセスに投入する:

- アクティブな購読セット `Set<(Venue, Ticker, StreamKind, TickMultiplier?, PushFrequency)>`
- 進行中フェッチ要求 `Map<RequestId, FetchCommand>`（`FetchKlines` 等、応答待ち）
- プロキシ設定
- schema バージョン（Rust クライアント側のコンパイル時定数）

**復旧フロー**:

1. 監視スレッドが Python の終了（exit code / broken pipe）を検知。
2. Rust は進行中フェッチを全て `Err(EngineRestarting)` で即時失敗させる（UI が自発的にリトライできる形にする）。
3. 既存の板キャッシュ・OI キャッシュ・kline の「未確定な最新バー」を破棄（古い `engine_session_id` のもの）。永続済みの確定データ（履歴 kline 等、`data/` crate が保持するもの）は保持してよい。
4. 指数バックオフで spawn（上限 N 回、超えたら UI にエラーバナー）。
5. 起動ハンドシェイク（§4.5）→ `SetProxy` → 保持していた購読を全て再送。
6. UI は `Ready` で通知された新しい `engine_session_id` に切り替わったことで、depth は snapshot 受信まで「同期中」表示、trade/kline 履歴は再受信で埋め直す。

**UI への影響**:
- `EngineRestarting` 中は各 pane に「データエンジン再起動中」のステータスを出す（チャートを消さず、最後の状態をグレーアウトで維持）。
- 復旧完了後、自動で通常表示に戻る。

**スコープと責務分担**:
- Python 側: プロセス単体で gap 検知・自発的再スナップショット（§4.4）と、クラッシュ時に再実行されたときに単体で正常起動できること。これは **フェーズ 1** 完了条件。
- Rust 側: プロセス監視 / 指数バックオフ spawn / 状態再投入 / UI ステータス。これは **フェーズ 2** 完了条件（Python が立っていないと意味がないため）。
- 以前「フェーズ 1 に復旧プロトコルを含める」と書いた箇所は上記の責務分担に整理し直す。

### 5.4 プロキシ資格情報の受け渡し

現行ではプロキシ認証は OS keyring から復元され（[`src/layout.rs`](../../src/layout.rs) 付近）、起動時に `AdapterHandles::spawn_all()` へ渡されている（[`src/main.rs`](../../src/main.rs) 付近）。Python サブプロセス化に伴い以下を決める:

- **受け渡し方法**: 次のいずれか。デフォルトは (a)。
  - (a) 起動後に IPC `SetProxy` コマンドで渡す（stdin 経由の初期ハンドシェイクでもよい）。ログ・コマンドラインに残らない。
  - (b) 環境変数で注入（`HTTPS_PROXY` 等）。シンプルだが子プロセスの `ps` / dump で見える可能性があるため非推奨。
  - CLI 引数での受け渡しは **採用しない**（`ps` で露出するため）。
- **再適用**: 現行は「設定変更後に再起動」UX。新構成でも「Python プロセスを再起動して再注入」を基本とし、ランタイム差し替えは後日課題。
- **クラッシュダンプ / stderr への漏洩防止**: Python 側ロガーでプロキシ URL をマスク、クラッシュハンドラでもスタックにクレデンシャルが出ないよう秘匿ラッパーで保持する。
- **Rust 側は keyring を引き続き真の保管場所**とし、Python には必要時に平文で渡すだけ（Python では永続化しない）。

## 6. Python 側の構成

```
python/
├── pyproject.toml
├── data/
│   ├── __main__.py          # CLI: `python -m data --port N`
│   ├── server.py            # WS サーバ・dispatch
│   ├── schemas.py           # pydantic モデル
│   ├── limiter.py           # 取引所別レート制限
│   ├── exchanges/
│   │   ├── base.py          # Exchange ABC（fetch_*, stream_*）
│   │   ├── binance.py
│   │   ├── bybit.py
│   │   ├── hyperliquid.py
│   │   ├── okex.py
│   │   └── mexc.py
│   └── bulk/                # data.binance.vision 等の bulk DL
└── tests/
```

- 主要依存: `aiohttp` or `httpx`（REST）、`websockets`（WS クライアント＆サーバ）、`pydantic`、`uvloop`（Linux/macOS のみ。Windows は対象外 → §6.2 参照）、`orjson`。
- 配布: 開発時は `uv` / `pip` セットアップ、リリース時は `PyInstaller` で同梱バイナリ化（決定は [open-questions.md](./open-questions.md) 参照）。

### 6.1 プロセスモデル（フェーズ 1 時点）

- **フェーズ 1 は asyncio 単一プロセス**で全 venue を扱う（MVP の立ち上げコスト最小化）。
- ただし将来 venue ごとに worker プロセスへ分割できるよう、最初から次の境界を守る:
  - `exchanges/<venue>.py` は **`ExchangeWorker` 抽象**を実装（`async def run(self, inbox, outbox)` のようなメッセージループ形）。
  - `server.py` はクライアント接続管理と `ExchangeWorker` インスタンスとの dispatch のみを担当し、取引所固有ロジックを持ち込まない。
  - Worker 間で状態を共有しない（共有は server 経由のメッセージのみ）。
- 将来 GIL / CPU ボトルネックが実測で出たら、`ExchangeWorker` を `multiprocessing` または `asyncio` subprocess に差し替える。server ↔ worker 間プロトコルは IPC スキーマと同じ DTO を使えるようにして、分割コストを最小化する。
- この抽象化は **フェーズ 1 の設計で導入**する（後から入れ直すとスキーマ・ライフサイクル・トークン配布がやり直しになるため）。

### 6.2 プラットフォーム対応

ユーザー開発環境は Windows だが、配布ターゲットは Win/Mac/Linux。

- **uvloop**: Linux/macOS のみ。Windows はデフォルトの asyncio（`SelectorEventLoop` または `ProactorEventLoop`）で動かす。フェーズ 0 のベースライン計測で Windows での性能を確認し、不足するなら Windows だけ winloop を検討する。
- **IPC トランスポートの OS 別選択**:
  - フェーズ 2 の時点では全 OS で **loopback WebSocket + JSON** を採用（実装統一のため）。
  - 将来バイナリ化や UDS/Named Pipe へ切り替える場合は、POSIX は Unix Domain Socket、Windows は Named Pipe。`websockets` のローカル bind と比べて実装コストが上がるため、計測結果を見てから判断。
- **PyInstaller**: 全 OS で利用可能だが、macOS では code signing / notarization、Windows では Defender 誤検知対策が必要。フェーズ 6 で扱う。

## 7. 互換性・移行戦略

- 既存ユーザー設定・レイアウト JSON はそのまま使える（UI 側スキーマは変えない）。
- 取引所名・ティッカー識別子は現行 Rust 型と同じ表記を維持。
- 段階的に取引所単位で Python に移し、未移行のものは Rust 直接接続を残す（Feature flag で切替）。

### 7.1 Rust 直結モードの長期方針（要決定）

計画全体の射程を決める論点。フェーズ 5 で `hub/*` を削除するかどうかは、ここで決める:

- **案 A: 完全撤去**（デフォルト）
  - フェーズ 5 で `hub/*` と各種取引所依存（`reqwest`, `fastwebsockets`, `sonic-rs` 等）を削除。
  - `VenueBackend` trait は `EngineClientBackend` のみを実装する「過渡的な抽象化」になる。シンプル。
  - 低レイテンシが絶対要件のユーザーは見捨てる。
- **案 B: 恒久残置**（低レイテンシオプション）
  - `NativeBackend` を恒久的にビルドに残し、ユーザーが venue 単位で backend を選択可能。
  - メンテナンス負荷が倍。スキーマ差異・レート制限ロジック二重管理。
  - `VenueBackend` は長期的な I/F として確定させる。
- **案 C: 別 crate 切り出し + optional feature**
  - `native-backend` crate を optional feature にしてデフォルト OFF。必要な人だけビルドイン。
  - 本家配布は Python 必須、ソースビルド派は Rust 直結も選択可。

**判断基準**: フェーズ 2 完了時の IPC レイテンシ計測結果と、実ユーザーからの要望。現時点の暫定方針は **案 A**（撤去）。フェーズ 2 終了時点で再判断する（[open-questions.md Q5](./open-questions.md) を clos 条件に変更）。

## 8. 非ゴール

- UI 機能の追加・変更は本計画の対象外。
- Python 側で任意の戦略実行 / 自動売買を行うことは対象外（あくまで取得・配信エンジン）。
- 永続化 DB の導入は対象外（必要なら別計画）。

## 9. 非機能要件（合格ライン）

フェーズ 2 完了時点で下記を満たすことを合格条件とする。未達の場合、§4.3.1 のバイナリ化や §7.1 案 C の判断を行う。

### 9.1 レイテンシ

- **IPC 追加オーバーヘッド**（trade イベント: 取引所 WS 受信 → Python 処理 → IPC → Rust 描画キューに入る）:
  - 中央値 **< 2 ms**
  - p99 **< 10 ms**
- **起動時間**（プロセス spawn → `Ready` 受領）: **< 500 ms**
- **Python クラッシュ → 自動復旧完了**（最初の購読再送完了まで）: **< 3 秒**（バックオフ初回試行時）
- **depth 再同期**（`DepthGap` 検知 → snapshot 受信 → 板復元）: **< 500 ms**（代表的 venue、BTCUSDT）

### 9.2 スループット

- Binance perp BTCUSDT の depth diff（秒あたり数百〜千メッセージ想定）を **drop 0** で処理できること。
- 全 5 取引所で **上位 20 ticker 同時購読時**に CPU 使用率（Python + Rust 合計）が現行 Rust 直結の **+30% 以内**。

### 9.3 ベースライン計測

- **フェーズ 0 で現行 Rust 直結のベースラインを取得**し、`docs/plan/benchmarks/` に記録。以降のフェーズで同条件で再測して比較する。
- 計測項目:
  - trade / depth の end-to-end レイテンシ（取引所 WS 受信タイムスタンプ → 描画タイミング）
  - アイドル時 / 高負荷時（上位 20 ticker 同時購読）の CPU / メモリ / スレッド数
  - バイナリサイズ
  - 起動時間
- 計測は Windows（開発環境）を最低条件、可能なら Linux も。

### 9.4 整合性

- depth の gap 検知漏れ = 0（長時間稼働テストで板と取引所の snapshot を突き合わせて検証）。
- trade の重複配信は許容するが、`(venue, ticker, trade_id)` で Rust 側が dedup すること。
