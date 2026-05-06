# リプレイファイル切替時に旧ペインが残る不具合 — 修正計画 (Approach C)

## 症状

リプレイモードで `examples/multiinst_10pairs_minute.py`（10銘柄）を実行した後、
別ファイル `examples/pair_trade_minute.py`（1301/7203 の 2 銘柄）を開いて
リプレイを再実行すると、

- 注文一覧 / チャート（focus pane）は file2 の内容に切り替わる
- しかし下段の各銘柄ペイン群には **file1 の銘柄（8306/7974/4502/6861/8035/9433 …）が残ったまま**
- file2 の 7203 ペインも追加されるため、画面が file1+file2 の重ね合わせ状態になる

期待挙動: file2 を開いた段階で旧ペインは消え、file2 の銘柄ペインだけが並ぶ。

## 関連コード

- ペイン自動生成ハンドラ: [`src/main.rs:3760`](../../src/main.rs#L3760)（`Message::ReplayDataLoaded`）
- ペイン生成ロジック: [`src/screen/dashboard.rs:1019`](../../src/screen/dashboard.rs#L1019)（`auto_generate_replay_panes`）
- レジストリ: [`src/screen/dashboard/replay_pane_registry.rs`](../../src/screen/dashboard/replay_pane_registry.rs)

## 根本原因

`auto_generate_replay_panes` は `replay_pane_registry.is_loaded(instrument_id)` をガードに使い、
**「同じセッション内で同じ銘柄を二重ペイン生成しない」** ことを保証している。

しかし「リプレイセッションの境界」を表現する仕組みが存在しないため、
file1 が残した `loaded` / `registered` エントリは file2 のロード時にもそのまま残り、

1. file2 の `instrument_ids` に含まれない file1 銘柄のペインは誰もクローズしない
2. file2 で file1 と同じ銘柄（例: 1301）が再度指定された場合は既存ペインを再利用するため問題なし
3. file1 にしかなかった銘柄（8306 等）のペインは取り残される

結果として GUI が「過去セッションの残骸 + 現セッション」の混在状態に陥る。

## 修正方針 (Approach C) — `ReplayDataLoaded` 受信時に「リスト外」のペインを自動クローズ

`ReplayDataLoaded` の `instrument_ids` を **「現セッションで生きている銘柄リスト」のスナップショット** とみなし、
ハンドラ側で **`registered` に居るが `instrument_ids` に含まれない銘柄** のペインを一括クローズする。

### なぜ C か

- engine 側のスキーマ変更不要（schema bump 不要）
- 現実装は **1 LoadReplayData = 1 ReplayDataLoaded = 1 セッション境界** が成立しているため、
  「instrument_ids を見て差分を取る」だけでセッション切替も追加ロードもうまく扱える
- フォーム Submit / メニュー / 将来の経路（CLI から再ロード等）に依存せず、
  受信側 1 箇所だけで完結する
- A 案より影響範囲が狭く、Submit 経路と reset の二重管理を避けられる

### トレードオフ（明示）

- 「追加ロード」と「セッション切替」の区別がスキーマ上は存在しない
  → 現仕様（1 Submit = 1 LoadReplayData = 全銘柄を一度に渡す）では問題にならない
  → 将来「追加ロード」（既存セッションに銘柄を足す）UX を導入する場合、
    その時点で Approach B（schema に session_epoch を載せる）への移行が必要

## 修正フロー

```
[engine] ReplayDataLoaded { instrument_ids: ["1301","7203"] }
   │
   ▼
[Rust UI handler]
1. ids ← 受信した instrument_ids（空 → 後方互換で instrument_id 単体）
2. 旧ペインの差分クローズ:
     stale = registered の銘柄 \ ids   （集合差）
     for inst in stale:
        - そのインストルメントの全 pane_kind を pane_grid から close
        - replay_pane_registry から remove_registered_pane
        - loaded から削除（→ 後の is_first 判定が壊れない）
3. 既存ループ: for id in ids → auto_generate_replay_panes
   - 残っている銘柄（例: 1301）は registered なので既存ペインを再利用
   - 新規（例: 7203）は is_first=true で新規 pane 生成
```

セッションレベルペイン（`instrument_id=""` の OrderList / BuyingPower）はそのまま温存する。
file1 → file2 の切替で OrderList ペイン自体は閉じる必要がない（中身だけ更新される）。

## 変更箇所

### 1. `ReplayPaneRegistry` に銘柄列挙 API を追加

ファイル: [`src/screen/dashboard/replay_pane_registry.rs`](../../src/screen/dashboard/replay_pane_registry.rs)

現状の registry は `instrument_id → pane` の引きはあるが
「現在 registered な銘柄一覧」を取り出す API が無いため、差分計算用に追加する。

```rust
impl ReplayPaneRegistry {
    /// 現在 registered な instrument_id（重複なし、`""` のセッションペインは除外）。
    pub fn loaded_instruments(&self) -> Vec<String> {
        self.loaded
            .iter()
            .filter(|s| !s.is_empty())
            .cloned()
            .collect()
    }

    /// `instrument_id` に紐づく registered な (pane_kind, pane) を全て返し、
    /// レジストリの `registered` / `loaded` から該当エントリを除去する。
    /// `dismissed` は触らない（ユーザの dismiss 意図は保持）。
    pub fn drain_instrument(
        &mut self,
        instrument_id: &str,
    ) -> Vec<(&'static str, pane_grid::Pane)> {
        let drained: Vec<_> = self
            .registered
            .iter()
            .filter(|(k, _)| k.instrument_id == instrument_id)
            .map(|(k, p)| (k.pane_kind, *p))
            .collect();
        self.registered
            .retain(|k, _| k.instrument_id != instrument_id);
        self.loaded.remove(instrument_id);
        drained
    }
}
```

設計判断:
- `dismissed` は **触らない**。
  - 「あるセッションで dismiss した pane を、その銘柄が次セッションでも残っているなら復活させない」のは現状の挙動。
  - C 案ではセッション境界を明示しないため、dismiss を巻き戻す積極的理由がない。
  - file2 の銘柄が file1 と全く別なら dismissed は事実上無効化される（別 instrument_id だから）。
- `loaded` から消すのは `is_first` 判定が破綻しないため。
  Stale 削除後に file2 で同銘柄が再登場するケース（`["1301", ...]` で file1 にも 1301 があった）は
  そもそも `stale` に入らないので影響なし。

### 2. `Dashboard` にスタールペインクローズ API を追加

ファイル: [`src/screen/dashboard.rs`](../../src/screen/dashboard.rs)

```rust
impl Dashboard {
    /// `keep` に含まれない instrument の registered な replay pane を全てクローズする。
    /// セッションレベルペイン（`instrument_id=""`）は keep の対象外として常に残す。
    pub fn close_replay_panes_not_in(&mut self, keep: &[String]) {
        let keep_set: HashSet<&str> = keep.iter().map(String::as_str).collect();
        let stale: Vec<String> = self
            .replay_pane_registry
            .loaded_instruments()
            .into_iter()
            .filter(|inst| !keep_set.contains(inst.as_str()))
            .collect();
        for inst in &stale {
            for (_kind, pane) in self.replay_pane_registry.drain_instrument(inst) {
                self.panes.close(pane);
            }
            log::info!("replay: closed stale panes for instrument {inst:?}");
        }
    }
}
```

### 3. `Message::ReplayDataLoaded` ハンドラに差分クローズを差し込む

ファイル: [`src/main.rs:3760`](../../src/main.rs#L3760)

`ids` を確定させた直後、`auto_generate_replay_panes` ループに入る前に呼ぶ。

```rust
let ids: Vec<String> = instrument_ids
    .filter(|v| !v.is_empty())
    .unwrap_or_else(|| instrument_id.into_iter().collect());

if ids.is_empty() {
    log::error!(
        "ReplayDataLoaded: instrument_id(s) missing — auto pane generation skipped."
    );
    return Task::none();
}

// Approach C: 受信した ids に含まれない旧 replay ペインを一括クローズ。
self.active_dashboard_mut().close_replay_panes_not_in(&ids);

// 既存ループ ...
```

## テスト計画

bin-only クレート + 既存パターン（source-scan）に倣う。
registry 単体テストは `#[cfg(test)] mod tests` で増やす。

### `replay_pane_registry.rs` 内の追加ユニットテスト

| テスト名 | 検証内容 |
|---|---|
| `loaded_instruments_excludes_empty_session_key` | `mark_loaded("")` を入れても `loaded_instruments()` には現れない |
| `drain_instrument_removes_all_pane_kinds_for_instrument` | TimeAndSales / CandlestickChart 両方を register した銘柄を drain すると 2 件返り、`registered` から消える |
| `drain_instrument_removes_loaded_entry` | `drain_instrument("1301.TSE")` 後 `is_loaded("1301.TSE") == false` |
| `drain_instrument_does_not_touch_dismissed` | dismiss した銘柄を drain しても `should_generate` は false のまま |
| `drain_instrument_does_not_touch_other_instruments` | 1301 を drain しても 7203 の registered は残る |

### 新規テストファイル: `tests/replay_pane_stale_close.rs`

| テスト名 | 検証内容 |
|---|---|
| `dashboard_has_close_replay_panes_not_in` | source-scan: `fn close_replay_panes_not_in(` が定義されている |
| `close_replay_panes_not_in_calls_drain_instrument_and_close` | source-scan: 関数 body に `drain_instrument` と `self.panes.close` の両方が含まれる |
| `replay_data_loaded_handler_calls_close_replay_panes_not_in` | source-scan: `Message::ReplayDataLoaded` arm body に `close_replay_panes_not_in(&ids)` が含まれる |
| `close_replay_panes_not_in_runs_before_auto_generate_loop` | source-scan: `close_replay_panes_not_in` の byte offset < `auto_generate_replay_panes` の byte offset（同一 arm 内） |
| `loaded_instruments_filter_excludes_session_level_keys` | source-scan: `loaded_instruments` 実装に `!s.is_empty()` フィルタが入っている |

### 既存テストへの影響

- `tests/auto_generate_replay_panes_auto_bind.rs`: 影響なし（registry 経由のみ）
- `tests/multiinst_replay_pane_routing.rs`: 影響なし（`map_engine_event_to_message` と handler ループ構造は不変）
- `tests/test_replay_order_list_view.rs`: `replay_pane_registry_tracks_pane_ids` 系は新 API を呼ばないため影響なし

## 実環境確認手順

1. `cargo build && target/debug/flowsurface`
2. メニュー > Replay > `examples/multiinst_10pairs_minute.py` を開き、10 銘柄ペインが生成されることを確認
3. メニュー > Replay > `examples/pair_trade_minute.py` を開く
4. **期待**: file1 の 10 ペインが消え、1301 / 7203 の 2 ペインだけが並ぶ
5. 注文一覧が file2 のフィルに切り替わっていることを確認
6. 共通銘柄でのリプレイ（file1 → file1 の granularity 変更など）でペインが二重化しないことを確認
7. ログに `replay: closed stale panes for instrument ...` が file1 由来の銘柄ぶんだけ出ていることを確認

## 既知の制約 / 非目標

- 「追加ロード」（既存セッションに銘柄を後足し）UX を将来導入する場合、
  Approach C は **すべての ReplayDataLoaded を「全置き換え」とみなす** ため挙動が破綻する。
  その時点で session_epoch ベース（Approach B）への移行が必要。
- ユーザが手動で `dismiss` した pane の扱いは現状維持（dismissed は drain 対象外）。
- レイアウト切替（active dashboard が切り替わる）と差分クローズの競合は非目標。
  `close_replay_panes_not_in` は `active_dashboard_mut` で 1 つだけ操作する
  （既存 `ReplayDataLoaded` ハンドラと同じ前提）。
- セッションレベルペイン（OrderList / BuyingPower、`instrument_id=""`）は drain 対象外。
  これらの中身は別経路（注文一覧の更新フロー）で file2 用に切り替わる。

## 作業チェックリスト

1. [ ] `ReplayPaneRegistry` に `loaded_instruments` / `drain_instrument` を実装 + 単体テスト 5 件（RED→GREEN）
2. [ ] `tests/replay_pane_stale_close.rs` の source-scan テスト 5 件を先に書いて RED 確認
3. [ ] `Dashboard::close_replay_panes_not_in` を実装（GREEN）
4. [ ] `Message::ReplayDataLoaded` ハンドラに `close_replay_panes_not_in(&ids)` を挿入（GREEN）
5. [ ] `cargo fmt && cargo check && cargo test && cargo clippy -- -D warnings`
6. [ ] 実環境確認手順を実施し、ユーザ確認待ちに移行
7. [ ] bug-postmortem を実施し、`MISSES.md` に「セッション境界の暗黙化（IPC スナップショット解釈の不在）」パターンを追記
