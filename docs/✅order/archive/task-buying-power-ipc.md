# Task: Buying Power IPC 配線（U3 完成）

**作業ブランチ**: `fix/engine-pipe-non-utf8-deadlock`（もしくは新規 feature ブランチ）  
**計画書ディレクトリ**: `docs/✅order/` — 作業前後に必ず参照する。不明な背景・仕様は
`implementation-plan.md` / `rust-ui-plan.md` / `spec.md` / `architecture.md` で確認すること。

---

## Goal（目的）

余力パネルの「更新」ボタンを押したときに "余力情報取得: IPC 未実装（準備中）" が表示されている。
Python 側の `fetch_buying_power` / `fetch_credit_buying_power` は実装済みだが、
IPC Command/Event が存在しないため Rust UI から呼べない。
**`GetBuyingPower` IPC を追加し、余力パネルに実データを表示できるようにする。**

---

## 現状（着手前の状態）

| 層 | 状態 |
|---|---|
| Python `tachibana_orders.fetch_buying_power` / `fetch_credit_buying_power` | ✅ 実装済み（`python/engine/exchanges/tachibana_orders.py:1551` / `1602`） |
| Python `server.py` import | ✅ import 済み（行57-58）。dispatch は**未配線** |
| `engine-client/src/dto.rs` `Command::GetBuyingPower` | ❌ 未追加 |
| `engine-client/src/dto.rs` `Event::BuyingPowerUpdated` | ❌ 未追加 |
| `python/engine/schemas.py` 対応モデル | ✅ 追加済み（SCHEMA_MINOR=2 に bump 済み） |
| `src/screen/dashboard/panel/buying_power.rs` | ✅ scaffold 実装済み。`set_cash_buying_power` / `set_credit_buying_power` メソッドあり |
| `src/main.rs` `BuyingPowerAction` ハンドラ | ❌ 固定 toast を返すだけ（IPC 未呼び出し） |

---

## Constraints（制約）

1. **IPC schema バージョニング必須**: Rust と Python の両方で `SCHEMA_MINOR` を `1 → 2` に bump する。
   - Rust: `engine-client/src/lib.rs` の `SCHEMA_MINOR`
   - Python: `python/engine/schemas.py` の `SCHEMA_MINOR`
   - ルール詳細は `docs/✅python-data-engine/spec.md §4.5.1`（minor 差は警告のみで接続継続・SCHEMA_MINOR bump は同期運用のため必須）参照
2. **dto.rs の命名規則**: Command/Event タグは PascalCase（`GetBuyingPower`, `BuyingPowerUpdated`）。値 enum（`OrderSide` 等）は `SCREAMING_SNAKE_CASE`。
3. **立花固有語禁止**: `sCLMID` / `CLMZan` などの立花 API 固有語を IPC 層（dto.rs / schemas.py / Rust UI）に漏らさない（`test_nautilus_boundary_lint.py` がガード）。
4. **Release ビルドの dev ログイン禁止**: dev 環境変数の追加経路を増やす場合は必ず `F-DevEnv-Release-Guard` を通す。
5. **既存テストをリグレッションさせない**: `cargo test --workspace` / `uv run pytest python/tests/ -v` が全緑であること。

---

## 実装ステップ

> **TDD アプローチ**: `.claude/skills/tdd-workflow/SKILL.md` の手順で実装する。テストを先に書いてから実装する。

### Step 1: dto.rs に Command / Event 追加（SCHEMA_MINOR bump）

```rust
// engine-client/src/dto.rs
Command::GetBuyingPower {
    request_id: String,
    venue: String,
}

Event::BuyingPowerUpdated {
    request_id: String,
    venue: String,
    cash_available: i64,      // 現物買付余力（円）
    cash_shortfall: i64,      // 余力不足額（0 は不足なし）
    credit_available: i64,    // 信用新規可能額（円）
    ts_ms: i64,               // 取得時刻 Unix ミリ秒
}
```

- `engine-client/src/lib.rs` の `SCHEMA_MINOR: u16 = 1` → `2` に変更
- `docs/✅python-data-engine/schemas/commands.json` と `events.json` を更新

### Step 2: Python schemas.py に対応モデル追加

- `SCHEMA_MINOR: int = 1` → `2`
- `GetBuyingPowerCommand` (pydantic) と `BuyingPowerUpdatedEvent` を追加

### Step 3: server.py dispatch 配線

- `GetBuyingPower` コマンドを受けたら `tachibana_fetch_buying_power` + `tachibana_fetch_credit_buying_power` を呼び、
  `BuyingPowerUpdated` イベントを返す
- **決定**: `InsufficientFundsError` は `_do_get_buying_power` では個別ハンドリングしない。`SessionExpiredError` 以外の例外はすべて generic catch → `Error(code: "INTERNAL_ERROR")` イベントを返す。`OrderRejected` 経路・エラーフィールド追加は採用しない。

### Step 4: main.rs の BuyingPowerAction ハンドラ実装

- `src/main.rs`（`BuyingPowerAction` ハンドラ）の固定 toast を削除し、`Command::GetBuyingPower` を IPC 送信する
- `Event::BuyingPowerUpdated` を受けて `distribute_buying_power()` 経由で `set_cash_buying_power` / `set_credit_buying_power` を一括呼び出しする
- `cash_available → spot_buying_power`, `credit_available → credit_buying_power` を set するメソッドを呼ぶ。`cash_shortfall` はパネルに直接マップするフィールドなし（表示なし）
- ハンドラのパターンは `src/main.rs`（`GetOrderList` IPC 送信パターン）を参照

### Step 5: ラウンドトリップテスト追加

- Rust: `engine-client/tests/schema_v2_1_roundtrip.rs` を追加
- Python: `python/tests/test_buying_power_ipc.py` を新規作成（`GetBuyingPowerCommand` / `BuyingPowerUpdatedEvent` の serialize/deserialize）
- Python: `python/tests/test_server_buying_power_dispatch.py` — dispatch 経路（正常系・SESSION_NOT_ESTABLISHED・INTERNAL_ERROR）を追加済み（J-7/J-9/J-11）

---

## Acceptance criteria（完了条件）

- [x] 余力パネルの「更新」ボタンを押すと IPC 経由で現物余力・信用余力が取得されてパネルに表示される
- [x] "余力情報取得: IPC 未実装（準備中）" の toast が出なくなる
- [x] `cargo test --workspace` 全緑
- [x] `uv run pytest python/tests/ -v` 全緑
- [x] `/ipc-schema-check` スキルで SCHEMA_MINOR の Rust / Python 整合が確認できる
- [x] `cargo clippy -- -D warnings` クリーン
- [x] 実装完了後に `.claude/skills/review-fix-loop/SKILL.md` でレビューと修正を行う
- [x] `docs/✅python-data-engine/schemas/commands.json` と `events.json` に `GetBuyingPower` / `BuyingPowerUpdated` が追記されていること
- [x] `python/tests/test_server_buying_power_dispatch.py` が全緑
- [x] 追加テストファイルが `cargo test --workspace` / `uv run pytest python/tests/ -v` で CI に自動収集されることを確認済み

---

## 進捗・知見記録（作業者が更新すること）

> 進捗があり次第ここに追記する。完了した項目には ✅ を付ける。

- ✅ Step 1: dto.rs Command/Event 追加 + SCHEMA_MINOR bump（1→2）
- ✅ Step 2: schemas.py 更新（GetBuyingPower / BuyingPowerUpdated モデル追加）
- ✅ Step 3: server.py dispatch 配線（`_do_get_buying_power` 実装）
- ✅ Step 4: main.rs ハンドラ実装（BuyingPowerAction IPC 送信 + BuyingPowerUpdated 受信）
- ✅ Step 5: ラウンドトリップテスト追加（schema_v2_1_roundtrip.rs / test_buying_power_ipc.py）
- ✅ Acceptance criteria 全項目確認（cargo test / pytest / clippy / ipc-schema-check 全緑）
- ✅ review-fix-loop 完了

### Ladder ヘッダ追加（fix-ladder-header-2026-04-28）

- ✅ HEADER_HEIGHT 定数追加（= ROW_HEIGHT = 16.0）
- ✅ header_cache フィールド追加・初期化・invalidate() でクリア
- ✅ visible_rows() / price_to_screen_y() の mid_screen_y をヘッダ分オフセット
- ✅ draw_vsplit を HEADER_HEIGHT 起点に修正（ヘッダ領域に縦線が入らない）
- ✅ header_geo（背景＋境界線＋ラベル）をオーバーレイとして描画
- ✅ 3 つのユニットテスト追加・全 PASS（mid_screen_y / build_price_grid None / narrow_pane）
- ✅ cargo test / cargo clippy 全緑
- ✅ review-fix-loop 完了（3ラウンド、MEDIUM+ ゼロ収束）

### 買余力 ¥0 表示 — フィールド名不一致の修正（fix-buying-power-field-names-2026-04-28）

**問題**: デモ口座（初期資金 2000万円）にログインしても `現物余力: ¥0 / 信用余力: ¥0` と表示された。

**原因**: `sJsonOfmt="5"` の `CLMZanKaiKanougaku` / `CLMZanShinkiKanoIjiritu` レスポンスは Summary 系フィールド名を使用しており、コードが期待する旧フィールド名が存在しなかった。`dict.get()` のデフォルト `"0"` にフォールバックし続けた。

| エンドポイント | 旧フィールド（誤）| 実際のフィールド（正）|
|---|---|---|
| `CLMZanKaiKanougaku` | `sZanKaiKanougakuGoukei` | `sSummaryGenkabuKaituke` |
| `CLMZanKaiKanougaku` | `sZanKaiKanougakuHusoku`（額）| `sHusokukinHasseiFlg`（0/1 フラグ）|
| `CLMZanShinkiKanoIjiritu` | `sZanShinkiKanoIjirituGoukei` | `sSummarySinyouSinkidate` |

- ✅ `fetch_buying_power` / `fetch_credit_buying_power` のフィールド名修正
- ✅ テストデータを実際の API レスポンス形式に更新（6/6 PASS → 全 948 テスト PASS）
- ✅ `scripts/diagnose_buying_power.py` を追加（実機検証・再発防止用）
- 計画書: `docs/✅order/fix-buying-power-field-names-2026-04-28.md`

### BuyingPower 新規登録後の自動フェッチ（fix-buying-power-auto-fetch-on-add-2026-04-28）

**問題**: 起動後にサイドバーから「買余力」ペインを新規登録した場合、`GetBuyingPower` IPC が発行されず「更新」ボタンを手動で押すまで余力が表示されなかった。VenueReady ハンドラの自動フェッチは 1 度しか走らないため、後から追加したペインはキャッチアップできなかった。

- ✅ `OpenOrderPanel(ContentKind::BuyingPower)` ハンドラに auto-fetch ロジックを追加（`src/main.rs`）
- ✅ `pane_added` フラグ導入: ペイン分割成功後のみ IPC を発行（split 失敗時の誤発行を防止）
- ✅ `buying_power_request_id.is_none()` ガード: in-flight 競合を防止
- ✅ send Err を `Message::IpcError` にルーティングして既存クリアロジックに乗せる
- ✅ `EngineConnected` 冒頭で `buying_power_request_id = None` リセット（再接続時の固着防止）
- ✅ VenueReady ハンドラに `is_none()` ガードと req_id 記録を追加（3 経路を対称化）
- ✅ cargo test / cargo clippy 全緑
- ✅ review-fix-loop 完了（R4 収束、MEDIUM+ ゼロ）
- 計画書: `docs/✅order/fix-buying-power-auto-fetch-on-add-2026-04-28.md`
- （auto-fetch 経路は Rust unit test 未起票 — `buying_power_request_id.is_none()` ガードのロジックテストは今後の改善候補）

### 新たな知見・設計判断・Tips

- `Message::BuyingPowerUpdated`（Rust 内部型）には `request_id` / `venue` を含めない設計にした。wire 上の IPC Event（Python→Rust）は `request_id`/`venue` を含む。Rust の engine-client がデシリアライズ後に除外して内部 Message に変換する。
  `OrderListUpdated` も同様の構造（orders のみ）。
- `distribute_buying_power` を dashboard.rs に追加し、`set_cash_buying_power` と
  `set_credit_buying_power` を 1 回のイベントでまとめて呼ぶ設計にした（2回 IPC を飛ばさない）。

---

## 関連ファイル

| ファイル | 役割 |
|---|---|
| `engine-client/src/dto.rs` | IPC Command/Event enum（要追加） |
| `engine-client/src/lib.rs` | `SCHEMA_MINOR` 定数（要 bump） |
| `python/engine/schemas.py` | Python 側 IPC スキーマ（要追加・bump） |
| `python/engine/server.py` | dispatch ループ（要配線）|
| `python/engine/exchanges/tachibana_orders.py:1551` | `fetch_buying_power` 実装済み |
| `python/engine/exchanges/tachibana_orders.py:1602` | `fetch_credit_buying_power` 実装済み |
| `src/screen/dashboard/panel/buying_power.rs` | Rust UI パネル（scaffold 済み） |
| `src/main.rs` | `BuyingPowerAction` ハンドラ（実装済み） |
| `docs/✅order/implementation-plan.md` | Phase O3 T3.2/T3.4 の詳細 |
| `docs/✅order/rust-ui-plan.md` | Phase U3 Tu3.1 の詳細 |
| `docs/✅python-data-engine/spec.md §4.5.1` | IPC schema バージョニングルール |
| `docs/✅python-data-engine/schemas/commands.json` | スキーマ JSON（要更新） |
| `docs/✅python-data-engine/schemas/events.json` | スキーマ JSON（要更新） |
