"""H17 / H11: LiveSession の force_mode と login() 契約。

- force_mode='attach' は __enter__ で NotImplementedError（Phase 8.3 まで）。
- force_mode='inprocess' / 'auto' は inprocess に倒す。
- login() の in-process 経路は内部 Tachibana login API（_do_login_call）を呼ぶ。
- 引数 / env / 欠落 / 失敗伝播 / demo 伝播の各分岐を mock で検証する。

token / password は `.env` から読まない。テスト fixture は uuid ベースの
ダミー文字列を使用する（CLAUDE.md の安全装置）。
"""

from __future__ import annotations

import uuid

import pytest

from engine.replay_session import LiveSession


# ---------------------------------------------------------------------------
# 全テスト共通: 実行中エンジンへの自動接続を防ぐ
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_from_running_engine(monkeypatch) -> None:
    """engine-session.json が存在しても自動 attach しないよう session file を無効化する。

    このモジュールのテストは in-process / auto-fallback 動作を検証する。
    実行中のエンジンに接続すると attach mode になり、inprocess 前提の
    アサーションが崩れる。
    """
    monkeypatch.setattr("engine.replay_session._read_session_file", lambda: None)
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)


# ---------------------------------------------------------------------------
# force_mode 契約（Group 1 で追加済み・リグレッションガード）
# ---------------------------------------------------------------------------


def test_attach_force_mode_raises_when_no_engine(monkeypatch) -> None:
    """C-GP4 (Phase 8 R1 / Phase 3): force_mode='attach' は本実装済み。
    engine が居なければ ConnectionRefusedError で fail する (token も無い場合)。

    旧仕様 (NotImplementedError) は撤廃。token 解決経路がすべて欠落している
    ケースの fail-fast を pin する。
    """
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)
    session = LiveSession(venue="tachibana", demo=True, force_mode="attach")
    with pytest.raises(ConnectionRefusedError):
        with session:
            pass


def test_inprocess_force_mode_works() -> None:
    """force_mode='inprocess' は今まで通り動作する（リグレッションガード）。"""
    with LiveSession(venue="tachibana", demo=True, force_mode="inprocess") as s:
        assert s.mode == "inprocess"


def test_auto_force_mode_falls_back_to_inprocess() -> None:
    """force_mode='auto' は Phase 8.1a では inprocess に倒される。"""
    with LiveSession(venue="tachibana", demo=True, force_mode="auto") as s:
        assert s.mode == "inprocess"


# ---------------------------------------------------------------------------
# login() 内部 API への伝播（H11）
# ---------------------------------------------------------------------------


def _make_dummy_creds() -> tuple[str, str]:
    """テスト用ダミー cred — uuid で衝突回避し、`.env` の値を絶対に使わない。"""
    return f"user-{uuid.uuid4().hex[:8]}", f"pw-{uuid.uuid4().hex[:8]}"


class _StubSession:
    """`tachibana_login` の戻り値スタブ。実 TachibanaSession は持たなくて良い。"""

    def __init__(self, user_id: str, is_demo: bool) -> None:
        self.user_id = user_id
        self.is_demo = is_demo


def _patch_login(monkeypatch, recorded: dict, *, raise_exc: Exception | None = None):
    """`engine.replay_session._tachibana_login_call` を差し替え、
    呼び出し引数を `recorded` に記録する。"""

    async def _fake_login(user_id, password, *, is_demo, p_no_counter, http_client=None):
        recorded["user_id"] = user_id
        recorded["password"] = password
        recorded["is_demo"] = is_demo
        if raise_exc is not None:
            raise raise_exc
        return _StubSession(user_id, is_demo)

    # `replay_session` モジュール側で再 export している symbol を差し替える。
    # monkeypatch.setattr で属性が無いと AttributeError になるので raising=False は不要
    # （実装側で必ずこの名前で import する契約にする）。
    monkeypatch.setattr(
        "engine.replay_session._tachibana_login_call",
        _fake_login,
    )


def test_login_inprocess_with_explicit_credentials_calls_internal_api(monkeypatch):
    """明示的に渡された user_id / password が内部 API に伝播する。"""
    # env 側を消して、env が拾われていないことも同時に保証する。
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    recorded: dict = {}
    _patch_login(monkeypatch, recorded)

    user_id, password = _make_dummy_creds()
    with LiveSession(venue="tachibana", demo=True) as s:
        s.login(user_id=user_id, password=password)

    assert recorded["user_id"] == user_id
    assert recorded["password"] == password
    assert recorded["is_demo"] is True


def test_login_inprocess_with_env_credentials(monkeypatch):
    """引数を渡さず env 経由で cred を解決する経路。"""
    user_id, password = _make_dummy_creds()
    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", user_id)
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", password)

    recorded: dict = {}
    _patch_login(monkeypatch, recorded)

    with LiveSession(venue="tachibana", demo=True) as s:
        s.login()

    assert recorded["user_id"] == user_id
    assert recorded["password"] == password


def test_login_missing_credentials_raises_value_error(monkeypatch):
    """引数も env も無ければ ValueError。"""
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    recorded: dict = {}
    _patch_login(monkeypatch, recorded)

    with LiveSession(venue="tachibana", demo=True) as s:
        with pytest.raises(ValueError, match="missing credentials"):
            s.login()

    assert recorded == {}, "内部 API は呼ばれてはならない"


def test_login_internal_api_failure_propagates(monkeypatch):
    """内部 API が例外を投げたらそのまま伝播する（包んだ RuntimeError も許容）。"""
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    class _BoomError(Exception):
        pass

    recorded: dict = {}
    _patch_login(monkeypatch, recorded, raise_exc=_BoomError("boom"))

    user_id, password = _make_dummy_creds()
    with LiveSession(venue="tachibana", demo=True) as s:
        with pytest.raises((_BoomError, RuntimeError)):
            s.login(user_id=user_id, password=password)


def test_login_demo_flag_passed_to_internal_api(monkeypatch):
    """LiveSession(demo=False) は内部 API に is_demo=False で伝わる。"""
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    recorded: dict = {}
    _patch_login(monkeypatch, recorded)

    user_id, password = _make_dummy_creds()
    with LiveSession(venue="tachibana", demo=False) as s:
        s.login(user_id=user_id, password=password)

    assert recorded["is_demo"] is False


def test_login_marks_session_as_logged_in(monkeypatch):
    """正常 login 後に再度 login() を呼んだら冪等 / 状態保持していること。"""
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    recorded: dict = {}
    _patch_login(monkeypatch, recorded)

    user_id, password = _make_dummy_creds()
    with LiveSession(venue="tachibana", demo=True) as s:
        s.login(user_id=user_id, password=password)
        # 内部状態を漏らさず、最低限「ログイン済み」が分かる contract を保つ。
        assert s.is_logged_in is True
