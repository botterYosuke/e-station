#!/usr/bin/env python3
"""issue #42 Phase 6 lint: ``examples/README.md`` に「ライブで動かす」を assert.

統一決定 #19 / 受け入れ基準 #3 / #4 の対応 lint。
``examples/README.md`` のいずれかの見出し（``#`` / ``##`` / ``###`` ...）に
「ライブで動かす」または "Live" / "live" を含む見出しが存在することを assert する。
本文中の偶発的な一致（地の文）は検出しない。

呼び出し方:

    # ライブラリとして
    from tools.lint.check_examples_readme import check_examples_readme
    check_examples_readme(repo_root=Path("/path/to/repo"))  # raises on failure

    # CLI として
    python -m tools.lint.check_examples_readme [--repo-root <PATH>]

Exit code:
    0  pass
    1  失敗（``examples/README.md`` 不在 or 見出し未検出）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[2]

# 見出し行の判定。ATX 形式 (`#` / `##` / ...) のみ対応。Setext (===) は使わない。
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")

# 「ライブで動かす」が canonical。柔軟性のため「ライブ」+「動かす」の連続も許容するが
# 過剰な後方互換性は持たせない（見出しでなければマッチしない）。
_LIVE_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ライブで動かす"),
    re.compile(r"ライブ運用"),       # 将来の表記揺れ
    re.compile(r"^Live\b", re.IGNORECASE),  # `## Live` 等の英語見出し
)


class LiveSectionMissingError(RuntimeError):
    """``examples/README.md`` に live section が見つからなかったときに raise."""


def _heading_matches_live(heading_text: str) -> bool:
    """与えられた見出し文字列が live section と判定できるか.

    本文中の地の文での偶発的な一致を防ぐため、呼び出し側は ATX heading 行のみ
    フィードしている前提（``_HEADING_RE`` を pre-filter）.
    """
    return any(p.search(heading_text) for p in _LIVE_HEADING_PATTERNS)


def _iter_headings(markdown: str) -> Iterable[tuple[int, int, str]]:
    """markdown 全文から (1-origin line_no, depth, text) を yield.

    fenced code block 内 (``` ... ```) の `#` は heading として扱わない。
    """
    in_fence = False
    fence_marker = ""
    for lineno, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.lstrip()
        # fence 開閉判定（```` も `~~~` も対応）
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            depth = len(m.group(1))
            text = m.group(2).strip()
            yield lineno, depth, text


def check_examples_readme(*, repo_root: Path) -> None:
    """``<repo_root>/examples/README.md`` の live section 存在を assert.

    Raises:
        LiveSectionMissingError: README が存在しない、または live 見出しがない。
    """
    readme = Path(repo_root) / "examples" / "README.md"
    if not readme.is_file():
        raise LiveSectionMissingError(
            f"examples/README.md not found at {readme!s}. "
            "受け入れ基準 #3 / #4 (issue #42): "
            "「ライブで動かす」セクションを含む README が必要です。"
        )
    text = readme.read_text(encoding="utf-8")
    for lineno, _depth, heading in _iter_headings(text):
        if _heading_matches_live(heading):
            return  # found
        _ = lineno  # 将来 verbose ログ用
    raise LiveSectionMissingError(
        f"{readme!s}: 「ライブで動かす」見出しが見つかりません "
        "(受け入れ基準 #3 / #4)。`## C. ライブで動かす（demo 口座）` のような "
        "ATX 見出し行を追加してください。"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.lint.check_examples_readme",
        description=(
            "issue #42 Phase 6 lint: assert 'ライブで動かす' section in "
            "examples/README.md (受け入れ基準 #3 / #4)."
        ),
    )
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT_DEFAULT,
        help="repo root（既定: スクリプトから 2 階層上）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        check_examples_readme(repo_root=args.repo_root.resolve())
    except LiveSectionMissingError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
