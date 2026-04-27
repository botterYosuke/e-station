# 立花ログインテスト完成計画

## 背景・目的

立花証券ログイン機能のテスト階層（Layer 3: `update()` 層）を完成させ、
`no_session` バグ修正を E2E で確認する。

## テスト階層

| 層 | 場所 | 状態 |
|---|---|---|
| Layer 1: FSM unit tests | `src/widget/venue_banner.rs`, `src/screen/dashboard/tickers_table.rs` | ✅ 完了 |
| Layer 2: iced_test シミュレータ | 同上 | ✅ 完了 |
| Layer 3: update() 構造的ピン | `tests/tachibana_login_update.rs` | ← 本作業 |
| Layer 4: E2E smoke | `tests/e2e/tachibana_demo_login.sh` | ← 実行確認 |

## 実装方針

### Layer 3: 構造的ピンの方針

`Flowsurface` 構造体は `window::open()` / `LayoutManager` 等の重依存があるため、
フル構造体インスタンス化は困難。既存パターン（`venue_ready_bridge_invalidates_on_login_events.rs` 等）
と同様に **ソースコードレベルの構造的ピン** で実装する。

ソースコードを文字列として読み込み、以下を assert する：

1. **`RequestTachibanaLogin` → `try_claim_login_in_flight()` ピン**
   - `Message::RequestTachibanaLogin` ハンドラ本体内に `try_claim_login_in_flight()` の呼び出しが存在すること
   - これにより「二重押し抑制」ロジックが update() に残り続けることを保証

2. **`Command::RequestVenueLogin` ピン**
   - `RequestTachibanaLogin` ハンドラが `Command::RequestVenueLogin` を IPC 送信していること
   - 名前変更・削除による無音の regression を防ぐ

3. **`TachibanaLoginIpcResult` フックピン**
   - `Task::perform(...)` の callback が `Message::TachibanaLoginIpcResult` であること
   - IPC 失敗時のロールバックロジック（`is_login_in_flight` → `Idle`）が hook されている証明

4. **未接続時早期リターンピン**
   - `engine_connection` が None のとき `Task::none()` で返すガード節が存在すること
   - 接続なしでの IPC 送信試みを防ぐ

### Layer 4: E2E スクリプト実行

```bash
# .env に設定後:
set -a && source .env && set +a
cargo build
bash tests/e2e/tachibana_demo_login.sh
```

確認ポイント:
- VenueReady が Rust ログに出る
- `using dev env fast path` が Python ログに出る
- `is_demo=True` が Python ログに出る
- **`no_session` エラーが出ない**（`_apply_tachibana_session` 修正の検証）

## 進行ログ

- ✅ 計画書作成
- ✅ `tests/tachibana_login_update.rs` 作成
- [ ] `cargo test --workspace` グリーン確認
- [ ] `uv run pytest python/tests/ -q` グリーン確認
- [ ] E2E スクリプト実行（`.env` 設定後）
- [ ] `/review-fix-loop` スキルでレビュー

## 設計上の判断

**なぜ構造的ピンか**: iced の `Task::perform` は実行時にしか評価されない非同期 future を返す。
`Flowsurface::update()` をユニットテストで呼び出すには GUI ランタイム全体が必要で、
CI での再現が困難。構造的ピンはソースコード不変条件として「実装が意図を外れていないか」を
コンパイル・テスト時に assert する代替手段として採用。

**ピンの粒度**: ハンドラ本体を 1500 バイトのウィンドウでスライスすることで、
他のハンドラの実装が混入しないよう境界を設定する。
