# Engine Discovery: ロックファイルベースの既存エンジン自動検出

## 概要

Rust アプリケーション起動時に、既に実行中の Python データエンジンを自動検出し、
新たにプロセスを spawn せずに既存エンジンに接続する仕組み。

## 問題

現在の仕様：
- マネージドモード（`--data-engine-url` なし）では、Rust は常に Python エンジンを spawn する
- 開発時に Python を手動起動する場合、`--data-engine-url` フラグが必須

改善点：
- Python エンジンが既に実行中なら、自動的にそちらを利用する
- フラグなしで複数の起動パターンに対応できる
- 1対1制約は変わらない（既存の単一クライアント制限を維持）

---

## 実装: ロックファイル方式

### ロックファイルの場所

```
%APPDATA%\flowsurface\engine.lock
```

既存の `saved-state.json` と同じディレクトリ（ユーザー専用）に配置。

### ロックファイル内容

```json
{
  "port": 19876,
  "token": "rD9Ff2mK8xQ+WjL/vN0oPq5TyZ1AcE3BgHjRs7UwV4M=",
  "pid": 12345,
  "started_at": "2026-04-29T15:30:45.123Z"
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `port` | u16 | Python エンジンが listen しているポート番号 |
| `token` | string | WebSocket 接続用のランダムトークン（base64） |
| `pid` | u32 | Python エンジンプロセスの PID |
| `started_at` | string | エンジン起動時刻（ISO8601）。デバッグ用 |

### セキュリティ考慮

- **ファイルパーミッション**: ロックファイルは `%APPDATA%\flowsurface\` に配置され、OS が自動的にユーザー専用にする。
- **トークン秘匿**: トークンはハンドシェイク時に `hmac.compare_digest()` で検証される（タイミング攻撃対策）。
- **外部プロセスからのアクセス**: ファイルの読み取り権限がないと、ポート・トークンを知ることはできない。

---

## Rust 側の動作フロー

```
Rust 起動時
  ├─ --data-engine-url が指定されている
  │   └─ 外部エンジンに直接接続（既存動作。以下のフロー不使用）
  │
  └─ --data-engine-url なし（マネージドモード）
      ├─ engine.lock が存在する？
      │   ├─ YES
      │   │   ├─ JSON をパース → port, token, pid を取得
      │   │   ├─ pid は生きているか？（プロセス存在確認）
      │   │   │   ├─ YES
      │   │   │   │   ├─ 127.0.0.1:{port} へ接続を試みる（タイムアウト: 2秒）
      │   │   │   │   ├─ 接続成功
      │   │   │   │   │   ├─ Hello ハンドシェイク実行（token を使用）
      │   │   │   │   │   ├─ 成功 → Ready 受領
      │   │   │   │   │   │   └─ spawn をスキップ、接続を返す（ここで完了）
      │   │   │   │   │   └─ ハンドシェイク失敗（スキーマ不一致など）
      │   │   │   │   │       └─ ロックファイル削除 → spawn へ
      │   │   │   │   └─ 接続失敗（タイムアウト or コネクション拒否）
      │   │   │   │       ├─ ロックファイル削除（既存エンジン が起動していない）
      │   │   │   │       └─ 通常 spawn フロー へ進む
      │   │   │   └─ NO（pid のプロセスが存在しない）
      │   │   │       ├─ ロックファイル削除
      │   │   │       └─ 通常 spawn フロー へ進む
      │   │   └─ NO（JSON パースエラー等）
      │   │       ├─ ログに警告を出力
      │   │       ├─ ロックファイル削除（破損ファイル）
      │   │       └─ 通常 spawn フロー へ進む
      │   │
      │   └─ NO（ロックファイルなし）
      │       └─ 通常 spawn フロー へ進む
      │
      └─ 通常 spawn フロー
          ├─ 127.0.0.1 上で空きポートを選択（OS が自動割り当て）
          ├─ Python プロセスを spawn
          │   ├─ stdin に JSON を送信: { "port": port, "token": token, "dev_tachibana_login_allowed": ... }
          │   └─ stdin を閉じる
          │
          ├─ ハンドシェイク実行（Hello → Ready）
          │   ├─ timeout: 10秒
          │   └─ 成功時に Python 側がロックファイルを書く
          │
          └─ 接続確立 → 通常の運用へ
```

---

## Python 側の動作フロー

### 起動時

1. `__main__.py` でサーバ起動（`DataEngineServer.run()` で port が確定）
2. `atexit.register(cleanup_lock_file)` でシグナルハンドラを登録
3. ロックファイルの内容を構築：
   ```python
   lock_data = {
       "port": port,
       "token": token,  # Rust から stdin で受け取ったトークン
       "pid": os.getpid(),
       "started_at": datetime.utcnow().isoformat() + "Z",
   }
   ```
4. ロックファイルに書き込み
5. ログ出力: `Engine started. Lock file: %APPDATA%\flowsurface\engine.lock`

### 既存ロックファイルが存在する場合

```python
if lock_file_exists:
    existing_data = json.loads(lock_file)
    existing_pid = existing_data["pid"]
    
    if process_alive(existing_pid):
        # プロセスが生きている
        logger.warning(f"Engine already running (PID {existing_pid}). "
                       "Will be replaced if new client connects.")
        # そのまま起動を続ける。後から接続した Rust が古い接続を追い出す
    else:
        # 古いプロセスは死んでいる。上書きして続行
        logger.info(f"Cleaning up stale lock (PID {existing_pid} not found)")
```

### 終了時

`atexit` ハンドラが自動実行：
```python
def cleanup_lock_file():
    try:
        lock_file_path.unlink()  # ファイル削除
    except FileNotFoundError:
        pass
```

シグナル（SIGTERM など）で停止された場合も自動削除される。

---

## トークンの流れ

### フロー1: Python を spawn する場合（マネージドモード）

```
Rust 生成 (engine-client/src/process.rs)
  ├─ ランダムトークン生成（32 byte base64）
  ├─ stdin JSON で Python に送信
  │
  └─ Python 受信 (python/engine/__main__.py)
      ├─ stdin JSON をパース
      ├─ token を取得
      ├─ ロックファイルに token を記録
      │
      └─ Rust が再度起動された場合（前の Rust がクラッシュ）
          ├─ ロックファイルから token を読み取り
          ├─ Hello ハンドシェイクで同じ token を使用
          └─ Python が token を検証 → 接続承認 → 先行接続を追い出す
```

### フロー2: 既存エンジンに接続する場合（Python 手動起動）

```
Python 手動起動
  ├─ uv run python -m engine --port 19876 --token dev-token
  ├─ ロックファイルに port + token を記録
  │
  └─ Rust 起動（--data-engine-url なし）
      ├─ ロックファイル読み込み
      ├─ ハンドシェイク実行（token を使用）
      └─ 接続成功
```

> **注**: Python 手動起動時の `--port` / `--token` フラグの実装は、
> 別の計画（Python 側の CLI インターフェース）で決定する。

---

## 1対1制約の維持

- **Python 側**: 既存の `_current_conn` 管理（単一クライアント制限）は変わらない。
  新しいクライアントが接続すると、古いクライアントに `Error{code: "superseded"}` を送って切断する。

- **Rust 側**: ロックファイル経由で既存エンジンに接続しても、基本動作は変わらない。
  複数の Rust が同時に起動してロックファイルを読んだ場合、後から接続した Rust が先行を追い出す。

---

## テスト方針

### Rust テスト

```rust
#[cfg(test)]
mod lock_file_tests {
    // 1. ロックファイルの読み書き
    #[test]
    fn test_lock_file_write_read() { }
    
    // 2. 有効な PID チェック
    #[test]
    fn test_is_alive_valid_process() { }
    
    #[test]
    fn test_is_alive_invalid_process() { }
    
    // 3. 既存エンジンへの接続試行
    #[test]
    fn test_connect_existing_engine_success() { }
    
    #[test]
    fn test_connect_existing_engine_timeout() { }
    
    // 4. spawn へのフォールバック
    #[test]
    fn test_fallback_to_spawn_on_lock_error() { }
}
```

### Python テスト

```python
def test_lock_file_written_on_startup():
    """起動時にロックファイルが書かれることを確認"""

def test_lock_file_deleted_on_exit():
    """終了時にロックファイルが削除されることを確認"""

def test_lock_file_stale_process():
    """古いプロセスのロックファイルが存在する場合の動作"""

def test_handshake_with_token_from_lock_file():
    """ロックファイルから読み取ったトークンでハンドシェイク成功"""
```

### E2E テスト

1. **手動起動 + Rust 接続**:
   ```bash
   # Terminal A: Python を手動起動
   uv run python -m engine --port 19876 --token dev-token
   
   # Terminal B: Rust 起動（--data-engine-url なし）
   cargo run -- --mode live
   
   # ログ確認: "Connecting to existing engine at 127.0.0.1:19876"
   ```

2. **ロックファイル削除テスト**:
   ```bash
   # Python プロセスを kill してから Rust を起動
   # → spawn が動作することを確認
   ```

---

## トラブルシューティング

### ロックファイルが残った状態で Rust を起動した場合

Rust は PID チェックで「プロセスが生きていない」ことを検出 → 
ロックファイル削除 → 通常 spawn フロー へ進む。

### ロックファイルが破損していた場合

JSON パースに失敗 → ログに警告を出力 → ロックファイル削除 → spawn へ進む。

### トークンが不一致の場合

Python の Hello ハンドシェイク時に `hmac.compare_digest()` で検証失敗 →
`EngineError` を返す → Rust が接続失敗と判定 → ロックファイル削除 → spawn へ進む。

### Rust が同時に複数起動した場合

- ロックファイルはすべての Rust が読める（OS ファイルシステムの読み取り競合は許容）
- Python へ接続した順序で「先行接続が追い出される」（既存設計を維持）
- 後から接続した Rust が生き残る

---

## 今後の拡張

### Python 手動起動時の CLI インターフェース

当面は `uv run python -m engine` でサーバ起動のみ。
将来、以下を検討：

- `--port 19876` で固定ポート指定
- `--token dev-token` でトークン指定
- `--lockfile-path` でロックファイルのカスタムパス指定

（フェーズ 6 以降の課題）

### Unix Domain Socket / Named Pipe への切り替え

フェーズ 3 で IPC レイテンシが critical だと判明した場合、
loopback WebSocket を UDS/Named Pipe に切り替え。その際、
ロックファイルの代わりに socket ファイルそのものを発見メカニズムとする。

---

## 実装チェックリスト

- [ ] `engine-client/src/lock_file.rs` 実装
- [ ] `python/engine/__main__.py` にロックファイル書き込みを追加
- [ ] `engine-client/src/process.rs` に既存エンジン検出ロジックを追加
- [ ] spec.md を更新（§3, §4.1.1）
- [ ] Rust テスト実装
- [ ] Python テスト実装
- [ ] E2E テスト実施（手動確認）
