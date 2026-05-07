"""E2E 用 live 戦略サンプル — デモ環境での動作確認用。

Usage:
    python examples/live_sample.py

環境変数:
    SECOND_PASSWORD: 第二暗証番号（live_session に直接渡す場合）
"""

from __future__ import annotations

import logging
import os
import time
from decimal import Decimal

from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig

log = logging.getLogger(__name__)


class LiveSampleConfig(StrategyConfig, frozen=True):
    instrument_id: str = "8306.T"
    max_qty: int = 100


class LiveSampleStrategy(Strategy):
    """デモ環境動作確認用の最小戦略。注文を出さずにログだけ出す。"""

    def __init__(self, config: LiveSampleConfig) -> None:
        super().__init__(config)
        self.cfg = config
        self._bar_count = 0

    def on_start(self) -> None:
        log.info("[LiveSample] strategy started instrument=%s", self.cfg.instrument_id)

    def on_trade_tick(self, tick) -> None:
        self._bar_count += 1
        if self._bar_count % 100 == 0:
            log.info("[LiveSample] received %d ticks, last price=%s", self._bar_count, tick.price)

    def on_stop(self) -> None:
        log.info("[LiveSample] strategy stopped after %d ticks", self._bar_count)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    second_password = os.environ.get("SECOND_PASSWORD")
    if not second_password:
        print("SECOND_PASSWORD 環境変数を設定してください")
        return

    from engine.replay_session import LiveSession

    events: list[dict] = []

    def on_event(evt: dict) -> None:
        events.append(evt)
        kind = evt.get("event", "")
        print(f"[event] {kind}: {evt}")

    session = LiveSession(venue="tachibana", demo=True, second_password=second_password)
    session.login()

    try:
        session.run(
            instrument_id="8306.T",
            strategy_file=__file__,
            max_qty=100,
            max_notional_jpy=500_000,
            on_event=on_event,
            strategy_id="live-sample",
        )
    except KeyboardInterrupt:
        session.stop()
        print("stopped by KeyboardInterrupt")


# StrategyConfig がここで設定されることを strategy_loader が期待する
STRATEGY_CONFIG = LiveSampleConfig(instrument_id="8306.T", max_qty=100)
STRATEGY_CLASS = LiveSampleStrategy


if __name__ == "__main__":
    main()
