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
| 表示名（M9: 4 種を全保持） | `sIssueName`（漢字）/ `sIssueNameRyaku`（漢字略称）/ `sIssueNameKana`（カナ）/ `sIssueNameEizi`（英語名 ASCII） | `Ticker::new` / `Ticker::new_with_display` は `exchange/src/lib.rs::Ticker::new` で `assert!(is_ascii())` 強制。よって **日本語名は `Ticker` / `display_symbol` には絶対に入れない**。代わりに **`EngineEvent::TickerInfo` の各 ticker dict**（現状 `Vec<serde_json::Value>`、`engine-client/src/dto.rs::EngineEvent::TickerInfo`）に Python 側が `display_name_ja: string \| null` キーを詰めて送る（T0.2 確定方針）。`TickerListed` という名の DTO 型は存在しない。Rust 側 UI は受信した dict から `display_name_ja` を取り出して `HashMap<Ticker, TickerDisplayMeta>` で別管理する（T4 で実装）。**キー名の typo サイレント失敗防止**のため、Python 単体テストで `display_name_ja`（`display_name_jp` ではない）を assert する（M9） |
| 市場コード | `sSizyouC`（`00`=東証） | Phase 1 は東証固定 |
| 売買単位 | 銘柄マスタ `sTatebaTanniSuu` | `TickerInfo.lot_size` 相当（新規プロパティ追加要） |
| 呼値単位 | `CLMYobine` テーブル（`sYobineTaniNumber` ごとに 20 段の `sKizunPrice_n` / `sYobineTanka_n` / `sDecimal_n`）+ `CLMIssueSizyouMstKabu.sYobineTaniNumber`（銘柄→ yobine_code 参照） | 銘柄ごとに `sYobineTaniNumber` で `CLMYobine` 行を引き、現在価格に応じた band を選ぶ。固定 1 値ではなく **per-stock per-band** lookup（§5 参照） |

`Ticker::new("7203", Exchange::TachibanaStock)` のような文字列パスで素直に通る（現 API は第 2 引数 `Exchange` が必須、`exchange/src/lib.rs::Ticker::new`）。既存 `Ticker::new` は ASCII 制約と `MAX_LEN` チェック（`exchange/src/lib.rs::Ticker::new`）のみで、**`130A0` のような英字混在 5 桁 ticker も許容可能**。T4 では「実データで通ること」（ASCII 制約 + MAX_LEN 収容）の確認に留める（F2）。

**先行実装参考（M9）**: 類似プロジェクト [flowsurface](file:///C:/Users/sasai/Documents/flowsurface) の `exchange/src/adapter/tachibana.rs:625-684` が同じ問題に対する `MasterRecord` 型（`sIssueName` / `sIssueNameRyaku` / `sIssueNameKana` / `sIssueNameEizi` の 4 種を全保持）と「`display_symbol` には英語名 `sIssueNameEizi` を採用、ASCII 28 文字に収まらないものは `None` フォールバック」というパターンを既に持っている。本計画では **同じ MasterRecord 4 フィールドを Python 側に踏襲**し、加えて `display_name_ja` (`sIssueName`) を別ルートで運ぶ（implementation-plan.md T0.2 の M9 項目）。

## 3. trade（FD frame からの合成）

立花にはミリ秒単位のテープデータ API は存在しない。代わりに EVENT の **FD frame**（時価情報）が変化分のみ来る。Phase 1 では下記をもって "trade" とみなす:

> **✅ 2026-04-26 情報コード確定**: `.claude/skills/tachibana/manual_files/api_web_access.xlsx` 内の実 FD frame サンプル（2022-03-15）から全キー名を確認済み。旧暫定名（`GAK/GBK/GAS/GBS`、`DPP_TIME`、`DDT`）はすべて誤りだったため本節・§4 を訂正済み。詳細は [inventory-T0.md §11](./inventory-T0.md#112b-fd-frame-data-key) を参照。


| 立花 FD フィールド | 意味 | TradeMsg |
| :--- | :--- | :--- |
| `p_<行>_DPP` | 現在値 | `price` |
| `p_<行>_DV` | 出来高（累積、日中） | 前 frame 値との差分を `qty` に。**差分が正のときのみ trade を生成**。0 または負（セッション跨ぎ・銘柄差替えによるリセット）の場合は trade を発火せず `prev_dv` を現在値にリセット |
| `p_<行>_DPP:T` | 現値 tick 時刻（`HH:MM` 形式、秒精度） | `ts_ms`（JST → ms）**の第一候補**。無ければ共通ヘッダ `p_date`（frame 配信時刻、`YYYY.MM.DD-HH:MM:SS.TTT`）にフォールバック。両方無ければ受信時刻（F17）|
| 前 frame の bid/ask | — | `side` を **Quote rule**（前 frame 気配と比較）で決定。下記参照 |

**Quote rule の詳細（F3）**:
- FD frame は DPP と GAP/GBP が**同一 frame で同時更新される**。当該 frame の気配と比較すると、約定を吸収した後の板と比較してしまい誤判定しやすい。
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
- 板の変化（`p_<行>_GAP1..10` / `p_<行>_GBP1..10`）は trade ではなく **DepthSnapshot 更新トリガ** として扱う

## 4. depth（板）

立花は L2 差分配信を持たない。FD frame は **10 本気配 + 現値**（旧想定 5 本は誤り。xlsx サンプルで `GAP1`〜`GAP10` / `GBP1`〜`GBP10` を確認済み）。

```
DepthSnapshot {
  bids: [(GBP1, GBV1), (GBP2, GBV2), ..., (GBP10, GBV10)],
  asks: [(GAP1, GAV1), (GAP2, GAV2), ..., (GAP10, GAV10)],
  sequence_id: <FD frame の到着順序カウンタ>,
  stream_session_id: "<engine_session_id>:<u32>",
  checksum: None,
}
```

- **`DepthDiff` は生成しない**。FD frame ごとに常に新規 `DepthSnapshot` を送る（10 本でも帯域は問題なし）
- Rust 側 [docs/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.4 バックプレッシャと整合性保証 の gap 検知ロジックは、**snapshot-only venue では `DepthDiff` を受けない限り誤動作しない**。Phase 1 は `DepthSnapshot` のみで成立させ、capabilities は主に UI 非活性化用途に使う
- **`sequence_id` リセット規約（F7）**: Python 側プロセス内の `AtomicI64` で単調増加 ID を振る。Python 再起動や WebSocket 切断で counter は 0 に戻りうるが、`stream_session_id` の値を同時に更新するため、**消費側は `stream_session_id` 切替を検知したら sequence 比較をリセットする**。既存 gap-detector にもこの契約を明示することを T0 で schema に書き込む
- 別ルート: ザラ場開始前 / 終了直後の板取得は `CLMMfdsGetMarketPrice` を 1 回叩いて `DepthSnapshot` を出す（ストリームに先立って 1 発投げる）

## 5. ticker metadata（呼値・売買単位）

立花の呼値は **銘柄ごとに `CLMYobine` 行を引いて求める**。`CLMYobine` は `sYobineTaniNumber` 別の最大 20 段 band（`sKizunPrice_n` / `sYobineTanka_n` / `sDecimal_n`）を持ち、銘柄は `CLMIssueSizyouMstKabu.sYobineTaniNumber` でこれを参照する（B1 確定方針）。**全銘柄共通の単一 hardcode 表は存在しない**。`api_request_if_master_v4r5.pdf §2-12` は呼値テーブルの**構造説明**（PDF 内で「資料_呼値」を参照と明記）であり、PDF 単独で全価格帯を写経する旧前提は撤回。実値は **`CLMYobine` の master download** で常に取得する。

スクリーンショット例（PDF §2-12、`sYobineTaniNumber=101` の抜粋。**hardcode 用ではなくテーブル形式の理解用**）:

```
価格 ≤ 3,000:    1 円刻み
価格 ≤ 5,000:    5 円刻み
...
価格 > 30,000,000: 10,000 円刻み（999999999 sentinel 終端）
```

既存 `MinTicksize` は **1 値固定** を前提とした型（[exchange/src/unit.rs](../../../exchange/src/unit.rs) 周辺）。**3 つの選択肢**:

- **(A) per-stock 1 値固定（Phase 1 採用、B4 改訂）** — `TickerInfo` 構築時に「当該銘柄の `sYobineTaniNumber` で `CLMYobine` を引き、現在価格帯から 1 band を選んだ刻み」を埋める。**T4 のマスタ取得時にスナップショット価格を参照して決定**し、ザラ場中の価格帯遷移は無視する。`tick_size_for_price(price, yobine_code, yobine_table)` の戻り値を `Decimal -> f32` で `TickerInfo::new_stock(min_ticksize: f32, ...)` に詰める
- **(B) `MinTicksize` を「価格帯テーブル参照可能」に拡張** — 既存型のオーバーホール。波及範囲が大きい。Phase 2 候補
- **(C) ticker ごとに「現在価格に応じた tick」を動的に再計算する** — `TickerInfo` を時価変動で更新。Phase 2（発注）候補

**Phase 1 採用: (A)**。**「最小値の 0.1 円固定」は不採用**。理由は通常株（TOPIX100 を除く大半）の呼値刻みは 1 円〜10 円であり、0.1 円固定だと価格軸ラベルに無意味な小数桁（`7203.0` `7203.1` ...）が出てチャート可読性が下がるため。リードオンリーなので発注 reject は起きないが、UI 描画品質を優先する。`sDecimal_n` 由来の量子化は trade price / depth price の **表示丸め**で別途使用する（軸ラベル刻みは `MinTicksize` 1 値で行う）。Phase 2（発注）で (C) に移行。

**呼値テーブル（`api_request_if_master_v4r5.pdf` §2-12 参照）の Python 側実装（B1 改訂、A 方針確定）**: §2-12 は呼値テーブルの**構造説明**（最大 20 段の `(sKizunPrice_N, sYobineTanka_N, sDecimal_N)` を持つ `sYobineTaniNumber` 別 row）であり、全価格帯を 1 つの hardcode 表に閉じ込められる類のドキュメントではない（PDF 内で「資料_呼値」を参照と明記。実値はランタイムでマスタダウンロードから供給される）。よって per-stock の `sYobineCode = CLMIssueSizyouMstKabu.sYobineTaniNumber` を `CLMYobine` レコードに引き当てて刻みを決定する設計を採る。`tachibana_master.py` に `CLMYobine` dataclass / decoder（`YobineBand`, `CLMYobineRecord`, `decode_clm_yobine_record`）と純粋関数 `tick_size_for_price(price: Decimal, yobine_code: str, yobine_table: dict[str, list[YobineBand]]) -> Decimal`（最初に `price <= band.kizun_price` を満たす band の `yobine_tanka` を返す）を実装する（B1 で完了）。20 段のうち末尾は仕様上 `999999999` の sentinel が必ず入る（PDF 注記）ため、これをテーブルの cap として活用する。テスト fixture は PDF 画像で見えた例示行（`101`/`103`/`418` の 1〜2 段）で十分。`yobine_table` をどう持ち回るか・`TickerInfo` payload に `yobine_code` を載せるかは B2 で実装。

`TickerInfo` 拡張案:

```rust
pub struct TickerInfo {
    // ... 既存 ...
    pub lot_size: Option<u32>,   // ← 立花の sTatebaTanniSuu（株式は 100 が大半）。暗号資産では None
}
```

> **注意（F13）**: 現行 `exchange/src/lib.rs::TickerInfo` の `TickerInfo` は `#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize, Hash, Eq)]` を持ち `HashMap` キー / `HashSet` 要素として全クレートで使われる。**`Copy` が付いているため、追加フィールドも `Copy` を満たすこと**（`String` 禁止。日本語名は `TickerDisplayMeta` へ）。フィールド追加で hash 値が変わると既存 UI 状態（pane ↔ ticker_info の紐づけ）と非互換になる可能性がある。T0 では:
> - `lot_size` / `quote_currency` 追加前に `git grep "TickerInfo"` / `HashMap.*TickerInfo` / `HashSet.*TickerInfo` で参照箇所を全数棚卸しする
> - 追加フィールドは **`#[serde(default)]`** を付け、対応する `Default` 実装を用意する（既存永続 state に missing field でも読める）
> - `QuoteCurrency` の `Default` は `Usdt`（暗号資産 venue 互換）、`lot_size: Option<u32>` は `None`
> - hash 入りデータが永続化レイヤ（state.rs / dashboard 設定）にあれば schema migration が必要

## 6. kline

| 項目 | 立花 | engine DTO |
| :--- | :--- | :--- |
| 取得 API | `CLMMfdsGetMarketPriceHistory` | `FetchKlines` |
| 時間枠 | **日足のみ**（最大約 20 年） | 既存 `Timeframe::D1`（`exchange/src/lib.rs::Timeframe`、IPC 文字列 `"1d"`）を**そのまま流用**。新規追加不要 |
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
- IPC DTO は **UNIX ms (UTC)** で統一（既存の方針通り、[docs/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.3.2 メッセージスキーマ）
- Python 側で `datetime.fromisoformat(...).replace(tzinfo=JST).timestamp() * 1000` 変換
- ザラ場判定は Python 側で実施。Rust 側は `Disconnected` / `Connected` で受け取るのみ

## 11. 数値表現（価格・数量）

| 立花 | 既存 IPC |
| :--- | :--- |
| 価格: 円・整数または小数点（呼値による） | `String`（`engine-client/src/dto.rs::TradeMsg`）— 既存通り |
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

## 12. capabilities ハンドシェイク（[docs/✅python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.5 起動ハンドシェイク）

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
