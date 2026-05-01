# 保有銘柄ペインを新設する

作成日: 2026-05-01
ステータス: planning
親計画: [docs/✅order/](./README.md) Phase O3 派生 UX 改善（implementation-plan.md T3.4 の延長）

## 1. 背景と問題

現在の発注フロー UI は以下の 3 ペインから成る:

| ペイン | `ContentKind` | 役割 | 取得 IPC |
|---|---|---|---|
| 注文入力 | `OrderEntry` | 銘柄・数量・価格を入力して `SubmitOrder` を送る | — |
| 注文一覧 | `OrderList` | 当日注文のステータス遷移（SUBMITTED → ACCEPTED → FILLED）を表示 | `GetOrderList` → `OrderListUpdated` |
| 買余力 | `BuyingPower` | 現物・信用の余力残高を表示 | `GetBuyingPower` → `BuyingPowerUpdated` |

**問題**: ユーザが現在保有している銘柄（=ポートフォリオ）を可視化する場所が
**どこにもない**。注文が `FILLED` になっても画面上は「注文ステータス」が変わるだけで、
「いま自分が何株持っているか」「評価額はいくらか」が確認できない。

具体例（ユーザ報告 2026-05-01）:
- 7203.TSE BUY 100 / 1100 を発注 → 注文一覧に `FILLED` と出る
- しかし「いま 7203 を 1200 株保有している」状態は UI 上に出ない
- ユーザは API レスポンスや Python ログ・立花ブラウザ画面を見ないと持ち高が分からない

**原因**:
- Python 側に `tachibana_orders.fetch_positions()`（`CLMGenbutuKabuList` + `CLMShinyouTategyokuList`）は **既に実装済み**（[python/engine/exchanges/tachibana_orders.py:1710](../../python/engine/exchanges/tachibana_orders.py#L1710), `implementation-plan.md` T3.2 ✅）
- しかし IPC コマンド `GetPositions` / イベント `PositionsUpdated` が **未定義**
- Rust 側にも保有銘柄を表示する panel / view が存在しない

## 2. ゴール

**保有銘柄を表示する専用ペインを新設する**。`OrderEntry` / `OrderList` / `BuyingPower`
と並ぶ第 4 の発注関連ペイン。注文一覧と同居せず、独立した `ContentKind::Positions` を作る。

理由:
- 注文一覧に同居させると `OrdersPanel` の責務が肥大化（過去のレビュー #review-fixes-2026-05-01.md でも指摘済み傾向）
- 余力 `BuyingPower` が独立ペインで成立しているのと整合
- 「保有銘柄をクリックして OrderEntry に銘柄を入れる」連携や「評価損益詳細」などの
  将来拡張に備え、独立ペインの方がレイアウトの自由度が高い
- ユーザは注文一覧と保有銘柄を **左右並列**で配置したい（同居だと縦方向に詰まる）

ペイン仕様:
- ヘッダー左: タイトル `保有銘柄`
- ヘッダー右: 「更新」ボタン + 取得中インジケータ「⟳ 更新中…」
- 本体: 1 列のスクロール可能な保有銘柄行リスト（区分「現物 / 信用」混在）
- venue ready 直後 / 注文約定 (`OrderFilled`) 直後に自動 fetch
- 手動「更新」ボタンで再取得（注文一覧の更新とは独立して走る）

### 2.1 非ゴール

- 評価損益（含み損益）計算（取得単価が `CLMGenbutuKabuList` で返らないケースがあるため別タスク）
- replay モードの保有銘柄表示（streaming `Position` イベントは別経路。本計画 §6.2 で扱い）
- 信用建玉の `tategyoku_id` 単位での詳細展開・返済操作 UI
- 保有銘柄行クリック → OrderEntry への銘柄連携（§6.4）
- 立花以外の venue（Binance / Bybit）への展開
- ソート切替・フィルタ UI

## 3. 設計

### 3.1 IPC スキーマ拡張（`engine-client/src/dto.rs` + `python/engine/schemas.py`）

#### 3.1.1 新規型 `PositionRecordWire`

`OrderRecordWire` と同じ tier の wire 型を追加する:

```rust
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PositionRecordWire {
    /// nautilus 形式の銘柄 ID（例 "7203.TSE"）
    pub instrument_id: String,
    /// 保有数量（株、整数文字列）
    pub qty: String,
    /// 評価額（円、整数文字列）。"" のとき不明（信用建玉で発生）
    pub market_value: String,
    /// "cash" | "margin_credit"
    pub position_type: String,
    /// 信用建玉番号（margin_credit のみ Some）
    pub tategyoku_id: Option<String>,
    /// venue 名（"tachibana" 固定）
    pub venue: String,
}
```

> **注意**: 現行 `fetch_positions()` 実装（`tachibana_orders.py:1794–1804`）では
> 信用建玉ループで `sTategyokuZanKingaku` を取得しておらず、`market_value` は
> dataclass デフォルト `0` になる。実装タスクで
> `market_value = int(item.get("sTategyokuZanKingaku") or 0)` 相当の取得処理を追加すること。

`qty` / `market_value` を **i64 ではなく String** で持つ理由:
- 大型株では評価額が i32 を超えるケースがある（i64 で足りるが nautilus 連携の Decimal
  契約に合わせ、文字列のまま受け渡し → UI 側で表示時のみ `i64::from_str` する）
- Python 側 `PositionRecord.market_value` が `int`（デフォルト 0）。0 は `"0"` として送出され、UI で `"¥0"` と表示する

#### 3.1.2 新規 `Command::GetPositions`

`GetBuyingPower` と対称に追加（[engine-client/src/dto.rs:196-199](../../engine-client/src/dto.rs#L196) 周辺）:

```rust
/// Fetch current positions (cash + margin) from the venue.
GetPositions {
    request_id: String,
    venue: String,
},
```

#### 3.1.3 新規 `Event::PositionsUpdated`

`BuyingPowerUpdated` と対称に追加（[engine-client/src/dto.rs:989-1003](../../engine-client/src/dto.rs#L989) 周辺）:

```rust
/// Response to `GetPositions`. Contains current positions held at the venue.
PositionsUpdated {
    request_id: String,
    venue: String,
    positions: Vec<PositionRecordWire>,
    /// 取得時刻 Unix ミリ秒
    ts_ms: i64,
},
```

#### 3.1.4 Python `schemas.py` Pydantic モデル追加

Rust DTO だけでなく、Python 側 IPC 契約も `python/engine/schemas.py` に Pydantic
モデルとして登録する（既存 `GetBuyingPower` = `schemas.py:270` /
`BuyingPowerUpdated` = `schemas.py:769` と対称）。

```python
# Command 側（Rust → Python）
# schemas.py:270 GetBuyingPower の直後に追加
class GetPositions(IpcMessage):
    op: Literal["GetPositions"] = "GetPositions"
    request_id: str
    venue: str


# Event 側（Python → Rust）
# schemas.py:769 BuyingPowerUpdated の直後に追加
class PositionRecord(IpcMessage):
    """Single position entry in PositionsUpdated."""

    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    qty: str  # 整数文字列
    market_value: str  # 整数文字列、"0" は ¥0 表示
    position_type: Literal["cash", "margin_credit", "margin_general"]
    tategyoku_id: str | None = None
    venue: Literal["tachibana"]


class PositionsUpdated(IpcMessage):
    """Response to GetPositions. Contains current positions held at the venue."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["PositionsUpdated"] = "PositionsUpdated"
    request_id: str
    venue: str
    positions: list[PositionRecord]
    ts_ms: int
```

`_dispatch` の `_DISPATCH_TABLE`（または相当のルーティング）にも `"GetPositions"`
キーを追加し、`GetPositions.model_validate(payload)` で型検証してから
`_do_get_positions` に渡す。

#### 3.1.5 SCHEMA_MINOR bump

- `engine-client/src/lib.rs` の `SCHEMA_MINOR` を +1
- `python/engine/schemas.py` の `SCHEMA_MINOR` を +1
- `SCHEMA_MAJOR` は据え置き（追加のみで破壊的変更なし）
- `docs/✅python-data-engine/schemas/commands.json` / `events.json` に新規エントリ追加
- `engine-client/tests/` 配下のラウンドトリップテストを更新

### 3.2 Python 側ディスパッチ（`python/engine/server.py`）

既存 `GetBuyingPower` ハンドラを参照モデルにして `GetPositions` を追加する。

`_dispatch` ループ内では `_spawn_fetch` を使い、処理本体を別メソッドに分離する:

```python
# _dispatch 内
elif cmd == "GetPositions":
    self._spawn_fetch(self._do_get_positions(msg), msg.get("request_id"))

# 別メソッド
async def _do_get_positions(self, msg: dict) -> None:
    req_id = msg.get("request_id", "")
    venue = msg.get("venue", "")
    if venue != "tachibana":
        self._outbox.append({"event": "Error", "request_id": req_id, "code": "unknown_venue", "message": f"venue not supported: {venue}"})
        return
    if not self._tachibana_session:
        self._outbox.append({"event": "Error", "request_id": req_id, "code": "SESSION_NOT_ESTABLISHED", "message": "session not ready"})
        return
    try:
        records = await fetch_positions(
            self._tachibana_session,
            p_no_counter=self._tachibana_p_no_counter,
        )
    except SessionExpiredError:
        self._session_holder.clear()  # セッションを無効化してログイン画面に戻す
        self._outbox.append({"event": "Error", "request_id": req_id, "code": "SESSION_EXPIRED", "message": "session expired"})
        return
    except TachibanaError as exc:
        self._outbox.append({"event": "Error", "request_id": req_id, "code": "fetch_error", "message": str(exc)})
        return
    # 注: 既存 GetBuyingPower ハンドラのレスポンスキー名と同様に "event" を使う
    self._outbox.append({
        "event": "PositionsUpdated",
        "request_id": req_id,
        "venue": venue,
        "positions": [
            {
                "instrument_id": r.instrument_id,
                "qty": str(r.qty),
                "market_value": str(r.market_value),
                "position_type": r.position_type,
                "tategyoku_id": r.tategyoku_id,
                "venue": "tachibana",
            }
            for r in records
        ],
        "ts_ms": int(time.time() * 1000),
    })
```

`_send_error` および `await self._send_event(...)` は使用しない。すべて `_outbox.append` パターンに統一する。

`SessionExpiredError` 経路は既存の `GetBuyingPower` と同じ `IpcError` を発行する。
`reason_code` は不要（買余力と同様）。

### 3.3 レイアウト型: `Pane::Positions` を新設（`data/src/layout/pane.rs`）

#### 3.3.1 `Pane` enum

`OrderList` / `BuyingPower` と対称に追加（[data/src/layout/pane.rs:102-109](../../data/src/layout/pane.rs#L102) 周辺）:

```rust
Positions {
    #[serde(deserialize_with = "ok_or_default", default)]
    link_group: Option<LinkGroup>,
},
```

#### 3.3.2 `ContentKind` enum

`OrderList` の隣に追加（[data/src/layout/pane.rs:230](../../data/src/layout/pane.rs#L230) 周辺）:

```rust
pub enum ContentKind {
    // ... 既存
    OrderList,
    BuyingPower,
    Positions,        // ★ 新設
    ReplayControl,
}

pub const ALL: [ContentKind; 13] = [
    // ... 既存 12 種 + Positions 1 = 13
    ContentKind::OrderList,
    ContentKind::BuyingPower,
    ContentKind::Positions,    // ★
    ContentKind::ReplayControl,
];
```

`label()` ([data/src/layout/pane.rs:268](../../data/src/layout/pane.rs#L268) 周辺):

```rust
ContentKind::Positions => "保有銘柄",
```

`uses_ticker_info()` 系のヘルパー / `match` 全箇所で `Positions` を `OrderList` と同等に扱う:
- 銘柄概念を持たない（`link_group=None` 強制、`ticker_info=None`）
- `from_config` で `link_group` を `None` 正規化
- `switch_tickers_in_group` の除外対象
- `is_orderbook_pane()` 等の各種マッチで対称扱い

#### 3.3.3 永続化互換性

**前提（実コード調査 2026-05-01）**: `saved-state.json` の読み込みは
`serde_json::from_str::<data::State>(&json)` の単純デシリアライズで、schema 交渉や
バージョン別フォールバック処理は持っていない（`src/main.rs:2509`
`Message::NativeOpenFileApply` ハンドラ。起動時の自動ロードも同経路）。

**前進方向（新版が旧 JSON を読む）**:
- 既存 `saved-state.json` には `Positions` バリアントは含まれていない
- 既存 JSON は新版で問題なくロード可能（追加バリアント・追加フィールドは新版が無視せず処理する）

**後退方向（旧版が新 JSON を読む = ロールバック）**:
- 旧版バイナリは `Pane::Positions` バリアントを知らない
- `serde_json::from_str` は untagged でない adjacently-tagged enum で
  unknown variant に出会うと **エラーで失敗する**
- 失敗するとアプリは「無効な設定ファイルです」Toast を出して `restart()` を呼ばず、
  既存 `saved-state.json` を上書きしない（自動ロードでも同じ。デフォルト状態で起動）
- `data/src/layout/pane.rs` の `Pane` / `ContentKind` enum には
  `#[serde(deny_unknown_fields)]` は付いていないが、これはフィールドレベルの設定で
  enum variant unknown には影響しない
- **Starter フォールバックは発生しない**（過去ラウンドで誤って書いた説明を訂正）

**運用上の方針（本計画で確定）**:
- `SCHEMA_MAJOR` は据え置く（追加変更のため）
- `SCHEMA_MINOR` を +1 する（変更があったことを記録するのみ。互換性チェックには使わない。
  IPC ハンドシェイクは MAJOR 一致のみ要求 = `engine-client/src/lib.rs` のロジック準拠）
- ロールバック時は「旧版が新 JSON を弾いてデフォルト起動 → ユーザーが
  別の `saved-state.json` を選び直すか初期状態でやり直し」となる
- マイグレーションスクリプトは追加しない（ロールバックは想定運用ではない）
- 計画書に `Positions` 追加の影響範囲としてロールバック非互換を明記する

### 3.4 Rust UI: `PositionsPanel` を新設

#### 3.4.1 新規ファイル `src/screen/dashboard/panel/positions.rs`

`OrdersPanel` のパターンを踏襲して以下を持つ:

```rust
//! Phase O3 UX — Positions panel.
//!
//! Displays current cash + margin positions held at the venue.
//! Refresh button fires `GetPositions` IPC; `PositionsUpdated` populates the table.

use engine_client::dto::PositionRecordWire;
use iced::{Element, widget::{button, center, column, container, row, scrollable, text}};

#[derive(Debug, Default)]
pub struct PositionsPanel {
    positions: Vec<PositionRecordWire>,
    /// True when this panel is shown in REPLAY mode (banner + no live IPC).
    pub is_replay: bool,
    /// Loading badge ("⟳ 更新中…") flag.
    loading: bool,
    /// Error message from latest fetch.
    last_error: Option<String>,
    /// 最終更新時刻（Unix ミリ秒）
    last_updated_ms: Option<i64>,
}

impl PositionsPanel {
    pub fn new() -> Self { Self::default() }

    pub fn new_replay() -> Self {
        Self { is_replay: true, ..Self::default() }
    }

    pub fn set_positions(&mut self, positions: Vec<PositionRecordWire>, ts_ms: i64) {
        self.positions = positions;
        self.last_updated_ms = Some(ts_ms);
        self.last_error = None;
        self.loading = false;
    }

    pub fn set_loading(&mut self, loading: bool) {
        if loading {
            self.last_error = None;     // stale-error 対策（inline-loading-indicator-plan.md と整合）
        }
        self.loading = loading;
    }

    pub fn set_error(&mut self, message: String) {
        self.last_error = Some(message);
        self.loading = false;
    }

    pub fn position_count(&self) -> usize { self.positions.len() }
    pub fn is_empty(&self) -> bool { self.positions.is_empty() }
}

#[derive(Debug, Clone)]
pub enum Message {
    RefreshClicked,
}

#[derive(Debug, Clone)]
pub enum Action {
    /// Request a fresh positions list via `GetPositions` IPC.
    RequestPositions,
}

pub fn update(panel: &mut PositionsPanel, msg: Message) -> Option<Action> {
    match msg {
        // REPLAY pane では IPC を発行しない（OrdersPanel と整合）
        Message::RefreshClicked if panel.is_replay => None,
        Message::RefreshClicked => Some(Action::RequestPositions),
    }
}
```

view は `OrdersPanel::view` を踏襲（タイトル + 更新ボタン + 「⟳ 更新中…」 + エラー表示 + リスト）。
`loading` / `last_error` の規約は `OrdersPanel` のパターンを踏襲する（`BuyingPowerPanel` には `loading` フィールドが存在しないため参照モデルとしては使わない）。

#### 3.4.2 行フォーマット

```
7203.TSE  現物  1200 株  ¥3,456,000
9984.TSE  信用  100 株   ¥2,134,500   [建T-12345]
6758.TSE  信用  50 株    -
```

- 区分ラベル変換表:

  | `position_type` 値 | 表示ラベル |
  |---|---|
  | `"cash"` | `"現物"` |
  | `"margin_credit"` | `"信用(信用)"` |
  | `"margin_general"` | `"信用(一般)"` |
  | 上記以外 | 原文をそのまま表示（または `"-"`）|

  `position_type` の既知の値は `"cash"` / `"margin_credit"` / `"margin_general"`（tachibana_orders.py 行 1540 コメント参照）。上記以外は原文を表示する防御的フォールバックを設ける。
- 数量: `qty` を `i64::from_str` してから千区切り（パース失敗時はそのまま `qty` 文字列を表示）
- 評価額: `market_value` が `"0"` の場合は `"¥0"` と表示する。空文字 `""` または `i64::from_str` 失敗時は `"-"`（防御的フォールバック）
- `tategyoku_id` が `Some` のとき末尾に `[建{id}]` を表示

「保有銘柄」ペインに取消・訂正・売却などの操作ボタンは置かない（本計画は閲覧のみ）。

#### 3.4.3 ソート順

- `position_type` で安定ソート（現物先・信用後）
- 同区分内では `instrument_id` 昇順
- ソートは Rust 側で行う（Python 側は API レスポンス順をそのまま渡す）
- ソート切替は本計画スコープ外

#### 3.4.4 panel.rs / pane.rs の編集

`src/screen/dashboard/panel.rs` に `pub mod positions;` を追加。

`src/screen/dashboard/pane.rs` の `Content` enum / `from_config` / `kind()` ほか
すべての match を `OrderList` と対称に拡張（[src/screen/dashboard/pane.rs:2206](../../src/screen/dashboard/pane.rs#L2206) `Content::OrderList(...)` の隣に `Content::Positions(panel::positions::PositionsPanel)` を追加し、各 match 文を網羅）。

`pub fn kind()` の戻り値:

```rust
Content::Positions(_) => ContentKind::Positions,
```

`label()` の戻り値:

```rust
Content::Positions(_) => Some("保有銘柄"),
```

`initialized()` は **常に `true`**（OrderList と同様、銘柄選択不要）。

### 3.5 Dashboard 配信ヘルパ（`src/screen/dashboard.rs`）

参照モデルは `distribute_order_list`（`distribute_buying_power` ではない）。
`distribute_order_list` のパターンを踏襲して 3 つ + ヘルパ 1 つを追加:

```rust
/// Positions ペインが存在するか確認するヘルパ。
/// VenueReady auto-fetch ガードで使用する。
pub fn has_positions_pane(&self, main_window: window::Id) -> bool {
    self.iter_all_panes(main_window)
        .any(|(_, _, state)| matches!(state.content, pane::Content::Positions(_)))
}
```

3 配信ヘルパ:

```rust
pub fn distribute_positions(
    &mut self,
    main_window: window::Id,
    positions: Vec<PositionRecordWire>,
    ts_ms: i64,
) {
    self.iter_all_panes_mut(main_window).for_each(|(_, _, state)| {
        if let pane::Content::Positions(panel) = &mut state.content {
            panel.set_positions(positions.clone(), ts_ms);
        }
    });
}

pub fn distribute_positions_loading(
    &mut self, main_window: window::Id, loading: bool,
) {
    self.iter_all_panes_mut(main_window).for_each(|(_, _, state)| {
        if let pane::Content::Positions(panel) = &mut state.content {
            panel.set_loading(loading);
        }
    });
}

pub fn distribute_positions_error(
    &mut self, main_window: window::Id, message: String,
) {
    self.iter_all_panes_mut(main_window).for_each(|(_, _, state)| {
        if let pane::Content::Positions(panel) = &mut state.content {
            panel.set_error(message.clone());
        }
    });
}
```

> **禁止規約**: `distribute_positions_loading(main_window, false)` を外部から呼ぶのは
> 切断・再接続経路（`EngineRestarting(true)` / `EngineConnected`）専用。
> `notify_engine_disconnected` は OrderEntry 向けのみなので Positions の loading 解除には使わない。
> `PositionsSendCompleted(Err)` など完了 setter 経路からも呼ばない（二重呼び出し禁止 /
> inline-loading-indicator-plan.md 統一決定 2 準拠）。

### 3.6 sidebar の追加（`src/screen/dashboard/sidebar.rs`）

[src/screen/dashboard/sidebar.rs:267-283](../../src/screen/dashboard/sidebar.rs#L267) の `order_menu_view()` に
「保有銘柄」ボタンを追加:

```rust
let positions_btn = iced::widget::button(iced::widget::text("保有銘柄").size(13))
    .on_press(Message::OrderPanelRequested(ContentKind::Positions))
    .style(|theme, status| crate::style::button::transparent(theme, status, false));

column![entry_btn, list_btn, power_btn, positions_btn].spacing(4)
```

ボタンの並び順: 注文入力 → 注文一覧 → 買余力 → 保有銘柄。

### 3.7 main.rs のトリガ配線

#### 3.7.1 自動 fetch ポイント

| トリガ | 既存（OrderList） | 追加（Positions） |
|---|---|---|
| `Message::TachibanaVenueEvent(VenueEvent::Ready)` ハンドラ内（`src/main.rs:1529–1635` 周辺） | [src/main.rs:1607](../../src/main.rs#L1607) auto-fetch | 同所で **`GetPositions` も同時発行**（`has_positions_pane(main_window) && tachibana_state.is_ready() && positions_request_id.is_none()` ガード付き） |
| `Message::EngineConnected`（リセット専用） | — | **auto-fetch しない**。`EngineConnected` は `positions_request_id = None` + loading 解除のリセットのみ行い、`GetPositions` を発行しない |
| `OpenOrderPanel(ContentKind::Positions)` ハンドラ | — | サイドバーから新規ペイン追加時に auto-fetch（`buying_power` の T3.5 修正と同方針 — `tachibana_state.is_ready()` + `positions_request_id.is_none()` ガード付き） |
| 「更新」ボタン | `Action::RequestOrderList` | `Action::RequestPositions` |
| `OrderAccepted` 受信後 | [src/main.rs:2541](../../src/main.rs#L2541) | **追加しない**（受付だけでは持ち高は変わらない） |
| **★ `OrderFilled` 受信後（新規）** | — | `src/main.rs の OrderToast ハンドラ内で live モード && positions_request_id.is_none() のときに GetPositions を追加発行する。新 Message バリアント不要`。現行コード (`src/main.rs:1067–1083`) では `EngineEvent::OrderFilled` は `Message::OrderToast` に変換されるため、その既存ハンドラ内で発行する |
| **★ `OrderCanceled` 受信後** | — | **追加しない**（取消は持ち高に影響しない） |

`OrderFilled` の連射対策:
- 1 つのバッチ約定で `OrderFilled` が連発するケースに備え
  `positions_request_id.is_some()` で 2 重発行を抑止
- 抑止中に発生したフィルは反映が 1 回遅れる（許容）
- 完了後（`PositionsUpdated` または `IpcError`）に `None` に戻す
- replay 中の `OrderFilled` では `mode == replay` ガードで `GetPositions` 発行をスキップする

#### 3.7.2 in-flight tracking

`buying_power_request_id` と対称:

```rust
positions_request_id: Option<String>,    // ★ 新設
```

- `GetPositions` 送信時に `Some(req_id)` を立てる
- `Event::PositionsUpdated` 受信時に request_id 一致で `None` に戻す
- `IpcError` 経路で同じ request_id 一致時に `None` に戻す
- `EngineConnected` ハンドラで `positions_request_id = None` + `distribute_positions_loading(false)` リセットを行う（auto-fetch はしない。R3-UD1 参照）
- `EngineRestarting(true)` ブロック（`src/main.rs:1393`）に `positions_request_id = None` + `distribute_positions_loading(main_window, false)` を追加する（`buying_power_request_id` リセット・`src/main.rs:1393–1411` と対称）
- `notify_engine_disconnected` 関数自体は **OrderEntry 向けのみ**なので変更しない。切断時の Positions リセットは `EngineRestarting(true)` ブロック側で完結する

#### 3.7.3 PositionsAction ハンドラ

`OrdersAction` / `BuyingPowerAction` と対称に新規:

```rust
pub enum Message {
    // ... 既存
    PositionsAction(panel::positions::Action),
    PositionsSendCompleted(Result<(), String>),
}
```

`PositionsAction(Action::RequestPositions)` ハンドラ:

1. `engine_conn` が `None` → error toast → `Task::none()`（loading は立てない）
2. `positions_request_id.is_some()` → 早期 return（重複抑止）
3. `distribute_positions_loading(main_window, true)`
4. `positions_request_id = Some(req_id)`
5. `Task::perform(...)` で送信

`PositionsSendCompleted(Ok(()))` ハンドラ:

```rust
Message::PositionsSendCompleted(Ok(())) => Task::none(),
```

`PositionsSendCompleted(Err(err))` ハンドラ:

```rust
self.active_dashboard_mut()
    .distribute_positions_error(main_window, err.clone());
self.positions_request_id = None;
self.notifications.push(Toast::error(format!("保有銘柄取得失敗: {err}")));
Task::none()
```

`PositionsSendCompleted(Err)` のとき `distribute_positions_loading(false)` は呼ばない。
`set_error` 内で `loading = false` が一元化されるため二重呼び出しは不要
（inline-loading-indicator-plan.md 統一決定 2 と同方針）。

`Event::PositionsUpdated` ハンドラ:

```rust
// request_id 不一致（stale）は early return。distribute_positions は呼ばない。
// positions_request_id も None に戻さない（in-flight 維持）。
let Some(rid) = &self.positions_request_id else {
    // active な request が無い（stale）。無視する。
    return Task::none();
};
if rid != &request_id {
    // request_id ミスマッチ（古いレスポンス）。無視する。
    return Task::none();
}
self.positions_request_id = None;
self.active_dashboard_mut()
    .distribute_positions(main_window, positions, ts_ms);
Task::none()
```

`IpcError` 経路では既存 `buying_power_request_id` / `order_list_request_id` の照合に
`positions_request_id` を追加（[src/main.rs:2282-2305](../../src/main.rs#L2282) 周辺）。

`IpcError` 経路（request_id 一致時）の擬似コード:

```rust
Event::IpcError { request_id: Some(id), message, .. }
    if Some(&id) == self.positions_request_id.as_ref() =>
{
    self.positions_request_id = None;
    self.active_dashboard_mut()
        .distribute_positions_error(main_window, message);
    Task::none()
}
```

#### 3.7.4 既存 Message 経路の対称化

`Message::OpenOrderPanel(ContentKind::Positions)` ハンドラを追加する箇所
（`OrderList` / `BuyingPower` と並列）:
- ペインが新規生成された時点で venue ready なら自動 fetch
- `tachibana_state.is_ready() && positions_request_id.is_none()` のガード必須
  （T3.5 修正 [docs/✅order/fix-buying-power-auto-fetch-on-add-2026-04-28.md](./fix-buying-power-auto-fetch-on-add-2026-04-28.md) と同方針）

**既知の制限**: `OpenOrderPanel(ContentKind::OrderList)` のキャッチアップは現行コード
（`src/main.rs:3053–3085`）に未実装のままであり、本計画のスコープ外とする。
Positions のキャッチアップは `ContentKind::BuyingPower` 実装と同様に追加するが、
OrderList の欠落は別タスクで対処する。

### 3.8 link_group / ticker_info の扱い

`Positions` ペインは銘柄概念を持たない:

- `link_group` は常に `None` に正規化（`from_config` の OrderList / BuyingPower 同等）
- `ticker_info` フィールドは持たない
- `switch_tickers_in_group` の除外対象（`Starter` / `OrderList` / `BuyingPower` と同列扱い）
- `linked_ticker()` は常に `None`

[pane.rs:670-675](../../src/screen/dashboard/pane.rs#L670) の
「銘柄概念を持たないペイン」コメント該当箇所を `Content::Positions` 含めて拡張:

```rust
Content::Starter | Content::OrderList(_) | Content::BuyingPower(_) | Content::Positions(_)
```

`pane.rs` の `top_left_buttons` 生成 match（`Content::Starter | Content::OrderList(_) | Content::BuyingPower(_)` の OR パターン）にも `Content::Positions(_)` を追加すること。

## 4. 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| [engine-client/src/dto.rs](../../engine-client/src/dto.rs) | `PositionRecordWire` 型 + `Command::GetPositions` + `Event::PositionsUpdated` |
| [engine-client/src/lib.rs](../../engine-client/src/lib.rs) | `SCHEMA_MINOR` を +1 |
| [python/engine/schemas.py](../../python/engine/schemas.py) | `GetPositions` / `PositionRecord` / `PositionsUpdated` Pydantic モデル追加（既存 `GetBuyingPower` / `BuyingPowerUpdated` と対称、§3.1.4）+ `SCHEMA_MINOR` を +1 |
| [docs/✅python-data-engine/schemas/commands.json](../✅python-data-engine/schemas/commands.json) | `GetPositions` を追加 |
| [docs/✅python-data-engine/schemas/events.json](../✅python-data-engine/schemas/events.json) | `PositionsUpdated` を追加 |
| [python/engine/server.py](../../python/engine/server.py) | `GetPositions` ディスパッチ → `fetch_positions` 呼出 → `PositionsUpdated` 送出 |
| [data/src/layout/pane.rs](../../data/src/layout/pane.rs) | `Pane::Positions` バリアント + `ContentKind::Positions` + `ALL` 配列 + `label()` 拡張 + 全 match 網羅 |
| `src/screen/dashboard/panel/positions.rs` (新規) | `PositionsPanel` + `Message::RefreshClicked` + `Action::RequestPositions` + view |
| [src/screen/dashboard/panel.rs](../../src/screen/dashboard/panel.rs) | `pub mod positions;` |
| [src/screen/dashboard/pane.rs](../../src/screen/dashboard/pane.rs) | `Content::Positions(PositionsPanel)` バリアント追加 + 全 match 拡張 + `kind()` / `label()` / `initialized()` / `from_config` / `switch_tickers_in_group` 除外対称化 |
| [src/screen/dashboard.rs](../../src/screen/dashboard.rs) | `distribute_positions` / `distribute_positions_loading` / `distribute_positions_error` / `has_positions_pane()` ヘルパー追加（`notify_engine_disconnected` は変更しない。切断時リセットは `EngineRestarting(true)` ブロックで実施） |
| [src/screen/dashboard/sidebar.rs](../../src/screen/dashboard/sidebar.rs) | `order_menu_view()` に「保有銘柄」ボタン追加 |
| [src/main.rs](../../src/main.rs) | `positions_request_id` フィールド + `Message::PositionsAction` / `PositionsSendCompleted` + `Event::PositionsUpdated` ハンドラ + venue ready / OpenOrderPanel(Positions) / OrderFilled 自動 fetch + `IpcError` 経路 + `EngineConnected` リセット |
| [python/engine/exchanges/tachibana_orders.py](../../python/engine/exchanges/tachibana_orders.py) | `fetch_positions` の信用建玉ループに `sTategyokuZanKingaku` 取得処理を追加（`market_value = int(item.get("sTategyokuZanKingaku") or 0)`） |
| `python/tests/test_tachibana_positions_dispatch.py` (新規) | `GetPositions` ディスパッチのモックテスト |
| `engine-client/tests/positions_roundtrip.rs` (新規) | `Event::PositionsUpdated` の wire ラウンドトリップ |
| `src/screen/dashboard/panel/positions.rs` 内 `#[cfg(test)] mod tests` | パネル単体テスト |

## 5. テスト計画

実行コマンド: `cargo test --workspace` + `uv run pytest python/tests/ -v`

### 5.1 Python ユニットテスト

新規 `python/tests/test_tachibana_positions_dispatch.py`:

- `GetPositions` 送信 → `fetch_positions` モック → `PositionsUpdated` イベント送出を確認
  （cash のみ / margin のみ / 混在 / 空配列 の 4 ケース）
- `tachibana_session` 未確立で wire `"event": "Error"` を返すこと（`code: "SESSION_NOT_ESTABLISHED"`）
- `SessionExpiredError` を発生させたとき wire `"event": "Error"` を返すこと（`code: "SESSION_EXPIRED"`、`reason_code` は付与しない）
- venue が `"tachibana"` 以外（例 "binance"）で wire `"event": "Error"` を返すこと（`code: "unknown_venue"`）

> 用語: Python が outbox に積む wire event 名は `Error`（`schemas.py:461` 既定）。
> Rust の `engine-client` 側は `Event::IpcError` バリアントとしてデシリアライズする（型名のみ異なる、wire は `Error`）。テストでは Python wire 名 `Error` でアサートする。
- `market_value` が `0` の `PositionRecord` で `"0"` に変換されること（`int` デフォルト 0 は `"0"` として送出）
- `sTategyokuZanKingaku = "2134500"` の信用建玉が `market_value = 2134500` → wire `"2134500"` として正しく変換されること（R2-H5 実装タスク完了後に追加）

参考: 既存 `python/tests/test_tachibana_buying_power.py` の dispatch テストパターン踏襲。

### 5.2 Rust ユニットテスト（`src/screen/dashboard/panel/positions.rs`）

`#[cfg(test)] mod tests` に追加:

- `position_count_starts_at_zero` — `new()` 直後は 0
- `set_positions` で `position_count()` が更新される
- `set_positions(vec![], ts)` で `position_count() == 0`
- `set_positions` 後に `loading == false` かつ `last_error == None`
- `set_loading(true)` 後に `set_positions` → `loading == false`
- `set_loading(true)` 後に `set_error("...")` → `loading == false` かつ `last_error == Some(_)`
- **stale-error クリア（HIGH）**: `set_error("...")` 後に `set_loading(true)` →
  `last_error == None` かつ `loading == true`
- **成功時 stale-error クリア**: `set_error("...")` 後に `set_positions(...)` →
  `last_error == None`
- `RefreshClicked` (live) → `Some(Action::RequestPositions)`
- `RefreshClicked` (replay) → `None`（IPC 発行しない）

**ライフサイクル統合テスト（`main.rs` ハンドラのロジック）**:
- `positions_request_id.is_some()` 時に `OrderFilled` が来ても `GetPositions` が 1 回しか発行されないことをアサートする（OrderFilled 連射 in-flight ガードの検証）
- `set_loading(true)` 後に `EngineConnected` 相当（= `set_loading(false)` + `positions_request_id = None`）で `loading == false` になること
- `EngineRestarting(true)` ハンドラ後に `loading == false` になること（`notify_engine_disconnected` は Positions に触れないため対象外）

### 5.3 IPC ラウンドトリップ（`engine-client/tests/positions_roundtrip.rs`）

- `Command::GetPositions { request_id, venue }` が JSON シリアライズ → デシリアライズで完全一致
- `Event::PositionsUpdated` が同様に完全一致
- `PositionRecordWire.tategyoku_id: None` / `Some("...")` 両方
- `market_value: ""`（空文字）を許容できることの確認
- 複数 `PositionRecordWire` を含む `Vec` の round-trip

参考: 既存 `engine-client/tests/` 配下のラウンドトリップパターン踏襲。

### 5.4 統合テスト（`engine-client/tests/`）

`tokio-tungstenite` モックで:

- `GetPositions` 送信 → `PositionsUpdated` モックレスポンス → クライアント側で
  `Event::PositionsUpdated` を受信できる
- request_id ミスマッチで `Event::PositionsUpdated` が来たケース: `distribute_positions` が呼ばれず、`positions_request_id.is_some()` が維持されること
- `IpcError` で `request_id` 一致時に `positions_request_id` 解除に相当する挙動

サンプル関数名例:
- `positions_updated_roundtrip_empty_vec`
- `positions_updated_roundtrip_cash_and_margin`
- `positions_updated_roundtrip_tategyoku_id_none`
- `get_positions_command_roundtrip`

### 5.5 永続化互換テスト（`data/src/layout/pane.rs`）

- 既存 `saved-state.json`（`Positions` バリアント無し）が問題なく読み込める
- `Pane::Positions { link_group: None }` が JSON ラウンドトリップで保持される
- 旧版 JSON（`Pane::Positions` を含まない）が新版で問題なくロードされること（前進互換）
- 新版 JSON（`Pane::Positions` を含む）を旧版バイナリ相当でデシリアライズすると
  `serde_json::Error` で失敗すること（§3.3.3 の挙動確定。Starter フォールバックは起きない）。
  旧版は失敗時にデフォルト状態で起動するため `saved-state.json` を上書きしない

### 5.6 画面目視チェック（debug ビルド）

```bash
cargo build
FLOWSURFACE_ENGINE_TOKEN=dev-token cargo run -- --mode live --data-engine-url ws://127.0.0.1:19876/
```

1. 立花デモログイン → サイドバーから「保有銘柄」ボタンを押下 → 新規ペイン生成を確認
2. ペインに既存保有が表示されることを確認（venue ready 直後 + 新規 open 双方で auto-fetch）
3. 7203.TSE BUY 100 を発注 → `FILLED` 後、保有銘柄ペインに 7203 数量が +100 反映されることを確認
4. 「更新」ボタンを押すと「⟳ 更新中…」バッジが点灯 → 完了で消灯
5. エンジンを停止 → 「更新」ボタン押下でエラー表示が出ることを確認
   再起動 → 再 Refresh でエラーが消え、loading バッジが出ることを確認（stale-error クリア）
6. 保有なしのアカウントで「保有なし」が表示されることを確認
7. 注文一覧ペインと保有銘柄ペインを左右に並べてレイアウト保存 → 再起動 → レイアウトが復元される
8. 信用建玉を持つアカウントで `[建T-12345]` 表示と `market_value=""` フォールバック `"-"` の目視確認
9. `--mode replay` で起動 → サイドバーから「保有銘柄」ペインを追加 → 空表示固定（「⏪ REPLAY」バナーまたは「保有なし」表示）であることを確認。「更新」ボタンをクリックしても IPC が発行されないことをログで確認

### 5.7 grep リグレッションガード

`inline-loading-indicator-plan.md` §5.3 と同じ方針で、新規禁止文字列があれば
`tests/regression_*.rs` に追加する（本計画では新規導入なし）。

### 5.8 invariant-tests.md の扱い

- I-Position-1（新設）:
  「`OrderFilled` 受信後、`positions_request_id.is_none()` のとき最大 1 回の
  `GetPositions` が発行され、`PositionsUpdated` 受信で in-flight ガードが解除される」
- I-Position-2:
  「`Pane::Positions` の `link_group` は `from_config` で常に `None` に正規化される」

I-Position-1 / I-Position-2 は **`invariant-tests.md` には登録せず、Rust ユニットテスト内コメントにとどめる**。

> **注**: `test_invariant_tests_doc.py` は `spec.md §6` との照合を行わない（内部整合チェックのみ）。
> `spec.md §6` への追記は不要であり、省略しても `test_invariant_tests_doc.py` は FAIL しない。

### 5.9 CI ゲート組込の確認

既存 CI ワークフロー（`.github/workflows/` 内の `cargo test --workspace` / `uv run pytest` ジョブ）に
本計画で追加するテストが自動実行されることを確認する（テスト存在と CI 組込は別物）。
新規テストファイルが CI のテスト収集対象ディレクトリ内にあること。

## 6. 非ゴール / 後回し

### 6.1 評価損益（含み損益）

`CLMGenbutuKabuList` は `sGenbutuZanKingaku`（評価額）を返すが、取得単価は別 API
（`CLMOrderListDetail` 約定単価などからの集計）が必要。本計画では評価額のみを表示し、
損益計算は別タスクとする。

### 6.2 replay モードの保有銘柄

replay は `portfolio_view.py` が streaming `Position` イベントを送出するため、
fetch ベースの本計画とは経路が異なる。replay 用 `PositionsPanel(is_replay=true)` は
**作成可能だが空表示で固定**（バナー「⏪ REPLAY」+ 「保有なし」）とし、
streaming `Position` イベントは別計画で受け取る。

将来 replay 仮想ポートフォリオを表示する場合は別タスクで `ReplayPositionUpdated`
イベントを追加する（本計画と独立）。

### 6.3 信用建玉の詳細展開

同一銘柄の建玉が複数（建日違い）の場合、本計画では `tategyoku_id` ごとに 1 行ずつ
表示する。建玉単位での返済操作（`tatebi_type=1` 個別返済）UI は実装しない。

### 6.4 持ち高クリック → OrderEntry 連携

OrderEntry の `link_group` 仕組みと同様に「保有銘柄行をクリックすると OrderEntry の
銘柄欄に入る」連携は本計画スコープ外。`Positions` ペインに `link_group` を持たせない
設計（§3.8）から外れるため、別計画で `link_group` 設計から再検討する。

### 6.5 Positions ペインを既定レイアウトに含めるか

新規ペインなので既定レイアウト（初回起動時）に含めるかは検討対象だが、
本計画では **既定に含めない**（ユーザがサイドバーから手動で開く）。
既定に含めるかは実装後の UX フィードバック待ち。

## 7. 想定リスク

### 7.1 約定ラッシュでの GetPositions 連射

1 注文が複数フィルに分割されると `OrderFilled` が連続発火し、`GetPositions` も
連射される。in-flight ガード（`positions_request_id.is_some()`）で抑止する設計だが、
**抑止中に発生したフィルは反映が 1 回遅れる**ことを許容する。

緩和策（本計画スコープ外）:
- `OrderFilled` 受信からデバウンス 200ms 後に `GetPositions` を発行
- streaming `Position` イベント（EVENT IF の `EC` フレーム）を Python 側で構築して push

### 7.2 立花 API のレート制限

`CLMGenbutuKabuList` + `CLMShinyouTategyokuList` は 2 回 HTTP 呼出。
venue ready 直後 / OpenOrderPanel(Positions) / 手動 Refresh / 約定後のすべてで両方走るため、
ピーク時で 1 約定あたり 2 リクエスト追加される。

立花仕様書（`api_request_if_v4r7.pdf`）にレート制限の明文記述はないが、
1 秒間に 5 リクエスト以下に収まるよう運用する想定。

### 7.3 `market_value` が空文字で返るケース

信用建玉では `sTategyokuZanKingaku` が空文字 `""` で返ることがある
（[python/engine/exchanges/tachibana_orders.py:1793](../../python/engine/exchanges/tachibana_orders.py#L1793) 周辺）。

UI 側は `i64::from_str` 失敗時に `"-"` を表示する分岐を必須にする。

### 7.4 SCHEMA_MINOR bump 漏れ

新規 IPC 追加時の Rust / Python 両 `SCHEMA_MINOR` 同時更新は MISSES.md にも
記録されているクラスの見逃し。`/ipc-schema-check` で必ず検証する。

### 7.5 Pane::Positions バリアント追加によるパネル match の見落とし

`Content` / `Pane` enum に新規バリアントを追加すると、ワイルドカード `_ =>` を使っている
match でコンパイルが通ってしまうため、暗黙の漏れが発生し得る。

対策:
- 既存の `Content::OrderList(_) | Content::BuyingPower(_) | Content::Starter` などの
  ORed パターンに `Content::Positions(_)` を **必ず明示追加**
- ワイルドカード `_ =>` は使わない（既存コードの慣習踏襲）
- `pane.rs` 内のすべての match にコンパイラでパターンを書かせるため、
  非ワイルドカードを保つ

### 7.6 既定レイアウトに無い → ユーザが「機能なし」と誤認

ユーザがサイドバーから手動で「保有銘柄」を選ばない限りペインが出ない。
README / wiki に表示方法を記載する（別 PR で対応）。

## 8. 進め方の段階分け

| Phase | 内容 | 完了条件 |
|---|---|---|
| **PP1** ✅ | IPC スキーマ追加 + Python ディスパッチ + ラウンドトリップテスト | `python/tests/test_tachibana_positions_dispatch.py` パス + Rust ラウンドトリップテストパス |
| **PP2** ✅ | レイアウト型 (`Pane::Positions` / `ContentKind::Positions`) + 永続化互換テスト | `cargo test -p data` パス + serde ラウンドトリップ通過 |
| **PP3** | `PositionsPanel` 新設 + `pane.rs` 全 match 拡張 | `cargo test -p flowsurface --lib panel::positions` パス + コンパイル成功 |
| **PP4** | sidebar ボタン + main.rs 配線（venue ready / OpenOrderPanel / Refresh / OrderFilled） | デモ環境で約定 → 自動更新確認 |
| **PP5** | 信用建玉表示 / 評価額空文字フォールバック / 目視確認 | 信用銘柄を持つアカウントで目視確認 |

PP1〜PP2 を 1 つの PR で先行マージすると、Python 側の動作確認（pytest）と
レイアウト型の互換性が独立に検証できる。PP3〜PP4 は UI 一括で 1 PR が読みやすい。

## 9. 関連計画との整合

| 計画 | 関係 |
|---|---|
| [docs/✅order/inline-loading-indicator-plan.md](./inline-loading-indicator-plan.md) | `PositionsPanel.loading` / `last_error` の規約はこの計画を踏襲。`distribute_positions_loading` / `_error` の責務は OrderList / BuyingPower と同方針 |
| [docs/✅order/order-entry-link-group-plan.md](./order-entry-link-group-plan.md) | 独立。本計画は `Positions` ペインに `link_group` を持たせない |
| [docs/✅order/implementation-plan.md](./implementation-plan.md) | T3.2 ✅ で実装済みの `fetch_positions` を IPC 配線して新ペインに出すフォローアップ |
| [docs/✅order/fix-buying-power-auto-fetch-on-add-2026-04-28.md](./fix-buying-power-auto-fetch-on-add-2026-04-28.md) | OpenOrderPanel(Positions) ハンドラの auto-fetch ガード設計をそのまま転用 |
| [docs/✅nautilus_trader/](../✅nautilus_trader/) | replay モードの仮想 Position は本計画対象外（§6.2） |
| [docs/✅python-data-engine/schemas/](../✅python-data-engine/schemas/) | スキーマ minor bump（§3.1.4） |
