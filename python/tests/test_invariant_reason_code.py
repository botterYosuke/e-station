"""A-H2: reason_code SCREAMING_SNAKE_CASE 不変条件テスト。

spec.md §5.2 に定義された全 reason_code が:
1. SCREAMING_SNAKE_CASE 形式（ASCII 大文字・数字・アンダースコアのみ）であること
2. 実際に server.py / tachibana_orders.py で使われているすべての reason_code が
   spec.md §5.2 の canonical 集合に含まれること
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Canonical list from spec.md §5.2
# ---------------------------------------------------------------------------

CANONICAL_REASON_CODES: frozenset[str] = frozenset(
    {
        "VALIDATION_ERROR",
        "UNSUPPORTED_IN_PHASE_O0",
        "VENUE_UNSUPPORTED",
        "SECOND_PASSWORD_REQUIRED",
        "SECOND_PASSWORD_INVALID",
        "SECOND_PASSWORD_LOCKED",
        "SESSION_EXPIRED",
        "REPLAY_MODE_ACTIVE",
        "RATE_LIMITED",
        "MARKET_CLOSED",
        "INSUFFICIENT_FUNDS",
        "VENUE_REJECTED",
        "ORDER_STATUS_UNKNOWN",
        "INTERNAL_ERROR",
        # TRANSPORT_ERROR: 内部 IPC 用途のみ（spec 外だが Python 内部でのみ使われる）
        "TRANSPORT_ERROR",
        # NOT_LOGGED_IN: server.py で使用（第二暗証番号未保持の別経路）
        "NOT_LOGGED_IN",
        # CONFLICTING_TAGS: spec §4 に記載の VENUE_UNSUPPORTED の sub-code
        "CONFLICTING_TAGS",
        # REPLAY_NOT_IMPLEMENTED: M-7 (R2 review-fix R1a) — venue=="replay" の
        # SubmitOrder を N1.4 で受けた場合に N1.5 未実装を明示する一時 reason_code。
        # N1.5 で wrapper Strategy が実装され次第、本コードは削除する。
        "REPLAY_NOT_IMPLEMENTED",
    }
)

_SCREAMING_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

REPO_ROOT = Path(__file__).parents[2]
PYTHON_ROOT = REPO_ROOT / "python" / "engine"

_FILES_TO_CHECK = [
    PYTHON_ROOT / "server.py",
    PYTHON_ROOT / "exchanges" / "tachibana_orders.py",
]

# ---------------------------------------------------------------------------
# Collect reason_code string literals from source files via AST
# ---------------------------------------------------------------------------


def _collect_reason_codes_from_file(path: Path) -> list[tuple[str, int]]:
    """Return [(reason_code_value, lineno), ...] from string literals that are
    assigned to a key named "reason_code" or passed as reason_code= kwarg."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        # Dict literal: {"reason_code": "VALUE", ...}
        if isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "reason_code"
                    and isinstance(val, ast.Constant)
                    and isinstance(val.value, str)
                ):
                    found.append((val.value, val.lineno))

        # Keyword argument: reason_code="VALUE"
        if isinstance(node, (ast.Call,)):
            for kw in node.keywords:
                if (
                    kw.arg == "reason_code"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    found.append((kw.value.value, kw.value.lineno))

    return found


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_canonical_codes_are_screaming_snake_case():
    """canonical セット内の全コードが SCREAMING_SNAKE_CASE である。"""
    violations = [
        code
        for code in CANONICAL_REASON_CODES
        if not _SCREAMING_SNAKE_RE.match(code)
    ]
    assert not violations, (
        f"Canonical reason_codes violate SCREAMING_SNAKE_CASE: {violations}"
    )


@pytest.mark.parametrize("path", _FILES_TO_CHECK)
def test_all_reason_codes_in_source_are_canonical(path: Path):
    """ソースファイル中の reason_code 文字列値がすべて canonical セットに含まれる。"""
    occurrences = _collect_reason_codes_from_file(path)
    unknown = [
        (code, lineno)
        for code, lineno in occurrences
        if code not in CANONICAL_REASON_CODES
    ]
    assert not unknown, (
        f"{path.name}: unknown reason_code values (not in spec.md §5.2): "
        + ", ".join(f"{code!r} (line {ln})" for code, ln in unknown)
    )


@pytest.mark.parametrize("path", _FILES_TO_CHECK)
def test_all_reason_codes_in_source_are_screaming_snake_case(path: Path):
    """ソースファイル中の reason_code 文字列値がすべて SCREAMING_SNAKE_CASE である。"""
    occurrences = _collect_reason_codes_from_file(path)
    violations = [
        (code, lineno)
        for code, lineno in occurrences
        if not _SCREAMING_SNAKE_RE.match(code)
    ]
    assert not violations, (
        f"{path.name}: reason_codes violating SCREAMING_SNAKE_CASE: "
        + ", ".join(f"{code!r} (line {ln})" for code, ln in violations)
    )
