# 立花注文機能: 実装計画

**前提条件（着手ブロッカー）**: 立花 Phase 1（[docs/plan/✅tachibana/implementation-plan.md](../✅tachibana/implementation-plan.md)）の T2（認証実装）以降が完了していること。ログイン経路は `tachibana_login_flow.py` + `tachibana_auth.py` で構成される（`tachibana_login.py` は存在しない）。

**現状確認（2026-04-25）**: `python/engine/exchanges/` に既存の tachibana 系ファイル:

- `tachibana_auth.py` — 認証・セッション管理（`PNoCounter` を含む）
- `tachibana_codec.py` — Shift-JIS / JSON エンコード
- `tachibana_helpers.py` — 共通ヘルパ（`current_p_sd_date()` 等）
- `tachibana_login_dialog.py` — tkinter ログインダイアログ
- `tachibana_login_flow.py` — ログインフロー本体
- `tachibana_master.py` — マスタデータ
- `tachibana_url.py` — 仮想 URL 管理

**未実装（本計画で新規作成）**:

- `tachibana_event.py` — EVENT WebSocket 受信ループ + EC パーサ。Phase O2 の Tpre.5 / T2.1 で新規作成する（FD 受信＋EC 受信の合流責務を持つ）。Phase 1（[docs/plan/✅tachibana/implementation-plan.md](../✅tachibana/implementation-plan.md)）には EVENT 受信ループは含まれていないため、本計画で初めて導入する

O-pre の Tpre タスクは Phase 1 の認証基盤が無くても型定義だけ進められるが、T0.3 以降は Phase 1 完了が必要。

## マイルストーン一覧

Rust UI トラック（[rust-ui-plan.md](./rust-ui-plan.md)）を Python トラックと並行実施する。

| Python Phase | ゴール | 並行 Rust UI Phase | 期間目安 |
|---|---|---|---|
| **O-pre** | nautilus 互換型のスケルトン凍結 + EVENT EC 仕様の根拠確保（実装ゼロ・型と一次資料のみ） | **U-pre**（並行）: パネルシェル・サイドバー 🖊 ボタン・フォーム UI 構造を先行実装。IPC 依存なし | 1〜2 日 |
| O0 | 現物・成行・買のみ単発発注がデモ環境で通る + WAL idempotent replay | **U0**（Tpre.2 完了直後）: Order Entry IPC 配線。完了後すぐ UI で O0 手動テスト可 | 3〜4 日 |
| O1 | 訂正・取消・注文一覧 | **U1**: Order List パネル + 訂正取消 UI | 3〜4 日 |
| O2 | EVENT EC 約定通知の購読と UI 反映、重複検知 | **U2**: Toast 通知 + リアルタイム更新 | 2〜3 日 |
| O3 | 信用・逆指値・期日指定・余力 API 連携 | **U3**: Buying Power パネル + フォーム拡張 | 4〜5 日 |

**全フェーズ共通の不変条件**: [spec.md §6](./spec.md#6-nautilus_trader-互換要件不変条件) の nautilus 互換要件に違反する PR は merge 禁止。各 PR レビューチェックリストに「立花固有用語が HTTP API / IPC / Rust UI 層に漏れていないか」を必ず入れる。

---

## Phase O-pre: nautilus 互換型のスケルトン凍結

**ゴール**: 公開 API 契約と IPC enum を nautilus に揃えた状態で凍結する。実装は空のまま、型だけ先に固める。

### Tpre.1 nautilus_trader 1.211 の型定義を一次資料として参照
- [x] ✅ `nautilus_trader.model.identifiers.{ClientOrderId, VenueOrderId, InstrumentId, TradeId}` のソースを読み、許容文字列・長さ制約をメモ
  - `ClientOrderId`: 長さ 1〜36、ASCII printable のみ（spec.md §5 注釈に記載済み）
- [x] ✅ `nautilus_trader.model.enums.{OrderSide, OrderType, TimeInForce, OrderStatus, TriggerType}` の enum 値を全列挙
  - IPC enum として Rust `dto.rs` に凍結済み（SCREAMING_SNAKE_CASE）
- [x] ✅ `nautilus_trader.model.orders.{Order, MarketOrder, LimitOrder, ...}` の field 構成と命名を確認
  - `NautilusOrderEnvelope` の field 構成として `tachibana_orders.py` に実装済み
- [x] ✅ **実体ライブラリは** `pyproject.toml` に**追加しない**（nautilus 統合は N0 まで先送り）
- **Tips**: Q0 を `案 A + C` で確定（open-questions.md に記録済み）。型はハードコード + dict テストで CI 保証

### Tpre.2 IPC スキーマ確定 ✅（2026-04-26 完了）
**前提**: Q0 確定済み（open-questions.md Q0 記録済み）
- [x] ✅ `engine-client/src/dto.rs` に `SubmitOrderRequest` / `OrderSide` / `OrderType` / `TimeInForce` / `OrderModifyChange` / `OrderListFilter` / 全 `OrderEvent::*` を追加
  - ファイル: `engine-client/src/dto.rs`
- [x] ✅ **enum の `serde rename_all = "SCREAMING_SNAKE_CASE"` を強制**（テスト: `schema_v1_3_roundtrip.rs` で pin）
- [x] ✅ **新規 enum variant を凍結**: `Command::SetSecondPassword` / `Command::ForgetSecondPassword` / `Event::SecondPasswordRequired`
- [x] ✅ **`Command` enum の `#[derive(Debug)]` を手実装に切り替え**。`value` が `[REDACTED]` にマスクされることをテスト検証
  - テスト: `engine-client/tests/command_debug_redaction.rs`（5 テスト全緑）
- [x] ✅ [docs/plan/✅python-data-engine/schemas/commands.json](../✅python-data-engine/schemas/commands.json) と `events.json` を更新（schema 1.3+）
  - 全発注コマンド（SetSecondPassword / ForgetSecondPassword / SubmitOrder / ModifyOrder / CancelOrder / CancelAllOrders / GetOrderList）と全発注イベント（SecondPasswordRequired / OrderSubmitted / OrderAccepted / OrderRejected / OrderPendingUpdate / OrderPendingCancel / OrderFilled / OrderCanceled / OrderExpired / OrderListUpdated）を追加
- [x] ✅ `python/engine/schemas.py` に対応 pydantic モデルを追加（`SCHEMA_MINOR=3`）
- [x] ✅ **ラウンドトリップテスト**: Rust serialize → JSON で shape 確認（`schema_v1_3_roundtrip.rs`）、Python pydantic serialize 確認（`test_order_schema_v1_3.py`）
- [x] ✅ **第二暗証番号 IPC 漏洩防止テスト（D2-M5）**: `cargo test --test creds_no_second_password_on_wire` — `Command::SubmitOrder` JSON に `second_password` が含まれないこと・`deny_unknown_fields` による注入拒否を 3 テストで検証
- [x] ✅ **DTO `deny_unknown_fields` テスト（D3-1）**: `engine-client/tests/dto_deny_unknown_fields.rs`（7 テスト全緑）

### Tpre.3 Python `NautilusOrderEnvelope` 雛形 ✅（2026-04-26 完了）
- [x] ✅ `python/engine/exchanges/tachibana_orders.py` 内に `NautilusOrderEnvelope` (pydantic) を定義
  - テスト: `python/tests/test_nautilus_order_envelope.py`（5 テスト全緑）
- [x] ✅ field 構成は nautilus `Order` と一致（`client_order_id` / `instrument_id` / 全 field）
- [x] ✅ 内部 wire 型 `TachibanaWireOrderRequest` を別 class として定義（写像は `_envelope_to_wire()` に集約）
- [x] ✅ `TAGS_REGISTRY` を architecture.md §10.4 のキー一覧で初期化
- **Tips**: `_envelope_to_wire()` / `submit_order()` 等は Phase O0 T0.4 で実装する `NotImplementedError` stub として置いた

### Tpre.4 Rust `OrderSessionState` の `client_order_id` 主キー化 ✅（2026-04-26 完了）
- [x] ✅ `engine-client/src/order_session_state.rs` を新設。`ClientOrderId` newtype 規約を踏襲
- [x] ✅ `try_insert(client_order_id, request_key) -> PlaceOrderOutcome` のシグネチャを確定
- [x] ✅ `update_venue_order_id(client_order_id, venue_order_id)` を追加
  - テスト: `engine-client/tests/order_session_state.rs`（7 テスト全緑）

### Tpre.5 EVENT EC フレームの仕様根拠を確保（Q5、Phase O2 ブロッカ解消）+ `tachibana_event.py` 新規作成

**理由**: Phase O2 着手時に「マニュアル PDF が無く、サンプル frame も無い」状態で詰まるのを防ぐため、O-pre で根拠を確定する。Phase O0/O1 の作業と並行で進めて良いが、O2 着手 **前**に必ず完了させる。

**新規作成**: `python/engine/exchanges/tachibana_event.py` をここで新規作成する（EVENT WebSocket 受信ループ含む。FD 受信＋EC 受信の合流責務）。Phase 1 計画 ([docs/plan/✅tachibana/implementation-plan.md](../✅tachibana/implementation-plan.md)) には EVENT 受信ループは含まれていないため、本計画で初めて導入する依存関係に注意。

- [ ] **flowsurface に EC パーサが存在するか確認**: `c:/Users/sasai/Documents/flowsurface/exchange/src/adapter/tachibana.rs` で `EC` / `OrderEcEvent` / `_parse_ec_frame` 相当を grep
  - 存在 → 移植元として記録（[architecture.md §6](./architecture.md#6-event-ec-フレームのパースphase-o2) のキー表を flowsurface 実装に基づき更新）
  - 不在 → 次項へ
- [x] ✅ **EC フレーム仕様は samples に実装済みで確認不要**: `.claude/skills/tachibana/samples/e_api_event_receive_tel.py`（行 534–568）および `e_api_websocket_receive_tel.py` に EC フレーム仕様（`^A`/`^B`/`^C` デリミタ、p_evt_cmd 値一覧、EC=注文約定通知）が Python コメントで完全に記載されている。`api_event_if_v4r7.pdf` / `api_event_if.xlsx` の入手は不要。
- [ ] **最終手段（実 frame キャプチャ）は Phase 1 T2 完了後でないと不可**（EVENT WebSocket 接続には認証セッションが必要なため）。それ以前の段階では「PDF 入手」または「flowsurface 移植元の特定」のいずれかに限定する。Phase 1 T2 が完了している場合に限り、デモ環境で 1 件発注 → EVENT WebSocket の生 frame を tcpdump / Python で捕捉 → サンプル frame を `.claude/skills/tachibana/samples/event_ec_capture/` に複数パターン保存（受付・全部約定・部分約定・取消・失効・拒否）
- [ ] 結果を [open-questions.md Q5](./open-questions.md) に追記し、Q5 を「解決」マークする

### Tpre.6 受け入れ条件
- [x] ✅ `cargo check --workspace` 成功（全エラー 0）
- [x] ✅ Python pytest 既存スイート緑（schema 変更起因のリグレッション 0）
- [x] ✅ enum ラウンドトリップ網羅テスト緑（`schema_v1_3_roundtrip.rs` + `test_order_schema_v1_3.py`）
- [x] ✅ **N2 シミュレーションテスト**: `python/tests/test_nautilus_order_envelope.py::test_nautilus_market_order_dict_validates` — ハードコード dict で `NautilusOrderEnvelope.model_validate(...)` 成功確認
- [x] ✅ **Q0 決定済み**: `open-questions.md` Q0 に「案 A + C / nautilus 1.211.x pin」を記録
- [x] ✅ **Tpre.5 EC 仕様根拠の所在が確定** — `.claude/skills/tachibana/samples/e_api_event_receive_tel.py` に仕様コメント実装済みを確認（2026-04-28）
- [x] ✅ **不変条件マッピング表の整備（D2-M1）**: `docs/plan/✅order/invariant-tests.md` を作成・骨格記入済み

これにより以降の Phase O0〜O3 は **「型は触らない、実装だけ足す」** モードで進められる。

---

## Phase O0: 第二暗証番号 + 現物成行買い

### T0.1 第二暗証番号: iced modal で取得（Q1 案 D）

- [x] ✅ ~~[data/src/config/tachibana.rs](../../../data/src/config/tachibana.rs) `TachibanaCredentials.second_password` は `None` 固定のまま~~（**N/A**: architecture.md §5 により `data/src/config/tachibana.rs` は削除済み。Python 自律管理方式への移行で Rust 側の creds/session 保持コードを全廃）
- [x] ✅ [.claude/skills/tachibana/SKILL.md](../../../.claude/skills/tachibana/SKILL.md) S2 の `DEV_TACHIBANA_SECOND_PASSWORD` コメントを「**ログインでは収集しない（Phase O0 以降も）/ 発注時に iced modal で取得・メモリのみ保持**」に書き換え
- [x] ✅ [docs/plan/✅tachibana/architecture.md](../✅tachibana/architecture.md) §7.7 F-H5 を「**Phase O0 でも解除しない**: 発注時 iced modal 取得方式に変更」と注記

### T0.2 iced modal: 第二暗証番号入力 ✅（2026-04-28 完了）

- [x] ✅ `src/modal/second_password.rs`（実装済み、`src/screen/dashboard/modal/second_password.rs` ではなく `src/modal/` 配下に着地）— Rust iced 側 modal
- [x] ✅ `Event::SecondPasswordRequired { request_id }` 受信で modal 表示 → 入力 → `Command::SetSecondPassword { value }` を送信（`src/main.rs` で配線済み）
- [x] ✅ modal キャンセル時は **未送信のまま HTTP 側で reject**
- [x] ✅ **tkinter ログインダイアログには第二暗証番号フィールドを追加しない**

### T0.3 IPC スキーマ 1.3 — 発注最小セット

**注**: enum 列挙体・`SubmitOrderRequest` shape・新規 variant（`Command::SetSecondPassword` / `Command::ForgetSecondPassword` / `Event::SecondPasswordRequired`）は **Tpre.2 で凍結済み**。本タスクは「Tpre.2 で凍結済み variant の **ディスパッチ実装**」だけで、Rust DTO 定義・新規 variant 追加は一切行わない。

- [x] ✅ Python `server.py` で `Command::SubmitOrder` を受信したら `tachibana_orders.submit_order(envelope, second_password)` にルーティング
- [x] ✅ `order_type=MARKET` / `order_side=BUY` / `time_in_force=DAY` / `tags=["cash_margin=cash"]` / `trigger_type=null` / `post_only=false` / `reduce_only=false` 以外は **Phase O0 では `OrderRejected{reason_code="UNSUPPORTED_IN_PHASE_O0"}` で reject**（`TriggerType` は Phase O0/O1 で null 必須、O3 まで `LAST` 固定での実装は禁止）
- [x] ✅ dispatch 層で `trigger_type != null` を `UNSUPPORTED_IN_PHASE_O0` で reject（観測点: `test_unsupported_phase_o0.py` 条件 (e)）
- [x] ✅ `OrderSubmitted` → `OrderAccepted` の 2 段イベントを順番に発火（nautilus 流）。立花応答受領前に `OrderSubmitted`、`sOrderNumber` 採番後に `OrderAccepted`
- [x] ✅ **`Command::SetSecondPassword` / `Command::ForgetSecondPassword` のディスパッチを Python 側で有効化**（enum 定義は Tpre.2 で凍結済み）。`String` で受信し、Python 側でメモリのみ保持（`_second_password: str | None`）
- [x] ✅ **`Event::SecondPasswordRequired { request_id }` の発火経路実装**（enum 定義は Tpre.2 で凍結済み）
- [x] ✅ **`Command` enum の `Debug` 手実装は Tpre.2 で実施済み**のため、ディスパッチ実装時はマスクが自動的に適用されることを確認するだけでよい（再実装不要）

**受け入れテスト追加**:

- [x] ✅ **第二暗証番号 idle forget テスト（D2-M3）**: `python/tests/test_tachibana_session_holder.py` に idle forget / lockout / on_submit_success / on_invalid / 組み合わせテスト 17 件実装済み（ファイル名は計画と異なるが受け入れ条件をカバー）。`ForgetSecondPassword` 受信 → `_second_password` が None になること（`python/tests/test_order_dispatch.py` 参照）
- [x] ✅ **`UNSUPPORTED_IN_PHASE_O0` 境界テスト（C1 / D3-2）**: `uv run pytest python/tests/test_unsupported_phase_o0.py -v` — `pytest.mark.parametrize` で 7 条件 × 各 2〜4 値をカバー（24 テスト全緑）

**Notes**:
- `TachibanaCredentialsWire.second_password` は Phase 1 で `assert!(... is_none())` 強制済み（`From<&TachibanaCredentials> for TachibanaCredentialsWire` impl 内、参照: [`data/src/config/tachibana.rs`](../../../data/src/config/tachibana.rs)）。Order Phase で `Command::SetSecondPassword` 導入と同時に Phase 1 側の `#[deprecated]` 化を [docs/plan/✅tachibana/implementation-plan.md](../✅tachibana/implementation-plan.md) 側にも書き戻すこと（双方向リンク）

### T0.4 Python 側 `tachibana_orders.py` の写像実装

**注**: 公開 class（`NautilusOrderEnvelope` / `SubmitOrderResult`）は Tpre.3 で凍結済み。本タスクは **写像と HTTP 送信の中身**だけ書く。

- [x] ✅ **flowsurface [`exchange/src/adapter/tachibana.rs:1307-1387`](../../../../flowsurface/exchange/src/adapter/tachibana.rs) の `NewOrderRequest` / `NewOrderResponse` を pydantic で wire 専用 class として 1:1 移植**:
  - 命名: `TachibanaWireOrderRequest` / `TachibanaWireOrderResponse`（**`Wire` prefix で「立花固有・公開しない」を明示**）
  - フィールド rename 名（`sZyoutoekiKazeiC` 等）一致
  - `__repr__` で `second_password` をマスク
  - テスト: `test_tachibana_order_mapping.py`（13 テスト全緑）
- [x] ✅ `submit_order(session, second_password, envelope: NautilusOrderEnvelope) -> SubmitOrderResult`:
  - 内部で `_envelope_to_wire(envelope, session, second_password) -> TachibanaWireOrderRequest` を呼ぶ。写像は architecture.md §10.1〜§10.4 に集約
  - 立花未対応の `order_type` / `time_in_force` 組合せは `UnsupportedOrderError` を上に返す
  - テスト: `test_tachibana_submit_order.py`（4 テスト全緑）
- [x] ✅ `_compose_request_payload(wire: TachibanaWireOrderRequest, p_no_counter) -> dict`:
  - `p_no` = `p_no_counter.next()`, `p_sd_date` = `current_p_sd_date()`, `sCLMID` = `"CLMKabuNewOrder"`, `sJsonOfmt` = `"5"`
  - 逆指値関連デフォルト（Phase O0 は固定値）
  - テスト: `test_tachibana_compose_payload.py`（8 テスト全緑）
- [x] ✅ HTTP 送信: `build_request_url(session.url_request, payload)` → `httpx.AsyncClient.get(url)` → Shift-JIS デコード → `check_response()`
- [x] ✅ **第二暗証番号 idle forget タイマー（C-R2-H2 / B3R3-1）**: `python/engine/exchanges/tachibana_auth.py` に `TachibanaSessionHolder` クラスを実装済み。monotonic clock（`asyncio.get_running_loop().time()` / `time.monotonic()` フォールバック）で idle 判定。reset trigger は `SetSecondPassword` 受信時のみ（`touch()` は発注時）。テスト: `python/tests/test_tachibana_session_holder.py`（17 テスト全緑）
- [x] ✅ **第二暗証番号 lockout state（HIGH-R6-B1）**: `TachibanaSessionHolder` に `on_invalid()` / `is_locked_out()` / `on_submit_success()` 実装済み。`p_errno=4` → `SecondPasswordInvalidError`（`tachibana_helpers.py` に追加）→ `on_invalid()` 呼び出しで counter += 1 → 閾値到達で lockout。`SubmitOrder` 成功時に `on_submit_success()` で counter リセット
- [x] ✅ **lockout 中の発注 reject（HIGH-R6-B1）**: `_do_submit_order` / `_do_modify_order` / `_do_cancel_order` / `_do_cancel_all_orders` で `is_locked_out()` チェック → `OrderRejected{reason_code="SECOND_PASSWORD_LOCKED"}` / `Error{code="SECOND_PASSWORD_LOCKED"}` を emit
- [x] ✅ **WAL 復元 truncated 行スキップ（HIGH-R6-B1）**: WAL 復元（Python `read_wal_records`）で末尾行に `\n` が無ければ truncated とみなしスキップ + WARN ログ（T0.7 で実装済み: `python/engine/exchanges/tachibana_orders.py::read_wal_records`）
- [x] ✅ ~~**Phase 1 second_password ガード解除タスク（B3R3-4）**~~（**N/A**: `data/src/config/tachibana.rs` は architecture.md §5 により削除済み。`TachibanaCredentials` / `TachibanaCredentialsWire` とも廃止済みのため本タスク全体が不要）

- [x] ✅ ~~**`with_second_password` ポジティブテスト（D4-3）**~~（**N/A**: B3R3-4 と同じ理由で不要。`data/src/config/tachibana.rs` 削除済み）

- ✅ **`Submitted → Rejected` 即時遷移テスト（D3-3）**: `uv run pytest python/tests/test_submitted_to_rejected_immediate.py` — `p_errno=2` モック応答 → `Event::OrderSubmitted` → `Event::OrderRejected{reason_code="SESSION_EXPIRED"}` の順で **2 イベントが発火し `OrderAccepted` を経由しない**ことを assert。`_map_tachibana_state_to_nautilus()` の単体テストも併設すること

**受け入れテスト追加**:

- [x] ✅ **`VENUE_UNSUPPORTED` 写像テスト（D4-2）**: `uv run pytest python/tests/test_venue_unsupported_mapping.py` — Python `submit_order` が `UnsupportedOrderError` を raise したケースで IPC 層が `Event::OrderRejected{reason_code="VENUE_UNSUPPORTED"}` に写ることを assert。`UNSUPPORTED_IN_PHASE_O0` とは別経路であることを明示

- ✅ **第二暗証番号 lockout テスト（HIGH-R6-D1）**: `uv run pytest python/tests/test_second_password_lockout.py` — SECOND_PASSWORD_INVALID 3 連投 → 4 回目が HTTP 423 + `reason_code="SECOND_PASSWORD_LOCKED"` で reject、1800 秒経過 (freezegun.freeze_time で +1800 秒進める) で解除されることを assert

- [x] ✅ **第二暗証番号マスク横断 grep テスト**: `repr / str / model_dump_json() / model_dump()` は `test_tachibana_order_mapping.py` に実装済み。`logging.getLogger().info(obj)` テストを追加済み（`test_tachibana_order_mapping.py::test_second_password_not_in_log_output`）
- [x] ✅ **session 切れ即停止テスト（Rust）**: `test_submit_after_session_frozen_returns_503` — session.freeze() 後の `submit_order` が 503 SESSION_EXPIRED を返す（`src/api/order_api.rs` 内 `#[cfg(test)]`）。Python dispatch 側は `test_submitted_to_rejected_immediate.py` / `test_cancel_all_exception_propagation.py` でカバー済み
- [ ] **Shift-JIS 受け入れテスト**: Shift-JIS 応答（ひらがな含むエラー文）→ `OrderRejectedError.message` が UTF-8 で正しく載ることを `pytest-httpx` で検証（デモ環境実 frame 必要なためスキップ）
- [ ] **仮想 URL マスクテスト**: `caplog` で `submit_order` 実行中のログを採取し `p_no=` 文字列が出ていないことを assert（httpx モック依存度高・手動確認で代替）
- ✅ **URL masker 単体テスト（D2-L1）**: `uv run pytest python/tests/test_url_masker.py` — マスクヘルパ単体をパラメタライズで検証
- [x] ✅ **`PNoCounter.peek()` 非使用 CI grep（B2-L2）**: `python/tests/test_p_no_counter_peek_guard.py` — tachibana_*.py ソース内 `.peek()` 呼び出しを静的 grep で検証
- [x] ✅ **`p_no` 単調増加 property test（D2-M4）**: `python/tests/test_p_no_counter_monotonic.py` — `PNoCounter.next()` の単調増加を hypothesis property test + 再起動シミュレーションで検証
- [x] ✅ **`expire_time_ns` 変換テスト（C5）**: `python/tests/test_expire_time_ns_conversion.py` — `_expire_ns_to_jst_yyyymmdd()` の UTC→JST YYYYMMDD 変換を複数パターンで検証。注: CLMDateZyouhou マスタガード（Phase O3 では Phase O4 送り）は Phase O3 の `Note` コメントに記載済み

### レビュー反映 (2026-04-26, ラウンド 1)

**解消した指摘**:
- C-1: _do_submit_order 例外時 OrderRejected 未発火 → try/except 追加、SessionExpiredError 時 second_password クリア (M-14 同時解消)
- C-3: TachibanaWireOrderRequest model_dump/str で second_password 平文 → @field_serializer + __str__ = __repr__
- H-5: SubmitOrderResult.venue_order_id 非 Optional → Optional[str] に変更
- H-6: rustfmt 差分 → cargo fmt 適用
- M-1: SetSecondPassword 空文字列サイレントスキップ → if value is not None に修正
- M-2: _tachibana_session is None チェック欠落 → NOT_LOGGED_IN reject 追加
- M-3: test_unimplemented_streams FAIL → fetch_depth_snapshot を NotImplementedError 期待から外す
- M-8: update_venue_order_id Some→Some 上書き → None のみ更新可に修正
- M-9: architecture.md の try_insert 3 引数記述を 2 引数に訂正
- M-10: OrderListFilter deny_unknown_fields 追加
- M-11: _REQUIRED_TAG_PREFIX → _REQUIRED_CASH_MARGIN_TAG リネーム
- M-12: _envelope_to_wire else ブランチに Phase O3 実装予定コメント追加

### レビュー反映 (2026-04-26, ラウンド 2)

**解消した指摘**:
- R2-CRITICAL: H-5 fix 副作用 — OrderAccepted.venue_order_id を Rust 側も Option<String> + #[serde(default)] に変更
- R2-MEDIUM: SetSecondPassword 空文字列バリデーション — 空文字列 / 空白のみは設定せずに早期 return
- R2-MEDIUM: update_venue_order_id に #[must_use] 追加 + 成功ケーステスト返り値検証
- R2-MEDIUM: OrderListFilter absent フィールドテスト追加
- R2-LOW: _compose_request_payload の @field_serializer 罠コメント追加
- R2-LOW: httpx エラー分類拡張 (TimeoutException / HTTPStatusError)
- M-13: TAGS_REGISTRY に close_action / tategyoku 追加
- M-4+M-5: schema_v1_3_roundtrip.rs に 6 テスト追加

**持ち越し（R2 以降）**:
- H-1: commands.json / events.json schema 1.3 反映（Tpre.2「次 PR で実施」継続）
- H-2: SecondPasswordRequired fire-and-forget + 再送 SubmitOrder ポリシー（architecture.md §2.1 に仕様追記が先決）
- H-3: NautilusOrderEnvelope extra="ignore" 二重解析（即時リスク低い、T0.5 HTTP 層実装時に整理）
- H-4: client_order_id pydantic バリデーション（T0.5 HTTP 層が正式境界のため T0.5 と同時に実装）
- C-2: ForgetSecondPassword 競合ポリシー（architecture.md §2.4 に仕様追記が先決）
- M-6: Python enum str パススルー（IPC 経由のため現実的リスク低い）
- M-7: ClientOrderId pub → T0.5 HTTP 層実装時に try_new() コンストラクタとセットで対応

### レビュー反映 (2026-04-26, ラウンド 3 — サニティチェック)

**R3 結果: MEDIUM 以上の新規指摘ゼロ。ループ終了。**

**LOW のみ残存（次フェーズ起票推奨）**:
- Phase O1 実装時の注意: `venue_order_id=None` のまま残った order レコードに `CancelOrder` を送る経路は、`CancelOrder` が `venue_order_id: String`（非 Optional）のため None を渡せない。Phase O1 CancelOrder 実装時に `venue_order_id=None` → HTTP 409 + `reason_code="ORDER_STATUS_UNKNOWN"` で early reject するガードを入れること

**明示持ち越し（ユーザー承認済み理由付き）**:
- H-1: commands.json / events.json schema 1.3 反映 → 次 PR でスケジュール確定させること
- H-2: SecondPasswordRequired 再送ポリシー → architecture.md §2.1 に仕様を先に書く
- H-3: NautilusOrderEnvelope extra="ignore" → T0.5 HTTP 層整備時に整理
- H-4: client_order_id pydantic バリデーション → T0.5 HTTP 層が正式境界のため T0.5 と同時
- C-2: ForgetSecondPassword 競合ポリシー → architecture.md §2.4 に仕様追記が先決

### T0.5 Rust HTTP API `/api/order/submit` ✅

- [x] **`Cargo.toml` に `xxhash-rust` を追加**（`xxh3` feature を有効化）。`request_key` の `xxh3_64` で使用（[architecture.md §4.1](./architecture.md#41-request_key-の-canonicalization)）
- [x] `src/api/order_api.rs` 新設
- [x] 入力スキーマバリデーション（[spec.md §5](./spec.md#5-入力バリデーションrust-http-層)）
- [x] `engine-client/src/order_session_state.rs` 実装済み（`OrderSessionState` / `ClientOrderId` / `PlaceOrderOutcome`）
- [x] `engine_client.send(SubmitOrder)` → `OrderAccepted` / `OrderRejected` を待機
  タイムアウト: `tokio::time::timeout(Duration::from_secs(30), ...)` / HTTP 504 + `reason_code="INTERNAL_ERROR"`
- [x] HTTP 応答: 201 Created（新規）/ 200 OK（idempotent replay）/ 409 / 400 / 403 / 502 / 504（タイムアウト）
- [x] `src/main.rs` で `OrderApiState` を構築し `replay_api::spawn()` に渡す

**実装メモ**:
- テストは `src/api/order_api.rs` 内 `#[cfg(test)]` モジュールに配置（binary crate は `tests/` から private モジュールにアクセス不可のため）
- events は `conn.subscribe_events()` を `conn.send(cmd)` より**先に**呼ぶこと（レースコンディション回避）
- `submit_timeout` は `#[cfg(test)]` の `.with_timeout()` ビルダーで短縮可能（タイムアウトテスト用）

### T0.6 安全装置 ✅（2026-04-27 完了）

- [x] ✅ 起動 config に `tachibana.order.max_qty_per_order` / `max_yen_per_order` / `require_confirmation`（`OrderGuardConfig` struct として `src/api/order_api.rs` に実装）
- [x] ✅ **rate limit config 実装**: `tachibana.order.rate_limit_window_secs=3` / `rate_limit_max_hits=2` を config キーとして実装し、超過時は HTTP 429 + `reason_code="RATE_LIMITED"` を返す（`RateLimiter` sliding-window 実装）
- [x] ✅ config 未設定時は `/api/order/submit` を 503 で reject（明示 opt-in、誤発注防止）（`OrderGuardConfig::default()` で `enabled: false`）
- [x] ✅ **Python `tachibana_url` で本番 URL 検出時、`os.getenv("TACHIBANA_ALLOW_PROD") != "1"` なら send をブロック**
  - `is_production_url(url)` / `guard_prod_url(url)` を `tachibana_url.py` に追加
  - `submit_order()` の HTTP 送信直前に `guard_prod_url()` を呼び出す
  - テスト: `python/tests/test_prod_url_guard.py`（12 テスト全緑）

**受け入れテスト追加**:

- [x] ✅ **誤発注ガード回帰テスト（必須）**:
  - Rust: `max_qty_per_order` 超 → 400 reject、`max_yen_per_order` 超 → 400 reject、未設定 → 503 を返す cargo test（`src/api/order_api.rs` 内 7 テスト全緑）
  - 同一 `client_order_id` で N 並列リクエスト → 1 件のみ発注される連打耐性 integration test（既存 idempotency テストでカバー済み）
- [x] ✅ **REPLAY skip テスト**: `config.mode == REPLAY` のとき `/api/order/submit` は 503 + `reason_code="REPLAY_MODE_ACTIVE"` で即 reject — `test_submit_order_replay_mode_returns_503` が `src/api/order_api.rs` 内 `#[cfg(test)]` に実装済み（line 1994）
- [x] ✅ **rate limit 連打抑止テスト（D2-M2 / D3-4）**: `src/api/order_api.rs` 内 4 テスト — (a) N 件目までは通る（`test_rate_limit_allows_up_to_max_hits`） / (b) N+1 件目が **HTTP 429** + `reason_code="RATE_LIMITED"`（`test_rate_limit_rejects_on_n_plus_1`） / (c) `rate_limit_window_secs` 経過後 counter が reset され再度通る（`test_rate_limit_resets_after_window`、`tokio::time::pause/advance` 使用） / (d) `(instrument_id, side, qty, price)` のいずれかが不一致なら**別カウンタ**として独立にカウントされる（`test_rate_limit_different_key_independent_counter`）
- [ ] **仮想 URL マスクテスト**: `caplog` で `submit_order` 実行中のログを採取し `https://kabuka.e-shiten.jp` 以外のホスト名・`p_no=` 文字列が出ていないことを assert（T0.4 と同じ観点で HTTP 層側からも確認）

### T0.7 監査ログ WAL + 起動時復元（重複発注防止）

- [x] ✅ **`python/engine/exchanges/tachibana_orders.py` に `_audit_log_submit()` / `_audit_log_accepted()` / `_audit_log_rejected()` を追加**（[architecture.md §4.2](./architecture.md#42-監査ログwal-write-ahead-log)）
- [x] ✅ **`wal_path` パラメータ経由で WAL ファイルに append**:
  - `submit` 行は HTTP 送信 **直前**に `f.write(line + "\n"); f.flush(); os.fsync(f.fileno())` で書く（クラッシュ安全性）
  - `accepted` / `rejected` 行は応答受領後に `f.write(line + "\n"); f.flush()` で書く（fsync 不要）
  - `accepted` が OS バッファ残りのままクラッシュした場合、起動時復元は `unknown` 状態（Phase O1 GetOrderList で補完可）— この許容を実装コメントに明記済み
  - **第二暗証番号は絶対に書かない**（テスト: `test_audit_log_no_secret.py` で grep 検証）
- [x] ✅ **`read_wal_records(wal_path)` で WAL 復元関数を実装**
  - 末尾行に `\n` が無ければ truncated とみなしてスキップ + WARN ログを出す（C-R5-H1）
  - 非存在ファイルや空ファイルは空リストを返す
  - テスト: `python/tests/test_wal_truncation.py`（6 テスト全緑）
- [x] ✅ **Rust 側 `OrderSessionState::load_from_wal()` の起動時復元**: アプリ起動 → 当日分 WAL を読み戻し → `client_order_id ↔ request_key ↔ venue_order_id` の map を復元
  - `submit` のみで `accepted`/`rejected` 無し → `unknown` 状態で復元（Phase O1 T1.5 で `GetOrderList` から確定）
  - 同一 `client_order_id` で再送 → `IdempotentReplay` を返す
  - テスト: `engine-client/tests/order_session_state_wal.rs`（8 テスト全緑）
- [x] ✅ **`request_key` の canonicalization** を [architecture.md §4.1](./architecture.md#41-request_key-の-canonicalization) の規則どおりに実装済み（`src/api/order_api.rs`）。テストで pin（`tags` 順序入替・null vs 空文字 で同一 key になることを確認）
  - **canonicalization テスト（D2-L2）**: `cargo test --test request_key_canonical` — 5 テスト全緑（`tags` 順序入替 / `null` ↔ `""` / 重複排除 / 異なる qty / OrderSessionState end-to-end）
- [x] ✅ **WAL 冪等再送テスト**: 同一論理リクエスト（tags 順序違い）の 2 連投 → 1 件 Created (201) + 1 件 IdempotentReplay (200) を Rust integration test で確認 — `test_idempotent_replay_with_different_tags_order_returns_200` を `src/api/order_api.rs` 内 `#[cfg(test)]` に実装済み
- [x] ✅ **WAL 第二暗証番号漏洩 grep テスト（D2-H2）**: `uv run pytest python/tests/test_audit_log_no_secret.py`（5 テスト全緑）
  - WAL `.jsonl` 全行を grep して `second_password` / `sSecondPassword` 等の禁止キー名が含まれないことを確認
  - C-L4 制御文字エスケープが効いて `\n` / `\t` / `\x01-\x03` が生のまま出力されないことを確認
- [x] ✅ **WAL truncation 復元テスト（HIGH-R6-D2）（Rust 側）**: `cargo test -p flowsurface-engine-client --test order_session_state_wal` — WAL 末尾行が `\n` 欠落の状態で `OrderSessionState::load_from_wal()` を実行し、当該行が skip + `log::warn!` が出ることを assert（8 テスト全緑）

### T0.8 テスト ✅（2026-04-26 完了）

- [x] ✅ Python pytest-httpx で **flowsurface テスト群を移植**（入力は `NautilusOrderEnvelope` 経由に置換）:
  - `submit_order_returns_error_on_wrong_password_response` ([flowsurface tachibana.rs:4168](../../../../flowsurface/exchange/src/adapter/tachibana.rs#L4168)）→ `python/tests/test_tachibana_error_responses.py`
  - `submit_order_returns_error_on_market_closed_response` (同 4215) → 同上
  - `submit_order_returns_error_on_invalid_issue_code_response` (同 4256) → 同上（3 テスト全緑）
- [x] ✅ Python: `_envelope_to_wire` の写像テーブルテスト — [architecture.md §10.1〜§10.4](./architecture.md#101-ordertype-写像) の各行に 1 ケースずつ（`python/tests/test_tachibana_order_mapping.py` 28 テスト全緑、2026-04-28 完了）
- [x] ✅ Python: `_compose_request_payload` のフィールド存在 / `sCLMID` / `sJsonOfmt` / 逆指値デフォルト（`test_tachibana_compose_payload.py` 8 テスト全緑）
- [x] ✅ **nautilus 互換性テスト**: nautilus を import しない状態で、`nautilus_trader.model.orders.MarketOrder.create(...)` 互換の dict を `NautilusOrderEnvelope.model_validate(...)` で読み込み可能（field 名・enum 文字列一致を検証）— `python/tests/test_nautilus_compatibility.py`（6 テスト全緑）
- [x] ✅ Rust: `OrderSessionState` の `Created/IdempotentReplay/Conflict` 3 ケース（flowsurface 同名テストの移植）— `engine-client/tests/order_session_state.rs`（既存 8 テスト）
- [x] ✅ Rust: `/api/order/submit` のスキーマバリデーション（不正 client_order_id、quantity=0、instrument_id 形式違反）— `src/api/order_api.rs` 内 `#[cfg(test)]` に追加（5 テスト全緑）
- [x] ✅ **Python: `TACHIBANA_ALLOW_PROD=1` ガードのテスト**（`test_prod_url_guard.py` 12 テスト全緑）— `monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)` で本番 URL ブロック確認済み
- [ ] **手動 E2E**（CI 載せず、デモ環境クレデンシャル必須）: `s80_order_submit_demo.sh` で「現物・成行・買 100 株」が通る
- [ ] **クラッシュリカバリ E2E**（`s80_order_crash_recovery_demo.sh`）:
  1. `POST /api/order/submit` を送信して WAL に `submit` 行が書かれた直後にプロセスを kill
  2. 再起動 → 同一 `client_order_id` で再送
  3. `IdempotentReplay`（HTTP 202 + `warning: order_status_unknown`）が返ること
  4. デモ注文一覧で重複発注が起きていないことを確認

**Exit 条件**: デモ環境で curl `/api/order/submit` → `sOrderNumber` が返る。監査ログに第二暗証番号が出ていないことを確認。クラッシュリカバリ E2E が手動で通ること。

---

## Phase O1: 訂正（modify）・取消・一覧

### T1.1 Python modify・取消・一覧 ✅
- [x] `tachibana_orders.modify_order` / `cancel_order` / `cancel_all_orders` / `fetch_order_list`
  - 関数名は **nautilus 抽象** に統一（`modify_order` / `cancel_order` / `cancel_all_orders` / `fetch_order_list`。`fetch_order_list` は nautilus 名そのまま）。内部で立花 `CLMKabuCorrectOrder` 等を呼ぶ
- [x] flowsurface の `CorrectOrderRequest` / `CancelOrderRequest` / `OrderListRequest` を pydantic で移植（型名は `TachibanaWireModifyRequest` 等にリネーム — T0.4 の `Wire` prefix 規約に統一）
- [x] レスポンス型 `ModifyOrderResult` / `CancelOrderResult` / `CancelAllResult` / `OrderRecordWire` 実装

### T1.2 IPC 拡張 ✅
- [x] `Command::ModifyOrder` / `CancelOrder` / `CancelAllOrders` / `GetOrderList`（schema 1.3 で既実装）
- [x] `Event::OrderListUpdated` / `OrderPendingUpdate` / `OrderPendingCancel`（schema 1.3 で既実装）
- [x] SCHEMA_MINOR: 3 → 4（Rust engine-client/src/lib.rs + Python engine/schemas.py）

### T1.3 Rust HTTP ✅
- [x] `/api/order/modify` `/api/order/cancel` `/api/order/cancel-all` `/api/order/list`
- [x] `cancel-all` は確認モーダル必須（HTTP 層では **JSON body に `confirm: true` を必須**とする。query param ではない。[spec.md §4](./spec.md#4-公開-apihttp) に準拠）
- [x] `/api/order/cancel` の Rust 実装では `OrderSessionState.get_venue_order_id(client_order_id)` で lookup し、`venue_order_id` を Python `cancel_order(...)` に渡すこと（[architecture.md §2.3](./architecture.md#23-取消フローphase-o1)）。`venue_order_id = None`（unknown）は 404 reject
- [x] **`cancel-all` の `confirm` フィールド検証はテーブルテスト化**（body 欠落 / `confirm: false` / `confirm: "true"`（文字列）すべて 400 reject）

### T1.4 UI: 注文一覧パネル ✅（scaffold）
- [x] `src/screen/dashboard/panel/orders.rs`（新設 — scaffold）
- [ ] 当日注文を表示・選択 → 訂正 / 取消ボタン（scaffold 実装済み、dashboard pane ルーターへの統合は Phase O2 以降）
- [ ] 確認モーダル（成行発注時・取消時）
- [ ] **発注フォーム・確認モーダル・訂正/取消モーダルはすべて iced 側**で実装（Q3 暫定確定: Q1 で第二暗証番号入力も iced modal にした流れに合わせる。tkinter はログイン専用）

### T1.5 起動時の台帳復元 ✅（一部）
- [x] `OrderSessionState::update_venue_order_id_from_list()` 追加（GetOrderList 応答からの venue_order_id 補完）
- [x] Python server.py に `_do_get_order_list` / `_do_modify_order` / `_do_cancel_order` / `_do_cancel_all_orders` dispatch handlers 追加
- [x] ✅ `client_order_id` 不明の注文は HTTP `/api/order/modify` `/api/order/cancel` 入力として **`venue_order_id` も受理**できるようにする（spec.md §5、2026-04-28 完了）。IPC `Command::ModifyOrder` に `venue_order_id: Option<String>` 追加（SCHEMA_MINOR 0→1）

### T1.6 テスト ✅
- [x] Python: modify・取消・一覧の正常系・session 切れ（`test_tachibana_modify_cancel_order.py` 13 tests）
- [x] Rust: `/api/order/cancel-all` の `confirm` 必須チェック（body 欠落・false・文字列 "true"・true）
- [x] Rust: `test_cancel_with_unknown_venue_order_id_returns_404`
- [x] Rust: `schema_v1_4_roundtrip.rs` — ModifyOrder / CancelOrder / OrderListUpdated roundtrip (12 tests)
- [ ] **session 切れ即停止テスト**: `p_errno=2` または HTTP 401 検知 → `OrderSessionState::Frozen` 遷移（Phase O2 以降）
- [ ] 手動 E2E: `s81_order_modify_cancel_demo.sh`

**Exit 条件**: デモ環境で「指値発注 → 訂正 → 取消」が UI から完結。

---

## Phase O2: EVENT EC 約定通知

### T2.1 EC パーサ + `tachibana_event.py` 実装本体 ✅（2026-04-26 完了）
- [x] ✅ **`tachibana_event.py` を Tpre.5 で新規作成済み**（EVENT WebSocket 受信ループ含む。FD 受信＋EC 受信の合流責務）。本タスクではその上に EC パース実装を載せる。Phase 1 計画 ([docs/plan/✅tachibana/implementation-plan.md](../✅tachibana/implementation-plan.md)) との依存関係: Phase 1 の認証セッション（`tachibana_auth.py`）が前提
- [x] ✅ `tachibana_event.py._parse_ec_frame(items) -> OrderEcEvent`
- [x] ✅ 主要項目（[architecture.md §6](./architecture.md#6-event-ec-フレームのパースphase-o2)）の写像（p_NO/p_EDA/p_NT/p_DH/p_DSU/p_ZSU/p_OD → IPC フィールド）
- [x] ✅ **EVENT URL sanitize（C-R2-L1）**: `build_event_url` 内 `_check_no_control_chars` で制御文字を reject（既実装）
- [x] ✅ **EVENT URL sanitize 受け入れテスト（D4-4）**: `python/tests/test_event_url_sanitize.py` — 15 テスト全緑
- [x] ✅ **マニュアル現物確認 → samples で代替**: EC フィールド仕様は `.claude/skills/tachibana/samples/e_api_event_receive_tel.py`（行 534–568）で確認済み。PDF 不要。

### T2.2 IPC イベント拡張 ✅（2026-04-26 完了）
- [x] ✅ `Event::OrderFilled` / `OrderCanceled` / `OrderExpired` — schema 1.3 で骨格定義済み。SCHEMA_MINOR 4→5 に bump
- [x] ✅ **`OrderPartiallyFilled` は持たない**: nautilus 流に `OrderFilled` の `leaves_qty` で部分/全部を判定する。詳細は [architecture.md §3](./architecture.md#3-ipc-スキーマ拡張schema-12--13) 末尾

### T2.3 重複検知 ✅（2026-04-26 完了）
- [x] ✅ `tachibana_event.py` の `TachibanaEventClient` に `_seen: set[tuple[str, str]]` を実装（**EC 重複検知キーは `(venue_order_id, trade_id)`** に統一。nautilus 用語）
- [x] ✅ 当日リセット: `reset_seen_trades()` メソッド実装済み（夜間閉局検知時に呼び出す）

### T2.4 Rust UI 反映 ✅（2026-04-26 完了）
- [x] ✅ notification toast: `Message::OrderToast(Toast)` variant を追加し、`map_engine_event_to_tachibana()` で `OrderFilled`/`OrderCanceled`/`OrderExpired` を toast に変換（既存通知機構を使用）
- [ ] 注文一覧パネルの行更新（Phase O1 OrderListUpdated 連携 — 後続タスクで対応）

### T2.5 テスト ✅（2026-04-26 完了）
- [x] ✅ Python: 実 frame サンプル（合成）でパース → 期待 IPC イベント — `python/tests/test_ec_parser.py`（11 テスト全緑）
- [x] ✅ Python: `(venue_order_id, trade_id)` キーの重複検知 — `python/tests/test_ec_dedup.py`（7 テスト全緑）
- [ ] **EC 重複検知 E2E**: 再接続を fault-injection で発生させ、同一 EC frame を 2 度受信しても `Event::OrderFilled` が 1 度しか発火しないこと
- [x] ✅ **EC state-machine テスト（D2-L3）**: 拒否 / 失効 / 部分→全部 の遷移順序を assert — `python/tests/test_ec_state_machine.py`（10 テスト全緑）
- [x] ✅ **Rust schema_v1_5_roundtrip テスト**: `engine-client/tests/schema_v1_5_roundtrip.rs`（8 テスト全緑）
- [ ] 手動 E2E: 発注 → 約定 toast を目視

**Exit 条件**: デモ環境で発注 → 約定通知が UI に出る。再接続時の再送が UI を二重表示させないことを確認。

---

## Phase O3: 信用・逆指値・余力 ✅（2026-04-26 完了）

### T3.1 NewOrderRequest 拡張 ✅
- [x] ✅ `cash_margin = 2/4/6/8`（信用新規・返済の制度・一般）— `sGenkinShinyouKubun` マッピング完成
- [x] ✅ `gyakusasi_order_type` / `gyakusasi_zyouken` / `gyakusasi_price` — STOP_MARKET / STOP_LIMIT 対応
- [x] ✅ `expire_day = YYYYMMDD` — `expire_time_ns → JST YYYYMMDD` 変換（`_expire_ns_to_jst_yyyymmdd()`）
- [x] ✅ 信用返済の建玉個別指定（`tatebi_type=1` + `aCLMKabuHensaiData`）— `tategyoku_id` tag 解析

### T3.2 余力・建玉 API ✅
- [x] ✅ `tachibana_orders.fetch_buying_power` (`CLMZanKaiKanougaku`) + `BuyingPowerResult` dataclass
- [x] ✅ `tachibana_orders.fetch_credit_buying_power` (`CLMZanShinkiKanoIjiritu`) + `CreditBuyingPowerResult`
- [x] ✅ `tachibana_orders.fetch_sellable_qty` (`CLMZanUriKanousuu`) + `SellableQtyResult`
- [x] ✅ `tachibana_orders.fetch_positions` (`CLMGenbutuKabuList` / `CLMShinyouTategyokuList`) + `PositionRecord`
- [x] ✅ `InsufficientFundsError` 例外（`reason_code="INSUFFICIENT_FUNDS"`, `shortfall` field）

### T3.3 発注前ガード ✅
- [x] ✅ `InsufficientFundsError` → `OrderRejected{reason_code="INSUFFICIENT_FUNDS"}` Python dispatch
- [x] ✅ Rust `reason_code_to_status("INSUFFICIENT_FUNDS")` → HTTP 403（`src/api/order_api.rs`）
- [x] ✅ Phase O3 解禁: LIMIT / SELL / STOP_MARKET / STOP_LIMIT / GTD — `check_phase_o0_order()` 更新
- [x] ✅ Phase O3 引き続き非対応: MARKET_IF_TOUCHED / LIMIT_IF_TOUCHED / GTC / IOC / FOK

### T3.4 UI ✅
- [x] ✅ `src/screen/dashboard/panel/buying_power.rs` 新設（`BuyingPowerPanel` scaffold）
- [x] ✅ `CashMarginSelection` enum（`to_tag()` / `label()`）、`StopOrderForm`、`GtdForm` stub
- [x] ✅ `src/screen/dashboard/panel.rs` に `pub mod buying_power;` 追加

### T3.5 テスト ✅
- [x] ✅ `python/tests/test_tachibana_credit_orders.py`（16 テスト）— 信用 cash_margin マッピング・逆指値・GTD・建玉
- [x] ✅ `python/tests/test_tachibana_buying_power.py`（6 テスト）— 余力 API パース・InsufficientFundsError
- [x] ✅ `python/tests/test_unsupported_phase_o0.py` 更新（31 テスト）— Phase O3 解禁パターン追加
- [x] ✅ `python/tests/test_order_dispatch.py` 更新（11 テスト）— LIMIT/SELL ガード通過テスト更新
- [x] ✅ Rust `test_insufficient_funds_returns_403`（`src/api/order_api.rs`）— HTTP 403 マッピング確認

**Exit 条件**: 信用新規買い・逆指値・期日指定がデモで完結。余力不足が UI で正しく表示。
**完了確認（2026-04-26）**: `cargo test --workspace` 全緑・`cargo clippy -- -D warnings` クリーン・`uv run pytest python/tests/ -v` 714 テスト全緑

---

## 横断タスク

- [x] ✅ `.claude/skills/tachibana/SKILL.md` の Phase 1 制約記述を Phase O0 解禁時に更新（T0.1 内）— 第二暗証番号は「ログイン時には収集しない / Phase O0 以降は iced modal で取得・メモリのみ保持」に更新済み
- [x] ✅ [docs/plan/✅tachibana/spec.md](../✅tachibana/spec.md) §2.2「発注は Phase 2+」記述を「[docs/plan/✅order/](.) で管理」に書き換え完了
- [x] ✅ [docs/plan/README.md](../README.md) の Phase ロードマップに Order Phase O0–O3 を追記完了
- [ ] [docs/plan/nautilus_trader/spec.md](../nautilus_trader/spec.md) §2.3 Phase N2 に「`tachibana_orders.py` を `LiveExecutionClient` 内で再利用」を明記（変更不要、既に方針一致）
- [x] ✅ **nautilus 互換境界 lint テスト**: `python/tests/test_nautilus_boundary_lint.py` — `dto.rs` / `schemas.py` / `src/` Rust UI 層に立花固有禁止語（`sCLMID` / `p_sd_date` / `Zyoutoeki` / `p_no` / `p_eda_no` 等）が含まれないことを grep で確認。注: CI workflow ファイル追加は GitHub Actions 設定が必要なため別 PR で対応
- [x] ✅ **不変条件マッピング doc 整合性テスト（D3-5）**: `uv run pytest python/tests/test_invariant_tests_doc.py`（5 テスト全緑）— `invariant-tests.md` の ✅ 行に紐付く test ファイル・関数名が実在することを CI で保証

## 下流計画への影響

本計画の完了は、以下の nautilus_trader 計画フェーズのブロッカーを解除する。

| 完了フェーズ | 解除されるブロッカー | 参照先 |
|---|---|---|
| **O0（現物成行買い）完了** | nautilus N1（リプレイ API 差し替え + REPLAY 仮想注文）着手可能。N1 で `order_router.py` が本計画の `tachibana_orders.submit_order` を live 経路として呼ぶ | [nautilus_trader/implementation-plan.md Phase N1](../nautilus_trader/implementation-plan.md) |
| **O0〜O2（約定通知）完了** | nautilus N2（`LiveExecutionClient` デモ）着手可能。N2 は本計画の `tachibana_orders.py` / `tachibana_event._parse_ec_frame` / 監査ログ WAL がすべて稼働している前提 | [nautilus_trader/implementation-plan.md Phase N2](../nautilus_trader/implementation-plan.md) |

> **nautilus 互換不変条件**: 本計画で書く `submit_order` / `modify_order` / `cancel_order` の**関数シグネチャ・型・戻り値は変更しない**（nautilus N2 での `LiveExecutionClient` 委譲先として再利用するため）。spec.md §6 の nautilus 互換要件違反は merge 禁止。
>
> **IPC schema 連鎖**: 本計画の Tpre.2 で schema **1.2 → 1.3** に bump する（tachibana T0.2 の schema 1.2 確定が前提）。nautilus N1.1（schema 1.3 → 1.4）は本計画の schema 1.3 ラウンドトリップテストが緑になるまで着手しないこと。連鎖の全体像は [docs/plan/README.md §実装トラック詳細](../README.md) を参照。

---

## nautilus N2 移行時に行う作業（参考・本計画スコープ外）

[architecture.md §10.6](./architecture.md#106-nautilus-移行時の差分n2-で実施する作業のみ) の通り、本計画完了時点で型互換が完全に取れていれば、N2 で行うのは:

1. `pyproject.toml` に `nautilus_trader` を追加
2. `python/engine/nautilus/clients/tachibana.py` を新設（`LiveExecutionClient` 継承）し中身は `tachibana_orders.submit_order(...)` を呼ぶだけ
3. `_envelope_to_wire` を `NautilusOrderEnvelope` の代わりに本物の `nautilus_trader.model.orders.Order` を受けるよう型注釈だけ書き換え（field アクセス互換のため動作変更なし）

**本計画のコードは削除しない**。HTTP API `/api/order/*` も nautilus 経路と並行して残す（手動発注・curl 経路の維持）。

---

## レビュー反省録

### R1 修正バッチ（2026-04-27）

**背景**: レビュー R1 で指摘された Group A（Rust 型/ロジック）・B（Python）・C（IPC スキーマ）・D（テスト追加）の 4 グループ計 28 項目を TDD（RED→GREEN→REFACTOR）で修正。

**実施内容**:

| ID | 内容 | ファイル | 状態 |
|---|---|---|---|
| A-1 | `update_venue_order_id_from_list` テスト追加 | `engine-client/tests/order_session_state.rs` | ✅ |
| A-2 | `ClientOrderId::try_new` 境界値テスト + `pub(crate)` | `engine-client/src/order_session_state.rs` | ✅ |
| A-3 | WAL restore で `request_key==0` スキップ | `engine-client/src/order_session_state.rs` | ✅ |
| A-4 | `update_venue_order_id` 戻り値チェック + warn | `src/api/order_api.rs` | ✅ |
| A-5 | `RateLimiter::record_and_check` 初回計測点 None ガード | `src/api/order_api.rs` | ✅ |
| A-6 | `trim_end_matches` → `strip_suffix` ダブルサフィックス防止 | `src/api/order_api.rs` | ✅ |
| A-7 | `/api/test/*` を `#[cfg(debug_assertions)]` でガード | `src/replay_api.rs` | ✅ |
| A-8 | `frozen` state + `SessionFrozen` variant | `engine-client/src/order_session_state.rs` | ✅ |
| A-9 | WAL 復元を main.rs で起動時に実行 | `src/main.rs` | ✅ |
| A-10 | `inject_hit_at` dead code 削除 | `src/api/order_api.rs` | ✅ |
| A-11 | `serde_json::to_string(...).unwrap_or_default()` → `unwrap_or_else` | `src/api/order_api.rs` | ✅ |
| B-1 | `OrderAccepted.venue_order_id` を Optional に | `python/engine/schemas.py` | ✅ |
| B-3 | `wal_path` を `_do_submit_order` に渡す | `python/engine/server.py` | ✅ |
| B-4 | `_sanitize_for_wal` の `\n`/`\t` 通過バグ修正 | `python/engine/exchanges/tachibana_orders.py` | ✅ |
| B-5 | `except OSError:` を `except Exception:` 前に追加 | `python/engine/server.py` | ✅ |
| B-6 | `order_side="BUY"` ハードコード修正 → `sBaibaiKubun` から読む | `python/engine/exchanges/tachibana_orders.py` | ✅ |
| B-7 | `receive_loop` に再接続ループ追加 | `python/engine/exchanges/tachibana_event.py` | ✅ |
| C-1 | `OrderListFilter` / `SetSecondPassword` に `extra="forbid"` | `python/engine/schemas.py` | ✅ |
| C-2 | `OrderRecordWire` に `#[serde(deny_unknown_fields)]` | `engine-client/src/dto.rs` | ✅ |
| D-3 | `close_strategy=funari` → `sCondition="6"` | `python/engine/exchanges/tachibana_orders.py` | ✅ |

**テスト追加数**: Rust +9、Python +14（合計 +23）

**4 コマンド検証結果**:
- `cargo fmt --check` → OK
- `cargo clippy -- -D warnings` → OK
- `cargo test --workspace` → 全緑（0 失敗）
- `uv run pytest python/tests/ -v` → 728 passed（0 失敗）

### R2 修正バッチ（2026-04-27）

**背景**: R1 完了後のブランチ全体レビュー（3 エージェント並列: silent-failure-hunter / rust-reviewer / general-purpose）。CRITICAL+HIGH+MEDIUM 計 16 件を修正。

**解消した指摘**:

| ID | 内容 | ファイル | 状態 |
|---|---|---|---|
| R2-C1 | `on_submit_success()` 未呼び出し → invalid_count リセットされず誤 lockout | `python/engine/server.py` | ✅ |
| R2-H1 | `touch()` が `get_password()` 前で idle 判定が常に False → 全 4 ハンドラで順序修正 | `python/engine/server.py` | ✅ |
| R2-H2 | `freeze()` 未配線 → SESSION_EXPIRED 受信時に `state.session.lock().await.freeze()` を追加 | `src/api/order_api.rs` | ✅ |
| R2-H3 | `parse_order_side/type/tif` ワイルドカード `_` → 誤フォールバック → `unreachable!()` に変更 | `src/api/order_api.rs` | ✅ |
| R2-H4 | `expect("validated")` → panic リスク → `match` + HTTP 500 に変換 | `src/api/order_api.rs` | ✅ |
| R2-H5 | `let _ = stream.write_all(...)` 書き込み失敗の無言破棄 → `if let Err(e)` + `log::debug!` | `src/api/order_api.rs` | ✅ |
| R2-H6 | receive_loop が接続成功のたびに retry_count リセット → 30 秒安定判定後のみリセット | `python/engine/exchanges/tachibana_event.py` | ✅ |
| R2-M1 | modify/cancel で `on_invalid()` 未呼び出し → `SecondPasswordInvalidError` サブクラス追加 + 配線 | `tachibana_helpers.py` / `server.py` | ✅ |
| R2-M3 | `RecvError::Lagged` ログ欠如 → `log::warn!` 追加（4 ヶ所） | `src/api/order_api.rs` | ✅ |
| R2-M4 | `_do_get_order_list` session=None 無言返却 → `log.warning` 追加 | `python/engine/server.py` | ✅ |
| R2-M5 | `_now()` deprecated `asyncio.get_event_loop()` → `get_running_loop()` に変更 | `python/engine/exchanges/tachibana_auth.py` | ✅ |
| R2-M7 | backoff 上限なし → `min(backoff, 60.0)` キャップ追加 | `python/engine/exchanges/tachibana_event.py` | ✅ |
| R2-M8 | テスト組み合わせ不足 → `test_clear_does_not_remove_lockout` + `test_idle_expired_while_locked_out` 追加 | `test_tachibana_session_holder.py` | ✅ |

**テスト追加数**: Rust +0、Python +3（合計 +3）

**4 コマンド検証結果**:
- `cargo fmt --check` → OK
- `cargo clippy -- -D warnings` → OK（警告 0 件）
- `cargo test -p flowsurface-engine-client` → 全緑
- `uv run pytest python/tests/ -q` → 744 passed（0 失敗）

### R3 サニティチェック（2026-04-27）

**R3 结果**: MEDIUM 以上の新規指摘 2 件を追加発見 → 即修正。

| ID | 内容 | ファイル | 状態 |
|---|---|---|---|
| R3-H1 | `_do_cancel_all_orders` に `SecondPasswordInvalidError` catch 漏れ → `on_invalid()` 未呼び出し | `python/engine/server.py` | ✅ |
| R3-M1 | `parse_order_*` の `other =>` + warn+default → `validate()` 保証不変条件なのに誤値許容 → `unreachable!()` に変更済み | `src/api/order_api.rs` | ✅（R2-H3 統合） |

**LOW のみ残存（次フェーズ以降）**:
- receive_loop の `retry_count = 0 + break` パターン（break 後に参照されない dead code、実害なし）
- `update_venue_order_id_from_list` 複数 unknown エントリ非決定性（BTreeMap 移行は Phase O2 以降 / WAL に挿入時刻追加時に対応）

**ループ収束確認**:
- `cargo fmt --check` → OK
- `cargo clippy -- -D warnings` → OK（警告 0 件）
- `cargo test -p flowsurface-engine-client` → 全緑
- `uv run pytest python/tests/ -q` → 744 passed（0 失敗）

**MEDIUM 以上ゼロ。ループ終了。**

---

### レビュー反映 (2026-04-27, ラウンド 4 — フルスタック完了後レビュー)

**背景**: O0〜O3 全フェーズ完了後のブランチ全体レビュー（5 エージェント並列: rust-reviewer / silent-failure-hunter / type-design-analyzer / ws-compatibility-auditor / general-purpose）。CRITICAL+HIGH+MEDIUM 計 17 件を R1〜R2 で修正、R3 サニティチェックで収束確認。

**R1 修正バッチ（2026-04-27）**:

| ID | 内容 | ファイル | 状態 |
|---|---|---|---|
| C-2 | cancel_all 内ループで SecondPasswordInvalidError/SessionExpiredError を個別 except + raise | `tachibana_orders.py` | ✅ |
| H-A | modify_order / cancel_order に is_frozen() チェック追加 | `src/api/order_api.rs` | ✅ |
| H-B | parse_trigger_type 未知値を validate() で 400 reject + unreachable!() | `src/api/order_api.rs` | ✅ |
| H-C | OrderModifyChange に extra="forbid" 追加 | `python/engine/schemas.py` | ✅ |
| H-D | OrderRecordWire (Python) に extra="forbid" 追加 | `python/engine/schemas.py` | ✅ |
| H-F | receive_loop 正常終了後に reconnect_fn で再接続 | `tachibana_event.py` | ✅ |
| H-G | on_submit_success() を modify/cancel/cancel_all 成功パスに追加 | `python/engine/server.py` | ✅ |
| H-H | SESSION_EXPIRED 検出を split_once exact match に変更 | `src/api/order_api.rs` | ✅ |
| H-I | touch() を get_password() より前に移動（全 4 ハンドラ） | `python/engine/server.py` | ✅ |
| M-1 | HTTP wire 型 6 構造体に #[serde(deny_unknown_fields)] 追加 | `src/api/order_api.rs` | ✅ |
| M-2 | ForgetSecondPassword に extra="forbid" 追加 | `python/engine/schemas.py` | ✅ |
| M-3 | TriggerType SCREAMING_SNAKE_CASE roundtrip テスト追加 | `schema_v1_3_roundtrip.rs` | ✅ |
| M-5 | p_OD パース失敗時に ts_event_ms=0 → 現在時刻に変更 | `tachibana_event.py` | ✅ |
| M-6 | WAL_ERROR reason_code → INTERNAL_ERROR に統一 | `python/engine/server.py` | ✅ |
| H-E | SubmitOrderRequest IPC に request_key: u64 追加、Python WAL に書き込み（SCHEMA_MINOR 5→6） | `dto.rs` / `order_api.rs` / `schemas.py` / `tachibana_orders.py` / `server.py` | ✅ |
| C-1 | cancel_all fire-and-forget 設計注記を order_api.rs に追加 | `src/api/order_api.rs` | ✅ |

**R2 修正バッチ（2026-04-27）**:

| ID | 内容 | ファイル | 状態 |
|---|---|---|---|
| R4-M-A | cancel_all の failed_count>0 を log.warning + PARTIAL_CANCEL_FAILURE Error で通知 | `python/engine/server.py` | ✅ |
| R4-M-B | reconnect_fn 失敗後に stale ws を再イテレーションしない（retry_count 二重インクリメント解消） | `tachibana_event.py` | ✅ |

**テスト追加数**: Rust +12、Python +27（合計 +39）

**4 コマンド検証結果（R3 サニティ後）**:
- `cargo fmt --check` → OK
- `cargo clippy --workspace -- -D warnings` → OK（警告 0 件）
- `cargo test --workspace` → 全緑
- `uv run pytest python/tests/ -q` → 775 passed, 2 skipped（0 失敗）

**LOW のみ残存（次フェーズ以降）**:
- receive_loop の外側/内側終了条件が `>` vs `>=` で非対称（余分な 1 回 reconnect 試行、データロスなし）
- フレーム処理エラーで `logger.error` のスタックトレース欠落（`exc_info=True` 推奨）
- compute_request_key が 0 を返した場合の WAL スキップ（xxh3_64 で 0 は確率的に極めて低い）
- modify/cancel 操作が WAL に記録されない（WAL は submit 冪等性専用の設計、意図通り）

**明示持ち越し（設計決定済み）**:
- C-1: cancel_all SESSION_EXPIRED の即時 freeze 不可 → 設計注記で許容（案 A 確定）
- H-E の IPC 経由 request_key は実装済み。Python 側の xxh3_64 独立計算（案 β）は不採用。

**MEDIUM 以上ゼロ。ループ終了。**
