"""Tpre.5 — 立花デモ口座で実 EC フレームをキャプチャする。

PURPOSE
-------
実際の発注 → 約定フローを通して EVENT WebSocket から EC フレームを受信し、
生のフィールド値を画面表示 + JSONL ファイルに保存する。

これにより:
  - EC パーサ実装（tachibana_event.py）が実フレームで正しく動くことを証明
  - Tpre.5「実 frame キャプチャ」の資料を取得
  - 将来のリグレッションテスト用サンプルを samples/ に保存

FLOW
----
  1. デモ口座にログイン（startup_login）
  2. EVENT WebSocket に接続して受信タスクを起動
  3. 成行買い（現物）を発注（submit_order / CLMKabuNewOrder）
  4. EC フレームが届いたら内容を表示＆保存
  5. 受け入れ/約定/取消/失効/拒否の各 notification_type を 180 秒待って受信

REQUIREMENTS
------------
  .env:
    DEV_TACHIBANA_USER_ID=<id>
    DEV_TACHIBANA_PASSWORD=<pass>
    DEV_TACHIBANA_DEMO=true
    DEV_TACHIBANA_SECOND_PASSWORD=<2nd pass>

USAGE
-----
  set -a && source .env && set +a
  uv run python scripts/capture_ec_frames.py
  uv run python scripts/capture_ec_frames.py --ticker 7203 --qty 100 --out /tmp/ec_frames.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# EC 通知種別
_NT_LABELS = {
    "1": "受付",
    "2": "約定",
    "3": "取消",
    "4": "失効",
}


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_results: list[tuple[str, bool, str]] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    icon = "✓" if ok else "✗"
    _results.append((label, ok, detail))
    print(f"  [{status}] {icon} {label}" + (f"\n         {detail}" if detail else ""))
    return ok


async def main(ticker: str, qty: int, out_path: Path, timeout_s: float) -> int:
    sys.path.insert(0, str(REPO_ROOT / "python"))

    from engine.exchanges.tachibana_auth import StartupLatch, TachibanaSession
    from engine.exchanges.tachibana_helpers import PNoCounter
    from engine.exchanges.tachibana_login_flow import startup_login
    from engine.exchanges.tachibana_orders import NautilusOrderEnvelope, submit_order

    second_password = os.environ.get("DEV_TACHIBANA_SECOND_PASSWORD", "")
    if not second_password:
        print("SKIP: DEV_TACHIBANA_SECOND_PASSWORD が未設定。.env を読み込んでください。")
        return 77

    # ── Step 1: ログイン ──────────────────────────────────────────────────────
    print("\n[1] デモ口座ログイン")
    p_no = PNoCounter()
    tmp_dir = Path(tempfile.mkdtemp(prefix="capture_ec_"))
    try:
        session: TachibanaSession = await startup_login(
            config_dir=tmp_dir,
            cache_dir=tmp_dir / "cache",
            p_no_counter=p_no,
            startup_latch=StartupLatch(),
            dev_login_allowed=True,
        )
    except Exception as exc:
        _check("ログイン成功", False, str(exc))
        return 1

    _check("ログイン成功", True)
    ws_url = session.url_event_ws
    print(f"  EVENT WS URL (先頭50文字): {ws_url[:50]}...")

    # ── Step 2: EVENT WebSocket 受信タスクを起動 ──────────────────────────────
    print("\n[2] EVENT WebSocket 接続")

    captured_frames: list[dict[str, Any]] = []
    ec_received = asyncio.Event()

    async def _on_frame(frame_type: str, event: Any) -> None:
        if frame_type != "EC":
            return
        nt = event.notification_type
        label = _NT_LABELS.get(nt, f"不明({nt})")
        record = {
            "notification_type": nt,
            "notification_label": label,
            "venue_order_id": event.venue_order_id,
            "trade_id": event.trade_id,
            "last_price": event.last_price,
            "last_qty": event.last_qty,
            "leaves_qty": event.leaves_qty,
            "ts_event_ms": event.ts_event_ms,
        }
        captured_frames.append(record)
        print(f"\n  [EC フレーム受信] notification_type={nt}({label})")
        for k, v in record.items():
            print(f"    {k}: {v!r}")
        ec_received.set()

    # TachibanaEventClient + websockets で EVENT WS に接続
    from engine.exchanges.tachibana_event import TachibanaEventClient
    import websockets

    ec_client = TachibanaEventClient()
    ws_ok = False

    async def _ws_task() -> None:
        nonlocal ws_ok
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=None,
                max_size=2**20,
            ) as ws:
                ws_ok = True
                print("  EVENT WebSocket 接続確立")
                await ec_client.receive_loop(ws, _on_frame)
        except Exception as exc:
            if not ws_ok:
                _check("EVENT WebSocket 接続", False, str(exc))

    ws_task = asyncio.create_task(_ws_task())
    # WS が接続するまで少し待つ
    await asyncio.sleep(1.5)
    _check("EVENT WebSocket 接続", ws_ok)

    # ── Step 3: 成行買いを発注 ────────────────────────────────────────────────
    instrument_id = f"{ticker}.TSE"
    print(f"\n[3] 発注: {instrument_id} 現物 成行 買 {qty} 株")

    import time as _time
    client_order_id = f"ec-capture-{int(_time.time())}"
    order = NautilusOrderEnvelope(
        client_order_id=client_order_id,
        instrument_id=instrument_id,
        order_side="BUY",
        order_type="MARKET",
        quantity=str(qty),
        time_in_force="DAY",
        post_only=False,
        reduce_only=False,
        tags=["cash_margin=cash"],
    )

    try:
        result = await submit_order(
            session,
            second_password,
            order,
            p_no_counter=p_no,
        )
        _check(
            f"発注成功 venue_order_id={result.venue_order_id}",
            result.venue_order_id is not None,
            f"client_order_id={client_order_id}",
        )
        print(f"  venue_order_id: {result.venue_order_id}")
        print(f"  status: {result.status}")
    except Exception as exc:
        _check("発注成功", False, str(exc))
        ws_task.cancel()
        return 1

    # ── Step 4: EC フレームを待機 ─────────────────────────────────────────────
    print(f"\n[4] EC フレーム待機 (タイムアウト {timeout_s:.0f} 秒)...")
    try:
        await asyncio.wait_for(ec_received.wait(), timeout=timeout_s)
        _check("EC フレームを受信", True, f"{len(captured_frames)} フレーム")
    except asyncio.TimeoutError:
        _check("EC フレームを受信", False, f"{timeout_s:.0f} 秒以内に EC フレームが届きませんでした")

    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass

    # ── Step 5: 保存 ──────────────────────────────────────────────────────────
    if captured_frames:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as f:
            for frame in captured_frames:
                f.write(json.dumps(frame, ensure_ascii=False) + "\n")
        print(f"\n[5] {len(captured_frames)} EC フレームを保存: {out_path}")
        _check("JSONL 保存", True, str(out_path))
    else:
        print("\n[5] 保存スキップ (EC フレームなし)")

    # ── 結果サマリ ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"結果: {passed}/{total} PASS, {failed} FAIL")
    return 0 if failed == 0 else 1


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="立花デモ口座 EC フレームキャプチャ")
    p.add_argument("--ticker", default="7203", help="銘柄コード (デフォルト: 7203=Toyota)")
    p.add_argument("--qty", type=int, default=100, help="発注数量 (デフォルト: 100株)")
    p.add_argument(
        "--out",
        default=str(REPO_ROOT / "tmp" / "ec_frames_captured.jsonl"),
        help="JSONL 保存先",
    )
    p.add_argument("--timeout", type=float, default=180.0, help="EC 受信タイムアウト秒 (デフォルト: 180)")
    return p.parse_args()


if __name__ == "__main__":
    _load_env(REPO_ROOT / ".env")
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    user_id = os.environ.get("DEV_TACHIBANA_USER_ID", "")
    password = os.environ.get("DEV_TACHIBANA_PASSWORD", "")
    is_demo_raw = os.environ.get("DEV_TACHIBANA_DEMO", "")

    if not user_id or not password:
        print("SKIP: DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD が未設定。")
        print("  set -a && source .env && set +a  でから実行してください。")
        sys.exit(77)

    if is_demo_raw.lower() not in ("1", "true", "yes", "on"):
        print("ABORT: DEV_TACHIBANA_DEMO が truthy ではありません。デモ口座のみ許可します。")
        sys.exit(4)

    os.environ["FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED"] = "1"

    args = _parse_args()
    rc = asyncio.run(main(args.ticker, args.qty, Path(args.out), args.timeout))
    sys.exit(rc)
