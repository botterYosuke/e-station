# 改修プラン: GUI 三重状態管理の解消

ステータス: **提案（未着手）**
作成日: 2026-05-07

---

## 1. 現状の問題

### 1.1 状態更新ループが 3 つ共存している

| ループ | 場所 | 管理する状態 |
|--------|------|-------------|
| ① Elm ループ | [src/main.rs — fn update](../../src/main.rs) `update()` 3,579行 | ViewState (translation, scaling)、アプリ全体 |
| ② Canvas ループ | [src/chart.rs — canvas::Program::update](../../src/chart.rs) `canvas::Program::update()` | `Interaction`（ドラッグ中・ズーム中など） |
| ③ HeatmapShader ループ | [src/widget/chart/heatmap.rs — HeatmapShader::update](../../src/widget/chart/heatmap.rs) `HeatmapShader::update()` | `scene.camera`（GPU カメラ）、`CanvasInvalidation`、`RebuildPolicy` |

Iced の設計上 ② は避けられない（Canvas::Program はフレームごとに独自の draw サイクルを持つ）。
問題の本質は ③ が ① の責務を侵食していること、および ① が 89 バリアント・3,579行に膨張していること。

### 1.2 状態が 6 か所に分散している

```
同一ウィジェットのズーム倍率だけで 2 か所に存在する:
  ViewState.scaling             … Elm 管理 (src/chart.rs)
  HeatmapShader.scene.camera   … GPU 管理 (src/widget/chart/heatmap.rs)

スクロール位置:
  Ladder.scroll_px              … insert_depth() が直接書き換える
                                   (Elm メッセージを経由しない)

再描画フラグ:
  Iced Canvas Cache             … clear_all() で要求
  HeatmapShader.canvas_invalidation … 独自フラグで二重管理
```

### 1.3 メッセージ経路が深すぎる

```
EngineEvent
  → map_engine_event_to_message()          src/main.rs:1553
  → Message::ExecutionMarkerReceived       89バリアント中の1つ
  → main.rs::update()                      3,579行の match
  → dashboard.panes[id].update()
  → pane.rs::handle_pane_event()           src/screen/dashboard/pane.rs:1492
  → super::chart::update()                 src/chart.rs:282
  → KlineChart.execution_markers.push()
  → 次の draw() フレームまで画面に出ない
```

---

## 2. 改修方針

### 原則

1. **Canvas::Program のローカル状態は純粋な入力エフェメラルに限定する**
   ドラッグ開始点・ホバー座標など「フレーム内に完結する UI インタラクション」のみ Canvas-local で持つ。
   データ座標・スケール・再描画ポリシーは Elm が所有する。

2. **HeatmapShader は描画専用に格下げする**
   `scene.camera` の位置・スケールを `ViewState` に統合し、HeatmapShader はそれを読むだけにする。
   `CanvasInvalidation` / `RebuildPolicy` フィールドを削除して Elm からの指示で動く。

3. **`Message` enum を論理グループで分割する**
   89 バリアントを持つフラットな enum を分解し、`main.rs::update()` を委譲ハブに縮小する。

---

## 3. フェーズ計画

### フェーズ 1: `Message` enum の分割と `update()` 委譲化（リスク低）

**目的**: main.rs::update() 3,579行 → 各ハンドラーへの委譲 400行以内。

**作業**:

```rust
// 現在: フラットな 89 バリアント
pub enum Message {
    EngineConnected(EngineConnection),
    TachibanaVenueEvent(VenueEvent),
    ReplayDataLoaded { .. },
    Dashboard { layout_id, event: DashboardEvent },
    // ... 85 個以上続く
}

// 改修後: 論理グループ化
pub enum Message {
    Engine(EngineMessage),
    Venue(VenueMessage),
    Replay(ReplayMessage),
    Dashboard { layout_id: LayoutId, event: DashboardEvent },
    Window(WindowMessage),
    Menu(MenuMessage),
    Settings(SettingsMessage),
}
```

`main.rs::update()` はグループごとのハンドラー関数に委譲するだけにする:

```rust
fn update(&mut self, message: Message) -> Task<Message> {
    match message {
        Message::Engine(msg)  => self.handle_engine(msg),
        Message::Venue(msg)   => self.handle_venue(msg),
        Message::Replay(msg)  => self.handle_replay(msg),
        Message::Dashboard { layout_id, event } => {
            self.handle_dashboard(layout_id, event)
        }
        // ...
    }
}
```

**完了条件**:
- `main.rs` の `update()` が 400 行以内
- 全既存テストがパス
- `cargo clippy` 警告なし
- `cargo test --workspace` で `tests/mode_toggle_footer.rs`・`tests/multiinst_replay_pane_routing.rs` を含む全統合テストが PASS
- `map_engine_event_to_message()` が全 `EngineEvent` バリアントを exhaustive に `Message` に変換していることを `#[test]` でカバーする（未配線バリアントへの match が compile-error になること、または enum のバリアント網羅テストを追加すること）。`src/main.rs` の `map_engine_event_to_message` 関数への入力と出力を pin するスナップショットテストでも可。

---

### フェーズ 2: HeatmapShader の状態整理（リスク中）

> ⚠ **ownership 移譲のリスク**: HeatmapShader は単なる描画状態だけでなく、follow（最新データ自動追従）/ pause（手動スクロール中）/ live（ライブ更新中）の遷移ロジックを内包している（`src/widget/chart/heatmap.rs` の `HeatmapShader::update()` 内）。これを Elm 管理に移す際、これら遷移の owner を先に明確にしないと、Phase 2 実装中に責務が曖昧なままになる。着手前に「follow/pause/live の所有者は誰か（`ViewState`? `DashboardMessage`?）」を設計ドキュメントに書き下すこと。
>
> **コード調査結果**: `HeatmapShader::update()` 内で密結合している状態グループ：
> 1. **カメラ**: `scene.camera.offset` / `scale()` — ViewState 候補
> 2. **再描画フラグ**: `canvas_invalidation`（`mark_all()`/`mark_axis_x_motion()`等）— Elm が `cache.clear()` で代替できるが、invalidation の粒度（全体 vs 軸のみ等）の情報を誰が持つか未定義
> 3. **再構築ポリシー**: `rebuild_policy.promote_to_immediate()` / `mark_input()` — タイミング（`Instant::now()`）に依存しており Elm メッセージで表現しにくい
> 4. **follow/pause/live 遷移**: `try_resume_if_x0_visible()` / `try_rebuild_instances()` — このロジックの owner が不明のまま Phase 2 を始めると壊れる
>
> Phase 2 着手条件: 上記4グループそれぞれの「移行先 owner」を設計ドキュメントに明記してから実装に入ること。`camera_offset`/`camera_scale` だけを ViewState に移しても三重状態は解消しない。
>
> **着手前成果物**: `docs/✅python-data-engine/🔵heatmap-phase2-ownership.md`（仮称）を作成し、上記4グループの移行先 owner を決定・記録すること。このファイルが存在し4グループ全ての owner が明記されていることが Phase 2 着手の前提条件。
>
> **Phase 2 着手条件（追加）**: `heatmap-phase2-ownership.md`（または本ドキュメント §X）に、以下の owner table が定義されていること。各行が未記入・未決のままでは Phase 2 に着手しない。
>
> | 遷移・操作 | 決定者（誰が状態を変えるか） | 更新者（誰が ViewState を書くか） |
> |-----------|----------------------------|----------------------------------|
> | follow モード（最新データ自動追従） | ? | ? |
> | pause / resume（手動スクロール解除） | ? | ? |
> | live 復帰（再接続後の追従再開） | ? | ? |
> | catch-up（再接続後の last-known 位置へのスクロール） | ? | ? |
> | rebuild trigger（データ更新時の再描画要求） | ? | ? |
>
> owner table の必須項目:
> - **follow モード**: 誰が追従先を決め、誰が ViewState を更新するか
> - **pause/resume**: 誰が pause 状態を保持し、誰が live 復帰をトリガーするか
> - **catch-up（再接続後）**: 誰が last-known 位置を保持し、誰がスクロール復帰をかけるか
> - **rebuild trigger**: データ更新時の再描画は誰が要求するか（shader vs ViewState vs main loop）

**目的**: HeatmapShader を「描画専用」にし、③ のループを廃止する。

#### 2-A: ViewState に camera を統合

`ViewState` に `camera_offset: Vector` と `camera_scale: f32` を追加し、
[src/chart.rs](../../src/chart.rs) の `ViewState` を Heatmap でも再利用する:

```rust
// src/chart.rs
pub struct ViewState {
    pub cache: Caches,
    pub bounds: Rectangle,
    pub translation: Vector,
    pub scaling: f32,
    pub camera_offset: Vector,   // 追加: Heatmap GPU カメラと共有
    pub camera_scale: f32,       // 追加
}
```

`HeatmapShader.update(ZoomAt { .. })` が `scene.camera` を直接変更している箇所を削除し、
Elm メッセージ経由で `ViewState` を更新後、`HeatmapShader::draw()` が読む形に変える。

> ⚠ 設計判断: `ViewState` を汎用的に拡張する代替として、`HeatmapViewState { camera_offset, camera_scale }` を分離して `ViewState` を内包する方式も検討すること。前者は実装が単純だが他 chart type に不使用フィールドが混入する。後者は型安全だが Phase 2 の作業範囲が広がる。Phase 2 着手時に選択する。
>
> 実装調査メモ: `scene.camera` は `scene.cell`（セルサイズ）・anchor（スクロール固定点）と連動しており、`camera_offset`/`camera_scale` だけを抽出しても残りの状態との整合性が必要。`HeatmapViewState { camera_offset, camera_scale, cell_size, anchor }` のような独立型の方が safer。

#### 2-B: CanvasInvalidation / RebuildPolicy の削除

`HeatmapShader` の以下フィールドを削除:

```rust
// 削除対象
canvas_invalidation: CanvasInvalidation,
rebuild_policy: view::RebuildPolicy,
```

代わりに `HeatmapShader::draw()` は `ViewState` の変化を検知して自動で再構築する
（Iced の `Cache::clear()` は Elm メッセージで呼ぶ）。

**完了条件**:
- `HeatmapShader` のフィールド数が現在の半分以下（直接フィールド数: 約25（ネスト先含む展開: 150+相当） → 75 以下）
- ズーム・パン操作で `ViewState.scaling` と GPU カメラが常に同値。フェーズ 2 実装時に `HeatmapShader::camera_scale() -> f32` を公開メソッドとして追加し、`assert_eq!(view_state.scaling, heatmap_shader.camera_scale())` を通す Rust 単体テストを完了条件の一部とする（現行コードには `camera_scale()` は存在しないため、Phase 2 実装と同時に追加する）。
- 既存 Heatmap 描画テストがパス
- **owner table 検証テスト（必須）**: Phase 2 acceptance は field 削減・line count 達成だけでは不十分。以下の各シナリオに対して Rust 単体テストが存在し PASS すること:
  - follow モード: 新データ到着時に ViewState が追従先へ更新されること
  - pause/resume: pause 中は ViewState が自動更新されず、resume で live 位置へ復帰すること
  - catch-up（再接続後）: 再接続イベント後に last-known 位置へのスクロール復帰が発火すること
  - rebuild trigger: データ更新時に再描画要求が正しい owner から発行されること

---

### フェーズ 3: Ladder.scroll_px の Elm 管理移行（リスク低）

**目的**: `insert_depth()` が `scroll_px` を直接書き換えないようにする。

```rust
// 現在: Elm を経由しない直接変更
pub fn insert_depth(&mut self, depth: &Depth, update_t: u64) {
    self.scroll_px = ...; // 直接書き換え
}

// 改修後: 変更が必要な量を返して呼び出し側に決めさせる
pub fn insert_depth(&mut self, depth: &Depth, update_t: u64) -> Option<f32> {
    // scroll_px は変更しない
    // 変更が必要なら Some(delta) を返す
}
```

呼び出し側（`dashboard.rs::ingest_depth`）が `Some(delta)` を受け取ったら `Message::LadderScroll { pane_id, delta }` を発行する（pane_id は `iter_all_panes_mut` で特定）。

> **multi-pane routing**: 同じ stream を複数 pane が表示する場合、`Message::LadderScroll(delta)` だけでは宛先が特定できない。`Message::LadderScroll { pane_id: PaneId, delta: f32 }` のように pane_id を含める必要がある。現状の `dashboard.rs::ingest_depth()` は `iter_all_panes_mut` で全 matching pane を走査しているため、message 化後も同じ宛先特定ロジックを Elm 側で再現する必要がある。Phase 3 着手時に `Message::LadderScroll` の型定義と routing ロジックを先に決めること。

**完了条件**:
- `Ladder::insert_depth()` が `self.scroll_px` を書き換えない
- `Ladder::insert_depth()` 内の `self.scroll_px =` 直接代入がゼロ件（機械検証: `rg "scroll_px" src/screen/dashboard/panel/ladder.rs`）。手動スクロール（`scroll()`・`reset_scroll()`）は既存のまま。**状態遷移の明文化**: 「自動追従」（`insert_depth` が要求する追従量を `Option<f32>` で返し、Elm が `Message::LadderScroll(delta)` を発行）と「手動スクロール」（ユーザー操作→Elm match アーム）の 2 経路を `ladder.rs` のコメントか専用ドキュメントに明記すること。

---

### フェーズ 4: Canvas::Program ローカル状態の境界明文化（仕上げ）

フェーズ 1〜3 完了後、Canvas-local として許容する状態をドキュメント化する。

**許容（Canvas-local）**:
- `Interaction` のサブ状態（`Panning { start: Point }` など）
- ホバー座標（`cursor: Option<Point>`）

**禁止（必ず Elm 管理）**:
- 表示範囲・スケール（translation, scaling, camera）
- データ配列（execution_markers, trades, depth）
- 再描画ポリシー（RebuildPolicy, CanvasInvalidation）

`canvas::Program` の `type State` に型エイリアスとコメントを付ける形で明文化する。

**完了条件**:
- `canvas::Program::State` の定義に境界コメントが追加されている（`cargo doc --no-deps 2>&1 | grep warning` がゼロ件であることを確認）
- `HeatmapShader` に相当する新規ウィジェットを作るときのガイドとして使える

---

## 4. 作業順序と依存関係

```
フェーズ 1（Message 分割）──┬── 完了後 ──► フェーズ 2（HeatmapShader）── 完了後 ──► フェーズ 4（境界明文化）
                             │
フェーズ 3（Ladder scroll）──┘
  ↑ フェーズ 1 と並行可
```

フェーズ 1 と 3 は並行して進められる。フェーズ 2 は最も影響範囲が広いため最後。

> **着手順序の検討**: Phase 1（Message 分割）を先に行う利点は「diff が小さく cargo test で安全確認できる」こと。ただし orchestration の本体的な複雑さは Phase 2（HeatmapShader ownership）にある。Phase 2 の owner 設計が固まる前に Phase 1 で enum を組み替えると、Phase 2 着手時に再び大きな変更が必要になるリスクがある。
>
> **Phase 2 所有権設計は「着手条件」（必須）**: ~~Phase 2 の所有権設計をラフスケッチした上で Phase 1 に着手することを推奨する。~~ → **`heatmap-phase2-ownership.md` に follow/pause/live 復帰・catch-up・rebuild trigger の owner table が定義され、承認済みであることが Phase 2 着手の必須条件。** ラフスケッチ推奨ではなく、owner table 作成・承認が完了しない限り Phase 2 の実装作業を開始しない。

---

## 5. 計測指標

| 指標 | 現在 | 目標 |
|------|------|------|
| `main.rs` 総行数 | 7,447行 | 4,000行以下 |
| `update()` 行数 | 3,579行 | 400行以下 |
| `Message` バリアント数 | 89 | 30 以下（フラット） |
| `HeatmapShader` フィールド数 | 150+ | 75 以下 |
| `HeatmapShader` が持つ独自 update ループ | 1 | 0 |
| `Ladder.scroll_px` の直接書き換え箇所 | 複数 | 0 |
