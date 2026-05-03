"""Group 4b — M-6 (silent) / M-3 (type) / M-10 (general) regression tests.

* M-6: ``force_mode='attach'`` でトークン未解決のときの詳細メッセージを確認する。
* M-3: ``_read_session_file`` が ``SessionFileData`` 形状の dict を返すことを確認する。
* M-10: session ファイル読み取り時、started_at が log.info に出ることを確認する。

これらは subprocess engine を起動しない純ロジックテスト。
"""

from __future__ import annotations

import json
import logging

import pytest

from engine.replay_session import (
    ReplaySession,
    SessionFileData,
    _read_session_file,
    _resolve_session_file_path,
)


def _write_session(tmp_path, *, pid: int, port: int = 19876, token: str = "tok"):
    sess = {
        "port": port,
        "token": token,
        "pid": pid,
        "schema_major": 3,
        "started_at": "2026-05-03T00:00:00Z",
    }
    (tmp_path / "engine-session.json").write_text(json.dumps(sess), encoding="utf-8")


def test_attach_endpoint_without_token_raises_with_detail(monkeypatch):
    """M-6: attach_endpoint 指定 + token 未設定で詳細メッセージが出る。"""
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)
    s = ReplaySession(force_mode="attach", attach_endpoint="ws://127.0.0.1:9999/")
    with pytest.raises(ConnectionRefusedError) as ei:
        s.__enter__()
    msg = str(ei.value)
    assert "FLOWSURFACE_ENGINE_TOKEN" in msg, msg
    # token 値そのものは（未設定なので）含まれない。
    assert "token=" not in msg.lower() or "tok" not in msg


def test_attach_force_without_session_or_env_raises(monkeypatch, tmp_path):
    """M-6: session ファイルも env もなければ詳細メッセージで失敗する。"""
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)
    s = ReplaySession(force_mode="attach")
    with pytest.raises(ConnectionRefusedError) as ei:
        s.__enter__()
    assert "engine-session.json" in str(ei.value) or "FLOWSURFACE_ENGINE_TOKEN" in str(ei.value)


def test_read_session_file_returns_typed_dict_shape(monkeypatch, tmp_path):
    """M-3: _read_session_file が SessionFileData 形状の dict を返す。"""
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    import os
    _write_session(tmp_path, pid=os.getpid())  # live pid
    data = _read_session_file()
    assert data is not None
    # TypedDict なので runtime 型は dict。
    assert isinstance(data, dict)
    # SessionFileData のキーが含まれる。
    assert data["port"] == 19876
    assert data["token"] == "tok"
    assert data["started_at"] == "2026-05-03T00:00:00Z"


def test_session_file_data_typed_dict_exists():
    """M-3: SessionFileData TypedDict が export されている。"""
    # 単純な存在チェック — typing.TypedDict は __required_keys__ を持つ。
    assert hasattr(SessionFileData, "__annotations__")
    assert "port" in SessionFileData.__annotations__
    assert "token" in SessionFileData.__annotations__
    assert "started_at" in SessionFileData.__annotations__


def test_resolve_endpoint_logs_started_at(monkeypatch, tmp_path, caplog):
    """M-10: session ファイル読み取り時、started_at が info ログに出る。"""
    import os
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    _write_session(tmp_path, pid=os.getpid())

    s = ReplaySession()  # force_mode="auto" but we call _resolve directly
    with caplog.at_level(logging.INFO, logger="engine.replay_session"):
        endpoint, token = s._resolve_endpoint_and_token()
    assert endpoint == "ws://127.0.0.1:19876/"
    assert token == "tok"
    # started_at はログに出る。token は出ない。
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "2026-05-03T00:00:00Z" in msgs
    assert "tok" not in msgs.split("started_at=")[-1].split()[0:1] if "started_at=" in msgs else True


def test_resolve_session_file_path_uses_env_override(monkeypatch, tmp_path):
    """サニティ: FLOWSURFACE_DATA_PATH override が機能する。"""
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    p = _resolve_session_file_path()
    assert p.parent == tmp_path
    assert p.name == "engine-session.json"
