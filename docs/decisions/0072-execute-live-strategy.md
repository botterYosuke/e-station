---
id: 0072
title: "Execute Live Strategy — TradingNode による live 実行アダプタ配線"
status: accepted
date: 2026-05-08
source_commit: ea5022b
old_path: "docs/✅nautilus_trader/archive/🔵execute-live-strategy.md"
---

# ADR 0072: Execute Live Strategy — TradingNode による live 実行アダプタ配線

## Status

accepted

> **Note**: 起票時点の `source_commit` は原本（`ea5022b`）。issue #42 の最終
> merge commit が確定したら、後続 PR の fixup commit で merge SHA に更新可能。

## Context

e-station の live 実行アダプタ（`TachibanaLiveExecutionClient` /
`TachibanaLiveDataClient` / `TachibanaEventBridge`）は実装済みだったが、
それらを **engine プロセスから起動して TradingNode に組み込む配線** が
全面的に欠けていた。

具体的な欠落:

1. `server.py::_handle_start_engine()` の else 分岐がバグで、live なのに
   `start_backtest_replay()` を呼んでいた
2. `NautilusRunner.start_live()` が stub のみ（`assert` + `log` だけ）
3. `LiveSession.run()` が未実装で、CLI / GUI から live を起こす経路がない
4. `LiveState` enum に `TRADING` / `STOPPING` がなく、live 中の state machine
   が未完成
5. IPC スキーマに `LiveBuyingPower` / `EngineStartConfig.max_qty` /
   `max_notional_jpy` がない
6. FD frame（板情報）/ EC frame（注文約定）を Python event loop A から
   TradingNode の event loop B に **スレッドセーフに渡す bridge** が未配線

これらを issue #42 の Phase 1 / Phase 4 / Wave 1 schema chain で順に解決し、
ADR を deferred から accepted に昇格させる。

## Decision

`engine_runner.start_live()` を中心に、TradingNode を core に据えた live 実行
アーキテクチャを採用する。

### 1. 全体構造（loop A / loop B 二段構成）

```text
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
  ├─ FD frame ──→ fd_queue.put_nowait()  [TRADING 状態のみ]
  ├─ EC frame ──→ ec_queue.put_nowait()  [TRADING 状態のみ]
  └─ on_event ←── loop_A.call_soon_threadsafe(outbox.append, event)
```

server.py は wire I/O を担う event loop A、TradingNode は Nautilus 内部で
動く event loop B。両者の間は **Python `queue.Queue` + `call_soon_threadsafe`**
でつなぎ、どちらの loop も blocking しない。

### 2. IPC スキーマ拡張

- `EngineStartConfig` に `max_qty: int | None` / `max_notional_jpy: int | None`
  を追加（live 必須、replay 時は None 許容）。replay 専用フィールド
  （`start_date` / `end_date` / `initial_cash` / `granularity`）は Optional 化。
- `LiveBuyingPower{strategy_id, cash, buying_power, equity, ts_event_ms}`
  event を新設し、Rust 側 `EngineEvent::LiveBuyingPower` variant を追加。
- `LiveState` enum に `TRADING` / `STOPPING` を追加し、`CONNECTED → TRADING →
  CONNECTED` の遷移を `_check_live_state()` で守る。
- issue #42 Wave 1 で SCHEMA_MINOR を 24 → 28 に bump し、新 IPCs
  （`LoadLiveStrategyScenario` / `LiveStrategyScenarioLoaded` /
  `LiveStrategyReady` / `LiveStrategyWarmingUp` + `EngineBusy.venue` /
  `busy_kind`）を追加する。

### 3. `NautilusRunner.start_live()` 実装契約

```python
def start_live(
    self,
    *,
    instrument_id: str,
    strategy_file: str | None,
    strategy_init_kwargs: dict | None,
    max_qty: int,
    max_notional_jpy: int,
    second_password: str,
    venue: str,                   # issue #42 Phase 4: tachibana / kabu_station
    session: Any,
    fd_queue: queue.Queue,
    ec_queue: queue.Queue,
    on_event: Callable[[dict], None],
    stop_event: threading.Event,
    strategy_id: str,
) -> None:
```

実装ステップ（loop B 内）:

1. `is_market_open()` ガード — 失敗時は `EngineError{code:"market_closed",
   strategy_id}` emit + abort（issue #42 統一決定 #5）
2. **TradingNode セットアップ** — `TradingNodeConfig(trader_id=...)` →
   `node = TradingNode(config=config)` → venue 別 client（tachibana /
   kabu_station）を `register_client(...)` で接続
3. **Instrument 登録** — `make_equity_instrument(instrument_id)` →
   `node.add_data(instrument)`
4. **Strategy ロード** — `load_strategy_from_file(strategy_file,
   strategy_init_kwargs)` で **replay と同じ loader** を使う
   （受け入れ基準 #21）
5. **warm_up** — `await exec_client.warm_up()` で未決注文を CLMOrderList
   から復元。**例外 OR `False` 戻り値の OR で abort** とし、
   `EngineError{code:"warm_up_failed"}` emit + `await exec_client.close()`
   呼出を必須にする（issue #42 統一決定 #16 / 受け入れ基準 #14）
6. **`EngineStarted` emit** — `warm_up()` 成功直後 / `node.build()` より前。
   account_id には venue 名（`"tachibana"` / `"kabu_station"`）をそのまま流す。
   Rust 側 `src/handlers/replay.rs::ReplayMsg::LiveStarted` arm が
   `pending_strategy_id` セット + 60s warm_up timeout タイマー起動を担当する。
7. **`LiveStrategyReady` emit** — `EngineStarted` の **直後**（必ず Started → Ready の順）。
   理由: 4 ペイン自動生成 + Rust state machine の `Running { strategy_id, instrument_id, venue }`
   遷移を「データが流れ始める前」に終わらせるため
   （受け入れ基準 #11、issue #42 統一決定 #21）。Rust 側 `ReplayMsg::LiveStrategyReady` arm が
   step 6 で立てた `pending_strategy_id` / timeout token を解除する責務を持つため
   **逆順だと phantom warm_up timeout banner が GUI に出る silent regression** を起こす
   （R8 HIGH-1 で固定、E2E 契約 `_EXPECTED_LIFECYCLE` と一致）。
8. **Bridge threads 起動** — `LiveDataBridge` / `LiveEcBridge` を daemon thread で
   起動し、`call_soon_threadsafe` 経由で loop B 上の client を呼ぶ
   （tachibana 専用 — kabu_station は `_handle_subscribe_kabu_station` 経路）。
9. **TradingNode 起動** — `node.build()` → `node.run()`（blocking）。
   `node.build()` 失敗時は `EngineError{code:"node_build_failed"}` emit +
   Rust 側で `teardown_live_panes` を呼ぶ責務
10. **finally** — bridge threads join（timeout=5s）+
    `EngineStopped{strategy_id}` emit + `LiveState` を CONNECTED に戻す +
    `_live_fd_queue` / `_live_ec_queue` をドレイン

### 4. 第二暗証番号の扱い

`second_password` は `start_live()` の引数で受け取る。
取得元は呼び出し元 `_handle_start_engine()` で `self._session_holder.get_password()`
を呼ぶ。**env 変数経路は使わない**（CLI 経路では §3.2-D.1 の優先順
[stdin > env > argv] で取得する）。

`SessionHolder.get_password()` が `None` の場合は `SecondPasswordRequired`
event を emit して abort。固定文言「第二暗証番号を設定してください」は
CLI / GUI で共通（受け入れ基準 #8）。

### 5. `LiveSession.run()` 実装契約

`python/engine/replay_session.py` 内の `LiveSession` クラスに `run()` /
`stop()` を実装する。

- `__init__` で `second_password: str | None = None` を受け取る
  （CLI から `--second-password-stdin` 経由で渡される）
- in-process 経路 — `runner.start_live(...)` を blocking 直接呼出
- attach 経路 — `StartEngine` を gRPC 経由で送り、`EngineStopped` 受信まで
  events を on_event に流す
- `stop()` — in-process では `self._stop_event.set()`、attach では
  `StopEngine{strategy_id}` を送る

### 6. `_handle_start_engine()` の live 分岐

`_run()` クロージャ内に `elif self._mode == "live":` 分岐を追加し、
次の順で gating する:

1. `engine_already_running` ガード（同一 strategy_id の重複起動 reject）
2. **venue 単位 concurrent ガード**（同 venue で別 strategy_id が走っている →
   `EngineBusy{busy_kind:"another_strategy_on_venue", venue}`）
3. `_check_live_state(LiveState.CONNECTED)` で venue 接続済みを確認
4. `max_qty` / `max_notional_jpy` 必須 validation
5. `SessionHolder.get_password()` で第二暗証番号取得（None なら
   `SecondPasswordRequired` emit）
6. `_active_live_venues.add(venue)` → `runner.start_live(...)` を
   `asyncio.to_thread(_run)` で起動
7. `finally` で `_active_live_venues.discard(venue)`（warm_up failure /
   timeout / 例外いずれの経路でも cleanup 保証）

### 7. capability の expose

- `nautilus_capabilities()` の `live: True` を venue 別 capability に分離
  （`supports_live_strategy: bool`）
- tachibana worker は `is_production: bool`（`TACHIBANA_ALLOW_PROD == "1"`）を expose
- kabu_station venue は issue #42 Phase 4 で `supports_live_strategy=True` に
  flip（`KabuStationLiveExecutionClient` / `KabuStationLiveDataClient` を新設）

## Consequences

### 良い点

- live 戦略を **TradingNode 上で動かす契約に統一** したことで、replay と live で
  Strategy SDK を完全に共有できる（`load_strategy_from_file` の loader を
  両経路で利用、受け入れ基準 #21）
- venue 単位 concurrent ガード + 同一 strategy_id ガードの 2 段で、
  multi-venue / multi-strategy が将来増えたときも安全装置が残る
  （受け入れ基準 #16）
- warm_up 失敗（例外 OR `False` 戻り値）→ `exec_client.close()` の
  cleanup チェーンを契約として固定（HTTP session / WebSocket subscription
  リーク防止、受け入れ基準 #14）
- `LiveStrategyReady` を `warm_up()` 成功直後に emit する規約により、
  GUI 側はデータが流れ始める前に 4 ペインを生成でき、初期データの
  取りこぼしリスクを排除（受け入れ基準 #11）

### コスト

- loop A / loop B 二段構成は `call_soon_threadsafe` を介した通信が必要で、
  bridge thread の lifecycle 管理（join timeout / stop_event）が複雑
- `node.run()` が blocking sync か `async def` かは Nautilus バージョンで
  違うため、stop_event 監視の実装は **spike が必要**（issue #42 では
  blocking sync 想定で実装し、`stop_event.set()` を別 thread から飛ばす）
- `is_production` cap を読む経路を増やしたことで、env が変わっても engine
  再起動するまで GUI に反映されない（が、これは仕様 — 統一決定 #14）

### 非ゴール

- 複数 live 戦略の同時実行（同 venue では `EngineBusy` で reject、
  別 venue は将来課題）
- 本番口座向け追加リスクガード（日次最大損失、ポジション上限等）
- live pane の `set_content_and_streams` 相当（既存 helper を再利用）

## 関連

- 原本: `git show ea5022b:"docs/✅nautilus_trader/archive/🔵execute-live-strategy.md"`
- 並列 ADR: [0071 — Live Strategy GUI](0071-live-strategy-gui.md)
- 仕様書: [`docs/specs/live-strategy.md §5`](../specs/live-strategy.md)
- 実装 issue: GitHub issue #42（feat/issue-42-live-strategy ブランチ）
