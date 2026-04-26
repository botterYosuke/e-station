"""TDD Red → Green: T0.7 — WAL truncation 復元テスト。

WAL 末尾行が \\n 欠落の場合:
- その行はスキップされること
- WARN ログが出ること
- 正常行は正しく読み込まれること
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from engine.exchanges.tachibana_orders import read_wal_records


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_wal(path: Path, lines: list[str]) -> None:
    """WAL ファイルを指定行で書き込む（各行末の \\n は呼び出し元が制御）。"""
    path.write_text("".join(lines), encoding="utf-8")


def _submit_line(client_order_id: str = "cid-001") -> str:
    record = {
        "phase": "submit",
        "ts": 1700000000000,
        "client_order_id": client_order_id,
        "request_key": 12345,
        "instrument_id": "7203.TSE",
        "order_side": "BUY",
        "order_type": "MARKET",
        "quantity": "100",
    }
    return json.dumps(record) + "\n"


def _accepted_line(client_order_id: str = "cid-001") -> str:
    record = {
        "phase": "accepted",
        "ts": 1700000001000,
        "client_order_id": client_order_id,
        "venue_order_id": "ORD-001",
        "p_no": 1700000001,
        "warning_code": None,
        "warning_text": None,
    }
    return json.dumps(record) + "\n"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWalTruncation:
    def test_normal_lines_are_all_loaded(self, tmp_path):
        """正常な \\n 終端行はすべて読み込まれる。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [_submit_line("cid-001"), _accepted_line("cid-001")])

        records = read_wal_records(wal)
        assert len(records) == 2
        assert records[0]["phase"] == "submit"
        assert records[1]["phase"] == "accepted"

    def test_truncated_last_line_is_skipped(self, tmp_path):
        """末尾行に \\n が無い（truncated）場合はスキップされる。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        # 1行目: 正常, 2行目: truncated（末尾 \n なし）
        truncated = _submit_line("cid-002").rstrip("\n")
        _write_wal(wal, [_submit_line("cid-001"), truncated])

        records = read_wal_records(wal)
        assert len(records) == 1
        assert records[0]["client_order_id"] == "cid-001"

    def test_truncated_last_line_emits_warn_log(self, tmp_path, caplog):
        """truncated 行に対して WARN ログが出ること。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        truncated = _submit_line("cid-999").rstrip("\n")
        _write_wal(wal, [_submit_line("cid-001"), truncated])

        with caplog.at_level(logging.WARNING):
            read_wal_records(wal)

        warn_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("truncat" in m.lower() or "skip" in m.lower() for m in warn_messages), (
            f"WARN log about truncation expected, got: {warn_messages}"
        )

    def test_empty_wal_returns_empty_list(self, tmp_path):
        """空の WAL は空リストを返す。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        wal.write_text("", encoding="utf-8")

        records = read_wal_records(wal)
        assert records == []

    def test_nonexistent_wal_returns_empty_list(self, tmp_path):
        """WAL ファイルが存在しない場合は空リストを返す。"""
        wal = tmp_path / "nonexistent.jsonl"
        records = read_wal_records(wal)
        assert records == []

    def test_multiple_valid_lines_before_truncated(self, tmp_path):
        """有効行が複数あり、末尾 1 行だけ truncated の場合、有効行のみ返す。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        truncated = _accepted_line("cid-003").rstrip("\n")
        _write_wal(
            wal,
            [
                _submit_line("cid-001"),
                _accepted_line("cid-001"),
                _submit_line("cid-002"),
                truncated,
            ],
        )

        records = read_wal_records(wal)
        assert len(records) == 3
        assert records[-1]["client_order_id"] == "cid-002"
