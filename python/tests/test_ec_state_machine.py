"""T2.5 (D2-L3): EC 状態遷移テスト。

拒否・失効・部分→全部 の遷移順序をアサートする state-machine テスト。
"""
from __future__ import annotations

import pytest

from engine.exchanges.tachibana_event import OrderEcEvent, _parse_ec_frame


# ---------------------------------------------------------------------------
# 遷移ヘルパ
# ---------------------------------------------------------------------------


def make_ec(*, venue_order_id: str, trade_id: str, nt: str, leaves_qty: str = "0") -> OrderEcEvent:
    items = [
        ("p_NO", venue_order_id),
        ("p_EDA", trade_id),
        ("p_NT", nt),
        ("p_DH", "3500"),
        ("p_DSU", "100"),
        ("p_ZSU", leaves_qty),
        ("p_OD", "20260426103000"),
    ]
    return _parse_ec_frame(items)


# ---------------------------------------------------------------------------
# 部分約定 → 全部約定の遷移
# ---------------------------------------------------------------------------


def test_partial_then_full_fill_transition():
    """部分約定 (leaves_qty>0) → 全約定 (leaves_qty=0) の遷移。"""
    partial = make_ec(venue_order_id="ORD-001", trade_id="EDA-001", nt="2", leaves_qty="50")
    full = make_ec(venue_order_id="ORD-001", trade_id="EDA-002", nt="2", leaves_qty="0")

    # 部分約定: leaves_qty > 0
    assert int(partial.leaves_qty) > 0
    assert partial.notification_type == "2"

    # 全約定: leaves_qty == 0
    assert partial.leaves_qty != "0"
    assert full.leaves_qty == "0"
    assert full.notification_type == "2"

    # EDA (trade_id) が異なる — 別の約定事象
    assert partial.trade_id != full.trade_id


def test_multiple_partial_fills_then_full():
    """複数回の部分約定の後、全約定に至る遷移チェーン。"""
    fill1 = make_ec(venue_order_id="ORD-002", trade_id="EDA-001", nt="2", leaves_qty="70")
    fill2 = make_ec(venue_order_id="ORD-002", trade_id="EDA-002", nt="2", leaves_qty="30")
    fill3 = make_ec(venue_order_id="ORD-002", trade_id="EDA-003", nt="2", leaves_qty="0")

    # 各フィルが別の trade_id を持つ
    trade_ids = [fill1.trade_id, fill2.trade_id, fill3.trade_id]
    assert len(set(trade_ids)) == 3  # すべて異なる

    # leaves_qty の減少（単調非増加）
    leaves = [int(fill1.leaves_qty), int(fill2.leaves_qty), int(fill3.leaves_qty)]
    assert leaves == sorted(leaves, reverse=True)  # 単調非増加

    # 最後のみ 0
    assert fill3.leaves_qty == "0"


# ---------------------------------------------------------------------------
# 取消遷移
# ---------------------------------------------------------------------------


def test_cancel_notification():
    """取消通知 (p_NT="3") のパース。"""
    canceled = make_ec(venue_order_id="ORD-003", trade_id="EDA-001", nt="3", leaves_qty="100")
    assert canceled.notification_type == "3"
    assert canceled.venue_order_id == "ORD-003"


def test_partial_fill_then_cancel():
    """部分約定後の取消遷移。"""
    partial = make_ec(venue_order_id="ORD-004", trade_id="EDA-001", nt="2", leaves_qty="50")
    canceled = make_ec(venue_order_id="ORD-004", trade_id="EDA-002", nt="3", leaves_qty="50")

    assert partial.notification_type == "2"
    assert partial.leaves_qty == "50"
    assert canceled.notification_type == "3"


# ---------------------------------------------------------------------------
# 失効遷移
# ---------------------------------------------------------------------------


def test_expired_notification():
    """失効通知 (p_NT="4") のパース。"""
    expired = make_ec(venue_order_id="ORD-005", trade_id="EDA-001", nt="4")
    assert expired.notification_type == "4"
    assert expired.venue_order_id == "ORD-005"


def test_accepted_then_expired():
    """受付後の失効遷移。"""
    accepted = make_ec(venue_order_id="ORD-006", trade_id="EDA-001", nt="1", leaves_qty="100")
    expired = make_ec(venue_order_id="ORD-006", trade_id="EDA-002", nt="4")

    assert accepted.notification_type == "1"
    assert expired.notification_type == "4"
    assert accepted.venue_order_id == expired.venue_order_id


# ---------------------------------------------------------------------------
# 重複検知との組み合わせ
# ---------------------------------------------------------------------------


def test_dedup_prevents_double_fill():
    """同一 (venue_order_id, trade_id) の EC は重複検知でスキップされる。"""
    from engine.exchanges.tachibana_event import TachibanaEventClient

    client = TachibanaEventClient()
    vid = "ORD-007"
    tid = "EDA-001"

    # 1 回目: 処理
    assert client._is_duplicate(vid, tid) is False

    # 2 回目: 重複 → スキップ
    assert client._is_duplicate(vid, tid) is True


def test_dedup_allows_different_trades_same_order():
    """同一注文の異なる約定は別々に処理される。"""
    from engine.exchanges.tachibana_event import TachibanaEventClient

    client = TachibanaEventClient()
    vid = "ORD-008"

    assert client._is_duplicate(vid, "EDA-001") is False
    assert client._is_duplicate(vid, "EDA-002") is False
    assert client._is_duplicate(vid, "EDA-003") is False

    # 同じ trade_id は 2 回目から重複
    assert client._is_duplicate(vid, "EDA-001") is True


# ---------------------------------------------------------------------------
# 通知種別の分類テスト
# ---------------------------------------------------------------------------


def test_notification_types_classification():
    """通知種別 (p_NT) の分類: 1=受付, 2=約定, 3=取消, 4=失効。"""
    type_map = {"1": "受付", "2": "約定", "3": "取消", "4": "失効"}

    for nt, desc in type_map.items():
        ev = make_ec(venue_order_id="ORD-TEST", trade_id="EDA-TEST", nt=nt)
        assert ev.notification_type == nt, f"{desc} の notification_type が {nt} でない"
