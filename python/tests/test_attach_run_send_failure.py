"""Attach mode `run()` で send_command が raise したときに status が
RUNNING のまま固着しないことを保証する回帰テスト。

回帰: status を RUNNING に遷移させた直後に send_command が raise すると、
try/finally の手前で抜けるため status が "running" のまま残り、
attach client の current_request_id もクリアされていなかった。
"""

from __future__ import annotations

import pytest


class _SendFailingClient:
    """send_command が必ず raise する fake client。"""

    def __init__(self) -> None:
        self.cleared_request_ids: list[str | None] = []
        self._current: str | None = None

    def set_current_request_id(self, request_id: str | None) -> None:
        self._current = request_id
        self.cleared_request_ids.append(request_id)

    def send_command(self, cmd: dict) -> None:
        if cmd.get("op") == "StartEngine":
            raise RuntimeError("simulated WS send failure")

    def wait_for(self, *a, **kw):  # noqa: ARG002
        return {"event": "ReplayDataLoaded"}

    def events(self):  # pragma: no cover
        yield from ()

    def close(self) -> None:
        pass


def test_attach_run_send_failure_rolls_back_status(tmp_path):
    from engine.replay_session import ReplaySession, _ReplayStatus

    strat = tmp_path / "s.py"
    strat.write_text("# dummy\n")

    client = _SendFailingClient()

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "attach"
    s._client = client
    s._status = _ReplayStatus.IDLE
    s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")

    with pytest.raises(RuntimeError, match="simulated WS send failure"):
        s.run(strategy_file=str(strat), on_event=lambda evt: None)

    assert s.status == "errored", (
        f"send_command 失敗時に status が rollback されていない: got {s.status!r}"
    )
    assert client.cleared_request_ids[-1] is None, (
        f"current_request_id がクリアされていない: history={client.cleared_request_ids}"
    )
