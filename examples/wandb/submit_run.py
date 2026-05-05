#!/usr/bin/env python3
"""W&B submit script -- called from Flowsurface GUI via subprocess.

Usage:
    uv run --with wandb python examples/wandb/submit_run.py \\
        --run-buffer PATH \\
        [--project NAME] [--run-name NAME] [--tags TAG1,TAG2]

Exit codes:
    0: success
    2: auth error
    3: rate limit
    4: network error
    5: server 5xx
    6: partial failure

Notes:
    - wandb is injected via `uv run --with wandb` (not in pyproject.toml dependencies)
    - WANDB_API_KEY env is inherited from the parent process (Rust GUI)
    - stdout last line is: URL: <wandb_run_url>  (parsed by Rust GUI)
    - stderr receives progress / error messages
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# PII scrub import (examples/wandb/pii_scrub.py)
# ---------------------------------------------------------------------------
# Add examples/wandb to sys.path so pii_scrub is importable when run as
# `python examples/wandb/submit_run.py` from the repo root.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from pii_scrub import (  # noqa: E402
    EQUITY_ALLOWED_KEYS,
    FILLS_ALLOWED_KEYS,
    NARRATIVE_ALLOWED_KEYS,
    assert_no_forbidden_keys,
    pii_scrub,
)

# ---------------------------------------------------------------------------
# Global: active wandb run (for SIGTERM handler)
# ---------------------------------------------------------------------------
_active_run = None


def install_sigterm_handler() -> None:
    """Install SIGTERM handler for graceful wandb.finish."""

    def _handle_sigterm(signum, frame):
        if _active_run is not None:
            try:
                import wandb  # noqa: PLC0415
                wandb.finish(exit_code=1, quiet=True)
            except Exception:
                pass
        sys.exit(1)

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (OSError, ValueError):
        # On Windows, SIGTERM may not be fully supported in all contexts
        pass


# ---------------------------------------------------------------------------
# Lock file utilities (F9b DoD: stale lock handling)
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """Return True if process with *pid* exists."""
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle == 0:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def remove_stale_lock(run_dir: Path) -> None:
    """Remove a .lock file if it is stale (broken JSON / dead PID / > 24h old)."""
    lock_path = run_dir / ".lock"
    if not lock_path.exists():
        return

    # 1. Parse JSON
    try:
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Broken JSON -> force remove
        try:
            lock_path.unlink()
        except OSError:
            pass
        return

    # 2. Check PID
    pid = lock_data.get("pid")
    if pid is None or not _is_pid_alive(pid):
        try:
            lock_path.unlink()
        except OSError:
            pass
        return

    # 3. Check age (> 24h)
    started_at_str = lock_data.get("started_at", "")
    try:
        started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
        age = datetime.now(tz=timezone.utc) - started_at
        if age.total_seconds() > 86400:
            try:
                lock_path.unlink()
            except OSError:
                pass
    except (ValueError, TypeError):
        pass


def _lock_is_live(run_dir: Path) -> bool:
    """Return True if a .lock file exists AND references a live process started < 24h ago.

    Call this AFTER `remove_stale_lock`; any remaining .lock is considered live.
    """
    lock_path = run_dir / ".lock"
    return lock_path.exists()


def _write_lock(run_dir: Path) -> Path:
    """Atomically write a .lock file and return its path.

    Uses tempfile.mkstemp + os.replace so a partial write cannot leave a
    corrupt .lock file behind (mirrors `_write_meta_atomic` pattern).
    """
    lock_path = run_dir / ".lock"
    lock_data = {
        "pid": os.getpid(),
        "started_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    payload = json.dumps(lock_data).encode("utf-8")

    # Create tempfile in the same directory so os.replace is atomic on Windows
    fd, tmp_path = tempfile.mkstemp(prefix=".lock.", dir=str(run_dir))
    tmp_path_obj = Path(tmp_path)
    try:
        try:
            os.write(fd, payload)
            # F9 R1-H6: fsync before close so a crash between write() and the
            # subsequent os.replace cannot leave a zero-byte .lock that masks
            # the real PID. Mirrors `_write_meta_atomic` durability guarantees.
            try:
                os.fsync(fd)
            except OSError as exc:
                # On some filesystems (tmpfs, certain network mounts) fsync is
                # not supported; warn but do not fail the lock acquisition.
                print(f"WARNING: fsync(.lock) failed: {exc}", file=sys.stderr)
        finally:
            os.close(fd)
        os.replace(tmp_path, lock_path)
    except Exception:
        # Cleanup the temp file on failure so it does not masquerade as a lock
        try:
            if tmp_path_obj.exists():
                tmp_path_obj.unlink()
        except OSError:
            pass
        raise
    return lock_path


# ---------------------------------------------------------------------------
# Core submission logic
# ---------------------------------------------------------------------------

def run_submission(
    run_buffer_dir: Path,
    *,
    project: str = "flowsurface-strategies",
    run_name: str = "",
    tags: str = "",
    notes: str = "",
) -> int:
    """
    Read a completed run-buffer directory and upload it to W&B.

    Returns an exit code:
        0: success
        2: auth error
        3: rate limit
        4: network error
        5: server 5xx
        6: partial failure (bad status, missing files, PII violation)
    """
    global _active_run

    run_buffer_dir = Path(run_buffer_dir)

    # ------------------------------------------------------------------
    # 1. Read and validate meta.json
    # ------------------------------------------------------------------
    meta_path = run_buffer_dir / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read meta.json: {exc}", file=sys.stderr)
        return 6

    if meta.get("status") != "completed":
        print(
            f"ERROR: run status is {meta.get('status')!r}, expected 'completed'",
            file=sys.stderr,
        )
        return 6

    # ------------------------------------------------------------------
    # 2. Resolve run name and tags
    # ------------------------------------------------------------------
    run_id = meta.get("run_id", "unknown-run")
    scenario = meta.get("scenario") or {}

    if not run_name:
        strategy_stem = Path(meta.get("strategy_file", "strategy")).stem
        instrument = scenario.get("instrument", "unknown")
        start = scenario.get("start", "")
        end = scenario.get("end", "")
        run_name = f"{strategy_stem} @ {instrument} {start}..{end}"

    tag_list: list[str] = []
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # ------------------------------------------------------------------
    # 3. Reap any stale lock; refuse if a live submit is in progress (H3)
    # ------------------------------------------------------------------
    remove_stale_lock(run_buffer_dir)
    if _lock_is_live(run_buffer_dir):
        print(
            "ERROR: another submit in progress for this run-buffer (live .lock present)",
            file=sys.stderr,
        )
        return 6

    # ------------------------------------------------------------------
    # 4. Import wandb (injected via `uv run --with wandb`) and resolve
    #    canonical exception classes from wandb.errors (H9).
    # ------------------------------------------------------------------
    try:
        import wandb  # noqa: PLC0415
    except ImportError as exc:
        print(f"ERROR: wandb is not installed: {exc}", file=sys.stderr)
        return 4

    try:
        from wandb.errors import (  # noqa: PLC0415
            AuthenticationError,
            CommError,
        )
    except ImportError:
        # R2-H1: fall back through (top-level wandb attrs) → distinct sentinels.
        # We must NOT default both classes to `Exception`; that would make the
        # inner `except AuthenticationError` swallow every error and lose the
        # error-class distinction the exit-code mapping relies on.
        _AuthErr = getattr(wandb, "AuthenticationError", None)
        _CommErr = getattr(wandb, "CommError", None)
        if (
            _AuthErr is None
            or _CommErr is None
            or _AuthErr is _CommErr
            or _AuthErr is Exception
            or _CommErr is Exception
        ):
            # F9 R1-M10: alert the operator that wandb.errors module did not
            # expose discrete classes, so the auth/comm exit-code mapping is
            # degraded (sentinels never match real exceptions, both fall
            # through to the generic OSError/Exception arms with exit=4).
            print(
                "WARNING: wandb.errors not importable; auth/comm exit-code mapping degraded",
                file=sys.stderr,
            )
            AuthenticationError = type("_AuthErrSentinel", (Exception,), {})
            CommError = type("_CommErrSentinel", (Exception,), {})
        else:
            AuthenticationError = _AuthErr
            CommError = _CommErr

    # ------------------------------------------------------------------
    # 5. Write lock atomically
    # ------------------------------------------------------------------
    # R2-C1: declare lock_path before the try so the finally clause can
    # safely reference it even if `_write_lock` itself raises (otherwise
    # the original exception is masked by NameError on `lock_path.unlink`).
    lock_path: Optional[Path] = None

    try:
        lock_path = _write_lock(run_buffer_dir)
        # ------------------------------------------------------------------
        # 6. Install SIGTERM handler
        # ------------------------------------------------------------------
        install_sigterm_handler()

        # ------------------------------------------------------------------
        # 7. Initialise wandb run
        # ------------------------------------------------------------------
        config = dict(scenario)
        config["run_id"] = run_id
        config["strategy_file"] = meta.get("strategy_file", "")
        config["strategy_sha256"] = meta.get("strategy_sha256", "")
        config["git_rev"] = meta.get("git_rev", "")
        config["started_at"] = meta.get("started_at", "")
        config["finished_at"] = meta.get("finished_at", "")

        try:
            init_kwargs: dict = dict(
                project=project,
                name=run_name,
                tags=tag_list if tag_list else None,
                config=config,
            )
            if notes:
                init_kwargs["notes"] = notes
            run = wandb.init(**init_kwargs)
            _active_run = run
        except AuthenticationError as exc:
            print(f"ERROR: auth: {exc}", file=sys.stderr)
            return 2
        except CommError as exc:
            msg = str(exc)
            if "429" in msg:
                print(f"ERROR: rate limit: {exc}", file=sys.stderr)
                return 3
            if any(code in msg for code in ("500", "501", "502", "503", "504", "5xx")):
                print(f"ERROR: server error: {exc}", file=sys.stderr)
                return 5
            print(f"ERROR: comm error: {exc}", file=sys.stderr)
            return 4
        except OSError as exc:
            print(f"ERROR: network error: {exc}", file=sys.stderr)
            return 4

        # ------------------------------------------------------------------
        # 7. Upload equity.jsonl
        # ------------------------------------------------------------------
        equity_path = run_buffer_dir / "equity.jsonl"
        if equity_path.exists():
            with equity_path.open(encoding="utf-8") as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    clean = pii_scrub(evt, EQUITY_ALLOWED_KEYS)
                    try:
                        assert_no_forbidden_keys(clean, EQUITY_ALLOWED_KEYS)
                    except ValueError as exc:
                        print(f"ERROR: PII in equity.jsonl row {i}: {exc}", file=sys.stderr)
                        # F9 R1-H5: ensure wandb.finish() runs so the partial run
                        # is flushed and shows up as exit_code=6 server-side.
                        if _active_run is not None:
                            try:
                                wandb.finish(exit_code=6, quiet=True)
                            except Exception as fexc:
                                print(
                                    f"WARNING: wandb.finish() failed: {fexc}",
                                    file=sys.stderr,
                                )
                        return 6
                    clean["step"] = i
                    wandb.log(clean)

        # ------------------------------------------------------------------
        # 8. Upload fills.jsonl as Artifact
        # ------------------------------------------------------------------
        fills_path = run_buffer_dir / "fills.jsonl"
        if fills_path.exists():
            fills_artifact = wandb.Artifact(f"fills-{run_id}", type="dataset")
            with fills_artifact.new_file("fills.jsonl", mode="w") as af:
                with fills_path.open(encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        clean = pii_scrub(evt, FILLS_ALLOWED_KEYS)
                        try:
                            assert_no_forbidden_keys(clean, FILLS_ALLOWED_KEYS)
                        except ValueError as exc:
                            print(f"ERROR: PII in fills.jsonl row {i}: {exc}", file=sys.stderr)
                            # F9 R1-H5
                            if _active_run is not None:
                                try:
                                    wandb.finish(exit_code=6, quiet=True)
                                except Exception as fexc:
                                    print(
                                        f"WARNING: wandb.finish() failed: {fexc}",
                                        file=sys.stderr,
                                    )
                            return 6
                        af.write(json.dumps(clean) + "\n")
            run.log_artifact(fills_artifact)

        # ------------------------------------------------------------------
        # 9. Upload narrative.jsonl as wandb.Table
        # ------------------------------------------------------------------
        narrative_path = run_buffer_dir / "narrative.jsonl"
        if narrative_path.exists():
            rows = []
            with narrative_path.open(encoding="utf-8") as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    clean = pii_scrub(evt, NARRATIVE_ALLOWED_KEYS)
                    try:
                        assert_no_forbidden_keys(clean, NARRATIVE_ALLOWED_KEYS)
                    except ValueError as exc:
                        print(f"ERROR: PII in narrative.jsonl row {i}: {exc}", file=sys.stderr)
                        # F9 R1-H5
                        if _active_run is not None:
                            try:
                                wandb.finish(exit_code=6, quiet=True)
                            except Exception as fexc:
                                print(
                                    f"WARNING: wandb.finish() failed: {fexc}",
                                    file=sys.stderr,
                                )
                        return 6
                    rows.append(clean)

            if rows:
                columns = list({k for r in rows for k in r.keys()})
                table = wandb.Table(columns=columns)
                for row in rows:
                    table.add_data(*[row.get(c) for c in columns])
                wandb.log({"narrative": table})

        # ------------------------------------------------------------------
        # 11. Finish (M7: log finish() failures to stderr instead of swallowing)
        # ------------------------------------------------------------------
        try:
            wandb.finish()
        except Exception as exc:
            print(f"WARNING: wandb.finish() failed: {exc}", file=sys.stderr)

        url = getattr(run, "url", None) or ""
        print(f"URL: {url}")
        return 0

    except AuthenticationError as exc:
        print(f"ERROR: auth: {exc}", file=sys.stderr)
        # R2-M2: only call wandb.finish() if a run was actually initialised;
        # otherwise wandb has no internal state to flush and the call would
        # spuriously emit warnings (and may itself raise on uninitialised SDK).
        if _active_run is not None:
            try:
                wandb.finish(exit_code=2, quiet=True)
            except Exception as fexc:
                print(f"WARNING: wandb.finish() failed: {fexc}", file=sys.stderr)
        return 2

    except CommError as exc:
        # F9 R2-M9: pass the same exit_code we return so wandb.finish()
        # records a status that matches the Rust caller's interpretation
        # (3=rate-limit / 5=5xx / 4=other). Previously a constant `1` was
        # passed regardless of which CommError sub-class triggered, leaving
        # the W&B-side run status inconsistent with the CLI exit code.
        msg = str(exc)
        if "429" in msg:
            mapped_exit = 3
            err_label = "rate limit"
        elif any(code in msg for code in ("500", "501", "502", "503", "504", "5xx")):
            mapped_exit = 5
            err_label = "server error"
        else:
            mapped_exit = 4
            err_label = "comm error"
        if _active_run is not None:
            try:
                wandb.finish(exit_code=mapped_exit, quiet=True)
            except Exception as fexc:
                print(f"WARNING: wandb.finish() failed: {fexc}", file=sys.stderr)
        print(f"ERROR: {err_label}: {exc}", file=sys.stderr)
        return mapped_exit

    except OSError as exc:
        # F9 R2-M9: align finish() exit_code with the function's return
        # value (4 = network/OS error).
        print(f"ERROR: network error: {exc}", file=sys.stderr)
        if _active_run is not None:
            try:
                wandb.finish(exit_code=4, quiet=True)
            except Exception as fexc:
                print(f"WARNING: wandb.finish() failed: {fexc}", file=sys.stderr)
        return 4

    finally:
        _active_run = None
        # Remove lock file (R2-C1: guard against `_write_lock` failure where
        # `lock_path` is still None; otherwise NameError-equivalent would
        # mask the original exception that aborted the try-block).
        if lock_path is not None:
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _configure_cli_logging() -> None:
    """F9 R2-M8: Configure stdlib logging only when invoked as a CLI script.

    Previously this lived inline at the top of ``main()`` and called
    ``logging.basicConfig(force=True)``, which silently stomped the logging
    configuration of any process that imported and called ``main()`` (most
    notably pytest, which then loses its own log capture). Moving the
    side-effecting call into a helper that is fired only under
    ``if __name__ == "__main__":`` makes the side effect explicit and
    confined to the CLI entry path.

    Routes WARNING and above to ``sys.stderr`` so pii_scrub's
    "forbidden keys detected" warnings reach the parent process / CI logs;
    ``stdout`` is reserved for the final ``URL: <wandb_run_url>`` line that
    the Rust GUI parses.
    """
    import logging
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(message)s",
        force=True,
    )


def main() -> None:
    # F9 R2-M8: logging configuration is no longer performed here. Callers
    # that import `main()` (e.g. tests) inherit the parent process's logging
    # configuration. The CLI entry path below invokes
    # `_configure_cli_logging()` to set up the WARNING -> stderr routing.

    parser = argparse.ArgumentParser(
        description="Upload a Flowsurface replay RunBuffer to Weights & Biases."
    )
    parser.add_argument(
        "--run-buffer",
        required=True,
        type=Path,
        help="Path to the run-buffer directory (contains meta.json / fills.jsonl / equity.jsonl / narrative.jsonl)",
    )
    parser.add_argument(
        "--project",
        default="flowsurface-strategies",
        help="W&B project name (default: flowsurface-strategies)",
    )
    parser.add_argument(
        "--run-name",
        default="",
        help="W&B run name (default: auto-generated from meta.json)",
    )
    parser.add_argument(
        "--tags",
        default="",
        help="Comma-separated list of tags to attach to the W&B run",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Free-form notes forwarded to wandb.init(notes=...) (M6).",
    )
    args = parser.parse_args()

    exit_code = run_submission(
        run_buffer_dir=args.run_buffer,
        project=args.project,
        run_name=args.run_name,
        tags=args.tags,
        notes=args.notes,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    _configure_cli_logging()
    main()
