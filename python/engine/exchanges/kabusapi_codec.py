"""kabuステーション API のエンコード/デコードヘルパー。

kabuステーション API は UTF-8 JSON (立花の Shift-JIS と異なる)。
Shift-JIS バイト列が渡された場合は即時 UnicodeDecodeError を raise して
混入を防ぐ (comparison.md §1, INV-K2-SJIS-REJECT)。
"""
from __future__ import annotations

import json
from typing import Any


def encode(payload: Any) -> bytes:
    """Python object を UTF-8 JSON bytes に変換する。"""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def decode(data: bytes | str) -> Any:
    """UTF-8 JSON bytes / str を Python object に変換する。

    bytes の場合は明示的に UTF-8 でデコードする。
    SJIS 非互換バイト列は UnicodeDecodeError を raise する (INV-K2-SJIS-REJECT)。
    """
    if isinstance(data, bytes):
        text = data.decode("utf-8")  # SJIS バイトなら ここで UnicodeDecodeError
    else:
        text = data
    return json.loads(text)


# sell/buy の区分コード (comparison.md §4 regression guard)
SIDE_SELL = "1"
SIDE_BUY = "2"  # kabu は "2" = 買い (立花の "3" と違う！ INV-K2-SIDE-BUY-2)
