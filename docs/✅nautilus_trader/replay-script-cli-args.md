# replay スクリプト: パラメータを CLI 引数に統一 ✅ 実装済み

## 問題（解決済み）

`replay_dev_load.sh` / `run-replay-debug.sh` のパラメータ渡し方が非対称だった。

- `strategy_file` のみ CLI 引数 `$1`
- `instrument_id` / `start_date` / `end_date` は env var 必須（`.env` 自動 source）
- これらは `/api/replay/load` と `/api/replay/start` の JSON body に詰めるだけのパラメータであり、
  速度変更（`/api/replay/control`）等と同列。env var に依存する理由がなかった

## 変更結果

`.env` の自動 source を完全廃止。全パラメータを CLI 引数に統一。

### シグネチャ

```
replay_dev_load.sh  <strategy_file> <instrument_id> <start_date> <end_date> [granularity]
run-replay-debug.sh <strategy_file> <instrument_id> <start_date> <end_date> [granularity]
```

| 位置 | 必須 | 例 |
|------|------|-----|
| `$1` strategy_file | ✅ | `docs/example/buy_and_hold.py` |
| `$2` instrument_id | ✅ | `1301.TSE` |
| `$3` start_date | ✅ | `2025-01-06` |
| `$4` end_date | ✅ | `2025-03-31` |
| `$5` granularity | 任意（既定 `Daily`） | `Daily` / `Minute` / `Trade` |

### 任意パラメータの env var（`REPLAY_INITIAL_CASH`, `REPLAY_STRATEGY_ID`）

`.env` 自動 source は廃止したため、**親シェルで `export` 済みの env var のみ有効**。
`.env` に書いても自動では読まれない。設定する場合は明示的に export すること：

```bash
export REPLAY_INITIAL_CASH=500000
bash scripts/run-replay-debug.sh docs/example/buy_and_hold.py 1301.TSE 2025-01-06 2025-03-31
```

### VSCode からの起動

`.vscode/tasks.json` の `replay: watch & load (active file)` タスクに `inputs` を追加済み。
`replay - Rust: Debug (CodeLLDB)` 起動時に銘柄・開始日・終了日はプロンプト入力ダイアログ、
足種はドロップダウンで選択できる。

## 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `scripts/replay_dev_load.sh` | `.env` source ブロック削除、`$2`〜`$5` を positional args に変更 |
| `scripts/run-replay-debug.sh` | `.env` source ブロック削除、`$2`〜`$5` 追加して `replay_dev_load.sh` に転送 |
| `.vscode/tasks.json` | `inputs` セクション追加済み（銘柄・開始日・終了日・足種） |
| `.claude/CLAUDE.md` | 最小コマンド例と引数一覧を更新 |
| `README.md` | replay 補助スクリプト行を CLI 引数形式に更新 |
| `docs/example/README.md` | 起動セクションと引数一覧を更新、`.env` 言及を削除 |
| `docs/example/buy_and_hold.py` | docstring の起動例を更新 |
| `docs/wiki/getting-started.md` | replay スクリプト行を更新 |
| `.env.example` | `REPLAY_*` セクションを削除 |
