"""P4-2: kabuステーション env 解決（KABU_ALLOW_PROD + KABU_ENV 二重判定）。

`resolve_kabu_env()` は:
- 両 env 揃ったときのみ "prod"
- 片方欠如は "verify" にフォールバック + WARN ログ
- 片方が許可外値（"true" / "0" / "Prod" 等）も "verify"
"""

from __future__ import annotations

import logging

import pytest

from engine.exchanges.kabusapi_url import resolve_kabu_env


pytestmark = pytest.mark.demo_kabu


class TestResolveKabuEnv:
    def test_defaults_to_verify(self, monkeypatch):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        monkeypatch.delenv("KABU_ENV", raising=False)
        assert resolve_kabu_env() == "verify"

    def test_both_envs_required_for_prod(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "1")
        monkeypatch.setenv("KABU_ENV", "prod")
        assert resolve_kabu_env() == "prod"

    def test_only_allow_prod_falls_back_to_verify(self, monkeypatch, caplog):
        monkeypatch.setenv("KABU_ALLOW_PROD", "1")
        monkeypatch.delenv("KABU_ENV", raising=False)
        with caplog.at_level(logging.WARNING):
            assert resolve_kabu_env() == "verify"
        assert any("KABU_ENV" in rec.message for rec in caplog.records)

    def test_only_kabu_env_prod_falls_back_to_verify(self, monkeypatch, caplog):
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        monkeypatch.setenv("KABU_ENV", "prod")
        with caplog.at_level(logging.WARNING):
            assert resolve_kabu_env() == "verify"
        assert any("KABU_ALLOW_PROD" in rec.message for rec in caplog.records)

    def test_allow_prod_with_kabu_env_verify_is_verify(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "1")
        monkeypatch.setenv("KABU_ENV", "verify")
        assert resolve_kabu_env() == "verify"

    def test_allow_prod_0_blocks_prod(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "0")
        monkeypatch.setenv("KABU_ENV", "prod")
        assert resolve_kabu_env() == "verify"

    def test_allow_prod_true_string_blocks_prod(self, monkeypatch):
        monkeypatch.setenv("KABU_ALLOW_PROD", "true")
        monkeypatch.setenv("KABU_ENV", "prod")
        assert resolve_kabu_env() == "verify"

    def test_kabu_env_uppercase_not_accepted(self, monkeypatch):
        """`Prod` / `PROD` は不可。完全一致 `prod` のみ。"""
        monkeypatch.setenv("KABU_ALLOW_PROD", "1")
        monkeypatch.setenv("KABU_ENV", "Prod")
        assert resolve_kabu_env() == "verify"

    def test_no_warning_when_both_unset(self, monkeypatch, caplog):
        """全部 unset（デフォルト動作）では WARN を出さない。"""
        monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
        monkeypatch.delenv("KABU_ENV", raising=False)
        with caplog.at_level(logging.WARNING):
            resolve_kabu_env()
        assert not any(
            "KABU_ALLOW_PROD" in rec.message or "KABU_ENV" in rec.message
            for rec in caplog.records
        )
