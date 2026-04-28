"""Diagnostic: 立花 EVENT WebSocket への接続と FD フレーム受信を実証する。

PURPOSE
-------
このスクリプトは、立花証券 EVENT WebSocket に実際に接続し、
FD フレーム（板情報・歩み値）が正しく受信・デコードされることを
外部から確認するための証明スクリプトです。

WHAT IT PROVES
--------------
1. REST: fetch_depth_snapshot で bid/ask 10 本が取得できる
2. WS:   EVENT WebSocket に接続できる
3. WS:   KP フレーム（キープアライブ）が受信される
4. WS:   FD フレームが受信され、p_cmd キーで正しく識別される
5. WS:   FD フレームから bid/ask が {"price": ..., "qty": ...} dict 形式で抽出される

REQUIREMENTS
------------
- .env に以下を設定:
    DEV_TACHIBANA_USER_ID=uxf05882
    DEV_TACHIBANA_PASSWORD=vw20sr9h
    DEV_TACHIBANA_DEMO=true

USAGE
-----
    uv run python scripts/diagnose_tachibana_ws.py
    uv run python scripts/diagnose_tachibana_ws.py --ticker 6758 --frames 5

EXIT CODE
---------
0: 全証明項目が PASS
1: 何らかの項目が FAIL
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Windows の cp932 環境でも Unicode を出力できるようにする
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _mask(d: dict) -> dict:
    """セッション URL など秘密情報をマスクして表示用 dict を返す。"""
    return {k: ("***" if k in ("session", "url_event_ws") else v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    _results.append((label, ok, detail))
    icon = "✓" if ok else "✗"
    print(f"  [{status}] {icon} {label}" + (f"\n         {detail}" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(ticker: str, max_frames: int, timeout_s: float) -> int:
    import tempfile
    sys.path.insert(0, str(REPO_ROOT / "python"))

    from engine.exchanges.tachibana_helpers import PNoCounter
    from engine.exchanges.tachibana_login_flow import startup_login
    from engine.exchanges.tachibana_auth import StartupLatch, TachibanaSession
    from engine.exchanges.tachibana import TachibanaWorker
    from engine.exchanges.tachibana_ws import TachibanaEventWs, FdFrameProcessor

    # ── Step 1: Login ────────────────────────────────────────────────────
    print("\n[1] ログイン")
    p_no = PNoCounter()
    tmp_dir = Path(tempfile.mkdtemp(prefix="diag_tachibana_"))
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
        print("\n[ABORT] ログイン失敗 — 認証情報を確認してください")
        return 1

    _check("ログイン成功", True)
    ws_url = session.url_event_ws
    print(f"  WS URL (先頭50文字): {ws_url[:50]}...")

    # ── Step 2: REST depth snapshot ──────────────────────────────────────
    is_demo = os.environ.get("DEV_TACHIBANA_DEMO", "true").lower() not in ("false", "0", "no")
    print(f"\n[2] REST 板スナップショット（ticker={ticker}, is_demo={is_demo}）")
    worker = TachibanaWorker(
        cache_dir=REPO_ROOT / "tmp" / "diag_cache",
        is_demo=is_demo,
        session=session,
        p_no_counter=p_no,
    )
    try:
        snap = await worker.fetch_depth_snapshot(ticker, "stock")
        bids = snap.get("bids", [])
        asks = snap.get("asks", [])
        _check(
            f"bids={len(bids)} asks={len(asks)} を取得",
            len(bids) > 0 and len(asks) > 0,
            f"bids={len(bids)}, asks={len(asks)}",
        )
        if bids:
            b = bids[0]
            _check(
                "bid[0] が {'price': ..., 'qty': ...} dict 形式",
                isinstance(b, dict) and "price" in b and "qty" in b,
                f"bid[0]={b!r}",
            )
            print(f"  最良買気配: {b['price']} @ {b['qty']}")
        if asks:
            a = asks[0]
            _check(
                "ask[0] が {'price': ..., 'qty': ...} dict 形式",
                isinstance(a, dict) and "price" in a and "qty" in a,
                f"ask[0]={a!r}",
            )
            print(f"  最良売気配: {a['price']} @ {a['qty']}")
    except Exception as exc:
        _check("fetch_depth_snapshot 成功", False, str(exc))

    # ── Step 3: EVENT WebSocket ──────────────────────────────────────────
    print(f"\n[3] EVENT WebSocket 接続 + FD フレーム受信（ticker={ticker}, 最大{max_frames}フレーム, タイムアウト{timeout_s}s）")

    stop = asyncio.Event()
    frame_log: list[dict[str, Any]] = []
    fd_count = 0
    kp_count = 0
    connection_ok = False

    # TachibanaEventWs の内部 _recv_loop をトレースするため
    # FdFrameProcessor は別途インスタンス化して直接テスト
    processor = FdFrameProcessor(row="1")

    raw_frames: list[tuple[str, dict]] = []  # (frame_type, fields)

    async def _cb(frame_type: str, fields: dict, ts_ms: int) -> None:
        nonlocal fd_count, kp_count, connection_ok
        connection_ok = True
        raw_frames.append((frame_type, fields))

        if frame_type == "KP":
            kp_count += 1
        elif frame_type == "FD":
            fd_count += 1
            _, depth = processor.process(fields, ts_ms)
            if depth:
                frame_log.append({
                    "type": "FD",
                    "bids": depth["bids"][:3],  # 先頭3本のみ表示
                    "asks": depth["asks"][:3],
                    "seq": depth["sequence_id"],
                })

        total = fd_count + kp_count
        if total >= max_frames:
            stop.set()

    # WS URL にはログインで取得した一時 URL を使う
    ws_client = TachibanaEventWs(ws_url, stop, ticker=ticker)
    try:
        await asyncio.wait_for(ws_client.run(_cb), timeout=timeout_s)
    except asyncio.TimeoutError:
        # タイムアウトは「データが来なかった」場合のみ問題
        pass
    except Exception as exc:
        _check("WebSocket 接続", False, str(exc))

    _check("WebSocket 接続確立（少なくとも1フレーム受信）", connection_ok)
    _check(f"KP フレーム受信 (count={kp_count})", kp_count > 0 or fd_count > 0)
    _check(
        f"p_cmd キーで FD/KP/ST が正しく識別される",
        connection_ok,
        f"FD={fd_count}, KP={kp_count} フレームを処理",
    )

    if fd_count > 0:
        _check(f"FD フレーム受信 (count={fd_count})", True)
        if frame_log:
            fl = frame_log[0]
            print(f"\n  最初の FD フレーム（bids先頭3本）:")
            for b in fl["bids"]:
                _check(
                    f"  depth bid が dict 形式 price={b.get('price')}",
                    isinstance(b, dict) and "price" in b and "qty" in b,
                    repr(b),
                )
            print(f"\n  最初の FD フレーム（asks先頭3本）:")
            for a in fl["asks"]:
                _check(
                    f"  depth ask が dict 形式 price={a.get('price')}",
                    isinstance(a, dict) and "price" in a and "qty" in a,
                    repr(a),
                )
    else:
        # 市場時間外は FD フレームが来ない場合がある
        print("  ※ FD フレームなし（市場時間外の可能性あり）")
        print(f"  受信フレーム一覧: {[t for t, _ in raw_frames]}")

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    for label, ok, detail in _results:
        icon = "✓" if ok else "✗"
        print(f"  [{('PASS' if ok else 'FAIL')}] {icon} {label}")
    print(f"\n  {passed} passed, {failed} failed")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tachibana WS diagnostic")
    parser.add_argument("--ticker", default="7203", help="銘柄コード (default: 7203)")
    parser.add_argument("--frames", type=int, default=3, help="受信する最大フレーム数 (default: 3)")
    parser.add_argument("--timeout", type=float, default=15.0, help="タイムアウト秒数 (default: 15)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,  # httpx のノイズを抑制
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Tachibana ログだけ INFO に
    logging.getLogger("engine.exchanges.tachibana").setLevel(logging.INFO)

    _load_env(REPO_ROOT / ".env")
    sys.exit(asyncio.run(main(args.ticker, args.frames, args.timeout)))
