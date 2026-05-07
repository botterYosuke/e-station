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
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Literal, Optional, TypedDict

from engine.nautilus.engine_runner import NautilusRunner  # noqa: F401 (re-exported for patch)
from engine.schemas import AUTH_FAILED_CODE

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
# M-Type2: 必須 (port/token) と任意 (pid/schema_major/started_at) を分離する。
# 読み取り中間表現は ``PartialSessionFileData`` (total=False)、最終的に
# 必須フィールド検証が通ったものは ``SessionFileData`` (total=True) として返す。
class PartialSessionFileData(TypedDict, total=False):
    """読み取り中間表現 — JSON にどのフィールドが入っているか不明な段階。"""

    port: int
    token: str
    pid: int
    schema_major: int
    started_at: str


class SessionFileData(TypedDict, total=False):
    """確定済み形 — token/port は必須、その他はオプショナル。

    Note: TypedDict は単一クラス内で必須/任意を分離できないため、
    必須性は ``_read_session_file`` のランタイム検証で担保する。
    型チェッカ向けには ``Required[...]`` を使ってもよいが、Python 3.10
    互換維持のため `total=False` のまま runtime guard を真実源とする。
    """

    # 必須 (runtime で _read_session_file が validate)
    port: int
    token: str
    # オプショナル
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
    # Rust 側 ``data/src/lib.rs::data_path`` は ``dirs_next::data_dir()`` を呼び、
    # Windows では ``%APPDATA%`` (Roaming) を返す。``platformdirs.user_data_dir`` は
    # 既定で Local を返すため、``roaming=True`` を渡して Rust と一致させる
    # (macOS/Linux では引数は no-op)。これを忘れると helper が GUI が書いた
    # ``engine-session.json`` を見つけられず attach mode で即終了する。
    base = platformdirs.user_data_dir("flowsurface", appauthor=False, roaming=True)
    return Path(base) / "engine-session.json"


def _is_pid_alive(pid: int) -> bool:
    """プロセスが生存しているか確認する（Unix/Windows 両対応）。"""
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
    M-Type2: 必須フィールド ``token`` と ``port`` をランタイムで検証してから
    narrow した ``SessionFileData`` 型として返す。
    """
    path = _resolve_session_file_path()
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    partial: PartialSessionFileData = data  # type: ignore[assignment]

    # M-SF3: token フィールドが存在しないか空の場合は invalid として None を返す。
    token = partial.get("token")
    if not token:
        log.debug("[_read_session_file] no token field, ignoring session file")
        return None

    # M-Type2: port も必須。欠損・非 int は invalid 扱い。
    port = partial.get("port")
    if not isinstance(port, int) or port <= 0:
        log.debug(
            "[_read_session_file] missing/invalid port=%r, ignoring session file", port
        )
        return None

    # M-GP2 (Phase 8 R1 / Phase 3): pid フィールドが欠損 (キー無し) または
    # ``None`` の session ファイルは invalid 扱いで None を返す。理由:
    # Rust 側 ``EngineSession`` は spawn 時に必ず pid を書き込む契約のため、
    # pid 不在 = ファイル破損 / 旧フォーマット / テスト漏れ のいずれか。
    # 安全側に倒して fallback させる。
    pid = partial.get("pid")
    if pid is None:
        log.debug("[_read_session_file] missing pid field, ignoring session file")
        return None
    if not _is_pid_alive(int(pid)):
        return None

    return partial  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# BusyError
# ---------------------------------------------------------------------------


def _credential_fingerprint(
    user_id: str | None, password: str | None, demo: bool
) -> str:
    """MEDIUM-R2-7: credential を平文で保持しないための fingerprint。

    SHA-256 をかけた hex digest を返す。`None` は空文字列として扱う。
    値そのものは外に出さない (RuntimeError メッセージにも fingerprint だけ
    含めない方針 — 比較にのみ使う)。
    """
    import hashlib
    payload = (
        f"{user_id or ''}\x00{password or ''}\x00{'1' if demo else '0'}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class BusyError(Exception):
    """エンジンが別操作中のときに raise される例外。"""


class _TokenMismatchError(ConnectionRefusedError):
    """token mismatch による接続拒否を示す専用例外（M-SF1）。"""


# ---------------------------------------------------------------------------
# _AttachClient
# ---------------------------------------------------------------------------


class _AttachClient:
    """GUI 内 engine への WS クライアント（helper attach mode 専用）。

    既存 schemas.py の wire format を再利用する。新規 wire format は導入しない。
    token はログ・例外に出力しない。
    compression=None を強制（RSV1 互換性問題: MISSES.md 2026-04-25 参照）。
    """

    def __init__(self, endpoint: str, token: str, timeout_s: float, *, mode: str = "replay") -> None:
        self._endpoint = endpoint
        self._token = token  # ログに出力禁止
        self._timeout_s = timeout_s
        self._mode = mode
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._send_queue: asyncio.Queue | None = None
        self._recv_queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._closed_event = threading.Event()
        self._handshake_ok: bool = False
        self._handshake_err: Exception | None = None
        self._thread: threading.Thread | None = None
        # HIGH-R2-3: events() / wait_for() で「自分宛の Error」を識別する
        # ための request_id。caller (run()) が StartEngine 発行時にセット。
        # None のときは broadcast 扱い (request_id=None の Error) のみ raise。
        self._current_request_id: str | None = None

    def set_current_request_id(self, request_id: str | None) -> None:
        """events()/wait_for() の Error フィルタに使う request_id を設定する。

        HIGH-R2-3: 自分が発行した request_id 一致 or broadcast (None) のみ raise。
        他 client 由来の Error (request_id 不一致) は無視して継続する。
        """
        self._current_request_id = request_id

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
                # H5 / M-SF1: token mismatch を専用例外型で区別する（文字列依存を排除）。
                err = self._handshake_err
                if isinstance(err, _TokenMismatchError):
                    raise err
                raise ConnectionRefusedError("attach handshake failed") from err
            if not self._handshake_ok:
                raise ConnectionRefusedError("attach handshake timeout")
        except BaseException:
            # H14 / H-GP2 (Phase 8 R1 / Phase 3): 失敗時は thread/loop を
            # リークさせない。close() で sentinel を投入し thread を join
            # するが、5s 以内に終わらなければ warning を残し RuntimeError
            # でも上位に伝播させない (元の例外を優先)。close() 自体は冪等。
            try:
                self.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("handshake cleanup close() failed: %s", exc)
            # close() が thread.join(5.0) 済み。残存していたら明示的に
            # warn する (リーク検知)。
            if self._thread is not None and self._thread.is_alive():
                log.warning(
                    "handshake cleanup: attach thread did not terminate "
                    "within 5s — possible loop deadlock"
                )
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
                    mode=self._mode,
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
                        # WS-MED3 / H-GP1 (Phase 8 R1 / Phase 3): token mismatch
                        # は code が `AUTH_FAILED_CODE` と完全一致するときのみ
                        # `_TokenMismatchError` に翻訳する。旧実装は
                        # `"auth" in code.lower()` という曖昧マッチで
                        # `authentication_error` 等もヒットしていた。
                        code = msg.get("code")
                        if isinstance(code, str) and code == AUTH_FAILED_CODE:
                            raise _TokenMismatchError(
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
            else:
                # C-1: ハンドシェイク後の例外: ログを出してエラーを recv_queue に通知する。
                # これにより events() が最大60秒ハングすることを防ぐ。
                log.warning(
                    "[_AttachClient] post-handshake error (endpoint=%s): %s: %s",
                    self._endpoint, type(exc).__name__, exc,
                )
                try:
                    self._recv_queue.put_nowait({"__error__": "ws_closed"})
                except Exception:
                    pass

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
        """指定 event type の event を待つ。他の event は再 queue に積む。

        Silent-M3 / M-GP3 (Phase 8 R1 / Phase 3): エラー終了パスでは pending を
        破棄する。具体的には ``Error`` event / ``EngineBusy`` で raise する場合、
        既に取り出した pending event は queue に戻さず捨てる
        (`events()` から後続呼び出ししたときに重複表示しない不変条件)。
        正常終了パス (待機 event 一致) でのみ pending を put_nowait で戻す。
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        pending: list[dict] = []
        # M-GP3: requeue するか drop するかを抜けるパスごとに切り替えるフラグ。
        # True = 戻す (待機 event を見つけた正常終了)、False = 破棄 (エラー)。
        requeue_pending = False
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
                    requeue_pending = True
                    return msg
                if "__error__" in msg:
                    raise ConnectionError("WS connection closed during wait_for")
                # Silent-M3 / HIGH-R2-3: engine からの Error event を
                # 自分宛 (request_id 一致 or broadcast) のときだけ raise する。
                # 別 client 由来の Error は pending に積まず無視して続行。
                if msg.get("event") == "Error":
                    evt_rid = msg.get("request_id")
                    if (
                        self._current_request_id is not None
                        and evt_rid is not None
                        and evt_rid != self._current_request_id
                    ):
                        log.debug(
                            "[_AttachClient.wait_for] ignoring Error from another "
                            "request_id=%r (current=%r)",
                            evt_rid,
                            self._current_request_id,
                        )
                        continue
                    raise ConnectionError(
                        f"engine returned Error: code={msg.get('code')!r} "
                        f"message={msg.get('message')!r}"
                    )
                # H12 / MEDIUM-R2-6: state guard で reject されたら BusyError に翻訳する。
                # broadcast EngineBusy (request_id 不一致) は自分と無関係なので無視。
                if msg.get("event") == "EngineBusy":
                    evt_rid = msg.get("request_id")
                    if (
                        self._current_request_id is not None
                        and evt_rid is not None
                        and evt_rid != self._current_request_id
                    ):
                        log.debug(
                            "[_AttachClient.wait_for] ignoring EngineBusy from another "
                            "request_id=%r (current=%r)",
                            evt_rid,
                            self._current_request_id,
                        )
                        continue
                    raise BusyError(
                        f"EngineBusy: state={msg.get('current_state')!r} "
                        f"cmd={msg.get('attempted_command')!r}"
                    )
                pending.append(msg)
        finally:
            # 正常終了 (待機 event 一致) のみ pending を re-queue する。
            # error path (BusyError / ConnectionError / TimeoutError) では
            # pending を破棄する (M-GP3)。
            if requeue_pending:
                for m in pending:
                    self._recv_queue.put_nowait(m)

    def events(self):
        """event stream を yield するジェネレータ。EngineStopped で終了。

        Silent-M3 (Phase 8 R1 / Phase 3): engine から ``Error`` event を
        受信したら ``ConnectionError`` を即 raise する。旧実装は Error を
        通常 event として yield していたため、ユーザーが気付かないまま
        次の event を待ち続けて 60s ハングするケースがあった。
        """
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
                # MEDIUM-R2-6: broadcast EngineBusy (別 client 起因 / request_id
                # 不一致) は自分の操作と無関係なので無視する。
                evt_rid = msg.get("request_id")
                if (
                    self._current_request_id is not None
                    and evt_rid is not None
                    and evt_rid != self._current_request_id
                ):
                    log.debug(
                        "[_AttachClient.events] ignoring EngineBusy from another "
                        "request_id=%r (current=%r)",
                        evt_rid,
                        self._current_request_id,
                    )
                    continue
                raise BusyError(
                    f"EngineBusy: state={msg.get('current_state')!r} "
                    f"cmd={msg.get('attempted_command')!r}"
                )

            # Silent-M3 / HIGH-R2-3: engine が返す Error event は
            # 自分宛のもの (request_id 一致 or broadcast) のときだけ raise する。
            # 別 client が起こした Error (request_id 不一致) は無視して続行。
            if msg.get("event") == "Error":
                evt_rid = msg.get("request_id")
                if (
                    self._current_request_id is not None
                    and evt_rid is not None
                    and evt_rid != self._current_request_id
                ):
                    log.debug(
                        "[_AttachClient.events] ignoring Error from another "
                        "request_id=%r (current=%r)",
                        evt_rid,
                        self._current_request_id,
                    )
                    continue
                raise ConnectionError(
                    f"engine returned Error: code={msg.get('code')!r} "
                    f"message={msg.get('message')!r}"
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
        # C-2: run() が呼ばれたときの strategy_id を保存して stop() で StopEngine に使う。
        self._strategy_id: str = ""

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
                # H5 / M-SF1: token mismatch は専用例外型で区別する（文字列依存を排除）。
                if isinstance(exc, _TokenMismatchError):
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
        # C-2: stop() を経由することで attach mode の StopEngine 送信と
        # in-process の stop_event セットを統一して行う。
        self.stop()
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                log.warning("[ReplaySession.__exit__] client.close() failed: %s", exc)
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
    def status(
        self,
    ) -> Literal["idle", "loaded", "running", "stopping", "stopped", "errored"]:
        # H-Type3 / C-GP1 (Phase 8 R1 / Phase 3): STOPPING は固有の状態として
        # 公開する。旧実装は "running" にマップしていたが、観測上 stop() 中の
        # フェーズと走行中を区別したいケースがあるため、Literal を 6 値に拡張。
        # 計画書 §4.1 の status プロパティ仕様。
        return self._status.value

    @property
    def portfolio(self) -> dict | None:
        return self._portfolio

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        instrument_id: str | list[str],
        start_date: str,
        end_date: str,
        granularity: str = "Daily",
    ) -> None:
        """J-Quants ファイルの存在を確認し、load パラメータを保存する。

        Args:
            instrument_id: 例 ``"1301.TSE"`` または ``["1301.TSE", "7203.TSE"]``
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
            req_id = str(uuid.uuid4())
            _single_id = instrument_id[0] if isinstance(instrument_id, list) else instrument_id
            _instrument_ids = instrument_id if isinstance(instrument_id, list) else None
            cmd = LoadReplayData(
                request_id=req_id,
                instrument_id=_single_id,
                instrument_ids=_instrument_ids,
                start_date=start_date,
                end_date=end_date,
                granularity=granularity,
            )
            # M-5 (type): assert は -O で消えるため、attach client 未初期化を
            # 明示的に RuntimeError として raise する。
            if self._client is None:
                raise RuntimeError("attach client not initialized")
            # HIGH-R2-3: 自分の request_id を attach client に伝え、
            # 他 client 由来の Error / EngineBusy を無視できるようにする。
            # 既存テストの fake client (`set_current_request_id` 未実装) と
            # 後方互換を保つため getattr で defensive に呼ぶ。
            _setter = getattr(self._client, "set_current_request_id", None)
            if callable(_setter):
                _setter(req_id)
            self._client.send_command(cmd.model_dump())
            # ReplayDataLoaded を待つ（timeout 60s）
            self._client.wait_for("ReplayDataLoaded", timeout_s=60.0)
            if isinstance(instrument_id, list):
                self._load_params = {
                    "instrument_id": instrument_id[0],  # 後方互換
                    "instrument_ids": instrument_id,    # 複数銘柄
                    "start_date": start_date,
                    "end_date": end_date,
                    "granularity": granularity,
                }
            else:
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

        if isinstance(instrument_id, list):
            for _iid in instrument_id:
                check_data_exists(_iid, start_date, end_date, granularity, **kwargs)
            self._load_params = {
                "instrument_id": instrument_id[0],  # 後方互換
                "instrument_ids": instrument_id,    # 複数銘柄
                "start_date": start_date,
                "end_date": end_date,
                "granularity": granularity,
            }
        else:
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

        # F9a: RunBuffer tee — replay イベントを JSONL ファイルに記録する。
        # strategy_file / instrument が取れない場合は None のまま graceful skip。
        self._last_run_buffer = None
        try:
            from engine.run_buffer import RunBuffer, make_run_id, get_run_buffer_base_dir
            _params = self._load_params or {}
            _instrument_ids = _params.get("instrument_ids")
            _instrument = _instrument_ids[0] if _instrument_ids else _params.get("instrument_id", "unknown")
            _run_id = make_run_id(strategy_file, _instrument)
            if _params:
                if _instrument_ids:
                    _scenario = {
                        "instruments": _instrument_ids,
                        "start": _params.get("start_date", ""),
                        "end": _params.get("end_date", ""),
                        "granularity": _params.get("granularity", ""),
                        "initial_cash": initial_cash,
                    }
                else:
                    _scenario = {
                        "instrument": _instrument,
                        "start": _params.get("start_date", ""),
                        "end": _params.get("end_date", ""),
                        "granularity": _params.get("granularity", ""),
                        "initial_cash": initial_cash,
                    }
            else:
                _scenario = None
            _run_buffer: RunBuffer | None = RunBuffer(
                run_id=_run_id,
                strategy_file=strategy_file,
                scenario=_scenario,
                base_dir=get_run_buffer_base_dir(),
            )
            self._last_run_buffer = _run_buffer
        except Exception as _rb_exc:
            log.warning("RunBuffer init failed, continuing without buffer: %s", _rb_exc)
            _run_buffer = None

        if self._mode == "attach":
            from engine.schemas import StartEngine, EngineStartConfig
            import uuid
            # C-2: strategy_id を保存して stop() で StopEngine コマンドに使えるようにする。
            self._strategy_id = strategy_id
            params = self._load_params or {}
            run_request_id = str(uuid.uuid4())
            _iids = params.get("instrument_ids")
            cmd = StartEngine(
                request_id=run_request_id,
                engine="Backtest",
                strategy_id=strategy_id,
                config=EngineStartConfig(
                    instrument_id=params["instrument_id"],
                    instrument_ids=_iids,
                    start_date=params["start_date"],
                    end_date=params["end_date"],
                    initial_cash=str(initial_cash),
                    granularity=params["granularity"],
                    strategy_file=str(strategy_file),
                    strategy_init_kwargs=strategy_init_kwargs,
                ),
            )
            # M-5 (type): assert は -O で消えるため明示的に raise する。
            if self._client is None:
                raise RuntimeError("attach client not initialized")
            # HIGH-R2-3: events() が他 client 由来の Error を無視するため、
            # 自分の StartEngine request_id を attach client に登録する。
            # fake client の後方互換のため getattr で defensive に呼ぶ。
            _setter = getattr(self._client, "set_current_request_id", None)
            # MEDIUM (2026-05-04): RUNNING 遷移と send_command を同一 try で囲む。
            # 送信失敗時に status=RUNNING / current_request_id を残したまま
            # 抜けると status() が偽陽性を返すため、ここで巻き戻す。
            self._status = _ReplayStatus.RUNNING
            if callable(_setter):
                _setter(run_request_id)
            try:
                self._client.send_command(cmd.model_dump())
            except Exception:
                self._status = _ReplayStatus.ERRORED
                if callable(_setter):
                    _setter(None)
                raise
            try:
                for evt in self._client.events():
                    # H13: attach 経路でも portfolio を更新する。
                    # event/type のどちらでも判定できるよう統一ヘルパ経由。
                    kind = _extract_event_kind(evt)
                    if kind == "ReplayBuyingPower":
                        self._portfolio = evt
                    # F9a: RunBuffer tee (attach mode)
                    if _run_buffer is not None:
                        try:
                            _run_buffer.write_event(evt)
                        except Exception as _rb_err:
                            log.warning("RunBuffer.write_event failed: %s", _rb_err)
                    # C-GP2 (Phase 8 R1 / Phase 3): EngineStopped を観測したら
                    # on_event 呼出 *前* に STOPPED へ遷移する。on_event 内で
                    # 例外が出ても status が ERRORED に上書きされず STOPPED の
                    # まま残ることを保証する (ユーザーコールバックのバグが
                    # ライフサイクル状態を破壊しない不変条件)。
                    if kind == "EngineStopped":
                        self._status = _ReplayStatus.STOPPED
                    on_event(evt)
                # ループが正常終了した場合、events() は EngineStopped で
                # return しているため _status は既に STOPPED。冪等更新。
                if self._status is not _ReplayStatus.STOPPED:
                    self._status = _ReplayStatus.STOPPED
                # F9a: 正常完了時に RunBuffer を finish する。
                if _run_buffer is not None:
                    try:
                        _run_buffer.finish()
                    except Exception as _rb_err:
                        log.warning("RunBuffer.finish failed: %s", _rb_err)
            except Exception:
                # C-GP2: 既に STOPPED に遷移済みなら維持する (on_event 内 raise
                # が STOPPED を ERRORED に上書きしないようにする)。
                if self._status is not _ReplayStatus.STOPPED:
                    self._status = _ReplayStatus.ERRORED
                # F9a: 例外時は abort する。
                if _run_buffer is not None:
                    try:
                        _run_buffer.abort()
                    except Exception:
                        pass
                raise
            finally:
                # LOW-R3-4: run() 退出時に attach client の current_request_id を
                # クリアする。次の load()/run() / 別 client の Error が前 run の
                # request_id にマッチして誤発火するのを防ぐ。
                _setter_finally = getattr(self._client, "set_current_request_id", None)
                if callable(_setter_finally):
                    _setter_finally(None)
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
            # F9a: RunBuffer tee (in-process mode)
            if _run_buffer is not None:
                try:
                    _run_buffer.write_event(evt)
                except Exception as _rb_err:
                    log.warning("RunBuffer.write_event failed: %s", _rb_err)
            on_event(evt)

        runner = NautilusRunner()

        base_dir = self._resolve_base_dir()

        try:
            _iids = params.get("instrument_ids")
            runner.start_backtest_replay_streaming(
                strategy_id=strategy_id,
                instrument_id=params["instrument_id"],
                instrument_ids=_iids,
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
            # F9a: 正常完了時に RunBuffer を finish する。
            if _run_buffer is not None:
                try:
                    _run_buffer.finish()
                except Exception as _rb_err:
                    log.warning("RunBuffer.finish failed: %s", _rb_err)
        except Exception:
            self._status = _ReplayStatus.ERRORED
            # F9a: 例外時は abort する。
            if _run_buffer is not None:
                try:
                    _run_buffer.abort()
                except Exception:
                    pass
            raise

    def set_speed(self, multiplier: int) -> None:
        """再生速度倍率を変更する（走行中も即時反映）。

        H-SF1: attach mode では SetReplaySpeed コマンドを engine に送信する。
        in-process mode では self._multiplier を更新するだけで runner が参照する。
        """
        self._multiplier = multiplier  # in-process 用
        if self._mode == "attach" and self._client is not None:
            import uuid as _uuid
            from engine.schemas import SetReplaySpeed
            cmd = SetReplaySpeed(
                request_id=str(_uuid.uuid4()),
                multiplier=multiplier,
            )
            try:
                self._client.send_command(cmd.model_dump())
            except Exception as exc:
                log.warning("[ReplaySession.set_speed] SetReplaySpeed send failed: %s", exc)

    def stop(self) -> None:
        """実行中の replay を停止する。

        attach mode 走行中: ``STOPPING`` に遷移し StopEngine を engine に送信する。
        EngineStopped 受信で events() ループが ``STOPPED`` に進む。
        in-process: ``stop_event`` をセットして runner を抜ける。
        """
        # R2-H1: StopEngine コマンドは RUNNING 状態のときのみ送信する。
        # ERRORED / LOADED / IDLE 状態では送信しない（engine が受け付けないため無意味）。
        if self._mode == "attach" and self._client is not None:
            if self._status is _ReplayStatus.RUNNING:
                self._status = _ReplayStatus.STOPPING
                import uuid as _uuid
                from engine.schemas import StopEngine
                cmd = StopEngine(
                    request_id=str(_uuid.uuid4()),
                    strategy_id=self._strategy_id,
                )
                try:
                    self._client.send_command(cmd.model_dump())
                except Exception as exc:
                    log.warning("[ReplaySession.stop] StopEngine send failed: %s", exc)
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
        """endpoint と token を解決する。

        M-GP1 (Phase 8 R1 / Phase 3): 解決順序を計画書 §4.1 に明記する。
        優先順は次の通り:

        1. **(a) 明示 attach_endpoint + env**
           ``__init__(attach_endpoint=...)`` が指定されている場合は
           その endpoint と ``FLOWSURFACE_ENGINE_TOKEN`` env の組み合わせ
           を使う。**session ファイルの token は混ぜない** (明示引数を
           受け入れた以上、ユーザーが env で token 管理する契約)。
        2. **(b) session ファイル**
           ``engine-session.json`` から ``{port, token}`` を読み取る。
           pid が dead だったり token / port が欠損していれば invalid
           として skip する (M-GP2)。
        3. **(c) env-only**
           ``FLOWSURFACE_ENGINE_TOKEN`` が設定されていて (1)(2) が
           当てはまらない場合、``FLOWSURFACE_ENGINE_PORT`` env を尊重
           しつつ既定 ``19876`` を使う (H-GP4)。

        いずれにも当てはまらなければ ``(None, None)`` を返し、
        ``__enter__`` 側で in-process フォールバックする。

        token はログ・例外メッセージに出力しない (CLAUDE.md「Token 認証」)。
        """
        # (a) 明示 attach_endpoint
        if self._attach_endpoint is not None:
            token = os.environ.get("FLOWSURFACE_ENGINE_TOKEN")
            return (self._attach_endpoint, token)

        # (b) session ファイル
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

        # (c) env-only — H-GP4: FLOWSURFACE_ENGINE_PORT を尊重する。
        token = os.environ.get("FLOWSURFACE_ENGINE_TOKEN")
        if token:
            port_env = os.environ.get("FLOWSURFACE_ENGINE_PORT")
            try:
                port = int(port_env) if port_env else 19876
            except ValueError:
                log.warning(
                    "FLOWSURFACE_ENGINE_PORT=%r is not an integer; using default 19876",
                    port_env,
                )
                port = 19876
            return (f"ws://127.0.0.1:{port}/", token)

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
        second_password: str | None = None,
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
        # C-GP4: attach mode 用の WS クライアント。inprocess では None のまま。
        self._client: _AttachClient | None = None
        # CRITICAL-R2-1: 直近の RequestVenueLogin 用 request_id (broadcast 経路の
        # VenueError と別 client 由来の event を区別するために保存)。
        self._login_request_id: str | None = None
        # MEDIUM-R2-7: 初回 login() で使った credential のハッシュ (検知用に
        # 平文ではなく SHA-256 で保管)。2回目以降 credential 変更を検知する。
        self._login_cred_fingerprint: str | None = None
        # Phase 4+5: in-process live run 用
        self._second_password: str | None = second_password
        self._stop_event: threading.Event | None = None
        self._strategy_id: str | None = None
        # attach mode run() 状態追跡（二重 stop 防止）
        self._live_running: bool = False

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "LiveSession":
        if self._entered:
            raise RuntimeError("LiveSession は既に with ブロックに入っています。再利用不可。")
        self._entered = True

        # C-GP4 (Phase 8 R1 / Phase 3): attach mode 本実装。
        # ReplaySession と同じ token / endpoint 解決順序を共有する。
        if self._force_mode == "attach":
            endpoint, token = self._resolve_endpoint_and_token()
            if endpoint is None or token is None:
                if self._attach_endpoint is not None and token is None:
                    raise ConnectionRefusedError(
                        "force_mode='attach' but FLOWSURFACE_ENGINE_TOKEN env var is not set"
                    )
                raise ConnectionRefusedError(
                    "force_mode='attach' but no engine-session.json / FLOWSURFACE_ENGINE_TOKEN found"
                )
            client = _AttachClient(endpoint, token, self._attach_timeout_s, mode="live")
            client.handshake()
            self._client = client
            self._mode = "attach"
            return self

        if self._force_mode == "auto":
            # auto: probe → attach mode → fallback to inprocess
            endpoint, token = self._resolve_endpoint_and_token()
            if endpoint is not None and token is not None:
                try:
                    client = _AttachClient(endpoint, token, self._attach_timeout_s, mode="live")
                    client.handshake()
                    self._client = client
                    self._mode = "attach"
                    log.info("LiveSession: attach mode (endpoint=%s)", endpoint)
                    return self
                except Exception as exc:
                    log.warning(
                        "LiveSession: attach probe failed, falling back to inprocess: %s",
                        type(exc).__name__,
                    )

        # force_mode == "inprocess" or auto-fallback
        self._mode = "inprocess"
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("[LiveSession.__exit__] client.close() failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_endpoint_and_token(self) -> tuple[str | None, str | None]:
        """ReplaySession と同じ解決ルール (M-GP1)。"""
        if self._attach_endpoint is not None:
            token = os.environ.get("FLOWSURFACE_ENGINE_TOKEN")
            return (self._attach_endpoint, token)

        session = _read_session_file()
        if session is not None:
            port = session.get("port", 19876)
            token = session.get("token")
            return (f"ws://127.0.0.1:{port}/", token)

        token = os.environ.get("FLOWSURFACE_ENGINE_TOKEN")
        if token:
            port_env = os.environ.get("FLOWSURFACE_ENGINE_PORT")
            try:
                port = int(port_env) if port_env else 19876
            except ValueError:
                port = 19876
            return (f"ws://127.0.0.1:{port}/", token)

        return (None, None)

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

    def login(self, *, user_id: str | None = None, password: str | None = None, venue: str | None = None) -> None:
        """ログインする。

        - in-process mode: 引数または ``DEV_TACHIBANA_*`` env から credential を
          解決して内部 Tachibana login API を直接叩く。
        - attach mode (C-GP4): credential を wire 経由で送らない。明示引数を
          渡されたら ``ValueError``。credential 無しなら engine に
          ``RequestVenueLogin`` を送り ``VenueReady`` / ``VenueError`` を待つ。
        - **M-GP5**: 既に ``is_logged_in == True`` なら無条件で no-op で return。
          (内部 API も engine への RequestVenueLogin も再送信しない)。

        ``demo`` flag は ``__init__`` の値をそのまま内部 API に伝播する。

        Args:
            user_id: ユーザー ID。明示しない場合は env から解決 (in-process のみ)。
            password: パスワード。明示しない場合は env から解決 (in-process のみ)。
            venue: venue 識別子 (例: ``"kabu_station"``、``"tachibana"``)。
                明示した場合は ``__init__`` の ``venue`` を上書きする。
                省略時は ``__init__`` の ``venue`` をフォールバックとして使う。

        Raises:
            ValueError: cred が引数でも env でも解決できない場合 (in-process)、
                または attach mode で credential を明示渡しされた場合。
            ConnectionError: attach mode で engine から ``VenueError`` が返った場合
                (code/message を含む)。
            Exception: 内部 Tachibana login API が raise した例外 (そのまま伝播)。
        """
        # MEDIUM-R2-7 / MEDIUM-R3-1: 2回目以降の login() は credential 変更を検知する。
        # ただし「両方とも明示渡し」のときだけ厳格チェックする。
        # 片方だけ明示 / 全部 None の場合は env / 既存値からの解決経路を尊重し、
        # caller の意図を「変更したい」と判別できないため過剰拒否を避ける。
        # `LiveSession` は immutable credential 前提 (変更したい場合は新 instance)。
        # 平文比較は監査ログ漏洩リスクがあるため SHA-256 fingerprint で比較する。
        if self._logged_in:
            if user_id is not None and password is not None:
                fp_now = _credential_fingerprint(user_id, password, self._demo)
                if (
                    self._login_cred_fingerprint is not None
                    and fp_now != self._login_cred_fingerprint
                ):
                    raise RuntimeError(
                        "LiveSession credentials cannot change after login; "
                        "create a new LiveSession instance instead"
                    )
            log.debug("LiveSession.login: already logged in — no-op")
            return

        # MEDIUM-B: venue 引数が渡された場合は self._venue を上書きする (MEDIUM-4)。
        # _logged_in ガードの後に置くことで、no-op パスでは副作用ゼロを保証する。
        # (M-GP5: is_logged_in=True なら副作用ゼロで return)
        if venue is not None:
            self._venue = venue

        # C-GP4: attach mode は engine に RequestVenueLogin を送って待つ。
        if self._mode == "attach":
            if user_id is not None or password is not None:
                raise ValueError(
                    "attach mode では user_id/password をワイヤー経由で送れません。"
                    "GUI/engine 側の保存済み credential か dev 用の credential を使ってください。"
                )
            self._login_attach()
            return

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
        # H-GP5 (Phase 8 R1 / Phase 3): p_no_counter は外側で 1 度だけ生成し、
        # nested-loop fallback でも同一インスタンスを再利用する (旧実装は
        # fallback 内で ``_PNoCounter()`` を再生成しており、最初に作った
        # counter が捨てられていた)。
        p_no_counter = _PNoCounter()
        # 注意: ``_tachibana_login_call`` はモジュールレベルで再 export して
        # いるため、テストは ``engine.replay_session._tachibana_login_call`` を
        # monkeypatch して引数伝播を検証できる。
        try:
            session = asyncio.run(
                _tachibana_login_call(
                    resolved_user_id,
                    resolved_password,
                    is_demo=self._demo,
                    p_no_counter=p_no_counter,
                )
            )
        except RuntimeError as exc:
            # asyncio.run はネスト不可。既に loop が走っている文脈
            # （例: notebook）では新しい thread で実行する fallback を用意する。
            if (
                "asyncio.run() cannot be called" in str(exc)
                or "running event loop" in str(exc)
            ):
                import concurrent.futures

                def _runner() -> object:
                    # H-GP5: 同一 p_no_counter インスタンスを再利用する。
                    return asyncio.run(
                        _tachibana_login_call(
                            resolved_user_id,
                            resolved_password,
                            is_demo=self._demo,
                            p_no_counter=p_no_counter,
                        )
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    session = ex.submit(_runner).result()
            else:
                raise

        self._session = session
        self._logged_in = True
        # MEDIUM-R2-7: 後続の login() で credential 変更を検知できるように
        # 解決済み credential の fingerprint を保存する (平文では保持しない)。
        self._login_cred_fingerprint = _credential_fingerprint(
            resolved_user_id, resolved_password, self._demo
        )

    def _login_attach(self) -> None:
        """C-GP4: attach mode の login flow.

        engine に ``RequestVenueLogin{venue=self._venue}`` を送り、
        ``VenueReady`` で成功 / ``VenueError`` で失敗を判定する。
        token は wire に出さない (`_AttachClient` がヘッドのみで管理)。
        """
        import uuid as _uuid
        from engine.schemas import RequestVenueLogin

        if self._client is None:
            raise RuntimeError("attach client not initialized")

        request_id = str(_uuid.uuid4())
        # CRITICAL-R2-1: 自分が発行した request_id を保存しておく。VenueError の
        # broadcast 経路 (request_id=None) と、別 client 由来のイベントを区別する。
        self._login_request_id = request_id
        cmd = RequestVenueLogin(request_id=request_id, venue=self._venue)
        self._client.send_command(cmd.model_dump())

        # VenueReady と VenueError どちらかを待つ。``wait_for`` は単一 event 名
        # しか待てないため、recv_queue を直接 poll して両方を捕捉する。
        deadline = time.monotonic() + 30.0
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError(
                    f"LiveSession.login: VenueReady/VenueError timed out (venue={self._venue!r})"
                )
            try:
                got = self._client._recv_queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self._client._closed_event.is_set():
                    raise ConnectionError(
                        "LiveSession.login: engine WS closed before response"
                    )
                continue

            if "__error__" in got:
                raise ConnectionError("LiveSession.login: engine WS connection lost")

            event = got.get("event")
            evt_request_id = got.get("request_id")
            if event == "VenueReady" and evt_request_id == request_id:
                self._logged_in = True
                self._session = got
                return
            # CRITICAL-R2-1 (silent-failure): VenueError を捕捉する条件を拡張する。
            # 旧実装は ``request_id == request_id`` の完全一致のみ raise していたため、
            # broadcast 経路で ``request_id is None`` の VenueError が来ても無視され、
            # _login_attach が 30 秒 timeout までハングしていた。
            # 以下の **いずれか** を満たす場合に raise する:
            #   (a) VenueError かつ request_id 一致 (unicast 応答)
            #   (b) VenueError かつ request_id is None (broadcast 経路)
            #   (c) Error イベント かつ request_id 一致 (engine の generic Error)
            if event == "VenueError" and (
                evt_request_id == request_id or evt_request_id is None
            ):
                code = got.get("code", "venue_error")
                message = got.get("message", "")
                raise ConnectionError(
                    f"LiveSession.login: VenueError code={code!r} message={message!r}"
                )
            if event == "Error" and evt_request_id == request_id:
                code = got.get("code", "error")
                message = got.get("message", "")
                raise ConnectionError(
                    f"LiveSession.login: Error code={code!r} message={message!r}"
                )
            if event == "EngineBusy":
                raise BusyError(
                    f"EngineBusy: state={got.get('current_state')!r} "
                    f"cmd={got.get('attempted_command')!r}"
                )
            # HIGH-A: VenueLoginCancelled を受信したら即座に ConnectionError を raise する。
            # kabu ダイアログキャンセル時に server.py が VenueLoginCancelled を emit するため、
            # ここで捕捉しないと 30 秒 timeout まで待機してしまう。
            # server.py は VenueLoginCancelled に必ず request_id を付けるため、
            # request_id 一致のみでフィルタする（is None アームは不要）。
            if event == "VenueLoginCancelled" and evt_request_id == request_id:
                raise ConnectionError(
                    f"LiveSession.login: login cancelled by user (venue={self._venue!r})"
                )
            # 他 event (VenueLoginStarted / 他 client 由来の Error 等) は無視して続行。

    # ------------------------------------------------------------------
    # Phase 4+5: live run / stop
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        instrument_id: str,
        strategy_file: str,
        max_qty: int,
        max_notional_jpy: int,
        on_event: "Callable[[dict], None] | None" = None,
        strategy_init_kwargs: "dict | None" = None,
        strategy_id: str = "live-strategy",
    ) -> None:
        """Live strategy を実行する（blocking）。

        attach 経路: StartEngine{engine:"Live"} を engine に送り、events() ループで
        EngineStopped を待つ。
        in-process 経路: NautilusRunner.start_live() を直接呼ぶ。

        Args:
            instrument_id: 取引対象銘柄 ID（例: ``"8306.T"``）。
            strategy_file: strategy .py ファイルパス。
            max_qty: 1 注文あたりの最大株数。
            max_notional_jpy: 1 注文あたりの最大金額（円）。
            on_event: IPC イベントを受け取るコールバック。None の場合は no-op。
            strategy_init_kwargs: strategy の `__init__` に渡す追加引数。
            strategy_id: strategy 識別子。

        Raises:
            RuntimeError: login() 前に呼んだ場合。
            RuntimeError: in-process モードで second_password が None の場合。
        """
        if not self._logged_in:
            raise RuntimeError("call login() before run()")

        self._strategy_id = strategy_id
        _on_event = on_event if on_event is not None else (lambda _: None)

        if self._mode == "attach":
            if self._client is None:
                raise RuntimeError("attach client not initialized")
            import uuid as _uuid
            from engine.schemas import StartEngine, EngineStartConfig

            run_request_id = str(_uuid.uuid4())
            cmd = StartEngine(
                request_id=run_request_id,
                engine="Live",
                strategy_id=strategy_id,
                config=EngineStartConfig(
                    instrument_id=instrument_id,
                    strategy_file=strategy_file,
                    strategy_init_kwargs=strategy_init_kwargs,
                    max_qty=max_qty,
                    max_notional_jpy=max_notional_jpy,
                ),
            )
            _setter = getattr(self._client, "set_current_request_id", None)
            self._live_running = True
            if callable(_setter):
                _setter(run_request_id)
            try:
                self._client.send_command(cmd.model_dump())
            except Exception:
                self._live_running = False
                if callable(_setter):
                    _setter(None)
                raise
            try:
                for evt in self._client.events():
                    _on_event(evt)
                    kind = _extract_event_kind(evt)
                    if kind == "EngineStopped":
                        break
                    if kind == "SecondPasswordRequired":
                        raise RuntimeError(
                            "LiveSession.run: engine requires second password — "
                            "set it via the GUI before calling run()"
                        )
            finally:
                self._live_running = False
                _setter_finally = getattr(self._client, "set_current_request_id", None)
                if callable(_setter_finally):
                    _setter_finally(None)
            return

        # in-process mode
        if self._second_password is None:
            raise RuntimeError(
                "second_password is required for in-process live run. "
                "Pass second_password=... to LiveSession()"
            )

        from engine.nautilus.engine_runner import NautilusRunner

        runner = NautilusRunner()
        self._stop_event = threading.Event()
        self._live_running = True
        try:
            runner.start_live(
                instrument_id=instrument_id,
                strategy_file=strategy_file,
                strategy_init_kwargs=strategy_init_kwargs,
                max_qty=max_qty,
                max_notional_jpy=max_notional_jpy,
                second_password=self._second_password,
                session=self._session,
                fd_queue=__import__("queue").Queue(maxsize=10_000),
                ec_queue=__import__("queue").Queue(maxsize=1_000),
                on_event=_on_event,
                stop_event=self._stop_event,
                strategy_id=strategy_id,
            )
        finally:
            self._live_running = False

    def stop(self) -> None:
        """実行中の live strategy を停止する。

        attach 経路: StopEngine コマンドを engine に送る。
        in-process 経路: stop_event を set して start_live() の worker に停止シグナルを送る。
        """
        if self._mode == "attach" and self._client is not None:
            if self._live_running:
                import uuid as _uuid
                from engine.schemas import StopEngine

                cmd = StopEngine(
                    request_id=str(_uuid.uuid4()),
                    strategy_id=self._strategy_id or "",
                )
                try:
                    self._client.send_command(cmd.model_dump())
                except Exception as exc:
                    log.warning("[LiveSession.stop] StopEngine send failed: %s", exc)
        if self._stop_event is not None:
            self._stop_event.set()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# F6b: CLI SCENARIO フォールバック解決
# ---------------------------------------------------------------------------


def _resolve_cli_params(
    *,
    strategy_path: str,
    cli_instrument: Optional[list[str]],  # nargs='+' で list が来る
    cli_start: Optional[str],
    cli_end: Optional[str],
    cli_granularity: Optional[str],
    cli_initial_cash: Optional[int],
) -> tuple[str | list[str], str, str, str, int]:
    """CLI 引数と戦略 .py の SCENARIO を統合して replay パラメータを確定する。

    優先順位: CLI 引数 > SCENARIO 定数。
    引数欠落 + SCENARIO 欠落のとき、必須フィールド（instrument/start/end）がなければ SystemExit。

    Returns:
        (instrument, start, end, granularity, initial_cash)
        instrument は str (単一) または list[str] (複数)
    """
    from engine.scenario import extract as _extract_scenario

    scenario: dict | None = None

    # 省略されている引数がある場合のみ SCENARIO を読む
    needs_scenario = any(
        v is None for v in [cli_instrument, cli_start, cli_end, cli_granularity, cli_initial_cash]
    )
    if needs_scenario:
        try:
            scenario = _extract_scenario(Path(strategy_path))
        except (SyntaxError, ValueError) as exc:
            log.error(
                "scenario.cli: SCENARIO in %s has invalid syntax: %s",
                strategy_path, exc,
            )
            print(
                f"error: SCENARIO in {strategy_path!r} has invalid syntax: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as exc:
            log.warning(
                "scenario.cli: failed to read SCENARIO from %s: %s",
                strategy_path, exc,
            )
            scenario = None

    def _resolve(field: str, cli_val, default=None):
        if cli_val is not None:
            if scenario is not None and field in scenario and scenario[field] != cli_val:
                log.info(
                    "scenario.cli source=cli: %s CLI=%r overrides SCENARIO=%r",
                    field, cli_val, scenario[field],
                )
            return cli_val
        if scenario is not None and scenario.get(field) is not None:
            log.info("scenario.cli source=file: using SCENARIO.%s=%r", field, scenario[field])
            return scenario[field]
        return default

    # instrument は nargs='+' で list[str] | None になる。
    # v2 SCENARIO には "instruments" (list)、v1 には "instrument" (str) がある。
    if cli_instrument is not None:
        # CLI 指定: 複数ならリスト、1要素なら str に正規化
        instrument: str | list[str] | None = cli_instrument if len(cli_instrument) > 1 else cli_instrument[0]
    elif scenario is not None and scenario.get("instruments") is not None:
        # v2 SCENARIO から複数銘柄取得
        instrument = scenario["instruments"]
    elif scenario is not None and scenario.get("instrument") is not None:
        # v1 SCENARIO から単一銘柄取得
        instrument = scenario["instrument"]
    else:
        instrument = None

    start = _resolve("start", cli_start)
    end = _resolve("end", cli_end)
    granularity = _resolve("granularity", cli_granularity, default="Daily")
    initial_cash = _resolve("initial_cash", cli_initial_cash, default=1_000_000)

    missing = []
    if instrument is None:
        missing.append("instrument")
    if start is None:
        missing.append("start")
    if end is None:
        missing.append("end")
    if missing:
        log.error("scenario.cli: required args missing: %s (provide via CLI or SCENARIO in .py)", missing)
        print(
            f"error: missing required args: {missing}. "
            "Provide via --instrument/--start/--end or add SCENARIO dict to the strategy .py",
            file=sys.stderr,
        )
        sys.exit(1)

    log.info("scenario.cli source=merged instrument=%r start=%r end=%r granularity=%r initial_cash=%r",
             instrument, start, end, granularity, initial_cash)
    return instrument, start, end, granularity, initial_cash


# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="python -m engine.replay_session")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="replay を実行する")
    run_p.add_argument("--strategy", required=True, help="戦略ファイルパス")
    # F6b: --instrument / --start / --end / --granularity / --initial-cash は optional。
    # 省略時は戦略 .py の SCENARIO 定数をフォールバック値として使う。CLI 引数優先。
    run_p.add_argument(
        "--instrument",
        nargs="+",
        default=None,
        metavar="INSTRUMENT_ID",
        help="銘柄 ID（複数指定可、例: --instrument 1301.TSE 7203.TSE）",
    )
    run_p.add_argument("--start", default=None, help="開始日 (ISO8601)")
    run_p.add_argument("--end", default=None, help="終了日 (ISO8601)")
    run_p.add_argument("--granularity", default=None, choices=["Trade", "Minute", "Daily", None])
    run_p.add_argument("--initial-cash", type=int, default=None)
    # L-GP2 (Phase 8 R1 / Phase 3): choices を _ForceMode Literal から派生させる
    # ことで DRY 違反を解消する。`_ForceMode` を 1 箇所変更すれば argparse 側も
    # 追従する不変条件。
    import typing as _typing
    run_p.add_argument(
        "--mode", choices=list(_typing.get_args(_ForceMode)), default="auto"
    )

    args = parser.parse_args()

    if args.cmd == "run":
        try:
            # F6b: SCENARIO フォールバック解決
            instrument, start, end, granularity, initial_cash = _resolve_cli_params(
                strategy_path=args.strategy,
                cli_instrument=args.instrument,
                cli_start=args.start,
                cli_end=args.end,
                cli_granularity=args.granularity,
                cli_initial_cash=args.initial_cash,
            )
            with ReplaySession(force_mode=args.mode) as s:
                s.load(instrument, start, end, granularity)
                s.run(
                    strategy_file=args.strategy,
                    on_event=lambda evt: print(json.dumps(evt, ensure_ascii=False)),
                    initial_cash=initial_cash,
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
