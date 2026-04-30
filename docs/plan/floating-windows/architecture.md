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
- Bevy Frontend: **layout shell**（pane 矩形管理 / hit test / ドラッグ / リサイズ / z-order / canvas
  パン・ズーム）と **高頻度描画面**（pane chrome の描画、chart surface の描画 host）
- iced Frontend（残置）: 設定 modal / indicator picker / study configurator / 認証ダイアログ /
  Tachibana ログイン UI / ダッシュボード起動前の Starter 画面 / 管理画面。**これらは
  本計画ではスコープ外**。Bevy 化したい場合は **別計画として起票が必要**

> 計画の境界を「Bevy へ全面移行」と読まないこと。本計画は **layout shell + chart surface** の
> Bevy 化に限定する。modal や認証 UI を Bevy へ移すには別計画の起票が必要。

## 3. Bevy 側の基本モデル

### Components

- `PaneId(uuid::Uuid)`
- `PaneRect(FloatRect)`
- `PaneZ(u32)`
- `PaneFocused`
- `PaneKind` — Heatmap / Kline / Ladder / TAS / Starter / Comparison chart などのいずれかを示す enum。レンダラ選択と pane 内 UI 構築の dispatch に使う（Comparison chart バリアントも含む）。

### Resources

- `DashboardCamera`
- `PointerState`
- `DragState`
- `ResizeState`

座標系の前提: logical px、原点は top-left、Y 軸下向き。`DashboardCamera` は
world→screen の affine 変換を保持する。

## 3.5 pane 種別ごとのレンダリング分類

各 pane 種別を **3 分類** のいずれかに割り当てる。**Phase 2 終了時点で全 pane 種別の分類を
確定する**（implementation-plan §2 Phase 2 deliverable）。Phase 3 着手時点で分類が未確定の
場合、Phase 3 を始めない。

| 分類 | 意味 | 入力ハンドリング |
|------|------|----------------|
| `Bevy native` | Bevy の renderer を使い、scene / pipeline は Bevy 側で記述する | pointer / wheel は Bevy が消費 |
| `host existing renderer` | Bevy pane 内に既存 wgpu / `iced::canvas` ベースの widget を host する。renderer は Bevy が所有する surface or texture に書き出す | pane 内 pointer は Bevy hit test → host renderer に委譲 |
| `keep iced overlay` | Bevy pane の上に iced ウィジェットを overlay として残す（modal / picker / configurator 等の一時 UI） | iced overlay が hit test で先取り、未消費分のみ Bevy へ |

暫定割当（Phase 2 spike で確定）:

- Heatmap pane: 候補 `host existing renderer`（既存 GPU pipeline `src/widget/chart/heatmap.rs:355` の `OverlayCanvas` を温存）
- Kline pane: 候補 `host existing renderer`（per-frame 描画・crosshair・study 反映を温存、`src/chart/kline.rs:49`（`impl Chart for KlineChart`）/ `src/chart/kline.rs:889`（`fn draw`））
- Comparison chart pane: 候補 `host existing renderer`（既存実装 `src/widget/chart/comparison.rs` / `src/chart/comparison.rs` を温存）
- Ladder pane: 候補 `Bevy native`（描画頻度が低く、テキスト中心で再実装コストが小さい場合）
- TAS / Starter pane: 候補 `Bevy native`
- 設定 modal / indicator picker / study configurator / 認証 / Tachibana ログイン: 確定 `keep iced overlay`

> Q2（pane 内容の描画責務）と Q3（設定 UI の移植順）は本分類のフレームに沿って解決する。
> 抽象論のままにすると Phase 3 の状態移行後に責務分解をやり直すリスクがあるため、
> Phase 2 終了時点で各 pane 種別を上表に確定すること。

> **UD14 注記**: `host existing renderer` 分類は **Q1=(a) 同一 wgpu device 共存前提**。
> Q1=(b)/(c) の場合は当該 pane の Bevy native 再実装 or オフスクリーン render→texture
> 経由が必要。

**3 分類の CI pin**: Phase 2 で `data::PaneKind::renderer_class() -> RendererClass { BevyNative | HostExisting | KeepIcedOverlay }` を導入し、unit test で全バリアントの値を assert する。

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

### 4.1 入力境界契約（hit test 優先順位）

ハイブリッド構成の入力境界を明示する。Phase 4 着手前にこの契約を決定済みとし、
実装段階で境界が崩れないようにする。

pointer / wheel イベントの hit test 優先順位（**上から順に消費**、消費されなければ次へ）:

1. **iced overlay**（一時 UI）— modal / indicator picker / study configurator /
   認証ダイアログ / Tachibana ログイン UI / context menu。表示中はこれらが最優先で
   pointer を消費する
2. **Bevy pane chrome** — pane タイトルバー / close ボタン / リサイズハンドル /
   ドラッグハンドル
3. **Bevy chart surface（pointer capture 領域）** — pane 内 chart の crosshair 追従 /
   ホイールズーム / 右クリックメニュートリガ。`host existing renderer` 分類の pane では
   Bevy が hit test し、消費判定後に host renderer へ委譲する
4. **Bevy canvas**（pane 外の空白領域）— canvas パン・ホイールズーム

入力契約の不変条件:

- **INV-INPUT-1**: iced overlay 表示中は Bevy のドラッグ / リサイズ / canvas パン操作を
  受け付けない（同一フレームでの二重消費を禁止）
- **INV-INPUT-2**: closing 中の pane（INV-CLOSE-1）は hit test から除外する
- **INV-INPUT-3**: pane 内 chart surface は Bevy が pointer capture を取得した後、
  host renderer に座標系変換済みのイベントを渡す（host が iced::canvas の場合は
  iced 座標系へ）
- **INV-INPUT-4**: 設定 modal / picker / configurator は **本計画ではスコープ外**。
  Bevy 化したい場合は **別計画として起票が必要**
- **INV-INPUT-5**: keyboard focus / Esc / Tab — iced overlay 表示中は overlay が独占。
  Bevy ペーン側のキーボードショートカットは抑制する
- **INV-INPUT-6**: drag dead zone 5px、wheel modifier 表（Ctrl+wheel=ズーム /
  wheel only=scroll / Shift+wheel=水平スクロール — Phase 4 で確定）
- **INV-INPUT-7**: context menu hit test は iced overlay 層で発火する
  （右クリックは overlay → Bevy chart の順）
- **INV-INPUT-8**: touch / tablet pen は MVP 非対応（Phase 6 まで non-goal）

> 計画策定時点の主な iced 一時 UI の参照位置: `src/modal/pane/settings.rs:744`（`mod study`）
> / `src/modal/pane/indicators.rs:11`（`fn view`）。これらは Phase 5 でも `keep iced overlay` 扱いとする。

### 4.2 ライフサイクル契約

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

**closing 状態の single source of truth は GUI Dashboard（`crate::screen::dashboard::Dashboard`）の
ランタイム状態**として保持する。`data::Dashboard` 永続化モデルには含めない
（`#[serde(skip)]` または GUI 側のみのフィールド）。Bevy hit test は毎フレーム
GUI Dashboard の closing 状態を参照する。Bevy 側で closing 状態を二重に保持しては
ならない。Phase 1 データモデルへの影響なし（Phase 1 は永続化フィールドのみ扱う）。

**auto-fail / 再試行 UI**: teardown timeout（5s）後の auto-fail（強制 close）と
再試行 UI を経路として用意する。**closing 状態はランタイム限定（GUI Dashboard 側）で
`data::Dashboard` の永続化フィールドには含めない**（永続化されない）。

teardown 実行規約:

- **逐次実行**: teardown は逐次実行する。並列 drop は禁止する。
- **順序**: 購読 stream cancel → aggregator drop → `replay_pane_registry` 解除 →
  data モデルから当該 pane を除去、の順に実行する。
- **タイムアウト**: 各リソースの drop に 5s のタイムアウトを設ける。タイムアウト
  したリソースはログに記録し、pane を closing 状態のまま保持する。タイムアウト
  超過分は auto-fail 経路で強制 close するか、再試行 UI から再度 teardown を試行する。
- **input 遮断**: closing 中の pane は input（pointer / wheel / keyboard）を
  受け付けない。Bevy 側 hit test も `data::Dashboard` の closing 状態を参照して
  当該 pane を除外する。

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
