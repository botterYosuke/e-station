"""issue #42 Phase 2 functional: ``LIVE_SCENARIO`` 抽出と
``LoadLiveStrategyScenario`` ハンドラの TDD テスト。

対象:
    - ``engine.scenario.extract_live(path)`` — LIVE_SCENARIO を AST で安全抽出する
    - ``engine.server.DataEngineServer._handle_load_live_strategy_scenario`` —
      LoadLiveStrategyScenario コマンドのハンドラ

受け入れ基準:
    - #19 (parse_failed): AST parse / IO 失敗で ``Error{code:"strategy_parse_failed"}``
    - #22: gRPC 経路で送受信できる（mapping pin / wire 別ファイル）
    - #23: ``LIVE_SCENARIO`` 不在時は即時 ``LiveStrategyScenarioLoaded(全フィールド None)``

統一決定 #18 (Open Q2): LIVE_SCENARIO 不在時の即応答（5s 待たせない）。
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.scenario import ScenarioValidationError, extract_live


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


_VALID_LIVE_SCENARIO_SOURCE = """\
LIVE_SCENARIO = {
    "schema_version": 1,
    "instrument": "8306.T",
    "max_qty": 100,
    "max_notional_jpy": 500000,
    "venue": "tachibana",
}
"""

_VALID_LIVE_SCENARIO_DICT = {
    "schema_version": 1,
    "instrument": "8306.T",
    "max_qty": 100,
    "max_notional_jpy": 500000,
    "venue": "tachibana",
}


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# extract_live() テスト
# ---------------------------------------------------------------------------


def test_extract_live_returns_dict_when_present(tmp_path: Path) -> None:
    """LIVE_SCENARIO が存在する .py → dict を返すこと。"""
    f = _write(tmp_path, "live.py", _VALID_LIVE_SCENARIO_SOURCE)
    result = extract_live(f)
    assert result is not None, "extract_live() が None を返した（LIVE_SCENARIO は存在する）"
    assert result["instrument"] == "8306.T"
    assert result["max_qty"] == 100
    assert result["max_notional_jpy"] == 500_000
    assert result["venue"] == "tachibana"
    assert result["schema_version"] == 1


def test_extract_live_returns_none_when_absent(tmp_path: Path) -> None:
    """LIVE_SCENARIO が存在しない .py → None を返すこと（受け入れ基準 #23 の前段）。"""
    f = _write(
        tmp_path,
        "no_live.py",
        """\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "Daily",
            "initial_cash": 1_000_000,
        }
        """,
    )
    result = extract_live(f)
    assert result is None, f"LIVE_SCENARIO 不在で None 以外を返した: {result!r}"


def test_extract_live_with_strategy_init_kwargs(tmp_path: Path) -> None:
    """``strategy_init_kwargs`` が optional フィールドとして正しく抽出されること。"""
    f = _write(
        tmp_path,
        "kwargs.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 1,
            "instrument": "8306.T",
            "max_qty": 100,
            "max_notional_jpy": 500000,
            "venue": "tachibana",
            "strategy_init_kwargs": {"trade_size": 100, "atr_period": 14},
        }
        """,
    )
    result = extract_live(f)
    assert result is not None
    assert result["strategy_init_kwargs"] == {"trade_size": 100, "atr_period": 14}


def test_extract_live_rejects_list_instrument(tmp_path: Path) -> None:
    """schema_version=1 で ``instrument`` が list の場合 → ScenarioValidationError。

    統一決定: schema_version=1 は単一銘柄のみ。複数銘柄は別 schema が必要。
    """
    f = _write(
        tmp_path,
        "list_instrument.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 1,
            "instrument": ["8306.T", "7203.T"],
            "max_qty": 100,
            "max_notional_jpy": 500000,
            "venue": "tachibana",
        }
        """,
    )
    with pytest.raises(ScenarioValidationError) as exc:
        extract_live(f)
    assert "instrument" in str(exc.value), (
        f"エラー文言に 'instrument' が含まれていない: {exc.value}"
    )


def test_extract_live_rejects_missing_required_fields(tmp_path: Path) -> None:
    """必須フィールド (venue / max_qty / max_notional_jpy) 欠落 → ScenarioValidationError。"""
    # venue 欠落
    f = _write(
        tmp_path,
        "no_venue.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 1,
            "instrument": "8306.T",
            "max_qty": 100,
            "max_notional_jpy": 500000,
        }
        """,
    )
    with pytest.raises(ScenarioValidationError) as exc:
        extract_live(f)
    assert "venue" in str(exc.value), f"エラー文言に 'venue' が含まれていない: {exc.value}"

    # max_qty 欠落
    f2 = _write(
        tmp_path,
        "no_max_qty.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 1,
            "instrument": "8306.T",
            "max_notional_jpy": 500000,
            "venue": "tachibana",
        }
        """,
    )
    with pytest.raises(ScenarioValidationError) as exc:
        extract_live(f2)
    assert "max_qty" in str(exc.value), f"エラー文言に 'max_qty' が含まれていない: {exc.value}"

    # max_notional_jpy 欠落
    f3 = _write(
        tmp_path,
        "no_max_notional.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 1,
            "instrument": "8306.T",
            "max_qty": 100,
            "venue": "tachibana",
        }
        """,
    )
    with pytest.raises(ScenarioValidationError) as exc:
        extract_live(f3)
    assert "max_notional_jpy" in str(exc.value), (
        f"エラー文言に 'max_notional_jpy' が含まれていない: {exc.value}"
    )


def test_extract_live_rejects_wrong_field_types(tmp_path: Path) -> None:
    """フィールド型違反（max_qty に str など） → ScenarioValidationError。"""
    f = _write(
        tmp_path,
        "wrong_types.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 1,
            "instrument": "8306.T",
            "max_qty": "100",
            "max_notional_jpy": 500000,
            "venue": "tachibana",
        }
        """,
    )
    with pytest.raises(ScenarioValidationError) as exc:
        extract_live(f)
    assert "max_qty" in str(exc.value)


def test_extract_live_rejects_unknown_schema_version(tmp_path: Path) -> None:
    """schema_version != 1 → ScenarioValidationError。"""
    f = _write(
        tmp_path,
        "wrong_schema.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 2,
            "instrument": "8306.T",
            "max_qty": 100,
            "max_notional_jpy": 500000,
            "venue": "tachibana",
        }
        """,
    )
    with pytest.raises(ScenarioValidationError) as exc:
        extract_live(f)
    assert "schema_version" in str(exc.value), (
        f"エラー文言に 'schema_version' が含まれていない: {exc.value}"
    )


def test_extract_live_no_side_effect(tmp_path: Path) -> None:
    """副作用コードを含む .py を extract_live() しても副作用が起きないこと。"""
    side_effect_marker = tmp_path / "side_effect_was_here.txt"

    f = _write(
        tmp_path,
        "side_effect.py",
        f"""\
        # 副作用コード — import すると実行される
        import os
        open({str(side_effect_marker)!r}, "w").write("x")

        LIVE_SCENARIO = {{
            "schema_version": 1,
            "instrument": "8306.T",
            "max_qty": 100,
            "max_notional_jpy": 500000,
            "venue": "tachibana",
        }}
        """,
    )

    result = extract_live(f)
    assert result is not None
    assert result["instrument"] == "8306.T"
    assert not side_effect_marker.exists(), (
        "副作用ファイルが作成された — extract_live() がモジュールを import している"
    )


def test_legacy_strategy_unaffected(tmp_path: Path) -> None:
    """既存の `SCENARIO`-only 戦略 → extract() は dict を返し、extract_live() は None を返す。

    Phase 2 で extract_live を追加しても、replay path（SCENARIO のみ）は副作用を受けない pin。
    """
    from engine.scenario import extract

    f = _write(
        tmp_path,
        "legacy.py",
        """\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "Daily",
            "initial_cash": 1_000_000,
        }
        """,
    )
    # replay extract は dict を返す
    legacy_scn = extract(f)
    assert legacy_scn is not None
    assert legacy_scn["instrument"] == "1301.TSE"
    # live extract は None
    live_scn = extract_live(f)
    assert live_scn is None


def test_extract_live_syntax_error_raises(tmp_path: Path) -> None:
    """構文エラーの .py → SyntaxError を raise すること（server 側で `strategy_parse_failed` に変換）。"""
    f = _write(
        tmp_path,
        "broken.py",
        """\
        def oops(:
            pass
        """,
    )
    with pytest.raises(SyntaxError):
        extract_live(f)


def test_extract_live_rejects_dict_unpacking(tmp_path: Path) -> None:
    """LIVE_SCENARIO = {**other} → ValueError（dict literal 以外は拒否）。"""
    f = _write(
        tmp_path,
        "unpack.py",
        """\
        _base = {"schema_version": 1, "venue": "tachibana"}
        LIVE_SCENARIO = {**_base, "instrument": "8306.T", "max_qty": 100, "max_notional_jpy": 500000}
        """,
    )
    with pytest.raises(ValueError):
        extract_live(f)


# ---------------------------------------------------------------------------
# server._handle_load_live_strategy_scenario テスト
# ---------------------------------------------------------------------------


def _make_server():
    """テスト用の最小 DataEngineServer を生成する（既存の test_scenario_load.py と同じ流儀）。"""
    from engine.server import DataEngineServer

    mock_worker = MagicMock()
    mock_worker.prepare = AsyncMock(return_value=None)

    with patch("engine.server.BinanceWorker", return_value=mock_worker), \
         patch("engine.server.BybitWorker", return_value=mock_worker), \
         patch("engine.server.HyperliquidWorker", return_value=mock_worker), \
         patch("engine.server.MexcWorker", return_value=mock_worker), \
         patch("engine.server.OkexWorker", return_value=mock_worker):
        return DataEngineServer(port=0, token="t")


def _drain_outbox(server) -> list[dict]:
    """outbox に蓄積された dict メッセージを list で返す。"""
    return list(server._outbox)


def test_load_live_strategy_scenario_emits_loaded_event(tmp_path: Path) -> None:
    """LIVE_SCENARIO ありの .py → ``LiveStrategyScenarioLoaded`` を emit すること。"""
    server = _make_server()
    f = _write(tmp_path, "ok.py", _VALID_LIVE_SCENARIO_SOURCE)

    msg = {"request_id": "req-live-001", "strategy_path": str(f)}
    asyncio.run(server._handle_load_live_strategy_scenario(msg))

    out = _drain_outbox(server)
    assert len(out) == 1, f"Expected 1 outbox event, got {len(out)}: {out}"
    ev = out[0]
    assert ev["event"] == "LiveStrategyScenarioLoaded", f"Unexpected event: {ev}"
    assert ev["request_id"] == "req-live-001"
    assert ev["instrument_id"] == "8306.T"
    assert ev["max_qty"] == 100
    assert ev["max_notional_jpy"] == 500_000
    assert ev["venue"] == "tachibana"
    assert ev["strategy_init_kwargs"] is None


def test_load_live_strategy_scenario_with_strategy_init_kwargs(tmp_path: Path) -> None:
    """``strategy_init_kwargs`` がある場合は dict として event に載ること。"""
    server = _make_server()
    f = _write(
        tmp_path,
        "kwargs.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 1,
            "instrument": "8306.T",
            "max_qty": 100,
            "max_notional_jpy": 500000,
            "venue": "tachibana",
            "strategy_init_kwargs": {"trade_size": 100, "atr_period": 14},
        }
        """,
    )

    msg = {"request_id": "req-live-002", "strategy_path": str(f)}
    asyncio.run(server._handle_load_live_strategy_scenario(msg))

    out = _drain_outbox(server)
    assert len(out) == 1
    ev = out[0]
    assert ev["event"] == "LiveStrategyScenarioLoaded"
    assert ev["strategy_init_kwargs"] == {"trade_size": 100, "atr_period": 14}


def test_absent_live_scenario_emits_immediate_loaded_with_nulls(tmp_path: Path) -> None:
    """受け入れ基準 #23: LIVE_SCENARIO 不在でも 5s 待たずに即時応答すること（全 None）。"""
    server = _make_server()
    f = _write(
        tmp_path,
        "no_live.py",
        """\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "Daily",
            "initial_cash": 1_000_000,
        }
        """,
    )

    msg = {"request_id": "req-live-absent", "strategy_path": str(f)}
    # asyncio.run は同期的に await し終えるので、即時応答を観測できる。
    asyncio.run(server._handle_load_live_strategy_scenario(msg))

    out = _drain_outbox(server)
    assert len(out) == 1, f"LIVE_SCENARIO 不在は即時 1 件応答のみ。実際: {out}"
    ev = out[0]
    assert ev["event"] == "LiveStrategyScenarioLoaded", f"Unexpected event: {ev}"
    assert ev["request_id"] == "req-live-absent"
    assert ev["instrument_id"] is None
    assert ev["max_qty"] is None
    assert ev["max_notional_jpy"] is None
    assert ev["venue"] is None
    assert ev["strategy_init_kwargs"] is None


def test_strategy_parse_failed_emits_error_with_code(tmp_path: Path) -> None:
    """受け入れ基準 #19 の一部: AST parse 失敗 → ``Error{code:"strategy_parse_failed", request_id}``。"""
    server = _make_server()
    # ファイル不在で IO エラーを誘発する（OSError → strategy_parse_failed）
    bogus = tmp_path / "does_not_exist.py"

    msg = {"request_id": "req-live-fail", "strategy_path": str(bogus)}
    asyncio.run(server._handle_load_live_strategy_scenario(msg))

    out = _drain_outbox(server)
    assert len(out) == 1, f"Expected 1 outbox event, got {len(out)}: {out}"
    ev = out[0]
    assert ev["event"] == "Error", f"Expected Error event, got: {ev}"
    assert ev["code"] == "strategy_parse_failed", f"Unexpected code: {ev}"
    assert ev["request_id"] == "req-live-fail"
    assert "message" in ev


def test_strategy_parse_failed_on_syntax_error(tmp_path: Path) -> None:
    """受け入れ基準 #19 の一部: 構文エラー .py → ``Error{code:"strategy_parse_failed"}``。"""
    server = _make_server()
    f = _write(
        tmp_path,
        "broken.py",
        """\
        def oops(:
            pass
        """,
    )

    msg = {"request_id": "req-live-syntax", "strategy_path": str(f)}
    asyncio.run(server._handle_load_live_strategy_scenario(msg))

    out = _drain_outbox(server)
    assert len(out) == 1
    ev = out[0]
    assert ev["event"] == "Error"
    assert ev["code"] == "strategy_parse_failed"
    assert ev["request_id"] == "req-live-syntax"


def test_strategy_parse_failed_on_validation_error(tmp_path: Path) -> None:
    """validation 失敗（必須フィールド欠落） → ``Error{code:"strategy_parse_failed"}``。

    GUI 側は ``strategy_parse_failed`` を 1 つの error code で扱う設計
    （Rust 側 ``handlers/replay.rs`` で release_scenario_pending する code）。
    """
    server = _make_server()
    f = _write(
        tmp_path,
        "missing_venue.py",
        """\
        LIVE_SCENARIO = {
            "schema_version": 1,
            "instrument": "8306.T",
            "max_qty": 100,
            "max_notional_jpy": 500000,
        }
        """,
    )

    msg = {"request_id": "req-live-validate", "strategy_path": str(f)}
    asyncio.run(server._handle_load_live_strategy_scenario(msg))

    out = _drain_outbox(server)
    assert len(out) == 1
    ev = out[0]
    assert ev["event"] == "Error"
    assert ev["code"] == "strategy_parse_failed"
