"""issue #42 Phase 1: server.py concurrent live ガード（venue 単位）。

統一決定 #13 (R3-H2 + R5-MED-1):

- 既存 ``_engine_tasks[strategy_id]`` ガードはそのまま残す
  → 同一 strategy_id が走行中なら ``Error{code:"engine_already_running"}``。
- **追加**: 同一 venue で別 strategy_id の Live engine が走行中なら
  ``EngineBusy{current_state:"TRADING", attempted_command:"StartEngine",
  reason:..., venue:..., busy_kind:"another_strategy_on_venue", request_id}``
  を emit + return（``_active_live_venues: set[str]`` で追跡）。
- どちらが優先か: ``engine_already_running`` を **先**に判定する
  （同一 strategy_id 連投は単純な再投稿エラーとして扱い、概念的にも
  「同じ strategy が走っているから busy」より具体的な指摘）。
"""

from __future__ import annotations

import queue as _stdlib_queue
import threading
from collections import deque
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class _ListOutbox:
    """_Outbox と duck-type 互換のテスト用スタブ。"""

    def __init__(self) -> None:
        self._q: deque = deque()

    def append(self, item: object) -> None:
        self._q.append(item)

    def send_to(self, ws: object, item: object) -> None:
        self._q.append(item)

    def popleft(self) -> object:
        return self._q.popleft()

    def count(self) -> int:
        return 0

    def __len__(self) -> int:
        return len(self._q)

    def __bool__(self) -> bool:
        return bool(self._q)

    def __iter__(self):
        return iter(list(self._q))


def _make_server():
    """live mode の最小構成 DataEngineServer を作る。"""
    from engine.server import DataEngineServer, ReplayState, LiveState
    from engine.nautilus.portfolio_view import PortfolioView

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _ListOutbox()
    server._mode = "live"
    server._workers = {"tachibana": MagicMock()}
    server._tachibana_session = None
    server._tachibana_p_no_counter = MagicMock()
    server._session_holder = MagicMock()
    server._session_holder.get_password.return_value = "test-password"
    server._engine_tasks = {}
    server._engine_stop_events = {}
    server._replay_speed_multiplier = 1
    server._replay_portfolio = PortfolioView(Decimal("1000000"))
    server._replay_strategy_id = ""
    server._cache_dir = Path("/tmp/test-engine-cache")
    server._replay_streaming_fills = []
    server._replay_state = ReplayState.IDLE
    server._live_state = LiveState.CONNECTED  # live: CONNECTED から StartEngine 受理
    server._connected_venue = "tachibana"
    server._event_task = None
    server._replay_session_epoch = 0
    server._live_fd_queue = _stdlib_queue.Queue(maxsize=10_000)
    server._live_ec_queue = _stdlib_queue.Queue(maxsize=1_000)
    # Phase 1: venue 単位 concurrent ガード用 set。
    # __init__ が patch されているので明示的に初期化する。
    server._active_live_venues = set()
    return server


def _events_of(server, kind: str) -> list[dict]:
    return [e for e in server._outbox if e.get("event") == kind]


def _start_engine_msg(strategy_id: str, request_id: str) -> dict:
    return {
        "op": "StartEngine",
        "request_id": request_id,
        "engine": "Live",
        "strategy_id": strategy_id,
        "config": {
            "instrument_id": "8306.T",
            "max_qty": 100,
            "max_notional_jpy": 500_000,
            "strategy_file": "dummy.py",
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestActiveLiveVenuesBlocksSecondStartOnSameVenue:
    """同 venue で別 strategy_id を投入 → EngineBusy(busy_kind=another_strategy_on_venue)."""

    @pytest.mark.asyncio
    async def test_active_live_venues_blocks_second_start_on_same_venue(self) -> None:
        """既に tachibana で live engine 走行中 → 別 strategy_id でも EngineBusy emit。"""
        server = _make_server()

        # 先行 strategy が tachibana で走行中の状態を作る。
        server._engine_tasks["existing-strategy"] = MagicMock()
        server._active_live_venues.add("tachibana")

        msg = _start_engine_msg("new-strategy", "req-busy-1")
        await server._handle_start_engine(msg)

        busy = _events_of(server, "EngineBusy")
        assert len(busy) == 1, (
            f"expected 1 EngineBusy for venue concurrency, got events={list(server._outbox)!r}"
        )
        evt = busy[0]
        assert evt["current_state"] == "TRADING", evt
        assert evt["attempted_command"] == "StartEngine", evt
        assert evt["venue"] == "tachibana", evt
        assert evt["busy_kind"] == "another_strategy_on_venue", evt
        assert evt["request_id"] == "req-busy-1", evt

        # 既存 task を上書きしていないこと
        assert "new-strategy" not in server._engine_tasks


class TestEngineAlreadyRunningTakesPrecedence:
    """同一 strategy_id 重複は engine_already_running が venue ガードより **先**。"""

    @pytest.mark.asyncio
    async def test_engine_already_running_takes_precedence(self) -> None:
        """同一 strategy_id + 同一 venue → engine_already_running（EngineBusy ではない）。"""
        server = _make_server()

        # 同じ strategy_id を _engine_tasks に登録
        server._engine_tasks["dup-strategy"] = MagicMock()
        server._active_live_venues.add("tachibana")

        msg = _start_engine_msg("dup-strategy", "req-dup-1")
        await server._handle_start_engine(msg)

        # Error{code:"engine_already_running"} が出る（EngineBusy ではない）
        errors = [
            e for e in server._outbox
            if e.get("event") == "Error" and e.get("code") == "engine_already_running"
        ]
        assert len(errors) == 1, (
            f"expected 1 Error(code='engine_already_running'), got events={list(server._outbox)!r}"
        )
        # EngineBusy(busy_kind=another_strategy_on_venue) は **emit されない**
        venue_busy = [
            e for e in _events_of(server, "EngineBusy")
            if e.get("busy_kind") == "another_strategy_on_venue"
        ]
        assert venue_busy == [], (
            "engine_already_running must take precedence over venue concurrency guard"
        )


class TestVenueAddedOnSuccessfulStart:
    """正常起動時に venue が _active_live_venues に追加される（finally で discard）。"""

    @pytest.mark.asyncio
    async def test_venue_added_during_run_and_discarded_after(self) -> None:
        """start_live が成功して終了 → finally で venue が _active_live_venues から消える。"""
        server = _make_server()

        # 走行中の venue を確認するための観測リスト
        observed_during_run: list[set[str]] = []

        def fake_start_live(self_runner, **kwargs):
            # 実行中はこの venue が登録されているはず
            observed_during_run.append(set(server._active_live_venues))
            on_event = kwargs.get("on_event")
            if on_event:
                strategy_id = kwargs.get("strategy_id", "")
                on_event({"event": "EngineStarted", "strategy_id": strategy_id,
                          "account_id": "tachibana", "ts_event_ms": 0})
                on_event({"event": "EngineStopped", "strategy_id": strategy_id,
                          "final_equity": "1000000", "ts_event_ms": 0})

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            fake_start_live,
        ):
            msg = _start_engine_msg("ok-strategy", "req-ok-1")
            await server._handle_start_engine(msg)

        # 走行中に venue が登録されていた
        assert observed_during_run, "fake_start_live was not called"
        assert observed_during_run[0] == {"tachibana"}, (
            f"expected _active_live_venues={{'tachibana'}} during start_live, got {observed_during_run[0]!r}"
        )

        # finally で venue が discard されている
        assert "tachibana" not in server._active_live_venues, (
            f"venue must be discarded from _active_live_venues after run, got {server._active_live_venues!r}"
        )


class TestVenueDiscardedOnWarmUpFailure:
    """warm_up 失敗で start_live が EngineError emit + early-return 経路でも venue が消える。"""

    @pytest.mark.asyncio
    async def test_venue_discarded_when_start_live_fails_early(self) -> None:
        """start_live が EngineError 経由で early-return しても venue は cleanup される。"""
        server = _make_server()

        def fake_start_live(self_runner, **kwargs):
            on_event = kwargs.get("on_event")
            if on_event:
                # warm_up_failed で early-return（EngineStarted を出さない）
                on_event({"event": "EngineError", "code": "warm_up_failed",
                          "message": "simulated", "strategy_id": kwargs.get("strategy_id", "")})

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            fake_start_live,
        ):
            msg = _start_engine_msg("fail-strategy", "req-fail-1")
            await server._handle_start_engine(msg)

        # finally で venue が discard されている
        assert "tachibana" not in server._active_live_venues, (
            f"_active_live_venues must be cleaned up even when start_live fails early, "
            f"got {server._active_live_venues!r}"
        )


# ---------------------------------------------------------------------------
# R2-A H6: CancelledError / SIGTERM 経路でも _active_live_venues が discard される
# ---------------------------------------------------------------------------


class TestActiveLiveVenuesDiscardedOnCancellation:
    """H6: ``asyncio.to_thread(_run)`` が CancelledError で中断された経路でも
    ``_active_live_venues`` が cleanup される（finally の discard で保証）。

    旧実装: ``finally`` で discard する作りだったが、CancelledError 伝播の
    タイミングによっては venue が discard されないリスクがあった
    （review 指摘 H6: "CancelledError 伝播時に到達しない可能性"）。
    新実装: 二重防御（``except asyncio.CancelledError`` 経路でも discard +
    ``finally`` の discard）で冪等に確実 cleanup する。
    """

    @pytest.mark.asyncio
    async def test_active_live_venues_discarded_on_cancellation(self) -> None:
        """``to_thread(_run)`` を CancelledError で中断 → venue が discard される。"""
        import asyncio

        server = _make_server()

        # asyncio.wait_for の内部で raise させるため、to_thread を patch する。
        async def fake_to_thread(_fn, *_args, **_kwargs):
            # SIGTERM / cancel 経路を模擬
            raise asyncio.CancelledError()

        # エンジンランナー本体は呼ばないので patch して安全に
        def fake_start_live(self_runner, **kwargs):
            return None

        with patch("asyncio.to_thread", fake_to_thread), patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            fake_start_live,
        ):
            msg = _start_engine_msg("cancel-strategy", "req-cancel-1")
            # CancelledError は server 内部で raise → ハンドラ自体は再 raise する
            with pytest.raises(asyncio.CancelledError):
                await server._handle_start_engine(msg)

        # CancelledError 経路でも venue が discard されている
        assert "tachibana" not in server._active_live_venues, (
            "_active_live_venues must be discarded on CancelledError "
            f"(SIGTERM 経路の cleanup 失敗), got {server._active_live_venues!r}"
        )


# ---------------------------------------------------------------------------
# R4 R3-SILENT-6: _connected_venue 未設定時の hardcode fallback 撤廃
# ---------------------------------------------------------------------------


class TestVenueNotConnectedRejectsLiveStart:
    """R3-SILENT-6: ``_connected_venue`` が未設定なら StartEngine(Live) を reject する。

    旧実装は ``current_venue = getattr(self, "_connected_venue", None) or "tachibana"``
    の hardcode fallback で「未接続なのに tachibana として動こうとして失敗する」
    silent failure を抱えていた。venue 未接続なら ``Error{code:"venue_not_connected"}``
    を emit + early return することで Rust 側に正しく状態を伝える。
    """

    @pytest.mark.asyncio
    async def test_venue_not_connected_rejects_live_start(self) -> None:
        """``_connected_venue`` が None なら StartEngine(Live) は reject される。"""
        server = _make_server()
        # 既存 fixture は ``_connected_venue = "tachibana"`` を設定済み → reset
        server._connected_venue = None

        msg = _start_engine_msg("orphan-strategy", "req-orphan-1")
        await server._handle_start_engine(msg)

        # Error{code:"venue_not_connected"} が emit される
        errors = [
            e for e in server._outbox
            if e.get("event") == "Error" and e.get("code") == "venue_not_connected"
        ]
        assert len(errors) == 1, (
            f"expected 1 Error(venue_not_connected), got events={list(server._outbox)!r}"
        )
        assert errors[0]["request_id"] == "req-orphan-1", errors[0]

        # tachibana hardcode fallback が走っていないこと
        assert "tachibana" not in server._active_live_venues, (
            "hardcode fallback to 'tachibana' must be removed: "
            f"_active_live_venues={server._active_live_venues!r}"
        )
        # 起動経路にも進まない (engine_tasks に登録されていない)
        assert "orphan-strategy" not in server._engine_tasks
