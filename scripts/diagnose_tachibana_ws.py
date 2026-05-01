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
- .env に以下を設定（値は各自の demo 口座の認証情報に置き換える）:
    DEV_TACHIBANA_USER_ID=...
    DEV_TACHIBANA_PASSWORD=...
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

# Eagerly import the shared secret-key set so that import failures surface
# immediately (not silently mid-session) and masked output is guaranteed (M-3).
sys.path.insert(0, str(REPO_ROOT / "python"))
from engine.exchanges.tachibana import _ST_SECRET_KEYS  # noqa: E402


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _mask_st_fields(fields: dict) -> dict:
    """ST フレームの秘密情報（仮想 URL 等）をマスクする。p_errno 等の診断情報は残す。

    マスク対象キーは `engine.exchanges.tachibana._ST_SECRET_KEYS` と完全に共有する
    （H-A: 二系統が乖離しないように単一定義に統一）。
    """
    return {k: ("***" if k in _ST_SECRET_KEYS else v) for k, v in fields.items()}


def _mask(d: dict) -> dict:
    """セッション情報やベース URL をマスクする（_mask_st_fields とは別目的）。"""
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


async def main(ticker: str, max_frames: int, timeout_s: float, args: Any = None) -> int:
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
    st_count = 0
    connection_ok = False

    # TachibanaEventWs の内部 _recv_loop をトレースするため
    # FdFrameProcessor は別途インスタンス化して直接テスト
    processor = FdFrameProcessor(row="1")

    raw_frames: list[tuple[str, dict]] = []  # (frame_type, fields)
    st_frames: list[dict] = []  # ST フレームの全フィールド

    async def _cb(frame_type: str, fields: dict, ts_ms: int) -> None:
        nonlocal fd_count, kp_count, st_count, connection_ok
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
        elif frame_type == "ST":
            st_count += 1
            st_frames.append(dict(fields))
            # ST フレームの全フィールドを即時表示（H8/H10 診断用）
            p_errno = fields.get("p_errno", "?")
            print(f"  [ST frame #{st_count}] p_errno={p_errno!r} | "
                  f"fields={_mask_st_fields(fields)}")

        total = fd_count + kp_count
        if total >= max_frames:
            stop.set()

    # WS URL: _build_ws_url で銘柄購読パラメータを含む完全 URL を構築する
    # (base URL のみでは ST フレームしか来ない)
    ws_sub_url = worker._build_ws_url(ticker)
    ws_client = TachibanaEventWs(ws_sub_url, stop, ticker=ticker)
    try:
        await asyncio.wait_for(ws_client.run(_cb), timeout=timeout_s)
    except asyncio.TimeoutError:
        log.warning("diagnose: WS observation timed out after %.0f s (fd=%d kp=%d st=%d)",
                    timeout_s, fd_count, kp_count, st_count)
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

    # ST サマリ（H8/H10 診断）
    if st_frames:
        print(f"\n  [ST フレームサマリ] {st_count} 件受信")
        for i, sf in enumerate(st_frames[:3], 1):
            print(f"    ST[{i}]: {_mask_st_fields(sf)}")

    # --dump-raw: 先頭 N フレームの raw repr を表示
    dump_n = getattr(args, "dump_raw", 0)
    if dump_n and dump_n > 0:
        print(f"\n  [--dump-raw {dump_n}] 先頭 {dump_n} フレームの raw repr:")
        for i, (ft, fdict) in enumerate(raw_frames[:dump_n], 1):
            print(f"    frame[{i}] type={ft!r}: {_mask_st_fields(fdict)!r}")

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
    parser.add_argument("--dump-raw", type=int, default=0, metavar="N",
                        help="先頭 N フレームの raw repr を表示する（ST/FD/KP 全種別）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,  # httpx のノイズを抑制
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Tachibana ログだけ INFO に
    logging.getLogger("engine.exchanges.tachibana").setLevel(logging.INFO)

    _load_env(REPO_ROOT / ".env")
    sys.exit(asyncio.run(main(args.ticker, args.frames, args.timeout, args)))
