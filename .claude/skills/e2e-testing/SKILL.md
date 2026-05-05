---
name: e2e-testing
description: e-station E2E テストパターン。Python helper（`ReplaySession` / `LiveSession`）+ pytest でアプリを操作する。HTTP API（旧 9876）と Playwright は使用しない。
origin: ECC (customized for e-station)
---

# E2E Testing — e-station (Rust + Iced GUI / Python data engine)

e-station は Rust GUI + Python データエンジン構成。Phase 8.3（2026-05-03）で
Rust 側 HTTP control API（旧ポート 9876）は完全廃止された。

E2E は **WebSocket IPC（:19876）** に attach する Python helper class を使う。
pytest からは `engine.replay_session.ReplaySession` / `LiveSession` を直接 import する。
Playwright / ブラウザは GUI が Iced のため使用しない。

---

## アーキテクチャ

```
pytest / notebook / examples
    ↓ import
engine.replay_session.ReplaySession / LiveSession
    ↓ __enter__ で :19876 を probe
┌────────────────────┬────────────────────┐
│ in-process mode    │ attach mode        │
│ (engine 不在)      │ (GUI 起動済み)     │
│                    │                    │
│ NautilusRunner を  │ _AttachClient で   │
│ helper 内で直接    │ WS :19876 に接続   │
│ 起動               │ Command を送信     │
└────────────────────┴────────────────────┘
                          ↓ event は GUI / helper 両方に fanout
                       Iced GUI チャート
```

ポイント：

- helper は **WS server を bind しない**。`:19876` を listen するのは GUI 起動 engine だけ
- public API は `dict` for events のみ。`Command` 列挙体や IPC schema を expose しない
- attach mode の token 解決順序：明示引数 → `engine-session.json` → `FLOWSURFACE_ENGINE_TOKEN` env → in-process fallback

> **削除済み**: HTTP API（`/api/replay/*` / `/api/order/*` / `/api/agent/*` / `/api/sidebar/*` /
> `/api/pane/*` / `/api/app/*`）と関連 bash + curl テストは Phase 8.3 で全廃された。
> 既存の `scripts/run-replay-debug.sh` / `scripts/replay_dev_load.sh` も廃止（残骸が残っているが機能しない）。

---

## テストファイル構成

```
e-station/
├── tests/e2e/                          # 維持される少数の bash smoke
│   ├── smoke.sh                        # 30s 観測（GUI プロセス起動・観測）
│   └── s55_mode_startup_smoke.sh       # --mode 必須化の dry-run smoke
├── python/tests/                       # pytest（メインの E2E）
│   ├── test_replay_session*.py         # ReplaySession helper
│   ├── test_live_session_login.py      # LiveSession.login() smoke（@pytest.mark.live）
│   ├── test_server_multi_client.py     # multi-client broadcast / FCFS
│   ├── test_engine_busy_reject.py      # state guard
│   └── ...
└── engine-client/tests/                # Rust 側 IPC クライアント統合テスト
    ├── handshake.rs
    ├── session_file.rs                 # engine-session.json atomic write / Drop 削除
    └── ...
```

---

## ReplaySession を使った pytest パターン

### in-process mode（GUI 不要・最も典型的）

```python
import pytest
from engine.replay_session import ReplaySession

def test_buy_and_hold_runs_to_completion(tmp_path):
    events: list[dict] = []
    with ReplaySession() as s:
        assert s.mode == "inprocess"   # engine が居なければ in-process
        s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
        s.run(
            strategy_file="examples/test_strategy_daily.py",
            on_event=events.append,
            initial_cash=1_000_000,
        )

    # 終了状態の検証
    assert s.status == "stopped"
    assert any(e.get("type") == "ReplayBuyingPower" for e in events)
    assert s.portfolio is not None
```

### attach mode（GUI 起動中の helper 並走）

GUI を立ち上げてから helper を attach する手順（手動デモ・人手確認）：

```bash
# ターミナル1: GUI を replay モードで起動。engine-session.json が
#   %APPDATA%\flowsurface\ に書かれるまで待つ（通常 5〜15s）。
cargo run -- --mode replay

# ターミナル2: helper を attach mode で走らせる。
uv run python -m engine.replay_session run \
    --strategy examples/test_strategy_daily.py \
    --instrument 1301.TSE --start 2025-01-06 --end 2025-03-31 \
    --mode auto
```

完全な観測点リスト（pane 自動生成・bar 蓄積・EngineBusy・session ファイル
の Drop 削除）は [python/tests/test_replay_session_attach_manual_smoke.md](../../../python/tests/test_replay_session_attach_manual_smoke.md) にある。
ユーザー向けの解説は [docs/wiki/backtest.md](../../../docs/wiki/backtest.md)。

pytest から attach mode を使う例：

```python
def test_helper_attaches_to_running_gui():
    # GUI 側で `cargo run -- --mode replay` 起動済み
    # 同じ engine-session.json / FLOWSURFACE_ENGINE_TOKEN を共有する
    with ReplaySession(force_mode="attach") as s:
        assert s.mode == "attach"
        s.load("1301.TSE", "2025-01-06", "2025-03-31")
        # event は GUI チャートと pytest 両方に流れる
        s.run(strategy_file="examples/buy_and_hold.py", on_event=lambda e: None)
```

> stderr に `Subscribe: unknown venue 'replay'` が出るのは仕様（[AGENTS.md](../../../AGENTS.md) §replay 参照）。
> Rust 側の Subscribe は Python が拒否するが、bar は streaming `KlineUpdate` で届く。

### 速度変更・中断

```python
import threading

def test_speed_change_and_stop():
    received = []
    with ReplaySession() as s:
        s.load("1301.TSE", "2025-01-06", "2025-03-31")

        def stopper():
            time.sleep(0.5)
            s.set_speed(10)
            time.sleep(0.5)
            s.stop()

        threading.Thread(target=stopper, daemon=True).start()
        s.run(strategy_file="strategy.py", on_event=received.append)
    assert s.status in ("stopped", "errored")
```

### EngineBusy（state guard）の検証

```python
from engine.replay_session import BusyError

def test_load_during_running_raises_busy():
    with ReplaySession() as s:
        s.load("1301.TSE", "2025-01-06", "2025-03-31")
        # … run() 中に外から load を投げると BusyError
```

---

## LiveSession を使ったパターン（立花ログイン）

`LiveSession.login()` は HTTP `/api/sidebar/tachibana/request-login` の置き換え。
`@pytest.mark.live` で marker を付けて CI 既定では除外する。

```python
@pytest.mark.live
def test_tachibana_demo_login(monkeypatch):
    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", os.environ["TEST_USER_ID"])
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", os.environ["TEST_PASSWORD"])
    monkeypatch.setenv("DEV_TACHIBANA_DEMO", "true")

    with LiveSession(venue="tachibana", demo=True) as s:
        s.login()              # 立花 demo にログイン
        # 後続: order 系（Phase 8.1 後半で実装予定）
```

> Phase 8.1a 時点では `LiveSession.login()` / order 系は in-process スタブ。
> Phase 8.1 後半で本実装される予定。

---

## token / engine-session の取り扱い

attach mode テストでは GUI 起動 engine から token を継承する必要がある。

| 経路 | 解決順位 |
|------|---------|
| 明示引数 | `ReplaySession(attach_endpoint="ws://127.0.0.1:19876/")` + env |
| session ファイル | `%APPDATA%\flowsurface\engine-session.json` の `{port, token, pid, schema_major}` |
| env | `FLOWSURFACE_ENGINE_TOKEN` |

helper は順に試して、いずれも取れなければ in-process にフォールバックする。

`engine-session.json` は engine プロセスが `Drop` 時に削除する（`engine-client/src/session_file.rs`）。
**手動でこのファイルを書き換えないこと**。

---

## 維持される bash smoke

WS IPC ハンドシェイク／プロセス起動の dry-run は依然 bash で運用：

```bash
# 30 秒観測（デフォルト）
bash tests/e2e/smoke.sh

# 2 分観測
OBSERVE_S=120 bash tests/e2e/smoke.sh

# --mode 必須化の dry-run
bash tests/e2e/s55_mode_startup_smoke.sh
```

`smoke.sh` は次を検査する：
- 15 秒以内にハンドシェイク完了
- `engine ws read error` が出ない（圧縮設定の MISSES 再発防止）
- 観測ウィンドウ中の再接続が 2 回以下
- DepthGap・parse error・snapshot fetch failed が出ない

---

## テスト実行コマンド

```bash
# Python 側 E2E（live マーカー除く）
uv run pytest python/tests/ -v

# live マーカーも実行（実 venue 接続あり）
uv run pytest python/tests/ -v -m live

# 単一テスト
uv run pytest python/tests/test_replay_session_attach.py -v

# Rust 側 IPC クライアント統合
cargo test -p flowsurface-engine-client

# bash smoke
bash tests/e2e/smoke.sh
```

---

## よくある問題と対処

### `:19876` に何も居ない（in-process にフォールバックされる）

`force_mode="attach"` を指定していない限り、`__enter__` は warn ログを出して
in-process mode に切り替える。CLAUDE.md の「外部エンジンに接続する際のトークン認証」
を確認すること。

### `ConnectionRefusedError` が attach mode で出る

token 不一致 / `SCHEMA_MAJOR` 不一致 / handshake timeout のいずれか。
- engine 起動時の `--token` と `FLOWSURFACE_ENGINE_TOKEN` が一致しているか確認
- `engine-client/src/lib.rs` と `python/engine/schemas.py` の `SCHEMA_MAJOR` が一致しているか確認

### 接続 4 つ越え（`MAX_CONNECTIONS=4`）

5 本目の helper を立てると `1008 Policy Violation` で reject される。
不要な helper プロセスを終了する。

### GUI チャートに pane が出ない（attach mode 時）

`saved-state.json` は replay モードで読み書きされない（D9）。
`ReplayDataLoaded` 受信後に `auto_generate_replay_panes` が pane を作るので、
empty pane grid から始まる前提でテストを書くこと。

### NautilusRunner 二重起動（構造的に禁止）

helper は `:19876` を probe してから spawn / attach を分岐する。probe をスキップする
独自実装は書かないこと。

---

## CI/CD 連携

```yaml
# .github/workflows/e2e.yml
name: E2E Tests
on: [push, pull_request]

jobs:
  pytest:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: astral-sh/setup-uv@v3
      - name: Run pytest (excluding live)
        run: uv run pytest python/tests/ -v -m "not live"
      - name: Run Rust IPC tests
        run: cargo test -p flowsurface-engine-client
      - name: Run smoke
        run: bash tests/e2e/smoke.sh
```

---

## 新シナリオ追加手順

1. `python/tests/` 配下に `test_*.py` を追加
2. `with ReplaySession() as s:` または `with LiveSession(...) as s:` で開始
3. `on_event` callback で event を受けて assert を書く
4. 実 venue 依存なら `@pytest.mark.live` を付ける
5. `s.status` / `s.portfolio` / `s.mode` プロパティで状態を検証
6. 必要なら attach mode テストを別途追加（GUI 起動 fixture）

---

## Success Metrics

- pytest が緑（live マーカー除く）
- `engine-session.json` が test 終了後に残らない（engine Drop で削除）
- `cargo test -p flowsurface-engine-client` が緑
- `tests/e2e/smoke.sh` が観測ウィンドウ中に再接続 2 回以下で完走
