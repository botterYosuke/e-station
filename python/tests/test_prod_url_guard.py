"""TDD Red → Green: T0.6 — 本番 URL ガード。

is_production_url() / guard_prod_url() が:
- kabuka.e-shiten.jp / e-shiten.jp を本番 URL として検出する
- TACHIBANA_ALLOW_PROD=1 が未設定の場合に ValueError を raise する
- TACHIBANA_ALLOW_PROD=1 が設定されている場合は通る
- demo URL は通る
"""

from __future__ import annotations

import pytest

from engine.exchanges.tachibana_url import guard_prod_url, is_production_url


# ---------------------------------------------------------------------------
# is_production_url
# ---------------------------------------------------------------------------


class TestIsProductionUrl:
    def test_kabuka_e_shiten_jp_is_prod(self):
        assert is_production_url("https://kabuka.e-shiten.jp/e_api_v4r8/") is True

    def test_e_shiten_jp_subdomain_is_prod(self):
        assert is_production_url("https://e-shiten.jp/foo") is True

    def test_demo_kabuka_is_not_prod(self):
        assert is_production_url("https://demo-kabuka.e-shiten.jp/e_api_v4r8/") is False

    def test_localhost_is_not_prod(self):
        assert is_production_url("https://localhost:8080/api") is False

    def test_empty_string_is_not_prod(self):
        assert is_production_url("") is False

    def test_random_url_is_not_prod(self):
        assert is_production_url("https://example.com/path") is False

    def test_prod_url_embedded_in_path_detected(self):
        """ホスト部分に本番パターンが含まれていれば検出される。"""
        assert is_production_url("https://kabuka.e-shiten.jp/some/path?q=1") is True


# ---------------------------------------------------------------------------
# guard_prod_url
# ---------------------------------------------------------------------------


class TestGuardProdUrl:
    def test_prod_url_blocked_without_env(self, monkeypatch):
        """TACHIBANA_ALLOW_PROD 未設定で本番 URL → ValueError。"""
        monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)
        with pytest.raises(ValueError, match="TACHIBANA_ALLOW_PROD"):
            guard_prod_url("https://kabuka.e-shiten.jp/e_api_v4r8/")

    def test_prod_url_allowed_with_env_1(self, monkeypatch):
        """TACHIBANA_ALLOW_PROD=1 で本番 URL → raise しない。"""
        monkeypatch.setenv("TACHIBANA_ALLOW_PROD", "1")
        # should not raise
        guard_prod_url("https://kabuka.e-shiten.jp/e_api_v4r8/")

    def test_demo_url_always_passes(self, monkeypatch):
        """デモ URL は TACHIBANA_ALLOW_PROD なしで通る。"""
        monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)
        # should not raise
        guard_prod_url("https://demo-kabuka.e-shiten.jp/e_api_v4r8/")

    def test_prod_url_blocked_when_env_is_0(self, monkeypatch):
        """TACHIBANA_ALLOW_PROD=0 は '1' ではないのでブロック。"""
        monkeypatch.setenv("TACHIBANA_ALLOW_PROD", "0")
        with pytest.raises(ValueError):
            guard_prod_url("https://kabuka.e-shiten.jp/e_api_v4r8/")

    def test_prod_url_blocked_when_env_is_true(self, monkeypatch):
        """TACHIBANA_ALLOW_PROD=true (文字列) はブロック（"1" のみ許可）。"""
        monkeypatch.setenv("TACHIBANA_ALLOW_PROD", "true")
        with pytest.raises(ValueError):
            guard_prod_url("https://kabuka.e-shiten.jp/e_api_v4r8/")
