"""MockIPCServer — in-process WebSocket fixture for IPC protocol tests.

🔵plan-test-mock-ipc-server.md S1 / S2 骨格。

設計方針:
- `asyncio` + `websockets.serve` をバックグラウンドスレッドで起動。
- ランダムポート（`port=0`）でバインドし、`.port` プロパティで実ポートを返す。
- **`compression=None` 必須**。省略すると `permessage-deflate` がデフォルトになり
  RSV1=1 フレームを送出するため fastwebsockets（Rust IPC client）が
  "Reserved bits are not zero" で切断する（MISSES.md 既知バグ）。
- `script` 形式: `[{"on": "Hello", "reply": [{"event": "Ready", ...}], ...]`。
  クライアントから届く各 op を順に処理し、対応する `reply` を順次送出する。
- スクリプトに無い op が届いた場合は無視する（呼び出し側がアサート責任）。
- `stop()` は冪等。複数回呼んでも例外を出さない。

S1 完了条件:
    pytest python/tests/test_mock_ipc_server_basic.py が 1 秒以内に PASS。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

log = logging.getLogger(__name__)


@dataclass
class _ScriptStep:
    on: str
    reply: list[dict[str, Any]]


def _normalize_script(script: Iterable[dict[str, Any]]) -> list[_ScriptStep]:
    steps: list[_ScriptStep] = []
    for s in script:
        on = s.get("on")
        if not isinstance(on, str):
            raise ValueError(f"script step missing 'on' (str): {s!r}")
        reply = s.get("reply", [])
        if not isinstance(reply, list):
            raise ValueError(f"script step 'reply' must be list: {s!r}")
        steps.append(_ScriptStep(on=on, reply=list(reply)))
    return steps


def _default_ready_payload(*, engine_session_id: str | None = None) -> dict[str, Any]:
    """`ReadyResponse` 相当の最小ペイロード（schemas.py の Literal を尊重）。"""
    return {
        "event": "Ready",
        "schema_major": SCHEMA_MAJOR,
        "schema_minor": SCHEMA_MINOR,
        "engine_version": "mock",
        "engine_session_id": engine_session_id or str(uuid.uuid4()),
        "capabilities": {},
    }


def _default_handshake_script() -> list[_ScriptStep]:
    """S1 default: Hello → Ready のみ。"""
    return [_ScriptStep(on="Hello", reply=[_default_ready_payload()])]


class MockIPCServer:
    """in-process WebSocket IPC server for tests.

    Usage:
        with MockIPCServer() as srv:
            ws_url = f"ws://127.0.0.1:{srv.port}/"
            os.environ["FLOWSURFACE_ENGINE_TOKEN"] = srv.token
            ...

    `script=None` のとき S1 の既定ハンドシェイク（Hello → Ready）のみを返す。
    """

    def __init__(
        self,
        script: Iterable[dict[str, Any]] | None = None,
        *,
        token: str = "test-token",
        host: str = "127.0.0.1",
    ) -> None:
        self._steps: list[_ScriptStep] = (
            _normalize_script(script) if script is not None else _default_handshake_script()
        )
        self._token = token
        self._host = host
        self._port: int | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        # 受信した op を順番に保持（テストアサート用）。
        self.received: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # public properties
    # ------------------------------------------------------------------

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("MockIPCServer not started — call start() first")
        return self._port

    @property
    def token(self) -> str:
        return self._token

    @property
    def url(self) -> str:
        return f"ws://{self._host}:{self.port}/"

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self, *, timeout_s: float = 2.0) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="MockIPCServer", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout_s):
            raise RuntimeError("MockIPCServer failed to start within timeout")

    def stop(self) -> None:
        """Idempotent stop. 複数回呼んでも例外なし。"""
        if self._stopped.is_set():
            return
        self._stopped.set()
        loop = self._loop
        server = self._server
        if loop is not None and server is not None:
            try:
                loop.call_soon_threadsafe(server.close)
            except RuntimeError:
                # loop already closed
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._loop = None
        self._server = None

    def __enter__(self) -> "MockIPCServer":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._async_main())
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    async def _async_main(self) -> None:
        # NOTE: compression=None は必須（RSV1 バグ回避）。
        server = await websockets.serve(
            self._handler,
            self._host,
            0,
            compression=None,
        )
        sockets = list(server.sockets or [])
        if not sockets:
            raise RuntimeError("MockIPCServer: no bound socket")
        self._port = sockets[0].getsockname()[1]
        self._server = server
        self._ready.set()
        try:
            await server.wait_closed()
        except asyncio.CancelledError:
            pass

    async def _handler(self, ws: Any) -> None:
        # script を index でなめる: クライアントから届いた op に対応するスクリプトを
        # 順番にマッチして reply を送出する。マッチしない op は無視（pytest 側で
        # received リストを見てアサートする）。
        step_idx = 0
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    text = raw.decode("utf-8")
                else:
                    text = raw
                msg: dict[str, Any] = json.loads(text)
                self.received.append(msg)

                op = msg.get("op")
                # token mismatch をテストしたい場合: script 側で Hello に
                # `EngineError` を仕込めば良い。token のサーバ側チェックは
                # 省略する（mock の振る舞いは script に委ねる）。
                if step_idx >= len(self._steps):
                    continue
                expected = self._steps[step_idx]
                if op != expected.on:
                    # 期待外。テストに任せる（received に残っている）。
                    continue
                step_idx += 1
                for reply in expected.reply:
                    await ws.send(json.dumps(reply))
        except websockets.ConnectionClosed:
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("MockIPCServer handler error: %s", exc)


__all__ = ["MockIPCServer"]
