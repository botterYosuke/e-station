# review-fixes — fix-ladder-tick-size-2026-04-28

対象: `docs/plan/✅tachibana/fix-ladder-tick-size-2026-04-28.md`

<!-- ラウンドごとに末尾に追記する -->

## ラウンド 1（2026-04-28）

### 統一決定
1. "Phase 2" 表記 → 「将来フェーズ（動的 tick 更新）」に統一し `open-questions.md` 登録リファレンス追加
2. `pDPP`「現値」→「直近終値（`pDPP`）」に統一
3. C-H1/D-H1/D-H2（同一エッジケース群）: エッジケース表追加 + テスト方針ネガティブケース明記
4. `FetchTickerStats("__all__")` パスでキャッシュ非更新の注記追加
5. セッションリセット vs プロセス再起動 を区別してエッジケース表を明確化

### Findings 一覧

| Finding ID | 観点 | 重要度 | 対象箇所 | 修正概要 |
|---|---|---|---|---|
| C-H1 | C | HIGH | エッジケース表 | `fetch_ticker_stats` 例外・空レスポンス行を追加 |
| D-H1 | D | HIGH | テスト方針 | ネガティブケース 3 件（失敗時非汚染・pDPP=""・InvalidOperation catch）を追記 |
| D-H2 | D | HIGH | テスト方針 | フォールバック assert 内容（min_ticksize==0.1）を明記 |
| A-M1 | A | MEDIUM | L175 付近 | "Phase 2 送り" → 将来フェーズ送り（open-questions 登録予定）に修正 |
| A-M2 | A | MEDIUM | 影響範囲節 | depth_stream() 関数名引用 → ファイル参照に留める |
| B-M1 | B | MEDIUM | 擬似コード 1-C | `code` 変数の由来コメント追加 |
| B-M2 | B | MEDIUM | 全体 | `pDPP`「現値」→「直近終値（pDPP）」に統一 |
| C-M1 | C | MEDIUM | エッジケース表 | セッションリセット行にプロセス再起動時の挙動を追記 |
| C-M2 | C | MEDIUM | 修正方針節 | `__all__` パスでキャッシュ非更新の注記を追加 |
| D-M1 | D | MEDIUM | テスト方針 | 実行コマンド・assert 期待値を追記 |
| D-M2 | D | MEDIUM | テスト方針 | fetch→cache→list 連鎖統合テストを計画に追加 |
| A-L1 | A | LOW | — | CLAUDE.md テスト表更新（次フェーズで対応） |
| A-L2 | A | LOW | — | InvalidOperation インポート明記（次フェーズで対応） |
| C-L1 | C | LOW | — | asyncio 安全性の明記（次フェーズで対応） |
| D-L1 | D | LOW | — | CI ゲート組込記述（次フェーズで対応） |
| D-L2 | D | LOW | — | 並列安全性テスト計画（GIL 保護、対応不要と判断） |

## ラウンド 2（2026-04-28）

収束確認: **HIGH/MEDIUM ゼロ** — ループ終了

### 残存 LOW（対応不要）

| Finding ID | 観点 | 対象箇所 | 内容 |
|---|---|---|---|
| R2-L1 | B | 擬似コード 1-B | 空レスポンス早期 return と `first` 確定後のキャッシュ挿入位置について一言コメント追記を推奨 |
| R2-L2 | D | テスト方針 | `aCLMMfdsMarketPrice=[]` の空レスポンスネガティブケースが未記載（実装時に補完推奨） |
| R2-L3 | C | エッジケース表 | `spec §3.2` の参照がファイルパス未記載（自己完結性の改善余地あり） |
