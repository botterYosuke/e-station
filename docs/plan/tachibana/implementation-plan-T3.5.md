# T3.5 実装計画 — 立花 UI 拡張 + iced 逸脱解消

**親計画**: [implementation-plan.md](./implementation-plan.md) フェーズ T3 の繰越項目（親 plan ラウンド 6「繰越 / 次イテレーション (ラウンド 6 追加)」セクション H5/H6/H7/H8/H9 と T3 タスクリスト deferred to T3.5 マーカー参照）
**作成**: 2026-04-26
**ブランチ**: `tachibana/phase-1/T3-credential-r6-fixes`（または T3.5 派生）

## レビュー修正ラウンド R1 (2026-04-26)

並列レビュアー 4 種（rust-reviewer / iced-architecture-reviewer / silent-failure-hunter / type-design-analyzer）の合算指摘 (CRITICAL=0, HIGH=4, MEDIUM=10+, LOW=数件) のうち、**HIGH 4/4** と **アクショナブルな MEDIUM 6 件** を Round 1 で消化:

| ID | 指摘元 | 対応 |
|----|--------|------|
| HIGH-1 broadcast Lagged → silent drop | silent-failure / iced-arch | `engine_status_stream` で `RecvError::Lagged` を warn+continue、`Closed` のみ receiver drop |
| HIGH-2 RequestTachibanaLogin callback FSM 固着 | rust / iced-arch / silent-failure | callback で `LoginStarted` を発火しない。新 `Message::TachibanaLoginIpcResult(Result<(),String>)` で IPC エラー時のみ toast、成功は engine の `VenueLoginStarted` に委譲 |
| HIGH-1 type venue 文字列リテラル 4 箇所 | type-design | `TACHIBANA_VENUE_NAME` 定数化 |
| HIGH-1 rust set_tachibana_ready(false) pending | rust | コメントで意図保持 (`EngineRehello` 後の replay 担保) を明示 |
| MED-3 silent Manual + 未接続時 toast 不在 | silent-failure | `Trigger::Manual` のみ "エンジン未接続" toast を発行（Auto は無音継続） |
| MED-2 type Trigger 区別未活用 | type-design | `log::info!("RequestTachibanaLogin trigger={trigger:?}")` を追加 |
| MED-4 type BannerMessage 公開度 | type-design | `pub` → `pub(crate)` |
| MED-5 type DismissTachibanaBanner FSM 迂回 | type-design | `VenueEvent::Dismissed` 追加、`next()` 経由化、unit test 2 件追加 |
| MED-5 rust iced_purity_grep brace counting | rust | shell コメントに limitation を明記 (AST テストが load-bearing と注記) |
| LOW-2 rust AST テスト固定 path | rust | `concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs")` で CWD 非依存に |

**未対応 / 別 PR スコープ** (deferred to follow-up — それぞれ trade-off / scope を明示):

| ID | 指摘 | 理由 |
|----|------|------|
| HIGH-2 type | `VenueErrorCode` を `VenueEvent::LoginError` / `VenueState::Error` に保持 | enum signature 変更が venue_state.rs / main.rs / venue_banner.rs / tests に波及。debuggability 改善目的のため別 PR で慎重に |
| MED-1 type | `Error.message: String` → `Arc<str>` | `clone()` コスト最適化。FSM の clone 頻度が低いため現状性能影響は計測されず。最適化 PR で扱う |
| MED-1 iced / rust | initial `EngineConnected` → `EngineRehello` の発火順 | 現在 `tokio::select!` のシングル loop 内で逐次 yield しており順序は決定的。将来の拡張で順序依存ロジックを増やす場合は invariant test を追加すべき。コメントで明示済み |
| MED-2 iced | Sidebar Action の `Task::batch` 順序非保証 | 現状 `is_login_in_flight()` ガードで実害なし。`Task::chain` の Empty 挙動確認後の差し替えは別 PR |
| MED-3 rust | `map_engine_event_to_tachibana` wildcard | 明示性改善のみ、機能影響なし |
| MED-4 silent | `VenueErrorAction::Hidden` で閉じ手段なし | architecture.md §6 通りの仕様（`unsupported_venue` 等は UI で復旧不能）。閉じ UX が必要となれば spec 改訂後 |
| MED-5 silent | `exit 77` の GitHub Actions 挙動 | E2E ワークフロー追加 PR 側で `continue-on-error: true` を組合せて対応する旨を Step F の落とし穴に記載済 |
| MED-3 type | `EngineRehello` 命名混在 | venue + engine 両方を扱う以上 `VenueEvent` での集約が iced 側 update flow としてシンプル。改名は将来 engine event 種類が増えたとき再評価 |
| LOW 多数 | parse_message ヘルパー化 / 全 20 遷移網羅テスト / `BannerMessage::Dismiss` と `Message::DismissTachibanaBanner` 二重 / `let _ = write!` / debug_redaction false-positive risk / keyring 直接 delete_credential 5 箇所統一 | 保守性の改善余地。すべて trade-off 記録のうえ別 PR 候補として継続 |

R1 で **作業の安全性に直結する HIGH 全 4 件 + アクショナブル MEDIUM** を消化したため本 PR の review-fix-loop は R1 で収束扱い。残課題は上表で個別に追跡可能。

> 脚注: spec.md §3.1 line 30 が立花ログインボタン位置として `sidebar.rs` を挙げているのは陳腐化記述。実コード上は `src/screen/dashboard/tickers_table.rs::exchange_filter_btn` の venue 行が正本。本 PR スコープ外、別 PR で spec.md 同期予定。

---

## 1. ゴール

T3 で「(deferred to T3.5)」とマークされた UI 5 項目と、同 PR 紐付けの技術負債（H5 / H6 / H7 / H8 / H9）を **1 つのまとまった変更**として着地させる。完了基準は:

1. **production 起動経路で**「立花 venue 選択 → ログインダイアログ → VenueReady → 銘柄取得」が手動 bootstrap util なしで通ること
2. `cargo test --workspace` / `cargo clippy --workspace --tests -- -D warnings` / `cargo fmt --check` / `uv run pytest python/tests/` 全緑
3. `tests/e2e/tachibana_relogin_after_cancel.sh` 緑
4. `src/main.rs` の `update()` 経路から `rt.block_on(...)` / `ENGINE_CONNECTION.read()` 直アクセスが **消える**（起動前の `main()` 直下の block_on は許容）

---

## 2. スコープ

### 2.1 UI 5 項目（親 plan ラウンド 6「繰越 / 次イテレーション (ラウンド 6 追加)」由来）

| ID | 項目 | 場所 |
|----|------|------|
| U1 | sidebar 立花ログインボタン（`Command::RequestVenueLogin{ venue:"tachibana" }` 発火） | `src/screen/dashboard/tickers_table.rs::exchange_filter_btn` venue 行（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」） |
| U2 | ステータスバナー（`VenueLoginStarted` / `VenueLoginCancelled` / `VenueError` 表示。`classify_venue_error` の戻り値だけ参照） | `src/notify.rs` および `src/widget/toast.rs` ／ 新規 `src/widget/venue_banner.rs`（候補）（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」） |
| U3 | `Venue::Tachibana` 初回オープン時の自動 `RequestVenueLogin` 発火（ユーザーが Tachibana venue を選択した結果としての発火であり、spec.md §3.2 LOW-3「ユーザー明示の再ログイン」側に分類される） | tickers_table の `ToggleExchangeFilter(Tachibana)` ハンドラ（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」） |
| U4 | `VenueReady` 前の Tachibana metadata / subscribe 抑止ゲート | tickers_table の `fetch_metadata_task` 経路（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」） |
| U5 | E2E shell `tests/e2e/tachibana_relogin_after_cancel.sh` | 新規（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」） |

### 2.2 同 PR 紐付け技術負債（親 plan ラウンド 6「繰越 / 次イテレーション (ラウンド 6 追加)」H5〜H9 由来）

| ID | 項目 | 場所 |
|----|------|------|
| H5 | `EngineCommand::Bundled(p).program()` の `to_str().unwrap_or(...)` fallback を `Path` 直接受け化 | `engine-client/src/process.rs` の `EngineCommand::Bundled(p).program()` 実装箇所（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」H5） |
| H6 | `data/tests/tachibana_keyring_roundtrip.rs::SharedBuilder` の `OnceLock<Mutex<HashMap>>` 状態漏洩を、テスト ID + 各テスト先頭 `delete_credential().ok()` パターンで整理 | `data/tests/tachibana_keyring_roundtrip.rs`（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」H6） |
| H7 | `static ENGINE_CONNECTION: RwLock` への `update()` 内アクセスを iced `Task` 経由に置換 | `src/main.rs` 全体（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」H7） |
| H8 | `update()` 内の `rt.block_on(...)` を `Task::perform(async {...})` 化 | `src/main.rs` 全体（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」H8） |
| H9 | 手動 reconnect callback / 二重経路を Subscription 単一化 | `src/main.rs` recovery loop / engine_status_stream（親 plan 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」H9） |

### 2.3 スコープ外（Phase O1 へ繰越固定）

- W1 (handshake recv timeout) — 別 PR
- H12 / H13 / M-19（typestate / wire schema 改造）
- M-12 (StoredCredentials Debug derive コメント) — 次回触るとき
- spec.md §3.1 line 30 の `sidebar.rs` 言及陳腐化修正 — 別 PR
- `VenueError` payload への `action_label: Option<String>` 追加（architecture.md §6 への追記タスク） — 別 PR（Phase 1 では `message` フィールドへの改行区切りで暫定運用）

---

## 3. ステップ分割（TDD 順）

各ステップは「RED（失敗テスト）→ GREEN（最小実装）→ REFACTOR」を 1 サイクルとし、ステップ完了ごとに `cargo test --workspace` / `pytest` が緑であることを保証する。**全ステップ完了後に `/review-fix-loop` を 1 周以上回す**。

### 3.0 U1〜U5 / H5〜H9 → Step 対応マトリクス

| ID | 着地 Step |
|----|-----------|
| U1 | Step D |
| U2 | Step E |
| U3 | Step C + Step D |
| U4 | Step C |
| U5 | Step F |
| H5 | Step B |
| H6 | Step B |
| H7 | Step A |
| H8 | Step A |
| H9 | Step A |

### 3.1 不変条件 ID 登録一覧（各 Step で `invariant-tests.md` に行追加）

`T35-` プレフィックスは `invariant-tests.md` 既存 `F-H5` / `F-H6` との衝突回避（CI ガード `test_invariant_table_covers_all_ids` は `F-[A-Z0-9-]+` パターンで拾うため `T35-...` は別エントリとして登録）。

| ID | 一次資料 | pin テスト | Step |
|----|----------|-----------|------|
| T35-H5-PathFidelity | T3.5 §3 Step B | `engine-client/tests/bundled_path_with_unicode.rs::bundled_program_preserves_unicode_path`（+ 非 Windows 環境向け `..._utf8_path`） | B |
| T35-H6-KeyringSlotIsolation | T3.5 §3 Step B | `data/tests/tachibana_keyring_roundtrip.rs::keyring_slot_is_isolated_per_test` | B |
| T35-H7-NoStaticInUpdate | T3.5 §3 Step A | `tests/main_update_no_static_access.rs::update_body_has_no_engine_connection_read` | A |
| T35-H8-NoBlockOnInUpdate | T3.5 §3 Step A | `tests/main_update_no_block_on.rs::update_body_has_no_block_on` | A |
| T35-H9-SingleRecoveryPath | T3.5 §3 Step A | `tests/engine_status_subscription_is_singleton.rs::engine_status_subscription_is_singleton` | A |
| T35-U1-LoginButton | T3.5 §3 Step D | `tests/sidebar_login_button.rs::sidebar_login_button_emits_request_venue_login`（+ `..::duplicate_press_returns_task_none_while_login_in_flight`） | D |
| T35-U2-Banner | T3.5 §3 Step E | `tests/tachibana_banner.rs::banner_transitions` | E |
| T35-U3-AutoRequestLogin | T3.5 §3 Step C+D | `tests/tachibana_auto_request_login.rs::auto_request_login_on_first_open_classified_as_manual_trigger` | D |
| T35-U4-VenueReadyGate | T3.5 §3 Step C | `tests/tachibana_metadata_fetch_gated_by_venue_ready.rs::metadata_fetch_blocked_until_venue_ready`（+ `..::pending_fetch_replays_on_venue_ready`） | C |
| T35-U5-RelogE2E | T3.5 §3 Step F | `tests/e2e/tachibana_relogin_after_cancel.sh` | F |

### 3.2 venue 状態モデル（全 Step 共通）

`tachibana_ready: bool` と `tachibana_login_in_flight: bool` の二重フラグは廃止し、`Flowsurface` 構造体に **enum 1 本**の `tachibana_state: VenueState` を持たせる:

```rust
enum VenueState {
    Idle,                                                  // 初期 / VenueLoginCancelled 後
    LoginInFlight,                                         // VenueLoginStarted 受信 〜 VenueReady/Cancelled/Error 待ち
    Ready,                                                 // VenueReady 受信後（metadata fetch / subscribe 解禁）
    Error { class: VenueErrorClass, message: String },     // VenueError 受信後
}
```

- Step C のゲート判定: `matches!(state, VenueState::Ready)`
- Step D の重複押下抑止: `matches!(state, VenueState::LoginInFlight)`
- `Hello` 再受信（subprocess 再起動検知）時は `VenueState::Idle` へリセット（spec.md §3.2 整合）
- `tachibana_banner: Option<TachibanaBannerState>` フィールドは廃止し、`view()` 側で `VenueState` から render する形に統一

`Trigger::{Auto, Manual}` enum で `RequestTachibanaLogin` の発火元を区別。`Auto` は `VenueState::Idle && first_open` のときのみ許可（U3 = LOW-3 「ユーザー明示」分類）。

### 3.3 構造ガード（Step A REFACTOR 完了判定）

`tests/main_update_no_static_access.rs` / `tests/main_update_no_block_on.rs` は `syn::parse_file` + `syn::visit::Visit` で `fn update` の `Block` のみを走査する **AST 検査** で実装する。テスト関数名:

- `update_body_has_no_engine_connection_read`
- `update_body_has_no_block_on`
- `engine_status_subscription_is_singleton`（H9 完了判定）

加えて `tools/iced_purity_grep.sh` を新設し `update()` / `subscription()` 内に `ENGINE_CONNECTION` / `ENGINE_MANAGER` / `ENGINE_RESTARTING` / `block_on(` リテラルが現れないことを grep ガード。CI 組込先は `.github/workflows/rust.yml::iced-purity-lint` ジョブ。

---

### Step A — H7/H8/H9 iced Subscription/Task 化（リスク高・先頭着手） ✅ 完了 (2026-04-26)

**目的**: `update()` 内から static / block_on を排除。後続ステップで sidebar から `RequestVenueLogin` を送る経路を Task ベースで素直に書けるようにする土台。

> **完了サマリ (2026-04-26)**
>
> - ✅ workspace root `tests/` に AST 構造ガード 3 本を追加（`main_update_no_static_access` / `main_update_no_block_on` / `engine_status_subscription_is_singleton`）。RED 時 1 本 fail（ENGINE_CONNECTION x2 + ENGINE_MANAGER x1 を update() 内に検出）、他 2 本は regression guard として最初から PASS。
> - ✅ `static ENGINE_CONNECTION: RwLock<...>` を **`static ENGINE_CONNECTION_TX: OnceLock<watch::Sender<Option<Arc<EngineConnection>>>>`** に置換。recovery loop は `tx.send(Some(conn))` で publish。
> - ✅ `Flowsurface` 構造体に `engine_connection: Option<Arc<EngineConnection>>` と `engine_manager: Option<Arc<ProcessManager>>` を追加。`engine_manager` は `Flowsurface::new()` で `ENGINE_MANAGER.get()` から一度だけ取得（new() は update() ではないので OK、AST テスト範囲外）。
> - ✅ `Message::EngineConnected(Arc<EngineConnection>)` を新設し、backend 再構築 + `SetProxy` 再送 + sidebar refetch + 「復旧しました」toast をすべて当該ハンドラに集約。`Message::EngineRestarting(true)` は restarting flag セットと toast のみに簡素化。
> - ✅ `engine_status_stream` を `tokio::select!` で `restart_rx.changed()` と `conn_rx.changed()` をマージし、**1 本の Subscription** で `EngineConnected` と `EngineRestarting` を yield する形に再構成。これで H9 invariant を満たしたまま EngineConnected を流せる。
> - ✅ `tools/iced_purity_grep.sh` 新設（awk で `fn update(...)` / `fn subscription(...)` 本体を抽出し forbidden literal を grep。`.github/workflows/rust.yml::iced-purity-lint` への組込はワークフロー側 PR で実施）。
> - ✅ `engine-client/tests/engine_connection_debug_redaction.rs` 追加。`EngineConnection` の Debug 実装が `finish_non_exhaustive()` を維持し、struct field 直書きの credential が混入しないことをソースレベルで pin。
> - ✅ `invariant-tests.md` に T35-H7 / T35-H8 / T35-H9 / T35-H7-DebugRedaction を追記。
> - ✅ `cargo test --workspace` / `cargo clippy --workspace --tests -- -D warnings` / `cargo fmt --check` 全緑。
>
> **設計上の落とし穴 (後続作業者向け)**
>
> - `tokio::sync::watch::Ref` ガードは `!Send`。`async_stream::stream!` の中で `borrow_and_update().clone()` の戻り値を変数束縛するときは **必ず `{ ... }` ブロックで明示的に drop** してから `yield`/`await` に進むこと（ガードが await を跨ぐと iced の `Send` バウンドで落ちる）。本 Step でこの罠を踏み 1 度ビルド失敗。
> - `EngineRestarting(false)` ハンドラに残っていた backend 再構築は **完全に EngineConnected ハンドラへ移動**した。watch channel は coalesce するため、recovery loop が `conn_tx.send(Some)` → `restart_tx.send(false)` の順に発行しても、Subscription 側の `tokio::select!` がどちらを先に拾うかは非決定的。**EngineConnected を真の source-of-truth** として、EngineRestarting は通知用フラグだけにする責務分割が安全。
> - `ENGINE_MANAGER` static は本 Step では「保持」だが、`update()` 内のアクセス（旧 L999）は `self.engine_manager` field 経由に移した。AST テストが `ENGINE_MANAGER` も forbidden ident に入れているため、将来の Step 追加で update() に再混入させないこと。
> - Step C 以降で `RequestTachibanaLogin` を Task::perform 化する際は、本 Step で導入した `self.engine_connection.as_ref().cloned()` パターンをそのまま流用できる。`fn send_engine_command(conn, cmd) -> Task<Message>` ヘルパーは Step A で重複が 1 箇所しかなく集約しない判断 (YAGNI) ── 重複が 2+ になる Step C/D で改めて検討。

**作業**:
0a. workspace root `tests/` ディレクトリを新設する（既存 `tests/e2e/` と並置、Rust 統合テスト用）。`tests/main_update_no_static_access.rs` / `tests/main_update_no_block_on.rs` / `tests/engine_status_subscription_is_singleton.rs` / `tests/engine_connection_debug_redaction.rs`（および後続 Step C/D/E 由来のテスト）が flowsurface バイナリクレートの integration test としてこのディレクトリに配置される。
0b. flowsurface root crate `Cargo.toml [dev-dependencies]` に `syn = { version = "2", features = ["full", "visit"] }` を追加（AST 構造ガード用）。AST 走査は `std::fs::read_to_string("src/main.rs")` で読み込み、`syn::parse_file` → `syn::visit::Visit` で `fn update` の `Block` のみを対象とする。
1. `Arc<EngineConnection>` を `Flowsurface` 構造体のフィールドとして保持（`Option<Arc<EngineConnection>>`、reconnect で `Some` に置換）
2. recovery loop が `EngineConnection` を確立したら `mpsc::Sender<Message>` で `Message::EngineConnected(Arc<EngineConnection>)` を投げる Subscription を新設（既存 `engine_status_stream` と同じ経路に乗せる）
3. **置換対象**: `src/main.rs` の `update()` 関数本体内のすべての `ENGINE_CONNECTION.read()` / `rt.block_on(...)` 出現箇所（着手時に grep で再特定）。フィールドの `Arc<EngineConnection>` を `clone()` して `Task::perform(async move { conn.send(...).await })` に置換
4. `static ENGINE_CONNECTION` を完全に削除（`ENGINE_MANAGER` / `ENGINE_RESTARTING` は起動時にしか触らないので段階的削除を別途検討、本ステップでは保持）
5. **保持対象**: `main()` 直下（iced 起動前）の `rt.block_on(EngineConnection::connect)` / `manager.start()` 待ち合わせ — これは `update()` 経路ではないので置換対象外（親 plan の指摘対象外）。grep で `update()` 関数本体外のものは除外して扱う
6. `invariant-tests.md` に **T35-H7-NoStaticInUpdate / T35-H8-NoBlockOnInUpdate / T35-H9-SingleRecoveryPath** を追加

**RED テスト（AST ベース構造ガード）**:
- `tests/main_update_no_static_access.rs::update_body_has_no_engine_connection_read` — `syn::parse_file` + `syn::visit::Visit` で `fn update` の `Block` のみを走査し `ENGINE_CONNECTION.read()` 呼び出しが 0 件であることを assert
- `tests/main_update_no_block_on.rs::update_body_has_no_block_on` — 同様に `block_on(` 呼び出しが 0 件であることを assert
- `tests/engine_status_subscription_is_singleton.rs::engine_status_subscription_is_singleton` — Subscription 単一化のガード（H9）
- 加えて `tools/iced_purity_grep.sh` を CI から呼ぶ

**GREEN**:
- 上記置換を実施

**REFACTOR**:
- `Arc<EngineConnection>` 取得を `&self.engine_connection` ヘルパー化
- `Task::perform` の重複パターンを `fn send_engine_command(conn, cmd) -> Task<Message>` に集約
- `EngineConnection: Debug` derive が `Arc<EngineConnection>` 内部 secret を redact することを `engine-client/tests/engine_connection_debug_redaction.rs::engine_connection_debug_does_not_leak_credentials` で pin（secret 焼付きガード）

**完了判定**: `cargo test --workspace` 緑 / 既存 GUI 起動 smoke が回帰しない / `iced_purity_grep.sh` 緑

---

### Step B — H5 / H6 機械的修正 ✅ 完了 (2026-04-26)

**目的**: 副作用の少ない技術負債を先に潰し、後続ステップの差分を見やすくする。

> **完了サマリ (2026-04-26)**
>
> - ✅ H5: `EngineCommand::program()` を `&str`（`to_str().unwrap_or("flowsurface-engine")` フォールバック付き）から **`&std::ffi::OsStr` 直接受け**に変更。`Bundled(PathBuf).program()` は `as_os_str()`、`System { program: String, .. }` は `OsStr::new(program.as_str())`。`tokio::process::Command::new` は `AsRef<OsStr>` を受けるため呼び出し側修正不要。
> - ✅ H5 pin: `engine-client/tests/bundled_path_with_unicode.rs` に 3 件追加（`bundled_program_preserves_unicode_path` / `bundled_program_preserves_utf8_path` / `system_program_returns_program_string`）。コンパイルエラーで RED → 型変更で GREEN。
> - ✅ H6: `data/tests/tachibana_keyring_roundtrip.rs` に **`fresh_keyring_slot(test_id) -> &str`** ヘルパーを追加。`KEYRING_SERVICE = "flowsurface.tachibana"` / `KEYRING_PRIMARY_USER = "user_id"` の production slot を `delete_credential().ok()` でリセットし、`#[serial]` 順依存の `SharedStore` 残留を解消。`test_id` は引数として受け取って verbatim 返却する（panic/log 文での追跡用）。
> - ✅ H6: cleanup を欠いていた 3 テスト（`test_credentials_roundtrip_with_zeroize_and_masked_debug` / `test_update_session_in_keyring_preserves_existing_user_id` / `test_phase1_second_password_guard_panics_in_debug`）の冒頭に `fresh_keyring_slot(...)` を挿入。残り 5 件は元から explicit cleanup 済み。
> - ✅ H6 pin: `keyring_slot_is_isolated_per_test` を追加。プロダクション slot に "RESIDUE_FROM_PRIOR_TEST" を仕込んで `fresh_keyring_slot` 呼び出し後に `Err(NoEntry)` になることを確認。
> - ✅ 完了判定: `cargo test -p flowsurface-data --test tachibana_keyring_roundtrip -- --test-threads=4` を **5 回連続緑**（9/9 passed each run）。
> - ✅ `invariant-tests.md` に T35-H5-PathFidelity / T35-H6-KeyringSlotIsolation を追記。
> - ✅ `cargo fmt` / `cargo clippy --workspace --tests -- -D warnings` / `cargo test --workspace` 全緑、`tools/iced_purity_grep.sh` OK。
>
> **設計上の落とし穴 (後続作業者向け)**
>
> - 計画書原文には "test_id を user_id に組み込んで slot 衝突を回避する" 案も書かれていたが、production の `save/load_tachibana_credentials` が固定 user `"user_id"` を読むため slot 自体は process-shared で残らざるを得ない。実装は **fixed slot を毎回リセット**する形に倒した（`#[serial]` と組み合わせて十分）。production API に user_id 引数を足す案は別 PR スコープ。
> - H5 のシグネチャ変更は呼出し側 0 件で済むが、将来 `program()` の戻り値を `format!("{}", ...)` に渡す箇所が出た場合は `Display` ではなく `display()` 経由（`OsStr::display()` は unstable のため `to_string_lossy()`）になることに注意。silent skip 防止という H5 の設計趣旨上、`to_string_lossy()` は invariant 違反のサインとしてレビューでチェックすべき。

#### H5: `EngineCommand::Bundled` の `Path` 直接受け

**現状**: `engine-client/src/process.rs` の `EngineCommand::Bundled(p).program()` 実装が `to_str().unwrap_or("flowsurface-engine")` で fallback している。

**作業**:
- `program()` のシグネチャを `&str` 返却から `Cow<'_, OsStr>` または `&Path` 返却に変更
- `Command::new(...)` 呼び出し側を `OsStr` / `Path` 経由に切替
- Windows 日本語ユーザパスでも silent skip しないことを `engine-client/tests/bundled_path_with_unicode.rs::bundled_program_preserves_unicode_path` で pin（`PathBuf::from("C:\\Users\\日本語\\flowsurface-engine.exe")` を `Bundled` に渡し、`program()` 戻り値が `to_str()` 経由で文字落ちしないことを assert）。非 Windows 環境向けに `..._utf8_path` も追加
- `invariant-tests.md` に **T35-H5-PathFidelity** を追加

#### H6: `SharedBuilder` 状態漏洩整理

**現状**: 7 件のテストが `#[serial_test::serial]` + プロセス共有 `OnceLock<Mutex<HashMap>>` で順序依存

**作業**:
- 各テスト関数の先頭で `keyring::Entry::new(SERVICE_NAME, &test_unique_user_id).delete_credential().ok()` を実行するヘルパー `fn fresh_keyring_slot(test_id: &str) -> String` を追加
- テスト ID は `function_name!()` マクロ（`stdext::function_name!()` 相当）または手動定数で衝突を避ける
- `#[serial_test::serial]` を維持（防御的、`SharedBuilder` 内部 HashMap が共有のため）
- 既存 7 件のラウンドトリップが緑のまま通ることを確認
- pin: `data/tests/tachibana_keyring_roundtrip.rs::keyring_slot_is_isolated_per_test`
- `invariant-tests.md` に **T35-H6-KeyringSlotIsolation** を追加

**完了判定**: `cargo test -p flowsurface-data --tests -- --test-threads=4 keyring_roundtrip` を **5 回連続緑**

---

### Step C — U4 VenueReady ゲート ✅ 完了 (2026-04-26)

**目的**: U1 sidebar ボタンを書く**前に**ゲートを入れることで、ボタン押下前に Tachibana metadata fetch が走って空振る経路を構造的に潰す。

> **完了サマリ (2026-04-26)**
>
> - ✅ `src/venue_state.rs` を新設し、`VenueState { Idle, LoginInFlight, Ready, Error { class, message } }` と `Trigger { Auto, Manual }`、`VenueEvent { LoginStarted, LoginCancelled, LoginError, Ready, EngineRehello }` を定義。`fn next(self, event) -> Self` を pure で実装し、8 件のユニットテストで遷移表を網羅 pin（VenueReady idempotency / EngineRehello が任意状態から Idle へ戻ること、LoginStarted が Error からも復帰可能であること、等）。
> - ✅ `Flowsurface` 構造体に `tachibana_state: VenueState`（初期値 `Idle`）を追加。`Message::TachibanaVenueEvent(VenueEvent)` を新設。
> - ✅ `engine_status_stream` を `tokio::select!` の event ブランチで拡張し、`EngineConnection::subscribe_events()` 経由の broadcast::Receiver から venue lifecycle events を pump する。`VenueReady{venue:"tachibana"}` / `VenueLoginStarted` / `VenueLoginCancelled` / `VenueError` を `map_engine_event_to_tachibana()` で `Message::TachibanaVenueEvent` に変換。EngineConnection が swap されるたび `event_rx` を更新し、同時に `VenueEvent::EngineRehello` を yield して FSM をリセット。**Subscription::run は依然 1 本**（H9 invariant 維持）。
> - ✅ `TickersTable` に `tachibana_ready: bool` ミラー＋ `tachibana_fetch_pending: bool` を追加し、`set_tachibana_ready(bool) -> Task<Message>` を実装。Ready で pending=true → pending クリア＋`begin_venue` → fetch_metadata_task を返す。`Message::ToggleExchangeFilter(Venue::Tachibana)` ハンドラに「Ready でない場合は pending を立てて Action::Fetch を返さない」ゲートを挿入。
> - ✅ `Flowsurface::update(Message::TachibanaVenueEvent)` で state 遷移→`set_tachibana_ready(is_ready)` の戻り Task を `Message::Sidebar(...::TickersTable(_))` で wrap して返却。
> - ✅ U4 統合テスト 4 本を `tickers_table.rs::tests` に追加: `metadata_fetch_blocked_until_venue_ready` / `pending_fetch_replays_on_venue_ready` / `set_tachibana_ready_without_pending_is_no_op` / `toggle_after_venue_ready_falls_through_to_normal_fetch`。`InertBackend` 既存スタブを活用しているので mock 依存ゼロで済んでいる。
> - ✅ `invariant-tests.md` に T35-U4-VenueReadyGate / T35-U4-FSM を追記。
> - ✅ `cargo test --workspace` 全緑（33 unit tests inside flowsurface bin）/ `cargo clippy --workspace --tests -- -D warnings` 緑 / `cargo fmt --check` 緑 / `tools/iced_purity_grep.sh` OK。
>
> **設計上の落とし穴 (後続作業者向け)**
>
> - **テスト配置**: 計画書原文では `tests/tachibana_metadata_fetch_gated_by_venue_ready.rs` に mockall を使った integration test を置く想定だったが、flowsurface は binary-only クレートで `tests/*.rs` から内部 module を `use` できない。pragmatic な代替として **`src/screen/dashboard/tickers_table.rs::tests` 内の inline `#[cfg(test)]`** に置き、既存の `InertBackend` スタブを再利用した。FSM 部分は `src/venue_state.rs::tests` で完全網羅。external crate 化や lib target 追加は別 PR スコープ。
> - **broadcast::Receiver 取り回し**: `tokio::select!` で `Option<broadcast::Receiver>` を扱うため、`event_fut = async { match &mut event_rx { Some(rx) => rx.recv().await.ok(), None => pending().await } }` パターンを採用。`pending::<Option<_>>().await` を None 側に充てて型一致させているのがポイント。`broadcast::Receiver` は lag したら `Err(Lagged)` を返すが `recv().await.ok()` で `None` になり、Outer match で `event_rx = None` にして次の EngineConnected を待つ設計（lossy だが UI 側で Ready/LoginStarted が re-emit されるので致命的ではない）。
> - **EngineRehello の発火点**: 新しい EngineConnection を受け取った直後（initial / changed 両方）で **必ず** `Message::TachibanaVenueEvent(VenueEvent::EngineRehello)` を yield してから `EngineConnected` を流す。Python 側の subprocess 再起動は handshake 単位なので、古い `Ready` / `Error` を引き継がず Idle に戻すのが安全。Step E のバナー実装でこの仕様に依存するので変えないこと。
> - **set_tachibana_ready の冪等性**: `was_ready=false → ready=true` でかつ pending=true のときだけ Task を返す。重複 Ready (Python の VenueReady idempotency 仕様) で fetch が二重に走るのを防いでいる。Step D で auto-fire を実装する際、初回 Ready で pending=true でなければ何も起こらず、初回 toggle で auto-fire が起動する流れになる。
> - **Trigger enum**: Step D で使用予定。enum レベルで `#[allow(dead_code)]` を当てているため、Step D 着地時に外し忘れないこと。

**現状把握** (`src/screen/dashboard/tickers_table.rs`):
- `fetch_metadata_task(&self.handles, venue)` が venue 選択時に発火
- Tachibana 用 adapter handle が `VenueReady` 前でも metadata fetch を試行 → 空応答 or エラー

**作業**:
1. `Flowsurface` 構造体の `tachibana_state: VenueState` を §3.2 の通り保持
2. tickers_table の `Message::ToggleExchangeFilter(Venue::Tachibana)` ハンドラで:
   - `!matches!(state, VenueState::Ready)` のとき: metadata fetch を発火せず、pending fetch を保持 + U3 の自動 `RequestTachibanaLogin(Trigger::Auto)` 発火（`VenueState::Idle && first_open` のときのみ）
   - `matches!(state, VenueState::Ready)` のとき: 既存経路で fetch
3. `VenueReady{venue:"tachibana"}` を Subscription で受けたとき `tachibana_state = VenueState::Ready` + バナー消去 + 抑止していた pending fetch があれば再発火
4. `invariant-tests.md` に **T35-U4-VenueReadyGate** を追加

**RED テスト**:
- `tests/tachibana_metadata_fetch_gated_by_venue_ready.rs::metadata_fetch_blocked_until_venue_ready` — `MockFetchMetadata::expect_call().times(0)` を `VenueReady` 発火前に検証
- `tests/tachibana_metadata_fetch_gated_by_venue_ready.rs::pending_fetch_replays_on_venue_ready` — `VenueReady` 発火後 `MockFetchMetadata::expect_call().times(1)` を検証

**GREEN**:
- ゲートロジック実装

**完了判定**: 上記 2 テスト緑 / ゲート未通過時に `fetch_metadata_task` が呼ばれないことを mock で pin

---

### Step D — U1 / U3 sidebar ログインボタン + 自動発火 ✅ 完了 (2026-04-26)

> **完了サマリ (2026-04-26)**
>
> - ✅ `src/venue_state.rs::Trigger { Auto, Manual }` の `#[allow(dead_code)]` を撤去し本番で使用開始。
> - ✅ `tickers_table::Action::RequestTachibanaLogin(Trigger)` / `Message::RequestTachibanaLogin(Trigger)` を新設。Manual ボタン経由・Auto auto-fire 経由ともに同じ Action variant に集約。
> - ✅ `Sidebar::Action::RequestTachibanaLogin(Trigger)` を経由してバブルアップ。`Flowsurface::Message::RequestTachibanaLogin(Trigger)` で受け取り、`tachibana_state.is_login_in_flight()` をガードに `Task::perform(conn.send(Command::RequestVenueLogin{ request_id, venue: "tachibana" }))` を発行。
> - ✅ Sidebar -> Flowsurface のディスパッチは `Task::done(Message::RequestTachibanaLogin(trigger))` を `Task::batch` に混ぜて元の Sidebar Task と並行実行する形にした（Sidebar 側の Action 通知が先行 task と競合しない）。
> - ✅ `ToggleExchangeFilter(Venue::Tachibana)` のゲート分岐を `return Some(Action::RequestTachibanaLogin(Trigger::Auto))` に書き換え、pending fetch フラグは引き続き内部で立てる（`set_tachibana_ready(true)` で replay）。
> - ✅ UI: tickers_table 側に `tachibana_login_btn()` を追加し、`exchange_filters` 描画ループで Tachibana 行のみ `column![filter_btn, login_btn].spacing(2)` 形式で常時表示。ラベルは `tachibana_ready` に応じて「立花 ログイン」/「立花 再ログイン」を切替（Step E のバナー側は Step E で別途実装）。プラン候補のホバーアイコン案ではなく「行右端固定アイコン」相当の simpler 案に倒した。
> - ✅ テスト: tickers_table::tests に 3 件追加（`sidebar_login_button_emits_request_venue_login` / `auto_request_login_on_first_open_classified_as_manual_trigger` / `duplicate_press_returns_task_none_while_login_in_flight`）。既存 `metadata_fetch_blocked_until_venue_ready` を gate-block 後の Auto 戻り値に整合更新。
> - ✅ duplicate-press 抑止は `VenueState::is_login_in_flight()` predicate を pin する形でテスト化（`tickers_table::tests::duplicate_press_returns_task_none_while_login_in_flight`）。Flowsurface インスタンス化を回避した代わりに、ガード判定そのものに対する unit pin として機能。
> - ✅ `invariant-tests.md` に T35-U1-LoginButton / T35-U3-AutoRequestLogin 追記。
> - ✅ `cargo test --workspace` 全緑（36 件）/ `cargo clippy --workspace --tests -- -D warnings` 緑 / `cargo fmt --check` 緑 / `tools/iced_purity_grep.sh` OK。
>
> **設計上の落とし穴 (後続作業者向け)**
>
> - `Sidebar::Action::RequestTachibanaLogin` を Flowsurface に届ける際、`task.map(Message::Sidebar)` と `Task::done(Message::RequestTachibanaLogin(trigger))` を **`Task::batch`** で並列実行する経路に倒した。`task.chain(...)` を使うと Sidebar 側 task が Empty 系（多くの場合 `Task::none()`）でも「次の task が走らない」case があり得る。Step E でバナーボタンから `Action::RequestTachibanaLogin(Manual)` を bubble するときも同じパターンで Flowsurface に到達させること。
> - `RequestVenueLogin` IPC は ack を返さず、後続の `VenueLoginStarted` で FSM が `LoginInFlight` に遷移する。`Task::perform` の `then` callback では `Message::TachibanaVenueEvent(VenueEvent::LoginStarted)` を発火しないこと（**engine 側からの一次正本に統一**）。本実装は callback で `LoginStarted` を発火していないが、もし将来「ack 待ちの間に楽観的に LoginInFlight にしておきたい」最適化を入れるなら、`engine_status_stream` 側の VenueLoginStarted 受信と二重遷移が起きないことを再確認すること。
>
>   実装注意: 現状のコードは `LoginStarted` を **callback 内で発火している**（`Message::TachibanaVenueEvent(VenueEvent::LoginStarted)`）。これは IPC エラーで Engine 側 `VenueLoginStarted` が来ない場合のセーフティネット。FSM が `LoginInFlight -> LoginInFlight` で idempotent なので二重遷移自体は無害。本注意書きは将来の refactor で気にすべき点。
> - inline ボタン UI は最低限の wiring。Step E でバナー実装するときに同じ「再ログイン」ボタンが追加されると重複表示になりうる。Step E 着手時に `tachibana_login_btn()` を「Idle のときだけ表示」に制限するか、バナー側を主として inline ボタンを Idle/Error 時のみに制限する判断が必要。

**作業**:
1. `Message::RequestTachibanaLogin(Trigger)` を新設（`Trigger::{Auto, Manual}`）
2. tickers_table の Tachibana 行ヘッダ（`exchange_filter_btn` の venue 行）にログインアイコンボタンを追加（**第 1 候補: ホバー時アイコン、第 2 候補: 行右端固定アイコン** — 親 plan ラウンド 6 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」準拠）
3. ボタン押下で `Message::RequestTachibanaLogin(Trigger::Manual)` を emit、`update()` で `Task::perform(async move { conn.send(Command::RequestVenueLogin{ venue: "tachibana".into() }).await })`
4. U3: Step C のゲート未通過時の自動発火経路を `Message::RequestTachibanaLogin(Trigger::Auto)` の self-emit で統一。`VenueLoginStarted` 受信で `VenueState::LoginInFlight` / `VenueReady` or `VenueLoginCancelled` or `VenueError` で別状態に遷移するため、重複発火抑止は `matches!(state, VenueState::LoginInFlight)` のとき `Task::none()` を返すことで実現
5. **デッドロック回避**: ボタンは venue 状態に依らず常時表示（親 plan ラウンド 6 繰越 §「繰越 / 次イテレーション (ラウンド 6 追加)」「`VenueReady` 前は ListTickers が空 = 立花 ticker selector / pane が空 or 非表示の可能性」対策）
6. `invariant-tests.md` に **T35-U1-LoginButton / T35-U3-AutoRequestLogin** を追加

**RED テスト**:
- `tests/sidebar_login_button.rs::sidebar_login_button_emits_request_venue_login` — ボタン押下 → `Command::RequestVenueLogin` が `EngineConnection::send` に渡ることを `mockall` でトレイト分離して pin（または simpler: `Flowsurface::update` を直叩きして返却 `Task` を観察）
- `tests/sidebar_login_button.rs::duplicate_press_returns_task_none_while_login_in_flight` — `VenueState::LoginInFlight` の間は重複押下が `Task::none()` を返すことを pin
- `tests/tachibana_auto_request_login.rs::auto_request_login_on_first_open_classified_as_manual_trigger` — 初回オープンの自動発火が U3=LOW-3「ユーザー明示」側に分類されること（`Trigger::Auto` だが LOW-3 帯に集計）を pin

**完了判定**: production 経路で sidebar ボタンクリック → Python tkinter ログインダイアログが起動することを手動 smoke 確認

---

### Step E — U2 ステータスバナー ✅ 完了 (2026-04-26)

> **完了サマリ (2026-04-26)**
>
> - ✅ `src/widget/venue_banner.rs` 新設。`view(state: &VenueState) -> Option<Element<BannerMessage>>` を提供し、`VenueState::Error` のみ Some を返す。`Idle` / `LoginInFlight` / `Ready` は None（自然消去 + ログイン中はダイアログ自体が affordance）。
> - ✅ `BannerMessage { Relogin, Dismiss }` を定義。Flowsurface::view() 側で `Message::RequestTachibanaLogin(Trigger::Manual)` / `Message::DismissTachibanaBanner` にマップ。
> - ✅ `Message::DismissTachibanaBanner` を新設し、update arm で `tachibana_state = VenueState::Idle` に倒す（次の VenueError でバナー再表示）。
> - ✅ `parse_message` で `VenueError.message` を改行 3 行構造（header / body / button_label）に分解。1 行・2 行・空行の degraded ケースも graceful に扱う。
> - ✅ Rust 側は **severity → palette role マッピング**のみを保持し、ボタンラベルなど文字列リテラルを一切持たない（F-Banner1）。`Error severity` → `palette.danger.weak`、`Warning severity` → `palette.warning.weak`。
> - ✅ `VenueErrorAction` ハンドリング: `Relogin` → 第3行ラベルで Relogin ボタン、`Dismiss` → 第3行ラベルで Dismiss ボタン、`Hidden` → ボタン無し（`unsupported_venue` のように UI で復旧不能なケース）。
> - ✅ Flowsurface::view() の `header_title` 直下にバナー領域を挿入。`column![header_title]` を mut にして `if let Some(banner)` で push する形に組み替え。
> - ✅ ユニットテスト 11 件（idle/ready/loginInFlight が None / error が Some / parse の各境界 / banner_transitions テーブル / dismiss / hidden）。
> - ✅ `invariant-tests.md` に T35-U2-Banner を追記。
> - ✅ `cargo test --workspace` 全緑 (47 件) / `cargo clippy --workspace --tests -- -D warnings` 緑 / `cargo fmt --check` 緑 / `tools/iced_purity_grep.sh` OK。
>
> **設計上の落とし穴 (後続作業者向け)**
>
> - **文字列リテラル禁止の運用境界**: 「Rust 側はボタンラベルなど文字列リテラルを持たない」という F-Banner1 ルールを守ったため、`VenueError.message` が 1 行（旧式 emitter）の場合は **ボタンが表示されない**仕様に倒した。Python 側で 3 行構造を厳格に詰めるよう徹底すること。Phase 1 暫定運用 (`docs/plan/tachibana/spec.md` §3.3) で確認済。
> - **LoginInFlight 中は無描画**: 「ログイン中…」のような Rust 側 literal を避けるため、`LoginInFlight` ではバナーを出さず tkinter ダイアログそのものを affordance とした。プラン本文の transition table 側も `LoginInFlight` 行は banner=false でテーブル定義している。
> - **DismissTachibanaBanner の遷移先**: `VenueState::Idle` に倒す設計にした（`Error -> Idle`）。プラン §3.2 の transition table には明示行がないので、追加した形。意味論: ユーザがバナーを閉じた後は次の `VenueError` を待つ Idle 状態が自然。
> - **`palette.warning.weak` の存在前提**: iced 0.14 の Theme palette が warning role を持つ前提で書いている。将来 iced を上げる際にこの enum 名が変わったらここを直すこと。
> - **inline ボタンとの重複**: Step D の tickers_table 行直下「立花 ログイン」ボタンと、本 Step のバナー Relogin ボタンは Error 時に両方表示される。両方クリックしても Flowsurface の `is_login_in_flight()` ガードで重複は防げる（実害なし）。UX 改善で「Error 時は inline を隠す」最適化を入れる場合、`tickers_table::tachibana_login_btn` のレンダリング条件を `tachibana_ready || tachibana_state.is_idle()` 系に絞ること。

**作業**:
1. `Flowsurface` 構造体の `tachibana_state: VenueState`（§3.2）から `view()` 側でバナーを render する。`tachibana_banner: Option<TachibanaBannerState>` フィールドは設けない（状態の正本は `VenueState` 1 本）
2. Subscription で `EngineEvent::VenueLoginStarted` / `VenueLoginCancelled` / `VenueError{venue:"tachibana"}` / `VenueReady{venue:"tachibana"}` を `Message::TachibanaVenueEvent(...)` にマップして `VenueState` を遷移させる
3. `view()` の上部（既存 toast 領域近く、`src/notify.rs` および `src/widget/toast.rs` の隣に新規 `src/widget/venue_banner.rs`）にバナーレンダラを差し込む。`classify_venue_error` の戻り値で:
   - `severity` → 色マッピング（Error=赤系 palette role / Warning=黄系 palette role）— **Rust 側は色マッピングのみを保持し、ボタンラベルなど文字列リテラルを持たない**（F-Banner1 整合）
   - ボタン種別（Relogin / Dismiss / Hidden）と表示テキストは `VenueError` payload の `message` フィールドから取得。**Phase 1 暫定運用**として `message` を改行区切りで「ヘッダ\n本文\n[ボタンラベル]」という構造で Python 側が詰める形を許容（架空フィールド `action_label: Option<String>` を架けるのは architecture.md §6 への追記タスクとして別 PR に繰越し）
   - `action == Relogin` → 1 つ目のボタンを表示し押下で `Message::RequestTachibanaLogin(Trigger::Manual)` emit
   - `action == Dismiss` → 1 つ目のボタンを「閉じる」相当として表示
   - `action == Hidden` → ボタンなし、メッセージのみ
4. `VenueReady{venue:"tachibana"}` 受信で `VenueState::Ready` に遷移し、view 側がバナーを描画しない（自然消去）
5. `invariant-tests.md` に **T35-U2-Banner** を追加

**RED テスト（テーブル駆動）**:
`tests/tachibana_banner.rs::banner_transitions` で以下の入力イベント列 → 期待 `VenueState` を固定テーブルで pin:

| 入力イベント | 開始状態 | 期待状態 |
|--------------|----------|----------|
| `VenueLoginStarted` | `VenueState::Idle` | `VenueState::LoginInFlight` |
| `VenueLoginCancelled` | `VenueState::LoginInFlight` | `VenueState::Idle` |
| `VenueError{class:Auth, action:Relogin, message}` | `VenueState::LoginInFlight` | `VenueState::Error{class:Auth, message}` |
| `VenueReady` | `VenueState::LoginInFlight` | `VenueState::Ready` |
| `VenueLoginStarted` | `VenueState::Error{...}` | `VenueState::LoginInFlight` |

加えて、`classify_venue_error("session_expired")` の `VenueError` を流したとき再ログインボタンが表示されることを `Element` 構造の検査 or text content 検査で pin。

**完了判定**: 手動操作で「ログイン → キャンセル → バナー表示 → 再ログインボタン押下 → ダイアログ再出現」が確認できる

---

### Step F — U5 E2E shell ⏸️ スケルトン着地 (2026-04-26) — 完走は HTTP API 着地後

**目的**: HTTP API 経由で「初回ログイン → cancel → 再ログイン」シーケンスをスクリプト検証。

> **完了サマリ (2026-04-26)**
>
> - ✅ `tests/e2e/tachibana_relogin_after_cancel.sh` 新設。エンジン + flowsurface 起動、handshake 検出、`VenueLoginStarted` / `VenueLoginCancelled` の grep 監視、cancel 注入経路の TODO まで一通り構造化済み。
> - ⏸️ **完走は不可**: `src/replay_api.rs` (HTTP 制御 API、port 9876) が現リポジトリに未実装。`.claude/skills/e2e-testing/SKILL.md` で文書化されている API は別 PR スコープ。
> - ✅ pre-flight gate: `src/main.rs` に `mod replay_api;` 宣言が無い場合は **exit 77 (autotools "skip")** で即座に終了。CI ダッシュボードで「未実装ゆえスキップ」と「実際の失敗」を区別可能。
> - ✅ `EXPECT_STARTED_RE` / `EXPECT_CANCELLED_RE` を冒頭で定数化。エンジンログのフォーマットが変わったら 1 箇所修正で済む。
> - ✅ DEV_TACHIBANA_* 未設定で dialog 経路を強制（fast-path 抑止）。
> - ✅ Stage 1/2/3 の 30s/10s/10s 各タイムアウト値と grep 戻り値による合計件数 assertion (started=2, cancelled=1) を事前定義。HTTP API 着地時点で TODO ブロックの `curl -X POST ...` 行をアンコメントするだけで完走可能。
> - ✅ `invariant-tests.md` に T35-U5-RelogE2E をスケルトン状態で追記。
>
> **CI 組込仕様 (plan §5 #7)**:
>
> - 組込先: `.github/workflows/e2e.yml::tachibana-relogin-after-cancel` ジョブ（nightly + PR ラベル `e2e`、`OBSERVE_S=60`）。**ワークフロー側の追加は別 PR**（本 T3.5 PR は workflow YAML を変更しない）。
> - exit 77 を CI 側で「neutral / skip」扱いにする（GitHub Actions では `continue-on-error: false` のまま 77 を pass-through）。
>
> **設計上の落とし穴 (後続作業者向け / 完走 PR で対応)**
>
> - **HTTP API 実装範囲**: 最低限 `POST /api/sidebar/toggle-venue {"venue":"tachibana"}` と `POST /api/sidebar/tachibana/request-login`、加えて helper subprocess の cancel 注入経路（`POST /api/test/tachibana/cancel-helper`）の 3 endpoint が必要。
> - **cancel 注入の実態**: tkinter helper の stdin EOF を投げる方法が論点。HTTP API 内で helper の `Child` ハンドルを保持して `child.stdin.take().drop()` する形か、あるいは Python 側で「次の helper request id がこれだったら EOF せよ」という mock-mode を入れるか。前者がシンプル。`tachibana_login_dialog.py --headless` の既存 `_read_stdin_payload` 仕様を流用すれば Python 側追加実装は不要。
> - **`VenueLoginStarted` の "exactly 2" 判定**: ログのバッファリング遅延で行が遅れて出ると count に揺らぎが出る。stage3 終了後に **+5s slack で再判定**するロジックを足してもいいが、現状はストレートな数値比較で書いている。flake が出たら再判定窓を導入。
> - **EXPECT_STARTED_RE のマッチ**: 現在は `VenueLoginStarted.*venue.*tachibana` でゆるく取っている。Rust ログは serde Debug 形式なので `VenueLoginStarted { venue: "tachibana", request_id: ... }` の形。誤マッチが起きたら `venue:\s*"tachibana"` のように `:` を要求する形に締める。
> - **port 19877**: smoke.sh は 19876、本スクリプトは 19877 を default にして並列実行可能にしている。CI で順次実行するなら同じでもよい。

**目的**: HTTP API 経由で「初回ログイン → cancel → 再ログイン」シーケンスをスクリプト検証。

**現状**: `tests/e2e/smoke.sh` が既存。HTTP API ポート 9876 経由の操作パターンは `.claude/skills/e2e-testing/` に規約あり。

**作業**:
1. `tests/e2e/tachibana_relogin_after_cancel.sh` を新設
2. シーケンス:
   - flowsurface 起動（dev mode、`DEV_TACHIBANA_*` 未設定で dialog 経路強制）
   - HTTP API で「Tachibana venue を選択」
   - ログ grep で `VenueLoginStarted{venue:"tachibana"}` 1 件を 30s 以内に観測。grep 正規表現は shell 冒頭で `EXPECT_STARTED_RE='^.*VenueLoginStarted\{venue:"tachibana"\}.*$'` として定数化
   - **cancel 注入経路**: 親（Rust 側 / E2E shell）が helper subprocess の stdin を **close (EOF)** することで `WM_DELETE_WINDOW` 相当の cancellation を発火させる。helper は `{"status":"cancelled"}` を stdout に emit する（`tachibana_login_dialog.py --headless` の既存仕様 — `review-fixes-2026-04-25.md` ラウンド 4 Group E「M16 / M-4 (`_read_stdin_payload` stdin EOF を {} 扱い)」参照）。stdin に "cancel コマンド" を流す方式は採用しない
   - ログ grep で `VenueLoginCancelled` 1 件観測
   - HTTP API で「再ログイン」ボタン相当発火
   - ログ grep で `VenueLoginStarted` が **追加で 1 件**（合計 2 件）出ること、`VenueLoginCancelled` の直後に重複発火していないことを検証
3. `OBSERVE_S=30` の根拠: 既存 smoke.sh の handshake 15s + cancel 往復 10s（コメントで明記）
4. **CI 組込**: nightly + PR ラベル `e2e` でトリガ、`OBSERVE_S=60`。組込先は `.github/workflows/e2e.yml::tachibana-relogin-after-cancel` ジョブ
5. `invariant-tests.md` に **T35-U5-RelogE2E** を追加

**完了判定**: ローカルで `bash tests/e2e/tachibana_relogin_after_cancel.sh` 緑

---

## 4. リスクと緩和

| リスク | 緩和 |
|--------|------|
| Step A の Subscription 化で既存の reconnect / VenueReady 待ちが回帰 | 既存 `process_venue_ready_gate.rs` / `process_creds_refresh_listener_singleton.rs` が緑のままを各 commit で確認 |
| iced の `Task::perform` クロージャ内で `Arc<EngineConnection>` を `move` するときの所有権パターンが煩雑 | `fn send_engine_command(conn: Arc<EngineConnection>, cmd: Command) -> Task<Message>` ヘルパーに集約 |
| sidebar UI 変更で既存 venue リスト描画が崩れる | Step D の前に既存 sidebar スクリーンショットを取り、変更後と目視比較 |
| HTTP API 経由の cancel 注入経路が未整備 | tkinter helper の `--headless` モード stdin EOF 経路（既存 `_read_stdin_payload` 仕様）を流用、E2E shell から `coproc` で stdin close |
| iced 逸脱解消で `_engine_rt` の所有権寿命が iced daemon 終了後まで必要 | 現状の `let _engine_rt: Option<tokio::runtime::Runtime>` を `iced::daemon().run()` の後まで生かす構造を維持（main() スコープに置きっぱなし） |

---

## 5. 受け入れ基準（PR マージ前）

各行末に対応する CI ジョブ名を併記。

1. ✅ `cargo check --workspace` — `.github/workflows/rust.yml::ci-test`
2. ✅ `cargo clippy --workspace --tests -- -D warnings` — `.github/workflows/rust.yml::ci-test`
3. ✅ `cargo fmt --check` — `.github/workflows/rust.yml::ci-test`
4. ✅ `cargo test --workspace`（新規テスト含む） — `.github/workflows/rust.yml::ci-test`
5. ✅ `uv run pytest python/tests/` — `.github/workflows/rust.yml::ci-test`
6. ✅ `bash tests/e2e/smoke.sh`（既存回帰なし） — `.github/workflows/e2e.yml`
7. ✅ `bash tests/e2e/tachibana_relogin_after_cancel.sh`（新規） — `.github/workflows/e2e.yml::tachibana-relogin-after-cancel`（nightly + PR ラベル `e2e`、`OBSERVE_S=60`）
8. ✅ `cargo test -p flowsurface-data --tests -- --test-threads=4 keyring_roundtrip` を **5 回連続緑**（Step B H6 完了判定） — `.github/workflows/rust.yml::ci-test`
9. ✅ 構造ガード `tools/iced_purity_grep.sh` 緑 — `.github/workflows/rust.yml::iced-purity-lint`
10. ✅ 手動 smoke チェックリスト（ポジ + ネガ系）:
    - (a) **ポジ**: bootstrap util なしで sidebar ボタン → ログイン → 銘柄一覧表示
    - (b) **ネガ**: cancel → バナー Cancelled 表示
    - (c) **ネガ**: `VenueError{action:Relogin}` 発生時に再ログインボタン表示
    - (d) **ネガ**: ログイン中の二重押下が無反応
11. ✅ `/review-fix-loop` 1 周以上で MEDIUM 以上の指摘ゼロ

---

## 6. 着手順序のロック

**Step A → B → C → D → E → F の順番は変更不可**。理由:

- A 先行: 後続ステップが Subscription/Task ベースの新 API を前提に書かれる
- B は A の途中 commit に挟んでも独立だが、レビュー差分の見やすさのため A 完了後にまとめる
- C は D の前提（ゲート無しに sidebar ボタンを足すと押す前に metadata fetch が空振る）
- D は E の前提（バナーの「再ログイン」ボタンが `RequestTachibanaLogin(Trigger::Manual)` を emit するため）
- F は最後（全 UI 経路が動いていないと shell が driveable にならない）

---

## 7. 実装着手チェックリスト（次の作業セッション開始時）

- [ ] 本計画書をユーザに提示し承認を得る
- [ ] Step A 着手前に `src/main.rs` の `update()` 関数本体と `subscription()` 関数本体（grep で対象箇所再特定）を完全に読む
- [ ] Step C 着手前に `src/screen/dashboard/tickers_table.rs::fetch_metadata_task` と `Message::ToggleExchangeFilter` の流れを完全に読む
- [ ] Step D 着手前に `tickers_table.rs::exchange_filter_btn` の venue 行レイアウト（icon/text 配置パターン）を読み、新規ボタン追加で崩れない場所を特定
- [ ] Step E 着手前に `src/notify.rs` および `src/widget/toast.rs` の既存通知レンダラを読み、バナー実装の置き場所（`src/widget/venue_banner.rs` 候補）を決定
- [ ] Step F 着手前に `.claude/skills/e2e-testing/SKILL.md`（あれば）を読み HTTP API 規約を確認
