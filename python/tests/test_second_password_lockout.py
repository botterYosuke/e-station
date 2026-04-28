"""TDD Red → Green: server.py の _do_submit_order ハンドラが
TachibanaSessionHolder の lockout を正しく統合しているかを確認する。

テストの意図（H-8 統合検証）:
  - server.py が is_locked_out() チェックを実際に行い、
    lockout 中は OrderRejected{reason_code="SECOND_PASSWORD_LOCKED"} を emit する
  - lockout 状態は server インスタンスをまたいでも持続する

単体テスト（TachibanaSessionHolder 自体）は test_tachibana_session_holder.py に
既に 17 件あるため、このファイルは server.py dispatch ロジックとの統合に絞る。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_auth import TachibanaSessionHolder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server():
    """DataEngineServer をワーカーなしで構築するヘルパー。"""
    from engine.server import DataEngineServer

    with (
        patch("engine.server.BinanceWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.BybitWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.HyperliquidWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.MexcWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.OkexWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.TachibanaWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
    ):
        srv = DataEngineServer(port=19998, token="test-token", wal_path=Path("/tmp/test_wal2.jsonl"))
    return srv


def _make_submit_msg(client_order_id: str = "cid-lock-001") -> dict:
    return {
        "op": "SubmitOrder",
        "request_id": "req-lockout",
        "venue": "tachibana",
        "order": {
            "client_order_id": client_order_id,
            "instrument_id": "7203.T/TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": False,
            "reduce_only": False,
            "tags": ["cash_margin=cash"],
        },
    }


def _collect_outbox(srv) -> list[dict]:
    events = []
    while srv._outbox:
        events.append(srv._outbox.popleft())
    return events


# ---------------------------------------------------------------------------
# テスト 1: server._session_holder.is_locked_out() が True のとき
#           _do_submit_order が OrderRejected(SECOND_PASSWORD_LOCKED) を emit する
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_rejects_when_locked_out():
    """server.py の _do_submit_order が is_locked_out() を呼び出し、
    lockout 中は OrderRejected{reason_code="SECOND_PASSWORD_LOCKED"} を emit することを確認する。
    """
    srv = _make_server()
    # lockout を手動で発動（max_retries=1 で 1 回 on_invalid → lockout）
    srv._session_holder = TachibanaSessionHolder(max_retries=1, lockout_secs=1800.0)
    srv._session_holder.set_password("pass")
    srv._session_holder.on_invalid()

    assert srv._session_holder.is_locked_out() is True, "前提: lockout 状態であること"

    await srv._do_submit_order(_make_submit_msg())

    events = _collect_outbox(srv)
    assert len(events) == 1, f"Expected 1 event, got: {events}"
    assert events[0]["event"] == "OrderRejected"
    assert events[0]["reason_code"] == "SECOND_PASSWORD_LOCKED"
    assert events[0]["client_order_id"] == "cid-lock-001"


# ---------------------------------------------------------------------------
# テスト 2: lockout が解除されると _do_submit_order が次のステップに進む
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_proceeds_after_lockout_expires():
    """lockout 期間が終了すると is_locked_out() が False になり、
    _do_submit_order が lockout ガードを通過して次のステップに到達する。

    セッション未設定なので NOT_LOGGED_IN で止まるが、
    SECOND_PASSWORD_LOCKED が出ないことを確認する。
    """
    srv = _make_server()
    srv._session_holder = TachibanaSessionHolder(max_retries=1, lockout_secs=1.0)
    srv._session_holder.set_password("pass")
    srv._session_holder.on_invalid()  # lockout 発動

    # lockout_until を過去に設定して期間終了をシミュレート
    srv._session_holder._lockout_until = srv._session_holder._now() - 1.0
    assert srv._session_holder.is_locked_out() is False, "前提: lockout 解除済みであること"

    await srv._do_submit_order(_make_submit_msg())

    events = _collect_outbox(srv)
    event_names = [e["event"] for e in events]

    # lockout ガードを通過するため SECOND_PASSWORD_LOCKED は出ない
    assert not any(
        e.get("reason_code") == "SECOND_PASSWORD_LOCKED" for e in events
    ), f"SECOND_PASSWORD_LOCKED should not appear after lockout expires, got: {events}"

    # セッション未設定なので最終的に NOT_LOGGED_IN か SecondPasswordRequired になる
    assert len(events) >= 1
    assert events[-1]["event"] in ("OrderRejected", "SecondPasswordRequired"), (
        f"Expected OrderRejected or SecondPasswordRequired after lockout expires, got: {event_names}"
    )


# ---------------------------------------------------------------------------
# テスト 3: 3 回の on_invalid で lockout が発動し、その後 _do_submit_order が
#           SECOND_PASSWORD_LOCKED を返すことを server 経由で確認する
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_invalid_via_server_triggers_lockout_response():
    """3 回の SecondPasswordInvalidError を server._session_holder.on_invalid() で
    シミュレートし、lockout 後に _do_submit_order が SECOND_PASSWORD_LOCKED を返すことを確認する。
    """
    srv = _make_server()
    srv._session_holder = TachibanaSessionHolder(max_retries=3, lockout_secs=1800.0)

    # 3 回 on_invalid を呼ぶ（server._session_holder 経由）
    srv._session_holder.on_invalid()
    srv._session_holder.on_invalid()
    locked = srv._session_holder.on_invalid()

    assert locked is True, "3 回目の on_invalid で lockout が発動するべき"
    assert srv._session_holder.is_locked_out() is True

    # lockout 後の発注を server ハンドラ経由で確認
    await srv._do_submit_order(_make_submit_msg("cid-three-invalid"))

    events = _collect_outbox(srv)
    assert len(events) == 1
    assert events[0]["event"] == "OrderRejected"
    assert events[0]["reason_code"] == "SECOND_PASSWORD_LOCKED"


# ---------------------------------------------------------------------------
# テスト 4: lockout 期間は 1800 秒（MonotonicClock ベースの now パラメータで検証）
# ---------------------------------------------------------------------------


def test_lockout_expires_after_1800_seconds():
    """lockout_secs=1800 の場合、1800 秒後に is_locked_out(now=...) が False になる。

    asyncio.get_running_loop() が存在しない環境で now パラメータを直接渡す形でテスト。
    freezegun 不要。
    """
    h = TachibanaSessionHolder(max_retries=1, lockout_secs=1800.0)
    h.set_password("pass")

    # lockout を発動（内部で _lockout_until = now + 1800 が設定される）
    now_base = 10000.0
    h.on_invalid(now=now_base)

    assert h.is_locked_out(now=now_base) is True
    assert h.is_locked_out(now=now_base + 1799.0) is True  # まだロック中
    assert h.is_locked_out(now=now_base + 1800.0) is False  # ちょうど解除
    assert h.is_locked_out(now=now_base + 1801.0) is False  # 解除後
