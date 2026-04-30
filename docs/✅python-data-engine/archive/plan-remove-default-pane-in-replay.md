# 計画書: replay モードの 5 ペインデフォルト廃止

## 背景・問題

`Dashboard::default()` は `default_pane_config()` を呼んで **5 枚の Starter ペイン** を
生成するスプリットグリッドを作る（`src/screen/dashboard.rs:109-131`）。

replay モードでは `Flowsurface::new()` が `LayoutManager::new()` を呼ぶが、
`LayoutManager::new()` は内部で `Dashboard::default()` を呼ぶため、
不要な 5 ペインが一度生成される。現在のコードはその後すぐ上書きしている（`main.rs:1214-1226`）：

```rust
// 現状（main.rs:1214-1226）
let layout_manager = if is_replay_mode {
    let mut lm = LayoutManager::new();          // ← 5 ペイン生成（無駄）
    if let Some(layout) = lm.layouts.first_mut() {
        let (panes, _initial_pane) = iced::widget::pane_grid::State::new(
            crate::screen::dashboard::pane::State::default(),
        );
        layout.dashboard.panes = panes;          // ← 上書き
        layout.dashboard.focus = None;
    }
    lm
} else {
    saved_state.layout_manager
};
```

この `panes` フィールドの直接書き換えは、`Dashboard` の内部構造に依存した脆いパターンである。

## 目標

replay モードでは **5 ペインデフォルトを一切生成しない**。
`auto_generate_replay_panes` が `ReplayDataLoaded` 受信後にペインを追加するので、
起動直後のダッシュボードは **空（ペインなし）** で構わない。

## 変更内容

### Step 1: `Dashboard::new_empty()` を追加

`src/screen/dashboard.rs`

```rust
impl Dashboard {
    /// Creates a dashboard with no panes (used in replay mode before
    /// `auto_generate_replay_panes` populates the grid).
    pub fn new_empty(layout_id: uuid::Uuid) -> Self {
        let (panes, _) = pane_grid::State::new(pane::State::default());
        Self {
            panes,
            focus: None,
            streams: UniqueStreams::default(),
            popout: HashMap::new(),
            layout_id,
            replay_pane_registry: replay_pane_registry::ReplayPaneRegistry::new(),
        }
    }
}
```

### Step 2: `LayoutManager::new_for_replay()` を追加

`src/modal/layout_manager.rs`

```rust
impl LayoutManager {
    /// Creates a `LayoutManager` with a single empty layout for replay mode.
    /// Unlike `LayoutManager::new()`, this does NOT call `Dashboard::default()`
    /// (which would generate 5 unused Starter panes).
    pub fn new_for_replay() -> Self {
        let layout_id = LayoutId {
            unique: Uuid::new_v4(),
            name: "Layout 1".into(),
        };
        Self {
            layouts: vec![Layout {
                id: layout_id.clone(),
                dashboard: Dashboard::new_empty(layout_id.unique),
            }],
            active_layout_id: Some(layout_id.unique),
            edit_mode: Editing::None,
        }
    }
}
```

### Step 3: `Flowsurface::new()` の workaround を置換

`src/main.rs:1214-1226`

```rust
// 変更後
let layout_manager = if is_replay_mode {
    LayoutManager::new_for_replay()
} else {
    saved_state.layout_manager
};
```

## 変更しないもの

| 対象 | 理由 |
|------|------|
| `Dashboard::default()` | live モードで引き続き使用（LayoutManager::new / AddLayout） |
| `default_pane_config()` | `Dashboard::default()` からのみ呼ばれる。live モード用として維持 |
| `LayoutManager::new()` | live モードと `Message::AddLayout` で使用。変更不要 |

## テスト方針

既存テストへの影響はなし（`replay_pane_registry` のテストは `ReplayPaneRegistry` 単体を対象）。

新規テスト不要：
- `new_for_replay()` はシンプルなコンストラクタ。`auto_generate_replay_panes` の
  既存統合テストが replay 起動フローをカバーしている。
- regression guard: `new_for_replay()` が `Dashboard::default()` を呼ばないことは
  コードを読めば自明。

## ファイル変更一覧

| ファイル | 変更種別 |
|---------|---------|
| `src/screen/dashboard.rs` | `Dashboard::new_empty()` 追加 |
| `src/modal/layout_manager.rs` | `LayoutManager::new_for_replay()` 追加 |
| `src/main.rs` | workaround 12 行 → 1 行に置換 |
