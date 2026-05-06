# ステータスバー（フッター）

メインウィンドウ最下部に固定表示される 20px のバー。
現在の起動モード（`LIVE` / `REPLAY`）を常時視認でき、クリックでモードをトグルできる。

---

## UI 仕様

```
┌─────────────────────────────────────────────────────────┐
│  sidebar │               dashboard                       │
├──────────┴───────────────────────────────────────────────┤
│  ● LIVE  ← クリックでトグル                               │
└─────────────────────────────────────────────────────────┘
```

| 項目 | 値 |
|------|----|
| 高さ | `STATUS_BAR_HEIGHT = 20`（u32） |
| 背景色 | `STATUS_BAR_BG = Color::from_rgb(0.08, 0.08, 0.08)` |
| バッジ位置 | 左端（padding left 8px） |
| フォントサイズ | 11px |

| 状態 | ラベル | 色 | カーソル |
|------|--------|----|----------|
| Live（有効） | `● LIVE` | 緑 `(0.2, 0.75, 0.3)` | pointer |
| Replay（有効） | `● REPLAY` | アンバー `(0.9, 0.6, 0.1)` | pointer |
| 抑制中 | `● LIVE …` / `● REPLAY …` | 各色を 50% 減光 | default |

- クリック: `Action::SwitchAppMode(target)` を `NativeMenuAction` として dispatch（`target` = 現在と逆のモード）
- ツールチップ（有効時）: `クリックで Replay に切替` / `クリックで Live に切替`
- ツールチップ（抑制中）: 抑制理由（後述の enable 計算参照）
- dirty 時は **disabled にせず**、クリック後に save/discard confirm dialog へ遷移
- `Ctrl/Cmd+M` アクセラレータと併存

---

## enable 計算（`mode_toggle_state`）

```rust
pub struct ModeToggleState {
    pub current: AppMode,
    pub enabled: bool,
    pub disabled_reason: Option<&'static str>,
}

pub fn mode_toggle_state(
    current: AppMode,
    engine_busy: bool,
    mode_switch_in_progress: bool,
) -> ModeToggleState { ... }
```

抑制理由の優先順位（高 → 低）:

1. `mode_switch_in_progress` → `"Engine を再起動中…"`
2. `engine_busy` → `"Engine がビジーです"`
3. それ以外 → `enabled = true`

---

## 合成位置

`status_bar` は `view_with_modal` に渡す `base` の**内側**（`active_menu` 分岐の直前）に push する:

```rust
base = base.push(
    row![sidebar_view, dashboard_view]
        .spacing(4).padding(8).height(Length::Fill)
);
let toggle_state = mode_toggle_state(current_mode, engine_busy, switching);
base = base.push(status_bar(toggle_state));

if let Some(menu) = self.sidebar.active_menu() {
    self.view_with_modal(base.into(), dashboard, menu)
} else {
    base.into()
}
```

- **popout ウィンドウには表示しない**（`id == self.main_window.id` ブロック内のみ）
- `main_dialog_modal`（全面暗転）展開中は overlay の下に隠れる（意図的トレードオフ）
- `dashboard_modal`（背景透過）展開中はフッターは見える

---

## テスト

| テスト | 対象 |
|--------|------|
| `src/menu.rs` unit TT1 | `mode_toggle_state(Live, busy=false, switching=false).enabled == true` |
| `src/menu.rs` unit TT2 | `engine_busy=true` で `enabled=false`、`disabled_reason` が `Engine がビジーです` |
| `src/menu.rs` unit TT3 | `mode_switch_in_progress=true` が最優先 |
| `src/main.rs` unit T5 | `STATUS_BAR_HEIGHT == 20` / `STATUS_BAR_BG` 定数が存在すること |
| integration TT5 | フッタークリック相当の Message で `Action::SwitchAppMode(!current)` が dispatch |
| integration TT6 | dirty 時クリック → `SaveAndSwitchMode` confirm dialog が出る |

---

## 既知の制限

- **toast と重複**: toast は footer に重なる可能性あり（意図的トレードオフ）。inset 調整は将来フェーズ。
- **右クリック未対応**: ミニメニューは将来フェーズ。
- **`status_bar()` の `'static`**: 入力参照がないため lifetime elision 不可。`'static` を維持（呼び出し元 `view()` での利用に問題なし）。
