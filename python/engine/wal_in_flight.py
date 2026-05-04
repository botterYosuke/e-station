"""WAL in-flight 検知ユーティリティ（F7 モード切替安全装置）。

tachibana_orders.jsonl を tail から逆順スキャンし、
最新 status が "submitted" または "partial" の order_id 集合を返す。

M6: IO エラー時は warning ログを出して空集合を返す。
M8: 終端ステータス集合（filled / cancelled / rejected）の補集合で
    in-flight を判定する。未知ステータスは保守的に in-flight 扱い。
M9: 大きな WAL でメモリ使用量を抑えるため、ファイル末尾から chunk 単位で
    逆順読み出しを行う（改行バイト境界で分割）。
M11: writer (`tachibana_orders.py`) は ``client_order_id`` のみを書き出す
     ため、``order_id`` フィールド名 fallback は不要となり削除した。
"""
from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# M8: 終端ステータス集合。これら以外のステータス（submitted / partial /
# 未知ステータス）はすべて in-flight として扱う（保守的判定）。
TERMINAL_STATUSES: frozenset[str] = frozenset({"filled", "cancelled", "rejected"})


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
    """WAL を逆順スキャンして in-flight な order_id 集合を返す。

    Returns:
        最新 status が ``filled`` / ``cancelled`` / ``rejected`` のどれでもない
        order_id の frozenset（M8: 未知ステータスも in-flight 扱い）。
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
            # M11: writer (`tachibana_orders.py`) は ``client_order_id`` のみ書き出す。
            # Legacy ``order_id`` fallback は writer 側で発生しないため削除。
            order_id = record.get("client_order_id")
            status = record.get("status")
            if order_id and status and order_id not in seen:
                seen[order_id] = status
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

    # M8: 終端集合の補集合で in-flight を判定。未知ステータスも in-flight 扱い。
    return frozenset(oid for oid, st in seen.items() if st not in TERMINAL_STATUSES)
