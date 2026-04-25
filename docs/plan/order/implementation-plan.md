# 立花注文機能: 実装計画

**前提条件（着手ブロッカー）**: 立花 Phase 1（[docs/plan/tachibana/implementation-plan.md](../tachibana/implementation-plan.md)）の T2（認証実装）以降が完了していること。ログイン経路は `tachibana_login_flow.py` + `tachibana_auth.py` で構成される（`tachibana_login.py` は存在しない）。

**現状確認（2026-04-25）**: `python/engine/exchanges/` に既存の tachibana 系ファイル:

- `tachibana_auth.py` — 認証・セッション管理（`PNoCounter` を含む）
- `tachibana_codec.py` — Shift-JIS / JSON エンコード
- `tachibana_helpers.py` — 共通ヘルパ（`current_p_sd_date()` 等）
- `tachibana_login_dialog.py` — tkinter ログインダイアログ
- `tachibana_login_flow.py` — ログインフロー本体
- `tachibana_master.py` — マスタデータ
- `tachibana_url.py` — 仮想 URL 管理

**未実装（本計画で新規作成）**:

- `tachibana_event.py` — EVENT WebSocket 受信ループ + EC パーサ。Phase O2 の Tpre.5 / T2.1 で新規作成する（FD 受信＋EC 受信の合流責務を持つ）。Phase 1（[docs/plan/tachibana/implementation-plan.md](../tachibana/implementation-plan.md)）には EVENT 受信ループは含まれていないため、本計画で初めて導入する

O-pre の Tpre タスクは Phase 1 の認証基盤が無くても型定義だけ進められるが、T0.3 以降は Phase 1 完了が必要。

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
- [ ] **新規 enum variant をここで凍結**（実ディスパッチは T0.3 で実装するが、enum 定義はすべて Tpre.2 で確定する）:
  - `Command::SetSecondPassword { value: String }`
  - `Command::ForgetSecondPassword`
  - `Event::SecondPasswordRequired { request_id }`
- [ ] **`Command` enum の `#[derive(Debug)]` をこのタスクで手実装に切り替える**（`SetSecondPassword` 追加前に実施。architecture.md §2.4 参照）。`value` が `[REDACTED]` にマスクされることをテストで検証すること（Tpre.2 の受け入れ条件）
- [ ] [docs/plan/✅python-data-engine/schemas/commands.json](../✅python-data-engine/schemas/commands.json) と `events.json` を更新（schema 1.3）
- [ ] [python/engine/schemas.py](../../../python/engine/schemas.py) に対応 pydantic モデル
- [ ] **ラウンドトリップテスト**: Rust serialize → Python deserialize、Python serialize → Rust deserialize の両方向で全 enum 値を検証（typo を 1 文字でも入れたら CI で落ちること）
- [ ] **第二暗証番号 IPC 漏洩防止テスト（D2-M5）**: `cargo test -p flowsurface-engine-client --test creds_no_second_password_on_wire` — `second_password=Some(...)` を IPC に乗せようとしたら serialize error / panic することを assert
- [ ] **DTO `deny_unknown_fields` テスト（D3-1）**: `cargo test -p flowsurface-engine-client --test dto_deny_unknown_fields` — `SubmitOrderRequest` / `OrderModifyChange` に未知 4 フィールド（`second_password` / `secondPassword` / `p_no` / 任意 `_extra`）を入れた JSON で deserialize error になることを assert。`invariant-tests.md` の C-R2-M3 行と紐付ける

### Tpre.3 Python `NautilusOrderEnvelope` 雛形
- [ ] `python/engine/exchanges/tachibana_orders.py` 内に `NautilusOrderEnvelope` (pydantic) を定義
- [ ] field 構成は `nautilus_trader.model.orders.Order` と一致（`client_order_id` / `instrument_id` / `order_side` / `order_type` / `quantity` / `price` / `trigger_price` / `time_in_force` / `expire_time_ns` / `tags` / `init_id` 等）
- [ ] 内部 wire 型 `TachibanaWireRequest` は別 class で切り、`_compose_request_payload(envelope, second_password) -> TachibanaWireRequest` 経由でしか生成しない（写像は 1 箇所集約）
- [ ] `TAGS_REGISTRY` を [architecture.md §10.4](./architecture.md#104-venue-extension-tags-の正規化キー) のキー一覧で初期化

### Tpre.4 Rust `OrderSessionState` の `client_order_id` 主キー化
- [ ] flowsurface [`agent_session_state.rs`](../../../../flowsurface/src/api/agent_session_state.rs) の `ClientOrderId` newtype 規約を踏襲
- [ ] `try_insert(client_order_id, request_key) -> PlaceOrderOutcome` のシグネチャを確定
- [ ] `update_venue_order_id(client_order_id, venue_order_id)` も追加（nautilus は client→venue の写像が必要）

### Tpre.5 EVENT EC フレームの仕様根拠を確保（Q5、Phase O2 ブロッカ解消）+ `tachibana_event.py` 新規作成

**理由**: Phase O2 着手時に「マニュアル PDF が無く、サンプル frame も無い」状態で詰まるのを防ぐため、O-pre で根拠を確定する。Phase O0/O1 の作業と並行で進めて良いが、O2 着手 **前**に必ず完了させる。

**新規作成**: `python/engine/exchanges/tachibana_event.py` をここで新規作成する（EVENT WebSocket 受信ループ含む。FD 受信＋EC 受信の合流責務）。Phase 1 計画 ([docs/plan/tachibana/implementation-plan.md](../tachibana/implementation-plan.md)) には EVENT 受信ループは含まれていないため、本計画で初めて導入する依存関係に注意。

- [ ] **flowsurface に EC パーサが存在するか確認**: `c:/Users/sasai/Documents/flowsurface/exchange/src/adapter/tachibana.rs` で `EC` / `OrderEcEvent` / `_parse_ec_frame` 相当を grep
  - 存在 → 移植元として記録（[architecture.md §6](./architecture.md#6-event-ec-フレームのパースphase-o2) のキー表を flowsurface 実装に基づき更新）
  - 不在 → 次項へ
- [ ] **マニュアル PDF の入手**: `api_event_if_v4r7.pdf` / `api_event_if.xlsx` を立花証券 e支店 サポートサイト or 担当者経由で入手し `.claude/skills/tachibana/manual_files/` に同梱
- [ ] **最終手段（実 frame キャプチャ）は Phase 1 T2 完了後でないと不可**（EVENT WebSocket 接続には認証セッションが必要なため）。それ以前の段階では「PDF 入手」または「flowsurface 移植元の特定」のいずれかに限定する。Phase 1 T2 が完了している場合に限り、デモ環境で 1 件発注 → EVENT WebSocket の生 frame を tcpdump / Python で捕捉 → サンプル frame を `.claude/skills/tachibana/samples/event_ec_capture/` に複数パターン保存（受付・全部約定・部分約定・取消・失効・拒否）
- [ ] 結果を [open-questions.md Q5](./open-questions.md) に追記し、Q5 を「解決」マークする

### Tpre.6 受け入れ条件
- [ ] `cargo check --workspace` 成功
- [ ] Python pytest 既存スイート緑
- [ ] enum ラウンドトリップ網羅テスト緑
- [ ] **N2 シミュレーションテスト**: `nautilus_trader.model.orders.MarketOrder.create(...)` で生成した値の dict を `NautilusOrderEnvelope.model_validate(...)` で読めることを確認するスタブテスト 1 本（nautilus を実 import せず、ハードコードした dict を使う）
  **注**: Q0（nautilus バージョン固定方針）が Case C（CI 互換チェック）を採用しない場合、このテストは nautilus の型変更で陳腐化する。Q0 の決定（推奨: 案 A + C）を本 Phase 着手前に [open-questions.md Q0](./open-questions.md) に記録し確定すること
- [ ] **Q0 決定済み**: nautilus バージョンが pin され、採用する互換チェック方式（CI の有無）が open-questions.md に記録されていること
- [ ] **Tpre.5 EC 仕様根拠の所在が確定**（PDF 入手 / flowsurface 移植元の特定 / 生 frame サンプル いずれか 1 つ）
- [ ] **不変条件マッピング表の整備（D2-M1）**: `docs/plan/order/invariant-tests.md` を作成し、spec.md §6 の各不変条件 ID ↔ test 関数名の対応表を維持する（追加・修正があれば必ず本表を更新する運用を導入）

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
- [ ] modal キャンセル時は **未送信のまま HTTP 側で reject**（`Command::CancelOrder` は `client_order_id` を受け取る venue 注文取消用なので、第二暗証番号入力中の発注（venue 採番前）には使えない）。HTTP `/api/order/submit` は 403 + `reason_code="SECOND_PASSWORD_REQUIRED"` を即返す。venue 採番後の取消は `Command::CancelOrder { client_order_id }`（[T1.2](#t12-ipc-拡張) で導入）を使う
- [ ] **tkinter ログインダイアログには第二暗証番号フィールドを追加しない**（追加忌避: 閲覧専用ユーザーを締め出さない / メモリ滞留時間を短くする）

### T0.3 IPC スキーマ 1.3 — 発注最小セット

**注**: enum 列挙体・`SubmitOrderRequest` shape・新規 variant（`Command::SetSecondPassword` / `Command::ForgetSecondPassword` / `Event::SecondPasswordRequired`）は **Tpre.2 で凍結済み**。本タスクは「Tpre.2 で凍結済み variant の **ディスパッチ実装**」だけで、Rust DTO 定義・新規 variant 追加は一切行わない。

- [ ] Python `server.py` で `Command::SubmitOrder` を受信したら `tachibana_orders.submit_order(envelope, second_password)` にルーティング
- [ ] `order_type=MARKET` / `order_side=BUY` / `time_in_force=DAY` / `tags=["cash_margin=cash"]` / `trigger_type=null` / `post_only=false` / `reduce_only=false` 以外は **Phase O0 では `OrderRejected{reason_code="UNSUPPORTED_IN_PHASE_O0"}` で reject**（`TriggerType` は Phase O0/O1 で null 必須、O3 まで `LAST` 固定での実装は禁止）
- [ ] dispatch 層で `trigger_type != null` を `UNSUPPORTED_IN_PHASE_O0` で reject（観測点: `test_unsupported_phase_o0.py` 条件 (e)）
- [ ] `OrderSubmitted` → `OrderAccepted` の 2 段イベントを順番に発火（nautilus 流）。立花応答受領前に `OrderSubmitted`、`sOrderNumber` 採番後に `OrderAccepted`
- [ ] **`Command::SetSecondPassword` / `Command::ForgetSecondPassword` のディスパッチを Python 側で有効化**（enum 定義は Tpre.2 で凍結済み）。`String` で受信し、Python 側で即 `SecretStr` 化する（`SecretString` は IPC JSON に送れない）
- [ ] **`Event::SecondPasswordRequired { request_id }` の発火経路実装**（enum 定義は Tpre.2 で凍結済み）
- [ ] **`Command` enum の `Debug` 手実装は Tpre.2 で実施済み**のため、ディスパッチ実装時はマスクが自動的に適用されることを確認するだけでよい（再実装不要）

**受け入れテスト追加**:

- [ ] **第二暗証番号 idle forget テスト（D2-M3）**: `uv run pytest python/tests/test_second_password_idle_forget.py` — N 秒経過で状態クリアが起き、次発注で再度 `Event::SecondPasswordRequired` が発火すること
- [ ] **`UNSUPPORTED_IN_PHASE_O0` 境界テスト（C1 / D3-2）**: `uv run pytest python/tests/test_unsupported_phase_o0.py -v` — `pytest.mark.parametrize` で **3 条件 × 各 2 値以上（境界 + 1）** をカバー。条件: (a) `order_type` (MARKET 通過 / LIMIT 拒否 / STOP 拒否) / (b) `order_side` (BUY 通過 / SELL 拒否) / (c) `time_in_force` (DAY 通過 / IOC 拒否 / GTC 拒否) / (d) `tags` (`cash_margin=cash` 通過 / `cash_margin=margin` 拒否) / (e) `trigger_type` (null 通過 / `LAST` 拒否 / `BID` 拒否) / (f) `post_only` (false 通過 / true 拒否) / (g) `reduce_only` (false 通過 / true 拒否)。**`TriggerType != null` も必ず `UNSUPPORTED_IN_PHASE_O0` 発火**

**Notes**:
- `TachibanaCredentialsWire.second_password` は Phase 1 で `assert!(... is_none())` 強制済み（`From<&TachibanaCredentials> for TachibanaCredentialsWire` impl 内、参照: [`data/src/config/tachibana.rs`](../../../data/src/config/tachibana.rs)）。Order Phase で `Command::SetSecondPassword` 導入と同時に Phase 1 側の `#[deprecated]` 化を [docs/plan/tachibana/implementation-plan.md](../tachibana/implementation-plan.md) 側にも書き戻すこと（双方向リンク）

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
  - `p_no` = `p_no_counter.next()`（**`tachibana_helpers.PNoCounter` は Python `int` カウンタ（lock 不要）、初期値は Unix 秒（`int(time.time())`、×1000 ではない）**。Python asyncio は単一スレッドのため並行安全。`PNoCounter.next()` が `await` を含まない同期カウンタであることを確認すること）
  - `p_sd_date` = `tachibana_helpers.current_p_sd_date()`
  - `sCLMID` = `"CLMKabuNewOrder"`
  - `sJsonOfmt` = `"5"`
  - 逆指値関連デフォルト（Phase O0 では固定値）
- [ ] HTTP 送信:
  - `tachibana_url.build_request_url(session.url_request, payload)`
  - `httpx.post(url)` → Shift-JIS デコード → `check_response()`
  - 失敗時 `OrderRejectedError(code, message)` → 上で `Event::OrderRejected` に写る
- [ ] **第二暗証番号 idle forget タイマー（C-R2-H2 / B3R3-1）**: `python/engine/exchanges/tachibana_auth.py` に新規 `TachibanaSessionHolder` クラスを追加する。**asyncio idle timer + `second_password: SecretStr | None` をフィールド保持**。idle timer は **monotonic clock**（`asyncio.get_event_loop().time()` または `time.monotonic()`）で計測し、reset trigger は `Command::SubmitOrder` 受信時 / `Command::SetSecondPassword` 受信時の 2 つに限定する（その他の API 呼出では reset しない）。`second_password_idle_forget_minutes` 経過で自動 forget し、経過後の発注は再度 `Event::SecondPasswordRequired` を発火させる
- [ ] **第二暗証番号 lockout state（HIGH-R6-B1）**: `TachibanaSessionHolder` に「連続 SECOND_PASSWORD_INVALID カウンタ」+「lockout state」を実装。閾値は config `second_password_invalid_max_retries`（デフォルト 3）、抑止期間は `second_password_lockout_secs`（デフォルト 1800）。`p_errno=4` 受領時に `second_password = None` 即クリア + counter += 1。閾値到達で lockout state に遷移、`asyncio.get_event_loop().time()` ベースで `second_password_lockout_secs` 経過で解除。`SubmitOrder` 成功時に counter リセット
- [ ] **lockout 中の発注 reject（HIGH-R6-B1）**: dispatch 層で lockout state 中の `SubmitOrder` / `ModifyOrder` / `CancelOrder` を HTTP 423 + `reason_code="SECOND_PASSWORD_LOCKED"` で reject
- [ ] **WAL 復元 truncated 行スキップ（HIGH-R6-B1）**: WAL 復元（`OrderSessionState::new` もしくは Python 側）で末尾行に `\n` が無ければ truncated とみなしスキップ + structured log で WARN（partial 行スキップ規約、[architecture.md §4.2](./architecture.md#42-監査ログwal-write-ahead-log) 参照）
- [ ] **Phase 1 second_password ガード解除タスク（B3R3-4）**: 立花 Phase 1 で `Option<SecretString>` の存在禁止だった配線を Order Phase で解禁する:
  - `data/src/config/tachibana.rs::TachibanaCredentials` に **`with_second_password(self, second_password: Option<SecretString>) -> Self` builder を追加する方針を default とする**。`TachibanaCredentials::new` の引数追加経路は **禁止**（既存 call site `save_refreshed_credentials` / `update_session_in_keyring` の Phase 1 invariant を壊さないため）
  - `From<&StoredCredentials>` 内の `let _ = s.second_password;` を `second_password: s.second_password.map(SecretString::new)` に置換
  - `From<&TachibanaCredentials> for TachibanaCredentialsWire` impl の Phase 1 `debug_assert!` を**削除**し、代わりに「**idle forget 適用後の `None` 不変条件**」を assert する形に置き換える
  - `data/tests/tachibana_keyring_roundtrip.rs::test_phase1_second_password_guard_panics_in_debug` を rename / 反転（`Some(...)` で panic しないこと、idle forget 後に `None` であることを assert）
  - **`set_second_password_for_test` の扱い**: 撤去するか、または `with_second_password` builder の単体テストへ転用する（テストヘルパが本番 API より緩いシグネチャで残らないようにする）
  - **二重防衛**: 上記の Rust 側ガードと、Tpre.2 の IPC 入口 `deny_unknown_fields`（D3-1）を**両方残す**こと（どちらか単独では IPC 漏洩 / メモリ漏洩を防げないため）

- [ ] **`with_second_password` ポジティブテスト（D4-3）**: `cargo test -p flowsurface-data --test tachibana_credentials_wire_strips_second_password` — `TachibanaCredentials::with_second_password(Some(...))` で構築したインスタンスを Wire 変換した結果 `second_password=None` になることを assert（idle forget 後の不変条件 pin、ポジティブテスト）

- [ ] **`Submitted → Rejected` 即時遷移テスト（D3-3）**: `uv run pytest python/tests/test_submitted_to_rejected_immediate.py` — `p_errno=2` モック応答 → `Event::OrderSubmitted` → `Event::OrderRejected{reason_code="SESSION_EXPIRED"}` の順で **2 イベントが発火し `OrderAccepted` を経由しない**ことを assert。`_map_tachibana_state_to_nautilus()` の単体テストも併設すること

**受け入れテスト追加**:

- [ ] **`VENUE_UNSUPPORTED` 写像テスト（D4-2）**: `uv run pytest python/tests/test_venue_unsupported_mapping.py` — Python `submit_order` が `UnsupportedOrderError` を raise したケースで IPC 層が `Event::OrderRejected{reason_code="VENUE_UNSUPPORTED"}` に写ることを assert。`UNSUPPORTED_IN_PHASE_O0`（dispatch 層 reject）とは**別経路**であることを区別し、両 reason_code が混同されないことを test 名・assert で明示する

- [ ] **第二暗証番号 lockout テスト（HIGH-R6-D1）**: `uv run pytest python/tests/test_second_password_lockout.py` — SECOND_PASSWORD_INVALID 3 連投 → 4 回目が HTTP 423 + `reason_code="SECOND_PASSWORD_LOCKED"` で reject、1800 秒経過 (time-freeze) で解除されることを assert

- [ ] **第二暗証番号マスク横断 grep テスト**: `pytest` で `TachibanaWireOrderRequest(second_password='X')` を `repr / str / .model_dump() / .model_dump_json() / logging.getLogger().info(obj)` した結果すべてに `'X'` 文字が含まれないことを assert
- [ ] **session 切れ即停止テスト**: `p_errno=2` または HTTP 401 検知 → `OrderSessionState::Frozen` 遷移 → 後続 `/api/order/*` を即 503 + `reason_code="SESSION_EXPIRED"` を返す
- [ ] **Shift-JIS 受け入れテスト**: Shift-JIS 応答（ひらがな含むエラー文）→ `OrderRejectedError.message` が UTF-8 で正しく載ることを `pytest-httpx` で検証
- [ ] **仮想 URL マスクテスト**: `caplog` で `submit_order` 実行中のログを採取し `https://kabuka.e-shiten.jp` 以外のホスト名・`p_no=` 文字列が出ていないことを assert
- [ ] **URL masker 単体テスト（D2-L1）**: `uv run pytest python/tests/test_url_masker.py` — マスクヘルパ単体をパラメタライズで検証
- [ ] **`PNoCounter.peek()` 非使用 CI grep（B2-L2）**: `rg -nF '.peek()' python/engine/exchanges/tachibana_*.py | grep request` で 1 件も出ないこと（リクエスト経路で `peek()` を呼ばない不変条件を CI で守る）
- [ ] **`p_no` 単調増加 property test（D2-M4）**: `uv run pytest python/tests/test_p_no_counter_monotonic.py` — 再起動シミュレーション・time freeze 下で `PNoCounter.next()` が単調増加することを property test で検証
- [ ] **`expire_time_ns` 変換テスト（C5）**: `nautilus_trader` の `expire_time_ns`（UNIX nanoseconds）→ 立花 `sCLMID=CLMKabuNewOrder` の `sExpireDay`（`YYYYMMDD`）変換を検証。**`CLMDateZyouhou` マスタ（営業日カレンダー）が未取得時は HTTP 503 + `reason_code="INTERNAL_ERROR"`** を返すこと（マスタ未取得のまま立花 API に投げないことを assert）。T1.x の `modify_order` 経路でも同じ変換不変条件を pin する

### T0.5 Rust HTTP API `/api/order/submit`

- [ ] **`Cargo.toml` に `xxhash-rust` を追加**（`xxh3` feature を有効化）。`request_key` の `xxh3_64` で使用（[architecture.md §4.1](./architecture.md#41-request_key-の-canonicalization)）
- [ ] `src/api/order_api.rs` 新設
- [ ] 入力スキーマバリデーション（[spec.md §5](./spec.md#5-入力バリデーションrust-http-層)）
- [ ] `src/api/order_session_state.rs` 新設 — flowsurface [`src/api/agent_session_state.rs`](../../../../flowsurface/src/api/agent_session_state.rs) の `AgentSessionState` を **Rust → Rust で移植**:
  - `ClientOrderId` newtype
  - `try_insert(client_order_id, request_key, new_venue_order_id) -> PlaceOrderOutcome`
  - `Created` / `IdempotentReplay` / `Conflict` の 3 ケース
  - 立花差分: `venue_order_id`（= 立花 `sOrderNumber`）を `Option<String>` で持ち、`OrderAccepted` 受信後に `update_venue_order_id()` で埋める
- [ ] `engine_client.send(SubmitOrder)` → `OrderAccepted` / `OrderRejected` を待機
  **タイムアウト**: `tokio::time::timeout(Duration::from_secs(30), ...)` を必ず掛ける。タイムアウト時は HTTP 504 + `reason_code="INTERNAL_ERROR"`（[architecture.md §2.1 タイムアウト節](./architecture.md#21-発注同期)）
- [ ] HTTP 応答: 201 Created（新規）/ 200 OK（idempotent replay）/ 409 / 400 / 403 / 502 / 504（タイムアウト）

### T0.6 安全装置

- [ ] 起動 config に `tachibana.order.max_qty_per_order` / `max_yen_per_order` / `require_confirmation`
- [ ] **rate limit config 実装**: `tachibana.order.rate_limit_window_secs=3` / `rate_limit_max_hits=2` を config キーとして実装し、超過時は HTTP 429 + `reason_code="RATE_LIMITED"` を返す
- [ ] config 未設定時は `/api/order/submit` を 503 で reject（明示 opt-in、誤発注防止）
- [ ] Python `tachibana_url` で本番 URL 検出時、`os.getenv("TACHIBANA_ALLOW_PROD") != "1"` なら send をブロック

**受け入れテスト追加**:

- [ ] **誤発注ガード回帰テスト（必須）**:
  - Rust: `max_qty_per_order` 超 → 400/422 reject、`max_yen_per_order` 超 → 400 reject、未設定 → 503 を返す pytest/cargo test
  - 同一 `client_order_id` で N 並列リクエスト → 1 件のみ発注される連打耐性 integration test
- [ ] **REPLAY skip テスト**: `config.mode == REPLAY` のとき `/api/order/*` は 503 + `reason_code="REPLAY_MODE_ACTIVE"` で即 reject する Rust 単体テスト
- [ ] **rate limit 連打抑止テスト（D2-M2 / D3-4）**: `cargo test --test order_rate_limit` — **4 ケースにパラメタライズ**: (a) N 件目までは通る / (b) N+1 件目が **HTTP 429** + `reason_code="RATE_LIMITED"` / (c) `rate_limit_window_secs` 経過後 counter が reset され再度通る / (d) `(instrument_id, side, qty, price)` のいずれかが不一致なら**別カウンタ**として独立にカウントされる（`RATE_LIMITED` の HTTP ステータスは **429** に統一、503 ではない）
- [ ] **仮想 URL マスクテスト**: `caplog` で `submit_order` 実行中のログを採取し `https://kabuka.e-shiten.jp` 以外のホスト名・`p_no=` 文字列が出ていないことを assert（T0.4 と同じ観点で HTTP 層側からも確認）

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
  - **canonicalization テスト（D2-L2）**: `cargo test --test request_key_canonical` — 3 パラメタ（`tags` 順序入替 / `null` ↔ `""` / 制御文字エスケープ後同一）で同一 hash になることを assert。観測点: `xxh3_64(canonical_bytes)` の出力一致
- [ ] **WAL 冪等再送テスト**: 同一論理リクエスト（tags 順序違い）の 2 連投 → 1 件 Created + 1 件 IdempotentReplay (200) を Rust integration test で確認
- [ ] **WAL 第二暗証番号漏洩 grep テスト（D2-H2）**: `uv run pytest python/tests/test_audit_log_no_secret.py` — WAL `.jsonl` 全行を grep して第二暗証番号の値文字列が含まれないこと、および C-L4 制御文字エスケープが効いて `\n` / `\t` / `\x01-\x03` が生のまま出力されないことを assert
- [ ] **WAL truncation 復元テスト（HIGH-R6-D2）**: `cargo test -p flowsurface-engine-client --test wal_restore_truncated_line` — WAL 末尾行が `\n` 欠落の状態で `OrderSessionState` 復元を実行し、当該行が skip + WARN ログが出ることを assert

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
- [ ] `tachibana_orders.modify_order` / `cancel_order` / `cancel_all_orders` / `fetch_order_list`
  - 関数名は **nautilus 抽象** に統一（`modify_order` / `cancel_order` / `cancel_all_orders` / `fetch_order_list`。`fetch_order_list` は nautilus 名そのまま）。内部で立花 `CLMKabuCorrectOrder` 等を呼ぶ
- [ ] flowsurface の `CorrectOrderRequest` / `CancelOrderRequest` / `OrderListRequest` を pydantic で移植（型名は `TachibanaWireModifyRequest` 等にリネーム — T0.4 の `Wire` prefix 規約に統一）
- [ ] レスポンス型 `ModifyOrderResponse` / `OrderListResponse` 移植

### T1.2 IPC 拡張
- [ ] `Command::ModifyOrder` / `CancelOrder` / `CancelAllOrders` / `GetOrderList`
- [ ] `Event::OrderListUpdated` / `OrderPendingUpdate` / `OrderPendingCancel`

### T1.3 Rust HTTP
- [ ] `/api/order/modify` `/api/order/cancel` `/api/order/cancel-all` `/api/order/list`
- [ ] `cancel-all` は確認モーダル必須（HTTP 層では **JSON body に `confirm: true` を必須**とする。query param ではない。[spec.md §4](./spec.md#4-公開-apihttp) に準拠）
- [ ] `/api/order/cancel` の Rust 実装では `OrderSessionState.get_venue_order_id(client_order_id)` で lookup し、`venue_order_id` を Python `cancel_order(...)` に渡すこと（[architecture.md §2.3](./architecture.md#23-取消フローphase-o1)）。`venue_order_id = None`（unknown）は 404 reject
- [ ] **`cancel-all` の `confirm` フィールド検証はテーブルテスト化**（body 欠落 / `confirm: false` / `confirm: "true"`（文字列）すべて 400 reject）

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
- [ ] **session 切れ即停止テスト**: `p_errno=2` または HTTP 401 検知 → `OrderSessionState::Frozen` 遷移 → 後続 `/api/order/*` を即 503 + `reason_code="SESSION_EXPIRED"`
- [ ] 手動 E2E: `s81_order_modify_cancel_demo.sh`

**Exit 条件**: デモ環境で「指値発注 → 訂正 → 取消」が UI から完結。

---

## Phase O2: EVENT EC 約定通知

### T2.1 EC パーサ + `tachibana_event.py` 実装本体
- [ ] **`tachibana_event.py` を Tpre.5 で新規作成済み**（EVENT WebSocket 受信ループ含む。FD 受信＋EC 受信の合流責務）。本タスクではその上に EC パース実装を載せる。Phase 1 計画 ([docs/plan/tachibana/implementation-plan.md](../tachibana/implementation-plan.md)) との依存関係: Phase 1 の認証セッション（`tachibana_auth.py`）が前提
- [ ] `tachibana_event.py._parse_ec_frame(items) -> OrderEcEvent`
- [ ] 主要項目（[architecture.md §6](./architecture.md#6-event-ec-フレームのパースphase-o2)）の写像
- [ ] **EVENT URL sanitize（C-R2-L1）**: EVENT URL 構築時に `\n` / `\t` / `\x01-\x03` 等の制御文字を **reject**（除去ではなく reject に統一）し、ログ・WS 接続先文字列に紛れ込まないようにする
- [ ] **EVENT URL sanitize 受け入れテスト（D4-4）**: `uv run pytest python/tests/test_event_url_sanitize.py` — `\n` / `\t` / `\x01-\x03` を含む URL 構築入力が **reject** されること（除去ではなく reject に統一、silent strip 禁止）を assert
- [ ] **マニュアル現物確認**: `api_event_if_v4r7.pdf` または `api_event_if.xlsx` から EC フィールド一覧を抽出（[docs/plan/tachibana/inventory-T0.md](../tachibana/inventory-T0.md) §11 と同じ理由で、PDF 同梱がなければ実 frame キャプチャに切替）

### T2.2 IPC イベント拡張
- [ ] `Event::OrderFilled` / `OrderCanceled` / `OrderExpired`（既に O0 で骨格があれば拡張）
- [ ] **`OrderPartiallyFilled` は持たない**: nautilus 流に `OrderFilled` の `leaves_qty` で部分/全部を判定する。詳細は [architecture.md §3](./architecture.md#3-ipc-スキーマ拡張schema-12--13) 末尾

### T2.3 重複検知
- [ ] `tachibana_event.py` に `_seen: set[tuple[VenueOrderId, TradeId]]` を持つ（**EC 重複検知キーは `(venue_order_id, trade_id)`** に統一。nautilus 用語）
- [ ] 当日リセット（夜間閉局検知時）

### T2.4 Rust UI 反映
- [ ] notification toast（既存通知機構を使う、なければ簡易バナー）
- [ ] 注文一覧パネルの行更新

### T2.5 テスト
- [ ] Python: 実 frame サンプル（または合成）でパース → 期待 IPC イベント
- [ ] Python: `(venue_order_id, trade_id)` キーの重複検知
- [ ] **EC 重複検知 E2E**: 再接続を fault-injection で発生させ、同一 EC frame を 2 度受信しても `Event::OrderFilled` が 1 度しか発火しないこと
- [ ] **EC state-machine テスト（D2-L3）**: 拒否 / 失効 / 部分→全部 の遷移順序を assert する state-machine テスト。実行コマンド: `uv run pytest python/tests/test_ec_state_machine.py -v`
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
- [ ] **nautilus 互換境界 lint テスト**: `engine-client/src/dto.rs` と `python/engine/schemas.py` を grep して `sCLMID` / `p_sd_date` / `Zyoutoeki` 等の立花固有禁止語が一致しなければ CI fail する Rust + Python の禁止語 lint テストを追加（IPC / Rust UI 層に立花用語が漏れないことを継続保証）
  - **CI 組込（D2-H1）**: `.github/workflows/*.yml` に `cargo test -p flowsurface --test nautilus_boundary_lint` と `uv run pytest python/tests/test_nautilus_boundary_lint.py` を追加し PR ゲートにする。spec.md §6 の禁止語 set（`sCLMID` / `p_sd_date` / `Zyoutoeki` / `p_no` / `p_eda_no`）を test 内に列挙する
- [ ] **不変条件マッピング doc 整合性テスト（D3-5）**: `uv run pytest python/tests/test_invariant_tests_doc.py` — `docs/plan/order/invariant-tests.md` に **spec.md §6 の不変条件 ID（A-H2 / C-H1 / C-H4 / C-M3 / C-R2-M3 等）が全件登場**していること、および**各行に紐付く test 関数名が `rg` で実在**することを assert する（マッピング表が陳腐化したら CI が落ちる運用）

## nautilus N2 移行時に行う作業（参考・本計画スコープ外）

[architecture.md §10.6](./architecture.md#106-nautilus-移行時の差分n2-で実施する作業のみ) の通り、本計画完了時点で型互換が完全に取れていれば、N2 で行うのは:

1. `pyproject.toml` に `nautilus_trader` を追加
2. `python/engine/nautilus/clients/tachibana.py` を新設（`LiveExecutionClient` 継承）し中身は `tachibana_orders.submit_order(...)` を呼ぶだけ
3. `_envelope_to_wire` を `NautilusOrderEnvelope` の代わりに本物の `nautilus_trader.model.orders.Order` を受けるよう型注釈だけ書き換え（field アクセス互換のため動作変更なし）

**本計画のコードは削除しない**。HTTP API `/api/order/*` も nautilus 経路と並行して残す（手動発注・curl 経路の維持）。
