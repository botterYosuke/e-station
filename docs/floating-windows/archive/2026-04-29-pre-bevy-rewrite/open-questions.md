# Floating Windows 移行: Open Questions

## 着手前に確定すべき事項

### Q1. ドラッグ中の中間 `on_move` 発行頻度 ✅ 決定: 案 A

**決定**: `MouseButtonReleased` 時のみ `on_move` を発行。ドラッグ中の中間座標は
`InternalState.drag` で管理し、`Widget::draw()` で加算して描画する。

`draw()` は毎フレーム走るためジャーキーにはならない。ジャーキーが問題になった時点で案 C に変更する。

---

### Q2. ズーム時のフォントサイズ・UI スケーリング ✅ 決定: 案 B（Phase 5 で実装）

**決定**: zoom に応じてフォントサイズ・アイコンサイズも比例スケールする。
ただし Phase 3 は案 A（最小実装）で完了し、Phase 5 以降で `zoom: f32` を
`pane::State::view()` に渡す方式で実装する。

**技術的制約**: iced の `canvas::Frame` 変換はテキストに効かない（既知制限）。
konva.js 相当のライブラリは iced エコシステムに存在しない。
`zoom: f32` を view に渡し全 `Text` ウィジェットで `font_size * zoom` を指定する方式で実装するが、
zoom 変化ごとに cosmic-text キャッシュが無効化されるためズーム中のフレームレートを Phase 5 で検証すること。

---

### Q3. ウィンドウがスクリーン外にドラッグされた場合の挙動 ✅ 決定: 案 A

**決定**: ワールド座標に制限を設けない（Figma 流）。
パンすれば辿り着けるため、クランプは不要。
「全パネルをビューに収める」ショートカットを着手後の決定事項として追加した。

---

### Q4. デフォルトレイアウト（`Dashboard::default()`）の座標算出方法 ✅ 決定: 案 A

**決定**: `DEFAULT_VIEWPORT_W = 1280 / DEFAULT_VIEWPORT_H = 800` でハードコード。
saved-state のフォールバック時のみ発動するため精度より単純さを優先する。

---

### Q5. `Widget::diff()` の実装方針 ✅ 決定: `diff_children()` + Vec 順序固定

**調査結果**: `multi_split.rs` は `tree.diff_children(&self.panels)` を使用。
これはインデックス順マッチングであり、**Vec の順序が安定していること**が前提。

**問題**: z 順管理のために Vec 要素を並び替えると `diff_children()` が誤った State を
別パネルに割り当ててクラッシュする。

**決定**:
- Vec 順序は挿入順で固定し、要素の並び替えは行わない
- z 順は `FloatingPane.z_index: u32` フィールドで管理する
- フォーカス変更時は対象パネルの `z_index` を `max(z_index) + 1` に更新するのみ
- `draw()` / `layout()` では `z_index` 昇順でソートしたインデックス列を使って描画順を制御する
- `diff()` の実装は `multi_split.rs` と同じ `tree.diff_children(&self.panels)` で良い

architecture.md §2・§7・§5・§12 および implementation-plan.md の `FloatingPane` 定義・`FloatingPanes` 構造体フィールドに反映済み。

---

### Q6. popout ウィンドウ内の FloatingPanes のカメラ初期状態 ✅ 決定: 案 A

**決定**: popout は独自 `Camera`（デフォルト: `zoom=1.0, pan=(0,0)`）を持つ。
popout は通常 1 パネルなのでズーム・パンを独立させた方が操作感が自然。

---

## 着手後に決めれば良い事項

- パネルのスナップグリッド（等間隔吸着）の必要性
- キーボードショートカットでのパネル間フォーカス移動（`Tab` / `Shift+Tab`）
- パネルの最小化（タイトルバーのみ表示）機能
- ズーム倍率・パン位置のリセットショートカット（`Ctrl+0`）
- 「全パネルをビューに収める」ショートカット（`Ctrl+Shift+H` 相当）← Q3 の補完機能
- パネル追加時のアニメーション（フェードイン）
- 複数パネル選択・一括移動の必要性
