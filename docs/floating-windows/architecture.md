# Floating Windows 移行: アーキテクチャ

## 1. 目標構成

```
App State
  ├─ data::Dashboard  (= flowsurface_data::Dashboard、永続化モデル)
  │    ├─ schema_version: u32
  │    ├─ windows: Vec<FloatingPaneData>
  │    └─ camera: Camera
  │    （popout は本計画では永続化対象外。Q6 参照）
  ├─ GUI Dashboard  (= crate::screen::dashboard::Dashboard、ランタイム状態)
  │    ├─ windows: Vec<FloatingPane>
  │    ├─ focus: Option<PaneLocation>
  │    └─ popout: ...
  └─ Bevy Frontend
       ├─ Pane entities
       ├─ Camera entity
       ├─ Input systems
       └─ UI systems
```

注記: `PaneLocation` の具体型（main 単独 / main+popout / Bevy Window 複数）は Q1
解決後に確定する。Q1 解決前の Phase 4 着手は不可。

`data::Dashboard` の責務は永続化モデルに限定する。永続化フィールドは
`schema_version: u32` + `windows: Vec<FloatingPaneData>` + `camera: Camera` のみ。
旧 saved-state は破棄してデフォルトで起動する。popout の状態は本計画ではスコープ外。

`GUI Dashboard` は `crate::screen::dashboard::Dashboard` を完全修飾名で指す
（永続化モデルの `data::Dashboard` とは別物）。pane の意味論・メッセージ・
レイアウト同期を担当する。

## 2. 責務分離

- `data` クレート: 永続化モデル
- GUI State: pane の意味論、メッセージ、レイアウト同期
- Bevy Frontend: 描画、hit test、ドラッグ、リサイズ、ズーム、パン

## 3. Bevy 側の基本モデル

### Components

- `PaneId(uuid::Uuid)`
- `PaneRect(FloatRect)`
- `PaneZ(u32)`
- `PaneFocused`
- `PaneKind` — Heatmap / Kline / Ladder / TAS / Starter のいずれかを示す enum。レンダラ選択と pane 内 UI 構築の dispatch に使う。

### Resources

- `DashboardCamera`
- `PointerState`
- `DragState`
- `ResizeState`

座標系の前提: logical px、原点は top-left、Y 軸下向き。`DashboardCamera` は
world→screen の affine 変換を保持する。

## 4. イベント境界

- App → Bevy
  - pane 一覧の反映
  - focus 変更
  - camera 復元
- Bevy → App
  - `WindowMoved`
  - `WindowResized`
  - `WindowFocused`
  - `WindowClosed`
  - `WindowAdded`
  - `CameraChanged`

iced UI レイヤ（タイトルバー等）が先に hit test し、未消費の pointer/wheel
イベントのみ Bevy frontend に転送する。

### 4.5 ライフサイクル契約

**INV-CLOSE-1**: Bevy → App の `WindowClosed` を受信したとき、App 側は pane 種別に
応じた teardown を完了させてから data モデルから当該 pane を除去する。teardown
は次を含む。

- chart pane: aggregator の drop
- heatmap pane: heatmap buffer の drop
- replay pane: `replay_pane_registry` から該当 entry を解除
- 任意の pane: 関連する購読 stream の cancel

teardown が完了するまで `data::Dashboard.windows` から `FloatingPaneData` を
削除しない。teardown 失敗時はログに記録し、pane を「closing」状態のまま保持して
再試行可能にする。

teardown 実行規約:

- **逐次実行**: teardown は逐次実行する。並列 drop は禁止する。
- **順序**: 購読 stream cancel → aggregator drop → `replay_pane_registry` 解除 →
  data モデルから当該 pane を除去、の順に実行する。
- **タイムアウト**: 各リソースの drop に 5s のタイムアウトを設ける。タイムアウト
  したリソースはログに記録し、pane を closing 状態のまま保持する。
- **input 遮断**: closing 中の pane は input（pointer / wheel / keyboard）を
  受け付けない。Bevy 側 hit test も closing pane を除外する。

## 5. popout

Phase 6 までは機能維持を前提とする。Phase 6 以降のスコープは open-questions Q6 で確定する。

- popout は機能を維持する
- 内部表現は Bevy frontend に合わせて更新してよい
- main window と独立した `Camera` を持てるようにする
- popout は main world と独立した focus / z-stack / `Camera` を持つ。イベントは popout window 内に閉じる
- popout の永続化は本計画ではスコープ外（Phase 6 まで非永続）

## 5.5 wgpu 共存ポリシー

iced 0.14 は wgpu 27、Bevy 0.15/0.16 は wgpu 23/24 を使う。両者を同一プロセスで
共存させられるかは Phase 2 spike（wgpu 共存性 PoC を含む）で判定する。

判定が NG の場合の選択肢を Q1 と合わせて検討する:

- (a) Bevy をオフスクリーン render → iced texture として表示
- (b) iced を Bevy `egui_inspector` 等に置換
- (c) Bevy 側 wgpu surface に統一し iced を捨てる

(c) を採用する場合、modal / settings / tachibana ログイン UI など iced 依存箇所の
再実装が必要となり、本計画は実質リセットとなる。Phase 2 PoC で (c) 必至と判定
された段階で計画書を再起票する。

## 6. 移行原則

- 先に状態モデルを `pane_grid` から切り離す
- 次に Bevy frontend を並走導入する
- 最後に旧 `iced` dashboard 表示を除去する
