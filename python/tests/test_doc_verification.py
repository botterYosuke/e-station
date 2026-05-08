"""Tests for the doc-restructure verification scripts.

Each test uses synthetic on-disk fixtures to invoke a script's `main()` with
`--repo-root` pointed at a temporary directory.

These exercise the script logic only; they do not depend on the real repo
state (the migration is in flight).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load(name: str):
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Each script must respond to --help with exit 0
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "script",
    [
        "verify_migration_manifest",
        "verify_migrated_from",
        "verify_no_legacy_paths",
        "verify_nav_depth",
        "check_adr_status",
    ],
)
def test_help_exits_zero(script: str) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / f"{script}.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()


# ---------------------------------------------------------------------------
# verify_migration_manifest
# ---------------------------------------------------------------------------

def test_verify_migration_manifest_happy(tmp_path: Path) -> None:
    mod = _load("verify_migration_manifest")
    (tmp_path / "docs").mkdir()
    new_md = tmp_path / "docs" / "specs" / "data-engine.md"
    new_md.parent.mkdir(parents=True)
    new_md.write_text("# Data Engine\n\n## Replay\n\nbody\n", encoding="utf-8")

    ledger = tmp_path / "docs" / "_migration-ledger.yaml"
    ledger.write_text(
        "- old_path: docs/✅python-data-engine/spec.md\n"
        "  new_path: docs/specs/data-engine.md\n"
        "  new_anchor: Replay\n"
        "  status: migrated\n"
        "- old_path: docs/✅somewhere/skipme.md\n"
        "  status: deferred\n",
        encoding="utf-8",
    )
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 0


def test_verify_migration_manifest_missing_new_path(tmp_path: Path) -> None:
    mod = _load("verify_migration_manifest")
    (tmp_path / "docs").mkdir()
    ledger = tmp_path / "docs" / "_migration-ledger.yaml"
    ledger.write_text(
        "- old_path: docs/✅x/foo.md\n"
        "  new_path: docs/specs/missing.md\n"
        "  status: migrated\n",
        encoding="utf-8",
    )
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# verify_migrated_from
# ---------------------------------------------------------------------------

def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "core.quotepath", "off"], cwd=repo, check=True
    )


def test_verify_migrated_from_happy(tmp_path: Path) -> None:
    mod = _load("verify_migrated_from")
    _git_init(tmp_path)
    legacy = tmp_path / "docs" / "✅mod" / "spec.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy body\n", encoding="utf-8")
    new = tmp_path / "docs" / "specs" / "mod.md"
    new.parent.mkdir(parents=True)
    new.write_text(
        "---\nmigrated_from: docs/✅mod/spec.md\n---\n\n# new\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True
    )
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# verify_no_legacy_paths
# ---------------------------------------------------------------------------

def test_verify_no_legacy_paths_clean(tmp_path: Path) -> None:
    mod = _load("verify_no_legacy_paths")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ok.md").write_text("nothing legacy here\n", encoding="utf-8")
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 0


def test_verify_no_legacy_paths_detects(tmp_path: Path) -> None:
    mod = _load("verify_no_legacy_paths")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "bad.md").write_text(
        "see docs/✅python-data-engine/spec.md\n", encoding="utf-8"
    )
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# verify_nav_depth
# ---------------------------------------------------------------------------

def test_verify_nav_depth_ok(tmp_path: Path) -> None:
    mod = _load("verify_nav_depth")
    (tmp_path / "mkdocs.yml").write_text(
        "site_name: t\n"
        "nav:\n"
        "  - A:\n"
        "    - B:\n"
        "      - C:\n"
        "        - D: file.md\n",
        encoding="utf-8",
    )
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 0


def test_verify_nav_depth_too_deep(tmp_path: Path) -> None:
    mod = _load("verify_nav_depth")
    (tmp_path / "mkdocs.yml").write_text(
        "site_name: t\n"
        "nav:\n"
        "  - A:\n"
        "    - B:\n"
        "      - C:\n"
        "        - D:\n"
        "          - E:\n"
        "            - F: file.md\n",
        encoding="utf-8",
    )
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# check_adr_status
# ---------------------------------------------------------------------------

def test_check_adr_status_happy(tmp_path: Path) -> None:
    mod = _load("check_adr_status")
    adr = tmp_path / "docs" / "decisions"
    adr.mkdir(parents=True)
    (adr / "0001-base.md").write_text(
        "---\nstatus: superseded\n---\n\n# base\n", encoding="utf-8"
    )
    (adr / "0002-accepted.md").write_text(
        "---\nstatus: accepted\nsource_commit: abc123\nsupersedes: 0001\n---\n\n# acc\n",
        encoding="utf-8",
    )
    (adr / "0003-deferred.md").write_text(
        "---\nstatus: deferred\n---\n\n# deferred\n",
        encoding="utf-8",
    )
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 0


def test_check_adr_status_accepted_missing_commit(tmp_path: Path) -> None:
    mod = _load("check_adr_status")
    adr = tmp_path / "docs" / "decisions"
    adr.mkdir(parents=True)
    (adr / "0001-x.md").write_text(
        "---\nstatus: accepted\n---\n\n# x\n", encoding="utf-8"
    )
    rc = mod.main(["--repo-root", str(tmp_path)])
    assert rc == 1
