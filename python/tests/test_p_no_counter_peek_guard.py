"""B2-L2: PNoCounter.peek() がリクエスト経路（tachibana_*.py）から呼ばれないことを確認。

PNoCounter.peek() はテスト・デバッグログ用途のみを想定している（tachibana_helpers.py 参照）。
リクエスト経路で使うべきメソッドは .next() であり、.peek() を呼ぶと p_no が進まずに
同じ値が送信される危険がある。
"""

from pathlib import Path


def test_pno_counter_peek_not_used_in_tachibana_files():
    """tachibana_*.py ソースファイル内で .peek() が呼ばれていないことを確認。

    テスト・デバッグファイル（test_*.py）は対象外。
    コメントアウトされた行は除外する。
    """
    engine_dir = Path(__file__).parents[2] / "python" / "engine" / "exchanges"

    lines_with_peek = []
    for py_file in sorted(engine_dir.glob("tachibana_*.py")):
        content = py_file.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines()):
            stripped = line.strip()
            # コメントアウトされた行は除外
            if stripped.startswith("#"):
                continue
            if ".peek()" in line:
                lines_with_peek.append(
                    f"{py_file.name}:{i + 1}: {stripped}"
                )

    assert not lines_with_peek, (
        "PNoCounter.peek() must not be used in tachibana source files "
        "(use .next() only in request paths); found:\n"
        + "\n".join(lines_with_peek)
    )
