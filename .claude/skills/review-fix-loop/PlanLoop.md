あなたはオーケストレーターです。
以下のタスクを「レビュー → 修正」のループとして、MEDIUM 以上の Finding がゼロになるまで繰り返してください。

## タスク概要

`docs/plan/tachibana/*.md`（architecture.md / implementation-plan.md / data-mapping.md / review-fixes-2026-04-25.md）の改修計画に対して、レビューと修正を反復する。

## 前提資料

- `.claude/skills/tachibana/SKILL.md` — 立花証券 API 仕様・コーディング規約の一次資料
- `engine-client/src/dto.rs` / `engine-client/src/capabilities.rs` / `data/src/config/tachibana.rs` — T0.2 で実装済みの Rust 型（計画との照合基準）
- `docs/plan/tachibana/review-fixes-2026-04-25.md` — 直前ラウンドの修正ログ（重複指摘を避けるために必ず読む）

## ループ手順

### Step 1: レビュー（サブエージェントで並列実行）

以下の 4 観点を **4 つのサブエージェント（general-purpose）** に並列で割り当て、各エージェントに観点別の Findings を返させる。

| エージェント | 観点 |
|---|---|
| A | **文書間整合性** — architecture.md / implementation-plan.md / data-mapping.md / SKILL.md 間で矛盾・不整合・旧表記残りがないか |
| B | **既存実装とのズレ** — dto.rs / capabilities.rs / tachibana.rs（data クレート）に実装済みの型・フィールド・trait が計画文書の記述と食い違っていないか |
| C | **仕様漏れ・設計リスク** — SKILL.md の R1〜R10 / EVENT 規約 / URL 形式 / Shift-JIS / p_no 規約のうち、計画文書で未対処または曖昧なままの箇所 |
| D | **テスト不足** — 計画に書かれた実装タスクに対して対応するテストケース（受け入れ条件・単体・結合・E2E）が明記されていない箇所 |

各エージェントへの指示：
- Findings を **HIGH / MEDIUM / LOW** に分類し、ファイル名・行範囲・具体的な問題箇所を添えて報告する
- すでに `review-fixes-2026-04-25.md` に記録済みの修正と重複する指摘は除外する
- 新規 Finding のみを返す

### Step 2: 集約と判定

4 エージェントの結果を集約し、重要度順（HIGH → MEDIUM → LOW）に整理する。

**終了条件チェック**:
- HIGH / MEDIUM の Finding がゼロ → ループ終了。最終サマリを出力する
- HIGH / MEDIUM が 1 件以上残っている → Step 3 へ進む

### Step 3: 修正（サブエージェントで並列実行）

HIGH / MEDIUM の Finding を **implementer サブエージェント** に渡して修正させる。

- Finding ごとに独立した修正が可能なものは並列実行する
- 依存関係がある修正（例：architecture.md の変更が implementation-plan.md の複数箇所に波及する）は直列に実行する
- 修正後、`review-fixes-2026-04-25.md` の末尾に「ラウンド N（日付）」セクションを追記し、修正内容を表形式で記録する

### Step 4: ループ継続

Step 1 に戻る。ただし次ラウンドのレビューでは、Step 3 で修正済みのファイルを重点的に確認する。

## 出力形式

各ラウンドの先頭に以下を出力する:

=== ラウンド N ===
残存 HIGH: X件 / MEDIUM: Y件 / LOW: Z件

ループ終了時:

=== 完了 ===
全ラウンド数: N
修正した Finding 総数: HIGH X件 / MEDIUM Y件
残存 LOW（対応不要）: Z件

## 禁止事項

- LOW の Finding を理由にループを継続してはいけない（LOW は対応不要）
- Finding を「修正済み」とマークする前に、対象ファイルを Read して実際に変更が反映されていることを確認すること
- 計画文書に存在しない新機能・新フェーズを追加してはいけない（計画の範囲内の修正のみ）
- SKILL.md の一次資料（R1〜R10、EVENT 規約）を計画文書側の記述で上書きしてはいけない（SKILL.md が正）