"""ReplaySession / LiveSession — Python ヘルパー直接 API (Phase 8.1b).

attach mode (_AttachClient) を実装済み。in-process mode との auto-detect 対応。

Example::

    with ReplaySession() as s:
        s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
        s.run(strategy_file="strategy.py", on_event=print)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Literal, TypedDict

from engine.nautilus.engine_runner import NautilusRunner  # noqa: F401 (re-exported for patch)

# H11: LiveSession.login() で使う Tachibana 内部ログイン API を
# モジュールレベルで再 export しておくことで、テストから monkeypatch で
# 差し替え可能にする（実 HTTP を打たずに引数伝播を検証する）。
# `_tachibana_login_call` は async である点に注意（asyncio.run でラップする）。
from engine.exchanges.tachibana_auth import login as _tachibana_login_call  # noqa: F401
from engine.exchanges.tachibana_helpers import PNoCounter as _PNoCounter

__all__ = ["ReplaySession", "LiveSession"]

# L-1 (general): force_mode の Literal 型エイリアス。ReplaySession / LiveSession で共有。
_ForceMode = Literal["auto", "inprocess", "attach"]


# M-3 (type): _read_session_file の戻り値型を TypedDict で明示する。
# total=False で部分書き込み・後方互換ファイルを許容する。
class SessionFileData(TypedDict, total=False):
    port: int
    token: str
    pid: int
    schema_major: int
    started_at: str

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status enum (C6 / H16)
# ---------------------------------------------------------------------------


class _ReplayStatus(Enum):
    """C6/H16: ReplaySession ライフサイクル状態。

    - IDLE: with ブロックに入った直後 / load() 呼び出し前
    - LOADED: load() 完了済み・run() 待ち
    - RUNNING: run() 実行中
    - STOPPING: attach mode で stop() を投げ EngineStopped 待ち
    - STOPPED: 正常終了
    - ERRORED: 実行中に例外発生
    """

    IDLE = "idle"
    LOADED = "loaded"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERRORED = "errored"


# H13: 同じ event を表現する複数のキー名を吸収する。
# in-process runner は ``{"type": "..."}``、attach mode の WS は
# ``{"event": "..."}`` を返すことがある。両方を許容する単一ヘルパで統一する。
def _extract_event_kind(evt: object) -> str | None:
    if not isinstance(evt, dict):
        return None
    kind = evt.get("event")
    if isinstance(kind, str):
        return kind
    kind = evt.get("type")
    if isinstance(kind, str):
        return kind
    return None


# ---------------------------------------------------------------------------
# Session file helpers
# ---------------------------------------------------------------------------


def _resolve_session_file_path() -> Path:
    """Rust 側 data::data_path(Some("engine-session.json")) と同じパスに解決する。"""
    if env_override := os.environ.get("FLOWSURFACE_DATA_PATH"):
        return Path(env_override) / "engine-session.json"
    import platformdirs
    base = platformdirs.user_data_dir("flowsurface", appauthor=False)
    return Path(base) / "engine-session.json"


def _is_pid_alive(pid: int) -> bool:
    """プロセスが生存しているか確認する（Unix/Windows 両対応）。"""
    import sys
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok) and exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        import signal
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but we can't send signals


def _read_session_file() -> SessionFileData | None:
    """engine-session.json を読んで内容を返す。stale/invalid なら None を返す。

    M-3 (type): 戻り値は ``SessionFileData`` TypedDict 形状を満たす dict、
    または stale/invalid なら ``None``。
    """
    path = _resolve_session_file_path()
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    # pid が生存しているか確認
    pid = data.get("pid")
    if pid is not None and not _is_pid_alive(int(pid)):
        return None

    return data  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# BusyError
# ---------------------------------------------------------------------------


class BusyError(Exception):
    """エンジンが別操作中のときに raise される例外。"""


# ---------------------------------------------------------------------------
# _AttachClient
# ---------------------------------------------------------------------------


class _AttachClient:
    """GUI 内 engine への WS クライアント（helper attach mode 専用）。

    既存 schemas.py の wire format を再利用する。新規 wire format は導入しない。
    token はログ・例外に出力しない。
    compression=None を強制（RSV1 互換性問題: MISSES.md 2026-04-25 参照）。
    """

    def __init__(self, endpoint: str, token: str, timeout_s: float) -> None:
        self._endpoint = endpoint
        self._token = token  # ログに出力禁止
        self._timeout_s = timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._send_queue: asyncio.Queue | None = None
        self._recv_queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._closed_event = threading.Event()
        self._handshake_ok: bool = False
        self._handshake_err: Exception | None = None
        self._thread: threading.Thread | None = None

    def handshake(self) -> None:
        """バックグラウンドスレッドで asyncio loop を起動し handshake を実行する。

        H14: 失敗時は try/finally で close() を呼んで thread / loop を確実に
        回収する（close は冪等なので成功時に呼ばれても問題なし）。
        """
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        try:
            # handshake 完了 or エラーを待つ
            self._ready.wait(timeout=self._timeout_s + 2.0)
            if self._handshake_err is not None:
                # H5: token mismatch を区別したエラーは原因をそのまま伝播する。
                err = self._handshake_err
                if isinstance(err, ConnectionRefusedError) and "token mismatch" in str(err):
                    raise err
                raise ConnectionRefusedError("attach handshake failed") from err
            if not self._handshake_ok:
                raise ConnectionRefusedError("attach handshake timeout")
        except BaseException:
            # H14: 失敗時は thread/loop をリークさせない。
            try:
                self.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("handshake cleanup close() failed: %s", exc)
            raise

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        finally:
            self._loop.close()
            self._closed_event.set()

    async def _async_main(self) -> None:
        import websockets
        import orjson
        from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR, Hello
        self._send_queue = asyncio.Queue()
        try:
            async with websockets.connect(
                self._endpoint,
                compression=None,
                open_timeout=self._timeout_s,
            ) as ws:
                self._ws = ws
                # Hello 送信
                hello = Hello(
                    schema_major=SCHEMA_MAJOR,
                    schema_minor=SCHEMA_MINOR,
                    client_version="helper-attach",
                    token=self._token,
                    mode="replay",
                )
                await ws.send(orjson.dumps(hello.model_dump()).decode())

                # Ready を待つ（ClientConnected は skip）
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout_s)
                    msg = orjson.loads(raw)
                    event = msg.get("event")
                    if event == "ClientConnected":
                        continue
                    if event == "Ready":
                        if msg.get("schema_major") != SCHEMA_MAJOR:
                            raise ConnectionRefusedError("schema_major mismatch")
                        break
                    if event in ("Error", "EngineError"):
                        # H5: token mismatch / auth_failed を専用エラーとして区別する。
                        # message は EngineError の構造に依存。token は raise しない。
                        code = msg.get("code") or msg.get("message", "")
                        if isinstance(code, str) and "auth" in code.lower():
                            raise ConnectionRefusedError(
                                "attach handshake: token mismatch"
                            )
                        raise ConnectionRefusedError("handshake rejected")
                    # unexpected — put in recv queue for wait_for
                    self._recv_queue.put_nowait(msg)

                self._handshake_ok = True
                # L-2 (general): handshake 成功を info で記録（token は出さない）。
                log.info("[_AttachClient] handshake ok (endpoint=%s)", self._endpoint)
                self._ready.set()

                # recv / send 並走。どちらかが終わったら他方も止める
                # （recv 終了 = 接続切断、send 終了 = sentinel/shutdown）。
                recv_task = asyncio.ensure_future(self._recv_loop(ws))
                send_task = asyncio.ensure_future(self._send_loop(ws))
                done, pending = await asyncio.wait(
                    {recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
        except Exception as exc:
            if not self._handshake_ok:
                # L-2 (general): handshake 失敗を error で記録（token は出さない）。
                log.error(
                    "[_AttachClient] handshake failed (endpoint=%s): %s",
                    self._endpoint,
                    type(exc).__name__,
                )
                self._handshake_err = exc
            self._ready.set()

    async def _recv_loop(self, ws) -> None:
        import websockets
        try:
            async for raw in ws:
                import orjson
                msg = orjson.loads(raw)
                self._recv_queue.put_nowait(msg)
            # graceful close: server 側が close() で抜けた場合は async-for が
            # 例外なしで終わる。events() / wait_for() を起こすため通知する。
            log.info("[_AttachClient] WS closed: graceful")
            self._recv_queue.put_nowait({"__error__": "ws_closed"})
        except websockets.ConnectionClosed as exc:
            # token は exc には含まれないため安全にログ可能。
            log.info(
                "[_AttachClient] WS closed: code=%s reason=%s",
                getattr(exc, "code", None),
                getattr(exc, "reason", None),
            )
            self._recv_queue.put_nowait({"__error__": "ws_closed"})
        except Exception as exc:
            log.warning("[_AttachClient] recv_loop terminated: %s", type(exc).__name__)
            self._recv_queue.put_nowait({"__error__": "ws_closed"})

    async def _send_loop(self, ws) -> None:
        """M-11 (general): timeout=1.0 を削除し sentinel `None` 経路のみで終了する。

        Group 2 で close() が必ず sentinel を投入するようになったため、polling
        ベースの timeout は不要。queue.get() を素直に await し、None で抜ける。
        """
        import orjson
        while True:
            cmd = await self._send_queue.get()
            if cmd is None:  # sentinel for close
                return
            await ws.send(orjson.dumps(cmd).decode())

    def send_command(self, cmd: dict) -> None:
        """同期的に command を送信キューに積む。"""
        if self._loop is None or self._send_queue is None:
            raise RuntimeError("handshake が完了していません")
        asyncio.run_coroutine_threadsafe(
            self._send_queue.put(cmd), self._loop
        ).result(timeout=5.0)

    def wait_for(self, event_type: str, timeout_s: float | None = None) -> dict:
        """指定 event type の event を待つ。他の event は再 queue に積む。"""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        pending: list[dict] = []
        try:
            while True:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"wait_for({event_type!r}) timed out")
                try:
                    msg = self._recv_queue.get(timeout=min(remaining if remaining is not None else 30, 30))
                except queue.Empty:
                    raise TimeoutError(f"wait_for({event_type!r}) timed out")

                if msg.get("event") == event_type:
                    return msg
                if "__error__" in msg:
                    raise ConnectionError("WS connection closed during wait_for")
                # H12: state guard で reject されたら BusyError に翻訳する。
                if msg.get("event") == "EngineBusy":
                    raise BusyError(
                        f"EngineBusy: state={msg.get('current_state')!r} "
                        f"cmd={msg.get('attempted_command')!r}"
                    )
                pending.append(msg)
        finally:
            # 保留 event を再 queue に戻す
            for m in pending:
                self._recv_queue.put_nowait(m)

    def events(self):
        """event stream を yield するジェネレータ。EngineStopped で終了。"""
        while True:
            try:
                msg = self._recv_queue.get(timeout=1.0)
            except queue.Empty:
                # H6: WS が閉じていれば直ちに終了する（最大 60s hang 防止）。
                if self._closed_event.is_set():
                    raise ConnectionError("WS connection closed")
                continue

            if "__error__" in msg:
                raise ConnectionError("WS connection lost")

            if msg.get("event") == "EngineStopped":
                yield msg
                return

            if msg.get("event") == "EngineBusy":
                raise BusyError(
                    f"EngineBusy: state={msg.get('current_state')!r} "
                    f"cmd={msg.get('attempted_command')!r}"
                )

            yield msg

    def close(self) -> None:
        """接続を閉じる（冪等）。

        C4: 二度呼んでも安全にし、sentinel 投入の Future を待つことで
        loop が確実に sentinel を消化してから join する。recv_loop は ws を
        閉じない限り永久に待つので、ここで ws.close() もスケジュールする。
        """
        # 既に閉じている場合は即 return（冪等性）。
        if self._closed_event.is_set():
            return

        # ループが生きていれば sentinel + ws.close をスケジュールする。
        if (
            self._loop is not None
            and self._send_queue is not None
            and self._loop.is_running()
        ):
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._send_queue.put(None), self._loop
                )
                fut.result(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("close: send sentinel failed: %s", exc)

            # recv_loop は ws を閉じない限り iter で待ち続けるので
            # ws.close() を loop 上で呼ぶ。loop が既に止まっていれば skip。
            ws = self._ws
            if ws is not None and self._loop.is_running():
                async def _close_ws():
                    try:
                        await ws.close()
                    except Exception:
                        pass
                try:
                    fut = asyncio.run_coroutine_threadsafe(_close_ws(), self._loop)
                    fut.result(timeout=2.0)
                except Exception as exc:  # noqa: BLE001
                    log.warning("close: ws.close failed: %s", exc)

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log.warning("close: attach thread did not terminate within 5s")


# ---------------------------------------------------------------------------
# ReplaySession
# ---------------------------------------------------------------------------

class ReplaySession:
    """replay セッション管理クラス (Phase 8.1b).

    Args:
        jquants_dir: J-Quants データディレクトリ。None のとき環境変数
            ``JQUANTS_DIR`` → ``S:/j-quants`` の順で解決する。
        log_level: ロガーレベル文字列。
        force_mode: "auto" | "inprocess" | "attach"。
            "auto" は engine-session.json / env var で probe して自動選択する。
        attach_endpoint: attach mode で使う WebSocket endpoint。
            None のとき session ファイル → env var の順で解決する。
        attach_timeout_s: probe / handshake タイムアウト秒数。
    """

    def __init__(
        self,
        *,
        jquants_dir: str | Path | None = None,
        log_level: str = "INFO",
        force_mode: _ForceMode = "auto",
        attach_endpoint: str | None = None,
        attach_timeout_s: float = 2.0,
    ) -> None:
        self._jquants_dir: Path | None = Path(jquants_dir) if jquants_dir else None
        self._log_level = log_level
        self._force_mode = force_mode
        self._attach_endpoint = attach_endpoint
        self._attach_timeout_s = attach_timeout_s

        self._mode: Literal["attach", "inprocess"] | None = None
        # C6: 状態は Enum で保持し、外部 API は文字列 Literal を返して互換維持。
        self._status: _ReplayStatus = _ReplayStatus.IDLE
        self._load_params: dict | None = None
        self._portfolio: dict | None = None
        self._stop_event: threading.Event = threading.Event()
        self._multiplier: int = 1
        self._entered: bool = False
        self._client: _AttachClient | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "ReplaySession":
        if self._entered:
            raise RuntimeError("ReplaySession は既に with ブロックに入っています。再利用不可。")
        self._entered = True

        if self._force_mode == "inprocess":
            self._mode = "inprocess"
            return self

        if self._force_mode == "attach":
            endpoint, token = self._resolve_endpoint_and_token()
            if endpoint is None or token is None:
                # M-6 (silent): 何が解決できなかったかを詳細化する。
                # attach_endpoint 指定済 + token 未解決のケースは env var の指定漏れが
                # 圧倒的多数なので、その点を明示する。token はログ・メッセージに出さない。
                if self._attach_endpoint is not None and token is None:
                    raise ConnectionRefusedError(
                        "force_mode='attach' but FLOWSURFACE_ENGINE_TOKEN env var is not set"
                    )
                raise ConnectionRefusedError(
                    "force_mode='attach' but no engine-session.json / FLOWSURFACE_ENGINE_TOKEN found"
                )
            client = _AttachClient(endpoint, token, self._attach_timeout_s)
            client.handshake()
            self._client = client
            self._mode = "attach"
            return self

        # force_mode == "auto": probe
        endpoint, token = self._resolve_endpoint_and_token()
        if endpoint is not None and token is not None:
            try:
                client = _AttachClient(endpoint, token, self._attach_timeout_s)
                client.handshake()
                self._client = client
                self._mode = "attach"
                log.info("ReplaySession: attach mode (endpoint=%s)", endpoint)
                return self
            except ConnectionRefusedError as exc:
                # H5: token mismatch は user 操作ミスとして surface する（error レベル）。
                if "token mismatch" in str(exc):
                    log.error(
                        "ReplaySession: attach failed (token mismatch?), "
                        "falling back to inprocess"
                    )
                else:
                    log.warning(
                        "ReplaySession: attach probe failed, falling back to inprocess: %s",
                        exc,
                    )
            except Exception as exc:
                log.warning(
                    "ReplaySession: attach probe failed, falling back to inprocess: %s", exc
                )

        self._mode = "inprocess"
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # stop_event をセットして run() を安全に終了させる
        self._stop_event.set()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        return False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> Literal["attach", "inprocess"]:
        if self._mode is None:
            raise RuntimeError("with ブロックの外では mode にアクセスできません。")
        return self._mode

    @property
    def status(self) -> Literal["idle", "loaded", "running", "stopping", "stopped", "errored"]:
        # 互換維持: 外部からは文字列で返す（Enum.value は Literal 値）。
        return self._status.value  # type: ignore[return-value]

    @property
    def portfolio(self) -> dict | None:
        return self._portfolio

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        instrument_id: str,
        start_date: str,
        end_date: str,
        granularity: str = "Daily",
    ) -> None:
        """J-Quants ファイルの存在を確認し、load パラメータを保存する。

        Args:
            instrument_id: 例 ``"1301.TSE"``
            start_date: ISO8601 文字列 ``"2025-01-06"``
            end_date: ISO8601 文字列 ``"2025-03-31"``
            granularity: ``"Trade"`` | ``"Minute"`` | ``"Daily"``

        Raises:
            RuntimeError: 既に load() が呼ばれている場合。
            FileNotFoundError: J-Quants ファイルが見つからない場合。
        """
        if self._status is not _ReplayStatus.IDLE:
            raise RuntimeError(
                f"load() は idle 状態でのみ呼べます (現在: {self._status.value!r})"
            )

        if self._mode == "attach":
            from engine.schemas import LoadReplayData
            import uuid
            cmd = LoadReplayData(
                request_id=str(uuid.uuid4()),
                instrument_id=instrument_id,
                start_date=start_date,
                end_date=end_date,
                granularity=granularity,
            )
            # M-5 (type): assert は -O で消えるため、attach client 未初期化を
            # 明示的に RuntimeError として raise する。
            if self._client is None:
                raise RuntimeError("attach client not initialized")
            self._client.send_command(cmd.model_dump())
            # ReplayDataLoaded を待つ（timeout 60s）
            self._client.wait_for("ReplayDataLoaded", timeout_s=60.0)
            self._load_params = {
                "instrument_id": instrument_id,
                "start_date": start_date,
                "end_date": end_date,
                "granularity": granularity,
            }
            self._status = _ReplayStatus.LOADED
            return

        # in-process
        from engine.nautilus.jquants_loader import check_data_exists

        base_dir = self._resolve_base_dir()
        kwargs: dict = {}
        if base_dir is not None:
            kwargs["base_dir"] = base_dir

        check_data_exists(instrument_id, start_date, end_date, granularity, **kwargs)

        self._load_params = {
            "instrument_id": instrument_id,
            "start_date": start_date,
            "end_date": end_date,
            "granularity": granularity,
        }
        self._status = _ReplayStatus.LOADED

    def run(
        self,
        *,
        strategy_file: str,
        on_event: Callable[[dict], None],
        strategy_id: str = "user-strategy",
        initial_cash: int = 1_000_000,
        currency: str = "JPY",
        multiplier: int = 1,
        strategy_init_kwargs: dict | None = None,
    ) -> None:
        """NautilusRunner.start_backtest_replay_streaming を呼ぶ（in-process）
        または attach mode で engine に StartEngine を送信する。

        Args:
            strategy_file: ユーザー戦略ファイルのパス。
            on_event: IPC イベント dict を受け取る callback。
            strategy_id: 戦略 ID 文字列。
            initial_cash: 初期資金（円）。
            currency: 通貨コード。
            multiplier: 初期再生速度倍率。
            strategy_init_kwargs: 戦略コンストラクタに渡す追加引数。

        Raises:
            RuntimeError: load() が呼ばれていない場合。
            FileNotFoundError: strategy_file が存在しない場合。
        """
        if self._status is not _ReplayStatus.LOADED:
            raise RuntimeError(
                f"run() の前に load() を呼んでください (現在: {self._status.value!r})"
            )
        if not Path(strategy_file).exists():
            raise FileNotFoundError(f"strategy_file が見つかりません: {strategy_file!r}")

        if self._mode == "attach":
            from engine.schemas import StartEngine, EngineStartConfig
            import uuid
            params = self._load_params or {}
            cmd = StartEngine(
                request_id=str(uuid.uuid4()),
                engine="Backtest",
                strategy_id=strategy_id,
                config=EngineStartConfig(
                    instrument_id=params["instrument_id"],
                    start_date=params["start_date"],
                    end_date=params["end_date"],
                    initial_cash=str(initial_cash),
                    granularity=params["granularity"],
                    strategy_file=str(strategy_file),
                    strategy_init_kwargs=strategy_init_kwargs,
                ),
            )
            self._status = _ReplayStatus.RUNNING
            # M-5 (type): assert は -O で消えるため明示的に raise する。
            if self._client is None:
                raise RuntimeError("attach client not initialized")
            self._client.send_command(cmd.model_dump())
            try:
                for evt in self._client.events():
                    # H13: attach 経路でも portfolio を更新する。
                    # event/type のどちらでも判定できるよう統一ヘルパ経由。
                    if _extract_event_kind(evt) == "ReplayBuyingPower":
                        self._portfolio = evt
                    on_event(evt)
                self._status = _ReplayStatus.STOPPED
            except Exception:
                self._status = _ReplayStatus.ERRORED
                raise
            return

        # in-process
        self._multiplier = multiplier
        self._stop_event.clear()
        self._status = _ReplayStatus.RUNNING

        params = self._load_params or {}

        def _wrapped_on_event(evt: dict) -> None:
            # H13: in-process / attach のキー名差異を吸収する。
            if _extract_event_kind(evt) == "ReplayBuyingPower":
                self._portfolio = evt
            on_event(evt)

        runner = NautilusRunner()

        base_dir = self._resolve_base_dir()

        try:
            runner.start_backtest_replay_streaming(
                strategy_id=strategy_id,
                instrument_id=params["instrument_id"],
                start_date=params["start_date"],
                end_date=params["end_date"],
                granularity=params["granularity"],
                initial_cash=initial_cash,
                multiplier=multiplier,
                get_multiplier=lambda: self._multiplier,
                currency=currency,
                base_dir=base_dir,
                on_event=_wrapped_on_event,
                strategy_file=strategy_file,
                strategy_init_kwargs=strategy_init_kwargs,
                stop_event=self._stop_event,
            )
            self._status = _ReplayStatus.STOPPED
        except Exception:
            self._status = _ReplayStatus.ERRORED
            raise

    def set_speed(self, multiplier: int) -> None:
        """再生速度倍率を変更する（走行中も即時反映）。"""
        self._multiplier = multiplier

    def stop(self) -> None:
        """実行中の replay を停止する。

        attach mode 走行中: ``STOPPING`` に遷移し、EngineStopped 受信で
        ``STOPPED`` に進む（events() ループ側で実施）。
        in-process: ``stop_event`` をセットして runner を抜ける。
        """
        # H16: attach mode の running → stopping 遷移を可視化する。
        if self._status is _ReplayStatus.RUNNING and self._mode == "attach":
            self._status = _ReplayStatus.STOPPING
        self._stop_event.set()

    def submit_order(
        self,
        *,
        instrument_id: str,
        side: str,
        quantity: float | int,
        order_type: str = "MARKET",
        price: float | None = None,
    ) -> str:
        """注文を送信する（Phase 8.1a ではスタブ）。"""
        raise NotImplementedError("submit_order() は後のフェーズで実装予定")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_base_dir(self) -> Path | None:
        """J-Quants データディレクトリを解決する。"""
        if self._jquants_dir is not None:
            return self._jquants_dir
        env_val = os.environ.get("JQUANTS_DIR")
        if env_val:
            return Path(env_val)
        default = Path("S:/j-quants")
        if default.exists():
            return default
        # check_data_exists のデフォルト値に委ねる
        return None

    def _resolve_endpoint_and_token(self) -> tuple[str | None, str | None]:
        """endpoint と token を (明示引数 → session ファイル → env) の順で解決する。"""
        # 1. 明示引数
        if self._attach_endpoint is not None:
            token = os.environ.get("FLOWSURFACE_ENGINE_TOKEN")
            return (self._attach_endpoint, token)

        # 2. session ファイル
        session = _read_session_file()
        if session is not None:
            port = session.get("port", 19876)
            token = session.get("token")
            # M-10 (general): started_at を info で出力（token は出さない）。
            started_at = session.get("started_at")
            if started_at:
                log.info(
                    "ReplaySession: using engine-session.json (port=%s, started_at=%s)",
                    port,
                    started_at,
                )
            return (f"ws://127.0.0.1:{port}/", token)

        # 3. env のみ
        token = os.environ.get("FLOWSURFACE_ENGINE_TOKEN")
        if token:
            return ("ws://127.0.0.1:19876/", token)

        return (None, None)


# ---------------------------------------------------------------------------
# LiveSession
# ---------------------------------------------------------------------------

class LiveSession:
    """ライブ取引セッション管理クラス (Phase 8.1a stub).

    Args:
        venue: 取引所識別子（例: ``"tachibana"``）。
        demo: True のとき demo 環境に接続する。
        force_mode: "auto" | "inprocess" | "attach"。
    """

    def __init__(
        self,
        *,
        venue: str,
        demo: bool = True,
        force_mode: _ForceMode = "auto",
        attach_endpoint: str | None = None,
        attach_timeout_s: float = 2.0,
    ) -> None:
        self._venue = venue
        self._demo = demo
        self._force_mode = force_mode
        # M-8 (general): attach 関連シグネチャを §4.3 に合わせて受け付ける。
        # 実装は Phase 8.3 まで NotImplementedError。
        self._attach_endpoint = attach_endpoint
        self._attach_timeout_s = attach_timeout_s
        self._mode: Literal["attach", "inprocess"] | None = None
        self._entered: bool = False
        # H11: login() 状態。in-process 経路で内部 API が成功すると True に遷移。
        self._logged_in: bool = False
        self._session: object | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "LiveSession":
        if self._entered:
            raise RuntimeError("LiveSession は既に with ブロックに入っています。再利用不可。")
        self._entered = True
        # H17: Phase 8.1a スコープでは attach mode は未実装。
        # silent fallback だと「動いているように見えて実は inprocess」になり
        # ユーザー側のデバッグが困難になるため、明示的に NotImplementedError を出す。
        if self._force_mode == "attach":
            raise NotImplementedError(
                "LiveSession attach mode is Phase 8.3 scope; not yet implemented"
            )
        # Phase 8.1a: always inprocess (auto / inprocess どちらも inprocess に倒す)
        self._mode = "inprocess"
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> Literal["attach", "inprocess"]:
        if self._mode is None:
            raise RuntimeError("with ブロックの外では mode にアクセスできません。")
        return self._mode

    @property
    def is_logged_in(self) -> bool:
        """H11: login() が成功して内部 API から session を得たかどうか。"""
        return self._logged_in

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self, *, user_id: str | None = None, password: str | None = None) -> None:
        """ログインする（H11: in-process 経路の本実装）。

        引数で渡された ``user_id`` / ``password`` を最優先し、無ければ
        ``DEV_TACHIBANA_USER_ID`` / ``DEV_TACHIBANA_PASSWORD`` から解決する。
        どちらも欠けている場合は ``ValueError``。

        ``demo`` flag は ``__init__`` の値をそのまま内部 API に伝播する。

        Args:
            user_id: ユーザー ID。明示しない場合は env から解決。
            password: パスワード。明示しない場合は env から解決。

        Raises:
            ValueError: cred が引数でも env でも解決できない場合 / attach mode の場合。
            NotImplementedError: attach mode（Phase 8.3 まで未実装）。
            Exception: 内部 Tachibana login API が raise した例外（そのまま伝播）。
        """
        # attach mode は __enter__ 時点で NotImplementedError になっているため
        # 通常はここに来ないが、防御的に明示的なエラーを返す。
        if self._mode == "attach":
            raise NotImplementedError(
                "LiveSession.login() in attach mode is Phase 8.3 scope"
            )

        resolved_user_id = user_id if user_id is not None else os.environ.get(
            "DEV_TACHIBANA_USER_ID"
        )
        resolved_password = password if password is not None else os.environ.get(
            "DEV_TACHIBANA_PASSWORD"
        )
        if not resolved_user_id or not resolved_password:
            raise ValueError(
                "LiveSession.login: missing credentials. "
                "Provide user_id/password or set DEV_TACHIBANA_* env"
            )

        # 内部 API は async — 既存の event loop と競合しないよう asyncio.run で
        # 同期ラップする。helper 直接 API は同期インターフェースを契約として
        # 提供するため、ここで sync ↔ async 境界を吸収する。
        p_no_counter = _PNoCounter()
        # 注意: ``_tachibana_login_call`` はモジュールレベルで再 export して
        # いるため、テストは ``engine.replay_session._tachibana_login_call`` を
        # monkeypatch して引数伝播を検証できる。
        login_coro = _tachibana_login_call(
            resolved_user_id,
            resolved_password,
            is_demo=self._demo,
            p_no_counter=p_no_counter,
        )
        try:
            session = asyncio.run(login_coro)
        except RuntimeError as exc:
            # asyncio.run はネスト不可。既に loop が走っている文脈
            # （例: notebook）では新しい thread で実行する fallback を用意する。
            if "asyncio.run() cannot be called" in str(exc) or "running event loop" in str(exc):
                import concurrent.futures

                def _runner() -> object:
                    return asyncio.run(
                        _tachibana_login_call(
                            resolved_user_id,
                            resolved_password,
                            is_demo=self._demo,
                            p_no_counter=_PNoCounter(),
                        )
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    session = ex.submit(_runner).result()
            else:
                raise

        self._session = session
        self._logged_in = True


# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m engine.replay_session")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="replay を実行する")
    run_p.add_argument("--strategy", required=True, help="戦略ファイルパス")
    run_p.add_argument("--instrument", required=True, help="銘柄 ID (例: 1301.TSE)")
    run_p.add_argument("--start", required=True, help="開始日 (ISO8601)")
    run_p.add_argument("--end", required=True, help="終了日 (ISO8601)")
    run_p.add_argument("--granularity", default="Daily", choices=["Trade", "Minute", "Daily"])
    run_p.add_argument("--initial-cash", type=int, default=1_000_000)
    run_p.add_argument(
        "--mode", choices=["auto", "inprocess", "attach"], default="auto"
    )

    args = parser.parse_args()

    if args.cmd == "run":
        try:
            with ReplaySession(force_mode=args.mode) as s:
                s.load(args.instrument, args.start, args.end, args.granularity)
                s.run(
                    strategy_file=args.strategy,
                    on_event=lambda evt: print(json.dumps(evt, ensure_ascii=False)),
                    initial_cash=args.initial_cash,
                )
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        except BusyError as exc:
            # M-1 (silent): BusyError は専用メッセージで surface する。
            print(
                f"engine is busy ({exc}); call load() first or wait for current operation",
                file=sys.stderr,
            )
            sys.exit(2)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
