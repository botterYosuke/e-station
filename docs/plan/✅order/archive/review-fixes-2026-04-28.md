# Review Fixes Log — 2026-04-28

対象: `docs/plan/✅order/task-buying-power-ipc.md`

---

## ラウンド 1（2026-04-28）

### 統一決定

- テストファイル名: `schema_v2_x_roundtrip.rs` → `schema_v2_1_roundtrip.rs` に確定
- SCHEMA_MINOR 現状記述: `現 SCHEMA_MINOR=1` → `SCHEMA_MINOR=2（bump済み）`
- 行番号参照: `main.rs:1618` などをシンボル名参照に置換
- InsufficientFundsError: 個別ハンドリングなし。generic Exception → Error(code:"INTERNAL_ERROR")
- wire vs Rust Message: wire 上の BuyingPowerUpdated は request_id/venue を含む。Rust Message では除外
- dispatch テスト: `test_server_buying_power_dispatch.py` が J-7/J-9/J-11 をカバー（計画書に未記録 → 追記）
- commands.json/events.json 更新確認を Acceptance criteria に追加

### Finding 一覧

| Finding ID | 観点 | 重要度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| A-1 | A 文書間整合性 | HIGH | task-buying-power-ipc.md:90,115 | Step5テストファイル名 `schema_v2_x` → `schema_v2_1` に確定 |
| A-2 | A 文書間整合性 | MEDIUM | task-buying-power-ipc.md:37 | §4.5.1 参照にminor差は警告のみで継続の補足を追加 |
| A-3 | A 文書間整合性 | MEDIUM | task-buying-power-ipc.md:58-65 | BuyingPowerUpdated フィールドと BuyingPowerPanel の写像を Step4 に注記 |
| B-1 | B 既存実装ズレ | MEDIUM | task-buying-power-ipc.md:26 | 現状テーブル SCHEMA_MINOR=1 → 2（bump済み）に更新 |
| B-2 | B 既存実装ズレ | MEDIUM | task-buying-power-ipc.md:85,165 | main.rs:1618 行番号参照 → シンボル名参照に置換 |
| B-3 | B 既存実装ズレ | LOW | task-buying-power-ipc.md:86 | OrderListAction シンボル名参照を行番号なしに更新 |
| B-4 | B 既存実装ズレ | LOW | task-buying-power-ipc.md:90 | Step5 候補ファイル名を実ファイル名に確定 |
| B-5 | B 既存実装ズレ | LOW | task-buying-power-ipc.md:85,149 | distribute_buying_power 経由設計を Step4 に反映 |
| C-1 | C 仕様漏れ | HIGH | task-buying-power-ipc.md:80 | InsufficientFundsError 方針の決定を記録（INTERNAL_ERROR経路） |
| C-2 | C 仕様漏れ | MEDIUM | task-buying-power-ipc.md:146-148 | wire vs Rust Message の区別を設計知見に明記 |
| C-3 | C 仕様漏れ | MEDIUM | task-buying-power-ipc.md:95-103 | commands.json/events.json 更新確認を Acceptance criteria に追加 |
| C-4 | C 仕様漏れ | LOW | task-buying-power-ipc.md:全体 | config キー設定不要の旨を記録 |
| D-1 | D テスト不足 | HIGH* | task-buying-power-ipc.md:88-91 | dispatch テスト（test_server_buying_power_dispatch.py）を Step5 に追記 ※テスト自体は実在 |
| D-2 | D テスト不足 | HIGH* | task-buying-power-ipc.md:80,95 | エラー経路テスト（J-7/J-11）を Step5 に追記 ※テスト自体は実在 |
| D-3 | D テスト不足 | MEDIUM | task-buying-power-ipc.md:130-142 | auto-fetch Rust unit test が未起票と注記 |
| D-4 | D テスト不足 | MEDIUM | task-buying-power-ipc.md:95-103 | CI 自動収集確認を Acceptance criteria に追加 |
| D-5 | D テスト不足 | LOW | task-buying-power-ipc.md:全体 | invariant-tests.md への GetBuyingPower エントリ未起票（今後の改善候補） |

※ D-1/D-2 はレビュー時点では HIGH として報告されたが、調査の結果テストファイル `test_server_buying_power_dispatch.py` が実在することを確認。計画書への未記録のみが問題（MEDIUM相当の文書ギャップ）。

---

## ラウンド 2（2026-04-28）— 収束確認

観点 A+B: 収束（HIGH/MEDIUM 新規ゼロ）  
観点 C+D: 収束（HIGH/MEDIUM 新規ゼロ）  

**全 Finding 解消。HIGH/MEDIUM ゼロ達成。**

---

## fix-account-type-map.md ラウンド 1（2026-04-28）

### 統一決定

- 修正対象拡張: §3.3 に `src/api/order_api.rs`（旧タグ名 5 箇所）と `docs/plan/✅order/spec.md`（L169）を追加
- Phase O4 defer: §6 に「今回はマップ定義のみ追加、UI 選択肢に nisa_growth 未収録のため dead code」を明記
- 行番号参照廃止: §3.1 の L98・L287-293 → シンボル名参照に置換
- B-M4 文書化: open-questions.md 独立エントリは architecture.md §10.4 更新で代替
- invariant-tests.md 登録: §5 Step 3.5・§7 受け入れ条件に追加

### Finding 一覧

| Finding ID | 観点 | 重要度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| A-H1 | A 文書間整合性 | HIGH | fix-account-type-map.md §1.2 | §1.2 冒頭に「以下は修正前の現行状態を示す差分表」注記を追加 |
| A-H2 | A 文書間整合性 | HIGH | fix-account-type-map.md §3.3 / spec.md L169 | spec.md を grep スコープ・修正対象に追加 |
| B-H1 | B 既存実装ズレ | HIGH | fix-account-type-map.md §3.3, §5, §7 / src/api/order_api.rs | Rust 旧タグ名 5 箇所を §3.3 修正対象・§5 Step 2・§7 受け入れ条件に追加 |
| A-M1 | A 文書間整合性 | MEDIUM | fix-account-type-map.md §3.2 | B-M4 open-questions.md 代替方針を §3.2 に注記 |
| B-M1 | B 既存実装ズレ | MEDIUM | fix-account-type-map.md §3.1 | コメント変更箇所に Before/After 明示 |
| B-M2 | B 既存実装ズレ | MEDIUM | fix-account-type-map.md §3.1 | L98・L287-293 行番号参照をシンボル名参照に置換 |
| C-M1 | C 仕様漏れ | MEDIUM | fix-account-type-map.md §6 | nisa_growth "6" は dead code 扱い・Phase O4 で UI 有効化の旨を §6 に明記 |
| D-M1 | D テスト不足 | MEDIUM | fix-account-type-map.md §4.1 | 既存テスト test_account_type_uses_session_zyoutoeki_when_no_tag を §4.1 に明記 |
| D-M2 | D テスト不足 | MEDIUM | fix-account-type-map.md §5, §7 | §5 Step 3.5・§7 受け入れ条件に invariant-tests.md B-M4 登録を追加 |
| A-L1 | A 文書間整合性 | LOW | fix-account-type-map.md L130 | Phase O4 implementation-plan.md に正式定義なし（許容範囲） |
| B-L1 | B 既存実装ズレ | LOW | tachibana_orders.py L457 | session.zyoutoeki_kazei_c パススルー確認済み（問題なし） |
| C-L1 | C 仕様漏れ | LOW | fix-account-type-map.md L23 | "0" 無効根拠の説明補強（許容範囲） |
| C-L2 | C 仕様漏れ | LOW | fix-account-type-map.md L54 | タグ不正時 silently fallback 設計意図の明示（LOW 対応不要） |
| D-L1 | D テスト不足 | LOW | fix-account-type-map.md §5 | TDD RED 確認ステップ省略（LOW 対応不要） |

---

## 銘柄選択（Instrument Selection）実装 レビュー（2026-04-28）

対象: `src/screen/dashboard/panel/order_entry.rs` / `src/screen/dashboard/pane.rs` / `src/screen/dashboard.rs` / `src/main.rs`

機能概要: タイトルバーの「銘柄未選択」ボタンをクリックすると既存 `MiniTickersList` モーダルが開き、`TachibanaStock` ティッカーを選択すると `instrument_id`（`<code>.TSE`）と `display_label` が `OrderEntryPanel` にセットされる。

### 統一決定

- `MiniTickersListInteraction` `RowSelection::Switch` の OrderEntry 分岐: `TachibanaStock` 以外は `Toast::warn` を表示して `return None`（`SwitchTickersInGroup` には進まない）
- `venue` フィールド: `build_submit_action` でハードコードせず `Option<String>` フィールドとして `set_instrument()` が自動設定する
- エンジン切断時の `submitting` 凍結: `on_engine_disconnected()` / `on_engine_reconnected()` を全レイアウトに伝播する（`layout_manager.iter_dashboards_mut()` 使用）
- `self.modal` 冗長再代入: `Add`/`Remove` arm のみ `Some(...)` をセット、`Switch` arm は `None` のみセット（`Some` 再代入を削除）

### ラウンド 1 Finding 一覧

| Finding ID | 観点 | 重要度 | 対象シンボル | 修正概要 |
|---|---|---|---|---|
| IS-H1 | B 既存実装ズレ | HIGH | `pane.rs` `RowSelection::Switch` arm | `Exchange::TachibanaStock` ガード未実装 → 任意取引所のティッカーに `.TSE` が付与される。ガード追加 + Toast 警告で修正 |
| IS-H2 | B 既存実装ズレ | HIGH | `pane.rs` `MiniTickersListInteraction` | `self.modal = Some(...)` 直後に `Switch` arm で `None` 上書き → redo バグ。`Add`/`Remove` のみ `Some(...)` をセットする構造に変更 |
| IS-M1 | C 仕様漏れ | MEDIUM | `order_entry.rs` `build_submit_action` | `venue: "tachibana".to_string()` ハードコード → `venue: Option<String>` フィールド化。`set_instrument()` が自動設定 |
| IS-M2 | C 仕様漏れ | MEDIUM | `order_entry.rs` `OrderEntryPanel` | エンジン切断後 `submitting = true` が凍結したまま復帰不能 → `on_engine_disconnected()` を追加し `EngineRestarting(true)` でリセット |
| IS-L1 | D テスト不足 | LOW | `pane.rs` `MiniTickersListInteraction` | `mini_panel.clone()` が使われず冗長（functional bug ではない） |

### ラウンド 2 修正後再レビュー

IS-H1/IS-H2 修正確認 ✅  
IS-M1/IS-M2 修正確認 ✅  

ラウンド 2 新規 Finding:

| Finding ID | 観点 | 重要度 | 対象シンボル | 修正概要 |
|---|---|---|---|---|
| IS-R2-H1 | B 既存実装ズレ | HIGH | `dashboard.rs` `notify_engine_disconnected` / `main.rs` | アクティブダッシュボードのみ通知 → 全レイアウトに伝播漏れ。`layout_manager.iter_dashboards_mut()` に変更 |
| IS-R2-M1 | C 仕様漏れ | MEDIUM | `order_entry.rs` `on_engine_reconnected` | 再接続後も `last_error` が「接続が切断されました」のまま残存 → `on_engine_reconnected()` を追加し `last_error = None` にクリア |

### ラウンド 3 収束確認

IS-R2-H1/IS-R2-M1 修正確認 ✅  
観点 A+B+C+D: 収束（HIGH/MEDIUM 新規ゼロ）

**全 Finding 解消。HIGH/MEDIUM ゼロ達成。**

### 追加テスト（TDD 準拠）

| テスト名 | カバー対象 |
|---|---|
| `without_set_instrument_submit_button_is_disabled` | 銘柄未選択時に送信ボタン disabled |
| `set_instrument_sets_venue_to_tachibana` | `set_instrument()` が `venue` を自動設定 |
| `submit_order_uses_venue_from_field` | `build_submit_action` がフィールド `venue` を使用 |
| `confirm_submit_with_instrument_id_but_no_venue_returns_none` | `venue = None` のとき送信が `None` を返す |
| `on_engine_disconnected_resets_submitting_state` | 切断時に `submitting` / `pending_request_id` をリセット |
| `on_engine_reconnected_clears_last_error` | 再接続時に `last_error` をクリア |

### LOW 残存

- IS-L1（`mini_panel.clone()` 冗長）: functional bug ではなく次回リファクタ候補（LOW 対応不要）
