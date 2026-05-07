---
type: review-fixes
target: docs/✅nautilus_trader/🔵live-strategy-gui.md
---

## ラウンド 1（2026-05-07）

### 統一決定

- D1: `EngineStopped` の live 停止判定は `strategy_id` 文字列一致ではなく、送信時に生成した UUID（セッション固有）で区別する。Phase 7 で `strategy_id: uuid::Uuid::new_v4().to_string()` を使う。
- D2: `clear_live_strategy_portfolio` は `distribute_live_buying_power` への空文字渡しではなく、`BuyingPowerPanel` に専用の `clear_live_strategy_portfolio()` メソッドを追加して直接呼ぶ。
- D3: `live_strategy_file_stem` の保存先は `self.menu_bar.live_bar.strategy_file_stem` のみ。Phase 7 のコード例から `self.live_strategy_file_stem = ...` を削除。
- D4: `LiveTimeUpdated` メッセージを Message 列挙から削除。時刻更新は `LiveStrategyStarted` / `LiveBuyingPowerUpdated` ハンドラで直接実施。

### Finding 一覧

| ID | 重要度 | 対象:行 | 問題 | 修正概要 |
|---|---|---|---|---|
| F01 | HIGH | Phase 6 + Phase 7 | strategy_id="live-strategy" 固定では mode switch / restart 由来 EngineStopped と区別不可 | UUID を Phase 7 で生成し、それを strategy_id として送信。Phase 6 の解決策テキスト・アーキテクチャ概要も更新 |
| F02 | HIGH | Phase 11 (L664〜692) | 「追加実装なし」と書きつつ clear API 追加と distribute 空文字渡しを要求。矛盾 + semantics 不正確 | タイトル修正、distribute 空文字渡し削除、clear_live_strategy_portfolio() 専用呼び出しに統一 |
| F03 | MEDIUM | Phase 7 (L360〜362) | `self.live_strategy_file_stem = ...` は App 直下への保存（Phase 2 の方針と矛盾） | `self.menu_bar.live_bar.strategy_file_stem = ...` に修正 |
| F04 | MEDIUM | Phase 2 Message (L199〜200) | `LiveTimeUpdated` が定義されているが送信元・ハンドラがなく未使用 | Message 列挙から `LiveTimeUpdated` エントリを削除 |

## ラウンド 2（2026-05-07）

### Finding 一覧

| ID | 重要度 | 対象 | 問題 | 修正概要 |
|---|---|---|---|---|
| F05 | HIGH | 受け入れ条件 8 | `EngineStopped{strategy_id: "live-strategy"}` リテラルが UUID 化後も残存。実装者が固定文字列でテストする誤解を招く | `strategy_id: "<session-uuid>"` に変更し「UUID が live_strategy_id と一致する場合のみ」を明記 |
| F06 | MEDIUM | テスト戦略 LG-12〜15 | Phase 6 前提条件「Python engine が strategy_id をエコーバック」に対応するテスト ID なし | LG-16 を追加（server.py/engine_runner.py のソース確認テスト） |

## ラウンド 3（2026-05-07）

### Finding 一覧

| ID | 重要度 | 対象 | 問題 | 修正概要 |
|---|---|---|---|---|
| F07 | HIGH | 実装順序 Phase 9 | Phase 9 全体を「独立先行可能」としているが widget_menu_bar.rs のシグネチャ変更（9b）は main.rs の全呼び出し箇所と同時更新が必要でビルドが壊れる | Phase 9 を 9a（menu_bar_state.rs データ定義のみ・独立可）と 9b+9c（シグネチャ変更＋呼び出し更新・同一コミット必須）に分割し依存グラフ更新 |
| F08 | HIGH | Phase 7 / Phase 8 コード | `Message::Nothing` は存在しない。実際の enum 値は `Message::Noop` | `Message::Nothing` → `Message::Noop` に全修正 |
| F09 | MEDIUM | Phase 7 コード | `.expect("engine_connection must be Some...")` は接続断時にパニック。replay フォームと設計が異なる | `if let Some(conn) = self.engine_connection.as_ref().cloned()` ガードに変更 |
| F10 | MEDIUM | Phase 7 / Phase 8 コード | StartEngine / StopEngine の send 失敗をサイレントに握り潰している（`\|_\| Message::Noop`）。replay フォームはトースト表示する | `map_err(\|e\| e.to_string())` + `Message::OrderToast(Toast::error(...))` パターンに統一 |
