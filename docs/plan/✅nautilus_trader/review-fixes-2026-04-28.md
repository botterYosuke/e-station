---
date: 2026-04-28
status: converged
scope: docs/plan/✅nautilus_trader/{README,spec,architecture,implementation-plan,open-questions}.md
trigger: archive/replay-ui-role-revision-2026-04-28.md（R7 確定）の 5 文書反映後の review-fix-loop
rounds: 2
---

# nautilus_trader 計画文書 review-fix ログ（2026-04-28）

`archive/replay-ui-role-revision-2026-04-28.md` の決定 D1〜D9 / Q13〜Q15 / N1.11〜N1.16 を 5 文書に反映した後の review-fix-loop の記録。

## ラウンド 1（2026-04-28）

### 統一決定

- Open question 相互参照は `[Q13](./open-questions.md#q13)` 形式（小文字アンカー）で統一
- spec §4 API 表は **3 列構成** `| エンドポイント | body | IPC 写像 |` に再構成
- `--mode` は「必須・デフォルトなし」を維持し、spec/impl で「(D8 起動時固定の踏襲、デフォルトなし)」と決定 ID 参照を補強
- 計画文書から archive への相対パスは `./archive/...` 形式で統一
- architecture §2 Hello/Ready サンプル JSON に `mode: "live"|"replay"` を追加（D8/N1.13 整合）

### Findings 件数

- HIGH: 0
- MEDIUM: 4
- LOW: 5
- 合計: 9

### Findings 一覧（表形式）

| Finding ID | 観点 | 重大度 | 対象ファイル | 修正概要 |
|---|---|---|---|---|
| D-M1 | D | MEDIUM | spec.md §4 API 表 | API 表を 3 列化（エンドポイント / body / IPC 写像）、`Command::SetReplaySpeed` を独立列へ |
| D-M2 | D | MEDIUM | spec.md §2.2.5 N1.13, impl N1.13 | `--mode` 必須化の根拠を D8 §6 起動例参照で補強 |
| B-M1 | B | MEDIUM | spec §2.2.5 N1.13, impl N1.13 | Q15（ランタイム切替）への相互参照を追加 |
| B-M2 | B | MEDIUM | spec §2.2.5 N1.12, impl N1.12 | Q13（signal_kind 語彙）への相互参照を追加、wire 表現は暫定 enum と注記 |
| A-L1 | A | LOW | architecture §2 | Hello/Ready サンプル JSON に `mode` フィールドを追加 |
| A-L2 | A | LOW | spec §4 API 表 | `/api/replay/load` 備考に MAX_REPLAY_INSTRUMENTS=4 / 400 を明示 |
| A-L3 | A | LOW | README §Rust UI の役割境界 | Q14 への Markdown リンクを追加 |
| D-L1 | D | LOW | spec.md L78 付近 | archive への相対パスを `./archive/...` に統一 |
| D-L2 | D | LOW | architecture §6.1 | Tpre.1 spike リンクにアンカーを付与 |

### 観点別収束状況（ラウンド 1 終了時点、修正前のレビュー結果）

- 観点 A: HIGH/MEDIUM ゼロ。LOW 3 件
- 観点 B: HIGH ゼロ、MEDIUM 2 件（M1: Q15 参照欠落 / M2: Q13 参照欠落）
- 観点 C: HIGH/MEDIUM/LOW すべてゼロ → **完全収束**
- 観点 D: HIGH ゼロ、MEDIUM 2 件、LOW 2 件

ラウンド 1 で 9 件すべてに修正を適用し、ラウンド 2 で再レビューを実施する。

## ラウンド 2（2026-04-28）

### 機械検証（grep ベース、ラウンド 1 修正の波及確認）

| 検査項目 | 期待 | 実測 |
|---|---|---|
| `open-questions.md#q1[345]` 参照 | spec / impl / README に分散 | spec L90, L97 / impl L257, L277 / README L91 で確認 |
| `PauseReplay\|ResumeReplay\|SeekReplay` の混入 | 5 反映文書に出現せず（archive のみ） | archive のみ（OK） |
| `カウンタ初期化` 表現の混入 | 5 反映文書に出現せず | archive のみ（R5 履歴） |
| `schema_minor` の値 | `4` で統一 | spec / arch / impl すべて 4。誤値なし |

### 観点合算 2 並列再レビュー結果

- 観点 A+B 合算: HIGH 0 / MEDIUM 0 / LOW 0 → **収束**
  - 受け入れ条件 16 項目すべて 5 文書に反映済みを確認
  - spec §4 API 表 3 列化に副作用なし（既存行は `—` プレースホルダで統一）
- 観点 C+D 合算: HIGH 0 / MEDIUM 0 / LOW 0 → **収束**
  - `[Q13](./open-questions.md#q13)` / `[Q15](./open-questions.md#q15)` のアンカーが open-questions.md の `### Q13.` / `### Q15.` の GitHub slug と一致
  - `[Tpre.1 spike](./implementation-plan.md#tpre1-clock-注入-feasibility-プロトタイプh4--完了-2026-04-26)` のアンカーが impl-plan の §Tpre.1 ヘッダ slug と一致
  - N1.12 / N1.13 のチェックボックスが一次資料比 +1 ずつ増えているが、これはラウンド 1 で意図的に挿入した Q13 / Q15 参照行の差分であり regression ではない

### Findings 件数

- HIGH: 0
- MEDIUM: 0
- LOW: 0
- 合計: 0 → **review-fix-loop 終了**

## 完了サマリ

- 全ラウンド数: 2
- 修正した Finding 総数: HIGH 0 / MEDIUM 4 / LOW 5（ラウンド 1 で全件解消）
- 残存 LOW: 0
- 主要な反映成果（規約レベル）:
  - **API 表規約**: spec §4 を「エンドポイント / body / IPC 写像」3 列構成に統一。新設 IPC 写像列は今後の REST↔IPC 写像追加の標準形
  - **Open question 参照規約**: 計画文書から open-questions への参照は `[Q<N>](./open-questions.md#q<n>)`（小文字 GitHub slug）形式で統一
  - **archive 相対パス**: 計画文書から archive への参照は `./archive/...` 形式で統一
  - **Hello/Ready ハンドシェイクの `mode` フィールド**: schema_minor=4 / N1.13 / D8 起動時固定と整合する形で architecture §2 サンプルに明示
  - **N1.13 の `--mode` 必須化**: D8 §6 起動例の踏襲として spec / impl 両方で根拠を明示
- ログ: docs/plan/✅nautilus_trader/review-fixes-2026-04-28.md
