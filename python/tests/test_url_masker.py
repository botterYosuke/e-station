"""TDD Red → Green: D2-L1 — tachibana_url.py の is_production_url() と
guard_prod_url() のパラメタライズ検証。

テストの意図:
  - kabuka.e-shiten.jp を含む URL が本番 URL として検出される
  - demo-kabuka.e-shiten.jp を含む URL が本番 URL として検出されない
  - TACHIBANA_ALLOW_PROD 未設定のとき本番 URL で ValueError が上がる
  - TACHIBANA_ALLOW_PROD=1 のとき本番 URL が通る
  - デモ URL は常に通る

既存の test_prod_url_guard.py はクラスベースの非パラメタライズ形式。
このファイルは pytest.mark.parametrize を使った追加検証。
"""

from __future__ import annotations

import pytest

from engine.exchanges.tachibana_url import guard_prod_url, is_production_url


# ---------------------------------------------------------------------------
# is_production_url — parametrize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://kabuka.e-shiten.jp/e_api_v4r8/",
        "https://kabuka.e-shiten.jp/e_api_v4r7/",
        "https://kabuka.e-shiten.jp/some/path?q=1",
        "https://e-shiten.jp/foo",
        "https://sub.e-shiten.jp/bar",
    ],
)
def test_is_production_url_identifies_prod_urls(url: str):
    """kabuka.e-shiten.jp や e-shiten.jp を含む URL は True を返す。"""
    assert is_production_url(url) is True, f"Expected production URL, got False for {url!r}"


@pytest.mark.parametrize(
    "url",
    [
        "https://demo-kabuka.e-shiten.jp/e_api_v4r8/",
        "https://demo-kabuka.e-shiten.jp/e_api_v4r7/",
        "https://demo-kabuka.e-shiten.jp/some/path",
        "https://localhost:8080/api",
        "https://example.com/path",
        "http://127.0.0.1:19876/",
        "",
    ],
)
def test_is_production_url_rejects_demo_urls(url: str):
    """デモ URL や非本番 URL は False を返す。"""
    assert is_production_url(url) is False, f"Expected non-production URL, got True for {url!r}"


# ---------------------------------------------------------------------------
# guard_prod_url — parametrize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prod_url",
    [
        "https://kabuka.e-shiten.jp/e_api_v4r8/",
        "https://kabuka.e-shiten.jp/request/",
        "https://e-shiten.jp/foo",
    ],
)
def test_guard_prod_url_raises_on_prod_without_env_var(prod_url: str, monkeypatch):
    """TACHIBANA_ALLOW_PROD が未設定のとき本番 URL で ValueError が上がる。"""
    monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)
    with pytest.raises(ValueError, match="TACHIBANA_ALLOW_PROD"):
        guard_prod_url(prod_url)


@pytest.mark.parametrize(
    "prod_url",
    [
        "https://kabuka.e-shiten.jp/e_api_v4r8/",
        "https://kabuka.e-shiten.jp/request/",
        "https://e-shiten.jp/foo",
    ],
)
def test_guard_prod_url_allows_prod_with_env_var(prod_url: str, monkeypatch):
    """TACHIBANA_ALLOW_PROD=1 のとき本番 URL が通る（ValueError が上がらない）。"""
    monkeypatch.setenv("TACHIBANA_ALLOW_PROD", "1")
    # should not raise
    guard_prod_url(prod_url)


@pytest.mark.parametrize(
    "demo_url",
    [
        "https://demo-kabuka.e-shiten.jp/e_api_v4r8/",
        "https://demo-kabuka.e-shiten.jp/request/",
        "http://127.0.0.1:19876/",
        "https://localhost:8080/api",
    ],
)
def test_guard_prod_url_passes_demo_url(demo_url: str, monkeypatch):
    """デモ URL は TACHIBANA_ALLOW_PROD なしで常に通る。"""
    monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)
    # should not raise
    guard_prod_url(demo_url)
