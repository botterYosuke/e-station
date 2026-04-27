"""B2 pin: ``resolve_min_ticksize_for_issue`` glues
``CLMIssueSizyouMstKabu.sYobineTaniNumber`` to the live ``yobine_table``
built from CLMYobine records, returning the tick size that applies at a
given snapshot price (or a conservative fallback when no snapshot is
available).

implementation-plan.md §T4 L538-542 (B2) / data-mapping.md §5.4.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.exchanges.tachibana_master import (
    YobineBand,
    resolve_min_ticksize_for_issue,
)


def _band(kizun: str, tanka: str, decimals: int = 0) -> YobineBand:
    return YobineBand(
        kizun_price=Decimal(kizun),
        yobine_tanka=Decimal(tanka),
        decimals=decimals,
    )


@pytest.fixture
def yobine_table() -> dict[str, list[YobineBand]]:
    # Two yobine codes, each with three bands ending in the 999999999 cap.
    # Code "00" — finer ticks at low prices.
    # Code "10" — coarser ticks (e.g. for high-priced issues).
    return {
        "00": [
            _band("3000", "1"),
            _band("5000", "5"),
            _band("999999999", "10"),
        ],
        "10": [
            _band("3000", "10"),
            _band("999999999", "100"),
        ],
    }


def test_resolve_tick_size_for_issue_uses_clm_yobine_lookup(yobine_table):
    """既知 sYobineTaniNumber + 既知 snapshot_price で正しい tick が返る。"""
    issue = {"sIssueCode": "7203", "sYobineTaniNumber": "00"}
    # 4500 はバンド ``(kizun=5000, tanka=5)`` に該当
    tick = resolve_min_ticksize_for_issue(
        issue, yobine_table, snapshot_price=Decimal("4500")
    )
    assert tick == Decimal("5")


def test_resolve_unknown_yobine_code_raises_keyerror(yobine_table):
    """未知 sYobineTaniNumber は KeyError (silent fallback しない)。"""
    issue = {"sIssueCode": "9999", "sYobineTaniNumber": "99"}
    with pytest.raises(KeyError):
        resolve_min_ticksize_for_issue(
            issue, yobine_table, snapshot_price=Decimal("1000")
        )


def test_resolve_with_none_snapshot_price_uses_first_band_fallback(yobine_table):
    """snapshot_price=None のとき sKizunPrice_1 相当 (= bands[0]) の tick を返す。

    起動直後など現値が未取得な場面で「過剰に細かい tick」を採る保守的
    フォールバック (data-mapping.md §5.4 / implementation-plan.md L539)。
    """
    issue = {"sIssueCode": "7203", "sYobineTaniNumber": "00"}
    tick = resolve_min_ticksize_for_issue(issue, yobine_table, snapshot_price=None)
    # bands[0] = (kizun=3000, tanka=1) → 1 が返る
    assert tick == Decimal("1")
