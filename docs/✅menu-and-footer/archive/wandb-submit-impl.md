# W&B Submit メニュー（Tools サブメニュー）

replay 結果を Weights & Biases にポストラン送信するためのメニュー機能。
replay 中は **wandb をインプロセスでロードしない**（コア非汚染ルール）。
送信は終了後に独立 subprocess で実行される。

---

## 設計の不変条件

- **コア非汚染**: `import wandb` は `examples/wandb/` 配下のみ。`src/` および
  `python/engine/` は wandb に依存しない
- **ポストラン送信のみ（V1）**: replay 中はリアルタイム送信しない。バックテスト
  終了後に RunBuffer を読んで一括 submit する
- **subprocess 経由**: wandb は `uv run --with wandb python examples/wandb/submit_run.py`
  として ad-hoc に注入。Rust プロセスに wandb を抱えない
- **Rust は認証ロジックを持たない**: `~/.netrc` / `WANDB_API_KEY` の判定は
  Python 側 `examples/wandb/check_auth.py` に完全集約
- **誤発注事故はユーザー責任**: live モードからの submit は対象外（V1）

---

## メニュー構成（Tools サブメニュー）

| ラベル | アクセラレータ | enable 条件 |
|--------|----------------|-------------|
| `W&B に登録…（Submit to W&B）` | — | `WandbAuthState::SignedIn` かつ RunBuffer に未送信 run あり かつ `submit_in_flight = false` |
| `送信履歴を開く（Open Submission Log）` | — | 送信履歴ファイルが存在する |
| `バッファを削除…（Clear Run Buffer）` | — | RunBuffer に run あり |
| `W&B にログイン…（Sign in to W&B）` | — | `WandbAuthState::SignedOut` |
| `W&B からログアウト（Sign out of W&B）` | — | `WandbAuthState::SignedIn` |

`MenuEntry` の `enabled` / `tooltip` は `tools_actions_for_state(auth, run_buffer)`
が返す（`src/menu.rs`）。Win/Mac/Linux 全 OS で同じ集合を返すことが
`tests/tools_actions_for_state.rs` で保護されている。

`Action` enum:

```rust
Action::SubmitToWandb
Action::OpenSubmissionLog
Action::ClearRunBuffer
Action::SignInWandb
Action::SignOutWandb
```

---

## RunBuffer 仕様

### 配置

```
%APPDATA%\flowsurface\run-buffer\          (Windows)
~/.local/share/flowsurface/run-buffer/     (Linux)
~/Library/Application Support/flowsurface/run-buffer/  (macOS)
```

### ファイル形式

replay 1 回ごとに 1 サブディレクトリ。`RunBufferIndex` が次の構造で列挙する：

| ファイル | 内容 |
|---------|------|
| `meta.json` | `{ run_id, instrument, start, end, granularity, strategy_path, finished_at, schema_version }` |
| `equity.jsonl` | `{ ts, equity, cash, position }` 1 行 / 1 step |
| `fills.jsonl` | 約定ログ（nautilus 互換） |
| `narrative.jsonl` | 戦略が `Strategy.log_narrative()` で記録した自由記述 |

### 書き出しタイミング

replay 終了時に `python/engine/run_buffer.py` の `RunBufferWriter` が atomic
write で書き出す。途中で abort された run は不完全な `meta.json` で識別される。

### バッファ保持ポリシー

- V1: 手動削除のみ（`バッファを削除…` メニュー or 自前で `run-buffer/` を消す）
- 自動 retention は実装しない（古い run が増えるのは許容）

---

## 認証フロー

### Sign in（`WandbSignin` モーダル）

`src/modal/wandb_signin.rs`：

1. ユーザーが API key を入力
2. `examples/wandb/check_auth.py` を subprocess で起動して検証
3. 成功時は wandb 標準の `~/.netrc` に保存（wandb SDK に委譲）
4. `WandbAuthState` を `SignedIn` に遷移、Tools サブメニューを再計算

API key は **コマンドライン引数に渡さない**（`ps` 経由で漏洩しないため）。
環境変数 `WANDB_API_KEY` で subprocess に伝播する。

### Sign out

`~/.netrc` の `api.wandb.ai` エントリを削除（手動でも可）。`WandbAuthState` を
`SignedOut` に戻す。

リグレッションガード:

- `tests/wandb_auth_state.rs` — `WandbAuthState` 遷移
- `tests/wandb_signin_flow.rs` — sign in のフル経路
- `tests/wandb_auth_timeout.rs` — タイムアウトハンドリング
- `tests/wandb_key_masking.rs` — ログから API key がマスクされる

---

## Submit フロー（`WandbSubmit` モーダル）

`src/modal/wandb_submit.rs`：

1. RunBuffer から最新 1 件を選択（V1 は複数選択不可）
2. ユーザーが notes / tags を入力
3. `wandb_submit_proc::build_submit_command()` が
   `uv run --with wandb python examples/wandb/submit_run.py ...` を組み立て
4. `WandbSubmitModal.submitting = true` を立てて再入を禁止（`submit_in_flight`）
5. subprocess の stdout を 1 行ずつ `SubmitEvent` に変換して UI に反映
6. 終了 JSON に含まれる W&B run URL を `parse_url_from_output()` で抽出
7. 履歴ファイルに追記

不変条件：

- `WandbSubmitModal.submitting` が `true` の間は **モード切替が抑制される**
  （[mode-switch-impl.md §5 軸 matrix](./mode-switch-impl.md#5-軸-matrix不変条件)
  の `submit_in_flight` 軸）
- subprocess 環境は **inherit**（`WANDB_API_KEY` を引数に出さず env 経由のみ）
- ログ出力前に `mask_secrets()`（`src/mask_secrets.rs`）で API key 形式の
  文字列をマスクする

リグレッションガード:

- `tests/wandb_submit_subprocess.rs` — `build_submit_command` / `parse_url_from_output`
- `tests/wandb_reentrancy.rs` — `submit_in_flight` 中の再入禁止
- `tests/wandb_modeswitch_lock_order.rs` — submit 中のモード切替抑制
- `tests/wandb_menu_action.rs` — Tools メニュー dispatch
- `tests/wandb_submission_log_ui.rs` — 履歴 UI
- `python/tests/test_run_buffer_writer.py` — RunBuffer 書き出し
- `examples/wandb/tests/test_pii_scrub.py` / `test_submit_run.py`

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action::{SubmitToWandb, OpenSubmissionLog, ClearRunBuffer, SignInWandb, SignOutWandb}` / `tools_actions_for_state` |
| `src/wandb_auth.rs` | `WandbAuthState` / `RunBufferIndex` |
| `src/wandb_submit_proc.rs` | `build_submit_command` / `SubmitEvent` / `parse_url_from_output` |
| `src/modal/wandb_signin.rs` | sign in モーダル |
| `src/modal/wandb_submit.rs` | submit モーダル / `submit_in_flight` ガード |
| `src/mask_secrets.rs` | `mask_secrets()` ログマスカ |
| `python/engine/run_buffer.py` | `RunBufferWriter`（replay 終了時に書き出し） |
| `examples/wandb/submit_run.py` | wandb への送信 subprocess |
| `examples/wandb/check_auth.py` | 認証情報の有無判定 |
| `examples/wandb/pii_scrub.py` | 送信前の PII スクラブ |

---

## 既知の制限と非スコープ

- **V1 は最新 1 run のみ送信**: 複数 run の一括送信は未対応
- **送信失敗時の自動 retry なし**: ユーザーが手動で再送する
- **live モードからの送信は不可**: V1 は replay 結果に限定
- **戦略 `.py` 内では `import wandb` 禁止**: コア非汚染ルール。送信は
  `examples/wandb/submit_run.py` 経由でのみ
- 詳細な戦略実装パターンは `/wandb` スキル / `docs/plan/wandb-vision.md` 参照
