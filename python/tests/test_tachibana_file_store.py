"""Tests for tachibana_file_store.py (T-SC5).

Covers:
- Account save / load round-trip and error paths
- Session save / load round-trip and error paths
- clear_session
- Atomic write: no .tmp left behind
- _is_session_fresh with freezegun-controlled clock
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from freezegun import freeze_time

from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_file_store import (
    ACCOUNT_FILENAME,
    SESSION_FILENAME,
    _is_session_fresh,
    clear_session,
    load_account,
    load_session,
    save_account,
    save_session,
)
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JST = timezone(timedelta(hours=9))

_SAMPLE_SESSION = TachibanaSession(
    url_request=RequestUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/request/"),
    url_master=MasterUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/master/"),
    url_price=PriceUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/price/"),
    url_event=EventUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/event/"),
    url_event_ws="wss://demo-kabuka.e-shiten.jp/e_api_v4r8/ws/",
    zyoutoeki_kazei_c="0",
    expires_at_ms=None,
)


def _ms_for_jst(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> int:
    dt = datetime(year, month, day, hour, minute, second, tzinfo=_JST)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Account: save / load round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_account_roundtrip(tmp_path: Path) -> None:
    save_account(tmp_path, "test123", is_demo=True)
    result = load_account(tmp_path)
    assert result is not None
    assert result["user_id"] == "test123"
    assert result["is_demo"] is True


def test_save_account_does_not_include_password(tmp_path: Path) -> None:
    """F-SC-NoPassword: tachibana_account.json must never contain a password field."""
    save_account(tmp_path, "test123", is_demo=True)
    raw = json.loads((tmp_path / ACCOUNT_FILENAME).read_text(encoding="utf-8"))
    assert "password" not in raw


def test_load_account_missing_file_returns_none(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does_not_exist"
    assert load_account(nonexistent) is None


def test_load_account_corrupt_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / ACCOUNT_FILENAME).write_text("not valid json", encoding="utf-8")
    assert load_account(tmp_path) is None


def test_load_account_missing_user_id_returns_none(tmp_path: Path) -> None:
    (tmp_path / ACCOUNT_FILENAME).write_text(
        json.dumps({"is_demo": True}), encoding="utf-8"
    )
    assert load_account(tmp_path) is None


def test_load_account_non_bool_is_demo_returns_none(tmp_path: Path) -> None:
    """is_demo が文字列 "true" だと None（型チェック）。"""
    (tmp_path / ACCOUNT_FILENAME).write_text(
        json.dumps({"user_id": "test123", "is_demo": "true"}), encoding="utf-8"
    )
    assert load_account(tmp_path) is None


# ---------------------------------------------------------------------------
# Session: save / load round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_session_roundtrip(tmp_path: Path) -> None:
    save_session(tmp_path, _SAMPLE_SESSION)
    result = load_session(tmp_path)
    assert result is not None
    assert str(result.url_request) == str(_SAMPLE_SESSION.url_request)
    assert str(result.url_master) == str(_SAMPLE_SESSION.url_master)
    assert str(result.url_price) == str(_SAMPLE_SESSION.url_price)
    assert str(result.url_event) == str(_SAMPLE_SESSION.url_event)
    assert result.url_event_ws == _SAMPLE_SESSION.url_event_ws
    assert result.zyoutoeki_kazei_c == _SAMPLE_SESSION.zyoutoeki_kazei_c
    # expires_at_ms は saved_at_ms が入っていること（None ではない）
    assert result.expires_at_ms is not None


def test_save_session_does_not_include_password(tmp_path: Path) -> None:
    """F-SC-NoPassword: tachibana_session.json must never contain a password field."""
    save_session(tmp_path, _SAMPLE_SESSION)
    raw = json.loads((tmp_path / SESSION_FILENAME).read_text(encoding="utf-8"))
    assert "password" not in raw


def test_load_session_missing_file_returns_none(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does_not_exist"
    assert load_session(nonexistent) is None


def test_load_session_corrupt_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / SESSION_FILENAME).write_text("bad json {{", encoding="utf-8")
    assert load_session(tmp_path) is None


def test_load_session_missing_url_field_returns_none(tmp_path: Path) -> None:
    """url_request フィールドが欠けていると None。"""
    data = {
        # url_request is deliberately omitted
        "url_master": "https://demo-kabuka.e-shiten.jp/e_api_v4r8/master/",
        "url_price": "https://demo-kabuka.e-shiten.jp/e_api_v4r8/price/",
        "url_event": "https://demo-kabuka.e-shiten.jp/e_api_v4r8/event/",
        "url_event_ws": "wss://demo-kabuka.e-shiten.jp/e_api_v4r8/ws/",
        "zyoutoeki_kazei_c": "0",
        "saved_at_ms": 1000000,
    }
    (tmp_path / SESSION_FILENAME).write_text(json.dumps(data), encoding="utf-8")
    assert load_session(tmp_path) is None


# ---------------------------------------------------------------------------
# clear_session
# ---------------------------------------------------------------------------


def test_clear_session_deletes_file(tmp_path: Path) -> None:
    save_session(tmp_path, _SAMPLE_SESSION)
    assert (tmp_path / SESSION_FILENAME).exists()
    clear_session(tmp_path)
    assert not (tmp_path / SESSION_FILENAME).exists()


def test_clear_session_on_missing_file_is_noop(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does_not_exist"
    # Should not raise
    clear_session(nonexistent)


# ---------------------------------------------------------------------------
# Atomic write (F-SC-Atomic)
# ---------------------------------------------------------------------------


def test_atomic_write_leaves_no_tmp_on_success(tmp_path: Path) -> None:
    """F-SC-Atomic: save_session 後に .tmp ファイルが残っていないこと。"""
    save_session(tmp_path, _SAMPLE_SESSION)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Unexpected .tmp files: {tmp_files}"


# ---------------------------------------------------------------------------
# _is_session_fresh (F-SC-FreshJST)
#
# JST = UTC+9 なので:
#   "2026-04-27 06:00:00 UTC" = "2026-04-27 15:00:00 JST"
#   "2026-04-27 03:00:00 UTC" = "2026-04-27 12:00:00 JST"
#
# 2026-04-27 修正: 旧仕様の「JST 15:30 cutoff」は廃止。同一 JST 日であれば
# fresh とし、broker 側の真の有効性は validate_session_on_startup の API 呼出
# に委ねる（spec L81: "session 検証が失敗した場合のみ再ログイン"）。
# ---------------------------------------------------------------------------


@freeze_time("2026-04-27 06:00:00")  # UTC → JST 15:00:00
def test_is_session_fresh_same_day_morning() -> None:
    """saved_at_ms が JST 同日午前 → True。"""
    saved_at_ms = _ms_for_jst(2026, 4, 27, 12, 0, 0)  # JST 12:00 同日
    session = TachibanaSession(
        url_request=_SAMPLE_SESSION.url_request,
        url_master=_SAMPLE_SESSION.url_master,
        url_price=_SAMPLE_SESSION.url_price,
        url_event=_SAMPLE_SESSION.url_event,
        url_event_ws=_SAMPLE_SESSION.url_event_ws,
        zyoutoeki_kazei_c="0",
        expires_at_ms=saved_at_ms,
    )
    assert _is_session_fresh(session) is True


@freeze_time("2026-04-27 10:00:00")  # UTC → JST 19:00:00
def test_is_session_fresh_same_day_evening_after_old_cutoff() -> None:
    """saved_at_ms が JST 同日 17:47（旧 cutoff 15:30 後） → True。

    リグレッションガード: 2026-04-27 までは旧仕様の 15:30 JST cutoff により
    夕方ログイン後の再起動でダイアログが必ず表示される不具合があった。
    案 A 修正で同一 JST 日であれば fresh と判定する。
    """
    saved_at_ms = _ms_for_jst(2026, 4, 27, 17, 47, 0)  # JST 17:47 同日
    session = TachibanaSession(
        url_request=_SAMPLE_SESSION.url_request,
        url_master=_SAMPLE_SESSION.url_master,
        url_price=_SAMPLE_SESSION.url_price,
        url_event=_SAMPLE_SESSION.url_event,
        url_event_ws=_SAMPLE_SESSION.url_event_ws,
        zyoutoeki_kazei_c="0",
        expires_at_ms=saved_at_ms,
    )
    assert _is_session_fresh(session) is True


@freeze_time("2026-04-27 14:30:00")  # UTC → JST 23:30:00
def test_is_session_fresh_same_day_late_night() -> None:
    """saved_at_ms が JST 同日 23:00 → True（同一日付であれば常に fresh）。"""
    saved_at_ms = _ms_for_jst(2026, 4, 27, 23, 0, 0)
    session = TachibanaSession(
        url_request=_SAMPLE_SESSION.url_request,
        url_master=_SAMPLE_SESSION.url_master,
        url_price=_SAMPLE_SESSION.url_price,
        url_event=_SAMPLE_SESSION.url_event,
        url_event_ws=_SAMPLE_SESSION.url_event_ws,
        zyoutoeki_kazei_c="0",
        expires_at_ms=saved_at_ms,
    )
    assert _is_session_fresh(session) is True


@freeze_time("2026-04-27 03:00:00")  # UTC → JST 12:00:00 今日
def test_is_session_fresh_different_day_is_stale() -> None:
    """saved_at_ms が JST 昨日 10:00 → False（日付が違う）。"""
    saved_at_ms = _ms_for_jst(2026, 4, 26, 10, 0, 0)  # JST 昨日 10:00
    session = TachibanaSession(
        url_request=_SAMPLE_SESSION.url_request,
        url_master=_SAMPLE_SESSION.url_master,
        url_price=_SAMPLE_SESSION.url_price,
        url_event=_SAMPLE_SESSION.url_event,
        url_event_ws=_SAMPLE_SESSION.url_event_ws,
        zyoutoeki_kazei_c="0",
        expires_at_ms=saved_at_ms,
    )
    assert _is_session_fresh(session) is False


@freeze_time("2026-04-27 03:00:00")  # UTC → JST 12:00:00
def test_is_session_fresh_clock_skew_future_is_stale() -> None:
    """saved_at_ms が now より 1 秒後（未来） → False（clock skew ガード）。"""
    # now_ms is 2026-04-27 03:00:00 UTC in milliseconds
    now_ms = int(datetime(2026, 4, 27, 3, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    future_ms = now_ms + 1000  # 1 second in the future
    session = TachibanaSession(
        url_request=_SAMPLE_SESSION.url_request,
        url_master=_SAMPLE_SESSION.url_master,
        url_price=_SAMPLE_SESSION.url_price,
        url_event=_SAMPLE_SESSION.url_event,
        url_event_ws=_SAMPLE_SESSION.url_event_ws,
        zyoutoeki_kazei_c="0",
        expires_at_ms=future_ms,
    )
    assert _is_session_fresh(session) is False


def test_is_session_fresh_none_expires_at_ms_is_stale() -> None:
    """expires_at_ms が None のとき False。"""
    session = TachibanaSession(
        url_request=_SAMPLE_SESSION.url_request,
        url_master=_SAMPLE_SESSION.url_master,
        url_price=_SAMPLE_SESSION.url_price,
        url_event=_SAMPLE_SESSION.url_event,
        url_event_ws=_SAMPLE_SESSION.url_event_ws,
        zyoutoeki_kazei_c="0",
        expires_at_ms=None,
    )
    assert _is_session_fresh(session) is False


# ---------------------------------------------------------------------------
# D-1: F-SC-Atomic — interrupt scenario (os.replace fails)
# ---------------------------------------------------------------------------


def test_atomic_write_preserves_original_on_exception(tmp_path: Path) -> None:
    """F-SC-Atomic: os.replace 前に例外が発生しても元ファイルは保全されること。

    D-1: save_session 中に os.replace が OSError を raise しても、
    最終ファイル (tachibana_session.json) は旧内容のままであること。
    また .tmp ファイルが残っていないこと（例外ハンドラで削除済み）。
    """
    from unittest.mock import patch
    import engine.exchanges.tachibana_file_store as fs_mod

    original_content = json.dumps({"original": True})
    (tmp_path / SESSION_FILENAME).write_text(original_content, encoding="utf-8")

    with patch.object(fs_mod.os, "replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            save_session(tmp_path, _SAMPLE_SESSION)

    # 最終ファイルは旧データのまま
    assert (tmp_path / SESSION_FILENAME).read_text(encoding="utf-8") == original_content
    # .tmp ファイルは残らない（例外ハンドラで削除済み）
    assert list(tmp_path.glob("*.tmp")) == []
