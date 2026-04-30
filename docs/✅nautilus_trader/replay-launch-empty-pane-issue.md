# replay - Rust: Debug 起動時に空ペインで止まる問題（調査ハンドオフ）

作成日: 2026-04-30  
状態: **真の原因判明**（VSCode タスクの bash が WSL bash にディスパッチされ、WSL に `/bin/bash` が無くて即死していた）。`tasks.json` を絶対パス指定に修正し再検証中。

## 真の原因（2026-04-30 追記・最優先）

`.vscode/tasks.json` の `replay: watch & load (active file)` が `command: "bash"` ＋ `type: "shell"` で定義されていたため、VSCode は PowerShell 経由で `bash` を呼び出す：

```
ターミナル プロセス "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe -Command bash <スクリプトパス> <引数...>"
```

PowerShell の PATH 解決は `C:\Windows\System32\bash.exe`（**WSL の bash**）を最初に拾ってしまう。WSL は導入済みだが Linux ディストリは未インストールなので：

```
<3>WSL (9 - Relay) ERROR: CreateProcessCommon:735: execvpe(/bin/bash) failed: No such file or directory
ターミナル プロセス ... が終了コード 1 で終了しました。
```

→ `replay_dev_load.sh` は**一行も実行されない**。よって：

- `POST /api/replay/load` が来ない
- `ReplayDataLoaded` イベントが出ない
- `AutoGenerateReplayPanes` が送られない
- GUI は空ペインのまま

VSCode の preLaunchTask は compound `replay: build & watch`（`cargo build` → `replay: watch & load (active file)` の sequence）。`cargo build` 完了後に background サブタスクが走るが、その bash が即死しても VSCode は debugger をそのまま launch するので GUI 自体は起動する → 「動いてるのに空」という症状になる。

### 修正

[.vscode/tasks.json](../../.vscode/tasks.json) を `type: "process"` + bash 絶対パス指定に変更：

```jsonc
"type": "process",
"command": "${env:LOCALAPPDATA}\\Atlassian\\SourceTree\\git_local\\usr\\bin\\bash.exe",
"args": [
    "${workspaceFolder}/scripts/replay_dev_load.sh",
    "${relativeFile}",
    ...
]
```

`type: "process"` は引数解析を経由せず exec で直接起動するので PATH 上の WSL bash に再ディスパッチされない。bash 本体は SourceTree 同梱の Git Bash（このマシンで唯一の MSYS bash）。

### 当初の「`${relativeFile}` 罠」仮説について

下記に書いた `${relativeFile}` 仮説は**今回の症状の原因ではなかった**。今回の検証中、ユーザーは `buy_and_hold.py` を開いた状態で起動したため `${relativeFile}` は正しく `docs\example\buy_and_hold.py` に解決されていた（VSCode が PowerShell 経由で渡したコマンドラインに含まれていた）。ただし将来的に「launch.json を開いた状態で F5 を押した場合」に同じ罠を踏む構造は残っているので、bash 修正後にもう一度ハンドオフ案 A（`${input:replayStrategyFile}` 化）を検討すること。

---

## 症状

VSCode の `replay - Rust: Debug (CodeLLDB)` 起動構成で F5 → 起動するが、
GUI が以下の状態で止まる：

- ウィンドウタイトル: `Flowsurface [Layout 1]`
- 中央ペイン: `Choose a view to get started` ＋ `Starter Pane` ドロップダウンのみ
- 左下ステータスバー: `● REPLAY`（オレンジ）
- 通常生成されるはずの **TimeAndSales / CandlestickChart / OrderList / BuyingPower の 4 ペインが auto-generate されない**

## 確認済み事実

| 項目 | 結果 |
|------|------|
| `flowsurface.exe --mode replay` プロセス起動 | ✅ 起動している |
| `GET http://127.0.0.1:9876/api/replay/status` | ✅ `{"status":"ok","version":"0.8.7"}` を返す |
| `~/AppData/Roaming/flowsurface/flowsurface-current.log` | 直近の更新なし（debug ビルドは stdout 出力のため当然） |
| `replay mode: discarding saved pane layout (D8)` ログ | 過去ログに残存（D9 仕様通り起動時は空レイアウト） |

→ Rust 側 exe は正しく起動し HTTP サーバも上がっているが、
   **`POST /api/replay/load` が一度も到達していない**ため
   `ReplayDataLoaded` イベントが発行されず `auto_generate_replay_panes` が走らない。

## 原因の最有力仮説

`.vscode/tasks.json` の `replay: watch & load (active file)` タスクが
`replay_dev_load.sh` の strategy_file 引数に **`${relativeFile}`** を渡している：

```jsonc
"args": [
    "${workspaceFolder}/scripts/replay_dev_load.sh",
    "${relativeFile}",              // ← アクティブエディタのファイル
    "${input:replayInstrumentId}",
    "${input:replayStartDate}",
    "${input:replayEndDate}",
    "${input:replayGranularity}"
]
```

`${relativeFile}` は **VSCode で現在フォーカスのあるエディタファイル**を返す。
今回ユーザーは `.vscode/launch.json` を開いた状態で F5 を押したため、
`${relativeFile}` が `.vscode\launch.json` に解決された可能性が高い。

結果として：

1. 戦略ファイルではない `.json` が `replay_dev_load.sh $1` に渡る
2. `replay_dev_load.sh` 内 `:?` チェックは通る（空ではない）
3. `POST /api/replay/load` は OK（load は instrument/期間しか見ない）
4. `POST /api/replay/start` の body に `strategy_file: ".vscode/launch.json"` が乗る
   → Python 側 `load_strategy_from_file` が JSON を Strategy モジュールとしてロードしようとして失敗
5. start が 4xx を返してタスクが死ぬ
6. `ReplayDataLoaded` ハンドラ側は到達せず GUI は空のまま

## 補強情報（追加で取るべきログ）

次に再現するときに取得すべき：

1. **VSCode 統合ターミナルの `replay-load` 専用パネル**の全出力
   - `[replay-load] waiting for HTTP server` から始まる行
   - `FAIL — /api/replay/start returned XXX` の HTTP コード
   - サーバが返した JSON エラーメッセージ
2. **CodeLLDB のデバッグコンソール（Rust stdout）**
   - 「Subscribe: unknown venue 'replay'」など既知 OK ログ以外のエラー
   - `LoadReplayData` / `StartEngine` 受信ログ
3. **Python エンジン側の出力**
   - debug ビルドでは Rust が spawn した Python の stdout/stderr が
     CodeLLDB のデバッグコンソールに混じる
   - `StrategyLoadError` / 例外トレースが出ていれば start 失敗確定

## 直前に行った関連変更

[replay-script-cli-args.md](./replay-script-cli-args.md) で以下を実施済：

- `.env` 自動 source の完全廃止
- `replay_dev_load.sh` / `run-replay-debug.sh` の REPLAY_* を CLI 引数化
- `.vscode/tasks.json` に `inputs` セクション追加（銘柄・開始日・終了日・足種）

この変更**前**は環境変数経由のため `${relativeFile}` の罠は同じ条件下でも発生していた
（strategy_file 引数だけ既に `${relativeFile}` だった）。
今回顕在化したのは、新シグネチャ移行に伴い再起動を行ったタイミングで
ユーザーが `launch.json` を開いていたから。

## 修正方針の選択肢

### 案 A: strategy_file もプロンプト入力に変更（推奨）

```jsonc
"args": [
    "${workspaceFolder}/scripts/replay_dev_load.sh",
    "${input:replayStrategyFile}",   // ← promptString
    "${input:replayInstrumentId}",
    ...
]
```

- メリット: アクティブエディタに依存しないので堅牢
- デメリット: 毎回パスを打つ必要がある → `default` を `docs/example/buy_and_hold.py` にすれば1キーで通る

### 案 B: `${relativeFile}` のままにし、起動前に拡張子チェック

スクリプト先頭で `[[ "$STRATEGY_FILE" == *.py ]]` を assert する。
非 .py なら明示的にエラーで早期終了。

- メリット: 戦略ファイルを開いた状態なら最速
- デメリット: 「戦略ファイルを開いた状態で F5」を覚える必要あり

### 案 C: `${input:replayStrategyFile}` を `pickString` で候補列挙

`docs/example/*.py` 等を `options` に列挙。

- メリット: タイプミスなし
- デメリット: サンプル増加のたびに tasks.json メンテが必要

## 推奨

**案 A + default 値**で進めるのが妥当。
F5 → 4 つのプロンプト（うち strategy_file は default 値で Enter）→ 起動、というフロー。

## 関連ファイル

- [.vscode/tasks.json](../../.vscode/tasks.json) — `inputs` 追加箇所
- [.vscode/launch.json](../../.vscode/launch.json) — `replay - Rust: Debug (CodeLLDB)` 構成
- [scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh) — strategy_file を `$1` で受ける
- [scripts/run-replay-debug.sh](../../scripts/run-replay-debug.sh) — CLI 経由起動用
- [src/replay_api.rs](../../src/replay_api.rs) — HTTP API ハンドラ
- [docs/✅nautilus_trader/replay-script-cli-args.md](./replay-script-cli-args.md) — 直前の変更履歴
