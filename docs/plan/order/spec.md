# 立花注文機能: 仕様

## 1. ゴール

1. **新規注文**: 現物・信用（制度／一般、新規／返済）の買・売を成行・指値・逆指値で発注できる
2. **訂正注文**: 値段・株数・期日・条件の変更
3. **取消注文**: 個別取消・全件取消
4. **注文一覧**: 当日の注文・約定状況を取得し UI に表示
5. **約定通知**: EVENT WebSocket の `EC` フレームをリアルタイム購読し、UI 反映と冪等性マップに連携
6. **デモ環境のみ**で実動作確認、本番は明示フラグ（`TACHIBANA_ALLOW_PROD=1`）併用時のみ解禁

## 2. スコープ

### 2.1 Phase O0 — 第二暗証番号の収集と単純な現物成行発注（MVP）

- 立花 Phase 1 spec §3.1 F-H5 の制約を **本フェーズで解禁**:
  - `data::config::tachibana::TachibanaCredentials.second_password` は **常に `None` のまま**（keyring に書かない。[architecture.md §5](./architecture.md#5-第二暗証番号の取扱い) Q1 確定）
  - 収集は iced modal（tkinter ではない）で発注時のみ。Python メモリ保持を有効化
- 新規注文 API: `CLMKabuNewOrder`（現物のみ・成行のみ・買のみ・東証 `00`）
- HTTP API `POST /api/order/submit` を Rust 側に新設
- 結果は同期的に Python から戻す（`sOrderNumber` を含む）
- 約定通知購読は **本フェーズでは行わない**（注文一覧ポーリングで状態確認）

### 2.2 Phase O1 — 訂正（modify）・取消・注文一覧

- `CLMKabuCorrectOrder` / `CLMKabuCancelOrder` / `CLMKabuCancelOrderAll`
- `CLMOrderList` / `CLMOrderListDetail` で注文台帳取得
- HTTP API:
  - `POST /api/order/modify`（nautilus 用語に統一。立花の "correct" 用語は Python `_compose_request_payload` 内に閉じる）
  - `POST /api/order/cancel`
  - `POST /api/order/cancel-all`
  - `GET /api/order/list`（フィルタ: `status` / `issue_code` / `date`）
- UI: 注文一覧パネル（Rust iced 側、新設 or 既存 dashboard 拡張）

### 2.3 Phase O2 — EVENT EC 約定通知の購読と UI 反映

- `python/engine/exchanges/tachibana_event.py`（Phase 1 で FD 用に既にあるはず）に `EC` パーサを追加
- パース後 `Event::OrderAccepted` / `OrderFilled` / `OrderRejected` / `OrderCanceled` を IPC で Rust に流す（**部分約定も `OrderFilled` として発火し、`leaves_qty` で部分/全部を判定する nautilus 流**。`OrderPartiallyFilled` は持たない）
- Rust UI 側で notification toast + 注文一覧の即時更新
- 接続復旧時は当日分の `EC` を立花が再送するため **重複検知**（`p_eda_no` 単位の seen-set）が必要

### 2.4 Phase O3 — 信用取引・逆指値・期日指定

- `sGenkinShinyouKubun = 2/4/6/8`（信用新規・返済の制度・一般）
- `sGyakusasiOrderType` / `sGyakusasiZyouken` / `sGyakusasiPrice`
- `sOrderExpireDay = YYYYMMDD`（10 営業日まで）
- 信用返済の建玉個別指定（`sTatebiType=1` + `aCLMKabuHensaiData`）
- 余力 API: `CLMZanKaiKanougaku` / `CLMZanShinkiKanoIjiritu` / `CLMZanUriKanousuu` を発注前ガードとして利用

### 2.5 含めないもの

**REPLAY モード仮想注文**: 本計画のスコープ外。**[nautilus_trader 統合 Phase N1](../nautilus_trader/README.md#replay-モード仮想注文の取り込み)** で実装する。本計画の Python 関数（`tachibana_orders.NautilusOrderEnvelope` 等）と HTTP API は N1 でそのまま再利用される設計（live / replay ディスパッチャを N1 で前段に追加）。

**Phase O4+ に送り**:

- **NISA 口座での発注**（`sZyoutoekiKazeiC=5/6`）— `sZyoutoekiKazeiC` はログイン応答の値をそのまま流す方針なので技術的には差は小さいが、**枠管理 UI** が必要なため別フェーズ
- **注文 GUI のリッチ化**（チャート上クリックで発注、ホットキー、ワンクリックトレード）— UI 詳細は本計画では薄い実装に留め、UX は別 plan で詰める
- **複数アカウント**
- **本番接続のデフォルト UI 露出**
- **ヒストリカル約定エクスポート**

## 3. 非機能要件

### 3.1 セキュリティ

- 第二暗証番号（`sSecondPassword`）の取り扱い:
  - **保存しない方針を再検討**: keyring に保存するか、起動セッションごとに毎回入力させるか。Q1 で確定（[open-questions.md](./open-questions.md)）
  - 暫定: **OS keyring に保存し、起動時に復元**（user_id/password と同じ扱い）
  - Python メモリ上は `SecretStr`（pydantic）で保持し、`__repr__` で `***` 化
  - **ログに絶対出さない**: `Debug` 系のフォーマット時はマスク（flowsurface の `NewOrderRequest` の手動 `Debug` 実装に倣う）
- 仮想 URL は SKILL.md R10 と同じくマスク
- HTTP API の認証: 既存 `/api/replay/*` と同じトークン方式（[`src/api/`](../../../src/api/) の既存ガード）。**localhost-only バインドを維持**

### 3.2 安全装置（誤発注防止）

- **デモ環境強制**: `TACHIBANA_ALLOW_PROD=1` 未設定なら本番 URL に発注リクエストを送らない（Python URL builder で reject）
- **数量・金額上限**: 起動 config で 1 注文最大株数 / 最大金額を必ず指定。未指定なら `/api/order/submit` を 503 で reject（明示 opt-in）
- **発注確認モーダル**（UI 側、Phase O1）: 成行発注時は明示的な確認ダイアログを出す
- **発注ログ**: `data_path()/tachibana_orders.jsonl` に append（人間監査用、第二暗証番号は除外）
- **冪等性キー必須**: HTTP API 経由の `/api/order/submit` は `client_order_id` を必須にし、再送時は同じ `sOrderNumber` を返す（flowsurface `agent_session_state.rs` パターン）

### 3.3 信頼性

- `p_errno=2`（session 切れ）→ 即座に発注停止 + バナー表示（Phase 1 の経路を流用）
- 約定通知の重複検知: `p_eda_no` キーで seen-set を持つ
- ネットワーク切断中の発注は **待たずに reject**（タイムアウトで詰まると誤発注の温床）

### 3.4 観測性

- 全発注に `client_order_id`（UUID v4）と `request_id`（IPC 相関）を埋める
- `tachibana_orders.jsonl` の各行に `client_order_id` / `sOrderNumber` / `p_no` / `sResultCode` / `sWarningCode` を入れる

## 4. 公開 API（HTTP）

すべて localhost のみ。既存トークンガードに乗る。

| メソッド | パス | リクエスト | レスポンス | フェーズ |
|---|---|---|---|---|
| `POST` | `/api/order/submit` | `{client_order_id, instrument_id, order_side, order_type, quantity, price?, time_in_force, ...}` (§5.1) | 201: `{client_order_id, venue_order_id, status: "ACCEPTED", warning_code?, warning_text?}` / 202: `{status: "SUBMITTED", venue_order_id: null, warning: "order_status_unknown"}`（idempotent replay で unknown） | O0 |
| `POST` | `/api/order/modify` | `{client_order_id, quantity?, price?, trigger_price?, expire_time?}` または `{venue_order_id, quantity?, price?, trigger_price?, expire_time?}`（他端末注文） | `{client_order_id, status: "PENDING_UPDATE"}` | O1 |
| `POST` | `/api/order/cancel` | `{client_order_id}` または `{venue_order_id}`（他端末注文。`client_order_id` 不明時のみ） | `{client_order_id, status: "PENDING_CANCEL"}` | O1 |
| `POST` | `/api/order/cancel-all` | `{instrument_id?, order_side?, confirm: true}`（`confirm: true` は **JSON body 必須**、query param ではない）。**Phase O0 時点ではこのエンドポイントは未実装（501 Not Implemented を返す）** | `{count}` | O1 |
| `GET` | `/api/order/list` | クエリ: `status?` / `instrument_id?` / `date?` | `{orders: [...]}` | O1 |
| `POST` | `/api/order/forget-second-password` | （body 無し） | `{status: "OK"}` | O0 |
| `GET` | `/api/order/positions` | — | 現物・信用建玉 | O3 |
| `GET` | `/api/order/buying-power` | — | 余力 | O3 |

**重要**: API は **`client_order_id` を一次キー**として動作する（nautilus 流）。`venue_order_id`（立花 `sOrderNumber`）は応答に含めるが、後続の `/modify` `/cancel` 入力は `client_order_id` で受ける。Rust 側 `OrderSessionState` が双方向写像を保持。WAL 復元で `client_order_id` が不明な「他端末経由の当日注文」のみ `venue_order_id` での `/modify` `/cancel` を受理する（[architecture.md §4.3](./architecture.md#43-起動時復元phase-o0-必須) / [T1.5](./implementation-plan.md#t15-起動時の台帳復元)）。

**`client_order_id` 発行元（Q2 確定: 2026-04-25）**: クライアント側で UUID v4 を生成して送る（flowsurface 流・案 A）。Rust 側は受け取った値を idempotency key として使い、独自に採番しない。iced 側発注フォームは送信時に `Uuid::new_v4()` を生成する。HTTP 直叩きユーザーは送信側責務。

JSON Schema は [`docs/plan/✅python-data-engine/schemas/`](../✅python-data-engine/schemas/) に追加（schema 1.3）。

## 5. 入力バリデーション（Rust HTTP 層）

Python に渡す前に **Rust 側で**早期に弾く:

- `client_order_id`: 任意の文字列（UUID v4 推奨）。nautilus `ClientOrderId` 制約に合わせ **長さ 1〜36、ASCII printable のみ**
- `instrument_id`: `<symbol>.<venue>` 形式。**Phase O0〜O2 は東証（`TSE`）のみ受理**（例 `7203.TSE`）。大証(OSE)・名証(NSE)等への `sSizyouC` 写像は O3 以降で対応（[open-questions.md Q9](./open-questions.md) として追跡）
- `order_side`: `"BUY"` / `"SELL"`（nautilus `OrderSide` enum 文字列）
- `order_type`: `"MARKET"` / `"LIMIT"` / `"STOP_MARKET"` / `"STOP_LIMIT"` の 4 種のみ受理。nautilus `OrderType` には `MARKET_IF_TOUCHED` / `LIMIT_IF_TOUCHED` も存在するが、立花が直接対応しないため **HTTP 層で 400 reject**（`reason_code="VENUE_UNSUPPORTED"`、[architecture.md §10.1](./architecture.md#101-ordertype-写像)）。Phase O0 は `MARKET` のみ受理、O1 で `LIMIT`、O3 で `STOP_*` を順次解禁
- `quantity`: 正の整数文字列。**nautilus の `Quantity` は文字列（precision 保持）が基本**なので合わせる。単元株チェックは Python 側で master 突合せ
- `price`: `order_type ∈ {LIMIT, STOP_LIMIT}` のとき必須、文字列。呼値単位の丸めは Python 側
- `time_in_force`: `"DAY"` / `"GTD"` / `"AT_THE_OPEN"` / `"AT_THE_CLOSE"` の 4 種のみ受理。nautilus 列挙の `GTC` / `IOC` / `FOK` は立花が直接対応しないため **HTTP 層で 400 reject**（[architecture.md §10.2](./architecture.md#102-timeinforce-写像)）。Python 写像は `AT_THE_OPEN` → `sCondition=2`、`AT_THE_CLOSE` → `4`、`tags=["close_strategy=funari"]` 併用で `6`（不成）、それ以外は `0`
- `expire_time`: ISO8601、`time_in_force=GTD` のとき必須。Python 側で `sOrderExpireDay` (YYYYMMDD JST) に変換、10 営業日上限を Python 側で検証
- `trigger_price`: `order_type ∈ {STOP_MARKET, STOP_LIMIT}` のとき必須。立花 `sGyakusasiZyouken` に写像
- `tags`: Rust HTTP 層では各要素が `key=value` 形式（ASCII printable、`=` を 1 つ含む）であることのみ検証し 400 reject。内容（未知タグ・組合せ）の検証は Python 側 `_compose_request_payload` 内責務
- 上限（数量・金額）チェックは Python 側で master + 起動 config から

**`venue_order_id` による modify/cancel（Phase O1 での他端末注文対応）**: 起動時 WAL 復元で `client_order_id` が不明な注文（他端末・他アプリ経由の当日注文）に対しては、`POST /api/order/modify` と `POST /api/order/cancel` で `venue_order_id` を直接受け入れる。この場合 `client_order_id` は応答に含まれない（`null`）。`client_order_id` と `venue_order_id` が同時に指定された場合は `client_order_id` を優先する。

## 5.1 nautilus 互換のリクエストシェイプ

`POST /api/order/submit` の body は **nautilus `OrderFactory` の入力と field 名を揃える**:

```json
{
  "client_order_id": "uuid-v4",
  "instrument_id": "7203.TSE",
  "order_side": "BUY",
  "order_type": "MARKET",
  "quantity": "100",
  "price": null,
  "time_in_force": "DAY",
  "expire_time": null,
  "trigger_price": null,
  "trigger_type": null,
  "post_only": false,
  "reduce_only": false,
  "tags": ["cash_margin=cash", "account_type=specific_with_withholding"]
}
```

立花固有の `sBaibaiKubun` / `sGenkinShinyouKubun` / `sZyoutoekiKazeiC` 等は **`tags` で venue extension として渡す**（nautilus の慣習に合わせる）。`tags` の正本レジストリは [architecture.md §10.4](./architecture.md#104-venue-extension-tags-の正規化キー)。spec 側はサンプルのみで、定義は architecture 側に集約する。

**`account_type` のデフォルト**: `account_type=*` タグが未指定なら、Python 写像層は **ログイン応答の `sZyoutoekiKazeiC` 値をパススルー**する。HTTP 層は `account_type` 未指定を許容する。

**バリデーション規則**（`_compose_request_payload` 内）:
- `cash_margin=*` / `account_type=*` の **同種重複は 400 reject**（`UnsupportedOrderError(reason_code="CONFLICTING_TAGS")` → IPC で `OrderRejected{reason_code="VENUE_UNSUPPORTED", reason_text="CONFLICTING_TAGS: <details>"}`）
- 信用 + NISA など立花が拒否する組合せは Python 写像層で 400 reject
- 未知タグは silently ignore（前方互換）

写像規則は Python 側 `tachibana_orders._compose_request_payload` に集約。HTTP API 層にも、IPC 層にも、立花固有の用語を漏らさない。

## 5.2 reason_code 体系（観測性）

`OrderRejected{reason_code, reason_text}` の `reason_code` は以下の固定文字列のみ:

| reason_code | 発生条件 |
|---|---|
| `VALIDATION_ERROR` | Rust HTTP 層のスキーマ違反（不正 UUID, 数量負, instrument_id 形式違反） |
| `UNSUPPORTED_IN_PHASE_O0` | Phase O0 で許可されない `order_type` / `time_in_force` / `tags` |
| `VENUE_UNSUPPORTED` | Python 写像層が立花未対応の組合せと判定（CONFLICTING_TAGS / MARKET_IF_TOUCHED / GTC 等を含む） |
| `SECOND_PASSWORD_REQUIRED` | 第二暗証番号未保持で発注 |
| `SECOND_PASSWORD_INVALID` | 立花応答 `p_errno=4` 等（第二暗証番号エラー） |
| `MARKET_CLOSED` | 立花応答 `sResultCode` が時間外 |
| `INSUFFICIENT_FUNDS` | Phase O3 余力ガード失敗 |
| `SESSION_EXPIRED` | `p_errno=2` |
| `VENUE_REJECTED` | 立花応答 `sResultCode≠0` の上記以外（`reason_text` に立花コードと文言） |
| `ORDER_STATUS_UNKNOWN` | 起動時復元で `venue_order_id = None`（unknown）の注文への cancel / modify 要求。`GET /api/order/list` で確認後に再試行を促す |
| `INTERNAL_ERROR` | Rust / Python 内部例外（タイムアウトを含む） |

**`reason_text` フォーマット規約**:
- `VENUE_REJECTED` / `VENUE_UNSUPPORTED`: `"<TACHIBANA_CODE_OR_TAG>: <message>"` の 1 行（改行禁止、最大 512 文字）
- `VALIDATION_ERROR`: `"<field_name>: <reason>"`
- 観測性ダッシュボードが prefix grep できるよう、コロンの前は ASCII 大文字 + 数字 + `_` のみ

`reason_code` の追加には spec 更新を必須とする（観測性ダッシュボードが破壊されないため）。

## 6. nautilus_trader 互換要件（不変条件）

将来 [docs/plan/nautilus_trader/](../nautilus_trader/) Phase N2 で nautilus `LiveExecutionClient` に切り替えるとき、本計画で書く Python レイヤを **そのまま nautilus に組み込める**ことを設計目標とする。これに反する実装は禁止:

### 6.1 用語・型の整合（必須）

| 概念 | 本計画で使う名前 | nautilus 対応型 | 立花対応 |
|---|---|---|---|
| クライアント注文 ID | `client_order_id: str` | `nautilus_trader.model.identifiers.ClientOrderId` | 内部で生成・採番、立花には送らない |
| Venue 注文 ID | `venue_order_id: str` | `VenueOrderId` | `sOrderNumber` |
| 銘柄 ID | `instrument_id: str`（`SYMBOL.VENUE` 形式） | `InstrumentId` | `sIssueCode` + `sSizyouC` |
| 売買方向 | `order_side: "BUY"\|"SELL"` | `OrderSide` enum | `sBaibaiKubun` |
| 注文種別 | `order_type` 上記 6 種 | `OrderType` enum | `sOrderPrice` 等の組合せ |
| 期間指定 | `time_in_force` 上記 7 種 | `TimeInForce` enum | `sCondition` + `sOrderExpireDay` |
| 数量 | `quantity: str`（精度保持） | `Quantity` | `sOrderSuryou` |
| 価格 | `price: str` / `trigger_price: str` | `Price` | `sOrderPrice` / `sGyakusasiZyouken` |

文字列値（enum 表記）は **nautilus の文字列表現と完全一致**させる（nautilus の `OrderSide.BUY.name == "BUY"` 等）。`"buy"` / `"limit_order"` 等の独自表記は使わない。

### 6.2 イベントタクソノミー（必須）

IPC `Event::Order*` および HTTP `status` フィールドは nautilus のオーダーステートマシン名と一致させる:

```
INITIALIZED → SUBMITTED → ACCEPTED → ┬─→ FILLED
                                     ├─→ PARTIALLY_FILLED → FILLED
                                     ├─→ PENDING_UPDATE → ACCEPTED (or REJECTED)
                                     ├─→ PENDING_CANCEL → CANCELED
                                     ├─→ EXPIRED
                                     └─→ REJECTED
```

立花固有の状態遷移（例: 訂正受付待ち / 部分約定）はこの 9 状態に正規化して写像する。Python 側 `_map_tachibana_state_to_nautilus()` 関数を 1 箇所に置く。

### 6.3 Python 関数シグネチャ（必須）

`tachibana_orders.py` の関数は **nautilus の `LiveExecutionClient` 抽象メソッド**と引数順・型を揃える:

```python
async def submit_order(
    session: TachibanaSession,
    second_password: SecretStr,
    order: NautilusOrderEnvelope,         # nautilus Order 相当の純データクラス
) -> SubmitOrderResult: ...

async def modify_order(
    session, second_password,
    client_order_id: str,
    new_quantity: Optional[str],
    new_price: Optional[str],
    new_trigger_price: Optional[str],
    new_expire_time: Optional[datetime],
) -> ModifyOrderResult: ...

async def cancel_order(
    session, second_password,
    client_order_id: str,
    venue_order_id: str,                  # 立花は cancel に sOrderNumber 必須
) -> CancelOrderResult: ...
```

`NautilusOrderEnvelope` は **nautilus の `nautilus_trader.model.orders.Order` 互換のフィールド構成**を持つ純データクラス（pydantic）。N0 着手時に nautilus 本体を import せず、独自に同じ shape を切る。N2 で nautilus を導入したら **`from nautilus_trader.model.orders import Order` で置き換えるだけ**で済むこと。

### 6.4 idempotency（必須）

- `client_order_id` は **1 戦略インスタンスのスコープで一意**（nautilus と同じ規約）
- 同じ `client_order_id` で再送 → 既存の応答を返す（HTTP 200、IdempotentReplay）
- 異なる body で再送 → 409 Conflict

### 6.5 禁止事項

- 立花固有の `sBaibaiKubun="3"` 等の値を HTTP API / IPC / Rust UI 層に漏らさない
- `order_number` のような立花用語を本計画の field 名に使わない（`venue_order_id` に統一）
- nautilus に存在しない概念（例: 立花の現渡 `sBaibaiKubun=5`）を新規 OrderType として追加しない。`tags` extension で表現する
- nautilus の `OrderStatus` 名から逸脱した状態名を IPC で使わない
