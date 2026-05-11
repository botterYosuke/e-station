"""issue #42 Phase 5: ``examples/test_strategy_*.py`` の ``LIVE_SCENARIO`` 定数 pin.

各 example .py がライブ起動用の ``LIVE_SCENARIO`` 定数を持ち、必須キーが揃っている
ことを ``engine.scenario.extract_live`` 経由で検証する。replay 用 ``SCENARIO`` は
触らない（既存 replay 互換）。

受け入れ基準 #3（`examples/README.md` に replay → demo → prod の同ファイル完全
コマンド例）の補強として、example が実際に live 経路で prefill 可能であることを
test 単位で固定する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.scenario import LIVE_REQUIRED_KEYS_V1, extract_live


_EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"


def _assert_live_scenario_well_formed(path: Path) -> dict:
    """``extract_live(path)`` が dict を返し、必須キーが揃っていることを assert."""
    assert path.is_file(), f"example not found: {path}"
    scenario = extract_live(path)
    assert scenario is not None, (
        f"{path.name}: LIVE_SCENARIO が抽出できなかった "
        "（example は live 経路で prefill 可能であるべき）"
    )
    # 必須キー網羅
    missing = LIVE_REQUIRED_KEYS_V1 - scenario.keys()
    assert not missing, f"{path.name}: 必須キー欠落 {sorted(missing)}"
    # venue は capability `supports_live_strategy=True` の値
    assert scenario["venue"] in ("tachibana", "kabu_station"), (
        f"{path.name}: venue={scenario['venue']!r} は未サポート "
        "（capability supports_live_strategy=True の値を使うこと）"
    )
    # max_qty / max_notional_jpy は CLI と同じレンジ
    assert 1 <= int(scenario["max_qty"]) <= 10_000, (
        f"{path.name}: max_qty={scenario['max_qty']!r} がレンジ外 [1, 10000]"
    )
    assert 1 <= int(scenario["max_notional_jpy"]) <= 100_000_000, (
        f"{path.name}: max_notional_jpy={scenario['max_notional_jpy']!r} が "
        "レンジ外 [1, 100000000]"
    )
    return scenario


# ---------------------------------------------------------------------------
# 各 example の LIVE_SCENARIO pin
# ---------------------------------------------------------------------------


def test_test_strategy_minute_has_live_scenario() -> None:
    """``examples/test_strategy_minute.py`` が LIVE_SCENARIO を持つ."""
    path = _EXAMPLES_DIR / "test_strategy_minute.py"
    scenario = _assert_live_scenario_well_formed(path)
    # SCENARIO の instrument と一致していること（同戦略を replay → live で動かす建前）
    from engine.scenario import extract
    replay_scn = extract(path)
    assert replay_scn is not None, "test_strategy_minute.py: SCENARIO が抽出できない"
    assert scenario["instrument"] == replay_scn["instrument"], (
        "test_strategy_minute.py: LIVE_SCENARIO['instrument'] と "
        "SCENARIO['instrument'] は一致する設計（同じ戦略ファイルを replay → live で）"
    )


def test_test_strategy_daily_has_live_scenario() -> None:
    """``examples/test_strategy_daily.py`` が LIVE_SCENARIO を持つ."""
    path = _EXAMPLES_DIR / "test_strategy_daily.py"
    scenario = _assert_live_scenario_well_formed(path)
    from engine.scenario import extract
    replay_scn = extract(path)
    assert replay_scn is not None
    assert scenario["instrument"] == replay_scn["instrument"]


def test_test_strategy_trade_has_live_scenario() -> None:
    """``examples/test_strategy_trade.py`` が LIVE_SCENARIO を持つ."""
    path = _EXAMPLES_DIR / "test_strategy_trade.py"
    scenario = _assert_live_scenario_well_formed(path)
    from engine.scenario import extract
    replay_scn = extract(path)
    assert replay_scn is not None
    assert scenario["instrument"] == replay_scn["instrument"]


# ---------------------------------------------------------------------------
# default venue は tachibana に揃える（Phase 5 設計判断）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "test_strategy_minute.py",
        "test_strategy_daily.py",
        "test_strategy_trade.py",
    ],
)
def test_default_live_scenario_venue_is_tachibana(filename: str) -> None:
    """test_strategy_*.py の LIVE_SCENARIO は default で venue=tachibana を使う.

    kabu_station 例は別ファイル / コメントで提供する（Phase 5 引き継ぎ）。
    """
    path = _EXAMPLES_DIR / filename
    scenario = extract_live(path)
    assert scenario is not None
    assert scenario["venue"] == "tachibana", (
        f"{filename}: default venue は 'tachibana'（issue #42 Phase 5 の建前）"
    )
