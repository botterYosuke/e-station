# Review Fixes Log — menu-and-footer

## ラウンド 1（2026-04-30）

### 統一決定
- view_with_modal 分岐: status_bar は base への push 後、active_menu 分岐の直前に追加。モーダル表示中もフッターは base の一部として残る
- ライフタイム: fn status_bar のシグネチャは Element<'_, Message> に統一（'static 不使用）
- 自動テスト: テスト方針に「popout 非表示ソース解析テスト」「status_bar() 単体テスト」を追記

### Findings 一覧

| ID | 観点 | 重大度 | 対象ファイル | 修正概要 |
|---|---|---|---|---|
| H1 | A/B/C | HIGH | spec.md §アーキテクチャ | view_with_modal 分岐での status_bar 消失リスクを擬似コードに明示 |
| H2 | A/B | HIGH | spec.md §実装イメージ | ライフタイム 'static → '_ に修正 |
| H3 | D | HIGH | spec.md §テスト方針 | popout 非表示のソース解析テストを追記 |
| H4 | D | HIGH | spec.md §テスト方針 | status_bar() 単体テスト観点を追記 |
| M1 | B | MEDIUM | spec.md §実装イメージ | background 書き方を .into() に変更・snap: true 追加 |
| M2 | B | MEDIUM | spec.md §テスト方針 | toast 重ね合わせ時の確認ケース追加 |
| M3 | D | MEDIUM | spec.md §実装ステップ | fn status_bar の配置位置と cargo test ゲートを追記 |
| M4 | C | MEDIUM | spec.md §アーキテクチャ | column![] フッター固定のため Length::Fill 指定を明記 |
| M5 | C | MEDIUM | spec.md §未決事項 | テーマ固定色の割り切りを明示 |
| L1 | A | LOW | spec.md §実装イメージ | \|m\| *m == → \|&m\| m == の慣用パターン注記 |
| L2 | B | LOW | spec.md §実装イメージ | padding::left(8) API 整合確認注記 |
| L3 | D | LOW | spec.md §実装ステップ | cargo fmt/clippy/test ゲート追記 |
| L4 | D | LOW | spec.md §テスト方針 | 目視テストの起動コマンド明示 |

## ラウンド 2（2026-04-30）

### 統一決定
- B4: row の Length::Fill 追加は sidebar_pos の match 後のメソッドチェーンで .spacing(4).padding(8).height(Length::Fill) と連結する
- C2: モーダル表示中フッターは opaque overlay に隠れる。意図的トレードオフとして spec を修正（実装変更はスコープ外）

### Findings 一覧

| ID | 観点 | 重大度 | 修正概要 |
|---|---|---|---|
| B4 | B | MEDIUM | row への Length::Fill 追加における match 分岐とメソッドチェーンの実装注意を追記 |
| C2 | C | MEDIUM | view_with_modal の opaque overlay でフッターが隠れることを意図的動作として spec に明記 |
| A2 | A | LOW | strategy_err_banner? の表記と実コードの差異（未修正・許容） |
| D7 | D | LOW | ユニットテスト配置ファイルの明記（未修正・許容） |
