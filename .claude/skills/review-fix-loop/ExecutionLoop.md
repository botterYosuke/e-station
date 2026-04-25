# Review-Fix Loop 起動プロンプト（汎用テンプレート）

このファイルは [`SKILL.md`](./SKILL.md) を実行するための **汎用呼び出しプロンプト**。新しいフェーズ・PR を仕上げるときにオーケストレーター（あなた）に貼り付けて使う。`{{}}` プレースホルダーを実値に置換すること。

---

あなたは **オーケストレーター** です。`{{repo_name}}` リポジトリで `{{feature}}` のフェーズ `{{phase_id}}` 「`{{phase_title}}`」を レビュー → 修正のループで仕上げてください。

## 唯一のリファレンス

すべての手順・不可侵ルール・収束基準・既知の落とし穴は [`SKILL.md`](./SKILL.md) に集約されています。**SKILL.md を最初に読み、以後それを単一の真実とすること**。本ファイルは入力パラメータのみを与えます。

特に以下は SKILL.md で明示されているので**勝手に判断しない**:
- 並列起動・サブエージェント選定・プロンプトテンプレ
- 重要度分類・収束基準・繰越承認フロー
- TDD 順守・uv run 利用・secrets 取扱い
- `Phase O1 / 繰越` への降格はユーザー権限（subagent の独断禁止）
- silent-failure-hunter は毎ラウンド必須
- subagent の「全緑」主張は実コマンドで裏取り

## 入力パラメータ

### 必読ドキュメント

```text
{{plan_doc}}              # 例: docs/plan/tachibana/implementation-plan.md
{{spec_doc}}              # 例: docs/plan/tachibana/spec.md
{{architecture_doc}}      # 例: docs/plan/tachibana/architecture.md
{{open_questions_doc}}    # 例: docs/plan/tachibana/open-questions.md
{{feature_skill}}         # 例: .claude/skills/tachibana/SKILL.md
.claude/skills/bug-postmortem/MISSES.md
.claude/skills/review-fix-loop/SKILL.md
.claude/skills/tdd-workflow/SKILL.md
CLAUDE.md
```

### レビュー対象スコープ

```text
{{file_list}}             # 例:
                          # data/src/config/tachibana.rs
                          # engine-client/src/{dto,error,process}.rs
                          # python/engine/...
```

### プロジェクト固有の検証コマンド

完了時にすべて緑であること:

```bash
{{verify_cmds}}
# 例（e-station）:
# cargo check --workspace
# cargo clippy --workspace -- -D warnings
# cargo fmt --check
# cargo test --workspace
# uv run pytest {{test_glob}} -v
```

### 起動するレビュアー

スコープに合わせて選定。GUI を含まないなら `iced-architecture-reviewer` を、Rust が無いなら `rust-reviewer` を省略する。

```text
{{reviewers}}
# デフォルト推奨セット（フルスタック変更時）:
# rust-reviewer, silent-failure-hunter, iced-architecture-reviewer,
# type-design-analyzer, ws-compatibility-auditor, general-purpose
```

### スコープ外（subagent が触らないこと）

```text
{{out_of_scope_paths}}
# 例:
# docs/plan/<other-phase>/      # 別フェーズの計画書
# .claude/skills/<other>/       # 他のスキル
# 上記以外の untracked artifact
```

## 進捗反映先

- 計画書: `{{plan_doc}}` の `§{{phase_id}}` 末尾に「レビュー反映 (YYYY-MM-DD, ラウンド N)」ブロックを追記
- スタイル参考: `{{plan_doc_style_ref}}` （例: `implementation-plan.md` T2 セクション L154-185）

## 開始

1. 上記必読ドキュメントを読み、`{{plan_doc}}` の `§{{phase_id}}` の現状を把握する
2. SKILL.md の Phase 0（裏取り）→ Phase 1（並列レビュー）から開始する
3. 各ラウンドの集約・修正・再レビューは SKILL.md の手順に従う
4. **MEDIUM 以上ゼロ** で終了。ループ完了後にユーザーへ最終サマリ（ラウンド毎の件数推移・繰越項目・新規追加テスト）を報告する

---

## テンプレート利用ガイド

### 新フェーズへの適用手順

1. このファイルをコピー（例: `CreateLoop-{{phase_id}}.md`）。元ファイルは汎用テンプレートとして温存
2. `{{}}` プレースホルダーを実値に置換
3. ユーザーに「このプロンプトで起動してよいか」確認後、貼り付けて開始

### 既知の罠（SKILL.md からの抜粋）

これらは **subagent が再発させやすい** ので、必要なら本プロンプトの「制約」節に明示再掲する:

- subagent が「影響範囲が大きい」「DTO restructuring が必要」を理由に **Phase O1 へ独断降格** する → プロンプトで「降格判断はユーザー権限。困ったら STOP+REPORT」を明記
- subagent が **対象ファイル外（隣接フェーズの docs 等）を編集** することがある → 「out_of_scope_paths は触らない」を明記
- Python プロジェクトで素の `python` を使う → 「`uv run` 必須」を明記
- `cargo fmt --all` が無関係ファイルまで sweep → コミット時に明示ステージング
- `From<inner>` を残した newtype は意図無効化 → newtype 導入時は `From` 撤廃を要求
- `#[doc(hidden)] pub` は production 漏出 → test-only API は `[features] testing = []` で gate
- `tokio::spawn` JoinHandle 捨て → `Mutex<Option<JoinHandle>>` で保持し abort+await
- 正規表現ソース検査は tuple unpack / walrus で false negative → AST ベースに昇華
- test sentinel と `.env` 値の衝突 → `TEST_SENTINEL_*` 形式で realistic value と分離

詳細・根拠・実例はすべて [`SKILL.md`](./SKILL.md) を参照。

---

## 参考: 立花 T3 R6-R9 の実例（インスタンス化済み）

このテンプレートを T3 phase で実例化したときのパラメータ:

```text
repo_name:           e-station
feature:             立花証券統合
phase_id:            T3
phase_title:         クレデンシャル受け渡し配線
plan_doc:            docs/plan/tachibana/implementation-plan.md
spec_doc:            docs/plan/tachibana/spec.md
architecture_doc:    docs/plan/tachibana/architecture.md
open_questions_doc:  docs/plan/tachibana/open-questions.md
feature_skill:       .claude/skills/tachibana/SKILL.md
test_glob:           python/tests/test_tachibana_*.py
out_of_scope_paths:  docs/plan/order/
                     .claude/skills/<other>/
                     docs/wiki/
plan_doc_style_ref:  docs/plan/tachibana/implementation-plan.md L154-185 (T2 スタイル)
verify_cmds:         cargo check --workspace
                     cargo clippy --workspace -- -D warnings
                     cargo fmt --check
                     cargo test --workspace
                     uv run pytest python/tests/test_tachibana_*.py -v
reviewers:           rust-reviewer, silent-failure-hunter, iced-architecture-reviewer,
                     type-design-analyzer, ws-compatibility-auditor, general-purpose
```

実測収束: R6 (27 件) → R7 (15 件) → R8 (7 件) → R9 (0 件、収束)。詳細は SKILL.md「適用例」を参照。
