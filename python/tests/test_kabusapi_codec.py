"""kabusapi_codec: UTF-8 JSON encode/decode と SJIS 拒否テスト。"""
import pytest

from engine.exchanges.kabusapi_codec import (
    SIDE_BUY,
    SIDE_SELL,
    decode,
    encode,
)


@pytest.mark.demo_kabu
def test_encode_decode_roundtrip():
    payload = {"Symbol": "9433", "Exchange": 1, "CurrentPrice": 2479.0}
    assert decode(encode(payload)) == payload


@pytest.mark.demo_kabu
def test_decode_utf8_string():
    json_str = '{"Symbol": "9433", "Price": 100.0}'
    result = decode(json_str)
    assert result["Symbol"] == "9433"


@pytest.mark.demo_kabu
def test_decode_rejects_sjis_bytes():
    """SJIS バイト列は UnicodeDecodeError を raise する (INV-K2-SJIS-REJECT)。"""
    sjis_bytes = "テスト".encode("shift_jis")
    with pytest.raises(UnicodeDecodeError):
        decode(sjis_bytes)


@pytest.mark.demo_kabu
def test_side_mapping_kabu_buy_is_2_not_3():
    """kabu の買い区分は "2" であり、立花の "3" とは異なる (INV-K2-SIDE-BUY-2, comparison.md §4)."""
    assert SIDE_BUY == "2", "kabu buy side must be '2', not '3' (tachibana)"
    assert SIDE_SELL == "1"


@pytest.mark.demo_kabu
def test_encode_japanese_name():
    """日本語文字列は ensure_ascii=False で UTF-8 エンコードされる。"""
    payload = {"SymbolName": "新日鐵住金"}
    encoded = encode(payload)
    assert isinstance(encoded, bytes)
    decoded = decode(encoded)
    assert decoded["SymbolName"] == "新日鐵住金"
