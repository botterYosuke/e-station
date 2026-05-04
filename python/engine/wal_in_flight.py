"""WAL in-flight 検知ユーティリティ（F7 モード切替安全装置）。

tachibana_orders.jsonl を tail から逆順スキャンし、未完了（in-flight）な
``client_order_id`` 集合を返す。

Writer schema (`tachibana_orders.py:_audit_log_*`)
--------------------------------------------------
レコードは ``phase`` キーで状態を表現する::

    {"phase": "submit",   "client_order_id": "...", ...}  # HTTP 送信直前 (fsync)
    {"phase": "accepted", "client_order_id": "...", ...}  # venue 受領済み
    {"phase": "rejected", "client_order_id": "...", ...}  # venue 拒否

**重要**: ``phase`` は writer/reader 両方で wire schema として共有されている。
writer (`python/engine/exchanges/tachibana_orders.py`) のキー名・値を変更したら、
本モジュール ``TERMINAL_PHASES`` と Rust 側 ``has_wal_in_flight_orders``
(`src/main.rs`) を同時に更新すること。両者の言語境界 contract は
``python/tests/test_wal_in_flight_detection.py::TestWalContract`` および
``tests/wal_writer_reader_contract.rs`` で pin されている。

判定ロジック
------------
- ``phase == "rejected"``: terminal（in-flight ではない）
- ``phase == "submit"``  : in-flight（HTTP 送信したが応答未着 = クラッシュ残留含む）
- ``phase == "accepted"``: in-flight（venue 受領済みの未約定。安全側で再送阻止）
- 未知 ``phase``        : in-flight 扱い（保守的）

M6: IO エラー時は warning ログを出して空集合を返す。
M9: 大きな WAL でメモリ使用量を抑えるため、ファイル末尾から chunk 単位で
    逆順読み出しを行う（改行バイト境界で分割）。
"""
from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# 終端 phase 集合（writer の `_audit_log_rejected` のみ）。
# `submit` / `accepted` / 未知 phase は in-flight 扱い（保守的判定）。
TERMINAL_PHASES: frozenset[str] = frozenset({"rejected"})


def _iter_lines_reverse(path: Path, chunk_size: int = 8192):
    """ファイル末尾から chunk 単位で読み戻し、行を逆順に yield する。

    改行バイト境界で分割するため、長い行（chunk_size 超）でも安全に動作する。
    バイトを内部バッファに蓄積し、改行が見つかった分だけ取り出して yield する。

    Args:
        path: 対象 JSONL ファイル。
        chunk_size: ファイル末尾から一度に読む最大バイト数。

    Yields:
        最終行 → 先頭行の順にデコード済み文字列（改行なし）。
    """
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        position = fh.tell()
        buffer = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            fh.seek(position)
            chunk = fh.read(read_size)
            buffer = chunk + buffer
            # Split on newline; first piece may be a partial line at the head
            # of the chunk that needs more bytes from earlier in the file.
            parts = buffer.split(b"\n")
            # The 0th element is the head fragment; keep it in the buffer
            # unless we've reached the start of the file.
            if position > 0:
                buffer = parts[0]
                tail = parts[1:]
            else:
                buffer = b""
                tail = parts
            for line in reversed(tail):
                if not line:
                    continue
                try:
                    yield line.decode("utf-8")
                except UnicodeDecodeError:
                    # Skip undecodable lines rather than abort — WAL may
                    # have a partial multibyte tail from a crashed writer.
                    continue
        # Any residual bytes at the head of the file (no trailing newline).
        if buffer:
            try:
                yield buffer.decode("utf-8")
            except UnicodeDecodeError:
                return


def detect_in_flight_orders(path: Path | str) -> frozenset[str]:
    """WAL を逆順スキャンして in-flight な client_order_id 集合を返す。

    最新 phase が ``rejected`` でない client_order_id を in-flight とみなす。
    ``submit`` / ``accepted`` / 未知 phase はすべて in-flight 扱い。

    Returns:
        in-flight な ``client_order_id`` の frozenset。
        ファイルが存在しない・読めない場合は空の frozenset を返す。
    """
    path = Path(path)
    if not path.exists():
        return frozenset()

    seen: dict[str, str] = {}
    try:
        # M9: ファイル末尾から逆順 chunk 読み出し（メモリ効率）。
        for line in _iter_lines_reverse(path):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # Truncated trailing line — skip but keep scanning.
                continue
            order_id = record.get("client_order_id")
            phase = record.get("phase")
            if order_id and phase and order_id not in seen:
                seen[order_id] = phase
    except (OSError, io.UnsupportedOperation) as exc:
        # M6: IO エラーは保守的に「未約定なし」と判断するが、観測のため
        # warning ログを出力する（無音 fallback だと診断できないため）。
        log.warning(
            "[F7/WAL] failed to read %s for in-flight detection: %s; "
            "treating as no in-flight orders",
            path,
            exc,
        )
        return frozenset()

    # 終端 phase（rejected）以外はすべて in-flight 扱い。未知 phase も同様。
    return frozenset(oid for oid, ph in seen.items() if ph not in TERMINAL_PHASES)
