"""issue #42 Phase 1: NautilusRunner.start_live() の is_market_open() ガード。

統一決定 #5 (R5-HIGH-3) で確定:
- ``start_live()`` 冒頭で ``is_market_open()`` を明示チェックし、閉場時は
  ``EngineError{code:"market_closed"}`` を emit して abort する
  （exec_client / TradingNode 構築前）。
- これが live strategy 起動の authoritative reject 点となる。
- CLI / GUI は事前 hint のみ（authoritative ではない）。

このテストは market closed を monkeypatch で強制し、abort 経路を検証する。
TradingNode / TachibanaLiveExecutionClient が **構築されない** ことも確認する
（市場閉場で reject されるため、副作用ゼロが invariant）。
"""

from __future__ import annotations

import queue as _stdlib_queue
import threading

import pytest

from engine.nautilus.engine_runner import NautilusRunner


def _make_runner_args(on_event):
    """start_live() に渡す引数 dict を作る。session/queue は dummy。"""
    return dict(
        instrument_id="8306.T",
        strategy_file=None,  # market_closed は strategy 解決前に return するため None で OK
        strategy_init_kwargs=None,
        max_qty=100,
        max_notional_jpy=500_000,
        second_password="test-second-pw",
        session=object(),  # 構築は走らないので dummy で OK
        fd_queue=_stdlib_queue.Queue(maxsize=10),
        ec_queue=_stdlib_queue.Queue(maxsize=10),
        on_event=on_event,
        stop_event=threading.Event(),
        strategy_id="test-live-strategy",
    )


class TestStartLiveRejectsWhenMarketClosed:
    """統一決定 #5 / R5-HIGH-3: 市場閉場時に start_live() は abort する。"""

    def test_start_live_rejects_when_market_closed(self, monkeypatch) -> None:
        """is_market_open=False → EngineError{code:"market_closed"} emit + 即 return。

        TradingNode / TachibanaLiveExecutionClient は **構築されない**。
        """
        # is_market_open を False に固定する（engine_runner が import している場所を上書き）。
        monkeypatch.setattr(
            "engine.nautilus.engine_runner.is_market_open",
            lambda *_args, **_kwargs: False,
        )

        # 構築されたら即失敗するセンチネル — もし構築されたらこの fixture が trigger される。
        sentinel_called: list[str] = []

        def _fail_if_constructed(*_args, **_kwargs):
            sentinel_called.append("constructed")
            raise AssertionError(
                "TachibanaLiveExecutionClient must NOT be constructed when market is closed"
            )

        monkeypatch.setattr(
            "engine.nautilus.clients.tachibana.TachibanaLiveExecutionClient",
            _fail_if_constructed,
        )

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        # 1) EngineError{code:"market_closed"} が emit されたか
        market_closed_events = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "market_closed"
        ]
        assert len(market_closed_events) == 1, (
            f"expected 1 EngineError(code='market_closed'), got events={events!r}"
        )

        # 2) EngineStarted は emit されていない（reject 経路では出さない）
        engine_started = [e for e in events if e.get("event") == "EngineStarted"]
        assert engine_started == [], (
            f"EngineStarted must NOT be emitted on market_closed reject, got {engine_started!r}"
        )

        # 3) LiveStrategyReady も emit されない
        ready = [e for e in events if e.get("event") == "LiveStrategyReady"]
        assert ready == [], (
            f"LiveStrategyReady must NOT be emitted on market_closed reject, got {ready!r}"
        )

        # 4) 副作用ゼロ invariant: exec_client は構築されていない
        assert sentinel_called == [], (
            "TachibanaLiveExecutionClient was constructed despite market_closed — "
            "is_market_open() check must fire BEFORE client construction"
        )

    def test_start_live_includes_strategy_id_in_market_closed_error(self, monkeypatch) -> None:
        """EngineError には strategy_id が付くこと（schemas.EngineError 契約）。"""
        monkeypatch.setattr(
            "engine.nautilus.engine_runner.is_market_open",
            lambda *_args, **_kwargs: False,
        )

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        market_closed = next(
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "market_closed"
        )
        assert market_closed.get("strategy_id") == "test-live-strategy", (
            f"strategy_id must be propagated to EngineError, got {market_closed!r}"
        )
