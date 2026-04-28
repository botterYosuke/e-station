# Ladder 市場時間外表示修正

**作成日**: 2026-04-28  
**状態**: 実装完了（2026-04-28）

---

## Goal

市場時間外に Ladder（板）パネルが「Waiting for data...」のまま表示されない問題を修正する。  
REST スナップショット（bid=10, ask=10）が取得できているにも関わらず表示されていない。

## Constraints

- `docs/plan/✅tachibana/` 以下のドキュメントに沿った設計を維持すること
- IPC スキーマ (`SCHEMA_MAJOR/MINOR`) を変更しない
- 既存の Binance/Bybit 等の深度ストリームに影響を与えない

## Acceptance criteria

- [x] アプリ起動後、市場時間外でも Ladder に最終スナップショット（前場終了前の気配）が表示される
- [x] 「Waiting for data...」の代わりに市場時間外であることが伝わる表示になる（VenueError バナー）
- [x] 市場時間内に遷移したとき、ライブ FD フレームに切り替わる（iced subscription 自動再起動）
- [x] 既存テストがすべて PASS

---

## 調査済み内容

### 診断スクリプト結果（2026-04-28）

```
# uv run python -X utf8 scripts/diagnose_tachibana_ws.py --ticker 7203 --frames 3 --timeout 15
[PASS] ログイン成功
[PASS] bids=10 asks=10 を取得 (bid[0]={'price': '3067', 'qty': '13800'})
[PASS] bid[0] / ask[0] が dict 形式
[PASS] WebSocket 接続確立
[FAIL] KP フレーム受信 (count=0)   ← 市場時間外なので正常
受信フレーム一覧: ['ST', 'ST', 'ST']  ← ST のみ（時間外）
```

### Python 側の実装（既存）

[tachibana.py:784-826](../../../python/engine/exchanges/tachibana.py) に `stream_depth` の市場時間外パスが既に存在する：

1. `self._session is not None` なら `fetch_depth_snapshot` を呼び `DepthSnapshot` を送出
2. `VenueError { code: "market_closed" }` を送出（バナー表示）
3. `Disconnected { reason: "market_closed" }` を送出してリターン

### Rust 側の処理（既存）

- [engine-client/src/backend.rs:332-356](../../../engine-client/src/backend.rs): `DepthSnapshot` → `Event::DepthReceived` に変換
- [src/main.rs:1247-1255](../../../src/main.rs): `Event::DepthReceived` → `dashboard.ingest_depth()`
- [src/screen/dashboard/panel/ladder.rs:92-125](../../../src/screen/dashboard/panel/ladder.rs): `insert_depth()` で orderbook を更新
- [src/screen/dashboard/panel.rs:32-44](../../../src/screen/dashboard/panel.rs): `is_empty()` が true なら "Waiting for data..."

### 根本原因の仮説

**タイミング競合**: アプリ起動と同時にサブスクリプションが開始されるが、ログイン完了前（`self._session is None`）に `stream_depth` が呼ばれると `DepthSnapshot` が送出されない。

ログ確認事項（`~/AppData/Roaming/flowsurface/flowsurface-current.log`）:
- `VenueReady` のタイムスタンプ
- `a stream connected to tachibana WS` のタイムスタンプ
- DepthSnapshot 受信ログ（もし INFO レベルにある場合）

[src/main.rs:1141-1158](../../../src/main.rs): `VenueEvent::Ready` ハンドラは `refresh_streams` を呼ばない → セッション確立後もサブスクリプションが即座に再起動しない可能性。

### ストリーム解決フロー

1. ペインは `ResolvedStream::Waiting` で起動
2. 2秒毎に `due_streams_to_resolve` が呼ばれる
3. TickerInfo（`tickers_info`）がロードされていれば `StreamKind::Depth` に解決
4. DepthSubscription 開始 → Python `stream_depth` 呼び出し
5. `self._session is None` → DepthSnapshot なし → VenueError + Disconnected のみ

---

## 方針（実装前に確認が必要）

### Step 1: ログで現状確認（必須）

アプリを起動し、以下を確認する：

```bash
# アプリ起動後に確認
tail -200 "C:/Users/sasai/AppData/Roaming/flowsurface/flowsurface-current.log"
```

確認ポイント：
- `a stream connected to tachibana WS` が出るか（depth subscription が開始されているか）
- VenueReady のタイムスタンプ vs 最初の stream connected のタイムスタンプ
- DEBUG ログが必要な場合は `log::debug!` を追加してビルド

### Step 2-A: セッション確立後にサブスクリプションを確実に再起動

`VenueEvent::Ready` ハンドラ（[src/main.rs:1141](../../../src/main.rs)）で  
`self.handles.bump_generation()` を呼んでサブスクリプションを再起動させる。

⚠️ 注意: `bump_generation()` は全 venue のサブスクリプションを再起動する。  
Binance/Bybit への影響を評価すること。

### Step 2-B: VenueError → Ladder に market_closed 状態を伝播（UX改善）

`is_empty()` が true かつ市場時間外の場合、「市場時間外」と表示する：

```
// panel.rs の変更イメージ
pub fn view<T: Panel>(panel: &'_ T, ...) -> Element<'_, Message> {
    if panel.is_empty() {
        let msg = panel.placeholder_text();  // 新メソッド
        return center(text(msg).size(16)).into();
    }
```

`Ladder` に `market_closed: bool` フィールドを追加し、VenueError(market_closed) 受信時にセット。

---

## 実装記録（2026-04-28）

### 根本原因（確定）

- `due_streams_to_resolve`（2 秒毎）が VenueReady より先に `stream_depth` を呼ぶ
- `stream_depth` は市場時間外 + `_session is None` → DepthSnapshot なし・VenueError・Disconnected で返る
- ログ: `VenueReady` 着信後に depth subscription 再起動がない

### 採用した設計（rev.2 — review-fix-loop ラウンド 1 反映）

**`src/main.rs` — `VenueEvent::Ready` ハンドラ**

```rust
let old_state = std::mem::replace(&mut self.tachibana_state, VenueState::Idle);
let next = old_state.next(event);
let is_ready = next.is_ready();
self.tachibana_state = next;

if (old_state.is_login_in_flight() || matches!(old_state, VenueState::Error { .. }))
    && is_ready
{
    self.handles.bump_generation();
    log::info!("tachibana: session established — restarting subscriptions (gen bumped)");
}
```

- `LoginInFlight → Ready`（ログイン完了）または `Error → Ready`（再ログイン / 市場再開）の場合のみバンプ
- `Idle → Ready`（EngineConnected 後の合成）はスキップ → EngineConnected がすでにバンプ済みのため二重バンプを防止
- `Ready → Ready`（冪等）もスキップ

**`src/main.rs` — `MarketWsEvent::Connected` ハンドラ（M2 追加）**

```rust
if let exchange::Event::Connected(exchange::adapter::Exchange::TachibanaStock) = &event {
    if matches!(self.tachibana_state, VenueState::Error { .. }) {
        return Task::done(Message::TachibanaVenueEvent(VenueEvent::Ready));
    }
}
```

- 市場再開後に depth ストリームが再接続した際、`Error(market_closed)` バナーを自動クリアする

### 他 Venue への影響

- `bump_generation()` はすべての venue サブスクリプション ID を変更する
- VenueReady はアプリ起動直後・再ログイン時・Python 再起動後に発生する（起動直後のみではない）
- Binance/Bybit の brief reconnect は許容範囲（セッション再確立後に全 subscription が再起動するのは意図的）

### 既知の制限（繰越）

- **M3 — VenueError の FSM ルーティング**: `classify_venue_error` の全エラーが `LoginError` として FSM に入るため、`Ready` 状態から `Error` への遷移が起きる。市場時間外の `market_closed` はその一例。FSM に `OperationalError`（セッション維持）と `AuthError`（セッション破棄）を区別するバリアントを追加することで根本解決できるが、FSM リファクタリングを要するため次イテレーション以降に持ち越す。

### UX フロー（修正後）

1. VenueReady → FSM: Ready → `bump_generation()`
2. 新しい depth subscription → stream_depth（session あり、市場時間外）
3. Python: DepthSnapshot + VenueError(market_closed) + Disconnected
4. Ladder: スナップショット表示（bid/ask 気配）
5. バナー: 「東証は現在市場時間外です」
6. iced は subscription 完了後に同一 ID で自動再試行 → 市場オープン後は live FD に切り替わる

---

## 関連ファイル

| ファイル | 内容 |
|---------|------|
| `python/engine/exchanges/tachibana.py` | `stream_depth` 市場時間外パス |
| `engine-client/src/backend.rs` | `depth_stream` / DepthSnapshot 処理 |
| `src/main.rs` | VenueEvent::Ready ハンドラ, DepthReceived ルーティング |
| `src/screen/dashboard/panel.rs` | "Waiting for data..." 表示 |
| `src/screen/dashboard/panel/ladder.rs` | `insert_depth`, `is_empty` |
| `src/screen/dashboard/pane.rs` | ストリーム解決, Ladder ペイン |
| `src/connector/stream.rs` | `ResolvedStream` / `due_streams_to_resolve` |
| `exchange/src/adapter/client.rs` | `bump_generation` |
| `scripts/diagnose_tachibana_ws.py` | 診断スクリプト |
