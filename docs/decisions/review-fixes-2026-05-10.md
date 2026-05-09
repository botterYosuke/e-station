---
title: docs レビュー修正ログ（2026-05-10）
date: 2026-05-10
round: 1
---

# Docs レビュー修正ログ — 2026-05-10

対象: 2026-05-09 の実装変更に対する docs/ 整合チェック

## ラウンド 1（2026-05-10）

### 統一決定

- Phase 表記: 「Phase 1 完了（2026-05-09 時点）」「Phase 2 着手（2026-05-09 Issue #25/#33/#34）」
- IPC schema bump なし（changelog.md への追記は不要）
- `docs/architecture/modules/ui-shell.md` の Tachibana ログインボタン廃止は既記録（Line 477）→ 追記不要
- 注文パネル venue toggle は dashboard 層のため ui-shell.md のスコープ外

### Findings

| ID | 重要度 | 対象ファイル | 問題 | 修正 |
|---|---|---|---|---|
| KP-M1 | MEDIUM | `docs/roadmap/kabusapi/implementation-plan.md` | Phase 1 post-fix バグ (#35/#36/#37) の解消と Phase 2 着手 (#25/#33/#34) が未記録 | ファイル末尾に「進捗ノート（2026-05-09 時点）」セクションを追記 |
| KS-M1 | MEDIUM | `docs/specs/venues/kabusapi.md` | Phase 1 完了ノートと Phase 2 状況が未記録 | ファイル末尾に「Phase 1 完了ノート」「Phase 2 状況」セクションを追記 |
| CL-L1 | LOW | `docs/roadmap/changelog.md` | 2026-05-09 変更が未記録（ただし IPC schema bump なし） | changelog.md は IPC schema 専用のため今回は追記不要 |
| UI-L2 | LOW | `docs/roadmap/ui-shell/spec.md` | 注文パネル venue toggle の記録なし | ui-shell は menu/status bar 層のドキュメント。dashboard 層の変更は別ドキュメントが担当するためスコープ外 |

### 結果

- 修正済み: KP-M1, KS-M1
- 対応不要（スコープ外）: CL-L1, UI-L2
- 残存 HIGH/MEDIUM: 0件 → **ラウンド 1 で収束**
