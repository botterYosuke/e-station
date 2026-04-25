あなたはオーケストレーターです。
以下のタスクを「レビュー → 修正」のループとして、MEDIUM 以上の Finding がゼロになるまで繰り返してください。

## タスク概要

`docs/plan/order/*.md`（README.md / spec.md / architecture.md / implementation-plan.md / open-questions.md）の立花証券 注文機能 統合計画に対して、レビューと修正を反復する。

## 前提資料

- `.claude/skills/tachibana/SKILL.md` — 立花証券 API 仕様・コーディング規約の一次資料（R1〜R10 / EVENT 規約 / URL 形式 / Shift-JIS / p_no 規約）
- `.claude/skills/tachibana/samples/e_api_correct_order_tel.py/e_api_correct_order_tel.py` 他 — 立花注文系サンプル（CLMKabuNewOrder / CLMKabuCorrectOrder / CLMKabuCancelOrder / CLMKabuCancelOrderAll の挙動正本）
- `docs/plan/tachibana/` — 依存先の Phase 1 計画（認証・session・URL ビルダ・codec を再利用）
- `docs/plan/nautilus_trader/` — 将来の置換対象 + REPLAY 仮想注文の引き取り先。本計画の **nautilus 互換不変条件**（[order/spec.md §6](../../../docs/plan/order/spec.md)）の整合先
- `python/engine/exchanges/tachibana_*.py` / `data/src/config/tachibana.rs` / `engine-client/src/` — Phase 1 で実装済みの認証・session・creds 経路（計画との照合基準）
- `C:\Users\sasai\Documents\flowsurface` の `exchange/src/adapter/tachibana.rs` / `src/api/agent_session_state.rs` — 移植元の Rust 実装（フィールド構成・Debug マスク方針・冪等性マップの正本）
- `docs/plan/order/review-fixes-2026-04-25.md` — 直前ラウンドの修正ログ（**存在しない場合はラウンド 1 として新規作成**）。重複指摘を避けるために必ず読む

## ループ手順

### Step 1: レビュー（サブエージェントで並列実行）

以下の 4 観点を **4 つのサブエージェント（general-purpose）** に並列で割り当て、各エージェントに観点別の Findings を返させる。

| エージェント | 観点 |
|---|---|
| A | **文書間整合性** — README.md / spec.md / architecture.md / implementation-plan.md / open-questions.md / SKILL.md 間で矛盾・不整合・旧表記残り（"correct" 用語漏れ・schema 1.2/1.3 のズレ・Phase O0/O1/O2/O3 のスコープ食い違い等）がないか |
| B | **既存実装・依存計画とのズレ** — Phase 1 の `tachibana_auth.py` / `tachibana_session` / `data/src/config/tachibana.rs`、および flowsurface の `tachibana.rs` / `agent_session_state.rs` と本計画の記述（移植元・再利用宣言）が食い違っていないか。`docs/plan/tachibana/` や `docs/plan/nautilus_trader/` の依存記述も照合 |
| C | **仕様漏れ・設計リスク** — SKILL.md の R1〜R10 / EVENT 規約（特に EC フレーム）/ 仮想 URL マスク / Shift-JIS / p_no 採番 / 第二暗証番号取扱い / 誤発注ガード / 冪等性 / 重複検知 のうち、計画文書で未対処または曖昧なままの箇所。**nautilus 互換不変条件**（spec.md §6）から逸脱する設計が他の節に紛れていないかも検査 |
| D | **テスト不足** — 計画に書かれた実装タスクに対して対応するテストケース（受け入れ条件・単体・結合・E2E・誤発注ガード回帰・冪等再送・第二暗証番号マスク・EC 重複検知・session 切れ即停止・REPLAY ガード skip）が明記されていない箇所 |

各エージェントへの指示：
- Findings を **HIGH / MEDIUM / LOW** に分類し、ファイル名・行範囲・具体的な問題箇所を添えて報告する
- すでに `review-fixes-2026-04-25.md` に記録済みの修正と重複する指摘は除外する
- 新規 Finding のみを返す
- **本計画は立花証券単独スコープ**（README.md 長期方針）。他 venue（暗号資産等）への発注経路を本計画に追加すべきという指摘は出さない

### Step 2: 集約と判定

4 エージェントの結果を集約し、重要度順（HIGH → MEDIUM → LOW）に整理する。

**終了条件チェック**:
- HIGH / MEDIUM の Finding がゼロ → ループ終了。最終サマリを出力する
- HIGH / MEDIUM が 1 件以上残っている → Step 3 へ進む

### Step 3: 修正（サブエージェントで並列実行）

HIGH / MEDIUM の Finding を **implementer サブエージェント** に渡して修正させる。

- Finding ごとに独立した修正が可能なものは並列実行する
- 依存関係がある修正（例：spec.md §5.1 の tag 規約変更が architecture.md §10.4 と implementation-plan.md の T1.x に波及する）は直列に実行する
- 修正後、`docs/plan/order/review-fixes-2026-04-25.md` の末尾に「ラウンド N（日付）」セクションを追記し、修正内容を表形式（Finding ID / 観点 / 対象ファイル:行 / 修正概要）で記録する。ファイルがまだ無ければ新規作成する

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
- 計画文書に存在しない新機能・新フェーズ・新 venue を追加してはいけない（計画の範囲内の修正のみ）
- SKILL.md の一次資料（R1〜R10、EVENT 規約）を計画文書側の記述で上書きしてはいけない（SKILL.md が正）
- nautilus 互換不変条件（spec.md §6）に反する修正を入れてはいけない（立花固有の用語・型を HTTP API / IPC / Rust UI 層に漏らさない）
- REPLAY モード仮想注文の実装詳細を本計画に書き戻してはいけない（nautilus_trader Phase N1 のスコープ）
- 立花証券以外の venue への発注経路を追加してはいけない（README.md 長期方針で除外済み。nautilus_trader Phase N3 のスコープ）
