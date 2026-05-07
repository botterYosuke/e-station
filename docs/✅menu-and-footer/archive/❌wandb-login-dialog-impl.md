# W&B ログイン: iced モーダル → tkinter サブプロセス ダイアログへの移行

立花証券ログインと同じ方式（Python tkinter ウィンドウをサブプロセスで起動）を  
W&B ログインにも適用する。現状の iced モーダル（`src/modal/wandb_signin.rs`）を廃止し、  
`examples/wandb/login_dialog.py` に置き換える。

---

## 背景と動機

### 現状（問題あり）

```
Rust iced モーダル（wandb_signin.rs）
  └─ API キー入力（iced text_input）
  └─ wandb_login() → uv run ... wandb login --relogin  ← CLI が stdin を読まない
  └─ do_login.py 経由で wandb.login(key=...) を呼ぶ（暫定修正済み）
```

**問題点：**
- iced モーダルは W&B の「ブラウザで API キーを取得」UX に馴染まない
- モーダルの背景・幅の調整が iced テーマ依存で複雑
- wandb CLI の stdin 読み取りが新バージョンで動作しないことが判明（今回の障害）

### 目標（立花証券と同方式）

```
Rust（メニュー Action::SignInWandb）
  └─ wandb_login() → uv run python examples/wandb/login_dialog.py
       └─ tkinter GUI でAPIキー入力（マスク表示）
       └─ wandb.login(key=..., relogin=True) を Python で直接呼ぶ
       └─ stdout JSON → {"ok": true} / {"ok": false, "error": "..."}
  └─ 結果を Rust が受け取り、Toast 通知を表示
```

---

## 設計の不変条件

| 不変条件 | 内容 |
|---------|------|
| **コア非汚染** | `import wandb` は `examples/wandb/` 配下のみ。`python/engine/` には入れない |
| **IPC 非経由** | W&B ログインは data engine IPC を通さない（立花の Venue 系イベントと混在させない） |
| **キーを argv に出さない** | API キーはプロセスリストに露出しない。subprocess は stdin 経由でキーを受け取る |
| **キーを Rust に渡さない** | `login_dialog.py` 内で完結。Rust は `ok/error` の結果のみ受け取る |
| **IPC スキーマ変更なし** | `schemas.py` / `commands.json` / `events.json` は変更しない |

---

## 削除するもの

| ファイル / シンボル | 理由 |
|--------------------|------|
| `src/modal/wandb_signin.rs` | iced モーダルを廃止 |
| `examples/wandb/do_login.py` | `login_dialog.py` に統合 |
| `main.rs` の `wandb_signin_modal: Option<WandbSignInModal>` フィールド | モーダル廃止 |
| `main.rs` の `Message::WandbSignInMsg` / `Message::WandbLoginResult` | モーダル廃止 |
| `main.rs` の `wandb_login()` async 関数 | `login_dialog.py` spawn に置換 |
| `main.rs` の `after_signin` overlay 合成ブロック（view） | モーダル廃止 |
| `src/modal.rs` の `pub mod wandb_signin` | モーダル廃止 |

---

## 追加するもの

### `examples/wandb/login_dialog.py`

```
┌────────────────────────────────────────────┐
│  W&B API キーでサインイン                  │
│                                            │
│  API キー: [●●●●●●●●●●●●●●●●●●]            │
│                                            │
│  [ブラウザで API キーを取得]               │
│                                            │
│  [エラーメッセージ（赤文字）]              │
│                                            │
│      [キャンセル]    [ログイン]            │
└────────────────────────────────────────────┘
```

**仕様：**
- `tkinter.ttk` を使用。ウィンドウサイズ: 420 × 200 px（最小、リサイズ不可）
- API キー入力フィールドは `show="*"`（マスク表示）
- 「ブラウザで API キーを取得」ボタン → `webbrowser.open("https://wandb.ai/authorize")`
- Enter キーでログイン、Escape でキャンセル
- ログインボタン押下 → `wandb.login(key=key, relogin=True)` を呼ぶ
  - 成功: stdout に `{"ok": true}` 出力して exit(0)
  - 失敗: エラーメッセージを UI に表示し、再入力を促す（exit しない）
- キャンセル: stdout に `{"ok": false, "error": "cancelled"}` 出力して exit(0)
- `--headless` 引数対応（pytest 用、GUI なし、stdin から key を読んで即 login）

**起動コマンド（Rust 側から）：**
```
uv run --with wandb python examples/wandb/login_dialog.py
```

---

## Rust 側の修正（`src/main.rs`）

### `wandb_login()` の置換

既存の `wandb_login(api_key: String)` async 関数を  
`wandb_launch_login_dialog()` async 関数に置き換える。

```rust
/// `examples/wandb/login_dialog.py` をサブプロセスで起動し、
/// tkinter ウィンドウでユーザーに API キーを入力させる。
/// 結果を stdout JSON で受け取る。
async fn wandb_launch_login_dialog() -> Result<(), String> {
    // API キーを Rust に渡さない設計:
    // login_dialog.py が自ら wandb.login() を呼んで netrc に保存する
    let child = Command::new("uv")
        .args(["run", "--with", "wandb", "python", "examples/wandb/login_dialog.py"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| format!("spawn failed: {e}"))?;

    // ウィンドウを閉じるまで待つ（ユーザー操作依存）
    // timeout なし — ユーザーが好きな時間を使える
    let output = child.wait_with_output().await
        .map_err(|e| format!("wait failed: {e}"))?;

    // stdout JSON をパース
    let stdout = String::from_utf8_lossy(&output.stdout);
    let line = stdout.lines().find(|l| !l.trim().is_empty()).unwrap_or("");
    if let Ok(val) = serde_json::from_str::<serde_json::Value>(line) {
        if val.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
            return Ok(());
        }
        let err = val.get("error").and_then(|v| v.as_str()).unwrap_or("unknown");
        return Err(err.to_string());
    }
    Err(format!("no JSON output from login_dialog.py"))
}
```

### `Message::WandbLoginResult` の変更

```rust
// 変更前: wandb_signin_modal からモーダルメッセージ経由で受け取っていた
// 変更後: Action::SignInWandb で直接 Task::perform を起動

Message::NativeMenuAction(Action::SignInWandb) => {
    // モーダルを開かず、直接 login_dialog.py を起動
    return Task::perform(
        wandb_launch_login_dialog(),
        Message::WandbLoginResult,
    );
}
Message::WandbLoginResult(Ok(())) => {
    self.notifications.push(Toast::info("W&B にログインしました".to_string()));
    return Task::perform(wandb_auth::refresh_wandb_auth(), Message::WandbAuthRefreshed);
}
Message::WandbLoginResult(Err(err)) => {
    if err != "cancelled" {
        self.notifications.push(Toast::error(format!("ログイン失敗: {err}")));
    }
    Task::none()
}
```

---

## フェーズ

### W1: `examples/wandb/login_dialog.py` 作成（1 日）

- [x] ~~`examples/wandb/do_login.py` 作成（暫定、本タスクで置換）~~
- [ ] `login_dialog.py` を実装
  - tkinter GUI（マスク入力・ブラウザリンク・エラー表示・Enter/Escape）
  - `wandb.login(key=..., relogin=True)` 成功 / 失敗ハンドリング
  - stdout JSON 出力 (`{"ok": ...}`)
  - `--headless` モード（pytest 用）
- [ ] `examples/wandb/do_login.py` を削除

**受け入れ条件：**
```bash
# 手動テスト: ウィンドウが開き、ログインできること
uv run --with wandb python examples/wandb/login_dialog.py

# headless テスト
echo "invalid_key" | uv run --with wandb python examples/wandb/login_dialog.py --headless
# → {"ok": false, "error": "..."} が stdout に出ること
```

### W2: Rust 側の配線（1 日）

- [ ] `src/main.rs` の `wandb_launch_login_dialog()` 実装
- [ ] `Message::NativeMenuAction(Action::SignInWandb)` ハンドラを新方式に変更
- [ ] `Message::WandbLoginResult` ハンドラを新方式に変更（`wandb_signin_modal` への参照削除）
- [ ] `wandb_signin_modal` フィールドと関連コードを削除
- [ ] `after_signin` overlay ブロックを main.rs view から削除
- [ ] `src/modal.rs` から `pub mod wandb_signin` を削除
- [ ] `src/modal/wandb_signin.rs` を削除

**受け入れ条件：**
```bash
cargo build  # エラーなし
cargo test --workspace  # 全 PASS
```

### W3: テスト追加（半日）

- [ ] `tests/wandb_signin_flow.rs` を新方式に合わせて更新
  - モーダルメッセージ系のテストを削除
  - `wandb_launch_login_dialog()` が subprocess を起動することの構造テスト
- [ ] `examples/wandb/tests/test_login_dialog.py` を追加
  - headless モードでの正常系（valid key）
  - headless モードでの異常系（invalid key / empty key）
  - キャンセル (`{"ok": false, "error": "cancelled"}`)

---

## テスト対応表

| テストファイル | 変更内容 |
|---------------|---------|
| `tests/wandb_signin_flow.rs` | モーダルメッセージ系を削除、subprocess 起動構造テストに置換 |
| `tests/wandb_key_masking.rs` | login_dialog.py が stderr / stdout にキーを出力しないことを確認 |
| `tests/wandb_auth_timeout.rs` | 変更なし（`refresh_wandb_auth` のタイムアウトテスト） |
| `examples/wandb/tests/test_login_dialog.py` | 新規追加（headless モード） |

---

## 主要ソース（変更後）

| ファイル | 役割 |
|---------|------|
| `examples/wandb/login_dialog.py` | tkinter ダイアログ + `wandb.login()` 呼び出し |
| `examples/wandb/check_auth.py` | 認証状態チェック（変更なし） |
| `examples/wandb/submit_run.py` | W&B Submit（変更なし） |
| `src/main.rs` | `wandb_launch_login_dialog()` / `Message::WandbLoginResult` |
| `src/wandb_auth.rs` | `WandbAuthState` / `RunBufferIndex`（変更なし） |
| `src/modal/wandb_submit.rs` | Submit モーダル（変更なし） |

**削除済みファイル：**
- `src/modal/wandb_signin.rs`
- `examples/wandb/do_login.py`

---

## 立花証券ログインとの対比

| 観点 | 立花証券 | W&B（本計画） |
|------|---------|-------------|
| UI 担当 | Python tkinter（`tachibana_login_dialog.py`） | Python tkinter（`login_dialog.py`） |
| 起動元 | Python data engine（IPC server 内から） | Rust（main.rs から直接） |
| IPC 使用 | あり（VenueLoginStarted / VenueReady 等） | なし（subprocess stdout のみ） |
| 認証実行 | Python（`tachibana_auth.login()`） | Python（`wandb.login(key=..., relogin=True)`） |
| 結果通知 | IPC event → Rust | stdout JSON → Rust |
| キーの扱い | パスワードは Python メモリ内のみ | API キーは Python メモリ内のみ |

W&B は Venue（取引所）ではないため IPC を通さない。  
Rust が subprocess を直接起動し、結果だけを受け取るシンプルな設計を選択する。
