"""Regression guards for `_do_request_venue_login` credential safety.

Tests for `_do_set_venue_credentials`, `_restore_session_from_payload`, and
`tachibana_run_login` were removed in T-SC3 when those code paths were deleted
(Python now self-initiates login via `startup_login`/`_startup_tachibana`).
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from engine.server import DataEngineServer


def _ast_has_fallback_binding(src: str) -> bool:
    """AST-based detector for any local binding whose target name starts with `fallback_`.

    Walks Assign / AnnAssign / NamedExpr nodes and resolves nested Tuple / List
    targets recursively (catches tuple-unpack, walrus, and annotated forms).
    """
    tree = ast.parse(textwrap.dedent(src))

    def _name_targets(node: ast.AST):
        if isinstance(node, ast.Name):
            yield node
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                yield from _name_targets(elt)
        elif isinstance(node, ast.Starred):
            yield from _name_targets(node.value)

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets.append(node.target)
        elif isinstance(node, ast.NamedExpr):  # walrus
            targets.append(node.target)
        for t in targets:
            for name_node in _name_targets(t):
                if name_node.id.startswith("fallback_"):
                    return True
    return False


def test_request_venue_login_source_has_no_unscrubbed_fallback_locals():
    """MEDIUM-1: `_do_request_venue_login` must not bind `fallback_*` locals
    without a `finally:` scrub. The new file-cache path clears state before
    delegating to `_startup_tachibana`, so no fallback locals exist."""
    import inspect

    src = inspect.getsource(DataEngineServer._do_request_venue_login)
    if _ast_has_fallback_binding(src):
        assert "finally:" in src, (
            "MEDIUM-1 invariant: `_do_request_venue_login` introduced "
            "`fallback_*` locals — it must also scrub them in a `finally:` clause."
        )


def test_ast_fallback_detector_catches_tuple_unpack_walrus_and_annotated_forms():
    """M-R8-5 meta-test: pin the AST detector against three false-negative forms."""
    assert _ast_has_fallback_binding(
        "def f():\n    fallback_user_id, other = (1, 2)\n"
    ), "tuple-unpack `fallback_user_id` must be detected"
    assert _ast_has_fallback_binding(
        "def f():\n    if (fallback_password := compute()):\n        pass\n"
    ), "walrus-bound `fallback_password` must be detected"
    assert _ast_has_fallback_binding(
        "def f():\n    fallback_pw: str\n"
    ), "annotated-without-value `fallback_pw` must be detected"
    assert _ast_has_fallback_binding(
        "def f():\n    fallback_user_id = 'x'\n"
    ), "plain `fallback_user_id =` must be detected"
    assert not _ast_has_fallback_binding(
        "def f():\n    x = 'fallback_user_id'\n    # fallback_password mention\n"
    ), "string/comment mention must NOT trigger detection"
