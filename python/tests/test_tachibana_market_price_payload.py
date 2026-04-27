"""TDD RED: CLMMfdsGetMarketPrice リクエストに sTargetColumn が必要 + 正しいフィールド名で応答を解析する。

バグ: fetch_ticker_stats / fetch_depth_snapshot が sTargetColumn をペイロードに含めず、
立花 API が -1: 引数（sTargetColumn:[NULL]）エラー を返す。

加えて、応答キー名 (aCLMMfdsMarketPrice) とアイテムのフィールド名 (pDPP / pDOP 等 FD コード) も
既存コードでは誤っており同時修正が必要。

根拠:
  - e_api_get_price_from_file_tel.py L936: dic_return.get('aCLMMfdsMarketPrice')
  - mfds_json_api_ref_text.html §CLMMfdsGetMarketPrice 応答例: pDPP / pDOP / pDHP / pDLP / pDV
  - inventory-T0.md §11.2.b FD 情報コード確定表
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


def _fake_session() -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws="wss://example.test/event/",
        zyoutoeki_kazei_c="",
    )


def _stubbed(tmp_path: Path) -> TachibanaWorker:
    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True, session=_fake_session())

    async def _fake_download() -> None:
        worker._master_records = {
            "CLMIssueMstKabu": [
                {"sIssueCode": "7203", "sIssueName": "トヨタ自動車", "sIssueNameEizi": "TOYOTA MOTOR"},
            ],
            "CLMIssueSizyouMstKabu": [
                {"sIssueCode": "7203", "sSizyouC": "00", "sBaibaiTaniNumber": "100", "sYobineTaniNumber": "1"},
            ],
        }
        worker._yobine_table = {}

    worker._download_master = AsyncMock(side_effect=_fake_download)  # type: ignore[method-assign]
    return worker


def _parse_url_payload(url: str) -> dict:
    """URL のクエリ文字列部分を JSON として解析する。"""
    import json as _json

    qs = urllib.parse.urlparse(url).query
    # Tachibana の URL エンコードを戻す（最低限 %22 → " だけ）
    decoded = urllib.parse.unquote(qs)
    return _json.loads(decoded)


# ---------------------------------------------------------------------------
# Bug 1: sTargetColumn が fetch_ticker_stats のペイロードに必要
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_ticker_stats_includes_sTargetColumn_in_request(tmp_path: Path):
    """fetch_ticker_stats は CLMMfdsGetMarketPrice リクエストに sTargetColumn を含めなければならない。

    この条件が満たされない場合、立花 API は -1: 引数（sTargetColumn:[NULL]）エラー を返す。
    """
    worker = _stubbed(tmp_path)
    captured_urls: list[str] = []

    async def _capture(url: str) -> bytes:
        captured_urls.append(url)
        # 最小限の成功応答（aCLMMfdsMarketPrice が正しいキー名）
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPrice","sResultCode":"0",'
            '"aCLMMfdsMarketPrice":['
            '{"sIssueCode":"7203","pDPP":"2880","pDOP":"2860","pDHP":"2900",'
            '"pDLP":"2800","pDV":"1234567","tDPP:T":"15:00"}'
            ']}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_capture)  # type: ignore[method-assign]
    await worker.fetch_ticker_stats("7203", "stock")

    assert captured_urls, "HTTP GET が呼ばれなかった"
    payload = _parse_url_payload(captured_urls[0])
    assert "sTargetColumn" in payload, (
        "sTargetColumn が CLMMfdsGetMarketPrice リクエストに含まれていない。"
        "立花 API は -1 エラーを返す。"
    )


@pytest.mark.asyncio
async def test_fetch_ticker_stats_sTargetColumn_contains_required_fd_codes(tmp_path: Path):
    """sTargetColumn に pDPP / pDOP / pDHP / pDLP / pDV を含む必要がある。"""
    worker = _stubbed(tmp_path)
    captured_urls: list[str] = []

    async def _capture(url: str) -> bytes:
        captured_urls.append(url)
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPrice","sResultCode":"0",'
            '"aCLMMfdsMarketPrice":[{"sIssueCode":"7203","pDPP":"2880",'
            '"pDOP":"2860","pDHP":"2900","pDLP":"2800","pDV":"1234567","tDPP:T":"15:00"}]}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_capture)  # type: ignore[method-assign]
    await worker.fetch_ticker_stats("7203", "stock")

    payload = _parse_url_payload(captured_urls[0])
    codes = set(payload.get("sTargetColumn", "").split(","))
    required = {"pDPP", "pDOP", "pDHP", "pDLP", "pDV"}
    missing = required - codes
    assert not missing, f"sTargetColumn に必須コードが含まれていない: {missing}"


# ---------------------------------------------------------------------------
# Bug 2 + 3: aCLMMfdsMarketPrice キーと FD コードフィールド名で応答を解析する
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_ticker_stats_parses_fd_code_fields_correctly(tmp_path: Path):
    """fetch_ticker_stats は FD コード（pDPP, pDOP 等）でフィールドを読まなければならない。

    実 API は sCurrentPrice や sOpenPrice を返さない。返り値のフィールド名は
    sTargetColumn で指定した FD コードそのものである（マニュアル応答例と
    e_api_get_price_from_file_tel.py サンプル L936 より確定）。
    """
    worker = _stubbed(tmp_path)

    async def _fake_get(url: str) -> bytes:
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPrice","sResultCode":"0",'
            '"aCLMMfdsMarketPrice":['
            '{"sIssueCode":"7203","pDPP":"2880","pDOP":"2860","pDHP":"2900",'
            '"pDLP":"2800","pDV":"1234567","tDPP:T":"15:00"}'
            ']}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]
    stats = await worker.fetch_ticker_stats("7203", "stock")

    assert stats.get("last_price") == "2880", f"last_price (pDPP) が正しく解析されていない: {stats}"
    assert stats.get("open") == "2860", f"open (pDOP) が正しく解析されていない: {stats}"
    assert stats.get("high") == "2900", f"high (pDHP) が正しく解析されていない: {stats}"
    assert stats.get("low") == "2800", f"low (pDLP) が正しく解析されていない: {stats}"
    assert stats.get("volume") == "1234567", f"volume (pDV) が正しく解析されていない: {stats}"


# ---------------------------------------------------------------------------
# Bug 1 (depth): sTargetColumn が fetch_depth_snapshot のペイロードに必要
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_depth_snapshot_includes_sTargetColumn_in_request(tmp_path: Path):
    """fetch_depth_snapshot も CLMMfdsGetMarketPrice を使い、sTargetColumn が必要。"""
    worker = _stubbed(tmp_path)
    captured_urls: list[str] = []

    async def _capture(url: str) -> bytes:
        captured_urls.append(url)
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPrice","sResultCode":"0",'
            '"aCLMMfdsMarketPrice":[{"sIssueCode":"7203",'
            '"pGBP1":"2878","pGBV1":"100","pGBP2":"2877","pGBV2":"200",'
            '"pGAP1":"2882","pGAV1":"150","pGAP2":"2883","pGAV2":"300",'
            '"pGBP3":"","pGBV3":"","pGBP4":"","pGBV4":"",'
            '"pGBP5":"","pGBV5":"","pGBP6":"","pGBV6":"",'
            '"pGBP7":"","pGBV7":"","pGBP8":"","pGBV8":"",'
            '"pGBP9":"","pGBV9":"","pGBP10":"","pGBV10":"",'
            '"pGAP3":"","pGAV3":"","pGAP4":"","pGAV4":"",'
            '"pGAP5":"","pGAV5":"","pGAP6":"","pGAV6":"",'
            '"pGAP7":"","pGAV7":"","pGAP8":"","pGAV8":"",'
            '"pGAP9":"","pGAV9":"","pGAP10":"","pGAV10":""}]}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_capture)  # type: ignore[method-assign]
    await worker.fetch_depth_snapshot("7203", "stock")

    assert captured_urls, "HTTP GET が呼ばれなかった"
    payload = _parse_url_payload(captured_urls[0])
    assert "sTargetColumn" in payload, (
        "sTargetColumn が fetch_depth_snapshot の CLMMfdsGetMarketPrice リクエストに含まれていない。"
    )


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_parses_fd_code_bid_ask_fields(tmp_path: Path):
    """fetch_depth_snapshot は pGBP{i}/pGBV{i}/pGAP{i}/pGAV{i} でフィールドを読む必要がある。"""
    worker = _stubbed(tmp_path)

    async def _fake_get(url: str) -> bytes:
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPrice","sResultCode":"0",'
            '"aCLMMfdsMarketPrice":[{"sIssueCode":"7203",'
            '"pGBP1":"2878","pGBV1":"100","pGBP2":"2877","pGBV2":"200",'
            '"pGAP1":"2882","pGAV1":"150","pGAP2":"2883","pGAV2":"300",'
            '"pGBP3":"","pGBV3":"","pGBP4":"","pGBV4":"",'
            '"pGBP5":"","pGBV5":"","pGBP6":"","pGBV6":"",'
            '"pGBP7":"","pGBV7":"","pGBP8":"","pGBV8":"",'
            '"pGBP9":"","pGBV9":"","pGBP10":"","pGBV10":"",'
            '"pGAP3":"","pGAV3":"","pGAP4":"","pGAV4":"",'
            '"pGAP5":"","pGAV5":"","pGAP6":"","pGAV6":"",'
            '"pGAP7":"","pGAV7":"","pGAP8":"","pGAV8":"",'
            '"pGAP9":"","pGAV9":"","pGAP10":"","pGAV10":""}]}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]
    result = await worker.fetch_depth_snapshot("7203", "stock")

    bids = result.get("bids", [])
    asks = result.get("asks", [])
    assert len(bids) >= 2, f"bid が 2 件以上取得できるはずだが: {bids}"
    assert len(asks) >= 2, f"ask が 2 件以上取得できるはずだが: {asks}"
    # bid: 高値が先（降順）
    bid_prices = [b["price"] for b in bids if b.get("price")]
    assert "2878" in bid_prices, f"最良買気配 pGBP1=2878 が含まれていない: {bids}"
    ask_prices = [a["price"] for a in asks if a.get("price")]
    assert "2882" in ask_prices, f"最良売気配 pGAP1=2882 が含まれていない: {asks}"
    # H1: last_update_id と recv_ts_ms が非ゼロであること
    assert result.get("last_update_id", 0) > 0, (
        f"last_update_id が 0 — 一意な非ゼロ値（recv_ts_ms）を使うべき: {result}"
    )
    assert result.get("recv_ts_ms", 0) > 0, (
        f"recv_ts_ms が 0 — 現在時刻（ms）を使うべき: {result}"
    )


# ---------------------------------------------------------------------------
# H1 (empty): aCLMMfdsMarketPrice が空のとき last_update_id/recv_ts_ms が非ゼロ、警告ログが出る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_empty_response_has_nonzero_ts_and_warns(tmp_path):
    """aCLMMfdsMarketPrice が空のとき recv_ts_ms > 0、last_update_id > 0、warning ログが出る。

    H1: 空レスポンスでも recv_ts_ms = 0 を返さない。
    M1: aCLMMfdsMarketPrice が空のとき log.warning を出す。
    """
    import logging
    from unittest.mock import patch as _patch

    worker = _stubbed(tmp_path)

    async def _fake_get(url: str) -> bytes:
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPrice","sResultCode":"0",'
            '"aCLMMfdsMarketPrice":[]}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_fake_get)

    warnings: list[str] = []

    class _CaptureLogs(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    handler = _CaptureLogs()
    logger = logging.getLogger("engine.exchanges.tachibana")
    logger.addHandler(handler)
    try:
        result = await worker.fetch_depth_snapshot("7203", "stock")
    finally:
        logger.removeHandler(handler)

    assert result.get("recv_ts_ms", 0) > 0, (
        f"空レスポンス時に recv_ts_ms が 0 — 現在時刻を使うべき: {result}"
    )
    assert result.get("last_update_id", 0) > 0, (
        f"空レスポンス時に last_update_id が 0 — recv_ts_ms を使うべき: {result}"
    )
    assert any("empty" in w or "fetch_depth_snapshot" in w for w in warnings), (
        f"aCLMMfdsMarketPrice 空のとき warning ログが出ていない: {warnings}"
    )
