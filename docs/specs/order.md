---
title: 立花注文機能 仕様
status: migrated
migrated_from:
  - docs/✅order/spec.md
  - docs/✅order/architecture.md
source_commit: f86c6a2
---
# 立花注文機能: 仕様

> **Phase 8（2026-05-03 完了）以降の経路**: Rust 側 HTTP API `/api/order/*`（ポート 9876）は完全廃止された。本仕様内に残る `POST /api/order/submit` 等の表記は **Phase 8 で廃止された旧 HTTP path 仕様**として読むこと。現在の正規ルートは以下の 2 経路に集約されている:
> - **GUI（iced）**: `Action::SubmitOrder` → `Command::SubmitOrder` を IPC（WebSocket、ポート 19876）に直接送信。HTTP を経由しない（元から `/api/order/*` は経由していなかった）
> - **スクリプト・E2E テスト**: 新設 Python helper `engine.replay_session.LiveSession`（`login()` / `submit_order()` / `modify_order()` / `cancel_order()` / `cancel_all()`）を呼び出し、内部で同じ IPC コマンドを発行
>
> 旧 HTTP path 専用の防壁（`OrderGuardConfig` の rate limit / qty/yen cap、`/api/order/cancel-all` の `confirm: true` boolean 強制など）は HTTP 廃止と同時に消滅した。GUI 発注は元から HTTP を経由していなかったため挙動変更なし。reason_code 体系（§5.2）と nautilus 互換要件（§6）は IPC 経路でも引き続き有効。

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
  - `data::config::tachibana::TachibanaCredentials.second_password` は **常に `None` のまま**（keyring に書かない。architecture.md §5 Q1 確定）
  - 収集は iced modal（tkinter ではない）で発注時のみ。Python メモリ保持を有効化
- 新規注文 API: `CLMKabuNewOrder`（現物のみ・成行のみ・買のみ・東証 `00`）
- ~~HTTP API `POST /api/order/submit` を Rust 側に新設~~（Phase 8 で廃止。代わりに `Command::SubmitOrder` を IPC で発行する経路に統一）
- 結果は同期的に Python から戻す（`venue_order_id`（= 立花 `sOrderNumber`）を含む）
- 約定通知購読は **本フェーズでは行わない**（注文一覧ポーリングで状態確認）

### 2.2 Phase O1 — 訂正（modify）・取消・注文一覧

- `CLMKabuCorrectOrder` / `CLMKabuCancelOrder` / `CLMKabuCancelOrderAll`
- `CLMOrderList` / `CLMOrderListDetail` で注文台帳取得
- ~~HTTP API~~（Phase 8 で全廃。下記は IPC `Command` / Python helper メソッドに置き換わった）:
  - `Command::ModifyOrder` / `LiveSession.modify_order()`（nautilus 用語に統一。立花の "correct" 用語は Python `_compose_request_payload` 内に閉じる）
  - `Command::CancelOrder` / `LiveSession.cancel_order()`
  - `Command::CancelAllOrders` / `LiveSession.cancel_all()`
  - `Command::GetOrderList`（フィルタ: `status` / `issue_code` / `date`）
- UI: 注文一覧パネル（Rust iced 側、新設 or 既存 dashboard 拡張）

### 2.3 Phase O2 — EVENT EC 約定通知の購読と UI 反映

- `python/engine/exchanges/tachibana_event.py`（Phase 1 で FD 用に既にあるはず）に `EC` パーサを追加
- パース後 `Event::OrderAccepted` / `OrderFilled` / `OrderRejected` / `OrderCanceled` を IPC で Rust に流す（**部分約定も `OrderFilled` として発火し、`leaves_qty` で部分/全部を判定する nautilus 流**。`OrderPartiallyFilled` は持たない）
- Rust UI 側で notification toast + 注文一覧の即時更新
- 接続復旧時は当日分の `EC` を立花が再送するため **重複検知**（`(venue_order_id, trade_id)` タプル単位の seen-set。`trade_id` は立花 `p_eda_no` に対応するが、立花 `p_eda_no` は注文番号またぎで衝突しうるため必ず `venue_order_id` と組で比較する）が必要

### 2.4 Phase O3 — 信用取引・逆指値・期日指定

- `sGenkinShinyouKubun = 2/4/6/8`（信用新規・返済の制度・一般）
- `sGyakusasiOrderType` / `sGyakusasiZyouken` / `sGyakusasiPrice`
- `sOrderExpireDay = YYYYMMDD`（10 営業日まで）
- 信用返済の建玉個別指定（`sTatebiType=1` + `aCLMKabuHensaiData`）
- 余力 API: `CLMZanKaiKanougaku` / `CLMZanShinkiKanoIjiritu` / `CLMZanUriKanousuu` を発注前ガードとして利用

### 2.5 含めないもの

**REPLAY モード仮想注文**: 本計画のスコープ外。**nautilus_trader 統合 Phase N1** で実装する。本計画の Python 関数（`tachibana_orders.NautilusOrderEnvelope` 等）と HTTP API は N1 でそのまま再利用される設計（live / replay ディスパッチャを N1 で前段に追加）。

**引き取り境界**（B-L1）: REPLAY モード中の仮想注文 WAL は、N1 で **`tachibana_orders_replay.jsonl`** に分岐して記録する（本計画の `tachibana_orders.jsonl` は live のみ扱う）。本計画の Phase O0〜O3 では `replay_mode == true` の間は全 `/api/order/*` を 503 + `reason_code="REPLAY_MODE_ACTIVE"` で拒否する（§3.2）ため、本計画から WAL に replay 行は混入しない。

**立花証券以外の venue への発注**: 本計画は **立花証券単独**。暗号資産 venue（Binance / Bybit / Hyperliquid 等）への発注経路は本計画に含めない。それらは [nautilus_trader 計画 Phase N3](../specs/backtest.md#24-phase-n3--暗号資産-venue-executionclient任意) で扱う。本計画で書く HTTP API・IPC・`tachibana_orders.py` は立花前提の写像のみを実装する。

**Phase O4+ に送り**:

- **NISA 口座での発注**（`sZyoutoekiKazeiC=5/6`）— `sZyoutoekiKazeiC` はログイン応答の値をそのまま流す方針なので技術的には差は小さいが、**枠管理 UI** が必要なため別フェーズ
- **注文 GUI のリッチ化**（チャート上クリックで発注、ホットキー、ワンクリックトレード）— UI 詳細は本計画では薄い実装に留め、UX は別 plan で詰める
- **複数アカウント**
- **本番接続のデフォルト UI 露出**
- **ヒストリカル約定エクスポート**

## 3. 非機能要件

### 3.0 立花 HTTP リクエスト規約（C-H2）

- 立花 e支店 HTTP リクエストは **Shift-JIS エンコーディング + `func_replace_urlecnode` パーセントエンコード**を必ず通す（SKILL.md R7・R9）。
- レスポンスの Shift-JIS デコードと**対称**な実装にする（送信は SJIS encode → percent encode、受信は percent decode → SJIS decode）。
- UTF-8 直送りや標準 `urllib.parse.quote` の素朴な利用は禁止。`tachibana_codec.encode_request()` 経由で必ずラップする。

### 3.1 セキュリティ

- 第二暗証番号（`sSecondPassword`）の取り扱い（Q1 確定: 2026-04-25）:
  - **keyring に保存しない**。発注時のみ iced modal で入力させ、Python メモリ上でのみ保持する（§2.1 F-H5 確定）
  - Python メモリ上は `SecretStr`（pydantic）で保持し、`__repr__` で `***` 化
  - **ログに絶対出さない**: `Debug` 系のフォーマット時はマスク（flowsurface の `NewOrderRequest` の手動 `Debug` 実装に倣う）
  - **アイドル forget**（C-M2）: 以下のいずれかで Python メモリから自動 forget する:
    - アイドル N 分（config 化、デフォルト 30 分）操作なし
    - 立花夜間閉局（接続不可・閉局応答）検知時
    - 仮想 URL refresh 検知時（`sUrlRequest` / `sUrlEvent` / `sUrlEventWebSocket` のいずれか変更）
- 仮想 URL マスク規約（C-H1）:
  - 立花の仮想 URL（`sUrlRequest` / `sUrlEvent` / `sUrlEventWebSocket`）と `p_no` クエリは **WAL / 構造化ログ / `reason_text` / 監査ログに一切出さない**。**host のみ出力可**。
  - 出力前に `tachibana_codec.mask_virtual_url()` を必ず通す（SKILL.md R3 #4・R10）。
- ~~HTTP API の認証: 既存 `/api/replay/*` と同じトークン方式（[`src/api/`](../../../src/api/) の既存ガード）。**localhost-only バインドを維持**~~（Phase 8 で HTTP 全廃に伴い該当なし。現在は IPC（ポート 19876）の HMAC token 認証のみ。Python helper の attach mode は `engine-session.json` から token を共有する）

### 3.2 安全装置（誤発注防止）

- **デモ環境強制**: `TACHIBANA_ALLOW_PROD=1` 未設定なら本番 URL に発注リクエストを送らない（Python URL builder で reject）
- **REPLAY ガード**（C-H4、Phase O0 必須）: replay モード稼働中（`ReplayState != Idle` 等）の発注は engine 側 state machine が `EngineBusy` event で reject する（Phase 8.1b で実装）。旧 `/api/order/*` 503 + `reason_code="REPLAY_MODE_ACTIVE"` の HTTP 層ガードは HTTP 廃止と同時に消滅。GUI 発注経路（`Command::SubmitOrder` の IPC 直送）は元から HTTP を経由しない
- **連打抑止 / rate limit**（C-M3）: ~~同一 `(instrument_id, order_side, quantity, price)` の組合せが N 秒以内（config 化、デフォルト 3 秒）に Y 回（デフォルト 2 回）以上送られたら、**429 + `reason_code="RATE_LIMITED"`** を返す~~（Phase 8 で HTTP 廃止に伴い消滅。`OrderGuardConfig` は HTTP path 専用の防壁だった。GUI 発注は元から HTTP を経由しないため挙動変更なし。Python helper / IPC 経路では現時点で rate limit 実装なし、ユーザー責任）
- **数量・金額上限**: ~~起動 config で 1 注文最大株数 / 最大金額を必ず指定。未指定なら `/api/order/submit` を 503 で reject（明示 opt-in）~~（同上、`OrderGuardConfig` は Phase 8 で削除済み）
- **発注確認モーダル**（UI 側、Phase O1）: 成行発注時は明示的な確認ダイアログを出す
- **発注ログ**: `data_path()/tachibana_orders.jsonl` に append（人間監査用、第二暗証番号は除外）
- **冪等性キー必須**: IPC `Command::SubmitOrder`（および `LiveSession.submit_order()`）は `client_order_id` を必須にし、再送時は同じ `venue_order_id`（= 立花 `sOrderNumber`）を返す（flowsurface `agent_session_state.rs` パターン）。Phase 8 以前は HTTP path にも同制約があったが、現在は IPC 経路と Python WAL の双方で担保

### 3.3 信頼性

- **session 切れ即停止伝播**（C-M5）: `p_errno=2` 検知時、`OrderSessionState` を `frozen` に遷移する。以降の発注 IPC コマンドはすべて `Event::OrderRejected{reason_code="SESSION_EXPIRED"}` で即時拒否する。in-flight な発注も同様に reject、WAL に `session_expired` 行を必ず書く（再送・再ログイン後の整合確認に必須）。バナー表示も併発する（Phase 1 の経路を流用）。~~旧 HTTP path の 503 即時応答~~ は Phase 8 で消滅。
- 約定通知の重複検知: **`(venue_order_id, trade_id)` タプル**で seen-set を持つ（C-H3。`trade_id` ＝ 立花 `p_eda_no` だが、`p_eda_no` は注文番号またぎで衝突しうるため必ず venue_order_id と組で比較）
- ネットワーク切断中の発注は **待たずに reject**（タイムアウトで詰まると誤発注の温床）

### 3.4 観測性

- WAL truncated 行（fsync 前 crash）は復元時スキップ + WARN ログ。詳細は architecture.md §4.2 を参照
- 全発注に `client_order_id`（UUID v4）と `request_id`（IPC 相関）を埋める
- `tachibana_orders.jsonl` の各行に `client_order_id` / `venue_order_id`（= 立花 `sOrderNumber`） / `result_code`（= 立花 `sResultCode`） / `warning_code`（= 立花 `sWarningCode`）を入れる
- **仮想 URL マスク厳守**（C-H1）: `tachibana_orders.jsonl` / 構造化ログ / `reason_text` / 監査ログには `sUrlRequest` / `sUrlEvent` / `sUrlEventWebSocket` および `p_no` クエリを出さない（host のみ）。`tachibana_codec.mask_virtual_url()` を必ず通す。SKILL.md R3 #4・R10 を参照。

## 4. 公開 API（旧 HTTP / 現 IPC + Python helper）

> **Phase 8（2026-05-03 完了）**: 下表の HTTP メソッド/パス列は **廃止された旧仕様**として残す。現在の正規ルートは:
> - GUI: `Command::SubmitOrder` 等を IPC で直接送信（HTTP は元から経由していない）
> - スクリプト: `engine.replay_session.LiveSession`（attach mode で GUI 内 engine に WS client として接続、または in-process mode で engine を spawn）の `login()` / `submit_order()` / `modify_order()` / `cancel_order()` / `cancel_all()`
>
> request body / response の field shape は IPC `SubmitOrderRequest` / `Event::Order*` の DTO（`engine-client/src/dto.rs` / `python/engine/schemas.py`）を正本とする。下表の HTTP status / reason_code 体系は IPC 経路では適用されず、reject は `Event::OrderRejected{reason_code, reason_text}` で表現される。

| メソッド | パス | リクエスト | レスポンス | フェーズ |
|---|---|---|---|---|
| `POST` | `/api/order/submit` | `{client_order_id, instrument_id, order_side, order_type, quantity, price?, time_in_force, ...}` (§5.1) | 201: `{client_order_id, venue_order_id, status: "ACCEPTED", warning_code?, warning_text?}` / 202: `{status: "SUBMITTED", venue_order_id: null, warning: "order_status_unknown"}`（idempotent replay で unknown） | O0 |
| `POST` | `/api/order/modify` | `{client_order_id, quantity?, price?, trigger_price?, expire_time?}` または `{venue_order_id, quantity?, price?, trigger_price?, expire_time?}`（他端末注文） | `{client_order_id, status: "PENDING_UPDATE"}` | O1 |
| `POST` | `/api/order/cancel` | `{client_order_id}` または `{venue_order_id}`（他端末注文。`client_order_id` 不明時のみ） | `{client_order_id, status: "PENDING_CANCEL"}` | O1 |
| `POST` | `/api/order/cancel-all` | `{confirm: true, instrument_id?, order_side?}`（**`confirm: true` は boolean リテラル必須**。文字列 `"true"` / 省略 / `false` は 400 + `reason_code="CONFIRM_REQUIRED"`、query param 不可。order guard 未投入時は 503 + `ORDER_GUARD_NOT_CONFIGURED`。受理時は 202 Accepted を即返却し fire-and-forget で IPC `CancelAllOrders` を送る、[src/api/order_api.rs::cancel_all_orders](https://github.com/botterYosuke/e-station/blob/main/../src/api/order_api.rs)） | 202: `{status: "ACCEPTED"}`（処理結果は `OrderListUpdated` で逐次反映） | O1 |
| `GET` | `/api/order/list` | クエリ: `status?` / `instrument_id?` / `date?` | `{orders: [...]}` | O1 |
| `POST` | `/api/order/forget-second-password` | （body 無し） | `{status: "OK"}` | O0（**HTTP ハンドラ未実装** — `src/api/order_api.rs` に対応 route なし。IPC コマンド `ForgetSecondPassword` は schema 上存在するため別経路から呼ばれる想定。HTTP 経由が必要になった時点で実装） |
| `GET` | `/api/order/positions` | — | 現物・信用建玉 | O3（**HTTP ハンドラ未実装** — Phase O3 で着地予定） |
| `GET` | `/api/order/buying-power` | — | 余力 | O3（**HTTP ハンドラ未実装** — Phase O3 で着地予定。UI 側 BuyingPowerPanel は IPC `BuyingPowerUpdated` イベントを直接受けているため HTTP API は不要状態） |

**重要**: API は **`client_order_id` を一次キー**として動作する（nautilus 流）。`venue_order_id`（立花 `sOrderNumber`）は応答に含めるが、後続の `/modify` `/cancel` 入力は `client_order_id` で受ける。Rust 側 `OrderSessionState` が双方向写像を保持。WAL 復元で `client_order_id` が不明な「他端末経由の当日注文」のみ `venue_order_id` での `/modify` `/cancel` を受理する（architecture.md §4.3 / [T1.5](../roadmap/order/implementation-plan.md#t15-起動時の台帳復元)）。

**`client_order_id` 発行元（Q2 確定: 2026-04-25）**: クライアント側で UUID v4 を生成して送る（flowsurface 流・案 A）。Rust 側は受け取った値を idempotency key として使い、独自に採番しない。iced 側発注フォームは送信時に `Uuid::new_v4()` を生成する。Python helper（`LiveSession.submit_order(...)`）の caller も同様に UUID v4 を渡す（helper 自身は採番しない、ユーザー責任）。

JSON Schema は [`docs/specs/data-engine/schemas/`](../specs/data-engine/schemas/) に追加（schema 1.3）。

## 5. 入力バリデーション（Rust IPC 層）

> **Phase 8 注記**: 旧 Rust HTTP 層（`src/api/order_api.rs`）が担っていた早期バリデーションは、HTTP 廃止後 IPC ハンドラ側のスキーマ検証（serde `deny_unknown_fields` + Python 側 pydantic `SubmitOrderRequest`）に移管されている。GUI 発注は `engine-client/src/dto.rs::SubmitOrderRequest` を直接組み立てて送るため、validation は serde + pydantic で担保。Python helper 経路も同じ DTO を経由する。下記の入力規約は引き続き有効:

Python に渡す前に **Rust 側 IPC ハンドラ / pydantic バリデータで**早期に弾く:

- `client_order_id`: 任意の文字列（UUID v4 推奨）。nautilus `ClientOrderId` 制約に合わせ **長さ 1〜36、ASCII printable のみ**[^cid-source]
- `instrument_id`: `<symbol>.<venue>` 形式。**Phase O0〜O2 は東証（`TSE`）のみ受理**（例 `7203.TSE`）。大証(OSE)・名証(NSE)等への `sSizyouC` 写像は O3 以降で対応（[open-questions.md Q9](../roadmap/order/open-questions.md) として追跡）
- `order_side`: `"BUY"` / `"SELL"`（nautilus `OrderSide` enum 文字列）
- `order_type`: `"MARKET"` / `"LIMIT"` / `"STOP_MARKET"` / `"STOP_LIMIT"` の 4 種のみ受理。nautilus `OrderType` には `MARKET_IF_TOUCHED` / `LIMIT_IF_TOUCHED` も存在するが、立花が直接対応しないため **HTTP 層で 400 reject**（`reason_code="VENUE_UNSUPPORTED"`、architecture.md §10.1）。Phase O0 は `MARKET` のみ受理、O1 で `LIMIT`、O3 で `STOP_*` を順次解禁
- `quantity`: 正の整数文字列。**nautilus の `Quantity` は文字列（precision 保持）が基本**なので合わせる。単元株チェックは Python 側で master 突合せ
- `price`: `order_type ∈ {LIMIT, STOP_LIMIT}` のとき必須、文字列。呼値単位の丸めは Python 側
- `time_in_force`: `"DAY"` / `"GTD"` / `"AT_THE_OPEN"` / `"AT_THE_CLOSE"` の 4 種のみ受理。nautilus 列挙の `GTC` / `IOC` / `FOK` は立花が直接対応しないため **HTTP 層で 400 reject**（architecture.md §10.2）。Python 写像は `AT_THE_OPEN` → `sCondition=2`、`AT_THE_CLOSE` → `4`、`tags=["close_strategy=funari"]` 併用で `6`（不成）、それ以外は `0`
- `expire_time`: ISO8601、`time_in_force=GTD` のとき必須。Python 側で `sOrderExpireDay` (YYYYMMDD JST) に変換、10 営業日上限を Python 側で検証
- `trigger_price`: `order_type ∈ {STOP_MARKET, STOP_LIMIT}` のとき必須。立花 `sGyakusasiZyouken` に写像
- `tags`: Rust IPC 層では各要素が `key=value` 形式（ASCII printable、`=` を 1 つ含む）であることのみ検証し 400 reject。内容（未知タグ・組合せ）の検証は Python 側 `_compose_request_payload` 内責務
- 上限（数量・金額）チェックは Python 側で master + 起動 config から

**`venue_order_id` による modify/cancel（Phase O1 での他端末注文対応）**: 起動時 WAL 復元で `client_order_id` が不明な注文（他端末・他アプリ経由の当日注文）に対しては、`Command::ModifyOrder` / `Command::CancelOrder`（および `LiveSession.modify_order()` / `cancel_order()`）で `venue_order_id` を直接受け入れる。この場合 `client_order_id` は応答に含まれない（`null`）。`client_order_id` と `venue_order_id` が同時に指定された場合は `client_order_id` を優先する。

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
  "tags": ["cash_margin=cash", "account_type=specific"]
}
```

立花固有の `sBaibaiKubun` / `sGenkinShinyouKubun` / `sZyoutoekiKazeiC` 等は **`tags` で venue extension として渡す**（nautilus の慣習に合わせる）。`tags` の正本レジストリは architecture.md §10.4。spec 側はサンプルのみで、定義は architecture 側に集約する。

**`account_type` のデフォルト**: `account_type=*` タグが未指定なら、Python 写像層は **ログイン応答の `sZyoutoekiKazeiC` 値をパススルー**する。HTTP 層は `account_type` 未指定を許容する。

**バリデーション規則**（`_compose_request_payload` 内）:
- `cash_margin=*` / `account_type=*` の **同種重複は 400 reject**（`UnsupportedOrderError(reason_code="CONFLICTING_TAGS")` → IPC で `OrderRejected{reason_code="VENUE_UNSUPPORTED", reason_text="CONFLICTING_TAGS: <details>"}`）
- 信用 + NISA など立花が拒否する組合せは Python 写像層で 400 reject
- 未知タグは silently ignore（前方互換）

写像規則は Python 側 `tachibana_orders._compose_request_payload` に集約。HTTP API 層にも、IPC 層にも、立花固有の用語を漏らさない。

## 5.2 reason_code 体系（観測性）

`OrderRejected{reason_code, reason_text}` の `reason_code` は以下の固定文字列のみ。**SCREAMING_SNAKE_CASE（ASCII 大文字 + 数字 + `_`）規約を厳守**する（A-H2）。

> **Phase 8 注記**: 「HTTP ステータス」列は **Phase 8 で廃止された旧 HTTP path の応答仕様**。現在の正規ルート（IPC + Python helper）では reason_code が `Event::OrderRejected` イベントの field としてのみ意味を持つ。HTTP 列は履歴情報として残置。`REPLAY_MODE_ACTIVE` / `RATE_LIMITED` / `ORDER_GUARD_NOT_CONFIGURED` / `QTY_LIMIT_EXCEEDED` / `YEN_LIMIT_EXCEEDED` は HTTP path 専用ガードに紐付いていた reason_code で、Phase 8 以降は IPC 経路で発火しない（`OrderGuardConfig` ごと削除済み。replay モードは engine 側 state machine の `EngineBusy` event で代替）。

| reason_code | HTTP ステータス（旧） | 発生条件 |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Rust HTTP 層のスキーマ違反（不正 UUID, 数量負, instrument_id 形式違反） |
| `UNSUPPORTED_IN_PHASE_O0` | 400 | Phase O0 で許可されない `order_type` / `time_in_force` / `tags`（脚注 [^o0-unsupported] 参照） |
| `VENUE_UNSUPPORTED` | 400 | Python 写像層が立花未対応の組合せと判定（CONFLICTING_TAGS / MARKET_IF_TOUCHED / GTC 等を含む） |
| `SECOND_PASSWORD_REQUIRED` | 401 | 第二暗証番号未保持で発注 |
| `SECOND_PASSWORD_INVALID` | 401 | 立花応答 `p_errno=4` 等（第二暗証番号エラー） |
| `SECOND_PASSWORD_LOCKED` | 423 | `SECOND_PASSWORD_INVALID` 連続 N 回（デフォルト 3 回）後。抑止期間（`second_password_lockout_secs`、デフォルト 1800 秒）中は `SubmitOrder` / `ModifyOrder` / `CancelOrder` すべてを reject。時間経過で自動解除。 |
| `SESSION_EXPIRED` | 503 | `p_errno=2`（OrderSessionState=frozen 中の全 `/api/order/*` 拒否を含む） |
| `REPLAY_MODE_ACTIVE` | 503 | `replay_mode == true` の間の全 `/api/order/*`（C-H4、Phase O0 必須） |
| `RATE_LIMITED` | 429 | 同一 `(instrument_id, side, qty, price)` の N 秒/Y 回連打検知（C-M3） |
| `UNSUPPORTED_INSTRUMENT` | 400 | Phase 2 で先物・OP の instrument_id が届いた場合の防御フェンス（kabu_station venue, Phase 3 配線完了まで） |
| `CONNECTION_ERROR` | 503 | kabu_station venue で `KabuConnectionError`（kabuステーション本体プロセスへの接続不可）が発生した場合。token と FSM をリセットして再ログインを促す |
| `MARKET_CLOSED` | 409 | 立花応答 `sResultCode` が時間外 |
| `ORDER_GUARD_NOT_CONFIGURED` | 503 | ~~`OrderGuardConfig.enabled == false`（運用ガード設定が未投入のまま `/api/order/*` を叩いた）。Rust HTTP 層 (`src/api/order_api.rs`) が最前段で判定~~（Phase 8 で `OrderGuardConfig` ごと削除。GUI 発注は元から HTTP を経由しないため挙動変更なし） |
| `QTY_LIMIT_EXCEEDED` | 400 | リクエスト `quantity` が `OrderGuardConfig.max_qty_per_order` を超過 |
| `YEN_LIMIT_EXCEEDED` | 400 | `LIMIT` / `STOP_LIMIT` で `price * quantity` が `OrderGuardConfig.max_yen_per_order` を超過（`MARKET` / `STOP_MARKET` は対象外） |
| `INSUFFICIENT_FUNDS` | 409 | Phase O3 余力ガード失敗 |
| `VENUE_REJECTED` | 422 | 立花応答業務エラー（`p_errno != 0` 等の venue 拒否。`reason_text` に立花コードと文言） |
| `ORDER_STATUS_UNKNOWN` | 409 | 起動時復元で `venue_order_id = None`（unknown）の注文への cancel / modify 要求。`GET /api/order/list` で確認後に再試行を促す |
| `INTERNAL_ERROR` | 500 | Rust / Python 内部例外（タイムアウトを含む） |

詳細な HTTP ステータスのマッピングと再試行可否は [§4 表](#4-公開-apihttp) / architecture.md §2.3 / architecture.md §4.3 を参照。

**`reason_text` フォーマット規約**:
- `VENUE_REJECTED` / `VENUE_UNSUPPORTED`: `"<TACHIBANA_CODE_OR_TAG>: <message>"` の 1 行（改行禁止、最大 512 文字）
- `VALIDATION_ERROR`: `"<field_name>: <reason>"`
- 観測性ダッシュボードが prefix grep できるよう、コロンの前は ASCII 大文字 + 数字 + `_` のみ
- `reason_text` には**仮想 URL / `p_no` を絶対に含めない**（C-H1。host のみ可、必ず `mask_virtual_url()` を通す）

`reason_code` の追加には spec 更新を必須とする（観測性ダッシュボードが破壊されないため）。

[^o0-unsupported]: **`UNSUPPORTED_IN_PHASE_O0=400` の発火条件 set**:
    - `order_type` が `MARKET` 以外（`LIMIT` / `STOP_MARKET` / `STOP_LIMIT` 等を Phase O0 で送信）
    - `time_in_force` が `DAY` 以外（`GTD` / `AT_THE_OPEN` / `AT_THE_CLOSE` 等を Phase O0 で送信）
    - 任意の `tags` フィールドの追加（Phase O0 ではタグ未対応）
    - `order_side != BUY`（Phase O0 は買のみ。`SELL` は O1 以降）
    - `post_only != false`（Phase O0 では未対応）
    - `reduce_only != false`（Phase O0 では未対応）
    Phase O1 解禁時はこの表脚注を更新する。

## 6. nautilus_trader 互換要件（不変条件）

将来 [docs/specs/backtest/](../specs/backtest/) Phase N2 で nautilus `LiveExecutionClient` に切り替えるとき、本計画で書く Python レイヤを **そのまま nautilus に組み込める**ことを設計目標とする。これに反する実装は禁止:

### 6.1 用語・型の整合（必須）

| 概念 | 本計画で使う名前 | nautilus 対応型 | 立花対応 |
|---|---|---|---|
| クライアント注文 ID | `client_order_id: str` | `nautilus_trader.model.identifiers.ClientOrderId` | 内部で生成・採番、立花には送らない |
| Venue 注文 ID | `venue_order_id: str` | `VenueOrderId` | `sOrderNumber` |
| 銘柄 ID | `instrument_id: str`（`SYMBOL.VENUE` 形式） | `InstrumentId` | `sIssueCode` + `sSizyouC` |
| 売買方向 | `order_side: "BUY"\|"SELL"` | `OrderSide` enum | `sBaibaiKubun` |
| 注文種別 | `order_type` 上記 6 種 | `OrderType` enum | `sOrderPrice` 等の組合せ |
| 期間指定 | `time_in_force` 上記 7 種 | `TimeInForce` enum | `sCondition` + `sOrderExpireDay` |
| 約定 ID | `trade_id: str` | `TradeId` | `p_eda_no`（注文番号またぎで衝突しうるため `(venue_order_id, trade_id)` で識別） |
| 数量 | `quantity: str`（精度保持） | `Quantity` | `sOrderSuryou` |
| 価格 | `price: str` / `trigger_price: str` | `Price` | `sOrderPrice` / `sGyakusasiZyouken` |
| トリガー種別 | `trigger_type: "LAST"\|null` | `TriggerType` enum | Phase O0/O1 では `null` 必須、Phase O2/O3 までは `LAST` 固定。他値受信時は 400 + `reason_code="VENUE_UNSUPPORTED"` |

文字列値（enum 表記）は **nautilus の文字列表現と完全一致**させる（nautilus の `OrderSide.BUY.name == "BUY"` 等）。`"buy"` / `"limit_order"` 等の独自表記は使わない。

**列挙数の注記**（A-M2）: 上記 `order_type` 6 種 / `time_in_force` 7 種は **IPC 型（nautilus 互換）として保持する列挙数**である。**HTTP 層が accept する部分集合**（Phase O0 では `MARKET` / `DAY` のみ等）は §5（入力バリデーション）/ architecture.md §10.1 / architecture.md §10.2 を参照。

### 6.2 イベントタクソノミー（必須）

IPC `Event::Order*` および HTTP `status` フィールドは nautilus のオーダーステートマシン名と一致させる:

```
INITIALIZED → SUBMITTED ─┬─→ REJECTED                       (即時 reject: SKILL R6 p_errno=2 等)
                         └─→ ACCEPTED → ┬─→ FILLED
                                        ├─→ PARTIALLY_FILLED → FILLED
                                        ├─→ PENDING_UPDATE → ACCEPTED (or REJECTED)
                                        ├─→ PENDING_CANCEL → CANCELED
                                        ├─→ EXPIRED
                                        └─→ REJECTED
```

`SUBMITTED → REJECTED` ブランチは SKILL R6「`p_errno=2` 即時 reject」（session 切れ等で venue へ到達せず即時拒否）の経路を表す。立花固有の状態遷移（例: 訂正受付待ち / 部分約定）はこの 9 状態に正規化して写像する。Python 側 `_map_tachibana_state_to_nautilus()` 関数を 1 箇所に置く。

**`SUBMITTED → REJECTED` を取る `reason_code` set**: `{SESSION_EXPIRED, SECOND_PASSWORD_INVALID, VENUE_REJECTED}`（最後は `p_errno != 0` の業務エラー）。それ以外の `reason_code`（`VALIDATION_ERROR` / `UNSUPPORTED_IN_PHASE_O0` / `VENUE_UNSUPPORTED` / `RATE_LIMITED` / `REPLAY_MODE_ACTIVE` 等）は `INITIALIZED` 段階で HTTP 層が弾くため、`SUBMITTED` には到達しない。

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

async def cancel_all_orders(
    session, second_password,
    instrument_id: Optional[str] = None,
    order_side: Optional[str] = None,
) -> CancelAllOrdersResult: ...           # nautilus 命名に合わせる（立花 CLMKabuCancelOrderAll 写像）
```

**用語統一**（横断）: 本計画のシグネチャ・IPC・HTTP API はすべて nautilus 名（`ModifyOrder` / `modify_order` / `cancel_order` / `cancel_all_orders` / `trade_id`）で表記する。立花の "correct" / "p_eda_no" 等の用語は Python `_compose_request_payload` / `_parse_*` 内に閉じ込め、外側に漏らさない。

`NautilusOrderEnvelope` は **nautilus の `nautilus_trader.model.orders.Order` 互換のフィールド構成**を持つ純データクラス（pydantic）。N0 着手時に nautilus 本体を import せず、独自に同じ shape を切る。N2 で nautilus を導入したら **`from nautilus_trader.model.orders import Order` で置き換えるだけ**で済むこと。

### 6.4 idempotency（必須）

- `client_order_id` は **1 戦略インスタンスのスコープで一意**（nautilus と同じ規約）
- 同じ `client_order_id` で再送 → 既存の応答を返す（HTTP 200、IdempotentReplay）
- 異なる body で再送 → 409 Conflict

### 6.5 禁止事項

- 立花固有の `sBaibaiKubun="3"` 等の値を HTTP API / IPC / Rust UI 層に漏らさない
- 立花用語 `order_number` / `sOrderNumber` を本計画の field 名に使わない（`venue_order_id` に統一、ラウンド 2 で全面置換済）
- nautilus に存在しない概念（例: 立花の現渡 `sBaibaiKubun=5`）を新規 OrderType として追加しない。`tags` extension で表現する
- nautilus の `OrderStatus` 名から逸脱した状態名を IPC で使わない

[^cid-source]: 「長さ 1〜36、ASCII printable のみ」の根拠は nautilus `ClientOrderId` の実装（`nautilus_trader.model.identifiers.ClientOrderId`、参照: <https://github.com/nautechsystems/nautilus_trader/blob/master/nautilus_core/model/src/identifiers/client_order_id.rs> および対応する Python バインディング）。本計画では Tpre.1（実装計画 [implementation-plan.md](../roadmap/order/implementation-plan.md) の事前タスク Tpre.1）で nautilus 本体ソースを直接参照して上限値・許容文字集合を確定する。確定値が本注記と差異が出た場合は spec を更新する。


---

## アーキテクチャ

> 注: 以下は移送元 docs/specs/order/architecture.md (source_commit: 674aefd) を本仕様の一部として統合したセクション。venue 共通の注文ライフサイクル抽象は specs/live-strategy.md 側を参照。

# 立花注文機能: アーキテクチャ

> **Phase 8（2026-05-03 完了）以降の経路**: Rust 側 HTTP API `/api/order/*`（ポート 9876）は完全廃止。`src/api/order_api.rs`（3,490 行）と `OrderGuardConfig` も削除済み。本ドキュメント中で `Rust src/api/order_api.rs` / `POST /api/order/*` / `OrderGuardConfig` を参照している箇所は **Phase 8 以前の旧設計**として読むこと。現在の正規ルート:
> - **GUI**: `Action::SubmitOrder` → `engine_client::dto::SubmitOrderRequest` を直接組んで `Command::SubmitOrder` を IPC（WebSocket、ポート 19876）に送信。`src/main.rs` 内で完結し HTTP を経由しない（元から経由していなかった）
> - **スクリプト・E2E**: `engine.replay_session.LiveSession`（in-process / attach mode 自動判定）の `login()` / `submit_order()` / `modify_order()` / `cancel_order()` / `cancel_all()`。内部で同じ IPC コマンドを発行
> - **冪等性マップ**: `engine-client/src/order_session_state.rs::OrderSessionState` は **Rust IPC ハンドラ層** に残存（HTTP 廃止に伴う移管）。`client_order_id ↔ venue_order_id` の lookup 責務は変更なし
> - **WAL**: Python `tachibana_orders.jsonl` も変更なし。`OrderSessionState::load_from_wal()` を Rust 起動時に呼ぶ経路も維持

## 1. 配置原則

[docs/specs/venues/tachibana/architecture.md §1](../architecture/modules/tachibana-adapter.md) の Python 集約方針を踏襲。発注経路の追加責務は以下:

| 責務 | 所在 |
|---|---|
| ~~HTTP API `/api/order/*` のレスポンス組立~~ | ~~**Rust** `src/api/order_api.rs`~~（Phase 8 で全廃。IPC 直送に移行） |
| 入力スキーマバリデーション | **Rust IPC ハンドラ + Python pydantic**（`engine-client/src/dto.rs::SubmitOrderRequest` の serde `deny_unknown_fields` + `python/engine/schemas.py` の `SubmitOrderRequest`） |
| 冪等性マップ（`client_order_id → venue_order_id`、注釈: venue_order_id = 立花 `sOrderNumber`） | **Rust** `engine-client/src/order_session_state.rs`（flowsurface `agent_session_state.rs` を移植。Phase 8 で `src/api/` から `engine-client/src/` 配下に集約） |
| 立花リクエスト本体（`CLMKabuNewOrder` 等の組立・送信・パース） | **Python** `python/engine/exchanges/tachibana_orders.py`（新設） |
| 第二暗証番号の保持 | **Python メモリ + Rust keyring**（Phase 1 の credentials 経路に追加） |
| 第二暗証番号の入力 UI | **Python tkinter ヘルパー** subprocess（Phase 1 のログイン UI と同一機構を再利用） |
| EVENT EC フレームのパース | **Python** `python/engine/exchanges/tachibana_event.py` に `_parse_ec_frame` を追加 |
| 注文台帳ストア（重複検知キー `(venue_order_id, trade_id)`、当日分のみ） | **Python メモリ** |
| 発注監査ログ | **Python**（`data_path()` を Rust から IPC で受領済み、Phase 1 T0.2） |
| UI（注文一覧 / 確認モーダル） | **Rust iced** |

## 2. プロセス境界とフロー

### 2.1 発注（同期）

> **Phase 8 注記**: 旧フロー図中の「`POST /api/order/submit` → Rust `src/api/order_api.rs`」段は **Phase 8 で削除**。現在は GUI（`src/main.rs` の `Action::SubmitOrder` ハンドラ）または Python helper（`LiveSession.submit_order()`）が直接 ③ 段の `engine_client.send(Command::SubmitOrder ...)` を発行する。冪等性チェック（`OrderSessionState.try_insert`）は IPC ハンドラ側に移管（`engine-client/src/order_session_state.rs`）。

```
ユーザー UI（iced）/ Python helper（LiveSession）
   │ Action::SubmitOrder / LiveSession.submit_order(...)
   ▼
GUI: src/main.rs / Python helper: engine.replay_session
   │ ① 入力検証（UUID, 銘柄コード形式, qty>0 …）— pydantic + serde deny_unknown_fields
   │ ② OrderSessionState.try_insert(client_order_id, request_key)
   │      （2 引数。venue_order_id は OrderAccepted 受信後に update_venue_order_id() で後着する）
   │      ├─ Created { client_order_id } → 続行
   │      ├─ IdempotentReplay { venue_order_id } → 既存 venue_order_id を即返却
   │      └─ Conflict  → エラー
   │ ③ engine_client.send(Command::SubmitOrder { request_id, payload })
   ▼
Python python/engine/server.py
   │ ④ Event::OrderSubmitted を即発行（立花 HTTP 送信の前。この時点で WAL に submit 行が既にある前提）
   │    ※ HTTP 送信前に p_errno=2（session 切れ）等が即判明した場合は OrderSubmitted の後に
   │      OrderRejected を続けて発行する。Rust 側 OrderSessionState はこの連続を許容すること
   │ ⑤ tachibana_orders.submit_order(session, second_password, envelope)
   ▼
Python tachibana_orders.py
   │ ⑤ NewOrderRequest を組み立て、_compose_request_payload() で
   │    p_no / p_sd_date / sCLMID / sJsonOfmt を後付け
   │      - p_no は `p_no_counter.next()` で取得（呼出側 `tachibana_orders` が
   │        `tachibana_helpers.PNoCounter` インスタンスを保持）
   │      - p_sd_date は `tachibana_helpers.current_p_sd_date()` で取得
   │ ⑥ Shift-JIS パイプライン（固定順）:
   │      payload を Shift-JIS 化 → `func_replace_urlecnode` で URL エンコード
   │      → `tachibana_url.build_request_url(session.url_request, encoded)` で URL 結合
   │      → `tachibana_codec.mask_virtual_url()` を必ず通してログ／監査用文字列を作る
   │ ⑦ httpx.post(url) → Shift-JIS デコード → check_response()
   │      p_errno=2     → SessionExpiredError（§C-M5: OrderSessionState を frozen 化）
   │      sResultCode≠0 → OrderRejectedError(code, message)
   │ ⑧ NewOrderResponse をパース、venue_order_id（= 立花 `sOrderNumber`）を返す
   ▼
Rust 受信
   │ ⑨ Event::OrderAccepted を待機していた send 側に解決
   │ ⑩ OrderSessionState.update_venue_order_id(client_order_id, venue_order_id) で埋める
   │ ⑪ caller（GUI または Python helper）に応答を返却
```

**H-2 SecondPasswordRequired ポリシー（確定）**:

Python が `_do_submit_order` を処理した時点で第二暗証番号がメモリに無い場合、以下の fire-and-forget 方式を採る。

```
Python
   │ second_password is None
   │ → Event::SecondPasswordRequired { request_id } を IPC 送信
   │ → ハンドラを return（OrderRejected は送らない）
   ▼
Rust src/main.rs（IPC イベント購読経路）
   │ EngineEvent::SecondPasswordRequired { request_id } → iced modal 表示
   ▼
ユーザー入力
   │ → Command::SetSecondPassword { value } を IPC 送信
   ▼
発注 caller（GUI または Python helper）
   │ SecondPasswordRequired を受けたら、同一 client_order_id で SubmitOrder を再送
   │ OrderSessionState の try_insert が IdempotentReplay をガード
   │ → 2 回目は second_password 設定済みのため正常に処理される
```

> **Phase 8 注記**: 旧 HTTP path では `/api/order/submit` が HTTP 401 + `{"reason_code":"SECOND_PASSWORD_REQUIRED"}` を返していたが、HTTP 廃止後は `Event::SecondPasswordRequired` を直接 caller が受け取る経路に統一。Python helper（`LiveSession`）は内部で `SecondPasswordRequired` イベントを listen し、ユーザー側コールバック（または `login()` 時に渡された `second_password`）で値を供給する。

**不変条件**:
- Python は SecondPasswordRequired を送った後 OrderRejected を送らない（Rust の OrderWaitResult が SecondPasswordRequired になり HTTP 401 で終了するため）
- 再送時の idempotency は `client_order_id + request_key` ペアで担保（WAL 復元も同様）
- 再送は無制限ではなく HTTP 呼び出し側の責務。Python には自動リトライ機構を持たせない（単一責務）

### 2.2 約定通知（非同期、Phase O2）

```
立花 EVENT WebSocket
   │ ^A 区切りフレーム（p_evt_cmd=EC）
   ▼
Python tachibana_event.py._receive_loop
   │ parse_event_frame で項目化
   │ _parse_ec_frame で OrderEcEvent に正規化
   │ seen_trade_keys（set[(venue_order_id, trade_id)]）に存在すれば skip（重複検知 / C-H3）
   ▼
Python server.py
   │ Event::OrderFilled / OrderCanceled / OrderRejected を IPC 送信
   │   ※ OrderPartiallyFilled は存在しない。leaves_qty > 0 の OrderFilled = 部分約定（nautilus 流）
   ▼
Rust 受信
   │ OrderSessionState.update_status(venue_order_id, ...)（= 立花 `sOrderNumber`）
   │ UI 通知 + 注文一覧パネルの再描画
```

**発注タイムアウト**: `engine_client.send(SubmitOrder)` → `OrderAccepted / OrderRejected` 待ちには **`tokio::time::timeout(Duration::from_secs(30), ...)`** を掛ける。タイムアウト時は caller に `INTERNAL_ERROR` を返し、Python 側の応答を待つ接続を破棄する（旧 HTTP 504 はもはや発生しない）。WAL には `submit` 行が残るため、再起動後の `IdempotentReplay` で unknown 状態として扱われる。**タイムアウト後に Python 側で受領した `sOrderNumber`** は WAL の `accepted` 行のみ書き込み（IPC 送信先の Rust は接続破棄済みなのでイベントは届かない）、Rust は次回起動時の WAL 復元で `client_order_id ↔ venue_order_id` を同期する。

同様に `ModifyOrder` / `CancelOrder` にも 30 秒タイムアウトを適用する。

**C-M1 `p_no` 連続性ルール**: `PNoCounter.next()` は **Unix 秒（×1000 ではない）** を初期値とする Python `int` カウンタ（asyncio 単一スレッドで lock 不要）。プロセス再起動時も wall-clock を初期値に取るため、再起動またぎでも `p_no` が必ず単調増加することが保証される（同一プロセス内では `+= 1` のみ）。実装は `python/engine/exchanges/tachibana_helpers.PNoCounter` として閉じる。

**C-M5 session 切れ伝播**: Python が `p_errno=2` を検知した場合、即座に `OrderSessionState` を `frozen` 状態へ遷移させる。以降:
- 全発注 IPC コマンドは `Event::OrderRejected{reason_code="SESSION_EXPIRED"}` で即時 reject（旧 HTTP 503 は Phase 8 で消滅）
- in-flight な `SubmitOrder` / `ModifyOrder` / `CancelOrder` / `CancelAllOrders` / `GetOrderList` は `OrderRejected { reason_code: "SESSION_EXPIRED" }` で完了させる
- WAL に `{"phase":"session_expired", ...}` 行を必ず書く（再起動時の状況復元用）
- `frozen` 解除は再ログイン成功イベント (`VenueCredentialsRefreshed`) 受領時のみ

**B-L1 REPLAY モード注記**: REPLAY モードは N1 で別ファイル分岐前提（本計画スコープ外）。本計画では live のみ扱う。詳細は [docs/specs/backtest/spec.md](./backtest.md) を参照。

### 2.3 取消フロー（Phase O1）

立花の `CLMKabuCancelOrder` は `sOrderNumber`（`venue_order_id`）が必須だが、IPC API は `client_order_id` を一次キーとする。そのため Rust IPC ハンドラ層で lookup してから Python に渡す。

```
ユーザー UI（iced） / Python helper（LiveSession.cancel_order）
   │ Command::CancelOrder { client_order_id }
   ▼
Rust IPC ハンドラ（engine-client + src/main.rs）
   │ ① OrderSessionState.get_venue_order_id(client_order_id)
   │      → None（unknown）: ORDER_STATUS_UNKNOWN reject
   │      → Some(venue_order_id): 続行
   │ ② engine_client.send(Command::CancelOrder { client_order_id, venue_order_id })
   ▼
Python tachibana_orders.cancel_order(session, second_password, client_order_id, venue_order_id)
   │ CLMKabuCancelOrder に sOrderNumber を載せて送信
   ▼
Rust 受信
   │ Event::OrderPendingCancel → OrderSessionState 更新 → caller に通知
```

> **Phase 8 注記**: 旧フローの `POST /api/order/cancel` → `Rust src/api/order_api.rs` → HTTP 200 は廃止。caller への応答は `Event::OrderPendingCancel` / `OrderCanceled` の IPC イベント受信で代替する。

**`venue_order_id` が unknown の場合**: `OrderSessionState` に `venue_order_id = None`（起動時復元の unknown 状態）の `client_order_id` へのキャンセル要求は `Event::OrderRejected{reason_code="ORDER_STATUS_UNKNOWN"}` で reject する。クライアントは `Command::GetOrderList` で確認してから再試行すること。

### 2.4 第二暗証番号 forget フロー（Phase O0）

```
ユーザー（UI ボタン or LiveSession の forget API）
   │ Command::ForgetSecondPassword を IPC 送信
   ▼
Python python/engine/server.py
   │ tachibana_auth.TachibanaSessionHolder（T0.4 新設）.second_password = None
   ▼
caller に完了通知
```

> **Phase 8 注記**: 旧 `POST /api/order/forget-second-password` HTTP path は廃止。IPC `Command::ForgetSecondPassword` を直接送る経路に統一。

`SetSecondPassword` フロー（第二暗証番号入力 modal から）:

```
iced modal（second_password.rs）
   │ ユーザー入力 → Command::SetSecondPassword { request_id, value }
   ▼
Python python/engine/server.py
   │ tachibana_auth.TachibanaSessionHolder（T0.4 新設）.second_password = SecretStr(value)
   │ 元の発注リクエスト（request_id で特定）を再開
   ▼
Rust 受信
   │ 元の SubmitOrder フロー §2.1 の ⑨ 以降へ
```

**注意**: `Command::SetSecondPassword` の `value` は IPC を経由するためプレーン `String`（`SecretString` は IPC JSON に送れない）。Python 側で `SecretStr` 化すること。

**C-2: ForgetSecondPassword ↔ in-flight SubmitOrder 競合ポリシー（確定 2026-04-28）**:

`Command::ForgetSecondPassword`（旧 `POST /api/order/forget-second-password`）と in-flight な `SubmitOrder` が同時に発生した場合の動作を以下のように規定する。

- **ポリシー**: `ForgetSecondPassword` は **即時適用**する（in-flight SubmitOrder の完了を待たない）。
- **根拠**: Python asyncio は単一スレッドであるため、`ForgetSecondPassword` の処理は必ず `await` ポイント間に割り込む。`_do_submit_order` は `second_password` を `self._session_holder.get_password()` でローカル変数に取得済みのため、その後に holder をクリアしても in-flight SubmitOrder には影響しない。
- **影響**: in-flight SubmitOrder は保持済みの `second_password` ローカル変数で正常に完了する。holder をクリアした後に発行される次の SubmitOrder は `SecondPasswordRequired` で応答される。
- **ログ**: `ForgetSecondPassword` 受信時に in-flight な SubmitOrder が存在する場合（`_submit_order_inflight_count > 0`）は `log.info` で件数を記録する（デバッグ用途）。`SubmitOrder` の in-flight カウントは `_do_submit_order` の先頭でインクリメント、`finally` でデクリメントする。

```
ForgetSecondPassword 受信（asyncio recv_loop）
   │ _session_holder.clear()  ← 即時クリア
   │ if _submit_order_inflight_count > 0:
   │     log.info("ForgetSecondPassword: %d SubmitOrder(s) in-flight; "
   │              "they will complete with already-captured second_password", count)
   ▼
既存 in-flight SubmitOrder（asyncio task, 別コルーチン）
   │ second_password = <ローカル変数に取得済み>  ← holder クリアの影響を受けない
   │ await tachibana_submit_order(..., second_password, ...)
   ▼
次の SubmitOrder（holder クリア後）
   │ second_password = self._session_holder.get_password()
   │ → None → SecondPasswordRequired を返す
```

**`Command` enum の `Debug` 手実装（セキュリティ必須・**Tpre.2 で実施**）**: `dto.rs` の `Command` に現在ある `#[derive(Debug)]` を **Tpre.2 でスキーマ拡張と同時に外して手実装に切り替える**。T0.3 まで先送りすると `SetSecondPassword` variant が derive された状態でコードが存在する期間が生じ、ログへの平文漏洩リスクがある。手実装では `SetSecondPassword { .. }` arm のみ `"SetSecondPassword { value: [REDACTED] }"` と出力し、他の variant は従来通り出力する。`value` が `[REDACTED]` にマスクされることを **Tpre.2 の受け入れ条件**として単体テストで検証すること。

## 3. IPC スキーマ拡張（schema 1.2 → 1.3）

[engine-client/src/dto.rs](https://github.com/botterYosuke/e-station/blob/main/../../engine-client/src/dto.rs) に追加:

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
        venue: String,                  // "tachibana" 固定（本計画では他 venue を扱わない）
        order: SubmitOrderRequest,      // nautilus OrderFactory 入力相当
    },
    ModifyOrder {
        request_id: String,
        venue: String,
        client_order_id: String,
        /// 他端末注文など caller が直接 venue_order_id を知っている場合のみ Some で渡す。
        /// None の場合は Rust IPC ハンドラが OrderSessionState から lookup する。
        venue_order_id: Option<String>,
        change: OrderModifyChange,      // qty / price / trigger / expire を Option で
    },
    // Rust は OrderSessionState で client_order_id → venue_order_id を lookup してから Python に渡す（§2.3）
    // venue_order_id が None（unknown）の場合は IPC で ORDER_STATUS_UNKNOWN を返し Python へ送らない
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
                                         // **C1 Phase 制約**: Phase O0/O1 では `null` 必須、Phase O2/O3 までは `LAST` 固定。
                                         //                  他値受信時は 400 + `reason_code="VENUE_UNSUPPORTED"`
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
    OrderAccepted    { client_order_id: String, venue_order_id: Option<String>, ts_event_ms: i64 },
    OrderRejected    { client_order_id: String, reason_code: String, reason_text: String, ts_event_ms: i64 },
    OrderPendingUpdate { client_order_id: String, ts_event_ms: i64 },
    OrderPendingCancel { client_order_id: String, ts_event_ms: i64 },
    OrderCanceled    { client_order_id: String, venue_order_id: String, ts_event_ms: i64 },
    OrderExpired     { client_order_id: String, venue_order_id: String, ts_event_ms: i64 },
    OrderFilled {
        client_order_id: String,
        venue_order_id: String,
        trade_id: String,                // 重複検知キー（`(venue_order_id, trade_id)`）
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
    pub venue_order_id: String,            // venue_order_id（= 立花 `sOrderNumber`）
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
    pub ts_event_ms: i64,                  // 立花 JST datetime をパースし Unix ms (UTC epoch) に変換（他 IPC イベントと単位統一）
}
```

**`OrderPartiallyFilled` は持たない**: nautilus では「約定が起きるたびに `OrderFilled` を出し、`leaves_qty` で部分か全部かを判定」する流儀。本計画も合わせる。

**`second_password` の IPC 扱い**:
- 既存の `TachibanaCredentialsWire.second_password: Option<String>` フィールドは Phase 1 から存在するが、Order Phase では **常に `None` で送信する**（`SetVenueCredentials` 経由では第二暗証番号を渡さない）
- 発注時の第二暗証番号は専用の `Command::SetSecondPassword { value: String }` コマンドのみで伝達する。IPC payload（`SubmitOrderRequest` 等）には含めない
- **A-M1 撤去タイミング統一（双方向リンク確定）**: `TachibanaCredentialsWire.second_password` は Phase 1 互換のため当面残す:
  - **O1 完了 PR**: `#[deprecated(note = "second_password は Order Phase 以降使用しない。Command::SetSecondPassword を使うこと")]` を付与
  - **後続 PR（O2 着手前）**: フィールド自体を削除し、schema minor を bump
- **C-M6 second_password 撤去安全弁**: `SetVenueCredentials` 経路では Rust serializer 側で `second_password.is_none()` を assert する（serializer に `debug_assert!`、テストで pin）。万一 `Some(_)` が混入した場合は serializer がパニックではなく `Err` を返し、HTTP 500 + `reason_code="INTERNAL_ERROR"` で reject する

## 4. 冪等性（flowsurface `agent_session_state.rs` の移植）

> **Phase 8 注記**: 当初は `src/api/order_session_state.rs` に新設予定だったが、HTTP API 廃止に合わせて **`engine-client/src/order_session_state.rs`** に着地している（Rust IPC ハンドラ層）。型構造・API は当初設計どおり。

`engine-client/src/order_session_state.rs`:

```rust
pub struct ClientOrderId(pub String);

// nautilus OrderStatus と同じ文字列を保持する型エイリアス
// 値は §10.5 の写像表の右列（"SUBMITTED" / "ACCEPTED" / ... / "REJECTED"）に限定
// enum にせず String にすることで、将来の nautilus バージョン追加に対して後方互換を保つ
type NautilusOrderStatus = String;

pub struct AgentOrderRecord {
    pub venue_order_id: Option<String>, // venue_order_id（= 立花 `sOrderNumber`）; Python 応答受領後に埋める（未確定時は None）
    pub request_key: u64,               // 入力 body の構造ハッシュ
    pub status: NautilusOrderStatus,    // nautilus OrderStatus 文字列（§10.5）
}

pub enum PlaceOrderOutcome {
    Created { client_order_id: ClientOrderId },
    /// venue_order_id は Python 応答受領前（unknown 状態）では None になりうる。
    IdempotentReplay { venue_order_id: Option<String> },
    /// 同一 client_order_id で body が異なる再送 — 409 Conflict。
    Conflict { existing_venue_order_id: Option<String> },
    /// p_errno=2 受領後の frozen 状態。以降の発注は即時 SESSION_EXPIRED で reject。
    SessionFrozen,
}

pub struct OrderSessionState {
    map: HashMap<ClientOrderId, AgentOrderRecord>,
}
```

flowsurface との差分:
- 立花は **`venue_order_id` がサーバから後で返ってくる**ため、`Created` 受領時点では `venue_order_id = None`。Python 応答で `update_venue_order_id()` を呼んで埋める
- セッションが日跨ぎで切れるので **当日分のみ保持**
- **プロセス再起動跨ぎは監査ログ WAL（§4.2）から復元**

**B-M2 flowsurface AgentSessionState 命名対応表**: 移植元 `flowsurface/src/api/agent_session_state.rs` から本計画へのリネーム前後対応:

| 移植元（flowsurface） | 本計画（e-station） | 備考 |
|---|---|---|
| `place_or_replay(...)` | `try_insert(client_order_id, key)` | API 名を nautilus 流の「冪等 insert」表現に統一 |
| `order_id` | `venue_order_id` | nautilus タクソノミー準拠（venue 採番 ID と明示） |
| `key`（u64 ハッシュ） | `request_key`（u64 ハッシュ） | flowsurface の `key` だけだと意味が曖昧なため改名 |
| （状態フィールド無し） | `status: NautilusOrderStatus`（追加） | flowsurface は単一段階だが本計画は SUBMITTED→ACCEPTED→… の状態遷移を持つ |
| `PlaceOrderOutcome::IdempotentReplay { order_id }` | `PlaceOrderOutcome::IdempotentReplay { venue_order_id: Option<String> }` | `order_id` → `venue_order_id` rename。Python 応答受領前は `None`（unknown 状態）のため `Option` |
| `PlaceOrderOutcome::Conflict { existing_order_id }` | `PlaceOrderOutcome::Conflict { existing_venue_order_id: Option<String> }` | 同。`Option` で unknown 状態も表現 |
| （移植元に無し） | `PlaceOrderOutcome::SessionFrozen` | `p_errno=2` 受領後の frozen 状態。以降の全発注を即時 `SESSION_EXPIRED` で reject |

**B-L2 OrderSubmitted 即時発火の根拠**: `OrderSubmitted` を HTTP 送信前に発火する設計は **flowsurface 移植ではなく nautilus タクソノミー準拠の新規追加**。flowsurface `place_or_replay` は単一段階（受付 = 結果確定）だが、本計画は nautilus の `SUBMITTED → ACCEPTED → FILLED` の段階遷移に合わせて `OrderSubmitted` を即時発火する。

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

`tachibana_orders.jsonl`（[spec.md §3.2](#32-安全装置誤発注防止)）を **発注前後 2 段階で append** し、起動時に復元できる WAL として使う。

#### WAL フォーマット仕様（T0.7 確定版 — Agent C の Rust 実装がこの仕様を読む）

各行は JSON オブジェクト + `\n` 終端（JSONL 形式）。ファイルエンコーディングは UTF-8。

**submit 行**（HTTP 送信直前に fsync 込みで書く）:
```json
{
  "phase": "submit",
  "ts": 1700000000000,
  "client_order_id": "cid-001",
  "request_key": 12345,
  "instrument_id": "7203.TSE",
  "order_side": "BUY",
  "order_type": "MARKET",
  "quantity": "100"
}
```
| フィールド | 型 | 説明 |
|---|---|---|
| `phase` | `string` | 固定値 `"submit"` |
| `ts` | `integer` | Unix ミリ秒（UTC）|
| `client_order_id` | `string` | クライアント注文 ID（ASCII printable、1〜36 文字）|
| `request_key` | `integer` | `xxh3_64` ハッシュ（u64）。Rust 側 canonicalization で計算、Python 側は 0 を仮置き（T0.7 時点）|
| `instrument_id` | `string` | `"7203.TSE"` 形式 |
| `order_side` | `string` | `"BUY"` または `"SELL"` |
| `order_type` | `string` | `"MARKET"` / `"LIMIT"` 等 |
| `quantity` | `string` | 発注数量（精度保持文字列）|

**accepted 行**（応答受領後に flush で書く）:
```json
{
  "phase": "accepted",
  "ts": 1700000001000,
  "client_order_id": "cid-001",
  "venue_order_id": "ORD-001",
  "p_no": 1700000001,
  "warning_code": null,
  "warning_text": null
}
```
| フィールド | 型 | 説明 |
|---|---|---|
| `phase` | `string` | 固定値 `"accepted"` |
| `ts` | `integer` | Unix ミリ秒（UTC）|
| `client_order_id` | `string` | submit と対応するクライアント注文 ID |
| `venue_order_id` | `string \| null` | 立花 `sOrderNumber`。欠落時は `null` |
| `p_no` | `integer` | 送信した `p_no` 値（単調増加保証の確認用）|
| `warning_code` | `string \| null` | 立花 `sWarningCode`（正常時は空文字 or `null`）|
| `warning_text` | `string \| null` | 立花 `sWarningText`（正常時は空文字 or `null`）|

**rejected 行**（応答受領後に flush で書く）:
```json
{
  "phase": "rejected",
  "ts": 1700000001000,
  "client_order_id": "cid-001",
  "reason_code": "SESSION_EXPIRED",
  "reason_text": "Tachibana セッションが切れています"
}
```
| フィールド | 型 | 説明 |
|---|---|---|
| `phase` | `string` | 固定値 `"rejected"` |
| `ts` | `integer` | Unix ミリ秒（UTC）|
| `client_order_id` | `string` | submit と対応するクライアント注文 ID |
| `reason_code` | `string` | `SESSION_EXPIRED` / `VENUE_REJECTED` 等 |
| `reason_text` | `string` | ヒューマンリーダブルなエラー詳細（第二暗証番号の実値を含まないこと）|

**Rust 実装者向けの起動時復元ロジック**（`OrderSessionState::restore_from_wal`）:
1. WAL ファイルを行単位で読み込む
2. 末尾行に `\n` が無ければ truncated → スキップ + `tracing::warn!`（C-R5-H1）
3. `phase == "submit"` の行を `client_order_id → (request_key, venue_order_id=None, status="SUBMITTED")` として map に登録
4. `phase == "accepted"` の行で `venue_order_id` を後着更新（`update_venue_order_id`）、`status` を `"ACCEPTED"` に更新
5. `phase == "rejected"` の行で当該エントリを map から除去（再送防止対象外）
6. WAL に `submit` のみで `accepted`/`rejected` が無い行 → `unknown` 状態で保持（`venue_order_id = None`）

```
{"phase":"submit", "ts":..., "client_order_id":"...", "request_key":12345, "instrument_id":"7203.TSE", ...}
{"phase":"accepted", "ts":..., "client_order_id":"...", "venue_order_id":"sOrderNumber=ABC", "p_no":..., "warning_code": null, "warning_text": null}
{"phase":"rejected", "ts":..., "client_order_id":"...", "reason_code":"...", "reason_text":"..."}
```

- `submit` 行は **HTTP 送信直前**に `fsync` 込みで書く（クラッシュ時の不整合を最小化）
- **fsync 失敗時の扱い**: `submit` 行の `fsync` が失敗した場合は HTTP 500 + `reason_code="INTERNAL_ERROR"` で reject し、**WAL に書けない発注は立花へ送信しない**（write-ahead log の前提を崩さない）
- `accepted` / `rejected` 行は応答受領後に `flush` で書く（`fsync` 不要）。`f.flush()` 直接呼び出し（同期）を許容する（`run_in_executor` 不要: `accepted` 行のバッファ残りクラッシュは Phase O1 `GetOrderList` で補完可能な設計のため同期 flush の遅延リスクを許容する）
  - `accepted` が OS バッファ残りのままクラッシュした場合、Rust 起動時は `unknown` 状態で復元する。
    Phase O1 の `GetOrderList` で `venue_order_id` を補完できるため許容する
- **第二暗証番号は絶対に出さない**（unit テストで `grep -i second_password` 等で検証）
- **C-H1 仮想 URL マスク**: 仮想 URL（`url_request` / `url_master` / `url_event` / `url_price` 等、ログイン応答で取得される一時 URL）は **WAL／ログ／`reason_text`／監査ログから完全除外**する。`tachibana_codec.mask_virtual_url(s: str) -> str` を 1 箇所だけ定義し、送信前後ですべての文字列出力経路を必ず通すこと（レビューチェック項目）。
- **L4 制御文字対策**: WAL の各行は **ASCII printable + UTF-8** に正規化する。改行は `\n` エスケープ、Shift-JIS 由来の制御文字（`\x00`–`\x1f`、`\x7f`）は除去する。JSON エンコード前に `s.encode("utf-8", "replace")` でサニタイズ。
- **B-L1 REPLAY ガード**: REPLAY モードは N1 で別ファイル分岐前提。本計画では live のみ扱う（[docs/specs/backtest/spec.md](./backtest.md) 参照）。WAL ファイル名は live 固定で `tachibana_orders.jsonl`、REPLAY が導入されるときは別ファイルに分岐する。
- **C-R2-L3 `p_errno` 空（正常）扱い**: `p_errno == ""`（正常）の場合は rejected 行を書かない（= 正常レスポンスのみ accepted/filled で記録）。
- **C-R5-H1 Truncation 復元規約**: 復元時に最終行に `\n` が無ければ truncated（fsync 前の crash）とみなしスキップ + structured log で WARN を出す。partial 行は再生対象外で、対応する `client_order_id` は WAL 上「未送信」扱い → 起動後 `OrderSessionState` には登場しない。

### 4.3 起動時復元（Phase O0 必須）

**WAL 読み込みオーナーシップ**: WAL ファイル (`tachibana_orders.jsonl`) は Python が書き、**Rust が直接読む**。`data_path()` は Rust 側が起動時 config から知っている（Python IPC 経由で受け取る必要はない）。Rust アプリ起動直後（Python プロセス起動の前）に読むため、ファイルが存在しない場合は空 map で初期化すればよい（初回起動 / 昨日以前のみ）。

**WAL パス安定性**: `data_path()` を設定で変更すると旧 WAL が読まれず重複発注防止が機能しなくなる。`tachibana_orders.jsonl` のパスは `data_path()` 変更後も旧パスを参照できるよう、**起動 config の `data_path` を変更したら旧 WAL を手動で移動またはリセットする**運用手順をドキュメントに記載すること（Phase O0 リリースノートに追記）。

**同時アクセス**: Rust は Python プロセス起動前に WAL を読み（§4.3 の順序）、その後は Python のみが append する。両プロセスが同時にアクセスするウィンドウは原則ない。ただし Python 再起動タイミングで競合しうる場合は Python 側で `fcntl.flock(f, LOCK_EX)` を `submit` 行書き込み前後に取得・解放すること（Windows では `msvcrt.locking` で代替）。

[implementation-plan T0.7](../roadmap/order/implementation-plan.md) で以下を Phase O0 段階で実装:

1. アプリ起動 → `OrderSessionState::new()` → 当日分 WAL を読み戻し
2. `submit` だけがあって `accepted`/`rejected` が無い行は **「unknown 状態」**で復元（`venue_order_id = None`）
3. ユーザーが同一 `client_order_id` で再送 → 下記ルールで処理（重複発注防止の本丸）
4. unknown 状態の解決は `Phase O1` の `GetOrderList` 復元（[T1.5](../roadmap/order/implementation-plan.md#t15-起動時の台帳復元)）で `venue_order_id` を埋める

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
- nautilus 互換要件（[spec.md §6.1 用語・型の整合](#61-用語型の整合必須)）で「Strategy 層に第二暗証番号を見せない」を採用するため、Python メモリ保持 1 箇所に閉じる方が一貫
- 「毎回入力」は UX が破綻するため折衷で「セッション中保持」
- keyring opt-in は **提供しない**（案を増やすと攻撃面・実装複雑度が上がるため）

### 5.1 取得タイミング（Phase O0）

- ログイン時には収集しない（[tachibana/architecture.md F-H5](../architecture/modules/tachibana-adapter.md) の Phase 1 方針を維持）
- `POST /api/order/submit` で **Python 側に未保持なら** Rust 側に `Event::SecondPasswordRequired { request_id }` を返す → iced 側で modal を出して入力 → `Command::SetSecondPassword { value }` で Python に渡す → Python メモリに保持 → 元の発注リクエストを再開
- 同一プロセス内では以降の発注で再入力不要（メモリヒット）
- `data::config::tachibana::TachibanaCredentials.second_password: Option<SecretString>` は **常に `None` のまま**（keyring に書かない）

### 5.2 入力 UI

- **iced 側 modal**（tkinter ではない）。発注フォームの隣で完結させ UX を保つ
- ユーザーがキャンセルした場合、`/api/order/submit` は 403 + `reason_code = "SECOND_PASSWORD_REQUIRED"`（spec.md §5.2 の SCREAMING_SNAKE_CASE に統一）
- ログインダイアログ（tkinter）には第二暗証番号フィールドを **追加しない**

### 5.3 メモリ保持

- **B5 worker フィールド配置**: `TachibanaWorker`（または equivalent runtime オーナー）が `StartupLatch`（既存・Phase 1）と `TachibanaSessionHolder`（新規・本計画 Phase O0）を **並列フィールド**として保持する。両者は相互依存しない（StartupLatch はログイン完了ゲート、Holder は第二暗証番号 + idle timer）。
- Python 側 `tachibana_auth.TachibanaSessionHolder`（**Phase O0（T0.4）で新規追加する runtime singleton クラス**。既存 `TachibanaSession` は wire データ、Holder は asyncio idle timer + `second_password: SecretStr | None` を保持。立花計画 Phase 1/2 と本計画 Phase O0/O1/O2/O3 の混同を避けるため本計画フェーズで表記）に `second_password: SecretStr | None` を追加
- 発注時のみ `expose_secret()` し、リクエスト送信後は **ローカル変数を削除**（function-local scope）
- セッション切れ（`p_errno=2`）検知時は `second_password` も **クリア**（再ログイン時に再入力させる）
- idle forget: `second_password_idle_forget_minutes`（§7、既定 30 分）経過で自動クリア。実装担当: `TachibanaSessionHolder` の asyncio idle timer
  - **C3 idle timer 規約**: monotonic（`asyncio.get_event_loop().time()` 基準）。reset trigger は **第二暗証番号を expose する全 IPC コマンド** = `SubmitOrder` / `ModifyOrder` / `CancelOrder` / `CancelAllOrders` / `SetSecondPassword` の 5 イベント（`GetOrderList` 等の expose しないコマンドではタイマーを延長しない）。suspend/resume またぎは復帰時刻基準で再計測
- プロセス終了時に消える（永続化なし）
- Python プロセスのコアダンプ・スワップ対策は `pydantic.SecretStr` に依存（best-effort）

- **C-R5-H2 `SECOND_PASSWORD_INVALID`（立花 `p_errno=4`）受領時の挙動**: (1) Python メモリの `second_password` を即クリア（`TachibanaSessionHolder.second_password = None`）、(2) 連続 3 回（counter は holder 内部）で 30 分間 modal 表示を抑止（カウンタ・抑止期間ともに `[tachibana.order]` config 化、デフォルト 3 / 1800 秒）、(3) 抑止期間中の発注は HTTP 423 Locked + `reason_code="SECOND_PASSWORD_LOCKED"` で reject。立花側のアカウントロックを未然に防ぐ。

### 5.4 forget API

- `POST /api/order/forget-second-password` を提供。ユーザーが「席を離れる」前に明示的にメモリから消せる

## 6. EVENT EC フレームのパース（Phase O2）

`tachibana_event.py._parse_ec_frame(items: list[tuple[str, str]]) -> OrderEcEvent`:

立花 EC フレームの主要項目（マニュアル §`#CLMEvent_EC` 参照）:

| 立花フレームキー | 意味 | IPC への写像 |
|---|---|---|
| `p_NO` | 注文番号 | `venue_order_id` |
| `p_EDA` | 約定枝番（= 立花 `p_eda_no`） | `trade_id`（IPC `OrderFilled.trade_id`、重複検知キー） |
| `p_NT` | 通知種別（注文受付・約定・取消・失効） | `OrderAccepted` / `OrderFilled` / `OrderCanceled` / `OrderExpired` への分岐 |
| `p_DH` | 約定単価 | `last_price` |
| `p_DSU` | 約定数量 | `last_qty` |
| `p_ZSU` | 残数量 | `leaves_qty`（部分約定判定。0 なら全約定） |
| `p_OD` | 約定日時 | `ts_event_ms`（注文日時ではなく **約定日時**を使うこと） |

**重複検知**: **キーは `(venue_order_id, trade_id)`** の組（C-H3 統一）。プロセスメモリ `set[tuple[str, str]]` に保持。IPC フィールド名 `trade_id` に統一する（`eda_no` / `p_EDA` / `p_eda_no` の混用を禁止）。再接続時の再送はここで弾く。

**B-L3 trade_id 命名注記**: flowsurface 側に EC パーサが存在しない場合、本計画で新規実装する（移植ではない）。IPC 名は **`trade_id` 固定**。Tpre.5（マニュアル pin）確認後、もし flowsurface に既存 EC 実装が見つかれば、本注記を「移植元シンボル名」付きで書き換える方針とする。

**表注記（venue 用語の集約）**: 上表 `p_EDA` 列の括弧書きが `trade_id = 立花 p_eda_no` の対応点。本計画ではコード／コメント／ログ／IPC では `trade_id` のみを使い、立花用語 `p_eda_no` はこの注記 1 箇所にのみ集約する。

**C2 SUBMITTED → REJECTED reason_code set**: `SUBMITTED → REJECTED` 遷移を取りうる `reason_code` は **`{SESSION_EXPIRED, SECOND_PASSWORD_INVALID, VENUE_REJECTED}`** に限定する（[spec.md §6.2 イベントタクソノミー](#62-イベントタクソノミー必須) 参照）。それ以外の reason_code は ACCEPTED 後の REJECTED として扱う。

**C4 EVENT URL 構築**: `tachibana_url.build_event_url(base, params)` 内で `\n` / `\t` / `\x01-\x03` を含むパラメータがあれば **reject（`ValueError`）**。log は host のみ（クエリ／パスは出さない）。所在はこの 1 箇所にピン留め。

## 7. 設定値（起動 config / env）

```toml
# pyproject 管轄外、起動 CLI / config ファイル経由
[tachibana.order]
max_qty_per_order = 1000
max_yen_per_order = 1_000_000
require_confirmation = true            # Rust UI: 確認モーダル必須
rate_limit_window_secs = 3              # C-R2-H1: rate limit のスライディング窓（秒）
rate_limit_max_hits = 2                 # C-R2-H1: 窓内の最大リクエスト数。超過時 reason_code="RATE_LIMITED"（HTTP 429）
second_password_idle_forget_minutes = 30  # C-R2-H2: 第二暗証番号の idle forget 閾値（分）
second_password_invalid_max_retries = 3   # C-R5-H2: SECOND_PASSWORD_INVALID 連続回数の上限（超過で抑止）
second_password_lockout_secs = 1800       # C-R5-H2: 抑止期間（秒、デフォルト 30 分）
```

env:
- `TACHIBANA_ALLOW_PROD=1` … 本番 URL での発注解禁（Phase 1 と共通ガード）
- `DEV_TACHIBANA_SECOND_PASSWORD` … debug ビルド + Python `tachibana_login_flow.py` のみが読む（SKILL.md S2）。release では無視

## 8. flowsurface との対応表

実装時に「ここは flowsurface のどこを写すか」を即引けるよう一覧化:

> ※ `TachibanaWire*` 型は T0.4 実装対象（未実装）。

| 本計画の Python シンボル | flowsurface Rust シンボル | 備考 |
|---|---|---|
| `tachibana_orders.TachibanaWireOrderRequest` (pydantic, 立花 wire 専用) | `tachibana::NewOrderRequest` | **これは内部 wire 型のみ**。public API は nautilus 互換 `NautilusOrderEnvelope` を受け取り、内部で `TachibanaWireOrderRequest` に写像する。wire 型 prefix は **`TachibanaWire*`** に統一（`TachibanaWireModifyRequest` 等） |
| `tachibana_orders.TachibanaWireModifyRequest` | `tachibana::CorrectOrderRequest` | "correct" 用語は Python 内部に閉じる。wire 型 prefix `TachibanaWire*` に統一 |
| `tachibana_orders.TachibanaWireCancelRequest` | `tachibana::CancelOrderRequest` | 同。wire 型 prefix `TachibanaWire*` に統一 |
| `tachibana_orders.NewOrderResponse` | `tachibana::NewOrderResponse` | `sWarningCode` / `sWarningText` も含める。**HTTP レスポンスの `warning_code` / `warning_text` フィールドに露出させること**（spec.md §4 の `POST /api/order/submit` レスポンスに追記済み）。立花が注文を受け付けつつ警告を返した場合にユーザーが気付けるようにする |
| `tachibana_orders.ModifyOrderResponse` | `tachibana::ModifyOrderResponse` | 同 |
| `tachibana_orders.OrderListRequest/Response/Record` | `tachibana::OrderListRequest/Response/OrderRecord` | 同 |
| `tachibana_orders.submit_order()` | `tachibana::submit_order()` | 戻り値型は `SubmitOrderResult`（pydantic 結果型、spec.md §6.3 と一致）。`OrderRejectedError` は raise 経路の例外型であり、`SubmitOrderResult` の `rejected` バリアントとは別概念（例外は internal error / 想定外失敗、`rejected` は venue 業務拒否を表す）。flowsurface 旧シンボル `submit_new_order` 等は本計画では使用しない |
| `tachibana_orders.cancel_order()` | `tachibana::cancel_order()` | flowsurface 旧シンボル `submit_new_order` 等は本計画では使用しない |
| `tachibana_orders.modify_order()` | `tachibana::modify_order()` | flowsurface 旧シンボル `submit_new_order` 等は本計画では使用しない |
| `tachibana_orders.cancel_all_orders()` | `tachibana::cancel_all_orders()` | flowsurface 旧シンボル `submit_new_order` 等は本計画では使用しない |
| `tachibana_orders._compose_request_payload()` | `tachibana::serialize_order_request()` | `p_no` / `p_sd_date` / `sCLMID` 後付け |
| 本計画 `PlaceOrderOutcome::Created { client_order_id }` | flowsurface `PlaceOrderOutcome::Created { order_id }` | **意味反転ではなく rename**: 両者とも事前採番 UUID を返す。フィールド名の差（`order_id` → `client_order_id` への rename）であり、venue 採番値は本計画では `update_venue_order_id` で後着する |
| `engine-client/src/order_session_state.rs::OrderSessionState` | `flowsurface/src/api/agent_session_state.rs::AgentSessionState`（移植元） | **Rust → Rust の移植**（Python ではない）。Phase 8 で `src/api/` から `engine-client/src/` に移管 |
| `engine-client/src/order_session_state.rs::PlaceOrderOutcome` | `flowsurface/src/api/agent_session_state.rs::PlaceOrderOutcome`（移植元） | 同 |
| `OrderSessionState::try_insert(client_order_id, request_key)` | `AgentSessionState::place_or_replay(...)` | **2 引数化**: venue_order_id は後着するため引数に含めない。`update_venue_order_id()` で OrderAccepted 受信後に埋める |

**B-M3 `TachibanaWireOrderRequest.__repr__` のマスク対象**: **`second_password` のみマスク**（flowsurface `NewOrderRequest` の `Debug` 手実装と同等）。`user_id` / `password` は注文 wire（`CLMKabuNewOrder` 等）には載らないため、注文 wire 型のマスク対象には含めない。`user_id` / `password` のログ漏洩防止は別経路（認証 wire 型 `TachibanaCredentialsWire` / ログイン関連 helper）で対処する。

## 9. Python 単独モードへの含み

将来 Rust（iced）を外しても、本計画の Python レイヤーはそのまま動く:
- `tachibana_orders.py` は HTTP/IPC 非依存
- 冪等性マップだけは Rust 側にあるため、Python 単独モード時は Python で同等のものを書く必要がある（[`python/engine/order_session.py`](../../../python/engine/) として将来追加）

## 10. nautilus_trader との型マッピング

[spec.md §6.5 禁止事項](#65-禁止事項) の不変条件を実装に落としたマッピング表。N2 移行時の作業はこの表の右 2 列を入れ替えるだけ。

### 10.0 立花送信パイプラインの不変条件（レビューチェック必須）

以下は §10 全体に係る前提条件。実装レビューでは毎回この 3 点を確認する:

- **C-H1 仮想 URL マスク**: 仮想 URL は WAL／ログ／`reason_text`／監査ログから完全除外する。`tachibana_codec.mask_virtual_url()` を 1 箇所だけ定義し、送信前後（リクエスト URL 構築直後、レスポンス受領後）でこの関数を必ず通すこと。
- **C-H2 Shift-JIS パイプライン**: 送信文字列は **「Shift-JIS 化 → `func_replace_urlecnode`（URL エンコード）→ URL 結合」** の順序で固定。順序入れ替え・段階スキップは禁止。実装は `tachibana_codec.encode_request_payload()` に集約し、レビューチェックリストにも追加すること。**JSON 構造文字（`{` / `}` / `"` / `:` / `,`）は構造維持のため非エンコード**で残し、**値部分のみ**を Shift-JIS 化 →`func_replace_urlecnode`（30 文字置換）→ URL 結合の順で処理する。
- **C-H3 EC 重複検知キー**: EC フレームの重複検知キーは **`(venue_order_id, trade_id)`** の組のみを正本とする。IPC フィールド名は **`trade_id` 固定**（`eda_no` / `p_EDA` / `p_eda_no` の混用は全レイヤーで禁止）。
- **C-M6 second_password 撤去安全弁**: `SetVenueCredentials` 経路では Rust serializer 側で `second_password.is_none()` を assert する（テストで pin）。
- **C-R2-M3 deny_unknown_fields**: `SubmitOrderRequest` / `OrderModifyChange` 等の IPC DTO は serde `#[serde(deny_unknown_fields)]` を必須。`second_password` 系フィールドの混入をテストで pin（C-M6 と組）。

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
| `GTD` | `sOrderExpireDay=YYYYMMDD`（`expire_time` から JST 営業日に変換） | 10 営業日上限の検証は Python 側。**C5**: `expire_time_ns: i64` → `sOrderExpireDay` 変換時の営業日判定は `CLMDateZyouhou` マスタから取得。マスタ未取得時は **503 + `reason_code="INTERNAL_ERROR"`** を返す |
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

**`trigger_type` の逆写像**: `trigger_type` は **Phase O0/O1 では `null` 固定**、Phase O2/O3 では `LAST` 固定とする。立花 `CLMOrderList` 応答からは `trigger_type` に直接マッピングされる field は存在せず、本計画では固定値で埋める方針（spec.md §6.1 と整合）。逆写像時に「逆指値あり = `LAST`、なし = `null`」を一律適用する。

### 10.3 OrderSide 写像

| nautilus | 立花 `sBaibaiKubun` |
|---|---|
| `BUY` | `"3"` |
| `SELL` | `"1"` |

立花固有の `5`=現渡 / `7`=現引 は **`tags=["close_action=physical_settle_*"]`** で `BUY` / `SELL` に sub-classification する。OrderSide enum は拡張しない。

### 10.4 venue extension `tags` の正規化キー

これが **tag の正本レジストリ**。[spec.md §5.1](#51-nautilus-互換のリクエストシェイプ) はサンプル提示のみ。新キーを追加する PR はこの表を更新すること。

**`cash_margin` 値**（`sGenkinShinyouKubun` への写像）:

| tag 値 | 立花値 | 意味 |
|---|---|---|
| `cash_margin=cash` | `"0"` | 現物（省略時の既定） |
| `cash_margin=margin_credit_new` | `"2"` | 制度信用 新規 |
| `cash_margin=margin_credit_repay` | `"4"` | 制度信用 返済 |
| `cash_margin=margin_general_new` | `"6"` | 一般信用 新規 |
| `cash_margin=margin_general_repay` | `"8"` | 一般信用 返済 |

**`account_type` 値**（`sZyoutoekiKazeiC` への写像）— **省略時はログイン応答の `sZyoutoekiKazeiC` をパススルー**（口座属性と一致させる意図）:

> **B-M4 確定（2026-04-28）**: 立花マニュアル §`CLMKabuNewOrder` の `sZyoutoekiKazeiC` 定義値を正本として確定。源泉徴収区分はこのフィールドで区別しない（ログイン応答の別フィールドで管理）。

| tag 値 | 立花値 | 意味 |
|---|---|---|
| `account_type=specific` | `"1"` | 特定口座 |
| `account_type=general` | `"3"` | 一般口座 |
| `account_type=nisa` | `"5"` | 一般NISA（2024年以降売却のみ可） |
| `account_type=nisa_growth` | `"6"` | NISA成長投資枠（Phase O4） |

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
4. ~~本計画の Rust 側 HTTP API `/api/order/*` は **そのまま残す**（手動発注・curl 経路は維持）~~（Phase 8 で HTTP API は全廃済み。手動発注経路は Python helper `LiveSession` に統一）。nautilus 戦略経由の発注は `LiveExecutionEngine` から直接 Python ワーカーに入る
5. `OrderSessionState`（Rust 冪等性マップ）は nautilus 経由フローでは不要だが、IPC 経路（GUI / Python helper）では引き続き使う（撤去しない）

つまり **N2 は新規ファイルを足すだけで、既存の本計画コードを書き換えない**ことが目標。本計画の Phase O0–O3 のレビューチェックリストに「nautilus 移行時に書き換えが発生しないか」を毎回入れる。
