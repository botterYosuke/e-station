"""issue #42 Phase 6: ``tools/lint/check_examples_readme.py`` のユニットテスト.

統一決定 #19 / 受け入れ基準 #3 / #4: ``examples/README.md`` に「ライブで動かす」
（または等価の見出し）が存在することを assert する lint script の挙動を pin する。

RED フェーズで先に書いて、lint script 本体実装で GREEN にする TDD 手順。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools.lint.check_examples_readme import (
    LiveSectionMissingError,
    check_examples_readme,
)


def _make_repo(tmp_path: Path, readme_body: str) -> Path:
    """``examples/README.md`` だけを持つ最小 repo を tmp_path に作成する."""
    repo = tmp_path / "repo"
    examples = repo / "examples"
    examples.mkdir(parents=True)
    (examples / "README.md").write_text(readme_body, encoding="utf-8")
    return repo


# ---------------------------------------------------------------------------
# GREEN: 「ライブで動かす」section が存在するケース
# ---------------------------------------------------------------------------


def test_live_section_present_passes_when_section_exists(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """\
        # ユーザー戦略サンプル

        ## A. headless

        ```
        echo hello
        ```

        ## C. ライブで動かす（demo 口座）

        TODO: demo / prod commands here.
        """
    )
    repo = _make_repo(tmp_path, body)
    # 例外を raise しないこと（戻り値は None）
    assert check_examples_readme(repo_root=repo) is None


def test_live_section_present_accepts_japanese_only_heading(tmp_path: Path) -> None:
    """「ライブで動かす」だけの heading（A./B./C. プレフィックスなし）でも OK."""
    body = textwrap.dedent(
        """\
        # README

        ## ライブで動かす

        Some content.
        """
    )
    repo = _make_repo(tmp_path, body)
    assert check_examples_readme(repo_root=repo) is None


def test_live_section_present_accepts_h3_heading(tmp_path: Path) -> None:
    """`###` 見出しでも検出される（深さは問わない、文字列マッチ）."""
    body = textwrap.dedent(
        """\
        # README

        ## 起動

        ### C. ライブで動かす（demo 口座）

        Demo run example.
        """
    )
    repo = _make_repo(tmp_path, body)
    assert check_examples_readme(repo_root=repo) is None


# ---------------------------------------------------------------------------
# RED: section 不在のケース → reject
# ---------------------------------------------------------------------------


def test_live_section_present_fails_when_section_missing(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """\
        # README

        ## A. headless

        ## B. GUI で目視

        ## 規約
        """
    )
    repo = _make_repo(tmp_path, body)
    with pytest.raises(LiveSectionMissingError):
        check_examples_readme(repo_root=repo)


def test_live_section_present_fails_when_match_in_body_only(tmp_path: Path) -> None:
    """heading ではなく本文中に「ライブで動かす」とだけ書かれていても reject."""
    body = textwrap.dedent(
        """\
        # README

        ## A. headless

        本文中で「ライブで動かす」と一瞬触れているが見出しではない。

        ## 規約
        """
    )
    repo = _make_repo(tmp_path, body)
    with pytest.raises(LiveSectionMissingError):
        check_examples_readme(repo_root=repo)


def test_live_section_present_fails_when_readme_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "examples").mkdir(parents=True)
    # README.md を意図的に作らない
    with pytest.raises(LiveSectionMissingError):
        check_examples_readme(repo_root=repo)


# ---------------------------------------------------------------------------
# CLI entrypoint: `python -m tools.lint.check_examples_readme`
# ---------------------------------------------------------------------------


def test_main_exit_code_zero_on_pass(tmp_path: Path) -> None:
    body = "# README\n\n## ライブで動かす\n\nok\n"
    repo = _make_repo(tmp_path, body)
    from tools.lint.check_examples_readme import main as cli_main
    rc = cli_main(["--repo-root", str(repo)])
    assert rc == 0


def test_main_exit_code_nonzero_on_fail(tmp_path: Path, capsys) -> None:
    body = "# README\n\n## A. headless\n"
    repo = _make_repo(tmp_path, body)
    from tools.lint.check_examples_readme import main as cli_main
    rc = cli_main(["--repo-root", str(repo)])
    assert rc != 0
    err = capsys.readouterr().err
    # stderr にエラー詳細が出ること（受け入れ基準 #3 / #4）
    assert "ライブで動かす" in err or "live" in err.lower()


# ---------------------------------------------------------------------------
# 受け入れ基準 #3 / #4: 実 repo の examples/README.md に対する pin
# ---------------------------------------------------------------------------


def test_live_section_present() -> None:
    """実 repo の `examples/README.md` に「ライブで動かす」見出しが存在する.

    受け入れ基準 #3 / #4 の対応 test 関数（issue #42 受け入れ表）。
    """
    repo_root = Path(__file__).resolve().parents[2]
    # 例外を raise しないこと
    check_examples_readme(repo_root=repo_root)
