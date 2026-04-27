# 立花証券: Python 完結型ファイル管理への変更計画

## 1. 背景と目的

現行 spec（architecture.md §2）は、クレデンシャル（user_id / password）を **Rust の OS keyring に保存し**、ログイン後の仮想 URL 5 種も keyring に保存した上で **`SetVenueCredentials` IPC で Python へ再注入する**設計になっている。

これを `e_api_login_tel.py` サンプルと同じように、**クレデンシャルもセッションも Python がファイルで管理し、Rust は一切関与しない**方式に変更する。

**ゴール**: 立花証券の認証・セッション管理を Python に完全に閉じる。`SetVenueCredentials` IPC コマンド・`VenueCredentialsRefreshed` イベント・Rust keyring コード・Wire DTO 群をすべて削除し、アーキテクチャを抜本的に簡素化する。

---

## 2. 変更前後の比較

### 変更前（現行 spec）

```
起動時
  Rust: keyring → user_id / password / session 読込
  Rust: SetVenueCredentials（creds + session）→ Python
  Python: session を validate
  Python: 失敗 → 再ログイン → VenueCredentialsRefreshed → Rust → keyring 更新

再起動時
  Rust: keyring → 読込 → SetVenueCredentials 再送
```

- Rust が creds / session の "source of truth"
- `SetVenueCredentials` IPC コマンドが必要
- `VenueCredentialsRefreshed` IPC イベントが必要（Python → Rust の逆送）
- `data/src/config/tachibana.rs` に keyring 操作コードが必要
- Wire DTO の 2 層構造が必要

### 変更後（Python 完結方式）

```
起動時
  Python: tachibana_account.json を読む
    ├─ なし → tkinter ログインダイアログ → 入力内容を tachibana_account.json に保存
    └─ あり → そのまま使う
  Python: tachibana_session.json を読む
    ├─ 有効な session あり → そのまま使う（再ログインなし）
    └─ なし / 無効 → tachibana_account.json の creds でログイン → session を保存

再起動時
  Python: 上記フローを繰り返す（Rust 関与ゼロ）
```

- Python が creds / session の "source of truth"
- `SetVenueCredentials` IPC コマンド削除
- `VenueCredentialsRefreshed` IPC イベント削除
- Rust 側の `data/src/config/tachibana.rs` 削除
- Wire DTO 群（`TachibanaCredentialsWire` / `TachibanaSessionWire` 等）削除

---

## 3. Python 側の変更

### 3.1 新設ファイル: `tachibana_file_store.py`

サンプルの `e_api_account_info.txt` + `e_api_login_response.txt` に相当する 2 ファイルを管理する（`e_api_info_p_no.txt` に相当する p_no 永続化は行わない）。

```python
# python/engine/exchanges/tachibana_file_store.py

ACCOUNT_FILENAME  = "tachibana_account.json"   # user_id / is_demo（password は保存しない）
SESSION_FILENAME  = "tachibana_session.json"   # 仮想 URL 5 種 + saved_at_ms

def save_account(config_dir: Path, user_id: str, is_demo: bool) -> None: ...
def load_account(config_dir: Path) -> dict | None: ...

def save_session(cache_dir: Path, session: TachibanaSession) -> None: ...
def load_session(cache_dir: Path) -> TachibanaSession | None: ...

# p_no は PNoCounter（Unix seconds 初期化）で管理し続ける。
# ファイルに保存して 1 にリセットすると R4 単調増加 invariant が壊れる。
```

**password はファイルに保存しない。** tkinter ダイアログで毎回入力させるか、`DEV_TACHIBANA_PASSWORD` env（debug ビルドのみ）で供給する。

保存内容:

```json
// tachibana_account.json
{ "user_id": "12345678", "is_demo": true }

// tachibana_session.json
{
  "url_request":      "https://demo-kabuka.e-shiten.jp/e_api_v4r8/xxxxxx/",
  "url_master":       "...",
  "url_price":        "...",
  "url_event":        "...",
  "url_event_ws":     "...",
  "zyoutoeki_kazei_c": "1",
  "saved_at_ms":      1745712000000
}

// tachibana_p_no.json は作成しない（p_no は PNoCounter が管理する）
```

`saved_at_ms` で当日判定する（JST 15:30 以降に保存 → 翌日朝に失効）。ファイルが壊れている場合は `None` を返す（例外を飲み込む）。ファイル書込みは `tempfile` + `os.replace` でアトミックに行う（Windows/Unix 両対応）。

### 3.2 `tachibana_login_flow.py` の変更

`SetVenueCredentials` IPC 受信を起点とするフローを廃止し、Python 自身が startup 時に呼ぶフローに変更する:

```python
async def startup_login(config_dir, cache_dir):
    """アプリ起動時に呼ぶ。creds とセッションをファイルから復元し、
    必要なら tkinter ログインダイアログを表示してログインする。"""

    # 1. セッションファイルを確認（`_is_session_fresh` は T-SC1 で新設）
    session = load_session(cache_dir)
    if session and _is_session_fresh(session):
        # 時刻チェックだけでは壊れた / 営業日外で失効した URL を誤受理するため、
        # 現行の validate_session_on_startup（CLMMfdsGetIssueDetail ping）で API 疎通確認する
        try:
            await validate_session_on_startup(session)
            return session                      # 再ログインなし
        except (LoginError, SessionExpiredError):
            pass                                # 失効済み → 以下で再ログイン

    # 2. アカウント情報ファイルを確認（`_is_session_fresh` は T-SC1 で新設）
    account = load_account(config_dir)
    if account:
        user_id = account["user_id"]
        is_demo = account["is_demo"]
    else:
        user_id, is_demo = None, True

    # 3. password は tkinter ダイアログで取得（env fast-path あり）
    result = await _spawn_login_dialog(prefill={"user_id": user_id, "is_demo": is_demo})
    if result["status"] == "cancelled":
        raise LoginCancelled()

    # 4. ログイン実行
    session = await login(result["user_id"], result["password"], result["is_demo"])

    # 5. 成功 → ファイルに保存（password は保存しない）
    save_account(config_dir, result["user_id"], result["is_demo"])
    save_session(cache_dir, session)
    # p_no は PNoCounter（Unix seconds 初期化）を使い続ける。
    # ファイルに保存して 1 にリセットすると R4 単調増加 invariant を壊すため、ここでは操作しない。
    return session
```

### 3.3 `server.py` の変更（実装主体）

> **注意**: ログイン orchestration と IPC 配線の実体は `tachibana.py` ではなく `python/engine/server.py` にある。具体的には `_do_set_venue_credentials`（L1564）・`_do_request_venue_login`・`_apply_tachibana_session`（L1784）が実体メソッドであり、これらを置き換える。

`_do_set_venue_credentials` を廃止し、エンジン起動時に Python 自身が `startup_login` を呼ぶフローに変更:

```python
# server.py の起動フロー（SetVenueCredentials 待ちを廃止）
async def _startup_tachibana(self):
    # IPC コマンド待ちではなく、自発的に起動フローを開始する
    session = await startup_login(self._config_dir, self._cache_dir)
    self._apply_tachibana_session(session)
    self._emit({"event": "VenueReady", "venue": "tachibana", "request_id": ...})
```

`VenueCredentialsRefreshed` 送信を削除。`_do_request_venue_login` 受信時はセッションファイルをクリアして `startup_login` を再実行する（`_apply_tachibana_session` で state と worker 両方を更新する既存の関数を使い続ける）。

**二重起動防止**: `startup_login` 実行中フラグ（`_login_in_flight: bool`）を保持し、`LoginInFlight` 状態での `RequestVenueLogin` は無視または `VenueLoginStarted` 再送のみを行う。

**`LoginCancelled` 後の遷移**: `LoginCancelled` を catch した場合は `VenueLoginCancelled{venue, request_id}` イベントを Python → Rust へ送信し `VenueState::Idle` へ遷移する。

**再ログイン時のセッション無効化**: `_do_request_venue_login` を受信したとき、`startup_login` を再実行する**前に** `self._tachibana_session = None` かつ `self._workers["tachibana"].set_session(None)` を呼んでセッションを即座に破棄する。これにより、Rust が `LoginInFlight` と認識している間に Python が旧 session で立花 API を叩く状態ズレを防ぐ。セッションファイルのクリアも同タイミングで行う。

**起動同期プロトコル（Rust 側 `apply_after_handshake_with_timeout`）**: `SetVenueCredentials` 削除後は `engine-client/src/process.rs` の `apply_after_handshake_with_timeout` を以下のように書き換える:
- **Step 3 削除**: `SetVenueCredentials` 送信ループ全体を削除する。Python が handshake 後に自律的に `_startup_tachibana` を開始する。
- **Step 4 変更**: `request_id` ベースの待ちを廃止し、venue タグ `"tachibana"` に対する `VenueReady` / `VenueError` を `venue_ready_timeout`（60 秒）以内に venue 文字列で待つ（`request_id` 不要）。Rust は早期 subscribe（Step 1）を維持しているため、Python が `VenueReady` を送るより前に届いたイベントを失わない。timeout した場合は既存の timeout エラーハンドリング（`VenueState::Error` 遷移）を使う。

---

## 4. Rust 側の削減

| 削除するもの | 理由 |
|---|---|
| `data/src/config/tachibana.rs` | Python が creds / session を自前管理するため全削除 |
| `TachibanaCredentials` struct（`data` クレート） | Rust が creds を持つ必要がなくなる |
| `TachibanaSession` struct（`data` クレート） | 同上 |
| `TachibanaCredentialsWire` struct（`engine-client` クレート） | IPC で creds を送らなくなる |
| `TachibanaSessionWire` struct（`engine-client` クレート） | IPC で session を送らなくなる |
| `VenueCredentialsPayload` enum | `SetVenueCredentials` ごと削除 |
| `Command::SetVenueCredentials` | Python が自発的に起動するため不要 |
| `EngineEvent::VenueCredentialsRefreshed` | Python → Rust の逆送が不要 |
| `ProcessManager` の credentials 保持・再注入ロジック | Python が再起動後も自前でファイルから復元 |
| `secrecy` / `zeroize` クレートの立花向け利用箇所 | Wire DTO が消えるため用途がなくなる |
| `data::wire::tachibana` モジュール（`TachibanaCredentialsWire` / `TachibanaSessionWire` の `data` 側定義） | `data` クレートにも Wire DTO が存在 |
| `data/tests/tachibana_keyring_roundtrip.rs` | keyring ラウンドトリップテスト、全削除 |

**残るもの**（Rust は状態管理と UI のみ）:

```rust
// EngineEvent（立花関連で残すもの）
VenueReady          { venue, request_id }
VenueError          { venue, request_id, code, message }
VenueLoginStarted   { venue, request_id }
VenueLoginCancelled { venue, request_id }

// Command（立花関連で残すもの）
RequestVenueLogin   { request_id, venue }   // ユーザーが「再ログイン」を押した時のみ
```

Rust は「ログイン状態の FSM（`VenueState`）」と「再ログインボタンの表示制御」だけを担う。creds / session は一切保持しない。

> **注記**: `spec.md §2.1` の `VenueState` 記述（`Idle/LoginInFlight/Ready/Error`）は変更なし。keyring 関連記述のみが本文書 §4 で上書きされる。

---

## 5. IPC の変更

### 削除するコマンド・イベント

```
Command::SetVenueCredentials       ← 削除
EngineEvent::VenueCredentialsRefreshed  ← 削除
```

### 残るコマンド（立花関連）

```
Command::RequestVenueLogin { request_id, venue }
```

ユーザーが「立花にログイン」ボタンを押したとき Rust → Python へ送る。Python はセッションファイルをクリアし `startup_login` を再実行する。

`schema_major` を bump する。`SetVenueCredentials` / `VenueCredentialsRefreshed` の削除は optional field 追加ではなく wire protocol の破壊的変更であり、旧 Rust / 新 Python（またはその逆）の組み合わせがハンドシェイク後に無言で壊れる。major のみ検査する設計上、major を上げることで接続拒否が保証される。`engine-client/src/lib.rs` と `python/engine/schemas.py` の両方で `SCHEMA_MAJOR` を同期更新すること。

---

## 6. セキュリティ考慮

| 項目 | 評価 |
|---|---|
| 仮想 URL がファイルに残る | △ plaintext。ただし 1 日券であり夜間閉局後は無効 |
| user_id がファイルに残る | △ plaintext。ただし公開情報に近い識別子 |
| password はファイルに書かない | ◎ tkinter ダイアログ入力 or debug env のみ |
| ファイルの所在 | `config_dir` / `cache_dir`（OS ユーザーディレクトリ配下） |
| 他プロセスからの読み取り | OS ファイルパーミッションに依存（Unix: 600 推奨、Windows: ユーザープロファイル） |
| p_no ファイル | 非機密（シーケンス番号のみ） |

**許容できる理由**: 仮想 URL は夜間閉局で自動失効する 1 日券。password という「真の機密」はファイルに出さない設計を維持する。

---

## 7. 実装タスク

### T-SC1: `tachibana_file_store.py` 新設

- `save_account` / `load_account`（user_id + is_demo のみ、password は保存しない）
- `save_session` / `load_session`（p_no はファイル永続化しない — PNoCounter が Unix seconds で管理するため R4 単調増加 invariant を維持できる）
- `_is_session_fresh(session)`: JST 当日 15:30 未満に保存されたものを有効判定
  - 境界値は `< 15:30:00 JST` を有効とし、`>= 15:30:00 JST` は無効（境界は閉で無効側）
  - `saved_at_ms > now_ms`（クロックスキューで保存時刻が未来になる場合）はセッションを**無効**扱いにする（保守的対処）
- ファイルが壊れている場合は `None` を返す（例外を飲み込む）
- ファイル書込みは `tempfile` + `os.replace` でアトミックに行う（`os.rename` ではなく `os.replace` を使用する。POSIX と Windows の両方で既存ファイルへの上書きが保証される）

### T-SC2: `tachibana_login_flow.py` に `startup_login` 追加

- `load_session` → `_is_session_fresh` で時刻確認 → 通過したら `validate_session_on_startup`（CLMMfdsGetIssueDetail ping）で API 疎通確認 → 成功なら返す（再ログインなし）
- API validate 失敗（`LoginError` / `SessionExpiredError`）→ 以下のログインフローへ
- `load_account` → user_id を prefill してダイアログ表示
- ログイン成功 → `save_account` + `save_session`（password は保存しない。p_no は PNoCounter のまま）
- ネットワーク例外（タイムアウト・接続失敗等）は catch して `VenueError{venue, request_id, code:"login_failed", message:...}` を送出し `VenueState::Error` へ遷移する
- `startup_login` 内の `DEV_TACHIBANA_PASSWORD` 読み取りは `dev_tachibana_login_allowed` フラグが `True` の場合のみ有効（`spec.md §3.1 F-DevEnv-Release-Guard` 参照）

### T-SC3: `server.py` の起動フロー変更（実装主体は tachibana.py ではなく server.py）

- `server.py:_do_set_venue_credentials`（L1564）を廃止し、`_startup_tachibana` 相当の自律起動メソッドに置き換える
- `server.py:_do_request_venue_login` はセッションファイルをクリアして `startup_login` を再実行するよう変更する。**再実行前に** `self._tachibana_session = None` と `self._workers["tachibana"].set_session(None)` を呼んで in-memory session を即座に無効化する（§3.3「再ログイン時のセッション無効化」参照）
- `server.py:_apply_tachibana_session`（L1784）は引き続き使用する（server state と worker の両方を同期する既存ロジックを維持）
- `VenueCredentialsRefreshed` 送信を削除
- `startup_login` 実行中フラグ（`self._tachibana_login_inflight`）を保持し、`LoginInFlight` 状態での `RequestVenueLogin` は無視または `VenueLoginStarted` 再送のみを行う（`server.py` では `_tachibana_login_inflight` が既に存在する）
- `LoginCancelled` を catch した場合は `VenueLoginCancelled{venue, request_id}` イベントを Python → Rust へ送信し `VenueState::Idle` へ遷移する

### T-SC3.5: `config_dir` の Python 側 plumbing（T-SC1 の前提）

現状、`python/engine/__main__.py` は stdin payload から `config_dir` を読む（`_parse_stdin_config` L22: `"config_dir": str | None`）が `_run()` 引数リストに含まれておらず `DataEngineServer.__init__` にも渡っていない（`cache_dir` のみ存在）。T-SC3 の `startup_login(self._config_dir, self._cache_dir)` 呼び出しには `self._config_dir` が必要なため先に plumbing する:

- `__main__.py` の `_run()` に `config_dir: str | None = None` 引数を追加し、`DataEngineServer(config_dir=Path(config_dir) if config_dir else None, ...)` で渡す
- `DataEngineServer.__init__` に `config_dir: Path | None = None` 引数を追加し `self._config_dir` として保持する
- デフォルト値: `Path.home() / ".config" / "flowsurface" / "engine"`（`cache_dir` の既存デフォルト `~/.cache/flowsurface/engine` と対称）
- `_parse_stdin_config()` の `config_dir` フィールドは既存（L22）→ Python 受け取りのみ追加で完結。Rust 側 stdin payload への変更が必要かは `build_stdin_payload` を確認して T-SC4 と合わせて判断する
- `--config-dir` CLI 引数も `__main__.py` の argparse に追加（dev mode で保存先を明示指定できるようにする）

### T-SC4: Rust 側の creds / session 関連コードを全削除

- `data/src/config/tachibana.rs` を削除
- `TachibanaCredentials` / `TachibanaSession` / `TachibanaCredentialsWire` / `TachibanaSessionWire` / `VenueCredentialsPayload` を削除
- `data::wire::tachibana` モジュール（`data` クレート側の Wire DTO 定義）を削除
- `data/tests/tachibana_keyring_roundtrip.rs` を削除
- `Command::SetVenueCredentials` variant を削除
- `EngineEvent::VenueCredentialsRefreshed` variant を削除
- `ProcessManager` の credentials 保持・再注入ロジックを削除
- `engine-client/tests/` 内の以下ファイルから `SetVenueCredentials` / `VenueCredentialsRefreshed` 関連テストを削除または代替テストに書き換え、`cargo test --workspace` が通ることを確認する:
  `process_lifecycle.rs`, `process_venue_ready_gate.rs`, `process_creds_refresh_hook.rs`, `process_creds_refresh_listener_singleton.rs`, `process_venue_login_cancelled.rs`, `process_venue_ready_timeout_marks_failed.rs`, `process_venue_error_session_restore_failed.rs`, `schema_v1_2_roundtrip.rs`, `venue_ready_idempotent.rs`
  - 例外: `schema_v1_2_roundtrip.rs` から `SetVenueCredentials` / `VenueCredentialsRefreshed` の test 関数のみ削除し、ファイル自体（v1.x 互換テスト）は維持する
- `secrecy` / `zeroize` の非 Tachibana 利用箇所がないことを確認し、利用がゼロであれば `data/Cargo.toml` と `engine-client/Cargo.toml` から依存を削除する
- `schema_major` bump（`SetVenueCredentials` / `VenueCredentialsRefreshed` の削除は破壊的変更のため minor ではなく major を上げる）
- `engine-client/src/process.rs` の `apply_after_handshake_with_timeout` を書き換える:
  - Step 3（`SetVenueCredentials` 送信ループ）を全削除する
  - Step 4 の `request_id` ベース待ちを、venue 文字列 `"tachibana"` で `VenueReady` / `VenueError` を待つ方式に変更する（詳細は §3.3「起動同期プロトコル」参照）
  - `ProcessManager` の `credentials_by_venue` 保持フィールドを削除する（Step 3 の送信元だったため）
- Rust 側 stdin payload（`build_stdin_payload`）に `config_dir` フィールドが未送信の場合は追加する（T-SC3.5 と協調）

### T-SC5: テスト追加

- `python/tests/test_tachibana_file_store.py`
  - 保存 → 読み込みラウンドトリップ
  - ファイル破損時に `None` を返すこと
  - `_is_session_fresh` の境界値: `freezegun.freeze_time` で JST 15:29:59 / 15:30:00 / 翌日 09:00 の 3 点を固定して assert する。`saved_at_ms > now_ms`（クロックスキュー）は無効扱いのテストも追加
  - アトミック書き込み（中断時に旧データが保全される）: tempfile への書き込み直後、`os.replace` を呼ぶ直前で例外を注入する。正しい原子性は「最終ファイル（`tachibana_session.json`）が旧内容のまま残る」であり「消える」ではない。`assert final_path.read_text() == original_content` で確認し、`.tmp` ファイルが残っていないことも `assert not tmp_path.exists()` で確認する
  - `test_tachibana_file_store.py` は特別なマーカー不要。既存 `python-tests.yml` の `uv run pytest python/tests/` で自動収集される
- `python/tests/test_tachibana_login_flow.py` の `startup_login` ケース追加
  - セッションキャッシュあり → ダイアログを開かない
  - キャッシュなし → ダイアログを開く → ファイルに保存
  - `_spawn_login_dialog` を `unittest.mock.AsyncMock` で patch して headless 環境で実行可能にする。`tk_smoke` マーカーは不要。既存 `python-tests.yml` の通常ジョブに含まれる
  - `test_login_in_flight_ignores_request_venue_login`: `_login_in_flight=True` 中に `RequestVenueLogin` が届いても `startup_login` が再実行されないこと
  - `test_login_cancelled_sends_venue_login_cancelled`: `LoginCancelled` 時に `VenueLoginCancelled{venue, request_id}` が送信され `VenueState::Idle` へ遷移すること
  - `test_network_error_sends_venue_error_login_failed`: ネットワーク例外発生時に `VenueError{code:"login_failed"}` が送信されること
- `invariant-tests.md` へ以下の不変条件 ID を起票し、test 関数名と対応付ける:
  - `F-SC-NoPassword`: password をファイルに書かない（test: `test_save_account_excludes_password`）
  - `F-SC-Atomic`: アトミック書き込み（最終ファイルのみ残る）（test: `test_atomic_write_no_partial_file`）
  - `F-SC-FreshJST`: JST 当日 15:30 未満のみ有効（test: `test_is_session_fresh_boundary`）

### T-SC6: `architecture.md` / `spec.md` の更新 ✅

更新対象を以下に明示列挙する:

- `spec.md §3.2`: 本ファイルの方式に書き換え
- `spec.md §4.A1 受け入れ条件 1`（keyring 保存 → 復元の記述）をファイルキャッシュ方式に書き換え
- `architecture.md §1/§2/§3/§5/§8`:
  - `§2`「クレデンシャル受け渡しのプロトコル拡張」を本方式に書き換え
  - `§3` 起動シーケンスを更新
  - `§5` の Rust 側変更箇所表から keyring / SetVenueCredentials / `data/src/config/tachibana.rs`（新設）行を削除し、Python 自律起動方式に書き換え
  - `§8.3` の `SetVenueCredentials` / `VenueCredentialsRefreshed` 関連テストファイル名の記述を削除
- `spec.md §3.1` のセキュリティ要件（「Python 側はメモリのみ」→「ファイルキャッシュあり、password のみメモリ」に変更）

> **architecture.md への直接修正は T-SC6 まで先送り**。本文書の T-SC6 記述が architecture.md 更新の作業仕様を兼ねる。spec.md には暫定注記のみ（別エージェント担当）。

---

## 8. 依存関係

```
T-SC3.5                              (前提タスク、単独で先行実施)
T-SC3.5 → T-SC1 → T-SC2 → T-SC3   (Python 変更、直列)
T-SC4                                (Rust 変更、T-SC3 と並行可)
T-SC5                                (T-SC1〜T-SC3 完了後)
T-SC6                                (全タスク完了後)
```
