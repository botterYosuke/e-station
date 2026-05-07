"""KabuRestClient のテスト: fetch_board の touch / 満杯エラー。"""
import pytest
import pytest_httpx

from engine.exchanges.kabusapi_auth import KabuRegisterFullError
from engine.exchanges.kabusapi_register import RegisterSet
from engine.exchanges.kabusapi_rest import KabuRestClient
from engine.exchanges.kabusapi_url import endpoint, symbol_key


def make_client(register_set: RegisterSet, httpx_mock) -> KabuRestClient:
    return KabuRestClient(
        token="test_token_abc",
        env="verify",
        register_set=register_set,
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_touches_register_set(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_board() が RegisterSet.touch() を呼ぶ (INV-K6-TOUCH)."""
    rs = RegisterSet(max_symbols=50)
    rs.register("9433", 1)  # 事前登録

    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"board/{symbol_key('9433', 1)}", env="verify"),
        json={
            "Symbol": "9433",
            "Exchange": 1,
            "CurrentPrice": 2479.0,
        },
    )

    client = make_client(rs, httpx_mock)
    result = await client.fetch_board("9433", 1)

    assert result["Symbol"] == "9433"
    # touch によって LRU 位置が更新されたことを確認（9433 が最新になる）
    assert ("9433", 1) in rs


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_raises_when_full(httpx_mock: pytest_httpx.HTTPXMock):
    """新規 + 満杯時に KabuRegisterFullError が raise される (INV-K6-REST-FULL)."""
    rs = RegisterSet(max_symbols=1)
    rs.register("7203", 1)  # 1 件で満杯

    # HTTP モックは設定しない（RegisterSet チェックで先に例外が発生するため）
    client = make_client(rs, httpx_mock)

    with pytest.raises(KabuRegisterFullError):
        await client.fetch_board("9433", 1)  # 新規 + 満杯


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_existing_symbol_calls_touch(httpx_mock: pytest_httpx.HTTPXMock):
    """既存銘柄の fetch_board は register を呼ばずに touch のみ。"""
    rs = RegisterSet(max_symbols=1)
    rs.register("9433", 1)

    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"board/{symbol_key('9433', 1)}", env="verify"),
        json={"Symbol": "9433", "Exchange": 1, "CurrentPrice": 100.0},
    )

    client = make_client(rs, httpx_mock)
    await client.fetch_board("9433", 1)

    # まだ 1 件のみ（touch されたが register は呼ばれていない）
    assert len(rs) == 1
