"""Lint test: ensure no real demo credentials are hardcoded in repo source.

Hardcoded `DEV_TACHIBANA_*` values are forbidden in:
  - scripts/*.py
  - python/engine/exchanges/*.py

Tokens to check are loaded from the repo-root `.env` file at test time so that
real credentials never appear in the source tree. If `.env` is absent or the
relevant keys are empty the test becomes a no-op (nothing to guard against).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_dotenv_values(path: Path) -> dict[str, str]:
    """Minimal .env parser — handles KEY=VALUE and KEY="VALUE" lines."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip().strip('"').strip("'")
        if raw:
            values[key] = raw
    return values


def _forbidden_tokens() -> tuple[str, ...]:
    env = _load_dotenv_values(REPO_ROOT / ".env")
    tokens = []
    for key in ("DEV_TACHIBANA_USER_ID", "DEV_TACHIBANA_PASSWORD"):
        val = env.get(key, "").strip()
        if val:
            tokens.append(val)
    return tuple(tokens)


_FORBIDDEN_TOKENS: tuple[str, ...] = _forbidden_tokens()

_TARGET_DIRS: tuple[Path, ...] = (
    REPO_ROOT / "scripts",
    REPO_ROOT / "python" / "engine" / "exchanges",
)


def test_no_hardcoded_demo_credentials() -> None:
    if not _FORBIDDEN_TOKENS:
        import pytest
        pytest.skip(
            "No DEV_TACHIBANA_* credentials found in .env — nothing to guard against. "
            "Set DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD in repo-root .env to enable."
        )
    offenders: list[tuple[Path, str]] = []
    for base in _TARGET_DIRS:
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = py.read_text(encoding="utf-8", errors="replace")
            for tok in _FORBIDDEN_TOKENS:
                if tok in text:
                    offenders.append((py, tok))
    assert not offenders, (
        "Hardcoded DEV_TACHIBANA_* credentials detected — replace with placeholders "
        f"like `DEV_TACHIBANA_USER_ID=...`. Offenders: {offenders}"
    )
