# Floating Windows 移行: 仕様

## 1. ゴール

1. **フローティングレイアウト**: メインウィンドウ内のパネルを任意の位置・サイズに配置・移動・リサイズできる
2. **ズーム・パン**: スクロールホイールでズーム、空白ドラッグでパン（Figma / Blender 操作感）
3. **既存コンテンツの完全移行**: Heatmap / Kline / Ladder / TAS / Starter など全コンテンツ種別が新レイアウト上で動作する
4. **popout 継続**: OS レベルの別ウィンドウ（popout）機能は変更なく維持する
5. **saved-state 互換フォールバック**: 旧フォーマット（`pane` ツリー）は起動時に自動的に空ウィンドウリストにフォールバックする

---

## 2. スコープ

### Phase 1 — データクレート: `FloatRect` / `FloatingPaneData` 追加

- `data/src/layout/mod.rs` に `FloatRect`（`x, y, width, height: f32`）を追加
- `data/src/layout/pane.rs` から `Pane::Split` と `Axis` を削除、`FloatingPaneData` を追加
- `data/src/layout/dashboard.rs` の `Dashboard` 構造体を `windows: Vec<FloatingPaneData>` に書き換え
- `Camera`（`pan: (f32,f32)`, `zoom: f32`）を `data` クレートに追加
- **ゴール**: `cargo test -p data` が通る

### Phase 2 — GUI 状態と型の移行

- `src/screen/dashboard.rs` の `Dashboard` 構造体を `Vec<FloatingPane>` ベースに変更
- `pane::Message` の追加・削除・引数型変更（`pane_grid::Pane` → `uuid::Uuid`）
- `pane::State::view()` の戻り値を `pane_grid::Content` → `Element` に変更
- `src/modal/pane/settings.rs`（7関数）・`src/modal/pane/indicators.rs`（4関数）の引数型変更
- `src/widget.rs` の `link_group_button` 識別子型変更
- **ゴール**: `cargo check` が通る（`view()` は一時的にダミーを返してよい）

### Phase 3 — `FloatingPanes` カスタムウィジェット実装

- `src/widget/floating_panes.rs` を新規作成
- `Camera` の `world_to_screen` / `screen_to_world` / `zoom_at` 実装
- `Widget::layout()` — カメラ変換を適用した絶対座標配置
- `Widget::draw()` — タイトルバー（28px）+ コンテンツ描画
- `Widget::on_event()` — ドラッグ・リサイズ（8方向）・パン・ズーム
- `Widget::mouse_interaction()` — カーソル形状切り替え
- **ゴール**: `cargo build` でウィジェットがビルドできる

### Phase 4 — `Dashboard::view()` 切り替えと動作確認

- `src/screen/dashboard.rs` の `view()` を `FloatingPanes` に切り替え
- `update()` で新メッセージ（`WindowMoved` / `WindowResized` / `WindowFocused` / `WindowClosed` / `WindowAdded` / `CameraChanged`）を処理
- `tick()` の `maximized_pane` 最適化をフォーカスベース（非フォーカスは N フレームに 1 回）に変更
- `src/style.rs` の `pub fn pane_grid()` と `Highlight`/`Line` インポートを削除
- `src/main.rs` の `dashboard.focus` 直接参照 5 箇所を `uuid::Uuid` ベースに修正
- `OpenOrderPanel` ハンドラの `dashboard.panes.split()` 直接呼び出しを `WindowAdded` 経由に変更
- **ゴール**: アプリが起動し、パネルをドラッグ移動・クローズできる

### Phase 5 — タイトルバー UI とパネル追加 UI

- タイトルバー: コンテンツ種別アイコン / ラベル + ×ボタン + ドラッグハンドル
- パネル追加: サイドバーボタン（ラベル "Add Window"）または `Ctrl+N` から `WindowAdded` を発行
- 既存「銘柄選択パレット」フローとの統合確認
- **ゴール**: 既存の全コンテンツ種別が表示できる

### Phase 6 — テスト・クリーンアップ

- `data/src/layout/pane.rs` に `FloatingPaneData` の roundtrip テストを追加
- `src/layout.rs` に変換関数のユニットテストを追加
- `pane_grid` 依存の import を全ファイルから削除
- `saved-state.json` 旧フォーマットとの互換確認
- `tests/e2e/smoke.sh` に FloatingPane 関連の観測項目を追加すること:
  - `WindowMoved` ログがハンドシェイク完了後に到達することを確認
  - 観測ウィンドウ中にクラッシュ（プロセス異常終了）が発生しないことを確認

---

## 3. 含めないもの

- **OS レベルウィンドウ数の増減**: popout の追加・削除ロジックはそのまま維持し、本計画では変更しない
- **タブ化・グループ化**: フローティングパネルのタブ表示やスナップグリッドは本計画スコープ外
- **アニメーション**: ドラッグ時のスムーズアニメーションは Phase 5 完了後に判断
- **キーボードナビゲーション**: パネル間のフォーカス移動ショートカットは Phase 6 以降

---

## 4. 機能要件

| ID | 要件 |
|----|------|
| F1 | パネルをドラッグでメインウィンドウ内の任意位置に移動できる |
| F2 | パネルの 8 方向エッジをドラッグしてリサイズできる。最小サイズは `240×150px`（ワールド座標） |
| F3 | スクロールホイールでカーソル位置を中心にズームできる（0.25〜4.0 倍） |
| F4 | 空白部分をドラッグまたはホイールボタンドラッグでパンできる |
| F5 | パネルをクリックするとフォーカスが移動し、最前面に表示される |
| F6 | タイトルバーの ×ボタンでパネルを閉じられる |
| F7 | サイドバーまたはショートカットで新規パネルを追加できる |
| F8 | カメラ状態（ズーム・パン）はレイアウトごとに `saved-state.json` に保存・復元される |
| F9 | popout 機能（OS 別ウィンドウ）は引き続き動作する |

---

## 5. 非機能要件

| ID | 要件 |
|----|------|
| NF1 | フォーカス中パネルの `tick()` は毎フレーム実行。非フォーカスは 4 フレームに 1 回以下 |
| NF2 | ドラッグ中の中間座標はウィジェット内部状態で管理し、`MouseButtonReleased` 時のみ `on_move` を発行してアプリ State 更新頻度を抑える |
| NF3 | ズーム倍率変更時は `on_camera` を毎ノッチ発行するが、State 更新コストは `Camera` 値 1 個のコピーのみ |
| NF4 | `saved-state.json` 旧フォーマット（`pane` ツリー）は `ok_or_default` でフォールバックし、クラッシュしない |
