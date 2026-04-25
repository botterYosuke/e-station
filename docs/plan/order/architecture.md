# 立花注文機能: アーキテクチャ

## 1. 配置原則

[docs/plan/tachibana/architecture.md §1](../tachibana/architecture.md) の Python 集約方針を踏襲。発注経路の追加責務は以下:

| 責務 | 所在 |
|---|---|
| HTTP API `/api/order/*` のレスポンス組立 | **Rust** `src/api/order_api.rs`（新設） |
| 入力スキーマバリデーション | **Rust** 同上 |
| 冪等性マップ（`client_order_id → order_number`） | **Rust** `src/api/order_session_state.rs`（新設、flowsurface `agent_session_state.rs` を移植） |
| 立花リクエスト本体（`CLMKabuNewOrder` 等の組立・送信・パース） | **Python** `python/engine/exchanges/tachibana_orders.py`（新設） |
| 第二暗証番号の保持 | **Python メモリ + Rust keyring**（Phase 1 の credentials 経路に追加） |
| 第二暗証番号の入力 UI | **Python tkinter ヘルパー** subprocess（Phase 1 のログイン UI と同一機構を再利用） |
| EVENT EC フレームのパース | **Python** `python/engine/exchanges/tachibana_event.py` に `_parse_ec_frame` を追加 |
| 注文台帳ストア（重複検知 `p_eda_no` set、当日分のみ） | **Python メモリ** |
| 発注監査ログ | **Python**（`data_path()` を Rust から IPC で受領済み、Phase 1 T0.2） |
| UI（注文一覧 / 確認モーダル） | **Rust iced** |

## 2. プロセス境界とフロー

### 2.1 発注（同期）

```
ユーザー UI / curl
   │ POST /api/order/submit
   ▼
Rust src/api/order_api.rs
   │ ① 入力検証（UUID, 銘柄コード形式, qty>0 …）
   │ ② OrderSessionState.try_insert(client_order_id, key)
   │      ├─ Created   → 続行
   │      ├─ IdempotentReplay → 既存 order_number で 200 を即返却
   │      └─ Conflict  → 409
   │ ③ engine_client.send(Command::SubmitOrder { request_id, payload })
   ▼
Python python/engine/server.py
   │ ④ Event::OrderSubmitted を即発行（立花 HTTP 送信の前。この時点で WAL に submit 行が既にある前提）
   │    ※ HTTP 送信前に p_errno=2（session 切れ）等が即判明した場合は OrderSubmitted の後に
   │      OrderRejected を続けて発行する。Rust 側 OrderSessionState はこの連続を許容すること
   │ ⑤ tachibana_orders.submit_new_order(session, second_password, req)
   ▼
Python tachibana_orders.py
   │ ⑤ NewOrderRequest を組み立て、_compose_request_payload() で
   │    p_no / p_sd_date / sCLMID / sJsonOfmt を後付け
   │ ⑥ tachibana_url.build_request_url(session.url_request, payload)
   │    → func_replace_urlecnode で URL エンコード
   │ ⑦ httpx.post(url) → Shift-JIS デコード → check_response()
   │      p_errno=2     → SessionExpiredError
   │      sResultCode≠0 → OrderRejectedError(code, message)
   │ ⑧ NewOrderResponse をパース、sOrderNumber を返す
   ▼
Rust 受信
   │ ⑨ Event::OrderAccepted を待機していた send 側に解決
   │ ⑩ OrderSessionState に order_number を埋める
   │ ⑪ HTTP 200 を返却
```

### 2.2 約定通知（非同期、Phase O2）

```
立花 EVENT WebSocket
   │ ^A 区切りフレーム（p_evt_cmd=EC）
   ▼
Python tachibana_event.py._receive_loop
   │ parse_event_frame で項目化
   │ _parse_ec_frame で OrderEcEvent に正規化
   │ seen_eda_no に存在すれば skip（重複検知）
   ▼
Python server.py
   │ Event::OrderFilled / OrderCanceled / OrderRejected を IPC 送信
   │   ※ OrderPartiallyFilled は存在しない。leaves_qty > 0 の OrderFilled = 部分約定（nautilus 流）
   ▼
Rust 受信
   │ OrderSessionState.update_status(order_number, ...)
   │ UI 通知 + 注文一覧パネルの再描画
```

**発注タイムアウト**: `engine_client.send(SubmitOrder)` → `OrderAccepted / OrderRejected` 待ちには **`tokio::time::timeout(Duration::from_secs(30), ...)`** を掛ける。タイムアウト時は HTTP 504 + `reason_code="INTERNAL_ERROR"` を返し、Python 側の応答を待つ接続を破棄する。WAL には `submit` 行が残るため、再起動後の `IdempotentReplay` で unknown 状態として扱われる。

同様に `ModifyOrder` / `CancelOrder` にも 30 秒タイムアウトを適用する。

### 2.3 取消フロー（Phase O1）

立花の `CLMKabuCancelOrder` は `sOrderNumber`（`venue_order_id`）が必須だが、HTTP API は `client_order_id` を一次キーとする。そのため Rust 層で lookup してから Python に渡す。

```
ユーザー UI / curl
   │ POST /api/order/cancel { client_order_id }
   ▼
Rust src/api/order_api.rs
   │ ① OrderSessionState.get_venue_order_id(client_order_id)
   │      → None（unknown）: 404 / IdempotentReplay ルールに準じる
   │      → Some(venue_order_id): 続行
   │ ② engine_client.send(Command::CancelOrder { client_order_id, venue_order_id })
   ▼
Python tachibana_orders.cancel_order(session, second_password, client_order_id, venue_order_id)
   │ CLMKabuCancelOrder に sOrderNumber を載せて送信
   ▼
Rust 受信
   │ Event::OrderPendingCancel → OrderSessionState 更新 → HTTP 200
```

**`venue_order_id` が unknown の場合**: `OrderSessionState` に `venue_order_id = None`（起動時復元の unknown 状態）の `client_order_id` へのキャンセル要求は **404 + `reason_code="ORDER_STATUS_UNKNOWN"`** で reject する。クライアントは `GET /api/order/list` で確認してから再試行すること。

### 2.4 第二暗証番号 forget フロー（Phase O0）

```
ユーザー（UI ボタン or curl）
   │ POST /api/order/forget-second-password
   ▼
Rust src/api/order_api.rs
   │ engine_client.send(Command::ForgetSecondPassword)
   ▼
Python python/engine/server.py
   │ tachibana_session_holder.second_password = None
   ▼
Rust 受信
   │ HTTP 200 { "status": "OK" }
```

`SetSecondPassword` フロー（第二暗証番号入力 modal から）:

```
iced modal（second_password.rs）
   │ ユーザー入力 → Command::SetSecondPassword { request_id, value }
   ▼
Python python/engine/server.py
   │ tachibana_session_holder.second_password = SecretStr(value)
   │ 元の発注リクエスト（request_id で特定）を再開
   ▼
Rust 受信
   │ 元の SubmitOrder フロー §2.1 の ⑨ 以降へ
```

**注意**: `Command::SetSecondPassword` の `value` は IPC を経由するためプレーン `String`（`SecretString` は IPC JSON に送れない）。Python 側で `SecretStr` 化すること。

**`Command` enum の `Debug` 手実装（セキュリティ必須・**Tpre.2 で実施**）**: `dto.rs` の `Command` に現在ある `#[derive(Debug)]` を **Tpre.2 でスキーマ拡張と同時に外して手実装に切り替える**。T0.3 まで先送りすると `SetSecondPassword` variant が derive された状態でコードが存在する期間が生じ、ログへの平文漏洩リスクがある。手実装では `SetSecondPassword { .. }` arm のみ `"SetSecondPassword { value: [REDACTED] }"` と出力し、他の variant は従来通り出力する。`value` が `[REDACTED]` にマスクされることを **Tpre.2 の受け入れ条件**として単体テストで検証すること。

## 3. IPC スキーマ拡張（schema 1.2 → 1.3）

[engine-client/src/dto.rs](../../../engine-client/src/dto.rs) に追加:

**設計原則**: IPC は **nautilus のオーダー DTO に shape を合わせる**。立花固有の `sBaibaiKubun` 等は IPC に出さず、Python 側で写像する。

```rust
pub enum Command {
    // 既存 ...

    // 第二暗証番号管理（Debug は手実装で value を [REDACTED] マスク）
    SetSecondPassword {
        request_id: String,    // 発注リクエストと相関
        value: String,         // SecretString は IPC 越しに送れないため String; Python 側で SecretStr 化
    },
    ForgetSecondPassword,      // POST /api/order/forget-second-password → Python メモリクリア

    SubmitOrder {
        request_id: String,
        venue: String,                  // "tachibana" / 将来 "binance" 等
        order: SubmitOrderRequest,      // nautilus OrderFactory 入力相当
    },
    ModifyOrder {
        request_id: String,
        venue: String,
        client_order_id: String,
        change: OrderModifyChange,      // qty / price / trigger / expire を Option で
    },
    // Rust は OrderSessionState で client_order_id → venue_order_id を lookup してから Python に渡す（§2.3）
    // venue_order_id が None（unknown）の場合は HTTP 404 reject し Python へ送らない
    CancelOrder  { request_id: String, venue: String, client_order_id: String, venue_order_id: String },
    CancelAllOrders {
        request_id: String,
        venue: String,
        instrument_id: Option<String>,  // 銘柄絞り込み（nautilus と同じシグネチャ）
        order_side: Option<OrderSide>,
    },
    GetOrderList { request_id: String, venue: String, filter: OrderListFilter },
}

#[derive(Serialize, Deserialize)]
pub struct SubmitOrderRequest {
    pub client_order_id: String,
    pub instrument_id: String,           // "7203.TSE"
    pub order_side: OrderSide,           // BUY | SELL
    pub order_type: OrderType,           // MARKET | LIMIT | STOP_MARKET | STOP_LIMIT | MARKET_IF_TOUCHED | LIMIT_IF_TOUCHED
                                         // NOTE: MARKET_IF_TOUCHED / LIMIT_IF_TOUCHED は nautilus N2 互換のため型として保持するが、
                                         //       Rust HTTP 層で 400 reject するため IPC に載ることは絶対にない（§10.1 参照）
    pub quantity: String,                // 精度保持の文字列（nautilus Quantity 互換）
    pub price: Option<String>,           // LIMIT 系で必須
    pub trigger_price: Option<String>,   // STOP / IF_TOUCHED 系で必須
    pub trigger_type: Option<TriggerType>,  // LAST | BID_ASK | INDEX 等。立花は LAST のみ
    pub time_in_force: TimeInForce,      // DAY | GTC | GTD | IOC | FOK | AT_THE_OPEN | AT_THE_CLOSE
    pub expire_time_ns: Option<i64>,     // GTD で必須（nautilus と同じ ns 単位 UTC）
                                         // 変換経路: HTTP ISO8601 文字列 → Rust order_api.rs で UTC ns i64 に変換 → IPC
                                         //           → Python 側で ns → JST YYYYMMDD に変換し sOrderExpireDay へ
    pub post_only: bool,
    pub reduce_only: bool,
    pub tags: Vec<String>,               // venue 拡張: "cash_margin=cash" / "account_type=specific" / "account_type=nisa" 等
}

// すべて nautilus enum と文字列表現を一致させる（serde rename_all = "SCREAMING_SNAKE_CASE"）
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum OrderSide { Buy, Sell }

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum OrderType {
    Market, Limit, StopMarket, StopLimit, MarketIfTouched, LimitIfTouched,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TimeInForce {
    Day, Gtc, Gtd, Ioc, Fok, AtTheOpen, AtTheClose,
}

// ModifyOrder で変更可能なフィールド（すべて Option; None は変更しない意味）
#[derive(Serialize, Deserialize)]
pub struct OrderModifyChange {
    pub new_quantity: Option<String>,        // 変更後数量（精度保持文字列）
    pub new_price: Option<String>,           // 変更後指値価格
    pub new_trigger_price: Option<String>,   // 変更後逆指値トリガー価格
    pub new_expire_time_ns: Option<i64>,     // 変更後 GTD 期日（UTC ns）
}

// GetOrderList のフィルタ条件
#[derive(Serialize, Deserialize)]
pub struct OrderListFilter {
    pub status: Option<String>,        // nautilus OrderStatus 文字列（例 "ACCEPTED"）; None = 全状態
    pub instrument_id: Option<String>, // 例 "7203.TSE"; None = 全銘柄
    pub date: Option<String>,          // YYYYMMDD JST; None = 当日
}

pub enum Event {
    // 既存 ...
    // nautilus の OrderEvent タクソノミーに合わせる
    OrderSubmitted   { client_order_id: String, ts_event_ms: i64 },
    OrderAccepted    { client_order_id: String, venue_order_id: String, ts_event_ms: i64 },
    OrderRejected    { client_order_id: String, reason_code: String, reason_text: String, ts_event_ms: i64 },
    OrderPendingUpdate { client_order_id: String, ts_event_ms: i64 },
    OrderPendingCancel { client_order_id: String, ts_event_ms: i64 },
    OrderCanceled    { client_order_id: String, venue_order_id: String, ts_event_ms: i64 },
    OrderExpired     { client_order_id: String, venue_order_id: String, ts_event_ms: i64 },
    OrderFilled {
        client_order_id: String,
        venue_order_id: String,
        trade_id: String,                // 立花 p_eda_no（重複検知キー）
        last_qty: String,
        last_price: String,
        cumulative_qty: String,
        leaves_qty: String,              // 残量。0 なら全約定（nautilus と同じ意味）
        ts_event_ms: i64,
    },
    OrderListUpdated { request_id: String, orders: Vec<OrderRecordWire> },
}

// OrderListUpdated の要素型（CLMOrderList / CLMOrderListDetail から写像）
#[derive(Serialize, Deserialize)]
pub struct OrderRecordWire {
    pub client_order_id: Option<String>,   // WAL 復元で紐付け済みなら Some、他端末発注なら None
    pub venue_order_id: String,            // 立花 sOrderNumber
    pub instrument_id: String,             // "<sIssueCode>.TSE"
    pub order_side: OrderSide,
    pub order_type: OrderType,
    pub quantity: String,                  // 元発注数量
    pub filled_qty: String,                // 約定済み数量
    pub leaves_qty: String,                // 残数量
    pub price: Option<String>,             // 指値価格
    pub trigger_price: Option<String>,     // 逆指値トリガー
    pub time_in_force: TimeInForce,
    pub expire_time_ns: Option<i64>,
    pub status: String,                    // nautilus OrderStatus 文字列（例 "ACCEPTED"）
    pub ts_event_ms: i64,                  // 立花の注文受付日時（JST → Unix ms、他 IPC イベントと単位統一）
}
```

**`OrderPartiallyFilled` は持たない**: nautilus では「約定が起きるたびに `OrderFilled` を出し、`leaves_qty` で部分か全部かを判定」する流儀。本計画も合わせる。

**`second_password` の IPC 扱い**:
- 既存の `TachibanaCredentialsWire.second_password: Option<String>` フィールドは Phase 1 から存在するが、Order Phase では **常に `None` で送信する**（`SetVenueCredentials` 経由では第二暗証番号を渡さない）
- 発注時の第二暗証番号は専用の `Command::SetSecondPassword { value: String }` コマンドのみで伝達する。IPC payload（`SubmitOrderRequest` 等）には含めない
- `TachibanaCredentialsWire.second_password` フィールドは **Phase O1 完了 PR で削除する**（Phase 1 の既存コードとの互換を O0 段階だけ保つ。O1 完了 PR のタスクに削除を含めること）

## 4. 冪等性（flowsurface `agent_session_state.rs` の移植）

`src/api/order_session_state.rs`（新設）:

```rust
pub struct ClientOrderId(pub String);

// nautilus OrderStatus と同じ文字列を保持する型エイリアス
// 値は §10.5 の写像表の右列（"SUBMITTED" / "ACCEPTED" / ... / "REJECTED"）に限定
// enum にせず String にすることで、将来の nautilus バージョン追加に対して後方互換を保つ
type NautilusOrderStatus = String;

pub struct AgentOrderRecord {
    pub venue_order_id: Option<String>, // 立花 sOrderNumber; Python 応答受領後に埋める（未確定時は None）
    pub request_key: u64,               // 入力 body の構造ハッシュ
    pub status: NautilusOrderStatus,    // nautilus OrderStatus 文字列（§10.5）
}

pub enum PlaceOrderOutcome {
    Created { client_order_id: ClientOrderId },
    IdempotentReplay { order_number: String },
    Conflict { existing_order_number: String },
}

pub struct OrderSessionState {
    map: HashMap<ClientOrderId, AgentOrderRecord>,
}
```

flowsurface との差分:
- 立花は **`venue_order_id` がサーバから後で返ってくる**ため、`Created` 受領時点では `venue_order_id = None`。Python 応答で `update_venue_order_id()` を呼んで埋める
- セッションが日跨ぎで切れるので **当日分のみ保持**
- **プロセス再起動跨ぎは監査ログ WAL（§4.2）から復元**

### 4.1 `request_key` の canonicalization

`Conflict` 判定の正本となる `request_key: u64` は **以下の規則で計算**する（実装時はこの規則をテストで pin する）:

1. `SubmitOrderRequest` の以下フィールドのみを使う:
   - `instrument_id`, `order_side`, `order_type`, `quantity`, `price`, `trigger_price`, `trigger_type`, `time_in_force`, `expire_time_ns`, `post_only`, `reduce_only`, `tags`
2. `tags: Vec<String>` は **昇順ソート + 重複排除**したうえでハッシュ対象に含める
3. `client_order_id` 自身は含めない（key 算出と key 自身の循環を避ける）
4. `request_id` / `venue` は含めない（同じ注文の再送で別値になり得るため）
5. ハッシュ関数は `xxhash::xxh3_64`。ソルトは固定 `b"order_request_key_v1"`
6. 数値型は **文字列のまま**（`Quantity` / `Price` は str）。`null` は空文字に正規化しない（`Some("")` と区別する）

これにより:
- 「`tags` の順序違いで Conflict」を回避
- 浮動小数の精度ゆらぎで Conflict することがない（文字列保持）
- スキーマ拡張時に `request_key_version` を上げれば過去 WAL は別 namespace で扱える

### 4.2 監査ログ WAL（write-ahead log）

`tachibana_orders.jsonl`（[spec.md §3.2](./spec.md#32-安全装置誤発注防止)）を **発注前後 2 段階で append** し、起動時に復元できる WAL として使う:

```
{"phase":"submit", "ts":..., "client_order_id":"...", "request_key":12345, "instrument_id":"7203.TSE", ...}
{"phase":"accepted", "ts":..., "client_order_id":"...", "venue_order_id":"sOrderNumber=ABC", "p_no":...}
{"phase":"rejected", "ts":..., "client_order_id":"...", "reason_code":"...", "reason_text":"..."}
```

- `submit` 行は **HTTP 送信直前**に `fsync` 込みで書く（クラッシュ時の不整合を最小化）
- `accepted` / `rejected` 行は応答受領後に `flush` で書く（`fsync` 不要）
  - `accepted` が OS バッファ残りのままクラッシュした場合、Rust 起動時は `unknown` 状態で復元する。
    Phase O1 の `GetOrderList` で `venue_order_id` を補完できるため許容する
- **第二暗証番号は絶対に出さない**（unit テストで `grep -i second_password` 等で検証）

### 4.3 起動時復元（Phase O0 必須）

**WAL 読み込みオーナーシップ**: WAL ファイル (`tachibana_orders.jsonl`) は Python が書き、**Rust が直接読む**。`data_path()` は Rust 側が起動時 config から知っている（Python IPC 経由で受け取る必要はない）。Rust アプリ起動直後（Python プロセス起動の前）に読むため、ファイルが存在しない場合は空 map で初期化すればよい（初回起動 / 昨日以前のみ）。

**WAL パス安定性**: `data_path()` を設定で変更すると旧 WAL が読まれず重複発注防止が機能しなくなる。`tachibana_orders.jsonl` のパスは `data_path()` 変更後も旧パスを参照できるよう、**起動 config の `data_path` を変更したら旧 WAL を手動で移動またはリセットする**運用手順をドキュメントに記載すること（Phase O0 リリースノートに追記）。

**同時アクセス**: Rust は Python プロセス起動前に WAL を読み（§4.3 の順序）、その後は Python のみが append する。両プロセスが同時にアクセスするウィンドウは原則ない。ただし Python 再起動タイミングで競合しうる場合は Python 側で `fcntl.flock(f, LOCK_EX)` を `submit` 行書き込み前後に取得・解放すること（Windows では `msvcrt.locking` で代替）。

[implementation-plan T0.7](./implementation-plan.md) で以下を Phase O0 段階で実装:

1. アプリ起動 → `OrderSessionState::new()` → 当日分 WAL を読み戻し
2. `submit` だけがあって `accepted`/`rejected` が無い行は **「unknown 状態」**で復元（`venue_order_id = None`）
3. ユーザーが同一 `client_order_id` で再送 → 下記ルールで処理（重複発注防止の本丸）
4. unknown 状態の解決は `Phase O1` の `GetOrderList` 復元（[T1.5](./implementation-plan.md#t15-起動時の台帳復元)）で `venue_order_id` を埋める

**unknown 状態への再送挙動（確定）**:

| 再送の request_key | venue_order_id | 挙動 |
|---|---|---|
| 元の request_key と一致 | None（unknown） | `IdempotentReplay` を返す。HTTP 202 + `{"status": "SUBMITTED", "venue_order_id": null, "warning": "order_status_unknown"}` を返し、クライアントに `GetOrderList` で確認させる |
| 元の request_key と不一致 | None（unknown） | `Conflict` → 409 Conflict を返す。重複発注を防ぐため、unknown でも別 body は拒否 |
| — | Some（確定済み） | `IdempotentReplay` → HTTP 200 + `{"venue_order_id": "<sOrderNumber>"}` |

**クライアントの責務**: `IdempotentReplay` で `venue_order_id: null` が返ったときは `GET /api/order/list` で当日台帳を照合してから次の操作に進むこと。

## 5. 第二暗証番号の取扱い

**Q1 確定（2026-04-25）**: **keyring 不採用 / セッション中メモリ保持 / 初回発注時に iced modal で取得**。理由:

- 実弾発注の鍵を keyring に置きっぱなしは OS 全体侵害時の被害が大きすぎる
- nautilus 互換要件（spec §6）で「Strategy 層に第二暗証番号を見せない」を採用するため、Python メモリ保持 1 箇所に閉じる方が一貫
- 「毎回入力」は UX が破綻するため折衷で「セッション中保持」
- keyring opt-in は **提供しない**（案を増やすと攻撃面・実装複雑度が上がるため）

### 5.1 取得タイミング（Phase O0）

- ログイン時には収集しない（[tachibana/architecture.md F-H5](../tachibana/architecture.md) の Phase 1 方針を維持）
- `POST /api/order/submit` で **Python 側に未保持なら** Rust 側に `Event::SecondPasswordRequired { request_id }` を返す → iced 側で modal を出して入力 → `Command::SetSecondPassword { value }` で Python に渡す → Python メモリに保持 → 元の発注リクエストを再開
- 同一プロセス内では以降の発注で再入力不要（メモリヒット）
- `data::config::tachibana::TachibanaCredentials.second_password: Option<SecretString>` は **常に `None` のまま**（keyring に書かない）

### 5.2 入力 UI

- **iced 側 modal**（tkinter ではない）。発注フォームの隣で完結させ UX を保つ
- ユーザーがキャンセルした場合、`/api/order/submit` は 403 + `reason_code = "SECOND_PASSWORD_REQUIRED"`（spec.md §5.2 の SCREAMING_SNAKE_CASE に統一）
- ログインダイアログ（tkinter）には第二暗証番号フィールドを **追加しない**

### 5.3 メモリ保持

- Python 側 `tachibana_session_holder` に `second_password: SecretStr | None` を追加
- 発注時のみ `expose_secret()` し、リクエスト送信後は **ローカル変数を削除**（function-local scope）
- セッション切れ（`p_errno=2`）検知時は `second_password` も **クリア**（再ログイン時に再入力させる）
- プロセス終了時に消える（永続化なし）
- Python プロセスのコアダンプ・スワップ対策は `pydantic.SecretStr` に依存（best-effort）

### 5.4 forget API

- `POST /api/order/forget-second-password` を提供。ユーザーが「席を離れる」前に明示的にメモリから消せる

## 6. EVENT EC フレームのパース（Phase O2）

`tachibana_event.py._parse_ec_frame(items: list[tuple[str, str]]) -> OrderEcEvent`:

立花 EC フレームの主要項目（マニュアル §`#CLMEvent_EC` 参照）:

| 立花フレームキー | 意味 | IPC への写像 |
|---|---|---|
| `p_NO` | 注文番号 | `venue_order_id` |
| `p_EDA` | 約定枝番（eda_no） | `trade_id`（IPC `OrderFilled.trade_id`、重複検知キー） |
| `p_NT` | 通知種別（注文受付・約定・取消・失効） | `OrderAccepted` / `OrderFilled` / `OrderCanceled` / `OrderExpired` への分岐 |
| `p_DH` | 約定単価 | `last_price` |
| `p_DSU` | 約定数量 | `last_qty` |
| `p_ZSU` | 残数量 | `leaves_qty`（部分約定判定。0 なら全約定） |
| `p_OD` | 約定日時 | `ts_event_ms`（注文日時ではなく **約定日時**を使うこと） |

**重複検知**: `(venue_order_id, trade_id)` の組をプロセスメモリ `set[tuple[str, str]]` に保持。IPC フィールド名 `trade_id` に統一する（`eda_no` / `p_EDA` / `p_eda_no` の混用を禁止）。再接続時の再送はここで弾く。

## 7. 設定値（起動 config / env）

```toml
# pyproject 管轄外、起動 CLI / config ファイル経由
[tachibana.order]
max_qty_per_order = 1000
max_yen_per_order = 1_000_000
require_confirmation = true            # Rust UI: 確認モーダル必須
```

env:
- `TACHIBANA_ALLOW_PROD=1` … 本番 URL での発注解禁（Phase 1 と共通ガード）
- `DEV_TACHIBANA_SECOND_PASSWORD` … debug ビルド + Python `tachibana_login_flow.py` のみが読む（SKILL.md S2）。release では無視

## 8. flowsurface との対応表

実装時に「ここは flowsurface のどこを写すか」を即引けるよう一覧化:

| 本計画の Python シンボル | flowsurface Rust シンボル | 備考 |
|---|---|---|
| `tachibana_orders.TachibanaWireOrderRequest` (pydantic, 立花 wire 専用) | `tachibana::NewOrderRequest` | **これは内部 wire 型のみ**。public API は nautilus 互換 `NautilusOrderEnvelope` を受け取り、内部で `TachibanaWireOrderRequest` に写像する |
| `tachibana_orders.TachibanaWireModifyRequest` | `tachibana::CorrectOrderRequest` | "correct" 用語は Python 内部に閉じる |
| `tachibana_orders.TachibanaWireCancelRequest` | `tachibana::CancelOrderRequest` | 同 |
| `tachibana_orders.NewOrderResponse` | `tachibana::NewOrderResponse` | `sWarningCode` / `sWarningText` も含める。**HTTP レスポンスの `warning_code` / `warning_text` フィールドに露出させること**（spec.md §4 の `POST /api/order/submit` レスポンスに追記済み）。立花が注文を受け付けつつ警告を返した場合にユーザーが気付けるようにする |
| `tachibana_orders.ModifyOrderResponse` | `tachibana::ModifyOrderResponse` | 同 |
| `tachibana_orders.OrderListRequest/Response/Record` | `tachibana::OrderListRequest/Response/OrderRecord` | 同 |
| `tachibana_orders.submit_new_order()` | `tachibana::submit_new_order()` | 戻り値型は `Result[NewOrderResponse, OrderRejectedError]` |
| `tachibana_orders.submit_cancel_order()` | `tachibana::submit_cancel_order()` | 同 |
| `tachibana_orders._compose_request_payload()` | `tachibana::serialize_order_request()` | `p_no` / `p_sd_date` / `sCLMID` 後付け |
| `src/api/order_session_state.rs::OrderSessionState` | `flowsurface/src/api/agent_session_state.rs::AgentSessionState` | **Rust → Rust の移植**（Python ではない） |
| `src/api/order_session_state.rs::PlaceOrderOutcome` | 同上 `PlaceOrderOutcome` | 同 |

**`TachibanaWireOrderRequest.__repr__` のマスク対象**: `second_password` だけでなく、flowsurface `NewOrderRequest` の `Debug` 手実装に倣い **`user_id` / `password` もマスク**すること（エラーログへの認証情報混入防止）。

## 9. Python 単独モードへの含み

将来 Rust（iced）を外しても、本計画の Python レイヤーはそのまま動く:
- `tachibana_orders.py` は HTTP/IPC 非依存
- 冪等性マップだけは Rust 側にあるため、Python 単独モード時は Python で同等のものを書く必要がある（[`python/engine/order_session.py`](../../../python/engine/) として将来追加）

## 10. nautilus_trader との型マッピング

[spec.md §6](./spec.md#6-nautilus_trader-互換要件不変条件) の不変条件を実装に落としたマッピング表。N2 移行時の作業はこの表の右 2 列を入れ替えるだけ。

### 10.1 OrderType 写像

| 本計画 / nautilus 共通 | 立花 `CLMKabuNewOrder` 表現 | 備考 |
|---|---|---|
| `MARKET` | `sOrderPrice="0"`, `sCondition="0"` | 成行 |
| `LIMIT` | `sOrderPrice=<price>`, `sCondition="0"` | 指値 |
| `STOP_MARKET` | `sOrderPrice="*"`, `sGyakusasiZyouken=<trigger>`, `sGyakusasiPrice="0"` | 逆指値成行 |
| `STOP_LIMIT` | `sOrderPrice=<price>`, `sGyakusasiZyouken=<trigger>`, `sGyakusasiPrice=<price>` | 逆指値指値（立花は同値運用） |
| `MARKET_IF_TOUCHED` | 立花直接対応なし → **400 reject** + 推奨案内 | nautilus からの呼出時は client 側で STOP に書き換えるよう案内 |
| `LIMIT_IF_TOUCHED` | 同上 | 同上 |

### 10.2 TimeInForce 写像

| 本計画 / nautilus 共通 | 立花表現 | 備考 |
|---|---|---|
| `DAY` | `sCondition="0"`, `sOrderExpireDay="0"` | 当日 |
| `GTC` | 立花直接対応なし → **400 reject** | 立花は最大 10 営業日。GTC は概念上不可 |
| `GTD` | `sOrderExpireDay=YYYYMMDD`（`expire_time` から JST 営業日に変換） | 10 営業日上限の検証は Python 側 |
| `IOC` | 立花直接対応なし → **400 reject** | 立花の即時執行系は「不成」(`sCondition=6`) のみ |
| `FOK` | 立花直接対応なし → **400 reject** | 同上 |
| `AT_THE_OPEN` | `sCondition="2"`（寄付） | |
| `AT_THE_CLOSE` | `sCondition="4"`（引け）または `"6"`（不成）| `tags=["close_strategy=funari"]` で `6` を選べる拡張 |

#### 逆写像（CLMOrderList レスポンス → nautilus、Phase O1 で使用）

`CLMOrderList` / `CLMOrderListDetail` の `sCondition` を `OrderRecordWire.time_in_force` に戻す写像:

| 立花 `sCondition` | `sOrderExpireDay` | nautilus `TimeInForce` |
|---|---|---|
| `"0"` | `"0"`（当日） | `DAY` |
| `"0"` | YYYYMMDD（将来日） | `GTD` |
| `"2"` | — | `AT_THE_OPEN` |
| `"4"` | — | `AT_THE_CLOSE` |
| `"6"` | — | `AT_THE_CLOSE`（`tags` に `close_strategy=funari` を付与して返す） |

GTC / IOC / FOK / GTC は立花が対応しないため逆写像不要。

### 10.3 OrderSide 写像

| nautilus | 立花 `sBaibaiKubun` |
|---|---|
| `BUY` | `"3"` |
| `SELL` | `"1"` |

立花固有の `5`=現渡 / `7`=現引 は **`tags=["close_action=physical_settle_*"]`** で `BUY` / `SELL` に sub-classification する。OrderSide enum は拡張しない。

### 10.4 venue extension `tags` の正規化キー

これが **tag の正本レジストリ**。[spec.md §5.1](./spec.md#51-nautilus-互換のリクエストシェイプ) はサンプル提示のみ。新キーを追加する PR はこの表を更新すること。

**`cash_margin` 値**（`sGenkinShinyouKubun` への写像）:

| tag 値 | 立花値 | 意味 |
|---|---|---|
| `cash_margin=cash` | `"0"` | 現物（省略時の既定） |
| `cash_margin=margin_credit_new` | `"2"` | 制度信用 新規 |
| `cash_margin=margin_credit_repay` | `"4"` | 制度信用 返済 |
| `cash_margin=margin_general_new` | `"6"` | 一般信用 新規 |
| `cash_margin=margin_general_repay` | `"8"` | 一般信用 返済 |

**`account_type` 値**（`sZyoutoekiKazeiC` への写像）— **省略時はログイン応答の `sZyoutoekiKazeiC` をパススルー**（口座属性と一致させる意図）:

| tag 値 | 立花値 | 意味 |
|---|---|---|
| `account_type=specific_with_withholding` | `"1"` | 特定預り（源泉徴収あり） |
| `account_type=specific_without_withholding` | `"3"` | 特定預り（源泉徴収なし） |
| `account_type=general` | `"0"` | 一般預り |
| `account_type=nisa_growth` | `"5"` | NISA 成長投資枠（Phase O4） |
| `account_type=nisa_tsumitate` | `"6"` | NISA つみたて投資枠（Phase O4） |

**その他**:

| tag 値 | 立花への写像 | 用途 |
|---|---|---|
| `close_action=physical_settle_buy` | `sBaibaiKubun="7"`（現引） | 信用買建玉の現物受渡 |
| `close_action=physical_settle_sell` | `sBaibaiKubun="5"`（現渡） | 信用売建玉の現物受渡 |
| `close_strategy=funari` | `sCondition="6"`（不成） | `AT_THE_CLOSE` 併用時のみ有効（不成注文） |
| `tategyoku=<id>` | `sTatebiType="1"` + `aCLMKabuHensaiData[*]` | 信用返済の建玉個別指定（複数指定可） |

**バリデーション規則**（`_compose_request_payload` 内）:
- `cash_margin=*` / `account_type=*` の**同種重複は 400 reject**（`reason_code="VENUE_UNSUPPORTED"`, `reason_text="CONFLICTING_TAGS: <details>"`）
- 信用 + NISA など立花が拒否する組合せも同様に reject
- `close_strategy=funari` は **`time_in_force=AT_THE_CLOSE` 以外との組合せは 400 reject**（`reason_code="VENUE_UNSUPPORTED"`, `reason_text="CONFLICTING_TAGS: close_strategy=funari requires AT_THE_CLOSE"`）
- 未知 tag は warn して無視（fail-open、前方互換）
- Rust HTTP 層では `tags` の各要素が `key=value` 形式（ASCII printable、`=` を 1 つ含む）であることのみ検証する。内容バリデーションは Python 側責務

**新しい tag を追加するルール**: `key=value` 形式でこの表に登録してから `tachibana_orders.TAGS_REGISTRY` に反映する。

### 10.5 OrderStatus 写像（nautilus 完全準拠）

| 立花の状態 | nautilus `OrderStatus` |
|---|---|
| 注文受付（IPC 送信完了、立花応答待ち） | `SUBMITTED` |
| 立花が `sOrderNumber` 採番 | `ACCEPTED` |
| 訂正リクエスト送信中 | `PENDING_UPDATE` |
| 取消リクエスト送信中 | `PENDING_CANCEL` |
| 部分約定発生（`leaves_qty > 0`） | `ACCEPTED`（nautilus 流: ステートは変えず `OrderFilled` イベントだけ発行） |
| 全約定（`leaves_qty == 0`） | `FILLED` |
| 取消完了 | `CANCELED` |
| 期日切れ（`sOrderExpireDay` 経過） | `EXPIRED` |
| 立花から拒否（`sResultCode≠0`） | `REJECTED` |

### 10.6 nautilus 移行時の差分（N2 で実施する作業のみ）

本計画完了時点で以下が実装されている前提:
- `python/engine/exchanges/tachibana_orders.py` の API surface は nautilus と互換
- IPC `SubmitOrder` / 各 `OrderEvent` の field 名・enum 値が nautilus と一致

N2 で行う作業:
1. `pyproject.toml` に `nautilus_trader` 依存を追加
2. `python/engine/nautilus/clients/tachibana.py` を新設し `nautilus_trader.live.execution_client.LiveExecutionClient` を継承
3. `LiveExecutionClient.submit_order(command)` の中身は `tachibana_orders.submit_order(...)` を呼ぶだけ
4. 本計画の Rust 側 HTTP API `/api/order/*` は **そのまま残す**（手動発注・curl 経路は維持）。nautilus 戦略経由の発注は HTTP を経由せず、`LiveExecutionEngine` から直接 Python ワーカーに入る
5. `OrderSessionState`（Rust 冪等性マップ）は nautilus 経由フローでは不要だが、HTTP API 経路では引き続き使う（撤去しない）

つまり **N2 は新規ファイルを足すだけで、既存の本計画コードを書き換えない**ことが目標。本計画の Phase O0–O3 のレビューチェックリストに「nautilus 移行時に書き換えが発生しないか」を毎回入れる。
