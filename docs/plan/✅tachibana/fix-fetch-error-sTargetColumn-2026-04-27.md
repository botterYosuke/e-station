# 修正計画: Fetch error -1 (sTargetColumn NULL) 2026-04-27

## 状況: 実装完了・review-fix-loop 実施中（2026-04-27）

## 1. エラーの概要

```
Fetch error: Tachibana: -1: 引数（sTargetColumn:[NULL]）エラー。
```

p_no 衝突バグ（error 6）修正後も本エラーが再現する独立した不具合。

## 2. 根本原因（コード調査による確定）

### Bug 1 (確定・Primary): `sTargetColumn` 欠如

`CLMMfdsGetMarketPrice` API は `sTargetColumn` を**必須**パラメータとして要求するが、
`fetch_ticker_stats` と `fetch_depth_snapshot` の両方でペイロードから欠落している。

**根拠**: サンプルコード `e_api_get_price_from_file_tel.py` L761:
```
"sCLMID":"CLMMfdsGetMarketPrice","sTargetIssueCode":"6501","sTargetColumn":"pDPP,tDPP:T,pPRP","sJsonOfmt":"5"
```

### Bug 2 (確定): スキーマのレスポンスキー名誤り

| | コード | 実際の API |
|---|---|---|
| レスポンスキー | `aCLMMfdsMarketPriceData` | `aCLMMfdsMarketPrice` |

**根拠**: サンプルコード L936:
```python
list_return = dic_return.get('aCLMMfdsMarketPrice')
```

マニュアル `mfds_json_api_ref_text.html` L6428 の応答例も同じ。

### Bug 3 (確定): `fetch_ticker_stats` のフィールド名誤り

| 現在のコード | 正しい FD コード |
|---|---|
| `sCurrentPrice` | `pDPP` |
| `sOpenPrice` | `pDOP` |
| `sHighPrice` | `pDHP` |
| `sLowPrice` | `pDLP` |
| `sVolume` | `pDV` |
| `sCurrentPriceTime` | `tDPP:T` |

**根拠**: マニュアル `mfds_json_api_ref_text.html` §CLMMfdsGetMarketPrice 応答例 +
`inventory-T0.md §11.2.b` FD 情報コード表

### Bug 4 (確定): `fetch_depth_snapshot` のフィールド名誤り

| 現在のコード | 正しい FD コード |
|---|---|
| `sGBP_{i}` / `sGBP{i}` | `pGBP1`..`pGBP10` |
| `sGBV_{i}` / `sGBV{i}` | `pGBV1`..`pGBV10` |
| `sGAP_{i}` / `sGAP{i}` | `pGAP1`..`pGAP10` |
| `sGAV_{i}` / `sGAV{i}` | `pGAV1`..`pGAV10` |

**根拠**: `inventory-T0.md §11.2.b` 確定コード表 + xlsx サンプル

## 3. 修正内容

### 3.1 `python/engine/schemas.py`
- `MarketPriceResponse.aCLMMfdsMarketPriceData` → `aCLMMfdsMarketPrice`

### 3.2 `python/engine/exchanges/tachibana.py` — `fetch_ticker_stats`
- `sTargetColumn` を `"pDPP,pDOP,pDHP,pDLP,pDV,tDPP:T"` でペイロードに追加
- レスポンスのフィールド名を FD コードに修正:
  - `sCurrentPrice` → `pDPP`
  - `sOpenPrice` → `pDOP`
  - `sHighPrice` → `pDHP`
  - `sLowPrice` → `pDLP`
  - `sVolume` → `pDV`
  - `sCurrentPriceTime` → `tDPP:T`

### 3.3 `python/engine/exchanges/tachibana.py` — `fetch_depth_snapshot`
- `sTargetColumn` を 気配 10 本分のコードでペイロードに追加:
  `"pGBP1,pGBV1,pGBP2,pGBV2,...,pGBP10,pGBV10,pGAP1,pGAV1,...,pGAP10,pGAV10"`
- フィールド名を `pGBP{i}` / `pGBV{i}` / `pGAP{i}` / `pGAV{i}` に修正

### 3.4 `python/engine/exchanges/tachibana.py` — `_polling_fetch_depth`
- 同様に `CLMMfdsGetMarketPrice` を呼ぶ全箇所に `sTargetColumn` を追加

### 3.5 `python/tests/test_tachibana_schemas.py`
- `aCLMMfdsMarketPriceData` → `aCLMMfdsMarketPrice` に更新

## 4. なぜ既存テストで発見できなかったか

| テスト | 見逃した理由 |
|--------|------------|
| `test_tachibana_schemas.py` | `aCLMMfdsMarketPriceData` というキー名を前提に書かれており、実 API が `aCLMMfdsMarketPrice` を返すことを検証していない |
| `test_server_dispatch.py` 等 | `httpx_mock` でレスポンスを固定しているため、実 API の必須パラメータ検証を通過しない |
| 実機テスト | `pytest -m demo_tachibana` が CI で実走しない（手動レーン） |

## 5. Acceptance criteria 対応

- ✅ 1. p_no 修正後に -1 エラーが再現：**再現した**（独立バグと確認）
- ✅ 2. `sTargetColumn` を要求するエンドポイント特定: `CLMMfdsGetMarketPrice`（fetch_ticker_stats / fetch_depth_snapshot / _polling_fetch_depth）
- ✅ 3. 正しい payload との差分記載（上記 §2）
- ✅ 4. RED テスト追加・FAIL 確認 (`test_tachibana_market_price_payload.py` 5 テスト、全 FAIL 実証)
- ✅ 5. GREEN: 最小修正で PASS (schemas.py + tachibana.py 修正、790 テスト全通過)
- ✅ 6. REFACTOR: 不要なフォールバック (`sGBP_{i}` 等) 除去・コメント整理済み
- [ ] 7. review-fix-loop
- ✅ 8. /bug-postmortem (MISSES.md に「API 仕様固定なし」パターンを追記)
- ✅ 9. リグレッション確認: 修正前 5 FAIL → 修正後 5 PASS を自動スクリプトで実証

## レビュー反映 (2026-04-27, ラウンド 1)

### 解消した指摘
- ✅ SFH-M1: `_row_to_kline` None 返却時にデバッグログを追加（呼び出し元ループで `sDate` を記録）
- ✅ SFH-M2: `fetch_klines` で非 dict 応答時に `TachibanaError(parse_error)` を即 raise（`check_response` スキップ排除）
- ✅ GP-M2: `_row_to_kline` で OHLC フィールドが空文字の行を `None` 返却でスキップ

### 持ち越し（既存挙動・今回修正スコープ外）
- SFH-H1: `fetch_depth_snapshot` が `session=None` で `{}` 返却（`fetch_klines` と非対称）— 既存テスト `test_fetch_depth_snapshot_returns_empty_dict_when_session_is_none` が承認済み設計のため保留
- SFH-M3: `stream_trades`/`stream_depth` の `session=None` silent return — 今回スコープ外

### GP-M1: NOT A BUG（設計上の差異）
`_row_to_kline` の `open_time_ms` は「日足開始時刻 = 0:00 JST」、`data_loader.py` の `_date_to_ts_ns` は「大引け = 15:30 JST」を `ts_event` として使う別用途。バグではない。

## レビュー反映 (2026-04-27, ラウンド 2)

### 解消した指摘
- ✅ R2-H1: `_row_to_kline` の OHLC ガードに `volume_v` を追加（`pDV=""` を Python 側でスキップし Rust 側 silent drop を防止）
- ✅ R2-M-A: `test_fetch_klines_skips_row_with_empty_ohlc_field` に `pDV` ケースを追加
- ✅ R2-M-B: ログ assert に `"sDate=" in r.message` を追加してフォーマットを固定

### 残存 LOW（対応不要）
- R2-L1: `test_fetch_klines_accepts_1d` の Shift-JIS encode にコメント不足（機能影響なし）
