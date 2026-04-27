"""TDD: TachibanaEventWs — Shift-JIS decode, ST frame, depth_unavailable (T5).

Uses a local websockets.serve mock server so no real Tachibana credentials
are needed.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import pytest
import websockets.server  # type: ignore[import-untyped]
import websockets  # type: ignore[import-untyped]

from engine.exchanges.tachibana_ws import FdFrameProcessor, TachibanaEventWs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_fd_frame(row: str = "1", dpp: str = "2500", dv: str = "100") -> bytes:
    """Build a minimal FD frame as Shift-JIS bytes."""
    text = (
        f"\x01p_cmd\x02FD"
        f"\x01p_{row}_DPP\x02{dpp}"
        f"\x01p_{row}_DV\x02{dv}"
        f"\x01p_{row}_GAP1\x02{int(dpp)+1}"
        f"\x01p_{row}_GBP1\x02{int(dpp)-1}"
        f"\x01p_{row}_GAV1\x02100"
        f"\x01p_{row}_GBV1\x02100"
        f"\x01p_date\x022024.01.01-09:30:00.000"
    )
    return text.encode("shift_jis")


def _kp_frame_bytes() -> bytes:
    return "\x01p_cmd\x02KP".encode("shift_jis")


def _st_frame_bytes(result_code: str = "1") -> bytes:
    text = f"\x01p_cmd\x02ST\x01sResultCode\x02{result_code}"
    return text.encode("shift_jis")


def _kanji_fd_frame_bytes() -> bytes:
    """FD frame containing Japanese kanji in a field value (HIGH-C3-1)."""
    text = (
        "\x01p_cmd\x02FD"
        "\x01p_1_DPP\x022500"
        "\x01p_1_DV\x02100"
        "\x01p_1_name\x02株式会社テスト"  # kanji + kana
        "\x01p_date\x022024.01.01-09:30:00.000"
    )
    # Encode as Shift-JIS — some kanji require multi-byte sequences
    return text.encode("shift_jis")


# ---------------------------------------------------------------------------
# WebSocket mock-server fixture
# ---------------------------------------------------------------------------


async def _serve_frames(
    frames: list[bytes],
    *,
    close_after: bool = True,
) -> AsyncGenerator[str, None]:
    """Async generator yielding ws URIs; server sends ``frames`` then closes."""
    received: list[tuple[str, bytes]] = []

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        for f in frames:
            await ws.send(f)
            await asyncio.sleep(0)
        if close_after:
            await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestShiftJisDecode:
    @pytest.mark.asyncio
    async def test_kanji_in_fd_frame_is_not_garbled(self) -> None:
        """Bytes payload decoded as Shift-JIS; kanji survives intact (HIGH-C3-1)."""
        stop = asyncio.Event()
        collected: list[tuple[str, dict, int]] = []

        async def _cb(frame_type: str, fields: dict, ts: int) -> None:
            collected.append((frame_type, fields, ts))
            stop.set()

        frame_bytes = _kanji_fd_frame_bytes()

        async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
            await ws.send(frame_bytes)
            await ws.close()

        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            ws_client = TachibanaEventWs(url, stop, ticker="7203")
            await asyncio.wait_for(ws_client.run(_cb), timeout=3.0)

        assert len(collected) >= 1
        frame_type, fields, _ = collected[0]
        assert frame_type == "FD"
        assert "株式会社テスト" in fields.get("p_1_name", "")

    @pytest.mark.asyncio
    async def test_fd_frame_triggers_callback(self) -> None:
        """An FD frame results in a callback with type='FD'."""
        stop = asyncio.Event()
        collected: list[str] = []

        async def _cb(frame_type: str, fields: dict, ts: int) -> None:
            collected.append(frame_type)
            stop.set()

        async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
            await ws.send(_encode_fd_frame())
            await ws.close()

        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ws_client = TachibanaEventWs(
                f"ws://127.0.0.1:{port}", stop, ticker="7203"
            )
            await asyncio.wait_for(ws_client.run(_cb), timeout=3.0)

        assert "FD" in collected


class TestStFrame:
    @pytest.mark.asyncio
    async def test_st_frame_nonzero_triggers_callback(self) -> None:
        """ST frame is forwarded to callback as type='ST'."""
        stop = asyncio.Event()
        received: list[tuple[str, dict]] = []

        async def _cb(frame_type: str, fields: dict, ts: int) -> None:
            received.append((frame_type, fields))
            stop.set()

        async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
            await ws.send(_st_frame_bytes("1"))
            await ws.close()

        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ws_client = TachibanaEventWs(
                f"ws://127.0.0.1:{port}", stop, ticker="7203"
            )
            await asyncio.wait_for(ws_client.run(_cb), timeout=3.0)

        assert any(ft == "ST" for ft, _ in received)

    @pytest.mark.asyncio
    async def test_st_zero_result_does_not_stop_callback(self) -> None:
        """ST with sResultCode=0 (info) must not stop; callback still called.
        (MEDIUM-D6: information-level ST must not halt subscriptions)
        """
        stop = asyncio.Event()
        received_types: list[str] = []

        async def _cb(frame_type: str, fields: dict, ts: int) -> None:
            received_types.append(frame_type)
            if len(received_types) >= 2:
                stop.set()

        async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
            await ws.send(_st_frame_bytes("0"))   # info ST
            await ws.send(_encode_fd_frame())     # FD follows
            await ws.close()

        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ws_client = TachibanaEventWs(
                f"ws://127.0.0.1:{port}", stop, ticker="7203"
            )
            await asyncio.wait_for(ws_client.run(_cb), timeout=3.0)

        assert "ST" in received_types
        assert "FD" in received_types


class TestKpFrame:
    @pytest.mark.asyncio
    async def test_kp_frame_triggers_callback(self) -> None:
        """KP keepalive frames must be forwarded to the callback."""
        stop = asyncio.Event()
        received: list[str] = []

        async def _cb(frame_type: str, fields: dict, ts: int) -> None:
            received.append(frame_type)
            stop.set()

        async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
            await ws.send(_kp_frame_bytes())
            await ws.close()

        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ws_client = TachibanaEventWs(
                f"ws://127.0.0.1:{port}", stop, ticker="7203"
            )
            await asyncio.wait_for(ws_client.run(_cb), timeout=3.0)

        assert "KP" in received


class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnects_after_server_closes(self) -> None:
        """After the server closes, TachibanaEventWs reconnects and delivers frames."""
        conn_count = 0
        stop = asyncio.Event()
        received_fds: list[int] = []

        async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
            nonlocal conn_count
            conn_count += 1
            await ws.send(_encode_fd_frame(dv=str(conn_count * 100)))
            if conn_count == 1:
                # First connection: close immediately to force reconnect
                await ws.close()
            else:
                # Second connection: stop the test
                stop.set()
                await ws.close()

        async def _cb(frame_type: str, fields: dict, ts: int) -> None:
            if frame_type == "FD":
                received_fds.append(conn_count)

        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ws_client = TachibanaEventWs(
                f"ws://127.0.0.1:{port}", stop, ticker="7203"
            )
            await asyncio.wait_for(ws_client.run(_cb), timeout=5.0)

        assert conn_count == 2
        assert len(received_fds) == 2


class TestReceivedFrameFieldName:
    """Regression for: received frames use p_cmd (not p_evt_cmd) as the command key.

    The EVENT WebSocket subscription URL uses the *parameter* ``p_evt_cmd=ST,KP,FD``
    to declare which events to receive.  The *received* frames, however, carry the
    command in a field named ``p_cmd`` (e.g. ``p_cmd\x02FD``).  These are two
    different names for two different roles: request vs. response.

    Fix: ``TachibanaEventWs._recv_loop`` reads ``fields.get("p_cmd")`` not
    ``fields.get("p_evt_cmd")``.  Reverting that change would make this test FAIL.
    """

    @pytest.mark.asyncio
    async def test_frame_with_p_cmd_key_triggers_fd_callback(self) -> None:
        """A frame containing ``p_cmd=FD`` (real server format) must fire the FD callback."""
        stop = asyncio.Event()
        collected: list[str] = []

        async def _cb(frame_type: str, fields: dict, ts: int) -> None:
            collected.append(frame_type)
            stop.set()

        # Real server format: command key is p_cmd, NOT p_evt_cmd
        frame = (
            "\x01p_cmd\x02FD"
            "\x01p_1_DPP\x023000"
            "\x01p_1_DV\x02500"
            "\x01p_date\x022025.01.01-09:30:00.000"
        ).encode("shift_jis")

        async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
            await ws.send(frame)
            await ws.close()

        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ws_client = TachibanaEventWs(f"ws://127.0.0.1:{port}", stop, ticker="7203")
            await asyncio.wait_for(ws_client.run(_cb), timeout=3.0)

        assert "FD" in collected, (
            "FD callback was not triggered.\n"
            "Fix: TachibanaEventWs._recv_loop must read fields.get('p_cmd'), "
            "not fields.get('p_evt_cmd'). The received frame field is 'p_cmd'; "
            "'p_evt_cmd' is only the subscription URL parameter."
        )

    @pytest.mark.asyncio
    async def test_frame_with_p_evt_cmd_key_does_not_trigger_callback(self) -> None:
        """A frame using the wrong key ``p_evt_cmd`` must NOT fire any callback.

        The server sends two frames in sequence:
        1. A bad frame with ``p_evt_cmd=FD`` — must be ignored.
        2. A known-good ``p_cmd=KP`` frame — confirms the dispatch loop ran.

        Asserting that KP was received proves the loop processed both frames;
        asserting that FD was not received proves the wrong key is truly ignored
        (not silently dropped by an exception before dispatch).
        """
        stop = asyncio.Event()
        collected: list[str] = []

        async def _cb(frame_type: str, fields: dict, ts: int) -> None:
            collected.append(frame_type)
            if frame_type == "KP":
                stop.set()

        bad_frame = (
            "\x01p_evt_cmd\x02FD"
            "\x01p_1_DPP\x023000"
            "\x01p_1_DV\x02500"
        ).encode("shift_jis")
        kp_frame = "\x01p_cmd\x02KP".encode("shift_jis")

        async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
            await ws.send(bad_frame)
            await ws.send(kp_frame)
            await asyncio.sleep(0.5)
            await ws.close()

        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ws_client = TachibanaEventWs(f"ws://127.0.0.1:{port}", stop, ticker="7203")
            await asyncio.wait_for(ws_client.run(_cb), timeout=3.0)

        assert "KP" in collected, (
            "KP frame not received — dispatch loop may not have run at all. "
            "Cannot conclude that the bad key was correctly ignored."
        )
        assert "FD" not in collected, (
            "FD callback was triggered by a frame with 'p_evt_cmd' key.\n"
            "The implementation must only respond to 'p_cmd', not 'p_evt_cmd'."
        )
