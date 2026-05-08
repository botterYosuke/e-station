"""server.py _on_kabu_board_push / _on_kabu_trade_push サーバー層テスト (C1 配線).

spec: adapter-type-boundary.md Step 3 残項目
  - seq 単調増加（pipeline 呼び出し前に +1）
  - Symbol 欠損 PUSH → 警告ログ + outbox への書き込みなし（seq も変化しない）
  - parse/map エラー → 例外を封じ込め・outbox 変化なし
  - _kabu_push_ssid=None 時に engine_session_id へフォールバック
  - .model_dump(mode="json") 直結禁止: outbox に書かれる dict が wire DTO 型から
    生成されていることを event フィールド存在で明示確認

リグレッション確認:
  - seq を increment しなければ test_seq_increments_on_each_call が FAIL
  - Symbol ガードを除けば test_missing_symbol_skips_outbox が FAIL（KeyError で crash）
  - except を除けば test_parse_error_does_not_raise が FAIL
"""
from __future__ import annotations

import asyncio
import uuid
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.kabusapi_adapter import KabuStationAdapter
from engine.exchanges.kabusapi_register import RegisterSet
from engine.server import DataEngineServer


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


class _StubOutbox:
    """_Broadcaster の最小スタブ。"""

    def __init__(self) -> None:
        self._q: deque[dict] = deque()

    def append(self, item: dict) -> None:
        self._q.append(item)

    def send_to(self, _ws: object, item: dict) -> None:
        self._q.append(item)

    def count(self) -> int:
        return 0

    def __len__(self) -> int:
        return len(self._q)

    def __iter__(self):
        return iter(list(self._q))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_VALID_BOARD: dict = {
    "Symbol": "1234",
    "CurrentPriceTime": "2024-01-01T15:00:00+09:00",
    "CurrentPrice": 3000.0,
    "Buy1": {"Price": 3000.0, "Qty": 100.0, "Time": "15:00:00", "Sign": "0101"},
    "Sell1": {"Price": 3001.0, "Qty": 50.0, "Time": "15:00:00", "Sign": "0101"},
}


def _make_server(*, ssid: str | None = "test-ssid", seq: int = 0) -> DataEngineServer:
    """最小フィールドのみ設定した DataEngineServer を返す。

    全コンストラクタの初期化は patch で迂回し、必要なフィールドのみ手動注入する。
    """
    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _StubOutbox()  # type: ignore[assignment]
    server._kabu_push_ssid = ssid
    server._kabu_push_seq = seq
    server._kabu_adapter = KabuStationAdapter([])
    server._engine_session_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    return server


# ---------------------------------------------------------------------------
# _on_kabu_board_push — seq 管理
# ---------------------------------------------------------------------------


class TestBoardPushSeq:
    def test_seq_increments_on_each_call(self) -> None:
        """2 回呼ぶと _kabu_push_seq が 0→1→2 と増える。"""
        s = _make_server()
        assert s._kabu_push_seq == 0
        s._on_kabu_board_push(_VALID_BOARD)
        assert s._kabu_push_seq == 1
        s._on_kabu_board_push(_VALID_BOARD)
        assert s._kabu_push_seq == 2

    def test_seq_in_outbox_matches_post_increment_value(self) -> None:
        """outbox の sequence_id が +1 後の _kabu_push_seq と一致する。"""
        s = _make_server(seq=9)
        s._on_kabu_board_push(_VALID_BOARD)
        payload = s._outbox._q[-1]
        assert payload["sequence_id"] == 10

    def test_two_calls_produce_consecutive_seqs(self) -> None:
        """連続 PUSH の sequence_id が 1 ずつ増加する。"""
        s = _make_server()
        s._on_kabu_board_push(_VALID_BOARD)
        s._on_kabu_board_push(_VALID_BOARD)
        seqs = [item["sequence_id"] for item in s._outbox._q]
        assert seqs == [1, 2]


# ---------------------------------------------------------------------------
# _on_kabu_board_push — Symbol 欠損ガード
# ---------------------------------------------------------------------------


class TestBoardPushSymbolGuard:
    def test_missing_symbol_key_skips_outbox(self) -> None:
        """Symbol キーなし PUSH はアウトボックスに書かれない。"""
        s = _make_server()
        s._on_kabu_board_push({"CurrentPrice": 100.0})
        assert len(s._outbox._q) == 0

    def test_missing_symbol_does_not_increment_seq(self) -> None:
        """Symbol 欠損時は seq も進まない。"""
        s = _make_server(seq=5)
        s._on_kabu_board_push({"CurrentPrice": 100.0})
        assert s._kabu_push_seq == 5

    def test_empty_symbol_string_skips_outbox(self) -> None:
        """Symbol が空文字列の PUSH はアウトボックスに書かれない。"""
        s = _make_server()
        raw = dict(_VALID_BOARD)
        raw["Symbol"] = ""
        s._on_kabu_board_push(raw)
        assert len(s._outbox._q) == 0


# ---------------------------------------------------------------------------
# _on_kabu_board_push — 例外封じ込め
# ---------------------------------------------------------------------------


class TestBoardPushExceptionIsolation:
    def test_parse_error_does_not_raise(self) -> None:
        """CurrentPriceTime=None など parse エラーはサーバーをクラッシュさせない。

        Fix: _on_kabu_board_push の except Exception → log.warning。
        """
        s = _make_server()
        bad = {"Symbol": "1234", "CurrentPriceTime": None}
        s._on_kabu_board_push(bad)  # must not raise

    def test_parse_error_does_not_write_outbox(self) -> None:
        """parse エラー後も outbox は変化しない（中途半端なデータを書かない）。

        設計上の tradeoff: Symbol チェック後・parse 前に seq がインクリメントされるため、
        parse 失敗時は seq が消費されたまま outbox は空白になる（ギャップ発生）。
        kabu は DepthSnapshot のみであり Rust 側 gap detector への影響はない。
        この動作は意図的であり「バグ動作 pin」ではない。
        """
        s = _make_server()
        bad = {"Symbol": "1234", "CurrentPriceTime": None}
        s._on_kabu_board_push(bad)
        assert len(s._outbox._q) == 0
        assert s._kabu_push_seq == 1  # seq は消費される（Symbol チェック後のため）

    def test_seq_consumed_on_parse_error(self) -> None:
        """parse 失敗でも seq がインクリメントされる（gap 発生は意図的）。"""
        s = _make_server(seq=5)
        bad = {"Symbol": "1234", "CurrentPriceTime": None}
        s._on_kabu_board_push(bad)
        assert s._kabu_push_seq == 6  # +1 consumed
        assert len(s._outbox._q) == 0

    def test_trade_push_does_not_affect_board_seq(self) -> None:
        """_on_kabu_trade_push は _kabu_push_seq を変更しない。"""
        s = _make_server(seq=7)
        s._on_kabu_trade_push(_VALID_BOARD)
        assert s._kabu_push_seq == 7


# ---------------------------------------------------------------------------
# _on_kabu_board_push — ssid フォールバック
# ---------------------------------------------------------------------------


class TestBoardPushSsidFallback:
    def test_explicit_ssid_is_used(self) -> None:
        """_kabu_push_ssid が設定されているとき、それが wire に渡る。"""
        s = _make_server(ssid="engine-sess:kabu_push")
        s._on_kabu_board_push(_VALID_BOARD)
        assert s._outbox._q[-1]["stream_session_id"] == "engine-sess:kabu_push"

    def test_ssid_fallback_to_engine_session_id_when_none(self) -> None:
        """_kabu_push_ssid=None のとき _engine_session_id を文字列化したものが ssid になる。

        Fix: _on_kabu_board_push の ssid = self._kabu_push_ssid or str(self._engine_session_id)。
        ssid が None のまま wire に渡ると mapper が ValueError を raise してしまう。
        """
        s = _make_server(ssid=None)
        s._on_kabu_board_push(_VALID_BOARD)
        assert len(s._outbox._q) == 1
        assert s._outbox._q[-1]["stream_session_id"] == str(s._engine_session_id)


# ---------------------------------------------------------------------------
# _on_kabu_board_push — wire DTO 型経由（.model_dump 直結禁止の明示確認）
# ---------------------------------------------------------------------------


class TestBoardPushWireDtoPath:
    def test_outbox_dict_has_event_field(self) -> None:
        """outbox の dict に 'event' フィールドがあることで mapper 経由を確認。

        adapter model (OrderBook) は wire 責務フィールドを持たない（test_models.py が保証）。
        'event' が存在 ⟹ wire DTO (DepthSnapshotMsg) 経由で model_dump されている。
        """
        s = _make_server()
        s._on_kabu_board_push(_VALID_BOARD)
        payload = s._outbox._q[-1]
        assert payload.get("event") == "DepthSnapshot"

    def test_outbox_dict_has_venue_field(self) -> None:
        """outbox の dict に 'venue' フィールドがあることで mapper 経由を確認。"""
        s = _make_server()
        s._on_kabu_board_push(_VALID_BOARD)
        assert s._outbox._q[-1].get("venue") == "kabu_station"

    def test_outbox_dict_has_no_none_top_level_values(self) -> None:
        """exclude_none=True で dump されているため top-level に None がない。"""
        s = _make_server()
        s._on_kabu_board_push(_VALID_BOARD)
        for key, val in s._outbox._q[-1].items():
            assert val is not None, f"unexpected None at key={key!r}"


# ---------------------------------------------------------------------------
# _on_kabu_trade_push
# ---------------------------------------------------------------------------


class TestTradePush:
    def test_valid_push_writes_trades_event(self) -> None:
        s = _make_server()
        s._on_kabu_trade_push(_VALID_BOARD)
        payload = s._outbox._q[-1]
        assert payload["event"] == "Trades"
        assert payload["venue"] == "kabu_station"

    def test_missing_symbol_skips_outbox(self) -> None:
        s = _make_server()
        s._on_kabu_trade_push({"CurrentPrice": 100.0})
        assert len(s._outbox._q) == 0

    def test_parse_error_does_not_raise(self) -> None:
        """CurrentPrice=None → parse エラー → クラッシュしない。"""
        s = _make_server()
        s._on_kabu_trade_push({"Symbol": "1234", "CurrentPrice": None,
                                "CurrentPriceTime": "2024-01-01T15:00:00+09:00"})

    def test_ssid_fallback_when_none(self) -> None:
        """_kabu_push_ssid=None でも Trades が outbox に書かれる。"""
        s = _make_server(ssid=None)
        s._on_kabu_trade_push(_VALID_BOARD)
        assert len(s._outbox._q) == 1
        assert s._outbox._q[-1]["stream_session_id"] == str(s._engine_session_id)


# ---------------------------------------------------------------------------
# PUSH WebSocket 起動確認（Issue #28 層1 Fix）
# ---------------------------------------------------------------------------


class TestKabuPushWsTaskStarted:
    @pytest.mark.asyncio
    async def test_kabu_push_ws_task_started_after_login(self) -> None:
        """_startup_kabu_station() ログイン成功後に kabusapi_ws.connect() が
        asyncio.create_task で起動されることを確認する（Issue #28 層1）。"""
        with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
            server = DataEngineServer()

        # 必要な最小フィールドを注入
        server._outbox = _StubOutbox()  # type: ignore[assignment]
        server._engine_session_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        server._kabu_env = "verify"
        server._kabu_venue = None
        server._kabu_login_inflight = asyncio.Lock()
        server._kabu_startup_task = None
        server._kabu_fill_poller_task = None
        server._kabu_push_task = None  # type: ignore[attr-defined]
        server._kabu_push_ssid = None
        server._kabu_push_seq = 0
        server._kabu_register_set = RegisterSet()  # type: ignore[attr-defined]
        server._connected_venue = None
        server._dev_kabu_login_allowed = False
        server._dev_kabu_trade_password_allowed = False
        server._live_state = MagicMock()

        from engine.exchanges.kabusapi_auth import (
            KabuLoginCancelledError,
            KabuConnectionError,
        )

        # mock_venue: startup_login() を成功させる
        mock_venue = MagicMock()
        mock_venue.startup_login = AsyncMock(return_value="test-token")
        server._kabu_venue = mock_venue

        connect_called_with: list = []

        async def fake_connect(**kwargs):
            connect_called_with.append(kwargs)
            # 即座に返る（タスクとして起動されるので await されるまで実行されない）
            await asyncio.sleep(0)

        with patch(
            "engine.server.kabusapi_ws.connect",
            side_effect=fake_connect,
        ) as mock_connect:
            with patch.object(server, "_emit"):
                with patch.object(server, "_kabu_fill_poller", return_value=asyncio.sleep(0)):
                    await server._startup_kabu_station()

        # _kabu_push_task が None でないことを確認（タスクが起動された）
        assert server._kabu_push_task is not None, (
            "_kabu_push_task should be set after successful login"
        )
        # タスクがキャンセルされていないことを確認
        assert not server._kabu_push_task.cancelled()
        # タスクをクリーンアップ
        server._kabu_push_task.cancel()
        try:
            await server._kabu_push_task
        except (asyncio.CancelledError, Exception):
            pass
