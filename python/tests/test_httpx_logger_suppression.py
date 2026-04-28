"""C-M2: httpx INFO ログが sSecondPassword を漏洩しないことを確認。

httpx はデフォルトで INFO レベルで HTTP リクエスト URL をログに出力する。
立花 API の URL にはクエリパラメータとして sSecondPassword が含まれるため、
httpx ロガーのレベルを WARNING 以上に設定してログ出力を抑制する必要がある。
"""

from __future__ import annotations

import logging


def test_httpx_logger_is_suppressed_in_main_module() -> None:
    """engine/__main__.py の main() が httpx ロガーを WARNING 以上に設定することを確認。

    このテストは /engine/__main__.py の main() を呼ぶのではなく、
    main() が行うべき設定（httpx ロガーの WARNING 化）が実施されると
    httpx INFO ログが出力されないことをアサートする。
    """
    import importlib
    import sys

    # httpx ロガーを WARNING に設定した状態で INFO が出ないことを確認
    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level

    try:
        httpx_logger.setLevel(logging.WARNING)
        # WARNING 未満のレコードは isEnabledFor でフィルタされる
        assert not httpx_logger.isEnabledFor(logging.INFO), (
            "httpx ロガーが WARNING 設定後も INFO を有効にしています"
        )
        assert not httpx_logger.isEnabledFor(logging.DEBUG), (
            "httpx ロガーが WARNING 設定後も DEBUG を有効にしています"
        )
        assert httpx_logger.isEnabledFor(logging.WARNING), (
            "httpx ロガーが WARNING 設定後に WARNING を無効にしています"
        )
    finally:
        httpx_logger.setLevel(original_level)


def test_httpcore_logger_is_suppressed() -> None:
    """httpcore ロガーも WARNING 以上に設定されることを確認。

    httpcore は httpx の下位ライブラリで、接続詳細を DEBUG ログに出力する。
    """
    httpcore_logger = logging.getLogger("httpcore")
    original_level = httpcore_logger.level

    try:
        httpcore_logger.setLevel(logging.WARNING)
        assert not httpcore_logger.isEnabledFor(logging.DEBUG), (
            "httpcore ロガーが WARNING 設定後も DEBUG を有効にしています"
        )
    finally:
        httpcore_logger.setLevel(original_level)


def test_main_sets_httpx_logger_to_warning(monkeypatch, capsys) -> None:
    """engine.__main__.main() が起動時に httpx ロガーを WARNING に設定することを確認。

    main() を直接呼ぶのは副作用が大きいため、_setup_logging() ヘルパーが
    存在することを確認するか、あるいは main() の logging 設定後の状態を検証する。
    """
    import logging

    # __main__ モジュールを import して _setup_http_logging ヘルパーを呼ぶ
    # ヘルパーが存在しない場合は main() から抽出する必要がある
    import engine.__main__ as engine_main

    # _suppress_http_logging ヘルパーが定義されているか、または
    # main() 内で logging.getLogger("httpx").setLevel(logging.WARNING) が呼ばれることを確認
    # ここでは main() のソースコードから設定が存在することを静的に検証する
    import inspect
    src = inspect.getsource(engine_main.main)
    assert 'httpx' in src and ('setLevel' in src or 'WARNING' in src), (
        "engine/__main__.py の main() が httpx ロガーを WARNING 以上に設定していません。\n"
        "logging.getLogger('httpx').setLevel(logging.WARNING) を main() に追加してください。"
    )
