---
# HeatmapShader Phase 2 Owner 設計

作成日: 2026-05-07
ステータス: 承認済み（B3 着手条件）

参照元: `docs/✅python-data-engine/🔵gui-triple-state-refactor.md` Phase 2 着手条件

---

## owner table（Phase 2 実装後の状態）

| 遷移・操作 | 決定者（誰が状態を変えるか） | 更新者（誰が ViewState / 描画状態を書くか） |
|-----------|------------------------------|---------------------------------------------|
| follow モード（最新データ自動追従） | `Anchor::tick_live_and_auto_follow()` — x=0 可視判定で `Anchor::Live` を維持 | `HeatmapShader::auto_update_anchor()` — `Anchor` 内で完結。Phase 2 後も `HeatmapShader` が保持し Elm の `ViewState` には移さない（理由: follow 判定は GPU カメラの `x=0` 可視性に依存し、Elm メッセージサイクルより先に解決する必要がある） |
| pause / resume（手動スクロール解除） | `Anchor::update_auto_follow()` が `Live → Paused` を決定。`resume_to_live()` が `Paused → Live` を決定 | `HeatmapShader::update()` — `try_resume_if_x0_visible()` と `Message::PauseBtnClicked` ハンドラが `anchor` を書き換える。Phase 2 後も `HeatmapShader` が保持する |
| live 復帰（再接続後の追従再開） | `HeatmapShader::auto_update_anchor()` が `FollowStateChange::ResumedToLive` を検知。`try_resume_if_x0_visible()` が手動 pan/zoom 後の復帰を検知 | `HeatmapShader` — `rebuild_all()` + `rebuild_policy` リセットを内部で実行。Elm は `Message::PauseBtnClicked` を仲介するのみ |
| catch-up（再接続後の last-known 位置へのスクロール復帰） | `HeatmapShader::insert_depth()` 呼び出し側（`pane.rs`）が再接続後もデータを流し続けることで自然に復帰。明示的な catch-up メッセージは存在しない | `Anchor` が `scroll_ref_bucket` と `render_latest_time` を保持しているため、新データが流れると `invalidate()` → `tick_live_and_auto_follow()` で自動追従が再開する |
| rebuild trigger（データ更新時の再描画要求） | `HeatmapShader::insert_depth()` が `rebuild_policy.promote_to_immediate()` を呼ぶ。`invalidate()` が `RebuildPolicy::decide()` で実行判定する | `HeatmapShader` 内部 — `rebuild_all()` / `rebuild_instances()` を直接呼ぶ。Phase 2 で `RebuildPolicy` を削除する場合、`insert_depth()` が直接 `rebuild_all()` を呼ぶか、`canvas_caches` を clear する形に変える（詳細は B3 実装方針を参照） |
| VenueReady sticky cache（readiness の保持と読取） | `spawn_venue_ready_bridge()` — `EngineEvent::VenueReady` 受信時に `VENUE_READY_CACHE` に insert する（`src/main.rs:622`） | `cached_venue_is_ready()` が読む（`src/main.rs:659`）。`VENUE_READY_CACHE` は `static OnceLock<Arc<Mutex<FxHashSet<String>>>>` であり `HeatmapShader` の管理外。HeatmapShader は VenueReady cache を一切読まない |
| cache invalidation（LoginStarted / LoginCancelled / relogin / reconnect） | `spawn_venue_ready_bridge_on()` — `VenueLoginStarted` / `VenueLoginCancelled` / `VenueError` 受信時に `cache.lock().remove(&venue)` する（`src/main.rs:626-629`）。reconnect 時は `cache.lock().await.clear()` で全エントリを消去（`src/main.rs:851, 973`） | `VENUE_READY_CACHE` 静的グローバル。`HeatmapShader` の `canvas_invalidation` / `CanvasCaches` とは別管理。HeatmapShader 側の cache invalidation（`CanvasInvalidation`）は `invalidate()` の `canvas_invalidation.apply()` で Iced の `Cache::clear()` を呼ぶ形で実行される |
| stale-ready 抑制ガード（reconnect 直後の誤 bootstrap 防止） | `spawn_venue_ready_bridge_on()` が reconnect 前に `VENUE_READY_CACHE.clear()` を実行することでガードする。新しい `VenueReady` が届くまでは `cached_venue_is_ready()` が `false` を返し、bootstrap が走らない（`src/main.rs:966-975` および `src/main.rs:847-854`） | `VENUE_READY_CACHE` が保持者。`HeatmapShader` はこのガードを管理しない |

---

## コード上の事実（owner table の根拠）

### 1. HeatmapShader.update() の戻り値

`pane.rs:1832` — `c.update(message)` は `()` を返す。Elm への Effect 発行は行わない。HeatmapShader の状態遷移はすべて内部完結している。

### 2. Anchor enum の構造（view.rs）

```rust
pub enum Anchor {
    Live { scroll_ref_bucket: i64, render_latest_time: u64, x_phase_bucket: f32 },
    Paused { scroll_ref_bucket: i64, render_latest_time: u64, x_phase_bucket: f32, frozen_base_price: Option<Price> },
}
```

follow/pause 状態機械は `Anchor` enum が表現する。`Live → Paused` は `update_auto_follow()` が x=0 不可視を検知した時点で発生する。`Paused → Live` は `resume_to_live()` または `tick_live_and_auto_follow()` の x=0 可視検知で発生する。

### 3. RebuildPolicy の 3 状態（view.rs）

```rust
pub enum RebuildPolicy { Immediate { force_rebuild_from_historical: bool }, Debounced { last_input: Instant, .. }, Idle }
```

`Instant::now()` に依存しており Elm メッセージで表現しにくい。`decide()` がフレームごとに debounce 判定を行う。Phase 2 で削除する場合の代替は下記を参照。

### 4. Camera の 3 フィールド（scene/camera.rs）

```rust
pub struct Camera {
    scale: f32,           // pixels per world unit
    pub offset: [f32; 2], // [x, y] world offset
    pub right_pad_frac: f32,
}
```

`scene.camera` は `Scene` struct の中に存在する。`cell: Cell`（列幅・行高）と連動しており、camera だけを抽出しても整合性が取れない。

### 5. VenueReady sticky cache（main.rs）

`VENUE_READY_CACHE` は `static OnceLock<Arc<Mutex<FxHashSet<String>>>>` として定義されており（`src/main.rs:88-90`）、`HeatmapShader` とは完全に独立した機構である。

---

## B3 実装方針

### 削除するフィールド

#### `canvas_invalidation: CanvasInvalidation`

`CanvasInvalidation` は 4 bool フラグ（`x_axis`, `y_axis`, `overlay_tooltip`, `overlay_scale_labels`）を持ち、`invalidate()` の末尾で `apply()` が `CanvasCaches` の各 `Cache::clear()` を呼ぶ仲介役として機能している。

**削除後の代替実装**:
- 各 `mark_*()` 呼び出し箇所で `canvas_caches.x_axis.clear()` / `canvas_caches.y_axis.clear()` 等を直接呼ぶ
- `invalidate()` から `canvas_invalidation.apply()` 呼び出しを削除する
- `CanvasInvalidation` struct 自体を削除する（`src/widget/chart/heatmap/ui.rs`）

#### `rebuild_policy: view::RebuildPolicy`

`RebuildPolicy` は `Instant::now()` に依存した debounce タイマーを内包している。Elm メッセージで代替するのが困難な最大の要因である。

**削除後の代替実装（選択肢）**:

**Option A（推奨）**: `HeatmapShader` 内に `last_interaction: Option<Instant>` フィールドを残し、`rebuild_all()` / `rebuild_instances()` の呼び出しタイミングを `invalidate()` で一元管理する。`RebuildPolicy` enum の責務を `invalidate()` のロジックに平坦化する。HeatmapShader がタイミング判定を持つことは許容する（GPU rendering サイドエフェクトとして）。

**Option B**: `RebuildPolicy` を `force_rebuild_from_historical: bool` と `debounce_deadline: Option<Instant>` の 2 フィールドに分解して保持する。`decide()` メソッドは `invalidate()` に直接インライン化する。

どちらの場合も Phase 2 完了条件の「`HeatmapShader` のフィールド数直接 26 → 半分以下」要件を満たすために `RebuildPolicy` enum を削除することが必要。

### ViewState への camera 統合

`gui-triple-state-refactor.md §2-A` で提案された `ViewState` 直接拡張ではなく、**`HeatmapViewState` 分離型を採用する**。

理由:
- `scene.camera` は `Cell`（列幅・行高）と連動しており `camera_offset` / `camera_scale` だけを抽出できない
- `HeatmapViewState { camera_offset, camera_scale, cell_width_world, cell_height_world, anchor }` として独立させる方が型安全
- `ViewState` に Heatmap 専用フィールドが混入することを防ぐ

**Phase 2 後の状態管理責任分担**:
```
HeatmapShader（描画専用）
  ├── scene: Scene           （GPU 描画状態 — camera を含む）
  ├── anchor: Anchor         （follow/pause 状態機械 — HeatmapShader が保持）
  ├── canvas_caches: CanvasCaches  （直接 clear() を呼ぶ形に）
  └── ← canvas_invalidation を削除
  └── ← rebuild_policy を削除（平坦化）

Elm ViewState（HeatmapViewState を別途定義）
  ├── camera_offset: [f32; 2]  （ViewState から読んだ値を Scene に反映）
  ├── camera_scale: f32
  ├── cell_width_world: f32
  └── cell_height_world: f32
```

> 注意: `anchor`（follow/pause 状態）は Phase 2 後も `HeatmapShader` が保持する。理由は owner table の「follow モード」行に記載の通り、x=0 可視判定が GPU カメラに依存しており Elm メッセージサイクルより先に解決する必要があるため。これは gui-triple-state-refactor.md §2 の「Elm が所有する」原則の例外として明示的に認める。

### フィールド数削減の見通し

**現在の直接フィールド（HeatmapShader struct、heatmap.rs:79-108）**: 22 個

削除対象:
- `canvas_invalidation: CanvasInvalidation` → 削除（直接 clear() に置き換え）
- `rebuild_policy: view::RebuildPolicy` → 削除（`last_interaction: Option<Instant>` 1 フィールドに縮小）

追加:
- `last_interaction: Option<Instant>` → +1（Option A の場合）

削除後の直接フィールド予測: 22 - 2 + 1 = **21**（現在比 -1 直接フィールド）

> 展開フィールド削減については: `CanvasInvalidation` の展開（4 bool）と `RebuildPolicy` の展開（Instant + 2 bool = 実質 ~3）が消えるため展開フィールド数は 150+ → 140+ 程度の削減に留まる。`gui-triple-state-refactor.md §5` の「75 以下」目標を達成するには `scene.camera` を ViewState に委譲する 2-A の作業まで完了が必要。

### camera_scale() 公開メソッドの追加

Phase 2 完了条件として `HeatmapShader::camera_scale() -> f32` を追加する。

```rust
pub fn camera_scale(&self) -> f32 {
    self.scene.camera.scale()
}
```

受け入れテスト:
```rust
assert_eq!(view_state.scaling, heatmap_shader.camera_scale());
```

---

## Phase 2 着手前チェックリスト

- [x] この `heatmap-phase2-ownership.md` が存在し owner table 8 行すべてに値が記入されている（本ファイルで充足）
- [x] `HeatmapViewState` の struct 定義（フィールド一覧）が確定している（`src/widget/chart/heatmap.rs` に `HeatmapViewState { camera_scale, camera_offset, cell_width_world, cell_height_world }` として実装済み）
- [ ] `RebuildPolicy` 削除後の debounce ロジック代替（Option A / B）が選択されている
- [ ] `CanvasInvalidation` 削除後の各 mark_*() 箇所一覧が抽出されている
- [x] `view_state()` / `apply_view_state()` 実装済み（`src/widget/chart/heatmap.rs`）
- [x] Elm 統合（pane.rs）実装済み — `TicksizeSelected` / `BasisSelected` ハンドラで `apply_view_state()` を呼び出す（2026-05-08 H-3 反映）
- [ ] Phase 2 acceptance テスト一覧（owner table 検証テスト 5 件）のスケルトンが作成されている
