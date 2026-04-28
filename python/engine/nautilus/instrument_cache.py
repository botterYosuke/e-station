"""Instrument マスタキャッシュ (N1.2 / Q10 案 B + 案 A fallback)

立花 live モードで取得した sHikaku を ~/.cache/flowsurface/instrument_master.json に
永続化し、replay モードからも参照する。

優先順位:
    1. 起動 config の lot_size_override
    2. ディスクキャッシュ
    3. fallback (lot_size=100, price_precision=1) + log.warning

設計:
- スレッドセーフ性は持たない（process-wide singleton + GIL に依存）
- atomic write: tmp → os.replace でクラッシュ耐性
- バージョンフィールドで将来のスキーマ進化に対応
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)

_CACHE_VERSION = 1
_FALLBACK_LOT_SIZE = 100
_FALLBACK_PRICE_PRECISION = 1


def _default_cache_path() -> Path:
    return Path.home() / ".cache" / "flowsurface" / "instrument_master.json"


class InstrumentCache:
    """Instrument マスタキャッシュ。

    JSON フォーマット (v1):
        {
          "version": 1,
          "instruments": {
            "7203.TSE": {"lot_size": 100, "price_precision": 1, "updated_ts_ms": 1714300000000}
          }
        }
    """

    _shared: ClassVar["InstrumentCache | None"] = None

    def __init__(self, cache_path: Path | str | None = None) -> None:
        self._cache_path: Path = (
            Path(cache_path) if cache_path is not None else _default_cache_path()
        )
        self._instruments: dict[str, dict] = {}
        self._load()

    @classmethod
    def shared(cls) -> "InstrumentCache":
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    @classmethod
    def reset_shared_for_testing(cls) -> None:
        cls._shared = None

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def get_lot_size(
        self,
        instrument_id: str,
        override: dict[str, int] | None = None,
    ) -> int:
        if override is not None and instrument_id in override:
            return int(override[instrument_id])
        entry = self._instruments.get(instrument_id)
        if entry is not None and "lot_size" in entry:
            return int(entry["lot_size"])
        logger.warning(
            "instrument master cache miss for %s, using fallback lot_size=%d",
            instrument_id,
            _FALLBACK_LOT_SIZE,
        )
        return _FALLBACK_LOT_SIZE

    def get_price_precision(self, instrument_id: str) -> int:
        entry = self._instruments.get(instrument_id)
        if entry is not None and "price_precision" in entry:
            return int(entry["price_precision"])
        return _FALLBACK_PRICE_PRECISION

    def update_from_live(
        self,
        instrument_id: str,
        lot_size: int,
        price_precision: int = _FALLBACK_PRICE_PRECISION,
    ) -> None:
        self._instruments[instrument_id] = {
            "lot_size": int(lot_size),
            "price_precision": int(price_precision),
            "updated_ts_ms": int(time.time() * 1000),
        }
        self._persist()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            raw = self._cache_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "instrument cache at %s is unreadable (%s); treating as empty",
                self._cache_path,
                exc,
            )
            return
        if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
            logger.warning(
                "instrument cache at %s has unexpected version; treating as empty",
                self._cache_path,
            )
            return
        instruments = data.get("instruments")
        if isinstance(instruments, dict):
            self._instruments = {
                str(k): dict(v) for k, v in instruments.items() if isinstance(v, dict)
            }

    def _persist(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._cache_path.with_name(self._cache_path.name + ".tmp")
        payload = {
            "version": _CACHE_VERSION,
            "instruments": self._instruments,
        }
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self._cache_path)
