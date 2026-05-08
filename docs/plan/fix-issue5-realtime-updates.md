# 修正計画書: Issue 5 — replay Positions パネルを end-to-end で成立させる

> **✅ 実装完了 (2026-05-08)**

## 問題の全体像

「replay 中に保有銘柄が更新されない」は、以下 **4 層の欠落** が重なっている。

| 層 | 場所 | 問題 |
|---|---|---|
| **emit** | Python `server.py` / `engine_runner.py` | StepBackward/Forward 後に `PositionsUpdated` を push していない |
| **receive** | `src/handlers/venue.rs:523-541` | `request_id` 不一致（push 型は `""` だが `positions_request_id` は `None`）で即 `return` |
| **distribute** | `src/screen/dashboard.rs:919` | `!panel.is_replay()` で replay の Positions pane を除外 |
| **render** | `src/screen/dashboard/panel/positions.rs:111` | `is_replay()` なら常にバナーを返しデータを描画しない |

これに加え、replay の `Positions` pane は **自動生成されない**（`OrderList` と `BuyingPower` だけ生成）。

---

## スコープの決定

- **対象**: replay モードの `Positions` pane を end-to-end で動作させる
- **自動生成**: `OrderList` / `BuyingPower` と同様に replay 開始時に自動生成する（scope に含める）
- **live pane**: 既存の pull 型フロー（`GetPositions` → `PositionsUpdated`）は一切変更しない
- **emit タイミング**: 「毎足」ではなく **状態変化時**（fill / restore）に限定して emit する

---

## 修正方針

### Step 1: Python — wire schema の確定

既存の `server.py:2778-2794` を参照し、push 型 emit も同じフォーマットに揃える。

**必須フィールド**（Rust側 deserialize と合わせること）:

```python
{
    "event": "PositionsUpdated",
    "request_id": "",          # push 型は空文字
    "venue": "replay",
    "positions": [
        {
            "instrument_id": "...",
            "qty": "100",
            "market_value": "500000",  # 不明なら ""
            "position_type": "Long",
            "tategyoku_id": "...",
            "venue": "replay",
        }
    ],
    "ts_ms": ts_ms,            # REQUIRED — 省略すると Rust で deserialize エラー
}
```

ヘルパー `_build_replay_positions(ts_ms: int) -> dict` を `server.py` に定義し、
各 emit 箇所から呼ぶ。スナップショット構造に `positions` が含まれる場合は直接利用可。

### Step 2: Python — StepBackward に emit 追加（`server.py`）

`server.py:1191-1195` の `OrderListUpdated` emit 直後（`ReplayBuyingPower` 後）に追加:

```python
# 2.8: PositionsUpdated — スナップショット復元後のポジションを push
ts_ms_pos = int(time.time() * 1000)
self._outbox.append(self._build_replay_positions(ts_ms_pos))
```

### Step 3: Python — fill 時に emit 追加（`engine_runner.py`）

`engine_runner.py:740-745` の `emit(bp_dict)` の直後に追加する（fill 単位で emit）:

```python
# fill 後のポジション状態を push emit（毎足 emit より意味的に正確）
pos_dict = server._build_replay_positions(ts_ms)
emit(pos_dict)
```

> **Play 中の emit 方針**: `_on_bar` ハンドラ（line:783）には追加しない。
> Daily/Minute は fill 契機で十分。Trade replay で tick 単位の bar がない場合も fill 契機のみ。
> これにより「tick 単位の毎足」という過剰 emit を避けられる。

### Step 4: Rust — push 型 receive を venue.rs に追加

`src/handlers/venue.rs:523-541` の `PositionsUpdated` アーム:

```rust
VenueMsg::PositionsUpdated {
    request_id,
    venue: _,
    positions,
    ts_ms,
} => {
    // push 型（request_id が空）は replay パネルへ配布
    if request_id.is_empty() {
        let main_window = self.main_window.id;
        self.active_dashboard_mut()
            .distribute_replay_positions(main_window, positions, ts_ms);
        return Task::none();
    }

    // pull 型: 既存の request_id 照合ロジック（live pane）
    let matches = self.positions_request_id.as_deref() == Some(request_id.as_str());
    if !matches {
        log::debug!(
            "[PositionsUpdated] stale/unrouted: request_id={request_id:?}, \
             in-flight={:?}",
            self.positions_request_id
        );
        return Task::none();
    }
    self.positions_request_id = None;
    let main_window = self.main_window.id;
    self.active_dashboard_mut()
        .distribute_positions(main_window, positions, ts_ms);
}
```

### Step 5: Rust — distribute_replay_positions を dashboard.rs に追加

`src/screen/dashboard.rs:924` の直後に新メソッドを追加:

```rust
/// Distribute a fresh positions snapshot to all *replay* `Positions` panes.
pub fn distribute_replay_positions(
    &mut self,
    main_window: window::Id,
    positions: Vec<engine_client::dto::PositionRecordWire>,
    ts_ms: i64,
) {
    self.iter_all_panes_mut(main_window)
        .for_each(|(_, _, state)| {
            if let pane::Content::Positions(panel) = &mut state.content
                && panel.is_replay()
            {
                panel.set_positions(positions.clone(), ts_ms);
            }
        });
}
```

既存の `distribute_positions`（live 用）はそのまま残す。

### Step 6: Rust — positions.rs の replay バナーを描画ロジックに置き換え

`src/screen/dashboard/panel/positions.rs:111-115`:

```rust
// 変更前
if panel.is_replay() {
    return column![header, center(text("⏪ REPLAY — 保有銘柄なし").size(13)),]
        .height(iced::Length::Fill)
        .into();
}

// 変更後: replay でもデータがあれば通常描画、なければプレースホルダー
if panel.is_replay() && panel.positions.is_empty() {
    return column![header, center(text("⏪ REPLAY — 保有銘柄なし").size(13)),]
        .height(iced::Length::Fill)
        .into();
}
```

`set_positions` が呼ばれて `positions` が入ったら通常の描画パスに進む。

### Step 7: Rust — replay Positions pane を自動生成（dashboard.rs）

`src/screen/dashboard.rs:1296` の `BuyingPower` 自動生成ブロックの直後に追加:

```rust
// N1.17: セッションレベルの REPLAY 保有銘柄 pane は最初の1銘柄ロード時のみ生成。
if is_first
    && self.replay_pane_registry.loaded_count() == 1
    && self.replay_pane_registry.should_generate("", "Positions")
{
    let new_state = pane::State::new_replay_positions();
    if let Some((new_pane, _)) =
        self.panes
            .split(pane_grid::Axis::Horizontal, last_split_pane, new_state)
    {
        log::info!("replay: auto-generated REPLAY Positions pane");
        self.replay_pane_registry
            .register_pane("", "Positions", new_pane);
        self.focus = Some((main_window_id, new_pane));
    } else {
        log::warn!(
            "auto_generate_replay_panes: pane split failed for Positions \
             (session-level pane skipped silently otherwise)"
        );
    }
}
```

`pane::State::new_replay_positions()` の実装が必要（`new_replay_buying_power` と同パターン）。

### Step 8: 既存 Finished ハンドラは存続

`handlers/replay.rs:145-178` の `Finished` 後の `GetOrderList` 発行は変更しない。
最終状態確定表示として残す。

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `python/engine/server.py` | `_build_replay_positions()` ヘルパー追加、StepBackward に emit 追加 |
| `python/engine/nautilus/engine_runner.py` | `_on_order_filled` の fill emit 後に `PositionsUpdated` emit 追加 |
| `src/handlers/venue.rs:523` | push 型（`request_id == ""`）を replay 配布に分岐 |
| `src/screen/dashboard.rs` | `distribute_replay_positions()` 追加、replay Positions pane 自動生成追加 |
| `src/screen/dashboard/panel/positions.rs:111` | replay バナーを `positions.is_empty()` 条件付きに変更 |
| `src/screen/pane.rs` (推定) | `State::new_replay_positions()` 追加 |

---

## 受け入れ条件

- [ ] `StepBackward` 後に `PositionsUpdated` が `ts_ms` 付きで送出される
- [ ] fill が発生した `StepForward` / Play 後にも `PositionsUpdated` が送出される
- [ ] replay `Positions` pane が push イベントを受けてポジション一覧を表示する
- [ ] live `Positions` pane の既存 pull 型フロー（GetPositions→PositionsUpdated）が壊れない
- [ ] Trade/Daily/Minute のいずれも bar 単位の過剰 emit が発生しない（fill 単位のみ）
- [ ] replay 開始時に `Positions` pane が自動生成される

---

## 実装難易度

**中〜高**。Python のヘルパー実装とスナップショット構造の確認が必要。
Rust 側は 4 ファイルへの分散修正だが、各変更は独立しており TDD で進めやすい。
