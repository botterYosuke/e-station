"""Bug Y (docs/✅tachibana/fix-event-ws-lifecycle-2026-05-04.md):
per-ticker EVENT WS multiplexer (`TickerEventWsHub`).

立花 EVENT WS は (session, p_issue_code) 単位で 1 接続のみ。stream_depth と
stream_trades が同 ticker に並行接続して broker から p_errno=2 を蹴られる事故の
再発防止として、ticker 毎に WS を 1 本だけ張り frame を fanout する hub を
入れる。
"""

from __future__ import annotations

import asyncio

import pytest


class _FakeWs:
    """TachibanaEventWs 互換の最小モック。

    `instances` クラス変数で生成回数を追跡する。`run(callback)` は
    `frames` に積まれたフレームを順に callback へ流し、最後に stop_event を
    待ってから return する（実物と同じ形）。
    """

    instances: list[_FakeWs] = []

    def __init__(self, url, stop_event, *, ticker, venue="tachibana", proxy=None):
        self.url = url
        self.stop_event = stop_event
        self.ticker = ticker
        self.proxy = proxy
        self.frames: list[tuple[str, dict, int]] = []
        self.run_started = asyncio.Event()
        self.run_finished = asyncio.Event()
        # テストが frames を仕込む隙を与えるため、明示的に start_streaming() を
        # 呼ぶまで配信を始めない。
        self.start_streaming = asyncio.Event()
        type(self).instances.append(self)

    async def run(self, callback, *, on_connect=None):
        self.run_started.set()
        if on_connect is not None:
            on_connect()
        # テスト側が frames 設定 → start_streaming.set() するまで待つ。
        # stop_event が先に立った場合は frames を流さず即終了する。
        start_task = asyncio.create_task(self.start_streaming.wait())
        stop_task = asyncio.create_task(self.stop_event.wait())
        done, pending = await asyncio.wait(
            [start_task, stop_task], return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if self.stop_event.is_set():
            self.run_finished.set()
            return
        for ft, fields, ts in self.frames:
            await callback(ft, fields, ts)
        await self.stop_event.wait()
        self.run_finished.set()


@pytest.fixture(autouse=True)
def _reset_fake_ws():
    _FakeWs.instances = []
    yield
    _FakeWs.instances = []


@pytest.fixture
def patch_event_ws(monkeypatch):
    from engine.exchanges import tachibana_ws
    monkeypatch.setattr(tachibana_ws, "TachibanaEventWs", _FakeWs)
    return _FakeWs


@pytest.mark.asyncio
async def test_two_subscribers_share_one_ws(patch_event_ws):
    """Y1: 同一 hub に 2 つ subscribe しても WS インスタンスは 1 つ。"""
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    hub = TickerEventWsHub("wss://example/event_ws/sess/?p_issue_code=7203", ticker="7203")

    async def _cb_a(_ft, _fields, _ts):
        pass

    async def _cb_b(_ft, _fields, _ts):
        pass

    await hub.subscribe("depth", _cb_a)
    await hub.subscribe("trades", _cb_b)

    # WS タスクが起動するまで 1 tick 進める
    await asyncio.sleep(0)

    assert hub.subscriber_count == 2
    assert len(patch_event_ws.instances) == 1, (
        f"per-ticker WS は 1 本のみであるべき: instances={len(patch_event_ws.instances)}"
    )

    # cleanup
    await hub.aclose()


@pytest.mark.asyncio
async def test_duplicate_subscribe_key_is_ignored(patch_event_ws):
    """Y1: 同じ key で 2 回 subscribe しても subscriber_count は 1 のまま。"""
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    hub = TickerEventWsHub("wss://example/", ticker="7203")

    async def _cb(_ft, _fields, _ts):
        pass

    await hub.subscribe("depth", _cb)
    await hub.subscribe("depth", _cb)  # duplicate

    await asyncio.sleep(0)
    assert hub.subscriber_count == 1
    await hub.aclose()


@pytest.mark.asyncio
async def test_unsubscribe_unknown_key_is_noop(patch_event_ws):
    """Y1: 存在しない key の unsubscribe は no-op。"""
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    hub = TickerEventWsHub("wss://example/", ticker="7203")
    # 何も subscribe していない状態で unsubscribe しても例外にならない
    await hub.unsubscribe("nonexistent")
    assert hub.subscriber_count == 0


@pytest.mark.asyncio
async def test_frame_fanout_to_all_subscribers(patch_event_ws):
    """Y2: 受信した frame は全 subscriber に同じ内容で配られる。"""
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    hub = TickerEventWsHub("wss://example/", ticker="7203")
    received_a: list[tuple[str, dict, int]] = []
    received_b: list[tuple[str, dict, int]] = []

    async def _cb_a(ft, fields, ts):
        received_a.append((ft, dict(fields), ts))

    async def _cb_b(ft, fields, ts):
        received_b.append((ft, dict(fields), ts))

    await hub.subscribe("depth", _cb_a)
    await hub.subscribe("trades", _cb_b)

    # WS タスクが起動するまで yield → instance を取得 → frames を仕込んでから配信開始
    await asyncio.sleep(0)
    ws = patch_event_ws.instances[0]
    ws.frames = [
        ("KP", {"p_no": "1"}, 1000),
        ("FD", {"p_no": "2", "p_BidPrice1": "100"}, 2000),
        ("ST", {"p_no": "3", "p_errno": "0"}, 3000),
    ]
    await ws.run_started.wait()
    ws.start_streaming.set()
    # frames は run の中で同期的に処理される。stop_event を立てる前に await sleep(0) で
    # callback が回るのを待つ。
    for _ in range(10):
        await asyncio.sleep(0)
    await hub.aclose()

    assert received_a == [
        ("KP", {"p_no": "1"}, 1000),
        ("FD", {"p_no": "2", "p_BidPrice1": "100"}, 2000),
        ("ST", {"p_no": "3", "p_errno": "0"}, 3000),
    ]
    assert received_b == received_a, "全 subscriber が同じフレームを同じ順で受け取ること"


@pytest.mark.asyncio
async def test_subscriber_exception_does_not_break_others(patch_event_ws, caplog):
    """Y2: 1 subscriber が例外を投げても他 subscriber は続行する。"""
    import logging
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    caplog.set_level(logging.ERROR, logger="engine.exchanges.tachibana_ws")

    hub = TickerEventWsHub("wss://example/", ticker="7203")
    received_good: list[str] = []

    async def _cb_bad(_ft, _fields, _ts):
        raise RuntimeError("intentional test failure")

    async def _cb_good(ft, _fields, _ts):
        received_good.append(ft)

    await hub.subscribe("bad", _cb_bad)
    await hub.subscribe("good", _cb_good)

    await asyncio.sleep(0)
    ws = patch_event_ws.instances[0]
    ws.frames = [
        ("KP", {}, 1),
        ("FD", {}, 2),
    ]
    await ws.run_started.wait()
    ws.start_streaming.set()
    for _ in range(10):
        await asyncio.sleep(0)
    await hub.aclose()

    assert received_good == ["KP", "FD"], (
        f"bad subscriber の例外が good subscriber を止めてはいけない: {received_good}"
    )
    assert any("subscriber 'bad' raised" in rec.message for rec in caplog.records), (
        "例外は log.exception で記録されること"
    )


@pytest.mark.asyncio
async def test_unsubscribe_last_closes_ws(patch_event_ws):
    """Y3: 最後の subscriber を外すと WS の stop_event が立ち、runner_task が完了する。"""
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    hub = TickerEventWsHub("wss://example/", ticker="7203")

    async def _cb(_ft, _fields, _ts):
        pass

    await hub.subscribe("a", _cb)
    await hub.subscribe("b", _cb)
    await asyncio.sleep(0)
    ws = patch_event_ws.instances[0]
    await ws.run_started.wait()
    ws.start_streaming.set()

    # 1 つ外しただけでは WS は止まらない
    await hub.unsubscribe("a")
    await asyncio.sleep(0)
    assert not ws.stop_event.is_set(), "subscriber が残っているうちは stop_event を立てない"
    assert hub.subscriber_count == 1

    # 最後の 1 つを外すと stop_event が立ち、runner_task が完了する
    await hub.unsubscribe("b")
    await asyncio.wait_for(ws.run_finished.wait(), timeout=1.0)
    assert ws.stop_event.is_set()
    assert hub.subscriber_count == 0


@pytest.mark.asyncio
async def test_resubscribe_after_close_starts_new_ws(patch_event_ws):
    """Y3: 全 subscriber を外して WS が止まった後、再 subscribe で新 WS が起動する。"""
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    hub = TickerEventWsHub("wss://example/", ticker="7203")

    async def _cb(_ft, _fields, _ts):
        pass

    await hub.subscribe("a", _cb)
    await asyncio.sleep(0)
    await hub.unsubscribe("a")
    await asyncio.wait_for(patch_event_ws.instances[0].run_finished.wait(), timeout=1.0)

    # 再 subscribe → 新しい WS インスタンス
    await hub.subscribe("a", _cb)
    await asyncio.sleep(0)
    assert len(patch_event_ws.instances) == 2, (
        f"再 subscribe で新 WS が立ち上がるべき: {len(patch_event_ws.instances)}"
    )
    await hub.aclose()


@pytest.mark.asyncio
async def test_set_session_drops_all_hubs(patch_event_ws, tmp_path):
    """Y4: TachibanaWorker.set_session() で既存 hub を全部畳む。

    旧セッション URL に紐付いた hub を放置すると、新ログイン後も旧 URL に WS が
    張られ続けるリーク。session swap (None 経由 or 別 session) のたびに
    `_ticker_hubs` を clear し、各 hub に aclose を発火する。
    """
    from engine.exchanges.tachibana import TachibanaWorker
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)

    # hub を 2 本仕込む（直接生成、subscribe で WS タスク起動）
    hub_a = TickerEventWsHub("wss://example/a", ticker="7203")
    hub_b = TickerEventWsHub("wss://example/b", ticker="9984")

    async def _cb(_ft, _fields, _ts):
        pass

    await hub_a.subscribe("k", _cb)
    await hub_b.subscribe("k", _cb)
    await asyncio.sleep(0)

    worker._ticker_hubs = {"7203": hub_a, "9984": hub_b}

    # set_session(None) で全 hub が畳まれること
    worker.set_session(None)

    # aclose は fire-and-forget なので待つ
    for _ in range(50):
        await asyncio.sleep(0.01)
        if hub_a.subscriber_count == 0 and hub_b.subscriber_count == 0:
            break

    assert worker._ticker_hubs == {}, "set_session で _ticker_hubs はクリアされる"
    assert hub_a.subscriber_count == 0, "旧 hub_a の subscribers がクリアされる"
    assert hub_b.subscriber_count == 0, "旧 hub_b の subscribers がクリアされる"


@pytest.mark.asyncio
async def test_stream_depth_uses_hub_not_direct_ws(patch_event_ws, tmp_path, monkeypatch):
    """Y5: stream_depth が TachibanaEventWs を直接インスタンス化しないこと。

    hub 経由で 1 ticker = 1 WS インスタンスになることをカウントで保証する。
    """
    from engine.exchanges.tachibana import TachibanaWorker
    from engine.exchanges.tachibana_auth import TachibanaSession
    from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl

    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
    session = TachibanaSession(
        url_request=RequestUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/request/X/"),
        url_master=MasterUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/master/X/"),
        url_price=PriceUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/price/X/"),
        url_event=EventUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/event/X/"),
        url_event_ws="wss://demo-kabuka.e-shiten.jp/e_api_v4r8/event_ws/X/",
        zyoutoeki_kazei_c="",
        expires_at_ms=None,
    )
    worker.set_session(session)

    # market_open / sizyou_c / build_ws_url 周りを最低限スタブ
    monkeypatch.setattr(
        "engine.exchanges.tachibana_ws.is_market_open", lambda _now: True
    )
    monkeypatch.setattr(worker, "_lookup_sizyou_c", lambda _ticker: "00")

    outbox: list[dict] = []
    stop_event = asyncio.Event()

    # stream_depth を起動 → WS が立ち上がるまで待つ → 即停止
    task = asyncio.create_task(
        worker.stream_depth("7203", "stock", "ssid-test", outbox, stop_event)
    )
    # WS インスタンス化まで yield
    for _ in range(20):
        await asyncio.sleep(0)
        if patch_event_ws.instances:
            break

    assert len(patch_event_ws.instances) == 1, (
        f"stream_depth は hub 経由で 1 WS のみ立ち上げる: {len(patch_event_ws.instances)}"
    )
    assert "7203" in worker._ticker_hubs, "ticker_hubs に hub が登録されること"

    # cleanup
    stop_event.set()
    # FakeWs.run は stop_event を見て return する
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.TimeoutError:
        task.cancel()


@pytest.mark.asyncio
async def test_concurrent_depth_and_trades_single_connection(
    patch_event_ws, tmp_path, monkeypatch,
):
    """Y6: 同 ticker への depth + trades 並行起動でも WS インスタンスは 1 本のみ。

    これが Bug Y の本丸。立花 broker は (session, p_issue_code) 単位で 1 接続
    しか許さないため、depth と trades が独立 WS を張ると後発が p_errno=2
    'session inactive.' で蹴られる。hub マルチプレクサで 1 本に集約する。
    """
    from engine.exchanges.tachibana import TachibanaWorker
    from engine.exchanges.tachibana_auth import TachibanaSession
    from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl

    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
    session = TachibanaSession(
        url_request=RequestUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/request/X/"),
        url_master=MasterUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/master/X/"),
        url_price=PriceUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/price/X/"),
        url_event=EventUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/event/X/"),
        url_event_ws="wss://demo-kabuka.e-shiten.jp/e_api_v4r8/event_ws/X/",
        zyoutoeki_kazei_c="",
        expires_at_ms=None,
    )
    worker.set_session(session)
    monkeypatch.setattr(
        "engine.exchanges.tachibana_ws.is_market_open", lambda _now: True
    )
    monkeypatch.setattr(worker, "_lookup_sizyou_c", lambda _ticker: "00")

    outbox: list[dict] = []
    stop_event = asyncio.Event()

    depth_task = asyncio.create_task(
        worker.stream_depth("7203", "stock", "ssid-d", outbox, stop_event)
    )
    trades_task = asyncio.create_task(
        worker.stream_trades("7203", "stock", "ssid-t", outbox, stop_event)
    )
    # 両 stream が hub に subscribe するまで yield
    for _ in range(50):
        await asyncio.sleep(0.001)
        if depth_task.done() and depth_task.exception() is not None:
            raise AssertionError(f"stream_depth raised: {depth_task.exception()}")
        if trades_task.done() and trades_task.exception() is not None:
            raise AssertionError(f"stream_trades raised: {trades_task.exception()}")
        hub = worker._ticker_hubs.get("7203")
        if hub is not None and hub.subscriber_count >= 2:
            break

    assert len(patch_event_ws.instances) == 1, (
        f"depth+trades 並行で WS は 1 本のみ: instances={len(patch_event_ws.instances)}"
    )
    hub = worker._ticker_hubs["7203"]
    assert hub.subscriber_count == 2, (
        f"depth と trades 両方が subscribe されている: {hub.subscriber_count}"
    )

    # cleanup
    stop_event.set()
    for t in (depth_task, trades_task):
        try:
            await asyncio.wait_for(t, timeout=2.0)
        except asyncio.TimeoutError:
            t.cancel()


@pytest.mark.asyncio
async def test_aclose_invokes_subscribers_on_close_callback(patch_event_ws):
    """Y7 (review-fix 2026-05-04): hub.aclose() で subscriber の on_close が呼ばれる。

    レビュアー指摘: set_session(None) で hub は閉じるが stream_depth /
    stream_trades の `_inner_stop.wait()` は解放されず stream coroutine が
    生き残る。hub close を購読側に伝える on_close フックが必要。
    """
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    hub = TickerEventWsHub("wss://example/", ticker="7203")
    closed_a = asyncio.Event()
    closed_b = asyncio.Event()

    async def _cb(_ft, _fields, _ts):
        pass

    await hub.subscribe("a", _cb, on_close=closed_a.set)
    await hub.subscribe("b", _cb, on_close=closed_b.set)

    await hub.aclose()
    # on_close は aclose 内で呼ばれているので即 set されているはず
    assert closed_a.is_set(), "subscriber 'a' の on_close が呼ばれていない"
    assert closed_b.is_set(), "subscriber 'b' の on_close が呼ばれていない"


@pytest.mark.asyncio
async def test_unsubscribe_does_not_invoke_on_close(patch_event_ws):
    """Y7: 自発的 unsubscribe では on_close を呼ばない (force-close との区別)。

    on_close は「外部から強制的に hub が閉じられた」シグナル。subscriber が
    自分で unsubscribe する場合は呼ばない (二重発火防止)。
    """
    from engine.exchanges.tachibana_ws import TickerEventWsHub

    hub = TickerEventWsHub("wss://example/", ticker="7203")
    closed = asyncio.Event()

    async def _cb(_ft, _fields, _ts):
        pass

    await hub.subscribe("a", _cb, on_close=closed.set)
    await hub.unsubscribe("a")
    await asyncio.sleep(0)
    assert not closed.is_set(), "自発的 unsubscribe では on_close を呼んではいけない"


@pytest.mark.asyncio
async def test_set_session_none_terminates_stream_coroutines(
    patch_event_ws, tmp_path, monkeypatch,
):
    """Y7 本丸: set_session(None) 後に stream_depth / stream_trades coroutine が終了する。

    レビュアー指摘の本体検証。session swap で hub close → on_close →
    各 stream の _inner_stop.set() → _inner_stop.wait() 解放 → coroutine 終了。
    server 側 `_streams` 管理は task 完了で自動 cleanup される。
    """
    from engine.exchanges.tachibana import TachibanaWorker
    from engine.exchanges.tachibana_auth import TachibanaSession
    from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl

    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
    session = TachibanaSession(
        url_request=RequestUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/request/X/"),
        url_master=MasterUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/master/X/"),
        url_price=PriceUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/price/X/"),
        url_event=EventUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/event/X/"),
        url_event_ws="wss://demo-kabuka.e-shiten.jp/e_api_v4r8/event_ws/X/",
        zyoutoeki_kazei_c="",
        expires_at_ms=None,
    )
    worker.set_session(session)
    monkeypatch.setattr(
        "engine.exchanges.tachibana_ws.is_market_open", lambda _now: True
    )
    monkeypatch.setattr(worker, "_lookup_sizyou_c", lambda _ticker: "00")

    outbox: list[dict] = []
    stop_event = asyncio.Event()
    depth_task = asyncio.create_task(
        worker.stream_depth("7203", "stock", "ssid-d", outbox, stop_event)
    )
    trades_task = asyncio.create_task(
        worker.stream_trades("7203", "stock", "ssid-t", outbox, stop_event)
    )

    # 両 stream が hub に subscribe するまで待つ
    for _ in range(50):
        await asyncio.sleep(0.001)
        if depth_task.done() and depth_task.exception() is not None:
            raise AssertionError(f"stream_depth raised: {depth_task.exception()}")
        if trades_task.done() and trades_task.exception() is not None:
            raise AssertionError(f"stream_trades raised: {trades_task.exception()}")
        hub = worker._ticker_hubs.get("7203")
        if hub is not None and hub.subscriber_count >= 2:
            break

    # session swap (再ログイン直前の挙動)
    worker.set_session(None)

    # stream coroutine が 2 秒以内に終了すること
    try:
        await asyncio.wait_for(
            asyncio.gather(depth_task, trades_task), timeout=2.0
        )
    except asyncio.TimeoutError:
        depth_task.cancel()
        trades_task.cancel()
        raise AssertionError(
            f"set_session(None) 後も stream coroutine が終了しない: "
            f"depth.done={depth_task.done()} trades.done={trades_task.done()}"
        )

    assert depth_task.done() and not depth_task.cancelled()
    assert trades_task.done() and not trades_task.cancelled()
    assert worker._ticker_hubs == {}, "set_session(None) で _ticker_hubs はクリアされる"
