"""N1.8: live/replay 互換 lint テスト。

Strategy ファイルの AST を解析し、on_order_book_* / on_quote_tick の定義があれば fail する。
spec.md §3.5.4 の互換性 CI 検査を実装する。
"""

from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Lint 関数
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES = ("on_order_book_",)
_FORBIDDEN_EXACT = {"on_quote_tick"}


def check_strategy_replay_compat(source: str) -> list[str]:
    """Strategy ソースコードを AST 解析し、禁止メソッド名のリストを返す。

    禁止メソッド:
      - on_order_book_* (prefix match)
      - on_quote_tick (exact match)

    戻り値が空リスト → 互換性あり（pass）
    戻り値が非空 → 禁止メソッドが含まれる（fail）

    replay モードでは OrderBook / QuoteTick が提供されないため、
    これらのメソッドを定義した戦略は replay 互換でない（spec.md §3.5.2）。
    """
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = item.name
            # on_order_book_* prefix check
            for prefix in _FORBIDDEN_PREFIXES:
                if name.startswith(prefix):
                    violations.append(name)
                    break
            else:
                # on_quote_tick exact check
                if name in _FORBIDDEN_EXACT:
                    violations.append(name)
    return violations


# ---------------------------------------------------------------------------
# テスト用サンプルソース
# ---------------------------------------------------------------------------

_GOOD_STRATEGY = '''
class MyStrategy:
    def on_trade_tick(self, tick): pass
    def on_bar(self, bar): pass
'''

_BAD_STRATEGY_ORDER_BOOK_DELTA = '''
class MyStrategy:
    def on_trade_tick(self, tick): pass
    def on_order_book_delta(self, data): pass  # NG
'''

_BAD_STRATEGY_QUOTE_TICK = '''
class MyStrategy:
    def on_quote_tick(self, tick): pass  # NG
'''

_BAD_STRATEGY_ORDER_BOOK_DELTAS = '''
class MyStrategy:
    def on_order_book_deltas(self, deltas): pass  # NG
'''

_BAD_STRATEGY_MULTIPLE = '''
class MyStrategy:
    def on_order_book_delta(self, data): pass  # NG
    def on_quote_tick(self, tick): pass         # NG
    def on_trade_tick(self, tick): pass
'''

# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

def test_good_strategy_passes_lint():
    """禁止メソッドを持たない戦略は lint を通過すること。"""
    violations = check_strategy_replay_compat(_GOOD_STRATEGY)
    assert violations == [], f"Expected no violations, got: {violations}"


def test_strategy_with_order_book_delta_fails_lint():
    """on_order_book_delta を持つ戦略は lint で検出されること。"""
    violations = check_strategy_replay_compat(_BAD_STRATEGY_ORDER_BOOK_DELTA)
    assert "on_order_book_delta" in violations, (
        f"Expected 'on_order_book_delta' in violations, got: {violations}"
    )


def test_strategy_with_quote_tick_fails_lint():
    """on_quote_tick を持つ戦略は lint で検出されること。"""
    violations = check_strategy_replay_compat(_BAD_STRATEGY_QUOTE_TICK)
    assert "on_quote_tick" in violations, (
        f"Expected 'on_quote_tick' in violations, got: {violations}"
    )


def test_strategy_with_order_book_deltas_fails_lint():
    """on_order_book_deltas (plural) を持つ戦略は lint で検出されること。"""
    violations = check_strategy_replay_compat(_BAD_STRATEGY_ORDER_BOOK_DELTAS)
    assert "on_order_book_deltas" in violations, (
        f"Expected 'on_order_book_deltas' in violations, got: {violations}"
    )


def test_strategy_with_multiple_forbidden_methods_detects_all():
    """複数の禁止メソッドを持つ戦略はすべて検出されること。"""
    violations = check_strategy_replay_compat(_BAD_STRATEGY_MULTIPLE)
    assert "on_order_book_delta" in violations
    assert "on_quote_tick" in violations
    assert len(violations) == 2


def test_example_buy_and_hold_replay_compat_lint():
    """docs/example/buy_and_hold.py は on_order_book_* / on_quote_tick を持たないこと。

    サンプル戦略が live/replay 互換規約（spec.md §3.5.2）を満たすことを保証する。
    """
    source_path = (
        Path(__file__).parents[2]
        / "docs"
        / "example"
        / "buy_and_hold.py"
    )
    source = source_path.read_text(encoding="utf-8")
    violations = check_strategy_replay_compat(source)
    assert violations == [], (
        f"Example BuyAndHold strategy must not define forbidden methods: {violations}"
    )


def test_non_class_functions_not_flagged():
    """クラス外のモジュールレベル関数は lint 対象外であること。"""
    source = '''
def on_order_book_delta(data): pass
def on_quote_tick(tick): pass

class MyStrategy:
    def on_trade_tick(self, tick): pass
'''
    violations = check_strategy_replay_compat(source)
    assert violations == [], (
        f"Module-level functions must not be flagged, got: {violations}"
    )
