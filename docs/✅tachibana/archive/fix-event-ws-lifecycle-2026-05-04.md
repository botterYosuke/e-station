# EVENT WebSocket ライフサイクル修正計画 (2026-05-04)

立花 EVENT WebSocket 周りの 2 件のバグを順に潰す計画。両者ともライフサイクル
管理の不備に起因し近接しているが、経路が違う別バグ。

---

## 背景：2 つの独立した EVENT WS

立花 EVENT WS には 2 系統ある。今回はどちらにも別個のバグがある。

| 系統 | URL | オーナー | 用途 |
|------|-----|---------|------|
| **EC ループ** | `wss://.../event_ws/<sess>/`（query なし） | `DataEngineServer._event_task` ([server.py:381](../../python/engine/server.py#L381)) | 約定通知 (EC) を 1 接続で受信 |
| **per-ticker ストリーム** | `wss://.../event_ws/<sess>/?p_issue_code=XXXX&p_evt_cmd=ST,KP,FD&...` | `TachibanaWorker.stream_depth` / `stream_trades` ([tachibana.py:942](../../python/engine/exchanges/tachibana.py#L942) / [:857](../../python/engine/exchanges/tachibana.py#L857)) | ticker 毎の板/約定/KP keepalive |

---

## Bug X：再ログイン時に旧 EC ループ (_event_task) が残る（レビュアー指摘）

### 症状

`LiveState.CONNECTED` の状態で `RequestVenueLogin` を受けると、本日の修正
([server.py:2588-2596](../../python/engine/server.py#L2588-L2596)) で
`_live_state` を `DISCONNECTED` に巻き戻して新ログインを始めるが、
**旧 `_event_task` を cancel していない**。

新ログインが成功すれば `_startup_tachibana` の終端で旧タスクが cancel される
([server.py:2454-2455](../../python/engine/server.py#L2454-L2455)) ため問題化しないが、
新ログインが**失敗・キャンセル**された場合、`_live_state` は `DISCONNECTED` だが
旧 `_event_task` だけが残り、旧セッション URL から EC 約定通知を受け続ける
ゴースト状態になる。

### 修正方針

`_do_request_venue_login` の CONNECTED 救済分岐内で `_event_task` も cancel する。
`_startup_tachibana` の 2454-2455 と同じパターン。

```python
if self._live_state == LiveState.CONNECTED:
    log.info("RequestVenueLogin: re-login while CONNECTED — clearing session and cancelling EC loop")
    self._live_state = LiveState.DISCONNECTED
    if self._event_task is not None and not self._event_task.done():
        self._event_task.cancel()
        # await は不要：fire-and-forget で _startup_tachibana が新タスクを建てる
```

### テスト

`python/tests/test_request_venue_login_state.py` に追加：

- `test_relogin_from_connected_cancels_old_event_task`
  - 既存 `_event_task = asyncio.create_task(<long-running coroutine>)` を仕込む
  - `_do_request_venue_login` 呼び出し後、旧 task が cancel 状態であることを assert
  - 新 task が startup_tachibana 起動を介して別オブジェクトとして作られても良い／作られなくても良い（`_startup_tachibana` は monkeypatch する）

### 工数

15〜30 分（コード 3 行＋テスト 1 件）。

---

## Bug Y：同一 ticker への並行 WS で立花が p_errno=2 を返す（H3 確定）

### 症状

起動直後にバナー「立花仮想 URL が失効しました（p_errno=2）。再ログインが必要です」
が出る。実際は URL は失効しておらず、立花 broker が「重複セッション」を蹴った
だけ（`p_err='session inactive.'`）。

### 観測ログ（2026-05-04 実測）

```
10:15:55.815  > GET /e_api_v4r8/event_ws/NTc4...=/?p_rid=22&p_issue_code=7203
10:15:55.816  > GET /e_api_v4r8/event_ws/NTc4...=/?p_rid=22&p_issue_code=7203  ← 完全同一
10:15:55.835  tachibana ws: connected ticker=7203 conn=#1   ← 2 回印字
10:16:05.304  ST p_errno=2 'session inactive.'              ← 約 10 秒後に broker が片側を切断
```

### 根本原因

`stream_depth` と `stream_trades` がそれぞれ独立に `TachibanaEventWs` を作って
**同一 ticker の同一 URL に並行接続**する。立花 EVENT WS は
`(session, p_issue_code)` 単位で 1 接続のみ許容。

```
[現状]
TachibanaWorker
 ├─ stream_depth(7203)  → TachibanaEventWs ─┐
 │                                          ├─→ wss://.../?p_issue_code=7203
 └─ stream_trades(7203) → TachibanaEventWs ─┘    （broker が後発を p_errno=2 で蹴る）
```

### 修正方針：per-ticker WS マルチプレクサ

ticker 毎に EVENT WS を **1 本だけ**張り、受信した KP/FD/ST フレームを
複数のコンシューマ（depth デコーダ・trade デコーダ）にファンアウトする。

```
[修正後]
TachibanaWorker
 ├─ stream_depth(7203)  ─→ Hub.subscribe("depth")  ─┐
 │                                                  │
 ├─ stream_trades(7203) ─→ Hub.subscribe("trades") ─┤
 │                                                  ▼
 └─ _ticker_hubs[7203]: TickerEventWsHub ──→ TachibanaEventWs ──→ broker (1 接続)
```

#### 新コンポーネント: `TickerEventWsHub`

設置場所: `python/engine/exchanges/tachibana_ws.py`（同一モジュール）

責務：
- 1 つの `TachibanaEventWs` を所有し、その frame callback を `_dispatch` に固定
- subscribers (`dict[str, FrameCallback]`) に対し各 frame を `await` でファンアウト
- 最初の `subscribe()` で WS タスクを起動、最後の `unsubscribe()` で stop_event を set
- WS 切断・再接続は既存 `TachibanaEventWs` の責任（変更なし）
- ST p_errno=2 のような fatal frame もそのままファンアウト（コンシューマ各自が解釈）

API（最小）：
```python
class TickerEventWsHub:
    def __init__(self, ws_url: str, *, ticker: str, proxy: str | None) -> None: ...
    async def subscribe(self, key: str, callback: FrameCallback) -> None: ...
    async def unsubscribe(self, key: str) -> None: ...
    @property
    def subscriber_count(self) -> int: ...
```

#### `TachibanaWorker` 側の変更

- `self._ticker_hubs: dict[str, TickerEventWsHub]` を追加（lock で保護）
- `_get_or_create_hub(ticker)` ヘルパー
- `stream_depth` / `stream_trades` から `TachibanaEventWs` 直接生成を削除し、
  代わりに hub に `subscribe(key, _cb_depth)` / `subscribe(key, _cb_trade)` する
- 各 stream のローカル状態（`_first_fd_received`, `processor`, `st_last_emit` 等）
  はクロージャに閉じ込めたまま hub の callback で参照する
- `stop_event` は hub 共有ではなく**各 subscriber 個別**に持つ：
  - depth が止まりたい時は `unsubscribe("depth")`、trades は継続
  - 全員 unsubscribe で hub が WS を畳む
- ST p_errno=2 を受けた depth は `unsubscribe("depth")` してから polling fallback へ

#### 不変条件

1. **同一 ticker・同一プロセス内で EVENT WS は 1 本のみ**
2. **subscribers が 0 になった時点で WS task は cancel** される（リーク禁止）
3. **subscribe/unsubscribe は idempotent**（二重 subscribe は警告ログのみ、二重 unsubscribe は no-op）
4. **session 切替時は hub も全廃**：`_apply_tachibana_session(None)` で全 hub を畳む

### 段取り（TDD）

| # | テスト | 実装 |
|---|------|------|
| Y1 | `test_two_subscribers_share_one_ws` | TickerEventWsHub 雛形＋ subscribe/unsubscribe/refcount |
| Y2 | `test_frame_fanout_to_all_subscribers` | `_dispatch` の await 順序保証＋例外で他 subscriber を巻き込まない |
| Y3 | `test_unsubscribe_last_closes_ws` | refcount=0 で stop_event.set() →task cancel |
| Y4 | `test_session_swap_drops_all_hubs` | `TachibanaWorker.set_session(None)` で `_ticker_hubs` clear |
| Y5 | `test_stream_depth_uses_hub_not_direct_ws` | TachibanaEventWs を mock、stream_depth が直接 instantiate しないことを確認 |
| Y6 | `test_concurrent_depth_and_trades_single_connection` | 同 ticker の depth+trades 起動で TachibanaEventWs インスタンス数 = 1 |

### リスク・落とし穴

- **ST p_errno=2 の解釈差**：depth は polling fallback、trades は単に "市場閉" 扱いと
  解釈分岐がある（[tachibana.py:925-937](../../python/engine/exchanges/tachibana.py#L925-L937) vs [:1141-1167](../../python/engine/exchanges/tachibana.py#L1141-L1167)）。
  hub は ST を解釈せず素通しすること。
- **callback の例外伝播**：1 subscriber の例外が他 subscriber を止めないよう
  `_dispatch` で `try/except` し `log.exception` する。
- **テスト用 mock**：`TachibanaEventWs` は monkeypatch 容易な作りなので問題なし。
- **既存テスト**：`test_tachibana_depth_safety.py` / `test_tachibana_ws_fd_depth_recv.py`
  / `test_tachibana_normalize_integration.py` 等は stream_depth を直接呼ぶ。
  hub 経由でも同じ outbox になるよう挙動互換を保つ（API 変更なし）。

### 工数

2〜4 時間（テスト 6 件＋実装＋既存テスト互換確認）。

---

## 共通：[DIAG] ログの削除

Bug Y 確定後、診断のため仕込んだ `[DIAG]` ログを全削除する：

- [tachibana_login_flow.py](../../python/engine/exchanges/tachibana_login_flow.py)
  - `SOURCE=cache` ログ
  - `SOURCE=fresh-dev-env` ログ
- [tachibana.py](../../python/engine/exchanges/tachibana.py)
  - `connecting EVENT WS` ログ
  - `ST p_errno=2` 詳細ログ

CLAUDE.md「不具合が見つかった時の対処法」ステップ 1.5（追加したログをすべて
削除する）に従う。

---

## 工程順序

1. **Bug X 修正**（先）
   - `_do_request_venue_login` の CONNECTED 分岐に `_event_task.cancel()` 追加
   - `test_relogin_from_connected_cancels_old_event_task` 追加
   - フルテスト：`uv run pytest python/tests/ -m "not live" -q`
2. **Bug Y 修正**（後）
   - Y1〜Y6 を順に TDD（RED → GREEN → REFACTOR）
   - フルテスト
   - 実機デバッグで「立花仮想URL失効」バナーが出ないことを目視確認
3. **DIAG ログ削除**
4. `/bug-postmortem` を Bug X / Bug Y それぞれに走らせ MISSES.md を更新

各ステップ完了時に都度ユーザー確認を取る。
