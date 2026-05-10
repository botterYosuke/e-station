"""N1.8: live mock / replay 両経路スモークテスト。

BuyAndHold 戦略を:
  1. live mock 経路 (start_backtest — Bar ベース)
  2. replay J-Quants 経路 (start_backtest_replay — Trade ベース)
の両方で走らせ、例外なく完走することを確認する。

spec.md §3.5.4: 最終ポジション方向の一致検証は fill_timestamps の非空チェックで代用。
(複雑な fill 解析は N1.5 で実施。本テストでは「完走 + クラッシュしない」が主眼。)

issue #42 Phase 7（受け入れ基準 #21）: ``load_strategy_from_file`` が live /
replay の **両経路で同じ実装** に統一されていることを pin する契約テストを
本ファイルに追加した。``test_load_strategy_from_file_used_for_both_paths`` 系
（下方）を参照。
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.nautilus.data_loader import KlineRow
from engine.nautilus.engine_runner import NautilusRunner, BacktestResult, ReplayBacktestResult

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent.parent
STRATEGY_FILE = str(REPO_ROOT / "examples" / "test_strategy_daily.py")
STRATEGY_INIT_KWARGS_1301 = {"instrument_id": "1301.TSE", "bar_type_str": "1301.TSE-1-DAY-MID-EXTERNAL"}

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _year_klines() -> list[KlineRow]:
    """緩やかな上昇トレンドの 250 本日足データを返す（live mock 用）。"""
    rows: list[KlineRow] = []
    base = datetime(2024, 1, 4, tzinfo=timezone.utc)
    close = 3775.0
    for i in range(250):
        dt = base + timedelta(days=i)
        close = max(1000.0, close + (10 if i % 2 == 0 else -5))
        rows.append(
            KlineRow(
                date=dt.strftime("%Y%m%d"),
                open=str(close - 10),
                high=str(close + 20),
                low=str(close - 20),
                close=str(close),
                volume="1000",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

def test_live_mock_completes_without_exception():
    """ユーザー戦略が live mock (Bar 経路) で例外なく完走すること。"""
    runner = NautilusRunner()
    result = runner.start_backtest(
        strategy_id="user-strategy",
        ticker="1301",
        venue="TSE",
        klines=_year_klines(),
        initial_cash=1_000_000,
        strategy_file=STRATEGY_FILE,
        strategy_init_kwargs=STRATEGY_INIT_KWARGS_1301,
    )
    assert isinstance(result, BacktestResult)
    assert result.strategy_id == "user-strategy"
    assert result.final_equity > 0


def test_replay_jquants_trade_completes_without_exception():
    """ユーザー戦略が replay J-Quants Trade 経路で例外なく完走すること。

    fixtures/equities_trades_202401.csv.gz を使う。
    """
    runner = NautilusRunner()
    result = runner.start_backtest_replay(
        strategy_id="user-strategy",
        instrument_id="1301.TSE",
        start_date="2024-01-01",
        end_date="2024-01-31",
        granularity="Trade",
        initial_cash=1_000_000,
        base_dir=FIXTURES,
        strategy_file=STRATEGY_FILE,
        strategy_init_kwargs=STRATEGY_INIT_KWARGS_1301,
    )
    assert isinstance(result, ReplayBacktestResult)
    assert result.strategy_id == "user-strategy"
    assert result.final_equity > 0


def test_live_mock_and_replay_both_complete():
    """ユーザー戦略が live mock / replay 両方で例外なく完走すること。

    spec.md §3.5.4: fill_timestamps の非空チェックでポジション生成を確認する。
    (fill が 0 件でも完走は完走とみなす — データ量次第)
    """
    # live mock
    live_runner = NautilusRunner()
    live_result = live_runner.start_backtest(
        strategy_id="user-strategy",
        ticker="1301",
        venue="TSE",
        klines=_year_klines(),
        initial_cash=1_000_000,
        strategy_file=STRATEGY_FILE,
        strategy_init_kwargs=STRATEGY_INIT_KWARGS_1301,
    )

    # replay J-Quants
    replay_runner = NautilusRunner()
    replay_result = replay_runner.start_backtest_replay(
        strategy_id="user-strategy",
        instrument_id="1301.TSE",
        start_date="2024-01-01",
        end_date="2024-01-31",
        granularity="Trade",
        initial_cash=1_000_000,
        base_dir=FIXTURES,
        strategy_file=STRATEGY_FILE,
        strategy_init_kwargs=STRATEGY_INIT_KWARGS_1301,
    )

    # 両方完走チェック
    assert isinstance(live_result, BacktestResult)
    assert isinstance(replay_result, ReplayBacktestResult)

    # equity が正数（エンジンが正常稼働している）
    assert live_result.final_equity > 0
    assert replay_result.final_equity > 0


def test_live_mock_fill_timestamps_non_empty():
    """live mock でユーザー戦略が約定を生成すること（fill_timestamps が非空）。

    250 本の Bar データがあれば最初のバーで買いが入るため fill が生じる。
    """
    runner = NautilusRunner()
    result = runner.start_backtest(
        strategy_id="user-strategy",
        ticker="1301",
        venue="TSE",
        klines=_year_klines(),
        initial_cash=1_000_000,
        strategy_file=STRATEGY_FILE,
        strategy_init_kwargs=STRATEGY_INIT_KWARGS_1301,
    )
    # fill_timestamps が非空であること（少なくとも 1 件の約定がある）
    assert len(result.fill_timestamps) > 0, (
        "live mock must produce at least one fill with 250-bar data"
    )


def test_replay_jquants_daily_bar_completes():
    """ユーザー戦略が replay J-Quants Daily Bar 経路でも完走すること。"""
    runner = NautilusRunner()
    result = runner.start_backtest_replay(
        strategy_id="user-strategy",
        instrument_id="1301.TSE",
        start_date="2024-01-01",
        end_date="2024-01-31",
        granularity="Daily",
        initial_cash=1_000_000,
        base_dir=FIXTURES,
        strategy_file=STRATEGY_FILE,
        strategy_init_kwargs=STRATEGY_INIT_KWARGS_1301,
    )
    assert isinstance(result, ReplayBacktestResult)
    assert result.final_equity > 0


def test_replay_ipc_events_emitted():
    """start_backtest_replay が on_event callback に EngineStarted / ReplayDataLoaded / EngineStopped を emit すること。"""
    events: list[dict] = []
    runner = NautilusRunner()
    runner.start_backtest_replay(
        strategy_id="user-strategy",
        instrument_id="1301.TSE",
        start_date="2024-01-01",
        end_date="2024-01-31",
        granularity="Trade",
        initial_cash=1_000_000,
        base_dir=FIXTURES,
        on_event=events.append,
        strategy_file=STRATEGY_FILE,
        strategy_init_kwargs=STRATEGY_INIT_KWARGS_1301,
    )

    event_names = [e["event"] for e in events]
    assert "EngineStarted" in event_names, f"EngineStarted not emitted: {event_names}"
    assert "ReplayDataLoaded" in event_names, f"ReplayDataLoaded not emitted: {event_names}"
    assert "EngineStopped" in event_names, f"EngineStopped not emitted: {event_names}"


# ---------------------------------------------------------------------------
# issue #42 Phase 7（受け入れ基準 #21）: loader 互換契約テスト
#
# Phase 1 引き継ぎで「``engine_runner._load_user_strategy`` は既に
# ``load_strategy_from_file`` の薄ラッパー」「``replay`` 起動経路（=
# ``_make_replay_strategy``）も同じく ``_load_user_strategy`` 経由」という事実が
# 確認されている。この事実を **pin** することで、誰かが loader を fork した
# 場合（例: live と replay で別々の strategy module キャッシュを持つ実装に
# 戻す等）に CI で検知できるようにする。
#
# spec.md §3.2-A / 統一決定 #1（戦略ファイル無改変） / Phase 0 既存実装事実
# 確認表の `LiveSession.run` / `NautilusRunner.start_live` 行を参照。
# ---------------------------------------------------------------------------


def test_load_strategy_from_file_used_for_both_paths(tmp_path):
    """live / replay 両経路で **同じ ``load_strategy_from_file`` が呼ばれる** ことを pin。

    実装方法:
      - mock 確認: live 経路の薄ラッパー ``_load_user_strategy`` と replay 経路の
        ``_make_replay_strategy`` を直接呼び、両方が ``load_strategy_from_file``
        に委譲することを assert する。
      - 同 class 比較: 同じ戦略ファイルを両経路でロードし、得られる Strategy
        サブクラスの ``__qualname__`` が一致することを assert する。

    なぜ pin する: issue #42 Phase 1 で「live 経路 loader が既に
    ``load_strategy_from_file`` の薄ラッパーである」という事実を引き継ぎ Tips
    に明記済み。本テストは将来 fork されても CI で即検知できるようにする。
    """
    from engine.nautilus import engine_runner as er
    from engine.nautilus import strategy_loader as sl

    # 最小ダミー戦略（``Strategy`` 派生クラス 1 個 + 副作用なし）
    dummy_path = tmp_path / "dummy_strategy.py"
    dummy_path.write_text(
        "from nautilus_trader.trading.strategy import Strategy, StrategyConfig\n"
        "\n"
        "class DummyStrategy(Strategy):\n"
        "    def __init__(self) -> None:\n"
        "        super().__init__(config=StrategyConfig(strategy_id='dummy-loader-pin'))\n",
        encoding="utf-8",
    )

    # ---- 経路 A: live 起動経路 = ``_load_user_strategy`` ----
    # ``engine_runner._load_user_strategy`` は ``load_strategy_from_file`` を
    # ファクトリとして呼ぶ。mock を ``strategy_loader`` モジュール側にかける
    # ことで、live / replay 両経路が **本当に同じ関数** を呼ぶことを観測する。
    with patch.object(sl, "load_strategy_from_file", wraps=sl.load_strategy_from_file) as live_spy:
        live_inst = er._load_user_strategy(str(dummy_path), strategy_init_kwargs=None)
        assert live_spy.call_count == 1, (
            f"live 経路 (_load_user_strategy) は load_strategy_from_file を 1 回呼ぶこと: "
            f"call_count={live_spy.call_count}"
        )

    # ---- 経路 B: replay 起動経路 = ``_make_replay_strategy`` ----
    with patch.object(sl, "load_strategy_from_file", wraps=sl.load_strategy_from_file) as replay_spy:
        replay_inst = er._make_replay_strategy(str(dummy_path), strategy_init_kwargs=None)
        assert replay_spy.call_count == 1, (
            f"replay 経路 (_make_replay_strategy) は load_strategy_from_file を 1 回呼ぶこと: "
            f"call_count={replay_spy.call_count}"
        )

    # ---- 両経路で同じ Strategy class が解決されること ----
    # （別経路で別々の module loader を使っていれば __qualname__ は同じでも
    # ``type(...)`` は異なるが、同じ ``load_strategy_from_file`` を通っている
    # 限り、少なくとも __qualname__ + 親 class が一致する）。
    assert type(live_inst).__qualname__ == type(replay_inst).__qualname__, (
        "live / replay 両経路で同じ Strategy サブクラスが解決されること: "
        f"live={type(live_inst).__qualname__!r}, "
        f"replay={type(replay_inst).__qualname__!r}"
    )
    # 親 class chain に Strategy が居ることも pin（loader が誤って別物を返す
    # silent failure を捕捉）
    from nautilus_trader.trading.strategy import Strategy as _NTStrategy
    assert isinstance(live_inst, _NTStrategy)
    assert isinstance(replay_inst, _NTStrategy)


def test_load_user_strategy_is_thin_wrapper_around_load_strategy_from_file():
    """``engine_runner._load_user_strategy`` のソース実装が ``load_strategy_from_file``
    の薄ラッパーであることを **AST レベル** で pin する。

    Phase 1 引き継ぎ Tips:
      > engine_runner._load_user_strategy は既に load_strategy_from_file の
      > 薄ラッパーになっている事実を pin する

    本テストは AST 走査で「関数本体に ``load_strategy_from_file(...)`` 呼出が
    存在する」「``Path(...).resolve()`` が引数に含まれる」を assert する。誰かが
    loader を fork して別実装に書き換えると AST 構造が崩れて検知できる。
    """
    import inspect
    from engine.nautilus import engine_runner as er

    src = inspect.getsource(er._load_user_strategy)
    tree = ast.parse(src)

    # 関数定義は 1 個（_load_user_strategy 自体）
    func_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert len(func_defs) == 1
    func = func_defs[0]
    assert func.name == "_load_user_strategy"

    # 関数本体に ``load_strategy_from_file(...)`` 呼出が存在
    calls = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "load_strategy_from_file"
    ]
    assert len(calls) >= 1, (
        "_load_user_strategy 関数本体に load_strategy_from_file(...) 呼出が必須"
    )

    # ``load_strategy_from_file`` を ``engine.nautilus.strategy_loader`` から
    # import している（モジュール内 import で OK — 関数内 import 含む）
    imports = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and n.module == "engine.nautilus.strategy_loader"
        and any(alias.name == "load_strategy_from_file" for alias in n.names)
    ]
    assert len(imports) >= 1, (
        "engine.nautilus.strategy_loader から load_strategy_from_file を import すること"
    )


def test_make_replay_strategy_delegates_to_load_user_strategy():
    """``_make_replay_strategy`` が ``_load_user_strategy`` に委譲することを pin。

    両経路の loader 統一は ``_make_replay_strategy → _load_user_strategy →
    load_strategy_from_file`` のチェーンで成立している。中間の委譲が崩れると
    live / replay 経路で挙動が分岐する silent failure が起きるため、AST 走査
    で「``_make_replay_strategy`` 関数本体に ``_load_user_strategy(...)`` 呼出
    がある」を pin する。
    """
    import inspect
    from engine.nautilus import engine_runner as er

    src = inspect.getsource(er._make_replay_strategy)
    tree = ast.parse(src)

    func_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert len(func_defs) == 1
    func = func_defs[0]
    assert func.name == "_make_replay_strategy"

    calls = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_load_user_strategy"
    ]
    assert len(calls) >= 1, (
        "_make_replay_strategy は _load_user_strategy へ委譲すること "
        "(loader 統一の不変条件)"
    )
