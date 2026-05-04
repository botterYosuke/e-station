"""F6 / P5-scenario-in-strategy: SCENARIO 定数読み込み・検証テスト。

extract() と validate() の RED フェーズ TDD テスト。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from engine.scenario import ScenarioValidationError, extract, validate

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

_VALID_SCENARIO = {
    "schema_version": 1,
    "instrument": "1301.TSE",
    "start": "2025-01-06",
    "end": "2025-03-31",
    "granularity": "1m",
    "initial_cash": 1_000_000,
}


def _write(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# extract() テスト
# ---------------------------------------------------------------------------


def test_reads_annotated_assign(tmp_path: Path) -> None:
    """AnnAssign 形 `SCENARIO: Scenario = {...}` を正しく読めること。"""
    f = _write(
        tmp_path,
        "ann.py",
        """\
        from typing import TypedDict

        class Scenario(TypedDict):
            schema_version: int
            instrument: str
            start: str
            end: str
            granularity: str
            initial_cash: int

        SCENARIO: Scenario = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1_000_000,
        }
        """,
    )
    result = extract(f)
    assert result is not None, "extract() が None を返した（AnnAssign 形が未実装か）"
    assert result["instrument"] == "1301.TSE"
    assert result["schema_version"] == 1
    assert result["initial_cash"] == 1_000_000


def test_reads_plain_assign(tmp_path: Path) -> None:
    """Assign 形 `SCENARIO = {...}`（注釈なし）を正しく読めること。"""
    f = _write(
        tmp_path,
        "plain.py",
        """\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "7203.TSE",
            "start": "2025-04-01",
            "end": "2025-06-30",
            "granularity": "5m",
            "initial_cash": 500_000,
        }
        """,
    )
    result = extract(f)
    assert result is not None, "extract() が None を返した（Assign 形が未実装か）"
    assert result["instrument"] == "7203.TSE"
    assert result["granularity"] == "5m"


def test_treats_annotation_only_as_absent(tmp_path: Path) -> None:
    """`SCENARIO: Scenario`（value=None、注釈のみ宣言）は None を返すこと。"""
    f = _write(
        tmp_path,
        "annonly.py",
        """\
        from typing import TypedDict

        class Scenario(TypedDict):
            schema_version: int

        SCENARIO: Scenario
        """,
    )
    result = extract(f)
    assert result is None, (
        f"注釈のみ宣言は None を返すべきだが {result!r} を返した"
    )


def test_rejects_dict_unpacking(tmp_path: Path) -> None:
    """`SCENARIO = {{**other_dict, "instrument": "..."}}` → ValueError（"dict literal 以外" を含む）。"""
    f = _write(
        tmp_path,
        "unpack.py",
        """\
        _base = {"schema_version": 1}
        SCENARIO = {**_base, "instrument": "1301.TSE"}
        """,
    )
    with pytest.raises(ValueError) as exc:
        extract(f)
    assert "dict literal 以外" in str(exc.value), (
        f"エラー文言に 'dict literal 以外' が含まれていない: {exc.value}"
    )


def test_rejects_dict_comprehension(tmp_path: Path) -> None:
    """`SCENARIO = {{k: v for k, v in [...]}}` → ValueError。"""
    f = _write(
        tmp_path,
        "comp.py",
        """\
        SCENARIO = {k: v for k, v in [("instrument", "1301.TSE")]}
        """,
    )
    with pytest.raises(ValueError) as exc:
        extract(f)
    assert "dict literal 以外" in str(exc.value), (
        f"エラー文言に 'dict literal 以外' が含まれていない: {exc.value}"
    )


def test_read_only_no_side_effect(tmp_path: Path) -> None:
    """副作用コードを含む .py を extract() しても副作用が起きないこと。

    - sys.modules の増加なし（モジュールが import されない）
    - 副作用ファイルが作成されない
    """
    side_effect_marker = tmp_path / "side_effect_was_here.txt"

    f = _write(
        tmp_path,
        "side_effect.py",
        f"""\
        # 副作用コード — import すると実行される
        import os
        os.makedirs({str(tmp_path)!r}, exist_ok=True)
        open({str(side_effect_marker)!r}, "w").write("x")

        SCENARIO = {{
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1_000_000,
        }}
        """,
    )

    modules_before = set(sys.modules.keys())
    result = extract(f)
    modules_after = set(sys.modules.keys())

    # SCENARIO は読めていること
    assert result is not None, "extract() が None を返した"
    assert result["instrument"] == "1301.TSE"

    # 副作用ファイルが作成されていないこと
    assert not side_effect_marker.exists(), (
        "副作用ファイルが作成された — extract() がモジュールを import している可能性"
    )

    # sys.modules が増加していないこと（ast.parse のみ使用）
    new_modules = modules_after - modules_before
    # ast / pathlib など extract() 自体が使うモジュールは許容する
    # ユーザーコードの副作用として増えたモジュール（"os" 等）がないことを確認
    # ただし "os" は標準ライブラリとして既にロードされていることが多い
    # ここでは side_effect.py 自体がモジュールとして登録されないことを確認する
    assert not any("side_effect" in m for m in new_modules), (
        f"side_effect.py がモジュールとして登録された: {new_modules}"
    )


def test_returns_none_when_scenario_absent(tmp_path: Path) -> None:
    """SCENARIO が存在しない .py → None を返すこと。"""
    f = _write(
        tmp_path,
        "no_scenario.py",
        """\
        def main():
            pass
        """,
    )
    result = extract(f)
    assert result is None, f"SCENARIO 不在ファイルで None 以外を返した: {result!r}"


def test_syntax_error_raises(tmp_path: Path) -> None:
    """構文エラーの .py → SyntaxError を raise すること。"""
    f = _write(
        tmp_path,
        "broken.py",
        """\
        def oops(:
            pass
        """,
    )
    with pytest.raises(SyntaxError):
        extract(f)


# ---------------------------------------------------------------------------
# validate() テスト
# ---------------------------------------------------------------------------


def test_validate_missing_keys() -> None:
    """必須キー欠落 → ScenarioValidationError を raise すること。"""
    d = dict(_VALID_SCENARIO)
    del d["instrument"]
    with pytest.raises(ScenarioValidationError) as exc:
        validate(d)
    assert "instrument" in str(exc.value), (
        f"エラー文言に 'instrument' が含まれていない: {exc.value}"
    )


def test_validate_extra_keys() -> None:
    """余剰キー → ScenarioValidationError を raise すること。"""
    d = dict(_VALID_SCENARIO)
    d["unexpected_key"] = "oops"
    with pytest.raises(ScenarioValidationError) as exc:
        validate(d)
    assert "unexpected_key" in str(exc.value), (
        f"エラー文言に 'unexpected_key' が含まれていない: {exc.value}"
    )


def test_validate_wrong_type() -> None:
    """型違反（instrument に int） → ScenarioValidationError を raise すること。"""
    d = dict(_VALID_SCENARIO)
    d["instrument"] = 1301  # str が期待されるが int を渡す
    with pytest.raises(ScenarioValidationError) as exc:
        validate(d)
    assert "instrument" in str(exc.value), (
        f"エラー文言に 'instrument' が含まれていない: {exc.value}"
    )


def test_validate_bool_rejected_for_int() -> None:
    """initial_cash に bool を渡す → ScenarioValidationError（bool は int のサブクラスだが拒否）。"""
    d = dict(_VALID_SCENARIO)
    d["initial_cash"] = True  # isinstance(True, int) は True だが拒否すべき
    with pytest.raises(ScenarioValidationError) as exc:
        validate(d)
    assert "bool" in str(exc.value) or "initial_cash" in str(exc.value), (
        f"エラー文言に 'bool' または 'initial_cash' が含まれていない: {exc.value}"
    )


def test_validate_valid_dict_passes() -> None:
    """有効な dict は例外を raise しないこと（正常系確認）。"""
    validate(dict(_VALID_SCENARIO))  # 例外が出なければ OK


def test_validate_wrong_schema_version() -> None:
    """schema_version が SCHEMA_VERSION(=1) 以外 → ScenarioValidationError（Fix 8）。"""
    d = dict(_VALID_SCENARIO)
    d["schema_version"] = 2
    with pytest.raises(ScenarioValidationError) as exc:
        validate(d)
    assert "schema_version" in str(exc.value), (
        f"エラー文言に 'schema_version' が含まれていない: {exc.value}"
    )


# ---------------------------------------------------------------------------
# 実ファイルテスト
# ---------------------------------------------------------------------------


def test_reads_buy_and_hold_example() -> None:
    """`docs/example/buy_and_hold.py`（実際のファイル）から SCENARIO を読めること。"""
    repo_root = Path(__file__).parent.parent.parent
    buy_and_hold = repo_root / "docs" / "example" / "buy_and_hold.py"

    assert buy_and_hold.exists(), (
        f"buy_and_hold.py が見つからない: {buy_and_hold}\n"
        "リポジトリルートを確認してください"
    )

    result = extract(buy_and_hold)
    assert result is not None, (
        f"buy_and_hold.py から SCENARIO を読めなかった（None が返った）"
    )

    # 最低限のキー確認
    assert "instrument" in result, f"'instrument' キーがない: {result}"
    assert "schema_version" in result, f"'schema_version' キーがない: {result}"
    assert result["instrument"] == "1301.TSE", (
        f"instrument が想定値と異なる: {result['instrument']!r}"
    )

    # validate も通過すること
    validate(result)
