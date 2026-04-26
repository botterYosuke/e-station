"""NautilusRunner: nautilus BacktestEngine / LiveExecutionEngine のライフサイクル管理 (N0.2)

N0 では backtest のみ。start_live() は stub。
IPC ディスパッチャは N0.2/N1.1 で server.py に追加予定（現時点は直接呼び出し用 API のみ）。

spec.md §3.2: CacheConfig.database = None（永続化 OFF）を必ず維持すること。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

from engine.nautilus.data_loader import KlineRow, klines_to_bars
from engine.nautilus.strategies.buy_and_hold import BuyAndHoldStrategy

log = logging.getLogger(__name__)

_BYPASS_LOG = LoggingConfig(bypass_logging=True)

_CURRENCY_MAP = {
    "JPY": JPY,
}


@dataclass
class BacktestResult:
    """start_backtest() の戻り値。IPC EngineStopped イベントに対応する。"""

    strategy_id: str
    final_equity: Decimal
    fill_timestamps: list[int] = field(default_factory=list)
    fill_last_prices: list[str] = field(default_factory=list)


class NautilusRunner:
    """nautilus エンジンのライフサイクルを管理するワーカー。

    N0: start_backtest() のみ実装。start_live() は stub。
    N1 以降: server.py のディスパッチャから StartEngine Command で呼ばれる。
    Python 単独モード: CLI から直接呼び出し可能（IPC 経由でなくてもよい）。
    """

    def __init__(self) -> None:
        self._engine: BacktestEngine | None = None

    def start_backtest(
        self,
        *,
        strategy_id: str,
        ticker: str,
        venue: str,
        klines: list[KlineRow],
        initial_cash: int,
        currency: str = "JPY",
    ) -> BacktestResult:
        """バックテストを実行し結果を返す。

        strategy_id: IPC 経由で BacktestResult に返す外部 ID ("buy-and-hold" 等)。
            nautilus 内部の StrategyConfig.strategy_id ("buy-and-hold-001") とは別物。
            N1 の EngineStopped IPC イベントには本パラメータの値を使う。

        spec.md §3.2: assert config.cache.database is None を内部で検証する。
        """
        if currency not in _CURRENCY_MAP:
            raise ValueError(
                f"Unsupported currency: {currency!r}. Supported: {list(_CURRENCY_MAP)}"
            )
        cur = _CURRENCY_MAP[currency]

        safe_id = strategy_id.replace("-", "").replace("_", "")[:8].upper() or "BACKTEST"
        cfg = BacktestEngineConfig(
            trader_id=f"RUNNER-{safe_id}",
            logging=_BYPASS_LOG,
        )
        # persistence 無効化の不変条件（spec.md §3.2）
        assert cfg.cache.database is None, "nautilus persistence must be disabled"

        engine = BacktestEngine(config=cfg)
        self._engine = engine
        try:
            engine.add_venue(
                venue=Venue(venue),
                oms_type=OmsType.NETTING,
                account_type=AccountType.CASH,
                base_currency=cur,
                starting_balances=[Money(initial_cash, cur)],
            )

            # Instrument: N0 ではハードコード Equity（data-mapping.md §1 N0 仮置き）
            from engine.nautilus.instrument_factory import make_equity_instrument
            instrument = make_equity_instrument(ticker, venue)
            engine.add_instrument(instrument)

            bars = klines_to_bars(ticker, venue, klines)
            if bars:
                engine.add_data(bars)

            strategy_instance = _make_strategy(strategy_id, instrument.id)
            engine.add_strategy(strategy_instance)

            log.info(
                "[NautilusRunner] engine.run() starting: strategy=%r ticker=%r bars=%d",
                strategy_id, ticker, len(bars),
            )
            engine.run()
            log.info("[NautilusRunner] engine.run() completed: strategy=%r", strategy_id)

            # 約定データ収集
            fill_timestamps, fill_last_prices = _collect_fill_data(engine)

            # 最終残高（文字列精度保持、H2 規約）
            account = engine.kernel.portfolio.account(Venue(venue))
            if account is None:
                raise RuntimeError(
                    f"[NautilusRunner] portfolio.account returned None for venue={venue!r}"
                )
            balance = account.balance_total(cur)
            final_equity = balance.as_decimal()

            return BacktestResult(
                strategy_id=strategy_id,
                final_equity=final_equity,
                fill_timestamps=fill_timestamps,
                fill_last_prices=fill_last_prices,
            )
        except Exception:
            log.error(
                "[NautilusRunner] start_backtest failed for strategy=%r ticker=%r",
                strategy_id, ticker, exc_info=True,
            )
            raise
        finally:
            engine.dispose()
            self._engine = None

    def start_live(self) -> None:
        """N0 stub。N2 で LiveExecutionEngine を組み立てる。

        Ready.capabilities.nautilus.live = false (N0)。
        """
        log.info("start_live() is a stub in N0; live execution not yet implemented")

    def stop(self) -> None:
        if self._engine is not None:
            try:
                self._engine.dispose()
            except Exception as exc:
                log.warning("stop(): engine dispose raised: %s", exc)
            self._engine = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_strategy(strategy_id: str, instrument_id):
    """nautilus Strategy インスタンスを生成して返す。

    strategy_id は外部 IPC 用 ID ("buy-and-hold" 等)。
    nautilus 内部の StrategyConfig.strategy_id ("buy-and-hold-001") とは別物。
    N1 の EngineStopped IPC イベントには呼び出し元から渡された strategy_id の値を使う。
    """
    if strategy_id == "buy-and-hold":
        return BuyAndHoldStrategy(instrument_id=instrument_id)
    raise ValueError(f"Unknown strategy_id: {strategy_id!r}. N0 supports 'buy-and-hold' only.")


def _collect_fill_data(engine: BacktestEngine) -> tuple[list[int], list[str]]:
    """約定タイムスタンプと約定価格を収集して返す（決定論性テスト用）。

    戻り値: (sorted_timestamps, sorted_last_prices)
    """
    try:
        fills = engine.kernel.cache.orders()
        timestamps: list[int] = []
        last_prices: list[str] = []
        for order in fills:
            if order.is_closed:
                # 最終イベントの ts_last を使う
                ts = getattr(order, "ts_last", None)
                if ts is not None:
                    timestamps.append(ts)
                lp = getattr(order, "avg_px", None)
                if lp is not None:
                    last_prices.append(str(lp))
        return sorted(timestamps), sorted(last_prices)
    except Exception as exc:
        log.warning(
            "[NautilusRunner] _collect_fill_data failed: %s", exc, exc_info=True
        )
        return [], []  # 意図的フォールバック: IPC EngineStopped の送出をブロックしない
