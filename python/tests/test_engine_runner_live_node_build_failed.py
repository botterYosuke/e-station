"""issue #42 R4 R3-SILENT-2: node.build() failure message scrub.

R3 sanity sweep で発見された HIGH:
- ``node.build()`` 失敗時に emit する ``EngineError{node_build_failed}`` の
  ``message`` フィールドが ``str(build_exc)`` そのままになっており、venue API の
  クレデンシャル / セッション断片が wire に乗る恐れがあった。
  ``_scrub_credential_exception`` 経由で scrub する。

R3-SILENT-5 (kabu_station kernel unavailable 経路) は
``test_engine_runner_live_kabu_kernel_unavailable.py`` で別途 pin する。
"""
from __future__ import annotations

import queue as _stdlib_queue
import threading

from engine.nautilus.engine_runner import NautilusRunner


# ---------------------------------------------------------------------------
# R3-SILENT-2: node_build_failed message scrub
# ---------------------------------------------------------------------------


class _FakeTachibanaCredentialError(Exception):
    """credential 関連 type 名を持つ模擬例外 (Tachibana prefix)。"""


class _FakeExecClient:
    """warm_up 成功 + close 実装の最小 fake。"""

    instances: list["_FakeExecClient"] = []

    def __init__(self, **_kwargs) -> None:
        self.close_called = False
        self.id = "fake-exec-client"
        type(self).instances.append(self)

    async def warm_up(self) -> bool:
        return True

    async def close(self) -> None:
        self.close_called = True


def _patch_tachibana_deps_with_build_failure(monkeypatch, build_exc: BaseException) -> None:
    """tachibana 経路で warm_up 成功 → node.build() で指定例外を raise する fake セット。"""
    monkeypatch.setattr(
        "engine.nautilus.engine_runner.is_market_open",
        lambda *_a, **_kw: True,
    )
    _FakeExecClient.instances = []
    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana.TachibanaLiveExecutionClient",
        lambda *a, **kw: _FakeExecClient(**kw),
    )

    class _FakeDataClient:
        def __init__(self, *_a, **_kw) -> None:
            self.id = "fake-data-client"

    class _FakeOrderIdMap:
        def __init__(self, *_a, **_kw) -> None:
            pass

    class _FakeEventBridge:
        def __init__(self, *_a, **_kw) -> None:
            self._loop = None

    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana_data.TachibanaLiveDataClient",
        _FakeDataClient,
    )
    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana_event_bridge.OrderIdMap",
        _FakeOrderIdMap,
    )
    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana_event_bridge.TachibanaEventBridge",
        _FakeEventBridge,
    )

    class _FakeNode:
        def __init__(self, *_a, **_kw) -> None:
            # R8 HIGH-2 / R4 Group F: canonical kernel surface のみ
            # (real TradingNode 準拠)。underscore intermediate を残さず
            # `data_engine` / `exec_engine` を class attr に直接代入する。
            class _Kernel:
                loop = None
                msgbus = None
                cache = None
                clock = None
                data_engine = type("DE", (), {"register_client": lambda *a, **k: None})()
                exec_engine = type("EE", (), {"register_client": lambda *a, **k: None})()

            self.kernel = _Kernel()

        def add_data(self, *_a, **_kw) -> None:
            pass

        def add_strategies(self, *_a, **_kw) -> None:
            pass

        def build(self) -> None:
            raise build_exc

    monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _FakeNode)
    monkeypatch.setattr(
        "engine.nautilus.instrument_factory.make_equity_instrument",
        lambda *_a, **_kw: object(),
    )


def _make_runner_args(on_event):
    return dict(
        instrument_id="8306.T",
        strategy_file=None,
        strategy_init_kwargs=None,
        max_qty=100,
        max_notional_jpy=500_000,
        second_password="test-second-pw",
        session=object(),
        fd_queue=_stdlib_queue.Queue(maxsize=10),
        ec_queue=_stdlib_queue.Queue(maxsize=10),
        on_event=on_event,
        stop_event=threading.Event(),
        strategy_id="test-live-strategy",
    )


class TestNodeBuildFailedMessageScrubbed:
    """R3-SILENT-2: ``node.build()`` 失敗時の message を scrub する。"""

    def test_credential_typed_build_failure_message_scrubbed(self, monkeypatch) -> None:
        """venue prefix を持つ例外なら ``EngineError{node_build_failed}`` の
        message は wire / GUI に流れる前に scrub される。
        """
        exc = _FakeTachibanaCredentialError(
            "TradingNode build failed: token=eyJSECRET pass=hunter2"
        )
        _patch_tachibana_deps_with_build_failure(monkeypatch, exc)

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        build_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "node_build_failed"
        ]
        assert len(build_failed) == 1, (
            f"expected exactly 1 EngineError(node_build_failed), got: {events!r}"
        )
        msg = build_failed[0]["message"]
        # 型名は残す（診断性のため）
        assert "FakeTachibanaCredentialError" in msg, (
            f"type name must be exposed: {msg!r}"
        )
        # credential 値は wire に流さない
        assert "hunter2" not in msg, (
            f"credential value leaked through node_build_failed wire: {msg!r}"
        )
        assert "eyJSECRET" not in msg, (
            f"token value leaked through node_build_failed wire: {msg!r}"
        )

    def test_unrelated_runtimeerror_build_failure_message_preserved(
        self, monkeypatch
    ) -> None:
        """credential 関連でない例外 type は str(exc) を保持（後方互換 / 診断性）。"""
        exc = RuntimeError("simulated node.build() failure: invalid config")
        _patch_tachibana_deps_with_build_failure(monkeypatch, exc)

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        build_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "node_build_failed"
        ]
        assert len(build_failed) == 1
        msg = build_failed[0]["message"]
        assert "simulated node.build() failure" in msg, (
            f"unrelated RuntimeError message must be preserved for diagnosability: {msg!r}"
        )
