"""P4-5: 発注パスでの guard_prod_url 多層 pin テスト。

`KabuOrderClient(env="prod")` で発注メソッドを呼んだとき、
KABU_ALLOW_PROD=1 が未設定なら URL 組立段階で ValueError が raise されること。
これは P4-1 の `base_url()` が `endpoint()` 経由で自動 pin している効果の最終フェンス確認。
"""

from __future__ import annotations

import pytest

from engine.exchanges.kabusapi_orders import KabuOrderClient


pytestmark = pytest.mark.demo_kabu


def _make_holder():
    from engine.exchanges.kabusapi_auth import KabuTradePasswordHolder

    holder = KabuTradePasswordHolder()
    holder.set_password("dummy-pass")
    return holder


@pytest.fixture
def prod_order_client():
    return KabuOrderClient(
        token="dummy-token",
        env="prod",
        trade_password_holder=_make_holder(),
    )


@pytest.fixture
def verify_order_client():
    return KabuOrderClient(
        token="dummy-token",
        env="verify",
        trade_password_holder=_make_holder(),
    )


@pytest.mark.asyncio
async def test_send_order_prod_blocked_without_env(monkeypatch, prod_order_client):
    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
    with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
        await prod_order_client.send_order(
            symbol="5401",
            exchange=1,
            side="buy",
            qty=100,
            front_order_type=10,
            price=1000.0,
            expire_day=0,
            cash_margin=1,
            deliv_type=2,
            account_type=4,
        )


@pytest.mark.asyncio
async def test_cancel_order_prod_blocked_without_env(monkeypatch, prod_order_client):
    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
    with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
        await prod_order_client.cancel_order(order_id="ORDER123")


@pytest.mark.asyncio
async def test_send_order_future_prod_blocked_without_env(monkeypatch, prod_order_client):
    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
    with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
        await prod_order_client.send_order_future(
            symbol="167060019",
            exchange=2,
            trade_type=1,
            time_in_force=1,
            side="buy",
            qty=1,
            front_order_type=120,
            price=0.0,
            expire_day=0,
        )


@pytest.mark.asyncio
async def test_send_order_option_prod_blocked_without_env(monkeypatch, prod_order_client):
    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
    with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
        await prod_order_client.send_order_option(
            symbol="167060011C28000",
            exchange=2,
            trade_type=1,
            time_in_force=1,
            side="buy",
            qty=1,
            front_order_type=120,
            price=0.0,
            expire_day=0,
        )


@pytest.mark.asyncio
async def test_poll_fills_prod_blocked_without_env(monkeypatch, prod_order_client):
    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
    with pytest.raises(ValueError, match="KABU_ALLOW_PROD"):
        await prod_order_client.poll_fills()


def test_verify_order_client_does_not_invoke_guard(monkeypatch, verify_order_client):
    """verify env では KABU_ALLOW_PROD なしで env 解決経路が成立することの最低限確認。
    URL 組立だけで ValueError が出ないことを base_url 経由で確認する。
    """
    from engine.exchanges.kabusapi_url import endpoint
    monkeypatch.delenv("KABU_ALLOW_PROD", raising=False)
    # 例外を上げない
    assert endpoint("sendorder", env="verify").startswith("http://localhost:18081")
