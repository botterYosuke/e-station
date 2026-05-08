# リプレイファイル切替時に旧ペインが残る不具合 — 修正計画 (Approach B: session_epoch)

> 関連: [`🔵replay-file-switch-stale-panes.md`](./🔵replay-file-switch-stale-panes.md) (旧 Approach A 計画)
> 本計画は **Approach B を採用** する版。Approach A は不採用。

## 症状

リプレイモードで以下の操作をすると、ファイル2 を開いた後もチャートエリア下段に
ファイル1 由来のペイン群が残る。

1. ファイル1 (`examples/multiinst_10pairs_minute.py`, 10銘柄) を開いてリプレイ実行
2. ファイル2 (`examples/pair_trade_minute.py`, 2銘柄: 1301/7203) を開く

期待: ファイル2 を開いた段階でペインが {1301, 7203} の構成に入れ替わる。
実際: 旧 10 銘柄のペイン (8306/7974/4502/6861/8035/9433/...) が `Waiting for data...` のまま残る。
注文一覧はファイル2 に更新される。

## 関連コード

- IPC スキーマ (Python): [`python/engine/schemas.py:771-786`](../../python/engine/schemas.py#L771)
- IPC スキーマ (Rust DTO): [`engine-client/src/dto.rs:1168-1186`](../../engine-client/src/dto.rs#L1168)
- スキーマバージョン: [`engine-client/src/lib.rs:30-38`](../../engine-client/src/lib.rs#L30) (`SCHEMA_MAJOR=3`, `SCHEMA_MINOR=13`)
- Python 送出箇所:
  - [`python/engine/server.py:2996`](../../python/engine/server.py#L2996) (LoadReplayData 単独経路)
  - [`python/engine/nautilus/engine_runner.py:382`](../../python/engine/nautilus/engine_runner.py#L382)
  - [`python/engine/nautilus/engine_runner.py:608`](../../python/engine/nautilus/engine_runner.py#L608) (start_backtest_replay 経路)
- イベント変換: [`src/main.rs:2131`](../../src/main.rs#L2131) (`map_engine_event_to_message`)
- ペイン自動生成ハンドラ: [`src/main.rs:3760`](../../src/main.rs#L3760) (`Message::ReplayDataLoaded` arm)
- ペイン生成ロジック: [`src/screen/dashboard.rs:1019`](../../src/screen/dashboard.rs#L1019) (`auto_generate_replay_panes`)
- レジストリ: [`src/screen/dashboard/replay_pane_registry.rs`](../../src/screen/dashboard/replay_pane_registry.rs)

## 根本原因

`ReplayDataLoaded` イベントには **「セッション識別子」が無い**。
GUI の `replay_pane_registry` はプロセス起動時に `new()` されたあと永続的に
`loaded` / `registered` を蓄積し続け、リセットの契機が `ClosePane` (ユーザー
手動の `dismiss`) しか存在しない。

そのため、

- ファイル1 を開いたとき: 10 銘柄が `mark_loaded` され 10 個のペインが
  `register_pane` される
- ファイル2 を開いたとき: `instrument_ids = ["1301", "7203"]` の各々について
  `auto_generate_replay_panes` が呼ばれるが、ファイル1 の登録銘柄に対する
  クリーンアップ経路がどこにも無い

結果としてファイル1 由来の 8 ペインが孤児としてペイングリッドに残る。
これらは IPC ストリームの再バインドも行われないため `Waiting for data...`
状態のまま固定される。

## 設計判断

### 選択肢比較

| 案 | 概要 | 評価 |
|---|---|---|
| A | Form Submit 時に GUI 側で旧ペイン群を一括クローズ + registry リセット | engine 側の協力不要だが、「セッション境界」の責務が GUI に偏在し、再接続/プロセス再起動などのケースをすべて GUI 側で網羅する必要がある |
| **B (採用)** | engine 側に `session_epoch: u64` を持たせ `ReplayDataLoaded` で通知。GUI は前 epoch の registered ペインを全閉じしてから新セッションを構築 | engine が「新セッション開始」を一意に表現でき、後段イベント (`ReplayFinished` など) との epoch 整合検査も将来的に拡張可能 |
| C | `instrument_ids` に含まれない既存 instrument を自動クローズ | スキーマ変更不要だが「追加ロード」と「セッション切替」を区別できない。将来 incremental load を入れた時に破綻する |

### B 採用理由

- 「セッション境界」を engine が一意に発番するため、GUI/CLI/将来の Web フロント
  すべてが同じ境界判定を共有できる
- `ReplayFinished` / `BarUpdate` / `OrderEvent` などの後段イベントに epoch を
  伝播させれば、古い epoch のイベントを silently drop するガードに拡張可能
  (本フェーズでは未実装、設計余地として明示)
- スキーマ変更は `Option<u64>` の追加のみで `serde(default)` により後方互換
- engine プロセスが「セッションのライフサイクル」の唯一の真実源になる

### `session_epoch` の意味論

- `session_epoch: u64` は **engine プロセス内で単調増加** する整数
  (起動時 0、`LoadReplayData` 受理ごとに +1)
- **発番元は `_handle_load_replay_data` のみ** （review-fix R1 HIGH-1 で確定）。
  `_handle_start_engine` は B3 ガード (`ReplayState.LOADED` 必須) で
  `LoadReplayData` 受理を前提にしており、そこで発番済みの
  `self._replay_session_epoch` を **再利用** して `runner.start_backtest_replay(_streaming)`
  に渡す。`_handle_start_engine` 経路で再発番すると、1 ファイル切替で
  `LoadReplayData→StartEngine` の 2-hop epoch が +2 進み、GUI が直前
  `LoadReplayData` で生成したペインを drain して誤再生成する。
- `1 ファイル切替 = 1 epoch` の不変条件は
  `python/tests/test_server_engine_dispatch.py::test_load_then_start_emits_same_session_epoch`
  (outbox 観測 e2e) と
  `test_start_engine_source_does_not_increment_session_epoch`
  (source-scan) の 2 段で pin している。
- `ReplayDataLoaded` 以外の replay 関連イベントには **本フェーズでは付与しない**
  (将来拡張余地として明文化のみ)
- engine プロセスが再起動されると 0 に戻るが、GUI 側は engine 切断時に
  `last_replay_session_epoch = None` にリセットするため衝突しない
- GUI 側の比較は `!=` (不一致検出) であり、`>` (前進検出) ではない
  (engine 再起動による巻き戻りを許容するため)

## 修正内容

### 1. Python (engine) 側

#### `python/engine/schemas.py`

`ReplayDataLoaded` に `session_epoch: int | None = None` を追加。

```python
class ReplayDataLoaded(IpcMessage):
    event: Literal["ReplayDataLoaded"] = "ReplayDataLoaded"
    strategy_id: str | None = None
    bars_loaded: int
    trades_loaded: int
    instrument_id: str | None = None
    instrument_ids: list[str] | None = None
    granularity: ReplayGranularity | None = None
    # schema 3.14: 新セッション識別子。LoadReplayData 受理ごとに +1。
    # 旧 GUI (minor<14) は Option<u64> 互換で None として無視される。
    session_epoch: int | None = None
    ts_event_ms: int
```

`SCHEMA_MINOR` を 13 → 14 に bump。

#### `python/engine/server.py`

`EngineServer` (またはセッションを保持する適切なクラス) にカウンタを追加:

```python
class EngineServer:
    def __init__(self, ...):
        ...
        self._replay_session_epoch: int = 0

    def _next_replay_epoch(self) -> int:
        self._replay_session_epoch += 1
        return self._replay_session_epoch
```

`LoadReplayData` ハンドラ ([server.py:2923](../../python/engine/server.py#L2923))
で `_next_replay_epoch()` を呼び `ReplayDataLoaded` payload に同梱する。

#### `python/engine/nautilus/engine_runner.py`

`start_backtest_replay` 経路 (L382, L608) も同様に `session_epoch` を埋める。
EngineRunner には server から epoch を引数で渡す (グローバル状態を増やさない)。

### 2. Rust (engine-client) 側

#### `engine-client/src/lib.rs`

```rust
pub const SCHEMA_MINOR: u16 = 14;
```

履歴コメントに `14: ReplayDataLoaded.session_epoch` を追記。

#### `engine-client/src/dto.rs`

```rust
ReplayDataLoaded {
    ...
    granularity: Option<ReplayGranularity>,
    /// schema 3.14: 新セッション識別子 (engine 内で LoadReplayData ごとに +1)。
    /// GUI は前 epoch の `replay_pane_registry` を全クリアしてから
    /// 新 epoch のペインを生成する。旧 engine (minor<14) からは `None`。
    #[serde(default)]
    session_epoch: Option<u64>,
    ts_event_ms: i64,
},
```

### 3. Rust (GUI) 側

#### `src/screen/dashboard/replay_pane_registry.rs`

新メソッド:

```rust
impl ReplayPaneRegistry {
    /// 現在 registered な全ペインを返却し、内部状態を完全リセットする。
    /// `dismissed` も含めて全消去する (新セッションでは旧 dismiss は無効)。
    pub fn drain_all_registered(&mut self) -> Vec<pane_grid::Pane> {
        let panes: Vec<pane_grid::Pane> = self.registered.values().copied().collect();
        self.loaded.clear();
        self.registered.clear();
        self.dismissed.clear();
        panes
    }
}
```

設計判断:
- `dismissed` もクリアする (新セッションでは過去 dismiss を持ち越さない)
- `registered` の値だけ返却し、`pane_grid::close` は呼び出し側 (Dashboard) が行う
  (registry は pane_grid を保持しないため)

#### `src/main.rs` — Flowsurface 構造体

```rust
struct Flowsurface {
    ...
    /// 直前に処理した `ReplayDataLoaded.session_epoch`。新 epoch を観測したら
    /// `replay_pane_registry` を全リセットして旧ペインを閉じる。engine 切断時に
    /// `None` にリセットする。
    last_replay_session_epoch: Option<u64>,
}
```

#### `src/main.rs` — Message 定義

`Message::ReplayDataLoaded` に `session_epoch: Option<u64>` を追加。

#### `src/main.rs` — `map_engine_event_to_message`

`session_epoch` を `EngineEvent::ReplayDataLoaded` から `Message::ReplayDataLoaded`
へ転送 (`..` で握り潰さない — schema 3.13 修正と同じパターンの再発防止)。

#### `src/main.rs` — `Message::ReplayDataLoaded` ハンドラ

```rust
Message::ReplayDataLoaded {
    instrument_id,
    instrument_ids,
    granularity,
    session_epoch,
    ..
} => {
    // schema 3.14: 新 epoch を観測したら旧ペインを全閉じ。
    // None → Some / Some(N) → Some(M ≠ N) で session_changed=true。
    // 旧 engine (永続 None) や同一 epoch 連続 (incremental 想定) は false。
    let session_changed = match (self.last_replay_session_epoch, session_epoch) {
        (Some(prev), Some(curr)) => prev != curr,
        (None, Some(_)) => self.replay_pane_registry_non_empty(),
        _ => false,
    };
    if session_changed {
        let dashboard = self.active_dashboard_mut();
        let stale = dashboard.replay_pane_registry.drain_all_registered();
        let n = stale.len();
        for pane in stale {
            dashboard.panes.close(pane);
        }
        log::info!(
            "ReplayDataLoaded: session_epoch={session_epoch:?} \
             — closed {n} stale panes from previous session"
        );
    }
    if session_epoch.is_some() {
        self.last_replay_session_epoch = session_epoch;
    }
    // ...既存の auto_generate_replay_panes 呼び出し...
}
```

> `replay_pane_registry_non_empty()` は registry が空かどうかを返す薄い
> ヘルパー。初回の `None → Some` 遷移で「すでに何かが登録されている」場合だけ
> リセットを発動するためのガード (起動直後の正常系では空なので何もしない)。

#### `src/main.rs` — engine 切断時のリセット

engine の reconnect / disconnect ハンドラで:

```rust
self.last_replay_session_epoch = None;
```

(プロセス再起動で epoch が 0 に戻ったとき、`Some(N) → Some(0)` の `!=` 比較が
誤発火しないようにするため。drain は不要 — どのみち空 registry 状態。)

### 4. 後方互換マトリクス

| GUI \ engine | 旧 (3.13) | 新 (3.14+) |
|---|---|---|
| 旧 (3.13) | 既存挙動 (本バグ残存) | engine 側 `session_epoch` を `serde` 無視 → 既存挙動 (本バグ残存) |
| 新 (3.14+) | `session_epoch=None` 連続受信 → `session_changed=false` → 既存挙動 (本バグ残存だが旧 engine 互換のため許容) | **修正発動 — 期待挙動** |

新 GUI × 旧 engine は schema_minor を起動時に検査する既存ロジックでログ警告を
出す形で許容する (既存運用と同じ)。

## テスト計画

### 新規テストファイル: `tests/replay_session_epoch_pane_reset.rs`

bin-only クレートのため source-scan + ライブラリ単体ロジック検証を併用。

| # | ケース | テスト名 | 種別 |
|---|---|---|---|
| 1 | `ReplayPaneRegistry::drain_all_registered()` が登録 pane 列を返し内部 (loaded/registered/dismissed) を空にする | `drain_all_registered_returns_panes_and_clears_state` | unit (`#[cfg(test)]`) |
| 2 | `Message::ReplayDataLoaded` に `session_epoch: Option<u64>` フィールドが存在する | `message_replay_data_loaded_has_session_epoch_field` | source-scan |
| 3 | `map_engine_event_to_message` が `session_epoch` を `..` で捨てず転送する | `dispatcher_forwards_session_epoch_to_message` | source-scan |
| 4 | `Flowsurface` 構造体に `last_replay_session_epoch: Option<u64>` がある | `flowsurface_has_last_replay_session_epoch_field` | source-scan |
| 5 | ハンドラが `last_replay_session_epoch` と `session_epoch` を比較する | `handler_compares_session_epoch_for_change_detection` | source-scan |
| 6 | session_changed=true 時に `drain_all_registered` + `panes.close` を呼ぶ | `handler_closes_stale_panes_when_session_epoch_changes` | source-scan |
| 7 | 旧 engine 互換: `session_epoch=None` 連続では close を呼ばない | `handler_does_not_close_when_session_epoch_is_none` | source-scan (`(None, None) | (Some(_), None) => false`) |
| 8 | 同一 epoch の連続 ReplayDataLoaded では close を呼ばない | `handler_does_not_close_for_same_session_epoch` | source-scan (`prev != curr` パターンの存在) |
| 9 | engine 切断ハンドラが `last_replay_session_epoch = None` を実行する | `disconnect_resets_last_replay_session_epoch` | source-scan |

### 既存テスト更新

- `tests/multiinst_replay_pane_routing.rs`:
  `Message::ReplayDataLoaded` の pin パターン (`message_replay_data_loaded_has_instrument_ids_field` など) に `session_epoch` を追加。
- `python/tests/test_engine_runner_replay.py`:
  `ReplayDataLoaded.session_epoch` が `int >= 1` であることを assert。
  連続 LoadReplayData で epoch が単調増加することを assert。
- `python/tests/test_server_engine_dispatch.py:136` 周辺:
  LoadReplayData → ReplayDataLoaded outbox の epoch 検証を追加。

### 手動確認 (受け入れ条件)

1. `cargo build && target/debug/flowsurface`
2. メニュー > Replay > `examples/multiinst_10pairs_minute.py` を開く → 10 ペイン生成
3. メニュー > Replay > `examples/pair_trade_minute.py` を開く
4. **期待**: 旧 8 ペイン (8306/7974/4502/6861/8035/9433/...) が消え、{1301, 7203} のみが並ぶ
5. ログに `ReplayDataLoaded: session_epoch=Some(2) — closed N stale panes` が出る
6. もう一度 file1 を開いて 10 ペインに復活 (dismissed もリセットされている)

## 作業手順

1. [x] **Python**: `schemas.py` に `session_epoch` 追加 + `SCHEMA_MINOR` 14 bump
2. [x] **Python**: `server.py` に `_replay_session_epoch` カウンタと `_next_replay_session_epoch()` 追加
3. [x] **Python**: `LoadReplayData` ハンドラと `engine_runner.py` 2 箇所で `session_epoch` を埋める
4. [x] **Python**: `test_engine_runner_replay.py` / `test_server_engine_dispatch.py` 更新
5. [x] **Rust DTO**: `engine-client/src/dto.rs` の `ReplayDataLoaded` に `session_epoch: Option<u64>` 追加
6. [x] **Rust DTO**: `engine-client/src/lib.rs` `SCHEMA_MINOR` 14 bump + 履歴追記
7. [x] **Rust GUI**: `replay_pane_registry.rs` に `drain_all_registered()` + `has_registered_panes()` 追加・unit テスト 2 件
8. [x] **Rust GUI**: `src/main.rs` Message に `session_epoch` 追加 / Flowsurface に `last_replay_session_epoch` 追加 / `map_engine_event_to_message` 転送 / ハンドラに切替検知ブロック追加 / `EngineRestarting(true)` でリセット
9. [x] **テスト**: `tests/replay_session_epoch_pane_reset.rs` 新規 (8 source-scan + 1 unit = 9 ケース)
10. [x] **既存テスト更新**: `tests/multiinst_replay_pane_routing.rs` の pin パターンに `session_epoch` 追加 + handler window を 4_000 → 8_000 に拡張
11. [x] `cargo fmt && cargo check && cargo test && cargo clippy -- -D warnings`（既存 `tt6_switch_mode_handler_body_contains_dirty_check_flow` は本変更前から失敗 — 別 Issue）
12. [ ] 手動確認 (上記 6 ステップ)
13. [x] bug-postmortem: 「セッション境界 IPC 欠如パターン」を `MISSES.md` に追記

## 2026-05-06 追記: 実装ノート

### 実装上の判断

- **`drain_all_registered` ユニットテストは crate 内部に置いた**: 計画書では
  `tests/replay_session_epoch_pane_reset.rs` に集約する案だったが、
  `pane_grid::Pane` の constructor は `pub(super)` で、外部 integration test
  からは生成できない（試したら `Pane(0)` の構築は不可能）。
  そのため drain_all_registered の実体検証は
  `src/screen/dashboard/replay_pane_registry.rs::tests` の `#[cfg(test)] mod`
  に置いた。`pane_grid::State::<()>::new(())` から `Pane` を借りて
  `register_pane` → `drain_all_registered` の往復を検証している。
  integration test ファイル側にはこの理由を comment で明示。
- **既存 `tests/multiinst_replay_pane_routing.rs` の handler window 拡張**:
  ハンドラに session-change 検出ブロックを追加した結果、
  `Task::batch` が 4_000 byte の window を超えてしまい
  `handler_uses_task_batch_for_multi_instrument` が false-fail。
  全 5 箇所の `min(4_000)` を `min(8_000)` に拡張して通した。
  source-scan テストの構造的脆弱性として要確認。
- **disconnect reset を `EngineRestarting(true)` 経路に集約**: 計画書の
  「engine の reconnect / disconnect ハンドラ」表現に対し、main.rs では
  `Message::EngineRestarting(restarting)` の `if restarting { ... }` ブロック
  が disconnect 専用のフックになっているため、ここに集約。
- **engine_runner.py: session_epoch をパラメータで注入**: グローバル状態を増やさず、
  `start_backtest_replay` / `start_backtest_replay_streaming` の引数に
  `session_epoch: int | None = None` を追加。server.py から
  `self._next_replay_session_epoch()` の戻り値を渡す。
  Runner 単体テスト（on_event を直接呼ぶ）では `None` が emit される。

### 既存テスト pre-existing failure

- `tests/mode_toggle_footer.rs::tt6_switch_mode_handler_body_contains_dirty_check_flow`
  は本変更着手前から既に失敗していた（`git stash` で本変更を退避して再実行
  しても同じ assert で fail）。Action::SwitchMode から SaveAndSwitchMode まで
  6_000 byte の window では届かないという同種の脆弱性。本 Issue では別件として
  扱い、修正は次回の cleanup ラウンドに送る。

### 2026-05-06 レビュー指摘修正: session-level pane の登録漏れ

**指摘内容**: `drain_all_registered()` が閉じるのは
`replay_pane_registry.registered` に入っている pane だけだが、replay 用
`OrderList` / `BuyingPower` は session-level pane として
`auto_generate_replay_panes()` で生成されるとき `register_pane()` を呼んで
いなかった。さらに `drain_all_registered()` は `dismissed` までクリアするため、
ファイル切替後の最初の `auto_generate_replay_panes()` で新しい
`OrderList` / `BuyingPower` が再生成され、古い pane は残ったまま重複する。

**修正**:
- [src/screen/dashboard.rs](../../src/screen/dashboard.rs) の N1.15 (OrderList) /
  N1.16 (BuyingPower) ブロックで pane 生成後に `register_pane("", "OrderList", new_pane)` /
  `register_pane("", "BuyingPower", new_pane)` を呼ぶ。sentinel `instrument_id=""`
  は既存の `dismiss` / `should_generate` と同一規約。
- [tests/replay_session_epoch_pane_reset.rs](../../tests/replay_session_epoch_pane_reset.rs):
  `session_level_order_list_pane_is_registered_for_drain` /
  `session_level_buying_power_pane_is_registered_for_drain` を追加して
  `dashboard.rs` ソース内で `register_pane("", "OrderList", ...)` /
  `register_pane("", "BuyingPower", ...)` 呼び出しが存在することを pin。
- [src/screen/dashboard/replay_pane_registry.rs](../../src/screen/dashboard/replay_pane_registry.rs):
  `drain_all_registered_includes_session_level_sentinel_panes` ユニットテストを追加し、
  sentinel キー (`("", "OrderList")` / `("", "BuyingPower")`) が drain で確実に
  返却されることを確認。

なお session-level pane を registry に入れた副作用として、ユーザーが OrderList を
dismiss した状態でファイル切替が起きた場合、`drain_all_registered()` が
`dismissed` も clear するため OrderList が新セッションで再生成される。これは
「ファイル切替 = 新セッション」の意味論として正しい挙動。

### 2026-05-06 R1 レビュー反映 (review-fix-loop ラウンド 1)

並列レビュアー 5 体（rust-reviewer / silent-failure-hunter / iced-architecture-reviewer
/ ws-compatibility-auditor / general-purpose）+ R2 サニティ 2 体で集約。

**集約: CRITICAL 2 / HIGH 4 / MEDIUM 7 / LOW 5**

#### 解消した R1 指摘
- **CRITICAL-1** `engine-client/tests/schema_v2_4_nautilus.rs:329` の match arm
  に `session_epoch: _,` 追加（`..` 不使用で全フィールド列挙していたため
  フィールド追加で `cargo test --workspace` がコンパイルエラー化していた）。
  MISSES.md「EngineEvent フィールド追加時の `..` 黙示破棄」の **逆パターン** =
  既存 arm が全列挙のため新フィールド追加で割れる。
- **CRITICAL-2** 同ファイル `assert_eq!(SCHEMA_MINOR, 13)` を 14 に同期 + 履歴
  コメント追記。fn 名 `schema_minor_is_7_for_positions` →
  `schema_minor_matches_current_bump` に rename。
- **HIGH-1 (epoch 二重発番)** `python/engine/server.py:_handle_start_engine`
  の streaming/non-streaming 両分岐で `_next_replay_session_epoch()` 呼出を削除し
  `session_epoch=self._replay_session_epoch` で再利用に変更。「1 ファイル切替 =
  1 epoch」の不変条件を確立。
- **HIGH-2 (e2e 観測テスト)** `test_load_then_start_emits_same_session_epoch`
  を追加（LoadReplayData → StartEngine で `ReplayDataLoaded` × 2 の
  `session_epoch` が同値であることを outbox 観測で pin）。
- **HIGH-3 (None→Some ガード pin)** `handler_none_to_some_uses_has_registered_panes_guard`
  追加。
- **HIGH-4 (StartEngine source-pin)** `test_start_engine_source_does_not_increment_session_epoch`
  追加（`_next_replay_session_epoch()` の実呼び出しが無いこと + 値再利用パターンが
  存在することを source-scan で pin）。
- **MEDIUM (iced 二段呼出統合)** `src/main.rs::Message::ReplayDataLoaded` の
  `(None, Some(_))` arm で `active_dashboard()` (immut) と
  `active_dashboard_mut()` の二段取得を 1 回の mutable 借用に統合。
- **MEDIUM (drain ループコメント)** `for pane in stale` の前に「pane_grid::State::close
  は未知 id で no-op」コメント追加。
- **MEDIUM (log 観測点)** `_handle_load_replay_data` / `engine_runner` emit に
  `log.info` 追加（GUI 側の close ログと突合できるよう）。
- **MEDIUM (thread-safety doc)** `_next_replay_session_epoch` docstring に
  「event loop main thread からのみ呼ぶこと」を明記。
- **LOW (split warn)** `src/screen/dashboard.rs` の OrderList / BuyingPower
  split 失敗パスに `log::warn!` 追加（silent skip 防止）。
- **LOW (履歴コメント)** `engine-client/src/lib.rs` minor=13 履歴を
  「instrument_ids 追加（複数銘柄対応）」に拡充。

#### 設計判断（§設計判断 / session_epoch の意味論 に追補済み）
- **1 ファイル切替 = 1 epoch**。発番元は `_handle_load_replay_data` のみ。
- `_handle_start_engine` は B3 ガード (`ReplayState.LOADED`) により
  LoadReplayData 受理を前提とするので `self._replay_session_epoch >= 1` を再利用。
- `engine_runner` 側は `session_epoch: int | None` 引数で受領するだけで自前の
  カウンタを持たない（グローバル状態の単一真実源を server に固定）。

#### 新規 MISSES.md 候補（次回追記検討）
1. **counter / epoch の二重発番**: 「外側で発番 → 内側でも発番」は schema 上の
   semantics 違反になりやすい。観測点 (outbox / IPC 受信側) で連続値の等価性を
   assert する e2e テストを 1 本必須化。本 fix の `test_load_then_start_emits_same_session_epoch`
   が好例。
2. **enum バリアント全列挙 match arm + フィールド追加**: 既存 integration test
   の `match EngineEvent { ... }` に新フィールドが追加されると pattern が
   non-exhaustive で割れる。`..` を使わない全列挙パターンが残っていると CRITICAL
   コンパイルエラー化。schema bump PR テンプレに「全 match arm の grep 結果を
   確認」を追加。
3. **SCHEMA_MINOR 値のテストハードコード**: integration test に
   `assert_eq!(SCHEMA_MINOR, N)` を直書きすると bump 時に同期忘れる。本ラウンドで
   `cargo test --workspace` を呼ぶまで気付かなかった。bump チェックリストに該当
   テストの値更新を必須化。
4. **source-scan handler_window のスケール脆弱性**: ハンドラに分岐追加するたびに
   `min(N)` 窓を拡張する N 競争。`extract_handler_body(msg_pattern)` ヘルパー
   抽出が望ましい。本ラウンドでは 4_000→8_000 で対応、別 Issue で構造改修。

#### 持ち越し（LOW、本 Issue スコープ外）
- M3-rfind 脆弱性 / `_REQUIRED_ATTRS` 値一致は LOW のまま持ち越し。
- handler_window スケール問題は MISSES.md 候補 #4 と合わせて別 Issue 化推奨。
- pre-existing `tt6_switch_mode_handler_body_contains_dirty_check_flow`
  は本変更着手前から失敗、別 Issue。

#### R1 後の検証
- `cargo test --workspace`: 全緑（pre-existing `tt6_*` 除く）
- `cargo clippy -- -D warnings`: 全緑
- `cargo fmt --check`: 全緑
- `uv run pytest python/tests/test_server_engine_dispatch.py python/tests/test_engine_runner_replay.py`:
  全 76 件緑（新規テスト 4 件追加: monotonic / source-pin / e2e outbox /
  Rust source-pin）

### 2026-05-06 R2 サニティ反映 (review-fix-loop ラウンド 2)

silent-failure-hunter + general-purpose の 2 体で再レビュー。

**集約: CRITICAL 0 / HIGH 2 / MEDIUM 1 / LOW 5**（HIGH/MEDIUM すべて解消）

#### 解消した R2 指摘
- **HIGH-R2-1**（計画書整合）: §設計判断 / session_epoch の意味論 に
  「LoadReplayData のみが発番、StartEngine は再利用」「不変条件は 2 段
  （outbox e2e + source-scan）で pin 済み」を追補。
- **HIGH-R2-2**（e2e 観測）: `test_load_then_start_emits_same_session_epoch`
  を追加。LoadReplayData → StartEngine の outbox に流れる
  `ReplayDataLoaded` × 2 の `session_epoch` が同値（== 1）であることを直接
  observation。source-scan の等価リファクタ脆弱性を補強。
- **MEDIUM-R2-1**（streaming 経路の log 欠落）: `start_backtest_replay_streaming`
  経路の emit 後に対称な `log.info` を追加（`replace_all` で書換時に
  非 streaming 側のみ更新していた）。

#### R2 後の検証
- `cargo test --workspace`: 全緑（`tt6_*` のみ pre-existing failure）
- `uv run pytest python/tests/test_server_engine_dispatch.py
   python/tests/test_engine_runner_replay.py`: 76 件全緑（新規 e2e 1 件追加）

#### 収束判定
**MEDIUM 以上ゼロ達成**（HIGH 0 / MEDIUM 0 / LOW 5）。LOW は本 Issue スコープ外
（rfind 脆弱性 / `_REQUIRED_ATTRS` 値一致 / engine_runner デフォルト引数 None
が silent / Rust ハンドラのコメント補強）として持ち越し。`/review-fix-loop`
は R2 で収束。

### 受け入れ条件 1〜5 のステータス

- 1. 作業手順 ✅: 12 を除く全項目に ✅。
- 2. テスト計画 9 ケース＋既存テスト更新: GREEN
  （8 source-scan + 1 unit + 既存 7 ケース）。
- 3. `cargo clippy -- -D warnings`: GREEN。
- 4. 手動確認のログ文言: 実装は
  `"ReplayDataLoaded: session_epoch={session_epoch:?} — closed {n} stale panes from previous session"`
  で出力。手動確認は別途実施が必要。
- 5. ファイル切替時の挙動: 実装上は session_changed=true 時に
  `replay_pane_registry.drain_all_registered()` で全 pane id を回収し、
  `dashboard.panes.close(pane)` を全件に呼ぶ。手動 GUI 確認は別途実施が必要。

## 既知の制約／非目標

- 本フェーズでは `session_epoch` を `ReplayDataLoaded` のみに付与する。
  `ReplayFinished` / `BarUpdate` / `OrderEvent` への epoch 付与による
  古い epoch イベントの silently drop ガードは別 Issue とする。
- `LoadReplayData` を立て続けに 2 回送るレース (epoch=N, epoch=N+1 の
  `ReplayDataLoaded` が逆順到着) は本フェーズでは想定しない。`!=` 比較は
  巻き戻りも切替として扱うため、逆順到着では「N+1 → N」を境界として誤検知
  し直前の epoch=N+1 ペインを破棄するリスクがある。現状の WS IPC は
  順序保証があるため許容、分散 IPC へ移行する場合は要再設計。
- `replay_pane_registry.dismissed` も新セッションで全消去するため、ファイル1 で
  「特定の銘柄ペインを閉じた」設定はファイル2 に持ち越されない。これは
  「ファイル切替 = 新セッション」の意味論として正しい挙動とする。
- レイアウト切替 (active dashboard 切替) と reset の競合は非目標。
  `active_dashboard_mut` で 1 つだけリセットする (既存 `ReplayDataLoaded`
  ハンドラと同じ前提)。

## 参照

- 関連先行修正: [`docs/✅python-data-engine/🔵multiinst-replay-pane-missing.md`](./🔵multiinst-replay-pane-missing.md)
  (schema 3.13 で `instrument_ids` 複数銘柄対応を入れた時の修正)
- 不採用案: [`docs/✅python-data-engine/🔵replay-file-switch-stale-panes.md`](./🔵replay-file-switch-stale-panes.md)
  (Approach A — Form Submit 起点の GUI 側リセット)
