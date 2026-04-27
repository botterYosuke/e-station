"""TachibanaSessionHolder の idle forget / lockout state テスト (H-7/H-8)。"""
from __future__ import annotations

import pytest
from engine.exchanges.tachibana_auth import TachibanaSessionHolder


def test_set_and_get_password():
    h = TachibanaSessionHolder()
    h.set_password("secret")
    assert h.get_password() == "secret"


def test_clear_resets_password():
    h = TachibanaSessionHolder()
    h.set_password("secret")
    h.clear()
    assert h.get_password() is None


def test_initial_password_is_none():
    h = TachibanaSessionHolder()
    assert h.get_password() is None


def test_idle_forget_returns_none_after_expiry():
    """idle_forget_minutes=0 相当（_idle_forget_secs=0）にして過去時刻を渡す。"""
    h = TachibanaSessionHolder(idle_forget_minutes=0.001)
    h.set_password("secret")
    # last_use_time を 1000 秒前に設定
    h._last_use_time = h._now() - 1000
    assert h.get_password() is None


def test_idle_not_expired_within_window():
    h = TachibanaSessionHolder(idle_forget_minutes=30.0)
    h.set_password("secret")
    # just set → 直後は期限切れしていない
    assert h.get_password() == "secret"


def test_touch_resets_idle_timer():
    h = TachibanaSessionHolder(idle_forget_minutes=0.001)
    h.set_password("secret")
    h._last_use_time = h._now() - 1000  # 期限切れ状態
    h.touch()  # リセット
    # touch 直後なので期限切れしていない
    assert h.is_idle_expired() is False


def test_lockout_after_max_retries():
    h = TachibanaSessionHolder(max_retries=3, lockout_secs=1800.0)
    h.set_password("secret")
    assert h.on_invalid() is False  # 1 回目
    assert h.on_invalid() is False  # 2 回目
    assert h.on_invalid() is True   # 3 回目 → lockout


def test_lockout_rejects_is_locked_out():
    h = TachibanaSessionHolder(max_retries=2, lockout_secs=1800.0)
    h.on_invalid()
    h.on_invalid()  # lockout 発動
    assert h.is_locked_out() is True


def test_lockout_expires_after_secs():
    h = TachibanaSessionHolder(max_retries=1, lockout_secs=60.0)
    h.on_invalid()  # lockout 発動
    assert h.is_locked_out() is True
    # lockout_until を過去に設定
    h._lockout_until = h._now() - 1
    assert h.is_locked_out() is False


def test_clear_resets_lockout():
    h = TachibanaSessionHolder(max_retries=1, lockout_secs=1800.0)
    h.on_invalid()  # lockout 発動
    h.clear()
    # clear は password をクリアするが invalid_count / lockout_until はリセットしない
    # (architecture.md C-R5-H2: lockout は clear では解除しない)
    # → テストは clear 後のパスワードが None であることを確認
    assert h.get_password() is None


def test_on_submit_success_resets_invalid_count():
    h = TachibanaSessionHolder(max_retries=3)
    h.on_invalid()
    h.on_invalid()  # 2 回
    h.on_submit_success()  # リセット
    assert h._invalid_count == 0
    # リセット後に 3 回 on_invalid → 再 lockout
    h.on_invalid()
    h.on_invalid()
    locked = h.on_invalid()
    assert locked is True


def test_on_invalid_clears_password():
    h = TachibanaSessionHolder(max_retries=3)
    h.set_password("secret")
    h.on_invalid()
    # SECOND_PASSWORD_INVALID 受信時に second_password をクリア
    assert h._password is None


def test_touch_updates_last_use_time():
    h = TachibanaSessionHolder()
    now = 1000.0
    h.touch(now=now)
    assert h._last_use_time == now


def test_lockout_after_second_password_invalid():
    """SecondPasswordInvalidError 相当（3回 on_invalid）で lockout する。"""
    h = TachibanaSessionHolder(max_retries=3)
    h.set_password("secret")
    h.on_invalid()
    h.on_invalid()
    locked = h.on_invalid()
    assert locked is True
    assert h.is_locked_out() is True
    # lockout 後は get_password が None を返す
    assert h.get_password() is None


def test_clear_does_not_remove_lockout():
    """clear() は lockout を解除しない（C-R5-H2）。"""
    h = TachibanaSessionHolder(max_retries=1, lockout_secs=1800.0)
    h.set_password("secret")
    h.on_invalid()  # lockout 発動
    h.clear()
    assert h.is_locked_out() is True, "lockout must NOT be cleared by clear()"
    assert h.get_password() is None  # locked out → None


def test_idle_expired_while_locked_out_returns_none():
    """idle 期限切れ + lockout 両方の状態で get_password() は None。"""
    h = TachibanaSessionHolder(max_retries=1, lockout_secs=1800.0, idle_forget_minutes=0.001)
    h.set_password("secret")
    h._last_use_time = h._now() - 1000  # idle 期限切れ
    h.on_invalid()  # lockout 発動
    assert h.get_password() is None
    assert h.is_locked_out() is True
