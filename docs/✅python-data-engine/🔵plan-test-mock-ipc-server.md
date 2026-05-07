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

`script` は `[{"on": "Hello", "reply": [{"event": "Ready", ...}]}, ...]` のような宣言的リストとし、テストが期待する IPC 応答シーケンスを明示できる形にする。

> **wire literal 参照先**: `event` フィールドの値（`"Ready"`, `"ClientConnected"`, `"EngineError"`, `"DepthSnapshot"` 等）は `python/engine/schemas.py` の各クラスの `event: Literal[...]` フィールドが canonical であり、`op` フィールド（コマンド側: `"Hello"`, `"Subscribe"` 等）も同ファイルの `op: Literal[...]` が canonical である。mock script 例はすべてこれらの値に合わせること。

### 既存 `ReplaySession` / `LiveSession` の拡張

`ReplaySession` は `attach_endpoint` 引数で endpoint を直接指定できる。`MockIPCServer` が発行したポートを `f"ws://127.0.0.1:{srv.port}/"` 形式で渡し、token は `FLOWSURFACE_ENGINE_TOKEN` 環境変数で渡す。テスト終了後は環境変数を必ず `del` すること（他テストへの汚染防止）。

テスト側:

```python
def test_handshake_schema_mismatch(mock_ipc_server_factory):
    script = [
        {"on": "Hello", "reply": [
            {"event": "ClientConnected", "count": 1},
            {"event": "EngineError", "code": "schema_mismatch", "message": "SCHEMA_MAJOR_MISMATCH"}
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

#### S4-A: 明示 attach_endpoint 指定（直接接続）

- `attach_endpoint=f"ws://127.0.0.1:{srv.port}/"` を直接指定し、`FLOWSURFACE_ENGINE_TOKEN` を env var で渡す。
- `_resolve_endpoint_and_token()` が session-file を読まず直接 endpoint を返すことを確認。
- **期待**: attach 成功、session-file 読み込みが発生しない。

#### S4-B: session-file 経由の attach（正常 pid + MockIPCServer）

- `engine-session.json` に生きているプロセスの pid と `MockIPCServer` のポートを書き込む。
- `_resolve_endpoint_and_token()` が session-file を読んで `MockIPCServer` へ attach することを確認。
- `engine-session.json` の書き込みは `tmp_path` fixture + `monkeypatch.setattr(replay_session, "_resolve_session_file_path", lambda: tmp_path / "engine-session.json")` で行い、実環境の `user_data_dir()` への書き込みを防ぐ。
- **期待**: attach 成功、inprocess フォールバックへ進まない。

#### S4-C: stale pid（プロセス死亡済み）→ フォールバック

- `engine-session.json` に存在しない pid（例: `99999999`）と dummy port を書き込む（`tmp_path` + `monkeypatch`）。
- `_read_session_file()` が pid の死亡を検出して `(None, None)` または probe fallback を返す動作を確認。
- **期待**: stale pid を読み捨てて probe fallback または inprocess fallback へ遷移する。

#### S4-D: session-file なし + env-only（token 設定済み）

- `engine-session.json` が存在しない状態で `FLOWSURFACE_ENGINE_TOKEN` のみ設定して呼び出す。
- **期待**: session-file なしを検知し、env-only パス（token のみ返却）またはフォールバックへ遷移する（実装に合わせてアサートを調整）。

#### 各シナリオ共通の注意点

- `engine-session.json` の書き込みは **`tmp_path` fixture + `monkeypatch.setattr(replay_session, "_resolve_session_file_path", lambda: tmp_path / "engine-session.json")`** を使用し、実環境の `platformdirs.user_data_dir()` への書き込みを防ぐ（並列テスト実行時の競合防止）。
- テスト終了後は `monkeypatch` が自動でクリーンアップするため手動削除は不要。環境変数 `FLOWSURFACE_ENGINE_TOKEN` は `finally` で `del` するか `monkeypatch.delenv` を使うこと。
- **既存テストとの棲み分け**: `test_live_session_kabu.py` 等の既存 attach テストは実プロセスに依存しており `@pytest.mark.smoke` で管理する。S4 のテストは `MockIPCServer` を使うことで**実プロセス不要**、かつ **1 秒以内**に同じ分岐を検証できる。

**完了条件**: `pytest python/tests/test_mock_ipc_server_attach.py` が 1 秒以内に PASS。`_resolve_endpoint_and_token()` の S4-A / S4-B / S4-C / S4-D の **4 分岐**すべてがカバーされていること（旧: 3分岐）。

### フェーズ S5: depth bootstrap / continuity（新規）

attach 後の板情報連続性を MockIPCServer で検証する。実プロセス不要で DepthSnapshot → DepthDiff の continuity 経路と、gap 検出後の再要求経路をカバーする。

#### S5-A: DepthSnapshot → DepthDiff continuity（同一 stream_session_id）

MockIPCServer が以下のシーケンスを送出するシナリオを定義する:

```python
script = [
    {"on": "Hello", "reply": [
        {"event": "Ready", "schema_major": SCHEMA_MAJOR, "schema_minor": SCHEMA_MINOR,
         "engine_version": "test", "engine_session_id": str(uuid4()), "capabilities": {}}
    ]},
    {"on": "Subscribe", "reply": [
        {"event": "DepthSnapshot",
         "request_id": None,
         "venue": "binance", "ticker": "BTCUSDT", "market": "spot",
         "stream_session_id": "sess-1", "sequence_id": 100,
         "bids": [{"price": "30000", "qty": "1.0"}],
         "asks": [{"price": "30001", "qty": "0.5"}]},
        {"event": "DepthDiff",
         "venue": "binance", "ticker": "BTCUSDT", "market": "spot",
         "stream_session_id": "sess-1",
         "sequence_id": 101, "prev_sequence_id": 100,
         "bids": [{"price": "30000", "qty": "1.5"}],
         "asks": []}
    ]}
]
```

- **期待**: クライアントが DepthSnapshot (seq=100) を受け取り、DepthDiff (seq=101, prev=100) で更新を適用できること。
- `stream_session_id` が両メッセージで一致していることを検証する。

#### S5-B: stream_session_id 変化（attach reconnect 相当）

`stream_session_id` が DepthSnapshot と DepthDiff で異なる場合の挙動を検証する:

```python
# DepthSnapshot: stream_session_id="sess-1"
# DepthDiff:     stream_session_id="sess-2"  ← セッション変更
```

- **期待**: クライアント実装が `stream_session_id` の不一致を検出し、`DepthGap` event（`{"event": "DepthGap", "stream_session_id": "sess-2", ...}`）を受け取るか、または RequestDepthSnapshot を再送する経路をとること。
- 注記: `DepthGap` の wire literal は `schemas.py` の `DepthGap` クラス（`event: Literal["DepthGap"]`）が canonical である。

#### S5-C: gap 検出後の RequestDepthSnapshot 再送（実 protocol path 1 本確保）

MockIPCServer が `DepthGap` を送出し、クライアントが `RequestDepthSnapshot` を送り返すシーケンスを検証する:

```python
script = [
    {"on": "Hello",   "reply": [{"event": "Ready", ...}]},
    {"on": "Subscribe", "reply": [
        {"event": "DepthGap",
         "venue": "binance", "ticker": "BTCUSDT", "market": "spot",
         "stream_session_id": "sess-broken"}
    ]},
    {"on": "RequestDepthSnapshot", "reply": [
        {"event": "DepthSnapshot",
         "request_id": None,  # MockIPCServer は request_id フィールドを検証しない（None で OK）
         "venue": "binance", "ticker": "BTCUSDT", "market": "spot",
         "stream_session_id": "sess-2", "sequence_id": 200,
         "bids": [], "asks": []}
    ]}
]
```

- **期待**: `DepthGap` 受信後にクライアントが `{"op": "RequestDepthSnapshot", ...}` を送出し、MockIPCServer が新しい DepthSnapshot を返すこと。
- **注記**: `RequestDepthSnapshot` の `op` literal は `schemas.py` `RequestDepthSnapshot` クラス（`op: Literal["RequestDepthSnapshot"]`）が canonical である。この経路は「gap → 再取得」の実 protocol path を 1 本確保する最小テストであり、リトライ回数・バックオフ等の詳細は実装に委ねる。
- **request_id マッチング**: MockIPCServer はこの script 例では request_id を検証しない（`None` を返す）。実際の Rust クライアントが `request_id` を一致確認するかどうかは実装に委ねる。厳密なマッチングが必要な場合は MockIPCServer の script エンジンに `on` フィールドの request_id パターンマッチ機能を追加すること（S2 フェーズで判断）。

#### S5 共通の注意点

- wire literal（`event` / `op` フィールド値）はすべて `python/engine/schemas.py` の Literal 定義と一致させること。
- `stream_session_id` は文字列 UUID などを使い、テスト間で衝突しないよう `uuid.uuid4()` で生成することを推奨する。
- テスト終了後は MockIPCServer を `stop()` し、環境変数を `del` して汚染を防ぐ。

**完了条件**: `pytest python/tests/test_mock_ipc_server_depth.py` が 1 秒以内に PASS。S5-A（continuity 確認）・S5-B（stream_session_id 変化検出）・S5-C（gap 後再要求）の 3 シナリオがカバーされていること。

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
3. `_resolve_endpoint_and_token()` の attach 解決 4 分岐（明示 attach_endpoint / session-file 正常 pid / stale pid / env-only）が `MockIPCServer` でカバーされている（S4 完了条件）。
4. `pytest --smoke -x` で既存 smoke テストが引き続き PASS する。

## gRPC 移行時の注意（Stage D / G3）

`ipc-grpc-migration.md` の G3 フェーズ（WebSocket トランスポート廃止）到達時に、
本 `MockIPCServer` を gRPC ベース（`grpcio` テストサーバー）に書き直す必要がある。
WebSocket 版の `MockIPCServer` は G3 で廃止される `server.py`（WebSocket）と同じプロトコル層に依存しているため。
G3 着手前に本ドキュメントを別の計画書（`🔵plan-test-mock-grpc-server.md` 等）として分岐させることを検討する。
