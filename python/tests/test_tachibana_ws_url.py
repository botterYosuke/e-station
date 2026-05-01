"""リグレッションガード: build_ws_url — WebSocket 購読 URL 形式（pure function）

§7.1 必須テスト (F-A URL エンコード修正 / H-E pure function化)

ROOT CAUSE: func_replace_urlecnode は ',' → '%2C' に変換する。
  build_ws_url がこの関数を p_evt_cmd に適用すると
  'ST,KP,FD' が 'ST%2CKP%2CFD' になり、サーバが FD 購読を認識しない。
  公式サンプル (e_api_websocket_receive_tel.py:573-585) はエンコードを使わない。
"""

from __future__ import annotations

import pytest

from engine.exchanges.tachibana import build_ws_url


_DEFAULT_BASE = "wss://example.invalid/event/"


def _parse_ws_params(url: str) -> dict[str, str]:
    """クエリ文字列を key=value dict に変換する（値はデコードしない）。"""
    query = url.split("?", 1)[1] if "?" in url else ""
    return dict(p.split("=", 1) for p in query.split("&") if "=" in p)


# ---------------------------------------------------------------------------
# F-A: p_evt_cmd のエンコードバグ
# ---------------------------------------------------------------------------


def test_build_ws_url_evt_cmd_has_raw_commas() -> None:
    """p_evt_cmd は生カンマ 'ST,KP,FD' でなければならない（'%2C' は不可）。

    公式サンプル e_api_websocket_receive_tel.py:582 より:
      str_url = str_url + '&' + 'p_evt_cmd=ST,KP,FD'
    """
    url = build_ws_url(_DEFAULT_BASE, "7203", "00")
    params = _parse_ws_params(url)
    assert params.get("p_evt_cmd") == "ST,KP,FD", (
        f"p_evt_cmd must be 'ST,KP,FD' but got {params.get('p_evt_cmd')!r}. "
        "func_replace_urlecnode must NOT be applied to WebSocket URL parameters."
    )


def test_build_ws_url_parameter_order() -> None:
    """パラメータ順は公式サンプルと一致しなければならない（p_rid が先頭、順番変更不可）。"""
    url = build_ws_url(_DEFAULT_BASE, "7203", "00")
    query = url.split("?", 1)[1]
    keys = [p.split("=")[0] for p in query.split("&")]
    expected = [
        "p_rid",
        "p_board_no",
        "p_gyou_no",
        "p_mkt_code",
        "p_eno",
        "p_evt_cmd",
        "p_issue_code",
    ]
    assert keys == expected, f"Parameter order mismatch: got {keys}, want {expected}"


def test_build_ws_url_fixed_params() -> None:
    """固定パラメータ値は公式サンプルと一致しなければならない。"""
    url = build_ws_url(_DEFAULT_BASE, "7203", "00")
    params = _parse_ws_params(url)
    assert params["p_rid"] == "22"
    assert params["p_board_no"] == "1000"
    assert params["p_gyou_no"] == "1"
    assert params["p_eno"] == "0"


def test_build_ws_url_issue_code() -> None:
    """p_issue_code は銘柄コードそのものでなければならない（エンコードなし）。"""
    url = build_ws_url(_DEFAULT_BASE, "6758", "00")
    params = _parse_ws_params(url)
    assert params["p_issue_code"] == "6758"


def test_build_ws_url_mkt_code_passed_through() -> None:
    """sizyou_c が p_mkt_code にそのまま入る（純関数なのでマスタロジック非依存）。"""
    url = build_ws_url(_DEFAULT_BASE, "7203", "01")
    params = _parse_ws_params(url)
    assert params["p_mkt_code"] == "01"


def test_build_ws_url_default_mkt_code() -> None:
    """マスタ未ロード時のデフォルト '00' が p_mkt_code に渡される。"""
    url = build_ws_url(_DEFAULT_BASE, "7203", "00")
    params = _parse_ws_params(url)
    assert params["p_mkt_code"] == "00"


def test_build_ws_url_base_url_used() -> None:
    """url_event_ws の値がベース URL として使われなければならない。"""
    url = build_ws_url("wss://custom.invalid/event/", "7203", "00")
    assert url.startswith("wss://custom.invalid/event/")


def test_build_ws_url_no_double_question_mark() -> None:
    """url_event_ws が '?' で終わっていても二重 '?' にならない。"""
    url = build_ws_url("wss://example.invalid/event/?", "7203", "00")
    assert "??" not in url
    query_part = url.split("?", 1)[1] if "?" in url else ""
    assert not query_part.startswith("&"), f"Query must not start with '&': {url!r}"


# ---------------------------------------------------------------------------
# H-E: _build_ws_url ラッパーは _lookup_sizyou_c を経由する（マスタロジック分離テスト）
# ---------------------------------------------------------------------------


def test_lookup_sizyou_c_returns_default_when_master_empty(tmp_path) -> None:
    """マスタ未ロード時は _SIZYOU_C_FALLBACK '00' を返す。"""
    from engine.exchanges.tachibana import TachibanaWorker

    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
    worker._master_records = {}
    assert worker._lookup_sizyou_c("7203") == "00"


def test_lookup_sizyou_c_reads_from_master(tmp_path) -> None:
    """マスタロード済みの場合は CLMIssueSizyouMstKabu から市場コードを取得する。"""
    from engine.exchanges.tachibana import TachibanaWorker

    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
    worker._master_records = {
        "CLMIssueSizyouMstKabu": [
            {"sIssueCode": "7203", "sSizyouC": "01"},
        ]
    }
    assert worker._lookup_sizyou_c("7203") == "01"


def test_sizyou_c_fallback_constant() -> None:
    """_SIZYOU_C_FALLBACK は '00' でなければならない（マスタ未ロード時の市場コード）。"""
    from engine.exchanges.tachibana import _SIZYOU_C_FALLBACK

    assert _SIZYOU_C_FALLBACK == "00"


# ---------------------------------------------------------------------------
# H-F: ホワイトリスト（[0-9A-Za-z]）バリデーション
# ---------------------------------------------------------------------------


def test_build_ws_url_rejects_ticker_with_control_char() -> None:
    """ticker に制御文字が含まれていたら ValueError を raise する。"""
    with pytest.raises(ValueError, match=r"outside \[0-9A-Za-z\]"):
        build_ws_url(_DEFAULT_BASE, "7203\x01", "00")


def test_build_ws_url_rejects_ticker_with_ampersand() -> None:
    """ticker に '&' が含まれていたら ValueError を raise する。"""
    with pytest.raises(ValueError, match=r"outside \[0-9A-Za-z\]"):
        build_ws_url(_DEFAULT_BASE, "7203&p_evil=1", "00")


def test_build_ws_url_rejects_ticker_with_percent() -> None:
    """ticker に '%' / 空白が含まれていたら ValueError を raise する（H-F）。"""
    with pytest.raises(ValueError, match=r"outside \[0-9A-Za-z\]"):
        build_ws_url(_DEFAULT_BASE, "7203%20", "00")


def test_build_ws_url_rejects_sizyou_c_with_question_mark() -> None:
    """sizyou_c に '?' が含まれていたら ValueError を raise する。"""
    with pytest.raises(ValueError, match=r"outside \[0-9A-Za-z\]"):
        build_ws_url(_DEFAULT_BASE, "7203", "?bad")


# ---------------------------------------------------------------------------
# H-G: タイムアウト定数の不変条件
# ---------------------------------------------------------------------------


def test_timeout_constants_ordering() -> None:
    """`_DEAD_FRAME_TIMEOUT_S < _DEPTH_SAFETY_TIMEOUT_S` および
    `_FRAME_STATS_INTERVAL_S < _DEPTH_SAFETY_TIMEOUT_S` を保証する（H-G / M2-3）。

    strict less-than: stats interval must fire *before* depth-safety declares
    depth_unavailable, so the first stats log is always visible in the warn context.
    """
    import engine.exchanges.tachibana_ws as _ws_mod

    assert _ws_mod._DEAD_FRAME_TIMEOUT_S < _ws_mod._DEPTH_SAFETY_TIMEOUT_S
    assert _ws_mod._FRAME_STATS_INTERVAL_S < _ws_mod._DEPTH_SAFETY_TIMEOUT_S
