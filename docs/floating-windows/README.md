# Floating Windows 移行計画

## 何をするか

`iced::widget::PaneGrid`（スプリット分割）を廃止し、iced のメインウィンドウ内で
任意の位置・サイズに配置できる **フローティングパネルシステム** に置き換える。

OS レベルのウィンドウ（既存 popout 機構）は継続して使用し、
in-canvas フローティングはメインウィンドウ内の表現方式の変更にとどめる。

Figma / Blender のような操作感（スクロールホイール＝ズーム、空白ドラッグ＝パン）も合わせて実装する。

## ゴール

| 変更前 | 変更後 |
|--------|--------|
| `pane_grid::State<pane::State>` — スプリット固定レイアウト | `Vec<FloatingPane>` — 任意位置・サイズ |
| `pane_grid::Pane` をペイン識別子として使用 | `uuid::Uuid` をペイン識別子として使用 |
| 最大化/スプリット軸ベースの操作 | ドラッグ移動・リサイズ・ズーム・パン |

## 文書構成

- [spec.md](./spec.md) — ゴール・スコープ（Phase 1〜6）・機能要件・非機能要件
- [architecture.md](./architecture.md) — 座標系・`FloatingPanes` ウィジェット設計・データモデル変更・永続化変換
- [implementation-plan.md](./implementation-plan.md) — 型定義・メッセージ変更・コード断片・変更ファイル一覧
- [open-questions.md](./open-questions.md) — 未解決事項（ドラッグ頻度・ズームスケール・スクリーン外挙動 等）

## 実装フェーズ概要

| Phase | 内容 |
|-------|------|
| **Phase 1** | `FloatRect` / `FloatingPaneData` をデータクレートに追加 |
| **Phase 2** | `FloatingPane` 型と `Dashboard` 状態を移行、`pane_grid::Pane` → `uuid::Uuid` 一括置換 |
| **Phase 3** | `FloatingPanes` カスタムウィジェット実装（ドラッグ・リサイズ・ズーム・パン） |
| **Phase 4** | `Dashboard::view()` 切り替え・`main.rs` の直接操作修正・動作確認 |
| **Phase 5** | タイトルバー UI・パネル追加 UI・全コンテンツ種別の表示確認 |
| **Phase 6** | テスト追加・`pane_grid` 依存の全削除・`saved-state.json` 互換確認 |

## 既存計画との関係

| 計画 | 関係 |
|------|------|
| [docs/✅python-data-engine/](../✅python-data-engine/) | IPC・エンジン側への影響なし。Rust GUI のみの変更 |
| [docs/✅nautilus_trader/](../✅nautilus_trader/) | D9（REPLAY 銘柄追加時の自動 pane 生成）が本計画の `WindowAdded` / `FloatingPane` API に依存する |

## 主な影響ファイル

| ファイル | Phase |
|---------|-------|
| `data/src/layout/pane.rs` / `data/src/layout/dashboard.rs` | 1 |
| `src/screen/dashboard.rs` / `src/screen/dashboard/pane.rs` | 2, 4 |
| `src/modal/pane/settings.rs` / `src/modal/pane/indicators.rs` | 2 |
| `src/widget/floating_panes.rs`（新規） | 3 |
| `src/main.rs` / `src/style.rs` | 4 |

## 破壊的変更

- `saved-state.json` の `pane`（ツリー構造）→ `windows`（フラットリスト）への非互換変更あり。
  旧フォーマットは `ok_or_default` で空ウィンドウリストにフォールバックする。
