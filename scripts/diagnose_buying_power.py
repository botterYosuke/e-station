"""Diagnostic: 買余力 (CLMZanKaiKanougaku / CLMZanShinkiKanoIjiritu) を直接叩いて原因を調べる。

WHAT IT PROVES
--------------
1. ログインが通ること
2. CLMZanKaiKanougaku レスポンスの生 JSON を表示する（H2: フィールド名確認）
3. CLMZanShinkiKanoIjiritu レスポンスの生 JSON を表示する
4. パース後の値が 0 かどうかを確認する（H1: 実際に API が 0 を返している）
5. sKinsyouhouMidokuFlg の値を確認する（H4: UnreadNoticesError の可否）

USAGE
-----
    uv run python scripts/diagnose_buying_power.py

REQUIREMENTS
------------
.env に以下を設定:
    DEV_TACHIBANA_USER_ID=...
    DEV_TACHIBANA_PASSWORD=...
    DEV_TACHIBANA_DEMO=true
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
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


_results: list[tuple[str, bool, str]] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    _results.append((label, ok, detail))
    icon = "✓" if ok else "✗"
    print(f"  [{status}] {icon} {label}" + (f"\n         {detail}" if detail else ""))
    return ok


async def main() -> int:
    sys.path.insert(0, str(REPO_ROOT / "python"))

    from engine.exchanges.tachibana_helpers import PNoCounter
    from engine.exchanges.tachibana_login_flow import startup_login
    from engine.exchanges.tachibana_auth import StartupLatch

    _load_env(REPO_ROOT / ".env")

    # ── Step 1: ログイン ─────────────────────────────────────────────────
    print("\n[1] ログイン")
    p_no = PNoCounter()
    tmp_dir = Path(tempfile.mkdtemp(prefix="diag_buying_power_"))
    try:
        session = await startup_login(
            config_dir=tmp_dir,
            cache_dir=tmp_dir / "cache",
            p_no_counter=p_no,
            startup_latch=StartupLatch(),
            dev_login_allowed=True,
        )
    except Exception as exc:
        _check("ログイン成功", False, str(exc))
        print("\n[ABORT] ログイン失敗 — .env を確認してください")
        return 1

    _check("ログイン成功", True)
    print(f"  url_request (先頭60文字): {str(session.url_request)[:60]}...")

    # ── Step 2: CLMZanKaiKanougaku (現物買付余力) ────────────────────────
    print("\n[2] CLMZanKaiKanougaku (現物買付余力)")
    import json
    import httpx
    from engine.exchanges.tachibana_codec import decode_response_body
    from engine.exchanges.tachibana_helpers import check_response, current_p_sd_date
    from engine.exchanges.tachibana_url import build_request_url, guard_prod_url

    try:
        payload_cash = {
            "p_no": str(p_no.next()),
            "p_sd_date": current_p_sd_date(),
            "sCLMID": "CLMZanKaiKanougaku",
            "sJsonOfmt": "5",
        }
        url_cash = build_request_url(session.url_request, payload_cash, sJsonOfmt="5")
        guard_prod_url(url_cash)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp_cash = await client.get(url_cash)
            resp_cash.raise_for_status()
            body_cash = decode_response_body(resp_cash.content)

        data_cash = json.loads(body_cash)
        print(f"  生 JSON キー: {sorted(data_cash.keys())}")
        print(f"  生 JSON 全体:\n  {json.dumps(data_cash, ensure_ascii=False, indent=2)}")

        # H4: UnreadNoticesError チェック
        midoku = data_cash.get("sKinsyouhouMidokuFlg", "")
        _check("sKinsyouhouMidokuFlg が '1' でない（H4）", midoku != "1", f"midoku={midoku!r}")

        err = check_response(data_cash)
        _check("check_response が None を返す（エラーなし）", err is None, repr(err))

        # H2: フィールド名確認（正しいフィールド名: sSummaryGenkabuKaituke）
        has_goukei = "sSummaryGenkabuKaituke" in data_cash
        _check(
            "sSummaryGenkabuKaituke フィールドが存在する（H2修正確認）",
            has_goukei,
            f"実際のキー={sorted(data_cash.keys())}",
        )

        raw_goukei = data_cash.get("sSummaryGenkabuKaituke", "MISSING")
        print(f"  sSummaryGenkabuKaituke = {raw_goukei!r}")
        available_cash = int(raw_goukei or "0") if raw_goukei != "MISSING" else 0
        _check(
            "現物買付余力 > 0 (H1: API が 0 を返しているか)",
            available_cash > 0,
            f"available_cash={available_cash:,}",
        )

    except Exception as exc:
        _check("CLMZanKaiKanougaku 呼び出し成功", False, str(exc))

    # ── Step 3: CLMZanShinkiKanoIjiritu (信用新規可能額) ─────────────────
    print("\n[3] CLMZanShinkiKanoIjiritu (信用新規可能額)")
    try:
        payload_credit = {
            "p_no": str(p_no.next()),
            "p_sd_date": current_p_sd_date(),
            "sCLMID": "CLMZanShinkiKanoIjiritu",
            "sJsonOfmt": "5",
        }
        url_credit = build_request_url(session.url_request, payload_credit, sJsonOfmt="5")
        guard_prod_url(url_credit)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp_credit = await client.get(url_credit)
            resp_credit.raise_for_status()
            body_credit = decode_response_body(resp_credit.content)

        data_credit = json.loads(body_credit)
        print(f"  生 JSON キー: {sorted(data_credit.keys())}")
        print(f"  生 JSON 全体:\n  {json.dumps(data_credit, ensure_ascii=False, indent=2)}")

        err_credit = check_response(data_credit)
        _check("check_response が None を返す（エラーなし）", err_credit is None, repr(err_credit))

        has_credit_goukei = "sSummarySinyouSinkidate" in data_credit
        _check(
            "sSummarySinyouSinkidate フィールドが存在する（H2修正確認）",
            has_credit_goukei,
            f"実際のキー={sorted(data_credit.keys())}",
        )

        raw_credit = data_credit.get("sSummarySinyouSinkidate", "MISSING")
        print(f"  sSummarySinyouSinkidate = {raw_credit!r}")
        available_credit = int(raw_credit or "0") if raw_credit != "MISSING" else 0
        _check(
            "信用新規可能額 > 0 (H1)",
            available_credit > 0,
            f"available_credit={available_credit:,}",
        )

    except Exception as exc:
        _check("CLMZanShinkiKanoIjiritu 呼び出し成功", False, str(exc))

    # ── 結果サマリー ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"RESULT: {passed}/{total} PASS")
    if passed < total:
        print("FAIL items:")
        for label, ok, detail in _results:
            if not ok:
                print(f"  ✗ {label}: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
