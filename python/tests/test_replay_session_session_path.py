"""`_resolve_session_file_path` が Rust 側 `data::data_path()` と一致することを保証する。

Rust 側 ``data/src/lib.rs::data_path`` は ``dirs_next::data_dir()`` を呼び、
Windows では ``%APPDATA%`` (Roaming) を返す。Python helper も同じディレクトリを
解決しないと、GUI が書いた ``engine-session.json`` を helper が見つけられず
``no engine-session.json / FLOWSURFACE_ENGINE_TOKEN found`` で attach mode が
即終了する。

リグレッションガード：
- ``platformdirs.user_data_dir(...)`` を ``roaming=True`` 抜きで呼ぶと
  Windows では Local が返るため、`roaming=True` の存在を pin する。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 固有のパス解決を検証する")
def test_resolve_session_file_path_uses_roaming_appdata_on_windows(monkeypatch):
    """Windows では Roaming AppData ($APPDATA) 配下を返すこと。"""
    monkeypatch.delenv("FLOWSURFACE_DATA_PATH", raising=False)

    from engine.replay_session import _resolve_session_file_path

    path = _resolve_session_file_path()

    appdata = Path(os.environ["APPDATA"])
    expected = appdata / "flowsurface" / "engine-session.json"
    assert path == expected, (
        f"Roaming AppData (%APPDATA%) 配下を返すべき。"
        f"got={path!r}, expected={expected!r}. "
        "Rust 側 dirs_next::data_dir() は Roaming を返すため、"
        "platformdirs.user_data_dir() も roaming=True が必要。"
    )


def test_resolve_session_file_path_respects_env_override(monkeypatch, tmp_path):
    """FLOWSURFACE_DATA_PATH が指定されればそちらを優先すること。"""
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))

    from engine.replay_session import _resolve_session_file_path

    path = _resolve_session_file_path()
    assert path == tmp_path / "engine-session.json"
