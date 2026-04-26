"""T2.5 (D4-4): EVENT URL 制御文字サニタイズテスト。

\n / \t / \x01-\x03 を含む URL 構築 → ValueError で reject されること。
silent strip ではなく reject であることを確認。
"""
from __future__ import annotations

import pytest

from engine.exchanges.tachibana_url import EventUrl, build_event_url


BASE = EventUrl("wss://demo-event.e-shiten.jp/event/")


# ---------------------------------------------------------------------------
# 正常系: 制御文字なし
# ---------------------------------------------------------------------------


def test_build_event_url_normal():
    """制御文字を含まない通常パラメータで URL が構築される。"""
    url = build_event_url(BASE, {"sUserId": "user123", "sCLMID": "CLMEventUpload"})
    assert "user123" in url
    assert BASE.value in url


# ---------------------------------------------------------------------------
# 異常系: 制御文字を含む値 → ValueError (reject)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("control_char", [
    "\n",    # LF
    "\t",    # HT
    "\x01",  # SOH
    "\x02",  # STX
    "\x03",  # ETX
    "\r",    # CR (CR も C0 に含まれる)
    "\x00",  # NUL
    "\x1f",  # US (最後の C0 制御文字)
])
def test_build_event_url_rejects_control_char_in_value(control_char: str):
    """制御文字を含む値は ValueError で reject される（silent strip ではない）。"""
    with pytest.raises(ValueError, match="control character"):
        build_event_url(BASE, {"sUserId": f"user{control_char}123"})


@pytest.mark.parametrize("control_char", [
    "\n",
    "\t",
    "\x01",
])
def test_build_event_url_rejects_control_char_in_key(control_char: str):
    """制御文字を含むキーも ValueError で reject される。"""
    with pytest.raises(ValueError, match="control character"):
        build_event_url(BASE, {f"key{control_char}name": "value"})


def test_build_event_url_rejects_newline_in_value():
    """\n はログインジェクション攻撃の主な攻撃手法。明示的にテスト。"""
    with pytest.raises(ValueError):
        build_event_url(BASE, {"sUserId": "attacker\nX-Injected: malicious"})


def test_build_event_url_rejects_tab_in_value():
    """\t も reject されること。"""
    with pytest.raises(ValueError):
        build_event_url(BASE, {"sParam": "value\twith\ttabs"})


def test_build_event_url_rejects_x01_in_value():
    """\x01 も reject されること。"""
    with pytest.raises(ValueError):
        build_event_url(BASE, {"sParam": "\x01malicious"})


# ---------------------------------------------------------------------------
# silent strip ではないことの確認
# ---------------------------------------------------------------------------


def test_build_event_url_does_not_silently_strip():
    """\n を含む値がストリップされてスルーするのではなく ValueError になること。"""
    bad_value = "user\n123"
    # silent strip なら "user123" が返るはずだが、reject でなければならない
    with pytest.raises(ValueError):
        build_event_url(BASE, {"sUserId": bad_value})


# ---------------------------------------------------------------------------
# EventUrl でなければ TypeError
# ---------------------------------------------------------------------------


def test_build_event_url_rejects_wrong_url_type():
    """EventUrl 以外の型では TypeError が raise される。"""
    from engine.exchanges.tachibana_url import RequestUrl
    wrong = RequestUrl("https://demo.e-shiten.jp/api/")
    with pytest.raises(TypeError):
        build_event_url(wrong, {"key": "value"})
