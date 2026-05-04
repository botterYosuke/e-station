"""WAL in-flight 検知のユニットテスト（P7 受け入れ基準 12）。

detect_in_flight_orders() が tachibana_orders.jsonl を tail から逆順スキャンし、
最新 status が "submitted" / "partial" の order_id を正しく返すことを検証する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_wal(path: Path, records: list[dict]) -> None:
    """JSONL 形式で WAL ファイルを書き出す。"""
    lines = [json.dumps(r) for r in records]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# fixture 1: 部分約定→全約定（in-flight なし）
# ---------------------------------------------------------------------------


class TestFilledOrder:
    """A1: submitted → partial → filled の順で記録された注文は in-flight 扱いしない。"""

    def test_filled_order_is_not_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"order_id": "A1", "status": "submitted"},
            {"order_id": "A1", "status": "partial"},
            {"order_id": "A1", "status": "filled"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), f"Expected empty frozenset, got {result!r}"


# ---------------------------------------------------------------------------
# fixture 2: プロセスクラッシュで submitted のまま残留（in-flight あり）
# ---------------------------------------------------------------------------


class TestCrashedSubmittedOrder:
    """A2: プロセスクラッシュで submitted 状態のまま残留した注文は in-flight とみなす。"""

    def test_submitted_order_is_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"order_id": "A2", "status": "submitted"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset({"A2"}), f"Expected {{A2}}, got {result!r}"


# ---------------------------------------------------------------------------
# fixture 3: 複数注文（一部 filled、一部 submitted）
# ---------------------------------------------------------------------------


class TestMixedOrders:
    """A3 は submitted（in-flight）、A4 は filled（in-flight ではない）。"""

    def test_only_submitted_is_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"order_id": "A3", "status": "submitted"},
            {"order_id": "A4", "status": "submitted"},
            {"order_id": "A4", "status": "filled"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset({"A3"}), f"Expected {{A3}}, got {result!r}"


# ---------------------------------------------------------------------------
# fixture 4: ファイル不在
# ---------------------------------------------------------------------------


class TestFileNotFound:
    """WAL ファイルが存在しない場合は空の frozenset を返す（エラーにしない）。"""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        wal = tmp_path / "nonexistent_orders.jsonl"

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), f"Expected empty frozenset, got {result!r}"

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        """Path 型だけでなく str でも動作すること。"""
        wal = str(tmp_path / "nonexistent.jsonl")

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset()


# ---------------------------------------------------------------------------
# fixture 5: partial（部分約定）のまま残留 → in-flight あり
# ---------------------------------------------------------------------------


class TestPartialOrder:
    """A5: partial 状態のまま残留した注文は in-flight とみなす。"""

    def test_partial_order_is_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"order_id": "A5", "status": "partial"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset({"A5"}), f"Expected {{A5}}, got {result!r}"


# ---------------------------------------------------------------------------
# fixture 6: 末尾 truncated 行（不完全 JSON）→ スキップして正常動作
# ---------------------------------------------------------------------------


class TestTruncatedLine:
    """末尾に不完全な JSON 行があっても、有効な行の判定は正しく行われること。"""

    def test_truncated_line_is_skipped(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        # 最後の行を不完全な JSON にする
        content = (
            '{"order_id": "A6", "status": "submitted"}\n'
            '{"order_id": "A6", "status": "filled"}\n'
            '{"order_id": "A7", "status": "submitted"}\n'
            '{"order_id": "A7", "sta'  # truncated
        )
        wal.write_text(content, encoding="utf-8")

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        # A6 は filled（in-flight ではない）、A7 の truncated 行はスキップされ
        # 有効な submitted 行のみ判定される
        assert "A6" not in result, f"A6 should not be in-flight, got {result!r}"
        # A7 の truncated 行はスキップされる→ A7 の有効な "submitted" 行が最新
        assert "A7" in result, f"A7 should be in-flight (truncated line skipped), got {result!r}"

    def test_completely_truncated_file(self, tmp_path: Path) -> None:
        """ファイルが truncated 行のみの場合も空を返すこと。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        wal.write_text('{"order_id": "A8", "sta', encoding="utf-8")

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), f"Expected empty frozenset for truncated-only file, got {result!r}"


# ---------------------------------------------------------------------------
# 追加: client_order_id フィールド名のサポート
# ---------------------------------------------------------------------------


class TestClientOrderId:
    """order_id ではなく client_order_id フィールドを持つレコードも処理できること。"""

    def test_client_order_id_field(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "COI-1", "status": "submitted"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset({"COI-1"}), f"Expected {{COI-1}}, got {result!r}"


# ---------------------------------------------------------------------------
# 追加: 既知の完結ステータス（cancelled / rejected）は in-flight ではない
# ---------------------------------------------------------------------------


class TestCompleteStatuses:
    """filled / cancelled / rejected で完結した注文は in-flight ではない。"""

    @pytest.mark.parametrize("status", ["filled", "cancelled", "rejected"])
    def test_completed_statuses_not_in_flight(self, tmp_path: Path, status: str) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"order_id": f"B-{status}", "status": status},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), (
            f"status={status!r} should not be in-flight, got {result!r}"
        )
