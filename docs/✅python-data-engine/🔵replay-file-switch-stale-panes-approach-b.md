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

1. [ ] **Python**: `schemas.py` に `session_epoch` 追加 + `SCHEMA_MINOR` 14 bump
2. [ ] **Python**: `server.py` に `_replay_session_epoch` カウンタと `_next_replay_epoch()` 追加
3. [ ] **Python**: `LoadReplayData` ハンドラと `engine_runner.py` 2 箇所で `session_epoch` を埋める
4. [ ] **Python**: `test_engine_runner_replay.py` / `test_server_engine_dispatch.py` 更新
5. [ ] **Rust DTO**: `engine-client/src/dto.rs` の `ReplayDataLoaded` に `session_epoch: Option<u64>` 追加
6. [ ] **Rust DTO**: `engine-client/src/lib.rs` `SCHEMA_MINOR` 14 bump + 履歴追記
7. [ ] **Rust GUI**: `replay_pane_registry.rs` に `drain_all_registered()` 追加 (RED テスト 1 を先に書く)
8. [ ] **Rust GUI**: `src/main.rs` Message に `session_epoch` 追加 / Flowsurface に `last_replay_session_epoch` 追加 / `map_engine_event_to_message` 転送 / ハンドラに切替検知ブロック追加 / 切断ハンドラでリセット
9. [ ] **テスト**: `tests/replay_session_epoch_pane_reset.rs` 新規 (9 ケース)
10. [ ] **既存テスト更新**: `tests/multiinst_replay_pane_routing.rs` の pin パターンに `session_epoch` 追加
11. [ ] `cargo fmt && cargo check && cargo test && cargo clippy -- -D warnings`
12. [ ] 手動確認 (上記 6 ステップ)
13. [ ] bug-postmortem: 「セッション境界 IPC 欠如パターン」を `MISSES.md` に追記

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
