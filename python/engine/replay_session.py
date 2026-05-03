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
from pathlib import Path
from typing import Callable, Literal

from engine.nautilus.engine_runner import NautilusRunner  # noqa: F401 (re-exported for patch)

__all__ = ["ReplaySession", "LiveSession"]

log = logging.getLogger(__name__)


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


def _read_session_file() -> dict | None:
    """engine-session.json を読んで内容を返す。stale/invalid なら None を返す。"""
    path = _resolve_session_file_path()
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    # pid が生存しているか確認
    pid = data.get("pid")
    if pid is not None and not _is_pid_alive(int(pid)):
        return None

    return data


def _probe_engine(endpoint: str, token: str, timeout_s: float) -> bool:
    """TCP 接続 + Hello/Ready handshake が成功するか probe する。

    成功したら True、失敗したら False を返す（例外は raise しない）。
    """

    async def _do_probe() -> bool:
        import websockets
        from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR, Hello
        import orjson
        try:
            async with websockets.connect(endpoint, compression=None, open_timeout=timeout_s) as ws:
                hello = Hello(
                    schema_major=SCHEMA_MAJOR,
                    schema_minor=SCHEMA_MINOR,
                    client_version="helper-probe",
                    token=token,
                    mode="replay",
                )
                await ws.send(orjson.dumps(hello.model_dump()).decode())
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
                msg = orjson.loads(raw)
                # ClientConnected event も来ることがあるのでスキップ
                if msg.get("event") == "ClientConnected":
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
                    msg = orjson.loads(raw)
                return msg.get("event") == "Ready" and msg.get("schema_major") == SCHEMA_MAJOR
        except Exception:
            return False

    try:
        return asyncio.run(_do_probe())
    except Exception:
        return False


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
        """バックグラウンドスレッドで asyncio loop を起動し handshake を実行する。"""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # handshake 完了 or エラーを待つ
        self._ready.wait(timeout=self._timeout_s + 2.0)
        if self._handshake_err is not None:
            raise ConnectionRefusedError("attach handshake failed") from self._handshake_err
        if not self._handshake_ok:
            raise ConnectionRefusedError("attach handshake timeout")

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
                        raise ConnectionRefusedError("handshake rejected")
                    # unexpected — put in recv queue for wait_for
                    self._recv_queue.put_nowait(msg)

                self._handshake_ok = True
                self._ready.set()

                # recv / send 並走
                await asyncio.gather(
                    self._recv_loop(ws),
                    self._send_loop(ws),
                )
        except Exception as exc:
            if not self._handshake_ok:
                self._handshake_err = exc
            self._ready.set()

    async def _recv_loop(self, ws) -> None:
        try:
            async for raw in ws:
                import orjson
                msg = orjson.loads(raw)
                self._recv_queue.put_nowait(msg)
        except Exception:
            self._recv_queue.put_nowait({"__error__": "ws_closed"})

    async def _send_loop(self, ws) -> None:
        import orjson
        while True:
            try:
                cmd = await asyncio.wait_for(self._send_queue.get(), timeout=1.0)
                if cmd is None:  # sentinel for close
                    return
                await ws.send(orjson.dumps(cmd).decode())
            except asyncio.TimeoutError:
                if self._closed_event.is_set():
                    return

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
                pending.append(msg)
        finally:
            # 保留 event を再 queue に戻す
            for m in pending:
                self._recv_queue.put_nowait(m)

    def events(self):
        """event stream を yield するジェネレータ。EngineStopped で終了。"""
        while True:
            try:
                msg = self._recv_queue.get(timeout=60.0)
            except queue.Empty:
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
        """接続を閉じる。"""
        if self._loop and self._send_queue:
            asyncio.run_coroutine_threadsafe(
                self._send_queue.put(None), self._loop
            )
        if self._thread:
            self._thread.join(timeout=5.0)


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
        force_mode: Literal["auto", "inprocess", "attach"] = "auto",
        attach_endpoint: str | None = None,
        attach_timeout_s: float = 2.0,
    ) -> None:
        self._jquants_dir: Path | None = Path(jquants_dir) if jquants_dir else None
        self._log_level = log_level
        self._force_mode = force_mode
        self._attach_endpoint = attach_endpoint
        self._attach_timeout_s = attach_timeout_s

        self._mode: Literal["attach", "inprocess"] | None = None
        self._status: Literal["idle", "loaded", "running", "stopped", "errored"] = "idle"
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
                raise ConnectionRefusedError("force_mode='attach' but no endpoint/token found")
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
    def status(self) -> Literal["idle", "loaded", "running", "stopped", "errored"]:
        return self._status

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
        if self._status not in ("idle",):
            raise RuntimeError(
                f"load() は idle 状態でのみ呼べます (現在: {self._status!r})"
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
            assert self._client is not None
            self._client.send_command(cmd.model_dump())
            # ReplayDataLoaded を待つ（timeout 60s）
            self._client.wait_for("ReplayDataLoaded", timeout_s=60.0)
            self._load_params = {
                "instrument_id": instrument_id,
                "start_date": start_date,
                "end_date": end_date,
                "granularity": granularity,
            }
            self._status = "loaded"
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
        self._status = "loaded"

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
        if self._status != "loaded":
            raise RuntimeError(
                f"run() の前に load() を呼んでください (現在: {self._status!r})"
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
            self._status = "running"
            assert self._client is not None
            self._client.send_command(cmd.model_dump())
            try:
                for evt in self._client.events():
                    if evt.get("event") == "ReplayBuyingPower":
                        self._portfolio = evt
                    on_event(evt)
                self._status = "stopped"
            except Exception:
                self._status = "errored"
                raise
            return

        # in-process
        self._multiplier = multiplier
        self._stop_event.clear()
        self._status = "running"

        params = self._load_params or {}

        def _wrapped_on_event(evt: dict) -> None:
            # ReplayBuyingPower イベントで portfolio を更新する
            if isinstance(evt, dict) and evt.get("type") == "ReplayBuyingPower":
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
            self._status = "stopped"
        except Exception:
            self._status = "errored"
            raise

    def set_speed(self, multiplier: int) -> None:
        """再生速度倍率を変更する（走行中も即時反映）。"""
        self._multiplier = multiplier

    def stop(self) -> None:
        """実行中の replay を停止する。"""
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
        force_mode: Literal["auto", "inprocess", "attach"] = "auto",
    ) -> None:
        self._venue = venue
        self._demo = demo
        self._force_mode = force_mode
        self._mode: Literal["attach", "inprocess"] | None = None
        self._entered: bool = False

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "LiveSession":
        if self._entered:
            raise RuntimeError("LiveSession は既に with ブロックに入っています。再利用不可。")
        self._entered = True
        # Phase 8.1a: always inprocess
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self, *, user_id: str | None = None, password: str | None = None) -> None:
        """ログインする。

        in-process mode: Phase 8.1 後半で実装予定（現在 NotImplementedError）。
        attach mode: ValueError を raise する。

        Args:
            user_id: ユーザー ID。
            password: パスワード。

        Raises:
            NotImplementedError: in-process mode での呼び出し時（Phase 8.1a）。
            ValueError: attach mode での呼び出し時。
        """
        if self._mode == "attach":
            raise ValueError("attach mode では user_id/password を直接渡せません")
        raise NotImplementedError("LiveSession.login() は Phase 8.1 の後半で実装")


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
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
