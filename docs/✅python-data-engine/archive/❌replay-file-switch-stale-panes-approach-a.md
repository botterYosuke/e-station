# リプレイファイル切替時に旧ペインが残る不具合 — 修正計画 (Approach A)

## 症状

リプレイモードで `examples/multiinst_10pairs_minute.py`（10銘柄）を実行した後、
別ファイル `examples/pair_trade_minute.py`（1301/7203 の 2 銘柄）を開いて
リプレイを再実行すると、

- 注文一覧 / チャート（focus pane）は file2 の内容に切り替わる
- しかし下段の各銘柄ペイン群には **file1 の銘柄（8306/7974/4502/6861/8035/9433 …）が残ったまま**
- file2 の 7203 ペインも追加されるため、画面が file1+file2 の重ね合わせ状態になる

期待挙動: file2 を開いた段階で旧ペインは消え、file2 の銘柄ペインだけが並ぶ。

## 関連コード

- フォーム送信ハンドラ: [`src/main.rs:4316`](../../src/main.rs#L4316)（`ReplayFormMsg(... Submit { ... })`）
- ペイン自動生成ハンドラ: [`src/main.rs:3760`](../../src/main.rs#L3760)（`Message::ReplayDataLoaded`）
- ペイン生成ロジック: [`src/screen/dashboard.rs:1019`](../../src/screen/dashboard.rs#L1019)（`auto_generate_replay_panes`）
- レジストリ: [`src/screen/dashboard/replay_pane_registry.rs`](../../src/screen/dashboard/replay_pane_registry.rs)
- replay 実行フラグ: [`src/main.rs:1456`](../../src/main.rs#L1456)（`replay_running`）
- StopReplay フロー: [`src/main.rs:4837`](../../src/main.rs#L4837)（`Message::StopReplayOnly`）

## 根本原因

`auto_generate_replay_panes` は `replay_pane_registry.is_loaded(instrument_id)` をガードに使い、
**「同じセッション内で同じ銘柄を二重ペイン生成しない」** ことを保証している。

しかし「リプレイセッションの境界」を表現する仕組みが存在しないため、
file1 が残した `loaded` / `registered` エントリは file2 のロード時にもそのまま残り、

1. file2 の `instrument_ids` に含まれない file1 銘柄のペインは誰もクローズしない
2. file2 で file1 と同じ銘柄（例: 1301）が再度指定されると、既存ペインを再利用する分には問題ないが、file1 にしかなかった銘柄（8306 等）のペインは取り残される

結果として GUI が「過去セッションの残骸 + 現セッション」の混在状態に陥る。

## 修正方針 (Approach A) — Form Submit 起点で GUI 側を全クリア

ユーザがリプレイフォームを Submit した時点を **「セッション境界」** とみなし、
`LoadReplayData` を engine に送る前に GUI 側で旧 replay ペインを全てクローズし、
`replay_pane_registry` を初期化する。

### なぜ A か

- セッション境界が **ユーザの明示的な操作（フォーム Submit）** に紐付くため意味が明確
- engine 側のスキーマ変更不要（schema bump 不要）
- 「追加ロード」と「セッション切替」の区別を将来仕様に持ち込まず、現行の「1 Submit = 1 セッション」という単純なモデルを維持できる
- 既存の StopReplay フロー（[`Message::StopReplayOnly`](../../src/main.rs#L4837)）と組み合わせて自然に動く

### 修正フロー

```
[user] フォーム Submit
   │
   ▼
1. replay_running == true なら StopReplayOnly を先行（既存実装に委譲）
2. GUI 側でセッションリセット
   - 全 registered pane を pane_grid から close
   - replay_pane_registry = ReplayPaneRegistry::new()
3. LoadReplayData → StartEngine を従来どおり送信
   │
   ▼
[engine] ReplayDataLoaded を返す
   │
   ▼
4. 既存ハンドラが file2 の instrument_ids 全件で auto_generate_replay_panes を呼ぶ
   - レジストリは空なので全 instrument が is_first=true として新規 pane を生成
```

## 変更箇所

### 1. `ReplayPaneRegistry::reset` を追加

ファイル: [`src/screen/dashboard/replay_pane_registry.rs`](../../src/screen/dashboard/replay_pane_registry.rs)

```rust
impl ReplayPaneRegistry {
    /// セッション境界で呼ばれ、loaded / registered / dismissed をすべてクリアする。
    /// 戻り値: クローズ対象として呼び出し側に渡す pane ID の集合。
    pub fn drain_registered(&mut self) -> Vec<pane_grid::Pane> {
        let panes: Vec<_> = self.registered.values().copied().collect();
        self.loaded.clear();
        self.registered.clear();
        self.dismissed.clear();
        panes
    }
}
```

設計判断:
- `dismissed` もクリアする（新セッションではユーザの過去の dismiss 意図はリセットしてよい）
- `registered` の値を呼び出し側に返し、Dashboard 側で実際の `pane_grid::close` を行う（registry 自身は pane_grid を持たないため）

### 2. `Dashboard::reset_replay_session` を追加

ファイル: [`src/screen/dashboard.rs`](../../src/screen/dashboard.rs)

```rust
impl Dashboard {
    /// リプレイセッション境界で呼ばれる。registered な replay pane を
    /// すべて pane_grid から閉じ、レジストリを初期化する。
    pub fn reset_replay_session(&mut self) {
        let panes = self.replay_pane_registry.drain_registered();
        for pane in panes {
            self.panes.close(pane);
        }
    }
}
```

設計判断:
- 注文一覧 / 買付余力ペイン（`instrument_id=""` で登録）も `registered` に入っているため一緒にクローズされる
- ユーザが手動配置した（自動生成でない）非 replay ペインには手を出さない

### 3. Form Submit 時にリセットを呼び出す

ファイル: [`src/main.rs:4316`](../../src/main.rs#L4316)

`Action::Submit { ... }` アームの先頭で:

```rust
// セッション境界: 旧 replay ペイン群を一括クローズ + レジストリ初期化
self.active_dashboard_mut().reset_replay_session();
```

### 4. replay 実行中の Submit 時のフロー

現状フォームを開ける条件は要確認だが、もし `replay_running == true` の状態で
フォーム Submit が起きうるなら、`StopReplayOnly` を先行させる必要がある。

調査タスク:
- [ ] `ShowReplayDialog` が `replay_running` 中に呼べるかを確認
- [ ] 呼べる場合、Submit 前に StopReplayOnly を `Task::done` で挟む
- [ ] 呼べない（メニュー側でガードされている）場合、Submit に到達した時点で必ず `replay_running == false` のはずなので、reset_replay_session のみで足りる

> 既存メニューが「Replay 実行中は新規ロードを禁止 / Stop 必須」をガードしているなら追加対応不要。
> ガードが無く UX 上「実行中でも別ファイルを開ける」を許す方針なら、Submit ハンドラ内で
> `if self.replay_running { Task::done(StopReplayOnly).chain(...) }` の合流が必要になる。

## テスト計画

bin-only クレート + 既存パターン（source-scan）に倣う。

### 新規テストファイル: `tests/replay_session_reset.rs`

| ケース | テスト名 | 検証内容 |
|---|---|---|
| `ReplayPaneRegistry::drain_registered` が registered を空にする | `drain_registered_clears_registered_map` | 単体ユニット（`#[cfg(test)] mod tests`）|
| `drain_registered` が `loaded` / `dismissed` も空にする | `drain_registered_clears_loaded_and_dismissed` | 単体ユニット |
| `drain_registered` の戻り値に登録済み全 pane が含まれる | `drain_registered_returns_all_registered_panes` | 単体ユニット |
| Dashboard に `reset_replay_session` が定義されている | `dashboard_has_reset_replay_session` | source-scan |
| `reset_replay_session` が `panes.close` を呼ぶ | `reset_replay_session_closes_panes_in_pane_grid` | source-scan（body に `self.panes.close` を含む）|
| Submit ハンドラが `reset_replay_session` を Submit 直後に呼ぶ | `submit_handler_resets_replay_session_before_load` | source-scan（`Action::Submit` arm body に `reset_replay_session` を含む、かつ `LoadReplayData` の send より上に位置することを byte offset で検証）|

### 既存テストへの影響

- `tests/auto_generate_replay_panes_auto_bind.rs`: 影響なし（registry の reset は外部から呼ばれる）
- `tests/multiinst_replay_pane_routing.rs`: 影響なし（map_engine_event_to_message と handler の動きは不変）
- `tests/test_replay_order_list_view.rs`: `replay_pane_registry_tracks_pane_ids` は drain を考慮しないため影響なし

## 実環境確認手順

1. `cargo build && target/debug/flowsurface`
2. メニュー > Replay > `examples/multiinst_10pairs_minute.py` を開く
3. 10 銘柄ペインが生成されることを確認
4. メニュー > Replay > `examples/pair_trade_minute.py` を開く
5. **期待**: file1 の 10 ペインが消え、1301 / 7203 の 2 ペインだけが並ぶ
6. 注文一覧が file2 のフィルだけになっていることを確認
7. 一旦 Replay Stop → 再度 file1 を開いた時に 10 ペインが復活することを確認（dismissed もリセットされていること）

## 既知の制約 / 非目標

- 本修正は GUI 側のセッション境界処理のみ。engine 側のセッション概念には踏み込まない（Approach B のスキーマ拡張は将来課題）
- ユーザが手動で `dismiss` した pane も新セッションでは復活する。これは「新セッションは UI を初期状態に戻す」という意図と一致するため許容
- レイアウト切替（active dashboard が切り替わる）と reset の競合は現フェーズでは非目標。`reset_replay_session` は `active_dashboard_mut` で 1 つだけリセットするため、別レイアウトに残った replay pane は触らない（既存 `ReplayDataLoaded` ハンドラと同じ前提）

## 作業チェックリスト

1. [ ] `ReplayPaneRegistry::drain_registered` を実装 + 単体テスト 3 件
2. [ ] `Dashboard::reset_replay_session` を実装
3. [ ] `tests/replay_session_reset.rs` の source-scan テスト 3 件を先に書いて RED 確認
4. [ ] `Action::Submit` アームに `reset_replay_session()` 呼び出しを追加（GREEN）
5. [ ] `ShowReplayDialog` が `replay_running` 中に呼べるかを調査し、必要なら `StopReplayOnly` 連鎖を追加
6. [ ] `cargo fmt && cargo check && cargo test && cargo clippy -- -D warnings`
7. [ ] 実環境確認手順を実施し、ユーザ確認待ちに移行
8. [ ] bug-postmortem を実施し、`MISSES.md` に「セッション境界の暗黙化」パターンを追記
