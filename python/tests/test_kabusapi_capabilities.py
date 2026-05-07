"""capabilities の max_push_symbols と RegisterSet.MAX の一致 assert (INV-K1-CAP)."""
import pytest

from engine.exchanges.kabusapi_register import RegisterSet, MAX_SYMBOLS


@pytest.mark.demo_kabu
def test_capabilities_max_push_symbols_matches_register_set():
    """max_push_symbols=50 と RegisterSet.MAX=50 は常に一致しなければならない。

    Rust 側の capabilities で "max_push_symbols": 50 を設定しているため、
    Python 側 RegisterSet.MAX も同じ値でなければ不変条件が壊れる。
    (INV-K1-CAP / U27 / R2-C-L1)
    """
    EXPECTED_MAX_PUSH_SYMBOLS = 50  # Rust capabilities.rs の値と一致させる
    assert MAX_SYMBOLS == EXPECTED_MAX_PUSH_SYMBOLS, (
        f"RegisterSet.MAX_SYMBOLS ({MAX_SYMBOLS}) must match "
        f"Rust capabilities 'max_push_symbols' ({EXPECTED_MAX_PUSH_SYMBOLS})"
    )
    assert RegisterSet.MAX == EXPECTED_MAX_PUSH_SYMBOLS, (
        f"RegisterSet.MAX ({RegisterSet.MAX}) must match "
        f"Rust capabilities 'max_push_symbols' ({EXPECTED_MAX_PUSH_SYMBOLS})"
    )
