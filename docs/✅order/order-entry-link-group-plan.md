# 注文入力ペインの link_group 同期 実装計画

## 背景

注文入力ペイン（`Content::OrderEntry`）には**銘柄**という概念がある（板・チャートと同じ）。
ユーザーは画面左上の `[-]` ボタン（`link_group_button`）を 1〜9 のリンクグループ番号に
合わせることで、同じ番号のチャート・板と銘柄を同期させたい。

現状はタイトルバーに `[-]` ボタンは描画されている（`Content::Starter | Content::OrderList(_) | Content::BuyingPower(_)`
以外は描画する分岐 — `pane.rs` の `State::view` 冒頭の `top_left_buttons` 構築箇所）が、
**実際にグループに参加させても銘柄が伝播しない**。理由を以下に分解する。

## なぜ今のままでは動かないか

注文入力は他のペインと違い `streams: ResolvedStream` を持たず、銘柄は
`OrderEntryPanel.instrument_id: Option<String>` に直接保持される
（`src/screen/dashboard/panel/order_entry.rs` の `OrderEntryPanel` 構造体）。

このため既存のリンクグループ伝播パスは **4 箇所** で素通りする：

### 1. グループ参加時に銘柄を取り込めない

`src/screen/dashboard.rs` の `SwitchLinkGroup` ハンドラは `other_state.stream_pair()` を
呼んで仲間の銘柄を引いている。注文入力は `streams` が空なので `stream_pair()` が常に
`None` を返し、**注文入力をグループに入れても他のペインから銘柄が伝播してこない**。

### 2. グループ全体の銘柄切替で更新されない（`init_pane` 経路）

`src/screen/dashboard.rs` の `init_pane` 関数は
`state.set_content_and_streams(vec![ticker_info], content_kind)` を呼んで
ストリーム購読を張り直す。注文入力は streams を持たないため、`switch_tickers_in_group`
経由で「グループ全体に銘柄をブロードキャスト」しても **`set_instrument` が呼ばれず
instrument_id が更新されない**。

### 3. 単独ペインの銘柄切替で更新されない（`init_focused_pane` 経路）

`src/screen/dashboard.rs` の `init_focused_pane` 関数も同様に `set_content_and_streams`
直呼びで、注文入力に対する分岐がない。link_group=None かつ focused の注文入力ペインで
画面上部の TickersTable から銘柄を選んでも、**ストリーム購読を張り直すパスを通り
`OrderEntryPanel.instrument_id` は無更新**。**`init_pane` と `init_focused_pane` の両方を
直す必要がある**（R1 で発見）。

### 4. 注文入力側の銘柄選択がグループに伝播しない

注文入力ペインで銘柄を選び直したとき（`pane.rs` の `RowSelection::Switch` 分岐）、
直接 `panel.set_instrument(...)` を呼んでローカルだけ更新し、`Effect::SwitchTickersInGroup`
を発行していない。**注文入力で銘柄を切り替えてもリンクグループの仲間（チャート・板）が
追従しない**。

### 5. 取引所制約（partial success 回避が必要）

注文入力は `Exchange::TachibanaStock` 専用。リンクグループに非 Tachibana 銘柄
（Binance 等）に切り替える操作が起きたとき、Kline/Depth は新銘柄に切り替わるが
注文入力だけ古い銘柄を保持する **partial success** が起きる。

R1 レビューで確認した方針: **partial success を許さず、`SwitchLinkGroup` ハンドラ層で
グループ参加自体を拒否**する。ガード位置をパネル側ではなくグループ参加経路に置くことで、
「グループに入ったが同期しない」状態を構造的に作らない。

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
    self.venue = Some("tachibana".into()); // Phase O0: Tachibana 専用。複数取引所対応時に呼出側から渡す
    self.ticker_info = Some(ti);
}
```

`venue` のハードコードは現行 `set_instrument` と同じ方針で、Phase O0 範囲では
TachibanaStock 専用ガードが上流で効いているため許容する。多取引所対応 (Phase O1+) では
呼出側から venue を渡す API に置換する。

既存の `set_instrument(id, display)` は内部呼び出し用に残してもよいが、
**Tachibana ガードを通った後は必ず `TickerInfo` 経由で渡す**ようにする。

### B. `State` レベルで「ペインの代表銘柄」を返す関数を追加

```rust
// src/screen/dashboard/pane.rs
pub fn linked_ticker(&self) -> Option<TickerInfo> {
    // 優先順位: streams > OrderEntry の ticker_info
    // OrderEntry は streams を持たない前提。将来 OrderEntry に streams を持たせる場合は
    // 「streams が新ティッカーで ticker_info が古い」ズレが発生しうるので、
    // その時点で優先順位を `ticker_info > streams` に逆転させるか、両者を強制同期させること。
    if let Some(ti) = self.stream_pair() {
        return Some(ti);
    }
    if let Content::OrderEntry(panel) = &self.content {
        return panel.ticker_info;
    }
    None
}
```

`SwitchLinkGroup` ハンドラ（`src/screen/dashboard.rs` の `pane::Message::SwitchLinkGroup`
分岐）は `stream_pair()` の代わりに `linked_ticker()` を使う。これにより
**チャート → 注文入力**だけでなく **注文入力 → チャート**方向も伝播する。

### C. `init_pane` と `init_focused_pane` の両方で OrderEntry を特別扱い

注文入力は streams を持たないため、グループ全体への銘柄ブロードキャストを担う
`init_pane` と単独ペイン用の `init_focused_pane` の **両方** に分岐を追加する必要がある
（R1 で発見：`init_focused_pane` を見落とすと link_group=None の単独 OrderEntry が
銘柄選択しても無更新になる silent failure）。

両関数で同じ振る舞いを保つため、ヘルパーに切り出すのが望ましい：

```rust
// src/screen/dashboard.rs
fn apply_ticker_to_order_entry(state: &mut pane::State, ti: TickerInfo) -> Task<Message> {
    // ガードはここでは行わない。Tachibana 取引所の検証は
    // SwitchLinkGroup / RowSelection::Switch の入口で完了している前提（§F）。
    if let Content::OrderEntry(panel) = &mut state.content {
        panel.set_instrument_from_ticker(ti);
    }
    Task::none()
}

fn init_pane(...) -> Task<Message> {
    if let Some(state) = self.get_mut_pane(...) {
        if matches!(content_kind, ContentKind::OrderEntry) {
            return Self::apply_ticker_to_order_entry(state, ticker_info);
        }
        // 既存のストリーム経路（Kline/Depth/Trades）
        let streams = state.set_content_and_streams(vec![ticker_info], content_kind);
        ...
    }
}

fn init_focused_pane(...) -> Task<Message> {
    if let Some((window, selected_pane)) = self.focus
        && let Some(state) = self.get_mut_pane(...)
    {
        if matches!(content_kind, ContentKind::OrderEntry) {
            return Self::apply_ticker_to_order_entry(state, ticker_info);
        }
        // 既存のストリーム経路
        ...
    }
}
```

### D. 注文入力での銘柄選択をグループにブロードキャスト

`pane.rs` の `RowSelection::Switch` 分岐を書き換える：

```rust
RowSelection::Switch(ti) => {
    if let Content::OrderEntry(_) = &self.content {
        // exchange ガードは SwitchTickersInGroup → init_*pane の手前で行う方が
        // 一貫しているが、UX 上「銘柄ピッカーでクリックした瞬間に Toast」が
        // 望ましいため、ここでも先行ガードを残す。
        if ti.ticker.exchange != Exchange::TachibanaStock {
            self.notifications.push(Toast::warn(
                "注文入力パネルは立花証券銘柄のみ対応しています".into()
            ));
            self.modal = None;
            return None;
        }
        self.modal = None;
        return Some(Effect::SwitchTickersInGroup(ti));
    }
    return Some(Effect::SwitchTickersInGroup(ti));
}
```

ローカル `set_instrument` は呼ばない。`SwitchTickersInGroup` を発行し、
`switch_tickers_in_group` 内で:

- link_group が `Some` → 同グループ全員（自分含む）を `init_pane` 経由で更新
- link_group が `None` → focus 経路の `init_focused_pane` で自分を更新

の **どちらの経路でも自ペインに到達する** ことが §C の `init_focused_pane` 拡張で
保証される。**§C と §D はセットで実装**しないと回帰する。

### E. グループ伝播時のリプレイモード考慮

リプレイモードでは `Content::OrderEntry` は生成されない（`auto_generate_replay_panes`）が、
将来のリプレイ発注対応を見据えて、**venue が replay の銘柄が群に流れた場合は
注文入力に伝播させない**。§F の Tachibana ガードが事実上これを兼ねる
（replay venue は Exchange::TachibanaStock ではない）。

### F. 取引所ガードを `SwitchLinkGroup` レイヤに置く（partial success 回避）

R1 で「Kline は新銘柄に切替・OrderEntry だけガードで弾く」の partial success が指摘された。
これを避けるため、**Tachibana ガードはグループ参加経路の最上流で行う**：

```rust
// src/screen/dashboard.rs の SwitchLinkGroup ハンドラ
pane::Message::SwitchLinkGroup(pane, group) => {
    if group.is_some() {
        // Pre-flight: 注文入力ペインがグループに含まれる場合、目標銘柄が
        // Tachibana 取引所でないとグループ参加自体を拒否する。
        let target_ti = self.iter_all_panes(main_window.id)
            .filter(|(w, p, _)| !(*w == window && *p == pane))
            .find_map(|(_, _, s)| if s.link_group == group { s.linked_ticker() } else { None });

        let joining_pane_is_order_entry = self
            .get_pane(main_window.id, window, pane)
            .map(|s| matches!(s.content, Content::OrderEntry(_)))
            .unwrap_or(false);

        let group_has_order_entry = self.iter_all_panes(main_window.id)
            .any(|(_, _, s)|
                s.link_group == group && matches!(s.content, Content::OrderEntry(_)));

        if (joining_pane_is_order_entry || group_has_order_entry)
            && let Some(ti) = target_ti
            && ti.ticker.exchange != Exchange::TachibanaStock
        {
            return (
                Task::done(Message::Notification(Toast::warn(
                    "注文入力ペインは立花証券銘柄のグループにのみ参加できます".into(),
                ))),
                None,
            );
        }
    }
    // 以降、既存の SwitchLinkGroup 本体（state.link_group = group; 銘柄伝播 …）
}
```

これにより:

- 非 Tachibana グループへの **OrderEntry の参加** が拒否される
- **Tachibana グループに非 Tachibana 銘柄を持つペインが新規参加** しても、
  `init_pane` 経路で OrderEntry を巻き込む前に拒否される
- 「グループに入ったが同期しない」状態を構造的に作らない（partial success 撲滅）

---

## 実装タスク

| # | 内容 | ファイル |
|---|------|--------|
| 1 | `OrderEntryPanel` に `ticker_info: Option<TickerInfo>` を追加 | `src/screen/dashboard/panel/order_entry.rs` |
| 2 | `set_instrument_from_ticker(TickerInfo)` を追加（既存 `set_instrument` を内部呼び出し） | 同上 |
| 3 | `State::linked_ticker()` 追加（優先順位コメント込み § B 参照） | `src/screen/dashboard/pane.rs` |
| 4 | `SwitchLinkGroup` ハンドラを `stream_pair` → `linked_ticker` に変更 + § F の Tachibana 先行ガードを追加 | `src/screen/dashboard.rs` |
| 5a | `init_pane` に `ContentKind::OrderEntry` 分岐 | `src/screen/dashboard.rs` |
| 5b | **`init_focused_pane` にも同じ分岐**（R1 で発見：見落とすと単独ペイン経路で silent failure） | `src/screen/dashboard.rs` |
| 5c | 共通ヘルパー `apply_ticker_to_order_entry` を切り出して 5a/5b で共有 | `src/screen/dashboard.rs` |
| 6 | `RowSelection::Switch` 経路を `Effect::SwitchTickersInGroup` ブロードキャストに統一（§ D） | `src/screen/dashboard/pane.rs` |
| 7 | `[-]` ボタンの描画分岐は変更不要（OrderEntry は既存の else 分岐で描画される） | — |
| 8 | `data::Pane` の serde 互換: `OrderEntry` の `ticker_info` 永続化方針を確認（既存 `instrument_id` 文字列との二重保存を避ける） | `data/src/layout/pane.rs` |

---

## テスト計画

### 単体テスト（`src/screen/dashboard/panel/order_entry.rs` の `#[cfg(test)] mod tests`）

- `OrderEntryPanel::set_instrument_from_ticker` が `instrument_id`・`display_label`・
  `venue`・`ticker_info` を正しくセットすること
- 非 Tachibana の `TickerInfo` を渡したらどうなるか — **呼び出し側でガードする方針なので、
  パネル内ではガードしない**（テストで意図を固定する：「TickerInfo を渡せば必ずセットする。
  exchange ガードは呼び出し側責務」）

### 統合テスト（`src/screen/dashboard/pane.rs` の `#[cfg(test)] mod tests` または `tests/` 配下に新設）

| 観測点 | シナリオ | 期待結果 |
|---|---|---|
| `dashboard.rs` の SwitchLinkGroup 経路 | グループ A にチャート（Tachibana 7203）あり、空の OrderEntry をグループ A に参加 | OrderEntry の `instrument_id == "7203.TSE"` |
| 同上 | グループ A にチャート（Binance BTCUSDT）あり、OrderEntry をグループ A に参加 | § F のガードで **グループ参加が拒否** され、Toast が出る。OrderEntry の `link_group` は None のまま |
| 同上 | Tachibana 銘柄のチャート＋ OrderEntry が同グループにいる状態で、Binance 銘柄を持つ別ペインをそのグループへ参加させる | § F の事前ガードで Binance ペインの参加が拒否される（Kline は古い銘柄保持） |
| `init_focused_pane` 経路 | link_group=None の OrderEntry が focus、TickersTable から Tachibana 銘柄を選択 | `OrderEntryPanel.instrument_id` が更新される（§ C / 5b の核心） |
| `RowSelection::Switch` 経路 | OrderEntry の銘柄ピッカーで 9984 を選択（同グループにチャートあり） | 同グループのチャートも 9984 に切り替わる + OrderEntry も 9984 |
| 同上 | OrderEntry の銘柄ピッカーで非 Tachibana 銘柄を選択 | Toast 表示、`SwitchTickersInGroup` 発行されない、グループの他ペインも変化なし |

### リグレッションガード（既存テストモジュールに追加）

- `Content::OrderEntry` が `[-]` ボタンの除外リストに**入っていない**ことを assert する
  テスト（R1 で `link_group_button_exclusion_excludes_only_non_ticker_panes` として実装済）
- `State::from_config` が OrderList/BuyingPower の link_group を None に正規化することを
  assert するテスト（R1 で実装済）
- 既存の「非 Tachibana 銘柄選択時に Toast が出る」テスト
  （`src/screen/dashboard/panel/order_entry.rs` の
  `without_set_instrument_submit_button_is_disabled` 周辺）がブロードキャスト経路でも壊れないこと
- **saved-state 復元シナリオ**: 旧バージョンで `OrderEntry { link_group: Some(A) }` を
  保存していた場合、新バイナリで復元しても `link_group` は保持される（R1 で OrderList/
  BuyingPower のみ正規化対象であることを `from_config_preserves_link_group_for_order_entry`
  で pin 済）
- **Tachibana ログアウト → グループ既存メンバーが残ったまま** のシナリオで、グループへ
  新銘柄が流れる場合の挙動（OrderEntry 内部状態が `submitting=false` にリセット
  されているか）を確認するテストを追加候補

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

---

## レビュー反映 (2026-05-01, ラウンド 1)

`/review-fix-loop` で 4 体並列レビュー（rust-reviewer / iced-architecture-reviewer /
silent-failure-hunter / general-purpose）を回した結果と修正内容を記録する。

### 統一決定

1. 注文入力ペインを非 Tachibana 銘柄の link_group に参加させる操作は、`SwitchLinkGroup`
   レイヤで**グループ参加自体を拒否**する（partial success 容認しない）
2. `saved-state.json` 復元時に OrderList/BuyingPower の `link_group` は **`from_config`
   レイヤで黙って `None` に正規化** する（debug ログのみ薄く残す）
3. タスク 5 を「`init_pane` のみ」から **「`init_pane` + `init_focused_pane` 両方」** に
   拡張。共通ヘルパーを切り出して二箇所で共有
4. 行番号アンカー（`pane.rs:1759-1779` 形式）は **シンボル名参照に置換**（陳腐化対策）

### 解消した指摘

| ID | 重要度 | 内容 | 対応 |
|---|---|---|---|
| H-1 | HIGH | 計画書 §C/タスク 5 が `init_focused_pane` を見落とし | §C 書き換え + タスク 5 を 5a/5b/5c に分割 |
| H-2 | HIGH | §D の擬似コードで link_group=None かつ非 focus 時に自ペインに到達しない | §D 書き換え + `switch_tickers_in_group` の 2 経路を §C で吸収する旨明記 |
| H-3 | HIGH | Tachibana ガードが partial success を起こす | §F 新設し SwitchLinkGroup レイヤに事前ガード移行 |
| M-A | MEDIUM | saved-state 経由のゴースト link_group が `set_content_and_streams` 経路で warn ログ大量出力 | `pane::State::from_config` で OrderList/BuyingPower の link_group を None に正規化 + `switch_tickers_in_group` で防御層追加（コード変更） |
| M-B | MEDIUM | `layout.rs` 復元時に link_group がそのまま渡る | `from_config` 内部での正規化で吸収（呼出側変更不要） |
| M-C | MEDIUM | リグレッションテスト不在 | `from_config_normalizes_link_group_for_order_list` / `_for_buying_power` / `_preserves_link_group_for_order_entry` / `link_group_button_exclusion_excludes_only_non_ticker_panes` の 4 件追加 |
| M-1 | MEDIUM | 既存計画群に link_group 同期記述ゼロ | `docs/✅order/implementation-plan.md` のフォローアップ節にリンク追加（次タスク） |
| M-2 | MEDIUM | `linked_ticker()` の優先順位が暗黙 | §B にコメント明記 |
| M-3 | MEDIUM | テスト計画の negative path / 観測点不足 | テスト計画を表形式に再構成、観測点（モジュール / pytest 不要・cargo test のみ）を明記 |
| M-4 | MEDIUM | 行番号アンカー陳腐化 | シンボル名参照に置換 |
| L-1 | LOW | `[-]` 分岐の意図コメント不足 | `pane.rs` の `State::view` 冒頭にある `top_left_buttons` 構築箇所にコメント追加 |
| L-2 | LOW | `Content::OrderEntry` が除外リストに無いこと pin テスト不在 | M-C の 4 件目で対応 |
| L-3 | LOW | venue ハードコードの説明不足 | §A に Phase O0 専用ガードに依存する旨を明記 |

### 新たな見逃しパターン候補（次回 MISSES.md 追記候補）

- **「`init_pane` と `init_focused_pane` の二経路ヌケ」**: ペイン更新ロジックは
  `switch_tickers_in_group` （グループ経路）と `init_focused_pane` （単独経路）の
  2 系統あり、片方だけ修正して silent failure を生むパターン。今回は計画段階で
  発見できたが、実装段階では気付きにくい
- **「saved-state 経由のゴースト UI 状態」**: UI 上トグル不能になった State フィールドが
  saved-state に残り続け、別経路で副作用を生むパターン。`from_config` 等の構築境界で
  正規化するのが定石

### 持ち越し項目

- **タスク 8 (`data::Pane` 永続化)**: `OrderEntryPanel.ticker_info` を saved-state にも
  保存するか、起動時に `instrument_id` 文字列から復元するかは未決。実装着手時に確定する
- **既存の clippy warning** (`engine-client/tests/process_lifecycle.rs` の `SubscriptionKey`
  未使用 import 等): 今フェーズ完全スコープ外。Tachibana セッションキャッシュフェーズ
  (`commit 16c27e6` / `de9fd7b`) で混入しており、当該フェーズの review-fix-loop で対応
  すべき

### 検証コマンド結果

| コマンド | 結果 |
|---|---|
| `cargo fmt --check` | ✅ 緑 |
| `cargo clippy --workspace -- -D warnings` | ✅ 緑（`--tests` 付きでは持ち越し warning あり、上記） |
| `cargo test --bin flowsurface from_config_normalizes` | ✅ 2/2 PASS |
| `cargo test --bin flowsurface from_config_preserves` | ✅ 1/1 PASS |
| `cargo test --bin flowsurface link_group_button_exclusion` | ✅ 1/1 PASS |
