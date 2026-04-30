# replay - Rust: Debug 起動時に空ペインで止まる問題

作成日: 2026-04-30  
更新日: 2026-04-30  
状態: **原因特定・修正済み（5 件）**

1. **WSL bash 即死問題** — `type: "shell"` + `command: "bash"` が WSL の `bash.exe` を拾い、`replay_dev_load.sh` が一行も実行されなかった。→ Git Bash 絶対パス + `type: "process"` で修正済み。
2. **compound task デッドロック (第一段)** — 親タスク `replay: build & watch` に独自の `problemMatcher` を付けていたため、自身の stdout から `beginsPattern` を探し続け、`flowsurface.exe` が起動されなかった。→ `problemMatcher: []` に変更して修正済み。
3. **compound task デッドロック (第二段)** — `problemMatcher: []` でも compound task は出力を持たないため `isBackground` の begin/end 検知が成立せず、デバッグターゲットの起動に進まなかった。→ compound task `replay: build & watch` を廃止し、`launch.json` の `preLaunchTask` から `replay: watch & load (active file)` を直接指すようにした。`cargo build` の順序保証は子タスクの `dependsOn: ["cargo build"]` に移譲。
5. **`/load` → `/start` の race で bars が空 pane に流れる** — `replay_dev_load.sh` が `/api/replay/load` 直後に `/api/replay/start` を投げると、`StartEngine` が Python に届くタイミングが `ReplayDataLoaded` より先になり、Rust 側の `AutoGenerateReplayPanes` が処理される前に bars streaming が始まる。CodeLLDB アタッチ下で GUI 初期化が遅延する F5 実行では、pane 生成より先に 57 本の `KlineUpdate` がキューに積まれ、pane が出来たときには既に `EngineStopped` 後で chart は空（"Waiting for data..."）、BuyingPower も `---` のまま。→ `/api/replay/load` を `AutoGenerateReplayPanes` 処理完了まで blocking にする本修正（`Arc<tokio::sync::Notify>` ack）で根本解消。詳細は下記「第五原因」参照。

4. **`python` が WindowsApps スタブに解決されて空 JSON を POST** — VSCode 経由で起動された Git Bash の PATH では `python` が `~/AppData/Local/Microsoft/WindowsApps/python.exe`（Microsoft Store の install ガイドスタブ）に解決され、`python -c "import json; ..."` が無音で空文字を返していた。結果として `curl --data ""` が `/api/replay/load` に送られて `{"error":"invalid JSON: EOF while parsing a value at line 1 column 0"}` で 400 を喰らい、replay 初期化が成立しなかった。→ JSON 組み立てを pure bash (`printf` + 自前 `json_escape`) に書き換えて python 依存を排除した。

## 結論

今回の空ペイン問題の真因は、戦略ファイルや replay API そのものではなく、**VSCode タスクの bash 起動方法**だった。

`.vscode/tasks.json` の `replay: watch & load (active file)` が以前は `type: "shell"` + `command: "bash"` だったため、VSCode は PowerShell 経由で `bash` を解決した。その結果、このマシンでは `C:\Windows\System32\bash.exe`（WSL 側）にディスパッチされ、WSL に Linux ディストリが入っていないため `/bin/bash` 起動で即死していた。

そのため `scripts/replay_dev_load.sh` は実行されず、`POST /api/replay/load` も `POST /api/replay/start` も一度も飛ばなかった。Rust の GUI 本体だけは通常どおり起動するので、見た目としては「REPLAY で立ち上がるが Starter Pane のまま何も起きない」という症状になった。

## 症状

VSCode の `replay - Rust: Debug (CodeLLDB)` で F5 すると、GUI は起動するが replay 用 pane が自動生成されない。

- ウィンドウタイトルは `Flowsurface [Layout 1]`
- 中央は `Choose a view to get started` + `Starter Pane`
- 左下ステータスは `● REPLAY`
- replay データ投入後に出るはずの pane が出ない

pane の自動生成数は granularity に依存する。

- `Daily` / `Minute`: `CandlestickChart` + `OrderList` + `BuyingPower`
- `Trade`: `TimeAndSales` + `OrderList` + `BuyingPower`

今回のケースでは、そもそも replay load まで到達していないため、これらが 1 つも生成されなかった。

## 原因

以前の VSCode タスクは実質的に次の形だった。

```jsonc
{
  "type": "shell",
  "command": "bash",
  "args": [
    "${workspaceFolder}/scripts/replay_dev_load.sh",
    "${relativeFile}",
    "${input:replayInstrumentId}",
    "${input:replayStartDate}",
    "${input:replayEndDate}",
    "${input:replayGranularity}"
  ]
}
```

PowerShell 経由では、これは概ね次のように実行される。

```text
C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe -Command bash ...
```

この `bash` 解決で `C:\Windows\System32\bash.exe` が選ばれ、WSL 側へ流れる。今回の環境では WSL 自体は入っているがディストリ未導入のため、実際には次のエラーで終了した。

```text
<3>WSL (9 - Relay) ERROR: CreateProcessCommon:735: execvpe(/bin/bash) failed: No such file or directory
```

ここで止まるので、`replay_dev_load.sh` 内の以下の処理は一切走らない。

- HTTP サーバ起動待ち
- `POST /api/replay/load`
- `POST /api/replay/start`

結果として `ReplayDataLoaded` が発生せず、`AutoGenerateReplayPanes` も送られない。

## なぜ空ペインのままなのか

REPLAY 用 pane の自動生成は、`/api/replay/load` 成功後に `ReplayDataLoaded` を受けたときに走る。

- [scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh) が `POST /api/replay/load` を送る
- [src/replay_api.rs](../../src/replay_api.rs) が `ReplayDataLoaded` を待つ
- 成功したら `AutoGenerateReplayPanes` を UI 側へ送る
- [src/main.rs](../../src/main.rs) / [src/screen/dashboard.rs](../../src/screen/dashboard.rs) で pane を生成する

つまり、今回のように `replay_dev_load.sh` 自体が起動していない場合、UI から見ると「Rust アプリは起動しているが replay 初期化イベントが一切来ない」状態になる。

## 修正

`.vscode/tasks.json` を `type: "process"` に変え、Git Bash を絶対パスで直接起動するようにした。

```jsonc
"type": "process",
"command": "${env:LOCALAPPDATA}\\Atlassian\\SourceTree\\git_local\\usr\\bin\\bash.exe",
"args": [
    "-l",
    "${workspaceFolder}/scripts/replay_dev_load.sh",
    "${relativeFile}",
    "${input:replayInstrumentId}",
    "${input:replayStartDate}",
    "${input:replayEndDate}",
    "${input:replayGranularity}"
]
```

ポイントは 2 つある。

- `type: "process"` にすることで、PowerShell の PATH 解決を介さず直接 `bash.exe` を起動できる
- `-l` を付けることで login shell として起動し、MSYS の `/usr/bin` が通って `dirname` `tee` `curl` などが利用できる

現行の定義は [ .vscode/tasks.json ](../../.vscode/tasks.json) に反映済み。

### `scripts/replay_dev_load.sh` 側にも同時に 2 点修正

層 1・層 2 を直しただけでは、続いて 2 つ別の問題が出た。

**問題 A — HTTP 待機 60 秒では足りない**

debug ビルドの flowsurface.exe を CodeLLDB が起動してシンボルロード →
Python engine spawn → websocket handshake → adapter 初期化を経て HTTP 9876
を listen するまで、Windows の debug ビルドで実測 4 分かかるケースがある。
スクリプト側の HTTP 待機が 60 秒で諦めていたため
`FAIL — server did not start within 60 s` で死んでいた。

タイムアウトを 600 秒に拡張し、10 秒ごとに進捗ログを出すようにした。

**問題 B — 失敗時に `[replay-load] done` を出さない**

タスクの problemMatcher は `endsPattern: "[replay-load] done"` で
background task の完了を検知している。スクリプトが
`exit 1` で死ぬパスでは `done` が出ないため、VSCode 側で task インスタンスが
「実行中」のまま残り、**次の F5 で bash が再 spawn されない**副作用が発生する。

`trap EXIT` で全終了パスから `done` を emit するよう修正した。

```bash
trap 'rc=$?; echo "[replay-load] done (exit=$rc)"; exit $rc' EXIT
```

## 確認できた事実

今回の調査で確認できたことを整理すると次のとおり。

| 項目 | 結果 |
|------|------|
| `flowsurface.exe --mode replay` | 起動している |
| `GET /api/replay/status` | `200 OK` を返す |
| GUI | Starter Pane のまま |
| 原因 | replay 初期化タスク未実行 |
| `POST /api/replay/load` | 到達していない |
| `ReplayDataLoaded` | 発生していない |
| `AutoGenerateReplayPanes` | 送られていない |

要するに、**Rust 本体は正常、preLaunchTask だけが死んでいた**。

## `${relativeFile}` について

当初は `${relativeFile}` が `.vscode/launch.json` を指し、`strategy_file` に誤ったパスが渡っているのではないかと疑った。しかし今回の再現では、アクティブファイルは `docs/example/buy_and_hold.py` であり、この仮説は今回の真因ではなかった。

ただし、`${relativeFile}` を strategy file に流用している構造自体は依然として脆い。将来、戦略ファイル以外を開いた状態で F5 すると、今度は別の失敗を起こす可能性がある。

これは今回の空ペイン問題とは別件だが、次の改善候補ではある。

- `${input:replayStrategyFile}` を追加して明示入力にする
- もしくは `replay_dev_load.sh` 側で `.py` 拡張子チェックを入れる

## 補足

`/api/replay/start` が失敗すると空ペインになる、という理解は**現行コードでは正しくない**。pane 自動生成は `/api/replay/load` 成功時点で走るため、`start` の成否とは切り分けて考える必要がある。

## 第二原因: compound task の problemMatcher デッドロック（2026-04-30 追記）

WSL bash 問題を修正した後、再度 F5 で起動すると今度は別の症状が出た。

### 症状

- `cargo build` は正常終了
- `replay: watch & load (active file)` は正常に bash で起動し、`[replay-load] waiting for HTTP server on :9876 (timeout 600s) ...` を出力
- しかし `flowsurface.exe` が起動されない
- スクリプトは永遠にサーバーを待ち続ける

### 原因

`launch.json` の `replay - Rust: Debug (CodeLLDB)` は `preLaunchTask: "replay: build & watch"` を指定している。この `replay: build & watch` は `dependsOn` で子タスクを参照する **compound task** だが、独自の `problemMatcher` を持っていた。

```jsonc
// 問題のあった設定
"problemMatcher": {
    "owner": "replay-build-watch",
    "background": {
        "activeOnStart": false,
        "beginsPattern": "\\[replay-load\\] waiting",
        "endsPattern": "\\[replay-load\\] done"
    }
}
```

compound task は自分自身でコマンドを実行しないため **stdout がない**。VSCode は親タスクの stdout から `beginsPattern` を探し続けるが、永遠にマッチしない。

結果として以下のデッドロックが発生：

```
flowsurface.exe 起動待ち
  ← preLaunchTask "replay: build & watch" 完了待ち
    ← 親タスクの problemMatcher が beginsPattern を待つ（出力なし → 永久ブロック）
      ← 子タスク "replay: watch & load" は起動済みだがサーバーを待っている
        ← flowsurface.exe が起動していない（ループ）
```

### 修正

親タスク `replay: build & watch` の `problemMatcher` を空配列 `[]` に変更した。background の開始/完了検知は子タスク `replay: watch & load (active file)` の `problemMatcher` に任せる。

```jsonc
// 修正後
"problemMatcher": []
```

### 確認

| 項目 | 修正前 | 修正後 |
|------|--------|--------|
| `flowsurface.exe` 起動 | されない（デッドロック） | preLaunchTask 完了後に起動 |
| ポート 9876 | LISTEN なし | flowsurface 起動後に LISTEN |
| スクリプトのサーバー待ち | 永久ループ | サーバー検知後に次ステップへ |

## 第三原因: compound task は出力を持たないので isBackground 検知が成立しない（2026-04-30 追記）

第二原因の修正（`problemMatcher: []`）後にもう一度 F5 すると、`replay: build & watch` から子タスクが順次走り `[replay-load] waiting ...` が子の terminal に出るのに、依然として `flowsurface.exe` が起動されなかった。

理由は単純で、**compound task そのものは stdout / stderr を持たない**ため、`isBackground: true` を付けても VSCode の begin/end 検知が永遠に発火しない。`problemMatcher: []` にしても親が「出力ゼロのまま動き続けるバックグラウンドタスク」になるだけで、`preLaunchTask` 解放のトリガが無い。

修正は compound task 自体を捨てる方向にした。

- `replay: build & watch` を `tasks.json` から丸ごと削除
- `launch.json` の `preLaunchTask` を `replay: watch & load (active file)` に直接張り替え
- 順序保証は子タスクの `dependsOn: ["cargo build"]` に移譲

これで VSCode は子タスクの stdout（`[replay-load] waiting`）から直接 begin を検知でき、`flowsurface.exe` が起動するようになった。

## 第四原因: VSCode 経由 bash の `python` が WindowsApps スタブに解決される（2026-04-30 追記）

第三原因まで直すと、`flowsurface.exe` は起動して `replay_api: HTTP control API listening on 127.0.0.1:9876` まで到達するのに、`replay_dev_load.sh` が `[replay-load] done (exit=1)` で死ぬ症状が残った。当初はターミナルに 1 行しか残らず原因不明だったので、スクリプトに `~/.cache/flowsurface/replay_dev_load.log` への trace を追加して特定した。

ログから：

```
[22:02:07] load response code=400 body={"error":"invalid JSON: EOF while parsing a value at line 1 column 0"}
[22:02:07] load request body=
```

`load request body=` が空。`python -c "import json; print(json.dumps(...))"` が VSCode 経由 bash で何も出力しない、ということ。

原因は PATH。VSCode が起動した Git Bash の PATH は

```
/c/Users/sasai/AppData/Local/Microsoft/WindowsApps:/...
```

を含んでおり、ここに置かれている **Microsoft Store の python.exe スタブ**が `python` として最初に hit する。スタブはインタラクティブに実行されると Store のインストール画面を開くが、非インタラクティブに `python -c "..."` で呼ばれた場合は **stdout に何も書かず exit する**。`set -uo pipefail`（`-e` なし）なので `load_body=$(python -c ...)` は失敗扱いにならず、空文字のまま `curl --data ""` で `/api/replay/load` に POST される。Rust 側は EOF JSON で 400 を返す。

手動で Git Bash から走らせた時は venv 起動済みで `python` が `.venv/Scripts/python.exe` に解決されて成功、VSCode preLaunchTask の素の bash では Store スタブに解決されて失敗、という差だった。

修正：JSON 組み立てを **pure bash** に書き換えて python 依存を排除した。`scripts/replay_dev_load.sh` 参照。

```bash
json_escape() {
    local s=${1//\\/\\\\}
    s=${s//\"/\\\"}
    printf '%s' "$s"
}
load_body=$(printf '{"instrument_id":"%s","start_date":"%s","end_date":"%s","granularity":"%s"}' \
    "$(json_escape "$INSTRUMENT_ID")" ...)
```

副次的に、スクリプトの全 trace を `~/.cache/flowsurface/replay_dev_load.log` に append する仕組みも残した。VSCode の terminal は何故か途中の出力が消えることがあるため、ファイル側を信頼源にする。

### 教訓

- VSCode 起動の bash の PATH は対話シェルとは別物。`python` `python3` `pip` 等は **WindowsApps スタブにフォールバックし得る** ことを前提にする
- preLaunchTask 内のスクリプトは外部ツールに依存しない方が壊れにくい。今回 python は JSON 組み立てだけのために呼んでいたので、削除コストは小さかった
- スクリプトが「無音で死ぬ」場合、terminal の出力だけを根拠にしないで、明示的にファイルログを残す。VSCode 経由 terminal は信用しない

## 第五原因: `/load` → `/start` race で bars が空 pane に流れる（2026-04-30 追記）

第一〜第四原因を直して `replay_dev_load.sh` が無事に `/api/replay/load` →
`/api/replay/start` まで到達するようになると、F5（CodeLLDB attach + debug
ビルド）で pane が空のまま動かない症状が新しく出た。CLI 起動
（`bash scripts/run-replay-debug.sh ...`）では再現せず、F5 起動だけが死んでいた。

### 症状

- chart pane は「Waiting for data...」のまま bars が描画されない
- BuyingPower pane は `仮想余力: ---` `評価額: ---` で値が入らない
- 注文一覧 pane は「注文なし」（戦略は実際には動いていて Python 側ログには
  fills が出ている）

### 原因

`/api/replay/load` の以前の実装は `AutoGenerateReplayPanes` を mpsc に
fire-and-forget で `try_send` してすぐ HTTP 200 を返していた。

```text
1. conn.send(LoadReplayData)
2. await ReplayDataLoaded
3. control_tx.try_send(AutoGenerateReplayPanes)   ← fire-and-forget
4. write_response(200, OK)                        ← ここで戻る
```

dev script はその直後に `/api/replay/start` を投げ、Python は streaming
replay を開始して `KlineUpdate` を 200ms 間隔で発行し始める。

debug + lldb 起動の Iced は GUI/レンダラ初期化に時間がかかるため、message
bus に積まれた数十件の `KlineUpdate` を処理する前に
`AutoGenerateReplayPanes` を処理する保証がない。実機ログ：

```
22:05:43 ReplayDataLoaded 受信
22:05:43-54 KlineUpdate 57 本 streaming
22:05:55 EngineStopped
22:05:58 (renderer 再初期化)
22:06:00 AutoGenerateReplayPanes 処理 ← ReplayDataLoaded から 17 秒遅れ
```

pane が出来た時点では engine は既に停止しており、bars は受信先が無いまま
破棄されていた。CLI 起動はデバッガが無く GUI 初期化が bars streaming に
間に合うので race は顕在化しない。

### 暫定対応（撤去済）

`replay_dev_load.sh` に `sleep ${REPLAY_PANE_WARMUP_S:-3}` を入れる band-aid。
環境依存（マシン性能・debug/release・lldb の有無で必要秒数が変わる）で根本
対策にならないため本修正で撤去した。

### 本修正

`/api/replay/load` の API 契約を変更：

> **変更前**: 200 = engine 側で `LoadReplayData` 成功 + `ReplayDataLoaded` 受信
> **変更後**: 200 = 上記に加え、Iced 側で `AutoGenerateReplayPanes` 処理完了
>          （pane 生成・stream bind 済みで `KlineUpdate` を受信可能）

実装：

- `ControlApiCommand::AutoGenerateReplayPanes` に `ack: Option<Arc<tokio::sync::Notify>>`
  を追加（`oneshot::Sender<()>` は `ControlApiCommand` の `Clone` 制約に乗らない
  ため `Arc<Notify>` を使う）
- `replay_load` の `ReplayLoadOutcome::Ok` 分岐で `tx.send(...)` を `try_send`
  から `send().await` に変更し、ack を timeout 付きで await
  - debug 30 s / release 10 s（debug+lldb で 17 s 観測のため）
  - env var `REPLAY_PANE_READY_TIMEOUT_S` で上書き可能
- `Flowsurface::update` の `AutoGenerateReplayPanes` arm で
  `Dashboard::auto_generate_replay_panes` 呼出直後に `ack.notify_one()`
- `replay_dev_load.sh` の `sleep ${REPLAY_PANE_WARMUP_S:-3}` と env var を撤去

エラーセマンティクス：

| 条件 | ステータス | body |
|------|----------|------|
| `ReplayDataLoaded` 受信 + pane ready | 200 | `{"status":"ok","bars_loaded":N,"trades_loaded":N}` |
| `ReplayDataLoaded` 受信したが ack timeout | **504** | `{"error":"pane_ready_timeout","retryable":false}` |
| `control_tx` の receiver が drop | **503** | `{"error":"ui control channel unavailable"}` |

504 を返した時点で engine 側 load は成功しており `loaded_instruments` も
更新済みなので、再 `/load` は idempotent に成功する。`AutoGenerateReplayPanes`
が二重発行されても `Dashboard::auto_generate_replay_panes` 冒頭の
`replay_pane_registry.is_loaded()` ガードで二重 pane 生成は抑止される。

### リグレッションガード

`src/replay_api.rs` のテストモジュールに以下の pin test を追加：

- `replay_load_blocks_until_pane_ack` — ack 前に 200 を返さないこと
- `replay_load_returns_504_when_pane_ack_times_out` — `pane_ready_timeout` 経過で 504 + body
- `replay_load_returns_503_when_control_channel_closed` — `send()` SendError で 503
- `replay_load_504_does_not_block_subsequent_load` — 504 後の再 `/load` が idempotent
- `auto_generate_replay_panes_skips_pane_when_ack_already_loaded` — 二重発行抑止

`tests/auto_generate_replay_panes_auto_bind.rs` に enum 定義の構造 pin
（`ack` フィールドと `Notify` 型）と `is_loaded` ガードの存在 pin を追加。

## 関連ファイル

- [.vscode/tasks.json](../../.vscode/tasks.json)
- [.vscode/launch.json](../../.vscode/launch.json)
- [scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh)
- [src/replay_api.rs](../../src/replay_api.rs)
- [src/main.rs](../../src/main.rs)
- [src/screen/dashboard.rs](../../src/screen/dashboard.rs)
- [docs/✅nautilus_trader/replay-script-cli-args.md](./replay-script-cli-args.md)
