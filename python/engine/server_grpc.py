"""gRPC IPC server — grpcio.aio ベース。DataEngineServer のビジネスロジックを再利用する。"""
from __future__ import annotations

import asyncio
import hmac
import logging
from pathlib import Path
from typing import AsyncIterator

import grpc
from grpc import aio

from engine.proto import engine_pb2, engine_pb2_grpc
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

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
}

_EVENT_TO_FIELD_AND_CLASS = {
    "Ready":                      ("ready",                       engine_pb2.ReadyResponse),
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
}


class _GrpcSessionKey:
    """gRPC セッション用の一意キー。_Broadcaster の dict key として機能する。"""
    pass


def _proto_mode_to_str(mode_int: int) -> str:
    """proto AppMode enum value を文字列に変換する。"""
    if mode_int == engine_pb2.APP_MODE_REPLAY:
        return "replay"
    return "live"


def _build_ready_event(server) -> engine_pb2.Event:
    """DataEngineServer の状態から ReadyResponse Event を構築する。"""
    from engine.server import _ENGINE_VERSION
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
    """Command の payload フィールドを dict に変換する。"""
    from google.protobuf.json_format import MessageToDict
    payload_msg = getattr(cmd, which)
    return MessageToDict(
        payload_msg,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=False,
    )


def _dict_to_proto_event(event_dict: dict) -> engine_pb2.Event | None:
    """dict を proto Event に変換する。不明な event_name は None を返す。"""
    from google.protobuf.json_format import ParseDict

    event_name = event_dict.get("event")
    mapping = _EVENT_TO_FIELD_AND_CLASS.get(event_name)
    if mapping is None:
        log.warning("Unknown event name: %s — dropping", event_name)
        return None

    field_name, msg_class = mapping
    payload_dict = {k: v for k, v in event_dict.items() if k != "event"}

    try:
        payload = ParseDict(payload_dict, msg_class(), ignore_unknown_fields=True)
        return engine_pb2.Event(**{field_name: payload})
    except Exception as exc:
        log.warning("Failed to build proto Event %s: %s — dropping", event_name, exc)
        return None


class _GrpcDataEngineServicer(engine_pb2_grpc.DataEngineServicer):
    """gRPC DataEngine.Session RPC の実装。"""

    def __init__(self, server) -> None:
        self._server = server

    async def Session(
        self,
        request_iterator: AsyncIterator[engine_pb2.Command],
        context: aio.ServicerContext,
    ) -> AsyncIterator[engine_pb2.Event]:
        # 1. 接続数チェック
        if self._server._outbox.count() >= MAX_CONNECTIONS:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "max connections exceeded")
            return

        # 2. 最初の Command を待つ（15秒タイムアウト）
        try:
            first_cmd = await asyncio.wait_for(
                request_iterator.__anext__(), timeout=_HANDSHAKE_TIMEOUT_S
            )
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "handshake timeout")
            return
        except Exception as exc:
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, f"handshake error: {exc}")
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

        # 6. mode チェック
        mode_str = _proto_mode_to_str(hello.mode)
        if self._server._mode == "live" and not self._server._connections:
            self._server._mode = mode_str
        elif mode_str != self._server._mode:
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                f"mode mismatch: engine={self._server._mode!r}, client={mode_str!r}",
            )
            return

        # 7. Worker prepare（タイムアウト付き）
        try:
            await asyncio.wait_for(
                asyncio.gather(*(w.prepare() for w in self._server._workers.values())),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            log.warning("worker prepare() timed out — continuing without full worker init")
        except Exception as exc:
            log.warning("worker prepare() failed: %s — continuing", exc)

        # 8. ReadyResponse を最初の Event として送信
        yield _build_ready_event(self._server)

        # 9. キュー登録 + 接続追加
        session_key = _GrpcSessionKey()
        q = self._server._outbox.add_conn(session_key)
        self._server._connections.add(session_key)
        count = self._server._outbox.count()
        self._server._outbox.append({"event": "ClientConnected", "count": count})

        # Tachibana スタートアップ（replay でない場合・最初の接続時のみ）
        if self._server._mode != "replay" and len(self._server._connections) == 1:
            startup_task = asyncio.create_task(self._server._startup_tachibana())
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
                        log.error("session task raised: %s", exc)
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
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
                await self._server._cancel_all_streams()

                from engine.server import ReplayState, LiveState
                self._server._replay_state = ReplayState.IDLE
                self._server._live_state = LiveState.DISCONNECTED
                self._server._connected_venue = None
                self._server._replay_streaming_fills.clear()

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
                log.error("gRPC dispatch error op=%s: %s", op, exc)

    async def _send_loop(self, q: asyncio.Queue, context: aio.ServicerContext) -> None:
        """_outbox キューからイベントを受信して gRPC stream に送信する。"""
        while True:
            try:
                event_dict = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if context.done():
                    return
                continue
            except asyncio.CancelledError:
                return

            proto_event = _dict_to_proto_event(event_dict)
            if proto_event is None:
                continue
            try:
                await context.write(proto_event)
            except Exception as exc:
                log.warning("gRPC send failed: %s", exc)
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
        await server.start()
        log.info("Data engine gRPC listening on 127.0.0.1:%d", actual_port)
        await self._inner._shutdown_event.wait()
        await server.stop(grace=5)
