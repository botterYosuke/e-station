"""WAL in-flight 検知のユニットテスト（P7 受け入れ基準 12）。

detect_in_flight_orders() が tachibana_orders.jsonl を tail から逆順スキャンし、
最新 phase が ``rejected`` でない client_order_id を返すことを検証する。

writer schema (`tachibana_orders.py`) は ``phase`` キーを使う:
    submit / accepted → in-flight
    rejected → terminal（in-flight ではない）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_wal(path: Path, records: list[dict]) -> None:
    """JSONL 形式で WAL ファイルを書き出す（末尾改行あり、writer 互換）。"""
    lines = [json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# fixture 1: submit → accepted → rejected （終端：in-flight ではない）
# ---------------------------------------------------------------------------


class TestRejectedOrder:
    """A1: submit → accepted → rejected の順で記録された注文は in-flight 扱いしない。"""

    def test_rejected_order_is_not_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "A1", "phase": "submit"},
            {"client_order_id": "A1", "phase": "accepted"},
            {"client_order_id": "A1", "phase": "rejected"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), f"Expected empty frozenset, got {result!r}"


# ---------------------------------------------------------------------------
# fixture 2: プロセスクラッシュで submit のまま残留（in-flight あり）
# ---------------------------------------------------------------------------


class TestCrashedSubmitOrder:
    """A2: プロセスクラッシュで submit 行のまま残留した注文は in-flight とみなす。"""

    def test_submit_order_is_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "A2", "phase": "submit"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset({"A2"}), f"Expected {{A2}}, got {result!r}"


# ---------------------------------------------------------------------------
# fixture 3: 複数注文（一部 rejected、一部 submit）
# ---------------------------------------------------------------------------


class TestMixedOrders:
    """A3 は submit（in-flight）、A4 は rejected（in-flight ではない）。"""

    def test_only_pending_is_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "A3", "phase": "submit"},
            {"client_order_id": "A4", "phase": "submit"},
            {"client_order_id": "A4", "phase": "rejected"},
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
# fixture 5: accepted （venue 受領済みの未約定）も in-flight 扱い
# ---------------------------------------------------------------------------


class TestAcceptedOrder:
    """A5: venue 受領済み（accepted）の注文は in-flight 扱い。

    accepted は venue が受け取ったが約定通知（fill）はまだ。
    モード切替時に再送・キャンセルが必要な状態なので、安全側で in-flight と判定する。
    """

    def test_accepted_order_is_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "A5", "phase": "submit"},
            {"client_order_id": "A5", "phase": "accepted"},
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
            '{"client_order_id": "A6", "phase": "submit"}\n'
            '{"client_order_id": "A6", "phase": "rejected"}\n'
            '{"client_order_id": "A7", "phase": "submit"}\n'
            '{"client_order_id": "A7", "pha'  # truncated
        )
        wal.write_text(content, encoding="utf-8")

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert "A6" not in result, f"A6 should not be in-flight, got {result!r}"
        assert "A7" in result, f"A7 should be in-flight (truncated line skipped), got {result!r}"

    def test_completely_truncated_file(self, tmp_path: Path) -> None:
        """ファイルが truncated 行のみの場合も空を返すこと。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        wal.write_text('{"client_order_id": "A8", "pha', encoding="utf-8")

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), f"Expected empty frozenset for truncated-only file, got {result!r}"


# ---------------------------------------------------------------------------
# 終端 phase（rejected）は in-flight ではない
# ---------------------------------------------------------------------------


class TestTerminalPhases:
    """rejected で完結した注文は in-flight ではない。"""

    @pytest.mark.parametrize("phase", ["rejected"])
    def test_terminal_phases_not_in_flight(self, tmp_path: Path, phase: str) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": f"B-{phase}", "phase": phase},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset(), (
            f"phase={phase!r} should not be in-flight, got {result!r}"
        )


# ---------------------------------------------------------------------------
# M8: 未知 phase は in-flight として扱う（保守的判定）
# ---------------------------------------------------------------------------


class TestUnknownPhase:
    """終端 phase 集合に含まれない未知 phase は in-flight 扱い。"""

    def test_unknown_phase_treated_as_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "U1", "phase": "future_unseen_phase"},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)

        assert result == frozenset({"U1"}), (
            f"Unknown phase must be conservatively treated as in-flight; got {result!r}"
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
        _write_wal(wal, [{"client_order_id": "X", "phase": "submit"}])

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
    """M9: 末尾だけスキャンするので、大きな WAL でも先頭を読み込まない。"""

    def test_large_wal_does_not_load_full_file(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        records = [
            {"client_order_id": f"REJ-{i}", "phase": "rejected"}
            for i in range(200)
        ]
        records.append({"client_order_id": "TAIL", "phase": "submit"})
        _write_wal(wal, records)

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)
        assert result == frozenset({"TAIL"}), (
            f"Tail submit entry must be detected; got {result!r}"
        )

        from engine.wal_in_flight import _iter_lines_reverse
        first_yielded = next(_iter_lines_reverse(wal, chunk_size=128))
        assert "TAIL" in first_yielded, (
            f"_iter_lines_reverse must yield the tail line first; got {first_yielded!r}"
        )

    def test_iter_lines_reverse_handles_chunk_boundary(self, tmp_path: Path) -> None:
        """chunk_size より長い行があっても改行バイト境界で正しく分割する。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        long_records = [
            {"client_order_id": f"L-{i}", "phase": "submit", "padding": "x" * 200}
            for i in range(5)
        ]
        _write_wal(wal, long_records)

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal)
        assert result == frozenset({f"L-{i}" for i in range(5)}), (
            f"Long-line WAL must be parsed correctly across chunk boundaries; got {result!r}"
        )


# ---------------------------------------------------------------------------
# H3: chunk boundary 強化テスト — 複数 chunk_size で総当たり parametrize
# ---------------------------------------------------------------------------


class TestChunkBoundaryParametrized:
    """H3: chunk_size を細かく振って境界バグを総当たりで pin する。

    各 chunk_size で同じファイルをスキャンしても結果が常に正しい行集合に
    なることを assert する。ファイル末尾 ``\\n`` あり / なし両方を試す。
    """

    @staticmethod
    def _records_for(n: int) -> list[dict]:
        return [
            {"client_order_id": f"R-{i}", "phase": "submit"} for i in range(n)
        ]

    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 10, 13, 50, 8192])
    @pytest.mark.parametrize("with_trailing_newline", [True, False])
    def test_5_short_lines(
        self,
        tmp_path: Path,
        chunk_size: int,
        with_trailing_newline: bool,
    ) -> None:
        from engine.wal_in_flight import _iter_lines_reverse

        wal = tmp_path / "tachibana_orders.jsonl"
        records = self._records_for(5)
        text = "\n".join(json.dumps(r) for r in records)
        if with_trailing_newline:
            text += "\n"
        wal.write_text(text, encoding="utf-8")

        yielded = list(_iter_lines_reverse(wal, chunk_size=chunk_size))
        # 5 行とも入っているはず（順は逆順）。
        assert len(yielded) == 5, (
            f"chunk_size={chunk_size} trailing_nl={with_trailing_newline}: "
            f"expected 5 lines, got {len(yielded)}: {yielded!r}"
        )
        # 末尾行 (R-4) が最初に yield される
        assert "R-4" in yielded[0]
        assert "R-0" in yielded[-1]

    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 10, 50, 199, 200, 201])
    def test_single_long_line_200_bytes(
        self, tmp_path: Path, chunk_size: int
    ) -> None:
        """単一の長い行（chunk_size より大きい場合がある）が正しく読まれること。"""
        from engine.wal_in_flight import _iter_lines_reverse

        wal = tmp_path / "tachibana_orders.jsonl"
        record = {"client_order_id": "LONG", "phase": "submit", "pad": "y" * 150}
        text = json.dumps(record) + "\n"
        wal.write_text(text, encoding="utf-8")

        yielded = list(_iter_lines_reverse(wal, chunk_size=chunk_size))
        assert len(yielded) == 1, (
            f"chunk_size={chunk_size}: expected 1 line, got {yielded!r}"
        )
        assert "LONG" in yielded[0]

    @pytest.mark.parametrize("chunk_size", [1, 2, 7, 13, 100, 8192])
    def test_file_larger_than_default_chunk(
        self, tmp_path: Path, chunk_size: int
    ) -> None:
        """8192 byte を超えるファイルでも全行 yield されること。"""
        from engine.wal_in_flight import _iter_lines_reverse, detect_in_flight_orders

        wal = tmp_path / "tachibana_orders.jsonl"
        records = [
            {"client_order_id": f"BIG-{i}", "phase": "submit", "pad": "z" * 100}
            for i in range(100)  # ~ 12 KB
        ]
        _write_wal(wal, records)

        yielded = list(_iter_lines_reverse(wal, chunk_size=chunk_size))
        assert len(yielded) == 100, (
            f"chunk_size={chunk_size}: expected 100 lines, got {len(yielded)}"
        )

        result = detect_in_flight_orders(wal)
        assert result == frozenset({f"BIG-{i}" for i in range(100)})


# ---------------------------------------------------------------------------
# C1 / M-2: writer 経由の言語境界 contract test
# ---------------------------------------------------------------------------


class TestTodayCutoff:
    """C2: JST 当日 0:00 より古い ``ts`` のレコードは terminal 扱い。

    立花 API は accepted までしか同期で返さず、約定/取消は EVENT 経由なので
    writer は終端 phase を書けない。前日以前の `accepted` 残骸は venue 側で
    必ず確定済み — `today_start_ms` でフィルタして false-positive を防ぐ。
    """

    TODAY_MS = 1_777_550_000_000  # 任意の "今日" 基準（テスト用固定値）
    YESTERDAY_MS = TODAY_MS - 60_000  # 1 分前（前日 23:59 と同じ扱い）
    TODAY_LATER_MS = TODAY_MS + 3_600_000  # 1 時間後

    def test_today_accepted_is_in_flight(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "T1", "phase": "accepted", "ts": self.TODAY_LATER_MS},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal, today_start_ms=self.TODAY_MS)
        assert result == frozenset({"T1"})

    def test_yesterday_accepted_is_terminal(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "T2", "phase": "accepted", "ts": self.YESTERDAY_MS},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal, today_start_ms=self.TODAY_MS)
        assert result == frozenset(), (
            "前日以前の accepted は venue 側で確定済みなので terminal 扱いされる必要がある"
        )

    def test_today_submit_plus_yesterday_accepted(self, tmp_path: Path) -> None:
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "T3a", "phase": "accepted", "ts": self.YESTERDAY_MS},
            {"client_order_id": "T3b", "phase": "submit", "ts": self.TODAY_LATER_MS},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal, today_start_ms=self.TODAY_MS)
        assert result == frozenset({"T3b"}), (
            "前日 accepted は除外、当日 submit のみ in-flight"
        )

    def test_legacy_e2e_residue_seventeen_yesterday(self, tmp_path: Path) -> None:
        """実環境再現 — 前日以前の `e2e-*` 残骸 17 件は in-flight にしない。"""
        records = [
            {"client_order_id": f"e2e-{i}", "phase": "accepted", "ts": self.YESTERDAY_MS}
            for i in range(17)
        ]
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, records)

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal, today_start_ms=self.TODAY_MS)
        assert result == frozenset()

    def test_today_submit_then_rejected(self, tmp_path: Path) -> None:
        """既存動作の再確認 — 当日 submit→rejected は terminal 扱い。"""
        wal = tmp_path / "tachibana_orders.jsonl"
        _write_wal(wal, [
            {"client_order_id": "T5", "phase": "submit", "ts": self.TODAY_LATER_MS},
            {"client_order_id": "T5", "phase": "rejected", "ts": self.TODAY_LATER_MS + 1},
        ])

        from engine.wal_in_flight import detect_in_flight_orders
        result = detect_in_flight_orders(wal, today_start_ms=self.TODAY_MS)
        assert result == frozenset()


class TestWalContract:
    """writer (`tachibana_orders.py`) を直接呼んで生成した WAL を、
    reader (`wal_in_flight.detect_in_flight_orders`) が正しく検知することを pin する。

    これにより writer が ``phase`` キーを変えたら本テストが先に落ちる。
    """

    def test_uses_real_writer_schema_submit_only(self, tmp_path: Path) -> None:
        """writer の `_audit_log_submit` だけ呼んだ後の WAL は in-flight 扱いになる。"""
        from engine.exchanges.tachibana_orders import _audit_log_submit
        from engine.wal_in_flight import detect_in_flight_orders

        wal = tmp_path / "tachibana_orders.jsonl"
        with wal.open("a", encoding="utf-8") as f:
            _audit_log_submit(
                f,
                client_order_id="CID-CONTRACT-1",
                request_key=12345,
                instrument_id="1301.TSE",
                order_side="buy",
                order_type="market",
                quantity="100",
            )

        result = detect_in_flight_orders(wal)
        assert "CID-CONTRACT-1" in result, (
            "writer の `_audit_log_submit` で書いた行は in-flight として検知される必要がある。"
            f"got {result!r}"
        )

    def test_uses_real_writer_schema_submit_then_rejected(self, tmp_path: Path) -> None:
        """submit → rejected を writer 経由で書いたら terminal 扱いになる。"""
        from engine.exchanges.tachibana_orders import (
            _audit_log_rejected,
            _audit_log_submit,
        )
        from engine.wal_in_flight import detect_in_flight_orders

        wal = tmp_path / "tachibana_orders.jsonl"
        with wal.open("a", encoding="utf-8") as f:
            _audit_log_submit(
                f,
                client_order_id="CID-CONTRACT-2",
                request_key=12346,
                instrument_id="1301.TSE",
                order_side="buy",
                order_type="market",
                quantity="100",
            )
            _audit_log_rejected(
                f,
                client_order_id="CID-CONTRACT-2",
                reason_code="E001",
                reason_text="venue rejected",
            )

        result = detect_in_flight_orders(wal)
        assert "CID-CONTRACT-2" not in result, (
            "rejected で完結した注文は in-flight 扱いされない必要がある。"
            f"got {result!r}"
        )

    def test_uses_real_writer_schema_submit_then_accepted(self, tmp_path: Path) -> None:
        """submit → accepted は in-flight 扱い（venue 受領済みの未約定）。"""
        from engine.exchanges.tachibana_orders import (
            _audit_log_accepted,
            _audit_log_submit,
        )
        from engine.wal_in_flight import detect_in_flight_orders

        wal = tmp_path / "tachibana_orders.jsonl"
        with wal.open("a", encoding="utf-8") as f:
            _audit_log_submit(
                f,
                client_order_id="CID-CONTRACT-3",
                request_key=12347,
                instrument_id="1301.TSE",
                order_side="buy",
                order_type="market",
                quantity="100",
            )
            _audit_log_accepted(
                f,
                client_order_id="CID-CONTRACT-3",
                venue_order_id="VENUE-99",
                p_no=1,
                warning_code=None,
                warning_text=None,
            )

        result = detect_in_flight_orders(wal)
        assert "CID-CONTRACT-3" in result, (
            "accepted（venue 受領済み・未約定）は in-flight 扱いされる必要がある。"
            f"got {result!r}"
        )
