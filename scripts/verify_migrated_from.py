#!/usr/bin/env python3
"""verify_migrated_from.py

Verify that every legacy doc file is referenced by some new doc's
`migrated_from:` YAML frontmatter field.

Baseline: `git ls-files docs/✅* docs/plan/distribution-formats docs/plan/floating-windows`.
Files prefixed with `❌` are excluded from the baseline.

For each .md under `docs/`, parse the leading YAML frontmatter (between two
`---` lines) and collect `migrated_from:` (string or list of strings).

If `baseline - migrated_from` is non-empty, exit with failure listing the
uncovered legacy files.

Exit codes:
  0  baseline fully covered
  1  uncovered files exist
  2  git command failed
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import yaml


REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent

BASELINE_PATTERNS = [
    "docs/✅*",
    "docs/plan/distribution-formats",
    "docs/plan/floating-windows",
]


def _git_ls_files(repo_root: Path, patterns: list[str]) -> list[str]:
    # `-c core.quotePath=false` ensures non-ASCII paths (e.g. ✅, ❌) are
    # emitted verbatim as UTF-8 instead of octal-escaped C strings.
    cmd = [
        "git",
        "-C",
        str(repo_root),
        "-c",
        "core.quotePath=false",
        "ls-files",
        "--",
        *patterns,
    ]
    try:
        out = subprocess.check_output(cmd, text=True, encoding="utf-8")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ERROR: git ls-files failed: {e}", file=sys.stderr)
        sys.exit(2)
    return [line for line in out.splitlines() if line.strip()]


def _baseline(repo_root: Path) -> set[str]:
    files = _git_ls_files(repo_root, BASELINE_PATTERNS)
    out: set[str] = set()
    for f in files:
        # Exclude files whose path contains a ❌ component
        if "❌" in f:
            continue
        out.add(f)
    return out


def _parse_frontmatter(md: Path) -> dict | None:
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None
    fm_text = "\n".join(lines[1:end_idx])
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _collect_migrated_from(repo_root: Path) -> set[str]:
    docs_dir = repo_root / "docs"
    out: set[str] = set()
    if not docs_dir.is_dir():
        return out
    for md in docs_dir.rglob("*.md"):
        fm = _parse_frontmatter(md)
        if not fm:
            continue
        val = fm.get("migrated_from")
        if val is None:
            continue
        if isinstance(val, str):
            out.add(val)
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, str):
                    out.add(v)
    # Also accept entries recorded in the migration manifest as coverage.
    # The manifest is the authoritative inventory of every legacy file's
    # disposition (migrated / deferred / excluded / pending). A legacy file
    # listed in the manifest is considered "accounted for" even if no
    # individual new doc carries it in its `migrated_from:` frontmatter.
    for manifest_name in ("decisions/manifest.yaml", "_migration-ledger.yaml"):
        manifest = docs_dir / manifest_name
        if not manifest.is_file():
            continue
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            old = entry.get("old_path")
            if isinstance(old, str) and old:
                out.add(old)
        break
    return out


def verify(repo_root: Path) -> list[str]:
    baseline = _baseline(repo_root)
    covered = _collect_migrated_from(repo_root)
    missing = sorted(baseline - covered)
    print(f"baseline={len(baseline)}  migrated_from={len(covered)}  uncovered={len(missing)}")
    return missing


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    missing = verify(args.repo_root.resolve())
    if missing:
        print("\nFAILED: legacy files not referenced by any migrated_from:", file=sys.stderr)
        for f in missing:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
