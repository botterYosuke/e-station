"""リグレッション: server と TachibanaWorker が同一の PNoCounter インスタンスを共有する。

p_no collision バグ (2026-04-27): server._tachibana_p_no_counter と
TachibanaWorker._p_no_counter が独立した PNoCounter として構築されており、
両者が同じ Unix 秒で初期化されると同一の p_no 値列を返し、立花 API が
error 6 (`引数（p_no:[X] <= 前要求p_no:[X]）エラー`) で拒否していた。

`int(time.time())` の解像度依存なので値比較では検出できない（同秒で構築すれば
両カウンターは同じ値を返してしまう）。**同一インスタンスであること** (`is`)
を検証することが本テストの肝。

本テストは `p_no_counter=self._tachibana_p_no_counter` 引数の削除を
サイレントなリグレッションから守る。
"""

from __future__ import annotations


def test_server_and_tachibana_worker_share_same_p_no_counter(tmp_path):
    """server._tachibana_p_no_counter と worker._p_no_counter は同一インスタンス。

    値比較ではなく `is` 比較で検証する。両カウンターを独立構築しても
    同じ Unix 秒で初期化されれば値は揃ってしまうため、値比較では
    リグレッションを検出できない。
    """
    from engine.server import DataEngineServer

    server = DataEngineServer(port=0, token="test-token", cache_dir=tmp_path)

    worker = server._workers["tachibana"]

    assert worker._p_no_counter is server._tachibana_p_no_counter, (
        "TachibanaWorker._p_no_counter must be the SAME instance as "
        "server._tachibana_p_no_counter — independent counters initialized "
        "in the same Unix second emit identical p_no sequences and trigger "
        "立花 API error 6 (`p_no <= 前要求p_no`).\n"
        "Fix: pass `p_no_counter=self._tachibana_p_no_counter` when "
        "constructing TachibanaWorker in DataEngineServer.__init__."
    )


def test_shared_counter_produces_monotonic_p_no_across_server_and_worker(tmp_path):
    """共有カウンターを介した次値が単調増加する（衝突しない）。

    server 側 (validate_session_on_startup 相当) と worker 側
    (_ensure_master_loaded / fetch_klines 相当) の next() を交互に呼び、
    重複が発生しないことを確認する。
    """
    from engine.server import DataEngineServer

    server = DataEngineServer(port=0, token="test-token", cache_dir=tmp_path)
    worker = server._workers["tachibana"]

    seen: list[int] = []
    for _ in range(5):
        seen.append(server._tachibana_p_no_counter.next())
        seen.append(worker._p_no_counter.next())

    assert len(seen) == len(set(seen)), (
        f"p_no values collided: {seen}. "
        "server と worker のカウンターが共有されていない可能性がある。"
    )
    assert seen == sorted(seen), (
        f"p_no values are not monotonically increasing: {seen}"
    )
