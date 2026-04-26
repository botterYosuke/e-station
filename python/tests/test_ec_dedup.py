"""T2.5: EC 重複検知テスト。

(venue_order_id, trade_id) キーの重複検知。
同一キーは 2 度目以降をスキップ。異なるキーは独立して処理。
"""
from __future__ import annotations

import pytest

from engine.exchanges.tachibana_event import TachibanaEventClient


def test_dedup_same_key_returns_false_then_true():
    """同一 (venue_order_id, trade_id) の 2 回目は重複として True を返す。"""
    client = TachibanaEventClient()
    vid = "ORD-001"
    tid = "EDA-001"

    assert client._is_duplicate(vid, tid) is False  # 初回: not duplicate
    assert client._is_duplicate(vid, tid) is True   # 2 回目: duplicate


def test_dedup_different_trade_id_is_independent():
    """同一 venue_order_id でも trade_id が異なれば独立して処理。"""
    client = TachibanaEventClient()
    vid = "ORD-001"

    assert client._is_duplicate(vid, "EDA-001") is False
    assert client._is_duplicate(vid, "EDA-002") is False  # 別 trade_id → not duplicate


def test_dedup_different_venue_order_id_is_independent():
    """同一 trade_id でも venue_order_id が異なれば独立して処理。"""
    client = TachibanaEventClient()
    tid = "EDA-001"

    assert client._is_duplicate("ORD-001", tid) is False
    assert client._is_duplicate("ORD-002", tid) is False  # 別 venue_order_id → not duplicate


def test_dedup_reset_clears_seen_keys():
    """reset_seen_trades() で seen セットがクリアされ、同一キーを再処理できる。"""
    client = TachibanaEventClient()
    vid = "ORD-001"
    tid = "EDA-001"

    client._is_duplicate(vid, tid)  # 1 回目: seen に追加
    assert client._is_duplicate(vid, tid) is True  # 2 回目: duplicate

    client.reset_seen_trades()  # リセット

    assert client._is_duplicate(vid, tid) is False  # リセット後: not duplicate


def test_dedup_multiple_orders():
    """複数の異なる注文の重複検知が独立して動作する。"""
    client = TachibanaEventClient()

    # ORD-001 の EDA-001
    assert client._is_duplicate("ORD-001", "EDA-001") is False
    # ORD-001 の EDA-002 (同注文の別約定)
    assert client._is_duplicate("ORD-001", "EDA-002") is False
    # ORD-002 の EDA-001 (別注文)
    assert client._is_duplicate("ORD-002", "EDA-001") is False

    # 各々の重複チェック
    assert client._is_duplicate("ORD-001", "EDA-001") is True
    assert client._is_duplicate("ORD-001", "EDA-002") is True
    assert client._is_duplicate("ORD-002", "EDA-001") is True


def test_dedup_initial_seen_empty():
    """新規インスタンスの seen セットは空。"""
    client = TachibanaEventClient()
    # _seen は set で、初期状態は空
    assert len(client._seen) == 0


def test_dedup_adds_to_seen_set():
    """_is_duplicate(False) 呼び出しで seen セットに追加される。"""
    client = TachibanaEventClient()
    client._is_duplicate("ORD-X", "EDA-X")
    assert ("ORD-X", "EDA-X") in client._seen
