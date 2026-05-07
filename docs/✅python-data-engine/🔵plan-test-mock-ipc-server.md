# テスト改善計画: モック IPC サーバーによる E2E 短縮

## 背景と課題

現在の bash smoke テスト（`test_live_session_kabu.py` 等）は Rust プロセス + Python エンジンを実際に起動するため **30〜120 秒**かかり、CI でのフィードバックが遅い。

テスト層の現状:

| 層 | 実装 | 速度 | WS 層をカバー |
|---|---|---|---|
| `force_mode="inprocess"` | `NautilusRunner` を直接呼ぶ / `MagicMock` で差し替え | < 1s | ✗ |
| WS プロトコルテスト | `test_server_ws_compat.py`・`test_schema_minor_compat.py`・`test_server_multi_client.py`・`test_replay_session_attach.py` | 1〜5 s | ✓（主要経路） |
| smoke（bash） | Rust GUI + Python エンジンを実プロセス起動 | 30〜120 s | ✓（結合） |
| **（欠落）** | WS プロトコルを in-process fixture で高速に検証（MockIPCServer） | — | — |

既存の attach/protocol テストは実プロセスに依存しているため CI のフィードバックが遅い。これらを実プロセス不要の in-process fixture（MockIPCServer）に移植して高速化することが本計画の目的である。

## 目標

`python/tests/` に **モック IPC サーバーフィクスチャ** を導入し、IPC プロトコル経路を実プロセス起動なしで 1 秒以内に検証できるようにする。smoke は**プロセス起動そのもの**の検証に絞る。

`MockIPCServer` は handshake シナリオだけでなく、**attach 解決・inprocess fallback・stale pid リカバリのシナリオもカバーする**。実プロセス不要で `_resolve_endpoint_and_token()` の全分岐を検証できることが本計画の価値の核心である。

## アプローチ

### 新規: `MockIPCServer` フィクスチャ

`python/tests/fixtures/mock_ipc_server.py` に以下を実装する:

```python
# 概略 — 詳細は実装時に調整
import asyncio, threading, json
import websockets

class MockIPCServer:
    """IPC プロトコルを喋るインプロセス WebSocket サーバー。"""

    def __init__(self, script: list[dict]):
        # script: クライアントから届く各コマンドに対する応答シーケンス
        self.script = script
        self._port: int | None = None
        self._token = "test-token"
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()

    @property
    def port(self) -> int: ...
    @property
    def token(self) -> str: ...

    def start(self) -> None:
        """バックグラウンドスレッドでサーバーを起動し ready まで待つ。"""
        ...

    async def _serve(self, ws, path):
        # websockets.serve は compression=None 必須。
        # compression="deflate"（デフォルト）だと RSV1=1 フレームを送出し、
        # fastwebsockets（Rust IPC client）が "Reserved bits are not zero" で切断する。
        ...

    def stop(self) -> None: ...
    def __enter__(self): self.start(); return self
    def __exit__(self, *_): self.stop()
```

`script` は `[{"on": "Hello", "reply": [{"type": "Ready", ...}]}, ...]` のような宣言的リストとし、テストが期待する IPC 応答シーケンスを明示できる形にする。

### 既存 `ReplaySession` / `LiveSession` の拡張

`ReplaySession` は `attach_endpoint` 引数で endpoint を直接指定できる。`MockIPCServer` が発行したポートを `f"ws://127.0.0.1:{srv.port}/"` 形式で渡し、token は `FLOWSURFACE_ENGINE_TOKEN` 環境変数で渡す。テスト終了後は環境変数を必ず `del` すること（他テストへの汚染防止）。

テスト側:

```python
def test_handshake_schema_mismatch(mock_ipc_server_factory):
    script = [
        {"on": "Hello", "reply": [
            {"event": "client_connected", "count": 1},
            {"event": "engine_error", "reason": "schema_mismatch", "code": "SCHEMA_MAJOR_MISMATCH"}
        ]}
    ]
    with mock_ipc_server_factory(script) as srv:
        import os
        os.environ["FLOWSURFACE_ENGINE_TOKEN"] = srv.token
        try:
            with pytest.raises(ConnectionRefusedError):
                with ReplaySession(force_mode="attach",
                                   attach_endpoint=f"ws://127.0.0.1:{srv.port}/") as s:
                    pass
        finally:
            del os.environ["FLOWSURFACE_ENGINE_TOKEN"]
```

> ⚠ `HelloReject` という wire は現行実装に存在しない。ハンドシェイク失敗は `EngineError`（スキーマ不一致）または接続切断（token 不一致）で表現する。`ClientConnected` は Phase 8 attach mode でのみ送出される（Phase 7 以前は省略）。実装前に `replay_session.py::_handshake()` の実際のフローを確認すること。

> ⚠ `ConnectionRefusedError` は `ReplaySession.__enter__()` から送出される。`with ReplaySession(...)` のブロック内（`s.load()` 呼び出し時点）では既にハンドシェイク完了済みか例外済みのいずれか。

### smoke テストの整理

実プロセス起動を伴うテストに `@pytest.mark.smoke` を付与し、CI の通常ジョブから除外する。

`pytest.ini` （現在の内容に `smoke` マーカーを追記する）:

```ini
[pytest]
markers =
    demo_tachibana: ...（既存）
    tk_smoke: ...（既存）
    demo_kabu: ...（既存）
    smoke: 実プロセス起動を伴う結合テスト（CI では --smoke フラグ時のみ実行）  ← 追加
```

`conftest.py` に `--smoke` フラグ未指定時は smoke をスキップする addoption を追加する。

## 実装フェーズ

### フェーズ S1: `MockIPCServer` 骨格 + Hello/Ready

- [ ] `python/tests/fixtures/mock_ipc_server.py` を実装。
  - `asyncio` + `websockets.serve` でインプロセス起動。**`compression=None` は必須。省略禁止**（省略すると `compression="deflate"` がデフォルトで適用され、RSV1=1 フレームを送出するため fastwebsockets が "Reserved bits are not zero" で切断する）。
    ```python
    server = await websockets.serve(handler, "127.0.0.1", 0, compression=None)
    ```
  - ランダムポート (`port=0`) でバインドし、起動後に実ポートを `_port` に格納。
  - `Hello` → `Ready` の最小ハンドシェイクのみ対応。
  - `python/tests/fixtures/__init__.py` に export を追加。
- [ ] `python/tests/test_mock_ipc_server_basic.py` を追加。
  - `MockIPCServer` を使って `ReplaySession(force_mode="attach")` が `Ready` を受け取れることを確認。
  - 実行時間: < 1 s を assert。

**完了条件**: `pytest python/tests/test_mock_ipc_server_basic.py` が 1 秒以内に PASS。テストコードに `@pytest.mark.timeout(1)` デコレータ（`pytest-timeout` パッケージ）または `start = time.monotonic(); ...; assert time.monotonic() - start < 1.0` による明示的 assert を含めること。`stop()` を複数回呼んでも例外が発生しないこと（`test_mock_ipc_server_basic.py` 内に `server.stop(); server.stop()` のアサーションを追加すること）。

### フェーズ S2: IPC プロトコル網羅

- [ ] `script` 形式でコマンド応答を定義できるようにする（`Subscribe`, `FetchKlines`, `Unsubscribe`, `Shutdown`）。
- [ ] スキーマ不一致（`EngineError` + `SCHEMA_MAJOR_MISMATCH`）応答のテストを追加（`HelloReject` という wire は存在しない）。
- [ ] `ConnectionRefusedError` / `attach` → `inprocess` フォールバックのテスト（現 `test_replay_session_attach.py` を `MockIPCServer` に移植）。
- [ ] `pytest.ini` に `timeout = 60` を追記（`pytest-timeout` により `pytest`（引数なし）の全体タイムアウトを CI で強制）。`pyproject.toml` の `[project.optional-dependencies]` に `pytest-timeout` を追加。

**完了条件**: `force_mode="attach"` 経路の WS ロジックが `MockIPCServer` でカバーされ、`pytest`（引数なし）が 60 秒以内に完走。

### フェーズ S4: attach 解決シナリオ

実プロセスを一切起動せず、`_resolve_endpoint_and_token()`（`replay_session.py` / `LiveSession`）の分岐を `MockIPCServer` で完全カバーする。

#### S4-A: 正常 attach（session-file に有効な pid/port が存在するケース）

- `engine-session.json` に生きているプロセスの pid と `MockIPCServer` のポートを書き込む。
- `_resolve_endpoint_and_token()` が session-file を読んで `MockIPCServer` へ attach し、`Ready` を受け取れることを確認。
- **期待**: attach 成功、inprocess フォールバックへ進まない。

#### S4-B: stale pid（プロセス死亡済み）のケース

- `engine-session.json` に存在しない pid（例: `99999999`）と dummy port を書き込む。
- `_resolve_endpoint_and_token()` が pid の死亡を検出してフォールバックを試みる動作を確認。
- `MockIPCServer` は probe 先として機能しない（接続拒否 or タイムアウト）。
- **期待**: stale pid を読み捨てて probe fallback または inprocess fallback へ遷移する。

#### S4-C: session-file なし（新規起動 fallback）のケース

- `engine-session.json` が存在しない状態で `force_mode="attach"` を呼び出す。
- **期待**: session-file なしを検知し、inprocess fallback または明示的な例外を送出する（実装に合わせてアサートを調整）。

#### 各シナリオ共通の注意点

- `MockIPCServer` が返す endpoint（`ws://127.0.0.1:{srv.port}/`）を `attach_endpoint` または session-file 経由で渡し、`FLOWSURFACE_ENGINE_TOKEN` 環境変数でトークンを注入する。
- テスト終了後は session-file を削除し、環境変数を `del` して他テストへの汚染を防ぐ。
- **既存テストとの棲み分け**: `test_live_session_kabu.py` 等の既存 attach テストは実プロセスに依存しており `@pytest.mark.smoke` で管理する。S4 のテストは `MockIPCServer` を使うことで**実プロセス不要**、かつ **1 秒以内**に同じ分岐を検証できる点が差別化価値である。重複を避けるため、S4 では「分岐ロジック」の検証に絞り、実プロセス間通信の結合確認は smoke に委ねる。

**完了条件**: `pytest python/tests/test_mock_ipc_server_attach.py` が 1 秒以内に PASS。`_resolve_endpoint_and_token()` の S4-A / S4-B / S4-C の 3 分岐すべてがカバーされていること。

### フェーズ S3: smoke 整理

- [ ] 既存 smoke テストに `@pytest.mark.smoke` を付与。
- [ ] `pytest.ini` の `markers` セクションに `smoke` マーカーを追加（現在は `demo_tachibana`・`tk_smoke`・`demo_kabu` のみ）。
- [ ] `conftest.py` に `--smoke` 未指定時スキップを実装。
- [ ] `.github/workflows/python-tests.yml`（既存 CI ワークフロー）を確認し、通常ジョブに `pytest`（引数なし）を設定。nightly ジョブ（cron トリガーの追加が必要）に `pytest --smoke -x` を設定する。

**完了条件**: `pytest` (引数なし) が実プロセスを一切起動しない。nightly で全 smoke が PASS。

## 設計上の注意点

- `MockIPCServer` は `websockets` ライブラリ（エンジン本体と同じもの）を使う。`asyncio.BaseEventLoop` の新規起動はスレッドを汚染するため、`asyncio.run()` を専用スレッドで走らせる。`_stop` は `threading.Event` を使う（`asyncio.Event` はイベントループに紐づくため、メインスレッドで生成したオブジェクトを別スレッドの `asyncio.run()` ループから `set()` / `wait()` するとループ違いで race condition が発生する）。asyncio ループ内での stop 待ちは `await asyncio.get_running_loop().run_in_executor(None, self._stop.wait)` で行う。`_ready` は既に `threading.Event` を使っており、`_stop` も同じパターンに揃える。
- `script` のマッチングは `{"on": <type>}` の最初のヒットを返す。順序依存が必要なテストは `script` をリスト先頭から消費するモードを別途実装する（フェーズ S2 で判断）。
- `MockIPCServer` は IPC プロトコルの**シミュレーター**であり、`NautilusRunner` や取引所 API には一切触れない。Nautilus 内部ロジックを検証したい場合は引き続き `force_mode="inprocess"` + `MagicMock` を使う。
- smoke テストは「プロセスが正常に起動し、最初のイベントを返すこと」を最小限に確認するだけに絞る。取引所 API 呼び出しは nightly の別ジョブに委ねる。

## 完了条件（全体）

1. `pytest -x` (引数なし) が実プロセス起動ゼロで **60 秒以内**に完走する。
2. IPC ハンドシェイク・`ConnectionRefusedError` フォールバック・スキーマ不一致の各経路が `MockIPCServer` でカバーされている。
3. `_resolve_endpoint_and_token()` の attach 解決 3 分岐（正常 attach / stale pid / session-file なし）が `MockIPCServer` でカバーされている（S4 完了条件）。
4. `pytest --smoke -x` で既存 smoke テストが引き続き PASS する。

## gRPC 移行時の注意（Stage D / G3）

`ipc-grpc-migration.md` の G3 フェーズ（WebSocket トランスポート廃止）到達時に、
本 `MockIPCServer` を gRPC ベース（`grpcio` テストサーバー）に書き直す必要がある。
WebSocket 版の `MockIPCServer` は G3 で廃止される `server.py`（WebSocket）と同じプロトコル層に依存しているため。
G3 着手前に本ドキュメントを別の計画書（`🔵plan-test-mock-grpc-server.md` 等）として分岐させることを検討する。
