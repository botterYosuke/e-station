"""R8-D1: CI guard for the invariant-tests.md table integrity.

Two checks:
1. Every row in the table whose Tx column is marked [x] (completed) must
   have a non-empty, non-TBD test function name in the "pin する test" column.
2. Every ID in the table must be unique (no duplicate 不変条件 ID entries).

This file does NOT assert that every ID referenced in planning documents has a
table entry — that's a looser "convergence target" tracked in the
"未対応 ID" section of invariant-tests.md and is enforced by the PR author, not
by this automated check.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parents[2]
INVARIANT_TABLE = REPO_ROOT / "docs" / "plan" / "✅tachibana" / "invariant-tests.md"

# ---------------------------------------------------------------------------
# Table parser
# ---------------------------------------------------------------------------


def _parse_table(table_path: Path) -> list[dict[str, str]]:
    """Parse the main Markdown table from invariant-tests.md.

    Returns a list of dicts with keys:
        id, primary, test_fn, cmd, tx, skill
    """
    if not table_path.exists():
        return []

    rows: list[dict[str, str]] = []
    in_table = False
    header_seen = False

    for line in table_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()

        if not in_table:
            if stripped.startswith("|") and "不変条件" in stripped:
                in_table = True
                header_seen = True
                continue
            continue

        if not stripped.startswith("|"):
            in_table = False
            continue

        # Skip separator rows (e.g. | :--- | :--- | ... |)
        if re.match(r"^\|[\s\-:|]+\|", stripped):
            continue

        cells = [c.strip() for c in stripped.split("|")]
        # Remove empty first/last due to leading/trailing |
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]

        if len(cells) < 2:
            continue

        rows.append(
            {
                "id": cells[0] if len(cells) > 0 else "",
                "primary": cells[1] if len(cells) > 1 else "",
                "test_fn": cells[2] if len(cells) > 2 else "",
                "cmd": cells[3] if len(cells) > 3 else "",
                "tx": cells[4] if len(cells) > 4 else "",
                "skill": cells[5] if len(cells) > 5 else "",
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TBD_RE = re.compile(r"\bTBD\b", re.IGNORECASE)
_COMPLETED_TX_RE = re.compile(r"\[x\]", re.IGNORECASE)


def _is_test_fn_missing(test_fn: str) -> bool:
    """Return True if the test function name is empty or a TBD placeholder."""
    cleaned = test_fn.strip()
    return not cleaned or bool(_TBD_RE.search(cleaned))


def _tx_is_completed(tx: str) -> bool:
    """Return True if the Tx column marks the task as completed ([x])."""
    return bool(_COMPLETED_TX_RE.search(tx))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_invariant_table_file_exists() -> None:
    assert INVARIANT_TABLE.exists(), (
        f"invariant-tests.md not found at {INVARIANT_TABLE}"
    )


def test_invariant_table_has_rows() -> None:
    rows = _parse_table(INVARIANT_TABLE)
    assert len(rows) > 0, (
        "invariant-tests.md table appears empty or could not be parsed"
    )


def test_completed_invariants_have_test_function_names() -> None:
    """Every completed invariant (Tx=[x]) must have a non-TBD test function name."""
    rows = _parse_table(INVARIANT_TABLE)
    violations: list[str] = []

    for row in rows:
        inv_id = row["id"]
        if not inv_id:
            continue
        if _tx_is_completed(row["tx"]) and _is_test_fn_missing(row["test_fn"]):
            violations.append(
                f"  {inv_id}: Tx marked [x] but test_fn is empty/TBD"
            )

    if violations:
        raise AssertionError(
            "The following invariant IDs are marked complete ([x]) but lack a "
            "test function name in invariant-tests.md:\n"
            + "\n".join(violations)
            + "\n\nUpdate invariant-tests.md to add the test function name, or "
            "un-mark the Tx column as incomplete."
        )


def test_invariant_table_ids_are_unique() -> None:
    """No two rows in the table should share the same 不変条件 ID."""
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
            "Duplicate invariant IDs found in invariant-tests.md:\n"
            + "\n".join(duplicates)
        )
