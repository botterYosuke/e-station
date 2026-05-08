# 修正計画書: Issue 2 — 保有銘柄ペインをリプレイ画面に追加

> **✅ 実装完了 (2026-05-08)**
>
> R1 反映ノート（2026-05-08）: 提供レビューで判明した 4 件の HIGH と 1 件の MEDIUM を反映。
> 「pane 自動生成漏れ」だけでは不十分で、(a) `PositionsPanel::view()` の REPLAY 早期 return、
> (b) `VenueMsg::PositionsUpdated` の request_id ガード、(c) `distribute_positions()` の
> replay pane 除外、(d) Python 側スナップショット構造から `PositionRecordWire` 互換配列への
> 変換、の 4 経路すべてを直さないと UI まで届かない。さらに前進再生中の authoritative
> emit ポイントを `engine_runner.py` 側 (_on_bar / fill_handler) に固定する。

## 根本原因（4 経路）

リプレイ画面で保有銘柄が表示されない原因は **単一ではなく以下 4 経路の連鎖**:

1. **pane 自動生成漏れ** (`screen/dashboard.rs:1270-1319` `auto_generate_replay_panes`)
   OrderList (N1.15) と BuyingPower (N1.16) は生成されるが Positions pane は生成されない。
2. **view() が REPLAY 専用バナーで早期 return** (`panel/positions.rs:111-115`)
   `panel.is_replay()` のとき `column![header, center(text("⏪ REPLAY — 保有銘柄なし"))]` を
   常に返しており、たとえ `set_positions()` でデータが入っても画面に出ない。
3. **request_id ガードで push が破棄される**
   - `main.rs:1434-1445` で `EngineEvent::PositionsUpdated` → `VenueMsg::PositionsUpdated` の
     変換は通るが、
   - `handlers/venue.rs:523-542` で `self.positions_request_id.as_deref() == Some(request_id.as_str())`
     と一致しない場合 (`request_id == ""` 含む) は drop される。
   - 計画当初の `handlers/replay.rs` 修正案は経路違い。
4. **distribute_positions() が replay pane を除外** (`screen/dashboard.rs:910-924`)
   `if let pane::Content::Positions(panel) = ... && !panel.is_replay()` で replay pane に
   `set_positions()` が伝搬しない。経路 (3) を通しても更新されない。

加えて Python 側:

- **スナップショット構造不一致** (`server.py:131-146 ReplaySnapshot` / `:586-593 push_snapshot`)
  `ReplaySnapshot.positions` フィールドは存在しない。保持しているのは
  `portfolio_state["positions"] = {instrument_id -> {qty: str, cost: str}}` であり、
  IPC 用 `PositionsUpdated.positions` の `PositionRecordWire` 互換配列とは形式が違う。
- **前進再生中の authoritative emit が未確立**
  `engine_runner.py:771-820 _on_bar` と fill_handler 経由で `ReplayBuyingPower` は emit するが
  `PositionsUpdated` は emit していない。StepBackward だけ修正しても、Play 中・StepForward
  完走後は更新されない半端状態になる。

## 統一決定

- **D1 — authoritative emit point**: 前進再生時の `PositionsUpdated` emit は
  **Python 側 `engine_runner.py` の fill_handler および `_on_bar` で行う**（`ReplayBuyingPower`
  と同じタイミング・同じトリガ）。`server.py` の StepBackward では snapshot 復元後に同等
  ペイロードを再 emit する。
- **D2 — IPC ペイロード形式**: `PositionsUpdated.positions` は **`PositionRecordWire` 互換
  配列** で送る。`engine-client/src/dto.rs:752-768` の実フィールドは
  `{instrument_id: String, qty: String, market_value: String, position_type: PositionType,
  tategyoku_id: Option<String>, venue: String}` で `#[serde(deny_unknown_fields)]`。
  `position_type` は `#[serde(rename_all = "snake_case")]` で wire 値は `"cash"` /
  `"margin_credit"` / `"margin_general"`。`portfolio_state["positions"]` の
  `{iid: {qty, cost}}` を engine_runner / server で配列に変換するが、`cost` フィールドは
  schema に存在しないため **`market_value` に詰める**（`last_price * qty` を整数文字列で。
  不明時は `""`）。`venue` は **`"replay"`** を許容する必要がある。これは 2 箇所修正:
  (a) `engine-client/src/dto.rs:766` のコメントを「`"tachibana"` または `"replay"`」に更新、
  (b) `python/engine/schemas.py:950` の `venue: Literal["tachibana"]` を
  `venue: Literal["tachibana", "replay"]` に拡張（Pydantic validation で reject されないため
  必須）。schema MINOR バンプは不要（既存フィールド組み換え + Literal 拡張のみで
  wire 互換は保たれる）。`PortfolioView` に変換ヘルパを追加する。
  `qty` / `market_value` は **整数文字列** が要求される（schemas.py:946-947）ので
  `str(int(decimal_value))` で正規化する（`Decimal` の素の `str()` は `"100.0"` や
  指数表記を生むため危険）。
- **D3 — replay pane への broadcast**: `distribute_positions()` の `!panel.is_replay()`
  ガードを外し、replay pane にも伝搬させる（オプションの分離関数を新設するのではなく
  既存関数を全 pane に統一）。live pane と replay pane を venue 値で区別する必要は無い
  （`PositionsPanel` 内部の `is_replay()` フラグは描画のみに使う）。
- **D4 — request_id ガード**: replay の push 型 `PositionsUpdated` は `request_id == ""`
  で来る。`handlers/venue.rs:523` のマッチを **「空 request_id は in-flight 比較を
  バイパスして broadcast に進める」** に変える（OrderListUpdated と整合）。
- **D5 — view() の描画**: `panel.is_replay()` の早期 return を削除し、live と同じ
  「positions 配列が空なら『保有なし』、非空なら一覧描画」フローに統合する。
  REPLAY バッジは header 行に小さく付ける（live と区別したい場合）。
- **D6 — Finished 後の表示維持**: リプレイ終了後も `PositionsPanel` は最終ポジションを
  保持し続ける（クリアしない）。

## 影響範囲

| ファイル | 変更点 | 経路 |
|---|---|---|
| `src/screen/dashboard.rs:1270-1319` | `auto_generate_replay_panes` に Positions ブロック追加（N1.17） | (1) |
| `src/screen/dashboard.rs:910-924` | `distribute_positions()` の `!panel.is_replay()` ガード削除 | (4) |
| `src/screen/dashboard/panel/positions.rs:111-115` | `view()` の REPLAY 早期 return 撤去 / header に REPLAY バッジ | (2) |
| `src/screen/dashboard/pane.rs` | `pane::State::new_replay_positions()` 追加（無ければ） | (1) |
| `src/handlers/venue.rs:523-542` | 空 `request_id` は guard をバイパスして broadcast | (3) |
| `python/engine/nautilus/portfolio_view.py:113-126` | `to_position_records()` 新設（`PositionRecordWire` 互換配列を返す） | D2 |
| `python/engine/nautilus/engine_runner.py:771-820, fill_handler` | `_on_bar` / fill_handler に `PositionsUpdated` emit を追加 | D1 |
| `python/engine/server.py:1175-1208 StepBackward` | snapshot 復元後に `PositionsUpdated` を emit（`portfolio_state["positions"]` を D2 で変換） | D1 |
| `python/engine/server.py:131-146 ReplaySnapshot` | （変更不要）`portfolio_state` を再利用 | — |

`src/handlers/replay.rs` は触らない（経路違い）。`src/main.rs:1434` の変換は既に通るので
そのまま。

## 修正方針（ステップ順）

実装は **Rust 側経路 (1)(2)(3)(4) と Python 側経路 D1/D2 が両方揃わないと観測できない**
ため、PR は両方同梱する。Step 順は依存度順:

### Step 1: `pane::State::new_replay_positions()` の確認・追加

`pane.rs` の `new_replay_order_list()` と同パターン。

```rust
pub fn new_replay_positions() -> Self {
    Self {
        content: Content::Positions(PositionsPanel::new_replay()),
        ..Self::default()
    }
}
```

### Step 2a: `ReplayPaneRegistry` の whitelist に "Positions" を追加

`should_generate("", "Positions")` が常に false を返すと pane が永遠に生成されない。
`replay_pane_registry` の許可キー集合に **`"Positions"`** が含まれているかを確認し、
無ければ追加する（`OrderList` / `BuyingPower` と同列の登録）。これは Step 2 の前提条件。

### Step 2: `auto_generate_replay_panes` に Positions ブロック追加（N1.17）

BuyingPower 生成ブロック (`:1298-1319`) の直後に追加:

```rust
// N1.17: セッションレベルの REPLAY 保有銘柄 pane は最初の1銘柄ロード時のみ生成。
if is_first
    && self.replay_pane_registry.loaded_count() == 1
    && self.replay_pane_registry.should_generate("", "Positions")
{
    let new_state = pane::State::new_replay_positions();
    if let Some((new_pane, _)) =
        self.panes.split(pane_grid::Axis::Horizontal, last_split_pane, new_state)
    {
        log::info!("replay: auto-generated REPLAY Positions pane");
        self.replay_pane_registry.register_pane("", "Positions", new_pane);
        self.focus = Some((main_window_id, new_pane));
        last_split_pane = new_pane;
    } else {
        log::warn!("auto_generate_replay_panes: pane split failed for Positions");
    }
}
```

### Step 3: `PositionsPanel::view()` の REPLAY 早期 return を撤去（D5）

`panel/positions.rs:111-115` の以下を削除:

```rust
// 削除する
if panel.is_replay() {
    return column![header, center(text("⏪ REPLAY — 保有銘柄なし").size(13)),]
        .height(iced::Length::Fill)
        .into();
}
```

REPLAY モードの視覚区別は header に小さく付与する:

```rust
let header = {
    let mut row = row![refresh_btn].spacing(4).padding([4, 8]);
    if panel.loading {
        row = row.push(text("↻ 更新中…").size(11));
    }
    if panel.is_replay() {
        row = row.push(text("⏪ REPLAY").size(11));
    }
    row
};
```

これ以降は live と同じく `panel.positions.is_empty()` 分岐 → 「保有なし」 or 一覧描画
に進む。

### Step 4: `distribute_positions()` の replay 除外撤去（D3）

`src/screen/dashboard.rs:910-924`:

```rust
pub fn distribute_positions(
    &mut self,
    main_window: window::Id,
    positions: Vec<engine_client::dto::PositionRecordWire>,
    ts_ms: i64,
) {
    self.iter_all_panes_mut(main_window).for_each(|(_, _, state)| {
        if let pane::Content::Positions(panel) = &mut state.content {
            // D3: replay pane も含めて全 Positions pane に broadcast
            panel.set_positions(positions.clone(), ts_ms);
        }
    });
}
```

### Step 5: `handlers/venue.rs` の request_id ガードを relax（D4）

`src/handlers/venue.rs:523-542`:

```rust
VenueMsg::PositionsUpdated { request_id, venue: _, positions, ts_ms } => {
    let in_flight = self.positions_request_id.as_deref();
    let is_pull_response =
        !request_id.is_empty() && in_flight == Some(request_id.as_str());
    let is_push_event = request_id.is_empty();
    if !is_pull_response && !is_push_event {
        log::debug!(
            "[PositionsUpdated] stale/unrouted: request_id={request_id:?}, in-flight={in_flight:?}"
        );
        return Task::none();
    }
    if is_pull_response {
        self.positions_request_id = None;
    }
    let main_window = self.main_window.id;
    self.active_dashboard_mut().distribute_positions(main_window, positions, ts_ms);
}
```

> 注: `VenueMsg::OrderListUpdated(orders)` は request_id を持たないシグネチャで in-flight
> ガード自体が無いため「OrderList と整合」という比較は誤り。本ステップでは
> **PositionsUpdated 独自に「空 request_id = push event」ルール**を導入する。妥当性根拠:
> `server.py:1192` の StepBackward が既に `"request_id": ""` で OrderListUpdated を push
> しており、push 用に空文字列を使う運用は server 側に前例がある。

### Step 6: `PortfolioView` に変換ヘルパを追加（D2）

`python/engine/nautilus/portfolio_view.py`:

```python
def to_position_records(self, last_prices: "dict | None" = None) -> list[dict]:
    """Serialize positions as PositionRecordWire-compatible array.

    Schema (engine-client/src/dto.rs:752-768, deny_unknown_fields):
      instrument_id: str
      qty: str                 # 整数文字列
      market_value: str        # last_price * qty を整数文字列。不明時 ""
      position_type: str       # "cash" | "margin_credit" | "margin_general" (snake_case)
      tategyoku_id: str | None # margin_credit のみ
      venue: str               # "replay"

    TODO(margin): replay は当面 "cash" 固定。信用建玉拡張時は position_type と
    tategyoku_id を分岐させる。
    """
    prices = last_prices if last_prices else self._last_prices
    out: list[dict] = []
    for inst, pos in self._positions.items():
        qty = pos["qty"]  # Decimal
        price = prices.get(inst)
        # 整数文字列に正規化（schemas.py:946-947 要求）。Decimal の str() は
        # 指数表記や小数点を含む可能性があるため int() を経由する。
        qty_str = str(int(qty))
        market_value = str(int(qty * price)) if price is not None else ""
        out.append({
            "instrument_id": inst,
            "qty": qty_str,
            "market_value": market_value,
            "position_type": "cash",  # snake_case wire value
            "tategyoku_id": None,
            "venue": "replay",
        })
    return out
```

> 並行で `engine-client/src/dto.rs:766` の venue コメント
> `"tachibana" 固定` を `"tachibana" または "replay"` に更新する（D2）。
> IPC schema MINOR バンプは不要（既存フィールド再利用）。`schemas.py` 側に
> `PositionRecord` Pydantic 定義がある場合は同じ field set / wire value で整合確認。

### Step 7: `engine_runner.py` で前進再生中の `PositionsUpdated` を emit（D1）

`_on_bar` と fill_handler の両方で、`ReplayBuyingPower` を emit する直後に
`PositionsUpdated` を emit する:

```python
# fill_handler / _on_bar 共通パターン
positions_payload = _portfolio.to_position_records()
emit({
    "event": "PositionsUpdated",
    "request_id": "",         # push event
    "venue": "replay",
    "positions": positions_payload,
    "ts_ms": ts_ms,
})
```

役割分担（既存コードと整合）:

- **fill_handler — authoritative for ポジション増減**: 約定発生時に毎回 emit。
  クローズ約定でポジションが 0 件になった場合の **空配列 emit はここでのみ行う**
  （`_on_bar` は `has_open_positions` ガードで早期 return するため空状態を発火できない）。
- **_on_bar — MTM 更新のみ**: 既存の `if not _portfolio.has_open_positions: return`
  ガードはそのまま維持。`PositionRecordWire.market_value` を含めるため、ポジション保有中の
  バー受信ごとに `to_position_records(_last_prices)` で再計算した配列を emit する。
  ポジション 0 件状態の通知責務は持たない。

### Step 8: StepBackward の `PositionsUpdated` re-emit（D1）

`server.py:1188-1195` の `OrderListUpdated` emit の直後に追加:

```python
# 2.7: PositionsUpdated を OrderListUpdated と同タイミングで re-emit
# snap.portfolio_state["positions"] は {iid: {qty, cost}} 形式なので
# PositionRecordWire 互換配列に変換してから送る。
positions_dict = snap.portfolio_state.get("positions", {}) if snap.portfolio_state else {}
last_prices_dict = snap.portfolio_state.get("last_prices", {}) if snap.portfolio_state else {}

def _market_value(inst: str, qty_str: str) -> str:
    p = last_prices_dict.get(inst)
    if p is None:
        return ""
    try:
        from decimal import Decimal as _D
        return str(int(_D(qty_str) * _D(str(p))))
    except Exception:
        return ""

def _qty_str(qty_raw) -> str:
    # snapshot 内 qty は Decimal を str 化したもの。整数文字列に正規化する。
    from decimal import Decimal as _D
    return str(int(_D(str(qty_raw))))

positions_array = [
    {
        "instrument_id": inst,
        "qty": _qty_str(pos["qty"]),
        "market_value": _market_value(inst, _qty_str(pos["qty"])),
        "position_type": "cash",
        "tategyoku_id": None,
        "venue": "replay",
    }
    for inst, pos in positions_dict.items()
]
self._outbox.append({
    "event": "PositionsUpdated",
    "request_id": "",
    "venue": "replay",
    "positions": positions_array,
    "ts_ms": int(snap.portfolio.get("ts_event_ms", 0)),
})
```

> 重複ロジック回避のため、Step 6 の `to_position_records()` を `_restore_snapshot` で
> 復元した `PortfolioView` から呼ぶリファクタも検討（実装時に判断）。

## テスト計画（acceptance）

R1 で指摘の最低 4 本を `tests/` 配下に追加する。Rust 側 iced 単体テスト不能な部分は
e2e helper（`ReplaySession`）で検証。

| ID | テスト内容 | 観測コマンド | 失敗パターン |
|---|---|---|---|
| T1 | `ReplayDataLoaded` 後に Positions pane が pane_grid に存在する | e2e: `replay_session.start(); assert pane_kinds.contains("Positions")` | (1) 未修正で fail |
| T2 | StepBackward で保有数量が 1 つ前の状態に巻き戻る（`PositionsUpdated` re-emit 含む） | e2e: 約定後 → step_backward → assert positions == 約定前 | (3)(4) D1 未修正で fail |
| T3 | replay 通常再生中（Play）に約定が起きると pane が更新される | e2e: 戦略を約定発生まで進める → assert pane.positions 非空 | D1 未修正で fail |
| T4 | 空ポジション時に「保有なし」が表示される（REPLAY バナー固定ではない） | unit (Rust): `view(panel_replay_empty)` snapshot に「保有なし」を含む | (2) 未修正で fail |

追加で観点 D 補強:
- T5: `to_position_records()` の単体テスト（Python）— `{iid: {qty, cost}}` → 配列変換の
  キー順序非依存・空 dict → 空配列・Decimal の str 化を確認。
- T6: `distribute_positions()` の単体テスト（Rust）— replay pane と live pane が混在する
  グリッドで両方に伝搬することを確認。

## 確認項目

- [ ] `pane::State::new_replay_positions()` の存在 / 追加
- [ ] `ReplayPaneRegistry` の whitelist に `"Positions"` が含まれる（無ければ追加 / Step 2a）
- [ ] `replay_pane_registry` に "Positions" キーが登録できる（registry の whitelist 確認）
- [ ] `engine-client/src/dto.rs:766` の venue コメントを `"tachibana" または "replay"` に更新
- [ ] `python/engine/schemas.py:950` の `venue: Literal["tachibana"]` を `Literal["tachibana", "replay"]` に拡張
- [ ] `qty` / `market_value` は整数文字列に正規化（`str(int(Decimal))`）
- [ ] e2e helper に Positions pane の internal state 観測 API があることを確認（無ければ追加）
- [ ] `PositionsPanel::view()` から REPLAY 早期 return を削除した
- [ ] `distribute_positions()` から `!panel.is_replay()` ガードを削除した
- [ ] `handlers/venue.rs` の `PositionsUpdated` で空 request_id を broadcast 扱いにした
- [ ] `PortfolioView.to_position_records()` を追加し、`PositionRecordWire` schema と整合
- [ ] `engine_runner.py` の fill_handler で `PositionsUpdated` を emit
- [ ] `engine_runner.py` の `_on_bar` で `PositionsUpdated` を emit（dto に MTM が含まれる場合）
- [ ] `server.py` の StepBackward で `PositionsUpdated` を re-emit
- [ ] IPC schema (`schemas.py` / `engine.proto` / `engine-client/src/dto.rs`) の `PositionRecordWire`
      フィールドが Python 側ペイロードと一致（必要なら schema MINOR バンプ）
- [ ] T1〜T6 全テスト緑
- [ ] リプレイ終了（`Finished`）後に保有銘柄パネルが最終状態を保持する

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `src/screen/dashboard.rs` | `auto_generate_replay_panes` に N1.17 Positions ブロック / `distribute_positions` の replay ガード削除 |
| `src/screen/dashboard/pane.rs` | `new_replay_positions()` 追加（無ければ） |
| `src/screen/dashboard/panel/positions.rs` | `view()` の REPLAY 早期 return 削除 / header に REPLAY バッジ追加 |
| `src/handlers/venue.rs` | `PositionsUpdated` の空 request_id バイパス |
| `python/engine/schemas.py:950` | `PositionRecord.venue` を `Literal["tachibana", "replay"]` に拡張 |
| `engine-client/src/dto.rs:766` | venue コメントを `"tachibana" または "replay"` に更新 |
| `python/engine/nautilus/portfolio_view.py` | `to_position_records()` 追加 |
| `python/engine/nautilus/engine_runner.py` | fill_handler / `_on_bar` に `PositionsUpdated` emit |
| `python/engine/server.py` | StepBackward に `PositionsUpdated` re-emit |
| `tests/...` | T1〜T6 テスト追加 |

## 実装難易度

**中**。経路 4 つ + Python 側変換 + IPC schema 整合確認 + 6 本のテスト。
schema バンプが必要かは `PositionRecordWire` 既存フィールド次第。
