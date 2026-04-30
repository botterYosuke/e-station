# Floating Windows 移行計画

## 何をするか

`iced::widget::PaneGrid`（スプリット分割）を廃止し、
dashboard UI を `Bevy` ベースで実質作り直したうえで、
任意の位置・サイズに配置できる **フローティングパネルシステム** に移行する。

OS レベルのウィンドウ（既存 popout 機構）は継続して使用する。
ただし in-canvas 側はウィジェット差し替えではなく、dashboard frontend を再設計する。

Figma / Blender のような操作感（スクロールホイール＝ズーム、空白ドラッグ＝パン）も合わせて実装する。

## ゴール

| 変更前 | 変更後 |
|--------|--------|
| `pane_grid::State<pane::State>` — スプリット固定レイアウト | `Vec<FloatingPane>` + `Bevy ECS` — 任意位置・サイズ |
| `pane_grid::Pane` をペイン識別子として使用 | `uuid::Uuid` をペイン識別子として使用 |
| 最大化/スプリット軸ベースの操作 | ドラッグ移動・リサイズ・ズーム・パン・camera ベースの canvas 操作 |

## 文書構成

- [spec.md](./spec.md) — Bevy 本線のゴール・スコープ・機能要件・非機能要件
- [architecture.md](./architecture.md) — 旧 `iced` カスタムウィジェット案。比較用の参考資料
- [implementation-plan.md](./implementation-plan.md) — 旧 `iced` カスタムウィジェット案の実装メモ。比較用の参考資料
- [open-questions.md](./open-questions.md) — 未解決事項（ドラッグ頻度・ズームスケール・スクリーン外挙動 等）

## 実装フェーズ概要

| Phase | 内容 |
|-------|------|
| **Phase 1** | `FloatRect` / `FloatingPaneData` / `Camera` をデータクレートに追加 |
| **Phase 2** | Bevy 検証アプリでフローティング pane・ズーム・パン・popout の最小プロトタイプを作る |
| **Phase 3** | dashboard 状態を `uuid::Uuid` ベースへ移行し、Bevy 側の pane entity / focus / z-order に接続 |
| **Phase 4** | dashboard frontend を Bevy 描画へ切り替え、`main.rs` の直接 `pane_grid` 依存を除去 |
| **Phase 5** | タイトルバー UI・パネル追加 UI・全コンテンツ種別・設定 UI の Bevy 側再構成 |
| **Phase 6** | テスト追加・旧 `pane_grid` 依存の全削除・`saved-state.json` 互換確認 |

## 実装方針

`Bevy` を代替案ではなく本線として採用する。
理由は、ズーム・パン・z-order・複数ウィンドウ・canvas 操作を
`iced` のカスタムウィジェットで抱え込むより、2D カメラと ECS を持つ Bevy の方が
最終形に近い設計で組めるため。

### 判断

- **本線**: dashboard UI は Bevy で再構成する
- **前提**: `FloatingPanes` だけの差し替えは行わない
- **受け入れる変更**: pane 本体 UI と設定 UI を含め、dashboard frontend を段階的に作り直す

### Bevy 本線の進め方

| Phase | 内容 |
|-------|------|
| **B0** | `bevy` 単体の検証用バイナリを追加し、1 枚のフローティング矩形に対してドラッグ・8 方向リサイズ・ホイールズーム・空白パンを確認 |
| **B1** | `FloatRect` / `Camera` / `FloatingPaneData` を `data` クレートの共通モデルにする |
| **B2** | dashboard の Bevy frontend を作り、pane entity・focus・z-order・popout・永続化を接続する |
| **B3** | `pane::State::view()` 相当の表示責務を Bevy UI または Bevy 描画コンポーネントへ移す |
| **B4** | 設定 UI・インジケーター UI・ショートカット・サイドバー連携を Bevy 側へ移植する |
| **B5** | 旧 `iced` の `pane_grid` / `Element` / スタイル依存を撤去し、dashboard から完全に切り離す |

### Bevy 案のメリット

- カメラ移動・ズーム・複数ウィンドウを座標系ベースで素直に組める
- ECS で `FloatingPane` を entity として管理でき、z-order や hit test を分離しやすい
- 将来的に Figma/Blender 的なキャンバス操作へ広げやすい

### Bevy 本線の主要リスク

- 現在の `iced` UI 資産をそのまま再利用しづらい
- `src/screen/dashboard/pane.rs` の既存 view 群、設定モーダル、`main.rs` のメッセージ配線を広く移植する必要がある
- 旧実装との過渡期に、状態同期とイベント配線の二重管理が発生しやすい

### 結論

この変更は `FloatingPanes` の置換ではなく、
**dashboard UI を Bevy で作り直すプロジェクト**として進める。
旧 `iced` 案は比較資料として残すが、実装本線は Bevy に切り替える。

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
