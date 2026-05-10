"""gRPC IPC server — grpcio.aio ベース。DataEngineServer のビジネスロジックを再利用する。"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from pathlib import Path
from typing import AsyncIterator

import grpc
from grpc import aio

from engine.proto import engine_pb2, engine_pb2_grpc
from engine.server import _ENGINE_VERSION

# gRPC IPC schema version — source of truth for the gRPC transport layer.
# Keep in sync with engine-client/src/lib.rs SCHEMA_MAJOR / SCHEMA_MINOR.
SCHEMA_MAJOR: int = 3   # gRPC IPC schema major version
SCHEMA_MINOR: int = 28  # gRPC IPC schema minor version

log = logging.getLogger(__name__)

_HANDSHAKE_TIMEOUT_S = 15.0
MAX_CONNECTIONS = 4

_FIELD_TO_OP = {
    "set_proxy": "SetProxy",
    "subscribe": "Subscribe",
    "unsubscribe": "Unsubscribe",
    "fetch_klines": "FetchKlines",
    "fetch_trades": "FetchTrades",
    "fetch_open_interest": "FetchOpenInterest",
    "fetch_ticker_stats": "FetchTickerStats",
    "list_tickers": "ListTickers",
    "get_ticker_metadata": "GetTickerMetadata",
    "request_depth_snapshot": "RequestDepthSnapshot",
    "ping": "Ping",
    "shutdown": "Shutdown",
    "request_venue_login": "RequestVenueLogin",
    "set_second_password": "SetSecondPassword",
    "forget_second_password": "ForgetSecondPassword",
    "submit_order": "SubmitOrder",
    "modify_order": "ModifyOrder",
    "cancel_order": "CancelOrder",
    "cancel_all_orders": "CancelAllOrders",
    "get_order_list": "GetOrderList",
    "get_buying_power": "GetBuyingPower",
    "get_positions": "GetPositions",
    "start_engine": "StartEngine",
    "stop_engine": "StopEngine",
    "load_replay_data": "LoadReplayData",
    "set_replay_speed": "SetReplaySpeed",
    "stop_replay": "StopReplay",
    "force_stop_replay": "ForceStopReplay",
    "pause_replay": "PauseReplay",
    "resume_replay": "ResumeReplay",
    "step_replay": "StepReplay",
    "step_backward": "StepBackward",
    "load_strategy_scenario": "LoadStrategyScenario",
    "save_strategy_scenario": "SaveStrategyScenario",
    "request_venue_logout": "RequestVenueLogout",
    "load_live_strategy_scenario": "LoadLiveStrategyScenario",
}

_EVENT_TO_FIELD_AND_CLASS = {
    # NOTE: "Ready" is intentionally absent.  ReadyResponse must be sent only
    # once during the handshake via _build_ready_event() — never via the outbox.
    "EngineError":                ("engine_error",                engine_pb2.EngineErrorEvent),
    "Connected":                  ("connected",                   engine_pb2.ConnectedEvent),
    "Disconnected":               ("disconnected",                engine_pb2.DisconnectedEvent),
    "Trades":                     ("trades",                      engine_pb2.TradesEvent),
    "TradesFetched":              ("trades_fetched",              engine_pb2.TradesFetchedEvent),
    "KlineUpdate":                ("kline_update",                engine_pb2.KlineUpdateEvent),
    "Klines":                     ("klines",                      engine_pb2.KlinesEvent),
    "DepthSnapshot":              ("depth_snapshot",              engine_pb2.DepthSnapshotEvent),
    "DepthDiff":                  ("depth_diff",                  engine_pb2.DepthDiffEvent),
    "DepthGap":                   ("depth_gap",                   engine_pb2.DepthGapEvent),
    "OpenInterest":               ("open_interest",               engine_pb2.OpenInterestEvent),
    "TickerInfo":                 ("ticker_info",                 engine_pb2.TickerInfoEvent),
    "TickerStats":                ("ticker_stats",                engine_pb2.TickerStatsEvent),
    "Pong":                       ("pong",                        engine_pb2.PongEvent),
    "Error":                      ("error",                       engine_pb2.ErrorEvent),
    "VenueReady":                 ("venue_ready",                 engine_pb2.VenueReadyEvent),
    "VenueError":                 ("venue_error",                 engine_pb2.VenueErrorEvent),
    "VenueLoginStarted":          ("venue_login_started",         engine_pb2.VenueLoginStartedEvent),
    "VenueLoginCancelled":        ("venue_login_cancelled",       engine_pb2.VenueLoginCancelledEvent),
    "SecondPasswordRequired":     ("second_password_required",    engine_pb2.SecondPasswordRequiredEvent),
    "OrderSubmitted":             ("order_submitted",             engine_pb2.OrderSubmittedEvent),
    "OrderAccepted":              ("order_accepted",              engine_pb2.OrderAcceptedEvent),
    "OrderRejected":              ("order_rejected",              engine_pb2.OrderRejectedEvent),
    "OrderPendingUpdate":         ("order_pending_update",        engine_pb2.OrderPendingUpdateEvent),
    "OrderPendingCancel":         ("order_pending_cancel",        engine_pb2.OrderPendingCancelEvent),
    "OrderFilled":                ("order_filled",                engine_pb2.OrderFilledEvent),
    "OrderCanceled":              ("order_canceled",              engine_pb2.OrderCanceledEvent),
    "OrderExpired":               ("order_expired",               engine_pb2.OrderExpiredEvent),
    "OrderListUpdated":           ("order_list_updated",          engine_pb2.OrderListUpdatedEvent),
    "EngineStarted":              ("engine_started",              engine_pb2.EngineStartedEvent),
    "EngineStopped":              ("engine_stopped",              engine_pb2.EngineStoppedEvent),
    "ReplayDataLoaded":           ("replay_data_loaded",          engine_pb2.ReplayDataLoadedEvent),
    "PositionOpened":             ("position_opened",             engine_pb2.PositionOpenedEvent),
    "PositionClosed":             ("position_closed",             engine_pb2.PositionClosedEvent),
    "ExecutionMarker":            ("execution_marker",            engine_pb2.ExecutionMarkerEvent),
    "StrategySignal":             ("strategy_signal",             engine_pb2.StrategySignalEvent),
    "BuyingPowerUpdated":         ("buying_power_updated",        engine_pb2.BuyingPowerUpdatedEvent),
    "PositionsUpdated":           ("positions_updated",           engine_pb2.PositionsUpdatedEvent),
    "ReplayBuyingPower":          ("replay_buying_power",         engine_pb2.ReplayBuyingPowerEvent),
    "DateChangeMarker":           ("date_change_marker",          engine_pb2.DateChangeMarkerEvent),
    "RestoreSnapshot":            ("restore_snapshot",            engine_pb2.RestoreSnapshotEvent),
    "ReplayHistoryChanged":       ("replay_history_changed",      engine_pb2.ReplayHistoryChangedEvent),
    "ReplayStopped":              ("replay_stopped",              engine_pb2.ReplayStoppedEvent),
    "EngineBusy":                 ("engine_busy",                 engine_pb2.EngineBusyEvent),
    "ClientConnected":            ("client_connected",            engine_pb2.ClientConnectedEvent),
    "ClientDisconnected":         ("client_disconnected",         engine_pb2.ClientDisconnectedEvent),
    "StrategyScenarioLoaded":     ("strategy_scenario_loaded",    engine_pb2.StrategyScenarioLoadedEvent),
    "StrategyScenarioLoadFailed": ("strategy_scenario_load_failed", engine_pb2.StrategyScenarioLoadFailedEvent),
    "StrategyScenarioSaved":      ("strategy_scenario_saved",     engine_pb2.StrategyScenarioSavedEvent),
    "LiveBuyingPower":            ("live_buying_power",           engine_pb2.LiveBuyingPowerEvent),
    "ReplayTimeUpdated":          ("replay_time_updated",         engine_pb2.ReplayTimeUpdatedEvent),
    "LiveStrategyScenarioLoaded": ("live_strategy_scenario_loaded", engine_pb2.LiveStrategyScenarioLoadedEvent),
    "LiveStrategyReady":          ("live_strategy_ready",          engine_pb2.LiveStrategyReadyEvent),
    "LiveStrategyWarmingUp":      ("live_strategy_warming_up",     engine_pb2.LiveStrategyWarmingUpEvent),
}


class _GrpcSessionKey:
    """gRPC session unique key using object identity (id-based hash). Intentional sentinel.

    _Broadcaster の dict key として機能する。各 gRPC Session RPC 呼び出しごとに
    新しいインスタンスを生成することで、Python のデフォルト id ベースの同一性比較
    (__eq__ / __hash__ をオーバーライドしない) により自然な一意キーとなる。
    """

    __slots__ = ()


def _log_startup_tachibana_error(t: asyncio.Task) -> None:
    """_startup_tachibana タスクの done callback — 例外をログする。"""
    if t.cancelled():
        return
    exc = t.exception()
    if exc is not None:
        log.error("_startup_tachibana failed: %s", exc, exc_info=exc)


def _proto_mode_to_str(mode_int: int) -> str:
    """proto AppMode enum value を文字列に変換する。

    未知の値は ValueError を raise する。呼び出し元でこれを捕捉して
    FAILED_PRECONDITION で接続を拒否すること。
    """
    if mode_int == engine_pb2.APP_MODE_REPLAY:
        return "replay"
    if mode_int == engine_pb2.APP_MODE_LIVE:
        return "live"
    raise ValueError(f"Unknown AppMode value: {mode_int}")


def _build_ready_event(server) -> engine_pb2.Event:
    """DataEngineServer の状態から ReadyResponse Event を構築する。"""
    engine_version = _ENGINE_VERSION
    return engine_pb2.Event(
        ready=engine_pb2.ReadyResponse(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            engine_version=engine_version,
            engine_session_id=str(server._engine_session_id),
            capabilities=engine_pb2.EngineCapabilities(
                supported_venues=list(server._workers.keys()) + ["kabu_station"],
                supports_bulk_trades=True,
                supports_depth_binary=False,
            ),
        )
    )


def _cmd_payload_to_dict(cmd, which: str) -> dict:
    """Command の payload フィールドを dict に変換する。

    M1 注記: `always_print_fields_with_no_presence=False` により proto の
    optional フィールドが未設定の場合は dict に含まれない（キー自体が欠落する）。
    呼び出し側では `msg[key]` ではなく `msg.get(key)` または `msg.get(key, default)`
    を使うこと。`server.py` の dispatch ハンドラがこれを尊重していることを確認済み。
    """
    from google.protobuf.json_format import MessageToDict
    payload_msg = getattr(cmd, which)
    d = MessageToDict(
        payload_msg,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=False,
    )

    def _unfix_enum(obj: dict):
        for k, v in list(obj.items()):
            if k == "granularity" and isinstance(v, str) and v.startswith("REPLAY_GRANULARITY_"):
                obj[k] = v[len("REPLAY_GRANULARITY_"):].capitalize()
            elif k == "engine" and isinstance(v, str) and v.startswith("ENGINE_KIND_"):
                obj[k] = v[len("ENGINE_KIND_"):].capitalize()
            elif isinstance(v, dict):
                _unfix_enum(v)
    _unfix_enum(d)

    return d


def _dict_to_proto_event(event_dict: dict) -> engine_pb2.Event | None:
    """dict を proto Event に変換する。不明な event_name は None を返す。"""
    from google.protobuf.json_format import ParseDict

    event_name = event_dict.get("event")
    if event_name == "Ready":
        log.error(
            "Unexpected 'Ready' in outbox — ReadyResponse must be sent only once during handshake"
        )
        return None
    mapping = _EVENT_TO_FIELD_AND_CLASS.get(event_name)
    if mapping is None:
        log.warning("Unknown event name: %s — dropping", event_name)
        return None

    field_name, msg_class = mapping
    payload_dict = {k: v for k, v in event_dict.items() if k != "event"}

    def _fix_enum_out(obj: dict):
        for k, v in list(obj.items()):
            if k == "granularity" and isinstance(v, str) and not v.startswith("REPLAY_GRANULARITY_"):
                obj[k] = f"REPLAY_GRANULARITY_{v.upper()}"
            elif k == "qty_norm_kind" and isinstance(v, str) and not v.startswith("QTY_NORM_KIND_"):
                obj[k] = f"QTY_NORM_KIND_{v.upper()}"
            elif isinstance(v, dict):
                _fix_enum_out(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        _fix_enum_out(item)
    _fix_enum_out(payload_dict)

    # TickerInfoEvent: Python list_tickers() returns flat dicts with
    # "kind": "stock"|"crypto", but proto TickerEntry uses
    # `oneof kind { StockTickerEntry stock; CryptoTickerEntry crypto; }`.
    # Restructure each entry so ParseDict sees {"stock": {...}} or
    # {"crypto": {...}} with the "kind" key removed.
    if event_name == "TickerInfo" and "tickers" in payload_dict:
        restructured: list[dict] = []
        for entry in payload_dict["tickers"]:
            kind = entry.get("kind")
            if kind in ("stock", "crypto"):
                inner = {k: v for k, v in entry.items() if k != "kind"}
                restructured.append({kind: inner})
            else:
                restructured.append(entry)
        payload_dict = dict(payload_dict, tickers=restructured)

    # Issue #43: proto scenario フィールドは optional string (JSON-encoded dict)。
    # server.py の outbox には raw dict が入るため、ParseDict に渡す前に json.dumps する。
    if event_name == "StrategyScenarioLoaded" and isinstance(payload_dict.get("scenario"), dict):
        payload_dict = dict(payload_dict, scenario=json.dumps(payload_dict["scenario"]))

    # issue #42 Phase 2: LiveStrategyScenarioLoaded.strategy_init_kwargs is wire-form
    # JSON string. Outbox payload has raw dict, so json.dumps it before ParseDict.
    if event_name == "LiveStrategyScenarioLoaded" and isinstance(
        payload_dict.get("strategy_init_kwargs"), dict
    ):
        payload_dict = dict(
            payload_dict, strategy_init_kwargs=json.dumps(payload_dict["strategy_init_kwargs"])
        )

    try:
        payload = ParseDict(payload_dict, msg_class(), ignore_unknown_fields=False)
        return engine_pb2.Event(**{field_name: payload})
    except Exception as exc:
        log.warning("Failed to build proto Event %s: %s — dropping: payload=%r", event_name, exc, payload_dict)
        return None


class _GrpcDataEngineServicer(engine_pb2_grpc.DataEngineServicer):
    """gRPC DataEngine.Session RPC の実装。"""

    def __init__(self, server) -> None:
        self._server = server
        self._handshake_lock = asyncio.Lock()

    async def Session(
        self,
        request_iterator: AsyncIterator[engine_pb2.Command],
        context: aio.ServicerContext,
    ) -> AsyncIterator[engine_pb2.Event]:
        # 2. 最初の Command を待つ（15秒タイムアウト）
        try:
            first_cmd = await asyncio.wait_for(
                request_iterator.__anext__(), timeout=_HANDSHAKE_TIMEOUT_S
            )
        except StopAsyncIteration:
            log.debug("gRPC session: client closed stream before sending HelloRequest")
            return
        except asyncio.TimeoutError:
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "handshake timeout")
            return
        except Exception as exc:
            log.warning("handshake error: %s", exc)
            await context.abort(grpc.StatusCode.INTERNAL, f"handshake error: {exc}")
            return

        # 3. HelloRequest チェック
        if not first_cmd.HasField("hello"):
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "first message must be HelloRequest"
            )
            return

        hello = first_cmd.hello

        # 4. Token チェック
        if not hmac.compare_digest(hello.token, self._server._token):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "token mismatch")
            return

        # 5. schema_major チェック
        if hello.schema_major != SCHEMA_MAJOR:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                f"schema_major mismatch: expected {SCHEMA_MAJOR}, got {hello.schema_major}",
            )
            return

        # 5b. schema_minor 不一致は警告のみ（互換性維持）
        if hello.schema_minor != SCHEMA_MINOR:
            log.warning(
                "schema_minor mismatch: client=%d server=%d — continuing",
                hello.schema_minor, SCHEMA_MINOR,
            )

        # 6–9. 接続数チェック・mode 確定・prepare・接続登録（Lock で同時ハンドシェイクの race を防ぐ）
        async with self._handshake_lock:
            # 1. 接続数チェック（Lock 内でアトミックに確認）
            if len(self._server._connections) >= MAX_CONNECTIONS:
                log.warning(
                    "gRPC connection rejected: MAX_CONNECTIONS=%d reached, peer=%s",
                    MAX_CONNECTIONS, context.peer(),
                )
                await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "max connections exceeded")
                return

            try:
                mode_str = _proto_mode_to_str(hello.mode)
            except ValueError as exc:
                await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
                return
            if self._server._mode == "live" and not self._server._connections:
                self._server._mode = mode_str
            elif mode_str != self._server._mode:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"mode mismatch: engine={self._server._mode!r}, client={mode_str!r}",
                )
                return

            is_first = not self._server._connections
            session_key = _GrpcSessionKey()
            q = self._server._outbox.add_conn(session_key)
            self._server._connections.add(session_key)

        # 7. Worker prepare（初回接続時のみ・タイムアウト付き）
        if is_first:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *(w.prepare() for w in self._server._workers.values()),
                        return_exceptions=True,
                    ),
                    timeout=20.0,
                )
                for name, res in zip(self._server._workers.keys(), results):
                    if isinstance(res, Exception):
                        log.warning("worker prepare() failed for %s: %s — continuing", name, res)
            except asyncio.TimeoutError:
                log.warning("worker prepare() timed out — continuing without full worker init")

        # 8. ReadyResponse を最初の Event として送信
        yield _build_ready_event(self._server)
        count = self._server._outbox.count()
        self._server._outbox.append({"event": "ClientConnected", "count": count})

        # Tachibana スタートアップ（replay でない場合・最初の接続時のみ）
        if self._server._mode != "replay" and is_first:
            startup_task = asyncio.create_task(self._server._startup_tachibana())
            startup_task.add_done_callback(_log_startup_tachibana_error)
            self._server._tachibana_startup_task = startup_task

        # 10. recv/send ループを並行実行
        recv_task = asyncio.create_task(
            self._recv_loop(request_iterator, session_key, context)
        )
        send_task = asyncio.create_task(
            self._send_loop(q, context)
        )

        try:
            done, pending = await asyncio.wait(
                {recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                if not t.cancelled():
                    exc = t.exception()
                    if exc is not None:
                        log.error("session task raised: %s", exc, exc_info=exc)
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    log.debug("pending task raised on cancel: %s", exc)
        finally:
            self._server._outbox.remove_conn(session_key)
            self._server._connections.discard(session_key)
            count = self._server._outbox.count()
            self._server._outbox.append({"event": "ClientDisconnected", "count": count})

            if not self._server._connections:
                # 最後の接続が切れた: クリーンアップ
                task_to_cancel = self._server._tachibana_startup_task
                if task_to_cancel and not task_to_cancel.done():
                    task_to_cancel.cancel()
                    try:
                        await task_to_cancel
                    except (asyncio.CancelledError, Exception):
                        pass
                self._server._tachibana_startup_task = None

                kabu_task = self._server._kabu_startup_task
                if kabu_task and not kabu_task.done():
                    kabu_task.cancel()
                    try:
                        await kabu_task
                    except (asyncio.CancelledError, Exception):
                        pass
                self._server._kabu_startup_task = None

                if self._server._event_task and not self._server._event_task.done():
                    self._server._event_task.cancel()
                    try:
                        await self._server._event_task
                    except (asyncio.CancelledError, Exception):
                        pass
                self._server._event_task = None

                from engine.exchanges.tachibana_auth import StartupLatch
                self._server._tachibana_startup_latch = StartupLatch()
                try:
                    await self._server._cancel_all_streams()
                except Exception as exc:
                    log.error("_cancel_all_streams failed: %s", exc, exc_info=True)

                from engine.server import ReplayState, LiveState
                self._server._replay_state = ReplayState.IDLE
                self._server._live_state = LiveState.DISCONNECTED
                self._server._connected_venue = None
                self._server._replay_streaming_fills.clear()
                self._server._mode = "live"

    async def _recv_loop(
        self,
        request_iterator: AsyncIterator[engine_pb2.Command],
        session_key: _GrpcSessionKey,
        context: aio.ServicerContext,
    ) -> None:
        """gRPC stream からコマンドを受信して dispatch する。"""
        async for cmd in request_iterator:
            which = cmd.WhichOneof("payload")
            if which is None:
                continue
            if which == "hello":
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "duplicate HelloRequest in session"
                )
                return
            op = _FIELD_TO_OP.get(which)
            if op is None:
                log.warning("Unknown command field %r — passing as-is to dispatch", which)
                op = which
            try:
                payload_dict = _cmd_payload_to_dict(cmd, which)
                msg = {"op": op, **payload_dict}
                await self._server._dispatch(op, msg, session_key)
            except Exception as exc:
                log.error("gRPC dispatch error op=%s: %s", op, exc, exc_info=True)

    async def _send_loop(self, q: asyncio.Queue, context: aio.ServicerContext) -> None:
        """_outbox キューからイベントを受信して gRPC stream に送信する。"""
        event_dict: dict | None = None
        while True:
            try:
                event_dict = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.CancelledError:
                return
            except asyncio.TimeoutError:
                if context.done():
                    return
                continue

            proto_event = _dict_to_proto_event(event_dict)
            if proto_event is None:
                continue
            try:
                await context.write(proto_event)
            except Exception as exc:
                log.warning(
                    "gRPC send failed (event=%s): %s",
                    event_dict.get("event", "?"),
                    exc,
                    exc_info=True,
                )
                return


class GrpcDataEngineServer:
    """gRPC サーバーのライフサイクル管理。"""

    def __init__(
        self,
        port: int,
        token: str,
        *,
        dev_tachibana_login_allowed: bool = False,
        dev_kabu_login_allowed: bool = False,
        dev_kabu_trade_password_allowed: bool = False,
        cache_dir: Path | None = None,
        config_dir: Path | None = None,
    ) -> None:
        from engine.server import DataEngineServer
        self._inner = DataEngineServer(
            port=port,
            token=token,
            dev_tachibana_login_allowed=dev_tachibana_login_allowed,
            dev_kabu_login_allowed=dev_kabu_login_allowed,
            dev_kabu_trade_password_allowed=dev_kabu_trade_password_allowed,
            cache_dir=cache_dir,
            config_dir=config_dir,
        )
        self._port = port
        self._token = token

    async def serve(self) -> None:
        server = aio.server()
        servicer = _GrpcDataEngineServicer(self._inner)
        engine_pb2_grpc.add_DataEngineServicer_to_server(servicer, server)
        actual_port = server.add_insecure_port(f"127.0.0.1:{self._port}")
        if actual_port == 0:
            raise RuntimeError(f"gRPC failed to bind port {self._port}")
        await server.start()
        log.info("Data engine gRPC listening on 127.0.0.1:%d", actual_port)
        # G2: write session file so that a Rust supervisor can attach via gRPC probe.
        try:
            _write_grpc_session_file(actual_port, self._token)
        except Exception as exc:
            log.error(
                "Failed to write engine-session.json: %s — attach mode will not work", exc
            )
        await self._inner._shutdown_event.wait()
        await server.stop(grace=5)


def _write_grpc_session_file(port: int, token: str) -> None:
    """Write engine-session.json with transport="grpc" so Rust can attach."""
    import json
    import os
    import tempfile
    from datetime import datetime, timezone

    pid = os.getpid()
    started_at = datetime.now(timezone.utc).isoformat()

    session = {
        "port": port,
        "token": token,
        "pid": pid,
        "schema_major": SCHEMA_MAJOR,
        "started_at": started_at,
        "transport": "grpc",
    }

    # Mirror the session file path used by Rust: data_path("engine-session.json").
    # FLOWSURFACE_DATA_PATH env override (mirrors replay_session._resolve_session_file_path).
    env_path = os.environ.get("FLOWSURFACE_DATA_PATH")
    if env_path:
        session_dir = Path(env_path)
    else:
        import platformdirs

        session_dir = Path(
            platformdirs.user_data_dir("flowsurface", appauthor=False, roaming=True)
        )

    session_dir.mkdir(parents=True, exist_ok=True)
    target = session_dir / "engine-session.json"

    # Atomic write: write to temp then rename.
    fd, tmp_path = tempfile.mkstemp(dir=session_dir, prefix=".engine-session-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(session, f)
        os.replace(tmp_path, target)
        log.debug("Wrote engine-session.json (port=%d, transport=grpc)", port)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
