# Floating Windows 移行: アーキテクチャ

> Status: この文書は旧 `iced` カスタムウィジェット案の設計メモです。
> 現在の本線は `Bevy` による dashboard UI 再構成であり、この文書は比較用の参考資料として扱います。

## 1. 現状アーキテクチャ

```
Dashboard (src/screen/dashboard.rs)
  ├─ panes:  pane_grid::State<pane::State>   ← iced 組み込みスプリット
  └─ popout: HashMap<window::Id, (pane_grid::State<pane::State>, WindowSpec)>
```

`pane_grid::Pane`（スロットキー）がペイン識別子として広く使われており、
`src/main.rs` / `src/modal/pane/` / `src/widget.rs` にまで散在している。

---

## 2. 目標アーキテクチャ

```
Dashboard (src/screen/dashboard.rs)
  ├─ windows:   Vec<FloatingPane>                         ← 平坦リスト（挿入順・順序固定）
  ├─ camera:    Camera                                    ← ズーム・パン状態
  ├─ focus:     Option<(window::Id, uuid::Uuid)>
  └─ popout:    HashMap<window::Id, (Vec<FloatingPane>, WindowSpec, Camera)>
```

ペイン識別子は `pane_grid::Pane` → `uuid::Uuid` に統一。
z 順は `FloatingPane.z_index: u32` フィールドで管理する。クリック時に対象パネルの `z_index` を
`windows` 内の最大値 + 1 に更新する。Vec の要素順序は変更しない（後述）。

`max(z_index) + 1` が `u32::MAX - windows.len()` を超えた場合は、全パネルの `z_index` を
0..N の連番に再正規化してからフォーカスパネルを N-1 に設定する（u32 オーバーフロー対策）。

---

## 3. レイヤ構成

```
+-----------------------------------------------------+
|  iced メインウィンドウ                                |
|  +-----------------------------------------------+  |
|  |  Dashboard::view()                             |  |
|  |  +- FloatingPanes ウィジェット (src/widget/)   |  |
|  |       +- Camera 変換（ワールド <-> スクリーン） |  |
|  |       +- FloatingPane[0] (タイトルバー+コンテンツ) |
|  |       +- FloatingPane[1]                       |  |
|  |       +- FloatingPane[N] <- 最前面             |  |
|  +-----------------------------------------------+  |
|  popout: HashMap<window::Id,                         |
|          (Vec<FloatingPane>, WindowSpec, Camera)>    |
+-----------------------------------------------------+
```

---

## 4. `Camera` 座標系

### 座標変換

```rust
// data/src/layout/mod.rs
pub struct Camera {
    pub pan:  (f32, f32),  // ワールド原点のスクリーン上の位置（px）
    pub zoom: f32,          // 0.25〜4.0、デフォルト 1.0
}
```

- **ワールド座標**: パネルの位置・サイズを格納する論理座標系（`FloatRect`、`FloatingPaneData`）
- **スクリーン座標**: iced が描画する物理ピクセル座標系

```
screen = world * zoom + pan
world  = (screen - pan) / zoom
```

### ズーム（カーソル位置中心）

```rust
fn zoom_at(camera: &Camera, cursor: Point, delta: f32) -> Camera {
    let new_zoom = (camera.zoom + delta).clamp(ZOOM_MIN, ZOOM_MAX);
    let factor   = new_zoom / camera.zoom;
    Camera {
        zoom: new_zoom,
        pan: (
            cursor.x - factor * (cursor.x - camera.pan.0),
            cursor.y - factor * (cursor.y - camera.pan.1),
        ),
    }
}
```

カーソル下のワールド座標が不変になるよう `pan` を補正する。

---

## 5. `FloatingPanes` カスタムウィジェット（`src/widget/floating_panes.rs`）

### 責務

| 責務 | 担当 |
|------|------|
| ドラッグ・リサイズ・パン・ズームの内部状態管理 | `FloatingPanes`（`Widget::state()`） |
| タイトルバーの描画（28px） | `FloatingPanes`（`Widget::draw()`） |
| z 順の管理とフォーカス変更 | `FloatingPanes` → `on_focus` コールバック |
| コンテンツ領域の描画 | 各 `pane::State::view()` の `Element` |
| メッセージ発行 | コールバック（`on_move` / `on_resize` / `on_focus` / `on_close` / `on_camera`） |

### `Widget::diff()` の実装方針

`diff()` は `tree.diff_children(&self.panels)` を使う（`multi_split.rs` と同じパターン。
ただしルーラー要素は含まない）。

```rust
fn diff(&self, tree: &mut Tree) {
    tree.diff_children(&self.panels);
}
```

`diff_children()` はインデックス順に State を照合するため、`panels` の **Vec 順序が安定している**
ことが前提。z_index でフォーカス順を管理し Vec の要素を並び替えないことでこの前提を満たす。

もし Vec を並び替えると `diff_children()` が誤った State を別パネルに割り当てクラッシュする。
この制約を守るため、フォーカス変更では要素移動ではなく `z_index` のみを更新する。

`draw()` / `layout()` では `z_index` 昇順でソートしたインデックス列を用いて描画・レイアウトを行う。

---

### 内部状態（`Widget::state()` で保持）

```rust
struct InternalState {
    drag:   Option<DragState>,   // タイトルバードラッグ中
    resize: Option<ResizeState>, // エッジリサイズ中（8方向）
    pan:    Option<PanState>,    // 空白ドラッグパン中
}
```

ドラッグ中の中間座標はここで管理し、`MouseButtonReleased` 時のみ
`on_move` / `on_resize` を発行してアプリ State 更新コストを抑える。

### レイアウト（`Widget::layout`）

```
全体ノード = limits.max() 相当（スクリーン全体）
各子ノード = FloatRect をカメラ変換 → Node::move_to() で絶対配置
            zoom に応じて width / height もスケール
```

iced の `Node::with_children` + `Node::move_to` を使用。
参考実装: `src/widget/multi_split.rs`（絶対座標ウィジェット）

### イベント処理（`Widget::on_event`）

| イベント | 条件 | 動作 |
|---------|------|------|
| `MouseButtonPressed(Left)` | タイトルバー上 | `drag` 開始、`on_focus` 発行 |
| `MouseButtonPressed(Left)` | リサイズハンドル上 | `resize` 開始、`on_focus` 発行 |
| `MouseButtonPressed(Left/Middle)` | どのパネルにも当たらない | `pan` 開始 |
| `CursorMoved` | `drag` 中 | delta をワールド座標に逆変換して加算（`on_move` は発行しない） |
| `CursorMoved` | `resize` 中 | edge 方向に応じて `FloatRect` 更新 |
| `CursorMoved` | `pan` 中 | `camera.pan` 更新、`on_camera` 発行 |
| `WheelScrolled` | 任意 | `zoom_at(cursor, delta * ZOOM_STEP)`、`on_camera` 発行 |
| `MouseButtonReleased` | `drag` 中 | 最終位置で `on_move` 発行、`drag` 解除 |
| `MouseButtonReleased` | `resize` 中 | 最終 rect で `on_resize` 発行、`resize` 解除 |
| `Window(Unfocused)` | `drag`/`resize`/`pan` 中 | 各状態を即時リセット（操作中断） |
| `CursorLeft` | `drag`/`resize`/`pan` 中 | 各状態を即時リセット（操作中断） |
| その他 | コンテンツ上 | z 順の最前面から順に子 `on_event` に委譲 |

### `Widget::overlay()` の実装方針

子ウィジェット（各パネルのコンテンツ要素）が overlay（モーダル・ドロップダウン等）を
持つ場合に備え、`overlay()` を実装する。

```rust
fn overlay<'b>(
    &'b mut self,
    tree: &'b mut Tree,
    layout: Layout<'_>,
    renderer: &Renderer,
    translation: Vector,
) -> Option<overlay::Element<'b, Message, Theme, Renderer>> {
    overlay::from_children(&mut self.panels, tree, layout, renderer, translation)
}
```

`overlay::from_children` は iced 標準ヘルパーで、全パネルの overlay を集約する
（z 順は `overlay::from_children` の内部実装に依存）。

### 定数

```rust
pub const TITLE_BAR_H:   f32 = 28.0;
pub const RESIZE_BORDER: f32 = 6.0;   // エッジ検出幅
pub const MIN_WIN_W:     f32 = 240.0;
pub const MIN_WIN_H:     f32 = 150.0;
pub const ZOOM_MIN:      f32 = 0.25;
pub const ZOOM_MAX:      f32 = 4.0;
pub const ZOOM_STEP:     f32 = 0.1;
```

---

## 6. データモデル変更

### データクレート（`data/src/layout/`）

#### 追加型

```rust
// data/src/layout/mod.rs
pub struct FloatRect { pub x: f32, pub y: f32, pub width: f32, pub height: f32 }
pub struct Camera    { pub pan: (f32, f32), pub zoom: f32 }

// data/src/layout/pane.rs
pub struct FloatingPaneData { pub rect: FloatRect, pub pane: Pane }
```

#### 削除型

```rust
Pane::Split { axis: Axis, ratio: f32, a: Box<Pane>, b: Box<Pane> }
pub enum Axis { Horizontal, Vertical }
```

#### `data::Dashboard` の変更

```rust
// 変更後
pub struct Dashboard {
    pub windows: Vec<FloatingPaneData>,
    pub popout:  Vec<(Vec<FloatingPaneData>, WindowSpec, Camera)>,
    pub camera:  Camera,
}
```

---

## 7. GUI 状態モデル変更

```rust
// src/screen/dashboard.rs — 新規型
pub struct FloatingPane {
    pub id:      uuid::Uuid,
    pub rect:    FloatRect,
    pub pane:    pane::State,
    pub z_index: u32,   // クリックで max(z_index)+1 に更新。Vec の順序は変えない
}

// Dashboard 構造体の変更後
pub struct Dashboard {
    pub windows:  Vec<FloatingPane>,
    pub camera:   Camera,
    pub focus:    Option<(window::Id, uuid::Uuid)>,
    pub popout:   HashMap<window::Id, (Vec<FloatingPane>, WindowSpec, Camera)>,
    pub streams:  UniqueStreams,
    layout_id:    uuid::Uuid,
}
```

---

## 8. 新規ウィンドウ spawn ロジック

### `spawn_floating_pane` ヘルパー

```rust
fn spawn_floating_pane(
    &mut self,
    state: Option<pane::State>,
    rect:  Option<FloatRect>,
) -> FloatingPane
```

### `default_spawn_rect()` のルール

1. フォーカス中ウィンドウあり → そのウィンドウから `(+30, +30)` オフセット・同サイズ
2. フォーカスなし → カメラのビューポート中央に `640×400` で配置
3. `MIN_WIN_W` / `MIN_WIN_H` を下回らないようクランプ

### spawn 発生経路

| 発生箇所 | 変更後 |
|---------|--------|
| `pane::Message::SplitPane` ハンドラ | `WindowAdded { state: None, rect: None }` |
| サイドバー "Split" ボタン（`main.rs`） | `WindowAdded` を発行、ボタンラベルを "Add Window" に変更 |
| `OpenOrderPanel` ハンドラ（`main.rs:2298`） | `panes.split()` 直接呼び出しを廃止、`WindowAdded` を `Task::done` で発行 |
| `merge_pane()`（`dashboard.rs`） | `spawn_floating_pane(Some(pane_state), None)` に置換 |

---

## 9. 永続化変換（`src/layout.rs`）

### 削除

```rust
pub fn configuration(pane: data::Pane) -> Configuration<pane::State>
```

### 追加

```rust
pub fn floating_pane_from_data(data: data::FloatingPaneData) -> FloatingPane
pub fn floating_pane_to_data(pane: &FloatingPane) -> data::FloatingPaneData
```

### `From<&Dashboard> for data::Dashboard` 変換

`src/layout.rs` の変換実装では `camera` フィールドを必ず引き継ぐこと。

```rust
impl From<&Dashboard> for data::Dashboard {
    fn from(dashboard: &Dashboard) -> Self {
        Self {
            windows: dashboard.windows.iter().map(floating_pane_to_data).collect(),
            popout:  /* ... */,
            camera:  dashboard.camera,  // ← 必須。省略すると Camera がデフォルトにリセットされる
        }
    }
}
```

### `saved-state.json` 互換

破壊的変更（`pane` ツリー → `windows` フラットリスト）。
旧フォーマットは `ok_or_default` で空リストにフォールバックする。
`camera` は旧フォーマットに存在しないため `#[serde(default)]` でデフォルト値にフォールバックする。

```rust
#[serde(deserialize_with = "ok_or_default", default)]
pub windows: Vec<FloatingPaneData>,

#[serde(default)]
pub camera: Camera,

#[serde(default)]
pub popout: Vec<(Vec<FloatingPaneData>, WindowSpec, Camera)>,
```

---

## 10. `pane::State::view()` の戻り値型変更

```rust
// 変更前
pub fn view<'a>(..., id: pane_grid::Pane, ...)
    -> pane_grid::Content<'a, Message, Theme, Renderer>

// 変更後
pub fn view<'a>(..., id: uuid::Uuid, ...)
    -> Element<'a, Message>
```

`pane_grid::Content` はタイトルバーを pane_grid 側が描画する構造だった。
フローティング化後はタイトルバーを `FloatingPanes` が担うため、
`pane::State::view()` はコンテンツ領域のみを `Element` で返す。
`maximized` パラメータも不要になる。

---

## 11. `tick()` 最適化の変更

```rust
// 変更前（削除）
let maximized_pane = self.panes.maximized();
for (pane_id, state) in self.panes.iter_mut() {
    if maximized_pane.is_some_and(|m| *pane_id != m) { continue; }
    tick_state(state);
}

// 変更後
for fp in &mut self.windows {
    let is_focused = self.focus.map(|(_, id)| id) == Some(fp.id);
    if is_focused || should_tick_background(frame_count) {
        tick_state(&mut fp.pane);
    }
}
// should_tick_background: 4 フレームに 1 回 true を返す（Phase 4 でチューニング）
```

---

## 12. リスクと対策

| リスク | 対策 |
|--------|------|
| iced `Widget::layout` で絶対座標が想定通り動かない | `src/widget/multi_split.rs` を参考にする |
| ウィジェットツリーの子数とウィンドウ数がズレてパニック | `diff_children()` を使い Vec 順序を固定する。z 順は `z_index` フィールドで管理し Vec の並び替えは行わない |
| `pane::State::view()` 戻り値変更でモーダル呼び出しが全滅 | Phase 2 で型変更とモーダル関数修正を同一 Phase で必ず同時実施 |
| `main.rs` の `OpenOrderPanel` ハンドラの `panes.split()` 残留 | Phase 4 の修正チェックリストに明示的に含める |
| saved-state.json 旧フォーマットでクラッシュ | `ok_or_default` を各フィールドに適用（既存パターン踏襲） |
| 非フォーカスパネルの tick 停止によるデータ欠損 | tick は「描画スキップ」であり IPC 受信は止めない。描画対象外フレームの蓄積で十分 |
