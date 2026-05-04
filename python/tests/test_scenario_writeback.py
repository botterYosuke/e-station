"""F6 / P5-scenario-in-strategy: write_back() のテスト。

P5-scenario-in-strategy.md §F6c DoD より:
  - CST 置換（コメント・空白完全保持）
  - atomic write: tempfile → os.replace()
  - 世代付きバックアップ: <file>.py.bak.<UTC秒>
  - 2 段検証: importlib reload + validate()
  - 検証失敗時は .bak から rollback
"""

from __future__ import annotations

import glob
import textwrap
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from engine.scenario import (
    ScenarioValidationError,
    write_back,
)

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

VALID_SCENARIO = {
    "schema_version": 1,
    "instrument": "1301.TSE",
    "start": "2025-01-06",
    "end": "2025-03-31",
    "granularity": "1m",
    "initial_cash": 1_000_000,
}

VALID_SCENARIO_2 = {
    "schema_version": 1,
    "instrument": "7203.TSE",
    "start": "2025-04-01",
    "end": "2025-06-30",
    "granularity": "5m",
    "initial_cash": 500_000,
}


def _write_py(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return p


def _do_write_back(
    path: Path,
    scenario: dict,
    *,
    save_as: bool = True,
    current_path: Optional[Path] = None,
    loaded_path: Optional[Path] = None,
) -> None:
    """write_back() のデフォルト引数付きラッパー。"""
    write_back(
        path,
        scenario,
        save_as=save_as,
        current_path=current_path,
        loaded_path=loaded_path,
    )


# ---------------------------------------------------------------------------
# test 1: SCENARIO = {...} が新しい値で置換される。コメント・他コードは変化しない
# ---------------------------------------------------------------------------


def test_replaces_existing_scenario_plain_assign(tmp_path: Path) -> None:
    """SCENARIO = {...} が新しい値で置換される。コメント・他コードは変化しない。"""
    source = """\
        # ストラテジーファイル
        # コメントは保持されるべき

        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1_000_000,
        }

        # ここから戦略ロジック
        def on_bar(bar):
            pass
        """
    path = _write_py(tmp_path, "strategy.py", source)
    _do_write_back(path, VALID_SCENARIO_2, save_as=True)

    result_text = path.read_text(encoding="utf-8")

    # 新しい値が書き込まれていること
    assert "7203.TSE" in result_text
    assert "500000" in result_text or "500_000" in result_text

    # コメントが保持されていること
    assert "# ストラテジーファイル" in result_text
    assert "# コメントは保持されるべき" in result_text
    assert "# ここから戦略ロジック" in result_text

    # 他のコードが保持されていること
    assert "def on_bar(bar):" in result_text
    assert "pass" in result_text

    # 古い値が残っていないこと
    assert "1301.TSE" not in result_text


# ---------------------------------------------------------------------------
# test 2: SCENARIO: Scenario = {...} が新しい値で置換される（注釈を保持）
# ---------------------------------------------------------------------------


def test_replaces_existing_scenario_ann_assign(tmp_path: Path) -> None:
    """SCENARIO: Scenario = {...} が新しい値で置換される（注釈を保持）。"""
    source = """\
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
        """
    path = _write_py(tmp_path, "strategy_ann.py", source)
    _do_write_back(path, VALID_SCENARIO_2, save_as=True)

    result_text = path.read_text(encoding="utf-8")

    # 新しい値が書き込まれていること
    assert "7203.TSE" in result_text

    # 注釈（Scenario 型）が保持されていること（AnnAssign ノードとして）
    assert "Scenario" in result_text

    # 古い値が残っていないこと
    assert "1301.TSE" not in result_text


# ---------------------------------------------------------------------------
# test 3: SCENARIO が存在しない .py → import 直後に挿入される
# ---------------------------------------------------------------------------


def test_inserts_scenario_when_absent(tmp_path: Path) -> None:
    """SCENARIO が存在しない .py → import 直後に挿入される。"""
    source = """\
        import os
        import sys

        def main():
            print("hello")
        """
    path = _write_py(tmp_path, "no_scenario.py", source)
    _do_write_back(path, VALID_SCENARIO, save_as=True)

    result_text = path.read_text(encoding="utf-8")

    # SCENARIO が挿入されていること
    assert "SCENARIO" in result_text
    assert "1301.TSE" in result_text

    # import より後に SCENARIO が現れること
    scenario_pos = result_text.index("SCENARIO")
    import_pos = result_text.index("import sys")
    assert scenario_pos > import_pos, "SCENARIO が import より前に挿入された"

    # 既存のコードが保持されていること
    assert "def main():" in result_text
    assert 'print("hello")' in result_text


# ---------------------------------------------------------------------------
# test 4: write_back 後に .bak.<UTC秒> が生成される
# ---------------------------------------------------------------------------


def test_atomic_write_creates_bak(tmp_path: Path) -> None:
    """write_back 後に .bak.<UTC秒> が生成される。"""
    path = _write_py(
        tmp_path,
        "strategy.py",
        """\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1_000_000,
        }
        """,
    )
    _do_write_back(path, VALID_SCENARIO_2, save_as=True)

    bak_files = list(tmp_path.glob("strategy.py.bak.*"))
    assert len(bak_files) >= 1, f".bak ファイルが生成されなかった: {list(tmp_path.iterdir())}"

    # .bak のファイル名形式を確認 (.bak.<数字> or .bak.<数字>-<数字>)
    bak_name = bak_files[0].name
    assert bak_name.startswith("strategy.py.bak."), f"bak ファイル名の形式が違う: {bak_name}"


# ---------------------------------------------------------------------------
# test 5: 同秒に 2 回保存した場合、2 つのバックアップが生成される（上書きなし）
# ---------------------------------------------------------------------------


def test_no_bak_overwrite_same_second(tmp_path: Path) -> None:
    """同秒に 2 回保存した場合、.bak.<UTC秒> と .bak.<UTC秒>-1 の 2 つのバックアップが生成される。"""
    source = """\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1_000_000,
        }
        """
    path = _write_py(tmp_path, "strategy.py", source)

    # time.time() を固定して同秒を再現
    fixed_time = 1_700_000_000.0
    with patch("engine.scenario.time.time", return_value=fixed_time):
        _do_write_back(path, VALID_SCENARIO_2, save_as=True)
        # 2 回目: path は更新済みなので再度保存
        _do_write_back(path, VALID_SCENARIO, save_as=True)

    bak_files = sorted(tmp_path.glob("strategy.py.bak.*"))
    assert len(bak_files) >= 2, (
        f"同秒 2 回保存で bak が 2 つ生成されるべきだが {len(bak_files)} つしかない: {bak_files}"
    )

    # 2 つ目のバックアップに -1 suffix がついていること
    names = [f.name for f in bak_files]
    assert any("-1" in n for n in names), f"2 つ目の bak に -1 suffix がない: {names}"


# ---------------------------------------------------------------------------
# test 6: validate() が失敗する scenario を書いた後、rollback が起き元ファイルと byte-diff 0
# ---------------------------------------------------------------------------


def test_writeback_rollback_on_validate_failure(tmp_path: Path) -> None:
    """validate() が失敗する scenario を書いた後、rollback が起き、元ファイルと byte-diff 0 になる。"""
    source = """\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1_000_000,
        }
        """
    path = _write_py(tmp_path, "strategy.py", source)
    original_bytes = path.read_bytes()

    # instrument が int（型違反）→ libcst は valid Python コードを生成するが
    # _verify_writeback → validate() でのみ失敗する
    invalid_scenario = {
        "schema_version": 1,
        "instrument": 1301,  # int、型違反
        "start": "2025-01-06",
        "end": "2025-03-31",
        "granularity": "1m",
        "initial_cash": 1_000_000,
    }

    with pytest.raises(ScenarioValidationError):
        _do_write_back(path, invalid_scenario, save_as=True)

    # rollback でオリジナルに戻っていること
    rolled_back_bytes = path.read_bytes()
    assert rolled_back_bytes == original_bytes, (
        f"rollback 後のファイルがオリジナルと一致しない "
        f"(original={len(original_bytes)} bytes, rolled_back={len(rolled_back_bytes)} bytes)"
    )


# ---------------------------------------------------------------------------
# test 7: import 失敗を simulate し、write_back が SyntaxError を raise しrollback
# ---------------------------------------------------------------------------


def test_writeback_rollback_on_import_error(tmp_path: Path) -> None:
    """_replace_or_insert_scenario を monkeypatch して壊れた Python コードを返させ、
    write_back が SyntaxError を raise し rollback する。"""
    source = """\
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1_000_000,
        }
        """
    path = _write_py(tmp_path, "strategy.py", source)
    original_bytes = path.read_bytes()

    # 壊れた Python コード（構文エラー）を返す monkeypatch
    broken_code = "def oops(:\n    pass\n"

    with patch(
        "engine.scenario._replace_or_insert_scenario", return_value=broken_code
    ):
        with pytest.raises(SyntaxError):
            _do_write_back(path, VALID_SCENARIO_2, save_as=True)

    # rollback でオリジナルに戻っていること
    rolled_back_bytes = path.read_bytes()
    assert rolled_back_bytes == original_bytes, (
        f"rollback 後のファイルがオリジナルと一致しない "
        f"(original={len(original_bytes)} bytes, rolled_back={len(rolled_back_bytes)} bytes)"
    )


# ---------------------------------------------------------------------------
# test 8: 他のコード・コメント・docstring・空行が完全に保持される
# ---------------------------------------------------------------------------


def test_other_code_and_comments_preserved(tmp_path: Path) -> None:
    """SCENARIO 置換後、他のコード・コメント・docstring・空行が完全に保持される。"""
    source = """\
        \"\"\"ストラテジーモジュールの docstring。\"\"\"

        # 外部インポート
        import os

        # SCENARIO 定数
        SCENARIO = {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1_000_000,
        }

        # 戦略関数
        def on_bar(bar):
            \"\"\"バーごとに呼ばれる関数。\"\"\"
            # 何もしない
            pass


        def on_finish():
            pass
        """
    path = _write_py(tmp_path, "strategy.py", source)

    # SCENARIO 以外のコードを抽出（SCENARIO ブロックを除く）
    lines_before = [
        line
        for line in textwrap.dedent(source).splitlines()
        if "SCENARIO" not in line
        and "1301.TSE" not in line
        and "1_000_000" not in line
        and "2025-01-06" not in line
        and "2025-03-31" not in line
    ]

    _do_write_back(path, VALID_SCENARIO_2, save_as=True)

    result_text = path.read_text(encoding="utf-8")

    # docstring が保持されていること
    assert '"""ストラテジーモジュールの docstring。"""' in result_text

    # コメントが保持されていること
    assert "# 外部インポート" in result_text
    assert "# SCENARIO 定数" in result_text
    assert "# 戦略関数" in result_text
    assert "# 何もしない" in result_text

    # import が保持されていること
    assert "import os" in result_text

    # 関数が保持されていること
    assert "def on_bar(bar):" in result_text
    assert "def on_finish():" in result_text

    # 新しい SCENARIO 値が書き込まれていること
    assert "7203.TSE" in result_text


# ---------------------------------------------------------------------------
# test 9: path が存在しない場合に新規ファイルとして SCENARIO block だけが書き出される
# ---------------------------------------------------------------------------


def test_new_file_created(tmp_path: Path) -> None:
    """path が存在しない場合に新規ファイルとして SCENARIO block だけが書き出される。
    (save_as=True, loaded_path=None)"""
    new_path = tmp_path / "new_strategy.py"
    assert not new_path.exists(), "テスト開始時点でファイルが存在してはいけない"

    _do_write_back(new_path, VALID_SCENARIO, save_as=True, loaded_path=None)

    assert new_path.exists(), "write_back 後にファイルが作成されていない"

    result_text = new_path.read_text(encoding="utf-8")

    # SCENARIO ブロックが書き出されていること
    assert "SCENARIO" in result_text
    assert "1301.TSE" in result_text
    assert "schema_version" in result_text

    # bak ファイルは作成されないこと（新規ファイルなので）
    bak_files = list(tmp_path.glob("new_strategy.py.bak.*"))
    assert len(bak_files) == 0, f"新規ファイルなのに bak が生成された: {bak_files}"
