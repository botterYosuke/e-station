"""Phase 8.1b B2 — session ファイル atomic write の Python 側検証。

Rust 側 write_atomic は .tmp → rename の 2 ステップで書く。
プロセスクラッシュ後に .tmp だけが残った場合、_read_session_file が
それを読んではいけないことを確認する。
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from engine.replay_session import _read_session_file


# ---------------------------------------------------------------------------
# .tmp のみ存在するとき None を返す
# ---------------------------------------------------------------------------


def test_tmp_file_only_returns_none(tmp_path):
    """.tmp ファイルのみ存在する場合（クラッシュシミュレーション）None を返す。

    Rust write_atomic の途中（rename 前）でプロセスが落ちると
    engine-session.json.tmp だけが残る。helper はこれを読んではならない。
    """
    tmp_file = tmp_path / "engine-session.json.tmp"
    tmp_file.write_text(
        json.dumps({"port": 19876, "token": "should-not-be-read", "pid": os.getpid()}),
        encoding="utf-8",
    )

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is None


# ---------------------------------------------------------------------------
# rename 後は読める
# ---------------------------------------------------------------------------


def test_final_file_readable_after_rename(tmp_path):
    """atomic rename 完了後は _read_session_file がデータを返す。"""
    tmp_file = tmp_path / "engine-session.json.tmp"
    final_file = tmp_path / "engine-session.json"

    data = {"port": 19876, "token": "correct-token", "pid": os.getpid()}
    tmp_file.write_text(json.dumps(data), encoding="utf-8")
    tmp_file.rename(final_file)

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is not None
    assert result["port"] == 19876


# ---------------------------------------------------------------------------
# .tmp と final が共存するとき final だけが読まれる
# ---------------------------------------------------------------------------


def test_only_final_read_not_tmp(tmp_path):
    """.tmp と final が共存する場合、final のみが読まれる（.tmp は無視される）。"""
    tmp_file = tmp_path / "engine-session.json.tmp"
    final_file = tmp_path / "engine-session.json"

    tmp_data = {"port": 99999, "token": "stale-token", "pid": os.getpid()}
    final_data = {"port": 19876, "token": "real-token", "pid": os.getpid()}

    tmp_file.write_text(json.dumps(tmp_data), encoding="utf-8")
    final_file.write_text(json.dumps(final_data), encoding="utf-8")

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is not None
    assert result["port"] == 19876, "final のポートが読まれるべき"


# ---------------------------------------------------------------------------
# final も .tmp も無ければ None
# ---------------------------------------------------------------------------


def test_no_files_returns_none(tmp_path):
    """session ファイルも .tmp も存在しない場合 None を返す。"""
    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is None
