"""issue #42 Phase 7: live_session_cli の E2E lifecycle スモーク。

Phase 1 の ``test_live_session_cli.py`` は CLI 引数解析と event ハンドリング
の単体 pin に特化していた。本ファイルでは「CLI が ``LiveSession`` API を
通じて期待 event 順序（``EngineStarted`` → ``LiveStrategyReady`` →
``EngineStopped``）を完走できる」 attach mode / in-process mode の
**lifecycle 契約** を pin する。

issue #42 の Phase 7 セクション + 統一決定 #9 / R3-M3:

  - ``test_attach_mode_full_lifecycle`` — ``@pytest.mark.live_demo``
    (workflow_dispatch only, attach mode)
    既存ログイン済 engine（``FLOWSURFACE_ENGINE_TOKEN`` 経由）に attach し、
    ``--strategy examples/test_strategy_minute.py --instrument 1301.TSE
    --max-qty 100 --max-notional-jpy 500000 --venue tachibana --demo
    --mode attach`` 相当を ``LiveSession`` API でシミュレート。実 engine が
    無い CI 環境では skip する（``FLOWSURFACE_ENGINE_TOKEN`` 未設定）。

  - ``test_inprocess_mode_full_lifecycle`` — ``@pytest.mark.live_demo_inprocess``
    （ローカル限定、CI 除外）
    in-process mode で同じ lifecycle を回す。実環境の credential
    （``DEV_TACHIBANA_USER_ID`` / ``DEV_TACHIBANA_PASSWORD`` /
    ``DEV_TACHIBANA_SECOND_PASSWORD``）と立花 demo 環境を必要とするため、
    CI からは除外（``--second-password-stdin`` 必須化、統一決定 #7）。

両テストとも ``LiveSession`` API を呼ぶ薄ラッパーで、CLI argparse 経路は
Phase 1 ``test_live_session_cli.py`` 側で別途 pin 済み。本テストは
「実 engine に繋がる lifecycle が壊れていないか」を **接続が利用可能なとき
だけ** 観測する。
"""

from __future__ import annotations

import io
import os
from typing import Any

import pytest


# 期待 event 順序の最小集合。``EngineStarted`` → ``LiveStrategyReady`` →
# ``EngineStopped`` の出現順を pin する（``LiveStrategyWarmingUp`` などの
# 中間 event は順序非依存で挟まる）。
_EXPECTED_LIFECYCLE = ["EngineStarted", "LiveStrategyReady", "EngineStopped"]


def _expected_subsequence(actual: list[str], expected: list[str]) -> bool:
    """``actual`` が ``expected`` を **部分列** として含むかを返す。

    例: ``actual=["EngineStarted", "LiveStrategyWarmingUp", "LiveStrategyReady",
    "EngineStopped"]`` / ``expected=["EngineStarted", "LiveStrategyReady",
    "EngineStopped"]`` → ``True``。

    実 engine から流れる event 順序は中間 event（``LiveStrategyWarmingUp``）
    が挟まる可能性があるため、厳密一致ではなく部分列を使う。
    """
    j = 0
    for name in actual:
        if j < len(expected) and name == expected[j]:
            j += 1
    return j == len(expected)


@pytest.mark.live_demo
def test_attach_mode_full_lifecycle() -> None:
    """attach mode で実 engine に繋がる lifecycle を pin（CI workflow_dispatch のみ）。

    実 engine が無い CI 環境では ``FLOWSURFACE_ENGINE_TOKEN`` 未設定で skip。
    立花 demo 環境のログインは事前に GUI / ``replay_session.py`` 経由で完了
    している前提（attach mode は engine プロセスに **接続** するだけで
    ログイン操作は行わない）。

    期待 event 順序（部分列）: ``EngineStarted`` → ``LiveStrategyReady``
    → ``EngineStopped``。
    """
    if not os.environ.get("FLOWSURFACE_ENGINE_TOKEN"):
        pytest.skip(
            "FLOWSURFACE_ENGINE_TOKEN 未設定 — 実 engine attach lifecycle は "
            "workflow_dispatch（事前ログイン済 engine 前提）でのみ実行する。"
        )

    # late import: 実 engine への接続テストなので、import 失敗時に skip する
    # 経路を分離する。``LiveSession`` 自体は CLI 単体テスト（test_live_session_cli.py）
    # で fake 経路を pin しているので、ここでは実物を使う。
    from engine.replay_session import LiveSession

    repo_root = _resolve_repo_root()
    strategy_path = repo_root / "examples" / "test_strategy_minute.py"
    if not strategy_path.exists():
        pytest.skip(f"strategy file not found: {strategy_path}")

    events: list[dict[str, Any]] = []
    with LiveSession(venue="tachibana", demo=True, force_mode="attach") as sess:
        # attach mode: ログインは engine 側で完了済。``run()`` で
        # ``StartEngine`` を送り EngineStarted / LiveStrategyReady を待つ。
        try:
            sess.run(
                strategy_file=str(strategy_path),
                instrument_id="1301.TSE",
                max_qty=100,
                max_notional_jpy=500_000,
                strategy_init_kwargs=None,
                on_event=events.append,
            )
        finally:
            # 明示停止 → EngineStopped を観測する
            sess.stop()

    event_names = [e.get("event", "") for e in events]
    assert _expected_subsequence(event_names, _EXPECTED_LIFECYCLE), (
        f"attach mode lifecycle が期待順序を満たさない: actual={event_names!r} / "
        f"expected subsequence={_EXPECTED_LIFECYCLE!r}"
    )


@pytest.mark.live_demo_inprocess
def test_inprocess_mode_full_lifecycle() -> None:
    """in-process mode で実 engine の lifecycle を pin（ローカル限定、CI 除外）。

    必要 env:
      - ``DEV_TACHIBANA_USER_ID``
      - ``DEV_TACHIBANA_PASSWORD``
      - ``DEV_TACHIBANA_SECOND_PASSWORD``（``--second-password-stdin`` 経由想定。
        ローカル実行時は env でも可）

    CI からは除外: 統一決定 #7（第二暗証番号の OS 露出対策）に従い、CI Secrets
    を経由させない。``tachibana-demo.yml`` でも ``-m "demo_tachibana or live_demo"``
    のため ``live_demo_inprocess`` は実行されない。

    期待 event 順序（部分列）: ``EngineStarted`` → ``LiveStrategyReady``
    → ``EngineStopped``。
    """
    user_id = os.environ.get("DEV_TACHIBANA_USER_ID")
    password = os.environ.get("DEV_TACHIBANA_PASSWORD")
    second_password = os.environ.get("DEV_TACHIBANA_SECOND_PASSWORD")
    if not user_id or not password or not second_password:
        pytest.skip(
            "DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD / "
            "DEV_TACHIBANA_SECOND_PASSWORD 未設定 — in-process lifecycle は "
            "ローカル実 credential 環境でのみ実行する。"
        )

    from engine.replay_session import LiveSession

    repo_root = _resolve_repo_root()
    strategy_path = repo_root / "examples" / "test_strategy_minute.py"
    if not strategy_path.exists():
        pytest.skip(f"strategy file not found: {strategy_path}")

    events: list[dict[str, Any]] = []
    with LiveSession(
        venue="tachibana",
        demo=True,
        force_mode="inprocess",
        second_password=second_password,
    ) as sess:
        sess.login(user_id=user_id, password=password, venue="tachibana")
        try:
            sess.run(
                strategy_file=str(strategy_path),
                instrument_id="1301.TSE",
                max_qty=100,
                max_notional_jpy=500_000,
                strategy_init_kwargs=None,
                on_event=events.append,
            )
        finally:
            sess.stop()

    event_names = [e.get("event", "") for e in events]
    assert _expected_subsequence(event_names, _EXPECTED_LIFECYCLE), (
        f"in-process lifecycle が期待順序を満たさない: actual={event_names!r} / "
        f"expected subsequence={_EXPECTED_LIFECYCLE!r}"
    )


# ---------------------------------------------------------------------------
# 部分列ヘルパー自体の self-check（CI 既定で実行される、実 engine 不要）
# ---------------------------------------------------------------------------


def test_expected_subsequence_self_check() -> None:
    """``_expected_subsequence`` ヘルパーが部分列マッチを正しく扱うことを self-check。

    本テストはマーカー無し → CI 既定で実行される。``_expected_subsequence`` の
    挙動が壊れると上記 lifecycle テストが silent に PASS してしまうため、
    helper 自体を独立して pin する。
    """
    expected = ["EngineStarted", "LiveStrategyReady", "EngineStopped"]
    # 厳密一致
    assert _expected_subsequence(
        ["EngineStarted", "LiveStrategyReady", "EngineStopped"], expected
    )
    # 中間 event が挟まる
    assert _expected_subsequence(
        [
            "EngineStarted",
            "LiveStrategyWarmingUp",
            "LiveStrategyWarmingUp",
            "LiveStrategyReady",
            "LiveBuyingPower",
            "EngineStopped",
        ],
        expected,
    )
    # 順序逆転は False
    assert not _expected_subsequence(
        ["LiveStrategyReady", "EngineStarted", "EngineStopped"], expected
    )
    # 欠落は False
    assert not _expected_subsequence(["EngineStarted", "EngineStopped"], expected)
    # 空 actual は False（expected が非空）
    assert not _expected_subsequence([], expected)
    # 空 expected は True（vacuous）
    assert _expected_subsequence(["X"], [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_repo_root():
    """テストファイルから repo root への相対パスを解決する。"""
    from pathlib import Path

    return Path(__file__).resolve().parents[2]
