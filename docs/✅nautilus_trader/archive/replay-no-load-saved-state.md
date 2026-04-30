# replay モードで saved-state.json をロードしない

## 背景・動機

現在の状態（2026-04-30 時点）：

| 操作 | live モード | replay モード |
|------|------------|--------------|
| ロード | `load_saved_state()` で全フィールドを復元 | ペインレイアウト（`layout_manager`）のみ破棄（D8）、他は live と同じ値を使用 |
| 保存 | `save_state_to_disk()` で全フィールドを書き込み | **実装済み**：no-op（`replay-no-saved-state.md` の実装）|

D8 でレイアウトは破棄済みだが、replay 起動時に以下のフィールドはまだ
`saved-state.json` から読み込んでいる：

| フィールド | `SavedState` のフィールド名 |
|-----------|--------------------------|
| ウィンドウ位置・サイズ | `main_window` |
| UI テーマ | `theme` / `custom_theme` |
| タイムゾーン | `timezone` |
| スケールファクタ | `scale_factor` |
| サイドバー状態 | `sidebar` |
| 音声設定 | `audio_cfg` |
| 出来高単位 | `volume_size_unit` |
| プロキシ設定 | `proxy_cfg` |

「ロードもスキップ」は、これらすべてを `SavedState::default()` に差し替え、
replay セッションを live 設定から完全に切り離す変更。

---

## ゴール

- **ロードしない**: replay モード起動時に `load_saved_state()` を呼ばず
  `SavedState::default()` を使う
- **保存しない**: すでに実装済み
- replay は毎回クリーンなデフォルト状態で起動する

---

## 変更対象と内容

### 1. `src/main.rs` — `Flowsurface::new()` の先頭を変更

現在（[main.rs:1155-1156](../../src/main.rs#L1155-L1156)）：

```rust
fn new() -> (Self, Task<Message>) {
    let saved_state = layout::load_saved_state();
```

変更後：

```rust
fn new() -> (Self, Task<Message>) {
    let is_replay_mode = APP_MODE
        .get()
        .map(|m| *m == engine_client::dto::AppMode::Replay)
        .unwrap_or(false);

    let saved_state = if is_replay_mode {
        log::info!("replay mode: skipping load_saved_state (D9-load), using defaults");
        layout::SavedState::default()
    } else {
        layout::load_saved_state()
    };
```

### 2. `src/main.rs` — 後続の D8 ガードを整理（任意）

`saved_state` が常に `LayoutManager::new()` を持つようになるため、
[main.rs:1197-1213](../../src/main.rs#L1197-L1213) の `is_replay_mode` ガード
（D8）は実質的に無意味になる。

削除してコードをシンプルにするか、コメントとして残すか選択する。
**推奨は「コメント付きで残す」**：D8 は replay での空レイアウト開始を
意図的に保証するドキュメントとして機能するため。

---

## 影響範囲まとめ

| ファイル | 変更種別 |
|---------|---------|
| `src/main.rs` | `Flowsurface::new()` 先頭に `is_replay_mode` チェックを追加（約 8 行） |

D8 ガードの整理は任意（+0〜-10 行程度）。

---

## トレードオフ

### メリット

- live / replay が完全分離：replay の操作が live 設定に一切影響しない
- `auto_generate_replay_panes` が「常に空グリッドから始まる」という前提で
  設計できる（`src/screen/dashboard.rs` の空グリッド bootstrap 修正と整合）
- `saved-state.json` が存在しない（初回起動・CI 環境）でも動作が安定

### デメリット

- replay 起動ごとにデフォルト状態に戻る：
  - ウィンドウサイズがデフォルト（`main_window: None` → OS が決定）
  - テーマがデフォルト（ダーク等のカスタム設定が反映されない）
  - プロキシ設定が `None`（プロキシ経由で J-Quants にアクセスしている場合、
    手動で再設定が必要）
  - タイムゾーンがデフォルト
- **プロキシ設定は注意が必要**：プロキシ経由でデータアクセスしている環境では
  replay セッションがネットワーク接続できなくなる可能性がある

---

## テスト方針

自動テストの追加は不要。理由：

- `APP_MODE` OnceLock に依存しており、単体テストで安全に切り替えられない
- `load_saved_state()` の呼び出し有無はユニットテストの対象になっていない

手動確認手順：

1. `saved-state.json` にカスタムテーマ・ウィンドウサイズが保存された状態を用意する
2. `--mode replay` で起動し、デフォルトのウィンドウサイズ・テーマで起動することを確認する
3. `--mode live` で起動し、カスタムテーマ・ウィンドウサイズが保持されていることを確認する
4. `saved-state.json` を削除した状態で `--mode replay` が正常起動することを確認する

---

## 設計上の判断

**「保存スキップだけで十分では？」（現行）**: live 設定の汚染防止は保存スキップで達成済み。
ロードもスキップするのは「完全分離を優先する」選択であり、
プロキシ・テーマ等の利便性を犠牲にする。

**「別ファイル（`replay-state.json`）を用意する案（没）**:
replay セッションの設定を永続化したい場合の解決策だが、
replay は実験的・使い捨てのセッションであり専用の永続ストアを持つ価値がない。

**D8 ガードの扱い**: このフェーズ実装後は D8 ガードが冗長になるが、
意図の明示性のため削除せずコメントで「D9-load 実装により常に空レイアウトになる」と
記載することを推奨する。
