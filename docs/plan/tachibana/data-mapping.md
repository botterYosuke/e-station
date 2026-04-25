# 立花証券統合: データモデル・マッピング

立花証券のドメイン概念を、既存の `engine-client` IPC DTO（[engine-client/src/dto.rs](../../../engine-client/src/dto.rs)）と `exchange` 型（[exchange/src/lib.rs](../../../exchange/src/lib.rs)）に押し込めるための写像表。「合わない」場所は **拡張する** か **明示的に未対応とする** かを決める。

## 1. venue / market の追加

```rust
// exchange/src/adapter.rs
pub enum Venue {
    Bybit, Binance, Hyperliquid, Okex, Mexc,
    Tachibana,   // ← 追加
}

pub enum MarketKind {
    Spot, LinearPerps, InversePerps,
    Stock,       // ← 追加（日本株現物・信用を一括して扱う）
}

pub enum Exchange {
    // ... 既存 14 ...
    TachibanaStock,   // ← 追加
}
```

- `MarketKind::Stock::qty_in_quote_value` は `price * qty`（quote = JPY）。`size_in_quote_ccy` は常に false 扱い
- 信用区分（`sGenkinShinyouKubun`）は **MarketKind では区別しない**（読み取り専用 Phase 1 ではチャート上区別不要）。発注時に別パラメータとして渡す（Phase 2）
- IPC では `venue: "tachibana"` / `market: "stock"` を文字列で使う

## 2. ticker

| 項目 | 立花 | 本アプリ |
| :--- | :--- | :--- |
| 銘柄コード | `sIssueCode` 4 桁数字 / 5 桁数字 / **末尾英字を含む 5 桁英数字**（新興市場の優先出資証券・新株予約権付社債で `130A0` のような表記あり） | `Ticker` の symbol 部分にそのまま入れる（ASCII 英数字のみ） |
| 表示名 | 銘柄マスタの銘柄名（Shift-JIS 漢字） | `TickerInfo.display_symbol` に Unicode で格納（[exchange/src/lib.rs L256](../../../exchange/src/lib.rs#L256) 周辺の Hyperliquid 流儀に合わせる） |
| 市場コード | `sSizyouC`（`00`=東証） | Phase 1 は東証固定 |
| 売買単位 | 銘柄マスタ `sTatebaTanniSuu` | `TickerInfo.lot_size` 相当（新規プロパティ追加要） |
| 呼値単位 | マスタ「呼値」テーブル（価格帯依存） | `Price` の min_ticksize で表現。**価格帯ごとに変わる** ため固定 1 値では足りない（§5 参照） |

`Ticker::new("7203")` のような文字列パスで素直に通る。ASCII 制約は既存ロジックでクリア（最近の commit `6d39f13` で非 ASCII ティッカーは除外済み）。**ただし `130A0` 等の英字混在 5 桁 ticker** がマスタダウンロード結果に含まれた際に既存 `Ticker::new` が許容するかは T4 受け入れ条件で確認すること（数字以外を許容しないバリデーションが入っている可能性あり）。

## 3. trade（FD frame からの合成）

立花にはミリ秒単位のテープデータ API は存在しない。代わりに EVENT の **FD frame**（時価情報）が変化分のみ来る。Phase 1 では下記をもって "trade" とみなす:

| 立花 FD フィールド | 意味 | TradeMsg |
| :--- | :--- | :--- |
| `p_<行>_DPP` | 現在値 | `price` |
| `p_<行>_DV` | 出来高（累積） | 前回値との差分を `qty` に |
| `p_<行>_DPP_TIME` または `p_<行>_DDT` | 時刻 | `ts_ms`（JST → ms） |
| 直前の bid/ask 比較 | — | `side`（"buy"/"sell" を価格と気配から推定） |

- **不正確になりうるトレードオフ**: 立花の FD は出来高が累積で、複数約定が 1 frame に集約されることがある。Phase 1 では「frame ごとに 1 trade」精度に留める（v2 で正確化）
- `is_liquidation` は常に `false`
- 板の変化（`p_<行>_GAK1..5` / `p_<行>_GBK1..5`）は trade ではなく **DepthSnapshot 更新トリガ** として扱う

## 4. depth（板）

立花は L2 差分配信を持たない。FD frame は **5 本気配 + 現値**。

```
DepthSnapshot {
  bids: [(GBK1, GBS1), (GBK2, GBS2), ..., (GBK5, GBS5)],
  asks: [(GAK1, GAS1), ...],
  sequence_id: <FD frame の到着順序カウンタ>,
  stream_session_id: "<engine_session_id>:<u32>",
  checksum: None,
}
```

- **`DepthDiff` は生成しない**。FD frame ごとに常に新規 `DepthSnapshot` を送る（5 本のみなので帯域は問題なし）
- Rust 側 [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.4 バックプレッシャと整合性保証 の gap 検知ロジックは「diff が来ない venue」として迂回する必要がある。`stream`-単位で「snapshot-only」フラグを capabilities に含める案を [open-questions.md](./open-questions.md) で扱う
- 別ルート: ザラ場開始前 / 終了直後の板取得は `CLMMfdsGetMarketPrice` を 1 回叩いて `DepthSnapshot` を出す（ストリームに先立って 1 発投げる）

## 5. ticker metadata（呼値・売買単位）

立花の呼値テーブル（マスタの「2-12 呼値」）は **価格帯ごとに刻みが変わる**:

```
価格 ≤ 3,000:    1 円刻み
価格 ≤ 5,000:    5 円刻み
...
価格 > 30,000,000: 10,000 円刻み
```

既存 `MinTicksize` は **1 値固定** を前提とした型（[exchange/src/unit.rs](../../../exchange/src/unit.rs) 周辺）。**3 つの選択肢**:

- **(A) 最小値固定** — 0.1 円（優先株や ETF の最小刻み）を採用し、UI 描画は許容。発注時の価格丸めが立花側で reject されうる
- **(B) `MinTicksize` を「価格帯テーブル参照可能」に拡張** — 既存型のオーバーホール。波及範囲が大きい
- **(C) ticker ごとに「現在価格に応じた tick」を動的に再計算する** — `TickerInfo` を時価変動で更新

**Phase 1 推奨: (A)**。Phase 1 はリードオンリーなので発注時の reject は起きない。チャート描画は最小刻みで十分機能する。Phase 2（発注）で (C) に移行。

`TickerInfo` 拡張案:

```rust
pub struct TickerInfo {
    // ... 既存 ...
    pub lot_size: Option<u32>,   // ← 立花の sTatebaTanniSuu（株式は 100 が大半）。暗号資産では None
}
```

## 6. kline

| 項目 | 立花 | engine DTO |
| :--- | :--- | :--- |
| 取得 API | `CLMMfdsGetMarketPriceHistory` | `FetchKlines` |
| 時間枠 | **日足のみ**（最大約 20 年） | `Timeframe::Day` 相当が必要。既存に無ければ追加 |
| OHLCV | 始値・高値・安値・終値・出来高 | そのまま `KlineMsg` |
| `is_closed` | 営業日が経過していれば true | JST 判定 |
| `taker_buy_volume` | 取得不可 | `None` |

- 分足は Phase 1 では FD frame からのリアルタイム集計のみ。`FetchKlines{timeframe:"M1"}` を立花に投げると `EngineError{code:"timeframe_not_supported"}` を返す

## 7. ticker stats（24h 統計相当）

立花の `CLMMfdsGetMarketPrice` は 1 銘柄分のスナップショットに「前日終値」「現在値」「日中高安」「出来高」が含まれる。これを `TickerStats` に詰める。
24h でなく **当日（ザラ場開始以降）統計** であることに注意。display 用の表記を Rust 側で venue に応じて切り替える（"24h Change" → "Daily Change"）。

## 8. open interest

株式に概念がない。`fetch_open_interest` は Python 側で `NotImplementedError` → Rust 側で `EngineError{code:"oi_not_supported"}` を返す。UI は OI インジケータを立花 venue では非表示にする。

## 9. fetch_trades（過去 trade）

立花は過去 tick API を提供しない。`NotImplementedError`。UI のヒストリカル trade ロード機能は立花 venue で非活性。

## 10. timezone / 時刻

- すべて **JST (UTC+9)**
- IPC DTO は **UNIX ms (UTC)** で統一（既存の方針通り、[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.3.2 メッセージスキーマ）
- Python 側で `datetime.fromisoformat(...).replace(tzinfo=JST).timestamp() * 1000` 変換
- ザラ場判定は Python 側で実施。Rust 側は `Disconnected` / `Connected` で受け取るのみ

## 11. 数値表現（価格・数量）

| 立花 | 既存 IPC |
| :--- | :--- |
| 価格: 円・整数または小数点（呼値による） | `String`（[dto.rs L227 TradeMsg.price](../../../engine-client/src/dto.rs#L227)）— 既存通り |
| 数量: 株・整数（売買単位の倍数） | `String` で渡す。Rust の `Qty` 復元は既存ロジック流用 |

JPY が quote currency になる venue は本アプリ初。**通貨表示用の `quote_ccy` フィールドが `TickerInfo` に必要**:

```rust
pub struct TickerInfo {
    // ...
    pub quote_currency: &'static str,  // 既定 "USDT"、立花は "JPY"
}
```

UI のフォーマッタはこれを見て `¥` プレフィックス + 桁区切りにする。

## 12. capabilities ハンドシェイク（[docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.5 起動ハンドシェイク）

`Ready.capabilities` に立花用フラグを追加:

```json
{
  "supported_venues": ["binance", "bybit", "hyperliquid", "okex", "mexc", "tachibana"],
  "venue_capabilities": {
    "tachibana": {
      "supports_depth_diff": false,
      "supports_historical_trades": false,
      "supports_open_interest": false,
      "supported_timeframes": ["D1"],
      "requires_credentials": true,
      "session_lifetime_seconds": 86400
    }
  }
}
```

Rust 側はこれを見て、UI 上で立花 ticker 選択時に「分足切替」「OI インジケータ」「ヒストリカル trade ロード」を非活性化する。
