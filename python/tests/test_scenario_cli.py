"""F6b: replay_session._resolve_cli_params の CLI SCENARIO フォールバックテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.replay_session import _resolve_cli_params

VALID_SCENARIO_PY = """\
SCENARIO = {
    "schema_version": 1,
    "instrument": "1301.TSE",
    "start": "2025-01-06",
    "end": "2025-03-31",
    "granularity": "Daily",
    "initial_cash": 1_000_000,
}
"""

NO_SCENARIO_PY = """\
def strategy():
    pass
"""


@pytest.fixture
def strategy_with_scenario(tmp_path: Path) -> Path:
    p = tmp_path / "strategy.py"
    p.write_text(VALID_SCENARIO_PY, encoding="utf-8")
    return p


@pytest.fixture
def strategy_without_scenario(tmp_path: Path) -> Path:
    p = tmp_path / "no_scenario.py"
    p.write_text(NO_SCENARIO_PY, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# SCENARIO フォールバック
# ---------------------------------------------------------------------------


def test_all_cli_args_provided(strategy_with_scenario: Path) -> None:
    """CLI 引数がすべて揃っているときは SCENARIO を読まずに CLI 値を返す。"""
    instrument, start, end, gran, cash = _resolve_cli_params(
        strategy_path=str(strategy_with_scenario),
        cli_instrument="7203.TSE",
        cli_start="2025-04-01",
        cli_end="2025-04-30",
        cli_granularity="Minute",
        cli_initial_cash=500_000,
    )
    assert instrument == "7203.TSE"
    assert start == "2025-04-01"
    assert end == "2025-04-30"
    assert gran == "Minute"
    assert cash == 500_000


def test_fallback_to_scenario_when_all_omitted(strategy_with_scenario: Path) -> None:
    """--instrument / --start / --end をすべて省略したとき SCENARIO の値が使われる。"""
    instrument, start, end, gran, cash = _resolve_cli_params(
        strategy_path=str(strategy_with_scenario),
        cli_instrument=None,
        cli_start=None,
        cli_end=None,
        cli_granularity=None,
        cli_initial_cash=None,
    )
    assert instrument == "1301.TSE"
    assert start == "2025-01-06"
    assert end == "2025-03-31"
    assert gran == "Daily"
    assert cash == 1_000_000


def test_cli_instrument_overrides_scenario(strategy_with_scenario: Path) -> None:
    """--instrument だけ CLI で指定した場合、そちらが優先される。"""
    instrument, start, end, *_ = _resolve_cli_params(
        strategy_path=str(strategy_with_scenario),
        cli_instrument="7203.TSE",
        cli_start=None,
        cli_end=None,
        cli_granularity=None,
        cli_initial_cash=None,
    )
    assert instrument == "7203.TSE"
    assert start == "2025-01-06"   # SCENARIO フォールバック
    assert end == "2025-03-31"     # SCENARIO フォールバック


def test_cli_partial_override(strategy_with_scenario: Path) -> None:
    """start / end のみ CLI で指定。instrument だけ SCENARIO から取得。"""
    instrument, start, end, *_ = _resolve_cli_params(
        strategy_path=str(strategy_with_scenario),
        cli_instrument=None,
        cli_start="2025-05-01",
        cli_end="2025-05-31",
        cli_granularity=None,
        cli_initial_cash=None,
    )
    assert instrument == "1301.TSE"  # SCENARIO
    assert start == "2025-05-01"
    assert end == "2025-05-31"


def test_missing_required_arg_without_scenario_exits(
    strategy_without_scenario: Path,
) -> None:
    """SCENARIO なし + --instrument 省略 → SystemExit。"""
    with pytest.raises(SystemExit) as exc_info:
        _resolve_cli_params(
            strategy_path=str(strategy_without_scenario),
            cli_instrument=None,
            cli_start="2025-01-06",
            cli_end="2025-03-31",
            cli_granularity=None,
            cli_initial_cash=None,
        )
    assert exc_info.value.code == 1


def test_missing_start_without_scenario_exits(
    strategy_without_scenario: Path,
) -> None:
    """SCENARIO なし + --start 省略 → SystemExit。"""
    with pytest.raises(SystemExit) as exc_info:
        _resolve_cli_params(
            strategy_path=str(strategy_without_scenario),
            cli_instrument="1301.TSE",
            cli_start=None,
            cli_end="2025-03-31",
            cli_granularity=None,
            cli_initial_cash=None,
        )
    assert exc_info.value.code == 1


def test_default_granularity_when_omitted(strategy_without_scenario: Path) -> None:
    """--granularity 省略 + SCENARIO なし → デフォルト "Daily"。"""
    _, _, _, gran, _ = _resolve_cli_params(
        strategy_path=str(strategy_without_scenario),
        cli_instrument="1301.TSE",
        cli_start="2025-01-06",
        cli_end="2025-03-31",
        cli_granularity=None,
        cli_initial_cash=None,
    )
    assert gran == "Daily"


def test_default_initial_cash_when_omitted(strategy_without_scenario: Path) -> None:
    """--initial-cash 省略 + SCENARIO なし → デフォルト 1_000_000。"""
    _, _, _, _, cash = _resolve_cli_params(
        strategy_path=str(strategy_without_scenario),
        cli_instrument="1301.TSE",
        cli_start="2025-01-06",
        cli_end="2025-03-31",
        cli_granularity=None,
        cli_initial_cash=None,
    )
    assert cash == 1_000_000


def test_scenario_syntax_error_exits(tmp_path: Path) -> None:
    """SCENARIO 行が構文エラーのとき SystemExit code=1 になること（Fix 3）。"""
    broken = tmp_path / "broken.py"
    broken.write_text("def oops(:\n    pass\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        _resolve_cli_params(
            strategy_path=str(broken),
            cli_instrument=None,
            cli_start="2025-01-06",
            cli_end="2025-03-31",
            cli_granularity=None,
            cli_initial_cash=None,
        )
    assert exc_info.value.code == 1


def test_scenario_value_error_exits(tmp_path: Path) -> None:
    """SCENARIO がリテラルでない（dict comprehension 等）のとき SystemExit code=1 になること（Fix 3）。"""
    bad_scenario = tmp_path / "bad_scenario.py"
    bad_scenario.write_text(
        "SCENARIO = {k: v for k, v in [('instrument', '1301.TSE')]}\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        _resolve_cli_params(
            strategy_path=str(bad_scenario),
            cli_instrument=None,
            cli_start="2025-01-06",
            cli_end="2025-03-31",
            cli_granularity=None,
            cli_initial_cash=None,
        )
    assert exc_info.value.code == 1


def test_granularity_only_omitted_uses_scenario(tmp_path: Path) -> None:
    """--granularity のみ省略 + instrument/start/end 指定 → SCENARIO から granularity を取得（Fix 6）。

    SCENARIO の granularity を "Minute" にして instrument/start/end はすべて CLI 指定。
    needs_scenario に cli_granularity を含めないと SCENARIO が読まれず
    デフォルト "Daily" になってしまう。
    """
    scenario_py = tmp_path / "minute_scenario.py"
    scenario_py.write_text(
        'SCENARIO = {\n'
        '    "schema_version": 1,\n'
        '    "instrument": "1301.TSE",\n'
        '    "start": "2025-01-06",\n'
        '    "end": "2025-03-31",\n'
        '    "granularity": "Minute",\n'
        '    "initial_cash": 1_000_000,\n'
        '}\n',
        encoding="utf-8",
    )
    _, _, _, gran, _ = _resolve_cli_params(
        strategy_path=str(scenario_py),
        cli_instrument="1301.TSE",
        cli_start="2025-01-06",
        cli_end="2025-03-31",
        cli_granularity=None,        # 省略 → SCENARIO から "Minute" が来るはず
        cli_initial_cash=500_000,
    )
    assert gran == "Minute", f"SCENARIO の granularity='Minute' が使われるべきだが {gran!r} になった"


def test_reads_buy_and_hold_scenario(tmp_path: Path) -> None:
    """docs/example/test_strategy_daily.py の SCENARIO が CLI フォールバックで使える。"""
    buy_and_hold = Path(__file__).parent.parent.parent / "docs" / "example" / "test_strategy_daily.py"
    if not buy_and_hold.exists():
        pytest.skip("docs/example/test_strategy_daily.py not found")

    instrument, start, end, *_ = _resolve_cli_params(
        strategy_path=str(buy_and_hold),
        cli_instrument=None,
        cli_start=None,
        cli_end=None,
        cli_granularity=None,
        cli_initial_cash=None,
    )
    assert instrument == "1301.TSE"
    assert start == "2025-01-06"
    assert end == "2025-03-31"
