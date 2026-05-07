# Review Fixes — wandb 移管計画 (2026-05-06)

対象:
- `e-station/docs/✅menu-and-footer/wandb-removal-plan.md`
- `🐃_blacksheep/docs/plan/wandb-ownership-migration-plan.md`

## ラウンド 1 (2026-05-06)

### 統一決定

- pii_scrub: 最終的に blacksheep 完全移管。`python/engine/pii_scrub.py` は run-buffer writer 内部限定、boundary には載せない
- stable contract: `narrative.jsonl` を追加（optional）
- 二重 publish 防止: 移行期間中の publish 経路は blacksheep manual のみ
- Phase 番号: 相互参照時 `e-station Phase N` / `blacksheep Phase N` プレフィックス必須
- 用語統一: `publish` / `auth,login,logout` / `run-buffer`
- 機械検証: e-station DoD に grep ゼロ件確認を追加
- 移行ゲート: e-station Phase 6 前提 = blacksheep スクリプト green
- secret masking: blacksheep に stderr masking / env 削除規約
- エンコーディング: `PYTHONUTF8=1` + `encoding='utf-8'`
- ロールバック: 両 plan に branch 戦略
- schema_version: 未知は fail-fast
- SUBMIT_IN_FLIGHT: 新理由 doc or guard 削除を二択明記

### Findings 一覧

| ID | 観点 | 対象 | 修正概要 |
|---|---|---|---|
| A-H1 / BC-B2 | A/B | 両 plan | pii_scrub 所有権を blacksheep 完全移管に統一 |
| A-H2 | A | e-station Sequencing | wiki integration 完了まで gating 追記 |
| D-H1 | D | e-station L152-161 | `tools_actions_for_state.rs` / `mode_switch_blocks_during_submit.rs` を削除候補に追加 |
| D-H2 | D | blacksheep Testing Plan | テストファイル名・実行コマンド・assert を具体化 |
| D-H3 | D | e-station Phase 6 | `rg` ベースの機械検証コマンドを DoD に追加 |
| BC-B1 | B | 両 plan stable contract | `narrative.jsonl` を 4 ファイル目として追加 |
| BC-C1 | C | 両 plan | 移行期間中 publish 経路 1 本ルールを明記 |
| A-M1 | A | 両 plan | Phase 番号にリポジトリ prefix を付ける規約を追加 |
| A-M2 | A | 両 plan | 用語表（publish/auth/run-buffer）追加 |
| A-M3 | A | e-station Goal | blacksheep plan への back-link 追加 |
| A-M4 | A | e-station DoD | blacksheep publish 経路 動作前提を追加 |
| D-M1 | D | blacksheep | publish failure → ingest 停止契約を明記 |
| D-M2 / BC-C4 | D/C | e-station Phase 3 | SUBMIT_IN_FLIGHT 二択を DoD に明記 |
| D-M3 | D | blacksheep auth test | stale netrc / 空 env 等の test ケース列挙 |
| D-M4 | D | e-station Phase 6 前提 | blacksheep CI green チェックリスト |
| BC-C2 | C | blacksheep | secret masking 規約 |
| BC-C3 | C | blacksheep stable contract | schema_version 未知時 fail-fast |
| BC-C5 | C | blacksheep Testing | PYTHONUTF8=1 / encoding='utf-8' / 絵文字 path smoke |
| BC-C6 | C | 両 plan | ロールバック条件と branch 戦略 |
| BC-B3 | B | e-station Phase 5 | examples/wandb/tests 個別判断（pii_scrub 移管に連動） |

LOW（対応不要、列挙のみ）: A-L1〜L5 / D-L1〜L5 / BC-L1〜L5 計 15 件は終了時にまとめて提示。

## ラウンド R3 ユーザー追加指摘 (2026-05-06)

### 追加修正

| ID | 観点 | 対象 | 修正概要 |
|---|---|---|---|
| U-H1 | B | e-station Phase 6 + DoD | grep スコープに `python/` を追加。`python/engine/summary.py` / `python/tests/test_summary.py` の W&B 残骸 docstring も確認対象と明記 |
| U-H2 | B | blacksheep stable contract | run-buffer 親ディレクトリ探索規約（`--source` / OS 別デフォルト / `_default_run_buffer_root()` を `ingest_run.py` と共通化）を stable contract に追加 |
| U-M1 | C | blacksheep `wandb_login.py` | キー入力方式を `--from-stdin` のみに確定。argv 禁止。Secret 規約に正規ルートを明記。Phase 2 完了条件に stdin テストを追加 |

## ラウンド 2 (2026-05-06)

### 統一決定

- blacksheep publish 解禁タイミング: 「e-station Phase 2 マージ完了前は本番運用しない」に統一
- e-station Phase 6 着手ゲート: blacksheep Testing Plan 全件（auth/login/logout/key_masking/publish/pii_scrub/pii_scrub_parity/e_station_path）に揃える
- pii_scrub parity test: `test_pii_scrub_parity.py` を blacksheep Phase 3 DoD + e-station Phase 4 前提ゲートに追加
- e-station 各 Phase 末尾に `cargo check --workspace` 中間ゲートを追加
- narrative.jsonl optional の pytest を Testing Plan 表に追加（test_publish_without/with_narrative_jsonl）
- Open Question の pii_scrub 項目を削除（確定済み）
- exit code 1 = URL 抽出失敗を明記

### Findings 一覧

| ID | 観点 | 対象 | 修正概要 |
|---|---|---|---|
| R2-M1 | A | blacksheep L335 / e-station Risk 4 | publish 解禁文言を「Phase 2 マージ完了前は本番運用しない」に統一 |
| R2-M2 | A | e-station Phase 6 ゲート L227 | blacksheep Testing Plan 全件と揃える |
| R2-H1 | D | blacksheep Phase 3 / test_pii_scrub | pii_scrub parity test を Phase 3 DoD と Phase 4 ゲートに追加 |
| R2-M3 | D | e-station Phase 1〜5 | 各 Phase 末尾に cargo check 中間ゲートを追加 |
| R2-M4 | D | blacksheep Testing Plan | narrative.jsonl optional の pytest 2 件を追加 |
| R2-M5 | D | blacksheep exit code 規約 | URL 抽出失敗 = exit 1 を追記 |
| R2-L2 | A | e-station Open Questions | pii_scrub 項目を削除（既に確定）|
