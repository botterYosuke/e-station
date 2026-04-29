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
- 旧 `iced` 案は [archive/2026-04-29-pre-bevy-rewrite/](./archive/2026-04-29-pre-bevy-rewrite/) に退避した
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

## 文書構成

- [spec.md](./spec.md) — スコープ・要件・完了条件（§6 機能保持マトリクスを含む）
- [architecture.md](./architecture.md) — Bevy 本線の構成案
- [implementation-plan.md](./implementation-plan.md) — 実装順序と変更対象
- [open-questions.md](./open-questions.md) — 未確定事項

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
| [../✅python-data-engine/](../✅python-data-engine/) | IPC・エンジン側への影響は基本なし |
| [../✅nautilus_trader/](../✅nautilus_trader/) | pane 追加 API の変更に追随が必要（引き取り境界: Phase 4 で本計画側が pane 追加 API を確定した直後に nautilus_trader 側担当者が追随する） |
| [../✅order/](../✅order/) | pane id 型変更（`pane_grid::Pane` → `uuid::Uuid`）による Modal 経路の追随が必要 |
| [../✅tachibana/](../✅tachibana/) | pane id 型変更（`pane_grid::Pane` → `uuid::Uuid`）による Modal 経路の追随が必要 |
