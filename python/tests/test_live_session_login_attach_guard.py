"""M-GP4: `LiveSession.login()` guard against credentials in attach mode.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

import pytest


def test_live_session_login_attach_mode_raises_value_error_when_credentials_passed():
    """M-GP4: attach mode で user_id/password を渡すと ValueError が出ること。"""
    from engine.replay_session import LiveSession

    s = LiveSession(venue="tachibana", demo=True, force_mode="inprocess")
    s._entered = True
    s._mode = "attach"  # 直接 attach mode に設定

    # user_id を渡すと ValueError
    with pytest.raises(ValueError, match="attach mode"):
        s.login(user_id="test-user", password="test-pass")


def test_live_session_login_attach_mode_only_user_id_raises_value_error():
    """M-GP4: attach mode で user_id のみ渡すと ValueError が出ること。"""
    from engine.replay_session import LiveSession

    s = LiveSession(venue="tachibana", demo=True, force_mode="inprocess")
    s._entered = True
    s._mode = "attach"

    with pytest.raises(ValueError, match="attach mode"):
        s.login(user_id="test-user")
