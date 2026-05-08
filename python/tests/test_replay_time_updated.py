"""schema 3.22: ReplayTimeUpdated イベントのテスト（Issue 3 current-time display）。

- schemas.py に ReplayTimeUpdated クラスが存在すること
- SCHEMA_MINOR が 22 に更新されていること
- engine_runner.py が毎足 ReplayTimeUpdated を emit すること（source inspection）
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
ENGINE_DIR = REPO_ROOT / "python" / "engine"


class TestReplayTimeUpdatedSchema:
    def test_class_exists(self) -> None:
        from engine.schemas import ReplayTimeUpdated

        assert ReplayTimeUpdated is not None

    def test_instantiates_with_timestamp_ms(self) -> None:
        from engine.schemas import ReplayTimeUpdated

        msg = ReplayTimeUpdated(timestamp_ms=1_700_000_000_000)
        assert msg.timestamp_ms == 1_700_000_000_000
        assert msg.event == "ReplayTimeUpdated"

    def test_event_field_is_literal(self) -> None:
        from engine.schemas import ReplayTimeUpdated

        msg = ReplayTimeUpdated(timestamp_ms=0)
        assert msg.event == "ReplayTimeUpdated"

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError

        from engine.schemas import ReplayTimeUpdated

        with pytest.raises(ValidationError):
            ReplayTimeUpdated(timestamp_ms=100, unknown_field="bad")

    def test_schema_minor_is_22(self) -> None:
        """ReplayTimeUpdated は schema 3.22 で導入。現在は 22 以上であることを確認する。"""
        from engine.schemas import SCHEMA_MINOR

        assert SCHEMA_MINOR >= 22, f"SCHEMA_MINOR must be >= 22 (ReplayTimeUpdated added), got {SCHEMA_MINOR}"


class TestEngineRunnerEmitsReplayTimeUpdated:
    """Source inspection: engine_runner.py が ReplayTimeUpdated を emit しているか。"""

    def _runner_source(self) -> str:
        return (ENGINE_DIR / "nautilus" / "engine_runner.py").read_text(encoding="utf-8")

    def test_replay_time_updated_emit_exists(self) -> None:
        src = self._runner_source()
        assert "ReplayTimeUpdated" in src, (
            "engine_runner.py must emit ReplayTimeUpdated after each tick"
        )

    def test_timestamp_ms_uses_ns_to_ms_division(self) -> None:
        """ns → ms 変換（// 1_000_000）が正しいことを確認する。"""
        src = self._runner_source()
        assert "1_000_000" in src, (
            "engine_runner.py must convert ns to ms via // 1_000_000"
        )

    def test_emit_is_after_date_change_marker(self) -> None:
        """ReplayTimeUpdated が DateChangeMarker より後に emit されること（行番号比較）。

        emit 行 = "DateChangeMarker" または "ReplayTimeUpdated" を含む行
        かつ emit( / _outbox.append( / append({ のいずれかを含む行。
        複数行 emit 呼び出し（emit({\n  "event": "Foo"\n}）も対応するため、
        "event": "..." と emit キーワードが近傍行にある emit ブロック先頭行を探す。
        """
        src = self._runner_source()
        lines = src.splitlines()

        _EMIT_KEYWORDS = ("emit(", "_outbox.append(", "append({")

        def _find_emit_lineno_for_event(event_name: str) -> int | None:
            """event_name が "event": "<name>" として現れる行、またはその直前の emit( 行番号を返す。"""
            for lineno, line in enumerate(lines):
                # 同一行に emit キーワードとイベント名がある場合（インライン形式）
                if event_name in line and any(kw in line for kw in _EMIT_KEYWORDS):
                    return lineno
                # "event": "<name>" がある行の場合、直前に遡って emit( 行を探す
                if f'"{event_name}"' in line or f"'{event_name}'" in line:
                    # 上方向に最大 5 行遡って emit キーワードを探す
                    for offset in range(min(lineno + 1, 6)):
                        prev = lines[lineno - offset]
                        if any(kw in prev for kw in _EMIT_KEYWORDS):
                            return lineno - offset
            return None

        date_lineno = _find_emit_lineno_for_event("DateChangeMarker")
        time_lineno = _find_emit_lineno_for_event("ReplayTimeUpdated")

        assert date_lineno is not None, "DateChangeMarker emit line not found"
        assert time_lineno is not None, "ReplayTimeUpdated emit line not found"
        assert time_lineno > date_lineno, (
            f"ReplayTimeUpdated (line {time_lineno}) must appear after "
            f"DateChangeMarker (line {date_lineno}) in source "
            "to guarantee ordering in the same message queue"
        )
