# POST /api/replay/start 実装計画

## Goal

`POST /api/replay/start` HTTP エンドポイントを新設し、Rust UI を起動した状態で
BuyAndHold 戦略の streaming バックテストをスクリプトからフル自動化できるようにする。

完成後のフロー:

```
POST /api/replay/load   → データロード + pane 自動生成
POST /api/replay/start  → BuyAndHold を streaming 起動
 ↓
 Rust UI でリアルタイムに:
   - ローソク足が流れる (1x/10x/100x 速度)
   - 約定マーカーがチャートに表示される (N1.12)
   - 注文一覧 pane に仮想約定が追加される (N1.15)
   - 買付余力 pane が更新される (N1.16)
 ↓
GET /api/replay/portfolio → 最終 equity / PnL が取得できる
```

## Constraints

- 既存のテストを壊さない (`cargo test --workspace` / `uv run pytest python/tests/` が GREEN を維持)
- `POST /api/replay/load` の挙動は変更しない（データ件数確認 + pane 生成のみ）
- `StartEngine` IPC コマンドは既存の `_handle_start_engine`（server.py）をそのまま呼ぶ
- `serde deny_unknown_fields` など既存の IPC スキーマ契約を変えない
- `SCHEMA_MAJOR` を bump しない（追加フィールドなし、既存コマンドを呼ぶだけ）

## Acceptance criteria

1. `POST /api/replay/start` に以下の JSON を投げると HTTP 200 が返る:
   ```json
   {
     "strategy_id": "buy-and-hold",
     "instrument_id": "7203.TSE",
     "start_date": "2024-01-01",
     "end_date": "2024-12-31",
     "granularity": "Daily",
     "initial_cash": 1000000
   }
   ```
   レスポンス例:
   ```json
   {"status": "started", "strategy_id": "buy-and-hold"}
   ```

2. Rust UI が `--mode replay` で起動している状態で上記を呼ぶと、
   Tick / Candlestick pane に価格データが流れる

3. `GET /api/replay/portfolio` が最終的に `final_equity` を含む JSON を返す

4. `docs/example/run_buy_and_hold_backtest_with_ui.py` を実行すると
   スクリプトが自動で load → start → portfolio 取得まで走りきる

5. 単体テスト（Rust + Python）が追加されており GREEN

---

## 実装方針

### Rust 側 (`src/replay_api.rs`)

`handle_replay_start` 関数を追加し、ルータに `("POST", "/api/replay/start")` を追加する。

受け取る JSON:

```rust
#[derive(serde::Deserialize)]
struct ReplayStartBody {
    strategy_id: String,
    instrument_id: String,
    start_date: String,
    end_date: String,
    granularity: ReplayGranularity,
    initial_cash: u64,
    #[serde(default = "default_speed")]
    speed: u32,   // 1 / 10 / 100
}
fn default_speed() -> u32 { 1 }
```

内部では `Command::StartEngine { engine: EngineKind::Backtest, ... }` を送出し、
`EngineEvent::EngineStarted` を待つ（タイムアウト 30s）。

### Python 側 (`python/engine/server.py`)

`_handle_start_engine` は既に実装済み。追加作業なし。

### `docs/example/run_buy_and_hold_backtest_with_ui.py` の更新

現状は `load` まで。`start` 呼び出しと `portfolio` ポーリングを追加する。

```python
# load 後に追加
status, resp = _http("POST", f"http://127.0.0.1:{API_PORT}/api/replay/start", {
    "strategy_id": "buy-and-hold",
    "instrument_id": INSTRUMENT_ID,
    "start_date": START_DATE,
    "end_date": END_DATE,
    "granularity": GRANULARITY,
    "initial_cash": INITIAL_CASH,
    "speed": REPLAY_SPEED,
})

# EngineStopped まで portfolio をポーリング
...
```

---

## 関連ファイル

| ファイル | 役割 |
|---|---|
| `src/replay_api.rs` | HTTP ルータ（追加先） |
| `engine-client/src/dto.rs` | `Command::StartEngine` / `EngineStartConfig` の定義 |
| `python/engine/server.py` | `_handle_start_engine`（既存、変更不要） |
| `docs/example/run_buy_and_hold_backtest_with_ui.py` | デモスクリプト（更新対象） |
| `docs/✅nautilus_trader/` | 設計ドキュメント群（参照専用） |

---

## 進捗ログ

| 日付 | 作業者 | 内容 |
|---|---|---|
| 2026-04-29 | botterYosuke | 計画書作成 |

