"""F6 / P5-scenario-in-strategy: path ガードのテスト。

_check_path_guard() および write_back() 経由での path ガード検証。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from engine.scenario import (
    _check_path_guard,
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


def _guard(
    path: Path,
    *,
    save_as: bool = True,
    current_path: Optional[Path] = None,
    loaded_path: Optional[Path] = None,
) -> None:
    """_check_path_guard のデフォルト引数付きラッパー。"""
    _check_path_guard(
        path,
        save_as=save_as,
        current_path=current_path,
        loaded_path=loaded_path,
    )


# ---------------------------------------------------------------------------
# test 1: .json 拡張子 → ValueError("path_guard_violation: ... .py extension")
# ---------------------------------------------------------------------------


def test_rejects_non_py_extension(tmp_path: Path) -> None:
    """.json 拡張子 → ValueError('path_guard_violation: ... .py extension')。"""
    path = tmp_path / "scenario.json"
    with pytest.raises(ValueError) as exc:
        _guard(path)
    assert "path_guard_violation" in str(exc.value)
    assert ".py" in str(exc.value) or "extension" in str(exc.value)


# ---------------------------------------------------------------------------
# test 2: %APPDATA%/flowsurface/ 配下 → ValueError("path_guard_violation")
# ---------------------------------------------------------------------------


def test_rejects_persistent_appdata_dir(tmp_path: Path) -> None:
    """%APPDATA%/flowsurface/ 配下 → ValueError('path_guard_violation')。"""
    # %APPDATA% を tmp_path に向けて persistent_dir を tmp_path/flowsurface にする
    fake_appdata = str(tmp_path)
    persistent_path = tmp_path / "flowsurface" / "strategy.py"
    persistent_path.parent.mkdir(parents=True, exist_ok=True)

    with patch.dict(os.environ, {"APPDATA": fake_appdata}):
        with pytest.raises(ValueError) as exc:
            _guard(persistent_path)
        assert "path_guard_violation" in str(exc.value)


# ---------------------------------------------------------------------------
# test 3: save_as=False, loaded_path=None → ValueError("path_guard_violation")
# ---------------------------------------------------------------------------


def test_rejects_save_without_prior_load(tmp_path: Path) -> None:
    """save_as=False, loaded_path=None → ValueError('path_guard_violation: ... loaded_path is None')。"""
    path = tmp_path / "strategy.py"
    with pytest.raises(ValueError) as exc:
        _guard(path, save_as=False, loaded_path=None)
    assert "path_guard_violation" in str(exc.value)
    assert "loaded_path" in str(exc.value) or "None" in str(exc.value)


# ---------------------------------------------------------------------------
# test 4: save_as=False, loaded_path=別のパス → ValueError("path_guard_violation")
# ---------------------------------------------------------------------------


def test_rejects_save_path_mismatch(tmp_path: Path) -> None:
    """save_as=False, loaded_path=別のパス → ValueError('path_guard_violation')。"""
    path = tmp_path / "strategy_a.py"
    other_path = tmp_path / "strategy_b.py"
    with pytest.raises(ValueError) as exc:
        _guard(path, save_as=False, loaded_path=other_path)
    assert "path_guard_violation" in str(exc.value)


# ---------------------------------------------------------------------------
# test 5: save_as=False, loaded_path=path と同じ → 成功（ValueError を raise しない）
# ---------------------------------------------------------------------------


def test_allows_save_when_paths_match(tmp_path: Path) -> None:
    """save_as=False, loaded_path=path と同じ → 成功（ValueError を raise しない）。"""
    path = tmp_path / "strategy.py"
    # 同じパスを loaded_path に渡す
    _guard(path, save_as=False, loaded_path=path)  # 例外が出なければ OK


# ---------------------------------------------------------------------------
# test 6: save_as=True, loaded_path=None → 成功
# ---------------------------------------------------------------------------


def test_allows_save_as_without_prior_load(tmp_path: Path) -> None:
    """save_as=True, loaded_path=None → 成功。"""
    path = tmp_path / "new_strategy.py"
    _guard(path, save_as=True, loaded_path=None)  # 例外が出なければ OK


# ---------------------------------------------------------------------------
# test 7: save_as=True, loaded_path=別パス → 成功
# ---------------------------------------------------------------------------


def test_allows_save_as_with_different_path(tmp_path: Path) -> None:
    """save_as=True, loaded_path=別パス → 成功。"""
    path = tmp_path / "strategy_new.py"
    other_path = tmp_path / "strategy_old.py"
    _guard(path, save_as=True, loaded_path=other_path)  # 例外が出なければ OK


# ---------------------------------------------------------------------------
# test 8: P5 spec 記載のケース
# ---------------------------------------------------------------------------


def test_save_without_prior_load(tmp_path: Path) -> None:
    """P5 spec 記載のケース:
    - Load 履歴 None + save_as=True → 許可
    - Load 履歴 None + save_as=False → 拒否
    """
    path = tmp_path / "strategy.py"

    # save_as=True, loaded_path=None → 許可
    _guard(path, save_as=True, loaded_path=None)  # 例外が出なければ OK

    # save_as=False, loaded_path=None → 拒否
    with pytest.raises(ValueError) as exc:
        _guard(path, save_as=False, loaded_path=None)
    assert "path_guard_violation" in str(exc.value)
