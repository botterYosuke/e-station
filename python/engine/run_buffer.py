"""engine.run_buffer -- RunBuffer writer class."""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional

from engine.pii_scrub import (
    EQUITY_ALLOWED_KEYS,
    FILLS_ALLOWED_KEYS,
    NARRATIVE_ALLOWED_KEYS,
    pii_scrub,
)

log = logging.getLogger(__name__)

_FILLS_EVENTS: frozenset = frozenset({"ExecutionMarker"})
_EQUITY_EVENTS: frozenset = frozenset({"ReplayBuyingPower"})
_NARRATIVE_EVENTS: frozenset = frozenset({"StrategySignal", "NarrativeWritten"})

# C1: SIGTERM handler chain. Globals at module scope so multiple RunBuffers
# share a single signal.signal() registration; previous handler is preserved
# and chained when our handler runs.
_PREVIOUS_SIGTERM_HANDLER = None
_SIGTERM_HANDLER_INSTALLED = False
# Active (not yet finished/aborted) RunBuffer instances. Used by the SIGTERM
# handler and atexit fallback to flush them out as "aborted" on abnormal
# termination. Weak so the set does not keep buffers alive.
_ACTIVE_BUFFERS: "weakref.WeakSet[RunBuffer]" = weakref.WeakSet()


def get_run_buffer_base_dir() -> Path:
    """Return OS-specific run-buffer base directory."""
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / "flowsurface" / "run-buffer"
        return Path.home() / "AppData" / "Roaming" / "flowsurface" / "run-buffer"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "flowsurface" / "run-buffer"
    else:
        return Path.home() / ".local" / "share" / "flowsurface" / "run-buffer"


def make_run_id(strategy_file: str, instrument: str) -> str:
    utc_sec = int(datetime.now(tz=timezone.utc).timestamp())
    stem = Path(strategy_file).stem
    instrument_clean = instrument.replace(".", "_")
    return f"{utc_sec}-{stem}-{instrument_clean}"


def _get_git_rev() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            rev = result.stdout.strip()
            return rev if rev else "unknown"
    except Exception:
        pass
    return "unknown"


def _sha256_file(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return "unknown"


def _write_meta_atomic(meta_path: Path, meta: dict) -> None:
    """Atomically write meta.json.

    M11: On Windows, os.replace can raise PermissionError if another process
    has the destination open for read. Retry up to 3 times with 50ms backoff.
    """
    parent = meta_path.parent
    fd, tmp_path_str = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        encoded = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    last_exc: Optional[BaseException] = None
    for attempt in range(3):
        try:
            os.replace(tmp_path_str, str(meta_path))
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.05)
                continue
        except Exception:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise
    # All 3 attempts failed with PermissionError.
    try:
        os.unlink(tmp_path_str)
    except OSError:
        pass
    assert last_exc is not None
    raise last_exc


def _install_sigterm_handler_once() -> None:
    """C1: Install a process-wide SIGTERM handler that aborts active RunBuffers.

    Posix only; Windows has no SIGTERM equivalent. Previous handler is
    preserved and chained.
    """
    global _PREVIOUS_SIGTERM_HANDLER, _SIGTERM_HANDLER_INSTALLED
    if _SIGTERM_HANDLER_INSTALLED:
        return
    if sys.platform == "win32":
        # Windows: SIGTERM is not delivered for graceful shutdown; rely on
        # atexit only.
        _SIGTERM_HANDLER_INSTALLED = True
        return
    try:
        _PREVIOUS_SIGTERM_HANDLER = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _sigterm_handler_global)
        _SIGTERM_HANDLER_INSTALLED = True
    except (ValueError, OSError) as exc:
        # signal.signal raises ValueError if not in main thread.
        log.debug("RunBuffer: SIGTERM handler not installed: %s", exc)


def _sigterm_handler_global(signum, frame) -> None:
    """SIGTERM handler: abort all active RunBuffers, then chain previous."""
    for rb in list(_ACTIVE_BUFFERS):
        try:
            rb.abort()
        except Exception as exc:
            log.warning("RunBuffer: SIGTERM abort failed: %s", exc)

    prev = _PREVIOUS_SIGTERM_HANDLER
    if callable(prev):
        try:
            prev(signum, frame)
            return
        except Exception:
            pass
    if prev == signal.SIG_IGN:
        return
    # Default: terminate the process with the conventional SIGTERM exit code.
    sys.exit(143)


class RunBuffer:
    """RunBuffer writer -- tees replay events to JSONL files."""

    def __init__(
        self,
        *,
        run_id: str,
        strategy_file: str,
        scenario: Optional[dict],
        base_dir: Path,
    ) -> None:
        self._run_id = run_id
        self._strategy_file = strategy_file
        self._scenario = scenario
        self._base_dir = base_dir
        self._run_dir = base_dir / run_id

        self._fills_fh: Optional[IO[str]] = None
        self._equity_fh: Optional[IO[str]] = None
        self._narrative_fh: Optional[IO[str]] = None

        self._finished = False
        self._aborted = False

        self._run_dir.mkdir(parents=True, exist_ok=True)

        started_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta = {
            "schema_version": 1,
            "run_id": run_id,
            "strategy_file": strategy_file,
            "strategy_sha256": _sha256_file(strategy_file),
            "git_rev": _get_git_rev(),
            "scenario": scenario,
            "started_at": started_at,
            "finished_at": None,
            "status": "running",
        }
        _write_meta_atomic(self._run_dir / "meta.json", meta)
        log.info("RunBuffer: started run_id=%s", run_id)

        # C1: register atexit + SIGTERM (Posix) handlers so abnormal
        # termination still flips status to "aborted".
        _ACTIVE_BUFFERS.add(self)
        atexit.register(self._atexit_handler)
        _install_sigterm_handler_once()

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    def __enter__(self) -> "RunBuffer":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._finished and not self._aborted:
            self.abort()
        self.close()

    def write_event(self, evt: dict) -> None:
        if not isinstance(evt, dict):
            return
        event_type = evt.get("event") or evt.get("type")
        if event_type in _FILLS_EVENTS:
            self._write_fills(evt)
        elif event_type in _EQUITY_EVENTS:
            self._write_equity(evt)
        elif event_type in _NARRATIVE_EVENTS:
            self._write_narrative(evt)

    def _write_fills(self, evt: dict) -> None:
        scrubbed = pii_scrub(evt, FILLS_ALLOWED_KEYS)
        if not scrubbed:
            return
        fh = self._get_fills_fh()
        fh.write(json.dumps(scrubbed, ensure_ascii=False) + "\n")
        fh.flush()

    def _write_equity(self, evt: dict) -> None:
        scrubbed = pii_scrub(evt, EQUITY_ALLOWED_KEYS)
        if not scrubbed:
            return
        fh = self._get_equity_fh()
        fh.write(json.dumps(scrubbed, ensure_ascii=False) + "\n")
        fh.flush()

    def _write_narrative(self, evt: dict) -> None:
        scrubbed = pii_scrub(evt, NARRATIVE_ALLOWED_KEYS)
        if not scrubbed:
            return
        fh = self._get_narrative_fh()
        fh.write(json.dumps(scrubbed, ensure_ascii=False) + "\n")
        fh.flush()

    def _get_fills_fh(self) -> IO[str]:
        if self._fills_fh is None:
            self._fills_fh = open(self._run_dir / "fills.jsonl", "a", encoding="utf-8")
        return self._fills_fh

    def _get_equity_fh(self) -> IO[str]:
        if self._equity_fh is None:
            self._equity_fh = open(self._run_dir / "equity.jsonl", "a", encoding="utf-8")
        return self._equity_fh

    def _get_narrative_fh(self) -> IO[str]:
        if self._narrative_fh is None:
            self._narrative_fh = open(self._run_dir / "narrative.jsonl", "a", encoding="utf-8")
        return self._narrative_fh

    def finish(self) -> None:
        """Complete the run: flush+fsync all jsonl, then atomic-rewrite meta.json.

        M9: BC3-5 invariant — if fsync fails, do NOT mark the run as
        "completed". Fall through to abort() so meta.json reflects the
        truncated/incomplete state. The original OSError is re-raised after
        abort so the caller can log/handle it.
        """
        if self._finished or self._aborted:
            return
        try:
            self._flush_and_fsync_all_jsonl()
        except OSError as exc:
            log.warning(
                "RunBuffer: fsync failed for run_id=%s, falling through to abort: %s",
                self._run_id,
                exc,
            )
            self.abort()
            raise
        finished_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._update_meta_status("completed", finished_at=finished_at)
        self._finished = True
        self._deregister_atexit()
        self.close()
        log.info("RunBuffer: finished run_id=%s", self._run_id)

    def abort(self) -> None:
        """Mark the run as aborted (best-effort, idempotent).

        Safe to call from atexit / SIGTERM handlers. No-op if the run is
        already finished or aborted. If the atomic meta.json rewrite fails
        (e.g. fsync is broken or the disk is full), fall back to a
        best-effort direct write so sweep_old_runs at least sees
        status="aborted" rather than stale "running".
        """
        if self._finished or self._aborted:
            return
        try:
            self._update_meta_status("aborted")
        except Exception as exc:
            log.warning(
                "RunBuffer: atomic abort write failed for run_id=%s: %s; "
                "falling back to direct write",
                self._run_id,
                exc,
            )
            self._best_effort_write_aborted()
        self._aborted = True
        self._deregister_atexit()
        self.close()
        log.info("RunBuffer: aborted run_id=%s", self._run_id)

    def _best_effort_write_aborted(self) -> None:
        """Last-resort write of status='aborted' when atomic write fails.

        Used only when the atomic path raises (e.g. fsync OSError). Errors
        are swallowed; this is best-effort and skips fsync.
        """
        meta_path = self._run_dir / "meta.json"
        try:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                meta = {
                    "schema_version": 1,
                    "run_id": self._run_id,
                    "strategy_file": self._strategy_file,
                    "status": "running",
                }
            meta["status"] = "aborted"
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning(
                "RunBuffer: best-effort abort write failed for run_id=%s: %s",
                self._run_id,
                exc,
            )

    def _atexit_handler(self) -> None:
        """C1: atexit fallback. Aborts the run if not yet finished/aborted.

        Idempotent: never overwrites a "completed" or already-"aborted" run.
        """
        if self._finished or self._aborted:
            return
        try:
            self.abort()
        except Exception as exc:
            log.warning(
                "RunBuffer: atexit handler failed for run_id=%s: %s",
                self._run_id,
                exc,
            )

    def _deregister_atexit(self) -> None:
        """Remove our atexit handler once the run reaches a terminal state."""
        try:
            atexit.unregister(self._atexit_handler)
        except Exception:
            pass
        try:
            _ACTIVE_BUFFERS.discard(self)
        except Exception:
            pass

    def _flush_and_fsync_all_jsonl(self) -> None:
        """Flush+fsync each open JSONL handle.

        M9: any fsync OSError propagates to the caller (finish()) which
        will fall through to abort() to preserve the BC3-5 invariant.
        """
        for fh in (self._fills_fh, self._equity_fh, self._narrative_fh):
            if fh is not None:
                fh.flush()
                os.fsync(fh.fileno())

    def _update_meta_status(self, status: str, *, finished_at: Optional[str] = None) -> None:
        meta_path = self._run_dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            log.warning("RunBuffer: meta.json read failed, creating fresh meta")
            meta = {
                "schema_version": 1,
                "run_id": self._run_id,
                "strategy_file": self._strategy_file,
                "strategy_sha256": "unknown",
                "git_rev": "unknown",
                "scenario": self._scenario,
                "started_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "finished_at": None,
                "status": "running",
            }
        meta["status"] = status
        if finished_at is not None:
            meta["finished_at"] = finished_at
        _write_meta_atomic(meta_path, meta)

    def close(self) -> None:
        for attr in ("_fills_fh", "_equity_fh", "_narrative_fh"):
            fh = getattr(self, attr)
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass
                setattr(self, attr, None)


def sweep_old_runs(base_dir: Path, max_runs: int = 30) -> None:
    """Sweep run-buffer dir: normalize stale 'running' runs and prune old ones."""
    if not base_dir.exists():
        return

    run_dirs = [d for d in base_dir.iterdir() if d.is_dir()]

    for run_dir in run_dirs:
        meta_path = run_dir / "meta.json"
        lock_path = run_dir / ".lock"

        if not meta_path.exists():
            continue
        if lock_path.exists():
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if meta.get("status") == "running":
            log.info(
                "sweep_old_runs: normalizing running->aborted run_id=%s",
                meta.get("run_id"),
            )
            meta["status"] = "aborted"
            try:
                _write_meta_atomic(meta_path, meta)
            except Exception as exc:
                log.warning("sweep_old_runs: failed to normalize %s: %s", run_dir, exc)

    def _get_started_at(d: Path) -> str:
        try:
            m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            return m.get("started_at", "")
        except Exception:
            return ""

    deletable = [d for d in run_dirs if not (d / ".lock").exists()]
    if len(deletable) > max_runs:
        sorted_runs = sorted(deletable, key=_get_started_at)
        to_delete = sorted_runs[: len(deletable) - max_runs]
        for run_dir in to_delete:
            try:
                shutil.rmtree(run_dir)
                log.info("sweep_old_runs: deleted old run %s", run_dir)
            except Exception as exc:
                log.warning("sweep_old_runs: failed to delete %s: %s", run_dir, exc)
