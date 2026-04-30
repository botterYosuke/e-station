# Floating Windows — レビュー修正ログ

本ドキュメントは `docs/floating-windows/` 配下の spec / architecture / implementation-plan / open-questions / README に対する
レビュー結果と統一決定、適用すべき修正項目（Findings）を記録する。レビューラウンドごとに追記する。

対象ファイル:

- `docs/floating-windows/README.md`
- `docs/floating-windows/spec.md`
- `docs/floating-windows/architecture.md`
- `docs/floating-windows/implementation-plan.md`
- `docs/floating-windows/open-questions.md`

## 統一決定

ラウンド横断で確定した方針。以後の修正・追記はすべてこの決定に従う。

- **旧 saved-state は破棄してデフォルトレイアウトで起動する。** `data::layout::Dashboard` に
  `schema_version: u32` を導入し、未知バージョンを検出したら一律デフォルトに落とす。
  legacy fixture を 2 件用意して migration ロジックの非存在を回帰テストでガードする。
- **popout（独立 OS ウィンドウ）は Phase 6 までスコープ外**。当面は非永続扱いとし、
  open-questions に **Q6（popout の永続化と OS ウィンドウ管理方針）** として正式起票する。
- **`focus` 型を `Option<PaneLocation>` に抽象化する。** architecture.md にある
  `focus: Option<(window::Id, uuid::Uuid)>` は popout の扱いを Q1 解決後まで保留できるよう
  抽象型へ差し替える。具体型はQ1解決後に確定する。
- **Phase 2 spike の最初の deliverable に「wgpu 共存性 PoC」を含める。**
  iced 0.14 系 (wgpu 27) と Bevy (wgpu 23/24) の併存可否を実機で判定する。
  Q1 が解決するまで Phase 4 には進まない（ハードゲート）。
- **不変条件 ID の命名を統一する。** 機能要件 `F1〜F10`、非機能要件 `NF1〜NF7`。
  追加仕様には `INV-CLOSE-1` など prefix 付きの ID を使う。test 関数名との対応表は
  `spec.md` 内にインラインで併記する（外部表に逃さない）。
- **座標系を明示する。** logical px、原点 top-left、Y 軸下向き。
  `Camera` は world→screen の affine 変換として定義する（ズーム＋パン）。
- **削除対象規模を明記する。** `PaneGrid::split` 呼び出し 6 箇所
  （`main.rs:2538` / `dashboard.rs:231,519,547,911,927`）、`pane_grid::Pane` 実コード約 39 箇所、
  影響ファイル 6 件。implementation-plan.md にファイル別の削除対象を明示する。
- **README 関連計画リストに `docs/✅order/` および `docs/✅tachibana/` を追加する。**
  既存の link path 表記と合わせる。

## ラウンド 1（2026-04-29）

### 統一決定（R1）

- 旧 saved-state は破棄してデフォルトレイアウトで起動、`schema_version: u32` を `data::layout::Dashboard` に導入
- popout は Phase 6 までスコープ外（非永続）。Q6 として open-questions に正式起票
- architecture.md の `focus: Option<(window::Id, uuid::Uuid)>` を `Option<PaneLocation>` に抽象化、Q1 解決後に具体化
- Phase 2 spike の最初の deliverable に「wgpu 共存性 PoC（iced 0.14+wgpu27 と Bevy=wgpu23/24 の併存判定）」を含める。Q1 解決まで Phase 4 不可
- 不変条件 ID は F1〜F10 / NF1〜NF7、追加仕様には `INV-CLOSE-1` 等の prefix。test 関数名対応表は spec.md 内 inline
- 座標系: logical px、原点 top-left、Y 軸下向き、`Camera` は world→screen affine
- 削除対象規模: `PaneGrid::split` 呼び出し 6 箇所（main.rs:2538 / dashboard.rs:231,519,547,911,927）/ `pane_grid::Pane` 実コード約 39 箇所 / 6 ファイル
- 関連計画リストに `docs/✅order/` `docs/✅tachibana/` を追加

### Findings 表（R1）

| ID | 観点 | 重大度 | 対象 | 修正概要 |
|---|---|---|---|---|
| H-A (H1+HB1) | A,B | HIGH | spec/arch/open-q | Q1 + wgpu 共存判定を Phase 2 spike PoC に統合、Q1 解決まで Phase 4 不可 |
| H-B (H2+H4+MC3) | A,C | HIGH | spec/arch/open-q | popout は Phase 6 までスコープ外、独立 focus/z/Camera 不変条件 NF7 追加、Q6 起票 |
| H-C (H3+HC2+MD1) | C,D | HIGH | spec/impl-plan | 旧 saved-state 破棄 + schema_version 導入 + legacy fixture 2 件 |
| H-D (HC1) | C | HIGH | arch §4.5 | pane teardown 契約 INV-CLOSE-1 を §4.5 に明記 |
| H-E (H5) | A | HIGH | arch §1 | focus 型を `Option<PaneLocation>` に抽象化 |
| H-F (HB2) | B | HIGH | impl-plan §3 | split() 除去対象 6 箇所をファイル別に明示 |
| H-G (HD1) | D | HIGH | spec/impl-plan | Phase 1 acceptance に必須 test 関数名 4 件 |
| H-H (HD2) | D | HIGH | spec/impl-plan | Phase 3 メッセージ 6 種の単体テスト計画 |
| M1 | A | MEDIUM | spec/arch | Dashboard 名前混線解消（完全修飾名） |
| M2 | A | MEDIUM | spec | Phase 4 acceptance に placeholder 内容明記 |
| M3 | B | MEDIUM | impl-plan/arch | Bevy features / wgpu バージョン制約 |
| M4 | A | MEDIUM | README | 関連計画 nautilus 引き取り境界 |
| M5 | D | MEDIUM | spec | Phase 6 テスト観測点具体化（ログ・コマンド） |
| M6 | A | MEDIUM | README | 関連計画 link path 表記統一 |
| M7 | A | MEDIUM | impl-plan §4 | Phase 別 acceptance 表に組み替え |
| M8 | A | MEDIUM | README | ゴール表に永続化型行を追加 |
| MB1 | B | MEDIUM | impl-plan | replay_pane_registry.rs を Phase 3 ファイルリストに追加 |
| MB2 | B | MEDIUM | spec | 現行 pane: Pane → windows 移行は default fallback |
| MB3 | B | MEDIUM | README | 関連計画に order/tachibana 追加 |
| MC1 | C | MEDIUM | spec §1 | 座標系・単位系定義段落を追加 |
| MC2 | C | MEDIUM | spec NF6 | viewport clamp 不変条件 |
| MD2 | D | MEDIUM | spec | Phase 2 spike 観測値（min size / zoom 範囲等） |
| MD3 | D | MEDIUM | spec | Phase 6 e2e smoke 追加観測点 3 件 |
| MD4 | D | MEDIUM | impl-plan | CI ゲート組込 |
| L1 | A | LOW | README | "FloatingPanes" 表記の言い換え |
| L2 | C | LOW | open-q | Q5 過渡期同期の選択肢列挙 |
| L3 | A | LOW | arch | PaneKind の責務一行追記 / open-q の決定構造 |
| LB1 | B | LOW | impl-plan | シンボル参照精度向上 |
| LB2 | B | LOW | README | archive 旧計画転換理由を追記 |
| LC1 | C | LOW | arch | z-order 決定論性 |
| LC2 | C | LOW | arch | iced UI と Bevy のイベント競合順序 |
| LD1 | D | LOW | open-q | Q7 Bevy 自動テスト方針起票 |

## ラウンド 2（2026-04-29）

### 統一決定（R2）
- Phase 3 メッセージ名は 6 イベント `WindowMoved` / `WindowResized` / `WindowFocused` / `WindowClosed` / `WindowAdded` / `CameraChanged` のみ（`Spawn/Move/Resize/Close/Focus/ZoomPan` 表記は廃止）
- `schema_version: u32` バンプ規則: 後方互換あり追加はバンプしない（serde `#[serde(default)]` で吸収）/ 破壊変更のみ +1 / Phase 1 = v1 / version 不在 or 最新より小は破棄しデフォルト起動
- DPR 値は永続化しない。座標は保存時 logical px、復元時は NF6 viewport clamp で吸収
- `INV-CLOSE-1` teardown: 逐次実行 / 個別 5s タイムアウト / closing 中 input 不可 / 順序は 購読 stream cancel → aggregator drop → `replay_pane_registry` 解除 → data モデル除去
- CI: 既存 `.github/workflows/rust-tests.yml` に `bevy-spike-build` job を追加（新規 yml は作らない）
- arch §5.5 PoC NG plan B (c) iced 完全置換は modal/settings/tachibana ログイン UI 再実装 = 計画リセット相当

### Findings 表

| ID | 観点 | 重大度 | 対象 | 修正概要 |
|---|---|---|---|---|
| H-R2-1 | A | HIGH | impl-plan §4 | Phase 3 6 メッセージ名を spec の 6 イベント名 (`WindowMoved` 系) に統一 |
| H-R2-C1 | C | HIGH | spec NF4, open-q Q8 | `schema_version` バンプ規則を明記、Q8 起票 |
| H-R2-C2 | C | HIGH | spec §1, open-q Q9 | DPR 値非永続化を座標系段落に追記、Q9 起票 |
| M-R2-1 | A | MEDIUM | impl-plan §4 | Phase 6 e2e 表に 3 観測項目すべて反映 |
| M-R2-2 | A | MEDIUM | impl-plan §4 | INV-CLOSE-1 を Phase 4 行 assert に紐付け |
| M-R2-3 | A | MEDIUM | impl-plan §4.1 | CI workflow を実在 `rust-tests.yml` に追加 job 形式で記述 |
| M-R2-C1 | C | MEDIUM | spec F6, arch §4.5 | INV-CLOSE-1 の teardown 順序・タイムアウト・input 不可を明記 |
| M-R2-C2 | C | MEDIUM | impl-plan Phase 3 | `PaneLocation` pattern match 箇所列挙を Phase 3 acceptance に追加 |
| M-R2-C3 | C | MEDIUM | arch §5.5 | PoC NG (c) は計画リセット相当を明記 |
| L-R2-1 | A | LOW | README | 関連計画 path 実在保証注記（任意） |
| L-R2-2 | A | LOW | impl-plan §4.1 | `Q6` → `Q7` typo 修正 |
| L-R2-C1 | C | LOW | spec §2 Phase 6 | e2e ログ責務を `tracing::info!` と注記 |
| L-R2-C2 | C | LOW | arch §5 | popout は Phase 6 までは機能維持を冒頭に明記 |
