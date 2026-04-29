"""N4.1 user Strategy loader tests."""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest
from nautilus_trader.trading.strategy import Strategy

from engine.nautilus.strategy_loader import (
    StrategyLoadError,
    load_strategy_from_file,
)


def test_loads_single_strategy_subclass_from_file(tmp_path: Path) -> None:
    """Happy path: a file containing exactly one Strategy subclass loads and instantiates."""
    strategy_file = tmp_path / "user_strategy.py"
    strategy_file.write_text(
        textwrap.dedent(
            """
            from nautilus_trader.config import StrategyConfig
            from nautilus_trader.trading.strategy import Strategy


            class MyUserStrategy(Strategy):
                def __init__(self) -> None:
                    super().__init__(config=StrategyConfig(strategy_id="my-user-001"))
                    self.marker = "loaded"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    instance = load_strategy_from_file(strategy_file)

    assert isinstance(instance, Strategy)
    assert type(instance).__name__ == "MyUserStrategy"
    assert getattr(instance, "marker", None) == "loaded"


def test_missing_file_raises_filenotfounderror(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.py"
    with pytest.raises(FileNotFoundError):
        load_strategy_from_file(missing)


def test_no_strategy_subclass_raises(tmp_path: Path) -> None:
    f = tmp_path / "no_strategy.py"
    f.write_text(
        textwrap.dedent(
            """
            class NotAStrategy:
                pass
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(StrategyLoadError) as exc:
        load_strategy_from_file(f)
    assert "no Strategy subclass found" in str(exc.value)
    assert str(f) in str(exc.value)


def test_multiple_strategy_subclasses_raises(tmp_path: Path) -> None:
    f = tmp_path / "two_strategies.py"
    f.write_text(
        textwrap.dedent(
            """
            from nautilus_trader.config import StrategyConfig
            from nautilus_trader.trading.strategy import Strategy


            class Foo(Strategy):
                def __init__(self) -> None:
                    super().__init__(config=StrategyConfig(strategy_id="foo-001"))


            class Bar(Strategy):
                def __init__(self) -> None:
                    super().__init__(config=StrategyConfig(strategy_id="bar-001"))
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(StrategyLoadError) as exc:
        load_strategy_from_file(f)
    msg = str(exc.value)
    assert "multiple Strategy subclasses found" in msg
    assert "Foo" in msg
    assert "Bar" in msg


def test_syntax_error_raises_strategy_load_error(tmp_path: Path) -> None:
    f = tmp_path / "broken.py"
    f.write_text("def oops(:\n    pass\n", encoding="utf-8")
    with pytest.raises(StrategyLoadError) as exc:
        load_strategy_from_file(f)
    msg = str(exc.value)
    assert "failed to import" in msg
    # traceback is included
    assert "SyntaxError" in msg or "Traceback" in msg


def test_import_error_raises_strategy_load_error(tmp_path: Path) -> None:
    f = tmp_path / "bad_import.py"
    f.write_text(
        "import definitely_not_a_real_module_xyz_12345\n",
        encoding="utf-8",
    )
    with pytest.raises(StrategyLoadError) as exc:
        load_strategy_from_file(f)
    msg = str(exc.value)
    assert "failed to import" in msg
    assert (
        "ModuleNotFoundError" in msg
        or "ImportError" in msg
        or "Traceback" in msg
    )


def test_init_kwargs_forwarded_to_constructor(tmp_path: Path) -> None:
    f = tmp_path / "with_kwargs.py"
    f.write_text(
        textwrap.dedent(
            """
            from nautilus_trader.config import StrategyConfig
            from nautilus_trader.trading.strategy import Strategy


            class Configurable(Strategy):
                def __init__(self, *, label: str, multiplier: int) -> None:
                    super().__init__(
                        config=StrategyConfig(strategy_id="cfg-001"),
                    )
                    self.label = label
                    self.multiplier = multiplier
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    instance = load_strategy_from_file(
        f, init_kwargs={"label": "alpha", "multiplier": 7}
    )
    assert getattr(instance, "label", None) == "alpha"
    assert getattr(instance, "multiplier", None) == 7


def test_incompatible_handler_emits_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """on_order_book_delta を定義した Strategy をロードすると WARNING が出るが、ロードは成功すること。"""
    f = tmp_path / "bad_compat.py"
    f.write_text(
        textwrap.dedent(
            """
            from nautilus_trader.config import StrategyConfig
            from nautilus_trader.trading.strategy import Strategy


            class IncompatStrategy(Strategy):
                def __init__(self) -> None:
                    super().__init__(config=StrategyConfig(strategy_id="incompat-001"))

                def on_order_book_delta(self, data) -> None:
                    pass
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="engine.nautilus.strategy_loader"):
        instance = load_strategy_from_file(f)

    # load succeeds — not blocked
    assert isinstance(instance, Strategy)
    assert type(instance).__name__ == "IncompatStrategy"

    # warning was emitted
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("on_order_book_delta" in str(m) for m in warning_messages), (
        f"Expected warning about 'on_order_book_delta', got: {warning_messages}"
    )


def test_compatible_handler_no_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """on_bar のみ定義した Strategy をロードしても WARNING が出ないこと。"""
    f = tmp_path / "good_compat.py"
    f.write_text(
        textwrap.dedent(
            """
            from nautilus_trader.config import StrategyConfig
            from nautilus_trader.trading.strategy import Strategy


            class CompatStrategy(Strategy):
                def __init__(self) -> None:
                    super().__init__(config=StrategyConfig(strategy_id="compat-001"))

                def on_bar(self, bar) -> None:
                    pass
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="engine.nautilus.strategy_loader"):
        instance = load_strategy_from_file(f)

    assert isinstance(instance, Strategy)

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_messages == [], (
        f"Expected no warnings for compatible strategy, got: {warning_messages}"
    )


def test_imported_strategy_subclass_is_filtered_out(tmp_path: Path) -> None:
    """A Strategy subclass imported from another module must not be counted."""
    f = tmp_path / "reimport.py"

    # Write a helper module that defines an importable Strategy subclass.
    helper = tmp_path / "helper_strategy.py"
    helper.write_text(
        textwrap.dedent(
            """
            from nautilus_trader.config import StrategyConfig
            from nautilus_trader.trading.strategy import Strategy


            class HelperStrategy(Strategy):
                def __init__(self) -> None:
                    super().__init__(config=StrategyConfig(strategy_id="helper-001"))
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    # The file under test imports HelperStrategy but defines its own LocalOnly.
    # Loader must pick only the locally-defined one.
    f.write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(tmp_path)!r})
            from nautilus_trader.config import StrategyConfig
            from nautilus_trader.trading.strategy import Strategy
            from helper_strategy import HelperStrategy  # noqa: F401


            class LocalOnly(Strategy):
                def __init__(self) -> None:
                    super().__init__(
                        config=StrategyConfig(strategy_id="local-001"),
                    )
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    instance = load_strategy_from_file(f)
    assert type(instance).__name__ == "LocalOnly"
