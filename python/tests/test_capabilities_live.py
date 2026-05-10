"""Issue #42 Phase 3.5: per-venue live capability + tachibana is_production.

このファイルは Phase 3.5 の受け入れ基準 #12 / #18 をカバーする:

- ``test_supports_live_strategy_per_venue`` — tachibana=True / kabu_station=False を pin
- ``test_tachibana_is_production_per_env`` — ``TACHIBANA_ALLOW_PROD=1`` で True、unset で False

両者とも venue capability map のキー追加なので SCHEMA_MINOR bump は不要（既存
``venue_capability`` helper の ``Ok(None)`` フォールバック仕様で旧 client は安全側に倒れる）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_dummy_worker() -> MagicMock:
    w = MagicMock()
    w.prepare = AsyncMock(return_value=None)
    w.capabilities = MagicMock(return_value={})
    return w


def _build_venue_caps(server: Any) -> dict[str, dict]:
    """Replicate ``server._handshake`` の venue_caps 構築ロジックの抜粋。

    実機の handshake ハンドラを呼び出さずに venue_caps だけ unit test する。
    実装が ``server.py::_handshake`` から逸脱した場合は本ヘルパーも更新すること。
    """
    venue_caps: dict[str, dict] = {}
    for venue, worker in server._workers.items():
        try:
            caps = worker.capabilities()
        except Exception:
            continue
        if caps:
            venue_caps[venue] = caps

    # kabu_station は _workers に含まれないため server._handshake と同じ流儀で直接追記。
    from engine.exchanges.kabusapi_register import RegisterSet as _KabuRegisterSet

    venue_caps["kabu_station"] = {
        "requires_local_app": True,
        "max_push_symbols": _KabuRegisterSet.MAX,
        "supports_amend": False,
        "requires_trade_password_for_cancel": True,
        "is_production": server._kabu_env == "prod",
        "supports_live_strategy": False,
    }
    return venue_caps


@pytest.fixture
def server_with_real_tachibana(tmp_path: Path):
    """実 ``TachibanaWorker`` を含む ``DataEngineServer`` を返す。

    他の venue worker は MagicMock で軽量化（capabilities() は空 dict を返す）。
    """
    from engine.server import DataEngineServer

    with (
        patch("engine.server.BinanceWorker", return_value=_make_dummy_worker()),
        patch("engine.server.BybitWorker", return_value=_make_dummy_worker()),
        patch("engine.server.HyperliquidWorker", return_value=_make_dummy_worker()),
        patch("engine.server.MexcWorker", return_value=_make_dummy_worker()),
        patch("engine.server.OkexWorker", return_value=_make_dummy_worker()),
    ):
        server = DataEngineServer(port=0, token="cap-test", cache_dir=tmp_path)
        yield server


def test_supports_live_strategy_per_venue(
    server_with_real_tachibana, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受け入れ基準 #12: ``supports_live_strategy`` cap が venue 単位で expose される。

    - tachibana = True (live strategy 対応済み venue)
    - kabu_station = False (Phase 4 で True に flip 予定)
    """
    # is_production を実環境に依存させない (False で確定させる)。
    monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)

    venue_caps = _build_venue_caps(server_with_real_tachibana)

    assert "tachibana" in venue_caps, f"tachibana cap missing: {venue_caps}"
    assert venue_caps["tachibana"].get("supports_live_strategy") is True, (
        f"tachibana must advertise supports_live_strategy=True (got {venue_caps['tachibana']})"
    )

    assert "kabu_station" in venue_caps, f"kabu_station cap missing: {venue_caps}"
    assert venue_caps["kabu_station"].get("supports_live_strategy") is False, (
        "kabu_station must advertise supports_live_strategy=False until Phase 4 "
        f"(got {venue_caps['kabu_station']})"
    )


def test_tachibana_is_production_per_env(
    server_with_real_tachibana, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受け入れ基準 #18: tachibana の ``is_production`` cap が
    ``TACHIBANA_ALLOW_PROD`` env に応じて True/False を返す。
    """
    # Case 1: env unset → False
    monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)
    venue_caps = _build_venue_caps(server_with_real_tachibana)
    assert venue_caps["tachibana"].get("is_production") is False, (
        "TACHIBANA_ALLOW_PROD unset must yield is_production=False"
    )

    # Case 2: env="0" → False
    monkeypatch.setenv("TACHIBANA_ALLOW_PROD", "0")
    venue_caps = _build_venue_caps(server_with_real_tachibana)
    assert venue_caps["tachibana"].get("is_production") is False, (
        "TACHIBANA_ALLOW_PROD=0 must yield is_production=False"
    )

    # Case 3: env="1" → True
    monkeypatch.setenv("TACHIBANA_ALLOW_PROD", "1")
    venue_caps = _build_venue_caps(server_with_real_tachibana)
    assert venue_caps["tachibana"].get("is_production") is True, (
        "TACHIBANA_ALLOW_PROD=1 must yield is_production=True"
    )

    # Case 4: env="true"（"1" 以外は全て False、orchestrator 指示に準拠）
    monkeypatch.setenv("TACHIBANA_ALLOW_PROD", "true")
    venue_caps = _build_venue_caps(server_with_real_tachibana)
    assert venue_caps["tachibana"].get("is_production") is False, (
        "Only literal '1' opts in (orchestrator-confirmed contract); "
        "'true' must yield is_production=False"
    )
