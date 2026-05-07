# Phase 3 実装作業依頼

## 前提

e-station (`C:\Users\sasai\Documents\e-station`) の kabuStation venue 統合 Phase 3 の実装依頼です。
Phase 1（リードオンリー）・Phase 2（株式現物・信用 発注／取消）はすでに完了しています。

**必読ドキュメント（作業前に必ず全部読むこと）:**

| ファイル | 内容 |
|---|---|
| `docs/✅kabusapi/implementation-plan.md` | Phase 1/2 完了済みタスク・R1〜R7 レビュー反映履歴 |
| `docs/✅kabusapi/plan.md` | Phase 3 定義（§2 Phase 3 セクション）・ファイル別追加計画 |
| `docs/✅kabusapi/architecture.md` | Python 自律方式・IPC ライフサイクル・禁則リスト |
| `docs/✅kabusapi/open-questions.md` | 未確定事項（Q-K3, Q-K4, Q-P2-5 など） |
| `docs/✅kabusapi/data-mapping.md` | API フィールドマッピング |
| `docs/✅kabusapi/spec.md` | スコープ・非ゴール |

---

## Goal（目的）

kabuStation Phase 3 を実装する。

- **先物・OP 発注**: `POST /sendorder/future` / `POST /sendorder/option`
- **先物・OP 余力照会**: `GET /wallet/future` / `GET /wallet/option`
- **銘柄名照会**: `GET /symbolname/*`
- **市場細分化**: `Exchange::KabuStationStock` を東証(1) / 名証(3) / 福証(5) / 札証(6) および先物・OP 系バリアントに拡張（Q-K4 解決）

---

## Constraints（制約）

- **検証環境 `localhost:18081` のみ**。本番 `localhost:18080` の経路はまだ作らない（Phase 4）
- **URL リテラルは `python/engine/exchanges/kabusapi_url.py` のみ**（`endpoint()` 経由で参照）
- **Rust 変更は最小限**: `exchange/src/adapter.rs` の `Exchange` enum 拡張のみ。`src/` 配下に kabu 固有コードを書かない
- **シークレットをログ・テスト・コメントに含めない**（INV-K2-NO-LOG-SECRET 遵守）
- **`uv run pytest`** でテスト実行（裸の `python` 不可）
- Phase 2 の既存テストを壊さない
- `cargo check --workspace` / `cargo clippy --workspace -- -D warnings` / `cargo fmt --check` / `cargo test --workspace` 全通過

---

## Acceptance criteria（完了条件）

1. `uv run pytest python/tests/ -q` が全件グリーン（既存テスト含む）
2. `cargo test --workspace` 全件グリーン
3. 先物発注 (`/sendorder/future`) が検証環境でエンドツーエンド動作すること（HTTPXMock テストで検証可）
4. OP 発注 (`/sendorder/option`) 同上
5. `Exchange` enum の市場細分化バリアントが Rust の網羅 match を通過
6. `docs/✅kabusapi/implementation-plan.md` に Phase 3 完了記録が追記されていること（完了タスクに ✅）

---

## 作業手順

### 1. 計画書に Phase 3 タスク表を追記

`docs/✅kabusapi/implementation-plan.md` の末尾に「Phase 3 タスク詳細」セクションを追加し、
作業を進めながら進捗（✅ / 🔄 / —）・知見・設計決定を随時書き込む。

### 2. TDD で実装

`.claude/skills/tdd-workflow/SKILL.md` の手順（RED → GREEN → REFACTOR）に従う。

### 3. 並行実装

タスク間に依存がない場合は `.claude/skills/parallel-agent-dev/SKILL.md` で並列 agent を使う。

### 4. レビュー・修正ループ

実装完了後、`.claude/skills/review-fix-loop/SKILL.md` を起動して MEDIUM+ 指摘をゼロにする。

---

## 実装スコープ詳細（`plan.md §2 Phase 3` 参照）

```
/sendorder/future   — 先物発注 (POST)
/sendorder/option   — OP 発注 (POST)
/wallet/future      — 先物余力照会 (GET)
/wallet/option      — OP 余力照会 (GET)
/symbolname/*       — 銘柄名照会 (GET)

Exchange enum 拡張:
  KabuStationStock  → 現行（東証デフォルト相当）
  KabuStationTse    — 東証 (exchange=1)
  KabuStationNse    — 名証 (exchange=3)
  KabuStationFse    — 福証 (exchange=5)
  KabuStationSse    — 札証 (exchange=6)
  KabuStationFuture — 先物
  KabuStationOption — OP
```

既存 `KabuOrderClient` を拡張する形で `send_order_future()` / `send_order_option()` を追加し、
server.py の IPC ルーティングに `venue="kabu_station"` + `instrument_type="future"/"option"` 分岐を追加する。

---

## 既存資産（流用可）

- `python/engine/exchanges/kabusapi_orders.py` — `KabuOrderClient`（株式発注 Phase 2）
- `python/engine/exchanges/kabusapi_auth.py` — `KabuTradePasswordHolder` / `check_response` / エラー型
- `python/engine/exchanges/kabusapi_url.py` — `endpoint()` / `KabuEnv`
- `python/engine/exchanges/kabusapi_ratelimit.py` — `OrderBucket` / `InfoBucket`
- `python/tests/test_kabusapi_orders.py` — 株式発注テスト（パターン参考）
- `python/tests/test_kabu_server_orders.py` — IPC ルーティングテスト（パターン参考）
