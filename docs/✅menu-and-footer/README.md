# ネイティブメニューバー + ステータスバー（フッター）

本ディレクトリは、メインウィンドウへの **OS ネイティブメニューバー** と
**ステータスバー（フッター）** 追加の仕様・実装記録をまとめたものです。

両機能は 2026-04-30 に実装・レビュー完了し、`cargo test --workspace` 全 PASS を確認済みです。

---

## 目次

- [`native-menu-bar-impl.md`](./native-menu-bar-impl.md) — OS ネイティブメニューバーの要件・設計・テスト
- [`footer-impl.md`](./footer-impl.md) — ステータスバー（フッター）の要件・設計・テスト
- [`archive/`](./archive/) — レビュー修正ログ

### 未実装計画（review-fix-loop R1〜 修正中）

- [`fix-save-menu.md`](./fix-save-menu.md) — `上書き保存（Save）` 追加・dirty 判定・current_path 管理（F1〜Fn）
- [`P5-scenario-in-strategy.md`](./P5-scenario-in-strategy.md) — Strategy ファイル内 SCENARIO 辞書による再現条件埋め込み
- [`P7-mode-switch-menu.md`](./P7-mode-switch-menu.md) — live ⇄ replay モード切替メニューと engine restart
- [`P8-widget-menu-bar-linux.md`](./P8-widget-menu-bar-linux.md) — Linux 向け iced widget メニューバー（GTK 非依存）
- [`P9-wandb-submit-menu.md`](./P9-wandb-submit-menu.md) — W&B Submit メニュー・wandb run lifecycle・PII scrubber
- [`review-fixes-2026-05-04.md`](./archive/review-fixes-2026-05-04.md) — 上記 4 計画書の review-fix-loop R1 ログ・統一決定・Findings 一覧

---

## 実装ステータス

| 機能 | 状態 | 完了日 |
|------|------|--------|
| OS ネイティブメニューバー（Win / macOS） | ✅ 完了 | 2026-04-30 |
| ステータスバー（`● LIVE` / `● REPLAY`） | ✅ 完了 | 2026-04-30 |

---

## 機能サマリ

### ネイティブメニューバー

タイトルバー直下に Win32 / macOS 標準のメニューバーを追加。

| モード | メニュー項目 |
|--------|------------|
| live | `File > 開く…（Open）` / `File > 上書き保存（Save）` / `File > 名前を付けて保存…（Save As）` / `File > 終了` |
| replay | `File > Replay を開始…` / `File > Replay を停止` / `File > 終了` |

> **表記注記**: 上の表は未実装計画（[`fix-save-menu.md`](./fix-save-menu.md) F2-F4 /
> [`P7-mode-switch-menu.md`](./P7-mode-switch-menu.md)）反映後の最終形を示す。
> 現在の実装では `File > 上書き保存（Save）` と `File > Replay を停止` は未実装で、
> live は `開く…` / `名前を付けて保存…` の 2 項目、replay は `Replay を開始…` のみ。

> **Phase 8 更新（python-helper-direct-api）**: 旧 `File > ストラテジーを開く…` は廃止し、
> `File > Replay を開始…` フォーム（instrument / start / end / granularity / strategy_file /
> initial_cash 入力）に統合された（[python-helper-direct-api.md §5 Phase 8](../✅python-data-engine/archive/python-helper-direct-api.md)）。

- `File > 開く…（Open）`: 任意の `.json` を選択してレイアウトを即座に反映（バリデーション付き）
- `File > 上書き保存（Save）`: 直前 Load した `.json` に dirty 差分を上書き（未実装、fix-save-menu.md F2-F4 で計画）
- `File > 名前を付けて保存…（Save As）`: 現在のレイアウトを任意のパスへ書き出し
- `File > Replay を開始…`: replay モードでパラメータを入力して開始
- `File > Replay を停止`: 進行中の replay を停止（未実装、P7-mode-switch-menu.md で計画）
- Linux は no-op（GTK 依存なし、`muda` は Win/macOS 限定でリンク）

### ステータスバー

メインウィンドウ最下部に高さ 20 px の固定バーを表示。

| モード | 表示 | 色 |
|--------|------|-----|
| live | `● LIVE` | 緑 `(0.2, 0.75, 0.3)` |
| replay | `● REPLAY` | アンバー `(0.9, 0.6, 0.1)` |

- popout ウィンドウには**表示しない**
- モーダル展開中はオーバーレイの下に隠れる（意図的トレードオフ — `footer-impl.md §アーキテクチャ` 参照）

---

## 関連ソースファイル

| ファイル | 役割 |
|---------|------|
| `src/native_menu.rs` | muda 統合・Subscription・プラットフォーム分岐 |
| `src/main.rs` | Message バリアント・ハンドラ・`fn view()` フッター合成 |
| `Cargo.toml` | `muda = "0.15"` 依存関係（Win/macOS 限定） |

---

## 将来の拡張候補

以下は本フェーズのスコープ外。要望・優先度に応じて対応する。

| 項目 | 優先度 | 詳細 |
|------|--------|------|
| アクセラレーター（Ctrl+O / Ctrl+S） | F2 で実装 | [`fix-save-menu.md` F2](./fix-save-menu.md#f2) / `native-menu-bar-impl.md §既知の制限` 参照 |
| Edit / View サブメニュー | 低 | 同上 |
| Linux ネイティブメニュー（GTK） | 低 | 同上 |
| `fn render_main_window(...)` helper 抽出 | 中 | popout 非表示を型で保証できる |
| toast の inset 調整（footer 高さ分） | 中 | `footer-impl.md §未決事項` 参照 |
| ステータスバーへの接続状態インジケーター | 中 | 同上 |
| バージョン表示（右端） | 低 | 同上 |
