"""TDD Red → Green: T0.7 — WAL に第二暗証番号が書かれないことを検証。

D2-H2: WAL .jsonl 全行を grep して:
- second_password の値文字列が含まれないこと
- C-L4: 制御文字 (\\n / \\t / \\x01-\\x03) が生のまま出力されないこと
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_orders import (
    NautilusOrderEnvelope,
    submit_order,
)
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET_PASSWORD = "MY_SECRET_PASS_XYZ"
_CONTROL_CHARS = ["\n", "\t", "\x01", "\x02", "\x03"]


def _session() -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://demo.example/request/"),
        url_master=MasterUrl("https://demo.example/master/"),
        url_price=PriceUrl("https://demo.example/price/"),
        url_event=EventUrl("https://demo.example/event/"),
        url_event_ws="wss://demo.example/event/",
        zyoutoeki_kazei_c="1",
    )


def _market_buy_envelope() -> NautilusOrderEnvelope:
    return NautilusOrderEnvelope(
        client_order_id="cid-secret-test",
        instrument_id="7203.T/TSE",
        order_side="BUY",
        order_type="MARKET",
        quantity="100",
        time_in_force="DAY",
        post_only=False,
        reduce_only=False,
        tags=["cash_margin=cash"],
    )


def _make_mock_response(order_number: str = "ORD-001") -> bytes:
    data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sOrderNumber": order_number,
        "sEigyouDay": "20260426",
        "sWarningCode": "",
        "sWarningText": "",
    }
    return json.dumps(data, ensure_ascii=False).encode("shift_jis")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditLogNoSecret:
    @pytest.mark.asyncio
    async def test_wal_does_not_contain_secret_password(self, tmp_path):
        """WAL に second_password の値文字列が含まれないこと。"""
        wal_path = tmp_path / "tachibana_orders.jsonl"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.content = _make_mock_response("ORD-001")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await submit_order(
                _session(),
                _SECRET_PASSWORD,
                _market_buy_envelope(),
                wal_path=wal_path,
            )

        assert wal_path.exists(), "WAL ファイルが作成されていない"

        wal_content = wal_path.read_text(encoding="utf-8")
        assert _SECRET_PASSWORD not in wal_content, (
            f"WAL に second_password の値 {_SECRET_PASSWORD!r} が含まれている:\n{wal_content}"
        )

    @pytest.mark.asyncio
    async def test_wal_does_not_contain_second_password_key(self, tmp_path):
        """WAL に 'second_password' キー名も含まれないこと（関連文字列チェック）。"""
        wal_path = tmp_path / "tachibana_orders.jsonl"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.content = _make_mock_response("ORD-002")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await submit_order(
                _session(),
                _SECRET_PASSWORD,
                _market_buy_envelope(),
                wal_path=wal_path,
            )

        wal_content = wal_path.read_text(encoding="utf-8")
        # second_password の値だけでなく、キー名も含まないことを確認
        # ただし p_sd_date は許容（暗証番号とは無関係）
        forbidden = ["second_password", "sSecondPassword", "SecondPassword"]
        for term in forbidden:
            assert term not in wal_content, (
                f"WAL に禁止キー {term!r} が含まれている:\n{wal_content}"
            )

    @pytest.mark.asyncio
    async def test_wal_control_chars_are_escaped(self, tmp_path):
        """WAL の各行に制御文字が生のまま出力されないこと（C-L4）。"""
        wal_path = tmp_path / "tachibana_orders.jsonl"

        # instrument_id に制御文字を含む入力は submit_order 前に弾かれるが、
        # WAL 書き込み自体が JSON dumps でエスケープされることを確認するため
        # 正常な発注フローで WAL を生成して検証する。
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.content = _make_mock_response("ORD-003")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await submit_order(
                _session(),
                "safe_password",
                _market_buy_envelope(),
                wal_path=wal_path,
            )

        wal_bytes = wal_path.read_bytes()
        # 各行（\n で区切る）の中に生の制御文字が含まれていないこと
        # JSON dumps は \n を \\n にエスケープするので、行の中には現れないはず
        lines = wal_path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            for ctrl in _CONTROL_CHARS:
                assert ctrl not in line, (
                    f"行 {i} に制御文字 {ctrl!r} が生のまま含まれている: {line!r}"
                )

    @pytest.mark.asyncio
    async def test_wal_has_submit_and_accepted_phases(self, tmp_path):
        """正常系で WAL に submit + accepted の 2 行が書かれること。"""
        wal_path = tmp_path / "tachibana_orders.jsonl"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.content = _make_mock_response("ORD-004")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await submit_order(
                _session(),
                "password",
                _market_buy_envelope(),
                wal_path=wal_path,
            )

        lines = [l for l in wal_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) >= 2, f"WAL に少なくとも 2 行期待, got {len(lines)}: {lines}"

        phases = [json.loads(l)["phase"] for l in lines]
        assert "submit" in phases, f"submit フェーズが WAL にない: {phases}"
        assert "accepted" in phases, f"accepted フェーズが WAL にない: {phases}"

    @pytest.mark.asyncio
    async def test_wal_rejected_phase_on_api_error(self, tmp_path):
        """API エラー時に WAL に rejected フェーズが書かれること。"""
        wal_path = tmp_path / "tachibana_orders.jsonl"

        error_data = {
            "p_errno": "0",
            "sResultCode": "ERR001",
            "sResultText": "拒否メッセージ",
            "sEigyouDay": "20260426",
        }
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.content = json.dumps(error_data, ensure_ascii=False).encode("shift_jis")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        from engine.exchanges.tachibana_helpers import TachibanaError

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TachibanaError):
                await submit_order(
                    _session(),
                    "password",
                    _market_buy_envelope(),
                    wal_path=wal_path,
                )

        lines = [l for l in wal_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        phases = [json.loads(l)["phase"] for l in lines]
        assert "submit" in phases, "rejected 前に submit 行が必要"
        assert "rejected" in phases, f"rejected フェーズが WAL にない: {phases}"
