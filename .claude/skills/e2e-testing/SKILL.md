---
name: e2e-testing
description: flowsurface E2E テストパターン。HTTP API（ポート 9876）経由でアプリを操作し bash スクリプトで検証する。Playwright は使用しない。
origin: ECC (customized for flowsurface)
---

# E2E Testing — flowsurface (Rust + Iced GUI)

flowsurface は GUI アプリ（Iced フレームワーク）のため、Playwright / ブラウザは使用しない。
テストは **TCP :9876 の HTTP API** 経由でアプリを操作し、bash スクリプト + node で JSON を検証する。

---

## アーキテクチャ

```
テストスクリプト (bash + curl + node)
    ↓ HTTP/JSON  (port 9876)
src/replay_api.rs  — TCP リスナー
    ↓ mpsc::Sender<Message>
src/main.rs       — Iced アプリ メッセージハンドラ
    ↓ oneshot チャネル
JSON レスポンス → curl → テストスクリプト
```

**ビルドフラグ**: debug ビルド（`cargo build`）で Tachibana セッション削除エンドポイントが有効になる。

---

## テストファイル構成

```
flowsurface/
├── docs/plan/e2e_scripts/      # 実装済みシナリオスクリプト
│   ├── common_helpers.sh       # 共通ヘルパー（jqn, pass, fail, start_app 等）
│   ├── s1_basic_lifecycle.sh   # リプレイ基本ライフサイクル（廃止 → tests/s1_basic_lifecycle.py）
│   ├── s2_persistence.sh       # saved-state 永続化テスト
│   ├── s3_autoplay.sh          # fixture 自動 play
│   ├── s4_multi_pane_binance.sh
│   ├── s6_mixed_timeframes.sh
│   ├── s8_error_boundary.sh
│   ├── s9_speed_step.sh
│   └── s10_range_end.sh
├── tests/
│   └── e2e_replay_api.sh       # CI 向け統合スクリプト
└── .claude/skills/e2e-test/    # スキル文書（シナリオ・フィクスチャ定義）
    ├── SKILL.md
    ├── api-reference.md
    ├── fixtures.md
    └── scenarios.md
```

---

## ビルド・起動

```bash
# リリースビルド
cargo build --release

EXE="./target/release/flowsurface.exe"
API="http://localhost:9876"
```

---

## 共通ヘルパー

```bash
# jq の代わりに node で JSON パース（Windows 環境向け）
jqn() {
  node -e "
    const d = JSON.parse(process.argv[1]);
    const v = $2;
    console.log(v === null || v === undefined ? 'null' : v);
  " "$1"
}

# テスト集計
PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1 — $2"; FAIL=$((FAIL + 1)); }

# アプリ起動（ログは C:/tmp/e2e_debug.log）
start_app() {
  mkdir -p C:/tmp
  "$EXE" 2>C:/tmp/e2e_debug.log &
  APP_PID=$!
  echo "  app PID=$APP_PID, waiting for API..."
  for i in $(seq 1 30); do
    sleep 1
    curl -sf "$API/api/replay/status" >/dev/null 2>&1 && break
    [ $i -eq 30 ] && { echo "FATAL: API did not start"; exit 1; }
  done
  echo "  API ready"
}

# アプリ終了
stop_app() {
  taskkill //f //im flowsurface.exe >/dev/null 2>&1 || true
  sleep 2
}

# API 呼び出しラッパー
api_get()  { curl -sf "$API$1"; }
api_post() { curl -sf -X POST -H "Content-Type: application/json" -d "${2:-{}}" "$API$1"; }
```

---

## API エンドポイント早見表

### Replay 制御

| メソッド | パス | Body | 用途 |
|---------|------|------|------|
| `GET`  | `/api/replay/status`   | —  | 現在状態の JSON 取得 |
| `POST` | `/api/replay/toggle`   | —  | Live ↔ Replay 切替 |
| `POST` | `/api/replay/play`     | `{"start":"YYYY-MM-DD HH:MM","end":"YYYY-MM-DD HH:MM"}` | 再生開始 |
| `POST` | `/api/replay/pause`    | —  | 一時停止 |
| `POST` | `/api/replay/resume`   | —  | 再開 |
| `POST` | `/api/replay/step-forward`  | — | 最小 timeframe 分前進（Paused 時のみ） |
| `POST` | `/api/replay/step-backward` | — | 前の kline 時刻へジャンプ（Paused 時のみ） |
| `POST` | `/api/replay/speed`    | —  | 速度サイクル（1x→2x→5x→10x→1x） |

### ペイン管理

| メソッド | パス | Body | 用途 |
|---------|------|------|------|
| `GET`  | `/api/pane/list`         | — | ペイン一覧（streams_ready フィールド含む） |
| `POST` | `/api/pane/split`        | `{"pane_id":"<uuid>","axis":"Vertical\|Horizontal"}` | 分割 |
| `POST` | `/api/pane/close`        | `{"pane_id":"<uuid>"}` | 削除 |
| `POST` | `/api/pane/set-ticker`   | `{"pane_id":"<uuid>","ticker":"BinanceLinear:BTCUSDT"}` | ティッカー変更 |
| `POST` | `/api/pane/set-timeframe`| `{"pane_id":"<uuid>","timeframe":"M1\|M5\|H1\|D1"}` | タイムフレーム変更 |
| `POST` | `/api/sidebar/select-ticker` | `{"pane_id":"<uuid>","ticker":"...","kind":null}` | サイドバー選択 |

### アプリ・その他

| メソッド | パス | 用途 |
|---------|------|------|
| `POST` | `/api/app/save`       | 状態をディスクに保存（saved-state.json） |
| `POST` | `/api/app/screenshot` | デスクトップ全体を C:/tmp/screenshot.png に保存 |
| `GET`  | `/api/notification/list` | Toast 通知一覧 |
| `GET`  | `/api/auth/tachibana/status` | Tachibana セッション有無 |

### デバッグビルド専用エンドポイント（`cargo build` debug ビルドのみ）

| メソッド | パス | 用途 |
|---------|------|------|
| `POST` | `/api/test/tachibana/delete-persisted-session` | keyring セッション削除 |

---

## ReplayStatus レスポンス形式

```json
// Live モード
{"mode":"Live","range_start":"","range_end":""}

// Replay モード（再生前）
{"mode":"Replay","range_start":"2026-04-10 09:00","range_end":"2026-04-10 15:00"}

// Replay モード（再生中）
{
  "mode":         "Replay",
  "status":       "Playing|Paused|Loading",
  "current_time": 1775869740288,
  "speed":        "1x|2x|5x|10x",
  "start_time":   1775869740000,
  "end_time":     1775912940000,
  "range_start":  "2026-04-11 01:09",
  "range_end":    "2026-04-11 13:09"
}
```

---

## テストパターン

### 基本テストの骨格

```bash
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common_helpers.sh"

echo "=== シナリオ名 ==="
stop_app
start_app

# --- テスト本体 ---
STATUS=$(api_get /api/replay/status)
MODE=$(jqn "$STATUS" 'd.mode')

if [ "$MODE" = "Live" ]; then
  pass "初期モードは Live"
else
  fail "初期モードは Live であるべき" "got: $MODE"
fi

stop_app
echo "--- $PASS passed, $FAIL failed ---"
[ $FAIL -eq 0 ]
```

### Replay ライフサイクルパターン

```bash
# 1. Replay モードへ切替
api_post /api/replay/toggle

# 2. 再生開始（UTC 時刻を使う）
START=$(date -u -d "2 hours ago" +"%Y-%m-%d %H:%M")
END=$(date -u -d "1 hour ago" +"%Y-%m-%d %H:%M")
api_post /api/replay/play "{\"start\":\"$START\",\"end\":\"$END\"}"

# 3. Playing になるまでポーリング（最大 30 秒）
wait_for_status() {
  local expected="$1" max="${2:-30}"
  for i in $(seq 1 $max); do
    sleep 1
    STATUS=$(api_get /api/replay/status)
    GOT=$(jqn "$STATUS" 'd.status // "none"')
    [ "$GOT" = "$expected" ] && return 0
  done
  fail "status=$expected を待ったがタイムアウト" "last=$GOT"
  return 1
}

wait_for_status "Playing"

# 4. 現在時刻が進んでいることを確認
T1=$(jqn "$(api_get /api/replay/status)" 'd.current_time')
sleep 3
T2=$(jqn "$(api_get /api/replay/status)" 'd.current_time')
[ "$T2" -gt "$T1" ] && pass "current_time が前進" || fail "current_time が停止" "$T1 → $T2"
```

### ステップ実行パターン

```bash
# Paused 状態での StepForward / StepBackward
api_post /api/replay/pause
sleep 1

T_BEFORE=$(jqn "$(api_get /api/replay/status)" 'd.current_time')
api_post /api/replay/step-forward
sleep 1
T_AFTER=$(jqn "$(api_get /api/replay/status)" 'd.current_time')

[ "$T_AFTER" -gt "$T_BEFORE" ] \
  && pass "StepForward: current_time が増加" \
  || fail "StepForward: current_time が変化しない" "$T_BEFORE → $T_AFTER"
```

### ペイン操作パターン

```bash
# ペイン一覧取得 → 最初のペイン ID を取得
PANES=$(api_get /api/pane/list)
PANE_ID=$(node -e "const p=JSON.parse(process.argv[1]); console.log(p[0].id);" "$PANES")

# ティッカー変更
api_post /api/pane/set-ticker "{\"pane_id\":\"$PANE_ID\",\"ticker\":\"BinanceLinear:ETHUSDT\"}"

# ストリームが Ready になるまで待機
wait_for_streams_ready() {
  local pane_id="$1" max="${2:-30}"
  for i in $(seq 1 $max); do
    sleep 1
    PANES=$(api_get /api/pane/list)
    READY=$(node -e "
      const ps = JSON.parse(process.argv[1]);
      const p = ps.find(x => x.id === '$pane_id');
      console.log(p && p.streams_ready ? 'true' : 'false');
    " "$PANES")
    [ "$READY" = "true" ] && return 0
  done
  fail "streams_ready を待ったがタイムアウト" "pane=$pane_id"
  return 1
}

wait_for_streams_ready "$PANE_ID"
```

---

## Auto-play フィクスチャパターン

`saved-state.json` に replay 設定を埋め込んで起動することで、アプリ起動時に自動 Play が走る：

```bash
# フィクスチャ作成（Binance, M1, 過去 2h）
START=$(date -u -d "2 hours ago" +"%Y-%m-%d %H:00")
END=$(date -u -d "30 minutes ago" +"%Y-%m-%d %H:00")

cat > "$APPDATA/flowsurface/saved-state.json" <<EOF
{
  "layout": { "panes": [{"kind":"KlineChart","ticker":"BinanceLinear:BTCUSDT","timeframe":"M1"}] },
  "replay": { "mode": "replay", "range_start": "$START", "range_end": "$END" }
}
EOF

# アプリ起動後、自動で Playing になるまで待機
start_app
wait_for_status "Playing" 60
```

**Auto-play の制約**:
- Binance: 通常数秒で Playing に遷移
- Tachibana（セッション有り）: master download 後に Playing（最大 120s）
- Tachibana（セッション無し）: auto-play は延期され `info` toast が出る

---

## タイミング・待機パターン

```bash
# BAD: 固定 sleep でタイミングに依存
sleep 5
check_status

# GOOD: ポーリングで条件待機
poll_until() {
  local condition="$1" max="${2:-30}" interval="${3:-1}"
  for i in $(seq 1 $max); do
    sleep "$interval"
    eval "$condition" && return 0
  done
  return 1
}

# 例: status が Paused になるまで最大 15 秒待つ
poll_until '[ "$(jqn "$(api_get /api/replay/status)" '"'"'d.status // "none"'"'"')" = "Paused" ]' 15
```

---

## テスト日時の注意

- **Binance**: 過去 24〜48 時間以内のデータのみ取得可能
- 未来の日時を指定すると EventStore が空になり StepForward が no-op になる
- 日時は **UTC** で指定する（アプリ内部も UTC）

```bash
# UTC の現在時刻確認
date -u +"%Y-%m-%d %H:%M"

# 2 時間前〜1 時間前のレンジ（推奨）
START=$(date -u -d "2 hours ago" +"%Y-%m-%d %H:%M")
END=$(date -u -d "1 hour ago" +"%Y-%m-%d %H:%M")
```

---

## ステップ幅（step_size_ms）の仕様

StepForward / StepBackward のステップ幅はアクティブなペインの **最小 timeframe** に依存する：

| アクティブな timeframe | step_size_ms |
|----------------------|-------------|
| M1（または M1+その他） | 60,000 ms（1 分） |
| M5 のみ | 300,000 ms（5 分） |
| H1 のみ | 3,600,000 ms（1 時間） |
| D1 のみ | 86,400,000 ms（1 日） |

---

## テスト実行コマンド

```bash
# 単体シナリオ実行
uv run tests/s1_basic_lifecycle.py          # GUI モード
IS_HEADLESS=true uv run tests/s1_basic_lifecycle.py  # headless モード

# 全シナリオ実行（CI 向け）
bash tests/e2e_replay_api.sh

# デバッグログ確認
cat C:/tmp/e2e_debug.log

# スクリーンショット取得
curl -sf -X POST http://localhost:9876/api/app/screenshot
# → C:/tmp/screenshot.png に保存される
```

---

## よくある問題と対処

### API が応答しない
```bash
# ポート確認
netstat -an | grep 9876

# プロセス確認
tasklist | grep flowsurface

# ログ確認
cat C:/tmp/e2e_debug.log | tail -50
```

### current_time が変化しない
- EventStore が空（未来の日時を指定していないか確認）
- ステータスが `Loading` のまま（データ取得中 → 待機が必要）
- ステータスが `Paused` のまま（`resume` か `step-forward` が必要）

### ペインの streams_ready が false のまま
- Tachibana の場合はセッションが必要（`inject-session` を先に実行）
- ネットワークエラーはデバッグログで確認

### taskkill が失敗する
```bash
# フォースキル（プロセスが残っている場合）
taskkill //f //im flowsurface.exe 2>/dev/null || true
sleep 3  # ポート解放待ち
```

### 既存 GUI アプリとのポート衝突（最重要）

**症状**: テストが「前提条件未達」や「pane count が想定と違う」で失敗する。
`env._start_process()` は起動後すぐに `:9876/api/replay/status` の応答を待つが、
既存アプリが先に応答するため、新プロセスではなく**汚染済みの既存アプリ**に対してテストが走る。

**確認方法**:
```bash
curl -s http://localhost:9876/api/replay/status
# → 応答が返れば既存アプリが起動中
```

**対処**: E2E テストを実行する前に必ず既存プロセスを終了する。
ただし**ユーザーが GUI を開いている可能性があるため、終了前に確認を取ること**。

```bash
# 既存プロセスの確認
tasklist | grep flowsurface

# 終了（確認を得てから実行）
taskkill //f //im flowsurface.exe 2>/dev/null || true
sleep 3  # ポート解放待ち

# その後テスト実行（各テストが自前でプロセスを管理する）
PYTHONIOENCODING=utf-8 uv run tests/s36_sidebar_order_pane.py
```

**headless モードの制限**: `IS_HEADLESS=true` で実行しても既存アプリがポートを握っていれば同じ問題が起きる。
また headless モードでは sidebar API（`/api/sidebar/open-order-pane` 等）が 501 を返すため、
GUI 専用テスト（s36 等）は headless では実行できない。

**GUI モードで複数テストを連続実行する場合の注意**:
s33 など前のテストがペインを追加すると pane count が汚染される。
各テストは `backup_state()` / `restore_state()` + `env._start_process()` / `env.close()` で
プロセスごとクリーンアップする設計なので、テスト間でプロセスを共有しないこと。

---

## 新しいシナリオの追加手順

1. `docs/plan/e2e_scripts/common_helpers.sh` を `source` する
2. `stop_app` → `start_app`（またはフィクスチャ起動）
3. `api_get` / `api_post` で操作
4. `jqn` で JSON をパース → `pass` / `fail` で結果記録
5. `stop_app` でクリーンアップ
6. `[ $FAIL -eq 0 ]` でスクリプト終了コードをテスト結果に連動

---

## CI/CD 連携

```yaml
# .github/workflows/e2e.yml
name: E2E Tests
on: [push, pull_request]

jobs:
  e2e:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Build
        run: cargo build --release
      - name: Run E2E tests
        run: bash tests/e2e_replay_api.sh
      - name: Upload logs on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: e2e-logs
          path: C:/tmp/e2e_debug.log
```

---

## Success Metrics

- 全シナリオスクリプトが `exit 0` で終了する
- `FAIL: 0` が出力される
- デバッグログにパニック・スタックトレースがない
- `current_time` が実際に前進している（数値比較で確認）
- 永続化テスト: アプリ再起動後に状態が復元されている
