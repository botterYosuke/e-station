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

- `MarketKind::Stock` の `qty_in_quote_value(qty, price, size_in_quote_ccy)` 実装は **常に `price * qty`**（quote = JPY、F-M3b）。**`size_in_quote_ccy` 引数は `MarketKind::Stock` 内部で無視する**（呼出規約に頼らず enum 内部分岐で強制し、暗号資産パスからの誤呼出しで silently 誤値にならないようにする）。実装は `match self { MarketKind::Stock => price * qty, ... }` で `Stock` バリアントを最優先に分岐
- 信用区分（`sGenkinShinyouKubun`）は **MarketKind では区別しない**（読み取り専用 Phase 1 ではチャート上区別不要）。発注時に別パラメータとして渡す（Phase 2）
- IPC では `venue: "tachibana"` / `market: "stock"` を文字列で使う
- 既存 UI には `Spot` / `LinearPerps` / `InversePerps` の 3 分岐を前提にした suffix・market filter・indicator 可用性・timeframe 可用性があるため、**`MarketKind::Stock` 追加は DTO だけでなく表示層の match も更新対象**とみなす（網羅 match の更新箇所は T0 で grep 棚卸し）

## 2. ticker

| 項目 | 立花 | 本アプリ |
| :--- | :--- | :--- |
| 銘柄コード | `sIssueCode` 4 桁数字 / 5 桁数字 / **末尾英字を含む 5 桁英数字**（新興市場の優先出資証券・新株予約権付社債で `130A0` のような表記あり） | `Ticker` の symbol 部分にそのまま入れる（ASCII 英数字のみ） |
| 表示名（M9: 4 種を全保持） | `sIssueName`（漢字）/ `sIssueNameRyaku`（漢字略称）/ `sIssueNameKana`（カナ）/ `sIssueNameEizi`（英語名 ASCII） | `Ticker::new` / `Ticker::new_with_display` は [exchange/src/lib.rs:291,303](../../../exchange/src/lib.rs#L291) で `assert!(is_ascii())` 強制。よって **日本語名は `Ticker` / `display_symbol` には絶対に入れない**。代わりに **`EngineEvent::TickerInfo` の各 ticker dict**（現状 `Vec<serde_json::Value>`、[engine-client/src/dto.rs:193](../../../engine-client/src/dto.rs#L193)）に Python 側が `display_name_ja: string \| null` キーを詰めて送る（T0.2 確定方針）。`TickerListed` という名の DTO 型は存在しない。Rust 側 UI は受信した dict から `display_name_ja` を取り出して `HashMap<Ticker, TickerDisplayMeta>` で別管理する（T4 で実装）。**キー名の typo サイレント失敗防止**のため、Python 単体テストで `display_name_ja`（`display_name_jp` ではない）を assert する（M9） |
| 市場コード | `sSizyouC`（`00`=東証） | Phase 1 は東証固定 |
| 売買単位 | 銘柄マスタ `sTatebaTanniSuu` | `TickerInfo.lot_size` 相当（新規プロパティ追加要） |
| 呼値単位 | マスタ「呼値」テーブル（価格帯依存） | `Price` の min_ticksize で表現。**価格帯ごとに変わる** ため固定 1 値では足りない（§5 参照） |

`Ticker::new("7203", Exchange::TachibanaStock)` のような文字列パスで素直に通る（現 API は第 2 引数 `Exchange` が必須、[exchange/src/lib.rs:281](../../../exchange/src/lib.rs#L281)）。既存 `Ticker::new` は ASCII 制約と `MAX_LEN` チェック（[lib.rs:290](../../../exchange/src/lib.rs#L290)）のみで、**`130A0` のような英字混在 5 桁 ticker も許容可能**。T4 では「実データで通ること」（ASCII 制約 + MAX_LEN 収容）の確認に留める（F2）。

**先行実装参考（M9）**: 類似プロジェクト [flowsurface](file:///C:/Users/sasai/Documents/flowsurface) の `exchange/src/adapter/tachibana.rs:625-684` が同じ問題に対する `MasterRecord` 型（`sIssueName` / `sIssueNameRyaku` / `sIssueNameKana` / `sIssueNameEizi` の 4 種を全保持）と「`display_symbol` には英語名 `sIssueNameEizi` を採用、ASCII 28 文字に収まらないものは `None` フォールバック」というパターンを既に持っている。本計画では **同じ MasterRecord 4 フィールドを Python 側に踏襲**し、加えて `display_name_ja` (`sIssueName`) を別ルートで運ぶ（implementation-plan.md T0.2 の M9 項目）。

## 3. trade（FD frame からの合成）

立花にはミリ秒単位のテープデータ API は存在しない。代わりに EVENT の **FD frame**（時価情報）が変化分のみ来る。Phase 1 では下記をもって "trade" とみなす:

> **情報コード出典の注意（F-M2a、F-H3）**: 下記の `DPP` / `DV` / `DPP_TIME` / `DDT` / `GAK1..5` / `GBK1..5` 等は SKILL.md には `DPP` 1 例しか記載がなく、公式 EVENT 仕様 PDF（`api_event_if_v4r7.pdf`）は `manual_files/` に同梱されていない。**T0 末で Python サンプル [`e_api_websocket_receive_tel.py`](../../../.claude/skills/tachibana/samples/e_api_websocket_receive_tel.py/e_api_websocket_receive_tel.py) のコード表抜粋を本節に転記する**こと（implementation-plan T0.1 タスク化）。**この一覧の確定は T1（codec）と T5（FD trade/depth）の前提条件**であり、未確定のまま下流フェーズに着手しない。それまで本節のコード名は暫定値として扱う。


| 立花 FD フィールド | 意味 | TradeMsg |
| :--- | :--- | :--- |
| `p_<行>_DPP` | 現在値 | `price` |
| `p_<行>_DV` | 出来高（累積、日中） | 前 frame 値との差分を `qty` に。**差分が正のときのみ trade を生成**。0 または負（セッション跨ぎ・銘柄差替えによるリセット）の場合は trade を発火せず `prev_dv` を現在値にリセット |
| `p_<行>_DPP_TIME` | 現値 tick 時刻（秒精度） | `ts_ms`（JST → ms）**の第一候補**。無ければ `p_<行>_DDT`（frame 配信時刻）にフォールバック。両方無ければ受信時刻（F17） |
| 前 frame の bid/ask | — | `side` を **Quote rule**（前 frame 気配と比較）で決定。下記参照 |

**Quote rule の詳細（F3）**:
- FD frame は DPP と GAK/GBK が**同一 frame で同時更新される**。当該 frame の気配と比較すると、約定を吸収した後の板と比較してしまい誤判定しやすい。
- 実装は `TachibanaWorker` 内で `prev_quote: Option<(best_bid, best_ask)>` を保持し、frame 到着時に
  1. まず DPP/DV から trade を合成（qty > 0 のときのみ、初回は skip）
  2. side 判定には **`prev_quote` の best_bid / best_ask を使う**
  3. `DPP >= prev_ask` → `buy` / `DPP <= prev_bid` → `sell` / 中値ぴったり → 直前 trade 価格との tick rule フォールバック
  4. trade 発火後に `prev_quote` を現 frame の best_bid/best_ask で更新
- **初回 frame（prev_quote=None かつ prev_dv=None）は trade を発火しない**（F4）。初回 frame は quote 初期化と DV 初期化だけを行い、2 件目以降で trade 合成を開始する
- 履歴が無くかつ tick rule も効かないエッジケースでは `buy` 既定、ただしログに `warn!("tachibana: initial trade side ambiguous")` を出す
- **tick rule fallback テストケース（F-M8b）**: `test_tachibana_fd_trade.py` に「DPP が前 frame の bid と ask の中値ぴったり / 直前 trade 価格より上昇 → `buy`」「同条件で直前 trade 価格より下落 → `sell`」「直前 trade も同値 → 既定 `buy` + warn ログ」の 3 ケースを追加

**DV リセット条件（F4）**:
- 新規 WebSocket 接続（`stream_session_id` が更新された場合）
- 銘柄 subscribe の切り替え
- 受信値が前 frame 値より小さい（日付跨ぎ・立花側リセット想定）
- どのケースも `prev_dv` を `None` に戻し、次 frame は「初回」扱いで trade 発火しない

- **不正確になりうるトレードオフ**: 立花の FD は出来高が累積で、複数約定が 1 frame に集約されることがある。Phase 1 では「frame ごとに 1 trade、qty は DV 差分」精度に留める（v2 で正確化）
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
- Rust 側 [docs/plan/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.4 バックプレッシャと整合性保証 の gap 検知ロジックは、**snapshot-only venue では `DepthDiff` を受けない限り誤動作しない**。Phase 1 は `DepthSnapshot` のみで成立させ、capabilities は主に UI 非活性化用途に使う
- **`sequence_id` リセット規約（F7）**: Python 側プロセス内の `AtomicI64` で単調増加 ID を振る。Python 再起動や WebSocket 切断で counter は 0 に戻りうるが、`stream_session_id` の値を同時に更新するため、**消費側は `stream_session_id` 切替を検知したら sequence 比較をリセットする**。既存 gap-detector にもこの契約を明示することを T0 で schema に書き込む
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

- **(A) 最小値固定** — `TickerInfo` 構築時に「当該銘柄の現在価格帯から呼値テーブル §2-12 を引いた 1 値」を埋める（例: 7203=1 円、9984=10 円、優先株 / 値嵩株 ETF=0.1 円）。**T4 のマスタ取得時にスナップショット価格を参照して決定**し、ザラ場中の価格帯遷移は無視する
- **(B) `MinTicksize` を「価格帯テーブル参照可能」に拡張** — 既存型のオーバーホール。波及範囲が大きい
- **(C) ticker ごとに「現在価格に応じた tick」を動的に再計算する** — `TickerInfo` を時価変動で更新

**Phase 1 推奨: (A)**（B4 改訂）。**「最小値の 0.1 円固定」は不採用**。理由は通常株（TOPIX100 を除く大半）の呼値刻みは 1 円〜10 円であり、0.1 円固定だと価格軸ラベルに無意味な小数桁（`7203.0` `7203.1` ...）が出てチャート可読性が下がるため。リードオンリーなので発注 reject は起きないが、UI 描画品質を優先する。`TickerInfo::new_stock` の引数で `min_ticksize: f32` を受け取る既存シグネチャはそのまま使え、Python 側 `tachibana_master.py` で「銘柄スナップショット価格 → 呼値刻み」を表引きで解決する。Phase 2（発注）で (C) に移行。

**呼値テーブル（§2-12）の Python 側実装**: `tachibana_master.py` に `tick_size_for_price(price: Decimal) -> Decimal` を 1 関数置き、SKILL.md / マスタ I/F PDF の表に基づき価格帯 →刻み を hardcode（Phase 1）。テーブル変更は立花側で年単位の頻度なので Phase 1 では更新監視を入れない。

`TickerInfo` 拡張案:

```rust
pub struct TickerInfo {
    // ... 既存 ...
    pub lot_size: Option<u32>,   // ← 立花の sTatebaTanniSuu（株式は 100 が大半）。暗号資産では None
}
```

> **注意（F13）**: 現行 [exchange/src/lib.rs:515](../../../exchange/src/lib.rs#L515) の `TickerInfo` は `#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize, Hash, Eq)]` を持ち `HashMap` キー / `HashSet` 要素として全クレートで使われる。**`Copy` が付いているため、追加フィールドも `Copy` を満たすこと**（`String` 禁止。日本語名は `TickerDisplayMeta` へ）。フィールド追加で hash 値が変わると既存 UI 状態（pane ↔ ticker_info の紐づけ）と非互換になる可能性がある。T0 では:
> - `lot_size` / `quote_currency` 追加前に `git grep "TickerInfo"` / `HashMap.*TickerInfo` / `HashSet.*TickerInfo` で参照箇所を全数棚卸しする
> - 追加フィールドは **`#[serde(default)]`** を付け、対応する `Default` 実装を用意する（既存永続 state に missing field でも読める）
> - `QuoteCurrency` の `Default` は `Usdt`（暗号資産 venue 互換）、`lot_size: Option<u32>` は `None`
> - hash 入りデータが永続化レイヤ（state.rs / dashboard 設定）にあれば schema migration が必要

## 6. kline

| 項目 | 立花 | engine DTO |
| :--- | :--- | :--- |
| 取得 API | `CLMMfdsGetMarketPriceHistory` | `FetchKlines` |
| 時間枠 | **日足のみ**（最大約 20 年） | 既存 `Timeframe::D1`（[exchange/src/lib.rs:83](../../../exchange/src/lib.rs#L83)、IPC 文字列 `"1d"`）を**そのまま流用**。新規追加不要 |
| OHLCV | 始値・高値・安値・終値・出来高 | そのまま `KlineMsg` |
| `is_closed` | 営業日が経過していれば true | JST 判定 |
| `taker_buy_volume` | 取得不可 | `None` |

- 分足は Phase 1 では FD frame からのリアルタイム集計のみ。`FetchKlines{timeframe:"1m"}` のような日足以外要求は Python サーバ経由で `Error{code:"not_implemented"}` を返す。専用コードが必要なら server 側の例外マッピング追加を別タスクにする

## 7. ticker stats（24h 統計相当）

立花の `CLMMfdsGetMarketPrice` は 1 銘柄分のスナップショットに「前日終値」「現在値」「日中高安」「出来高」が含まれる。これを `TickerStats` に詰める。
24h でなく **当日（ザラ場開始以降）統計** であることに注意。display 用の表記を Rust 側で venue に応じて切り替える（"24h Change" → "Daily Change"）。

## 8. open interest

株式に概念がない。`fetch_open_interest` は Python 側で `NotImplementedError` を投げ、現行 server 実装では `Error{code:"not_implemented"}` に変換される。UI は OI インジケータを立花 venue では非表示にする。`oi_not_supported` のような専用コードは Phase 1 の必須条件にしない。

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

JPY が quote currency になる venue は本アプリ初。**通貨表示用フィールドを `TickerInfo` に追加する**:

```rust
#[derive(Copy, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub enum QuoteCurrency {
    Usdt,
    Usdc,
    Usd,
    Jpy,
}

pub struct TickerInfo {
    // ...
    pub quote_currency: Option<QuoteCurrency>,
}
```

- **`&'static str` は採用しない**: serde で受信した文字列を `&'static` に戻せず、`Hash`/`Eq` 派生が崩れるため。enum で表現し、不明値は serde error にする
- **`Default` は付けない・`Option<QuoteCurrency>` で持つ（F-M6a）**: `Default = Usdt` にしてしまうと、新フォーマット導入前の永続 state を読み戻したときに **立花銘柄まで `Usdt` で復元され UI が `$` 表記する**事故が起きる。`Option<QuoteCurrency>` + `#[serde(default)]` で missing field は `None`、`None` のときはフォーマッタが `Exchange`/`Venue` から venue ごとに決定論的に算出する（暗号資産 venue は USDT/USDC、立花は JPY）。**永続 state からの復元は読み込み時に必ず venue 由来の値を再注入**して `Some(...)` に正規化する
- venue ごとの quote 抽出関数は `Exchange::default_quote_currency(&self) -> QuoteCurrency` として `exchange/src/adapter.rs` に実装。ticker 単位で例外がある場合のみ `Some(_)` で override
- UI のフォーマッタはこの enum を見て `¥` / `$` プレフィックス + 桁区切りを切り替える

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
      "supported_timeframes": ["1d"],
      "requires_credentials": true
    }
  }
}
```

> **`session_lifetime_seconds` は削除**（MEDIUM-2）。立花 API は session 有効期限を明示的に返さないため（Q28 / F-B3）、固定値 86400 は誤解を招く（「session は確実に 24h 続く」と読まれる）。起動時 `validate_session_on_startup` による実際の生存確認が正の手段であり、capabilities でハードコードした期限値は不要。期限情報が必要になったら `CLMDateZyouhou` から動的取得する（Phase 2 課題）。

Rust 側はこれを見て、UI 上で立花 ticker 選択時に「分足切替」「OI インジケータ」「ヒストリカル trade ロード」を非活性化する。なお日本語銘柄名の表示可否は capabilities ではなく metadata DTO 側で扱う。

ただし現コードには `Exchange::supports_kline_timeframe()` や `Indicator::for_market()` のように **capabilities を見ず enum だけで分岐する箇所が既にある**。したがって Phase 1 では「capabilities を足せば UI が自動で追従する」とは見なさず、enum ベースの既存分岐修正を T0 の明示タスクに含める。
