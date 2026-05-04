"""H-TA1: `RequestVenueLogin` during CONNECTING returns VenueLoginStarted.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

import asyncio


def test_request_venue_login_connecting_returns_venue_login_started():
    """H-TA1: CONNECTING 中に RequestVenueLogin を送ると EngineBusy でなく
    VenueLoginStarted が返ること。
    """
    from engine.server import DataEngineServer, LiveState

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTING

    # _outbox をモックに置き換える
    emitted: list[dict] = []

    class _FakeOutbox:
        def append(self, item):
            emitted.append(item)

        def count(self):
            return 1

    srv._outbox = _FakeOutbox()
    srv._tachibana_login_inflight = asyncio.Lock()

    async def _run():
        msg = {
            "op": "RequestVenueLogin",
            "request_id": "test-req-1",
            "venue": "tachibana",
        }
        await srv._do_request_venue_login(msg)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    # EngineBusy でなく VenueLoginStarted が返ること
    events = [e.get("event") for e in emitted]
    assert "EngineBusy" not in events, f"EngineBusy should not be returned for CONNECTING; got: {emitted}"
    assert "VenueLoginStarted" in events, f"VenueLoginStarted should be returned; got: {emitted}"


def test_request_venue_login_connected_triggers_relogin(monkeypatch, tmp_path):
    """既ログイン済 (CONNECTED) で RequestVenueLogin を送ると、
    EngineBusy ではなく再ログインが開始されること。

    Rust 側 FSM (`venue_state.rs::try_claim_login_in_flight_succeeds_from_ready`)
    は Ready からの再ログインを許可しているため、Python 側も CONNECTED を
    「ユーザーが明示的に再ログインを要求した」とみなし、セッションをクリア
    してから CONNECTING へ遷移する。
    """
    from engine.server import DataEngineServer, LiveState

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTED
    srv._cache_dir = tmp_path
    srv._tachibana_session = object()  # 既存セッションが存在する状態を再現

    class _FakeWorker:
        def __init__(self):
            self.cleared = False

        def set_session(self, session):
            if session is None:
                self.cleared = True

    fake_worker = _FakeWorker()
    srv._workers = {"tachibana": fake_worker}

    emitted: list[dict] = []

    class _FakeOutbox:
        def append(self, item):
            emitted.append(item)

        def send_to(self, _ws, item):
            emitted.append(item)

        def count(self):
            return 1

    srv._outbox = _FakeOutbox()
    srv._tachibana_login_inflight = asyncio.Lock()
    srv._tachibana_startup_task = None
    srv._event_task = None

    # _startup_tachibana を no-op にして、本体のログインフローを実行しない
    spawned: list[str | None] = []

    async def _fake_startup(request_id=None):
        spawned.append(request_id)

    monkeypatch.setattr(srv, "_startup_tachibana", _fake_startup)

    # tachibana_clear_session の副作用 (ファイル削除) を避ける
    monkeypatch.setattr(
        "engine.server.tachibana_clear_session", lambda _cache_dir: None
    )

    async def _run():
        msg = {
            "op": "RequestVenueLogin",
            "request_id": "test-relogin-1",
            "venue": "tachibana",
        }
        await srv._do_request_venue_login(msg)
        # spawn したタスクの完了を待つ
        if srv._tachibana_startup_task is not None:
            await srv._tachibana_startup_task

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    events = [e.get("event") for e in emitted]
    assert "EngineBusy" not in events, (
        f"CONNECTED からの再ログイン要求は EngineBusy を返してはならない; got: {emitted}"
    )
    assert srv._tachibana_session is None, "既存セッションをクリアすること"
    assert fake_worker.cleared, "worker の session も None にリセットすること"
    assert srv._live_state == LiveState.CONNECTING, (
        f"CONNECTING に遷移すること; got: {srv._live_state}"
    )
    assert spawned == ["test-relogin-1"], (
        f"_startup_tachibana が request_id 付きで起動されること; got: {spawned}"
    )


def test_relogin_from_connected_cancels_old_event_task(monkeypatch, tmp_path):
    """CONNECTED 中の RequestVenueLogin で旧 `_event_task` が cancel されること。

    Bug X (docs/✅tachibana/fix-event-ws-lifecycle-2026-05-04.md):
    `_live_state` を CONNECTED → DISCONNECTED に巻き戻すだけでは旧 EVENT WS
    受信ループが残り、新ログインが失敗・キャンセルされた場合に旧セッション URL
    から EC 約定通知を受け続けるゴースト状態になる。
    """
    from engine.server import DataEngineServer, LiveState

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTED
    srv._cache_dir = tmp_path
    srv._tachibana_session = object()

    class _FakeWorker:
        def set_session(self, session):
            pass

    srv._workers = {"tachibana": _FakeWorker()}

    class _FakeOutbox:
        def append(self, _item): pass
        def send_to(self, _ws, _item): pass
        def count(self): return 1

    srv._outbox = _FakeOutbox()
    srv._tachibana_login_inflight = asyncio.Lock()
    srv._tachibana_startup_task = None

    async def _fake_startup(request_id=None):  # noqa: ARG001
        return None

    monkeypatch.setattr(srv, "_startup_tachibana", _fake_startup)
    monkeypatch.setattr(
        "engine.server.tachibana_clear_session", lambda _cache_dir: None
    )

    # 「旧 EVENT ループ」を模した長寿命タスクを仕込む
    cancelled = asyncio.Event()

    async def _old_event_loop():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _run():
        srv._event_task = asyncio.create_task(_old_event_loop())
        # ループに制御を渡してタスクを起動させる
        await asyncio.sleep(0)
        old_task = srv._event_task

        msg = {
            "op": "RequestVenueLogin",
            "request_id": "relogin-cancel-old",
            "venue": "tachibana",
        }
        await srv._do_request_venue_login(msg)
        # cancel が伝播するまで待つ
        try:
            await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        return old_task

    loop = asyncio.new_event_loop()
    try:
        old_task = loop.run_until_complete(_run())
    finally:
        loop.close()

    assert cancelled.is_set(), "旧 _event_task に CancelledError が伝播していない"
    assert old_task.cancelled(), (
        f"旧 _event_task が cancel 状態になっていない: done={old_task.done()}"
    )
