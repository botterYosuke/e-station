# 作業依頼: replay モードで saved-state.json を保存しない

## 仕様書

`docs/✅nautilus_trader/replay-no-saved-state.md` を必ず最初に読んでください。
変更内容・設計判断・没案の理由がすべて記載されています。

---

## Goal（目的）

`src/main.rs` の `save_state_to_disk()` 先頭に replay モードガードを追加し、
replay セッション終了時に `saved-state.json` を上書きしないようにする。

## Constraints（制約）

- 変更は `src/main.rs` 1 ファイルのみ（仕様書に 4 行のコード例あり）
- ロード側（`load_saved_state()`）は変更しない
- `APP_MODE` OnceLock を参照する既存パターンに倣うこと（コード内に同様の参照箇所あり）

## Acceptance criteria（完了条件）

- `--mode replay` で起動→終了しても `saved-state.json` が生成・更新されない
- `--mode live` での保存動作は変化しない
- `cargo build` と `cargo test --workspace` がすべて通る

---

## 作業方針

1. **実装**: `.claude/skills/parallel-agent-dev/SKILL.md` のオーケストレーション手法で実装
   （今回は変更箇所が 1 ファイルのため並列化は不要だが、スキルのワークフローに従うこと）
2. **TDD**: `.claude/skills/tdd-workflow/SKILL.md` に従って進める
3. **レビュー**: 実装完了後、`.claude/skills/review-fix-loop/SKILL.md` でレビューと修正を行う

---

## 進捗記録ルール

作業中は**この計画書（`replay-no-saved-state.md`）を直接更新**してください。

- 完了した作業項目には `✅` を付ける
- 新たな知見・設計判断・詰まった点・Tips を「実装メモ」セクションに追記する
- 他の作業者が引き継げる状態を常に保つ
