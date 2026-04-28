"""D3-5: CI guard for the order invariant-tests.md table integrity.

Three checks:
1. Every row marked ✅ must have a non-TBD test file path and function name.
2. For ✅ rows, the listed test file must exist in the repo.
3. For ✅ rows, every listed function name must appear in that test file.
4. Every invariant ID must be unique.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
INVARIANT_TABLE = REPO_ROOT / "docs" / "plan" / "✅order" / "invariant-tests.md"

# ---------------------------------------------------------------------------
# Table parser
# ---------------------------------------------------------------------------


def _parse_table(table_path: Path) -> list[dict[str, str]]:
    """Parse the main Markdown table from invariant-tests.md.

    Returns a list of dicts with keys: id, description, test_file, test_fn, status
    """
    if not table_path.exists():
        return []

    rows: list[dict[str, str]] = []
    in_table = False

    for line in table_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()

        if not in_table:
            if stripped.startswith("|") and "不変条件" in stripped:
                in_table = True
                continue
            continue

        if not stripped.startswith("|"):
            in_table = False
            continue

        if re.match(r"^\|[\s\-:|]+\|", stripped):
            continue

        cells = [c.strip() for c in stripped.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]

        if len(cells) < 2:
            continue

        rows.append(
            {
                "id": cells[0] if len(cells) > 0 else "",
                "description": cells[1] if len(cells) > 1 else "",
                "test_file": cells[2] if len(cells) > 2 else "",
                "test_fn": cells[3] if len(cells) > 3 else "",
                "status": cells[4] if len(cells) > 4 else "",
            }
        )

    return rows


_TBD_RE = re.compile(r"\bTBD\b", re.IGNORECASE)
_COMPLETED_RE = re.compile(r"✅")


def _is_tbd(value: str) -> bool:
    return not value.strip() or bool(_TBD_RE.search(value))


def _is_completed(status: str) -> bool:
    return bool(_COMPLETED_RE.search(status))


def _fn_names(test_fn: str) -> list[str]:
    """Split slash-separated function names, stripping backticks."""
    return [
        part.strip().strip("`")
        for part in test_fn.split("/")
        if part.strip().strip("`")
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_invariant_table_file_exists() -> None:
    assert INVARIANT_TABLE.exists(), f"invariant-tests.md not found at {INVARIANT_TABLE}"


def test_invariant_table_has_rows() -> None:
    rows = _parse_table(INVARIANT_TABLE)
    assert len(rows) > 0, "invariant-tests.md table appears empty or could not be parsed"


def test_completed_invariants_have_non_tbd_fields() -> None:
    rows = _parse_table(INVARIANT_TABLE)
    violations: list[str] = []

    for row in rows:
        inv_id = row["id"]
        if not inv_id or not _is_completed(row["status"]):
            continue
        if _is_tbd(row["test_file"]):
            violations.append(f"  {inv_id}: marked ✅ but test_file is TBD/empty")
        if _is_tbd(row["test_fn"]):
            violations.append(f"  {inv_id}: marked ✅ but test_fn is TBD/empty")

    if violations:
        raise AssertionError(
            "Completed (✅) invariants with missing test metadata:\n"
            + "\n".join(violations)
            + "\n\nUpdate invariant-tests.md to fill in test_file and test_fn."
        )


def test_completed_invariant_test_files_exist() -> None:
    rows = _parse_table(INVARIANT_TABLE)
    missing: list[str] = []

    for row in rows:
        inv_id = row["id"]
        if not inv_id or not _is_completed(row["status"]):
            continue
        test_file = row["test_file"].strip().strip("`")
        if _is_tbd(test_file):
            continue
        path = REPO_ROOT / test_file
        if not path.exists():
            missing.append(f"  {inv_id}: {test_file} does not exist")

    if missing:
        raise AssertionError(
            "Completed (✅) invariants reference test files that do not exist:\n"
            + "\n".join(missing)
        )


def test_completed_invariant_test_functions_exist_in_file() -> None:
    rows = _parse_table(INVARIANT_TABLE)
    missing: list[str] = []

    for row in rows:
        inv_id = row["id"]
        if not inv_id or not _is_completed(row["status"]):
            continue
        test_file = row["test_file"].strip().strip("`")
        if _is_tbd(test_file) or _is_tbd(row["test_fn"]):
            continue

        path = REPO_ROOT / test_file
        if not path.exists():
            continue

        content = path.read_text(encoding="utf-8", errors="replace")
        for fn_name in _fn_names(row["test_fn"]):
            if fn_name not in content:
                missing.append(f"  {inv_id}: function `{fn_name}` not found in {test_file}")

    if missing:
        raise AssertionError(
            "Completed (✅) invariants list test functions not found in their test files:\n"
            + "\n".join(missing)
            + "\n\nUpdate invariant-tests.md with the correct function names."
        )


def test_invariant_table_ids_are_unique() -> None:
    rows = _parse_table(INVARIANT_TABLE)
    seen: dict[str, int] = {}
    duplicates: list[str] = []

    for i, row in enumerate(rows):
        inv_id = row["id"]
        if not inv_id:
            continue
        if inv_id in seen:
            duplicates.append(
                f"  {inv_id}: first at row {seen[inv_id] + 1}, duplicate at row {i + 1}"
            )
        else:
            seen[inv_id] = i

    if duplicates:
        raise AssertionError(
            "Duplicate invariant IDs in invariant-tests.md:\n" + "\n".join(duplicates)
        )
