#!/usr/bin/env python3
"""verify_migration_manifest.py

Verify the doc-restructure migration manifest.

Reads `docs/_migration-ledger.yaml` (in-flight) or `docs/decisions/manifest.yaml`
(post-migration). If both exist, the latter takes precedence. If neither exists,
this exits with an error.

For each entry (excluding `status: deferred` and `status: excluded`):
  * `new_path` must exist on disk.
  * If `new_anchor` is set, the new file must contain a `## ` heading
    matching the anchor (markdown slug-ish loose match).
  * If `status: migrated`, `old_path` must NOT exist (it should have been removed).

`MANIFEST.md` is intentionally not parsed — it is a human-facing index.

Exit codes:
  0  all checks pass
  1  validation failure
  2  manifest not found / unreadable
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

import yaml


REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent


def _slugify_heading(text: str) -> str:
    """Loose markdown slug — lowercase, spaces->hyphens, drop punctuation."""
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text)
    return text.strip("-")


def _heading_present(md_path: Path, anchor: str) -> bool:
    anchor_norm = anchor.lstrip("#").strip().lower()
    target_slug = _slugify_heading(anchor_norm)
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        if not line.startswith("## "):
            continue
        heading = line[3:].strip()
        if heading.lower() == anchor_norm or _slugify_heading(heading) == target_slug:
            return True
    return False


def _load_manifest(repo_root: Path) -> tuple[Path, list[dict]]:
    legacy = repo_root / "docs" / "_migration-ledger.yaml"
    final = repo_root / "docs" / "decisions" / "manifest.yaml"
    chosen: Path | None
    if final.is_file():
        chosen = final
    elif legacy.is_file():
        chosen = legacy
    else:
        print(
            f"ERROR: manifest not found. Looked at:\n  {legacy}\n  {final}",
            file=sys.stderr,
        )
        sys.exit(2)
    raw = yaml.safe_load(chosen.read_text(encoding="utf-8"))
    if raw is None:
        return chosen, []
    if not isinstance(raw, list):
        print(f"ERROR: {chosen} top-level must be a list", file=sys.stderr)
        sys.exit(2)
    return chosen, raw


def verify(repo_root: Path) -> list[str]:
    chosen, entries = _load_manifest(repo_root)
    errors: list[str] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"[{idx}] not a mapping: {entry!r}")
            continue
        status = entry.get("status", "pending")
        if status in ("deferred", "excluded"):
            continue
        new_path = entry.get("new_path")
        if not new_path:
            errors.append(f"[{idx}] missing new_path (status={status})")
            continue
        # Virtual destinations (e.g. GITHUB_WIKI:<page>) are not filesystem
        # paths — they refer to content moved out of this repo (the GitHub
        # Wiki). Skip the on-disk existence check for these.
        if new_path.startswith("GITHUB_WIKI:"):
            continue
        new_abs = repo_root / new_path
        if not new_abs.exists():
            errors.append(f"[{idx}] new_path missing: {new_path}")
            continue
        anchor = entry.get("new_anchor")
        if anchor and not _heading_present(new_abs, str(anchor)):
            errors.append(
                f"[{idx}] anchor {anchor!r} not found as ## heading in {new_path}"
            )
        if status == "migrated":
            old_path = entry.get("old_path")
            if old_path and (repo_root / old_path).exists():
                errors.append(
                    f"[{idx}] status=migrated but old_path still exists: {old_path}"
                )
    print(f"Manifest: {chosen.relative_to(repo_root)}  entries={len(entries)}")
    return errors


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT_DEFAULT,
        help="Repository root (default: parent of scripts/)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    errors = verify(args.repo_root.resolve())
    if errors:
        print(f"\nFAILED: {len(errors)} issue(s)", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
