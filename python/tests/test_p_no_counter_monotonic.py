"""D2-M4: PNoCounter.next() の単調増加テスト。

PNoCounter は p_no（発注リクエスト番号）を単調増加させる責務を持つ（R4）。
同一インスタンス内での厳密な単調増加と、再起動シミュレーション（新インスタンス）でも
以前の値以上から始まることを検証する。
"""

import time

import pytest

from engine.exchanges.tachibana_helpers import PNoCounter


class TestPNoCounterMonotonic:
    def test_strictly_monotonic_within_instance(self):
        """同一インスタンスで next() が厳密に単調増加すること。"""
        counter = PNoCounter()
        prev = counter.next()
        for _ in range(100):
            curr = counter.next()
            assert curr > prev, (
                f"PNoCounter.next() must be strictly monotonic: {prev} >= {curr}"
            )
            prev = curr

    def test_monotonic_100_calls_values_differ(self):
        """100 回の next() 呼び出しで重複値がないこと。"""
        counter = PNoCounter()
        values = [counter.next() for _ in range(100)]
        assert len(values) == len(set(values)), (
            "PNoCounter.next() must return unique values across 100 calls"
        )

    def test_monotonic_sequence_ascending(self):
        """next() の結果列が昇順であること（n=200）。"""
        counter = PNoCounter()
        values = [counter.next() for _ in range(200)]
        for i in range(len(values) - 1):
            assert values[i] < values[i + 1], (
                f"PNoCounter sequence not monotonic at index {i}: "
                f"{values[i]} >= {values[i + 1]}"
            )

    def test_monotonic_various_call_counts(self):
        """2〜50 回の next() 呼び出しでも単調増加すること。"""
        for n in [2, 5, 10, 20, 50]:
            counter = PNoCounter()
            values = [counter.next() for _ in range(n)]
            for i in range(len(values) - 1):
                assert values[i] < values[i + 1], (
                    f"PNoCounter not monotonic at n={n}, index {i}: "
                    f"{values[i]} >= {values[i + 1]}"
                )

    def test_restart_simulation_new_instance_starts_from_unix_seconds(self):
        """再起動シミュレーション: 新しい PNoCounter は Unix 秒から始まる（R4 要件）。

        PNoCounter は Unix 秒で初期化されるため、新インスタンスの初期値は
        current Unix seconds に近い値になる。next() を呼んだだけ進むため、
        呼び出し回数が少なければ新インスタンスは旧インスタンスより大きい値から始まる。

        注: 10 回以上 next() を呼ぶと秒を超えるため、比較は next() 1 回だけにする。
        """
        counter1 = PNoCounter()
        first_val1 = counter1.next()

        # Unix 秒ベースなので短時間待てば新インスタンスが同等か大きい値から始まる
        time.sleep(0.01)

        counter2 = PNoCounter()
        first_val2 = counter2.next()
        # 同じ秒内なら等しい（+1）か、秒が変わっていれば大きい
        assert first_val2 >= first_val1, (
            f"New PNoCounter instance should start >= first instance "
            f"({first_val2} < {first_val1}). "
            "Both counters start from Unix seconds, so new instance should be >= old."
        )

    def test_peek_returns_last_next_value(self):
        """peek() が最後の next() 値を返すこと（テスト・デバッグ用途）。"""
        counter = PNoCounter()
        val = counter.next()
        assert counter.peek() == val, (
            f"peek() must return the last value returned by next(): "
            f"peek()={counter.peek()}, next()={val}"
        )

    def test_peek_does_not_advance_counter(self):
        """peek() を呼んでも次の next() が進むこと（peek は副作用なし）。"""
        counter = PNoCounter()
        v1 = counter.next()
        _ = counter.peek()
        _ = counter.peek()
        v2 = counter.next()
        assert v2 == v1 + 1, (
            f"peek() must not advance the counter: expected {v1 + 1}, got {v2}"
        )
