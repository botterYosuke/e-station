---
title: Floating Windows 移行 — 原典 spec / architecture / README（参考保存）
status: archived-reference
migrated_from:
  - docs/plan/floating-windows/README.md
  - docs/plan/floating-windows/spec.md
  - docs/plan/floating-windows/architecture.md
source_commit: 5c15ccd
---

> このファイルは旧 `docs/plan/floating-windows/` 配下の `README.md` / `spec.md` /
> `architecture.md` を 1 ファイルに連結した参考保存版である。最新の作業計画は
> 同階層の `implementation-plan.md` / `open-questions.md` を参照のこと。
> 本ドキュメントを更新しても運用には反映されない。

---

## A. README（旧 `docs/plan/floating-windows/README.md`）

# Floating Windows 移行計画

## 何をするか

`iced::widget::PaneGrid` ベースの dashboard レイアウトをやめて、
**フローティング pane の layout shell（配置・hit test・z-order・canvas パン/ズーム）と
高頻度描画面（chart surface）に限って** `Bevy` を導入する。

目的は、メインウィンドウ内で pane を任意位置・任意サイズで扱える
フローティングレイアウトへ移行すること。

OS レベルの別ウィンドウである popout は維持する。

## 方針

- `pane_grid` の代替を 1 つだけ差し替えるのではなく、dashboard の **layout shell** を Bevy で再構成する
- **Bevy 化対象は layout shell と高頻度描画面に限定する**:
  - 含む: pane フローティング配置 / hit test / z-order / canvas パン・ズーム / pane 内 chart surface（描画 + pointer capture）
  - 含まない: 設定 modal / indicator picker / study configurator / 認証 / Tachibana ログイン UI / 管理画面は **本計画ではスコープ外**。Bevy 化したい場合は **別計画として起票が必要**
- **非 dashboard の modal / 認証 / 管理画面は原則 iced を維持する**。これらは Phase 5 でも Bevy 化しない（設定 modal / indicator picker / study configurator / 認証 / Tachibana ログイン UI / 管理画面は **本計画ではスコープ外**。Bevy 化したい場合は **別計画として起票が必要**）
- レイアウト永続化モデルは frontend 非依存に保つ
- `pane_grid` 依存は段階的に剥がす
- 旧 `iced` 案は `archive/2026-04-29-pre-bevy-rewrite/` に退避した
  - 退避理由: iced `PaneGrid` ではフローティング配置（任意位置・任意サイズの重なり）と
    canvas 全体のズーム/パンを満たせないため、frontend を Bevy へ転換した
  - 旧計画は split 木前提で組まれており、フローティング前提の本計画とはデータモデル自体が異なる

## ゴール

| 変更前 | 変更後 |
|--------|--------|
| `pane_grid::State<pane::State>` | `Vec<FloatingPane>` + `Bevy ECS` |
| `pane_grid::Pane` を識別子に使用 | `uuid::Uuid` を識別子に使用 |
| スプリット前提の UI | フローティング pane + canvas 操作 |
| 永続化モデル: `pane: Pane`（split 木）+ `popout: Vec<(Pane, WindowSpec)>` | 永続化モデル: `windows: Vec<FloatingPaneData>` + `Camera` + `schema_version: u32`（popout 永続化は Phase 6 までスコープ外） |

## 文書構成（旧）

- `spec.md` — スコープ・要件・完了条件（§6 機能保持マトリクスを含む）
- `architecture.md` — Bevy 本線の構成案
- `implementation-plan.md` — 実装順序と変更対象
- `open-questions.md` — 未確定事項

## 実装フェーズ概要

| Phase | 内容 |
|-------|------|
| **Phase 1** | `FloatRect` / `FloatingPaneData` / `Camera` をデータモデルに追加 |
| **Phase 2** | Bevy Spike を作り、ドラッグ・リサイズ・ズーム・パン・focus を確認 |
| **Phase 3** | GUI 状態を `uuid::Uuid` / `Vec<FloatingPane>` ベースへ移行 |
| **Phase 4** | Bevy frontend を dashboard に接続し、`pane_grid` 直結コードを除去 |
| **Phase 5** | pane 内容・タイトルバー・追加 UI を Bevy 側へ移行（**設定 modal / indicator picker / study configurator / 認証ダイアログ / Tachibana ログイン UI / 管理画面は iced overlay のまま維持**）。機能保持マトリクス（spec §6）を満たすこと。`tests/manual/floating-windows-CHECKLIST.md` を成果物として PR に添付 |
| **Phase 6** | テスト追加・旧依存削除・互換確認。初回起動で旧 saved-state 検知時に一度だけ通知ログ + CHANGELOG 注意書きを出す |

## 関連計画

| 計画 | 関係 |
|------|------|
| `../specs/data-engine/` | IPC・エンジン側への影響は基本なし |
| `../specs/backtest/` | pane 追加 API の変更に追随が必要（引き取り境界: Phase 4 で本計画側が pane 追加 API を確定した直後に nautilus_trader 側担当者が追随する） |
| `../specs/order/` | pane id 型変更（`pane_grid::Pane` → `uuid::Uuid`）による Modal 経路の追随が必要 |
| `../specs/venues/tachibana/` | pane id 型変更（`pane_grid::Pane` → `uuid::Uuid`）による Modal 経路の追随が必要 |

---

## B. spec（旧 `docs/plan/floating-windows/spec.md`）

# Floating Windows 移行: 仕様

## 1. ゴール

1. Bevy ベースの **layout shell** で pane を任意位置・任意サイズに配置できる
2. スクロールホイールでズーム、空白ドラッグでパンできる
3. Heatmap / Kline / Ladder / TAS / Starter など既存 pane の **機能を保持したまま** Bevy layout shell 上で動作させる（§6 機能保持マトリクスを満たす）
4. popout を維持する（Phase 6 までスコープ外・非永続）
5. `saved-state.json` の旧フォーマットは破棄してデフォルトレイアウトで起動する
6. **設定 modal / indicator picker / study configurator / 認証ダイアログ / Tachibana ログイン UI は iced のまま残す**。これらは **本計画ではスコープ外**。Bevy 化したい場合は **別計画として起票が必要**（spec §3 含めないもの・参照）

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
- acceptance（INV-REPLAY 系）:
  - **INV-REPLAY-1**: 起動時 `replay_pane_registry` は `windows` と 1:1 対応する
  - **INV-REPLAY-2**: pane 種別変更時は unregister → register が atomic に行われる
  - **INV-REPLAY-3**: replay モード起動直後の registry 構築は `windows` 構築完了後に行う
- ゴール: 状態が `pane_grid` から独立する

### Phase 4 — Bevy frontend 接続

**Q1（wgpu 共存性）が Phase 2 で解決していることが前提条件**。

- Bevy 側で pane entity / camera / hit test / z-order を実装
- dashboard から Bevy frontend を起動・更新できるようにする
- `main.rs` の `dashboard.panes.split()` 直接呼び出しを除去
- pane 内容は **placeholder（pane id と種別ラベルのみ）**。実コンテンツ移行は Phase 5
- ゴール: アプリ上で pane の移動・クローズ・ズーム・パンが動く

### Phase 5 — コンテンツ移行

- pane タイトルバー UI（Bevy 側で実装）
- pane 追加 UI（Bevy 側で実装）
- 既存 pane 種別の **chart surface** を Bevy host 上で動作させる（§6 機能保持マトリクスに従う）
- **設定 modal / indicator picker / study configurator は iced overlay のまま維持** する
  （Bevy が pointer を消費しない領域で iced ウィジェットを上に重ねる。**architecture §4.1 入力境界契約**を参照）。Bevy 化は **本計画ではスコープ外**。希望する場合は **別計画として起票が必要**
- ゴール: 既存 pane の **機能（操作・設定変更・表示）が現状同等** であること。
  「表示できるか」ではなく §6 機能保持マトリクスの全項目を pass することが完了条件。
- 成果物: `tests/manual/floating-windows-CHECKLIST.md` を Phase 5 完了 PR に **チェック済み証跡として添付** する

### Phase 6 — テストとクリーンアップ

- roundtrip テスト
- layout 変換テスト
- `pane_grid` import の全削除（`Grep "panes.split("` 全ヒット（現状 8 箇所、内訳: `main.rs:2538` (`OpenOrderPanel` handler 1 件) + `dashboard.rs` 7 件（`update(SplitPane)` / `fn merge_pane` / `fn split_pane` / `fn replace_new_pane` / 自動生成 split for OrderList / BuyingPower 等）。詳細内訳は impl §3 を参照）/ `pane_grid::Pane` 実コード ~39 箇所 / 6 ファイル）
- `saved-state.json` 互換確認: `tests/fixtures/saved-state-legacy-*.json` を 2 種
  （pane_grid 単段 / popout あり）置き、`Dashboard::deserialize` が `windows: vec![]`
  で fallback することを assert する
- popout が main と独立した Camera / z-stack で動くことを確認する（または non-goal と
  して確定させる）。永続化はスコープ外
- e2e smoke 観測項目（追加観測点）:
  1. `floating windows: dashboard_loaded uuids=N` ログが存在する
  2. `camera saved zoom=` が観測ウィンドウ中 1 回以上出る
  3. `pane_grid` 文字列が `flowsurface-current.log` に出現しない

  これらは Rust GUI 側の `tracing::info!` で出力する。**target = `flowsurface::floating_windows` / level = INFO** とし、grep は **target フィルタ後** に行う。
- acceptance（旧 saved-state 周知）: 初回起動で旧 saved-state を検知したら **一度だけ通知ログ**（target = `flowsurface::floating_windows`, level = INFO）を出力し、README / CHANGELOG に注意書きを追加する。**「一度だけ」の保証は `%APPDATA%\flowsurface\.legacy-notified-v1` 別ファイルフラグで行う**（`saved-state.json` とは独立した別ファイル。flag 存在で 2 度目以降の通知ログ出力を抑止する）

## 3. 含めないもの

- タブ化
- スナップグリッド
- 派手なアニメーション
- 高度なキーボードナビゲーション
- popout の永続化（Phase 6 までスコープ外。非永続で main と独立した Camera / z-stack
  を持たせるに留める）。**本計画ではスコープ外**。永続化したい場合は **別計画として起票が必要**
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
| NF4 | 旧 `saved-state.json` でクラッシュしない（互換 deserialize は試みず、`schema_version: u32` の不一致または不在を検知したら破棄して default レイアウトで起動する。バンプ規則: 後方互換ありフィールド追加は serde `#[serde(default)]` で吸収しバンプしない / 破壊変更時のみ +1 / Phase 1 を v1 とする / version 不在 or 最新より小は破棄してデフォルト起動する / **`schema_version > 自分が知る最大値` も破棄して default 起動**する） |
| NF5 | レイアウトモデルは frontend 非依存を保つ |
| NF6 | pane の可視矩形は viewport と最低 64 px × 64 px 重なる（`Camera` 復元時に clamp する） |
| NF7 | popout は main と独立した focus / z-stack / `Camera` を持つ（Phase 6 までスコープ外、非永続） |

## 6. 機能保持マトリクス（Phase 5 acceptance）

Phase 5 完了条件は「表示が崩れない」ではなく、以下の各項目が現状同等に動作することとする。
各項目は手動確認 + 可能なものは unit/integration test を追加する。
（参照される現行実装位置は計画策定時点のもので、実装移行に伴い変動しうる。）

### 6.1 共通（全 pane 種別）

| ID | 機能 | 現行参照 | 検証方法 |
|----|------|---------|---------|
| C1 | pane の追加 / 削除 / focus / 最前面化 | `src/screen/dashboard.rs` | F1〜F7（spec §4） |
| C2 | pane タイトルバーからの close（teardown 順序） | INV-CLOSE-1（spec §4 F6） | unit test：spec §2 Phase 4 acceptance |
| C3 | settings modal の開閉と適用（**iced overlay として残す**。Bevy 化は **本計画ではスコープ外**、希望する場合は **別計画として起票が必要**） | `src/modal/pane/settings.rs` | 手動：modal 表示・値変更・反映 |
| C4 | indicator picker（**iced overlay として残す**。Bevy 化は **本計画ではスコープ外**、希望する場合は **別計画として起票が必要**） | `src/modal/pane/indicators.rs:11 fn view` | 手動：picker から indicator 追加・削除 |
| C5 | input 境界契約（INV-INPUT-5/6/7）※ INV-INPUT-8（touch / tablet pen）は MVP non-goal のため検証対象外 | architecture §4.1 入力境界契約（spec から referenced のみ。spec で重複定義しない） | architecture 側で扱う（INV-INPUT-8 は MVP non-goal のため検証対象外） |

### 6.2 Kline pane

| ID | 機能 | 現行参照 | 検証方法 |
|----|------|---------|---------|
| K1 | per-frame 描画 / crosshair / study 反映 | `src/chart/kline.rs:49 impl Chart for KlineChart` / `src/chart/kline.rs:889 fn draw` | 手動：Kline pane を 1 つ開き、操作中に crosshair が追従し study が描画される |
| K2 | overlay marker の配信（pane 経由） | `src/main.rs:2096 Message::ExecutionMarkerReceived`、`src/screen/dashboard/pane.rs:197 push_execution_marker` | 手動：marker source ありで marker が描画される |
| K3 | indicator の追加 / 削除 / 並べ替え | `src/modal/pane/indicators.rs:63 fn selected_list` | 手動：indicator を 2 つ以上追加・並べ替え・削除 |
| K4 | 詳細設定（footprint cluster / scaling / studies） + `Sync all` | `src/modal/pane/settings.rs:575 fn kline_cfg_view` | 手動：設定変更後 `Sync all` で他 Kline pane に反映 |

### 6.3 Heatmap pane

| ID | 機能 | 現行参照 | 検証方法 |
|----|------|---------|---------|
| H1 | 専用 scene / pipeline での描画（GPU 寄り） | `src/widget/chart/heatmap.rs:355 OverlayCanvas` | 手動：heatmap が現状同等の解像度・FPS で描画される |
| H2 | 詳細設定 + `Sync all` | `src/modal/pane/settings.rs` | 手動：設定変更が反映される |

### 6.4 Ladder pane

| ID | 機能 | 現行参照 | 検証方法 |
|----|------|---------|---------|
| L1 | 板表示の更新 | `src/screen/dashboard/pane.rs` | 手動：取引所接続中に板が更新される |
| L2 | 詳細設定 + `Sync all` | `src/modal/pane/settings.rs` | 手動：設定反映 |

### 6.5 TAS / Starter pane

| ID | 機能 | 検証方法 |
|----|------|---------|
| T1 | TAS の流入更新（trade 流入） | 手動：TAS pane で trade が流れる |
| T2 | ticker 切替時の clear-on-symbol-change | unit test：`tas_clears_on_symbol_change` + 手動：ticker を切り替えると過去 ticker の trade が残らない |
| T3 | 上限間引き（バッファ上限超過時の drop / coalesce） | unit test：`tas_buffer_drops_oldest_on_overflow` + 手動：高頻度 trade で UI がスタックせず古い行が間引かれる |
| S1 | Starter からの pane 起動経路 | 手動：Starter から各 pane を 1 つずつ起動 |
| S2 | ticker picker + recent | 手動：picker から銘柄選択 / recent 一覧から再選択 |
| S3 | 検索フィルタ | 手動：picker の検索ボックスで部分一致絞り込み |

### 6.6 Comparison chart pane

実在確認済み: `src/widget/chart/comparison.rs` / `src/chart/comparison.rs`

| ID | 機能 | 検証方法 |
|----|------|---------|
| CMP1 | series の追加・削除 | unit test：`comparison_series_add_remove_roundtrip` + 手動：comparison pane に series を 2 つ以上追加し、削除できる |
| CMP2 | 設定 modal | 手動：設定 modal を開き値変更が反映される |

### 6.7 popout 経路

**Phase 6 までスコープ外（non-goal）**。永続化はしない。実装する場合は以下を満たす：

| ID | 機能 | 検証方法 |
|----|------|---------|
| P1 | popout 起動経路 | 手動：pane を popout として独立ウィンドウに切り出せる |
| P2 | main と独立した focus / z-stack | 手動：popout 内 focus が main 側 focus に干渉しない |
| P3 | main と独立した `Camera` | 手動：popout の zoom/pan が main に影響しない |
| P4 | popout 側 pane でも該当する K/H/L/T/CMP/S 項目を満たす | 手動：popout した各 pane 種別について §6.2（Kline）〜§6.6（Comparison）（すなわち Kline / Heatmap / Ladder / TAS / Starter / Comparison）を確認 |
| P5 | popout pane close で INV-CLOSE-1 teardown 4 ステップが log で順に観測される | 手動 + log 検査：popout pane を close したとき、`購読 cancel → aggregator drop → replay_pane_registry 解除 → data モデル除去` の 4 ステップが target = `flowsurface::floating_windows` の log にこの順序で出力される |
| P6 | replay モードで popout pane が main 側 registry を壊さず個別 unregister される | 手動 + log 検査：replay モードで popout pane が `replay_pane_registry` に独立 key（`PaneLocation::Popout(window::Id, Uuid)`）で登録され、popout close 時に main 側 registry のエントリを削除せず、対応する Popout key のみを unregister する |

### 6.8 acceptance ルール

- 上記 C1〜CMP2（および popout を実装する場合は P1〜P6）のいずれかが Phase 5 終了時点で現状から劣化した場合、Phase 5 は完了させない
- 「現状同等」の判定は実機操作で行い、回帰した項目は GitHub Issue として起票する
- マトリクスに含まれない機能（例: 新規 pane 種別）は本計画のスコープ外
- 成果物として `tests/manual/floating-windows-CHECKLIST.md` を Phase 5 完了 PR に **チェック済み証跡として添付** する
- `tests/manual/floating-windows-CHECKLIST.md` は §6.1〜§6.7 全 ID（C1〜C5 / K1〜K4 / H1〜H2 / L1〜L2 / T1〜T3 / S1〜S3 / CMP1〜CMP2 / P1〜P6）を **1 行ずつ** 含み、各行は `[ ] OS=Win/macOS/Linux いずれかの実機 / 操作手順 / 期待結果 / 観測結果` の **4 列** を持つ

---

## C. architecture（旧 `docs/plan/floating-windows/architecture.md`）

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
