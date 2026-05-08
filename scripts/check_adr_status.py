#!/usr/bin/env python3
"""check_adr_status.py

Validate `docs/decisions/*.md` ADR status invariants.

Rules:
  * `status: deferred` ADRs must have an effectively empty body
    (frontmatter excluded; remaining text < 200 characters).
  * `status: accepted` ADRs must have a `source_commit:` frontmatter field.
  * If an ADR has `supersedes: NNNN`, the referenced ADR file (matching the
    `NNNN-*.md` prefix) must have `status: superseded`.

Excluded files: `README.md`, `MANIFEST.md`.

Exit codes:
  0  all ADRs valid
  1  validation failure
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

import yaml


REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
ADR_FILENAME_RE = re.compile(r"^(\d{4})-")


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines()
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, text
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None, body
    if not isinstance(data, dict):
        data = None
    return data, body


def _adr_number(path: Path) -> str | None:
    m = ADR_FILENAME_RE.match(path.name)
    return m.group(1) if m else None


def _find_by_number(adr_dir: Path, number: str) -> Path | None:
    matches = list(adr_dir.glob(f"{number}-*.md"))
    return matches[0] if matches else None


def verify(repo_root: Path) -> list[str]:
    adr_dir = repo_root / "docs" / "decisions"
    errors: list[str] = []
    if not adr_dir.is_dir():
        # Nothing to check yet — treat as success but warn.
        print(f"NOTE: {adr_dir} does not exist; skipping")
        return errors

    files = [
        p for p in sorted(adr_dir.glob("*.md"))
        if p.name not in ("README.md", "MANIFEST.md")
    ]

    # Pre-load all ADR statuses for cross-references.
    statuses: dict[str, str] = {}
    parsed: dict[Path, tuple[dict, str]] = {}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            errors.append(f"{p.name}: cannot read ({e})")
            continue
        fm, body = _split_frontmatter(text)
        if fm is None:
            errors.append(f"{p.name}: missing or invalid YAML frontmatter")
            continue
        parsed[p] = (fm, body)
        num = _adr_number(p)
        if num:
            statuses[num] = str(fm.get("status", "")).strip()

    for p, (fm, body) in parsed.items():
        status = str(fm.get("status", "")).strip()
        # 1. deferred: body must be effectively empty (< 200 chars).
        # Strip HTML comments (<!-- ... -->) before counting — these are
        # commonly used to leave scaffold/typing notes inside otherwise
        # placeholder ADRs.
        if status == "deferred":
            without_comments = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
            stripped = without_comments.strip()
            if len(stripped) >= 200:
                errors.append(
                    f"{p.name}: status=deferred but body has {len(stripped)} chars (>=200)"
                )
        # 2. accepted: must have source_commit
        if status == "accepted":
            if not fm.get("source_commit"):
                errors.append(f"{p.name}: status=accepted requires source_commit field")
        # 3. supersedes: referenced ADR must be status=superseded
        sup = fm.get("supersedes")
        if sup is not None:
            sup_nums: list[str] = []
            if isinstance(sup, (int, str)):
                m = re.search(r"(\d{4})", str(sup))
                if m:
                    sup_nums.append(m.group(1))
            elif isinstance(sup, list):
                for item in sup:
                    m = re.search(r"(\d{4})", str(item))
                    if m:
                        sup_nums.append(m.group(1))
            for num in sup_nums:
                target_status = statuses.get(num)
                if target_status is None:
                    errors.append(
                        f"{p.name}: supersedes {num} but no ADR file matches"
                    )
                elif target_status != "superseded":
                    errors.append(
                        f"{p.name}: supersedes {num} but its status is "
                        f"{target_status!r} (expected 'superseded')"
                    )

    print(f"checked {len(parsed)} ADR file(s)")
    return errors


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
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
