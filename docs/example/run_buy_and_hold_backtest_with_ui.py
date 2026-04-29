"""Rust UI 起動 + BuyAndHold バックテスト全自動スクリプト (N1.3 / N1.11 / N1.14 / N1.17)。

headless 版 (run_buy_and_hold_backtest.py) と対になる UI バージョン。

## このスクリプトがやること

    1. Python エンジン (port 19876) をサブプロセスで起動
    2. flowsurface を `--mode replay` でサブプロセス起動（Rust UI ウィンドウが開く）
    3. GET /api/replay/status でアプリ起動完了を待機
    4. POST /api/replay/start で戦略起動（StartEngine IPC）
       → EngineStarted を受け次第 202 Accepted が返る (N1.17)
       → Rust UI に Tick pane / Candlestick pane / 注文一覧 pane が自動生成される (N1.14)
    5. POST /api/replay/control で再生速度を設定
    6. GET /api/replay/portfolio でポートフォリオ状態を取得・表示

## headless 版との違い

    | 項目 | headless 版 | UI 版（本スクリプト） |
    |------|------------|----------------------|
    | Rust UI | 起動しない | 起動する (ウィンドウが開く) |
    | 時刻 | 仮想時刻 (wall clock 非参照) | wall-clock pacing (1x/10x/100x) |
    | 経路 | `start_backtest_replay()` | `start_backtest_replay_streaming()` |
    | 決定論性 | 保証 | pacing により保証しない |

## 前提

    - `cargo build --release` 済み (target/release/flowsurface.exe が存在)
    - `uv sync` 済み
    - `S:/j-quants/` に J-Quants 月次 CSV が存在する
      (`equities_bars_daily_YYYYMM.csv.gz` / `equities_trades_YYYYMM.csv.gz`)

## 実行

    uv run python docs/example/run_buy_and_hold_backtest_with_ui.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# python/ をモジュールパスに足す
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

# ── パラメータ ────────────────────────────────────────────────────────────────

INSTRUMENT_ID = "7203.TSE"
GRANULARITY = "Daily"
START_DATE = "2024-01-01"
END_DATE = "2024-12-31"
STRATEGY_ID = "buy-and-hold"
INITIAL_CASH = "1000000"    # decimal string — float 丸め防止
REPLAY_SPEED = 100          # 1=等速, 10=10倍速, 100=100倍速

ENGINE_PORT = 19876
API_PORT = 9876
ENGINE_TOKEN = "dev-token"

BINARY = _REPO_ROOT / "target" / "release" / "flowsurface.exe"
STARTUP_TIMEOUT_S = 30      # /api/replay/status が応答するまでの最大待機秒数
START_TIMEOUT_S = 60        # /api/replay/start で EngineStarted を待つ最大秒数
LOAD_OBSERVE_S = 5          # start 後に UI を観察する秒数


# ── HTTP ヘルパー ─────────────────────────────────────────────────────────────

def _http(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}


def _wait_for_api(timeout_s: int) -> bool:
    url = f"http://127.0.0.1:{API_PORT}/api/replay/status"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, _ = _http("GET", url)
        if status == 200:
            return True
        time.sleep(0.5)
    return False


# ── メイン ────────────────────────────────────────────────────────────────────

def main() -> int:
    if not BINARY.exists():
        print(
            f"[ERROR] {BINARY} が見つかりません。\n"
            "       先に `cargo build --release` を実行してください。"
        )
        return 1

    print(f"=== BuyAndHold (Rust UI): {INSTRUMENT_ID} "
          f"{START_DATE}..{END_DATE} granularity={GRANULARITY} ===")

    # ① Python エンジン起動
    print(f"\n[1/6] Python エンジン起動 (port={ENGINE_PORT}) …")
    env_engine = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "python")}
    engine_proc = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "engine",
            "--port", str(ENGINE_PORT),
            "--token", ENGINE_TOKEN,
            "--mode", "replay",
        ],
        cwd=str(_REPO_ROOT),
        env=env_engine,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)  # エンジン初期化待ち

    # ② Rust UI 起動
    print(f"[2/6] Rust UI 起動 (--mode replay) …")
    env_ui = {
        **os.environ,
        "FLOWSURFACE_ENGINE_TOKEN": ENGINE_TOKEN,
    }
    ui_proc = subprocess.Popen(
        [
            str(BINARY),
            "--mode", "replay",
            "--data-engine-url", f"ws://127.0.0.1:{ENGINE_PORT}/",
        ],
        cwd=str(_REPO_ROOT),
        env=env_ui,
    )

    try:
        # ③ API 起動待機
        print(f"[3/6] /api/replay/status 待機 (最大 {STARTUP_TIMEOUT_S}s) …")
        if not _wait_for_api(STARTUP_TIMEOUT_S):
            print(f"[ERROR] {STARTUP_TIMEOUT_S}s 以内に API が応答しませんでした。")
            return 1
        print("       → OK")

        # ④ 戦略起動（StartEngine IPC 経由）
        print(f"\n[4/6] POST /api/replay/start (strategy={STRATEGY_ID}) …")
        t0 = time.time()
        status, resp = _http(
            "POST",
            f"http://127.0.0.1:{API_PORT}/api/replay/start",
            {
                "instrument_id": INSTRUMENT_ID,
                "start_date": START_DATE,
                "end_date": END_DATE,
                "granularity": GRANULARITY,
                "strategy_id": STRATEGY_ID,
                "initial_cash": INITIAL_CASH,
            },
        )
        elapsed_start = time.time() - t0
        if status != 202:
            print(f"[ERROR] HTTP {status}: {resp}")
            return 1

        account_id = resp.get("account_id", "")
        print(f"       → HTTP {status} in {elapsed_start:.2f}s")
        print(f"          strategy_id={resp.get('strategy_id')}  account_id={account_id}")
        print("          Rust UI に pane が自動生成されました (N1.14)")

        # ⑤ 再生速度設定
        print(f"\n[5/6] POST /api/replay/control (speed={REPLAY_SPEED}x) …")
        status_ctrl, _ = _http(
            "POST",
            f"http://127.0.0.1:{API_PORT}/api/replay/control",
            {"action": "speed", "multiplier": REPLAY_SPEED},
        )
        print(f"       → HTTP {status_ctrl}")

        # ⑥ ポートフォリオ確認（streaming 再生中は not_ready の可能性あり）
        print(f"\n[6/6] GET /api/replay/portfolio …")
        _, portfolio = _http("GET", f"http://127.0.0.1:{API_PORT}/api/replay/portfolio")
        print(f"       → {portfolio}")

        # 結果表示
        print()
        print("=== RESULT ===")
        print(f"  instrument    : {INSTRUMENT_ID}")
        print(f"  period        : {START_DATE} .. {END_DATE}")
        print(f"  granularity   : {GRANULARITY}")
        print(f"  start_elapsed : {elapsed_start:.2f} sec")
        print(f"  strategy_id   : {resp.get('strategy_id')}")
        print(f"  account_id    : {account_id}")
        print(f"  replay_speed  : {REPLAY_SPEED}x")
        portfolio_status = portfolio.get("status", "unknown") if portfolio else "unknown"
        if portfolio_status not in ("not_ready", "unknown"):
            print(f"  cash          : {portfolio.get('cash')}")
            print(f"  equity        : {portfolio.get('equity')}")
        else:
            print(f"  portfolio     : {portfolio_status} (streaming 再生中は約定後に確定)")

        print()
        print(f"Rust UI ウィンドウを {LOAD_OBSERVE_S}s 観察中 … (Ctrl+C で中断可)")
        time.sleep(LOAD_OBSERVE_S)

    finally:
        print("\n[cleanup] プロセスを終了します …")
        ui_proc.terminate()
        engine_proc.terminate()
        try:
            ui_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ui_proc.kill()
        try:
            engine_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            engine_proc.kill()
        print("[cleanup] 完了")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
