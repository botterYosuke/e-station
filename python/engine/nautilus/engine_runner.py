"""NautilusRunner: nautilus BacktestEngine / LiveExecutionEngine のライフサイクル管理 (N0.2 / N1.4)

N0: ``start_backtest()`` (Bar 入力) のみ実装。
N1.4: ``start_backtest_replay()`` (J-Quants TradeTick / Bar 入力) を新設。
``on_event`` callback で IPC イベント (``EngineStarted`` / ``ReplayDataLoaded`` /
``EngineStopped``) を呼出側 (server.py) に渡す。

spec.md §3.2: CacheConfig.database = None（永続化 OFF）を必ず維持すること。

設計判断 (N1.4):
- 後方互換 ``start_backtest()`` を残し、replay 経路は別関数 ``start_backtest_replay()``
  にした。N0 互換テスト 8 件を破壊しないため。
- replay 内部 venue は instrument_id の venue (``"TSE"``) を使う。これは
  jquants_loader が ``"1301.TSE"`` のように TSE タグで TradeTick を emit するため、
  ``BacktestEngine`` 内のデータ整合上 TSE で揃える必要があるため。D5 で言う
  「venue タグは ``replay``」は **IPC EngineEvent (Trades/KlineUpdate) の wire 表現**
  に関するもので、BacktestEngine 内部 venue とは独立。本実装では IPC で送出する
  venue 文字列のみ ``"replay"`` 等にスタンプ可能 (本タスクでは market data 複製は
  N1.11 まで no-op なので未使用)。
- market data 複製送出 (Trades/KlineUpdate) は N1.4 では no-op。N1.11 streaming で実装。
- SubmitOrder の replay 経路は N1.4 ではスケルトンのみ (ユーザー Strategy 自身が
  on_trade_tick / on_bar で submit_order するのが本筋)。外部 IPC からの SubmitOrder の
  replay venue 内部 queue 投入は N1.5 で実装。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.objects import Money

from engine.nautilus.data_loader import KlineRow, klines_to_bars
from engine.nautilus.jquants_loader import (
    load_daily_bars,
    load_minute_bars,
    load_trades,
)
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


@dataclass
class ReplayBacktestResult(BacktestResult):
    """``start_backtest_replay()`` の戻り値。

    BacktestResult を拡張し、ロード件数と IPC EngineStarted/EngineStopped で使う
    タイムスタンプを保持する。
    """

    bars_loaded: int = 0
    trades_loaded: int = 0
    account_id: str = ""
    start_ts_event_ms: int = 0
    stop_ts_event_ms: int = 0



# 注: 内部 BacktestEngine の venue は instrument_id (例: "1301.TSE") から派生させる。
# 別定数を持たず、nautilus_iid.venue.value を使う設計とした (D5 解釈は docstring 参照)。


class NautilusRunner:
    """nautilus エンジンのライフサイクルを管理するワーカー。

    N0: start_backtest() のみ実装。start_live() は stub。
    N1 以降: server.py のディスパッチャから StartEngine Command で呼ばれる。
    Python 単独モード: CLI から直接呼び出し可能（IPC 経由でなくてもよい）。
    """

    def __init__(self) -> None:
        self._engine: BacktestEngine | None = None
        # H2: BacktestEngine.run() 走行中は dispose() を呼ばない (Cython 内部競合)。
        # _running は engine.run() の前後でのみ True になる。stop() はこのフラグを
        # 見て running 中なら no-op、idle 中ならば dispose() する。
        self._running: bool = False

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

    def start_backtest_replay(
        self,
        *,
        strategy_id: str,
        instrument_id: str,
        start_date: str,
        end_date: str,
        granularity: Literal["Trade", "Minute", "Daily"],
        initial_cash: int,
        currency: str = "JPY",
        base_dir: Path | str | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> ReplayBacktestResult:
        """J-Quants 入力でバックテストを実行する (N1.4)。

        ``on_event`` は呼出側 (server.py の outbox 等) に IPC イベントを渡す
        callback。``None`` の場合は no-op。emit 順:
            1. ``EngineStarted``
            2. ``ReplayDataLoaded`` (件数通知)
            3. ``EngineStopped``

        venue は ``"REPLAY"`` 固定 (D5)。立花 live の ``"TSE"`` とは別空間。

        spec.md §3.2: CacheConfig.database is None を内部で検証する。

        ``granularity`` 別データソース:
            - ``"Trade"``: ``jquants_loader.load_trades(...)``
            - ``"Minute"``: ``jquants_loader.load_minute_bars(...)``
            - ``"Daily"``: ``jquants_loader.load_daily_bars(...)``

        market data 複製 (Rust UI 向け Trades/KlineUpdate) は N1.4 では no-op。
        ReplayDataLoaded で件数だけ通知する。N1.11 streaming で実装する。
        """
        if currency not in _CURRENCY_MAP:
            raise ValueError(
                f"Unsupported currency: {currency!r}. Supported: {list(_CURRENCY_MAP)}"
            )
        cur = _CURRENCY_MAP[currency]
        emit = on_event if on_event is not None else (lambda _evt: None)

        # InstrumentId のフォーマットを起動前に検証 (ValueError は呼出側に伝搬)
        nautilus_iid = InstrumentId.from_str(instrument_id)
        symbol = nautilus_iid.symbol.value
        venue = nautilus_iid.venue.value
        safe_id = strategy_id.replace("-", "").replace("_", "")[:8].upper() or "REPLAY"
        cfg = BacktestEngineConfig(
            trader_id=f"REPLAY-{safe_id}",
            logging=_BYPASS_LOG,
        )
        # persistence 無効化の不変条件 (spec.md §3.2)
        assert cfg.cache.database is None, "nautilus persistence must be disabled"

        engine = BacktestEngine(config=cfg)
        self._engine = engine
        # account_id は engine 構築後に kernel から推定する (trader_id ベース)
        account_id = f"{venue}-{cfg.trader_id}"
        start_ts_ms = int(time.time() * 1000)
        emit({
            "event": "EngineStarted",
            "strategy_id": strategy_id,
            "account_id": account_id,
            "ts_event_ms": start_ts_ms,
        })
        try:
            engine.add_venue(
                venue=Venue(venue),
                oms_type=OmsType.NETTING,
                account_type=AccountType.CASH,
                base_currency=cur,
                starting_balances=[Money(initial_cash, cur)],
            )

            from engine.nautilus.instrument_factory import make_equity_instrument
            instrument = make_equity_instrument(symbol, venue)
            engine.add_instrument(instrument)

            bars_loaded = 0
            trades_loaded = 0
            subscribe_kind = "bar"
            bar_type_str: str | None = None
            if granularity == "Trade":
                subscribe_kind = "trade"
                ticks = list(
                    load_trades(
                        instrument_id,
                        start_date,
                        end_date,
                        base_dir=base_dir if base_dir is not None else Path("S:/j-quants"),
                    )
                )
                trades_loaded = len(ticks)
                if ticks:
                    engine.add_data(ticks)
            elif granularity == "Minute":
                bar_type_str = f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
                bars = list(
                    load_minute_bars(
                        instrument_id,
                        start_date,
                        end_date,
                        base_dir=base_dir if base_dir is not None else Path("S:/j-quants"),
                    )
                )
                bars_loaded = len(bars)
                if bars:
                    engine.add_data(bars)
            elif granularity == "Daily":
                bar_type_str = f"{instrument_id}-1-DAY-LAST-EXTERNAL"
                bars = list(
                    load_daily_bars(
                        instrument_id,
                        start_date,
                        end_date,
                        base_dir=base_dir if base_dir is not None else Path("S:/j-quants"),
                    )
                )
                bars_loaded = len(bars)
                if bars:
                    engine.add_data(bars)
            else:
                raise ValueError(f"unknown granularity: {granularity!r}")

            loaded_ts_ms = int(time.time() * 1000)
            emit({
                "event": "ReplayDataLoaded",
                "strategy_id": strategy_id,
                "bars_loaded": bars_loaded,
                "trades_loaded": trades_loaded,
                "ts_event_ms": loaded_ts_ms,
            })

            strategy_instance = _make_replay_strategy(
                strategy_id, nautilus_iid, subscribe_kind, bar_type_str
            )
            engine.add_strategy(strategy_instance)

            log.info(
                "[NautilusRunner] replay run starting: strategy=%r instrument=%r "
                "trades=%d bars=%d",
                strategy_id, instrument_id, trades_loaded, bars_loaded,
            )
            self._running = True
            try:
                engine.run()
            finally:
                self._running = False
            log.info(
                "[NautilusRunner] replay run completed: strategy=%r", strategy_id
            )

            fill_timestamps, fill_last_prices = _collect_fill_data(engine)

            account = engine.kernel.portfolio.account(Venue(venue))
            if account is None:
                raise RuntimeError(
                    f"[NautilusRunner] portfolio.account returned None for venue={venue!r}"
                )
            balance = account.balance_total(cur)
            final_equity = balance.as_decimal()

            stop_ts_ms = int(time.time() * 1000)
            emit({
                "event": "EngineStopped",
                "strategy_id": strategy_id,
                "final_equity": str(final_equity),
                "ts_event_ms": stop_ts_ms,
            })

            return ReplayBacktestResult(
                strategy_id=strategy_id,
                final_equity=final_equity,
                fill_timestamps=fill_timestamps,
                fill_last_prices=fill_last_prices,
                bars_loaded=bars_loaded,
                trades_loaded=trades_loaded,
                account_id=account_id,
                start_ts_event_ms=start_ts_ms,
                stop_ts_event_ms=stop_ts_ms,
            )
        except Exception:
            log.error(
                "[NautilusRunner] start_backtest_replay failed: strategy=%r instrument=%r",
                strategy_id, instrument_id, exc_info=True,
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
        """エンジンを停止する。

        H2: engine.run() 走行中の dispose() は Cython 内部で再 dispose 防護と
        race を起こすため no-op。run() は同期実行で別 thread から asyncio.to_thread
        経由で走らせている前提（server.py:_handle_start_engine 参照）。
        run() 完了後に finally から engine.dispose() が走るので、
        N1.4 では「running 中の StopEngine は実質ノーオペ + log」で運用する。
        """
        if self._running:
            log.info(
                "stop(): engine.run() is currently running; "
                "dispose deferred to run() finally (no-op here)"
            )
            return
        if self._engine is not None:
            try:
                self._engine.dispose()
            except Exception as exc:
                log.warning("stop(): engine dispose raised: %s", exc)
            self._engine = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_replay_strategy(
    strategy_id: str,
    instrument_id,
    subscribe_kind: str,
    bar_type_str: str | None,
):
    """N1.4 replay 用 Strategy ファクトリ。

    granularity に応じた subscribe_kind / bar_type_str を BuyAndHold に渡す。
    """
    if strategy_id == "buy-and-hold":
        return BuyAndHoldStrategy(
            instrument_id=instrument_id,
            subscribe_kind=subscribe_kind,
            bar_type_str=bar_type_str,
        )
    raise ValueError(
        f"Unknown strategy_id: {strategy_id!r}. N1.4 supports 'buy-and-hold' only."
    )


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
                ts = getattr(order, "ts_last", None)
                lp = getattr(order, "avg_px", None)
                if ts is not None and lp is not None:
                    timestamps.append(ts)
                    last_prices.append(str(lp))
        pairs = sorted(zip(timestamps, last_prices), key=lambda p: p[0])
        if pairs:
            ts_out, px_out = zip(*pairs)
            return list(ts_out), list(px_out)
        return [], []
    except Exception as exc:
        log.warning(
            "[NautilusRunner] _collect_fill_data failed: %s", exc, exc_info=True
        )
        return [], []  # 意図的フォールバック: IPC EngineStopped の送出をブロックしない
