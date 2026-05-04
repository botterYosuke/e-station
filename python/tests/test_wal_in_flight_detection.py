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
            {"client_order_id": "A1", "status": "submitted"},
            {"client_order_id": "A1", "status": "partial"},
            {"client_order_id": "A1", "status": "filled"},
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
            {"client_order_id": "A2", "status": "submitted"},
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
            {"client_order_id": "A3", "status": "submitted"},
            {"client_order_id": "A4", "status": "submitted"},
            {"client_order_id": "A4", "status": "filled"},
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
            {"client_order_id": "A5", "status": "partial"},
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
            '{"client_order_id": "A6", "status": "submitted"}\n'
            '{"client_order_id": "A6", "status": "filled"}\n'
            '{"client_order_id": "A7", "status": "submitted"}\n'
            '{"client_order_id": "A7", "sta'  # truncated
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
        wal.write_text('{"client_order_id": "A8", "sta', encoding="utf-8")

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), f"Expected empty frozenset for truncated-only file, got {result!r}"


# ---------------------------------------------------------------------------
# (M11) 旧 `TestClientOrderId` クラスは削除した。writer 側 `tachibana_orders.py`
# は `client_order_id` のみを書き出すため fallback / 別フィールド名サポートは
# 不要となった。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 追加: 既知の完結ステータス（cancelled / rejected）は in-flight ではない
# ---------------------------------------------------------------------------


class TestCompleteStatuses:
    """filled / cancelled / rejected で完結した注文は in-flight ではない。"""

    @pytest.mark.parametrize("status", ["filled", "cancelled", "rejected"])
    def test_completed_statuses_not_in_flight(self, tmp_path: Path, status: str) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": f"B-{status}", "status": status},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), (
            f"status={status!r} should not be in-flight, got {result!r}"
        )


# ---------------------------------------------------------------------------
# M8: 未知ステータスは in-flight として扱う（保守的判定）
# ---------------------------------------------------------------------------


class TestUnknownStatus:
    """M8: 終端ステータス集合に含まれない未知ステータスは in-flight 扱い。"""

    def test_unknown_status_treated_as_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "U1", "status": "future_unseen_status"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset({"U1"}), (
            f"Unknown status must be conservatively treated as in-flight (M8); got {result!r}"
        )


# ---------------------------------------------------------------------------
# M6: IO エラー時は warning ログを出して空集合を返す
# ---------------------------------------------------------------------------


class TestIoErrorLogging:
    """M6: ファイルが読めないとき warning ログを出して空集合を返す。"""

    def test_io_error_emits_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        import logging
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [{"client_order_id": "X", "status": "submitted"}])

        # Force the reverse-iterator to raise OSError mid-scan.
        from engine import wal_in_flight as mod

        def _broken(*_args, **_kwargs):
            def _gen():
                raise OSError("simulated IO failure")
                yield  # pragma: no cover
            return _gen()

        monkeypatch.setattr(mod, "_iter_lines_reverse", _broken)

        with caplog.at_level(logging.WARNING, logger="engine.wal_in_flight"):
            result = mod.detect_in_flight_orders(wal)

        assert result == frozenset(), "IO error must yield empty frozenset (M6)"
        assert any(
            "in-flight detection" in rec.getMessage().lower()
            or "failed to read" in rec.getMessage().lower()
            for rec in caplog.records
        ), f"Expected a warning log entry on IO error; got {[r.getMessage() for r in caplog.records]!r}"


# ---------------------------------------------------------------------------
# M9: 大きな WAL でも tail-only 読み出しでメモリ効率を保つ
# ---------------------------------------------------------------------------


class TestLargeWal:
    """M9: 末尾だけスキャンするので、大きな WAL でも先頭を読み込まない。

    `_iter_lines_reverse` の chunk_size を強制的に小さくし、ファイルを
    先頭 → 末尾の順に走査していないことを確認する（読み出しオフセットが
    末尾から逆順であることを観測）。
    """

    def test_large_wal_does_not_load_full_file(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        # 200 件の filled 注文 + 末尾に 1 件 submitted を置く。
        records = [
            {"client_order_id": f"FILLED-{i}", "status": "filled"}
            for i in range(200)
        ]
        records.append({"client_order_id": "TAIL", "status": "submitted"})
        _write_wal(wal, records)

        # 末尾の 1 件 (TAIL) のみ in-flight であること。
        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)
        assert result == frozenset({"TAIL"}), (
            f"Tail submitted entry must be detected; got {result!r}"
        )

        # _iter_lines_reverse が末尾から走査していること（最終行から yield する）。
        from engine.wal_in_flight import _iter_lines_reverse
        first_yielded = next(_iter_lines_reverse(wal, chunk_size=128))
        assert "TAIL" in first_yielded, (
            f"_iter_lines_reverse must yield the tail line first; got {first_yielded!r}"
        )

    def test_iter_lines_reverse_handles_chunk_boundary(self, tmp_path: Path) -> None:
        """chunk_size より長い行があっても改行バイト境界で正しく分割する。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        # 各行 200 byte 程度になるよう padding を入れる。
        long_records = [
            {"client_order_id": f"L-{i}", "status": "submitted", "padding": "x" * 200}
            for i in range(5)
        ]
        _write_wal(wal, long_records)

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)
        # All 5 are submitted → all in-flight.
        assert result == frozenset({f"L-{i}" for i in range(5)}), (
            f"Long-line WAL must be parsed correctly across chunk boundaries; got {result!r}"
        )
