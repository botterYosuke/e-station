# Floating Windows 計画書 レビュー修正ログ

## ラウンド 1（2026-04-29）

### 統一決定

1. Camera.pan 型分割: `data::Camera` は `pan: (f32, f32)`、ウィジェット内 Camera は `pan: iced::Point`（非Serialize）
2. z_order フィールド削除: FloatingPanes 構造体から削除。z 順は FloatingPane.z_index で管理
3. From実装に camera 追記: `camera: dashboard.camera` を追加
4. ok_or_default の camera 適用: `data::Dashboard.camera` に `#[serde(default)]` を追加
5. popout Camera 永続化: `data::Dashboard.popout` を `Vec<(Vec<FloatingPaneData>, WindowSpec, Camera)>` に変更
6. z_index オーバーフロー対策: `max+1` が閾値超えで 0..N 再正規化を仕様化
7. Widget::overlay(): `overlay::from_children` 呼び出しを architecture.md §5 に追記
8. フォーカス消失リセット: Unfocused/CursorLeft でも drag/resize/pan をリセット
9. link_group_button: 「構造体フィールド」→「関数引数とクロージャ境界」に修正
10. Phase 6 テスト観測点: ファイル名・コマンド・assert 内容を明記

### Findings 一覧

| Finding ID | 観点 | 対象ファイル | 問題概要 | 優先度 | 修正概要 |
|---|---|---|---|---|---|
| A1 | A | implementation-plan.md:256–258 | FloatingPane に z_index なし | HIGH | z_index: u32 を追加 |
| A2/C5 | A/C | implementation-plan.md:487–499, architecture.md:§9 | From実装に camera なし | HIGH | camera: dashboard.camera を追記 |
| A3 | A | implementation-plan.md:159–165 | CursorMoved/drag中に on_move 発行と誤記（Q1と矛盾） | HIGH | 内部 rect 更新のみに修正 |
| A4 | A | implementation-plan.md:35–57 | z_order フィールドが z_index 方式と齟齬 | MEDIUM | フィールド削除 |
| A5/B5 | A/B | implementation-plan.md:97–98, architecture.md:§4 | Camera.pan 型不一致（iced::Point vs (f32,f32)） | MEDIUM | 型分割を明記 |
| A6 | A | open-questions.md:Q5 | implementation-plan.md への反映漏れ未記載 | MEDIUM | 注記を更新 |
| A7 | A | README.md:26 | open-questions 説明が「未解決事項」のまま | LOW | README.md を更新（※別対応） |
| B1 | B | implementation-plan.md:392 | link_group_button を構造体フィールドとして誤記 | HIGH | 関数引数・クロージャ境界に修正 |
| B2 | B | implementation-plan.md:339 | view() 変更前シグネチャに panes: usize 欠落 | HIGH | 追記 |
| B3 | B | implementation-plan.md:414 | focus 参照が5箇所と誤記（実際は6箇所） | HIGH | main.rs:2988–2990 を追加 |
| B4 | B | architecture.md:§5 | diff() 記述がルーラー要素混在と誤解されやすい | MEDIUM | 「ルーラー要素含まず」と明示 |
| B6 | B | implementation-plan.md:339 | 行番号参照 pane.rs:539 が陳腐化 | LOW | シンボル名参照に置換 |
| C1 | C | architecture.md:§9 | camera への ok_or_default 未記載 | HIGH | serde(default) を追記 |
| C2 | C | architecture.md:§2,§6 | popout Camera 永続化スキーマ未規定 | HIGH | popout 型に Camera を追加 |
| C3 | C | architecture.md:§2 | z_index u32 オーバーフロー境界条件未定義 | HIGH | 再正規化仕様を追記 |
| C4 | C | architecture.md:§5 | フォーカス消失時のリセット経路未記載 | MEDIUM | イベント表に追記 |
| C6 | C | architecture.md:§5 | Widget::overlay() 実装方針未記載 | MEDIUM | overlay::from_children を追記 |
| C7 | C | architecture.md:§4 | zoom_at clamp後のfactor補足なし | LOW | 補足を追記（任意） |
| D-01 | D | implementation-plan.md:755–761 | Phase 6 テストにファイル名・コマンド・assert未記載 | HIGH | 観測点を追記 |
| D-02 | D | implementation-plan.md | Vec順序固定のpin testなし | HIGH | vec_order_stable_on_focus_change を追加 |
| D-03 | D | implementation-plan.md | Camera変換ユニットテストなし | HIGH | camera_roundtrip を追加 |
| D-04 | D | implementation-plan.md | 旧フォーマット変換テストなし | HIGH | legacy_pane_format_falls_back_to_empty を追加 |
| D-05 | D | implementation-plan.md | popout Camera独立性テストなし | MEDIUM | popout_camera_independent を追加 |
| D-06 | D | spec.md:59–65 | E2E smoke test 観測項目追加が計画に未記載 | MEDIUM | smoke.sh への追加を明記 |
| D-07 | D | implementation-plan.md | negative testなし | MEDIUM | close_nonexistent_pane_is_noop を追加 |

## ラウンド 2（2026-04-29）

### 統一決定

1. GUI 層 Dashboard.popout 型に Camera を追加: `HashMap<window::Id, (Vec<FloatingPane>, WindowSpec, Camera)>`
2. architecture.md §5 CursorMoved/drag 行に「on_move は発行しない」を明示追記
3. implementation-plan.md link_group_button 節に変更前シグネチャを追加
4. architecture.md §9 popout フィールドに `#[serde(default)]` を追記
5. implementation-plan.md Phase 5 にフレームレート検証観測項目を追記

### Findings 一覧

| Finding ID | 観点 | 対象ファイル | 問題概要 | 優先度 | 修正概要 |
|---|---|---|---|---|---|
| R2-A1 | A | architecture.md:§7, implementation-plan.md:287 | GUI 層 Dashboard.popout に Camera 欠落 | HIGH | Camera を追加 |
| R2-A2 | A | architecture.md:§5 イベント表 | CursorMoved/drag 行に「on_move は発行しない」が未記載 | MEDIUM | 追記 |
| R2-B1 | B | implementation-plan.md:398–413 | link_group_button 変更前シグネチャ未記載 | HIGH | 変更前コードブロックを追加 |
| C8 | C | architecture.md:§9 | popout への serde(default) 未記載 | MEDIUM | #[serde(default)] を追記 |
| C9 | C | architecture.md:§5 | overlay 全パネル集約方針の明示なし | LOW | 一文追記 |
| D-08 | D | implementation-plan.md:Phase 5 | フレームレート検証手順未記載 | MEDIUM | 観測項目を追記 |
| D-09 | D | implementation-plan.md:811–823 | vec_order_stable_on_focus_change テストが不完全擬似コード | LOW | 初期化コードを補完（次ラウンドで対応） |
