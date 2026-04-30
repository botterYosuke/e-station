"""No-op strategy for unit/integration tests. No constructor kwargs required."""
from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy


class NoOpTestStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__(config=StrategyConfig(strategy_id="noop-test"))
