---
name: review-fix-loop
description: 並列の専門サブエージェントで多角レビュー → 修正エージェントで TDD 修正 → 再レビュー、を MEDIUM 以上の指摘がゼロになるまで繰り返すオーケストレーション手法。新フェーズ完了後・大規模 PR 着地前に使う。
origin: ECC (e-station 向けカスタム)
---

# Review-Fix Loop

新フェーズや大規模 PR の実装が完了したあと、このスキルを起動する。

```
/review-fix-loop
```

オーケストレーター（あなた）が レビュー段階 → 集約 → 修正段階 → 再レビュー を **MEDIUM 以上の指摘がゼロになるまで** 繰り返す。

対象に応じて以下の詳細手順を参照すること:

| 対象 | 参照ファイル |
|---|---|
| 計画書（`docs/` 配下の `.md` ファイル群） | [`PlanLoop.md`](./PlanLoop.md) |
| ソースコード（Rust / Python 実装ファイル） | [`ImplementationLoop.md`](./ImplementationLoop.md) |

> **計画書とコードの両方が対象の場合**: `ImplementationLoop` を先行させる。`general-purpose` レビュアーの観点が計画書クロスチェックを兼ねるため、コード収束後に計画書の「レビュー反映」ブロック追記のみ `PlanLoop` で仕上げる。

---

## 不可侵ルール（両ループ共通）

- **secrets を log/test/comment/commit に含めない**
- **TDD 厳守**: 修正は `.claude/skills/tdd-workflow/SKILL.md` に従い RED → GREEN → REFACTOR
- **既存テストを壊さない**
- **完了時の検証**: プロジェクトの最終コマンド全件緑（e-station なら `cargo check --workspace` / `cargo clippy --workspace -- -D warnings` / `cargo fmt --check` / `cargo test --workspace`（デフォルト並列）/ `uv run pytest <対象>`）
- **prompt は self-contained**: サブエージェントは前会話を見ない。必読ドキュメントの相対パスを毎回明記する

---

## 収束基準（両ループ共通）

- **CRITICAL はラウンド内即修正。持ち越し不可**（持ち越す場合はユーザーの明示承認が要る）
- **HIGH / MEDIUM 以上ゼロ** で終了
- LOW のみ残った場合は LOW 一覧を提示して終了
- HIGH 以上が「次イテレーション持ち越し」と判断される場合は、計画書の「繰越 / 次イテレーション」ブロックに明示記載した上で終了（理由・期限・代替策を必ず添える）。**降格はユーザーの明示承認が要る**
