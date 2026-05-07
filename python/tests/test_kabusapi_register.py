"""RegisterSet のテスト: 51件目エラー / LRU evict / touch。"""
import pytest

from engine.exchanges.kabusapi_auth import KabuRegisterFullError
from engine.exchanges.kabusapi_register import RegisterSet, MAX_SYMBOLS


@pytest.mark.demo_kabu
def test_register_51st_raises_full():
    """51 件目の register で KabuRegisterFullError が発生する (INV-K4-REG-FULL)."""
    rs = RegisterSet(max_symbols=50)
    for i in range(50):
        rs.register(str(i), 1)
    assert len(rs) == 50

    with pytest.raises(KabuRegisterFullError):
        rs.register("9999", 1)  # 51件目


@pytest.mark.demo_kabu
def test_lru_evict_emits_subscription_evicted():
    """LRU evict 時に on_evict コールバックが呼ばれる (INV-K4-LRU-EVICT)."""
    evicted = []
    rs = RegisterSet(on_evict=lambda sym, exc: evicted.append((sym, exc)), max_symbols=3)

    rs.register("A", 1)
    rs.register("B", 1)
    rs.register("C", 1)

    evicted_key = rs.evict_lru()
    assert evicted_key == ("A", 1), "LRU は最初に登録した銘柄"
    assert evicted == [("A", 1)], "on_evict が呼ばれた"


@pytest.mark.demo_kabu
def test_touch_updates_lru_order():
    """touch で LRU 順が更新される。"""
    rs = RegisterSet(max_symbols=3)
    rs.register("A", 1)
    rs.register("B", 1)
    rs.register("C", 1)

    # A を touch すると A が最新になり、次の evict は B が対象
    rs.touch("A", 1)
    evicted_key = rs.evict_lru()
    assert evicted_key == ("B", 1)


@pytest.mark.demo_kabu
def test_register_existing_symbol_does_not_raise():
    """既存銘柄の再 register は KabuRegisterFullError を raise しない。"""
    rs = RegisterSet(max_symbols=1)
    rs.register("A", 1)
    # 同じ銘柄の再 register は touch 扱いで例外なし
    rs.register("A", 1)
    assert len(rs) == 1


@pytest.mark.demo_kabu
def test_unregister_all_clears():
    """unregister_all で全銘柄がクリアされる。"""
    rs = RegisterSet(max_symbols=3)
    rs.register("A", 1)
    rs.register("B", 1)
    rs.unregister_all()
    assert len(rs) == 0


@pytest.mark.demo_kabu
def test_max_symbols_constant():
    """MAX_SYMBOLS は 50 (comparison.md §7 一次ソース)."""
    assert MAX_SYMBOLS == 50
    assert RegisterSet.MAX == 50
