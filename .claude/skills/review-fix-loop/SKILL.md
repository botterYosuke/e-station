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

---

## 大規模 fix のエスカレーション閾値

R1 集約後の MEDIUM+ 件数で修正フローを使い分ける（Phase 8 R1-R4 の実測ベース）:

| 規模 | 推奨フロー |
|---|---|
| 〜10 項目 + 軽い機能 | 単一 `general-purpose` で batch 修正 |
| 10〜30 項目 | 単一 `general-purpose` でも可（依存順を明示）、不安なら parallel-agent-dev |
| **30 項目超 / 大型新機能含む** | **`/parallel-agent-dev` 必須**。Phase 1（型基盤）→ Phase 2 → Phase 3/4 並列 → Phase 5 のような multi-stage で組む |

単一 agent に大規模 batch を投げて STOP+REPORT が返ってきたら、案 A（parallel-agent-dev）/ 案 B（CRITICAL+HIGH のみ次ラウンド分割）/ 案 C（TDD 緩和に明示承認）の 3 択をユーザーに提示する。

**`isolation: "worktree"` の罠**: フィーチャーブランチ作業中の worktree 起動は base 不整合（main から作られて新規ファイルが消える）を起こすことがある。ImplementationLoop.md「知見 12」参照。並行性は worktree でなく「ファイル単位の担当分け」で確保するのが安全。
