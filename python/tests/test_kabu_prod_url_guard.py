"""P4-1: kabuステーション 本番 URL ガード。

is_production_url() / guard_prod_url() が:
- localhost:18080 を本番 URL として検出
- KABU_ALLOW_PROD=1 が未設定の場合は ValueError を raise
- KABU_ALLOW_PROD=1 設定時は通る
- localhost:18081 (verify) は env なしで通る
"""

from __future__ import annotations

import pytest

from engine.exchanges.kabusapi_url import (
    base_url,
    endpoint,
    guard_prod_url,
    is_production_url,
    ws_url,
)


pytestmark = pytest.mark.demo_kabu


# ---------------------------------------------------------------------------
# is_production_url
# ---------------------------------------------------------------------------


class TestIsProductionUrl:
    def test_localhost_18080_is_prod(self):
        assert is_production_url("http://localhost:18080/kabusapi/token") is True

    def test_localhost_18080_ws_is_prod(self):
        assert is_production_url("ws://localhost:18080/kabusapi/websocket") is True

    def test_localhost_18081_is_not_prod(self):
        assert is_production_url("http://localhost:18081/kabusapi/token") is False

    def test_localhost_18081_ws_is_not_prod(self):
        assert is_production_url("ws://localhost:18081/kabusapi/websocket") is False

    def test_empty_string_is_not_prod(self):
        assert is_production_url("") is False

    def test_random_url_is_not_prod(self):
        assert is_production_url("http://example.com/path") is False


# ---------------------------------------------------------------------------
# guard_prod_url
# ---------------------------------------------------------------------------


class TestGuardProdUrl:
    def test_prod_url_blocked_without_env(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
            guard_prod_url("http://localhost:18080/kabusapi/token")

    def test_prod_url_allowed_with_env_1(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "1")
        guard_prod_url("http://localhost:18080/kabusapi/token")  # no raise

    def test_verify_url_always_passes(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        guard_prod_url("http://localhost:18081/kabusapi/token")  # no raise

    def test_prod_url_blocked_when_env_is_0(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "0")
        with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
            guard_prod_url("http://localhost:18080/kabusapi/token")

    def test_prod_url_blocked_when_env_is_true_string(self, monkeypatch):
        """'1' のみ許可。'true' などは不可。"""
        monkeypatch.setenv("KABU_ALLOW_PROD", "true")
        with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
            guard_prod_url("http://localhost:18080/kabusapi/token")

    def test_prod_ws_url_blocked_without_env(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
            guard_prod_url("ws://localhost:18080/kabusapi/websocket")


# ---------------------------------------------------------------------------
# Integration with existing builders: base_url / endpoint / ws_url
# ---------------------------------------------------------------------------


class TestEndpointBuildersInvokeGuard:
    """endpoint(env="prod") / ws_url("prod") / base_url("prod") は KABU_ALLOW_PROD 未設定で ValueError。"""

    def test_base_url_prod_blocked(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
            base_url("prod")

    def test_base_url_prod_allowed(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "1")
        assert base_url("prod") == "http://localhost:18080"

    def test_base_url_verify_always_ok(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        assert base_url("verify") == "http://localhost:18081"

    def test_endpoint_prod_blocked(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
            endpoint("token", env="prod")

    def test_endpoint_prod_allowed(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "1")
        assert endpoint("token", env="prod") == "http://localhost:18080/kabusapi/token"

    def test_endpoint_verify_always_ok(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        assert endpoint("token", env="verify") == "http://localhost:18081/kabusapi/token"

    def test_ws_url_prod_blocked(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
            ws_url("prod")

    def test_ws_url_prod_allowed(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "1")
        assert ws_url("prod") == "ws://localhost:18080/kabusapi/websocket"

    def test_ws_url_verify_always_ok(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        assert ws_url("verify") == "ws://localhost:18081/kabusapi/websocket"


# ---------------------------------------------------------------------------
# MED-C: 127.0.0.1:18080 の検出 (RED → GREEN)
# ---------------------------------------------------------------------------


class TestIsProductionUrlIpLiteral:
    """127.0.0.1:18080 も本番 URL として検出すること (MED-C)。"""

    def test_127_0_0_1_18080_is_prod(self):
        assert is_production_url("http://127.0.0.1:18080/kabusapi/token") is True

    def test_127_0_0_1_18081_is_not_prod(self):
        assert is_production_url("http://127.0.0.1:18081/kabusapi/token") is False


# ---------------------------------------------------------------------------
# MED-B: guard_prod_url の呼出チェーンを mock で assert (P4-5 完了条件)
# ---------------------------------------------------------------------------


def test_send_order_invokes_guard_prod_url(monkeypatch):
    """P4-5 pin: base_url() → guard_prod_url() の呼出チェーンを mock で assert"""
    from unittest.mock import patch
    from engine.exchanges.kabusapi_url import base_url
    with patch("engine.exchanges.kabusapi_url.guard_prod_url") as mock_guard:
        mock_guard.return_value = None  # guard 通過扱い
        base_url("prod")
    mock_guard.assert_called_once()
