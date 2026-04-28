# Floating Windows 移行計画

## 概要

`iced::widget::PaneGrid`（スプリット分割）を廃止し、iced の単一ウィンドウ内で
任意の位置・サイズに配置できるフローティングパネルシステムに置き換える。

OS レベルのウィンドウ（現 popout 機構）は **継続して使用**。
in-canvas フローティングはメインウィンドウ内の表現方式の変更。

キャンバス全体のズームイン・ズームアウトおよびパン移動もサポートする。
Figma / Blender のような操作感（スクロールホイール＝ズーム、空白ドラッグ＝パン）。

---

## アーキテクチャ方針

### 現状

```
Dashboard
  └─ panes: pane_grid::State<pane::State>   ← iced 組み込みスプリット
  └─ popout: HashMap<window::Id, (pane_grid::State<pane::State>, WindowSpec)>
```

### 目標

```
Dashboard
  └─ windows: Vec<FloatingPane>             ← 新規カスタム型（平坦リスト）
  └─ popout: HashMap<window::Id, (Vec<FloatingPane>, WindowSpec)>
```

各 `FloatingPane` はキャンバス座標系での位置・サイズ（`FloatRect`）を持ち、
ズオーダーは `Vec` のインデックスで管理する（末尾が最前面）。

---

## 新規カスタムウィジェット設計

### `src/widget/floating_panes.rs`（新規）

iced の `Widget` トレイトを実装するコンテナ。
`PaneGrid` の代替として `Dashboard::view()` から呼び出す。

```
FloatingPanes<'a, Message>
  ├─ rects: &'a [FloatRect]               各ウィンドウのワールド座標
  ├─ z_order: &'a [uuid::Uuid]            前→後の z 順（末尾が最前面）
  ├─ focused: Option<uuid::Uuid>
  ├─ camera: &'a Camera                   ズーム・パン状態
  ├─ contents: Vec<Element<'a, M>>        各ウィンドウのコンテンツ要素
  ├─ on_move: Fn(Uuid, Point) -> M        ドラッグ完了時（ワールド座標）
  ├─ on_resize: Fn(Uuid, FloatRect) -> M  （ワールド座標）
  ├─ on_focus: Fn(Uuid) -> M
  ├─ on_close: Fn(Uuid) -> M
  └─ on_camera: Fn(Camera) -> M          ズーム・パン変更時
```

#### 内部ウィジェットツリー状態（`InternalState`）

ドラッグ中・リサイズ中・パン中の一時状態はアプリ State ではなくウィジェットツリーに保持する。
（iced の `Widget::state()` / `Widget::tag()` で登録）

```rust
#[derive(Default)]
struct InternalState {
    drag:   Option<DragState>,
    resize: Option<ResizeState>,
    pan:    Option<PanState>,    // 空白ドラッグによるパン
}

struct DragState {
    id: uuid::Uuid,
    cursor_start: iced::Point,
    pos_start:    iced::Point,   // ウィンドウ左上（ワールド座標）
}

struct ResizeState {
    id:            uuid::Uuid,
    edge:          ResizeEdge,   // 8方向
    cursor_start:  iced::Point,
    rect_start:    FloatRect,    // ワールド座標
}

struct PanState {
    cursor_start: iced::Point,
    pan_start:    iced::Point,   // Camera.pan の初期値
}
```

#### カメラ変換（ワールド座標 → スクリーン座標）

```rust
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Camera {
    pub pan:  iced::Point,  // ワールド原点のスクリーン上の位置（px）
    pub zoom: f32,          // 倍率: 0.25〜4.0、デフォルト 1.0
}

impl Camera {
    pub fn world_to_screen(&self, world: iced::Point) -> iced::Point {
        iced::Point {
            x: world.x * self.zoom + self.pan.x,
            y: world.y * self.zoom + self.pan.y,
        }
    }

    pub fn screen_to_world(&self, screen: iced::Point) -> iced::Point {
        iced::Point {
            x: (screen.x - self.pan.x) / self.zoom,
            y: (screen.y - self.pan.y) / self.zoom,
        }
    }

    pub fn scale_scalar(&self, v: f32) -> f32 {
        v * self.zoom
    }
}

pub const ZOOM_MIN: f32 = 0.25;
pub const ZOOM_MAX: f32 = 4.0;
pub const ZOOM_STEP: f32 = 0.1;  // ホイール 1 ノッチ分
```

ズームはカーソル位置を中心に行う（カーソル下のワールド座標が不変になるよう pan を補正）:

```rust
fn zoom_at(camera: &Camera, cursor: iced::Point, delta: f32) -> Camera {
    let new_zoom = (camera.zoom + delta).clamp(ZOOM_MIN, ZOOM_MAX);
    let factor   = new_zoom / camera.zoom;
    Camera {
        zoom: new_zoom,
        pan:  iced::Point {
            x: cursor.x - factor * (cursor.x - camera.pan.x),
            y: cursor.y - factor * (cursor.y - camera.pan.y),
        },
    }
}
```

#### レイアウト（`Widget::layout`）

```
全体の Node = limits.max() 相当のサイズ（スクリーン全体）
各子 Node   = FloatRect をカメラ変換してスクリーン座標に変換し move_to
              width/height も zoom でスケール
```

iced の `Node::with_children` + `Node::move_to` を使用。
絶対座標なので Flexbox の影響を受けない。

#### イベント処理（`Widget::on_event`）

| イベント | 条件 | 動作 |
|---------|------|------|
| `MouseButtonPressed(Left)` | タイトルバー領域 | `drag` 開始、`on_focus` 発行 |
| `MouseButtonPressed(Left)` | リサイズハンドル領域 | `resize` 開始、`on_focus` 発行 |
| `MouseButtonPressed(Middle)` または `Left` on 空白 | どのパネルにも当たらない | `pan` 開始 |
| `CursorMoved` | `drag` 中 | delta をワールド座標に逆変換して加算、`on_move` 発行 |
| `CursorMoved` | `resize` 中 | edge 方向に応じて rect 更新、`on_resize` 発行 |
| `CursorMoved` | `pan` 中 | `camera.pan` を更新、`on_camera` 発行 |
| `WheelScrolled` | 任意 | `zoom_at(cursor, delta * ZOOM_STEP)` 、`on_camera` 発行 |
| `MouseButtonReleased(Left/Middle)` | 任意 | `drag` / `resize` / `pan` 解除 |
| その他 | コンテンツ上 | z 順の最前面ウィンドウから順に子 `on_event` に委譲 |

#### 定数

```rust
pub const TITLE_BAR_H:    f32 = 28.0;
pub const RESIZE_BORDER:  f32 = 6.0;   // エッジ検出幅
pub const MIN_WIN_W:      f32 = 240.0;
pub const MIN_WIN_H:      f32 = 150.0;

pub const ZOOM_MIN:       f32 = 0.25;
pub const ZOOM_MAX:       f32 = 4.0;
pub const ZOOM_STEP:      f32 = 0.1;   // ホイール 1 ノッチ分
```

---

## データモデル変更

### `FloatRect` と `Camera`（新規、`data` クレートへ追加）

```rust
// data/src/layout/mod.rs
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct FloatRect {
    pub x:      f32,
    pub y:      f32,
    pub width:  f32,
    pub height: f32,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Camera {
    pub pan:  (f32, f32),  // (pan_x, pan_y) — iced::Point は非 Serialize
    pub zoom: f32,
}

impl Default for Camera {
    fn default() -> Self {
        Self { pan: (0.0, 0.0), zoom: 1.0 }
    }
}
```

### `data::Pane` の変更（`data/src/layout/pane.rs`）

#### 削除

```rust
// 削除
Pane::Split { axis: Axis, ratio: f32, a: Box<Pane>, b: Box<Pane> }

// 削除（不要になる）
pub enum Axis { Horizontal, Vertical }
```

#### 追加

```rust
// ラッパー: ペイン種別 + キャンバス上の位置・サイズ
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FloatingPaneData {
    pub rect: FloatRect,
    pub pane: Pane,   // 既存の Pane バリアント（Starter/HeatmapChart/…）
}
```

### `data::Dashboard` の変更（`data/src/layout/dashboard.rs`）

```rust
// 変更前
pub struct Dashboard {
    pub pane:   Pane,
    pub popout: Vec<(Pane, WindowSpec)>,
}

// 変更後
pub struct Dashboard {
    pub windows: Vec<FloatingPaneData>,
    pub popout:  Vec<(Vec<FloatingPaneData>, WindowSpec)>,
    pub camera:  Camera,   // レイアウトごとにカメラ状態を保存
}
```

---

## GUI 状態モデル変更

### 新規型（`src/screen/dashboard.rs` または `src/widget/floating_panes.rs`）

```rust
pub struct FloatingPane {
    pub id:   uuid::Uuid,
    pub rect: FloatRect,
    pub pane: pane::State,   // 既存のペイン状態をそのまま使用
}
```

### `Dashboard` 構造体の変更（`src/screen/dashboard.rs`）

```rust
// 変更前
pub struct Dashboard {
    pub panes:  pane_grid::State<pane::State>,
    pub focus:  Option<(window::Id, pane_grid::Pane)>,
    pub popout: HashMap<window::Id, (pane_grid::State<pane::State>, WindowSpec)>,
    pub streams: UniqueStreams,
    layout_id: uuid::Uuid,
}

// 変更後
pub struct Dashboard {
    pub windows:  Vec<FloatingPane>,
    pub camera:   Camera,                            // メインキャンバスのカメラ
    pub focus:    Option<(window::Id, uuid::Uuid)>,  // window + floating_pane_id
    pub popout:   HashMap<window::Id, (Vec<FloatingPane>, WindowSpec)>,
    pub streams:  UniqueStreams,
    layout_id:    uuid::Uuid,
}
```

---

## メッセージ変更（`src/screen/dashboard/pane.rs`）

### 削除するメッセージ

```rust
// 削除
pane::Message::PaneClicked(pane_grid::Pane)
pane::Message::PaneResized(pane_grid::ResizeEvent)
pane::Message::PaneDragged(pane_grid::DragEvent)
pane::Message::SplitPane(pane_grid::Axis, pane_grid::Pane)
pane::Message::MaximizePane(pane_grid::Pane)
pane::Message::Restore
```

### 追加するメッセージ

```rust
// 追加
pane::Message::WindowFocused(uuid::Uuid)
pane::Message::WindowMoved(uuid::Uuid, iced::Point)          // 新しい左上座標（ワールド）
pane::Message::WindowResized(uuid::Uuid, FloatRect)          // ワールド座標
pane::Message::WindowClosed(uuid::Uuid)
pane::Message::WindowAdded {
    state: Option<pane::State>,   // None = 空 Starter、Some = Merge 復帰やパネル種別指定
    rect:  Option<FloatRect>,     // None = デフォルト spawn 位置
}
pane::Message::CameraChanged(Camera)                         // ズーム・パン変更
```

### 変更なし（継続使用）―ただし引数型が変わる

以下のメッセージは **機能は継続するが、`pane_grid::Pane` 引数を `uuid::Uuid` に変更する**。
`ClosePane` も `WindowClosed` に改名される。

| バリアント | 変更前 | 変更後 |
|-----------|--------|--------|
| `ClosePane(pane_grid::Pane)` | → | `WindowClosed(uuid::Uuid)` |
| `ReplacePane(pane_grid::Pane)` | → | `ReplacePane(uuid::Uuid)` |
| `SwitchLinkGroup(pane_grid::Pane, Option<LinkGroup>)` | → | `SwitchLinkGroup(uuid::Uuid, Option<LinkGroup>)` |
| `VisualConfigChanged(pane_grid::Pane, VisualConfig, bool)` | → | `VisualConfigChanged(uuid::Uuid, VisualConfig, bool)` |
| `PaneEvent(pane_grid::Pane, Event)` | → | `PaneEvent(uuid::Uuid, Event)` |
| `Popout` | 変更なし | 変更なし |
| `Merge` | 変更なし | 変更なし |

---

## スロット型（`pane_grid::Pane`）除去の影響詳細

`pane_grid::Pane` はペインのスロットキーとして広範に使われており、
`uuid::Uuid` への置き換えは以下の範囲に及ぶ。**計画の中で最も影響範囲が広い変更。**

### `pane::State::view()` の戻り値型変更（`src/screen/dashboard/pane.rs:539`）

```rust
// 変更前
pub fn view<'a>(
    &'a self,
    id: pane_grid::Pane,          // スロットキー
    panes: usize,
    is_focused: bool,
    maximized: bool,
    window: window::Id,
    main_window: &'a Window,
    timezone: UserTimezone,
    tickers_table: &'a TickersTable,
) -> pane_grid::Content<'a, Message, Theme, Renderer>  // pane_grid 専用型

// 変更後
pub fn view<'a>(
    &'a self,
    id: uuid::Uuid,               // FloatingPane の id
    is_focused: bool,
    window: window::Id,
    main_window: &'a Window,
    timezone: UserTimezone,
    tickers_table: &'a TickersTable,
) -> Element<'a, Message>         // 通常の Element に変わる
```

`pane_grid::Content` はタイトルバー・コントロールを pane_grid が担う構造だった。
フローティング化後はタイトルバーを `FloatingPanes` カスタムウィジェット側が描画するため
`pane::State::view()` はコンテンツ領域のみを `Element` で返せばよい。
`maximized` パラメータも不要になる（フローティングに最大化概念がないため）。

### モーダルビュー関数群（`src/modal/pane/settings.rs`・`src/modal/pane/indicators.rs`）

設定モーダルとインジケーターモーダルの全関数が `pane: pane_grid::Pane` を引数に取り、
メッセージ生成時に `VisualConfigChanged(pane, cfg, sync)` へ渡している。
`pane_grid::Pane` → `uuid::Uuid` への変更が全関数に波及する。

| 関数 | ファイル:行 |
|------|-----------|
| `heatmap_cfg_view()` | settings.rs:44 |
| `heatmap_shader_cfg_view()` | settings.rs:275 |
| `timesales_cfg_view()` | settings.rs:391 |
| `comparison_cfg_view()` | settings.rs:559 |
| `kline_cfg_view()` | settings.rs:579 |
| `ladder_cfg_view()` | settings.rs:650 |
| `sync_all_button()` | settings.rs:736 |
| `indicators::view()` 他3関数 | indicators.rs:12,35,64,94 |

**対応方針:** 引数型を `pane: pane_grid::Pane` → `pane_id: uuid::Uuid` に一括置換。
生成するメッセージの引数も同様に `uuid::Uuid` に変える。Phase 2 で実施。

### `link_group_button` ウィジェット（`src/widget.rs:238-244`）

```rust
// 変更前（widget.rs:238）
pub struct LinkGroupButton {
    id: iced::widget::pane_grid::Pane,   // ← pane_grid::Pane フィールド
    // ...
}
```

LinkGroup 機能は継続するが、ペイン識別子を `uuid::Uuid` に変える必要がある。
Phase 2 で `pane_grid::Pane` → `uuid::Uuid` に変更。

### `src/main.rs` での `focus` フィールド直接操作（5箇所）

`dashboard.focus` は `dashboard.rs` 外の `main.rs` でも直接参照・更新される。
型が `Option<(window::Id, pane_grid::Pane)>` → `Option<(window::Id, uuid::Uuid)>` に変わると
main.rs 側のコードも全て変更が必要。

| 行 | 操作 |
|----|------|
| main.rs:1514–1515 | `focus.is_some()` 確認 → `focus = None` へクリア |
| main.rs:1932 | `let Some((window_id, focused_pane)) = dashboard.focus` → `PaneEvent(focused_pane, ...)` 生成 |
| main.rs:2303–2312 | `focused_pane` 取得 → `focus = Some((window_id, new_pane))` 更新 |
| main.rs:2874 | `focus` 参照 |

特に **main.rs:1932 の `ConfirmOrderEntrySubmit` ハンドラ**は
`PaneEvent(focused_pane, ...)` へ `uuid::Uuid` を渡すよう変更が必要。Phase 4 で実施。

### `src/style.rs` の `pane_grid` スタイル関数

```rust
// 削除対象（style.rs:418-439）
use iced::widget::pane_grid::{Highlight, Line};

pub fn pane_grid(theme: &Theme) -> widget::pane_grid::Style { ... }
```

`PaneGrid::style(style::pane_grid)` を呼んでいるコールサイト（`dashboard.rs:759`）も
`PaneGrid` ウィジェット自体の削除に伴い消える。
`Highlight` / `Line` インポートも同時に削除する。Phase 4 で実施。

### `tick()` 内の `maximized()` 最適化（`src/screen/dashboard.rs:1306-1313`）

```rust
// 削除対象
let maximized_pane = self.panes.maximized();
for (pane_id, state) in self.panes.iter_mut() {
    if maximized_pane.is_some_and(|maximized| *pane_id != maximized) {
        continue;  // 最大化中は他のペインを tick しない（パフォーマンス最適化）
    }
    tick_state(state);
}
```

`pane_grid::State::maximized()` はフローティング化で使えなくなる。
代替最適化として **フォーカス中の FloatingPane を優先 tick し、
非フォーカスパネルは N フレームに 1 回だけ tick する**方針を採用する。

```rust
// 変更後の tick() 方針
for fp in &mut self.windows {
    let is_focused = self.focus.map(|(_, id)| id) == Some(fp.id);
    if is_focused || should_tick_background(frame_count) {
        tick_state(&mut fp.pane);
    }
}
```

`should_tick_background(frame_count)` は例えば 4 フレームに 1 回 true を返す関数。
Phase 4 で実装・チューニングする。

---

## 永続化変換（`src/layout.rs`）

### 削除する関数

```rust
// 削除
pub fn configuration(pane: data::Pane) -> Configuration<pane::State>
```

### 追加する関数

```rust
// 追加
pub fn floating_pane_from_data(data: data::FloatingPaneData) -> FloatingPane
pub fn floating_pane_to_data(pane: &FloatingPane) -> data::FloatingPaneData
```

### `From<&Dashboard> for data::Dashboard` の書き直し

```rust
impl From<&Dashboard> for data::Dashboard {
    fn from(dashboard: &Dashboard) -> Self {
        data::Dashboard {
            windows: dashboard.windows.iter()
                .map(floating_pane_to_data)
                .collect(),
            popout: dashboard.popout.iter()
                .map(|(_, (panes, spec))| {
                    (panes.iter().map(floating_pane_to_data).collect(), *spec)
                })
                .collect(),
        }
    }
}
```

---

## `Dashboard` 主要メソッドの変更

### `iter_all_panes` / `iter_all_panes_mut`

```rust
// pane_grid::Pane の代わりに uuid::Uuid を使う
fn iter_all_panes(&self, main_window: window::Id)
    -> impl Iterator<Item = (window::Id, uuid::Uuid, &pane::State)>
```

### `view()` の変更（`src/screen/dashboard.rs:736`）

```rust
// 変更前
PaneGrid::new(&self.panes, |id, pane, maximized| { ... })
    .on_click(...)
    .on_drag(...)
    .on_resize(...)
    .into()

// 変更後
floating_panes::FloatingPanes::new(
    &self.windows,
    |fp| fp.pane.view(fp.id, ...),
)
.on_move(|id, pt| pane::Message::WindowMoved(id, pt))
.on_resize(|id, rect| pane::Message::WindowResized(id, rect))
.on_focus(|id| pane::Message::WindowFocused(id))
.on_close(|id| pane::Message::WindowClosed(id))
.into()
```

---

## 新規ウィンドウ spawn ロジック

### spawn の発生経路（現コードから洗い出し）

| 発生箇所 | 現在の実装 | 移行後 |
|---------|-----------|--------|
| `pane::Message::SplitPane` ハンドラ（`dashboard.rs:223`） | `panes.split(axis, pane, State::new())` | `WindowAdded { state: None, rect: None }` を処理し `spawn_floating_pane` を呼ぶ |
| サイドバー "Split" ボタン（`main.rs:2906`） | `SplitPane(Axis::Horizontal, pane_id)` を送信 | `WindowAdded { state: None, rect: None }` を送信に変更、ボタンラベルも "Add Window" に変更 |
| `OpenOrderPanel` ハンドラ（`main.rs:2298`） | **`dashboard.panes.split()` を直接呼ぶ**（`update()` 迂回） | `Dashboard::spawn_floating_pane(kind, main_window)` メソッドを公開して呼ぶ、または `WindowAdded { state: Some(State::with_kind(kind)), rect: None }` を `Task::done` で発行 |
| `merge_pane()`（`dashboard.rs:538`） | `new_pane(Axis::Horizontal, main_window, Some(pane_state))` → `pane_grid::split()` | `spawn_floating_pane(Some(pane_state), None)` に置き換え |

### `spawn_floating_pane` ヘルパー（`src/screen/dashboard.rs` に追加）

```rust
fn spawn_floating_pane(
    &mut self,
    state: Option<pane::State>,
    rect: Option<FloatRect>,
) -> FloatingPane {
    let rect = rect.unwrap_or_else(|| self.default_spawn_rect());
    FloatingPane {
        id:   uuid::Uuid::new_v4(),
        rect,
        pane: state.unwrap_or_default(),
    }
}
```

### デフォルト spawn 位置の決定ルール（`default_spawn_rect()`）

1. フォーカス中のウィンドウがある → そのウィンドウから `(+30, +30)` オフセット、サイズは同じ
2. フォーカスなし → カメラのビューポート中央に `DEFAULT_WIN_W × DEFAULT_WIN_H` で配置
3. 配置後、MIN_WIN_W / MIN_WIN_H を下回らないようにクランプ

```rust
fn default_spawn_rect(&self) -> FloatRect {
    if let Some((_, focused_id)) = self.focus
        && let Some(fp) = self.windows.iter().find(|w| w.id == focused_id)
    {
        FloatRect {
            x:      fp.rect.x + 30.0,
            y:      fp.rect.y + 30.0,
            width:  fp.rect.width,
            height: fp.rect.height,
        }
    } else {
        // カメラのビューポート中央（ワールド座標）
        // camera.pan と zoom から逆算してビューポート中央のワールド座標を求める
        FloatRect {
            x:      (-self.camera.pan.0 + DEFAULT_VIEWPORT_W / 2.0) / self.camera.zoom
                    - DEFAULT_WIN_W / 2.0,
            y:      (-self.camera.pan.1 + DEFAULT_VIEWPORT_H / 2.0) / self.camera.zoom
                    - DEFAULT_WIN_H / 2.0,
            width:  DEFAULT_WIN_W,
            height: DEFAULT_WIN_H,
        }
    }
}
```

定数:
```rust
const DEFAULT_WIN_W: f32 = 640.0;
const DEFAULT_WIN_H: f32 = 400.0;
const DEFAULT_VIEWPORT_W: f32 = 1280.0;  // フォールバック用
const DEFAULT_VIEWPORT_H: f32 = 800.0;
```

### `main.rs:2298` の修正方針

現在 `OpenOrderPanel` ハンドラは `dashboard.panes.split()` を **直接** 呼んでおり
`Dashboard::update()` を迂回している。これは pane_grid API への直接依存であり今回の移行で必ず修正する。

修正後:
```rust
// main.rs:2298 付近
Some(dashboard::sidebar::Action::OpenOrderPanel(kind)) => {
    let new_state = dashboard::pane::State::with_kind(kind);
    // dashboard.panes.split() を直接呼ぶのをやめる
    // → update() 経由で WindowAdded を発行する
    let task = Task::done(Message::Dashboard {
        layout_id: None,
        event: dashboard::Message::Pane(
            main_window.id,
            dashboard::pane::Message::WindowAdded {
                state: Some(new_state),
                rect:  None,
            },
        ),
    });
    // pane_added フラグは WindowAdded の update() 内で Task として返す
    return task;
}
```

> BuyingPower の自動フェッチキャッチアップ（`main.rs:2325`）は
> `WindowAdded` の処理後に `Dashboard::update()` が返す `Task` に組み込む形に移す。

---

## 既存 popout 機構との関係

popout（OS レベルウィンドウ）は機能を維持するが内部表現を変更する。

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| 内部状態 | `pane_grid::State<pane::State>` | `Vec<FloatingPane>` |
| 表示 | `PaneGrid::new(state, ...)` | `FloatingPanes::new(&state, ...)` |
| 意味 | popout ウィンドウ内でさらにスプリット可能 | popout ウィンドウ内でもフローティング |

> popout ウィンドウ内のパネル数は通常 1 枚なので実質的な変化は少ない。

---

## デフォルトレイアウト

`Dashboard::default()` で生成する初期配置（現 `default_pane_config()` の代替）:

```
メインウィンドウを仮に 1280×800 として:
┌──────────┬──────────┐
│ Starter  │ Starter  │
│ (0,0)    │ (640,0)  │
│ 640×400  │ 640×400  │
├──────────┴──────────┤
│      Starter        │
│ (0,400) 1280×400   │
└─────────────────────┘
```

実際の座標は `Window` の初期サイズから比率計算する。

---

## saved-state.json の互換性

**破壊的変更**。`pane` キー（ツリー構造）→ `windows` キー（フラットリスト）。

旧フォーマットのデータは `ok_or_default` で空のウィンドウリストにフォールバックする。

```rust
// data/src/layout/dashboard.rs のデシリアライズ
#[serde(deserialize_with = "ok_or_default", default)]
pub windows: Vec<FloatingPaneData>,
```

---

## 実装フェーズ

### Phase 1: `FloatRect` と `FloatingPaneData` をデータクレートに追加

**変更ファイル:**
- `data/src/layout/mod.rs` — `FloatRect` 追加・再エクスポート
- `data/src/layout/pane.rs` — `Pane::Split` と `Axis` 削除、`FloatingPaneData` 追加
- `data/src/layout/dashboard.rs` — `Dashboard` 構造体書き換え

**ゴール:** `cargo test -p data` が通る。既存の pane 種別の roundtrip テストが残る。

---

### Phase 2: `FloatingPane` 型と `Dashboard` 状態を移行

**変更ファイル:**
- `src/screen/dashboard.rs` — `Dashboard` 構造体・`iter_all_panes*` 書き換え
- `src/screen/dashboard/pane.rs` — `pane::Message` のメッセージ追加・削除・引数型変更、`view()` 戻り値を `Element` に変更
- `src/layout.rs` — `configuration()` 削除、新規変換関数追加
- `src/modal/pane/settings.rs` — 全ビュー関数の `pane: pane_grid::Pane` → `pane_id: uuid::Uuid` 変更（7関数）
- `src/modal/pane/indicators.rs` — 同上（4関数）
- `src/widget.rs` — `link_group_button` の `pane_grid::Pane` フィールドを `uuid::Uuid` に変更

**ゴール:** `cargo check` が通る（`view()` は一時的にダミーを返してよい）。

---

### Phase 3: `FloatingPanes` カスタムウィジェット実装

**新規ファイル:** `src/widget/floating_panes.rs`

**変更ファイル:** `src/widget.rs` — `pub mod floating_panes;` 追加

実装順序:
1. `Camera` 型の実装（`world_to_screen` / `screen_to_world` / `zoom_at`）
2. `Widget::layout()` — カメラ変換を適用した絶対座標配置
3. `Widget::draw()` — タイトルバー + コンテンツ描画（zoom に応じてフォントサイズ等を調整）
4. `Widget::on_event()` — ドラッグ・リサイズ・パン・ズーム
5. `Widget::mouse_interaction()` — カーソル変更（ドラッグ/リサイズ/パン中で切り替え）

**ゴール:** `cargo build` でウィジェットがビルドできる（コンテンツは空でよい）。

---

### Phase 4: `Dashboard::view()` の切り替えと動作確認

**変更ファイル:**
- `src/screen/dashboard.rs` — `view()` / `view_window()` を `FloatingPanes` に切り替え
- `src/screen/dashboard.rs` — `update()` で新メッセージを処理
- `src/screen/dashboard.rs` — `tick()` の `maximized_pane` 最適化をフォーカスベースに書き直し
- `src/style.rs` — `pub fn pane_grid()` 関数と `Highlight` / `Line` インポートを削除
- `src/main.rs` — `dashboard.focus` の直接参照・更新（5箇所）を `uuid::Uuid` ベースに修正
- `src/main.rs` — `ConfirmOrderEntrySubmit` ハンドラの `PaneEvent(focused_pane, ...)` 修正
- `src/main.rs` — `OpenOrderPanel` ハンドラの `dashboard.panes.split()` 直接呼び出しを `WindowAdded` 経由に変更

**ゴール:** アプリが起動し、パネルをドラッグ移動・クローズできる。

---

### Phase 5: タイトルバー UI とパネル追加 UI

- タイトルバー: コンテンツ種別アイコン / ラベル + ×ボタン + ドラッグハンドル
- パネル追加: サイドバーまたはキーボードショートカットから `WindowAdded` を発行
- 既存の「パレット（銘柄選択）」フローとの統合確認

**ゴール:** 既存の全コンテンツ種別（Heatmap/Kline/Ladder/TAS/…）が表示できる。

---

### Phase 6: テスト・クリーンアップ

- `data/src/layout/pane.rs` — `FloatingPaneData` の roundtrip テスト追加
- `src/layout.rs` — 変換関数のユニットテスト追加
- `pane_grid` 依存の import を全削除
- `saved-state.json` の旧フォーマットとの互換確認

---

## 削除する依存

Phase 完了後に各ファイルから以下を削除できる:

```rust
// src/screen/dashboard.rs
use iced::widget::{PaneGrid, pane_grid::{self, Configuration}};

// src/screen/dashboard/pane.rs
use iced::widget::{..., pane_grid, ...};

// src/layout.rs
use iced::widget::pane_grid::{self, Configuration};
use data::layout::pane::Axis;

// src/modal/pane/settings.rs、src/modal/pane/indicators.rs
use iced::widget::pane_grid;   // 各ファイルのインポート

// src/widget.rs
use iced::widget::pane_grid;   // link_group_button 関連

// src/style.rs
use iced::widget::pane_grid::{Highlight, Line};
pub fn pane_grid(theme: &Theme) -> widget::pane_grid::Style { ... }  // 関数ごと削除
```

`iced::widget::PaneGrid` 自体は `Cargo.toml` で feature として制御されていないため
ビルドへの影響はなし。コードから参照が消えれば dead-code lint も消える。

---

## リスクと注意点

| リスク | 対策 |
|--------|------|
| iced の `Widget::layout` で絶対座標が想定通り動かない | Phase 3 で `multi_split.rs`（`src/widget/multi_split.rs`）を参考にする |
| ウィジェットツリーの子数とウィンドウ数がズレてパニック | `diff()` で子ツリーを正しく同期する |
| ドラッグ中に `on_move` が毎フレーム発行され state 更新が多い | `MouseButtonReleased` 時のみ最終位置を確定させる（中間はウィジェット内部状態で管理） |
| saved-state.json 旧フォーマットでクラッシュ | `ok_or_default` を各フィールドに適用（既存パターン踏襲） |
| `tick()` の `maximized_pane` 最適化消滅によるパフォーマンス劣化 | フォーカス中パネル優先 + 非フォーカスは N フレームに 1 回 tick するフォールバック最適化で代替（Phase 4） |
| `pane::State::view()` 戻り値変更でモーダル等の呼び出しが全滅 | Phase 2 で型を `Element` に変え、モーダルビュー関数群を同一 Phase で一括修正する。片方だけ変えると `cargo check` が通らないため必ず同時実施 |
| `main.rs` の `dashboard.panes.split()` 直接呼び出し（`OpenOrderPanel` ハンドラ）が残留 | Phase 4 の `main.rs` 修正チェックリストに明示的に含める |

---

## 参照ファイル一覧

| ファイル | 役割 | Phase |
|---------|------|-------|
| `src/screen/dashboard.rs` | Dashboard 構造体・view/update・tick | 2,4 |
| `src/screen/dashboard/pane.rs` | pane::State・pane::Message・view() 戻り値型 | 2 |
| `src/layout.rs` | 永続化 ↔ GUI 変換 | 2 |
| `src/main.rs` | focus 直接操作・ConfirmOrderEntrySubmit・OpenOrderPanel | 4 |
| `src/style.rs` | pane_grid スタイル関数削除 | 4 |
| `src/modal/pane/settings.rs` | 設定モーダルビュー関数（7関数）引数型変更 | 2 |
| `src/modal/pane/indicators.rs` | インジケーターモーダルビュー関数（4関数）引数型変更 | 2 |
| `src/widget.rs` | link_group_button の pane_grid::Pane フィールド除去 | 2 |
| `data/src/layout/pane.rs` | データ層 Pane 型・Axis 削除・FloatingPaneData 追加 | 1 |
| `data/src/layout/dashboard.rs` | データ層 Dashboard 型 | 1 |
| `src/widget/floating_panes.rs` | **新規** カスタムウィジェット | 3 |
| `src/widget/multi_split.rs` | 絶対座標ウィジェットの実装参考 | — |
| `src/widget/column_drag.rs` | ドラッグイベント設計の参考 | — |
