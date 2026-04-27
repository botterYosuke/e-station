# 立花ログインテスト完成計画

## 背景・目的

立花証券ログイン機能のテスト階層（Layer 3: `update()` 層）を完成させ、
`no_session` バグ修正を E2E で確認する。

## テスト階層

| 層 | 場所 | 状態 |
|---|---|---|
| Layer 1: FSM unit tests | `src/widget/venue_banner.rs`, `src/screen/dashboard/tickers_table.rs` | ✅ 完了 |
| Layer 2: direct `update()` / `view()` 呼び出し | 同上 | ✅ 完了 |
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
- ✅ `tests/tachibana_login_update.rs` 作成（4 テスト: try_claim / RequestVenueLogin / TachibanaLoginIpcResult / Task::none）
- ✅ `cargo test --workspace` 全グリーン
- ✅ `uv run pytest python/tests/ -q` 全グリーン（775 passed, 2 skipped）
- ✅ E2E スクリプト実行 PASS（VenueReady / dev fast path / is_demo=True 全確認、no_session エラーなし）
- [ ] `/review-fix-loop` スキルでレビュー

## 設計上の判断

**なぜ構造的ピンか**: iced の `Task::perform` は実行時にしか評価されない非同期 future を返す。
`Flowsurface::update()` をユニットテストで呼び出すには GUI ランタイム全体が必要で、
CI での再現が困難。構造的ピンはソースコード不変条件として「実装が意図を外れていないか」を
コンパイル・テスト時に assert する代替手段として採用。

**ピンの粒度**: ハンドラ本体を 3000 バイトのウィンドウでスライスすることで、
他のハンドラの実装が混入しないよう境界を設定する。
`RequestTachibanaLogin` アーム本体は ~2378 バイト（2026-04 計測）であり、
3000 バイトは 1 アーム分を確実に収める余裕値として選択した。

---

## レビュー反映 (2026-04-27, R1)

### 解消した指摘
- H1: tachibana_login_flow.py:259 — result dict ログ出力を status のみに修正
- H2: no_session リグレッションテスト追加（`_workers["tachibana"]._session` の同期確認）
- H3: invariant-tests.md に T35-LoginUpdate 登録
- H4: 計画書「1500バイト」→「3000バイト」修正 + コード内コメント追加
- M1: last_line ログを先頭40文字に制限
- M2: LoginError の for ループ内 raise を _venue_error_event + return に統一
- M3: TachibanaLoginIpcResult(Err) 直接代入に説明コメント追加
- M4: request_id 生成コメント追加
- M5: 計画書 Layer 2 説明を実態（direct update()/view() パターン）に修正
- M6: set_session docstring から陳腐化 TODO コメント除去
- M7: _apply_tachibana_session docstring「every API call」を修正
- M8: engine_connection 因果関係チェック強化
- M9: TokioMutex テストに #[tokio::test] 追加

### LOW 繰越
- L1: user_id ログマスク（次フェーズで検討）
- L2: review-fix-loop チェックボックス（このブロック追記で対応）
- L3-L6: 次フェーズで検討
- R2-M1: tachibana_login_flow.py:229-231 — stderr ログ上限 [:1000] 追加（R2 で修正）
- R2-M2: test_tachibana_apply_session_sync.py — zyoutoeki_kazei_c="" → "0" sentinel 化（R2 で修正）

## R3 サニティ (2026-04-27)

MEDIUM 以上ゼロを確認。収束。

- `cargo fmt --check` / `cargo clippy -D warnings` / `cargo test --workspace`: 全グリーン
- `uv run pytest python/tests/ -q`: 776 passed, 2 skipped
- ✅ `/review-fix-loop` 完了
