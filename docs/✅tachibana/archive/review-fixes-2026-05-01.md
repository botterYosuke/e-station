# review-fixes — fix-min-ticksize-fallback-2026-05-01

対象: `docs/✅tachibana/fix-min-ticksize-fallback-2026-05-01.md`  
開始日: 2026-05-01

---

## ラウンド 1（2026-05-01）

### 統一決定
1. T1 説明を GroupedDepth 経由（grouped_asks/grouped_bids）に修正
2. 行番号リンクを #L200（関数定義）/ L237（fallback 行）に修正
3. 関数シグネチャを 3引数形式に修正
4. 空板エッジケースを §4 T1 に追記
5. step 移行不変条件を §4 T1 に追記
6. T3 に実行コマンド列を追加
7. T3 に negative test 3件追加（empty_book / one_side_only / price_format）
8. リグレッションガード §5 に grep 手順追記
9. §6 手順2を自動確認（test_ladder_sparse_price_format）に変更
10. §7 将来対応に行間 gap ラベル項目を追加

### Findings 表

| Finding ID | 観点 | 対象:行 | 修正概要 |
|---|---|---|---|
| B-H1 | B | §4 T1 | T1説明をGroupedDepth経由に書き直し |
| C-H1 | C | §3.2/§7 | §7に大スプレッド時gap ラベル追記、§3.2に参照一文追記 |
| D-H1 | D | §4 T3 | T3テスト表に実行コマンド列を追加 |
| D-H2 | D | §4 T3 | negative test 3件（empty_book/one_side_only/price_format）追加 |
| A-M1 | A | §1.2:24 | 関数シグネチャを3引数形式に修正 |
| A-M2/B-M2 | A/B | §2:53 | 行番号リンクを#L200/#L237に修正、本文記述も整合 |
| B-M1 | B | §4 T1 | 現在の動作説明をPriceGrid::index_to_price ループと明記 |
| C-M1 | C | §4 T1 | 空板エッジケース追記 |
| C-M2 | C | §4 T1 | step 移行不変条件追記 |
| C-M3/D-M1 | C/D | §5 | リグレッションガードに既存テストgrep手順追記 |
| D-M2 | D | §6:2 | 手順2を自動テスト確認に変更 |
| B-L1 | B | §8 | pane.rs 説明文を PaneSetup::new() に修正 |
| D-L1 | D | §6:1 | CI追加作業不要の旨を一文追記 |
| D-L2 | D | §4 T3 | スプレッドラベルテストの意図注記追加 |

## ラウンド 2（2026-05-01）

### 統一決定
- C-M4: test_ladder_sparse_price_format の説明に「`5379.0` は許容出力・PASS 期待」を注記追加

### Findings 表

| Finding ID | 観点 | 対象:行 | 修正概要 |
|---|---|---|---|
| C-M4 | C | §4 T3 | test_ladder_sparse_price_format の assert 方向を明示 |

### 残存 LOW（対応不要）
- C-L1: §3.2 と §7 の gap ラベル表現揺れ（「極端に大きい」vs「10 円以上」）— 将来対応記述のため実装影響なし
- C-L2: 空状態表示の指示対象が不明 — test_ladder_sparse_empty_book でカバー済みのため実装リスク低
- C-L3: 他 venue への波及言及なし — §6 に他 venue 目視確認を追記することを推奨するが必須でない
