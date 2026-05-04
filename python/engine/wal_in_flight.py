"""WAL in-flight 検知ユーティリティ（F7 モード切替安全装置）。

tachibana_orders.jsonl を tail から逆順スキャンし、
最新 status が "submitted" または "partial" の order_id 集合を返す。
"""
from __future__ import annotations

import json
from pathlib import Path


def detect_in_flight_orders(path: Path | str) -> frozenset[str]:
    """WAL を逆順スキャンして in-flight な order_id 集合を返す。

    Returns:
        最新 status が "submitted" または "partial" の order_id の frozenset。
        ファイルが存在しない場合は空の frozenset を返す（エラーにしない）。
    """
    path = Path(path)
    if not path.exists():
        return frozenset()

    # tail から逆順スキャン — order_id ごとに最新 status を収集
    seen: dict[str, str] = {}  # order_id → latest status

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # 末尾 truncated 行はスキップ

        order_id = record.get("order_id") or record.get("client_order_id")
        status = record.get("status")
        if order_id and status and order_id not in seen:
            seen[order_id] = status

    in_flight_statuses = {"submitted", "partial"}
    return frozenset(oid for oid, st in seen.items() if st in in_flight_statuses)
