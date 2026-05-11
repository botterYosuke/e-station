"""LiveDataBridge / LiveEcBridge — loop A → loop B bridge (Phase 3)

server.py (loop A) の fd_queue / ec_queue から受け取ったイベントを
TradingNode (loop B) 上の client に call_soon_threadsafe 経由で安全に渡す。

daemon thread として動作するため、メインスレッドが終了すれば自動停止する。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.nautilus.clients.kabu_station.kabu_station_data_client import (
        KabuStationLiveDataClient,
    )
    from engine.nautilus.clients.kabu_station.kabu_station_event_bridge import (
        KabuStationEventBridge,
    )
    from engine.nautilus.clients.tachibana_data import TachibanaLiveDataClient
    from engine.nautilus.clients.tachibana_event_bridge import TachibanaEventBridge

log = logging.getLogger(__name__)


class LiveDataBridge:
    """server.py (loop A) の fd_queue から trade dict を受け取り、
    TachibanaLiveDataClient._feed_trade_dict_sync() を loop B 上で安全に呼ぶ。
    daemon thread として動作する。
    """

    def __init__(
        self,
        data_client: "TachibanaLiveDataClient",
        fd_queue: queue.Queue,
        instrument_id: str,
        stop_event: threading.Event,
    ) -> None:
        self._client = data_client
        self._queue = fd_queue
        self._instrument_id = instrument_id
        self._stop = stop_event

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                trade_dict = self._queue.get(timeout=0.05)
                self._client._loop.call_soon_threadsafe(
                    self._client._feed_trade_dict_sync,
                    self._instrument_id,
                    trade_dict,
                )
            except queue.Empty:
                continue


class LiveEcBridge:
    """server.py (loop A) の ec_queue から EC イベントを受け取り、
    TachibanaEventBridge.process_ec_event() を loop B 上で安全に呼ぶ。
    daemon thread として動作する。
    """

    def __init__(
        self,
        event_bridge: "TachibanaEventBridge",
        ec_queue: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        self._bridge = event_bridge
        self._queue = ec_queue
        self._stop = stop_event

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                ec_event = self._queue.get(timeout=0.05)
                self._bridge._loop.call_soon_threadsafe(
                    self._bridge.process_ec_event, ec_event
                )
            except queue.Empty:
                continue


# ---------------------------------------------------------------------------
# issue #42 R2 CRITICAL-1: kabu_station 用 bridge (loop A → loop B)
# ---------------------------------------------------------------------------


class KabuLiveDataBridge:
    """server.py (loop A) の _live_fd_queue から kabu trade dict を受け取り、
    KabuStationLiveDataClient._feed_trade_dict_sync() を loop B 上で安全に呼ぶ。

    LiveDataBridge (tachibana 用) の kabu_station 版。trade dict 形式は
    server.py 側で kabu PUSH JSON から変換した {ts_ms, price, qty, side} 互換。
    daemon thread として動作する。
    """

    def __init__(
        self,
        data_client: "KabuStationLiveDataClient",
        fd_queue: queue.Queue,
        instrument_id: str,
        stop_event: threading.Event,
    ) -> None:
        self._client = data_client
        self._queue = fd_queue
        self._instrument_id = instrument_id
        self._stop = stop_event

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                trade_dict = self._queue.get(timeout=0.05)
                if self._client._loop is None:
                    # R3 M1: 観測性確保 — DEBUG だと運用ログから抜けて silent loss
                    # になる。loop 未注入は明確な regression なので WARNING で
                    # 可視化する (frame は破棄される = live data drop)。
                    log.warning(
                        "KabuLiveDataBridge: client._loop not yet set; "
                        "dropping frame (silent loss)"
                    )
                    continue
                self._client._loop.call_soon_threadsafe(
                    self._client._feed_trade_dict_sync,
                    self._instrument_id,
                    trade_dict,
                )
            except queue.Empty:
                continue


class KabuLiveEcBridge:
    """server.py (loop A) の _live_ec_queue から kabu fill record を受け取り、
    KabuStationEventBridge.process_order_record() を loop B 上で安全に呼ぶ。

    LiveEcBridge (tachibana 用) の kabu_station 版。kabu の fill は dict 形式で
    (OrderID/State/Symbol/...) を含む。daemon thread として動作する。
    """

    def __init__(
        self,
        event_bridge: "KabuStationEventBridge",
        ec_queue: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        self._bridge = event_bridge
        self._queue = ec_queue
        self._stop = stop_event

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                record = self._queue.get(timeout=0.05)
                if self._bridge._loop is None:
                    # R3 M1: 観測性確保 — DEBUG だと運用ログから抜けて silent loss
                    # になる。loop 未注入は明確な regression なので WARNING で
                    # 可視化する (record は破棄される = order fill drop)。
                    log.warning(
                        "KabuLiveEcBridge: bridge._loop not yet set; "
                        "dropping record (silent loss)"
                    )
                    continue
                self._bridge._loop.call_soon_threadsafe(
                    self._bridge.process_order_record, record
                )
            except queue.Empty:
                continue
