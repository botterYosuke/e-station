"""Phase 8.3 リグレッションガード: 旧 HTTP API ポート 9876 が誰にも listen されていないこと。

`src/replay_api.rs` / `src/api/*.rs` の HTTP API モジュールが Phase 8.3 で削除されている。
本テストは pytest 単体実行時にバックエンドが何も bind していない状態で
127.0.0.1:9876 が空いていることを確認する。

復活したらこのテストが「Address already in use」で fail する。
"""

from __future__ import annotations

import socket

import pytest


def test_port_9876_is_not_bound() -> None:
    """127.0.0.1:9876 を bind できる（=誰も listen していない）。

    `socket.bind` は他プロセスが listen 中だと OSError(EADDRINUSE) を raise する。
    bind 成功で「Phase 8.3 で HTTP API が削除されたまま」が確認できる。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        try:
            sock.bind(("127.0.0.1", 9876))
        except OSError as exc:
            pytest.fail(
                f"port 9876 is bound by another process — HTTP API may have been "
                f"reintroduced after Phase 8.3 removal: {exc}"
            )
    finally:
        sock.close()
