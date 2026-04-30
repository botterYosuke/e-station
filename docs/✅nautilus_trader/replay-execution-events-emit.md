# Replay 経路 Execution Events 配線（streaming 約定の UI 反映）

> **Revision 2026-04-30**: 初版の「`engine.cache.orders()` snapshot 差分」案は 4 件の高/中
> Findings で却下。OrderFilled イベントを **nautilus MessageBus から直接購読する**設計に
> 変更し、wire schema の不一致（side 大文字小文字・field 名）も修正済み。
> Findings は本文末尾の「Revision History」を参照。

## 背景・症状

2026-04-30 に `bash scripts/run-replay-debug.sh docs/example/sma_cross.py`
（1301.TSE / Daily / 2025-01-06〜2025-03-31）で動作確認したところ、
market data（KlineUpdate / Trades / DateChangeMarker）は仕様どおりストリーミングされ、
チャートに 57 本の Daily バーが描画されるところまでは動いた。

しかし **戦略が発注して fill しても UI に何も反映されない**：

| 期待される表示 | 実際 |
|---|---|
| CandlestickChart 上に BUY/SELL ドット overlay | × 何も出ない |
| OrderList ペインに約定行が増える | × 空のまま |
| BuyingPower ペインの残高が変動する | × 初期 1,000,000 円のまま固定 |

検証ログ（`engine.cache.orders()` を `_collect_fill_data()` で読んだ結果）：

```
fills: 8 timestamps      ← 戦略は 8 回約定している
```

手計算した SMA(3)/SMA(5) クロスは **8 回**（2025-01-21〜2025-03-21）で fills と一致しており、
**戦略は確実に発注・約定している**。にもかかわらず IPC イベントが UI に届いていない。

## 根本原因

`start_backtest_replay_streaming()`（および非ストリーミング版 `start_backtest_replay()`）が
**OrderFilled イベントを購読する経路を持たない**。

### 既存資産の現状

[python/engine/nautilus/narrative_hook.py](../../python/engine/nautilus/narrative_hook.py)
の `NarrativeHook` クラス（N1.6 / N1.12）は

- OrderFilled dict を受け取って `/api/agent/narrative` に POST する
- `on_event` callback 経由で `ExecutionMarker` IPC イベントを emit する

機能を持つ。`_emit_execution_marker()` も実装済みで [`test_execution_marker_emit.py`](../../python/tests/test_execution_marker_emit.py)
が 1:1 マッピング契約を固定している。
しかし **engine_runner からも server.py からも一切呼ばれておらず、production 経路では dead code** である。

[python/engine/nautilus/portfolio_view.py](../../python/engine/nautilus/portfolio_view.py)
の `PortfolioView` も同様に存在しているが streaming 経路で使われていない。
`on_fill(instrument_id, side, qty, price)` で残高を更新し、`to_ipc_dict(strategy_id, last_prices)`
で `ReplayBuyingPower` dict を返す API は完成済み。

### IPC スキーマ（実装の確定値）

| 型 | フィールド（必須） | 補足 |
|---|---|---|
| `ExecutionMarker` | `event="ExecutionMarker"`, `strategy_id`, `instrument_id`, `side` ∈ `{"BUY","SELL"}`, `price` (decimal str), `ts_event_ms` | **`venue` も `quantity` も無い**。1 OrderFilled = 1 ExecutionMarker（1:1） [schemas.py:715](../../python/engine/schemas.py#L715) [dto.rs:955](../../engine-client/src/dto.rs#L955) |
| `ReplayBuyingPower` | `event="ReplayBuyingPower"`, `strategy_id`, `cash`, `buying_power`, `equity` (全て decimal str), `ts_event_ms` | **`venue` 無し**。`extra="forbid"` なので未知フィールドは reject される [schemas.py:750](../../python/engine/schemas.py#L750) [portfolio_view.py:73](../../python/engine/nautilus/portfolio_view.py#L73) |
| `OrderListUpdated.orders[].OrderRecordWire` | `client_order_id`, `venue_order_id`, `instrument_id`, `order_side`, `order_type`, `quantity`, `filled_qty`, `leaves_qty`, `price?`, `time_in_force`, `status`, `ts_event_ms`, `venue` | **`deny_unknown_fields`**。ExecutionMarker から派生不可（情報不足） [dto.rs:601](../../engine-client/src/dto.rs#L601) |

Rust 側受信パスも揃っている：
- ExecutionMarker → [src/main.rs:1126](../../src/main.rs#L1126) → [src/chart/kline.rs:623](../../src/chart/kline.rs#L623) `push_execution_marker`
  → [kline.rs:1054](../../src/chart/kline.rs#L1054) で `side == "BUY"` を緑、それ以外を赤で描画
- OrderListUpdated → [src/main.rs:2092](../../src/main.rs#L2092) → `distribute_order_list`

つまり **wire は全部引かれており Python の streaming ループから何も流していない** だけ。

### なぜテストで検知できなかったか

[`python/tests/fixtures/test_strategy.py`](../../python/tests/fixtures/test_strategy.py)
は `NoOpTestStrategy`（`StrategyConfig` のみ・`on_start` も `on_bar` も実装なし）で、
**1 件も発注しない**。streaming 系テスト（`test_engine_runner_replay.py:468-562`）は
すべてこの fixture を使うため、fill 経路の IPC emit が検証されてこなかった。

> Misses パターン: 「fixture が no-op なため発注パス未検証」
> `bug-postmortem` Phase 5 で MISSES.md に追記する対象。

---

## 設計判断（採用案）

### OrderFilled の取得方法: nautilus MessageBus 購読

**採用**: `engine.kernel.msgbus.subscribe(topic="events.order.filled*", handler=...)` で
nautilus 内部のイベントバスに購読する。OrderFilled の `OrderFilledEvent` オブジェクトを
そのまま受け、こちらで dict 化する。

**初版の `cache.orders()` 差分スキャン案を却下した理由**（Findings 参照）:

| 問題 | 詳細 |
|---|---|
| 1 OrderFilled = 1 ExecutionMarker の契約を破る | 注文スナップショット単位なので部分約定や複数 fill は 1 件に潰れる。逆に「fill ではない close（reject 等）」を拾い得る |
| `is_closed` ≠ filled | キャンセル・拒否・期限切れもすべて `is_closed=True` |
| 既存テストと矛盾 | [test_execution_marker_emit.py:15](../../python/tests/test_execution_marker_emit.py#L15) は OrderFilled 1 件 → ExecutionMarker 1 件を契約として fix |
| sma_cross.py（成行のみ）は通るが一般戦略で壊れる | 部分約定する指値戦略では即座に破綻 |

**msgbus 購読のリスクと対策**:

- nautilus の internal API（`engine.kernel.msgbus`）依存が増える → topic 文字列・event 型を
  `nautilus_trader.model.events.OrderFilled` から import して name 変更にも対応
- topic は nautilus の docs / code で確認: `events.order.filled.<strategy_id>` が
  慣例。実装前に `nautilus_trader.core.message` / `OrderFilled.topic()` で正確な
  topic を確認する（**Open Question 1** 参照）
- 購読 handler が例外を出すと nautilus 内部で吞まれる可能性 → handler 内で
  `try/except + log.error(..., exc_info=True)` で必ず可視化する

### 残高変動: `PortfolioView` を流用

**採用**: 既存 [portfolio_view.py](../../python/engine/nautilus/portfolio_view.py) を
streaming ループに 1 個インスタンス化し、上記 OrderFilled handler 内で `on_fill()` を
呼び、続けて `to_ipc_dict(strategy_id, last_prices)` で `ReplayBuyingPower` dict を作って
emit する。

理由:
- スキーマ（`cash` / `buying_power` / `equity`）と dict 化ロジックが既に実装済み
- `on_fill` は qty / price / side をそのまま反映するので OrderFilled handler と相性が良い
- live モードで使われる立花残高経路（`BuyingPowerUpdated`）と独立しているので干渉しない

### OrderList の更新: 別タスク（Step B として分離）

**Step A スコープ外**: ExecutionMarker から `OrderRecordWire` を派生させるのは情報不足で不可能。
代わりに以下のどちらかが必要だが、本タスク（Step A）には含めない：

- 案 X: 同じ msgbus subscription 経路で `events.order.submitted` / `events.order.accepted` /
  `events.order.filled` を購読し、状態遷移を反映した `OrderRecordWire` を組み立てて
  `OrderListUpdated` を emit する（push 型）
- 案 Y: nautilus が出した発注を [order_router.py](../../python/engine/order_router.py)
  の `submit_order_replay` 経由にして `tachibana_orders_replay.jsonl` WAL に追記し、
  既存 [server.py:1393](../../python/engine/server.py#L1393) `_do_get_order_list_replay`
  に乗せる（pull 型 / GetOrderList 駆動）

このどちらを取るかは Step A 完了後の別 PR（**Step B**）で `/council` で判断する。
本ドキュメントでは「Step A 完了時点では OrderList ペインは空のまま」を明示する。

---

## ゴール

### Step A（本タスク）

1. streaming replay で fill が 1 回起きるごとに `ExecutionMarker` IPC が **1 件** emit される
2. fill 直後に `ReplayBuyingPower` IPC が emit され、cash/buying_power/equity が反映される
3. 既存テスト全件 PASS、決定論性テストを破壊しない
4. fixture を「fill する戦略」に置き換えた回帰テストを追加（misses 防止）
5. sma_cross.py を replay 実機で回し、チャートに 8 個の overlay ドット・BuyingPower の
   数値変動を確認する

### Step B（別タスク）

6. OrderList ペインに submit / accepted / filled の状態遷移が反映される（push or pull で別途設計）

### 非ゴール

- 非ストリーミング版 `start_backtest_replay()` への配線（streaming 経路だけが
  `/api/replay/start` から呼ばれる本流）
- live モードの execution event 配線（live は別系統で配線済み）
- `/api/agent/narrative` への HTTP POST（N1.6 機能・本タスクのスコープ外）
- StrategySignal の自動配線（`StrategySignalMixin.emit_signal()` を strategy 側で
  明示的に呼ぶ既存設計を維持）

---

## 詳細実装計画 — Step A

### A-0. 事前確認（実装前に解消する Open Question）

| # | 確認事項 | 確認方法 |
|---|---|---|
| 1 | OrderFilled の MessageBus topic 文字列 | `from nautilus_trader.model.events import OrderFilled` してから `OrderFilled` のクラスメソッド `topic()` または nautilus docs を引く。`events.order.filled.<strategy_id>` がワイルドカード対応するかも要確認 |
| 2 | `msgbus.subscribe(topic, handler)` の handler シグネチャ | 同期 callback で OK か、async が要るか。nautilus の Subscriber インタフェースを読む |
| 3 | `OrderFilled` の主要属性名 | `instrument_id` / `order_side` / `last_px` / `last_qty` / `ts_event` の正式属性名を nautilus docs で確認（バージョン依存の可能性） |
| 4 | 購読タイミング | `engine.add_strategy()` の前か後か。`engine.run(streaming=True)` のループ内で動作するか。スパイク（10 行）で確認 |

これらを A-1 のコードに着地させる前に、5 分のスパイクスクリプトで以下を試す：

```python
from nautilus_trader.model.events import OrderFilled
print(OrderFilled.topic() if hasattr(OrderFilled, "topic") else "no topic class method")
# msgbus.subscribe を一度叩いて handler が呼ばれるか確認
```

### A-1. `start_backtest_replay_streaming()` に hook を仕込む

**対象**: [python/engine/nautilus/engine_runner.py](../../python/engine/nautilus/engine_runner.py)
の `start_backtest_replay_streaming()` 内、`engine.add_strategy(strategy_instance)`
（現在 621 行）の **直後**。

**変更内容**:

```python
from decimal import Decimal
from nautilus_trader.model.events import OrderFilled

from engine.nautilus.narrative_hook import _emit_execution_marker
from engine.nautilus.portfolio_view import PortfolioView

# ── execution event emit hook ─────────────────────────────────────────
portfolio = PortfolioView(initial_cash=Decimal(initial_cash))
last_prices: dict[str, Decimal] = {}

def _on_order_filled(event: OrderFilled) -> None:
    """nautilus OrderFilled → ExecutionMarker + ReplayBuyingPower IPC."""
    try:
        # OrderFilled → dict（_emit_execution_marker が期待する形）
        side_str = "BUY" if event.order_side == OrderSide.BUY else "SELL"
        instrument_str = str(event.instrument_id)
        price_str = str(event.last_px)
        qty_dec = Decimal(str(event.last_qty))  # 部分約定対応
        ts_event_ms = int(event.ts_event // 1_000_000)

        order_filled_dict = {
            "instrument_id": instrument_str,
            "side": side_str,
            "price": price_str,
            "ts_event_ms": ts_event_ms,
        }

        # ExecutionMarker emit（既存ヘルパー流用）
        _emit_execution_marker(strategy_id, order_filled_dict, emit)

        # PortfolioView 更新 → ReplayBuyingPower emit
        portfolio.on_fill(instrument_str, side_str, qty_dec, Decimal(price_str))
        last_prices[instrument_str] = Decimal(price_str)
        bp_dict = portfolio.to_ipc_dict(strategy_id, last_prices)
        # ts_event_ms は portfolio_view が time.time() を入れるので、
        # fill のイベント時刻に揃えるなら上書き（A-2 で要決定）
        bp_dict["ts_event_ms"] = ts_event_ms
        emit(bp_dict)

    except Exception:
        log.error(
            "[NautilusRunner] OrderFilled handler failed: strategy=%r",
            strategy_id, exc_info=True,
        )
        # market data 配信を止めない（silent failure 防止のため log は出す）

# A-0/Q1 で確定した topic に置き換える
engine.kernel.msgbus.subscribe(
    topic="events.order.filled*",  # ← A-0/Q1 で確定
    handler=_on_order_filled,
)
```

**設計上の注意**:

1. `last_qty` を Decimal 化して `PortfolioView.on_fill()` に渡す（部分約定の cash 計算が
   合うようにするため。`last_qty` が部分約定単位の数量である前提。A-0/Q3 で確認）
2. `ts_event_ms` は OrderFilled の `ts_event` を採用し、`portfolio_view.to_ipc_dict()`
   が入れる `time.time()` を上書きする。決定論性テスト（同入力同出力）の維持に必要
3. handler 内例外は **必ず log.error(exc_info=True)** で可視化。`break` せず継続
4. 購読は `engine.add_strategy(strategy_instance)` の **後**（A-0/Q4 で要確認）
5. `currency / venue / cur` 等は既存ローカル変数を流用

### A-2. ts_event_ms の決定論性

`portfolio.to_ipc_dict()` は `time.time()` を使うので、
そのまま emit すると **テストの再現性が壊れる**。

対策:
- handler 側で OrderFilled の `ts_event` を ms に変換した値で `bp_dict["ts_event_ms"]`
  を上書きする（上記コードに反映済み）
- 単体テストでは `time.time` を mock せず、ts_event_ms が OrderFilled.ts_event 由来で
  あることを assert する

### A-3. テスト計画

#### 新規 fixture: `python/tests/fixtures/sma_cross_test_strategy.py`

[`docs/example/sma_cross.py`](../example/sma_cross.py) をテスト用に最小化したコピー。
`instrument_id` / `lot_size` をハードコードして決定論性を担保する。

> 既存 `NoOpTestStrategy` は維持（他テストが依存）。新 fixture は別ファイルで追加。

#### RED テスト 1: ExecutionMarker が 1 OrderFilled につき 1 件出る

新規ファイル: `python/tests/test_engine_runner_streaming_fills.py`

```python
def test_streaming_replay_emits_one_execution_marker_per_fill():
    events: list[dict] = []
    runner = NautilusRunner()
    runner.start_backtest_replay_streaming(
        strategy_id="test",
        instrument_id="1301.TSE",
        granularity="Daily",
        start_date="2025-01-06",
        end_date="2025-03-31",
        initial_cash=1_000_000,
        multiplier=0,
        currency="JPY",
        on_event=events.append,
        strategy_file=str(FIXTURES / "sma_cross_test_strategy.py"),
    )
    markers = [e for e in events if e["event"] == "ExecutionMarker"]
    assert len(markers) == 8  # SMA cross 数と一致
    for m in markers:
        assert m["side"] in ("BUY", "SELL")  # 大文字
        assert "venue" not in m              # スキーマに無い
        assert "quantity" not in m           # スキーマに無い
        assert m["instrument_id"] == "1301.TSE"
```

#### RED テスト 2: ReplayBuyingPower が fill ごとに出る・残高が動く

```python
def test_streaming_replay_emits_buying_power_per_fill():
    events: list[dict] = []
    runner = NautilusRunner()
    runner.start_backtest_replay_streaming(...)  # 同上
    bp_events = [e for e in events if e["event"] == "ReplayBuyingPower"]
    assert len(bp_events) == 8

    # スキーマ完全一致（extra="forbid"）
    expected_keys = {"event", "strategy_id", "cash", "buying_power", "equity", "ts_event_ms"}
    for e in bp_events:
        assert set(e.keys()) == expected_keys
        assert "venue" not in e
        assert "cash_available" not in e

    # 1 件目（最初の BUY）で cash が initial_cash より小さくなる
    assert Decimal(bp_events[0]["cash"]) < Decimal("1000000")
```

#### RED テスト 3: pydantic 検証で wire schema を担保

```python
def test_emitted_events_pass_pydantic_schema():
    """schemas.py の pydantic モデルで全 ExecutionMarker / ReplayBuyingPower を検証する。"""
    from engine.schemas import ExecutionMarker, ReplayBuyingPower
    events: list[dict] = []
    runner = NautilusRunner()
    runner.start_backtest_replay_streaming(...)
    for e in events:
        if e["event"] == "ExecutionMarker":
            ExecutionMarker.model_validate(e)  # raises on schema drift
        elif e["event"] == "ReplayBuyingPower":
            ReplayBuyingPower.model_validate(e)
```

#### 既存テストへの影響

- `test_engine_runner_replay.py` の streaming 系: `NoOpTestStrategy` のため fill 0 件
  → ExecutionMarker / ReplayBuyingPower も 0 件。events 全件カウントしている
  assertion が無いことを確認したうえで、新規 emit は無害
- `test_execution_marker_emit.py`: 既存ヘルパー `_emit_execution_marker()` を流用するので
  影響なし（リグレッション保護）

#### E2E（手動）

`scripts/run-replay-debug.sh docs/example/sma_cross.py` を回し、
ターミナル log（debug ビルドは stdout）に対して：

- `"event":"ExecutionMarker"` の出現回数 = 8
- `"event":"ReplayBuyingPower"` の出現回数 = 8
- `"side":"BUY"` と `"side":"SELL"` がそれぞれ 4 回ずつ（SMA 上下クロス各 4 回）
- `Kline fetch failed` が 0 回（commit e149950 の回帰ガード）

を `grep -c` で機械検証する。

---

## 受入条件（Step A）

| # | 条件 | 検証方法 |
|---|---|---|
| 1 | 1 OrderFilled = 1 ExecutionMarker（1:1） | RED テスト 1 PASS |
| 2 | ExecutionMarker の wire は `event/strategy_id/instrument_id/side/price/ts_event_ms` のみ・side は大文字 | RED テスト 1 + 3 PASS |
| 3 | ReplayBuyingPower の wire は `event/strategy_id/cash/buying_power/equity/ts_event_ms` のみ | RED テスト 2 + 3 PASS |
| 4 | sma_cross.py 実機で overlay ドット 8 個・BuyingPower 数値変動 | 手動実機確認 |
| 5 | 既存テスト全件 PASS | `uv run pytest python/tests/ -v` |
| 6 | `Kline fetch failed` ログスパムが出ない | E2E grep |
| 7 | 決定論性: 同入力で同 ts_event_ms / 同順序の event 列が emit される | RED テスト 1 を 2 回流して結果一致を assert |

---

## リスクと留意点

- **nautilus internal API 依存**: `engine.kernel.msgbus.subscribe` は public 扱いだが、
  バージョンアップ時に topic 名や API シグネチャが変わるリスクがある
  → 実装時に nautilus バージョンを `Cargo.toml` / `pyproject.toml` でピン留め確認、
  CI で互換性を担保
- **OrderFilled.last_qty / last_px の単位**: nautilus 内部で Decimal/Quantity 型。
  str → Decimal 変換時のフォーマット崩れに注意。`portfolio_view.on_fill` は Decimal 期待
- **handler 内例外の silent failure**: nautilus が握り潰す可能性があるので必ず
  `log.error(exc_info=True)` で可視化（このプロジェクトの sile-failure-hunter 観点）
- **複数戦略 / 複数 instrument の将来拡張**: `PortfolioView` は instrument_id ごとに
  position を持つので問題ない。strategy_id は handler 内 closure で固定される前提
  なので、複数戦略になったら topic を `events.order.filled.<strategy_id>` で
  分離するなど別途設計が要る（Step B 以降）
- **OrderList ペインは Step A 完了時点でも空**: ユーザーには「ドットは出るが
  注文表は空」という UX になる。Step B の優先度を高めに設定する

---

## Revision History

### 2026-04-30 v2 — レビュー反映

レビュー Findings 4 件を受けて以下を修正:

| Finding | 修正内容 |
|---|---|
| F1（高）: snapshot 差分は 1:1 を保証できない | OrderFilled の MessageBus 購読に変更 |
| F2（高）: `ReplayBuyingPower` の field 不一致 | `cash` / `buying_power` / `equity` に揃え、`PortfolioView.to_ipc_dict()` 流用 |
| F3（中）: `ExecutionMarker` の side 大小・余分 field | `BUY/SELL` 大文字、`venue/quantity` 削除、既存 `_emit_execution_marker()` 流用 |
| F4（中）: OrderList を ExecutionMarker から派生不可 | Step A から外し Step B として分離。push/pull 案を提示し別 PR で `/council` 判断 |

### 2026-04-30 v1 — 初版

`cache.orders()` 差分スキャン案 + 簡易 wire 例（誤）。レビューで却下。

---

## 関連リンク

- 修正済み（前段）: [archive/replay-market-data-emit.md](./archive/replay-market-data-emit.md)
  — KlineUpdate / Trades の per-tick emit 実装
- ログスパム解消（前段）: commit e149950 `fix(replay): Kline フェッチスパム解消`
- IPC スキーマ: [`python/engine/schemas.py:715`](../../python/engine/schemas.py#L715)
- Rust 側受信パス: [`src/main.rs:1126`](../../src/main.rs#L1126) `EngineEvent::ExecutionMarker`
- 既存資産（dead code 化していた）: [`python/engine/nautilus/narrative_hook.py`](../../python/engine/nautilus/narrative_hook.py),
  [`python/engine/nautilus/portfolio_view.py`](../../python/engine/nautilus/portfolio_view.py)
- 戦略サンプル: [`docs/example/sma_cross.py`](../example/sma_cross.py)
- 既存契約テスト: [`python/tests/test_execution_marker_emit.py`](../../python/tests/test_execution_marker_emit.py)
- 関連 WAL（Step B 候補）: [`python/engine/order_router.py`](../../python/engine/order_router.py),
  [`python/engine/server.py:1393`](../../python/engine/server.py#L1393) `_do_get_order_list_replay`
- Misses パターン追記対象: [`.claude/skills/bug-postmortem/MISSES.md`](../../.claude/skills/bug-postmortem/MISSES.md)
  — 「fixture が no-op なため発注パス未検証」
