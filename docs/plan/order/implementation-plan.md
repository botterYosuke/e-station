# 立花注文機能: 実装計画

**前提条件（着手ブロッカー）**: 立花 Phase 1（[docs/plan/tachibana/implementation-plan.md](../tachibana/implementation-plan.md)）の T2（認証実装）以降が完了していること。具体的には以下のファイルが実在していること:

- `python/engine/exchanges/tachibana_login.py`（認証・セッション管理）
- `python/engine/exchanges/tachibana_event.py`（EVENT WebSocket 受信ループ、Phase O2 の EC パーサ追加先）

**現状確認（2026-04-25）**: `python/engine/exchanges/` に tachibana 系ファイルが存在しない。**Phase 1 を先に完了させてから本計画に着手すること**。O-pre の Tpre タスクは Phase 1 の認証基盤が無くても型定義だけ進められるが、T0.3 以降は Phase 1 完了が必要。

## マイルストーン一覧

| Phase | ゴール | 期間目安 |
|---|---|---|
| **O-pre** | **nautilus 互換型のスケルトン凍結 + EVENT EC 仕様の根拠確保**（実装ゼロ・型と一次資料のみ） | 1〜2 日 |
| O0 | 第二暗証番号 iced modal + 現物・成行・買のみ単発発注がデモ環境で通る + 監査ログ WAL から再起動跨ぎで idempotent replay 可能 | 3〜4 日 |
| O1 | 訂正・取消・注文一覧 + UI 注文一覧パネル | 3〜4 日 |
| O2 | EVENT EC 約定通知の購読と UI 反映、重複検知 | 2〜3 日 |
| O3 | 信用・逆指値・期日指定・余力 API 連携 | 4〜5 日 |

**全フェーズ共通の不変条件**: [spec.md §6](./spec.md#6-nautilus_trader-互換要件不変条件) の nautilus 互換要件に違反する PR は merge 禁止。各 PR レビューチェックリストに「立花固有用語が HTTP API / IPC / Rust UI 層に漏れていないか」を必ず入れる。

---

## Phase O-pre: nautilus 互換型のスケルトン凍結

**ゴール**: 公開 API 契約と IPC enum を nautilus に揃えた状態で凍結する。実装は空のまま、型だけ先に固める。

### Tpre.1 nautilus_trader 1.211 の型定義を一次資料として参照
- [ ] `nautilus_trader.model.identifiers.{ClientOrderId, VenueOrderId, InstrumentId, TradeId}` のソースを読み、許容文字列・長さ制約をメモ
- [ ] `nautilus_trader.model.enums.{OrderSide, OrderType, TimeInForce, OrderStatus, TriggerType}` の enum 値を全列挙
- [ ] `nautilus_trader.model.orders.{Order, MarketOrder, LimitOrder, ...}` の field 構成と命名を確認
- [ ] **実体ライブラリは**この時点では `pyproject.toml` に**追加しない**（nautilus 統合は N0 まで先送り）。型情報のみ先取り

### Tpre.2 IPC スキーマ確定
**前提**: Q0（nautilus バージョン固定方針）を本タスク着手前に open-questions.md に確定記録すること（Tpre.6 受け入れ条件から前倒し）。
- [ ] [engine-client/src/dto.rs](../../../engine-client/src/dto.rs) に [architecture.md §3](./architecture.md#3-ipc-スキーマ拡張schema-12--13) の `SubmitOrderRequest` / `OrderSide` / `OrderType` / `TimeInForce` / `OrderModifyChange` / `OrderListFilter` / `OrderEvent::*` を追加
- [ ] **enum の `serde rename_all = "SCREAMING_SNAKE_CASE"` を強制**（nautilus 文字列表現と一致）
- [ ] **`Command` enum の `#[derive(Debug)]` をこのタスクで手実装に切り替える**（`SetSecondPassword` 追加前に実施。architecture.md §2.4 参照）。`value` が `[REDACTED]` にマスクされることをテストで検証すること（Tpre.2 の受け入れ条件）
- [ ] [docs/plan/✅python-data-engine/schemas/commands.json](../✅python-data-engine/schemas/commands.json) と `events.json` を更新（schema 1.3）
- [ ] [python/engine/schemas.py](../../../python/engine/schemas.py) に対応 pydantic モデル
- [ ] **ラウンドトリップテスト**: Rust serialize → Python deserialize、Python serialize → Rust deserialize の両方向で全 enum 値を検証（typo を 1 文字でも入れたら CI で落ちること）

### Tpre.3 Python `NautilusOrderEnvelope` 雛形
- [ ] `python/engine/exchanges/tachibana_orders.py` 内に `NautilusOrderEnvelope` (pydantic) を定義
- [ ] field 構成は `nautilus_trader.model.orders.Order` と一致（`client_order_id` / `instrument_id` / `order_side` / `order_type` / `quantity` / `price` / `trigger_price` / `time_in_force` / `expire_time_ns` / `tags` / `init_id` 等）
- [ ] 内部 wire 型 `TachibanaWireRequest` は別 class で切り、`_compose_request_payload(envelope, second_password) -> TachibanaWireRequest` 経由でしか生成しない（写像は 1 箇所集約）
- [ ] `TAGS_REGISTRY` を [architecture.md §10.4](./architecture.md#104-venue-extension-tags-の正規化キー) のキー一覧で初期化

### Tpre.4 Rust `OrderSessionState` の `client_order_id` 主キー化
- [ ] flowsurface [`agent_session_state.rs`](../../../../flowsurface/src/api/agent_session_state.rs) の `ClientOrderId` newtype 規約を踏襲
- [ ] `try_insert(client_order_id, request_key) -> PlaceOrderOutcome` のシグネチャを確定
- [ ] `update_venue_order_id(client_order_id, venue_order_id)` も追加（nautilus は client→venue の写像が必要）

### Tpre.5 EVENT EC フレームの仕様根拠を確保（Q5、Phase O2 ブロッカ解消）

**理由**: Phase O2 着手時に「マニュアル PDF が無く、サンプル frame も無い」状態で詰まるのを防ぐため、O-pre で根拠を確定する。Phase O0/O1 の作業と並行で進めて良いが、O2 着手 **前**に必ず完了させる。

- [ ] **flowsurface に EC パーサが存在するか確認**: `c:/Users/sasai/Documents/flowsurface/exchange/src/adapter/tachibana.rs` で `EC` / `OrderEcEvent` / `_parse_ec_frame` 相当を grep
  - 存在 → 移植元として記録（[architecture.md §6](./architecture.md#6-event-ec-フレームのパースphase-o2) のキー表を flowsurface 実装に基づき更新）
  - 不在 → 次項へ
- [ ] **マニュアル PDF の入手**: `api_event_if_v4r7.pdf` / `api_event_if.xlsx` を立花証券 e支店 サポートサイト or 担当者経由で入手し `.claude/skills/tachibana/manual_files/` に同梱
- [ ] **どちらも不可なら**: デモ環境で 1 件発注 → EVENT WebSocket の生 frame を tcpdump / Python で捕捉 → サンプル frame を `.claude/skills/tachibana/samples/event_ec_capture/` に複数パターン保存（受付・全部約定・部分約定・取消・失効・拒否）
- [ ] 結果を [open-questions.md Q5](./open-questions.md) に追記し、Q5 を「解決」マークする

### Tpre.6 受け入れ条件
- [ ] `cargo check --workspace` 成功
- [ ] Python pytest 既存スイート緑
- [ ] enum ラウンドトリップ網羅テスト緑
- [ ] **N2 シミュレーションテスト**: `nautilus_trader.model.orders.MarketOrder.create(...)` で生成した値の dict を `NautilusOrderEnvelope.model_validate(...)` で読めることを確認するスタブテスト 1 本（nautilus を実 import せず、ハードコードした dict を使う）
  **注**: Q0（nautilus バージョン固定方針）が Case C（CI 互換チェック）を採用しない場合、このテストは nautilus の型変更で陳腐化する。Q0 の決定（推奨: 案 A + C）を本 Phase 着手前に [open-questions.md Q0](./open-questions.md) に記録し確定すること
- [ ] **Q0 決定済み**: nautilus バージョンが pin され、採用する互換チェック方式（CI の有無）が open-questions.md に記録されていること
- [ ] **Tpre.5 EC 仕様根拠の所在が確定**（PDF 入手 / flowsurface 移植元の特定 / 生 frame サンプル いずれか 1 つ）

これにより以降の Phase O0〜O3 は **「型は触らない、実装だけ足す」** モードで進められる。

---

## Phase O0: 第二暗証番号 + 現物成行買い

### T0.1 第二暗証番号: iced modal で取得（Q1 案 D）

- [ ] [data/src/config/tachibana.rs](../../../data/src/config/tachibana.rs) `TachibanaCredentials.second_password` は **`None` 固定のまま**（keyring に書かない）
- [ ] [.claude/skills/tachibana/SKILL.md](../../../.claude/skills/tachibana/SKILL.md) L19・S2 の「Phase 1 では収集しない」記述を「**ログインでは収集しない（Phase O0 でも）/ 発注時に iced modal で取得・メモリのみ保持**」に書き換え
- [ ] [docs/plan/tachibana/architecture.md](../tachibana/architecture.md) §3.1 F-H5 を「**Phase O0 でも解除しない**: 発注時 iced modal 取得方式に変更」と注記

### T0.2 iced modal: 第二暗証番号入力

- [ ] `src/screen/dashboard/modal/second_password.rs`（新設）— Rust iced 側 modal
- [ ] `Event::SecondPasswordRequired { request_id }` 受信で modal 表示 → 入力 → `Command::SetSecondPassword { value }` を送信
- [ ] キャンセル時は `Command::CancelOrder { request_id }` で発注をキャンセル → HTTP 403 + `reason_code="second_password_required"` を返す
- [ ] **tkinter ログインダイアログには第二暗証番号フィールドを追加しない**（追加忌避: 閲覧専用ユーザーを締め出さない / メモリ滞留時間を短くする）

### T0.3 IPC スキーマ 1.3 — 発注最小セット

**注**: enum 列挙体・`SubmitOrderRequest` shape は **Tpre で凍結済み**。本タスクは「Phase O0 で必要な variant のディスパッチを Python 側で有効化する」だけで、Rust DTO 定義は触らない。

- [ ] Python `server.py` で `Command::SubmitOrder` を受信したら `tachibana_orders.submit_order(envelope, second_password)` にルーティング
- [ ] `order_type=MARKET` / `order_side=BUY` / `time_in_force=DAY` / `tags=["cash_margin=cash"]` 以外は **Phase O0 では `OrderRejected{reason_code="UNSUPPORTED_IN_PHASE_O0"}` で reject**
- [ ] `OrderSubmitted` → `OrderAccepted` の 2 段イベントを順番に発火（nautilus 流）。立花応答受領前に `OrderSubmitted`、`sOrderNumber` 採番後に `OrderAccepted`
- [ ] **新規 events**: `Event::SecondPasswordRequired { request_id }`
- [ ] **新規 commands**: `Command::SetSecondPassword { value: String }` / `Command::ForgetSecondPassword`
  （`SecretString` は IPC JSON に送れないため `String`。Python 側で `SecretStr` 化する）
- [ ] **`Command` enum の `Debug` 手実装は Tpre.2 で実施済み**のため、`SetSecondPassword` 追加時はマスクが自動的に適用されることを確認するだけでよい（再実装不要）

### T0.4 Python 側 `tachibana_orders.py` の写像実装

**注**: 公開 class（`NautilusOrderEnvelope` / `SubmitOrderResult`）は Tpre.3 で凍結済み。本タスクは **写像と HTTP 送信の中身**だけ書く。

- [ ] **flowsurface [`exchange/src/adapter/tachibana.rs:1307-1387`](../../../../flowsurface/exchange/src/adapter/tachibana.rs) の `NewOrderRequest` / `NewOrderResponse` を pydantic で wire 専用 class として 1:1 移植**:
  - 命名: `TachibanaWireOrderRequest` / `TachibanaWireOrderResponse`（**`Wire` prefix で「立花固有・公開しない」を明示**）
  - フィールド rename 名（`sZyoutoekiKazeiC` 等）一致
  - `__repr__` で `second_password` をマスク
- [ ] `submit_order(session, second_password, envelope: NautilusOrderEnvelope) -> SubmitOrderResult`:
  - 内部で `_envelope_to_wire(envelope, session, second_password) -> TachibanaWireOrderRequest` を呼ぶ。**写像は [architecture.md §10](./architecture.md#10-nautilus_trader-との型マッピング) の表に従って 1 箇所に集約**
  - 立花未対応の `order_type` / `time_in_force` 組合せは `UnsupportedOrderError` を上に返す（IPC 層で `OrderRejected{reason_code="VENUE_UNSUPPORTED"}` に写る）
- [ ] `_compose_request_payload(wire: TachibanaWireOrderRequest) -> dict`:
  - `p_no` = `tachibana_auth.next_p_no()`（Python asyncio は単一スレッドのため並行安全。`next_p_no()` が `await` を含まない同期カウンタであることを確認すること）
  - `p_sd_date` = `tachibana_auth.current_p_sd_date()`
  - `sCLMID` = `"CLMKabuNewOrder"`
  - `sJsonOfmt` = `"5"`
  - 逆指値関連デフォルト（Phase O0 では固定値）
- [ ] HTTP 送信:
  - `tachibana_url.build_request_url(session.url_request, payload)`
  - `httpx.post(url)` → Shift-JIS デコード → `check_response()`
  - 失敗時 `OrderRejectedError(code, message)` → 上で `Event::OrderRejected` に写る

### T0.5 Rust HTTP API `/api/order/submit`

- [ ] **`Cargo.toml` に `xxhash-rust` を追加**（`xxh3` feature を有効化）。`request_key` の `xxh3_64` で使用（[architecture.md §4.1](./architecture.md#41-request_key-の-canonicalization)）
- [ ] `src/api/order_api.rs` 新設
- [ ] 入力スキーマバリデーション（[spec.md §5](./spec.md#5-入力バリデーションrust-http-層)）
- [ ] `src/api/order_session_state.rs` 新設 — flowsurface [`src/api/agent_session_state.rs`](../../../../flowsurface/src/api/agent_session_state.rs) の `AgentSessionState` を **Rust → Rust で移植**:
  - `ClientOrderId` newtype
  - `try_insert(client_order_id, request_key, new_order_id) -> PlaceOrderOutcome`
  - `Created` / `IdempotentReplay` / `Conflict` の 3 ケース
  - 立花差分: `order_number` を `Option<String>` で持ち、`OrderAccepted` 受信後に `update_order_number()` で埋める
- [ ] `engine_client.send(SubmitOrder)` → `OrderAccepted` / `OrderRejected` を待機
  **タイムアウト**: `tokio::time::timeout(Duration::from_secs(30), ...)` を必ず掛ける。タイムアウト時は HTTP 504 + `reason_code="INTERNAL_ERROR"`（[architecture.md §2.1 タイムアウト節](./architecture.md#21-発注同期)）
- [ ] HTTP 応答: 201 Created（新規）/ 200 OK（idempotent replay）/ 409 / 400 / 403 / 502 / 504（タイムアウト）

### T0.6 安全装置

- [ ] 起動 config に `tachibana.order.max_qty_per_order` / `max_yen_per_order` / `require_confirmation`
- [ ] config 未設定時は `/api/order/submit` を 503 で reject（明示 opt-in、誤発注防止）
- [ ] Python `tachibana_url` で本番 URL 検出時、`os.getenv("TACHIBANA_ALLOW_PROD") != "1"` なら send をブロック

### T0.7 監査ログ WAL + 起動時復元（重複発注防止）

- [ ] `python/engine/exchanges/tachibana_orders.py` に `_audit_log_submit(payload)` / `_audit_log_accepted(...)` / `_audit_log_rejected(...)` を追加（[architecture.md §4.2](./architecture.md#42-監査ログwal-write-ahead-log)）
- [ ] `data_path()/tachibana_orders.jsonl` に append:
  - `submit` 行は HTTP 送信 **直前**に `fsync` 込み（クラッシュ時の不整合最小化）
    Python 実装: `f.write(json_line + "\n"); f.flush(); os.fsync(f.fileno())`
    async context では `loop.run_in_executor(None, os.fsync, f.fileno())` を使うこと（ブロッキング防止）
  - `accepted` / `rejected` 行は応答受領後（`f.write + flush` で十分、fsync 不要）
    - `accepted` が OS バッファ残りのままクラッシュした場合、起動時復元は `unknown` 状態になるが Phase O1 の `GetOrderList` で補完できる。この許容を意図的な設計として実装者にコメントで残すこと
  - **第二暗証番号は絶対に書かない**（unit テストで grep 検証）
- [ ] **Rust 側 `OrderSessionState` の起動時復元**: アプリ起動 → 当日分 WAL を読み戻し → `client_order_id ↔ request_key ↔ venue_order_id` の map を復元
  - `submit` のみで `accepted`/`rejected` 無し → `unknown` 状態で復元（Phase O1 T1.5 で `GetOrderList` から確定）
  - 同一 `client_order_id` で再送 → `IdempotentReplay` を返す
- [ ] **`request_key` の canonicalization** を [architecture.md §4.1](./architecture.md#41-request_key-の-canonicalization) の規則どおりに実装。テストで pin（`tags` 順序入替・null vs 空文字 で同一 key になることを確認）

### T0.8 テスト

- [ ] Python pytest-httpx で **flowsurface テスト群を移植**（入力は `NautilusOrderEnvelope` 経由に置換）:
  - `submit_order_returns_error_on_wrong_password_response` ([flowsurface tachibana.rs:4168](../../../../flowsurface/exchange/src/adapter/tachibana.rs#L4168)）
  - `submit_order_returns_error_on_market_closed_response` (同 4215)
  - `submit_order_returns_error_on_invalid_issue_code_response` (同 4256)
- [ ] Python: `_envelope_to_wire` の写像テーブルテスト — [architecture.md §10.1〜§10.4](./architecture.md#101-ordertype-写像) の各行に 1 ケースずつ
- [ ] Python: `_compose_request_payload` のフィールド存在 / `sCLMID` / `sJsonOfmt` / 逆指値デフォルト
- [ ] **nautilus 互換性テスト**: nautilus を import しない状態で、`nautilus_trader.model.orders.MarketOrder.create(...)` 互換の dict を `NautilusOrderEnvelope.model_validate(...)` で読み込み可能（field 名・enum 文字列一致を検証）
- [ ] Rust: `OrderSessionState` の `Created/IdempotentReplay/Conflict` 3 ケース（flowsurface 同名テストの移植）
- [ ] Rust: `/api/order/submit` のスキーマバリデーション（不正 client_order_id、quantity=0、instrument_id 形式違反）
- [ ] **Python: `TACHIBANA_ALLOW_PROD=1` ガードのテスト**: `os.getenv("TACHIBANA_ALLOW_PROD") != "1"` のとき本番 URL への送信がブロックされることを pytest で検証（`monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)` を使う）
- [ ] **手動 E2E**（CI 載せず、デモ環境クレデンシャル必須）: `s80_order_submit_demo.sh` で「現物・成行・買 100 株」が通る
- [ ] **クラッシュリカバリ E2E**（`s80_order_crash_recovery_demo.sh`）:
  1. `POST /api/order/submit` を送信して WAL に `submit` 行が書かれた直後にプロセスを kill
  2. 再起動 → 同一 `client_order_id` で再送
  3. `IdempotentReplay`（HTTP 202 + `warning: order_status_unknown`）が返ること
  4. デモ注文一覧で重複発注が起きていないことを確認

**Exit 条件**: デモ環境で curl `/api/order/submit` → `sOrderNumber` が返る。監査ログに第二暗証番号が出ていないことを確認。クラッシュリカバリ E2E が手動で通ること。

---

## Phase O1: 訂正（modify）・取消・一覧

### T1.1 Python modify・取消・一覧
- [ ] `tachibana_orders.submit_modify_order` / `submit_cancel_order` / `submit_cancel_all` / `fetch_order_list`
  - 関数名は **nautilus 用語の `modify`** で統一。内部で立花 `CLMKabuCorrectOrder` を呼ぶ
- [ ] flowsurface の `CorrectOrderRequest` / `CancelOrderRequest` / `OrderListRequest` を pydantic で移植（型名は `ModifyOrderRequest` 等にリネーム）
- [ ] レスポンス型 `ModifyOrderResponse` / `OrderListResponse` 移植

### T1.2 IPC 拡張
- [ ] `Command::ModifyOrder` / `CancelOrder` / `CancelAllOrders` / `GetOrderList`
- [ ] `Event::OrderListUpdated` / `OrderPendingUpdate` / `OrderPendingCancel`

### T1.3 Rust HTTP
- [ ] `/api/order/modify` `/api/order/cancel` `/api/order/cancel-all` `/api/order/list`
- [ ] `cancel-all` は確認モーダル必須（HTTP 層では **JSON body に `confirm: true` を必須**とする。query param ではない。[spec.md §4](./spec.md#4-公開-apihttp) に準拠）
- [ ] `/api/order/cancel` の Rust 実装では `OrderSessionState.get_venue_order_id(client_order_id)` で lookup し、`venue_order_id` を Python `cancel_order(...)` に渡すこと（[architecture.md §2.3](./architecture.md#23-取消フローphase-o1)）。`venue_order_id = None`（unknown）は 404 reject

### T1.4 UI: 注文一覧パネル
- [ ] `src/screen/dashboard/panel/orders.rs`（新設）
- [ ] 当日注文を表示・選択 → 訂正 / 取消ボタン
- [ ] 確認モーダル（成行発注時・取消時）
- [ ] **発注フォーム・確認モーダル・訂正/取消モーダルはすべて iced 側**で実装（Q3 暫定確定: Q1 で第二暗証番号入力も iced modal にした流れに合わせる。tkinter はログイン専用）

### T1.5 起動時の台帳復元
- [ ] **T0.7（監査ログ WAL）から `client_order_id ↔ sOrderNumber` を起動時に復元**（重複発注防止の本丸）
- [ ] それでも欠損するもの（他端末から発注された当日注文等）は `VenueReady` 後の `GetOrderList` で `venue_order_id` ベースの別 map に入れる
- [ ] `client_order_id` 不明の注文は HTTP `/api/order/modify` `/api/order/cancel` 入力として **`venue_order_id` も受理**できるようにする（spec.md §5 に記載）。`client_order_id` と `venue_order_id` が同時指定された場合は `client_order_id` 優先。応答の `client_order_id` は `null` を返す

### T1.6 テスト
- [ ] Python: modify・取消の正常系・session 切れ
- [ ] Rust: `/api/order/cancel-all` の `confirm` 必須チェック（Phase O0 時点では 501 を返すことのテストも含める）
- [ ] Rust: 起動時 WAL 復元 → 同一 `client_order_id` で再送 → `IdempotentReplay` を返すこと
- [ ] 手動 E2E: `s81_order_modify_cancel_demo.sh`

**Exit 条件**: デモ環境で「指値発注 → 訂正 → 取消」が UI から完結。

---

## Phase O2: EVENT EC 約定通知

### T2.1 EC パーサ
- [ ] `tachibana_event.py._parse_ec_frame(items) -> OrderEcEvent`
- [ ] 主要項目（[architecture.md §6](./architecture.md#6-event-ec-フレームのパースphase-o2)）の写像
- [ ] **マニュアル現物確認**: `api_event_if_v4r7.pdf` または `api_event_if.xlsx` から EC フィールド一覧を抽出（[docs/plan/tachibana/inventory-T0.md](../tachibana/inventory-T0.md) §11 と同じ理由で、PDF 同梱がなければ実 frame キャプチャに切替）

### T2.2 IPC イベント拡張
- [ ] `Event::OrderFilled` / `OrderCanceled` / `OrderExpired`（既に O0 で骨格があれば拡張）
- [ ] **`OrderPartiallyFilled` は持たない**: nautilus 流に `OrderFilled` の `leaves_qty` で部分/全部を判定する。詳細は [architecture.md §3](./architecture.md#3-ipc-スキーマ拡張schema-12--13) 末尾

### T2.3 重複検知
- [ ] `tachibana_event.py` に `_seen_eda_no: set[tuple[str, str]]` を持つ
- [ ] 当日リセット（夜間閉局検知時）

### T2.4 Rust UI 反映
- [ ] notification toast（既存通知機構を使う、なければ簡易バナー）
- [ ] 注文一覧パネルの行更新

### T2.5 テスト
- [ ] Python: 実 frame サンプル（または合成）でパース → 期待 IPC イベント
- [ ] Python: `_seen_eda_no` の重複検知
- [ ] 手動 E2E: 発注 → 約定 toast を目視

**Exit 条件**: デモ環境で発注 → 約定通知が UI に出る。再接続時の再送が UI を二重表示させないことを確認。

---

## Phase O3: 信用・逆指値・余力

### T3.1 NewOrderRequest 拡張
- [ ] `cash_margin = 2/4/6/8`（信用新規・返済の制度・一般）
- [ ] `gyakusasi_order_type` / `gyakusasi_zyouken` / `gyakusasi_price`
- [ ] `expire_day = YYYYMMDD`
- [ ] 信用返済の建玉個別指定（`tatebi_type=1` + `aCLMKabuHensaiData`）

### T3.2 余力・建玉 API
- [ ] `tachibana_orders.fetch_buying_power` (`CLMZanKaiKanougaku`)
- [ ] `tachibana_orders.fetch_credit_buying_power` (`CLMZanShinkiKanoIjiritu`)
- [ ] `tachibana_orders.fetch_sellable_qty` (`CLMZanUriKanousuu`)
- [ ] `tachibana_orders.fetch_positions` (`CLMGenbutuKabuList` / `CLMShinyouTategyokuList`)

### T3.3 発注前ガード
- [ ] 余力不足時は **Rust HTTP 層**で 403 + 説明文（spec.md §3.2 の精神に合わせ、ユーザーが理由を即わかる）

### T3.4 UI
- [ ] 発注フォームに 「信用 / 現物」「逆指値」「期日」セレクタ追加
- [ ] 余力表示パネル

### T3.5 テスト
- [ ] flowsurface の信用関連テスト（[`tachibana.rs:4014-4350`](../../../../flowsurface/exchange/src/adapter/tachibana.rs#L4014)）を Python に移植
- [ ] 余力ガードの単体テスト

**Exit 条件**: 信用新規買い・逆指値・期日指定がデモで完結。余力不足が UI で正しく表示。

---

## 横断タスク

- [ ] `.claude/skills/tachibana/SKILL.md` の Phase 1 制約記述を Phase O0 解禁時に更新（T0.1 内）
- [ ] [docs/plan/tachibana/spec.md](../tachibana/spec.md) §2.2「発注は Phase 2+」記述を「[docs/plan/order/](.) で管理」に書き換え
- [ ] [docs/plan/README.md](../README.md) の Phase ロードマップに Order Phase O0–O3 を追記
- [ ] [docs/plan/nautilus_trader/spec.md](../nautilus_trader/spec.md) §2.3 Phase N2 に「`tachibana_orders.py` を `LiveExecutionClient` 内で再利用」を明記（変更不要、既に方針一致）

## nautilus N2 移行時に行う作業（参考・本計画スコープ外）

[architecture.md §10.6](./architecture.md#106-nautilus-移行時の差分n2-で実施する作業のみ) の通り、本計画完了時点で型互換が完全に取れていれば、N2 で行うのは:

1. `pyproject.toml` に `nautilus_trader` を追加
2. `python/engine/nautilus/clients/tachibana.py` を新設（`LiveExecutionClient` 継承）し中身は `tachibana_orders.submit_order(...)` を呼ぶだけ
3. `_envelope_to_wire` を `NautilusOrderEnvelope` の代わりに本物の `nautilus_trader.model.orders.Order` を受けるよう型注釈だけ書き換え（field アクセス互換のため動作変更なし）

**本計画のコードは削除しない**。HTTP API `/api/order/*` も nautilus 経路と並行して残す（手動発注・curl 経路の維持）。
