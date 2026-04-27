"""リグレッション: _apply_tachibana_session が server と worker 両方を更新する。

no_session バグ (2026-04-27): _apply_tachibana_session が server._tachibana_session
のみを更新し、worker._session を更新していなかった。これにより最初のメタデータ
フェッチ時に worker が no_session エラーを起こしていた。

本テストは修正が壊れないことを継続的に保証する。
"""

from __future__ import annotations

import pytest


def _make_fake_session():
    """テスト用最小 TachibanaSession を構築する。"""
    from engine.exchanges.tachibana_auth import TachibanaSession
    from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl

    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws="wss://example.test/event/",
        zyoutoeki_kazei_c="0",
    )


def test_apply_tachibana_session_syncs_worker_session(tmp_path):
    """リグレッション: _apply_tachibana_session が server と worker 両方を更新する。

    (no_session バグ 2026-04-27 の修正検証)
    """
    from engine.server import DataEngineServer

    server = DataEngineServer(port=0, token="test-token", cache_dir=tmp_path)

    # 初期状態: server._tachibana_session も worker._session も None
    assert server._tachibana_session is None
    assert server._workers["tachibana"]._session is None

    session = _make_fake_session()
    server._apply_tachibana_session(session)

    # server 側も worker 側も同じ session オブジェクトを保持している
    assert server._tachibana_session is session, (
        "_apply_tachibana_session must update server._tachibana_session"
    )
    assert server._workers["tachibana"]._session is session, (
        "_apply_tachibana_session must also update worker._session "
        "(no_session バグ 2026-04-27: worker が更新されず最初の API 呼び出しが失敗)"
    )
