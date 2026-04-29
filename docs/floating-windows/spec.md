# Floating Windows 移行: 仕様

## 1. ゴール

1. Bevy ベースの dashboard で pane を任意位置・任意サイズに配置できる
2. スクロールホイールでズーム、空白ドラッグでパンできる
3. Heatmap / Kline / Ladder / TAS / Starter など既存コンテンツを移行する
4. popout を維持する（Phase 6 までスコープ外・非永続）
5. `saved-state.json` の旧フォーマットは破棄してデフォルトレイアウトで起動する

### 座標系・単位系

座標は **logical px**（HiDPI スケール後の論理ピクセル）、原点は **top-left**、Y 軸は
**下向き**。`Camera` は world 座標 → screen 座標への **affine 変換**（translation + uniform
scale）として定義する。回転・剪断は持たない。

DPR 値は永続化しない。`saved-state` の座標は保存時の logical px のまま、復元時は NF6
の viewport clamp で吸収する。

## 2. スコープ

### Phase 1 — データモデル更新（`data::layout::Dashboard`）

ここで言う `Dashboard` は永続化用の `data::layout::Dashboard` を指す（GUI state である
`crate::screen::dashboard::Dashboard` とは別物）。

- `FloatRect` を追加
- `FloatingPaneData` を追加
- `Camera` を追加
- `data::layout::Dashboard` 永続化モデルを `windows: Vec<FloatingPaneData>` ベースへ変更
- `schema_version: u32` を導入
- 現行 `pane: Pane`（split 木）→ `windows: Vec<FloatingPaneData>` への移行は best-effort
  せず、旧フォーマットは破棄して default fallback で吸収する
- ゴール: `cargo test -p data` が通り、最低以下の test 関数が green になる:
  - `floatrect_rejects_negative_size`
  - `floating_pane_data_serde_roundtrip`
  - `camera_zoom_clamped`
  - `dashboard_legacy_pane_grid_falls_back_to_default`

### Phase 2 — Bevy Spike

- `bevy` 依存を追加
- 検証用バイナリで 1 pane のドラッグ・8 方向リサイズ・ズーム・パンを実装
- focus / z-order / 最小サイズを確認
- **wgpu 共存性 PoC** を含む（iced 0.14 + wgpu 27 と Bevy が同一プロセスで wgpu を共存
  させられるかを実機で確認する）
- 合否観測値:
  - 最小サイズ: 120 × 80 px
  - focus 取得 pane の `PaneZ` が他 pane の最大値 +1 以上
  - ズーム範囲 0.25 〜 4.0
  - ホイール 1 ノッチで 1.1 倍
  - wgpu 共存可否（iced 0.14 + wgpu 27 と Bevy）
- ゴール: 最小プロトタイプが動き、Q1（wgpu 共存）の判定が出る。**Q1 解決まで Phase 4 へ
  進めない**

### Phase 3 — GUI 状態移行（`crate::screen::dashboard::Dashboard`）

ここで言う `Dashboard` は GUI state の `crate::screen::dashboard::Dashboard` を指す
（永続化型 `data::layout::Dashboard` とは別物）。

- `crate::screen::dashboard::Dashboard` を `Vec<FloatingPane>` ベースへ変更
- `pane_grid::Pane` を `uuid::Uuid` に置換
- focus 型は `Option<PaneLocation>` に抽象化（Q1 解決後に具体化）
- `WindowMoved` / `WindowResized` / `WindowFocused` / `WindowClosed` / `WindowAdded` /
  `CameraChanged` の 6 イベントを整備
- 各イベントに対する state 変化 assert を `src/screen/dashboard.rs` の
  `#[cfg(test)] mod tests` に追加。最低限:
  - `WindowClosed` → focus が次に高い z の pane へ移る
  - `WindowAdded` → 新 pane が最前面（`PaneZ` が最大）
  - `WindowFocused` → `PaneZ` が他 pane の最大値 +1
- `src/layout.rs` の変換を更新
- ゴール: 状態が `pane_grid` から独立する

### Phase 4 — Bevy frontend 接続

**Q1（wgpu 共存性）が Phase 2 で解決していることが前提条件**。

- Bevy 側で pane entity / camera / hit test / z-order を実装
- dashboard から Bevy frontend を起動・更新できるようにする
- `main.rs` の `dashboard.panes.split()` 直接呼び出しを除去
- pane 内容は **placeholder（pane id と種別ラベルのみ）**。実コンテンツ移行は Phase 5
- ゴール: アプリ上で pane の移動・クローズ・ズーム・パンが動く

### Phase 5 — コンテンツ移行

- pane タイトルバー UI
- pane 追加 UI
- 設定 UI / インジケーター UI
- 既存コンテンツ種別の表示移行
- ゴール: 既存コンテンツが Bevy dashboard 上で表示できる

### Phase 6 — テストとクリーンアップ

- roundtrip テスト
- layout 変換テスト
- `pane_grid` import の全削除（split() 6 箇所 / `pane_grid::Pane` ~39 箇所 / 6 ファイル）
- `saved-state.json` 互換確認: `tests/fixtures/saved-state-legacy-*.json` を 2 種
  （pane_grid 単段 / popout あり）置き、`Dashboard::deserialize` が `windows: vec![]`
  で fallback することを assert する
- popout が main と独立した Camera / z-stack で動くことを確認する（または non-goal と
  して確定させる）。永続化はスコープ外
- e2e smoke 観測項目（追加観測点）:
  1. `floating windows: dashboard_loaded uuids=N` ログが存在する
  2. `camera saved zoom=` が観測ウィンドウ中 1 回以上出る
  3. `pane_grid` 文字列が `flowsurface-current.log` に出現しない

  これらは Rust GUI 側の `tracing::info!` で出力する。

## 3. 含めないもの

- タブ化
- スナップグリッド
- 派手なアニメーション
- 高度なキーボードナビゲーション
- popout の永続化（Phase 6 までスコープ外。非永続で main と独立した Camera / z-stack
  を持たせるに留める）
- 旧 `saved-state.json` フォーマットの互換 deserialize（破棄してデフォルトレイアウトで
  起動する方針）

## 4. 機能要件

| ID | 要件 |
|----|------|
| F1 | pane をドラッグ移動できる |
| F2 | pane を 8 方向リサイズできる |
| F3 | カーソル中心ズームができる |
| F4 | 空白ドラッグまたは中ボタンでパンできる |
| F5 | クリックで focus と最前面化ができる |
| F6 | タイトルバーから pane を閉じられる（`INV-CLOSE-1`: クローズ時に pane が保持する購読・aggregator・`replay_pane_registry` 登録を解放してから data モデルから除去する。teardown は **逐次実行**、各リソース drop に **5s タイムアウト** を設ける。closing 中の pane は **input 不可**（クリック・ドラッグ無視）） |
| F7 | 新規 pane を追加できる |
| F8 | camera 状態を保存・復元できる |
| F9 | popout が継続動作する（main と独立した focus / z-stack / `Camera`、非永続） |
| F10 | dashboard frontend が `pane_grid` に依存しない |

## 5. 非機能要件

| ID | 要件 |
|----|------|
| NF1 | focus 中 pane の更新は毎フレーム、非 focus は間引き可能 |
| NF2 | ドラッグ中間状態は frontend ローカルで持ち、commit を絞る |
| NF3 | camera 更新コストは低く保つ |
| NF4 | 旧 `saved-state.json` でクラッシュしない（互換 deserialize は試みず、`schema_version: u32` の不一致または不在を検知したら破棄して default レイアウトで起動する。バンプ規則: 後方互換ありフィールド追加は serde `#[serde(default)]` で吸収しバンプしない / 破壊変更時のみ +1 / Phase 1 を v1 とする / version 不在 or 最新より小は破棄してデフォルト起動する） |
| NF5 | レイアウトモデルは frontend 非依存を保つ |
| NF6 | pane の可視矩形は viewport と最低 64 px × 64 px 重なる（`Camera` 復元時に clamp する） |
| NF7 | popout は main と独立した focus / z-stack / `Camera` を持つ（Phase 6 までスコープ外、非永続） |
