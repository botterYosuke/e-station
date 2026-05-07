# 立花ログインボタン — サイドバー → フッター移動

## Context

「立花 ログイン / 再ログイン」ボタンは現在 tickers_table サイドバーの取引所フィルタ列の中（Tachibana 行の直下）に置かれている。ユーザーの要求は、このボタンをアプリ最下部の status bar（フッター）に移動すること。

フッターは常時表示されるため、サイドバーが閉じていても Tachibana ログインが可能になり UX が向上する。
T35-U1 要件（常に表示・deadlock 回避）を維持したまま、より見つけやすい場所へ移動する。

---

## 変更ファイル

| ファイル | 役割 |
|---|---|
| `src/screen/dashboard/tickers_table.rs` | ① getter 追加、② サイドバーからボタン削除 |
| `src/main.rs` | ③ `status_bar` に Tachibana ボタン追加 |

---

## 変更内容

### ① `tickers_table.rs` — `tachibana_ready` getter を追加

`set_tachibana_ready` (line 246) の直後に追加：

```rust
pub fn tachibana_ready(&self) -> bool {
    self.tachibana_ready
}
```

### ② `tickers_table.rs` — サイドバーからログインボタンを削除

**2a) コメントブロック（lines 955-961）を削除する。**

ボタン移動後にこのコメントが残ると「sidebar に login button がある」と誤読される：

```rust
// Tachibana ships with a dedicated inline login
// button so the user can re-trigger the dialog
// without first clicking the venue toggle. The
// button is always visible (T35-U1, deadlock
// avoidance: VenueReady-gated UIs would otherwise
// hide the only way to recover from a cancelled
// login).
```

**2b) lines 962-965 の Tachibana 分岐を変更：**

**変更前**
```rust
col = col.push(
    column![self.exchange_filter_btn(venue), self.tachibana_login_btn(),]
        .spacing(2),
);
```

**変更後**
```rust
col = col.push(self.exchange_filter_btn(venue));
```

**2c) `tachibana_login_btn` メソッド（lines 777-790）を削除する。**
ロジックは `status_bar` 内でインライン化する。

### ③ `main.rs` — `status_bar` に Tachibana ボタンを追加

#### 関数シグネチャ変更（line 1806）

```rust
fn status_bar(
    state: crate::menu::ModeToggleState,
    tachibana_ready: bool,
) -> Element<'static, Message>
```

#### 関数本体末尾のレイアウトを row に変更（lines 1864-1875）

```rust
// 変更前
let content = tooltip(badge_el, tip, TooltipPosition::Top);
container(content)
    .width(iced::Length::Fill)
    ...

// 変更後
let badge_with_tip = tooltip(badge_el, tip, TooltipPosition::Top);

let tachibana_label = if tachibana_ready {
    "立花 再ログイン"
} else {
    "立花 ログイン"
};
let tachibana_btn: Element<'static, Message> = button(text(tachibana_label).size(11))
    .on_press(Message::RequestTachibanaLogin(
        crate::venue_state::Trigger::Manual,
    ))
    .style(|theme, status| style::button::transparent(theme, status, false))
    .into();

let content = row![
    badge_with_tip,
    iced::widget::horizontal_space(),
    tachibana_btn,
]
.align_y(Alignment::Center);

// padding::left(8).right(8) は row 全体の左右余白。
// badge ボタン自身の内部 padding とは別物で、
// フッター左端からバッジ・右端からTachibanaボタンに8pxの余白を与える。
container(content)
    .width(iced::Length::Fill)
    .height(STATUS_BAR_HEIGHT)
    .padding(padding::left(8).right(8))
    .align_y(Alignment::Center)
    .style(...)
    .into()
```

#### 呼び出し側 (line 5629) を変更

```rust
// 変更前
base = base.push(status_bar(mode_toggle));

// 変更後
base = base.push(status_bar(mode_toggle, tickers_table.tachibana_ready()));
```

`tickers_table` は同スコープ内の line 5535 で `&self.sidebar.tickers_table` として定義済み。

---

## 検証

```bash
cargo check -p flowsurface
cargo test
```

- アプリ起動後、フッターに「立花 ログイン」が表示されることを目視確認
- Tachibana ログイン後、「立花 再ログイン」に切り替わることを確認
- サイドバーの Tachibana 行に余分なボタンが表示されないことを確認
