# Live Strategy 実行 — 完全実装プラン

## Context

e-station の live 実行アダプタ（`TachibanaLiveExecutionClient` / `TachibanaLiveDataClient` / `TachibanaEventBridge`）は実装済みだが、それらを繋ぐ配線が全て未完成。具体的には:

1. `server.py._handle_start_engine()` の else 分岐がバグ（live なのに `start_backtest_replay()` を呼んでいる）
2. `NautilusRunner.start_live()` が stub のみ（assertion + log だけ）
3. `LiveSession.run()` が存在しない
4. `LiveState` に TRADING / STOPPING がなく、live 中の state machine が未完
5. IPC スキーマに `LiveBuyingPower`・`max_qty`/`max_notional_jpy` がない

---

## アーキテクチャ概要

```
User
  └─ LiveSession.run(strategy_file, max_qty, max_notional_jpy, on_event)
        │
        ├─[attach]── StartEngine{engine:"Live"} ──→ server.py (loop A)
        │                                               │
        └─[inprocess]─────────────────────────→ runner.start_live() [blocking]
                                                        │
                                          ┌─────────────▼──────────────┐
                                          │ worker thread (loop B)      │
                                          │ NautilusRunner.start_live() │
                                          │  ├─ TradingNode             │
                                          │  │   ├─ LiveDataClient      │
                                          │  │   └─ LiveExecClient      │
                                          │  ├─ LiveDataBridge          │
                                          │  │   fd_queue → feed_trade  │
                                          │  └─ LiveEcBridge            │
                                          │      ec_queue → process_ec  │
                                          └─────────────────────────────┘

server.py (loop A)
  ├─ FD frame (EVENT WS) ──→ fd_queue.put_nowait()  [TRADING 状態のみ]
  ├─ EC frame (EVENT WS) ──→ ec_queue.put_nowait()  [TRADING 状態のみ]
  └─ on_event ←── loop_A.call_soon_threadsafe(outbox.append, event)

IPC events (live stream):
  EngineStarted → ExecutionMarker → LiveBuyingPower → EngineStopped
```

---

## ✅ Phase 1: IPC スキーマ拡張（Python + Rust）

### `python/engine/schemas.py`

- `SCHEMA_MINOR` を `17` に上げる（現在は `16`、schemas.py 参照。+1 で 17）
- `LiveStateName` に `"TRADING"` / `"STOPPING"` を追加（現在の schemas.py は 3 値 DISCONNECTED/CONNECTING/CONNECTED。Phase 1 で TRADING/STOPPING を追加する）:
  ```python
  LiveStateName = Literal["DISCONNECTED", "CONNECTING", "CONNECTED", "TRADING", "STOPPING"]
  ```
- `EngineStartConfig` の replay 専用フィールドを Optional 化（live では不要）:
  ```python
  start_date: str | None = None
  end_date: str | None = None
  initial_cash: str | None = None
  granularity: Literal["Trade", "Minute", "Daily"] | None = None
  ```
  live 専用フィールドを追加:
  ```python
  max_qty: int | None = None           # live 必須 (N2.5)
  max_notional_jpy: int | None = None  # live 必須 (N2.5)
  ```
  cross-field validator を追加: replay 時は `start_date`/`end_date`/`initial_cash`/`granularity` が全て必須。live 時は `max_qty`/`max_notional_jpy` が必須。
- `LiveBuyingPower` 新規追加:
  ```python
  class LiveBuyingPower(IpcMessage):
      model_config = ConfigDict(extra="forbid")
      event: Literal["LiveBuyingPower"] = "LiveBuyingPower"
      strategy_id: str
      cash: str           # decimal 文字列（円）
      buying_power: str   # decimal 文字列（円）
      equity: str         # decimal 文字列（円）
      ts_event_ms: int
  ```
- `AttemptedCommand` に `"StartEngine"` が既に含まれているか確認 → なければ追加

### `engine-client/src/dto.rs`

- `EngineStartConfig` の replay 専用フィールドを Optional 化:
  ```rust
  #[serde(default, skip_serializing_if = "Option::is_none")]
  pub start_date: Option<String>,
  #[serde(default, skip_serializing_if = "Option::is_none")]
  pub end_date: Option<String>,
  #[serde(default, skip_serializing_if = "Option::is_none")]
  pub initial_cash: Option<String>,
  #[serde(default, skip_serializing_if = "Option::is_none")]
  pub granularity: Option<ReplayGranularity>,
  // live 専用
  #[serde(default, skip_serializing_if = "Option::is_none")]
  pub max_qty: Option<u32>,
  #[serde(default, skip_serializing_if = "Option::is_none")]
  pub max_notional_jpy: Option<u64>,
  ```
- `CurrentEngineState` に追加（既存の `Stopping` variant は replay/live 共用としてそのまま使用。新規追加は `Trading` のみ）:
  ```rust
  Trading,   // live strategy 実行中
  // Stopping は既存 variant（replay/live 共用）をそのまま使う — 新規追加しない
  ```
  `as_wire_str()` / `Display` の `match` も更新。
- `EngineEvent` に追加:
  ```rust
  LiveBuyingPower {
      strategy_id: String,
      cash: String,
      buying_power: String,
      equity: String,
      ts_event_ms: i64,
  },
  ```
- `cargo test` + `cargo clippy` で確認

---

## ✅ Phase 2: LiveState 拡張 + server.py dispatch 修正

### `python/engine/server.py`

**1. LiveState 拡張**:
```python
class LiveState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    TRADING = auto()    # 追加: live strategy 実行中
    STOPPING = auto()   # 追加: graceful stop 待ち
```

**2. Queue 追加** (`__init__`):
```python
import queue as _stdlib_queue
self._live_fd_queue: _stdlib_queue.Queue = _stdlib_queue.Queue(maxsize=10_000)
self._live_ec_queue: _stdlib_queue.Queue = _stdlib_queue.Queue(maxsize=1_000)
```

**3. `_handle_start_engine()` の前段 validation を replay 分岐内へ移設** (現在 L3382-3394):

現行コードは `initial_cash = int(config_obj.initial_cash)` を分岐より前に無条件実行している。live 時は `initial_cash=None` を許容するため、このパースを `_run()` 内の `if self._mode == "replay":` ブロック先頭に移動する:
```python
# _run() 内 replay 分岐の先頭に移動
if self._mode == "replay":
    try:
        initial_cash = int(config_obj.initial_cash)
    except (ValueError, TypeError) as exc:
        # _emit_threadsafe 経由で返す（_run はスレッド内）
        _on_event_tracked({"event": "Error", "code": "invalid_config",
                           "message": f"initial_cash: {exc}"})
        return
    # ... 既存の replay 処理 ...
```
前段の `int(config_obj.initial_cash)` ブロックは丸ごと削除する。

**4. `_handle_start_engine()` else 分岐を修正** (現在 L3504-3519):

この else ブロックは **`_run()` クロージャ内の `elif self._mode == "live":` ブロックとして追加する**（`asyncio.to_thread(_run)` の外に置く書き方は使わない）:

```python
# _run() 内に追加する elif 分岐
elif self._mode == "live":
    # live: CONNECTED 状態のみ受理
    if not self._check_live_state("StartEngine", LiveState.CONNECTED, ws=ws):
        return
    # live 専用バリデーション
    if config_obj.max_qty is None or config_obj.max_notional_jpy is None:
        _emit({"event": "Error", "code": "invalid_config",
               "message": "max_qty and max_notional_jpy required for live engine"})
        return
    # SessionHolder から第二暗証番号を取得（env 変数不使用）
    second_password = self._session_holder.get_password()
    if second_password is None:
        _emit({"event": "SecondPasswordRequired", "request_id": request_id})
        return
    self._live_state = LiveState.TRADING
    runner.start_live(
        instrument_id=config_obj.instrument_id,
        strategy_file=config_obj.strategy_file,
        strategy_init_kwargs=config_obj.strategy_init_kwargs,
        max_qty=config_obj.max_qty,
        max_notional_jpy=config_obj.max_notional_jpy,
        second_password=second_password,
        session=self._tachibana_session,
        fd_queue=self._live_fd_queue,
        ec_queue=self._live_ec_queue,
        on_event=_on_event_tracked,
        stop_event=stop_event,
        strategy_id=strategy_id,
    )
```

**5. `asyncio.to_thread(_run)` 完了後のリセットブロックに replay ガードを追加**:

```python
# asyncio.to_thread(_run) 完了後
if self._mode == "replay":
    self._replay_portfolio.reset(initial_cash)  # replay のみ
```

**6. `_handle_stop_engine()` に live 遷移を追加**: `StopEngine` コマンド受信時に `LiveState.TRADING → STOPPING` の遷移を追加する。guard 条件と非 TRADING 時の応答仕様は以下の通り:

```python
# _handle_stop_engine() への live 分岐追加
elif self._mode == "live":
    # TRADING 状態のみ StopEngine を受理
    if not self._check_live_state("StopEngine", LiveState.TRADING, ws=ws):
        return  # 非 TRADING 時は busy/invalid_state エラーを返す（_check_live_state 内で emit）
    self._live_state = LiveState.STOPPING
    # stop_event.set() で start_live() の worker thread に停止シグナルを送る
    stop_event = self._engine_stop_events.get(strategy_id)
    if stop_event:
        stop_event.set()
```

**7. finally ブロック**で live state を CONNECTED に戻す + キュードレイン:
```python
finally:
    ...
    if self._mode == "live" and self._live_state in (LiveState.TRADING, LiveState.STOPPING):
        self._live_state = LiveState.CONNECTED
    # _live_fd_queue / _live_ec_queue の残留メッセージをドレイン
    # （while not q.empty(): q.get_nowait() でドレインするか、毎回 Queue() で再生成する）
    for q in (self._live_fd_queue, self._live_ec_queue):
        while not q.empty():
            q.get_nowait()
```

**8. `_on_event_tracked` の `EngineStarted` ガード**: `EngineStarted` 時の `self._replay_streaming_fills.clear()` には `if self._mode == "replay":` ガードを追加する。同様に、`ExecutionMarker` 時の `self._replay_streaming_fills.append(...)` にも `if self._mode == "replay":` ガードを追加する（live モードでは `_replay_streaming_fills` を操作しない）。

**9. FD/EC frame → queue routing**: 既存の EVENT WS ハンドラ（FD frame 受信部）に追加:
```python
if self._mode == "live" and self._live_state == LiveState.TRADING:
    try:
        self._live_fd_queue.put_nowait(trade_dict)
    except _stdlib_queue.Full:
        log.warning("live_fd_queue full, dropping trade frame")
```
EC frame も同様に `_live_ec_queue` へ。

---

## ✅ Phase 3: LiveDataBridge / LiveEcBridge

### 新規ファイル: `python/engine/nautilus/live_bridges.py`

**設計方針**:  
- `server.py` (loop A) から `queue.put_nowait()` で投入（non-blocking）
- Bridge は daemon thread として動作し、TradingNode (loop B) の client を `call_soon_threadsafe` 経由で呼ぶ
- `TachibanaLiveDataClient` は `_connect()` 時に `asyncio.get_running_loop()` を `_loop` に保存しておく

```python
class LiveDataBridge:
    def __init__(self, data_client, fd_queue, instrument_id, stop_event):
        self._client = data_client
        self._queue = fd_queue
        self._instrument_id = instrument_id
        self._stop = stop_event

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                trade_dict = self._queue.get(timeout=0.05)
                # loop B 上で安全に呼ぶ
                self._client._loop.call_soon_threadsafe(
                    self._client._feed_trade_dict_sync,
                    self._instrument_id,
                    trade_dict,
                )
            except queue.Empty:
                continue


class LiveEcBridge:
    def __init__(self, event_bridge, ec_queue, stop_event):
        self._bridge = event_bridge
        self._queue = ec_queue
        self._stop = stop_event

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                ec_event = self._queue.get(timeout=0.05)
                self._bridge._loop.call_soon_threadsafe(
                    self._bridge.process_ec_event, ec_event
                )
            except queue.Empty:
                continue
```

**`TachibanaLiveDataClient`** に追加:
- `_connect()` で `self._loop = asyncio.get_running_loop()` をキャプチャ
- `_feed_trade_dict_sync()` を `feed_trade_dict()` の同期薄ラッパーとして追加

**`TachibanaEventBridge`** に追加:
- 同様に `_loop` をキャプチャ、`process_ec_event()` を loop B 上から呼べるよう確認

---

## ✅ Phase 4: NautilusRunner.start_live() 実装

### `python/engine/nautilus/engine_runner.py`

stub (L1043-1063) を**完全削除（`assert` 行含む）して**新実装に置き換える:

```python
def start_live(
    self,
    *,
    instrument_id: str,
    strategy_file: str | None,
    strategy_init_kwargs: dict | None,
    max_qty: int,
    max_notional_jpy: int,
    session: Any,
    fd_queue: "queue.Queue",
    ec_queue: "queue.Queue",
    on_event: Callable[[dict], None],
    stop_event: "threading.Event",
    strategy_id: str,
) -> None:
```

**実装ステップ（ループ B 内）**:

1. `second_password` は引数で受け取る（env 変数不使用）。  
   取得は呼び出し元 `_handle_start_engine()` の else 分岐で `self._session_holder.get_password()` を呼ぶ。  
   `start_live()` のシグネチャに `second_password: str` を追加する。
2. **TradingNode セットアップ**:
   - `TradingNodeConfig(trader_id=f"TACHIBANA-{safe_id}", cache=CacheConfig(database=None), ...)`
   - `node = TradingNode(config=config)`
   - `data_client = TachibanaLiveDataClient(...)` + `node._data_engine.register_client(data_client)`
   - `exec_client = TachibanaLiveExecutionClient(session, second_password, max_qty, max_notional_jpy, p_no_counter=PNoCounter(), ...)` （`PNoCounter` を新規生成して渡す）+ `node._exec_engine.register_client(exec_client)`
3. **Instrument 登録**: `make_equity_instrument(instrument_id)` → `node.add_data(instrument)`
4. **Strategy ロード**: `_load_user_strategy(strategy_file, strategy_init_kwargs)` → `node.add_strategies([strategy])`
5. **warm_up**: `await exec_client.warm_up()` (CLMOrderList から未決注文復元)  
   warm_up が例外を raise した場合は `on_event({"event": "Error", "code": "warm_up_failed", "message": str(exc)})` を emit して return する（EngineStarted は emit しない）
6. **OrderFilled 購読**: msgbus で `OrderFilled` → ExecutionMarker emit + LiveBuyingPower emit（call_soon_threadsafe 経由で loop A へ）
7. **EngineStarted emit** (call_soon_threadsafe):
   ```python
   on_event({"event": "EngineStarted", "strategy_id": strategy_id,
             "account_id": "tachibana", "ts_event_ms": now_ms()})
   ```
8. **Bridge threads 起動**:
   ```python
   data_bridge = LiveDataBridge(data_client, fd_queue, instrument_id, stop_event)
   ec_bridge = LiveEcBridge(event_bridge, ec_queue, stop_event)
   t_data = threading.Thread(target=data_bridge.run, daemon=True); t_data.start()
   t_ec = threading.Thread(target=ec_bridge.run, daemon=True); t_ec.start()
   ```
9. **TradingNode 起動**: `node.build()` → `node.run()` (blocking, loop B 内)  
   stop_event を polling しながらグレースフルシャットダウン。**実装前に nautilus バージョンの API を spike で確認すること**:
   - `TradingNode.stop()` が `async def` の場合: loop B 上から `await node.stop()` を呼ぶ（`asyncio.run(node.stop())` は入れ子禁止のため不可）。`node.run()` が blocking sync の場合は `threading.Thread` 内で実行し、stop_event を監視する別 thread が `loop.call_soon_threadsafe(node.stop_sync)` を呼ぶ。
   - `TradingNode.stop()` が sync の場合: 直接呼ぶ。stop_event を `asyncio.wait([node_task, stop_waiter], return_when=FIRST_COMPLETED)` パターンで監視することも可。
10. **finally**:
    - bridge threads join（timeout=5s）
    - `on_event({"event": "EngineStopped", "strategy_id": strategy_id, ...})`

**OrderFilled → ExecutionMarker + LiveBuyingPower** (msgbus callback):
```python
def _on_order_filled(event: OrderFilled) -> None:
    on_event({
        "event": "ExecutionMarker",
        "strategy_id": strategy_id,
        "instrument_id": str(event.instrument_id),
        "side": event.order_side.name,
        "price": str(event.last_px),
        "qty": str(event.last_qty),
        "ts_event_ms": event.ts_event // 1_000_000,
    })
    # LiveBuyingPower: tachibana API から取得してリアルタイム push
    # レート制限: 最大 1回/秒
    # 実装方針: asyncio.call_later(1.0, ...) でデバウンスし、
    #            _buying_power_pending: bool フラグで重複排除する
    _schedule_buying_power_push(session, strategy_id, on_event)
```

---

## ✅ Phase 5: LiveSession.run() 実装

### `python/engine/replay_session.py`

> **注記**: `LiveSession` クラスは N2 以前から `python/engine/replay_session.py` に同居している（`ReplaySession` と同ファイル）。

`LiveSession` に `run()` / `stop()` を追加。

`__init__` に `second_password: str | None = None` を追加する:
```python
def __init__(self, ..., second_password: str | None = None):
    ...
    self._second_password = second_password
```

```python
def run(
    self,
    *,
    instrument_id: str,       # 必須: 取引対象銘柄 ID
    strategy_file: str,
    max_qty: int,
    max_notional_jpy: int,
    on_event: Callable[[dict], None] | None = None,
    strategy_init_kwargs: dict | None = None,
    strategy_id: str = "live-strategy",
    run_buffer: "RunBuffer | None" = None,
) -> None:
    if not self._logged_in:
        raise RuntimeError("call login() before run()")
    if self._second_password is None:
        raise RuntimeError("second_password is required for in-process live run")
```

**in-process 経路**（`ReplaySession.run()` L1061-1100 と同方式の blocking 直接呼び出し）:

`run()` 冒頭で `self._strategy_id = strategy_id` をインスタンス変数に格納する（`stop()` メソッドから参照するため）。
```python
runner = NautilusRunner()
self._stop_event = threading.Event()
# asyncio.to_thread() は await が必要な coroutine を返すため同期関数から呼べない。
# ReplaySession と同様に直接 blocking 呼び出しとする。
runner.start_live(
    instrument_id=instrument_id,
    strategy_file=strategy_file,
    strategy_init_kwargs=strategy_init_kwargs,
    max_qty=max_qty,
    max_notional_jpy=max_notional_jpy,
    second_password=self._second_password,
    session=self._session,
    fd_queue=...,
    ec_queue=...,
    on_event=on_event or (lambda _: None),
    stop_event=self._stop_event,
    strategy_id=strategy_id,
)
```
> **Note**: `second_password` は `LiveSession.__init__` で受け取る。`run()` 冒頭で `None` なら `RuntimeError` を raise する（attach 経路は server.py の `SessionHolder` が管理するので問題ない）。

**attach 経路**:
```python
cmd = StartEngine(
    request_id=run_request_id,
    engine="Live",
    strategy_id=strategy_id,
    config=EngineStartConfig(
        instrument_id=instrument_id,
        max_qty=max_qty,
        max_notional_jpy=max_notional_jpy,
        strategy_file=strategy_file,
        strategy_init_kwargs=strategy_init_kwargs,
    ),
)
self._client.send_command(cmd.model_dump())
for evt in self._client.events():
    kind = evt.get("event", "")
    if run_buffer:
        run_buffer.write_event(evt)
    on_event(evt) if on_event else None
    if kind == "EngineStopped":
        break
```

**`stop()` メソッド**:
- in-process: `self._stop_event.set()`
- attach: `StopEngine(strategy_id=self._strategy_id)` 送信（`strategy_id` は `run()` 冒頭で `self._strategy_id` に格納済みの値を参照する）

---

## ✅ Phase 6: LiveBuyingPower UI 統合

### `src/screen/dashboard/panel/buying_power.rs`

`BuyingPowerPanel` に live strategy 用フィールドを追加:
```rust
live_strategy_cash: Option<String>,
live_strategy_equity: Option<String>,
live_strategy_ts_ms: Option<i64>,
```

`set_live_strategy_portfolio(cash, equity, ts_ms)` メソッドを追加。

`view()` で live mode かつ live strategy データがある場合の表示分岐:
- "ライブ戦略余力: ¥{cash}" 
- "ライブ戦略評価額: ¥{equity}"

### Rust backend / event dispatcher

`EngineEvent::LiveBuyingPower { ... }` のパターンマッチを追加して `buying_power_panel` を更新:
```rust
EngineEvent::LiveBuyingPower { strategy_id, cash, equity, ts_event_ms, .. } => {
    Message::LiveBuyingPowerUpdated { strategy_id, cash, equity, ts_event_ms }
}
```

---

## ✅ Phase 7: capabilities 更新（mode.py + server.py 両方）

`Ready` イベントの `capabilities["nautilus"]` は `server.py` でハードコードされている（L799: `"live": False`）。`mode.py` の `nautilus_capabilities()` を変更するだけではクライアントに届く値が変わらない。両箇所を必ず更新すること。

### `python/engine/mode.py`

```python
def nautilus_capabilities(mode: Mode) -> dict[str, bool]:
    return {"backtest": True, "live": True}  # N3: live=True に変更
```

### `python/engine/server.py` — `_handshake()` 内の Ready 構築部（capabilities ハードコード箇所、L799）

```python
"nautilus": nautilus_capabilities(self._mode),  # mode.py から読む
```

`nautilus_capabilities` を `from engine.mode import nautilus_capabilities` でインポートし、ハードコード `{"backtest": True, "live": False}` を削除する。

---

## ✅ Phase 8: テスト（Phase 2+7 分のみ）

### 新規テストファイル

**`python/tests/test_live_state_machine.py`**:
- `LiveState.CONNECTED → TRADING → CONNECTED` の正常遷移
- `DISCONNECTED` で StartEngine{engine:"Live"} → EngineBusy
- `TRADING` で LoadReplayData → EngineBusy
- D1: `max_qty=None` で TRADING 状態から StartEngine → `invalid_config` テスト
- D2: `initial_cash=None` で live StartEngine → `int(None)` が走らず live 分岐到達テスト
- D3: `SessionHolder.get_password()` が None → `SecondPasswordRequired` emit テスト
- D4: `TRADING` 中に別 StartEngine → busy ガードテスト、`StopEngine` 受信 → `STOPPING` 遷移テスト

**`python/tests/test_live_bridges.py`**:
- `LiveDataBridge.run()`: `fd_queue.put(trade)` → `data_client.feed_trade_dict()` が呼ばれる
- `LiveEcBridge.run()`: `ec_queue.put(ec)` → `event_bridge.process_ec_event()` が呼ばれる
- D5: `stop_event.set()` 後 bridge thread 終了 + timeout join テスト
- D6: `fd_queue.Full` 時ドロップ + warning ログ テスト

**`python/tests/test_live_session_run.py`**:
- `LiveSession.run()` inprocess: monkeypatch した `start_live()` が EngineStarted / EngineStopped を emit することを確認
- D7: `login()` 前に `run()` → `RuntimeError` テスト
- D8: attach 経路で `SecondPasswordRequired` が返ったとき `run()` が伝播するテスト
- D9: `stop()` が `stop_event.set()` を呼ぶテスト

**`python/tests/test_live_buying_power_schema.py`**:
- `LiveBuyingPower` の Python ↔ JSON シリアライズ対称性確認
- D10: `extra="forbid"` で未知フィールドを含む JSON → `ValidationError` テスト

**`python/tests/test_live_validation.py`**（新規、または `test_live_state_machine.py` に統合）:
- D11: 不正 `initial_cash` 文字列で replay StartEngine → `invalid_config` テスト（`assert evt["event"] == "Error"` かつ `assert evt["code"] == "invalid_config"` かつ `assert "initial_cash" in evt["message"]`）

---

## 変更ファイル一覧

| ファイル | 内容 |
|---|---|
| `python/engine/schemas.py` | `SCHEMA_MINOR=17`、`EngineStartConfig` Optional 化 + max_qty/max_notional_jpy、`LiveBuyingPower` 新規、`LiveStateName` 拡張 |
| `engine-client/src/dto.rs` | `EngineStartConfig` Optional 化 + max_qty/max_notional_jpy、`CurrentEngineState::Trading`（新規追加、`Stopping` は既存 replay/live 共用）、`EngineEvent::LiveBuyingPower` |
| `python/engine/server.py` | `LiveState` 拡張、`_handle_start_engine` の `_run()` 内 `elif self._mode == "live":` 追加（前段 `initial_cash` パースを replay 分岐内へ移設、`SessionHolder.get_password()` 経由で `second_password` 取得、`_replay_portfolio.reset` に `if self._mode == "replay":` ガード追加、`_on_event_tracked` の `EngineStarted` 時 `clear()` に replay ガード、`_handle_stop_engine()` に TRADING→STOPPING 遷移追加、finally でキュードレイン）、Queue 追加、FD/EC routing、`_handshake()` capabilities を `nautilus_capabilities()` 呼び出しに変更 |
| `python/engine/nautilus/engine_runner.py` | `start_live()` 完全実装（stub 全体を削除、`second_password: str` + `p_no_counter=PNoCounter()` パラメータ追加、env 変数依存なし、warm_up 失敗時 Error emit） |
| `python/engine/nautilus/live_bridges.py` | 新規: `LiveDataBridge` / `LiveEcBridge` |
| `python/engine/nautilus/clients/tachibana_data.py` | `_loop` キャプチャ、`_feed_trade_dict_sync()` 追加 |
| `python/engine/nautilus/clients/tachibana_event_bridge.py` | `_loop` キャプチャ確認 |
| `python/engine/replay_session.py` | `LiveSession.__init__` に `second_password: str \| None = None` 追加、`LiveSession.run(instrument_id: str, ...)` / `stop()` 実装 |
| `python/engine/mode.py` | `nautilus_capabilities` live=True（server.py の `_handshake()` Ready 構築も併せて変更） |
| `src/screen/dashboard/panel/buying_power.rs` | `LiveBuyingPower` 受信・表示 |
| Rust backend dispatcher | `EngineEvent::LiveBuyingPower` ルーティング |
| `python/tests/` | 上記テストファイル追加（test_live_state_machine / test_live_bridges / test_live_session_run / test_live_buying_power_schema / test_live_validation） |
| `examples/strategies/live_sample.py` | 新規作成: E2E 用 live 戦略サンプル |

---

## 再利用する既存実装

| パス | 名前 | 用途 |
|---|---|---|
| `python/engine/nautilus/clients/tachibana.py` | `TachibanaLiveExecutionClient` | そのまま start_live() で使用 |
| `python/engine/nautilus/clients/tachibana_data.py` | `TachibanaLiveDataClient.feed_trade_dict()` | LiveDataBridge の投入先 |
| `python/engine/nautilus/clients/tachibana_event_bridge.py` | `TachibanaEventBridge` | LiveEcBridge の投入先 |
| `python/engine/nautilus/clients/tachibana_event_bridge.py` | `OrderIdMap.warm_up_from_records()` | warm_up() で使用 |
| `python/engine/exchanges/tachibana_orders.py` | `fetch_order_list()` | warm_up() の CLMOrderList 取得 |
| `python/engine/exchanges/tachibana_ws.py` | `is_market_open()` | N2.4 ガード確認 |
| `python/engine/nautilus/strategy_loader.py` | `_load_user_strategy()` | strategy_file ロード |
| `python/engine/nautilus/engine_runner.py` | `_BYPASS_LOG` / `make_equity_instrument()` | TradingNode セットアップ |
| `python/engine/replay_session.py` | `_AttachClient` / `_read_session_file()` | attach 経路で共用 |
| `python/engine/server.py` | `_emit_threadsafe` パターン / `_check_replay_state()` | `_check_live_state()` の参考実装 |

---

## Verification

### 静的確認
```
/ipc-schema-check          # SCHEMA_MINOR 整合
cargo check --workspace    # Rust コンパイル
cargo clippy -- -D warnings
```

### Unit テスト
```
pytest python/tests/test_live_state_machine.py
pytest python/tests/test_live_bridges.py
pytest python/tests/test_live_session_run.py
pytest python/tests/test_live_buying_power_schema.py
pytest python/tests/test_live_validation.py
```

### E2E 確認（デモ環境）
1. デモ環境でログイン（`SetSecondPassword` で第二暗証番号を UI/helper から注入）
2. `LiveSession(demo=True).login()` → VenueReady 確認
3. `run(instrument_id="8306.T", strategy_file="examples/strategies/live_sample.py", max_qty=100, max_notional_jpy=500_000)` 実行
4. `EngineStarted` イベント受信を確認
5. デモ注文送信 → EC frame → ExecutionMarker + LiveBuyingPower が push されることを確認
6. `stop()` → `EngineStopped` 受信、`LiveState.CONNECTED` に戻ることを確認

### 安全装置確認
- `max_qty` 未指定 → `invalid_config` エラー返却
- `SessionHolder.get_password()` が None → `SecondPasswordRequired` で起動拒否（env 変数不使用）
- `initial_cash=None` で live StartEngine → replay 分岐の `int()` パースが走らず live 分岐へ正常到達
- 市場閉場時刻に発注 → `order_denied` 返却（`is_market_open()` ガード）
- FD queue 満杯時にフレームをドロップし warning ログが出ることを確認
