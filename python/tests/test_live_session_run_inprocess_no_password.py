"""R4 Group B (silent-HIGH-2): in-process mode の second_password=None 経路を pin する。

旧実装 (`python/engine/replay_session.py::LiveSession.run` in-process arm) は
`second_password is None` を ``RuntimeError`` で直接 raise していたため、
auto mode fallback (`force_mode=None` で attach probe 失敗 → in-process 経路) で
到達したユーザーには:

- attach 経路: ``SecondPasswordRequired`` event が ``on_event`` に届く
- in-process 経路: ``on_event`` に何も emit せず Python 例外だけ

という非対称な silent UX failure があった。本テストは:

1. in-process 経路でも ``SecondPasswordRequired`` event が ``on_event`` に
   emit されることを pin する
2. event 形式が attach 経路 (``replay_session.py:2166``) と対称的に
   ``{"event": "SecondPasswordRequired", "strategy_id": ..., "ts_event_ms": ...}``
   を持つことを pin する
3. credential (=second_password) が event 中に絶対に含まれないことを pin する
4. event emit 後に従来通り RuntimeError が raise されることを pin する
   (CLI 側のリトライ判断や exit code 計算は例外伝播に依存している)
"""

from __future__ import annotations

import pytest

from engine.replay_session import LiveSession


@pytest.fixture(autouse=True)
def _isolate_from_running_engine(monkeypatch) -> None:
    """engine-session.json / env から自動 attach しないよう session file / token を無効化する。"""
    monkeypatch.setattr("engine.replay_session._read_session_file", lambda: None)
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)


class _StubSession:
    def __init__(self) -> None:
        self.user_id = "test_user"
        self.is_demo = True


def _make_logged_in_session(monkeypatch) -> LiveSession:
    """login() 済みで second_password=None / force_mode=inprocess の LiveSession を返す。"""

    async def _fake_login(user_id, password, *, is_demo, p_no_counter, http_client=None):
        return _StubSession()

    monkeypatch.setattr("engine.replay_session._tachibana_login_call", _fake_login)

    s = LiveSession(
        venue="tachibana",
        demo=True,
        force_mode="inprocess",
        second_password=None,
    )
    s.__enter__()
    s.login(user_id="user123", password="pw456")
    return s


def test_run_inprocess_without_second_password_emits_second_password_required_event(
    monkeypatch,
) -> None:
    """in-process mode で second_password=None なら on_event に
    SecondPasswordRequired event を 1 件 emit してから RuntimeError を raise する。"""
    s = _make_logged_in_session(monkeypatch)
    events: list[dict] = []

    with pytest.raises(RuntimeError, match="second_password"):
        s.run(
            instrument_id="8306.T",
            strategy_file="dummy.py",
            max_qty=100,
            max_notional_jpy=500_000,
            on_event=events.append,
            strategy_id="silent-h2-test",
        )

    spr_events = [e for e in events if e.get("event") == "SecondPasswordRequired"]
    assert len(spr_events) == 1, (
        "in-process arm must emit exactly one SecondPasswordRequired event "
        f"before raising RuntimeError; got events={events!r}"
    )

    evt = spr_events[0]
    # attach 経路と対称のフィールドを持つこと
    assert evt["event"] == "SecondPasswordRequired"
    assert evt.get("strategy_id") == "silent-h2-test", (
        f"event must include strategy_id (attach 経路と対称): {evt!r}"
    )
    assert "ts_event_ms" in evt, (
        f"event must include ts_event_ms (attach 経路と対称): {evt!r}"
    )


def _walk_keys(obj, _seen=None):
    """dict キーを任意の深さで再帰的に yield する (str キーのみ)。

    R6 silent-MEDIUM-2: 「将来 event payload に ``"password_hint"`` のような
    nested key が追加された場合に検出する」ためのキー専用走査。値側はイベント
    型名 ("SecondPasswordRequired") を含むため別の検査経路を使う。
    """
    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return
    _seen.add(oid)
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            yield from _walk_keys(v, _seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            yield from _walk_keys(item, _seen)


def _walk_values(obj, _seen=None):
    """dict 値 / list 要素を任意の深さで再帰的に yield する (str 値のみ)。

    R6 silent-MEDIUM-2: credential リテラル (例: ``pw456``) 探索用。dict キーは
    含めない (キーは ``_walk_keys`` で別走査)。
    """
    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return
    _seen.add(oid)
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_values(v, _seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            yield from _walk_values(item, _seen)


def test_walk_keys_finds_nested_keys() -> None:
    """``_walk_keys`` が nested 構造のキーを発見することを pin。"""
    nested = {
        "event": "X",
        "outer": {"inner": ["leaf-a", {"deep": "leaf-b"}]},
    }
    found = set(_walk_keys(nested))
    assert {"event", "outer", "inner", "deep"} <= found, found


def test_walk_values_finds_nested_values_but_not_keys() -> None:
    """``_walk_values`` は値のみを yield し、キーは含まないことを pin。"""
    nested = {
        "outer_key": {"inner_key": ["leaf-a", {"deep_key": "leaf-b"}]},
    }
    found = set(_walk_values(nested))
    assert found == {"leaf-a", "leaf-b"}, found


def test_run_inprocess_second_password_required_event_contains_no_credential(
    monkeypatch,
) -> None:
    """credential が SecondPasswordRequired event の **どのフィールド** にも
    含まれないことを pin する (silent failure 防御 + secret leak 防止)。

    R6 silent-MEDIUM-2: `_walk_keys` / `_walk_values` で nested 構造を再帰的に
    探索し、将来 event 構造が拡張されても credential leak を確実に検出する。
    キー側 = ``password`` / ``credential`` 含有を禁止 (新 field name の警告)。
    値側 = login 時の literal ``pw456`` 含有を禁止 (実際の credential leak 検出)。
    """
    s = _make_logged_in_session(monkeypatch)
    events: list[dict] = []

    with pytest.raises(RuntimeError):
        s.run(
            instrument_id="8306.T",
            strategy_file="dummy.py",
            max_qty=100,
            max_notional_jpy=500_000,
            on_event=events.append,
            strategy_id="silent-h2-cred",
        )

    spr_events = [e for e in events if e.get("event") == "SecondPasswordRequired"]
    assert spr_events, "expected at least one SecondPasswordRequired event"
    for evt in spr_events:
        # キー: 新 field 追加で credential 関連語を含めることを禁止
        for key in _walk_keys(evt):
            lowered = key.lower()
            assert "password" not in lowered, (
                f"event must not include 'password' in any key (got {key!r}): {evt!r}"
            )
            assert "credential" not in lowered, (
                f"event must not include 'credential' in any key (got {key!r}): {evt!r}"
            )
        # 値: login 時の literal credential が任意の深さで漏れていないこと
        for value in _walk_values(evt):
            assert "pw456" not in value, (
                f"event must not leak the login password literal anywhere: {evt!r}"
            )
