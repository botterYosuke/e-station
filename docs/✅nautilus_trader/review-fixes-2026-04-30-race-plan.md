# Review Fixes Log — replay-load-start-race-fix-plan.md

対象: `docs/✅nautilus_trader/replay-load-start-race-fix-plan.md`
開始日: 2026-04-30

---

## ラウンド 1（2026-04-30）

### 統一決定（実コード調査ベース）

- **D1**: ack 配置 = 同期戻り直後で確定（`set_content_and_streams` が `Dashboard::auto_generate_replay_panes` 内で同期完了することを実コードで確認済み）。「Task 末尾移動」TODO は削除
- **D2**: timeout デフォルト = release 10s / debug 30s の二段化（`cfg!(debug_assertions)`）+ env var `REPLAY_PANE_READY_TIMEOUT_S` で上書き可
- **D3**: 現状 emitter callsite は `replay_load` の 1 箇所のみと確定記載（他 grep ヒットはテスト内 match pattern）
- **D4**: control_tx bound = production 64 / tests 8（実コード確認値、十分余裕あり）
- **D5**: 行番号参照 → シンボル名参照（`Flowsurface::update` の `AutoGenerateReplayPanes` arm / `Dashboard::auto_generate_replay_panes` / `ControlApiCommand` enum 定義）に置換
- **D6**: 波及更新対象に `.claude/CLAUDE.md` 「IPC イベントの流れ」節を追加
- **D7**: pin test 追加 — `ack=None` 互換 / 504 後の再 load idempotency / `replay_pane_registry.is_loaded` ガード
- **D8**: `Notify` の Debug 出力で Arc アドレスがログに出る副作用を「実装の落とし穴」に明記
- **D9**: A+B-H1（番号順崩れ）は誤認識と判定 — 実ファイルには第五原因セクションが**未存在**。「並べ直し」ではなく「新規追記」と修正

### Findings

| ID | 観点 | 重大度 | 対象:箇所 | 修正概要 |
|----|------|--------|-----------|---------|
| AB-H1 | A | HIGH→修正 | replay-launch-empty-pane-issue.md / 計画書 冒頭・修正範囲 | 「第五原因の順序」ではなく「未追記」と判明。修正範囲表で「**新規追記**」と明示。冒頭関連リンクも訂正 |
| AB-H2 / CD-H1 | B,C | HIGH→修正 | 計画書「設計」「Iced 側」 | ack 配置を実コード調査結果（subscription bind は同期完了）で確定。Task 末尾推奨の TODO 化を削除 |
| CD-H2 | C | HIGH→修正 | 計画書「実装の落とし穴」 | control_tx bound = 64（production）と確定値を記載。TODO 文言を削除 |
| CD-H3 | D | HIGH→修正 | 計画書「テスト計画」 | pin test を 3→5 件に拡張（504 後再 load idempotency / 二重 pane 抑止）。手動検証チェックリストを E2E 補完として明記 |
| AB-M1 | B | MEDIUM→修正 | 計画書全体 | 行番号参照を `Flowsurface::update` arm / `Dashboard::auto_generate_replay_panes` / `ControlApiCommand` enum 等のシンボル参照に置換 |
| AB-M2 | B | MEDIUM→修正 | 計画書「実装の落とし穴」 | `Notify` の Debug 出力で Arc アドレスがログに出る副作用を追記 |
| AB-M3 | A | MEDIUM→修正 | 計画書「波及更新」「修正範囲」 | `.claude/CLAUDE.md` の「IPC イベントの流れ」節を波及更新対象に追加 |
| AB-M4 | B | MEDIUM→修正 | 計画書「実コード調査結果」 | 「他 callsite は 1 箇所のみ」を確定値として記載 |
| CD-M1 | C | MEDIUM→修正 | 計画書「設計」 | timeout を release 10s / debug 30s に二段化、env var 上書き可と明記 |
| CD-M2 | C | MEDIUM→修正 | 計画書「API 契約変更」 | 504 後の遅延 ack 整合性ロジックを明記（`replay_pane_registry.is_loaded` ガード） |
| CD-M3 | D | MEDIUM→修正 | 計画書「テスト計画」 | `ack=None` 経路の pin は将来用互換テストとして明記 |
| CD-M4 | D | MEDIUM | 計画書「テスト計画」 | replay モードで live subscription 不在の pin は本計画スコープ外（既存 replay モード設計の前提条件）— 計画書では言及せず |
| AB-L1〜L3 | - | LOW | - | 対応不要（PlanLoop 収束基準） |
| CD-L1 | D | LOW→修正 | 計画書「テスト計画」 | 実行コマンドを `cargo test -p flowsurface --lib replay_load_` に確定 |
| CD-L2 | A | LOW | - | 文書間 cross-update は実装時 TODO（finding 扱いせず） |

## ラウンド 2（2026-04-30 / サニティ確認）

### 結果

**収束**: HIGH/MEDIUM ゼロ。R1 統一決定 D1〜D9 が計画書全体に整合的に波及。新規矛盾なし。

### 残存 LOW（対応不要・参考）

| ID | 観点 | 重大度 | 対象:箇所 | 内容 |
|----|------|--------|-----------|------|
| R2-L1 | A | LOW | 計画書:5 | 冒頭リンク行「追記予定」と修正範囲表「新規追記」の表記揺れ |
| R2-L2 | A | LOW | 計画書:135-145 | 擬似コードコメントの変数名（`notified` vs `wait`）対応がやや読みにくい |
| R2-L3 | C | LOW | 計画書:264 | テスト 2 タイムアウト注入手段の二択（env var or 新メソッド）が冗長 |
| R2-L4 | C | LOW | 計画書:309 | `is_lone_starter` 経路確認が TODO のまま残存 |

### 機械検証結果

- 行番号スタイル参照（`#L\d+`）: 残存 0 件（R1 で全置換済み）
- 「実装時に確認」TODO: 1 件残存（R2-L4 該当・LOW）
- `oneshot` 残存: 2 件（理由説明文として正当・修正不要）

