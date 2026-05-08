---
title: kabuステーション API 外部仕様リファレンス
status: migrated
migrated_from:
  - docs/✅kabusapi/architecture.md
  - docs/✅kabusapi/data-mapping.md
source_commit: ea5022b
---

# kabuステーション API 外部仕様リファレンス

本ファイルは kabuステーション API（v1.5）の **外部仕様**（エンドポイント / レスポンス型 / エラーコード / ドメイン用語）と、それを本アプリの内部 IPC DTO へどう写像するかを扱う。内部アダプタ実装は `docs/architecture/modules/kabusapi-adapter.md` を参照。

## 1. 接続先 / 認証

| 項目 | 値 |
| :--- | :--- |
| 検証環境 base URL | `http://localhost:18081` |
| 本番環境 base URL | `http://localhost:18080`（`KABU_ALLOW_PROD=1` opt-in 必須） |
| WebSocket 検証 | `ws://localhost:18081/kabusapi/websocket` |
| WebSocket 本番 | `ws://localhost:18080/kabusapi/websocket` |
| 前提 | kabuステーション本体（Windows GUI）が起動していること |
| ヘッダ | 全リクエストに `X-API-KEY: <token>` 付与 |
| トークン取得 | `POST /token` （body: API パスワード）|
| URL リテラル所在 | Python `kabusapi_url.py` 1 箇所のみ（CI lint 強制） |

CI 制約: kabuステーション本体は Windows 限定 → CI で本物 API は叩けない。`pytest-httpx` (HTTPXMock) + WebSocket mock のみ。

## 2. venue / market / exchange の追加

```rust
// exchange/src/adapter.rs に追加
pub enum Venue {
    // ... 既存 ...
    KabuStation,  // ← 追加（IPC 文字列: "kabu_station"）
}

pub enum Exchange {
    // ... 既存 15 ...
    KabuStationStock,  // ← 追加（Phase 1 = 東証のみ。市場細分化は Phase 3）
}
```

- `MarketKind::Stock` は既に存在する（立花 venue で追加済み）。kabu でも同じ variant を使う
- IPC では `venue: "kabu_station"` / exchange の Display 文字列で区別
- Phase 1 は `KabuStationStock` 1 variant のみ。市場細分化（東証=1/名証=3/福証=5/札証=6）は Phase 3

## 3. ticker

| 項目 | kabu | 本アプリ |
| :--- | :--- | :--- |
| 銘柄コード | `Symbol`（4-9 桁、例 `"9433"`） | `Ticker` の symbol 部分にそのまま入れる（ASCII 数字） |
| 市場コード | `Exchange`（`1`=東証）| Phase 1 は東証固定。`KabuStationStock` に対応 |
| 銘柄名 | `SymbolName`（例 `"新日鐵住金"`）| IPC `TickerInfo.tickers[*]` に `display_name_ja` キーで詰めて運ぶ |
| 複合キー | `Symbol@Exchange` 形式（例 `"5401@1"`）| URL パスパラメータに使う（`kabusapi_url.symbol_key()` で生成） |

`Ticker::new("9433", Exchange::KabuStationStock)` のように通す。

## 4. 板・気配（PUSH: PushBoardSuccess → DepthSnapshot）

kabuステーション PUSH は WebSocket frame 1 つ = 1 JSON（UTF-8）で届く。

```json
{
  "Symbol": "5401",
  "SymbolName": "新日鐵住金",
  "Exchange": 1,
  "CurrentPrice": 2479.0,
  "CurrentPriceTime": "2020-07-22T15:00:00+09:00",
  "BidPrice": 2479.0, "BidQty": 100,
  "AskPrice": 2480.0, "AskQty": 200,
  "Sell1": {"Price": 2480.0, "Qty": 200, "Time": "...", "Sign": "0101"},
  "Buy1":  {"Price": 2479.0, "Qty": 100, ...},
  ...
}
```

`PushBoardSuccess` → `DepthSnapshot` マッピング:

| PushBoardSuccess フィールド | DepthSnapshot フィールド | 備考 |
| :--- | :--- | :--- |
| `Sell1..Sell10` の Price/Qty | `asks[]` | 売り気配 10 本 |
| `Buy1..Buy10` の Price/Qty | `bids[]` | 買い気配 10 本 |
| `CurrentPrice` | `last_price` 相当 | IPC 型に合わせて送出 |
| `CurrentPriceTime` | `timestamp_ms` | ISO 8601 JST → ms 変換 |
| `Symbol` + `Exchange` | `ticker` | `KabuStationStock` で composite key |

`BidPrice` / `AskPrice`（最良気配 1 本）と `Sell1` / `Buy1` は重複するが、
`Sell1..Sell10` / `Buy1..Buy10` の 10 本配列で DepthSnapshot を構築する。

## 5. trade（直近約定）

kabuステーション PUSH には `CurrentPrice` / `CurrentPriceTime` が含まれる。
立花の FD frame とは異なり、累積出来高（`TradingVolume`）から差分を合成する手法は kabu でも使える。

| PushBoardSuccess フィールド | Trade フィールド | 備考 |
| :--- | :--- | :--- |
| `CurrentPrice` | `price` | |
| `TradingVolume` 差分 | `qty` | 前 frame との差分。差分 ≤ 0 は trade 発火しない |
| `CurrentPriceTime` | `ts_ms` | ISO 8601 JST → ms 変換。無ければ受信時刻 |
| — | `side` | Quote rule（前 frame の BidPrice/AskPrice と比較）|

**Quote rule**:
- `CurrentPrice >= prev_ask` → `buy`
- `CurrentPrice <= prev_bid` → `sell`
- 中値ぴったり → `buy` 既定（warn ログ）

初回 frame（prev_quote=None）は trade 発火しない。`TradingVolume` リセット時も初回扱い。

## 6. 余力・注文照会

Phase 1 は読取のみ。GET エンドポイントのレスポンスを既存 IPC 型にマッピング。

| API | マッピング先 IPC |
| :--- | :--- |
| `GET /wallet/cash` | `GetBuyingPower` レスポンス（現物余力） |
| `GET /wallet/margin` | `GetBuyingPower` レスポンス（信用余力） |
| `GET /orders` | `GetOrderList` レスポンス |
| `GET /positions` | `GetPositions` レスポンス |

## 7. エラーコード → 例外 マッピング

| HTTP Status | body Code | Python 例外 |
| :--- | :--- | :--- |
| 401 | 4001001 (not logged in) | `KabuTokenExpiredError` |
| 401 | 4001003 (invalid APIPassword) | `KabuApiError` |
| 401 | 4001005 (token expired) | `KabuTokenExpiredError` |
| 429 / 4xx | 4002001 (register full) | `KabuRegisterFullError` |
| 429 / 4xx | 4002006 (rate limit exceeded) | `KabuRateLimitError` |
| (TCP) | ConnectionRefusedError | `KabuConnectionError` |

`4001001` と `4001005` は**どちらも** `KabuTokenExpiredError` を発火する（U35）。

## 8. symbol_key 複合キー形式

kabu API の URL パスパラメータ形式: `{symbol}@{exchange}`（例 `"5401@1"`）

```python
# kabusapi_url.py
def symbol_key(symbol: str, exchange: int) -> str:
    return f"{symbol}@{exchange}"
```

リクエスト body 内では `Symbol` と `Exchange` を別フィールドで持つ。
URL パスパラメータ用の複合キーは `kabusapi_url.symbol_key()` で生成し、文字列連結を散らさない。

## 9. 売買区分の違い（regression guard）

| 区分 | 立花 | kabu |
| :--- | :--- | :--- |
| 売 | `"1"` | `"1"` |
| 買 | `"3"` | `"2"` |

kabu で買いを `"3"` にすると売り注文になる。
`test_kabusapi_codec.py::test_side_mapping_kabu_buy_is_2_not_3` で regression guard を設置。

Phase 1 は発注しないため実害は無いが、Phase 2 着手前にこの違いを知っておくこと。

## 10. PUSH 銘柄登録上限

PUSH 銘柄は REST/PUSH 合算 **50 銘柄上限**。51 件目で `KabuRegisterFullError`。
`GET /board` が自動的に PUSH 登録を発火する仕様（SKILL R6）に注意。
詳細は `docs/architecture/modules/kabusapi-adapter.md` §5 RegisterSet。

## 11. 流量制限

- 発注系: 5 req/sec
- 余力系: 10 req/sec
- 情報系: 10 req/sec

サーバから `4002006` が返ったらバックオフリトライ。

## 12. 一次資料

- 公式 OpenAPI: `.claude/skills/kabusapi/reference/kabu_STATION_API.yaml`
- ポータル: `.claude/skills/kabusapi/ptal/`（howto / push / error / faq）
- Python サンプル: `.claude/skills/kabusapi/sample/Python/`
- コーディング規約・運用ルール: `.claude/skills/kabusapi/SKILL.md`
