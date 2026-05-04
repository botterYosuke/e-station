"""F9a: RunBuffer 書き出しのテスト（TDD: RED → GREEN → REFACTOR）。

P9-wandb-submit-menu.md §F9a DoD より:
  - ExecutionMarker → fills.jsonl
  - ReplayBuyingPower → equity.jsonl
  - StrategySignal → narrative.jsonl
  - finish() → meta.json status="completed"
  - abort() → meta.json status="aborted"
  - jsonl の fsync が meta.json rewrite より前（呼出し順序を mock で assert）
  - PII 禁止キー が出力に出ない
  - status="running" & no .lock → sweep 時 "aborted" に正規化
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.run_buffer import RunBuffer, sweep_old_runs


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_run_buffer(tmp_path: Path, run_id: str = "test-run-001") -> RunBuffer:
    """テスト用 RunBuffer を tmp_path 配下に作成する。"""
    return RunBuffer(
        run_id=run_id,
        strategy_file="docs/example/buy_and_hold.py",
        scenario={"instrument": "1301.TSE"},
        base_dir=tmp_path,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# test 1: ExecutionMarker を write_event → fills.jsonl に書かれる
# ---------------------------------------------------------------------------


def test_write_fills_event(tmp_path: Path) -> None:
    """ExecutionMarker を write_event → fills.jsonl に書かれる。"""
    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "ExecutionMarker",
        "strategy_id": "user-strategy",
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "2500.0",
        "qty": "100",
        "ts_event_ms": 1714800123000,
    }
    rb.write_event(evt)

    fills_path = rb.run_dir / "fills.jsonl"
    assert fills_path.exists(), "fills.jsonl が生成されていない"

    rows = _read_jsonl(fills_path)
    assert len(rows) == 1
    assert rows[0]["side"] == "BUY"
    assert rows[0]["price"] == "2500.0"
    # event キーは除去されている（allow-list から外れているため）
    assert "event" not in rows[0]


# ---------------------------------------------------------------------------
# test 2: ReplayBuyingPower を write_event → equity.jsonl に書かれる
# ---------------------------------------------------------------------------


def test_write_equity_event(tmp_path: Path) -> None:
    """ReplayBuyingPower を write_event → equity.jsonl に書かれる。"""
    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "ReplayBuyingPower",
        "strategy_id": "user-strategy",
        "cash": "990000",
        "buying_power": "990000",
        "equity": "995000",
        "ts_event_ms": 1714800123001,
    }
    rb.write_event(evt)

    equity_path = rb.run_dir / "equity.jsonl"
    assert equity_path.exists(), "equity.jsonl が生成されていない"

    rows = _read_jsonl(equity_path)
    assert len(rows) == 1
    assert rows[0]["equity"] == "995000"
    assert rows[0]["cash"] == "990000"
    # event キーは除去されている
    assert "event" not in rows[0]


# ---------------------------------------------------------------------------
# test 3: StrategySignal を write_event → narrative.jsonl に書かれる
# ---------------------------------------------------------------------------


def test_write_narrative_event(tmp_path: Path) -> None:
    """StrategySignal を write_event → narrative.jsonl に書かれる。"""
    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "StrategySignal",
        "strategy_id": "user-strategy",
        "instrument_id": "1301.TSE",
        "signal_kind": "EntryLong",
        "side": "BUY",
        "price": "2500.0",
        "tag": "momentum",
        "note": "RSI cross",
        "ts_event_ms": 1714800123002,
    }
    rb.write_event(evt)

    narrative_path = rb.run_dir / "narrative.jsonl"
    assert narrative_path.exists(), "narrative.jsonl が生成されていない"

    rows = _read_jsonl(narrative_path)
    assert len(rows) == 1
    assert rows[0]["signal_kind"] == "EntryLong"
    assert rows[0]["tag"] == "momentum"
    assert rows[0]["note"] == "RSI cross"
    # event キーは除去されている
    assert "event" not in rows[0]


# ---------------------------------------------------------------------------
# test 4: finish() → meta.json の status が "completed"
# ---------------------------------------------------------------------------


def test_finish_writes_meta_completed(tmp_path: Path) -> None:
    """finish() → meta.json の status が "completed"、finished_at が設定される。"""
    rb = _make_run_buffer(tmp_path)

    # 起動直後は "running"
    meta_path = rb.run_dir / "meta.json"
    assert meta_path.exists(), "meta.json が生成されていない"
    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "running"

    rb.finish()

    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "completed"
    assert meta["finished_at"] is not None
    assert meta["run_id"] == "test-run-001"


# ---------------------------------------------------------------------------
# test 5: abort() → status が "aborted"
# ---------------------------------------------------------------------------


def test_abort_writes_meta_aborted(tmp_path: Path) -> None:
    """abort() → meta.json の status が "aborted"。"""
    rb = _make_run_buffer(tmp_path)

    rb.abort()

    meta_path = rb.run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "aborted"


# ---------------------------------------------------------------------------
# test 6: finish() 呼出し順序: jsonl の fsync が meta.json rewrite より前
# ---------------------------------------------------------------------------


def test_meta_json_flushed_after_jsonl(tmp_path: Path) -> None:
    """finish() で jsonl の fsync が meta.json rewrite より前に行われること。

    os.fsync と os.replace（atomic rewrite）の呼出し順序を assert する。
    """
    rb = _make_run_buffer(tmp_path)

    # equity.jsonl にデータを書いてファイルを開かせる
    rb.write_event({
        "event": "ReplayBuyingPower",
        "strategy_id": "s1",
        "cash": "1000000",
        "buying_power": "1000000",
        "equity": "1000000",
        "ts_event_ms": 1714800123000,
    })

    call_log: list[str] = []

    original_fsync = os.fsync

    def mock_fsync(fd: int) -> None:
        call_log.append(f"fsync:{fd}")
        original_fsync(fd)

    original_replace = os.replace

    def mock_replace(src: str, dst: str) -> None:
        call_log.append("meta_replace")
        original_replace(src, dst)

    with patch("os.fsync", side_effect=mock_fsync):
        with patch("os.replace", side_effect=mock_replace):
            rb.finish()

    # fsync が meta_replace より前に少なくとも 1 回呼ばれていること
    fsync_indices = [i for i, c in enumerate(call_log) if c.startswith("fsync:")]
    replace_indices = [i for i, c in enumerate(call_log) if c == "meta_replace"]

    assert len(fsync_indices) >= 1, "fsync が 1 回も呼ばれていない"
    assert len(replace_indices) >= 1, "meta_replace（os.replace）が呼ばれていない"
    assert max(fsync_indices) < min(replace_indices), (
        f"fsync が meta_replace より後に呼ばれている: {call_log}"
    )


# ---------------------------------------------------------------------------
# test 7: PII scrub が禁止キーを除去する
# ---------------------------------------------------------------------------


def test_pii_scrub_strips_forbidden_keys_instead_of_dropping(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """H2: 禁止キー検出時に event 全体を skip するのではなく、禁止キーを strip
    して allow-list 適用結果を書き出す。WARN ログで開発者に通知する。
    """
    import logging as _logging

    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "ExecutionMarker",
        "strategy_id": "user-strategy",
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "2500.0",
        "qty": "100",
        "ts_event_ms": 1714800123000,
        # 禁止キー（engine.pii_scrub.FORBIDDEN_KEYS に含まれるもの）
        "venue_order_id": "V12345678",
        "client_order_id": "C-abc-001",
        "raw_data": {"secret": "leak"},
        "payload": "raw_bytes",
    }
    with caplog.at_level(_logging.WARNING, logger="engine.pii_scrub"):
        rb.write_event(evt)

    fills_path = rb.run_dir / "fills.jsonl"
    rows = _read_jsonl(fills_path)

    # event は drop されず、禁止キー除去後の allow-list 適用結果が 1 行書かれる
    assert len(rows) == 1, (
        f"禁止キー検出時に event 全体が捨てられている（H2 違反）: {rows}"
    )
    row = rows[0]
    # 禁止キーは含まれない
    for k in ("venue_order_id", "client_order_id", "raw_data", "payload"):
        assert k not in row, f"strip したはずの禁止キーが残っている: {k}"
    # allow-list のキーは保持される
    assert row["side"] == "BUY"
    assert row["price"] == "2500.0"
    assert row["qty"] == "100"
    assert row["instrument_id"] == "1301.TSE"
    # WARN ログが出ている
    assert any(
        "forbidden keys detected" in rec.getMessage() for rec in caplog.records
    ), f"WARN ログが出ていない: {[r.getMessage() for r in caplog.records]}"


def test_real_nautilus_fill_event_is_written(tmp_path: Path) -> None:
    """H2: order_id を含む Nautilus 風 Fill イベントが fills.jsonl に書かれる。

    旧実装では `order_id` が FORBIDDEN_KEYS に含まれていたため、Nautilus が
    生成する全 fill が捨てられていた。order_id を FORBIDDEN_KEYS から外し、
    かつ allow-list で自然に剥がれることを検証する。
    """
    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "ExecutionMarker",
        "order_id": "O-LIVE-12345",  # Nautilus 由来。allow-list 外なので自然に落ちる
        "instrument_id": "1301.TSE",
        "symbol": "1301.TSE",
        "side": "BUY",
        "price": "2500.0",
        "qty": "100",
    }
    rb.write_event(evt)

    rows = _read_jsonl(rb.run_dir / "fills.jsonl")
    assert len(rows) == 1, "Nautilus Fill が fills.jsonl に書かれていない"
    row = rows[0]
    assert row.get("symbol") == "1301.TSE"
    assert row.get("qty") == "100"
    assert row.get("price") == "2500.0"
    # order_id は allow-list 外なので自動的に落ちる
    assert "order_id" not in row


# ---------------------------------------------------------------------------
# H1: engine と examples の PII allow-list 定数が完全一致する（CI lint）
# ---------------------------------------------------------------------------


def test_pii_allowlist_consistency_engine_and_examples() -> None:
    """engine.pii_scrub と examples.wandb.pii_scrub の定数が完全一致する。

    両ファイルで定数が乖離すると JSONL 書き出しと W&B アップロードで
    異なる allow-list が適用され、PII 漏洩や不整合が発生する。
    """
    repo_root = Path(__file__).resolve().parents[2]
    examples_dir = repo_root / "examples" / "wandb"
    if str(examples_dir) not in sys.path:
        sys.path.insert(0, str(examples_dir))

    # examples 側を独立 module として import（同名 module の衝突を避けるため）
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "examples_wandb_pii_scrub", examples_dir / "pii_scrub.py"
    )
    assert spec is not None and spec.loader is not None
    examples_pii = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(examples_pii)

    from engine import pii_scrub as engine_pii

    assert engine_pii.FILLS_ALLOWED_KEYS == examples_pii.FILLS_ALLOWED_KEYS, (
        f"FILLS_ALLOWED_KEYS が不一致: engine={engine_pii.FILLS_ALLOWED_KEYS} "
        f"examples={examples_pii.FILLS_ALLOWED_KEYS} "
        f"diff={engine_pii.FILLS_ALLOWED_KEYS ^ examples_pii.FILLS_ALLOWED_KEYS}"
    )
    assert engine_pii.EQUITY_ALLOWED_KEYS == examples_pii.EQUITY_ALLOWED_KEYS, (
        f"EQUITY_ALLOWED_KEYS が不一致: "
        f"diff={engine_pii.EQUITY_ALLOWED_KEYS ^ examples_pii.EQUITY_ALLOWED_KEYS}"
    )
    assert engine_pii.NARRATIVE_ALLOWED_KEYS == examples_pii.NARRATIVE_ALLOWED_KEYS, (
        f"NARRATIVE_ALLOWED_KEYS が不一致: "
        f"diff={engine_pii.NARRATIVE_ALLOWED_KEYS ^ examples_pii.NARRATIVE_ALLOWED_KEYS}"
    )
    assert engine_pii.FORBIDDEN_KEYS == examples_pii.FORBIDDEN_KEYS, (
        f"FORBIDDEN_KEYS が不一致: "
        f"diff={engine_pii.FORBIDDEN_KEYS ^ examples_pii.FORBIDDEN_KEYS}"
    )


# ---------------------------------------------------------------------------
# M12: pii_scrub のシグネチャ・挙動が engine と examples で一致する
# ---------------------------------------------------------------------------


def test_engine_and_examples_pii_scrub_have_same_signature() -> None:
    """engine.pii_scrub.pii_scrub と examples.wandb.pii_scrub.pii_scrub が
    同じシグネチャを持ち、同じ入力で同じ出力を返す。
    """
    import inspect

    repo_root = Path(__file__).resolve().parents[2]
    examples_dir = repo_root / "examples" / "wandb"
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "examples_wandb_pii_scrub_sig", examples_dir / "pii_scrub.py"
    )
    assert spec is not None and spec.loader is not None
    examples_pii = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(examples_pii)

    from engine import pii_scrub as engine_pii

    eng_sig = inspect.signature(engine_pii.pii_scrub)
    exa_sig = inspect.signature(examples_pii.pii_scrub)
    assert list(eng_sig.parameters.keys()) == list(exa_sig.parameters.keys()), (
        f"パラメータ名が異なる: engine={list(eng_sig.parameters)} "
        f"examples={list(exa_sig.parameters)}"
    )

    # 同一入力で同一出力
    sample = {
        "symbol": "X",
        "side": "BUY",
        "qty": 1,
        "price": 100.0,
        "ts": "2025-01-01T00:00:00Z",
        "pnl": 0.0,
        "instrument_id": "X.T",
        "venue_order_id": "V-secret",  # forbidden
        "extra_unknown": "drop me",
    }
    eng_out = engine_pii.pii_scrub(sample, engine_pii.FILLS_ALLOWED_KEYS)
    exa_out = examples_pii.pii_scrub(sample, examples_pii.FILLS_ALLOWED_KEYS)
    assert eng_out == exa_out, f"出力が異なる: engine={eng_out} examples={exa_out}"
    # 両者とも dict を返す（None ではない）
    assert isinstance(eng_out, dict) and isinstance(exa_out, dict)
    # forbidden は剥がれる、allow-list 外も剥がれる
    assert "venue_order_id" not in eng_out
    assert "extra_unknown" not in eng_out


# ---------------------------------------------------------------------------
# test 8: status="running" & no .lock → sweep 時 "aborted" に正規化
# ---------------------------------------------------------------------------


def test_running_without_lock_is_normalized_to_aborted(tmp_path: Path) -> None:
    """status="running" & .lock ファイル無し → sweep_old_runs が "aborted" に正規化する。"""
    # running のまま放置された run-buffer を手動作成
    run_dir = tmp_path / "1714800123-strategy-1301_TSE"
    run_dir.mkdir(parents=True)

    meta = {
        "schema_version": 1,
        "run_id": "1714800123-strategy-1301_TSE",
        "strategy_file": "strategy.py",
        "strategy_sha256": "abc",
        "git_rev": "unknown",
        "scenario": None,
        "started_at": "2026-05-04T07:42:03Z",
        "finished_at": None,
        "status": "running",
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    # .lock ファイルなし

    sweep_old_runs(tmp_path, max_runs=30)

    meta_after = json.loads((run_dir / "meta.json").read_text())
    assert meta_after["status"] == "aborted", (
        f"running & no .lock の run が aborted に正規化されていない: {meta_after['status']}"
    )


# ---------------------------------------------------------------------------
# C1: SIGTERM / atexit handler テスト
# ---------------------------------------------------------------------------


def _spawn_run_buffer_subprocess(
    base_dir: Path,
    run_id: str,
    *,
    finish: bool = False,
    sleep_seconds: float = 0.0,
) -> subprocess.Popen:
    """子プロセスで RunBuffer を生成する。

    finish=True なら finish() を呼んでから sleep する。
    finish=False なら何もせず sleep する（atexit/SIGTERM の動作確認用）。
    """
    repo_root = Path(__file__).resolve().parents[2]
    code = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, r"{repo_root / 'python'}")
        from pathlib import Path
        from engine.run_buffer import RunBuffer

        rb = RunBuffer(
            run_id={run_id!r},
            strategy_file="docs/example/buy_and_hold.py",
            scenario={{"instrument": "1301.TSE"}},
            base_dir=Path(r"{base_dir}"),
        )
        if {finish!r}:
            rb.finish()
        # ready signal
        print("READY", flush=True)
        time.sleep({sleep_seconds})
        """
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_ready(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """子プロセスが READY を出力するまで待つ。"""
    deadline = time.time() + timeout
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                raise RuntimeError(f"subprocess exited early: {stderr}")
            continue
        if line.strip() == "READY":
            return
    raise TimeoutError("subprocess did not become ready")


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not supported on Windows")
def test_sigterm_aborts_run(tmp_path: Path) -> None:
    """SIGTERM 受信時に RunBuffer が status='aborted' を書き出す。"""
    import signal

    run_id = "sigterm-test-001"
    proc = _spawn_run_buffer_subprocess(tmp_path, run_id, sleep_seconds=10.0)
    try:
        _wait_ready(proc)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)

    meta_path = tmp_path / run_id / "meta.json"
    assert meta_path.exists(), f"meta.json が生成されていない: {meta_path}"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "aborted", (
        f"SIGTERM 受信後 status が aborted になっていない: {meta['status']}"
    )


def test_atexit_aborts_unfinished_run(tmp_path: Path) -> None:
    """子プロセスが finish() せず exit すると atexit handler が aborted に書く。"""
    run_id = "atexit-test-001"
    proc = _spawn_run_buffer_subprocess(tmp_path, run_id, sleep_seconds=0.1)
    try:
        proc.wait(timeout=15.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)

    assert proc.returncode == 0, (
        f"subprocess exited with {proc.returncode}, "
        f"stderr={proc.stderr.read() if proc.stderr else ''}"
    )

    meta_path = tmp_path / run_id / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "aborted", (
        f"atexit 後 status が aborted になっていない: {meta['status']}"
    )


def test_finish_then_atexit_no_double_write(tmp_path: Path) -> None:
    """finish() 後に atexit handler が走っても status='completed' を上書きしない。"""
    rb = _make_run_buffer(tmp_path, run_id="finish-then-atexit-001")
    rb.finish()

    meta_path = rb.run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "completed"

    # 内部 atexit handler を直接呼ぶ（実装は _atexit_handler メソッドを持つ前提）
    assert hasattr(rb, "_atexit_handler"), (
        "RunBuffer に _atexit_handler メソッドが定義されていない"
    )
    rb._atexit_handler()

    meta_after = json.loads(meta_path.read_text())
    assert meta_after["status"] == "completed", (
        f"finish 後の atexit handler で status が上書きされた: {meta_after['status']}"
    )


# ---------------------------------------------------------------------------
# M9: fsync 失敗時に completed にせず aborted にする
# ---------------------------------------------------------------------------


def test_fsync_failure_aborts_instead_of_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_flush_and_fsync_all_jsonl が OSError を raise した場合、
    finish() は status='completed' を書かず status='aborted' に倒す。
    """
    rb = _make_run_buffer(tmp_path, run_id="fsync-fail-001")
    rb.write_event({
        "event": "ReplayBuyingPower",
        "strategy_id": "s1",
        "cash": "1000000",
        "buying_power": "1000000",
        "equity": "1000000",
        "ts_event_ms": 1714800123000,
    })

    original_fsync = os.fsync

    def failing_fsync(fd: int) -> None:
        # jsonl ファイルディスクリプタの fsync を失敗させる。
        # tempfile (meta.json atomic write) の fsync は成功させたいが、
        # ここでは全 fsync を失敗させても実装側で abort 経路に入れば OK。
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    # finish() は OSError を吞んで abort 経路に倒すか、raise するか
    # いずれかの実装許容だが、最終 status は aborted であること。
    try:
        rb.finish()
    except OSError:
        # 実装が raise する選択肢の場合、ここで catch して abort を保証
        pass

    # fsync を元に戻して meta を読む
    monkeypatch.setattr(os, "fsync", original_fsync)

    meta_path = rb.run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "aborted", (
        f"fsync 失敗時に status が aborted になっていない: {meta['status']}"
    )
    assert meta["status"] != "completed", (
        "fsync 失敗時に status='completed' になっている（不変条件違反）"
    )


# ---------------------------------------------------------------------------
# M11: Windows os.replace race（PermissionError retry）
# ---------------------------------------------------------------------------


def test_write_meta_atomic_retries_on_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_write_meta_atomic は PermissionError を 3 回まで retry し、最終的に成功する。"""
    from engine import run_buffer as rb_mod

    original_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise PermissionError("simulated Windows lock")
        return original_replace(src, dst)

    monkeypatch.setattr(rb_mod.os, "replace", flaky_replace)

    meta_path = tmp_path / "meta.json"
    meta = {"schema_version": 1, "status": "running", "run_id": "x"}
    rb_mod._write_meta_atomic(meta_path, meta)

    assert call_count["n"] == 3, f"retry 回数が想定と異なる: {call_count['n']}"
    assert meta_path.exists()
    assert json.loads(meta_path.read_text(encoding="utf-8"))["status"] == "running"


def test_write_meta_atomic_gives_up_after_3_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_write_meta_atomic は 3 回連続 PermissionError なら raise する。"""
    from engine import run_buffer as rb_mod

    call_count = {"n": 0}

    def always_failing_replace(src, dst):
        call_count["n"] += 1
        raise PermissionError("simulated permanent lock")

    monkeypatch.setattr(rb_mod.os, "replace", always_failing_replace)

    meta_path = tmp_path / "meta.json"
    meta = {"schema_version": 1, "status": "running", "run_id": "x"}
    with pytest.raises(PermissionError):
        rb_mod._write_meta_atomic(meta_path, meta)

    assert call_count["n"] == 3, f"retry 回数が 3 ではない: {call_count['n']}"
