"""H-TA1: `RequestVenueLogin` during CONNECTING returns VenueLoginStarted.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


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

    Bug X (docs/specs/venues/tachibana/fix-event-ws-lifecycle-2026-05-04.md):
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


@pytest.mark.demo_kabu
def test_startup_kabu_station_cancel_emits_venue_login_cancelled(monkeypatch):
    """HIGH-2: _startup_kabu_station が KabuLoginCancelledError を受けると
    VenueLoginCancelled を emit して DISCONNECTED に戻る。
    """
    from engine.server import DataEngineServer, LiveState
    from engine.exchanges.kabusapi_auth import KabuLoginCancelledError

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTING
    srv._kabu_env = "verify"  # H-2: インスタンスキャッシュ
    srv._kabu_venue = None
    srv._dev_kabu_login_allowed = False
    srv._dev_kabu_trade_password_allowed = False
    srv._kabu_login_inflight = asyncio.Lock()

    emitted: list[dict] = []

    class _FakeOutbox:
        def append(self, item):
            emitted.append(item)
        def count(self):
            return 1

    srv._outbox = _FakeOutbox()

    # KabuStationVenue.startup_login が KabuLoginCancelledError を raise するようにモック
    class _FakeKabuVenue:
        async def startup_login(self):
            raise KabuLoginCancelledError(0, "Login cancelled by user")
        def clear(self):
            pass

    monkeypatch.setattr(
        "engine.server.KabuStationVenue",
        lambda **_kwargs: _FakeKabuVenue(),
    )

    async def _run():
        await srv._startup_kabu_station(request_id="req-cancel-1")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    events = [e.get("event") for e in emitted]
    assert "VenueLoginCancelled" in events, (
        f"VenueLoginCancelled が emit されていない; got: {emitted}"
    )
    assert "VenueError" not in events, (
        f"VenueError が emit されるべきでない (cancel なので); got: {emitted}"
    )
    assert srv._live_state == LiveState.DISCONNECTED, (
        f"DISCONNECTED に戻るべき; got: {srv._live_state}"
    )
    cancel_evt = next(e for e in emitted if e.get("event") == "VenueLoginCancelled")
    assert cancel_evt.get("venue") == "kabu_station"
    assert cancel_evt.get("request_id") == "req-cancel-1"


@pytest.mark.demo_kabu
def test_startup_kabu_station_uses_resolve_kabu_env_for_env(monkeypatch):
    """P4-2: _startup_kabu_station が resolve_kabu_env() の戻り値を
    KabuStationVenue 構築の env パラメータに使う。env=verify、dev_login_allowed=True 経路。
    """
    from engine.server import DataEngineServer, LiveState
    from engine.exchanges.kabusapi_auth import KabuLoginCancelledError

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTING
    srv._kabu_env = "verify"  # H-2: インスタンスキャッシュ済みの env を直接設定
    srv._kabu_venue = None
    srv._dev_kabu_login_allowed = True
    srv._dev_kabu_trade_password_allowed = False
    srv._kabu_login_inflight = asyncio.Lock()

    class _FakeOutbox:
        def __init__(self):
            self.items: list[dict] = []
        def append(self, item):
            self.items.append(item)
        def count(self):
            return 1

    srv._outbox = _FakeOutbox()

    captured: dict = {}

    class _FakeKabuVenue:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def startup_login(self):
            raise KabuLoginCancelledError(0, "skip")
        def clear(self):
            pass

    monkeypatch.setattr("engine.server.KabuStationVenue", _FakeKabuVenue)

    async def _run():
        await srv._startup_kabu_station(request_id="req-env-1")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    assert captured.get("env") == "verify"
    assert captured.get("dev_login_allowed") is True


@pytest.mark.demo_kabu
def test_startup_kabu_station_disables_dev_login_in_prod(monkeypatch):
    """P4-2: prod env 解決時は dev_login_allowed が強制 False。
    self._dev_kabu_login_allowed=True でも prod では release ガードが効く。
    """
    from engine.server import DataEngineServer, LiveState
    from engine.exchanges.kabusapi_auth import KabuLoginCancelledError

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTING
    srv._kabu_env = "prod"  # H-2: インスタンスキャッシュ済みの env を直接設定
    srv._kabu_venue = None
    srv._dev_kabu_login_allowed = True  # debug ビルドのつもり
    srv._dev_kabu_trade_password_allowed = True
    srv._kabu_login_inflight = asyncio.Lock()

    class _FakeOutbox:
        def __init__(self):
            self.items: list[dict] = []
        def append(self, item):
            self.items.append(item)
        def count(self):
            return 1

    srv._outbox = _FakeOutbox()

    captured: dict = {}

    class _FakeKabuVenue:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def startup_login(self):
            raise KabuLoginCancelledError(0, "skip")
        def clear(self):
            pass

    monkeypatch.setattr("engine.server.KabuStationVenue", _FakeKabuVenue)

    async def _run():
        await srv._startup_kabu_station(request_id="req-env-2")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    assert captured.get("env") == "prod"
    assert captured.get("dev_login_allowed") is False, (
        "prod env では dev_login_allowed は強制 False になるべき"
    )
    # 取引パスワード dev も同様にゼロ化（誤発注リスク低減）
    assert captured.get("dev_trade_password_allowed") is False


@pytest.mark.skip(reason="WS transport removed in G3 — _handshake() deleted; migrate to test _build_ready_event() in server_grpc.py")
@pytest.mark.demo_kabu
def test_kabu_ready_capabilities_include_kabu_station(monkeypatch):
    """HIGH-1: _handshake が送る Ready.capabilities.venue_capabilities["kabu_station"] が存在する。"""
    import asyncio
    import orjson
    from engine.server import DataEngineServer, SCHEMA_MAJOR, SCHEMA_MINOR
    from engine.schemas import Hello

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._kabu_env = "verify"  # H-2: インスタンスキャッシュ
    srv._workers = {}  # kabu_station は _workers に含まれない
    srv._engine_session_id = "00000000-0000-0000-0000-000000000000"
    srv._connections = set()  # handshake 内で参照される
    srv._token = "test-token"  # hmac 比較で使われる

    sent: list[dict] = []
    hello = Hello(
        token="test-token",
        mode="live",
        schema_major=SCHEMA_MAJOR,
        schema_minor=SCHEMA_MINOR,
        client_version="test",
    )

    class _FakeWs:
        async def recv(self):
            return orjson.dumps(hello.model_dump(mode="json")).decode()

        async def send(self, data):
            sent.append(orjson.loads(data))

        async def close(self, *args, **kwargs):
            pass

    monkeypatch.setattr("engine.server.nautilus_capabilities", lambda _mode: {})

    async def _run():
        await srv._handshake(_FakeWs())

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    assert len(sent) >= 1, f"Ready が送信されていない; got: {sent}"
    ready_msg = next((m for m in sent if m.get("event") == "Ready"), None)
    assert ready_msg is not None, f"Ready event がない; got: {sent}"
    caps = ready_msg.get("capabilities", {})
    venue_caps = caps.get("venue_capabilities", {})
    assert "kabu_station" in venue_caps, (
        f"venue_capabilities に kabu_station がない; got: {venue_caps}"
    )
    kabu_cap = venue_caps["kabu_station"]
    assert kabu_cap.get("requires_local_app") is True
    assert kabu_cap.get("max_push_symbols") == 50
    assert kabu_cap.get("supports_amend") is False
    assert "kabu_station" in caps.get("supported_venues", []), (
        f"supported_venues に kabu_station がない; got: {caps.get('supported_venues')}"
    )
    # P4-3: is_production フラグの存在確認（デフォルト = False, verify env）
    assert "is_production" in kabu_cap, (
        f"kabu_station capabilities に is_production がない; got: {kabu_cap}"
    )
    assert kabu_cap["is_production"] is False


@pytest.mark.skip(reason="WS transport removed in G3 — _handshake() deleted; migrate to test _build_ready_event() in server_grpc.py")
@pytest.mark.demo_kabu
def test_kabu_ready_capabilities_is_production_true_in_prod_env(monkeypatch):
    """P4-3: KABU_ALLOW_PROD=1 + KABU_ENV=prod のとき capabilities.is_production=True。"""
    import asyncio
    import orjson
    from engine.server import DataEngineServer, SCHEMA_MAJOR, SCHEMA_MINOR
    from engine.schemas import Hello

    monkeypatch.setenv("KABU_ALLOW_PROD", "1")
    monkeypatch.setenv("KABU_ENV", "prod")

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._kabu_env = "prod"  # H-2: インスタンスキャッシュ（prod 直接設定）
    srv._workers = {}
    srv._engine_session_id = "00000000-0000-0000-0000-000000000000"
    srv._connections = set()
    srv._token = "test-token"

    sent: list[dict] = []
    hello = Hello(
        token="test-token",
        mode="live",
        schema_major=SCHEMA_MAJOR,
        schema_minor=SCHEMA_MINOR,
        client_version="test",
    )

    class _FakeWs:
        async def recv(self):
            return orjson.dumps(hello.model_dump(mode="json")).decode()

        async def send(self, data):
            sent.append(orjson.loads(data))

        async def close(self, *args, **kwargs):
            pass

    monkeypatch.setattr("engine.server.nautilus_capabilities", lambda _mode: {})

    async def _run():
        await srv._handshake(_FakeWs())

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    ready_msg = next((m for m in sent if m.get("event") == "Ready"), None)
    assert ready_msg is not None
    venue_caps = ready_msg["capabilities"]["venue_capabilities"]
    assert venue_caps["kabu_station"]["is_production"] is True


@pytest.mark.demo_kabu
def test_schema_minor_current_value():
    """SCHEMA_MINOR の現在値を固定するスモークテスト。
    変更時はここも更新すること（履歴は engine-client/src/lib.rs を参照）。
    24: RequestVenueLogout コマンド追加（立花セッション明示破棄 IPC）。
    """
    from engine.schemas import SCHEMA_MINOR

    assert SCHEMA_MINOR == 24


@pytest.mark.demo_kabu
def test_kabu_relogin_from_tachibana_connected_cancels_event_task(monkeypatch, tmp_path):
    """MEDIUM-3: tachibana CONNECTED 中に kabu RequestVenueLogin が来ると
    tachibana event_task が cancel される。
    """
    from engine.server import DataEngineServer, LiveState

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTED
    srv._connected_venue = "tachibana"
    srv._cache_dir = tmp_path
    srv._tachibana_session = object()
    srv._kabu_venue = None
    srv._kabu_login_inflight = asyncio.Lock()
    srv._kabu_startup_task = None

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

    # _startup_kabu_station を no-op にする
    spawned: list = []
    async def _fake_kabu_startup(request_id=None):
        spawned.append(request_id)
    monkeypatch.setattr(srv, "_startup_kabu_station", _fake_kabu_startup)
    monkeypatch.setattr(
        "engine.server.tachibana_clear_session", lambda _cache_dir: None
    )

    cancelled = asyncio.Event()

    async def _old_event_loop():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _run():
        srv._event_task = asyncio.create_task(_old_event_loop())
        await asyncio.sleep(0)  # タスクを起動

        msg = {
            "op": "RequestVenueLogin",
            "request_id": "kabu-req-1",
            "venue": "kabu_station",
        }
        await srv._do_request_venue_login(msg)
        # cancel が伝播するまで待つ
        try:
            await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    assert cancelled.is_set(), (
        "tachibana _event_task が cancel されていない"
    )
    assert srv._tachibana_session is None, "tachibana_session がクリアされていない"
    assert fake_worker.cleared, "worker の session が None にリセットされていない"


@pytest.mark.demo_kabu
def test_kabu_env_cached_in_init(monkeypatch):
    """H-2: DataEngineServer.__init__() が resolve_kabu_env() をキャッシュし、
    _kabu_env 属性として保持する。env が変わっても _build_ready() と
    _startup_kabu_station() が同じ値を参照する。
    """
    import os
    from engine.server import DataEngineServer

    monkeypatch.setenv("KABU_ENV", "verify")
    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)

    srv = DataEngineServer(port=19876, token="test-token")

    assert hasattr(srv, "_kabu_env"), "_kabu_env 属性が DataEngineServer に存在しない"
    assert srv._kabu_env == "verify", (
        f"_kabu_env は 'verify' であるべき; got: {srv._kabu_env!r}"
    )
