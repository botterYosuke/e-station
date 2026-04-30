# Floating Windows 移行: 実装計画

## 1. 実装順序

1. `data` モデルを先に確定する
2. Bevy Spike を別バイナリで作る（**wgpu 共存性 PoC を含む**）
3. GUI 状態とメッセージを `uuid::Uuid` ベースへ移す
4. Bevy frontend を dashboard に接続する（**Q1 解決まで着手不可**）
5. 既存 pane 内容を順に移植する
6. 旧 `pane_grid` 依存を削除する（popout サポートはこの Phase までスコープ外）

> 統一決定:
> - 旧 `saved-state.json` は破棄し、新形式に `schema_version: u32` を導入する。
> - popout（ウィンドウ分離）は Phase 6 までスコープ外とする。
> - focus 型は `Option<PaneLocation>` に抽象化する（旧 `Option<(window::Id, pane_grid::Pane)>` は廃止）。
> - 座標系は logical px、原点 top-left、Y 軸下向き。`Camera` は world→screen の affine 変換とする。

## 2. 主要変更対象

### Phase 1

- `data/src/layout/mod.rs`
- `data/src/layout/pane.rs`
- `data/src/layout/dashboard.rs`
- `data/tests/fixtures/saved-state-legacy-pane-grid-single.json`（新規追加: 旧 `pane_grid` 形式・単一 pane）
- `data/tests/fixtures/saved-state-legacy-with-popout.json`（新規追加: 旧 `pane_grid` 形式・popout 込み）
  - 両 fixture を読み込んで `Dashboard::default()` にフォールバックする挙動を Phase 1 で確立する。

### Phase 2

- `Cargo.toml`
- `src/bin/` または Bevy frontend 用モジュール（`flowsurface-bevy-spike` バイナリ）
- **wgpu 共存性 PoC**: iced（既存 wgpu バックエンド）と Bevy（wgpu）の同時稼働可能性を検証する。
  この PoC 結果が Q1 への入力となり、結論が出るまで Phase 4 へは進まない。
- **pane 種別レンダリング分類の確定**: architecture §3.5 の 3 分類
  （`Bevy native` / `host existing renderer` / `keep iced overlay`）に
  Heatmap / Kline / Ladder / TAS / Starter / Comparison chart / 設定 modal / indicator picker /
  study configurator / 認証 / Tachibana ログイン UI を割り当てて確定する。
  Phase 3 着手時点で分類が未確定の場合、Phase 3 を始めない（Q2 / Q3 はこのフレームで closing する）。
  分類確定の成果物として `data::PaneKind::renderer_class()` を導入し、全バリアントの
  返り値を unit test (`pane_kind_renderer_class_covers_all_variants`) で assert する。
- **iced::canvas host 困難リスクの実機判定**: iced::canvas は texture 単独書き出し標準 API
  を持たないため、Kline `host existing renderer` 不能リスクを Phase 2 spike で実機判定する。
  不能と判定された場合は Kline を `Bevy native` 再実装にスコープ変更する。
- **入力境界契約の文書化**: architecture §4.1 の優先順位（iced overlay > Bevy pane chrome >
  Bevy chart surface > Bevy canvas）と INV-INPUT-1〜4 を Phase 2 終了時点で確定し、
  Phase 4 着手時点で iced 残置 UI の一覧を appendix として追加する。

### Phase 3

> Phase 3 で置換対象となる現行フィールドは以下の 3 つ（シンボル名で明示）:
> - `Dashboard::panes: pane_grid::State<pane::State>`
> - `Dashboard::focus: Option<(window::Id, pane_grid::Pane)>`
> - `Dashboard::popout: HashMap<window::Id, (pane_grid::State<pane::State>, WindowSpec)>`
>
> これらを `uuid::Uuid` ベース ID および `Option<PaneLocation>` 型へ移行する。

- `src/screen/dashboard.rs`（`Dashboard::panes` / `Dashboard::focus` / `Dashboard::popout` を中心に改修）
- `src/screen/dashboard/pane.rs`（`pane::State` 構造体 / `pane::Message` enum）
- `src/screen/dashboard/replay_pane_registry.rs`（`ReplayPaneRegistry::register` / `::resolve` / `::unregister` の 3 箇所で `pane_grid::Pane` を参照）
- `src/layout.rs`（`SerializableLayout` / `Layout::from_dashboard` / `Layout::apply`）
- `src/main.rs`（`Flowsurface::update` の pane 関連分岐 / `Message` enum）
- `src/modal/pane/settings.rs`（`SettingsModal` の親 pane 特定）
- `src/modal/pane/indicators.rs`（`IndicatorsModal` の親 pane 特定）
- `src/widget.rs`（`pane_grid` 依存ヘルパ）

> 実コード上の `pane_grid::Pane` 参照は **約 39 箇所 / 6 ファイル** にわたる。

> 暫定型: Phase 3 で `enum PaneLocation { Main(Uuid), Popout(window::Id, Uuid) }` を導入する。Q1（wgpu 共存性）解決後に `BevyWindow(...)` バリアント追加候補とする（Phase 4 以降）。

> Acceptance: Phase 3 末で `PaneLocation` を pattern match する箇所（メッセージ payload / modal の親特定 / `replay_pane_registry` の key 等）を `Grep` で列挙し、Q1 解決後の再 touch 範囲を確定する。

### Phase 4-5

- `src/bevy_dashboard/` または同等の新規モジュール群
- dashboard と Bevy 間の同期レイヤ

## 3. 削除対象

- `pane_grid` 前提の状態管理
- `PaneGrid::split` 呼び出し（`Grep "panes.split("` の全ヒット **現状 8 箇所**）
  - `src/main.rs:2538` `OpenOrderPanel handler の panes.split()`
  - `src/screen/dashboard.rs:231` `update(Message::SplitPane) の panes.split()`
  - `src/screen/dashboard.rs:519` `fn merge_pane の panes.split()`
  - `src/screen/dashboard.rs:547` `fn split_pane の panes.split()`
  - `src/screen/dashboard.rs:911` `fn replace_new_pane / 自動生成 split (1) の panes.split()`
  - `src/screen/dashboard.rs:927` `自動生成 split (2) の panes.split()`
  - `src/screen/dashboard.rs:948` `OrderList 自動生成 split の panes.split()`
  - `src/screen/dashboard.rs:964` `BuyingPower 自動生成 split の panes.split()`
  - シンボル名は推定込み。正確な fn 名は Phase 3 着手時に再確認する。
- dashboard 用 `pane_grid` スタイル
- `pane_grid::Pane` ベースの識別子配線（実コード **約 39 箇所 / 6 ファイル**）

## 4. 検証

Phase ごとに acceptance を 1:1 で対応させる。

| Phase | 実行コマンド | テスト/ファイル | assert 内容 |
|-------|-------------|-----------------|-------------|
| Phase 1 | `cargo test -p data` | `data/src/layout/...` 配下に 4 件必須 | `floatrect_rejects_negative_size` / `floating_pane_data_serde_roundtrip` / `camera_zoom_clamped` / `dashboard_legacy_pane_grid_falls_back_to_default` |
| Phase 2 | `cargo run -p flowsurface-bevy-spike` + `cargo test -p data pane_kind_renderer_class_covers_all_variants` | Bevy spike バイナリ（手動 + headless）+ `data::PaneKind::renderer_class()` unit test | min サイズ 120×80 / focus 取得 pane の `PaneZ` が他より +1 以上 / ズーム範囲 0.25〜4.0 / マウスホイール 1 ノッチで 1.1 倍 / **wgpu 共存性 PoC 結果が判定可能（Q1 への入力）** / **pane 種別レンダリング分類が architecture §3.5 の表で確定済み** / **入力境界契約 INV-INPUT-1〜4 が architecture §4.1 で確定済み** / **`pane_kind_renderer_class_covers_all_variants` で全バリアントの分類値を assert** |
| Phase 3 | `cargo test --workspace` | `src/screen/dashboard.rs::tests` | 6 メッセージ（`WindowMoved` / `WindowResized` / `WindowFocused` / `WindowClosed` / `WindowAdded` / `CameraChanged`）の単体テストが pass / `WindowClosed` 受信で focus が他 pane へ移譲される / `WindowAdded` 受信で新 pane の `PaneZ` が最大値となる / `WindowFocused` 受信で対象 pane の `PaneZ` が他 pane +1 以上になる / **INV-REPLAY 観測点**: `replay_registry_starts_1to1` / `replay_registry_unregister_register_atomic` / `replay_registry_built_after_windows` の 3 件が pass（spec §2 Phase 3 INV-REPLAY-1/2/3 に対応） |
| Phase 4 | `cargo run` + `cargo test --workspace` + `bash scripts/measure_window_moved_rate.sh` | placeholder pane（手動操作）/ `src/screen/dashboard.rs::tests` の closing SoT / 強制 close unit & integration test / `scripts/measure_window_moved_rate.sh` 5 分計測 | 移動・close・ズーム・パンが動作する / `INV-CLOSE-1`: `WindowClosed` 受信時に 購読 stream cancel → aggregator drop → `replay_pane_registry` 解除 が `data::Dashboard.windows` 削除より前に行われることを assert / `inv_input_1_overlay_blocks_canvas_drag` / `inv_input_2_closing_pane_excluded_from_hit_test` / `inv_input_5_keyboard_focus_overlay_owns` / `inv_input_6_drag_dead_zone_5px` / `inv_input_6_wheel_modifier_zoom` / `inv_input_7_context_menu_overlay_first` / `inv_close_1_teardown_drop_5s_timeout` / `inv_close_1_sequential_not_parallel` / `inv_close_1_closing_rejects_pointer_input` の 9 件が pass（INV-INPUT-8（touch / tablet pen）は MVP non-goal のため test 名なし） / **teardown timeout 後の auto-fail（強制 close）と再試行 UI が動作する unit/integration test** が pass / **Q5 計測**: `scripts/measure_window_moved_rate.sh` で 5 分計測し、`> 120 events/s` で (c) 16ms throttle 採用、`< 60 events/s` で (d) 即 commit 許容、許容 latency ≤ 16ms を判定する |
| Phase 5 | （手動確認 + 既存 unit/integration テスト + `tests/manual/floating-windows-CHECKLIST.md`） | spec §6 機能保持マトリクス（C1〜C5 / K1〜K4 / H1〜H2 / L1〜L2 / T1〜T3 / S1〜S3 / CMP1〜CMP2 / P1〜P6）/ `tests/manual/floating-windows-CHECKLIST.md` | **「表示が崩れない」ではなく機能保持マトリクスの全項目（C1〜C5 / K1〜K4 / H1〜H2 / L1〜L2 / T1〜T3 / S1〜S3 / CMP1〜CMP2 / P1〜P6（popout 実装時のみ））が現状同等に動作すること**。Kline overlay marker 配信（K2）/ indicator 追加・削除・並べ替え（K3）/ Kline `Sync all`（K4）/ Heatmap GPU 描画維持（H1）/ 設定 modal・indicator picker・study configurator が iced overlay として動作（C3 / C4）/ 入力境界契約 INV-INPUT-5/6/7（C5）が architecture §4.1 通りに動作（INV-INPUT-8 は MVP non-goal）。回帰した項目は Issue 起票し Phase 5 を完了させない / **`tests/manual/floating-windows-CHECKLIST.md` を成果物として PR にチェック済み証跡を添付する（未添付の PR は Phase 5 完了とみなさない）** |
| Phase 6 | `cargo test --workspace` + grep + e2e | `tests/fixtures/saved-state-legacy-*.json` 2 種 | 2 fixture とも fallback assert が pass / e2e smoke ログは **target = `flowsurface::floating_windows`, level = INFO** で発行され、grep は target フィルタ後に行う。(1) `target=flowsurface::floating_windows` のログ中に `dashboard_loaded uuids=N` が出現する / (2) 観測ウィンドウ中に同 target のログで `camera saved zoom=` が 1 回以上観測される / (3) `pane_grid` 文字列が flowsurface-current.log に出現しない |

### 4.1 CI 組込

- 既存 `.github/workflows/rust-tests.yml` に `bevy-spike-build` job を **Phase 2 で追加**する（新規 yml は作らない）。
- headless 実行は Linux で `bevy_ci_testing` または `bevy/x11` を使用する。
- spike バイナリのビルド失敗 / headless 起動失敗を CI ゲートとする。
- **Phase 6 完了後**: `rg "pane_grid" src/ data/src/ -t rust -l` が空であることを CI で fail させる job を `rust-tests.yml` に追加する（残存検出ガード）。
- **Phase 6 完了後**: e2e ログ 3 項目（target=`flowsurface::floating_windows`、§4 Phase 6 行の (1)〜(3)）の観測を nightly job として CI に組み込む。

> Bevy 自動テスト方針は Q7（`open-questions.md`）を参照。同質問は別 implementer が更新する。
