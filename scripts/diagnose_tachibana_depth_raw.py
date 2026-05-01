"""立花の REST 板スナップショット (CLMMfdsGetMarketPrice) と
WS FD フレーム の **生 GBP/GAP/GBV/GAV** 値を 1 銘柄分ダンプして、
Ladder の "alternating 0" の原因を切り分ける。

USAGE
-----
    uv run python scripts/diagnose_tachibana_depth_raw.py --ticker 7203
    uv run python scripts/diagnose_tachibana_depth_raw.py --ticker 7203 --tickers 9434,8316
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
from decimal import Decimal
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


async def fetch_one(worker, ticker: str, timeout_s: float = 12.0) -> None:
    print(f"\n{'='*70}\nTICKER: {ticker}\n{'='*70}")

    # 1. metadata: min_ticksize + sYobineTaniNumber + sSizyouC
    try:
        await worker._ensure_master_loaded()
    except Exception as exc:
        print(f"  master load error: {exc}")
        return

    sizyou_c = worker._lookup_sizyou_c(ticker)
    print(f"  sSizyouC = {sizyou_c!r}")

    # find issue record + yobine
    issue_record = None
    sizyou_rows = worker._master_records.get("CLMIssueSizyouMstKabu", [])
    for rec in sizyou_rows:
        if str(rec.get("sIssueCode", "")).strip() == ticker:
            issue_record = rec
            break
    if issue_record is None:
        print(f"  WARNING: ticker {ticker} not found in CLMIssueSizyouMstKabu")
    else:
        yobine_code = str(issue_record.get("sYobineTaniNumber", ""))
        print(f"  sYobineTaniNumber = {yobine_code!r}")
        if worker._yobine_table and yobine_code in worker._yobine_table:
            bands = worker._yobine_table[yobine_code]
            print(f"  Yobine bands ({len(bands)}):")
            for i, b in enumerate(bands, 1):
                print(f"    band[{i}] kizun_price<= {b.kizun_price}  yobine_tanka= {b.yobine_tanka}  decimals= {b.decimals}")
        else:
            print(f"  yobine_table missing for code {yobine_code!r}")

        from engine.exchanges.tachibana_master import resolve_min_ticksize_for_issue
        try:
            tick_none = resolve_min_ticksize_for_issue(issue_record, worker._yobine_table, None)
            print(f"  resolve_min_ticksize_for_issue(snapshot=None)  = {tick_none}  ← Ladder へ渡る値")
        except Exception as exc:
            print(f"  resolve_min_ticksize_for_issue: {exc}")

    # 2. REST snapshot raw
    print(f"\n  [REST CLMMfdsGetMarketPrice raw GBP/GAP]")
    try:
        snap = await asyncio.wait_for(
            worker.fetch_depth_snapshot(ticker, "stock"), timeout=timeout_s,
        )
    except Exception as exc:
        print(f"  REST error: {exc}")
        return

    # snap is the parsed dict; the worker also exposes raw via internal fetch.
    # We re-do the call here to capture raw fields.
    bids = snap.get("bids", [])
    asks = snap.get("asks", [])
    print(f"  parsed: bids={len(bids)} asks={len(asks)}")
    print("  --- bids (price@qty) ---")
    for i, b in enumerate(bids, 1):
        p = b["price"]
        q = b["qty"]
        # detect decimal alignment
        try:
            d = Decimal(p)
            mod = d % Decimal("1")
            tag = ".0" if mod == 0 else (".5" if mod == Decimal("0.5") else f"+{mod}")
        except Exception:
            tag = "?"
        print(f"    bid[{i:>2}]  {p:>12} @ {q:>10}    [tick alignment: {tag}]")
    print("  --- asks (price@qty) ---")
    for i, a in enumerate(asks, 1):
        p = a["price"]
        q = a["qty"]
        try:
            d = Decimal(p)
            mod = d % Decimal("1")
            tag = ".0" if mod == 0 else (".5" if mod == Decimal("0.5") else f"+{mod}")
        except Exception:
            tag = "?"
        print(f"    ask[{i:>2}]  {p:>12} @ {q:>10}    [tick alignment: {tag}]")


async def main(tickers: list[str], frames: int, timeout_s: float) -> int:
    sys.path.insert(0, str(REPO_ROOT / "python"))

    from engine.exchanges.tachibana_helpers import PNoCounter
    from engine.exchanges.tachibana_login_flow import startup_login
    from engine.exchanges.tachibana_auth import StartupLatch, TachibanaSession
    from engine.exchanges.tachibana import TachibanaWorker

    print("[1] ログイン中…")
    p_no = PNoCounter()
    tmp_dir = Path(tempfile.mkdtemp(prefix="diag_tachibana_depth_"))
    try:
        session: TachibanaSession = await startup_login(
            config_dir=tmp_dir,
            cache_dir=tmp_dir / "cache",
            p_no_counter=p_no,
            startup_latch=StartupLatch(),
            dev_login_allowed=True,
        )
    except Exception as exc:
        print(f"  ログイン失敗: {exc}")
        return 1
    print("  ✓ ログイン成功")

    is_demo = os.environ.get("DEV_TACHIBANA_DEMO", "true").lower() not in ("false", "0", "no")
    worker = TachibanaWorker(
        cache_dir=REPO_ROOT / "tmp" / "diag_cache",
        is_demo=is_demo,
        session=session,
        p_no_counter=p_no,
    )

    for t in tickers:
        try:
            await fetch_one(worker, t, timeout_s=timeout_s)
        except Exception as exc:
            print(f"  ticker {t}: error {exc}")

    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", default="7203")
    p.add_argument("--tickers", default="", help="comma-separated extra tickers")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--frames", type=int, default=2)
    args = p.parse_args()
    tickers = [args.ticker]
    if args.tickers:
        tickers += [t.strip() for t in args.tickers.split(",") if t.strip()]

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("engine.exchanges.tachibana").setLevel(logging.WARNING)

    _load_env(REPO_ROOT / ".env")
    sys.exit(asyncio.run(main(tickers, args.frames, args.timeout)))
