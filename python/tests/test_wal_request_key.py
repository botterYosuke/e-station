"""H-E: WAL request_key 伝播テスト

Rust 側で計算した request_key が IPC (SubmitOrderRequest) 経由で Python に届き、
WAL の submit 行に正しく書かれることを確認する。

TDD RED: このテストは実装前に FAIL することを確認してから GREEN にする。
"""

from __future__ import annotations

import io
import json

import pytest

from engine.schemas import SubmitOrderRequest


# ---------------------------------------------------------------------------
# Test 1: SubmitOrderRequest に request_key フィールドが存在する
# ---------------------------------------------------------------------------


class TestSubmitOrderRequestHasRequestKey:
    """SubmitOrderRequest pydantic モデルに request_key フィールドがあること。"""

    def test_request_key_field_exists_with_default_zero(self):
        """request_key は省略時 0 になる（Rust 側からの値がない旧バージョン向けデフォルト）。"""
        req = SubmitOrderRequest(
            client_order_id="abc-123",
            instrument_id="7203.TSE",
            order_side="BUY",
            order_type="MARKET",
            quantity="100",
            time_in_force="DAY",
            post_only=False,
            reduce_only=False,
        )
        assert req.request_key == 0

    def test_request_key_field_accepts_nonzero_value(self):
        """request_key に 0 以外の値を設定できる。"""
        req = SubmitOrderRequest(
            client_order_id="abc-456",
            instrument_id="7203.TSE",
            order_side="BUY",
            order_type="MARKET",
            quantity="100",
            time_in_force="DAY",
            post_only=False,
            reduce_only=False,
            request_key=1234567890,
        )
        assert req.request_key == 1234567890

    def test_request_key_field_is_int_type(self):
        """request_key は int 型であること。"""
        req = SubmitOrderRequest(
            client_order_id="abc-789",
            instrument_id="9984.TSE",
            order_side="SELL",
            order_type="LIMIT",
            quantity="50",
            price="3500",
            time_in_force="DAY",
            post_only=False,
            reduce_only=False,
            request_key=9999999999999999999,  # u64 max range
        )
        assert isinstance(req.request_key, int)
        assert req.request_key == 9999999999999999999

    def test_extra_forbid_still_rejects_unknown_fields(self):
        """extra='forbid' は request_key 追加後も機能する（injection 防止）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SubmitOrderRequest(
                client_order_id="abc",
                instrument_id="7203.TSE",
                order_side="BUY",
                order_type="MARKET",
                quantity="100",
                time_in_force="DAY",
                post_only=False,
                reduce_only=False,
                injected_field="bad",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Test 2: _audit_log_submit が request_key を WAL に書く
# ---------------------------------------------------------------------------


class TestAuditLogSubmitWritesRequestKey:
    """_audit_log_submit() が request_key を WAL の submit 行に書くこと。"""

    def _call_audit_log_submit(
        self,
        buf: io.StringIO,
        *,
        request_key: int = 0,
    ) -> dict:
        from engine.exchanges.tachibana_orders import _audit_log_submit

        _audit_log_submit(
            buf,
            client_order_id="test-cid-001",
            request_key=request_key,
            instrument_id="7203.TSE",
            order_side="BUY",
            order_type="MARKET",
            quantity="100",
        )
        line = buf.getvalue().strip()
        return json.loads(line)

    def test_default_request_key_zero_written_to_wal(self):
        """request_key=0 のとき WAL に 0 が書かれる。"""
        buf = io.StringIO()

        # io.StringIO は fileno() を持たないので fsync を回避するモック経由で呼ぶ
        # _audit_log_submit は f.flush() / os.fsync(f.fileno()) を呼ぶ
        # → StringIO では fileno() が OSError を raise するため buf を wrap する
        class _FakeFd(io.StringIO):
            def fileno(self) -> int:  # type: ignore[override]
                return -1  # fsync 不要のフラグ値

            def flush(self) -> None:
                pass  # no-op

        fake = _FakeFd()
        from engine.exchanges.tachibana_orders import _audit_log_submit

        # fsync をモック: os.fsync(-1) は実際は呼ばせたくない
        import unittest.mock as mock

        with mock.patch("os.fsync"):
            _audit_log_submit(
                fake,
                client_order_id="test-cid-001",
                request_key=0,
                instrument_id="7203.TSE",
                order_side="BUY",
                order_type="MARKET",
                quantity="100",
            )

        record = json.loads(fake.getvalue().strip())
        assert record["request_key"] == 0

    def test_nonzero_request_key_written_correctly(self):
        """request_key=1234567890 の値が WAL に正しく書かれる。"""
        import unittest.mock as mock

        class _FakeFd(io.StringIO):
            def fileno(self) -> int:  # type: ignore[override]
                return -1

            def flush(self) -> None:
                pass

        fake = _FakeFd()
        from engine.exchanges.tachibana_orders import _audit_log_submit

        with mock.patch("os.fsync"):
            _audit_log_submit(
                fake,
                client_order_id="test-cid-002",
                request_key=1234567890,
                instrument_id="9984.TSE",
                order_side="SELL",
                order_type="LIMIT",
                quantity="50",
            )

        record = json.loads(fake.getvalue().strip())
        assert record["request_key"] == 1234567890, (
            f"Expected 1234567890, got {record['request_key']!r}"
        )
        assert record["phase"] == "submit"
        assert record["client_order_id"] == "test-cid-002"

    def test_large_u64_request_key_written_correctly(self):
        """u64 最大値相当の request_key が WAL に正しく書かれる（overflow なし）。"""
        import unittest.mock as mock

        class _FakeFd(io.StringIO):
            def fileno(self) -> int:  # type: ignore[override]
                return -1

            def flush(self) -> None:
                pass

        fake = _FakeFd()
        from engine.exchanges.tachibana_orders import _audit_log_submit

        large_key = 18446744073709551615  # 2^64 - 1
        with mock.patch("os.fsync"):
            _audit_log_submit(
                fake,
                client_order_id="test-cid-003",
                request_key=large_key,
                instrument_id="7203.TSE",
                order_side="BUY",
                order_type="MARKET",
                quantity="200",
            )

        record = json.loads(fake.getvalue().strip())
        assert record["request_key"] == large_key


# ---------------------------------------------------------------------------
# Test 3: submit_order が request_key 引数を受け取り WAL に渡す
# ---------------------------------------------------------------------------


class TestSubmitOrderPassesRequestKeyToWal:
    """submit_order() に request_key を渡すと WAL の submit 行に反映される。"""

    @pytest.mark.asyncio
    async def test_submit_order_writes_request_key_to_wal(self, tmp_path):
        """submit_order(request_key=42) → WAL に request_key=42 が書かれる。"""
        import unittest.mock as mock

        from engine.exchanges.tachibana_orders import NautilusOrderEnvelope, submit_order

        wal_path = tmp_path / "test_orders.wal"

        # session モック
        session_mock = mock.MagicMock()
        session_mock.url_request = "http://demo.example.com/request"
        session_mock.zyoutoeki_kazei_c = "1"

        envelope = NautilusOrderEnvelope(
            client_order_id="test-rk-cid",
            instrument_id="7203.TSE",
            order_side="BUY",
            order_type="MARKET",
            quantity="100",
            time_in_force="DAY",
            post_only=False,
            reduce_only=False,
            tags=["cash_margin=cash", "account_type=specific"],
        )

        # HTTP 送信とURL構築をすべてモックしてWAL書き込みのみ観察する
        with (
            mock.patch("engine.exchanges.tachibana_orders._envelope_to_wire") as mock_wire,
            mock.patch(
                "engine.exchanges.tachibana_orders._compose_request_payload"
            ) as mock_payload,
            mock.patch(
                "engine.exchanges.tachibana_url.build_request_url", return_value="http://fake"
            ),
            mock.patch("engine.exchanges.tachibana_url.guard_prod_url"),
            mock.patch("os.fsync"),
            mock.patch("httpx.AsyncClient") as mock_client_cls,
        ):
            # wire モック
            mock_wire_obj = mock.MagicMock()
            mock_wire_obj.account_type = "1"
            mock_wire_obj.issue_code = "7203"
            mock_wire_obj.market_code = "00"
            mock_wire_obj.side = "3"
            mock_wire_obj.condition = "0"
            mock_wire_obj.price = "0"
            mock_wire_obj.qty = "100"
            mock_wire_obj.cash_margin = "0"
            mock_wire_obj.expire_day = "0"
            mock_wire_obj.second_password = "dummy"
            mock_wire_obj.gyakusasi_zyouken = "0"
            mock_wire_obj.gyakusasi_price = "*"
            mock_wire_obj.gyakusasi_order_type = "0"
            mock_wire_obj.tatebi_type = "*"
            mock_wire_obj.tategyoku_id = None
            mock_wire.return_value = mock_wire_obj

            # payload モック
            mock_payload.return_value = {
                "p_no": "12345",
                "sSecondPassword": "dummy",
            }

            # HTTP レスポンスモック
            mock_response = mock.MagicMock()
            mock_response.content = b'{"sCLMID":"CLMKabuNewOrder","sResultCode":"0","sOrderNumber":"ORD001"}'
            mock_client = mock.AsyncMock()
            mock_client.__aenter__ = mock.AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = mock.AsyncMock(return_value=False)
            mock_client.post = mock.AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            # decode_response_body と check_response をモック
            with (
                mock.patch(
                    "engine.exchanges.tachibana_codec.decode_response_body",
                    return_value={"sCLMID": "CLMKabuNewOrder", "sResultCode": "0", "sOrderNumber": "ORD001"},
                ),
                mock.patch("engine.exchanges.tachibana_helpers.check_response"),
            ):
                try:
                    await submit_order(
                        session=session_mock,
                        second_password="dummy_pass",
                        order=envelope,
                        request_key=42,
                        wal_path=wal_path,
                    )
                except Exception:
                    pass  # WAL 書き込み後の処理エラーは無視

        # WAL ファイルが作成され、request_key=42 が書かれていることを確認
        assert wal_path.exists(), "WAL file must be created"
        lines = [
            line for line in wal_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        assert len(lines) >= 1, "At least one WAL line must be written"

        submit_record = json.loads(lines[0])
        assert submit_record["phase"] == "submit"
        assert submit_record["request_key"] == 42, (
            f"Expected request_key=42 in WAL, got {submit_record.get('request_key')!r}"
        )
