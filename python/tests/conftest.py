"""pytest 共通設定。

`--smoke` フラグ未指定のとき `@pytest.mark.smoke` のテストを skip する。
smoke は実プロセス（Rust GUI / Python engine）起動を伴う結合テストであり、
CI の通常ジョブからは除外する（plan-test-mock-ipc-server.md S3）。
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--smoke",
        action="store_true",
        default=False,
        help="run @pytest.mark.smoke tests (実プロセス起動を伴う結合テスト)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--smoke"):
        return
    skip_smoke = pytest.mark.skip(reason="smoke test (use --smoke to run)")
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(skip_smoke)
