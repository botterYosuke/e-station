"""KabuOrderClient / KabuTradePasswordHolder テスト (Phase 2: 発注・取消・polling)。

Acceptance criteria:
- test_sendorder_uses_order_bucket
- test_sendorder_buy_side_is_2
- test_trade_password_held_once_per_session
- test_dev_trade_password_env_skips_dialog
- test_idle_forget_clears_trade_password
- test_order_fill_polling_emits_fill_event
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_httpx

from engine.exchanges.kabusapi_auth import KabuTradePasswordHolder
from engine.exchanges.kabusapi_orders import KabuOrderClient
from engine.exchanges.kabusapi_ratelimit import OrderBucket
from engine.exchanges.kabusapi_url import endpoint


# ---------------------------------------------------------------------------
# KabuTradePasswordHolder テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
def test_trade_password_held_once_per_session():
    """set_password で保持した値が get_password で返る（セッション内は 1 回のみ収集）。"""
    holder = KabuTradePasswordHolder()
    holder.set_password("secret123")
    assert holder.get_password() == "secret123"
    # 2 回目以降も同じ値が返る
    assert holder.get_password() == "secret123"


@pytest.mark.demo_kabu
def test_idle_forget_clears_trade_password():
    """30 分 idle 後に get_password が None を返す。"""
    holder = KabuTradePasswordHolder(idle_forget_minutes=0.0)  # 即時期限切れ
    holder.set_password("secret123")

    # 過去時刻に touch を偽装して idle 期限切れにする
    far_past = time.monotonic() - 1.0
    holder._last_use_time = far_past

    assert holder.get_password() is None
    assert holder._password is None  # クリアされている


@pytest.mark.demo_kabu
def test_trade_password_lockout_after_three_invalid():
    """3 回連続誤入力後は is_locked_out() が True を返す。"""
    holder = KabuTradePasswordHolder()
    holder.set_password("wrong")

    holder.on_invalid()
    assert not holder.is_locked_out()
    holder.on_invalid()
    assert not holder.is_locked_out()
    locked = holder.on_invalid()  # 3 回目
    assert locked
    assert holder.is_locked_out()


@pytest.mark.demo_kabu
def test_trade_password_clear():
    """clear() でパスワードが None になる。"""
    holder = KabuTradePasswordHolder()
    holder.set_password("secret")
    holder.clear()
    assert holder.get_password() is None


@pytest.mark.demo_kabu
def test_on_submit_success_resets_invalid_count():
    """on_submit_success() で invalid カウンタがリセットされる。"""
    holder = KabuTradePasswordHolder()
    holder.on_invalid()
    holder.on_invalid()
    holder.on_submit_success()
    # カウンタリセット済みなので再度 2 回は lockout にならない
    holder.on_invalid()
    holder.on_invalid()
    assert not holder.is_locked_out()


# ---------------------------------------------------------------------------
# KabuOrderClient テスト
# ---------------------------------------------------------------------------


def make_order_client(httpx_mock, holder: KabuTradePasswordHolder | None = None) -> KabuOrderClient:
    if holder is None:
        holder = KabuTradePasswordHolder()
        holder.set_password("trade_pass_xyz")
    return KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_sendorder_uses_order_bucket(httpx_mock: pytest_httpx.HTTPXMock):
    """send_order() が OrderBucket を経由する（流量制限 5/s が掛かる）。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder", env="verify"),
        json={"Result": 0, "OrderID": "20230101A01234567"},
    )

    bucket = MagicMock()
    bucket.__aenter__ = AsyncMock(return_value=None)
    bucket.__aexit__ = AsyncMock(return_value=False)

    holder = KabuTradePasswordHolder()
    holder.set_password("trade_pass_xyz")

    client = KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
        order_bucket=bucket,
    )

    await client.send_order(
        symbol="9433",
        exchange=1,
        side="buy",
        qty=100,
    )

    bucket.__aenter__.assert_called_once()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_sendorder_buy_side_is_2(httpx_mock: pytest_httpx.HTTPXMock):
    """buy 発注の Side は "2"（立花の "3" と混同しない — data-mapping.md §8）。"""
    captured_body: dict = {}

    def capture(request, **kwargs):
        import json
        captured_body.update(json.loads(request.content))
        return pytest_httpx.HTTPXMock._Response(  # type: ignore[attr-defined]
            status_code=200,
            json={"Result": 0, "OrderID": "20230101A01234567"},
        )

    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder", env="verify"),
        json={"Result": 0, "OrderID": "20230101A01234567"},
    )

    client = make_order_client(httpx_mock)
    await client.send_order(
        symbol="9433",
        exchange=1,
        side="buy",
        qty=100,
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    import json
    body = json.loads(requests[0].content)
    assert body["Side"] == "2", f"buy の Side は '2' でなければならない。実際: {body['Side']!r}"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_sendorder_sell_side_is_1(httpx_mock: pytest_httpx.HTTPXMock):
    """sell 発注の Side は "1"。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder", env="verify"),
        json={"Result": 0, "OrderID": "20230101A01234568"},
    )

    client = make_order_client(httpx_mock)
    await client.send_order(
        symbol="9433",
        exchange=1,
        side="sell",
        qty=100,
    )

    import json
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["Side"] == "1"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_cancelorder_uses_order_bucket(httpx_mock: pytest_httpx.HTTPXMock):
    """cancel_order() が OrderBucket を経由する。"""
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("cancelorder", env="verify"),
        json={"Result": 0, "OrderID": "20230101A01234567"},
    )

    bucket = MagicMock()
    bucket.__aenter__ = AsyncMock(return_value=None)
    bucket.__aexit__ = AsyncMock(return_value=False)

    holder = KabuTradePasswordHolder()
    holder.set_password("trade_pass_xyz")

    client = KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
        order_bucket=bucket,
    )

    await client.cancel_order(order_id="20230101A01234567")
    bucket.__aenter__.assert_called_once()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_order_fill_polling_emits_fill_event(httpx_mock: pytest_httpx.HTTPXMock):
    """poll_fills() が GET /orders を呼び、約定済みレコードを返す。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("orders", env="verify"),
        json=[
            {
                "ID": "20230101A01234567",
                "State": 5,  # 5 = 約定
                "Side": "2",
                "Symbol": "9433",
                "Exchange": 1,
                "Price": 2479.0,
                "Qty": 100.0,
                "CumQty": 100.0,
                "RecvTime": "2023-01-01T10:00:00+09:00",
            },
            {
                "ID": "20230101A01234568",
                "State": 1,  # 1 = 受付中（未約定）
                "Side": "1",
                "Symbol": "7203",
                "Exchange": 1,
                "Price": 1500.0,
                "Qty": 200.0,
                "CumQty": 0.0,
                "RecvTime": "2023-01-01T10:01:00+09:00",
            },
        ],
    )

    client = make_order_client(httpx_mock)
    fills = await client.poll_fills()

    assert len(fills) == 1, "約定済み (State=5) の 1 件のみ返す"
    assert fills[0]["ID"] == "20230101A01234567"
    assert fills[0]["State"] == 5


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_dev_trade_password_env_skips_dialog(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: pytest_httpx.HTTPXMock,
):
    """DEV_KABU_TRADE_PASSWORD が設定されている場合は tkinter ダイアログを spawn しない。"""
    monkeypatch.setenv("DEV_KABU_TRADE_PASSWORD", "env_trade_pass")

    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder", env="verify"),
        json={"Result": 0, "OrderID": "20230101A01234569"},
    )

    with patch(
        "engine.exchanges.kabusapi_orders._spawn_trade_dialog",
        new_callable=AsyncMock,
    ) as mock_dialog:
        client = KabuOrderClient(
            token="test_token_abc",
            env="verify",
            trade_password_holder=KabuTradePasswordHolder(),
            dev_trade_password_allowed=True,
        )
        await client.send_order(
            symbol="9433",
            exchange=1,
            side="buy",
            qty=100,
        )

    mock_dialog.assert_not_called()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_cancelorder_includes_correct_fields(httpx_mock: pytest_httpx.HTTPXMock):
    """cancel_order() のリクエストボディが OpenAPI RequestCancelOrder 仕様に合致する。

    OpenAPI RequestCancelOrder は OrderId（小文字 d）のみ必須。
    Password フィールドは RequestCancelOrder スキーマに存在しない。
    （旧テスト名: test_cancelorder_sends_password_in_body — M-6 修正）
    """
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("cancelorder", env="verify"),
        json={"Result": 0, "OrderID": "20230101A01234567"},
    )

    client = make_order_client(httpx_mock)
    await client.cancel_order(order_id="20230101A01234567")

    import json
    body = json.loads(httpx_mock.get_requests()[0].content)
    # [M-6] OpenAPI RequestCancelOrder に合わせて OrderId（小文字 d）を確認
    assert "OrderId" in body, f"OrderId が body に含まれていない: {body}"
    assert body["OrderId"] == "20230101A01234567"
    # Password フィールドは OpenAPI スキーマに存在しないので含まれてはならない
    assert "Password" not in body, "Password は RequestCancelOrder スキーマに存在しないので含めてはならない"
    # 旧フィールド名 OrderID が使われていないことを確認
    assert "OrderID" not in body, "OrderID（大文字 D）は誤り。正しくは OrderId（小文字 d）"


# ---------------------------------------------------------------------------
# MEDIUM-3: KabuTradePasswordInvalidError → holder.on_invalid() テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_sendorder_trade_password_invalid_calls_on_invalid(httpx_mock: pytest_httpx.HTTPXMock):
    """send_order() で KabuTradePasswordInvalidError が発生したとき holder.on_invalid() が呼ばれる。"""
    from engine.exchanges.kabusapi_auth import KabuTradePasswordInvalidError

    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder", env="verify"),
        json={"Code": 4002013, "Message": "Trade password is invalid"},
        status_code=400,
    )

    holder = KabuTradePasswordHolder()
    holder.set_password("wrong_password")
    client = KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
    )

    with pytest.raises(KabuTradePasswordInvalidError):
        await client.send_order(
            symbol="9433",
            exchange=1,
            side="buy",
            qty=100,
        )

    # on_invalid() が呼ばれたことでパスワードが None になっているはず
    assert holder._password is None


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_cancelorder_does_not_send_password(httpx_mock: pytest_httpx.HTTPXMock):
    """cancel_order() のリクエストボディに Password が含まれない。

    OpenAPI RequestCancelOrder に Password フィールドは存在しないため、
    cancel_order() は取引パスワードを収集・送信しない設計に変更済み。
    （旧テスト: test_cancelorder_trade_password_invalid_calls_on_invalid — M-6 対応で仕様変更）
    """
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("cancelorder", env="verify"),
        json={"Result": 0, "OrderID": "20230101A01234567"},
    )

    holder = KabuTradePasswordHolder()
    holder.set_password("trade_pass_xyz")
    client = KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
    )

    await client.cancel_order(order_id="20230101A01234567")

    import json
    body = json.loads(httpx_mock.get_requests()[0].content)
    # Password は RequestCancelOrder スキーマに存在しないので送信してはならない
    assert "Password" not in body, "cancel_order は Password を送信してはならない"
    assert "OrderId" in body
    assert body["OrderId"] == "20230101A01234567"


# ---------------------------------------------------------------------------
# A-1 HIGH: on_invalid() 二重呼び出しのテスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_on_invalid_called_exactly_once_on_invalid_password(httpx_mock: pytest_httpx.HTTPXMock):
    """send_order() で 4002013 エラーが返ったとき holder.on_invalid() は 1 回だけ呼ばれる。

    KabuOrderClient.send_order() 内で on_invalid() を呼ぶ。
    server.py の except ブランチで再度呼んではならない。
    """
    from unittest.mock import patch as _patch

    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder", env="verify"),
        json={"Code": 4002013, "Message": "Trade password is invalid"},
        status_code=400,
    )

    holder = KabuTradePasswordHolder()
    holder.set_password("wrong_password")

    on_invalid_calls: list[int] = []
    original_on_invalid = holder.on_invalid

    def counting_on_invalid():
        on_invalid_calls.append(1)
        return original_on_invalid()

    holder.on_invalid = counting_on_invalid  # type: ignore[method-assign]

    client = KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
    )

    from engine.exchanges.kabusapi_auth import KabuTradePasswordInvalidError
    with pytest.raises(KabuTradePasswordInvalidError):
        await client.send_order(
            symbol="9433",
            exchange=1,
            side="buy",
            qty=100,
        )

    assert len(on_invalid_calls) == 1, (
        f"on_invalid() は 1 回だけ呼ばれるべき。実際: {len(on_invalid_calls)} 回"
    )


# ---------------------------------------------------------------------------
# A-2 HIGH: poll_fills が非 list レスポンスで KabuApiError を raise するテスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_poll_fills_non_list_response_raises(httpx_mock: pytest_httpx.HTTPXMock):
    """poll_fills() が dict レスポンスを受け取ったとき KabuApiError を raise する。

    非 list をサイレントに [] として返してはならない。
    """
    from engine.exchanges.kabusapi_auth import KabuApiError

    httpx_mock.add_response(
        method="GET",
        url=endpoint("orders", env="verify"),
        json={"error": "something"},
    )

    client = make_order_client(httpx_mock)
    with pytest.raises(KabuApiError):
        await client.poll_fills()


# ---------------------------------------------------------------------------
# A-3 MEDIUM: cancel_order 成功時に on_submit_success() が呼ばれるテスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_cancel_order_success_does_not_reset_invalid_count(httpx_mock: pytest_httpx.HTTPXMock):
    """cancel_order() が成功しても holder.on_submit_success() は呼ばれない。

    OpenAPI RequestCancelOrder に Password フィールドが存在しないため、
    cancel_order() は取引パスワード管理（on_submit_success / on_invalid）を行わない。
    （旧テスト: test_cancel_order_success_resets_invalid_count — M-6 対応で仕様変更）
    """
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("cancelorder", env="verify"),
        json={"Result": 0, "OrderID": "20230101A01234567"},
    )

    holder = KabuTradePasswordHolder()
    holder.set_password("trade_pass_xyz")
    # 事前に invalid カウントを 2 にしておく（lockout 前）
    holder.on_invalid()
    holder.on_invalid()
    invalid_count_before = holder._invalid_count
    assert not holder.is_locked_out()

    client = KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
    )
    await client.cancel_order(order_id="20230101A01234567")

    # cancel_order は Password を送らないので on_submit_success() を呼ばない → カウント不変
    assert holder._invalid_count == invalid_count_before, (
        f"cancel_order は on_submit_success() を呼ばないので invalid_count は変わらないはず。"
        f"before={invalid_count_before}, after={holder._invalid_count}"
    )
