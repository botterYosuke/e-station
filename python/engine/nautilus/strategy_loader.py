"""User Strategy loader for N4.1.

Dynamically loads a user-written ``.py`` file containing exactly one
``nautilus_trader.trading.strategy.Strategy`` subclass and returns an
instantiated instance.

Design notes:
    - No sandboxing / process isolation. User strategies run in-process
      (Q2 resolved: user strategy execution is the user's responsibility).
    - Subclasses imported transitively from other modules are filtered out
      via ``cls.__module__ == module.__name__``.
    - The module name passed to ``importlib.util.spec_from_file_location``
      is fixed to ``"user_strategy"``. Loading multiple strategies
      simultaneously is out of scope for N4.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import logging
import traceback
from pathlib import Path
from typing import Any

from nautilus_trader.trading.strategy import Strategy

__all__ = ["StrategyLoadError", "_INCOMPATIBLE_HANDLERS", "load_strategy_from_file"]

_MODULE_NAME = "user_strategy"
_INCOMPATIBLE_HANDLERS: frozenset[str] = frozenset(
    {"on_order_book_delta", "on_order_book_deltas", "on_quote_tick"}
)

_logger = logging.getLogger(__name__)


class StrategyLoadError(Exception):
    """Raised when a user Strategy file cannot be loaded or instantiated."""


def load_strategy_from_file(
    path: Path,
    init_kwargs: dict[str, Any] | None = None,
) -> Strategy:
    """Load a user-defined Strategy ``.py`` file and instantiate it.

    Args:
        path: Path to the ``.py`` file. Must exist.
        init_kwargs: Keyword arguments forwarded to the Strategy constructor.

    Returns:
        An instantiated Strategy subclass.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        StrategyLoadError: If the module contains zero or multiple Strategy
            subclasses, or fails to import (syntax error / ImportError).
        TypeError: If ``init_kwargs`` does not match the constructor signature
            (propagated as-is).
    """
    if not path.exists():
        raise FileNotFoundError(f"strategy file not found: {path}")

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise StrategyLoadError(f"could not create module spec for {path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SyntaxError:
        raise StrategyLoadError(
            f"failed to import {path}:\n{traceback.format_exc()}"
        )
    except ImportError:
        raise StrategyLoadError(
            f"failed to import {path}:\n{traceback.format_exc()}"
        )
    except Exception:  # pragma: no cover - defensive
        raise StrategyLoadError(
            f"failed to import {path}:\n{traceback.format_exc()}"
        )

    subclasses: list[type[Strategy]] = [
        cls
        for _name, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, Strategy)
        and cls is not Strategy
        and cls.__module__ == module.__name__
    ]

    if len(subclasses) == 0:
        raise StrategyLoadError(f"no Strategy subclass found in {path}")
    if len(subclasses) > 1:
        names = ", ".join(cls.__name__ for cls in subclasses)
        raise StrategyLoadError(
            f"multiple Strategy subclasses found: [{names}]"
        )

    strategy_cls = subclasses[0]
    kwargs = init_kwargs or {}
    instance = strategy_cls(**kwargs)
    _check_compat(path)
    return instance


def _check_compat(path: Path) -> None:
    """AST-scan *path* and emit a WARNING for each incompatible handler found."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _INCOMPATIBLE_HANDLERS:
            _logger.warning(
                "strategy file %s defines '%s' which is not compatible "
                "with replay mode (TradeTick/Bar only). "
                "This handler will not be called during replay.",
                path,
                node.name,
            )
