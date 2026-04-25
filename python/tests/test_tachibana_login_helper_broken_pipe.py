"""H2 / M-3-py / M-15: when `_spawn_login_dialog` cannot write its
stdin payload (helper exited before reading), it must abort
immediately with `LoginError(code='login_failed')` instead of waiting
for the 10-minute communicate() timeout.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from engine.exchanges.tachibana_helpers import LoginError
from engine.exchanges import tachibana_login_flow


class _FakeStdin:
    """An async stdin substitute that pretends to be a closed pipe."""

    def __init__(self):
        self.closed = False

    def write(self, _data: bytes) -> None:
        raise BrokenPipeError("helper closed stdin before reading")

    async def drain(self) -> None:  # pragma: no cover — never reached
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    """An asyncio.subprocess.Process stand-in just sufficient for the
    `_spawn_login_dialog` early-abort path."""

    def __init__(self):
        self.stdin = _FakeStdin()
        self.stderr = None
        self.terminated = False
        self.killed = False
        self._returncode = None
        self.wait_calls = 0

    def terminate(self):
        # Real semantics: terminate() sends a signal but does not reap.
        # The caller must `await proc.wait()` afterwards or the helper
        # becomes a zombie. We model that explicitly: returncode stays
        # None until wait() is awaited.
        self.terminated = True

    def kill(self):
        self.killed = True

    @property
    def returncode(self):
        return self._returncode

    async def wait(self):
        # Reaping: only flips returncode when actually awaited.
        self.wait_calls += 1
        self._returncode = -15 if self.terminated else -9
        return self._returncode


@pytest.mark.asyncio
async def test_broken_pipe_on_stdin_aborts_immediately():
    """The early-abort path must NOT touch `wait_for(proc.communicate())`
    (the 10-minute hang). It must terminate the helper, await its
    reaping, then raise `LoginError(code='login_failed')`. Reaping is
    asserted via `returncode is not None` (M-15 ラウンド 5: orphan
    helpers were leaking when terminate() was fire-and-forget)."""
    fake = _FakeProc()

    async def fake_create_subprocess_exec(*_a, **_k):
        return fake

    # Swap *only* the long-running `wait_for(communicate(), 600.0)` call.
    # The new code path uses `wait_for(proc.wait(), 5.0)` for reaping,
    # which we route to the real implementation so the orphan-reap
    # behaviour is observable.
    real_wait_for = asyncio.wait_for

    async def selective_wait_for(awaitable, timeout):
        if timeout == 600.0:
            raise AssertionError(
                "wait_for(communicate(), 600.0) must not be reached after BrokenPipe"
            )
        return await real_wait_for(awaitable, timeout)

    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=fake_create_subprocess_exec,
        ),
        patch("asyncio.wait_for", side_effect=selective_wait_for),
    ):
        with pytest.raises(LoginError) as excinfo:
            await tachibana_login_flow._spawn_login_dialog(prefill=None)

    assert excinfo.value.code == "login_failed"
    assert fake.terminated, "helper must be terminated on broken pipe"
    assert fake.wait_calls >= 1, (
        "helper must be reaped via `await proc.wait()` — orphan process otherwise"
    )
    assert fake.returncode is not None, (
        "helper must have a returncode after reaping — orphan process otherwise"
    )
