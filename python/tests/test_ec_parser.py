"""T2.5: EC パーサのユニットテスト。

合成 EC フレームでパース → 期待 OrderEcEvent の確認。
全約定・部分約定・取消・失効フレームをカバー。
"""
from __future__ import annotations

import pytest

from engine.exchanges.tachibana_event import OrderEcEvent, _parse_ec_frame


# ---------------------------------------------------------------------------
# ヘルパ: テスト用 EC フレームファクトリ
# ---------------------------------------------------------------------------


def _make_ec_items(
    *,
    p_NO: str = "ORD-001",
    p_EDA: str = "EDA-001",
    p_NT: str = "2",          # 2 = 約定
    p_DH: str = "3500",       # 約定単価
    p_DSU: str = "100",       # 約定数量
    p_ZSU: str = "0",         # 残数量（0=全約定）
    p_OD: str = "20260426103000",  # 約定日時 JST YYYYMMDDHHMMSS
) -> list[tuple[str, str]]:
    return [
        ("p_NO", p_NO),
        ("p_EDA", p_EDA),
        ("p_NT", p_NT),
        ("p_DH", p_DH),
        ("p_DSU", p_DSU),
        ("p_ZSU", p_ZSU),
        ("p_OD", p_OD),
    ]


# ---------------------------------------------------------------------------
# 全約定（leaves_qty == "0"）
# ---------------------------------------------------------------------------


def test_parse_ec_frame_full_fill():
    """全約定フレームのパース — leaves_qty == "0" で全約定を判定。"""
    items = _make_ec_items(p_ZSU="0", p_DSU="100", p_DH="3500", p_NT="2")
    ev = _parse_ec_frame(items)

    assert isinstance(ev, OrderEcEvent)
    assert ev.venue_order_id == "ORD-001"
    assert ev.trade_id == "EDA-001"
    assert ev.notification_type == "2"
    assert ev.last_price == "3500"
    assert ev.last_qty == "100"
    assert ev.leaves_qty == "0"
    # ts_event_ms は JST 2026-04-26 10:30:00 → UTC ms
    assert ev.ts_event_ms > 0


def test_parse_ec_frame_full_fill_ts_event_ms():
    """約定日時 JST → UTC ms 変換の精度テスト。"""
    # 2026-04-26 10:30:00 JST = 2026-04-26 01:30:00 UTC
    items = _make_ec_items(p_OD="20260426103000")
    ev = _parse_ec_frame(items)

    # 2026-04-26 01:30:00 UTC の ms 値を期待値として計算
    from datetime import datetime, timezone
    expected_dt = datetime(2026, 4, 26, 1, 30, 0, tzinfo=timezone.utc)
    expected_ms = int(expected_dt.timestamp() * 1000)
    assert ev.ts_event_ms == expected_ms


# ---------------------------------------------------------------------------
# 部分約定（leaves_qty > "0"）
# ---------------------------------------------------------------------------


def test_parse_ec_frame_partial_fill():
    """部分約定フレームのパース — leaves_qty > 0。"""
    items = _make_ec_items(p_ZSU="50", p_DSU="50", p_DH="3500", p_NT="2")
    ev = _parse_ec_frame(items)

    assert ev.leaves_qty == "50"
    assert ev.last_qty == "50"
    assert ev.last_price == "3500"
    assert ev.notification_type == "2"


def test_parse_ec_frame_partial_fill_leaves_qty_nonzero():
    """部分約定: leaves_qty が非ゼロであることを確認。"""
    items = _make_ec_items(p_ZSU="30", p_DSU="70", p_NT="2")
    ev = _parse_ec_frame(items)

    assert ev.leaves_qty == "30"
    assert ev.last_qty == "70"


# ---------------------------------------------------------------------------
# 取消（p_NT="3"）
# ---------------------------------------------------------------------------


def test_parse_ec_frame_canceled():
    """取消フレームのパース — p_NT="3"。"""
    items = _make_ec_items(p_NT="3", p_DH="", p_DSU="", p_ZSU="100")
    ev = _parse_ec_frame(items)

    assert ev.notification_type == "3"
    assert ev.venue_order_id == "ORD-001"


def test_parse_ec_frame_canceled_empty_price():
    """取消時は last_price / last_qty が空文字または None になってもよい。"""
    items = [
        ("p_NO", "ORD-CANCEL"),
        ("p_EDA", "EDA-CANCEL"),
        ("p_NT", "3"),
        ("p_OD", "20260426110000"),
    ]
    ev = _parse_ec_frame(items)

    assert ev.notification_type == "3"
    assert ev.venue_order_id == "ORD-CANCEL"
    assert ev.trade_id == "EDA-CANCEL"
    # price/qty は None でも空文字でも OK（取消時は約定情報なし）
    assert ev.last_price is None or ev.last_price == ""
    assert ev.last_qty is None or ev.last_qty == ""


# ---------------------------------------------------------------------------
# 失効（p_NT="4"）
# ---------------------------------------------------------------------------


def test_parse_ec_frame_expired():
    """失効フレームのパース — p_NT="4"。"""
    items = _make_ec_items(p_NT="4")
    ev = _parse_ec_frame(items)

    assert ev.notification_type == "4"


def test_parse_ec_frame_expired_venue_order_id():
    """失効フレームの venue_order_id が正しく取得される。"""
    items = [
        ("p_NO", "ORD-EXPIRE"),
        ("p_EDA", "EDA-EXP"),
        ("p_NT", "4"),
        ("p_OD", "20260426150000"),
    ]
    ev = _parse_ec_frame(items)

    assert ev.venue_order_id == "ORD-EXPIRE"
    assert ev.notification_type == "4"


# ---------------------------------------------------------------------------
# 受付（p_NT="1"）
# ---------------------------------------------------------------------------


def test_parse_ec_frame_received():
    """注文受付フレームのパース — p_NT="1"。"""
    items = _make_ec_items(p_NT="1", p_DH="", p_DSU="", p_ZSU="")
    ev = _parse_ec_frame(items)

    assert ev.notification_type == "1"
    assert ev.venue_order_id == "ORD-001"
    assert ev.trade_id == "EDA-001"


# ---------------------------------------------------------------------------
# フィールド対応表の検証
# ---------------------------------------------------------------------------


def test_parse_ec_frame_field_mapping():
    """architecture.md §6 フィールド対応表の全項目を検証。"""
    items = [
        ("p_NO", "ORDER-XYZ"),   # venue_order_id
        ("p_EDA", "TRADE-ABC"),  # trade_id
        ("p_NT", "2"),           # notification_type
        ("p_DH", "4000"),        # last_price
        ("p_DSU", "200"),        # last_qty
        ("p_ZSU", "0"),          # leaves_qty
        ("p_OD", "20260426090000"),  # ts_event_ms
    ]
    ev = _parse_ec_frame(items)

    assert ev.venue_order_id == "ORDER-XYZ"
    assert ev.trade_id == "TRADE-ABC"
    assert ev.notification_type == "2"
    assert ev.last_price == "4000"
    assert ev.last_qty == "200"
    assert ev.leaves_qty == "0"
    assert ev.ts_event_ms > 0


def test_parse_ec_frame_missing_optional_fields():
    """オプションフィールドが欠落しても例外が出ないこと。"""
    items = [
        ("p_NO", "ORD-MIN"),
        ("p_EDA", "EDA-MIN"),
        ("p_NT", "3"),
        ("p_OD", "20260426120000"),
    ]
    ev = _parse_ec_frame(items)
    assert ev.venue_order_id == "ORD-MIN"
    assert ev.trade_id == "EDA-MIN"
