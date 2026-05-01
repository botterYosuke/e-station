# 注文入力ペインの link_group 同期 実装計画

## 背景

注文入力ペイン（`Content::OrderEntry`）には**銘柄**という概念がある（板・チャートと同じ）。
ユーザーは画面左上の `[-]` ボタン（`link_group_button`）を 1〜9 のリンクグループ番号に
合わせることで、同じ番号のチャート・板と銘柄を同期させたい。

現状はタイトルバーに `[-]` ボタンは描画されている（`Content::Starter` 以外は描画する分岐
[src/screen/dashboard/pane.rs:642-651](../../src/screen/dashboard/pane.rs#L642-L651)）が、
**実際にグループに参加させても銘柄が伝播しない**。理由を以下に分解する。

## なぜ今のままでは動かないか

注文入力は他のペインと違い `streams: ResolvedStream` を持たず、銘柄は
`OrderEntryPanel.instrument_id: Option<String>` に直接保持される
（[src/screen/dashboard/panel/order_entry.rs:60-76](../../src/screen/dashboard/panel/order_entry.rs#L60-L76)）。

このため既存のリンクグループ伝播パスは 3 箇所で素通りする：

### 1. グループ参加時に銘柄を取り込めない

[src/screen/dashboard.rs:376-385](../../src/screen/dashboard.rs#L376-L385) の
`SwitchLinkGroup` ハンドラは `other_state.stream_pair()` を呼んで仲間の銘柄を引いている。
注文入力は `streams` が空なので `stream_pair()` が常に `None` を返し、
**注文入力をグループに入れても他のペインから銘柄が伝播してこない**。

### 2. グループ全体の銘柄切替で更新されない

[src/screen/dashboard.rs:1257-1287](../../src/screen/dashboard.rs#L1257-L1287) の `init_pane` は
`state.set_content_and_streams(vec![ticker_info], content_kind)` を呼んで
ストリーム購読を張り直す。注文入力は streams を持たないため、`switch_tickers_in_group`
（[dashboard.rs:1338](../../src/screen/dashboard.rs#L1338)）経由で「グループ全体に銘柄を
ブロードキャスト」しても **`set_instrument` が呼ばれず instrument_id が更新されない**。

### 3. 注文入力側の銘柄選択がグループに伝播しない

注文入力ペインで銘柄を選び直したとき
（[src/screen/dashboard/pane.rs:1759-1775](../../src/screen/dashboard/pane.rs#L1759-L1775)）、
直接 `panel.set_instrument(...)` を呼んでローカルだけ更新し、
`Effect::SwitchTickersInGroup` を発行していない。**注文入力で銘柄を切り替えても
リンクグループの仲間（チャート・板）が追従しない**。

### 4. 取引所制約

注文入力は `Exchange::TachibanaStock` 専用。リンクグループに非 Tachibana 銘柄
（Binance 等）が混在している場合、銘柄を取り込むと不整合になる。
グループ参加・銘柄切替の両経路でガードと Toast 通知が必要。

---

## 設計方針

注文入力は「**ストリームは持たないが銘柄は持つ**」ペインとして扱う。
既存の link_group 経路（`stream_pair()` ベース）に**注文入力の銘柄も合流させる**。

### A. 注文入力の銘柄を `TickerInfo` で保持する

現状の `instrument_id: Option<String>` は文字列ベース。リンクグループ伝播時に
`TickerInfo` 比較が必要なので、`OrderEntryPanel` に `TickerInfo` を保持する：

```rust
pub struct OrderEntryPanel {
    pub instrument_id: Option<String>,
    pub display_label: Option<String>,
    pub venue: Option<String>,
    pub ticker_info: Option<TickerInfo>, // 追加
    ...
}

pub fn set_instrument_from_ticker(&mut self, ti: TickerInfo) {
    let display = ti.ticker.display_symbol_and_type().0;
    let id = format!("{}.TSE", ti.ticker.to_full_symbol_and_type().0);
    self.instrument_id = Some(id);
    self.display_label = Some(display);
    self.venue = Some("tachibana".into());
    self.ticker_info = Some(ti);
}
```

既存の `set_instrument(id, display)` は内部呼び出し用に残してもよいが、
**Tachibana ガードを通った後は必ず `TickerInfo` 経由で渡す**ようにする。

### B. `State` レベルで「ペインの代表銘柄」を返す関数を追加

```rust
// src/screen/dashboard/pane.rs
pub fn linked_ticker(&self) -> Option<TickerInfo> {
    if let Some(ti) = self.stream_pair() {
        return Some(ti);
    }
    if let Content::OrderEntry(panel) = &self.content {
        return panel.ticker_info;
    }
    None
}
```

`SwitchLinkGroup` ハンドラ（dashboard.rs:376）は `stream_pair()` の代わりに
`linked_ticker()` を使う。これにより**チャート → 注文入力**だけでなく
**注文入力 → チャート**方向も伝播する。

### C. `init_pane` で OrderEntry を特別扱い

```rust
fn init_pane(...) -> Task<Message> {
    if let Some(state) = self.get_mut_pane(...) {
        if matches!(content_kind, ContentKind::OrderEntry) {
            // Tachibana ガード
            if ticker_info.ticker.exchange != Exchange::TachibanaStock {
                return Task::done(Message::Notification(Toast::warn(
                    "注文入力は立花証券銘柄のみ対応しています".into()
                )));
            }
            if let Content::OrderEntry(panel) = &mut state.content {
                panel.set_instrument_from_ticker(ticker_info);
            }
            return Task::none();
        }
        // 既存のストリーム経路（Kline/Depth/Trades）
        let streams = state.set_content_and_streams(vec![ticker_info], content_kind);
        ...
    }
}
```

`switch_tickers_in_group` から呼ばれたとき、グループ内の OrderEntry ペインも自動で
`set_instrument_from_ticker` 経由で更新される。

### D. 注文入力での銘柄選択をグループにブロードキャスト

[src/screen/dashboard/pane.rs:1759-1779](../../src/screen/dashboard/pane.rs#L1759-L1779) を
書き換える：

```rust
RowSelection::Switch(ti) => {
    if let Content::OrderEntry(_) = &self.content {
        if ti.ticker.exchange != Exchange::TachibanaStock {
            self.notifications.push(Toast::warn(
                "注文入力パネルは立花証券銘柄のみ対応しています".into()
            ));
            self.modal = None;
            return None;
        }
        // ローカル更新は switch_tickers_in_group の init_pane が行うので、
        // 単一ペインでもグループ参加でも同じ経路を通す。
        self.modal = None;
        return Some(Effect::SwitchTickersInGroup(ti));
    }
    return Some(Effect::SwitchTickersInGroup(ti));
}
```

`switch_tickers_in_group` は link_group が `None` のときは `init_focused_pane` 相当の
動作をすべきか？現状（[dashboard.rs:1383-1391](../../src/screen/dashboard.rs#L1383-L1391)）は
focus が立っていれば自分自身を init するので、**OrderEntry が単独ペインのときも正しく動く**。

### E. グループ伝播時のリプレイモード考慮

リプレイモードでは `Content::OrderEntry` は生成されない（`auto_generate_replay_panes`）が、
将来のリプレイ発注対応を見据えて、**venue が replay の銘柄が群に流れた場合は
注文入力に伝播させない**。Tachibana ガードが事実上これを兼ねるが、コメントで意図を残す。

---

## 実装タスク

| # | 内容 | ファイル |
|---|------|--------|
| 1 | `OrderEntryPanel` に `ticker_info: Option<TickerInfo>` を追加 | `src/screen/dashboard/panel/order_entry.rs` |
| 2 | `set_instrument_from_ticker(TickerInfo)` を追加（既存 `set_instrument` を内部呼び出し） | 同上 |
| 3 | `State::linked_ticker()` 追加 | `src/screen/dashboard/pane.rs` |
| 4 | `SwitchLinkGroup` ハンドラを `stream_pair` → `linked_ticker` に変更 | `src/screen/dashboard.rs` |
| 5 | `init_pane` に `ContentKind::OrderEntry` 分岐（Tachibana ガード付き） | `src/screen/dashboard.rs` |
| 6 | `RowSelection::Switch` 経路を `Effect::SwitchTickersInGroup` ブロードキャストに統一 | `src/screen/dashboard/pane.rs` |
| 7 | `[-]` ボタンの描画分岐は変更不要（OrderEntry は既存の else 分岐で描画される） | — |

---

## テスト計画

### 単体テスト

- `OrderEntryPanel::set_instrument_from_ticker` が `instrument_id`・`display_label`・
  `venue`・`ticker_info` を正しくセットすること
- 非 Tachibana の `TickerInfo` を渡したらどうなるか — **呼び出し側でガードする方針なので、
  パネル内ではガードしない**（テストで意図を固定する：「TickerInfo を渡せば必ずセットする。
  exchange ガードは呼び出し側責務」）

### 統合テスト（dashboard.rs）

- グループ 1 にチャート（Tachibana 7203）が存在する状態で、空の OrderEntry を
  グループ 1 に参加させると `instrument_id == "7203.TSE"` になる
- グループ 1 にチャート（Binance BTCUSDT）が存在する状態で、OrderEntry を
  グループ 1 に参加させると **Tachibana ガードで Toast が出て instrument は変わらない**
- OrderEntry の銘柄ピッカーで 9984 を選ぶと、同じグループのチャートも 9984 に切り替わる
- リンクグループ未割り当ての OrderEntry は従来通り単独で動作する

### リグレッションガード

- `Content::OrderEntry` が `[-]` ボタンの除外リストに**入っていない**ことを assert する
  テスト（注文入力と OrderList/BuyingPower の境界が崩れないため）
- 既存の「非 Tachibana 銘柄選択時に Toast が出る」テスト
  （[order_entry.rs:518-535](../../src/screen/dashboard/panel/order_entry.rs#L518-L535) 周辺）が
  ブロードキャスト経路でも壊れないこと

---

## 補足: ユーザーが報告した LLDB エラーについて

ユーザーが貼った CodeLLDB のコンソール出力にある

```
error: flowsurface.exe :: Class 'alloc::collections::btree::node::InternalNode<...>'
  has a member 'data' of type '...' which does not have a complete definition.
```

の一連の `error:` は、**LLDB の Rust 型情報フォーマッタの既知の制約**であり、
プロセスの実行エラーではない。`This version of LLDB has no plugin for the language "rust"`
という直前の warning が示す通り、LLDB は Rust の `BTreeMap` 等の内部表現を完全には
解決できず、debug ビルドの起動時に毎回これらの error 行を吐く。link_group 機能の不具合とは無関係。

実装中はこれらの行を無視してよい。debug 中に変数を覗くときの表示が一部欠けるだけで、
プログラムの挙動には影響しない。
