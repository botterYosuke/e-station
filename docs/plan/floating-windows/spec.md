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
