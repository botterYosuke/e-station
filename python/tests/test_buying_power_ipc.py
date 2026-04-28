"""Python IPC schema roundtrip tests for GetBuyingPower / BuyingPowerUpdated (schema 2.1)."""

from __future__ import annotations

import pytest
from engine.schemas import (
    SCHEMA_MINOR,
    GetBuyingPower,
    BuyingPowerUpdated,
)


# ── Schema version guard ──────────────────────────────────────────────────────


def test_schema_minor_is_at_least_2() -> None:
    assert SCHEMA_MINOR >= 2, f"SCHEMA_MINOR must be >= 2 for BuyingPower IPC, got {SCHEMA_MINOR}"


# ── GetBuyingPower Command ────────────────────────────────────────────────────


def test_get_buying_power_round_trips() -> None:
    cmd = GetBuyingPower(request_id="req-001", venue="tachibana")
    data = cmd.model_dump(mode="json")
    assert data["op"] == "GetBuyingPower"
    assert data["request_id"] == "req-001"
    assert data["venue"] == "tachibana"


def test_get_buying_power_validates_from_dict() -> None:
    raw = {"op": "GetBuyingPower", "request_id": "req-002", "venue": "tachibana"}
    cmd = GetBuyingPower.model_validate(raw)
    assert cmd.request_id == "req-002"
    assert cmd.venue == "tachibana"


# ── BuyingPowerUpdated Event ──────────────────────────────────────────────────


def test_buying_power_updated_round_trips() -> None:
    ev = BuyingPowerUpdated(
        request_id="req-001",
        venue="tachibana",
        cash_available=1_000_000,
        cash_shortfall=0,
        credit_available=500_000,
        ts_ms=1_745_640_000_000,
    )
    data = ev.model_dump(mode="json")
    assert data["event"] == "BuyingPowerUpdated"
    assert data["request_id"] == "req-001"
    assert data["venue"] == "tachibana"
    assert data["cash_available"] == 1_000_000
    assert data["cash_shortfall"] == 0
    assert data["credit_available"] == 500_000
    assert data["ts_ms"] == 1_745_640_000_000


def test_buying_power_updated_with_shortfall() -> None:
    ev = BuyingPowerUpdated(
        request_id="req-002",
        venue="tachibana",
        cash_available=0,
        cash_shortfall=50_000,
        credit_available=0,
        ts_ms=1_745_640_001_000,
    )
    assert ev.cash_shortfall == 50_000
    assert ev.cash_available == 0


def test_buying_power_updated_validates_from_dict() -> None:
    raw = {
        "event": "BuyingPowerUpdated",
        "request_id": "req-003",
        "venue": "tachibana",
        "cash_available": 2_000_000,
        "cash_shortfall": 0,
        "credit_available": 1_000_000,
        "ts_ms": 1_745_640_002_000,
    }
    ev = BuyingPowerUpdated.model_validate(raw)
    assert ev.cash_available == 2_000_000
    assert ev.credit_available == 1_000_000


def test_buying_power_updated_json_shape() -> None:
    """Python が emit する JSON の shape が Rust の Deserialize と一致することを確認。"""
    ev = BuyingPowerUpdated(
        request_id="req-shape",
        venue="tachibana",
        cash_available=300_000,
        cash_shortfall=0,
        credit_available=150_000,
        ts_ms=1_745_640_003_000,
    )
    data = ev.model_dump(mode="json")
    required_keys = {"event", "request_id", "venue", "cash_available", "cash_shortfall",
                     "credit_available", "ts_ms"}
    assert required_keys.issubset(data.keys()), f"missing keys: {required_keys - data.keys()}"
