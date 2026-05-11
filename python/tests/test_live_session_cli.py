"""issue #42 Phase 1: ``python -m engine.live_session_cli run`` CLI のテスト。

統一決定 #1 #5 #7 #8 #13 #16 を満たすことを検証する。

検証項目（RED → GREEN）:

- ``--max-qty`` 不足で argparse reject（受け入れ基準 #6）
- ``--prod`` + ``TACHIBANA_ALLOW_PROD`` 未設定で argparse reject（#7）
- ``--second-password`` 平文渡しで stderr 警告（OS 露出対策、#7 統一決定 #7）
- ``--second-password-stdin`` の 4 経路（heredoc / pipe / empty / 非対話、#20）
- ``SecondPasswordRequired`` 受信で stderr に固定文言 + exit != 0（#8）
- ``EngineBusy{busy_kind:another_strategy_on_venue}`` 受信で stderr + exit != 0（#16）
- ``Error{code:engine_already_running}`` 受信で stderr + exit != 0（#16）
- ``EngineError{code:market_closed}`` 受信で stderr + exit != 0（#9）
- 不正 JSON ``--strategy-init-kwargs`` で reject
- ``--max-qty 0`` / 負値 / overflow で reject

CLI 経路は subprocess を立てずに直接 ``main(argv, stdin)`` を import して呼ぶことで
テスト時間を短縮する。標準出力 / 標準エラーは ``capsys`` で捕捉する。

attach mode の **実 engine 接続テスト**は ``pytest -m live_demo`` マーカーで
分離する（CI workflow_dispatch のみ）。本ファイルでは LiveSession を monkeypatch して
mock 経路だけ pin する。
"""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _common_args(strategy_path: str) -> list[str]:
    """正常系の最小引数。テストで一部を override して使う。"""
    return [
        "run",
        "--strategy", strategy_path,
        "--instrument", "8306.T",
        "--max-qty", "100",
        "--max-notional-jpy", "500000",
        "--mode", "attach",
        "--demo",
    ]


@pytest.fixture
def strategy_file(tmp_path):
    """空の戦略ファイル（CLI は parse のみで実行はしない）。"""
    p = tmp_path / "noop_strategy.py"
    p.write_text("# empty strategy\n", encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """credential / prod env をテスト毎にリセットする。"""
    for key in [
        "TACHIBANA_ALLOW_PROD",
        "KABU_ALLOW_PROD",
        "KABU_ENV",
        "DEV_TACHIBANA_USER_ID",
        "DEV_TACHIBANA_PASSWORD",
        "DEV_TACHIBANA_SECOND_PASSWORD",
        "FLOWSURFACE_ENGINE_TOKEN",
    ]:
        monkeypatch.delenv(key, raising=False)
    # in-process でも attach でも自動 attach しないよう session file を無効化
    monkeypatch.setattr(
        "engine.replay_session._read_session_file", lambda: None, raising=False
    )


class _FakeLiveSession:
    """LiveSession のドロップイン代替。CLI 経路の振る舞いだけ pin する。

    ``run`` に渡された ``on_event`` を保存しておき、テストごとに事前設定した
    ``injected_events`` を順に流す。``stop`` は冪等性をテストするためにカウントを取る。
    """

    last_instance: "_FakeLiveSession | None" = None

    def __init__(self, *, venue, demo, force_mode, second_password=None, **_kw) -> None:
        self.venue = venue
        self.demo = demo
        self.force_mode = force_mode
        self.second_password = second_password
        self.login_called_with: dict | None = None
        self.run_called_with: dict | None = None
        self.stop_call_count: int = 0
        # テストが事前にセットする
        self.injected_events: list[dict] = []
        type(self).last_instance = self

    # context manager
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    # public API mirrors LiveSession
    def login(self, *, user_id=None, password=None, venue=None) -> None:
        self.login_called_with = {
            "user_id": user_id, "password": password, "venue": venue,
        }

    def run(self, *, on_event=None, **kwargs) -> None:
        self.run_called_with = kwargs
        if on_event is not None:
            for evt in self.injected_events:
                on_event(evt)

    def stop(self) -> None:
        self.stop_call_count += 1


@pytest.fixture
def fake_live_session(monkeypatch):
    """live_session_cli が import する LiveSession を _FakeLiveSession に差し替える。"""
    monkeypatch.setattr(
        "engine.live_session_cli.LiveSession", _FakeLiveSession,
    )
    yield _FakeLiveSession
    _FakeLiveSession.last_instance = None


# ---------------------------------------------------------------------------
# 正常系（attach mode）— happy path で exit 0 を返す
# ---------------------------------------------------------------------------


@pytest.mark.live_demo
def test_attach_starts_engine_for_replay_strategy_unchanged(
    fake_live_session, strategy_file, monkeypatch,
) -> None:
    """attach mode で正常起動 → EngineStarted + EngineStopped を on_event で受け、exit 0。

    `pytest -m live_demo` で attach mode の smoke テスト（CI workflow_dispatch のみ）。
    """
    from engine.live_session_cli import main

    fake_live_session.last_instance = None  # reset
    # 事前に LiveSession インスタンスを作る前に injected_events をセットできないので、
    # __init__ をフックして即セットする戦略をとる。代わりに login 後 → run の per-instance
    # injection 経路を用意する: 事前インスタンスを作る前にクラス属性として置く。

    # 戦略: __init__ 時にインスタンスを capture し inject する factory wrap
    captured: list[_FakeLiveSession] = []

    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "EngineStarted", "strategy_id": "live-strategy",
             "account_id": "tachibana", "ts_event_ms": 0},
            {"event": "LiveStrategyReady", "strategy_id": "live-strategy",
             "venue": "tachibana", "instrument_id": "8306.T", "ts_event_ms": 0},
            {"event": "EngineStopped", "strategy_id": "live-strategy",
             "final_equity": "0", "ts_event_ms": 0},
        ]
        captured.append(self)

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    rc = main(argv=_common_args(strategy_file), stdin=io.StringIO(""))
    assert rc == 0, f"expected exit 0, got {rc}"
    assert captured, "LiveSession was not constructed"
    inst = captured[0]
    assert inst.force_mode == "attach"
    assert inst.run_called_with is not None
    assert inst.run_called_with["instrument_id"] == "8306.T"
    assert inst.run_called_with["max_qty"] == 100
    assert inst.run_called_with["max_notional_jpy"] == 500_000


@pytest.mark.live_demo_inprocess
def test_inprocess_starts_engine_for_replay_strategy_unchanged(
    fake_live_session, strategy_file, monkeypatch,
) -> None:
    """in-process mode で正常起動 → second_password が LiveSession に伝わり exit 0。"""
    from engine.live_session_cli import main

    captured: list[_FakeLiveSession] = []
    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "EngineStarted", "strategy_id": "live-strategy",
             "account_id": "tachibana", "ts_event_ms": 0},
            {"event": "EngineStopped", "strategy_id": "live-strategy",
             "final_equity": "0", "ts_event_ms": 0},
        ]
        captured.append(self)

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)
    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", "test-user")
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", "test-pw")
    monkeypatch.setenv("DEV_TACHIBANA_SECOND_PASSWORD", "test-second-pw")

    args = [
        "run",
        "--strategy", strategy_file,
        "--instrument", "8306.T",
        "--max-qty", "100",
        "--max-notional-jpy", "500000",
        "--mode", "inprocess",
        "--demo",
    ]
    rc = main(argv=args, stdin=io.StringIO(""))
    assert rc == 0, f"expected exit 0, got {rc}"
    assert captured
    inst = captured[0]
    assert inst.force_mode == "inprocess"
    assert inst.second_password == "test-second-pw"


# ---------------------------------------------------------------------------
# argparse-level rejects（受け入れ基準 #6 / #7 / boundary）
# ---------------------------------------------------------------------------


def test_invalid_config_when_max_qty_missing(strategy_file) -> None:
    """``--max-qty`` 省略 → argparse SystemExit (LIVE_SCENARIO 不在前提では必須)."""
    from engine.live_session_cli import main

    args = [
        "run",
        "--strategy", strategy_file,
        "--instrument", "8306.T",
        # --max-qty intentionally missing
        "--max-notional-jpy", "500000",
        "--mode", "attach",
        "--demo",
    ]
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    # argparse は usage error で exit code 2
    assert excinfo.value.code != 0


def test_max_qty_zero_or_negative_reject(strategy_file) -> None:
    """``--max-qty 0`` / 負値 → argparse reject（範囲: 1 ≤ n ≤ 10000）."""
    from engine.live_session_cli import main

    for bad in ["0", "-1"]:
        args = _common_args(strategy_file)
        # replace --max-qty value
        idx = args.index("--max-qty")
        args[idx + 1] = bad
        with pytest.raises(SystemExit) as excinfo:
            main(argv=args, stdin=io.StringIO(""))
        assert excinfo.value.code != 0, f"--max-qty {bad} must be rejected"


def test_max_notional_overflow_reject(strategy_file) -> None:
    """``--max-notional-jpy`` 上限超え → argparse reject（範囲: 1 ≤ n ≤ 100_000_000）."""
    from engine.live_session_cli import main

    args = _common_args(strategy_file)
    idx = args.index("--max-notional-jpy")
    args[idx + 1] = "999999999999"  # 100億超
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    assert excinfo.value.code != 0


def test_venue_choices_accept_tachibana_and_kabu_station(strategy_file) -> None:
    """``--venue`` choices は tachibana / kabu_station の両方を受理する.

    issue #42 Phase 4 で kabu_station capability flip 済 + engine_runner.start_live が
    venue 引数で dispatch するため、CLI argparse も両 venue を受理する必要がある。
    Phase 5 で choices を拡張した（README §C の kabu_station 例を成立させる）。
    """
    from engine.live_session_cli import _build_arg_parser

    parser = _build_arg_parser()
    # --venue tachibana
    a = parser.parse_args(_common_args(strategy_file) + ["--venue", "tachibana"])
    assert a.venue == "tachibana"
    # --venue kabu_station
    b = parser.parse_args(_common_args(strategy_file) + ["--venue", "kabu_station"])
    assert b.venue == "kabu_station"


def test_venue_choices_reject_unknown(strategy_file) -> None:
    """``--venue`` choices に無い値は argparse SystemExit で reject される."""
    from engine.live_session_cli import main

    args = _common_args(strategy_file) + ["--venue", "binance"]
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    assert excinfo.value.code != 0, "unknown venue must be rejected"


def test_strategy_init_kwargs_parse_error_reject(strategy_file) -> None:
    """``--strategy-init-kwargs`` 不正 JSON → argparse reject."""
    from engine.live_session_cli import main

    args = _common_args(strategy_file) + ["--strategy-init-kwargs", "{not json"]
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    assert excinfo.value.code != 0


def test_prod_blocked_without_env(strategy_file) -> None:
    """``--prod`` + ``TACHIBANA_ALLOW_PROD`` 未設定 → reject."""
    from engine.live_session_cli import main

    args = _common_args(strategy_file)
    # demo を prod に置換
    idx = args.index("--demo")
    args[idx] = "--prod"
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    assert excinfo.value.code != 0


def test_prod_blocked_for_tachibana_without_env(strategy_file) -> None:
    """R1 HIGH-2: ``--venue tachibana --prod`` + env 未設定 → reject (venue 明示版).

    ``test_prod_blocked_without_env`` の venue 指定明示版。venue 別分岐後でも
    tachibana 経路の既存契約 (``TACHIBANA_ALLOW_PROD=1`` 必須) を pin する。
    """
    from engine.live_session_cli import main

    args = _common_args(strategy_file)
    idx = args.index("--demo")
    args[idx] = "--prod"
    args += ["--venue", "tachibana"]
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    assert excinfo.value.code != 0


def test_prod_for_tachibana_does_not_check_kabu_env(
    fake_live_session, strategy_file, monkeypatch,
) -> None:
    """R1 HIGH-2: ``--venue tachibana --prod`` + ``TACHIBANA_ALLOW_PROD=1`` のみ → 受理.

    ``KABU_ALLOW_PROD`` / ``KABU_ENV`` が未設定でも tachibana 経路は影響を受けない
    (venue 分離の対称性を pin)。
    """
    from engine.live_session_cli import main

    monkeypatch.setenv("TACHIBANA_ALLOW_PROD", "1")
    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
    monkeypatch.delenv("KABU_ENV", raising=False)

    captured: list[_FakeLiveSession] = []
    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "EngineStopped", "strategy_id": "live-strategy",
             "final_equity": "0", "ts_event_ms": 0},
        ]
        captured.append(self)

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    args = _common_args(strategy_file)
    idx = args.index("--demo")
    args[idx] = "--prod"
    args += ["--venue", "tachibana"]
    rc = main(argv=args, stdin=io.StringIO(""))
    assert rc == 0, (
        f"tachibana prod path must NOT check KABU envs; got rc={rc}"
    )
    assert captured
    assert captured[0].venue == "tachibana"
    assert captured[0].demo is False


def test_prod_blocked_for_kabu_without_env(strategy_file, capsys) -> None:
    """R1 HIGH-2 / R3 M7: ``--venue kabu_station --prod`` + env 未設定 → reject.

    venue 別分岐後、kabu_station 経路は ``KABU_ALLOW_PROD=1`` + ``KABU_ENV=prod``
    の二重判定を要求する (``resolve_kabu_env() == "prod"``)。env 未設定では
    argparse error 経由で SystemExit する。
    R3 M7: error 文言が必要 env 変数 (KABU_ALLOW_PROD / KABU_ENV) を **両方**
    含めて、運用者がどちらか片方だけ立てる typo を防ぐ。
    """
    from engine.live_session_cli import main

    args = _common_args(strategy_file)
    idx = args.index("--demo")
    args[idx] = "--prod"
    args += ["--venue", "kabu_station"]
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    assert excinfo.value.code != 0
    # R3 M7: error 文言は両 env 変数を含めるべき。
    captured = capsys.readouterr()
    err_text = captured.err + captured.out
    assert "KABU_ALLOW_PROD" in err_text, (
        f"error must mention KABU_ALLOW_PROD (R3 M7); got: {err_text!r}"
    )
    assert "KABU_ENV" in err_text, (
        f"error must mention KABU_ENV (R3 M7); got: {err_text!r}"
    )


def test_prod_blocked_for_kabu_with_only_one_env(strategy_file, monkeypatch) -> None:
    """R1 HIGH-2: ``--venue kabu_station --prod`` + 片方 env のみ → reject.

    ``KABU_ALLOW_PROD=1`` を立てただけ (``KABU_ENV`` 欠落) では
    ``resolve_kabu_env()`` は ``"verify"`` を返すため reject されること。
    """
    from engine.live_session_cli import main

    monkeypatch.setenv("KABU_ALLOW_PROD", "1")
    monkeypatch.delenv("KABU_ENV", raising=False)

    args = _common_args(strategy_file)
    idx = args.index("--demo")
    args[idx] = "--prod"
    args += ["--venue", "kabu_station"]
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    assert excinfo.value.code != 0


def test_prod_blocked_for_kabu_with_only_kabu_env(strategy_file, monkeypatch) -> None:
    """R1 HIGH-2: ``--venue kabu_station --prod`` + ``KABU_ENV=prod`` のみ → reject.

    ``KABU_ENV=prod`` だけでは ``KABU_ALLOW_PROD=1`` が無いので
    ``resolve_kabu_env() == "verify"`` となり argparse error。
    """
    from engine.live_session_cli import main

    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
    monkeypatch.setenv("KABU_ENV", "prod")

    args = _common_args(strategy_file)
    idx = args.index("--demo")
    args[idx] = "--prod"
    args += ["--venue", "kabu_station"]
    with pytest.raises(SystemExit) as excinfo:
        main(argv=args, stdin=io.StringIO(""))
    assert excinfo.value.code != 0


def test_prod_accepted_for_kabu_with_env(
    fake_live_session, strategy_file, monkeypatch,
) -> None:
    """R1 HIGH-2: ``--venue kabu_station --prod`` + 両 env 揃い → 受理.

    ``KABU_ALLOW_PROD=1`` + ``KABU_ENV=prod`` 揃えば
    ``resolve_kabu_env() == "prod"`` となり argparse prod guard を通過する。
    実際の login attempt は別経路 (LiveSession 側) で行われるため、
    本テストは argparse 経路の OK パスのみを pin する。
    """
    from engine.live_session_cli import main

    monkeypatch.setenv("KABU_ALLOW_PROD", "1")
    monkeypatch.setenv("KABU_ENV", "prod")
    # tachibana 系 env は未設定でも kabu 経路では影響を受けない (分離契約)
    monkeypatch.delenv("TACHIBANA_ALLOW_PROD", raising=False)

    captured: list[_FakeLiveSession] = []
    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "EngineStopped", "strategy_id": "live-strategy",
             "final_equity": "0", "ts_event_ms": 0},
        ]
        captured.append(self)

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    args = _common_args(strategy_file)
    idx = args.index("--demo")
    args[idx] = "--prod"
    args += ["--venue", "kabu_station"]
    rc = main(argv=args, stdin=io.StringIO(""))
    assert rc == 0, (
        f"kabu prod path must accept when both envs set; got rc={rc}"
    )
    assert captured
    assert captured[0].venue == "kabu_station"
    assert captured[0].demo is False


# ---------------------------------------------------------------------------
# 第二暗証番号の OS 露出対策（統一決定 #7）
# ---------------------------------------------------------------------------


def test_second_password_warns_on_plain(
    fake_live_session, strategy_file, monkeypatch, capsys,
) -> None:
    """``--second-password`` 平文渡し → stderr に警告（in-process）."""
    from engine.live_session_cli import main

    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", "u")
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", "p")
    args = [
        "run",
        "--strategy", strategy_file,
        "--instrument", "8306.T",
        "--max-qty", "100",
        "--max-notional-jpy", "500000",
        "--mode", "inprocess",
        "--demo",
        "--second-password", "leaked-second-pw",
    ]
    main(argv=args, stdin=io.StringIO(""))
    captured = capsys.readouterr()
    # warning 表現は柔軟だが「非推奨」「deprecated」「警告」「OS」「shell」のいずれかを含む
    err = captured.err.lower()
    assert any(
        kw in err
        for kw in ("非推奨", "deprecated", "警告", "warning", "shell", "history", "ps")
    ), f"expected deprecation warning in stderr, got: {captured.err!r}"


def test_second_password_stdin_handles_heredoc_pipe_empty_and_noninteractive(
    fake_live_session, strategy_file, monkeypatch,
) -> None:
    """``--second-password-stdin`` の 4 経路（受け入れ基準 #20）.

    1. heredoc 風（trailing newline 付き）→ rstrip("\\r\\n") で除去
    2. pipe（trailing newline なし）→ そのまま
    3. empty stdin + 非対話（CI）→ reject
    4. 対話（isatty）+ 値あり → 受理
    """
    from engine.live_session_cli import main

    captured: list[_FakeLiveSession] = []
    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "EngineStopped", "strategy_id": "live-strategy",
             "final_equity": "0", "ts_event_ms": 0},
        ]
        captured.append(self)

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)
    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", "u")
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", "p")

    base_args = [
        "run",
        "--strategy", strategy_file,
        "--instrument", "8306.T",
        "--max-qty", "100",
        "--max-notional-jpy", "500000",
        "--mode", "inprocess",
        "--demo",
        "--second-password-stdin",
    ]

    # ケース1: heredoc 風（trailing newline）
    captured.clear()

    class _NonInteractiveStdin(io.StringIO):
        def isatty(self) -> bool:
            return False

    rc1 = main(argv=base_args, stdin=_NonInteractiveStdin("heredoc-pw\n"))
    assert rc1 == 0
    assert captured[0].second_password == "heredoc-pw", (
        f"heredoc trailing newline must be stripped, got {captured[0].second_password!r}"
    )

    # ケース2: pipe（trailing newline なし）
    captured.clear()
    rc2 = main(argv=base_args, stdin=_NonInteractiveStdin("pipe-pw"))
    assert rc2 == 0
    assert captured[0].second_password == "pipe-pw"

    # ケース3: empty stdin + 非対話 → reject（SystemExit または rc != 0）。
    # argparse.error 経由で SystemExit(2) を投げる経路と、main() が rc != 0 を
    # return する経路の **どちらでも** 受理する（empty stdin が CI で silent に
    # 通らないことが本質的 invariant）。
    captured.clear()
    try:
        rc3 = main(argv=base_args, stdin=_NonInteractiveStdin(""))
        assert rc3 != 0, "empty stdin in non-interactive context must be rejected"
    except SystemExit as exc:
        assert exc.code != 0, (
            f"empty stdin should exit non-zero, got SystemExit({exc.code})"
        )

    # ケース4: 対話 + 値あり → 受理
    captured.clear()

    class _InteractiveStdin(io.StringIO):
        def isatty(self) -> bool:
            return True

    rc4 = main(argv=base_args, stdin=_InteractiveStdin("interactive-pw\n"))
    assert rc4 == 0
    assert captured[0].second_password == "interactive-pw"


def test_auto_mode_without_second_password_does_not_error_at_cli(
    fake_live_session, strategy_file, monkeypatch,
) -> None:
    """R8 MEDIUM-1: ``--mode auto`` で second_password が未設定でも CLI で error しない。

    旧実装は ``_resolve_second_password(..., mode=args.mode if != "auto" else "inprocess")``
    で auto を inprocess 扱いし、credential が無いと argparse error で停止していた。
    結果として ``--mode auto`` で既存 engine に attach するワークフローが実質使えなかった。

    本テストは auto モードで credential 未設定 → CLI が SystemExit せず、
    ``LiveSession(force_mode="auto", second_password=None)`` を構築することを pin する。
    実 attach probe の結果（attach 成功 / inprocess fallback）は LiveSession 側の責務。
    """
    from engine.live_session_cli import main

    captured: list[_FakeLiveSession] = []
    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "EngineStopped", "strategy_id": "live-strategy",
             "final_equity": "0", "ts_event_ms": 0},
        ]
        captured.append(self)

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    # _common_args は ``--mode attach`` を含むので auto に差し替える。
    args = [a if a != "attach" else "auto" for a in _common_args(strategy_file)]
    # second_password 系の env / args は一切設定しない。
    rc = main(argv=args, stdin=io.StringIO(""))

    assert rc == 0, (
        f"auto mode without second_password must NOT exit non-zero at CLI, got rc={rc}"
    )
    assert captured, "LiveSession was not constructed"
    assert captured[0].force_mode == "auto"
    assert captured[0].second_password is None, (
        f"auto mode must defer second_password resolution to LiveSession; "
        f"CLI passed {captured[0].second_password!r}"
    )


def test_attach_mode_does_not_accept_second_password(
    fake_live_session, strategy_file, monkeypatch,
) -> None:
    """attach mode では第二暗証番号を wire に流さない（``LiveSession`` に渡さない）."""
    from engine.live_session_cli import main

    captured: list[_FakeLiveSession] = []
    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "EngineStopped", "strategy_id": "live-strategy",
             "final_equity": "0", "ts_event_ms": 0},
        ]
        captured.append(self)

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)
    monkeypatch.setenv("DEV_TACHIBANA_SECOND_PASSWORD", "should-not-be-passed")

    args = _common_args(strategy_file)  # attach mode
    rc = main(argv=args, stdin=io.StringIO(""))
    assert rc == 0
    # attach mode では second_password=None で LiveSession を作る
    assert captured[0].second_password is None, (
        f"attach mode must NOT pass second_password to LiveSession (was {captured[0].second_password!r})"
    )


# ---------------------------------------------------------------------------
# Engine からの reject 経路（exit code != 0 + stderr 文言）
# ---------------------------------------------------------------------------


def test_attach_second_password_required_exits_nonzero(
    fake_live_session, strategy_file, monkeypatch, capsys,
) -> None:
    """``SecondPasswordRequired`` 受信 → stderr に固定文言「第二暗証番号を設定してください」+ exit != 0."""
    from engine.live_session_cli import main

    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "SecondPasswordRequired"},
        ]

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    rc = main(argv=_common_args(strategy_file), stdin=io.StringIO(""))
    assert rc != 0, "SecondPasswordRequired must result in non-zero exit"
    err = capsys.readouterr().err
    assert "第二暗証番号を設定してください" in err, (
        f"expected fixed Japanese message in stderr, got: {err!r}"
    )


def test_concurrent_live_emits_engine_busy_for_venue(
    fake_live_session, strategy_file, monkeypatch, capsys,
) -> None:
    """``EngineBusy{busy_kind:another_strategy_on_venue}`` 受信 → stderr + exit != 0."""
    from engine.live_session_cli import main

    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {
                "event": "EngineBusy",
                "current_state": "TRADING",
                "attempted_command": "StartEngine",
                "reason": "another live strategy is already running on venue 'tachibana'",
                "venue": "tachibana",
                "busy_kind": "another_strategy_on_venue",
                "request_id": "req-x",
            },
        ]

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    rc = main(argv=_common_args(strategy_file), stdin=io.StringIO(""))
    assert rc != 0, "EngineBusy(another_strategy_on_venue) must exit non-zero"
    err = capsys.readouterr().err
    # reason / busy_kind / venue のいずれかが stderr に表示されている
    assert "another_strategy_on_venue" in err or "tachibana" in err or "EngineBusy" in err, (
        f"expected EngineBusy details in stderr, got: {err!r}"
    )


def test_duplicate_strategy_id_emits_engine_already_running(
    fake_live_session, strategy_file, monkeypatch, capsys,
) -> None:
    """``Error{code:engine_already_running}`` 受信 → stderr + exit != 0."""
    from engine.live_session_cli import main

    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {
                "event": "Error",
                "code": "engine_already_running",
                "message": "engine for strategy_id='dup' is already running",
                "request_id": "req-dup",
            },
        ]

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    rc = main(argv=_common_args(strategy_file), stdin=io.StringIO(""))
    assert rc != 0
    err = capsys.readouterr().err
    assert "engine_already_running" in err or "already running" in err, (
        f"expected duplicate-strategy reason in stderr, got: {err!r}"
    )


def test_market_closed_exits_nonzero(
    fake_live_session, strategy_file, monkeypatch, capsys,
) -> None:
    """``EngineError{code:market_closed}`` 受信 → stderr + exit != 0."""
    from engine.live_session_cli import main

    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {
                "event": "EngineError",
                "code": "market_closed",
                "message": "market is closed; live strategy cannot start.",
                "strategy_id": "live-strategy",
            },
        ]

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    rc = main(argv=_common_args(strategy_file), stdin=io.StringIO(""))
    assert rc != 0
    err = capsys.readouterr().err
    assert "market_closed" in err or "market is closed" in err.lower(), (
        f"expected market_closed reason in stderr, got: {err!r}"
    )


# ---------------------------------------------------------------------------
# SIGINT idempotent stop（受け入れ基準 boundary）
# ---------------------------------------------------------------------------


def test_sigint_stop_idempotent(
    fake_live_session, strategy_file, monkeypatch,
) -> None:
    """``LiveSession.stop()`` が複数回呼ばれても冪等であることを CLI 経路で確認する。

    本テストは LiveSession.stop の冪等性自体は LiveSession 側の責務だが、
    CLI が stop を呼んでも例外で落ちないことを確認する smoke。
    """
    from engine.live_session_cli import main

    instances: list[_FakeLiveSession] = []
    orig_init = _FakeLiveSession.__init__

    def _wrap_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.injected_events = [
            {"event": "EngineStopped", "strategy_id": "live-strategy",
             "final_equity": "0", "ts_event_ms": 0},
        ]
        instances.append(self)

    monkeypatch.setattr(_FakeLiveSession, "__init__", _wrap_init)

    rc = main(argv=_common_args(strategy_file), stdin=io.StringIO(""))
    assert rc == 0
    # CLI が cleanup で stop を呼んだとして、複数呼出しも安全であること
    inst = instances[0]
    inst.stop()
    inst.stop()
    assert inst.stop_call_count >= 2, "stop() should be callable multiple times safely"
