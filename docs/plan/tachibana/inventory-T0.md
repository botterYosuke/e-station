# フェーズ T0.1: 既存型棚卸し

立花 venue 統合に向けた既存コード参照箇所の表化。`MarketKind::Stock` / `TickerInfo` 拡張 / `Timeframe` serde 変更 / FD 情報コードの影響範囲を確定する。

## 1. `TickerInfo` 参照箇所

`exchange/src/lib.rs:515` で定義。
```rust
#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize, Hash, Eq)]
pub struct TickerInfo {
    pub ticker: Ticker,
    pub min_ticksize: MinTicksize,
    pub min_qty: MinQtySize,
    pub contract_size: Option<ContractSize>,
}
```

`#[derive(Hash, Eq, Copy)]` を持ち、`HashMap` キー / `HashSet` 要素として全クレートで使われる。フィールド追加は次の制約を満たすこと:

- `Copy` を維持（`String` 不可、Newtype は中身 `Copy` のみ）
- 永続 state へ `#[serde(default)]` で missing field 互換を保つ
- `Hash` 値変化により `HashMap<TickerInfo, _>` のキー突合が壊れる可能性あり → `state.json` 起動テストで検証必須

### 1.1 全 42 ファイル中、コードでの参照（grep "TickerInfo"）

| クレート | ファイル | 用途 |
| :--- | :--- | :--- |
| exchange | `exchange/src/lib.rs` (515-550) | 定義本体・`new`/`market_type`/`is_perps`/`exchange` メソッド |
| exchange | `exchange/src/adapter.rs:153` | `EnumMap<Exchange, Option<FxHashMap<TickerInfo, FxHashSet<StreamKind>>>>` — UniqueStreams のキー |
| exchange | `exchange/src/adapter/venue_backend.rs:11` | `pub type TickerMetadataMap = HashMap<Ticker, Option<TickerInfo>>` |
| exchange | `exchange/src/adapter/client.rs:127` | trait method `fetch_ticker_metadata` 戻り値 |
| exchange | `exchange/src/unit/qty.rs` | `Qty` フォーマッタへの `TickerInfo` 受け渡し |
| exchange | `exchange/tests/*.rs` | 各種テスト |
| engine-client | `engine-client/src/backend.rs` | `EngineClientBackend` 内 `Ticker::new_with_display` → `TickerInfo::new` の経路 |
| engine-client | `engine-client/tests/depth_gap_recovery.rs` | depth テストの fixture |
| data | `data/src/chart.rs`, `data/src/chart/heatmap.rs`, `data/src/chart/indicator.rs` | 集計ロジック |
| data | `data/src/layout/pane.rs:240,251,252` | **永続化対象**: pane 設定に `ticker_info: TickerInfo` を保持 |
| data | `data/src/stream.rs` | `StreamSpec` resolver で `TickerInfo` を引く |
| data | `data/src/tickers_table.rs` | tickers table 集計 |
| src (UI) | `src/screen/dashboard/tickers_table.rs:92,106,1656` | `FxHashMap<Ticker, Option<TickerInfo>>` — UI sidebar の metadata cache |
| src (UI) | `src/screen/dashboard/sidebar.rs:249` | sidebar が tickers_info を返す |
| src (UI) | `src/screen/dashboard/pane.rs`, `src/screen/dashboard.rs`, `src/screen/dashboard/panel/{timeandsales,ladder}.rs` | UI 描画 |
| src (UI) | `src/chart.rs`, `src/chart/{kline,heatmap,comparison}.rs`, `src/widget/chart{,/heatmap,/comparison}.rs` | チャート描画。`FxHashMap<TickerInfo, ...>` を多用 |
| src (UI) | `src/widget/chart/heatmap/instance.rs`, `src/widget/chart/heatmap/scene/depth_grid.rs` | heatmap |
| src (UI) | `src/connector/fetcher.rs` | adapter 経由のフェッチ |
| src (UI) | `src/modal/{audio,pane/mini_tickers_list}.rs` | modal 表示 |

### 1.2 `HashMap` / `HashSet` キーとしての使用（grep `(Hash|FxHashMap|HashMap).*TickerInfo`）

| 場所 | 種別 | 備考 |
| :--- | :--- | :--- |
| `exchange/src/adapter.rs:153` | `FxHashMap<TickerInfo, FxHashSet<StreamKind>>` | UniqueStreams、in-memory only |
| `src/chart/comparison.rs:28,30,351,356` | `FxHashMap<TickerInfo, _>` | runtime チャート、in-memory |

→ **永続化されているのは `data/src/layout/pane.rs` の `ticker_info: TickerInfo` フィールド**（Layout 経由 `saved-state.json`）。`HashMap` キーで永続化されている箇所はない。よって追加フィールドの `Hash` 値変化は in-memory map にのみ影響し、永続 state は **`#[serde(default)]` のフィールド追加で互換が取れる**。

## 2. `MarketKind::(Spot|LinearPerps|InversePerps)` 網羅 match 棚卸し

`MarketKind::Stock` 追加で **網羅 match を破る箇所**は以下。すべて `Stock` 分岐の追加が必要。

| ファイル | 行 | パターン | `Stock` の扱い |
| :--- | ---: | :--- | :--- |
| `exchange/src/adapter.rs` | 45-47 | `pub const ALL: [MarketKind; 3]` | 配列を `[MarketKind; 4]` へ拡張、`Stock` を追加 |
| `exchange/src/adapter.rs` | 53-62 | `qty_in_quote_value` の match | **`Stock => price * qty`** を最優先で追加（F-M3b、`size_in_quote_ccy` 引数を見ない） |
| `exchange/src/adapter.rs` | 71-75 | `Display` | `Stock => "Stock"` |
| `exchange/src/adapter.rs` | 84-92 | `FromStr` | `"stock" => Stock` |
| `exchange/src/adapter.rs` | 392-407 | `Exchange::market_type` | `TachibanaStock => Stock` |
| `data/src/chart/indicator.rs:22-23, 54-55` | | `MarketKind::Spot => &Self::FOR_SPOT, ::LinearPerps \| ::InversePerps => &Self::FOR_PERPS` | `Stock => &Self::FOR_SPOT`（Phase 1 は spot 相当のインジケータ可用性。OI などは capabilities で UI 非活性化）。**M4 修正**: `FOR_SPOT` の実体（[data/src/chart/indicator.rs](../../../data/src/chart/indicator.rs)）を T0.2 着手時に列挙し、株式に意味を持たないもの（funding rate / open interest / liquidation 系など）が混入していないか確認する。混入していれば `Stock` 専用配列を別途用意するか、UI 側で個別非活性化する判断を T0.2 内で行う |
| `data/src/tickers_table.rs:169-170` | | `Spot => "" / Perps => "P"` 表示サフィックス | `Stock => ""`（株式に Perp サフィックス不要） |
| `engine-client/src/backend.rs:50-56` | | `market_kind_to_ipc` | `Stock => "stock"` |
| `src/screen/dashboard/tickers_table.rs:63-66, 750-752, 1090-1091` | | venue ごとの `MarketKind` 配列、market filter ボタン、UI suffix | `Venue::Tachibana => &[MarketKind::Stock]`、Stock 用 market filter は Phase 1 では非表示推奨。Stock の suffix は `""` |
| `src/screen/dashboard/pane.rs:529-530` | | `Spot => symbol / Perps => symbol + " PERP"` | `Stock => symbol` |
| `exchange/src/lib.rs:367, 544` | | `MarketKind::LinearPerps`/`InversePerps` を直接比較 | `is_perps` は false のまま、Display ロジックは Hyperliquid 専用なので `Stock` は分岐不要 |
| `exchange/src/unit/qty.rs:225, 259, 269, 281` | | `matches!(market_kind, MarketKind::InversePerps)` | `Stock` は inverse でないため修正不要（`is_inverse=false`） |

**T0.2 で対応すべき網羅 match の必須修正は 11 箇所**。`matches!()` でピンポイントに `InversePerps` のみ問う箇所は修正不要（`Stock` は inverse でない）。

## 3. `Ticker::new` の ASCII 制約と `MAX_LEN`

`exchange/src/lib.rs:281-318` で定義。

```rust
pub const MAX_LEN: u8 = 28;
assert!(ticker.len() <= Self::MAX_LEN as usize, "Ticker too long");
assert!(ticker.is_ascii(), "Ticker must be ASCII");
assert!(!ticker.contains('|'), "Ticker cannot contain '|'");
```

立花の銘柄コード（4-5 桁数字 + 末尾英字 `130A0` 等）はすべて ASCII 英数字 5 文字以内 → 制約を満たす。**ユニットテスト追加（T4）で `Ticker::new("130A0", Exchange::TachibanaStock)` が panic しないことを確認**。

日本語銘柄名は `Ticker` には入らない（ASCII 強制）。`engine-client::dto::EngineEvent::TickerInfo.tickers[*].display_name_ja` 経由で運搬する（Q16 決定）。

## 4. `qty_in_quote_value` 呼出箇所（F-H4）

正本: `exchange/src/adapter.rs:50`

呼出 9 箇所:

| ファイル | 行 |
| :--- | ---: |
| `src/chart/heatmap.rs` | 539, 614 |
| `src/widget/chart/heatmap/instance.rs` | 320 |
| `src/widget/chart/heatmap/scene/depth_grid.rs` | 195, 585 |
| `src/screen/dashboard/panel/timeandsales.rs` | 128, 230, 464 |
| `data/src/chart/heatmap.rs` | 322, 496 |

すべて `market_type.qty_in_quote_value(qty, price, size_in_quote_ccy)` のシグネチャで呼んでいる。`MarketKind::Stock => price * qty` を enum 内部で強制（`size_in_quote_ccy` 引数を無視）すれば、**呼出側コードは変更不要**で `Stock` venue でも正値が出る。網羅 match は match 内の追加分岐 1 行のみ。

## 5. `Timeframe` の serde 形式（F-H1）

`exchange/src/lib.rs:67-83` の現状:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub enum Timeframe {
    MS100, MS200, MS300, MS500, MS1000,
    M1, M3, M5, M15, M30,
    H1, H2, H4, H12, D1,
}
```

`#[serde(rename = "...")]` 無し → JSON 化で `"D1"` などの**変種名**が出る。一方 `Display` (`exchange/src/lib.rs:41-65`) は `"1d"` などの **wire 形式**を返し、`engine-client::backend::timeframe_to_str` も同 wire 形式を使う。

**T0.2 で全 15 変種に `#[serde(rename = "...")]` を付与**し、IPC / 永続 state の双方を `Display` と一致させる。永続 state ロード時の互換性は要検証（旧形式 `"D1"` で書かれていた場合）。

### 5.1 既存の serde ユース箇所

| 場所 | 用途 |
| :--- | :--- |
| `exchange/src/adapter.rs:96-110` `StreamKind::Kline { timeframe: Timeframe }` | 永続 state（pane 設定） |
| `exchange/src/lib.rs:25-30` `PushFrequency::Custom(Timeframe)` | depth push freq、永続 state |
| `data/src/chart.rs`, `data/src/layout/pane.rs` 等 | 永続 state |

→ **永続 state にも書かれているため、rename 適用時は古い形式 (`"D1"`) のロードを別途吸収**するか、**現状 JSON 出力 (`"D1"`) と Display (`"1d"`) の不整合を許容して新規ロード時のみ wire 形式**にするかの判断が必要。

**判断**: T0.2 では全変種に `#[serde(rename = "1d")]` 等を付与し、書込み時は新形式 `"1d"` のみ、読込み時は `#[serde(alias = "D1")]` で旧形式を吸収する**フォワード互換のみ**の方針。**ロールバック非互換**（新形式で書かれた永続 state を旧バイナリへ戻すと panic）を [README.md](./README.md) ＋本ファイル §12 に明記。リリースノートにも書く。

**マイグレーションテスト（LOW-2）**: `cargo test --workspace` だけでは旧形式の alias が全 use-site で機能するか保証しない。`exchange/tests/timeframe_state_migration.rs` を `ticker_info_state_migration.rs` と並列で新設し、以下をカバーする:
- 旧形式 JSON `"D1"` / `"M1"` 等 15 変種すべてが `serde_json::from_str::<Timeframe>` で成功すること
- 新形式 `"1d"` / `"1m"` で serialize 後に deserialize するラウンドトリップが通ること
- `StreamKind::Kline { timeframe: Timeframe::D1 }` を旧形式で書いた `pane_state.json` fixture を `data::layout::pane` 経由でロードできること（saved-state 経路の統合確認）

## 6. `EngineEvent::Disconnected` の shape（F-H2）

`engine-client/src/dto.rs:115-122`:

```rust
Disconnected {
    venue: String,
    ticker: String,
    stream: String,
    #[serde(default)]
    market: String,
    reason: Option<String>,
},
```

→ **DTO 拡張は不要**。`reason: "market_closed"` は文字列規約として `events.json` schema に追記するのみ。

## 7. `ProcessManager` の credentials 保持戦略（F-m4）

`engine-client/src/process.rs:205-211`:

```rust
pub struct ProcessManager {
    pub command: EngineCommand,
    pub active_subscriptions: Arc<Mutex<HashSet<SubscriptionKey>>>,
    pub proxy_url: Arc<Mutex<Option<String>>>,
}
```

→ proxy は `Arc<Mutex<Option<String>>>` パターン。**`venue_credentials: Arc<Mutex<Option<VenueCredentialsPayload>>>` を同一パターンで追加**するのが整合的。`set_venue_credentials(payload)` setter / `start()` 内で `SetProxy` の直後に `SetVenueCredentials` を送るシーケンス追加（T3 で実装、T0 では型と setter のみ用意）。

## 8. `src/screen/` の現状とログイン UI 追加先（F-m3）

ログイン UI は **Python tkinter ヘルパー subprocess** に置く方針（Q31/Q32/Q33）。**Rust 側に立花のログイン画面コードは追加しない**。立花起因のステータスバナー（`VenueLoginStarted` / `VenueError` の `message` 描画）は既存の Banner 系コンポーネント（既存実装の場所は T3 着手時に再特定）に薄く差し込む。Rust 側でログイン画面コードファイル（`src/screen/login.rs` 等）を**新設しない**ことを T0 で固定。

## 9. Python テストフレームワーク

`python/tests/test_binance_rest.py` ほかで `pytest-httpx` の `HTTPXMock` フィクスチャを利用済み。立花 mock テスト（T2）も同一ツールチェーンで揃える。`respx` は不採用。

## 10. スキーマファイルの実在確認

- `docs/plan/✅python-data-engine/schemas/commands.json` ✅
- `docs/plan/✅python-data-engine/schemas/events.json` ✅
- `docs/plan/✅python-data-engine/schemas/CHANGELOG.md` ✅（最新は v1.1）

## 11. FD 情報コード一覧（F-M2a / F-H3）

立花 EVENT WebSocket / HTTP long-poll が返す **FD frame の情報コード**は、API ドキュメントの「**型_行番号_情報コード**」キー形式（`p_<行>_<コード>`）で運ばれる。

### 11.1 一次資料状況

- 公式 EVENT 仕様 PDF（`api_event_if_v4r7.pdf`）は **本リポジトリの `manual_files/` に同梱されていない**
- HTML マニュアル（`mfds_json_api_ref_text.html`）は **REST 系の `CLMMfdsGetMarketPrice` / `CLMMfdsGetMarketPriceHistory`** のフィールド（`pDPP` / `pDOP` / `pDV` / `pSPUO` / `pSPUC` 等）を記載しているが、EVENT FD frame のキー一覧（`p_<行>_<コード>`）の網羅表は無い
- Python サンプル（`e_api_websocket_receive_tel.py`、`e_api_event_receive_tel.py`）は **`p_1_DPP`（現在値）の 1 例のみ**コメントで言及（L618 / L571）。気配 5 本（GAK/GBK 系）・出来高（DV）・現値時刻（DPP_TIME / DDT）の正式コード名は **本リポジトリ内資料には未記載**

### 11.2 暫定コード名（plan 文書からの転記、未確認マーク付き）

**L5 修正**: `p_evt_cmd` 値（コマンド種別）と FD frame data key（`p_<行>_<コード>` の `<コード>` 部分）はレイヤが異なるため別表に分離する:

#### 11.2.a `p_evt_cmd` 値（FD/EVENT 接続時の購読コマンド種別）

これは EVENT URL の `?p_evt_cmd=...` に渡す値。FD frame の中身ではない。

| コード | 意味 | 一次資料での確認 |
| :--- | :--- | :--- |
| `FD` | 時価情報配信 | ✅ 確認済（SKILL.md / サンプル L539-540） |
| `KP` | KeepAlive frame（5 秒周期） | ✅ 確認済（SKILL.md ストリーム規約） |
| `ST` | エラーステータス frame | ✅ 確認済（サンプル L539-540） |
| `SS` | システムステータス frame | ✅ 確認済（サンプル L539-540） |
| `US` | 運用ステータス frame | ✅ 確認済（サンプル L539-540） |
| `EC` | 約定通知 frame（Phase 2 で利用） | ✅ 確認済（サンプル L539-540） |
| `NS` | ニュース通知 | ✅ 確認済（重いため Phase 1 不使用） |
| `RR` | 画面リフレッシュ | ✅ 確認済（不使用） |

#### 11.2.b FD frame data key（`p_<行>_<コード>` の `<コード>` 部分）

FD frame ペイロードのフィールド名。[data-mapping.md §3-4](./data-mapping.md) で trade/depth 合成に使用。

| 暫定コード | 意味 | 一次資料での確認 |
| :--- | :--- | :--- |
| `DPP` | 現在値 | ✅ 確認済（サンプル L618 / HTML マニュアル `pDPP`） |
| `DV` | 出来高（累積） | ⚠️ **未確認**。HTML マニュアルの `pDV`（履歴 API）と同名のため強い類推、要 EVENT 仕様 PDF |
| `DPP_TIME` | 現値 tick 時刻 | ⚠️ **未確認** |
| `DDT` | frame 配信時刻 | ⚠️ **未確認** |
| `GAK1..5` | 売気配 価格 1〜5 本目 | ⚠️ **未確認** |
| `GBK1..5` | 買気配 価格 1〜5 本目 | ⚠️ **未確認** |
| `GAS1..5` | 売気配 株数 1〜5 本目 | ⚠️ **未確認** |
| `GBS1..5` | 買気配 株数 1〜5 本目 | ⚠️ **未確認** |

### 11.3 ブロッカー扱いと対応方針（B3 再オープン）

implementation-plan.md T0.1 の規約「**実コード名と一致しないものは「未確認」マークして T0 内で解消するか、解消できないならその情報コードを使う実装タスク自体を Phase 1 から外す**」に従い:

**🔴 現状: T0 完了マーク (`[x]`) は B3 レビューで再オープン**。

> **重要度の再評価（Phase 1 生死に直結）**: 本ブロッカーが解消不能で縮退案（案 3）を取った場合、T5 の trade 合成・depth スナップショットが Phase 2 へ全繰越しとなり、Phase 1 の主要価値（trade/depth リアルタイム表示）が消滅する。spec.md §4 受け入れ条件 2〜3 も達成不能になる。**T1（codec）着手前にリスク識別し、T5 着手前に完全解消すること。** 案 3 を選んだ場合は下記「縮退時の計画更新リスト」をすべて実施してから T5 以降のタスクに着手する。

**責任者・期限の明示**: 本ブロッカーは **T0 担当者が T0 完了前に案 1/2/3 のいずれかを選択し、PR 説明文に解決証跡を記載することで完了**とする。「後でやる」は認めない。

**解消に必要なアクション（3 案のいずれか 1 つ）**:

1. **(推奨) `api_event_if_v4r7.pdf` を入手して `.claude/skills/tachibana/manual_files/` に同梱**し、§11.2 の暫定コード名を確定値で更新する。PDF は立花証券公式サイトから入手できるが URL は公開状態が変わりうるため、ダウンロード日と入手元を同梱 `README_event_if.txt` に記録する
2. **(代替) 実 frame キャプチャ**: ユーザー保有の Windows サンプル `e_api_websocket_receive_tel.py` をデモ環境に対して実行し、受信した FD frame の生バイト列（少なくとも 5 銘柄 × 30 秒分）を `manual_files/captured_fd_frames/*.bin` として保存。データから `p_<行>_<コード>` キー一覧を逆引きで確定し、§11.2.b の表を更新する
3. **(縮退) どちらも T1 着手前に不可なら**: 該当情報コード（`DV` / `GAK*` / `GBK*` / `GAS*` / `GBS*` / `DPP_TIME` / `DDT`）を **使う実装タスクを Phase 1 から外す**。着手前に以下の計画更新をすべて実施すること:
   - `spec.md §2.1` の「含めるもの」から FD ストリーム・DepthSnapshot 関連を削除し「Phase 2 送り」に移動
   - `spec.md §4` 受け入れ条件 2〜3 を修正（kline + ticker stats のみで成立するよう書き直す）
   - `data-mapping.md §3 / §4` を「Phase 2 へ繰越し（縮退）」とマーク
   - `implementation-plan.md T5` タスクを全件 `[ ]` → Phase 2 送りに移動
   - `architecture.md §4` Python ファイル構成から `tachibana_ws.py` を削除

**T1（codec）と T5（FD trade/depth）の着手前に、本 §11 を実体的に解決したかどうかを PR 説明文に明記すること**。`DPP` / `KP` / `ST` / `SS` / `US` / `EC` 確認済みコードのみで成立する範囲は T1 で先行着手してよい。

T1 / T5 着手前のチェックリストとしてこの §11 を参照すること。

## 12. 残課題（T0 内で完結しないが T0 で記録すべきもの）

- **`api_event_if_v4r7.pdf` 同梱**: §11.3 のブロッカー解消
- **旧 `state.json` 起動テスト**: `TickerInfo` フィールド追加後に既存 `saved-state.json` を読み込んで pane 復元・ticker 表示が壊れないこと → T0.2 末で実機確認
- **`Timeframe` 旧 serde 形式 (`"D1"`) からの互換ロード**: `#[serde(alias = "D1")]` 等で吸収するか、新形式のみ受け付けるかを T0.2 着手時に決定
