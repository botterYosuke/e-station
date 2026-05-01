"""TDD: depth_unavailable safety (T5, plan §T5 MEDIUM-6 / F-M12 / HIGH-D4).

depth_unavailable fires when stream_depth receives FD frames with no bid/ask
keys for _DEPTH_SAFETY_TIMEOUT_S seconds.  When bid/ask keys arrive in time,
the safety must NOT fire.

Also covers §7.1 リグレッションガード (fix-tachibana-ws-fd-not-pushing-2026-05-01):
- test_st_frame_emits_venue_error: ST フレーム受信 → p_errno 非ゼロ → VenueError
- test_depth_unavailable_warn_log: depth_unavailable 発火時に WARN ログが出る

Tests use a real websockets.serve mock server and patch _DEPTH_SAFETY_TIMEOUT_S
to a small value for speed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import websockets
import websockets.server  # type: ignore[import-untyped]

import engine.exchanges.tachibana_ws as _ws_mod
from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_session(ws_port: int) -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws=f"ws://127.0.0.1:{ws_port}/event/",
        zyoutoeki_kazei_c="",
    )


def _make_worker(tmp_path: Path) -> TachibanaWorker:
    return TachibanaWorker(cache_dir=tmp_path, is_demo=True)


def _fd_no_depth() -> bytes:
    """FD frame WITHOUT bid/ask keys (no GAP/GBP).

    フレーム形式: ^A key ^B value ... ^A (終端 ^A でフレーム境界を明示)
    """
    text = (
        "\x01p_cmd\x02FD"
        "\x01p_1_DPP\x022500"
        "\x01p_1_DV\x02100"
        "\x01p_date\x022024.01.01-09:30:00.000"
        "\x01"  # 終端マーカー
    )
    return text.encode("shift_jis")


def _fd_with_depth() -> bytes:
    """FD frame WITH bid/ask keys.

    フレーム形式: ^A key ^B value ... ^A (終端 ^A でフレーム境界を明示)
    """
    text = (
        "\x01p_cmd\x02FD"
        "\x01p_1_DPP\x022500"
        "\x01p_1_DV\x02100"
        "\x01p_1_GAP1\x022501"
        "\x01p_1_GBP1\x022499"
        "\x01p_1_GAV1\x02100"
        "\x01p_1_GBV1\x02100"
        "\x01p_date\x022024.01.01-09:30:00.000"
        "\x01"  # 終端マーカー
    )
    return text.encode("shift_jis")


# ---------------------------------------------------------------------------
# HIGH-D4: negative test — safety does NOT fire when keys arrive in time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_safety_does_not_fire_when_keys_arrive_within_30s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bid/ask keys arriving before the timeout must NOT emit depth_unavailable.

    plan §HIGH-D4: VenueError{code:"depth_unavailable"} not emitted,
    fetch_depth_snapshot call count == 0.
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.3)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        # Send FD WITHOUT depth at t=0 (initialises DV state)
        await ws.send(_fd_no_depth())
        # Wait a bit, then send FD WITH depth — before 0.3 s timeout
        await asyncio.sleep(0.1)
        await ws.send(_fd_with_depth())
        # Let the test finish
        stop.set()
        await asyncio.sleep(0.5)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-1", outbox, stop),
                timeout=3.0,
            )

    depth_errors = [
        e for e in outbox
        if e.get("event") == "VenueError" and e.get("code") == "depth_unavailable"
    ]
    assert not depth_errors, f"depth_unavailable should not fire; got: {depth_errors}"
    assert mock_snap.call_count == 0, (
        f"fetch_depth_snapshot must not be called; called {mock_snap.call_count} times"
    )


# ---------------------------------------------------------------------------
# MEDIUM-6 / F-M12: positive test — safety fires when no keys within timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_safety_fires_when_no_keys_within_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bid/ask keys within timeout → VenueError{code:'depth_unavailable'} emitted.

    plan §MEDIUM-6 / F-M12.  Poll constants are also patched so stream_depth
    returns quickly after the safety fires.
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.15)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)  # exits after ~2 polls

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        # Keep sending FD frames WITHOUT depth keys well past the timeout
        for _ in range(10):
            await ws.send(_fd_no_depth())
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-1", outbox, stop),
                timeout=5.0,
            )

    depth_errors = [
        e for e in outbox
        if e.get("event") == "VenueError" and e.get("code") == "depth_unavailable"
    ]
    assert len(depth_errors) == 1, (
        f"exactly 1 depth_unavailable expected; got {len(depth_errors)}: {depth_errors}"
    )
    assert depth_errors[0].get("venue") == "tachibana"
    assert "message" in depth_errors[0]


# ---------------------------------------------------------------------------
# §7.1 リグレッションガード — ST フレーム VenueError 伝搬（F-C）
# ---------------------------------------------------------------------------


def _st_frame(p_errno: str = "1", **extra: str) -> bytes:
    """ST フレーム（エラーコード付き）を生成する。

    末尾の `\\x01` は他のヘルパ (`_fd_no_depth` / `_fd_with_depth`) と統一した
    終端マーカー（M-E）。
    """
    parts = ["\x01p_cmd\x02ST", f"\x01p_errno\x02{p_errno}", "\x01p_status\x02test"]
    for k, v in extra.items():
        parts.append(f"\x01{k}\x02{v}")
    parts.append("\x01")  # 終端マーカー
    return "".join(parts).encode("shift_jis")


def _st_frame_no_errno(**extra: str) -> bytes:
    """`p_errno` キー欠落の ST フレーム（H-B 検証用）。"""
    parts = ["\x01p_cmd\x02ST", "\x01p_status\x02test"]
    for k, v in extra.items():
        parts.append(f"\x01{k}\x02{v}")
    parts.append("\x01")
    return "".join(parts).encode("shift_jis")


@pytest.mark.asyncio
async def test_st_frame_with_nonzero_errno_emits_venue_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ST フレームで p_errno が非ゼロの場合、VenueError が outbox に積まれる。

    §7.1 test_st_frame_emits_venue_error (fix-tachibana-ws-fd-not-pushing-2026-05-01)
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(_st_frame(p_errno="99"))
        await asyncio.sleep(1.0)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-st", outbox, stop),
                timeout=5.0,
            )

    st_errors = [
        e for e in outbox
        if e.get("event") == "VenueError" and "st_errno" in e.get("code", "")
    ]
    assert len(st_errors) >= 1, (
        f"VenueError with st_errno code expected for non-zero p_errno; got: {outbox}"
    )
    assert st_errors[0].get("venue") == "tachibana"
    assert "99" in st_errors[0].get("code", "")


@pytest.mark.asyncio
async def test_st_frame_with_zero_errno_does_not_emit_venue_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ST フレームで p_errno が '0' の場合は VenueError を outbox に積まない。

    §7.1 test_st_frame_emits_venue_error の補完テスト
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.3)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(_st_frame(p_errno="0"))
        await asyncio.sleep(1.0)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-st0", outbox, stop),
                timeout=5.0,
            )

    st_errors = [
        e for e in outbox
        if e.get("event") == "VenueError" and "st_errno" in e.get("code", "")
    ]
    assert st_errors == [], (
        f"VenueError must NOT be emitted for p_errno='0'; got: {st_errors}"
    )


# ---------------------------------------------------------------------------
# §7.1 リグレッションガード — depth_unavailable WARN ログ（§6 O1）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_unavailable_emits_warn_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """depth_unavailable 発火時に WARN レベルのログが出力される。

    §7.1 test_depth_unavailable_warn_log (§6 O1)
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.15)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        for _ in range(5):
            await ws.send(_fd_no_depth())
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with (
            patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True),
            caplog.at_level(logging.WARNING, logger="engine.exchanges.tachibana"),
        ):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-warn", outbox, stop),
                timeout=5.0,
            )

    warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    depth_warn = [m for m in warn_msgs if "depth_unavailable" in m]
    assert depth_warn, (
        f"Expected WARN log containing 'depth_unavailable'; got warn_msgs={warn_msgs}"
    )
    # M-C: frame-type counts must be included in the WARN message
    msg = depth_warn[0]
    assert "FD=" in msg, f"WARN must include FD count: {msg!r}"
    assert "ST=" in msg, f"WARN must include ST count: {msg!r}"


# ---------------------------------------------------------------------------
# §7.1 リグレッションガード — FD フレームカウントメトリクス（§6 O1）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fd_frame_count_metric_logged_after_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FD フレーム受信後、統計インターバル経過で INFO ログにカウントが出力される。

    §7.1 test_fd_frame_count_metric (§6 O1)

    H-D: 旧テストは `_FRAME_STATS_INTERVAL_S=0` で production 乖離していたため
    0.05s に変更し、フレーム間隔を 0.06s 以上空ける。
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 1.0)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.05)
    # production-like interval (still small enough for tests)
    monkeypatch.setattr(_ws_mod, "_FRAME_STATS_INTERVAL_S", 0.05)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        # 1st frame at t=0
        await ws.send(_fd_with_depth())
        # Wait past the stats interval (0.05s) so the 2nd frame triggers log
        await asyncio.sleep(0.08)
        await ws.send(_fd_with_depth())
        await asyncio.sleep(0.02)
        await ws.send(_fd_with_depth())
        stop.set()
        await asyncio.sleep(0.5)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with (
            patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True),
            caplog.at_level(logging.INFO, logger="engine.exchanges.tachibana_ws"),
        ):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-metric", outbox, stop),
                timeout=5.0,
            )

    import re

    # 統計ログが INFO で出力されたことを確認
    info_msgs = [r.message for r in caplog.records if r.levelno >= logging.INFO]
    stat_msgs = [m for m in info_msgs if "frame stats" in m and "FD=" in m]
    assert stat_msgs, (
        f"Expected INFO log containing 'frame stats' with 'FD='; got: {info_msgs}"
    )
    # FD カウントが > 0 であることを確認（3 フレーム送信済み）
    first_stat = stat_msgs[0]
    match = re.search(r"FD=(\d+)", first_stat)
    assert match and int(match.group(1)) > 0, (
        f"FD count should be > 0 in stats log: {first_stat}"
    )


# ---------------------------------------------------------------------------
# H-A: ST フレーム sUrl* 系シークレットマスク
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_st_log_masks_sUrlEventWebSocket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """ST フレームに `sUrlEventWebSocket` が含まれる場合、WARN ログでマスクされる（H-A）。"""
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(_st_frame(p_errno="0", sUrlEventWebSocket="wss://leak/"))
        await asyncio.sleep(1.0)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with (
            patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True),
            caplog.at_level(logging.WARNING, logger="engine.exchanges.tachibana"),
        ):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-mask", outbox, stop),
                timeout=5.0,
            )

    msgs = [r.getMessage() for r in caplog.records]
    leaks = [m for m in msgs if "wss://leak/" in m]
    assert not leaks, f"sUrlEventWebSocket value must be masked; leaked in: {leaks}"
    masked = [m for m in msgs if "sUrlEventWebSocket" in m and "***" in m]
    assert masked, (
        f"Expected WARN with sUrlEventWebSocket masked as ***; got msgs={msgs}"
    )


# ---------------------------------------------------------------------------
# H-B: p_errno 分岐
# ---------------------------------------------------------------------------


async def _run_st_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frame: bytes
) -> list[dict]:
    """Boilerplate: serve `frame` once, run stream_depth, return outbox."""
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)
    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(frame)
        await asyncio.sleep(1.5)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)
        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]
        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-x", outbox, stop),
                timeout=5.0,
            )
    return outbox


@pytest.mark.asyncio
async def test_st_frame_missing_p_errno_emits_st_no_errno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`p_errno` キー欠落 → code='st_no_errno' で VenueError（H-B）。"""
    outbox = await _run_st_capture(tmp_path, monkeypatch, _st_frame_no_errno())
    errs = [e for e in outbox if e.get("event") == "VenueError"]
    no_errno = [e for e in errs if e.get("code") == "st_no_errno"]
    assert no_errno, f"Expected code=st_no_errno; got {errs}"


@pytest.mark.asyncio
async def test_st_frame_p_errno_2_emits_session_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`p_errno=2` → code='st_session_expired' で polling fallback（H-B）。"""
    outbox = await _run_st_capture(tmp_path, monkeypatch, _st_frame(p_errno="2"))
    errs = [e for e in outbox if e.get("event") == "VenueError"]
    expired = [e for e in errs if e.get("code") == "st_session_expired"]
    assert expired, f"Expected code=st_session_expired for p_errno=2; got {errs}"


@pytest.mark.asyncio
async def test_st_frame_p_errno_empty_does_not_emit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`p_errno=''` は正常扱い → VenueError 出さない（H-B / SKILL.md R6）。"""
    outbox = await _run_st_capture(tmp_path, monkeypatch, _st_frame(p_errno=""))
    st_errs = [
        e for e in outbox
        if e.get("event") == "VenueError" and "st_" in str(e.get("code", ""))
    ]
    assert st_errs == [], f"VenueError must NOT be emitted for empty p_errno; got: {st_errs}"


# ---------------------------------------------------------------------------
# H-C: ST→VenueError rate limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_st_venue_error_rate_limited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ code の ST→VenueError は 30 秒に 1〜2 件までに rate-limit される（H-C）。"""
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 1.0)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        for _ in range(10):
            await ws.send(_st_frame(p_errno="99"))
            await asyncio.sleep(0.02)
        await asyncio.sleep(1.5)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-rl", outbox, stop),
                timeout=5.0,
            )

    st99 = [
        e for e in outbox
        if e.get("event") == "VenueError" and e.get("code") == "st_errno_99"
    ]
    # 10 frames sent within ~200ms; default rate limit window is 30s.
    # Should yield 1 (or at most 2 in flaky timing) — never 10.
    assert 1 <= len(st99) <= 2, (
        f"Expected 1-2 rate-limited VenueErrors; got {len(st99)}: {st99}"
    )


# ---------------------------------------------------------------------------
# H2-1: invalid ticker → VenueError + stream_depth terminates cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_depth_invalid_ticker_emits_venue_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不正 ticker（制御文字含む）は VenueError{code:invalid_ticker} を積んで即座に終了する（H2-1）。

    stream_depth が asyncio.wait_for タイムアウトを超えずに完走することも保証する。
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 1.0)
    stop = asyncio.Event()
    worker = _make_worker(tmp_path)
    # session が必要（session=None だと no_session で返る前に到達しない）
    from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl
    worker._session = TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws="ws://127.0.0.1:9/event/",  # unreachable but not used
        zyoutoeki_kazei_c="",
    )
    outbox: list[dict] = []
    with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
        await asyncio.wait_for(
            worker.stream_depth("7203\x01", "stock", "session-invalid", outbox, stop),
            timeout=2.0,
        )
    invalid = [e for e in outbox if e.get("code") == "invalid_ticker"]
    assert invalid, f"Expected VenueError{{code:invalid_ticker}}; got outbox={outbox}"


# ---------------------------------------------------------------------------
# H-2: ST rate-limit clears on each WS reconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_st_rate_limit_resets_on_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ST→VenueError rate-limit は WS 再接続ごとにリセットされる（H-2）。

    1 接続目で st_errno_99 → VenueError 1 件（rate-limit window 内なので以降は抑制）。
    2 接続目（再接続）でも st_errno_99 → VenueError がもう 1 件出ること。
    合計 2 件以上 + 各接続で少なくとも 1 件が保証される。
    """
    # Safety timeout = 3.0 s; dead-frame timeout = 0.25 s.
    # dead-frame fires first (0.25 s < 0.30 s handler close), ensuring a clean
    # reconnect backoff path and reducing flakiness on slow CI hosts.
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 3.0)
    monkeypatch.setattr(_ws_mod, "_DEAD_FRAME_TIMEOUT_S", 0.25)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)
    import engine.exchanges.tachibana as _tachi

    monkeypatch.setattr(_tachi, "_ST_VENUE_ERROR_RATE_LIMIT_S", 999.0)

    stop = asyncio.Event()
    conn_count = [0]

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        conn_count[0] += 1
        await ws.send(_st_frame(p_errno="99"))
        # Close after dead-frame window (0.30 s > _DEAD_FRAME_TIMEOUT_S=0.25 s).
        await asyncio.sleep(0.30)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)
        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-reconnect-rl", outbox, stop),
                timeout=6.0,
            )

    st99 = [e for e in outbox if e.get("event") == "VenueError" and e.get("code") == "st_errno_99"]
    assert conn_count[0] >= 2, f"Expected at least 2 WS connections; got {conn_count[0]}"
    assert len(st99) >= 2, (
        f"Expected at least 2 VenueErrors (one per reconnect); got {len(st99)}: {st99}"
    )


# ---------------------------------------------------------------------------
# H-1: polling fallback stops immediately when session expires mid-poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_fallback_emits_venue_error_on_session_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ポーリング fallback 中にセッションが None になると VenueError を積んで即座に停止する（H-1）。"""
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.3)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 5.0)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await asyncio.sleep(0.5)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)
        outbox: list[dict] = []

        poll_calls = [0]

        async def _mock_fetch(_ticker, _market):
            poll_calls[0] += 1
            if poll_calls[0] >= 2:
                # Simulate session expiry after first poll.
                worker._session = None
            return {}

        worker.fetch_depth_snapshot = _mock_fetch  # type: ignore[method-assign]

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-expiry", outbox, stop),
                timeout=5.0,
            )

    expired = [
        e for e in outbox
        if e.get("event") == "VenueError" and e.get("code") == "session_expired_during_poll"
    ]
    assert expired, f"Expected VenueError{{code:session_expired_during_poll}}; got {outbox}"
    assert poll_calls[0] >= 2, f"Expected at least 2 poll calls before detection; got {poll_calls[0]}"


# ---------------------------------------------------------------------------
# §7.1 リグレッションガード — polling interval (F-B deferred)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="F-B (立会時間帯依存 polling 周期変更) は deferred。"
    "_DEPTH_POLL_INTERVAL_S の時間帯依存切り替えが実装されたら有効化する。"
)
@pytest.mark.asyncio
async def test_polling_interval_shorter_during_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """立会時間中 fallback polling 周期は立会外より短縮される。

    §7.1 test_polling_interval_in_session (F-B: 時間帯依存 polling)
    現状は _DEPTH_POLL_INTERVAL_S = 10.0 固定のため skip。
    """
    raise NotImplementedError("F-B 未実装")
