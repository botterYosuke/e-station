"""NautilusRunner: nautilus BacktestEngine / LiveExecutionEngine のライフサイクル管理 (N0.2 / N1.4 / N1.11)

N0: ``start_backtest()`` (Bar 入力) のみ実装。
N1.4: ``start_backtest_replay()`` (J-Quants TradeTick / Bar 入力) を新設。
``on_event`` callback で IPC イベント (``EngineStarted`` / ``ReplayDataLoaded`` /
``EngineStopped``) を呼出側 (server.py) に渡す。
N1.11: ``start_backtest_replay_streaming()`` を新設。
streaming=True 経路で 1 tick/bar ずつ D7 pacing しながら replay する。

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
- market data 複製送出 (Trades/KlineUpdate) は run-once 版 ``start_backtest_replay()``
  では no-op（決定論性テスト経路）。streaming 版で per-tick emit する。
  詳細は ``docs/✅nautilus_trader/replay-market-data-emit.md`` を参照。
- SubmitOrder の replay 経路は N1.4 ではスケルトンのみ (ユーザー Strategy 自身が
  on_trade_tick / on_bar で submit_order するのが本筋)。外部 IPC からの SubmitOrder の
  replay venue 内部 queue 投入は N1.5 で実装。

設計判断 (N1.11):
- 既存 ``start_backtest_replay()`` (run() 一発自走) は温存し、決定論性テスト経路を維持。
- 新規 ``start_backtest_replay_streaming()`` は add_data([item]) → run(streaming=True) →
  clear_data() を 1 件ずつ回し、tick 間に D7 pacing sleep を挟む。
- sleep は time.sleep() / stop_event.wait() を使う (同期関数 + asyncio.to_thread 想定)。
- stop_event は threading.Event を受け取り、set されたらループを break する。
- 営業日跨ぎ時に on_event({"event": "DateChangeMarker", "date": "YYYY-MM-DD"}) を emit。
- 1 tick 処理ごとに ``KlineUpdate`` (Bar) または ``Trades`` (TradeTick) を venue="replay"
  で emit し、Rust UI のチャート / Time&Sales ペインに送る。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.data import Bar, TradeTick
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.objects import Money

from engine.nautilus.data_loader import KlineRow, klines_to_bars
from engine.nautilus.jquants_loader import (
    load_daily_bars,
    load_minute_bars,
    load_trades,
)

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
class FillRecord:
    """PortfolioView.on_fill() に渡すための約定レコード（C-1 fix）。"""

    instrument_id: str
    side: str  # "BUY" or "SELL"
    qty: Decimal
    price: Decimal


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
    portfolio_fills: list["FillRecord"] = field(default_factory=list)



# 注: 内部 BacktestEngine の venue は instrument_id (例: "1301.TSE") から派生させる。
# 別定数を持たず、nautilus_iid.venue.value を使う設計とした (D5 解釈は docstring 参照)。

# ── H-H: BacktestEngine 内部 venue と外向け IPC venue の分離 ─────────────────
#
# BacktestEngine 内では instrument_id (例 "1301.TSE") から派生する venue
# (例 "TSE") を使う必要がある (jquants_loader が TSE タグの TradeTick を emit する)。
# 一方 IPC で送出する EngineStarted.account_id / 後続 N1.5 の SubmitOrder /
# OrderFilled の venue タグは外向け wire 表現として "replay" 固定にする (D5)。
# どちらの空間に属するかを取り違えないよう、外向け venue は必ずこの定数経由で参照する。
_IPC_VENUE_TAG: str = "replay"


def _granularity_to_timeframe(g: str) -> str:
    """granularity 文字列を IPC timeframe 文字列に変換する。"""
    mapping = {"Daily": "1d", "Minute": "1m", "Trade": "tick"}
    if g not in mapping:
        raise ValueError(f"unknown granularity: {g!r}")
    return mapping[g]


def _aggressor_to_side(side) -> str:
    """nautilus AggressorSide を IPC side 文字列 ("BUY" / "SELL") に変換する。

    unknown / NO_AGGRESSOR は "BUY" にフォールバック。
    """
    name = getattr(side, "name", str(side)).upper()
    if "BUY" in name:
        return "BUY"
    if "SELL" in name:
        return "SELL"
    log.debug("[_aggressor_to_side] unrecognized side %r, falling back to BUY", side)
    return "BUY"


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
        strategy_file: str | None = None,
        strategy_init_kwargs: dict | None = None,
    ) -> BacktestResult:
        """バックテストを実行し結果を返す。

        strategy_id: IPC 経由で BacktestResult に返す外部 ID ("buy-and-hold" 等)。
            nautilus 内部の StrategyConfig.strategy_id ("buy-and-hold-001") とは別物。
            N1 の EngineStopped IPC イベントには本パラメータの値を使う。

        strategy_file: ユーザー定義 Strategy ファイルのパス（必須）。

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

            strategy_instance = _make_replay_strategy(
                strategy_file=strategy_file,
                strategy_init_kwargs=strategy_init_kwargs,
            )
            engine.add_strategy(strategy_instance)

            log.info(
                "[NautilusRunner] engine.run() starting: strategy=%r ticker=%r bars=%d",
                strategy_id, ticker, len(bars),
            )
            engine.run()
            log.info("[NautilusRunner] engine.run() completed: strategy=%r", strategy_id)

            # 約定データ収集
            fill_timestamps, fill_last_prices = _collect_fill_data(engine, strategy_id)

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
        instrument_ids: list[str] | None = None,
        start_date: str,
        end_date: str,
        granularity: Literal["Trade", "Minute", "Daily"],
        initial_cash: int,
        currency: str = "JPY",
        base_dir: Path | str | None = None,
        on_event: Callable[[dict], None] | None = None,
        strategy_file: str | None = None,
        strategy_init_kwargs: dict | None = None,
        session_epoch: int | None = None,
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

        本 run-once 版は market data 複製 (Trades/KlineUpdate) を **意図的に行わない**
        (決定論性テスト・gym_env 用に温存)。``ReplayDataLoaded`` で件数のみ通知する。
        Rust UI へのリアルタイム配信は ``start_backtest_replay_streaming()`` を使うこと。
        詳細は ``docs/✅nautilus_trader/replay-market-data-emit.md`` 参照。
        """
        if currency not in _CURRENCY_MAP:
            raise ValueError(
                f"Unsupported currency: {currency!r}. Supported: {list(_CURRENCY_MAP)}"
            )
        cur = _CURRENCY_MAP[currency]
        emit = on_event if on_event is not None else (lambda _evt: None)

        # instrument_ids が None なら instrument_id からシングル銘柄リストを作る
        _iids: list[str] = instrument_ids if instrument_ids is not None else [instrument_id]

        # InstrumentId のフォーマットを起動前に検証 (ValueError は呼出側に伝搬)
        # venue が全銘柄で一致することを検証
        _venues = [InstrumentId.from_str(iid).venue.value for iid in _iids]
        if len(set(_venues)) > 1:
            raise ValueError(
                f"All instruments must share the same venue. Got: {dict(zip(_iids, _venues))}"
            )
        venue = _venues[0]

        safe_id = strategy_id.replace("-", "").replace("_", "")[:8].upper() or "REPLAY"
        cfg = BacktestEngineConfig(
            trader_id=f"REPLAY-{safe_id}",
            logging=_BYPASS_LOG,
        )
        # persistence 無効化の不変条件 (spec.md §3.2)
        assert cfg.cache.database is None, "nautilus persistence must be disabled"

        engine = BacktestEngine(config=cfg)
        self._engine = engine
        # H-H: 外向け IPC では _IPC_VENUE_TAG ("replay") を使う。
        # internal BacktestEngine の venue (TSE 等) とは別空間。
        account_id = f"{_IPC_VENUE_TAG}-{cfg.trader_id}"
        start_ts_ms = int(time.time() * 1000)
        # H-1 (R2 review-fix R2): 二重送出ガード。正常系で EngineStopped を emit したら
        # stop_ts_ms に値を立て、except 側はそれが 0 のときだけ補完 emit する。
        # streaming 版 (start_backtest_replay_streaming) と同じパターンに揃える。
        stop_ts_ms: int = 0
        # H-C: EngineStarted の emit を try 内に移し、emit 自体が raise した場合も
        # except に降りて EngineStopped 補完が走るようにする。
        try:
            emit({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": account_id,
                "ts_event_ms": start_ts_ms,
            })
            engine.add_venue(
                venue=Venue(venue),
                oms_type=OmsType.NETTING,
                account_type=AccountType.CASH,
                base_currency=cur,
                starting_balances=[Money(initial_cash, cur)],
            )

            from engine.nautilus.instrument_factory import make_equity_instrument
            for iid in _iids:
                _sym = InstrumentId.from_str(iid).symbol.value
                _inst = make_equity_instrument(_sym, venue)
                engine.add_instrument(_inst)

            bars_loaded = 0
            trades_loaded = 0
            _base = base_dir if base_dir is not None else Path("S:/j-quants")

            for _iid in _iids:
                if granularity == "Trade":
                    ticks = list(load_trades(_iid, start_date, end_date, base_dir=_base))
                    trades_loaded += len(ticks)
                    if ticks:
                        engine.add_data(ticks)
                elif granularity == "Minute":
                    bars = list(load_minute_bars(_iid, start_date, end_date, base_dir=_base))
                    bars_loaded += len(bars)
                    if bars:
                        engine.add_data(bars)
                elif granularity == "Daily":
                    bars = list(load_daily_bars(_iid, start_date, end_date, base_dir=_base))
                    bars_loaded += len(bars)
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
                # schema 3.12: GUI helper-attach 経路の auto_generate_replay_panes 用。
                "instrument_id": instrument_id,
                "instrument_ids": _iids,
                "granularity": granularity,
                # schema 3.14: server から受領した既存セッション識別子。LoadReplayData
                # 受理時に発番済み（StartEngine 経路では再利用するのみ）。
                "session_epoch": session_epoch,
                "ts_event_ms": loaded_ts_ms,
            })
            log.info(
                "ReplayDataLoaded emitted (start_backtest_replay): session_epoch=%r "
                "strategy=%r instruments=%r",
                session_epoch, strategy_id, _iids,
            )

            strategy_instance = _make_replay_strategy(
                strategy_file=strategy_file,
                strategy_init_kwargs=strategy_init_kwargs,
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

            fill_timestamps, fill_last_prices = _collect_fill_data(engine, strategy_id)
            portfolio_fills = _collect_portfolio_fills(engine, strategy_id)

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

            # H-1: stop_ts_ms はここで初めて非ゼロになる。これ以降の except は
            # 「EngineStopped 既送出」とみなし二重 emit を抑制する。
            return ReplayBacktestResult(
                strategy_id=strategy_id,
                final_equity=final_equity,
                fill_timestamps=fill_timestamps,
                fill_last_prices=fill_last_prices,
                portfolio_fills=portfolio_fills,
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
            # H-C: try 内例外で EngineStopped が抜けるのを補完。
            # final_equity は失敗時 fallback の "0"、ts は now()。
            # H-1 (R2 review-fix R2): stop_ts_ms == 0 のときだけ emit して二重送出を防ぐ。
            # streaming 版と同じガードに揃えた。
            if stop_ts_ms == 0:
                try:
                    emit({
                        "event": "EngineStopped",
                        "strategy_id": strategy_id,
                        "final_equity": "0",
                        "ts_event_ms": int(time.time() * 1000),
                    })
                except Exception:
                    # emit 自体が壊れていても元例外を mask しない
                    log.exception(
                        "[NautilusRunner] EngineStopped fallback emit failed: strategy=%r",
                        strategy_id,
                    )
            raise
        finally:
            engine.dispose()
            self._engine = None

    def start_backtest_replay_streaming(
        self,
        *,
        strategy_id: str,
        instrument_id: str,
        instrument_ids: list[str] | None = None,
        start_date: str,
        end_date: str,
        granularity: Literal["Trade", "Minute", "Daily"],
        initial_cash: int,
        multiplier: int = 1,
        get_multiplier: Callable[[], int] | None = None,
        get_paused: Callable[[], bool] | None = None,
        consume_step_request: Callable[[], int] | None = None,
        push_snapshot: "Callable[..., None] | None" = None,
        restore_strategy_holder: "list | None" = None,
        restore_portfolio_holder: "list | None" = None,
        get_orders_snapshot: "Callable[[], list] | None" = None,
        currency: str = "JPY",
        base_dir: Path | str | None = None,
        on_event: Callable[[dict], None] | None = None,
        strategy_file: str | None = None,
        strategy_init_kwargs: dict | None = None,
        stop_event: threading.Event | None = None,
        session_epoch: int | None = None,
    ) -> ReplayBacktestResult:
        """streaming=True 経路で 1 tick ずつ pacing しながら replay する (N1.11)。

        multiplier: 再生速度倍率。1=等速, 10=10倍速, 100=100倍速。
        各 tick 処理後に D7 pacing 式で sleep を挟む。
        既存 start_backtest_replay() の自走経路（run() 一発）は温存する。

        on_event 送出順:
            EngineStarted
            → ReplayDataLoaded
            → [DateChangeMarker / KlineUpdate / Trades] × N
            → EngineStopped

            KlineUpdate / Trades は per-tick で emit され、venue="replay" タグで
            Rust UI のチャート / Time&Sales ペインに送られる。
            詳細は ``docs/✅nautilus_trader/replay-market-data-emit.md`` 参照。

        get_multiplier: 走行中に再生倍率を読み直すための callback。``None`` のときは
            初期 ``multiplier`` を使い続ける。``SetReplaySpeed`` IPC を per-tick で
            反映するために server.py から ``lambda: self._replay_speed_multiplier``
            を渡す想定。

        stop_event: threading.Event。set されたらループを break して
            EngineStopped を送出する（StopEngine IPC 対応）。
            pacing sleep 中も ``stop_event.wait()`` で受け付ける。
        """
        from engine.nautilus.replay_speed import (
            compute_sleep_sec,
            is_new_trading_day,
        )

        if currency not in _CURRENCY_MAP:
            raise ValueError(
                f"Unsupported currency: {currency!r}. Supported: {list(_CURRENCY_MAP)}"
            )
        cur = _CURRENCY_MAP[currency]
        emit = on_event if on_event is not None else (lambda _evt: None)

        # instrument_ids が None なら instrument_id からシングル銘柄リストを作る
        _iids: list[str] = instrument_ids if instrument_ids is not None else [instrument_id]

        # InstrumentId のフォーマットを起動前に検証 (ValueError は呼出側に伝搬)
        # venue が全銘柄で一致することを検証
        _venues = [InstrumentId.from_str(iid).venue.value for iid in _iids]
        if len(set(_venues)) > 1:
            raise ValueError(
                f"All instruments must share the same venue. Got: {dict(zip(_iids, _venues))}"
            )
        venue = _venues[0]
        # streaming emit 用: 単一銘柄後方互換の ipc_ticker は _iids[0] のシンボルから取得
        symbol = InstrumentId.from_str(_iids[0]).symbol.value

        safe_id = strategy_id.replace("-", "").replace("_", "")[:8].upper() or "REPLAY"
        cfg = BacktestEngineConfig(
            trader_id=f"REPLAY-{safe_id}",
            logging=_BYPASS_LOG,
        )
        # persistence 無効化の不変条件 (spec.md §3.2)
        assert cfg.cache.database is None, "nautilus persistence must be disabled"

        engine = BacktestEngine(config=cfg)
        self._engine = engine
        account_id = f"{_IPC_VENUE_TAG}-{cfg.trader_id}"
        start_ts_ms = int(time.time() * 1000)
        stop_ts_event_ms = 0

        try:
            emit({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": account_id,
                "ts_event_ms": start_ts_ms,
            })

            engine.add_venue(
                venue=Venue(venue),
                oms_type=OmsType.NETTING,
                account_type=AccountType.CASH,
                base_currency=cur,
                starting_balances=[Money(initial_cash, cur)],
            )

            from engine.nautilus.instrument_factory import make_equity_instrument
            for iid in _iids:
                _sym = InstrumentId.from_str(iid).symbol.value
                _inst = make_equity_instrument(_sym, venue)
                engine.add_instrument(_inst)

            # データロード
            bars_loaded = 0
            trades_loaded = 0
            _base = base_dir if base_dir is not None else Path("S:/j-quants")
            items: list = []

            for _iid in _iids:
                if granularity == "Trade":
                    _ticks = list(load_trades(_iid, start_date, end_date, base_dir=_base))
                    trades_loaded += len(_ticks)
                    items.extend(_ticks)
                elif granularity == "Minute":
                    _bars = list(load_minute_bars(_iid, start_date, end_date, base_dir=_base))
                    bars_loaded += len(_bars)
                    items.extend(_bars)
                elif granularity == "Daily":
                    _bars = list(load_daily_bars(_iid, start_date, end_date, base_dir=_base))
                    bars_loaded += len(_bars)
                    items.extend(_bars)
                else:
                    raise ValueError(f"unknown granularity: {granularity!r}")

            # 複数銘柄のデータを時系列順にソート（ts_event で昇順）
            if len(_iids) > 1:
                items.sort(key=lambda x: x.ts_event)

            loaded_ts_ms = int(time.time() * 1000)
            emit({
                "event": "ReplayDataLoaded",
                "strategy_id": strategy_id,
                "bars_loaded": bars_loaded,
                "trades_loaded": trades_loaded,
                # schema 3.12: GUI helper-attach 経路の auto_generate_replay_panes 用。
                "instrument_id": instrument_id,
                "instrument_ids": _iids,
                "granularity": granularity,
                # schema 3.14: server から受領した既存セッション識別子（再利用）。
                "session_epoch": session_epoch,
                "ts_event_ms": loaded_ts_ms,
            })
            log.info(
                "ReplayDataLoaded emitted (start_backtest_replay_streaming): "
                "session_epoch=%r strategy=%r instruments=%r",
                session_epoch, strategy_id, _iids,
            )

            strategy_instance = _make_replay_strategy(
                strategy_file=strategy_file,
                strategy_init_kwargs=strategy_init_kwargs,
            )
            engine.add_strategy(strategy_instance)

            # ── N1.13 Step A: OrderFilled → ExecutionMarker + ReplayBuyingPower ──
            # lazy import: nautilus Cython 型は engine setup 後に安定して参照できるため
            from nautilus_trader.model.events import OrderFilled as _OrderFilled  # noqa: PLC0415
            from engine.nautilus.portfolio_view import PortfolioView  # noqa: PLC0415

            # NOTE: この _portfolio は streaming push-based 専用。
            # server.py._replay_portfolio（非ストリーミング経路・pull-based GetBuyingPower 向け）
            # とは独立したインスタンスのため、streaming 中の GetBuyingPower(pull) は古い状態を返す。
            # push/pull の同期は Step B で対応すること。
            _portfolio = PortfolioView(initial_cash=Decimal(initial_cash))
            _last_prices: dict[str, Decimal] = {}

            def _make_fill_handler(iid_str: str):
                """銘柄ごとに独立した OrderFilled handler closure を生成する。"""
                def _on_order_filled(event: _OrderFilled) -> None:
                    """nautilus OrderFilled を ExecutionMarker + ReplayBuyingPower に変換して emit する。

                    _emit_execution_marker() ヘルパーは dict 入力前提で型変換が冗長になるため、
                    OrderFilled オブジェクトから直接 dict を構築する（意図的な分岐）。
                    """
                    try:
                        side_str = event.order_side.name  # "BUY" or "SELL" (OrderSide enum name)
                        if side_str not in ("BUY", "SELL"):
                            log.warning(
                                "[NautilusRunner] OrderFilled with unexpected side %r, skipping: strategy=%r",
                                side_str,
                                strategy_id,
                            )
                            return
                        instrument_str = str(event.instrument_id)
                        price_str = str(event.last_px)
                        qty_dec = Decimal(str(event.last_qty))
                        ts_ms = event.ts_event // 1_000_000
                        # schema 3.21: commission（nautilus Money）を decimal 文字列で抽出。
                        # 設計方針:
                        #   - schema は Optional → 不明時は marker に key を入れない（live 経路と統一）
                        #   - 抽出失敗は portfolio update とは独立した except でログし、
                        #     commission_str=None として処理続行（ExecutionMarker は drop しない）
                        commission_raw = getattr(event, "commission", None)
                        # 初期値 None で明示初期化。下の str() が想定外に raise しても
                        # commission_str は必ず束縛され、UnboundLocalError による
                        # ExecutionMarker silent drop を防ぐ。
                        commission_str: str | None = None
                        if commission_raw is not None:
                            try:
                                commission_str = str(commission_raw.as_decimal())
                            except AttributeError:
                                commission_str = str(commission_raw)
                            except Exception as exc:  # noqa: BLE001
                                log.warning(
                                    "[NautilusRunner] commission.as_decimal() raised "
                                    "(%s), falling back to str(): commission_raw=%r "
                                    "strategy=%r instrument=%r",
                                    exc, commission_raw, strategy_id, instrument_str,
                                )
                                try:
                                    commission_str = str(commission_raw)
                                except Exception:  # noqa: BLE001
                                    commission_str = None

                        # portfolio 更新を先に行い、失敗した場合は emit しない（状態整合を保つ）
                        _portfolio.on_fill(instrument_str, side_str, qty_dec, Decimal(price_str))
                        _last_prices[instrument_str] = Decimal(price_str)

                    except Exception:
                        log.error(
                            "[NautilusRunner] OrderFilled portfolio update failed: "
                            "strategy=%r instrument=%r px=%r side=%r",
                            strategy_id,
                            getattr(event, "instrument_id", "?"),
                            getattr(event, "last_px", "?"),
                            getattr(event, "order_side", "?"),
                            exc_info=True,
                        )
                        return  # emit せずに終了（ExecutionMarker / ReplayBuyingPower 両方スキップ）

                    # portfolio 更新成功後に IPC emit（両イベントを一括送出）
                    try:
                        # ExecutionMarker: 1 OrderFilled = 1 ExecutionMarker（1:1 契約）
                        marker_dict: dict = {
                            "event": "ExecutionMarker",
                            "strategy_id": strategy_id,
                            "instrument_id": instrument_str,
                            "side": side_str,
                            "price": price_str,
                            "qty": str(qty_dec),
                            "ts_event_ms": ts_ms,
                        }
                        # schema 3.21: commission は Optional。不明時は key を省略
                        # （schemas.py:860 / dto.rs:1294-1295 と整合）
                        if commission_str is not None:
                            marker_dict["commission"] = commission_str
                        emit(marker_dict)

                        # ReplayBuyingPower: fill 後の残高を push emit する
                        bp_dict = _portfolio.to_ipc_dict(strategy_id, _last_prices)
                        bp_dict["ts_event_ms"] = ts_ms  # time.time() を上書きして決定論性を保つ
                        emit(bp_dict)

                    except Exception:
                        log.error(
                            "[NautilusRunner] OrderFilled emit failed (portfolio already updated): "
                            "strategy=%r instrument=%r px=%r side=%r",
                            strategy_id,
                            getattr(event, "instrument_id", "?"),
                            getattr(event, "last_px", "?"),
                            getattr(event, "order_side", "?"),
                            exc_info=True,
                        )
                return _on_order_filled

            # N1.13 Step A: topic は instrument_id string を直接使う（InstrumentId.__str__ 依存を避ける）
            # 各銘柄ごとに handler を登録する
            _fill_handlers: dict[str, object] = {}
            for _iid_str in _iids:
                _fill_topic = f"events.fills.{_iid_str}"
                _handler = _make_fill_handler(_iid_str)
                _fill_handlers[_iid_str] = _handler
                engine.kernel.msgbus.subscribe(
                    topic=_fill_topic,
                    handler=_handler,
                )

            # --- N1.13 Step A: per-bar mark-to-market (Daily / Minute のみ) ------
            # Trade granularity は TradeTick を使うためバーなし → スキップ。
            # _on_bar は position が存在するバーごとに ReplayBuyingPower を push する。
            # これにより保有中に価格が動いても評価額（equity）がリアルタイムに更新される。
            _GRANULARITY_TO_BAR_PERIOD: dict[str, str] = {
                "Daily": "DAY",
                "Minute": "MINUTE",
            }
            _bar_period = _GRANULARITY_TO_BAR_PERIOD.get(granularity)
            # 複数銘柄: topic → handler のマッピング
            _bar_handlers: dict[str, object] = {}

            if _bar_period is not None:
                def _on_bar(bar) -> None:  # noqa: PLC0415
                    instrument_str = str(bar.bar_type.instrument_id)
                    _last_prices[instrument_str] = Decimal(str(bar.close))
                    if not _portfolio.has_open_positions:
                        return  # ポジションなし: equity == cash であり fill event が担う
                    ts_ms = bar.ts_event // 1_000_000
                    try:
                        bp_dict = _portfolio.to_ipc_dict(strategy_id, _last_prices)
                        bp_dict["ts_event_ms"] = ts_ms
                        emit(bp_dict)
                    except Exception:
                        log.error(
                            "[NautilusRunner] bar MTM emit failed: strategy=%r instrument=%r",
                            strategy_id,
                            instrument_str,
                            exc_info=True,
                        )

                for _iid_str in _iids:
                    _bar_topic = f"data.bars.{_iid_str}-1-{_bar_period}-LAST-EXTERNAL"
                    _bar_handlers[_bar_topic] = _on_bar
                    engine.kernel.msgbus.subscribe(topic=_bar_topic, handler=_on_bar)
            # ── N1.13 Step A end ──────────────────────────────────────────────────

            log.info(
                "[NautilusRunner] streaming replay starting: strategy=%r instrument=%r "
                "trades=%d bars=%d multiplier=%d",
                strategy_id, instrument_id, trades_loaded, bars_loaded, multiplier,
            )

            # streaming ループ — IPC emit 用の変数をループ外で事前計算（毎 tick 再計算しない）
            ipc_venue = _IPC_VENUE_TAG          # "replay"
            # 複数銘柄対応: シンボルは item ごとに取得するため事前マップを構築
            # 単一銘柄の場合は symbol（_iids[0] のシンボル）をデフォルトとして使う
            _iid_to_symbol: dict[str, str] = {
                iid: InstrumentId.from_str(iid).symbol.value for iid in _iids
            }
            ipc_market = "stock"                 # equity 固定
            ipc_timeframe = _granularity_to_timeframe(granularity)  # "1d" / "1m" / "tick"

            prev_ts_ns: int | None = None
            _step_counter: int = 0
            self._running = True
            try:
                for item in items:
                    # stop_event による中断チェック
                    if stop_event is not None and stop_event.is_set():
                        log.info(
                            "[NautilusRunner] streaming replay stopped by stop_event: "
                            "strategy=%r",
                            strategy_id,
                        )
                        break

                    # Pause / step check.
                    # When paused and no step is requested the loop busy-waits
                    # until ResumeReplay, StepReplay, or StopReplay.
                    while True:
                        if stop_event is not None and stop_event.is_set():
                            break
                        _is_paused = get_paused() if get_paused is not None else False
                        if not _is_paused:
                            break
                        _pending = (
                            consume_step_request() if consume_step_request is not None else 0
                        )
                        if _pending > 0:
                            break
                        time.sleep(0.01)
                    if stop_event is not None and stop_event.is_set():
                        break

                    # HIGH-1: apply pending strategy_state restore before processing this step.
                    # Update attributes in-place so the Nautilus engine's registered reference
                    # also reflects the restored state (replacing the local var alone is not enough).
                    if restore_strategy_holder is not None and restore_strategy_holder[0] is not None:
                        _restored_strategy = restore_strategy_holder[0]
                        restore_strategy_holder[0] = None
                        try:
                            # Only copy non-underscore attrs; framework attrs (_cache, _msgbus …)
                            # must not be overwritten because they reference live engine objects.
                            _public = {k: v for k, v in _restored_strategy.__dict__.items()
                                       if not k.startswith("_")}
                            strategy_instance.__dict__.update(_public)
                        except Exception as _e:
                            log.warning("[NautilusRunner] strategy restore in-place failed: %s", _e)
                        log.debug(
                            "[NautilusRunner] strategy_state restored in-place at step %d",
                            _step_counter,
                        )

                    # HIGH-2: apply pending portfolio_state restore before processing this step.
                    if restore_portfolio_holder is not None and restore_portfolio_holder[0] is not None:
                        _pstate = restore_portfolio_holder[0]
                        restore_portfolio_holder[0] = None
                        try:
                            from decimal import Decimal as _D
                            _portfolio._restore_from_dict(_pstate)
                            _last_prices.clear()
                            _last_prices.update(
                                {k: _D(str(v)) for k, v in _pstate.get("last_prices", {}).items()}
                            )
                        except Exception as _e:
                            log.warning("[NautilusRunner] portfolio restore failed: %s", _e)
                        log.debug(
                            "[NautilusRunner] portfolio restored at step %d", _step_counter
                        )

                    curr_ts_ns: int = item.ts_event

                    # 営業日跨ぎ判定 → DateChangeMarker emit
                    if is_new_trading_day(prev_ts_ns, curr_ts_ns):
                        from datetime import datetime, timezone, timedelta
                        _JST = timezone(timedelta(hours=9))
                        curr_date_str = datetime.fromtimestamp(
                            curr_ts_ns / 1_000_000_000, tz=_JST
                        ).strftime("%Y-%m-%d")
                        emit({
                            "event": "DateChangeMarker",
                            "date": curr_date_str,
                        })

                    # D7 pacing sleep 計算（multiplier=0 は「即時」扱い）
                    _mult = get_multiplier() if get_multiplier is not None else multiplier
                    if _mult <= 0 or prev_ts_ns is None:
                        sleep_sec = 0.0
                    else:
                        dt_event_sec = (curr_ts_ns - prev_ts_ns) / 1_000_000_000
                        sleep_sec = compute_sleep_sec(
                            dt_event_sec=dt_event_sec,
                            multiplier=_mult,
                            ts_event_ns=curr_ts_ns,
                        )

                    # 1 tick 処理
                    engine.add_data([item])
                    engine.run(streaming=True)
                    engine.clear_data()

                    # per-tick emit: engine.run() 完了後・pacing sleep 前に emit する
                    try:
                        if isinstance(item, Bar):
                            _item_iid_str = str(item.bar_type.instrument_id)
                            _item_ticker = _iid_to_symbol.get(_item_iid_str, symbol)
                            emit({
                                "event": "KlineUpdate",
                                "venue": ipc_venue,
                                "ticker": _item_ticker,
                                "market": ipc_market,
                                "timeframe": ipc_timeframe,
                                "kline": {
                                    "open_time_ms": item.ts_event // 1_000_000,
                                    "open": str(item.open),
                                    "high": str(item.high),
                                    "low": str(item.low),
                                    "close": str(item.close),
                                    "volume": str(item.volume),
                                    "is_closed": True,
                                },
                            })
                        elif isinstance(item, TradeTick):
                            _item_iid_str = str(item.instrument_id)
                            _item_ticker = _iid_to_symbol.get(_item_iid_str, symbol)
                            emit({
                                "event": "Trades",
                                "venue": ipc_venue,
                                "ticker": _item_ticker,
                                "market": ipc_market,
                                "stream_session_id": account_id,
                                "trades": [{
                                    "price": str(item.price),
                                    "qty": str(item.size),
                                    "side": _aggressor_to_side(item.aggressor_side),
                                    "ts_ms": item.ts_event // 1_000_000,
                                    "is_liquidation": False,
                                }],
                            })
                    except Exception:
                        log.error(
                            "[NautilusRunner] per-tick emit failed: strategy=%r",
                            strategy_id,
                            exc_info=True,
                        )
                        break

                    prev_ts_ns = curr_ts_ns

                    # schema 3.16: push_snapshot at each granularity boundary.
                    # Every Bar (Daily/Minute) and every TradeTick (Trade) is one
                    # granularity unit, so each processed item is a boundary.
                    if push_snapshot is not None:
                        try:
                            _snap_portfolio = _portfolio.to_ipc_dict(strategy_id, _last_prices)
                            _snap_portfolio_state = _portfolio.to_snapshot_dict(_last_prices)
                            # schema 3.16: server-side fills を snapshot 時点でコピーして含める。
                            # server.py の _replay_streaming_fills は ExecutionMarker emit 経由で
                            # 蓄積されており、StepBackward 後の OrderList rollback に必要。
                            _snap_orders: list = (
                                list(get_orders_snapshot()) if get_orders_snapshot else []
                            )
                            # strategy_instance may not support deepcopy; pass None if so.
                            # server.push_snapshot handles None strategy_state gracefully.
                            push_snapshot(
                                _step_counter,
                                _snap_portfolio,
                                _snap_orders,
                                strategy_instance,
                                [],  # ui_events collected per-step not yet wired; pass empty
                                _snap_portfolio_state,
                            )
                        except Exception:
                            log.warning(
                                "[NautilusRunner] push_snapshot failed at step %d: "
                                "strategy=%r",
                                _step_counter,
                                strategy_id,
                                exc_info=True,
                            )

                    _step_counter += 1

                    # pacing sleep: stop_event.wait で sleep しつつ中断要求を受け付ける
                    if sleep_sec > 0.0:
                        if stop_event is not None:
                            if stop_event.wait(timeout=sleep_sec):
                                break
                        else:
                            time.sleep(sleep_sec)

            finally:
                self._running = False
                # N1.13: engine.dispose() の前に購読解除（dispose 後に handler closure が残らないよう）
                # fill handlers: 銘柄ごとに登録したものをすべて解除
                for _iid_str, _handler in _fill_handlers.items():
                    try:
                        engine.kernel.msgbus.unsubscribe(
                            topic=f"events.fills.{_iid_str}",
                            handler=_handler,
                        )
                    except Exception:
                        log.warning(
                            "[NautilusRunner] msgbus.unsubscribe fill failed "
                            "(handler may not have been registered due to early failure): "
                            "strategy=%r instrument=%r",
                            strategy_id,
                            _iid_str,
                        )
                # bar handlers: 銘柄ごとに登録したものをすべて解除
                for _btopic, _bhandler in _bar_handlers.items():
                    try:
                        engine.kernel.msgbus.unsubscribe(
                            topic=_btopic,
                            handler=_bhandler,
                        )
                    except Exception:
                        log.warning(
                            "[NautilusRunner] msgbus.unsubscribe bar failed: strategy=%r topic=%r",
                            strategy_id,
                            _btopic,
                        )

            log.info(
                "[NautilusRunner] streaming replay completed: strategy=%r", strategy_id
            )

            fill_timestamps, fill_last_prices = _collect_fill_data(engine, strategy_id)

            # portfolio.account は run() を一度も呼んでいない場合（stop_event 即中断など）
            # に None を返すことがある。その場合は initial_cash をそのまま返す。
            account = engine.kernel.portfolio.account(Venue(venue))
            if account is None:
                log.info(
                    "[NautilusRunner] portfolio.account is None (no ticks processed): "
                    "strategy=%r venue=%r, returning initial_cash as final_equity",
                    strategy_id, venue,
                )
                final_equity = Decimal(initial_cash)
            else:
                balance = account.balance_total(cur)
                final_equity = balance.as_decimal()

            stop_ts_event_ms = int(time.time() * 1000)
            emit({
                "event": "EngineStopped",
                "strategy_id": strategy_id,
                "final_equity": str(final_equity),
                "ts_event_ms": stop_ts_event_ms,
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
                stop_ts_event_ms=stop_ts_event_ms,
            )
        except Exception:
            log.error(
                "[NautilusRunner] start_backtest_replay_streaming failed: "
                "strategy=%r instrument=%r",
                strategy_id, instrument_id, exc_info=True,
            )
            # H-C: 例外時の EngineStopped 補完（二重送出防止）
            if stop_ts_event_ms == 0:
                try:
                    emit({
                        "event": "EngineStopped",
                        "strategy_id": strategy_id,
                        "final_equity": "0",
                        "ts_event_ms": int(time.time() * 1000),
                    })
                except Exception:
                    log.exception(
                        "[NautilusRunner] EngineStopped fallback emit failed: strategy=%r",
                        strategy_id,
                    )
            raise
        finally:
            engine.dispose()
            self._engine = None

    def start_live(
        self,
        *,
        instrument_id: str,
        strategy_file: "str | None",
        strategy_init_kwargs: "dict | None",
        max_qty: int,
        max_notional_jpy: int,
        second_password: str,
        session: "Any",
        fd_queue: "queue.Queue",
        ec_queue: "queue.Queue",
        on_event: "Callable[[dict], None]",
        stop_event: "threading.Event",
        strategy_id: str,
    ) -> None:
        """N3: TradingNode を起動して live strategy を実行する。

        この関数は同期関数であり、server.py の asyncio.to_thread(_run) 内か、
        LiveSession.run() から直接 blocking 呼び出しされる。
        内部で asyncio.run() を使って TradingNode の非同期処理を実行する。

        Args:
            instrument_id: 取引対象銘柄 ID（例: ``"8306.T"``）。
            strategy_file: strategy .py ファイルパス。None の場合は ValueError。
            strategy_init_kwargs: strategy の `__init__` に渡す追加引数。
            max_qty: 1 注文あたりの最大株数。
            max_notional_jpy: 1 注文あたりの最大金額（円）。
            second_password: 第二暗証番号（env 変数不使用。引数で受け取る）。
            session: Tachibana セッションオブジェクト。
            fd_queue: FD frame を受け取るキュー。
            ec_queue: EC frame を受け取るキュー。
            on_event: IPC イベントを受け取るコールバック。
            stop_event: 停止シグナル。set されたら TradingNode を停止する。
            strategy_id: strategy 識別子。
        """
        import asyncio as _asyncio
        import threading as _threading
        import time as _time

        from nautilus_trader.config import CacheConfig, TradingNodeConfig
        from nautilus_trader.live.node import TradingNode

        from engine.nautilus.instrument_factory import make_equity_instrument
        from engine.nautilus.clients.tachibana import TachibanaLiveExecutionClient
        from engine.nautilus.clients.tachibana_data import TachibanaLiveDataClient
        from engine.nautilus.clients.tachibana_event_bridge import (
            OrderIdMap,
            TachibanaEventBridge,
        )
        from engine.exchanges.tachibana_helpers import PNoCounter

        # instrument_id は "8306.T" 形式 → symbol="8306", venue="T"
        # make_equity_instrument は (symbol, venue) を受け取る
        if "." in instrument_id:
            _sym, _venue = instrument_id.rsplit(".", 1)
        else:
            _sym = instrument_id
            _venue = "TSE"

        async def _run_node() -> None:
            safe_id = strategy_id.replace("-", "_").replace(".", "_")
            config = TradingNodeConfig(
                trader_id=f"TACHIBANA-{safe_id}",
                cache=CacheConfig(database=None),
            )
            node = TradingNode(config=config)

            # クライアント生成
            p_no_counter = PNoCounter()
            exec_client = TachibanaLiveExecutionClient(
                session=session,
                second_password=second_password,
                max_qty=max_qty,
                max_notional_jpy=max_notional_jpy,
                p_no_counter=p_no_counter,
                strategy_id=strategy_id,
            )
            data_client = TachibanaLiveDataClient()
            order_id_map = OrderIdMap()
            event_bridge = TachibanaEventBridge(
                client=exec_client,
                order_id_map=order_id_map,
            )

            node._data_engine.register_client(data_client)
            node._exec_engine.register_client(exec_client)

            # Instrument 登録
            instrument = make_equity_instrument(_sym, _venue)
            node.add_data(instrument)

            # Strategy ロード
            if strategy_file:
                strategy = _load_user_strategy(strategy_file, strategy_init_kwargs)
                node.add_strategies([strategy])

            # warm_up: CLMOrderList から未決注文復元
            try:
                if hasattr(exec_client, "warm_up"):
                    await exec_client.warm_up()
            except Exception as exc:
                on_event({
                    "event": "Error",
                    "code": "warm_up_failed",
                    "message": str(exc),
                })
                return

            # EngineStarted emit
            on_event({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": "tachibana",
                "ts_event_ms": int(_time.time() * 1000),
            })

            # live_bridges モジュール存在確認のみ（thread 起動は node.build() 後）
            try:
                from engine.nautilus.live_bridges import LiveDataBridge, LiveEcBridge
                _have_bridges = True
            except ImportError:
                _have_bridges = False

            _bridge_threads: list[_threading.Thread] = []

            try:
                # TradingNode ビルドと実行（_connect() 内で data_client._loop が確定する）
                node.build()

                # node.build() 完了後にブリッジを起動する。
                # build() 内の _connect() で data_client._loop が asyncio.get_running_loop()
                # に確定するため、ここまで待つ必要がある。
                # event_bridge._loop は自動設定されないので明示的に注入する。
                if _have_bridges:
                    event_bridge._loop = _asyncio.get_running_loop()
                    data_bridge = LiveDataBridge(data_client, fd_queue, instrument_id, stop_event)
                    ec_bridge = LiveEcBridge(event_bridge, ec_queue, stop_event)
                    t_data = _threading.Thread(target=data_bridge.run, daemon=True)
                    t_ec = _threading.Thread(target=ec_bridge.run, daemon=True)
                    t_data.start()
                    t_ec.start()
                    _bridge_threads = [t_data, t_ec]
                # stop_event を監視しながらグレースフルシャットダウン
                loop = _asyncio.get_event_loop()
                node_task: _asyncio.Task | None = None
                if hasattr(node, "run_async"):
                    node_task = loop.create_task(node.run_async())
                else:
                    # run() が同期 blocking の場合: executor で実行
                    node_task = loop.create_task(
                        loop.run_in_executor(None, node.run)
                    )
                stop_waiter = loop.create_task(
                    loop.run_in_executor(None, stop_event.wait)
                )
                done, pending = await _asyncio.wait(
                    {node_task, stop_waiter},
                    return_when=_asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except _asyncio.CancelledError:
                        pass

                # グレースフルシャットダウン
                if hasattr(node, "stop_async"):
                    try:
                        await node.stop_async()
                    except Exception as exc:
                        log.warning("start_live: node.stop_async() raised: %s", exc)
                elif hasattr(node, "stop"):
                    try:
                        node.stop()
                    except Exception as exc:
                        log.warning("start_live: node.stop() raised: %s", exc)
            finally:
                stop_event.set()
                for t in _bridge_threads:
                    t.join(timeout=5.0)
                on_event({
                    "event": "EngineStopped",
                    "strategy_id": strategy_id,
                    "final_equity": "0",
                    "ts_event_ms": int(_time.time() * 1000),
                })

        _asyncio.run(_run_node())

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

def _load_user_strategy(
    strategy_file: str,
    strategy_init_kwargs: dict | None,
):
    """strategy_file パスからユーザー定義 Strategy をロードして返す。

    StrategyLoadError は呼び出し側で EngineError に変換すること。
    """
    from engine.nautilus.strategy_loader import load_strategy_from_file

    return load_strategy_from_file(Path(strategy_file).resolve(), strategy_init_kwargs or {})


def _make_replay_strategy(
    strategy_file: str | None = None,
    strategy_init_kwargs: dict | None = None,
):
    """N1.4 replay 用 Strategy ファクトリ。

    strategy_file は必須。None または空文字の場合は ValueError を raise する。
    ユーザーは POST /api/replay/start で strategy_file を明示指定すること。
    """
    if not strategy_file:
        raise ValueError(
            "strategy_file is required. "
            "Specify a .py file path via POST /api/replay/start."
        )
    return _load_user_strategy(strategy_file, strategy_init_kwargs)




def _collect_portfolio_fills(
    engine: BacktestEngine, strategy_id: str = ""
) -> "list[FillRecord]":
    """PortfolioView.on_fill() 用に約定レコードを収集する（C-1 fix）。

    order.side / filled_qty / avg_px / instrument_id が欠落している場合は
    そのオーダーをスキップする。
    """
    try:
        orders = engine.kernel.cache.orders()
        fills: list[FillRecord] = []
        for order in orders:
            if not getattr(order, "is_closed", False):
                continue
            inst = getattr(order, "instrument_id", None)
            side_raw = getattr(order, "side", None)
            qty_raw = getattr(order, "filled_qty", None)
            px_raw = getattr(order, "avg_px", None)
            if inst is None or side_raw is None or qty_raw is None or px_raw is None:
                continue
            try:
                side_str = side_raw.name  # OrderSide.BUY → "BUY"
            except AttributeError:
                side_str = str(side_raw)
            try:
                qty = Decimal(str(qty_raw))
                price = Decimal(str(px_raw))
            except Exception:
                continue
            if qty <= 0 or price <= 0:
                continue
            fills.append(FillRecord(
                instrument_id=str(inst),
                side=side_str,
                qty=qty,
                price=price,
            ))
        return fills
    except (AttributeError, KeyError, TypeError):
        log.exception(
            "[NautilusRunner] _collect_portfolio_fills failed: strategy=%r", strategy_id
        )
        return []


def _collect_fill_data(
    engine: BacktestEngine, strategy_id: str = ""
) -> tuple[list[int], list[str]]:
    """約定タイムスタンプと約定価格を収集して返す（決定論性テスト用）。

    戻り値: (sorted_timestamps, sorted_last_prices)

    H-I: 決定論性のため (ts, price) の lex sort で同一 ts 内も安定化する。
    例外は ``AttributeError`` / ``KeyError`` / ``TypeError`` のみ握り、それ以外は
    呼出側に伝搬する (例外マスクで本物の不具合を見逃さないため)。
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
        # H-I: (ts, price) 両方をキーにして同一 ts 内も lex sort
        pairs = sorted(zip(timestamps, last_prices), key=lambda p: (p[0], p[1]))
        if pairs:
            ts_out, px_out = zip(*pairs)
            return list(ts_out), list(px_out)
        return [], []
    except (AttributeError, KeyError, TypeError):
        # H-I: 想定可能な属性欠落のみ握って fallback。Exception 全捕捉は
        # 本物の不具合を見逃すので避ける。
        log.exception(
            "[NautilusRunner] _collect_fill_data failed: strategy=%r", strategy_id,
        )
        return [], []  # 意図的フォールバック: IPC EngineStopped の送出をブロックしない
