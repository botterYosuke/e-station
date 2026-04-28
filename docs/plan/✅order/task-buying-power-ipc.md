# Task: Buying Power IPC 配線（U3 完成）

**作業ブランチ**: `fix/engine-pipe-non-utf8-deadlock`（もしくは新規 feature ブランチ）  
**計画書ディレクトリ**: `docs/plan/✅order/` — 作業前後に必ず参照する。不明な背景・仕様は
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
| `python/engine/schemas.py` 対応モデル | ❌ 未追加（現 SCHEMA_MINOR=1） |
| `src/screen/dashboard/panel/buying_power.rs` | ✅ scaffold 実装済み。`set_cash_buying_power` / `set_credit_buying_power` メソッドあり |
| `src/main.rs:1618` `BuyingPowerAction` ハンドラ | ❌ 固定 toast を返すだけ（IPC 未呼び出し） |

---

## Constraints（制約）

1. **IPC schema バージョニング必須**: Rust と Python の両方で `SCHEMA_MINOR` を `1 → 2` に bump する。
   - Rust: `engine-client/src/lib.rs` の `SCHEMA_MINOR`
   - Python: `python/engine/schemas.py` の `SCHEMA_MINOR`
   - ルール詳細は `docs/plan/✅python-data-engine/spec.md` §4.5.1 参照
2. **dto.rs の命名規則**: `serde(rename_all = "SCREAMING_SNAKE_CASE")` を全 enum に維持。
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
- `docs/plan/✅python-data-engine/schemas/commands.json` と `events.json` を更新

### Step 2: Python schemas.py に対応モデル追加

- `SCHEMA_MINOR: int = 1` → `2`
- `GetBuyingPowerCommand` (pydantic) と `BuyingPowerUpdatedEvent` を追加

### Step 3: server.py dispatch 配線

- `GetBuyingPower` コマンドを受けたら `tachibana_fetch_buying_power` + `tachibana_fetch_credit_buying_power` を呼び、
  `BuyingPowerUpdated` イベントを返す
- `InsufficientFundsError` は既存の `OrderRejected` 経路に乗せるか、エラーフィールドを返すかを判断する（spec に記述がなければ `BuyingPowerUpdated` に `error` フィールド追加を検討）

### Step 4: main.rs の BuyingPowerAction ハンドラ実装

- `src/main.rs:1618` の固定 toast を削除し、`Command::GetBuyingPower` を IPC 送信する
- `Event::BuyingPowerUpdated` を受けて `BuyingPowerPanel::set_cash_buying_power` / `set_credit_buying_power` を呼ぶ
- ハンドラのパターンは `OrderListAction::RequestOrderList`（`main.rs:1628`）を参照

### Step 5: ラウンドトリップテスト追加

- Rust: `engine-client/tests/` に `schema_v2_x_roundtrip.rs` または既存 `schema_v1_3_roundtrip.rs` を拡張
- Python: `python/tests/test_buying_power_ipc.py` を新規作成（`GetBuyingPowerCommand` / `BuyingPowerUpdatedEvent` の serialize/deserialize）

---

## Acceptance criteria（完了条件）

- [ ] 余力パネルの「更新」ボタンを押すと IPC 経由で現物余力・信用余力が取得されてパネルに表示される
- [ ] "余力情報取得: IPC 未実装（準備中）" の toast が出なくなる
- [ ] `cargo test --workspace` 全緑
- [ ] `uv run pytest python/tests/ -v` 全緑
- [ ] `/ipc-schema-check` スキルで SCHEMA_MINOR の Rust / Python 整合が確認できる
- [ ] `cargo clippy -- -D warnings` クリーン
- [ ] 実装完了後に `.claude/skills/review-fix-loop/SKILL.md` でレビューと修正を行う

---

## 進捗・知見記録（作業者が更新すること）

> 進捗があり次第ここに追記する。完了した項目には ✅ を付ける。

- ✅ Step 1: dto.rs Command/Event 追加 + SCHEMA_MINOR bump（1→2）
- ✅ Step 2: schemas.py 更新（GetBuyingPower / BuyingPowerUpdated モデル追加）
- ✅ Step 3: server.py dispatch 配線（`_do_get_buying_power` 実装）
- ✅ Step 4: main.rs ハンドラ実装（BuyingPowerAction IPC 送信 + BuyingPowerUpdated 受信）
- ✅ Step 5: ラウンドトリップテスト追加（schema_v2_1_roundtrip.rs / test_buying_power_ipc.py）
- ✅ Acceptance criteria 全項目確認（cargo test / pytest / clippy / ipc-schema-check 全緑）
- [ ] review-fix-loop 完了

### 新たな知見・設計判断・Tips

- `Message::BuyingPowerUpdated` には `request_id` / `venue` を含めない設計にした。
  これらは IPC ルーティング用で Rust UI 側では不要（全 BuyingPower ペインにブロードキャストするだけ）。
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
| `src/main.rs:1618` | `BuyingPowerAction` ハンドラ（scaffold のみ） |
| `docs/plan/✅order/implementation-plan.md` | Phase O3 T3.2/T3.4 の詳細 |
| `docs/plan/✅order/rust-ui-plan.md` | Phase U3 Tu3.1 の詳細 |
| `docs/plan/✅python-data-engine/spec.md §4.5.1` | IPC schema バージョニングルール |
| `docs/plan/✅python-data-engine/schemas/commands.json` | スキーマ JSON（要更新） |
| `docs/plan/✅python-data-engine/schemas/events.json` | スキーマ JSON（要更新） |
