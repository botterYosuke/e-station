"""N1.13: live / replay 起動時固定モードの分離テスト.

server.py レベルの dispatch 相当を `engine.mode` のヘルパー関数で表現し、
両モードでの許可・拒否ポリシーが正しいことを検証する。
"""

from __future__ import annotations

import pytest

from engine.mode import (
    is_replay_path_allowed,
    nautilus_capabilities,
    order_dispatch_target,
    validate_start_engine,
)


# ── /api/replay/* gating (live モードでは 400 相当) ─────────────────────────


def test_live_mode_rejects_replay_paths() -> None:
    assert is_replay_path_allowed("live", "/api/replay/load") is False
    assert is_replay_path_allowed("live", "/api/replay/portfolio") is False
    assert is_replay_path_allowed("live", "/api/replay/order") is False


def test_replay_mode_allows_replay_paths() -> None:
    assert is_replay_path_allowed("replay", "/api/replay/load") is True
    assert is_replay_path_allowed("replay", "/api/replay/portfolio") is True


def test_non_replay_paths_are_unaffected_by_mode() -> None:
    # /api/order/* や /api/sidebar/* はこの関数で判断しない (常に True)
    assert is_replay_path_allowed("live", "/api/order/submit") is True
    assert is_replay_path_allowed("replay", "/api/order/submit") is True


# ── /api/order/submit dispatch ─────────────────────────────────────────────


def test_order_dispatch_in_replay_mode_routes_to_replay() -> None:
    assert order_dispatch_target("replay") == "replay"


def test_order_dispatch_in_live_mode_routes_to_live() -> None:
    assert order_dispatch_target("live") == "live"


# ── mode と StartEngine.engine の不一致は ValueError ────────────────────────


def test_start_engine_mismatch_live_mode_with_backtest_engine() -> None:
    with pytest.raises(ValueError, match="requires mode='replay'"):
        validate_start_engine("live", "Backtest")


def test_start_engine_mismatch_replay_mode_with_live_engine() -> None:
    with pytest.raises(ValueError, match="requires mode='live'"):
        validate_start_engine("replay", "Live")


def test_start_engine_match_replay_backtest_passes() -> None:
    validate_start_engine("replay", "Backtest")  # no raise


def test_start_engine_match_live_live_passes() -> None:
    validate_start_engine("live", "Live")  # no raise


def test_start_engine_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown engine kind"):
        validate_start_engine("replay", "Bogus")


# ── live モードで Hello.capabilities.nautilus.live が false のまま ──────────


def test_live_mode_nautilus_live_capability_is_false() -> None:
    caps = nautilus_capabilities("live")
    assert caps["backtest"] is True
    assert caps["live"] is False


def test_replay_mode_nautilus_live_capability_is_false() -> None:
    # N1 では replay でも live engine は起動しない
    caps = nautilus_capabilities("replay")
    assert caps["backtest"] is True
    assert caps["live"] is False
