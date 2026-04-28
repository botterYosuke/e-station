# review-fixes — fix-ladder-header-2026-04-28

対象計画書: `docs/plan/✅tachibana/fix-ladder-header-2026-04-28.md`

---

## ラウンド 1（2026-04-28）

### 統一決定

1. クロージャ外事前計算: `header_cache.draw()` クロージャ外で `grid`/`cols` を事前計算してキャプチャ
2. `Ladder::invalidate`（`impl Ladder` ブロック）が修正対象と明記
3. `draw_cell_text` 第4引数は行上端 Y（内部で +ROW_HEIGHT/2 加算）と明記
4. Step 6 の gap なし版差分コードを明示
5. 受け入れ条件を「機械検証」「目視確認」に分類し、ユニットテスト計画を追加

### Finding 一覧

| ID | 優先度 | タイトル | 対象箇所 | 修正概要 |
|---|---|---|---|---|
| B-1 | HIGH | クロージャ内借用競合 | Step 5 擬似コード | クロージャ外事前計算パターンに修正 |
| C-1 | HIGH | テーマ変更時の header_cache 無効化経路未記述 | Step 2 周辺 | テーマ変更 → invalidate() 経路を注記追加 |
| C-2 | HIGH | draw_vsplit gap なし版 HEADER_HEIGHT 未適用 | Step 6 | None アームの差分コードを明示 |
| D-1 | HIGH | 受け入れ条件の機械/目視区別未記載 | 受け入れ条件 | 2グループに分類 |
| D-2 | HIGH | cargo test が Ladder 描画を検証しない | 受け入れ条件 | mid_screen_y ユニットテスト計画を追加 |
| A-1 | MEDIUM | Ladder::invalidate vs Panel::invalidate 不明確 | Step 2 | impl Ladder ブロックと明記 |
| B-2 | MEDIUM | draw_cell_text y 引数の意味未記述 | Step 5 | 行上端 Y 規約を注記 |
| B-3 | MEDIUM | Step 6 変更前スニペットが実コードと不一致 | Step 6 | 実コードのパターンに合わせて修正 |
| B-4 | MEDIUM | visible_rows 可視判定説明が不正確 | Step 3 | 「header_geo がオーバーレイ」と正確に説明 |
| C-3 | MEDIUM | mid_screen_y 負値コーナーケース未考慮 | Step 3 | .max(HEADER_HEIGHT) ガードを追記 |
| C-4 | MEDIUM | ペイン幅極小時の受け入れ条件未定義 | 受け入れ条件 | ≤60px パニックなし条件を追加 |
| D-3 | MEDIUM | None パステスト計画なし | 受け入れ条件 | build_price_grid() None アサート計画追加 |
| A-3 | LOW | ColumnRanges フィールド名の表記揺れ | 列構成表 | フィールド名対応を括弧書きで追加（次ラウンド持ち越し） |
| B-4 | LOW | visible_rows 上端カットオフ説明（再掲） | Step 3 | MEDIUM として解消 |
| C-5 | LOW | ヘッダ背景アルファ強制指定未明記 | Step 5 | iced::Color { a: 1.0, ..bg } 明示（次ラウンド持ち越し） |
| D-4 | LOW | price_to_screen_y 回帰テスト未計画 | 受け入れ条件 | 次ラウンド持ち越し |

---

## ラウンド 2（2026-04-28）

### 統一決定

1. `layout_opt` を削除し `cols_opt` のみ保持（タプルを廃止）
2. gap あり版の条件を `top > HEADER_HEIGHT` のみに簡略化（恒偽 dead condition を削除）

### Finding 一覧

| ID | 優先度 | タイトル | 対象箇所 | 修正概要 |
|---|---|---|---|---|
| B-5 | HIGH | `layout_opt` 未使用 → clippy エラー | Step 5 擬似コード | `cols_opt` のみ保持するよう書き直し |
| B-6/C-6 | MEDIUM | `top_clamped < top` 恒偽条件 | Step 6 gap あり版 | `if top > HEADER_HEIGHT` に簡略化 |
| A-3 | LOW | ColumnRanges フィールド名の表記揺れ | 計画書全体 | R2 確認で揺れなし → 解消済み |
| C-5 | LOW | 背景アルファ強制指定未明記 | Step 5 | LOW 持ち越し |
| D-4 | LOW | price_to_screen_y 回帰テスト未計画 | 受け入れ条件 | LOW 持ち越し |
