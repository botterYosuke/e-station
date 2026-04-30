# OS ネイティブメニューバー 実装記録

**実装日**: 2026-04-30  
**実装者**: Claude Sonnet 4.6 (botterYosuke)  
**ステータス**: 完了・ビルド確認済み

---

## 要件

### ユーザー要件

- **OS ネイティブのメニューバー**（タイトルバー直下に表示される Win32 / macOS 標準メニュー）をメインウィンドウに追加する
- **live モード**: `File > 開く...` / `File > 名前を付けて保存...`
- **replay モード**: `File > ストラテジーを開く...`
- いずれのモードにも `File > 終了` を配置する

### 非要件（スコープ外）

- Linux での OS ネイティブメニュー（Linux は no-op、既存のサイドバーで代替）
- Edit / View などの追加サブメニュー
- キーボードショートカット（アクセラレーター）のバインド
- メニューバーのカスタマイズ UI

---

## 実装前の状態

### 任意ファイル読み書き機能

- アプリは `%APPDATA%\flowsurface\saved-state.json` への**自動保存のみ**対応
- ユーザーが任意のパスへ設定を書き出したり、任意の設定ファイルを読み込む手段がなかった
- replay モードのストラテジー選択は既存の rfd ダイアログを使っていたが、トリガーはサイドバー UI 経由のみ

### メニューバー

- macOS ではタイトルバーを非表示にして `FLOWSURFACE` テキストをカスタム描画していたが、メニューバーはなかった
- Windows / Linux では装飾なし（OS 標準タイトルバーのみ）

---

## 実装後の状態

### ファイル構成

| ファイル | 変更種別 | 内容 |
|---------|---------|------|
| `Cargo.toml` | 追加 | `muda = "0.15"` を Windows/macOS 限定で追加 |
| `src/native_menu.rs` | 新規 | muda 統合・Subscription・プラットフォーム分岐 |
| `src/main.rs` | 変更 | mod / Message / フィールド / ハンドラ / subscription |

### `src/native_menu.rs` の構造

```
native_menu
├── pub enum Action { OpenFile, SaveAs, OpenStrategy }
├── pub fn attach(raw_id: u64, app_mode: AppMode)   ← Windows/macOS で muda 初期化
├── pub fn subscription() -> Subscription<Action>    ← 16ms ポーリング
└── mod platform (cfg: windows or macos)
    ├── static MENU_IDS: OnceLock<MenuIds>
    ├── fn attach(...)  ← Menu 構築 + init_for_hwnd / init_for_nsapp
    └── fn event_stream() -> impl Stream  ← MenuEvent::receiver() をポーリング
```

### `src/main.rs` の変更

#### 追加した Message バリアント

```rust
NativeMenuSetup(u64),                              // HWND 取得後に attach を呼ぶ
NativeMenuAction(native_menu::Action),             // メニュー項目クリック
NativeSaveAsPath(Option<PathBuf>),                 // rfd 保存先選択結果
NativeSaveAsWithSpecs(HashMap<window::Id, WindowSpec>), // ウィンドウ情報収集後
NativeOpenFileApply(String),                       // ファイル読み込み内容
```

#### 追加したフィールド

```rust
// Flowsurface 構造体
pending_save_path: Option<std::path::PathBuf>,
```

#### fn new() の変更

```rust
// ウィンドウ作成後に raw_id を取得し NativeMenuSetup へ繋ぐ
let setup_native_menu =
    iced::window::raw_id::<Message>(main_window_id).map(Message::NativeMenuSetup);

// タスクチェーンに挿入
open_main_window.discard()
    .chain(setup_native_menu)   // ← 追加
    .chain(load_layout)
    .chain(launch_sidebar.map(Message::Sidebar))
```

#### save_state_to_disk のリファクタ

シリアライズロジックを `build_state_json` として抽出し、`save_state_to_disk` から呼ぶ形に変更した。  
これにより "Save As" ハンドラが同じロジックを DRY に使える。

```rust
// 変更前: save_state_to_disk に全ロジックが詰まっていた

// 変更後:
fn build_state_json(&mut self, windows: &HashMap<..>) -> Option<String>  // 新規
fn save_state_to_disk(&mut self, windows: &HashMap<..>)                  // build_state_json を呼ぶだけに
```

---

## 各ハンドラの動作フロー

### 起動時（NativeMenuSetup）

```
fn new()
  → iced::window::raw_id::<Message>(main_window_id)
  → Message::NativeMenuSetup(hwnd: u64)
  → native_menu::attach(hwnd, AppMode)
      → Menu::new() + Submenu::new("File")
      → app_mode に応じてメニュー項目を追加
      → menu.append(&file)
      → MENU_IDS.set(ids)
      → Box::leak(Box::new(menu))     ← Drop 抑制（後述）
      → menu_ref.init_for_hwnd(hwnd)  ← Win32 SetMenu
```

### File > 開く...（live モードのみ）

```
NativeMenuAction(OpenFile)
  → rfd::AsyncFileDialog::pick_file() (Python フィルタ除く)
  → ファイル読み込み → String
  → NativeOpenFileApply(json)
      → serde_json::from_str::<data::State>(&json) でバリデーション
        ✓ OK: data::write_json_to_file(json, SAVED_STATE_PATH) → self.restart()
        ✗ Err: Toast::error("無効な設定ファイルです: {e}")
```

`self.restart()` は `Flowsurface::new()` を呼び `load_saved_state()` が上書き済みの
`saved-state.json` を読み直すため、新しい設定が即座に反映される。

### File > 名前を付けて保存...（live モードのみ）

```
NativeMenuAction(SaveAs)
  → rfd::AsyncFileDialog::save_file()
  → NativeSaveAsPath(Some(path))
      → self.pending_save_path = Some(path)
      → window::collect_window_specs(...)
  → NativeSaveAsWithSpecs(windows)
      → self.pending_save_path.take()
      → self.build_state_json(&windows)
      → std::fs::write(path, json)
      → Toast::info("保存しました: {path}")
```

2 メッセージに分割しているのは、ウィンドウの位置・サイズ収集が非同期タスクであるため。  
`pending_save_path` フィールドで保存先パスを橋渡しする。

### File > ストラテジーを開く...（replay モードのみ）

```
NativeMenuAction(OpenStrategy)
  → rfd::AsyncFileDialog::pick_file() (.py フィルタ)
  → Message::StrategyFilePicked(path)
      → self.replay_strategy_file = path  (既存ハンドラ)
```

既存の `PickStrategyFile` フローと全く同じ `StrategyFilePicked` メッセージを使う。
メニューバーとサイドバーどちらからでも同じ結果になる。

---

## 設計上の判断

### muda::Menu の保持方法: `Box::leak`

muda 0.15 の `Menu` は内部が `Rc<RefCell<...>>` ベースのため `!Send`。  
通常の `static Mutex<Option<Menu>>` に置けない（`Sync` 要件を満たせない）。

`Box::leak(Box::new(menu))` で生ポインタをリークする方法を採用した。

- `Menu` の `Drop` が呼ばれず HMENU / NSMenu が生き続ける
- プロセス終了時に OS がハンドル回収するため実害なし
- `muda` の内部実装を変えずに済む（unsafe を muda に押し付けない）

代替案として `once_cell::sync::OnceBox<Menu>` も検討したが、`OnceBox` は `T: Send` を要求するため NG だった。

### HWND 取得タイミング: `fn new()` のタスクチェーン

`iced::window::raw_id::<Message>(id)` は `Task<u64>` を返し、ウィンドウが実際に
作成された後に値が確定する。`fn new()` の初期タスクチェーンに組み込むことで
最速のタイミングでメニューを装着できる。

`window::events()` の `Opened` イベントを拾う代替案もあったが、現在の `window::events()`
は `CloseRequested` しか監視しておらず（`src/window.rs:29`）、拡張コストが高いため採用しなかった。

### メニュー構造をモードで切り替える

`attach()` 呼び出し時点で `APP_MODE` static が確定しているため、起動時に一度だけ
モードに応じたメニューを構築する。  
live/replay の切り替えはアプリ再起動を必要とするため、動的な切り替えは不要。

### Linux は no-op

`muda` はデフォルトで GTK を引き込む（Linux ターゲット）。  
プロジェクトの `x11`/`wayland` ビルドを壊さないよう、Linux では
`attach` / `subscription` を完全に no-op にした。

```toml
# Cargo.toml
[target.'cfg(any(target_os = "windows", target_os = "macos"))'.dependencies]
muda = { version = "0.15", default-features = false }
```

`default-features = false` で GTK / libxdo を無効化し、Linux での不要な
ビルド依存を防ぐ。

### "開く" のバリデーション

ファイルを `saved-state.json` に書き込む前に `serde_json::from_str::<data::State>` で
解析を試みる。パースエラーなら上書きせずエラー toast を表示する。  
完全な UI 検証（ペイン数・フィールド範囲など）は行わない（既存の `read_from_file`
が破損時にバックアップを作る仕組みがあるため）。

---

## テストカバレッジ

### テスト実装ログ（2026-04-30 追加）

以下 6 項目の自動テストを追加した（`cargo test --bin flowsurface` で 234 PASS 確認済み）。

#### 実装方針

- **muda / rfd の OS API は触らない**。muda の native menu API はプロセス外 Win32/NSMenu を叩くため
  単体テスト不可。rfd ダイアログもモック不可。
- **ソースインスペクション方式** を使用（`confirm_dialog_overlay_tests` と同じパターン）。
  `include_str!("./main.rs")` でハンドラ本体を文字列として検証する。iced ランタイムを起動せずに
  「このハンドラはトーストを出さない」「このブランチは `restart()` を呼ぶ」などを機械的に守れる。
- `NativeOpenFileApply` の JSON バリデーションは `serde_json::from_str::<data::State>` を
  テスト内で直接呼ぶ純粋 Rust テストで補完。

#### テスト追加位置・コマンド

| テスト | ファイル | テスト名 |
|--------|---------|---------|
| 1 live/replay アクション | `src/native_menu.rs` | `native_menu::tests::*` |
| 6 Mutex 上書き可能性 | `src/native_menu.rs` | `native_menu::platform::tests::*` |
| 2–5 ハンドラ構造 + JSON | `src/main.rs` | `native_menu_handler_tests::*` |

```bash
cargo test --bin flowsurface native_menu   # 18 テストのみ実行
cargo test --bin flowsurface               # 234 テスト全体
```

#### 設計上の判断（テスタビリティ）

- **`actions_for_mode` の分離**: live/replay でどのアクションが有効かを純粋な関数として抽出
  （`#[cfg(test)]` で test ビルド限定）。muda の Menu 構築コードと切り離すことでモード分岐を
  テスト可能にした。
- **`handler_body` のマーカー設計**: ハンドラアームのマーカーは `"            Message::Foo =>"` と
  インデントと `=>` を含む一意な形式にした。`"Message::Foo"` だと
  `None => Message::Foo` などのメッセージ構築サイトを誤検出する。
- **iced ランタイムを使わない理由**: `Flowsurface` 構造体は iced の Window/Task が絡む複雑な
  初期化が必要。ランタイムなしでは構築できないため、ハンドラ本体のソース検証にとどめた。

#### `let...else` → `?` リファクタ（clippy 対応）

`NativeMenuAction(OpenFile)` ハンドラ内の `let Some(handle) = ... else { return None; }` を
`?` 演算子に書き換えた（clippy の `let_else` lint 対応）。動作は同一。

### 手動確認項目

| No. | 確認内容 | モード | 期待結果 | 自動テスト |
|-----|---------|--------|---------|----------|
| 1 | 起動直後にウィンドウ上部に `File` メニューが表示される | live / replay | タイトルバー直下にネイティブメニューバー | — (OS API) |
| 2 | `File > 開く...` → 任意の `.json` を選択 | live | アプリがリスタートし設定が反映される | ✅ `open_file_apply_valid_json_calls_write_and_restart` |
| 3 | `File > 開く...` → 壊れた JSON ファイルを選択 | live | エラー toast が出てアプリは継続 | ✅ `open_file_apply_invalid_json_pushes_error_toast`, `open_file_apply_invalid_json_does_not_restart` |
| 4 | `File > 名前を付けて保存...` → パスを選択 | live | 選択したパスに `saved-state.json` が書き出される | ✅ `save_as_with_specs_delegates_to_build_state_json` |
| 5 | `File > ストラテジーを開く...` → `.py` ファイルを選択 | replay | `replay_strategy_file` にパスがセットされる | — (rfd dialog) |
| 6 | `File > 終了` | どちらも | アプリが終了する（OS ネイティブ Quit 動作） | — (OS API) |
| 7 | popout ウィンドウにメニューバーが表示されない | live | popout ウィンドウに File メニューなし | — (runtime) |
| 8 | replay モードで `File > 開く...` が表示されない | replay | メニューに「ストラテジーを開く...」のみ | ✅ `replay_mode_provides_open_strategy_only` |

### 既存テストへの影響

```bash
cargo test --workspace
```

既存テスト（`confirm_dialog_overlay_tests` を含む）は全 PASS 確認済み。  
`src/main.rs` の `MAIN_RS` ソース解析テストは `build_state_json` / `save_state_to_disk`
のリファクタ後も通過する（削除した関数名ではなく追加した関数名をテストしているわけではないため）。

---

## 既知の制限・将来の拡張候補

| 項目 | 内容 | 優先度 |
|------|------|--------|
| アクセラレーター | Ctrl+O / Ctrl+S などのショートカット未設定 | 低 |
| Edit / View サブメニュー | テーマ切り替え・タイムゾーン設定などを移動できる | 低 |
| Linux ネイティブメニュー | GTK メニューバーへの対応（現状 no-op） | 低 |
| "開く" のホットリロード | 現状は `self.restart()` で再起動。レイアウトのみホットスワップする方法もある | 中 |
| "保存" の上書き確認ダイアログ | 既存ファイルを上書きする際に rfd の save_file は OS 側で確認するが、動作確認が必要 | 低 |
| replay モードでの "名前を付けて保存..." | replay セッション中は live 設定を汚染しないよう保存を禁止中。replay 独自のスナップショット保存は別途設計が必要 | 中 |

---

## 関連ファイル早見表

| ファイル | 役割 |
|---------|------|
| `src/native_menu.rs` | メニュー構築・muda 統合・Subscription |
| `src/main.rs:19` | `mod native_menu;` |
| `src/main.rs:748` | Flowsurface 構造体 `pending_save_path` フィールド |
| `src/main.rs:888-905` | 追加した Message バリアント |
| `src/main.rs:1318-1322` | `fn new()` での `setup_native_menu` タスクチェーン |
| `src/main.rs:2289-2410` | NativeMenu* ハンドラ群 |
| `src/main.rs:3232` | `subscription()` への `native_menu::subscription()` 追加 |
| `src/main.rs:3581` | `build_state_json` ヘルパー（新規） |
| `src/main.rs:3655` | `save_state_to_disk`（`build_state_json` を呼ぶよう変更） |
| `Cargo.toml:81-83` | `muda = "0.15"` 依存関係 |
