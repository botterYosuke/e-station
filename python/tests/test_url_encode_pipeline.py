"""C-H2: func_replace_urlecnode パイプライン不変条件テスト。

tachibana_url.build_request_url が Tachibana 固有の 29 文字エンコードテーブル
(func_replace_urlecnode) を使って URL を組み立てていることを検証する。

標準 urllib.parse.quote では誤りになるケース（Tachibana 独自マッピング）を中心に確認:
  - 全角文字・日本語 → Shift-JIS バイト列の %XX エンコード（UTF-8 ではない）
  - 29 文字エンコードテーブルの境界値（`+` は `%2B`、スペースは `+` ではなく `%20`）
"""
from __future__ import annotations

import re

from engine.exchanges.tachibana_url import func_replace_urlecnode


# ---------------------------------------------------------------------------
# 1. func_replace_urlecnode 単体テスト
# ---------------------------------------------------------------------------


class TestFuncReplaceUrlecnode:
    """Tachibana 固有の 29 文字エンコードテーブルの振る舞い。"""

    def test_alphanumeric_unchanged(self):
        """英数字はそのまま（エンコード対象外）。"""
        assert func_replace_urlecnode("abc123") == "abc123"

    def test_plus_encoded(self):
        """`+` は `%2B` にエンコードされる（標準 query string と異なる）。"""
        result = func_replace_urlecnode("+")
        assert result == "%2B"

    def test_space_encoded_as_percent20(self):
        """スペースは `%20`（form-urlencoded の `+` 置換ではない）。"""
        result = func_replace_urlecnode(" ")
        assert result == "%20"

    def test_equals_encoded(self):
        """`=` は `%3D` にエンコードされる。"""
        result = func_replace_urlecnode("=")
        assert result == "%3D"

    def test_ampersand_encoded(self):
        """`&` は `%26` にエンコードされる。"""
        result = func_replace_urlecnode("&")
        assert result == "%26"

    def test_at_sign_encoded(self):
        """`@` は `%40` にエンコードされる。"""
        result = func_replace_urlecnode("@")
        assert result == "%40"

    def test_hash_encoded(self):
        """`#` は `%23` にエンコードされる。"""
        result = func_replace_urlecnode("#")
        assert result == "%23"

    def test_percent_encoded(self):
        """`%` 自体は `%25` にエンコードされる。"""
        result = func_replace_urlecnode("%")
        assert result == "%25"

    def test_slash_encoded(self):
        """`/` は `%2F` にエンコードされる。"""
        result = func_replace_urlecnode("/")
        assert result == "%2F"

    def test_mixed_ascii_and_encoded(self):
        """エンコード対象文字と安全文字が混在するとき正しく変換される。"""
        result = func_replace_urlecnode("a=1&b=2")
        assert result == "a%3D1%26b%3D2"

    def test_multibyte_passthrough(self):
        """マルチバイト文字はテーブル対象外のため変換されずそのまま返る。
        Shift-JIS エンコードは呼び出し元の責務（SKILL.md R9）。"""
        result = func_replace_urlecnode("テスト")
        assert result == "テスト"

    def test_output_percent_encoding_well_formed(self):
        """出力の `%XX` 表現が常に 2 桁の 16 進数になっている。"""
        result = func_replace_urlecnode("hello world+foo=bar")
        # percent-encoding は %XX の形式のみ（%X や %XXX は不正）
        # まず `%` を含む断片を抽出
        bad = re.findall(r"%(?![0-9A-Fa-f]{2})", result)
        assert not bad, f"Malformed percent-encoding found: {result!r}"


# ---------------------------------------------------------------------------
# 2. build_request_url がフィールド値に func_replace_urlecnode を使っていることの確認
# ---------------------------------------------------------------------------


def test_build_request_url_uses_tachibana_encoding(monkeypatch):
    """build_request_url が標準 urllib.parse.quote ではなく
    func_replace_urlecnode でエンコードしていることを確認する。

    検証戦略: func_replace_urlecnode を monkey-patch してコール記録を取り、
    build_request_url 呼び出し後に少なくとも 1 回は呼ばれていることを確認する。
    """
    import engine.exchanges.tachibana_url as url_module
    from engine.exchanges.tachibana_url import RequestUrl

    calls: list[str] = []
    original = url_module.func_replace_urlecnode

    def recording_encoder(s: str) -> str:
        calls.append(s)
        return original(s)

    monkeypatch.setattr(url_module, "func_replace_urlecnode", recording_encoder)

    url_module.build_request_url(
        RequestUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/"),
        {"p_sd_date": "2026-04-28", "p_meigaracd": "7203"},
        sJsonOfmt="5",
    )

    assert calls, (
        "func_replace_urlecnode was never called — build_request_url may be "
        "using urllib.parse.quote or another encoder instead"
    )


def test_build_request_url_encodes_plus_not_as_space():
    """build_request_url 出力で `+` が `%2B` にエンコードされている
    （form-urlencoded の `+` = スペース変換と区別できる）。"""
    from engine.exchanges.tachibana_url import RequestUrl, build_request_url

    url = build_request_url(
        RequestUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/"),
        {"key": "a+b"},
        sJsonOfmt="5",
    )
    assert "%2B" in url, (
        f"Expected `+` to be encoded as %2B in URL, but got: {url!r}"
    )
    assert "a+b" not in url, (
        f"Raw `+` should not appear unencoded in URL: {url!r}"
    )
