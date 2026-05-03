"""Phase 8.1b B2 — engine-session.json ライフサイクルの Python 側検証。

engine subprocess が alive な間は _read_session_file がデータを返し、
プロセス終了後は stale pid を検出して None を返すことを確認する。

NOTE: session ファイルの書き手は Rust (engine-client/src/session_file.rs) だが、
      このテストでは subprocess の pid を使って手動で session ファイルを作成し、
      _read_session_file の pid liveness チェックを検証する。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from engine.replay_session import _read_session_file
from engine.schemas import SCHEMA_MAJOR


# ---------------------------------------------------------------------------
# alive pid → data を返す / dead pid → None を返す
# ---------------------------------------------------------------------------


def test_engine_session_file_lifecycle(tmp_path):
    """subprocess 起動中は data を返し、終了後は None を返す（pid liveness 検証）。

    Rust が書く engine-session.json と同じ構造を手動で作成し、
    _read_session_file の pid liveness チェックが機能することを確認する。
    """
    session_file = tmp_path / "engine-session.json"

    # dummy subprocess で alive pid を確保する
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        session_data = {
            "port": 19876,
            "token": "lifecycle-test-token",
            "pid": proc.pid,
            "schema_major": SCHEMA_MAJOR,
            "started_at": "2026-05-03T00:00:00+00:00",
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        # プロセス alive → data を返す
        with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
            result = _read_session_file()

        assert result is not None, "alive pid は data を返すべき"
        assert result["port"] == 19876

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # Windows でプロセステーブルが更新されるまで少し待つ
    time.sleep(0.3)

    # プロセス dead → None を返す
    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is None, "dead pid は None を返すべき（session ファイルは stale）"


# ---------------------------------------------------------------------------
# session ファイルが無い場合は None
# ---------------------------------------------------------------------------


def test_no_session_file_returns_none(tmp_path):
    """session ファイルが存在しない場合は None を返す。"""
    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is None


# ---------------------------------------------------------------------------
# 正常な session ファイル（alive pid）は data を返す
# ---------------------------------------------------------------------------


def test_valid_session_file_returns_data(tmp_path):
    """有効な session ファイル（current pid = alive）は data を返す。"""
    session_file = tmp_path / "engine-session.json"
    session_data = {
        "port": 19876,
        "token": "valid-session-token",
        "pid": os.getpid(),
        "schema_major": SCHEMA_MAJOR,
        "started_at": "2026-05-03T00:00:00+00:00",
    }
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is not None
    assert result["port"] == 19876
    assert result["schema_major"] == SCHEMA_MAJOR
