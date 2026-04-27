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
  Rust: keyring → TachibanaSession 読込
  Rust: SetVenueCredentials（session 含む）→ Python
  Python: session を validate
  Python: 失敗 → 再ログイン → VenueCredentialsRefreshed → Rust → keyring 更新

再起動時
  Rust: keyring → session 読込 → SetVenueCredentials 再送
```

- Rust が session の "source of truth"
- `VenueCredentialsRefreshed` IPC が必要（Python → Rust のセッション更新）
- `data/src/config/tachibana.rs` に keyring 操作コードが必要
- Wire DTO の 2 層構造（`TachibanaSession` / `TachibanaSessionWire`）が必要

### 変更後（ファイルキャッシュ方式）

```
起動時
  Rust: keyring → user_id / password のみ読込
  Rust: SetVenueCredentials（user_id + password のみ、session なし）→ Python
  Python: session_cache.json を読む
    ├─ 有効な session あり → そのまま使う
    └─ なし / 無効 → user_id/password でログイン → session_cache.json に保存

再起動時
  Python: session_cache.json から直接復元（Rust 関与なし）
```

- Python が session の "source of truth"
- `VenueCredentialsRefreshed` IPC が不要
- Rust の keyring は user_id / password のみ保持（session は持たない）
- Wire DTO から `session` フィールドを削除

---

## 3. Python 側の変更

### 3.1 新設ファイル: `tachibana_session_cache.py`

サンプルの `func_write_to_file` / `func_get_login_info` / `func_save_p_no` に相当。

```python
# python/engine/exchanges/tachibana_session_cache.py

SESSION_FILENAME = "tachibana_session.json"
P_NO_FILENAME    = "tachibana_p_no.json"

def save_session(cache_dir: Path, session: TachibanaSession) -> None:
    """ログイン成功後にセッション情報をファイルへ保存する"""
    ...

def load_session(cache_dir: Path) -> TachibanaSession | None:
    """起動時にファイルからセッションを復元する。ファイルがない/不正なら None"""
    ...

def save_p_no(cache_dir: Path, p_no: int) -> None:
    ...

def load_p_no(cache_dir: Path) -> int:
    ...
```

保存内容（`tachibana_session.json`）:

```json
{
  "url_request":   "https://demo-kabuka.e-shiten.jp/e_api_v4r8/xxxxxx/",
  "url_master":    "...",
  "url_price":     "...",
  "url_event":     "...",
  "url_event_ws":  "...",
  "zyoutoeki_kazei_c": "1",
  "saved_at_ms":   1745712000000
}
```

`saved_at_ms` を見て「当日のセッションかどうか」を簡易判定する（日本時間 15:30 以降に保存されたものは翌朝に無効扱い）。正確な期限管理は Phase 2 で行う。

### 3.2 `tachibana_auth.py` の変更

`validate_session_on_startup` でファイルキャッシュを先に確認する:

```python
async def restore_or_login(creds, cache_dir):
    # 1. ファイルから session を読む
    session = load_session(cache_dir)
    if session and _is_session_fresh(session):
        return session                      # 再ログインなし

    # 2. 期限切れ or なし → user_id/password でログイン
    session = await login(creds.user_id, creds.password, creds.is_demo)

    # 3. 成功したらファイルに保存
    save_session(cache_dir, session)
    save_p_no(cache_dir, p_no=1)
    return session
```

### 3.3 `tachibana.py` の変更

`SetVenueCredentials` ハンドラから `session` フィールドの処理を削除し、`restore_or_login` を呼ぶ:

```python
async def _handle_set_venue_credentials(self, payload):
    # session は IPC から来なくなる。ファイルから自前で復元
    self._session = await restore_or_login(payload.credentials, self._cache_dir)
    await self._send(VenueReadyEvent(...))
```

`VenueCredentialsRefreshed` の送信も削除（Rust keyring への逆送が不要）。

---

## 4. Rust 側の削減

| 削除するもの | 理由 |
|---|---|
| `data/src/config/tachibana.rs` の session 保存・復元コード | Python がファイルで管理するため不要 |
| `TachibanaSession` struct（`data` クレート） | Rust が session を持つ必要がなくなる |
| `TachibanaSessionWire` struct（`engine-client` クレート） | IPC でセッションを送らなくなる |
| `VenueCredentialsRefreshed` IPC イベント | Python → Rust の session 更新が不要 |

**残るもの**（user_id / password は引き続き Rust keyring で管理）:

```rust
// data/src/config/tachibana.rs（簡略化後）
pub struct TachibanaCredentials {
    pub user_id: String,
    pub password: SecretString,
    pub is_demo: bool,
    // session フィールドを削除
}
```

`SetVenueCredentials` IPC コマンドは残るが、`session: Option<TachibanaSessionWire>` フィールドを削除してシンプルになる。

---

## 5. IPC の変更

### 削除するイベント

```rust
// 削除
EngineEvent::VenueCredentialsRefreshed { ... }
```

### 変更するコマンド

```rust
// 変更前
Command::SetVenueCredentials {
    payload: TachibanaCredentialsWire {
        user_id, password, is_demo,
        session: Option<TachibanaSessionWire>,  // ← 削除
    }
}

// 変更後
Command::SetVenueCredentials {
    payload: TachibanaCredentialsWire {
        user_id, password, is_demo,
        // session フィールドなし
    }
}
```

`schema_minor` を bump する。

---

## 6. セキュリティ考慮

| 項目 | 評価 |
|---|---|
| 仮想 URL がファイルに残る | △ plaintext。ただし 1 日券であり夜間閉局後は無効 |
| ファイルの所在 | `cache_dir`（Rust が起動時に Python へ渡す）。OS ユーザーディレクトリ配下 |
| 他プロセスからの読み取り | OS ファイルパーミッションに依存（Unix: 600 を推奨、Windows: ユーザープロファイル） |
| user_id / password | 引き続き Rust OS keyring のみ。ファイルには書かない |
| p_no ファイル | 非機密（シーケンス番号のみ） |

**許容できる理由**: 仮想 URL は夜間閉局で自動失効する 1 日券のため、流出しても翌朝には無効になる。user_id / password という「真の機密」はファイルに出さない設計を維持する。

---

## 7. 実装タスク

### T-SC1: `tachibana_session_cache.py` 新設

- `save_session` / `load_session` / `save_p_no` / `load_p_no` 実装
- `_is_session_fresh(session)`: JST 当日 15:30 未満に保存されたものを有効判定
- ファイルが壊れている場合は `None` を返す（例外を飲み込む）
- ファイル書込みは `tempfile` + `rename` でアトミックに行う

### T-SC2: `tachibana_auth.py` に `restore_or_login` 追加

- 上記フローの実装
- `load_session` → 有効なら返す → なければ `login` → `save_session`

### T-SC3: `tachibana.py` の `_handle_set_venue_credentials` 修正

- `session` フィールドの参照を削除
- `restore_or_login` を呼ぶように変更
- `VenueCredentialsRefreshed` 送信コードを削除

### T-SC4: Rust 側の session 関連コードを削除

- `TachibanaSession` / `TachibanaSessionWire` struct を削除
- `TachibanaCredentials.session` フィールドを削除
- `TachibanaCredentialsWire.session` フィールドを削除
- `VenueCredentialsRefreshed` variant を削除
- `ProcessManager` の `VenueCredentialsRefreshed` ハンドラを削除
- `data/src/config/tachibana.rs` の session 保存・復元コードを削除
- `schema_minor` bump

### T-SC5: テスト追加

- `python/tests/test_tachibana_session_cache.py`
  - 保存 → 読み込みラウンドトリップ
  - ファイル破損時に `None` を返すこと
  - `saved_at_ms` が古い場合に `_is_session_fresh` が `False` を返すこと
  - アトミック書き込み（書きかけファイルが残らない）
- `python/tests/test_tachibana_auth.py` の `restore_or_login` ケース追加
  - キャッシュあり → ログインを呼ばない
  - キャッシュなし → ログインを呼ぶ → ファイルに保存

### T-SC6: `architecture.md` / `spec.md` の更新

- §2.3「セッション再永続化」を本方式に書き換え
- §3 起動シーケンスを更新
- `spec.md §3.1` の Python session 保持箇所（「メモリのみ」→「メモリ + ファイルキャッシュ」に変更）

---

## 8. 依存関係

```
T-SC1 → T-SC2 → T-SC3   (Python 変更、直列)
T-SC4                     (Rust 変更、T-SC3 と並行可)
T-SC5                     (T-SC1〜T-SC3 完了後)
T-SC6                     (全タスク完了後)
```
