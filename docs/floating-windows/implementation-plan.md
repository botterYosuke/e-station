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

### Phase 3

> Phase 3 で置換対象となる現行フィールドは以下の 3 つ（シンボル名で明示）:
> - `Dashboard::panes: pane_grid::State<pane::State>`
> - `Dashboard::focus: Option<(window::Id, pane_grid::Pane)>`
> - `Dashboard::popout: HashMap<window::Id, (pane_grid::State<pane::State>, WindowSpec)>`
>
> これらを `uuid::Uuid` ベース ID および `Option<PaneLocation>` 型へ移行する。

- `src/screen/dashboard.rs`
- `src/screen/dashboard/pane.rs`
- `src/screen/dashboard/replay_pane_registry.rs`（`pane_grid::Pane` を 3 箇所参照）
- `src/layout.rs`
- `src/main.rs`
- `src/modal/pane/settings.rs`
- `src/modal/pane/indicators.rs`
- `src/widget.rs`

> 実コード上の `pane_grid::Pane` 参照は **約 39 箇所 / 6 ファイル** にわたる。

> Acceptance: Phase 3 末で `PaneLocation` を pattern match する箇所（メッセージ payload / modal の親特定 / `replay_pane_registry` の key 等）を `Grep` で列挙し、Q1 解決後の再 touch 範囲を確定する。

### Phase 4-5

- `src/bevy_dashboard/` または同等の新規モジュール群
- dashboard と Bevy 間の同期レイヤ

## 3. 削除対象

- `pane_grid` 前提の状態管理
- `PaneGrid::split` 呼び出し計 **6 箇所**
  - `src/main.rs:2538` の 1 件
  - `src/screen/dashboard.rs:231, 519, 547, 911, 927` の 5 件
- dashboard 用 `pane_grid` スタイル
- `pane_grid::Pane` ベースの識別子配線（実コード **約 39 箇所 / 6 ファイル**）

## 4. 検証

Phase ごとに acceptance を 1:1 で対応させる。

| Phase | 実行コマンド | テスト/ファイル | assert 内容 |
|-------|-------------|-----------------|-------------|
| Phase 1 | `cargo test -p data` | `data/src/layout/...` 配下に 4 件必須 | `floatrect_rejects_negative_size` / `floating_pane_data_serde_roundtrip` / `camera_zoom_clamped` / `dashboard_legacy_pane_grid_falls_back_to_default` |
| Phase 2 | `cargo run -p flowsurface-bevy-spike` | Bevy spike バイナリ（手動 + headless） | min サイズ 120×80 / focus 取得 pane の `PaneZ` が他より +1 以上 / ズーム範囲 0.25〜4.0 / マウスホイール 1 ノッチで 1.1 倍 / **wgpu 共存性 PoC 結果が判定可能（Q1 への入力）** |
| Phase 3 | `cargo test --workspace` | `src/screen/dashboard.rs::tests` | 6 メッセージ（`WindowMoved` / `WindowResized` / `WindowFocused` / `WindowClosed` / `WindowAdded` / `CameraChanged`）の単体テストが pass / `WindowClosed` 受信で focus が他 pane へ移譲される / `WindowAdded` 受信で新 pane の `PaneZ` が最大値となる / `WindowFocused` 受信で対象 pane の `PaneZ` が他 pane +1 以上になる |
| Phase 4 | `cargo run` | placeholder pane（手動操作） | 移動・close・ズーム・パンが動作する / `INV-CLOSE-1`: `WindowClosed` 受信時に 購読 stream cancel → aggregator drop → `replay_pane_registry` 解除 が `data::Dashboard.windows` 削除より前に行われることを assert（順序検証 unit test を `src/screen/dashboard.rs::tests` に追加） |
| Phase 5 | （手動確認） | 既存 pane コンテンツ | 既存コンテンツの表示が崩れないこと |
| Phase 6 | `cargo test --workspace` + grep + e2e | `tests/fixtures/saved-state-legacy-*.json` 2 種 | 2 fixture とも fallback assert が pass / (1) e2e smoke ログに `floating windows: dashboard_loaded uuids=N` が出現する / (2) 観測ウィンドウ中に `camera saved zoom=` が 1 回以上観測される / (3) `pane_grid` 文字列が flowsurface-current.log に出現しない |

### 4.1 CI 組込

- 既存 `.github/workflows/rust-tests.yml` に `bevy-spike-build` job を **Phase 2 で追加**する（新規 yml は作らない）。
- headless 実行は Linux で `bevy_ci_testing` または `bevy/x11` を使用する。
- spike バイナリのビルド失敗 / headless 起動失敗を CI ゲートとする。

> Bevy 自動テスト方針は Q7（`open-questions.md`）を参照。同質問は別 implementer が更新する。
