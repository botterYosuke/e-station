#!/usr/bin/env python3
"""verify_no_legacy_paths.py

Grep the repository for stale references to legacy doc paths.

Targets (relative to repo root):
  README.md AGENTS.md CLAUDE.md docs/ examples/ python/ scripts/ tests/

Excluded:
  .git/ site/ target/
  docs/_migration-ledger.yaml
  docs/decisions/manifest.yaml
  this script itself (self-reference)

Patterns flagged:
  - literal `docs/✅`
  - any path-looking string containing the chars [✅✓✔]
  - legacy module path string prefixes (with checkmark): ✅python-data-engine/,
    ✅tachibana/, ✅kabusapi/, ✅order/, ✅nautilus_trader/, ✅menu-and-footer/
  - GitHub Pages URL-encoded form `%E2%9C%85`

Exit codes:
  0  no matches
  1  matches found
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent

TARGETS = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs",
    "examples",
    "python",
    "scripts",
    "tests",
]

EXCLUDE_DIRS = {".git", "site", "target", "node_modules", "__pycache__", ".venv", "venv"}
EXCLUDE_FILES_REL = {
    "docs/_migration-ledger.yaml",
    "docs/decisions/manifest.yaml",
    "docs/decisions/MANIFEST.md",
    "scripts/verify_no_legacy_paths.py",
    # Tooling that intentionally references legacy paths (baseline grep targets,
    # test fixtures for the migration verifier itself).
    "scripts/verify_migrated_from.py",
    "python/tests/test_doc_verification.py",
}
# Paths whose content intentionally references the legacy structure
# (planning records, archive). Exclude to avoid noisy false positives.
EXCLUDE_PREFIXES = (
    "docs/plan/doc-restructure/",
    "docs/plan/❌archive/",
)

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("docs/checkmark prefix", re.compile(r"docs/✅")),
    (
        "legacy module path prefix",
        re.compile(
            r"✅(?:python-data-engine|tachibana|kabusapi|order|nautilus_trader|menu-and-footer)/"
        ),
    ),
    ("URL-encoded checkmark", re.compile(r"%E2%9C%85")),
    # path-looking string with checkmark char (✅✓✔). A 'path-looking'
    # heuristic: contains '/' adjacent and at least one of those chars.
    ("checkmark in path", re.compile(r"[A-Za-z0-9_./\-]*[✅✓✔][A-Za-z0-9_./\-]*/")),
]


def _iter_files(repo_root: Path) -> Iterable[Path]:
    for target in TARGETS:
        base = repo_root / target
        if not base.exists():
            continue
        if base.is_file():
            yield base
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            parts = set(p.relative_to(repo_root).parts)
            if parts & EXCLUDE_DIRS:
                continue
            yield p


def _is_text(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
        ".zip", ".gz", ".tar", ".whl", ".so", ".dll", ".exe", ".dylib",
        ".pyc", ".pyo", ".bin", ".woff", ".woff2", ".ttf", ".otf",
        ".mp4", ".mov", ".mp3", ".wav",
    }:
        return False
    return True


def scan(repo_root: Path) -> list[tuple[str, int, str, str]]:
    hits: list[tuple[str, int, str, str]] = []
    for path in _iter_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        if rel in EXCLUDE_FILES_REL:
            continue
        if any(rel.startswith(prefix) for prefix in EXCLUDE_PREFIXES):
            continue
        if not _is_text(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        in_frontmatter = False
        in_migrated_from_block = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            # Track YAML frontmatter and skip migrated_from: list entries
            # (legitimate historical references).
            stripped = line.strip()
            if lineno == 1 and stripped == "---":
                in_frontmatter = True
                continue
            if in_frontmatter and stripped == "---":
                in_frontmatter = False
                in_migrated_from_block = False
                continue
            if in_frontmatter:
                if line.startswith("migrated_from:"):
                    in_migrated_from_block = True
                    continue
                if in_migrated_from_block:
                    # YAML list items start with "  -" (indented dash) under
                    # migrated_from. The block ends when we hit a non-list,
                    # non-indented-list line (i.e. a new top-level key).
                    if line.startswith("  -") or line.startswith("    "):
                        continue
                    in_migrated_from_block = False
            for label, pat in PATTERNS:
                if pat.search(line):
                    hits.append((rel, lineno, label, line.strip()[:200]))
                    break
    return hits


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    hits = scan(args.repo_root.resolve())
    if hits:
        print(f"FAILED: {len(hits)} legacy reference(s) found", file=sys.stderr)
        for rel, lineno, label, snippet in hits:
            print(f"  {rel}:{lineno} [{label}] {snippet}", file=sys.stderr)
        return 1
    print("OK: no legacy doc path references")
    return 0


if __name__ == "__main__":
    sys.exit(main())
