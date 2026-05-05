"""M6 受け入れテスト: 複数銘柄リプレイ対応 (T-AC-1 〜 T-AC-6)。

T-AC-1: v1 SCENARIO（既存）が無修正で動く（回帰）
T-AC-2: v2 SCENARIO（2銘柄）で両銘柄の Bar が on_bar に到達
T-AC-3: CLI --instrument A B で SCENARIO を上書きできる
T-AC-4: 旧 GUI（古い SCHEMA_MINOR）が単一銘柄として fallback して動く
T-AC-5: 10銘柄 × 1営業日（minute）でメモリ上限内に完走
T-AC-6: 未収録銘柄がある場合、明示的エラーで停止（silent fallback なし）
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from engine.replay_session import ReplaySession
from engine.scenario import ScenarioValidationError, validate


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_fake_result():
    from engine.nautilus.engine_runner import ReplayBacktestResult

    return ReplayBacktestResult(
        strategy_id="test",
        final_equity=Decimal("1_000_000"),
    )


def _make_mock_runner(events: list[dict]) -> MagicMock:
    """events を on_event に送出する mock NautilusRunner。"""

    def _fake_streaming(*args, on_event=None, stop_event=None, get_multiplier=None, **kwargs):
        for evt in events:
            if stop_event and stop_event.is_set():
                break
            if on_event:
                on_event(evt)
        return _make_fake_result()

    mock = MagicMock()
    mock.start_backtest_replay_streaming.side_effect = _fake_streaming
    return mock


@pytest.fixture()
def tmp_strategy(tmp_path: Path) -> str:
    """実在する空の戦略ファイルを返す。"""
    f = tmp_path / "strategy.py"
    f.write_text("# dummy strategy\n")
    return str(f)


# ---------------------------------------------------------------------------
# T-AC-1: v1 SCENARIO が無修正で動く（回帰テスト）
# ---------------------------------------------------------------------------


def test_ac1_v1_scenario_validate_passes() -> None:
    """T-AC-1: v1 SCENARIO が validate() を通過する。"""
    v1 = {
        "schema_version": 1,
        "instrument": "1301.TSE",
        "start": "2025-01-06",
        "end": "2025-03-31",
        "granularity": "Daily",
        "initial_cash": 1_000_000,
    }
    validate(v1)  # ScenarioValidationError が出なければ OK


def test_ac1_v1_load_and_run(tmp_strategy: str) -> None:
    """T-AC-1: v1 形式（単一 instrument_id str）での load/run が動く。"""
    events = [
        {"event": "EngineStarted", "strategy_id": "test", "account_id": "replay-TEST", "ts_event_ms": 0},
        {"event": "ReplayDataLoaded", "strategy_id": "test", "bars_loaded": 10, "trades_loaded": 0,
         "instrument_id": "1301.TSE", "instrument_ids": ["1301.TSE"], "granularity": "Daily", "ts_event_ms": 1},
        {"event": "EngineStopped", "strategy_id": "test", "final_equity": "1000000", "ts_event_ms": 2},
    ]
    mock_runner = _make_mock_runner(events)

    received: list[dict] = []
    with patch("engine.replay_session.ReplaySession._resolve_base_dir", return_value=None), \
         patch("engine.nautilus.jquants_loader.check_data_exists"), \
         patch("engine.replay_session.NautilusRunner", return_value=mock_runner):
        with ReplaySession(force_mode="inprocess") as s:
            s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
            s.run(strategy_file=tmp_strategy, on_event=received.append)

    event_names = [e["event"] for e in received]
    assert "EngineStarted" in event_names
    assert "EngineStopped" in event_names

    # runner に渡った instrument_id が元の str のままであることを確認
    call_kwargs = mock_runner.start_backtest_replay_streaming.call_args.kwargs
    assert call_kwargs["instrument_id"] == "1301.TSE"
    assert call_kwargs.get("instrument_ids") is None


# ---------------------------------------------------------------------------
# T-AC-2: v2 SCENARIO（2銘柄）で両銘柄の Bar が on_bar に到達
# ---------------------------------------------------------------------------


def test_ac2_v2_scenario_validate_passes() -> None:
    """T-AC-2: v2 SCENARIO が validate() を通過する。"""
    v2 = {
        "schema_version": 2,
        "instruments": ["1301.TSE", "7203.TSE"],
        "start": "2025-01-06",
        "end": "2025-03-31",
        "granularity": "Minute",
        "initial_cash": 1_000_000,
    }
    validate(v2)  # ScenarioValidationError が出なければ OK


def test_ac2_v2_load_passes_instrument_ids_to_runner(tmp_strategy: str) -> None:
    """T-AC-2: v2 形式（instruments list）での load/run が instrument_ids をランナーに渡す。"""
    events = [
        {"event": "EngineStarted", "strategy_id": "test", "account_id": "a", "ts_event_ms": 0},
        {"event": "ReplayDataLoaded", "strategy_id": "test", "bars_loaded": 20, "trades_loaded": 0,
         "instrument_id": "1301.TSE", "instrument_ids": ["1301.TSE", "7203.TSE"],
         "granularity": "Minute", "ts_event_ms": 1},
        {"event": "EngineStopped", "strategy_id": "test", "final_equity": "1000000", "ts_event_ms": 2},
    ]
    mock_runner = _make_mock_runner(events)

    received: list[dict] = []
    with patch("engine.replay_session.ReplaySession._resolve_base_dir", return_value=None), \
         patch("engine.nautilus.jquants_loader.check_data_exists"), \
         patch("engine.replay_session.NautilusRunner", return_value=mock_runner):
        with ReplaySession(force_mode="inprocess") as s:
            s.load(["1301.TSE", "7203.TSE"], "2025-01-06", "2025-03-31", "Minute")
            s.run(strategy_file=tmp_strategy, on_event=received.append)

    call_kwargs = mock_runner.start_backtest_replay_streaming.call_args.kwargs
    # instrument_ids が渡っていること
    assert call_kwargs["instrument_ids"] == ["1301.TSE", "7203.TSE"]
    # 後方互換: instrument_id は先頭銘柄
    assert call_kwargs["instrument_id"] == "1301.TSE"


def test_ac2_v2_load_stores_instrument_ids_in_params() -> None:
    """T-AC-2: load() が instrument_ids を _load_params に保存する。"""
    with patch("engine.replay_session.ReplaySession._resolve_base_dir", return_value=None), \
         patch("engine.nautilus.jquants_loader.check_data_exists"):
        with ReplaySession(force_mode="inprocess") as s:
            s.load(["1301.TSE", "7203.TSE"], "2025-01-06", "2025-03-31", "Minute")

    assert s._load_params is not None
    assert s._load_params["instrument_ids"] == ["1301.TSE", "7203.TSE"]
    assert s._load_params["instrument_id"] == "1301.TSE"  # 後方互換


def test_ac2_v2_check_data_exists_called_for_each_instrument() -> None:
    """T-AC-2: v2 load() は全銘柄について check_data_exists を呼ぶ。"""
    with patch("engine.replay_session.ReplaySession._resolve_base_dir", return_value=None), \
         patch("engine.nautilus.jquants_loader.check_data_exists") as mock_check:
        with ReplaySession(force_mode="inprocess") as s:
            s.load(["1301.TSE", "7203.TSE"], "2025-01-06", "2025-03-31", "Minute")

    assert mock_check.call_count == 2
    mock_check.assert_any_call("1301.TSE", "2025-01-06", "2025-03-31", "Minute")
    mock_check.assert_any_call("7203.TSE", "2025-01-06", "2025-03-31", "Minute")


# ---------------------------------------------------------------------------
# T-AC-3: CLI --instrument A B で SCENARIO を上書きできる
# ---------------------------------------------------------------------------


def test_ac3_cli_multi_instrument_parse(tmp_path: Path) -> None:
    """T-AC-3: --instrument A B が複数銘柄として解釈される（引数パース）。"""
    from engine.replay_session import _resolve_cli_params

    # v1 SCENARIO を持つ戦略ファイルを用意
    strategy_py = tmp_path / "strat.py"
    strategy_py.write_text(textwrap.dedent("""\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Daily",
            "initial_cash": 1000000,
        }
    """))

    instrument, start, end, gran, cash = _resolve_cli_params(
        strategy_path=str(strategy_py),
        cli_instrument=["1301.TSE", "7203.TSE"],  # nargs='+' の結果
        cli_start=None,
        cli_end=None,
        cli_granularity=None,
        cli_initial_cash=None,
    )

    # CLI で複数銘柄を指定した場合、list[str] で返る
    assert instrument == ["1301.TSE", "7203.TSE"]
    # start/end は SCENARIO からフォールバック
    assert start == "2025-01-06"
    assert end == "2025-01-10"


def test_ac3_cli_single_instrument_parse(tmp_path: Path) -> None:
    """T-AC-3: --instrument A のみ（1銘柄）は str で返る。"""
    from engine.replay_session import _resolve_cli_params

    strategy_py = tmp_path / "strat.py"
    strategy_py.write_text(textwrap.dedent("""\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Daily",
            "initial_cash": 1000000,
        }
    """))

    instrument, *_ = _resolve_cli_params(
        strategy_path=str(strategy_py),
        cli_instrument=["9999.TSE"],  # nargs='+' の結果だが要素 1 個
        cli_start=None,
        cli_end=None,
        cli_granularity=None,
        cli_initial_cash=None,
    )

    # 1要素リストは str に変換される（後方互換）
    assert instrument == "9999.TSE"


def test_ac3_cli_v2_scenario_no_override(tmp_path: Path) -> None:
    """T-AC-3: CLI 引数なし + v2 SCENARIO なら instruments list が返る。"""
    from engine.replay_session import _resolve_cli_params

    strategy_py = tmp_path / "strat.py"
    strategy_py.write_text(textwrap.dedent("""\
        SCENARIO = {
            "schema_version": 2,
            "instruments": ["1301.TSE", "7203.TSE"],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Minute",
            "initial_cash": 1000000,
        }
    """))

    instrument, *_ = _resolve_cli_params(
        strategy_path=str(strategy_py),
        cli_instrument=None,
        cli_start=None,
        cli_end=None,
        cli_granularity=None,
        cli_initial_cash=None,
    )

    assert instrument == ["1301.TSE", "7203.TSE"]


# ---------------------------------------------------------------------------
# T-AC-4: 旧 GUI（instrument_ids=None）が単一銘柄として fallback して動く
# ---------------------------------------------------------------------------


def test_ac4_schema_instrument_ids_defaults_to_none() -> None:
    """T-AC-4: instrument_ids フィールドが省略された IPC メッセージは None で受理される。"""
    from engine.schemas import LoadReplayData, EngineStartConfig, ReplayDataLoaded

    # instrument_ids を含まない旧 GUI 由来のメッセージが pydantic で受理されること
    msg = LoadReplayData.model_validate({
        "op": "LoadReplayData",
        "request_id": "r1",
        "instrument_id": "1301.TSE",
        "start_date": "2025-01-06",
        "end_date": "2025-01-10",
        "granularity": "Daily",
    })
    assert msg.instrument_ids is None
    assert msg.instrument_id == "1301.TSE"


def test_ac4_engine_start_config_backward_compat() -> None:
    """T-AC-4: EngineStartConfig が instrument_ids なしでも受理される。"""
    from engine.schemas import EngineStartConfig

    cfg = EngineStartConfig(
        instrument_id="1301.TSE",
        start_date="2025-01-06",
        end_date="2025-01-10",
        initial_cash="1000000",
        granularity="Daily",
    )
    assert cfg.instrument_ids is None
    assert cfg.instrument_id == "1301.TSE"


def test_ac4_replay_data_loaded_backward_compat() -> None:
    """T-AC-4: ReplayDataLoaded が instrument_ids なしでも受理される。"""
    from engine.schemas import ReplayDataLoaded

    msg = ReplayDataLoaded.model_validate({
        "event": "ReplayDataLoaded",
        "bars_loaded": 100,
        "trades_loaded": 0,
        "instrument_id": "1301.TSE",
        "granularity": "Minute",
        "ts_event_ms": 12345,
    })
    assert msg.instrument_ids is None
    assert msg.instrument_id == "1301.TSE"


# ---------------------------------------------------------------------------
# T-AC-5: 10銘柄 × 1営業日（minute）でメモリ上限内に完走
# (J-Quants データが必要なため、データなしの環境では mock で構造確認のみ)
# ---------------------------------------------------------------------------


def test_ac5_10_instruments_load_passes_all_to_runner(tmp_strategy: str) -> None:
    """T-AC-5: 10銘柄の load/run が instrument_ids をランナーに渡す。"""
    instruments = [
        "1301.TSE", "7203.TSE", "6758.TSE", "9984.TSE", "8306.TSE",
        "7974.TSE", "4502.TSE", "6861.TSE", "8035.TSE", "9433.TSE",
    ]
    events = [
        {"event": "EngineStarted", "strategy_id": "test", "account_id": "a", "ts_event_ms": 0},
        {"event": "ReplayDataLoaded", "strategy_id": "test", "bars_loaded": 15000,
         "trades_loaded": 0, "instrument_id": instruments[0],
         "instrument_ids": instruments, "granularity": "Minute", "ts_event_ms": 1},
        {"event": "EngineStopped", "strategy_id": "test", "final_equity": "10000000", "ts_event_ms": 2},
    ]
    mock_runner = _make_mock_runner(events)

    with patch("engine.replay_session.ReplaySession._resolve_base_dir", return_value=None), \
         patch("engine.nautilus.jquants_loader.check_data_exists"), \
         patch("engine.replay_session.NautilusRunner", return_value=mock_runner):
        with ReplaySession(force_mode="inprocess") as s:
            s.load(instruments, "2025-01-06", "2025-01-06", "Minute")
            s.run(strategy_file=tmp_strategy, on_event=lambda _: None)

    call_kwargs = mock_runner.start_backtest_replay_streaming.call_args.kwargs
    assert call_kwargs["instrument_ids"] == instruments
    assert len(call_kwargs["instrument_ids"]) == 10


# ---------------------------------------------------------------------------
# T-AC-6: 未収録銘柄がある場合、明示的エラーで停止
# ---------------------------------------------------------------------------


def test_ac6_missing_instrument_raises_on_load() -> None:
    """T-AC-6: check_data_exists が FileNotFoundError を raise すると load() から伝播する。"""
    def _check_fail(instrument_id: str, *args, **kwargs) -> None:
        if instrument_id == "9999.TSE":
            raise FileNotFoundError(f"データが見つかりません: {instrument_id}")

    with patch("engine.replay_session.ReplaySession._resolve_base_dir", return_value=None), \
         patch("engine.nautilus.jquants_loader.check_data_exists", side_effect=_check_fail):
        with ReplaySession(force_mode="inprocess") as s:
            with pytest.raises(FileNotFoundError, match="9999.TSE"):
                s.load(["1301.TSE", "9999.TSE"], "2025-01-06", "2025-01-10", "Minute")


def test_ac6_missing_instrument_does_not_partial_load() -> None:
    """T-AC-6: 1銘柄でも失敗したら load は LOADED 状態にならない（silent fallback なし）。"""
    call_count = 0

    def _check_fail(instrument_id: str, *args, **kwargs) -> None:
        nonlocal call_count
        call_count += 1
        if instrument_id == "9999.TSE":
            raise FileNotFoundError(f"データが見つかりません: {instrument_id}")

    with patch("engine.replay_session.ReplaySession._resolve_base_dir", return_value=None), \
         patch("engine.nautilus.jquants_loader.check_data_exists", side_effect=_check_fail):
        with ReplaySession(force_mode="inprocess") as s:
            with pytest.raises(FileNotFoundError):
                s.load(["1301.TSE", "9999.TSE"], "2025-01-06", "2025-01-10", "Minute")
            # load 失敗後は IDLE のまま（LOADED 遷移していない）
            assert s.status == "idle"


def test_ac6_missing_instrument_in_engine_runner() -> None:
    """T-AC-6: engine_runner が FileNotFoundError を握り潰さずに伝播する。"""
    from engine.nautilus.engine_runner import NautilusRunner

    runner = NautilusRunner()

    def _raise_file_not_found(instrument_id, *args, **kwargs):
        if instrument_id == "9999.TSE":
            raise FileNotFoundError(f"j-quants data not found: {instrument_id}")
        return iter([])

    on_events: list[dict] = []

    with patch("engine.nautilus.engine_runner.load_minute_bars", side_effect=_raise_file_not_found), \
         patch("engine.nautilus.engine_runner.BacktestEngine"), \
         patch("engine.nautilus.engine_runner.InstrumentId") as mock_iid_cls, \
         patch("engine.nautilus.instrument_factory.make_equity_instrument"):
        # InstrumentId.from_str("9999.TSE") が同一 venue を返すように設定
        mock_iid = MagicMock()
        mock_iid.venue.value = "TSE"
        mock_iid.symbol.value = "9999"
        mock_iid_cls.from_str.return_value = mock_iid

        with pytest.raises(FileNotFoundError, match="9999.TSE"):
            runner.start_backtest_replay(
                strategy_id="test",
                instrument_id="9999.TSE",
                instrument_ids=["1301.TSE", "9999.TSE"],
                start_date="2025-01-06",
                end_date="2025-01-10",
                granularity="Minute",
                initial_cash=1_000_000,
                on_event=on_events.append,
            )


# ---------------------------------------------------------------------------
# schema_version 検証
# ---------------------------------------------------------------------------


def test_v2_empty_instruments_fails_validate() -> None:
    """v2 で instruments が空リストなら ScenarioValidationError。"""
    with pytest.raises(ScenarioValidationError, match="empty"):
        validate({
            "schema_version": 2,
            "instruments": [],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Daily",
            "initial_cash": 1_000_000,
        })


def test_v2_non_str_instruments_fails_validate() -> None:
    """v2 で instruments 要素が str でないなら ScenarioValidationError。"""
    with pytest.raises(ScenarioValidationError):
        validate({
            "schema_version": 2,
            "instruments": [1301, "7203.TSE"],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Daily",
            "initial_cash": 1_000_000,
        })


def test_v1_with_instruments_key_fails_validate() -> None:
    """v1 に instruments（複数形）が混入したらエラー（余剰キー）。"""
    with pytest.raises(ScenarioValidationError):
        validate({
            "schema_version": 1,
            "instrument": "1301.TSE",
            "instruments": ["1301.TSE"],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Daily",
            "initial_cash": 1_000_000,
        })


def test_v2_with_instrument_key_fails_validate() -> None:
    """v2 に instrument（単数形）が混入したらエラー（余剰キー）。"""
    with pytest.raises(ScenarioValidationError):
        validate({
            "schema_version": 2,
            "instrument": "1301.TSE",
            "instruments": ["1301.TSE"],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Daily",
            "initial_cash": 1_000_000,
        })
