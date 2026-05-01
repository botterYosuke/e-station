# Review Fixes Log — replay-script-cli-args.md

対象: `docs/✅nautilus_trader/replay-script-cli-args.md`  
開始日: 2026-04-30

---

<!-- ラウンドごとに「## ラウンド N（日付）」ブロックを追記 -->

## ラウンド 1（2026-04-30）

### 統一決定

- 修正対象は `replay-script-cli-args.md` のみ。実装ファイル本体には触れない
- LOW は対応不要（PlanLoop の収束基準）。HIGH/MEDIUM のみ修正

### Findings

| ID | 観点 | 重大度 | 対象ファイル:行 | 修正概要 |
|----|------|--------|----------------|---------|
| M1 | C 仕様漏れ | MEDIUM | replay-script-cli-args.md:31-39 | `PORT` env var（既定 `9876`）が任意 env 一覧に未記載。`replay_dev_load.sh:17,26,33` で参照されているため表に追加 |
| M2 | C 仕様漏れ | MEDIUM | replay-script-cli-args.md:41-45 | VSCode `inputs` の `id`/`type`/`description`/`default`/`options` が記載されておらず `tasks.json` から復元不能。表形式で 4 項目を明記 |
| L1 | C  | LOW | 全体 | 引数バリデーション（`${VAR:?}` による即終了・ISO8601 ノーチェック）の振る舞い未記載 — 対応不要 |
| L2 | D  | LOW | 全体 | 受入確認・`.env` source 再混入リグレッションガード未記載 — 対応不要 |
| L3 | C  | LOW | L14 | Breaking change 注意書きなし — 対応不要 |
| L4 | C  | LOW | 変更ファイル表 | live モード等への副作用なしの明示なし — 対応不要 |
| L5 | A  | LOW | L59 | `.env.example` から「もともと無かった」のか「削除した」のかが曖昧 — 対応不要 |

