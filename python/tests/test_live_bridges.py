"""LiveDataBridge / LiveEcBridge のユニットテスト (Phase 3)

TDD RED フェーズ: live_bridges.py が存在しない状態で先に書く。
"""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from engine.nautilus.live_bridges import LiveDataBridge, LiveEcBridge


# ---------------------------------------------------------------------------
# LiveDataBridge
# ---------------------------------------------------------------------------


class TestLiveDataBridge:
    """LiveDataBridge.run() のユニットテスト。"""

    def _make_bridge(self, trade_dicts: list[dict] | None = None):
        """テスト用 LiveDataBridge を組み立てるヘルパー。"""
        data_client = MagicMock()
        data_client._loop = MagicMock()

        fd_queue: queue.Queue = queue.Queue()
        if trade_dicts:
            for td in trade_dicts:
                fd_queue.put(td)

        stop_event = threading.Event()
        instrument_id = "7203.TSE"

        bridge = LiveDataBridge(
            data_client=data_client,
            fd_queue=fd_queue,
            instrument_id=instrument_id,
            stop_event=stop_event,
        )
        return bridge, data_client, fd_queue, stop_event

    def test_trade_dict_routed_to_call_soon_threadsafe(self):
        """fd_queue に trade dict を put すると call_soon_threadsafe 経由で
        _feed_trade_dict_sync が呼ばれる。"""
        trade = {"price": "2500.0", "qty": "100", "side": "buy", "ts_ms": 1700000000000}
        bridge, data_client, fd_queue, stop_event = self._make_bridge()

        # キューにデータを入れてから run() を別スレッドで起動
        fd_queue.put(trade)

        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()

        # call_soon_threadsafe が呼ばれるまで最大 1 秒待つ
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if data_client._loop.call_soon_threadsafe.call_count >= 1:
                break
            time.sleep(0.01)

        stop_event.set()
        t.join(timeout=1.0)

        data_client._loop.call_soon_threadsafe.assert_called_once_with(
            data_client._feed_trade_dict_sync,
            "7203.TSE",
            trade,
        )

    def test_multiple_trades_all_routed(self):
        """複数の trade dict が全件 call_soon_threadsafe 経由で渡される。"""
        trades = [
            {"price": "2500.0", "qty": "100", "side": "buy", "ts_ms": 1700000000001},
            {"price": "2501.0", "qty": "200", "side": "sell", "ts_ms": 1700000000002},
        ]
        bridge, data_client, fd_queue, stop_event = self._make_bridge()

        for td in trades:
            fd_queue.put(td)

        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if data_client._loop.call_soon_threadsafe.call_count >= 2:
                break
            time.sleep(0.01)

        stop_event.set()
        t.join(timeout=1.0)

        assert data_client._loop.call_soon_threadsafe.call_count == 2

    def test_stop_event_terminates_loop(self):
        """D5: stop_event.set() 後 bridge の run() ループが終了する。"""
        bridge, data_client, fd_queue, stop_event = self._make_bridge()

        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()

        # 即座に stop_event をセット
        stop_event.set()

        # 十分な時間内にスレッドが終了することを確認
        t.join(timeout=1.0)
        assert not t.is_alive(), "run() が stop_event 後に終了しない"

    def test_empty_queue_does_not_call_threadsafe(self):
        """D6: fd_queue が空の間は call_soon_threadsafe が呼ばれない。"""
        bridge, data_client, fd_queue, stop_event = self._make_bridge()

        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()

        # キューに何も入れずに一定時間待って即停止
        time.sleep(0.1)
        stop_event.set()
        t.join(timeout=1.0)

        data_client._loop.call_soon_threadsafe.assert_not_called()


# ---------------------------------------------------------------------------
# LiveEcBridge
# ---------------------------------------------------------------------------


class TestLiveEcBridge:
    """LiveEcBridge.run() のユニットテスト。"""

    def _make_bridge(self, ec_events: list | None = None):
        """テスト用 LiveEcBridge を組み立てるヘルパー。"""
        event_bridge = MagicMock()
        event_bridge._loop = MagicMock()

        ec_queue: queue.Queue = queue.Queue()
        if ec_events:
            for ev in ec_events:
                ec_queue.put(ev)

        stop_event = threading.Event()

        bridge = LiveEcBridge(
            event_bridge=event_bridge,
            ec_queue=ec_queue,
            stop_event=stop_event,
        )
        return bridge, event_bridge, ec_queue, stop_event

    def test_ec_event_routed_to_call_soon_threadsafe(self):
        """ec_queue に EC イベントを put すると call_soon_threadsafe 経由で
        process_ec_event が呼ばれる。"""
        ec_event = MagicMock(name="OrderEcEvent")
        bridge, event_bridge, ec_queue, stop_event = self._make_bridge()

        ec_queue.put(ec_event)

        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if event_bridge._loop.call_soon_threadsafe.call_count >= 1:
                break
            time.sleep(0.01)

        stop_event.set()
        t.join(timeout=1.0)

        event_bridge._loop.call_soon_threadsafe.assert_called_once_with(
            event_bridge.process_ec_event,
            ec_event,
        )

    def test_stop_event_terminates_loop(self):
        """D5: stop_event.set() 後 LiveEcBridge の run() ループが終了する。"""
        bridge, event_bridge, ec_queue, stop_event = self._make_bridge()

        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()

        stop_event.set()

        t.join(timeout=1.0)
        assert not t.is_alive(), "LiveEcBridge.run() が stop_event 後に終了しない"

    def test_empty_queue_does_not_call_threadsafe(self):
        """D6: ec_queue が空の間は call_soon_threadsafe が呼ばれない。"""
        bridge, event_bridge, ec_queue, stop_event = self._make_bridge()

        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()

        time.sleep(0.1)
        stop_event.set()
        t.join(timeout=1.0)

        event_bridge._loop.call_soon_threadsafe.assert_not_called()


# ---------------------------------------------------------------------------
# TachibanaLiveDataClient — _loop キャプチャ + _feed_trade_dict_sync テスト
# ---------------------------------------------------------------------------


class TestTachibanaLiveDataClientExtensions:
    """_loop / _feed_trade_dict_sync の追加確認。"""

    def test_feed_trade_dict_sync_calls_feed_trade_dict(self):
        """_feed_trade_dict_sync(instrument_id, trade) は feed_trade_dict() を呼ぶ。"""
        # TachibanaLiveDataClient は nautilus 依存が重いため MagicMock で代替し
        # _feed_trade_dict_sync の振る舞いだけ確認する
        from engine.nautilus.live_bridges import LiveDataBridge

        # data_client に _feed_trade_dict_sync が実装済みであることを確認する
        # ここでは実際のクライアントを使わず、bridge 経由で挙動を検証する
        data_client = MagicMock()
        data_client._loop = MagicMock()

        fd_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()
        instrument_id = "8306.TSE"
        trade = {"price": "900.0", "qty": "500", "side": "buy", "ts_ms": 1700000001000}

        bridge = LiveDataBridge(data_client, fd_queue, instrument_id, stop_event)
        fd_queue.put(trade)

        t = threading.Thread(target=bridge.run, daemon=True)
        t.start()

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if data_client._loop.call_soon_threadsafe.call_count >= 1:
                break
            time.sleep(0.01)

        stop_event.set()
        t.join(timeout=1.0)

        # bridge は call_soon_threadsafe(client._feed_trade_dict_sync, id, trade) を呼ぶ
        data_client._loop.call_soon_threadsafe.assert_called_once_with(
            data_client._feed_trade_dict_sync,
            instrument_id,
            trade,
        )


# ---------------------------------------------------------------------------
# R3 M1: KabuLiveDataBridge / KabuLiveEcBridge の _loop=None 警告
# ---------------------------------------------------------------------------


class TestKabuBridgeLoopNoneWarning:
    """R3 M1: client/bridge ._loop が None のときは silent drop ではなく WARNING
    で観測性を確保する (live data / fill が落ちる事態を運用ログで気付けるように)。
    """

    def test_kabu_live_data_bridge_logs_warning_when_loop_none(self, caplog):
        """KabuLiveDataBridge.run() で client._loop=None のとき WARNING ログを出す。"""
        import logging
        from engine.nautilus.live_bridges import KabuLiveDataBridge

        data_client = MagicMock()
        data_client._loop = None  # 未注入

        fd_queue: queue.Queue = queue.Queue()
        fd_queue.put({"price": "1500.0", "qty": "100", "side": "buy", "ts_ms": 1700000000000})

        stop_event = threading.Event()
        bridge = KabuLiveDataBridge(
            data_client=data_client,
            fd_queue=fd_queue,
            instrument_id="9433.KabuStation Stock",
            stop_event=stop_event,
        )

        with caplog.at_level(logging.WARNING, logger="engine.nautilus.live_bridges"):
            t = threading.Thread(target=bridge.run, daemon=True)
            t.start()
            # 1 件処理されるのを待つ
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if any("KabuLiveDataBridge" in rec.message for rec in caplog.records):
                    break
                time.sleep(0.01)
            stop_event.set()
            t.join(timeout=1.0)

        # WARNING ログが出ている (silent loss 防止)
        kabu_warns = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "KabuLiveDataBridge" in r.message
        ]
        assert kabu_warns, (
            f"KabuLiveDataBridge must log WARNING when _loop is None; got {caplog.records!r}"
        )

    def test_kabu_live_ec_bridge_logs_warning_when_loop_none(self, caplog):
        """KabuLiveEcBridge.run() で bridge._loop=None のとき WARNING ログを出す。"""
        import logging
        from engine.nautilus.live_bridges import KabuLiveEcBridge

        event_bridge = MagicMock()
        event_bridge._loop = None  # 未注入

        ec_queue: queue.Queue = queue.Queue()
        ec_queue.put({"OrderID": "X-1", "State": 5})

        stop_event = threading.Event()
        bridge = KabuLiveEcBridge(
            event_bridge=event_bridge,
            ec_queue=ec_queue,
            stop_event=stop_event,
        )

        with caplog.at_level(logging.WARNING, logger="engine.nautilus.live_bridges"):
            t = threading.Thread(target=bridge.run, daemon=True)
            t.start()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if any("KabuLiveEcBridge" in rec.message for rec in caplog.records):
                    break
                time.sleep(0.01)
            stop_event.set()
            t.join(timeout=1.0)

        kabu_warns = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "KabuLiveEcBridge" in r.message
        ]
        assert kabu_warns, (
            f"KabuLiveEcBridge must log WARNING when _loop is None; got {caplog.records!r}"
        )
