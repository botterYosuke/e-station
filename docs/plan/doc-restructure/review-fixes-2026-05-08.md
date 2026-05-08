# docs 再編計画 review-fixes

対象計画書: `~/.claude/plans/e-station-c-users-sasai-documents-e-stat-frolicking-parnas.md`

## ラウンド 1（2026-05-08）

### 統一決定
1. 旧パスは `docs/✅<module>/...` 形式で絶対化
2. archive 参照は `docs/✅<module>/archive/...` 形式（リポジトリ直下に `archive/` は無い）
3. モジュール名は旧 `nautilus_trader`（ディレクトリ名）／新 `nautilus-trader` の正規化を冒頭で宣言
4. `docs/plan/`（distribution-formats / floating-windows / ❌archive）を再編スコープに取り込む
5. `proto/engine.proto`+`scripts/check_schema_parity.py` を IPC 一次ソースに加える（protobuf 補助系統断定を撤回）
6. SCHEMA_MINOR SoT は `engine-client/src/lib.rs` の履歴コメント
7. 実行ステップ 6.5「検証スクリプトと CI ゲート整備」を新設（4 つの verify_*.py スクリプト + CI required check 化）
8. ADR ナンバリングは 0001 開始、manifest は `decisions/MANIFEST.md`（番号外 artifact）
9. ❌ プレフィックスは ADR 抽出候補から除外
10. 中間 PR 期間は `mkdocs-redirects` で旧 URL 救済 + 各 PR で `mkdocs build --strict` 緑
11. `mkdocs.yml` `exclude_docs` 方針表（`*.json`/`wiki/**`/`plan/**`/`*/archive/**`/`roadmap/**`）を計画書に追加
12. index.md ナビは「エンジニア / コントリビュータ」2 区分。エンドユーザーは GitHub Wiki 外部導線、運用 runbook は `development/troubleshooting.md` に集約
13. Wiki link 検証は `lychee --offline` 等。`gh api` には link checker は無い

### Findings 一覧

| ID | 観点 | 重要度 | 対象（計画書セクション） | 修正概要 |
|----|------|--------|--------------------------|----------|
| H1 (A1+C1) | A/C | HIGH | Context, 移送マッピング, 実行ステップ | `docs/plan/` 配下の扱いをマッピング表・実行ステップ・主要ファイルに追記 |
| H2 (A2+B2) | A/B | HIGH | ADR リスト, 既存資産表, 実行ステップ 4 | archive パスを `docs/✅<module>/archive/...` に正規化 |
| H3 (A3) | A | HIGH | 移送マッピング, 実行ステップ 3 | kabusapi 行追加、menu-and-footer の roadmap 行先記載、PR 順を nautilus-trader に統一 |
| H4 (B1) | B | HIGH | 既存資産表 (ipc-schema/ipc-protocol) | proto/engine.proto + scripts/check_schema_parity.py を一次ソースに追加、protobuf「補助系統」断定を撤回 |
| H5 (C2) | C | HIGH | 新ディレクトリ構成, 移送マッピング | `reference/schemas/` 新設、`{commands,events}.json` の据え置き先を明記 |
| H6 (C3) | C | HIGH | 主要ファイル | `mkdocs.yml` `exclude_docs` 方針表を追加 |
| H7 (D1+D2+D5+D6) | D | HIGH | 実行ステップ, 検証 | 実行ステップ 6.5 を新設、4 つの verify_*.py を CI required 化、manifest を YAML 機械可読 |
| H8 (D3) | D | HIGH | 検証 | grep 対象を限定列挙、互換文字 + 旧モジュール path string + URL エンコード形を網羅 |
| H9 (D4) | D | HIGH | Wiki 移送手順 5, 検証 | `gh api wiki` link checker 認識誤りを撤回、`lychee --offline` 等に置換 |
| A4 | A | MED | 移送マッピング | 旧パス全件を `docs/✅<module>/...` に絶対化 |
| A5 | A | MED | 構成・PR 順 | `nautilus-adapter.md` を `nautilus-trader.md` に修正、PR 順を `nautilus-trader` に統一 |
| A6 | A | MED | 構成コメント, マッピング | `current-architecture.md` 由来明記、`data-mapping.md` の行先を `external-apis/tachibana.md` に明示 |
| A7 | A | MED | 見出し | 「（HIGH→MEDIUM 修正分）」「（HIGH 修正分）」見出しサフィックス削除 |
| A8 | A | MED | 既存資産表 | `roadmap/changelog.md` を年表のみ、SoT を `engine-client/src/lib.rs` に明記 |
| B3 | B | MED | 移送マッピング | `venue-login-footer.md`・`menu-and-footer/assets/**` 行を追加 |
| B4 | B | MED | 移送マッピング (benchmarks) | `testing/benchmarks.md` は索引のみ、元 md は `testing/benchmarks/` 据え置き |
| B5 | B | MED | 主要ファイル, 検証 | README/AGENTS/CLAUDE の rewrite 対象に追記、壊れたリンク修正を主要ファイルに記載 |
| B6 | B | MED | 既存資産表 | SCHEMA_MINOR SoT を `engine-client/src/lib.rs` の履歴コメントと明記 |
| C4 | C | MED | 実行ステップ 3 | `mkdocs-redirects` plugin による旧 URL 救済を merge gate に追加 |
| C5 | C | MED | 表記正規化ルール | ❌ プレフィックス据え置きを冒頭ルール化 |
| C6 | C | MED | 実行ステップ 6.5 | `verify_migrated_from.py` を CI 化 |
| C7 | C | MED | 実行ステップ 3 | 各 PR の merge gate (a)-(d) を明記 |
| C8 | C | MED | 構成 (index.md) | 2 区分に縮約、ops 集約先を記述 |
| C9 | C | MED | 構成 (decisions/) | `MANIFEST.md` を番号外 artifact として併設 |
| C10 | C | MED | 既存資産表 (採用根拠 ADR 棚卸し) | iced/Bevy/nautilus/tachibana/kabusapi 採用根拠 ADR の棚卸しサブタスクを追加 |
| D7 | D | MED | 検証 | `scripts/check_adr_status.py` で deferred ADR の本文空・解除時出典必須を CI 検査 |
| D8 | D | MED | 実行ステップ 3 | PR 単位 merge gate (a)-(d) を明記 |
| D9 | D | MED | 検証 | Wiki 移送 PR で `exclude_docs` 変更前後の両 commit で `mkdocs build --strict` 緑 |
| D10 | D | MED | 検証 | `scripts/verify_nav_depth.py` で nav 4 階層上限を CI 検査 |
| D12 | D | MED | 検証 | invariant 項目に `INV-<MODULE>-NNN` ID を付与し manifest で 1:1 対応 |
| A9, A10, B7-B9, C11, D11 | — | LOW | — | 対応不要、参考記録のみ |

### 機械検証要点

- `Grep "(HIGH→MEDIUM 修正分|HIGH 修正分|0000-doc-restructure|nautilus-adapter)"`: 0 件（修正適用済み）
- `Grep "^| \`✅"`: 0 件（移送マッピング表の旧パスはすべて `docs/` 接頭済み）
- `Grep "archive/refactor|archive/review-fixes"` で `docs/` プレフィックスなしの参照: 0 件
- `Grep "protobuf は補助系統"`: 0 件
- 新設項目の行数増分: 約 +120 行（実行ステップ 6.5・方針表・採用根拠 ADR 棚卸し・YAML スキーマなど）

### 残存

ラウンド 1 で全 HIGH 9 件・MEDIUM 17 件を反映、LOW は対応不要方針。

## ラウンド 2（2026-05-08）

### 統一決定（追加）
14. venue 固有仕様は `docs/specs/venues/<venue>.md` に分離、`live-strategy.md` は venue 横断の抽象契約のみ
15. `mkdocs-redirects` plugin と依存ファイルを「主要ファイル」に追記
16. manifest は YAML 単一源（`_migration-ledger.yaml` → `decisions/manifest.yaml`）、`MANIFEST.md` は人間向け索引のみ
17. ADR 遷移ルール表を `decisions/README.md` に明記、`check_adr_status.py` 責務を 6.5 に追加
18. 言語選択 ADR を棚卸し対象に追加
19. `verify_nav_depth.py` のスコープは nav section 深さ、ファイル自身は除外
20. `docs/plan/floating-windows/archive/` は ADR 抽出候補に含める（❌ 無し）
21. floating-windows の進行中/完了/停止 判定基準を実行ステップ 1 に明記

### Findings 一覧

| ID | 観点 | 重要度 | 対象 | 修正概要 |
|----|------|--------|------|----------|
| R2-C1 | C | HIGH | specs/ 構成・移送マッピング | venue 固有 spec を `specs/venues/{tachibana,kabusapi}.md` に分離、`live-strategy.md` は抽象契約のみに縮約 |
| R2-C2 | C | HIGH | 主要ファイル | `mkdocs-redirects` plugin + 依存ファイルを主要ファイルに追記 |
| R2-AB1 | A/B | MED | 実行ステップ 3 | PR 順を `docs/plan/distribution-formats` と `docs/plan/floating-windows` の 2 PR に分割 |
| R2-AB2 | A/B | MED | 既存資産表 | ADR 0002 出典に `engine-client/src/lib.rs`・proto・check_schema_parity を追加 |
| R2-AB3 | A/B | MED | exclude_docs 方針表 | `_migration-ledger.md` → `_migration-ledger.yaml` に修正 |
| R2-AB4 | A/B | MED | 移送マッピング | `floating-windows/archive/` は ADR 抽出候補に含めることを明示 |
| R2-D1 | D | MED | 実行ステップ 6.5・8・履歴保持 | `manifest.yaml` を rename 後の単一 YAML 源に統一、`MANIFEST.md` は人間向け索引と切り分け |
| R2-C3 | C | MED | 実行ステップ 1 | 進行中/完了/停止 判定基準（90日 commit、open-questions 残）を明記 |
| R2-C4 | C | MED | specs/ 構成 | `replay.md`・`backtest.md` の出典分担と cross-link を明文化 |
| R2-C5 | C | MED | decisions/ 構成・実行ステップ 6.5 | ADR ステータス遷移ルール表を新設、`check_adr_status.py` 責務明記 |
| R2-C6 | C | MED | 採用根拠 ADR 棚卸し | Rust+Python / Rust / Python の 3 言語選択根拠を棚卸し対象に追加 |
| R2-C7 | C | MED | 実行ステップ 6.5 | `verify_nav_depth.py` のスコープを nav section 深さに限定 |
| R2-D2 | D | LOW | — | 対応不要（参考記録のみ） |

### 機械検証要点

- `Grep "(MANIFEST\.md.*埋め込み|ledger\.md|HIGH→MEDIUM|protobuf は補助)"`: 0 件
- `Grep "(manifest\.yaml|venues/|mkdocs-redirects|verify_*|check_adr_status)"`: 19 ヒット（適用済み）

### 残存

HIGH 0、MEDIUM 2、LOW 3。次ラウンドへ。

## ラウンド 3（2026-05-08）

### 統一決定（追加）
22. redirect map は `mkdocs.yml` の `plugins.redirects.redirect_maps:` 単一累積先（書式 `old_path: new_path`）。別ファイルは新設しない
23. 言語選択 ADR は 0006 (Rust+Python)、0007 (Rust)、0008 (Python) として番号予約

### Findings 一覧

| ID | 観点 | 重要度 | 対象 | 修正概要 |
|----|------|--------|------|----------|
| R3-1 | A | MED | 主要ファイル | redirect map の単一累積先を `mkdocs.yml plugins.redirects.redirect_maps:` と明示、書式を 1 行例示 |
| R3-2 | A | MED | decisions/ 構成・棚卸し | ADR 0006-0008 を予約、棚卸し各項目に ADR 番号候補を付ける |
| R3-3, R3-4, R3-5 | C | LOW | — | 対応不要（cross-link anchor 命名・責務表脚注・exclude_docs 文言は LOW、対応で plan が冗長化するため見送り） |

### 機械検証要点

- `Grep "(redirect_maps|0006-language|0007-rust|0008-python|ADR 0006 候補)"`: 5 ヒット（適用済み）

### 残存

HIGH 0、MEDIUM 0、LOW 3。**収束**。LOW のみ残存のため終了。

