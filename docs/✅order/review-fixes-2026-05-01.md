# review-fixes 2026-05-01 — inline-loading-indicator-plan.md

`/review-fix-loop`（PlanLoop）の実行ログ。対象は単一計画書
`docs/✅order/inline-loading-indicator-plan.md`。

## ラウンド 1（2026-05-01）

### 統一決定

1. OrderList も in-flight tracking を追加（`order_list_request_id: Option<String>`）。§7「OrderList ガード不要」は撤回。
2. loading 解除は完了 setter 側に一元化（`set_orders` / `set_*_buying_power` / `set_replay_portfolio` / `set_error` 末尾で `loading=false`）。`distribute_*_loading(false)` の二重呼び出し禁止。
3. エンジン切断・再接続でも loading 解除（`Message::EngineConnected` で broadcast）。
4. engine 未接続 early-return では `loading=true` を立てない。
5. バッジ表示文言は **「更新中…」** に統一（廃止対象 grep 語「取得中」と衝突回避）。
6. 送信結果 Message を `*SendCompleted(Result<(), String>)` 1 種に統合。`Message::Noop` 新設は不要。
7. CI grep リグレッションガードテストを追加（source に「取得中」を含む `Toast::info` が無いことを assert）。
8. 新規ペイン open 時の loading は引き継がない（loading=false で生成、対象外として明記）。
9. replay BuyingPower / OrderList ペインは loading 対象外（手動 Refresh 経路がない）。
10. 手動 E2E は「画面目視 + 構造化ログ観測」、`cargo test --workspace` 実行コマンドと対象ユニットテストファイル名を明記。

### Findings 一覧

| ID | 観点 | 重大度 | 対象節 | 修正概要 |
|---|---|---|---|---|
| B1 | A+B | HIGH | §3.4 / §7 | OrderList の IpcError 経路欠如 → `order_list_request_id` 新設・guard ロジック明文化 |
| B2 | A+B | HIGH | §3.4.2 | engine 未接続 early-return での loading=true 残存 → 送信前ガード順序を明文化 |
| C1 | C+D | HIGH | §3.4.5 / §7 | エンジン切断時の永久 loading → `EngineConnected` ハンドラで全ペイン解除 |
| C2 | C+D | HIGH | §3.1 / §6.3 / §7 | `distribute_buying_power_error` 全ペイン broadcast による他ペイン loading 巻き添え解除 → 許容仕様として明記 |
| D1 | C+D | HIGH | §5.3 | grep リグレッションガードテスト欠落 → `tests/regression_loading_toast_strings.rs` を §5 に追記 |
| A1 | A+B | MEDIUM | §2 / §3.2 / §5.2 | 「取得中…」「更新中…」用語揺れ → バッジは「更新中…」統一 |
| B3 | A+B | MEDIUM | §3.4.3 | `Message::Noop` 不要 → `*SendCompleted(Result<(), String>)` パターンに置換 |
| B4 | A+B | MEDIUM | §3.4.3 / §3.4.4 | `set_error` と IpcError ハンドラの loading=false 重複 → setter 側に一元化 |
| B5 | A+B | MEDIUM | §3.4.1 / §7 | 重複 fetch IPC 二重送信 → `order_list_request_id` Some ガードで抑止 |
| C3 | C+D | MEDIUM | §6.2 | 新規 pane open 時の初期 loading → 引き継がない仕様として対象外明記 |
| C4 | C+D | MEDIUM | §6.1 | replay モード切替で loading 引きずり → 別プロセス起動なので発生しない旨明記 |
| D2 | C+D | MEDIUM | §5.1 | ライフサイクル網羅テスト不足（3 経路）→ 統合テスト列挙 |
| D3 | C+D | MEDIUM | §5.2 | 手動 E2E 観測手段未指定 → ログ観測点 + `cargo test --workspace` 明記 |
| A2 | A+B | LOW | §1 | 行番号参照にシンボル名併記 |
| A3 | A+B | LOW | §3.5 / §5.3 | grep を正規表現 `Toast::info\(.*取得中` で行う旨明記 |
| C5 | C+D | LOW | §3.2 | `⟳`(U+27F3) フォントカバレッジフォールバック注記 |
| D4 | C+D | LOW | §5.1 | `set_replay_portfolio` で `loading` が触れられない negative test を追加 |

### 機械検証（Step 4）

- `Grep "取得中" docs/✅order/inline-loading-indicator-plan.md`: 出現箇所はすべて「廃止対象として参照する文脈」または「grep ターゲット」または「説明文」のみ。新バッジ語「更新中…」とは語が異なるため衝突なし。
- ファイル行数: 227 → 403 行（加筆中心、節構造維持）。

## ラウンド 2（2026-05-01）

### 集計
HIGH 0 / MEDIUM 3 / LOW 4

### 統一決定（追加）
- [R02] `OrdersPanel::set_error(message)` を本フェーズで **必須新設**（暫定実装は採用しない）。dashboard 側に対称ヘルパ `distribute_order_list_error` を追加。
- [R03] 切断検知点は `dashboard.notify_engine_disconnected(main_window)`（src/main.rs:1395）と `Message::EngineConnected` の **2 箇所両方**で `distribute_*_loading(false)` + `*_request_id = None`。
- [R05] grep 正規表現は `Toast::info\([^)]*取得中` で §3.5 / §5.3 統一。

### Findings 一覧

| ID | 観点 | 重大度 | 対象節 | 修正概要 |
|---|---|---|---|---|
| R01 | 構文 | MEDIUM | §3.4.3 | 各 `*SendCompleted(Err)` ブロック末尾に `Task::none()` を追加し型を揃えた |
| R02 | 責務分裂 | MEDIUM | §3.1 / §3.3 / §3.4.3 / §3.4.4 | `OrdersPanel::set_error` + `distribute_order_list_error` を必須として追加。「暫定」記述を削除 |
| R03 | 経路特定 | MEDIUM | §3.4.5 | 切断検知点 `notify_engine_disconnected` を実コード grep で確定し明記 |
| R04 | grep 限界 | LOW | §5.3 | format! 経由動的生成時の検知漏れを注記 |
| R05 | regex 統一 | LOW | §3.5 | §5.3 と同一の `Toast::info\([^)]*取得中` に統一 |
| R06 | 観測点 | LOW | §5.2 | tofu 表示確認をチェックリスト 2 に追記 |
| R07 | replay 観測 | LOW | §5.2 | replay ペインで loading バッジが出ないことの目視確認を追加 |

### 機械検証
- §3.4.3 の Err ブランチが全て `Task::none()` を末尾に持つこと → 目視確認済み
- 「暫定」キーワード残存 grep → 削除済み

## ラウンド 3（2026-05-01）

### 集計
HIGH 0 / MEDIUM 2 / LOW 1

### Findings 一覧

| ID | 観点 | 重大度 | 対象節 | 修正概要 |
|---|---|---|---|---|
| R3-1 | 整合性 | MEDIUM | §4 | R02 の `OrdersPanel::set_error` / `last_error` / `distribute_order_list_error` を変更ファイル一覧に追記 |
| R3-2 | 整合性 | MEDIUM | §4 | R03 の `notify_engine_disconnected` 経路追加を main.rs 行に追記 |
| R3-3 | テスト網羅 | LOW | §5.1 | `OrdersPanel::set_error` 用ユニットテストを追加 + 統合テスト経路 4（切断検知）を追加 |

## ラウンド 4（2026-05-01・収束）

### 集計
HIGH 0 / MEDIUM 0 / LOW 2 → **収束**

### 残 LOW（対応不要、記録のみ）
- L1: §3.4.5 切断時パスは `distribute_*_loading(false)` のみで `set_error` 経路を通らない（error メッセージは残さない設計）。意図通りだが明示があると親切。
- L2: §5.1 統合テスト経路 4 の対象が「main.rs state + dashboard state 両方」である旨の補足が望ましい。

## ラウンド 5（2026-05-01・ユーザーレビュー反映）

### 集計
HIGH 1 / MEDIUM 2 / LOW 0 → 修正済み

### 統一決定（追加）
- **stale-error 対策**: `set_loading(true)` で error / last_error をクリア。成功 setter（`set_orders` / `set_cash_buying_power` / `set_credit_buying_power` / `set_replay_portfolio`）でも error クリア。これにより失敗→再 Refresh で「⟳ 更新中…」バッジが正しく出る。
- **replay-refresh-scope**: replay の `OrdersPanel` には Refresh ボタンがある（[src/screen/dashboard/panel/orders.rs:81](../../src/screen/dashboard/panel/orders.rs#L81)）ため loading 対象に含める。replay の `BuyingPowerPanel` は live ブランチでしか refresh_btn を生成しない（[src/screen/dashboard/panel/buying_power.rs:169](../../src/screen/dashboard/panel/buying_power.rs#L169) 周辺）ため対象外のままでよい。
- **test-wording**: §5.1 統合テスト 1/2 を「`distribute_*_loading(false)` が呼ばれる」から「setter 経由で `panel.loading == false` になる」に書き換えて設計方針（setter 一元化）と一致させる。

### Findings 一覧

| ID | 観点 | 重大度 | 対象節 | 修正概要 |
|---|---|---|---|---|
| stale-error | UX | HIGH | §3.1 / §4 / §5.1 / §5.2 | 失敗後の再試行で error 残存 → `set_loading(true)` で error クリア + 成功 setter での明示クリア + ユニット 3 件 + 統合テスト経路 5 + 画面目視 8 を追加 |
| replay-refresh-scope | 設計矛盾 | MEDIUM | §6.1 / §5.2 | replay OrderList も Refresh あり → loading 対象に含める。表で live/replay × 両ペインを明示。画面目視 7 を BuyingPower / OrderList で分離 |
| test-wording | テスト整合 | MEDIUM | §5.1 統合 1/2 | error 経路は「setter 経由 loading=false」を assert する書き方に統一 |

## ラウンド 6（2026-05-01・収束）

### 集計
HIGH 0 / MEDIUM 0 / LOW 2 → **収束**

### 残 LOW（対応不要）
- L6-1: §3.4.5 の `notify_engine_disconnected` 経路にコード例なし（本文記述のみ）。R03 延長で対応不要。
- L6-2: §6.1 表で OrderList replay を含めたことが「統一決定 9」原文と差分するが、ラウンド 5 ログで上書き済みのため対応不要。

```
全ラウンド数: 6（うちラウンド 5 はユーザーレビュー反映）
修正した Finding 総数: HIGH 6 / MEDIUM 13 / LOW 6
残存 LOW（対応不要）: 4件

主要な反映成果:
- 用語: バッジ文言「更新中…」、廃止対象「取得中」を分離
- in-flight tracking: BuyingPower / OrderList の両方に request_id
- 解除経路網羅: 完了 setter / SendCompleted Err / IpcError / EngineConnected / notify_engine_disconnected の 5 経路
- 設計規約: loading 解除は完了 setter に一元化（重複 distribute 禁止）
- stale-error 対策: set_loading(true) と成功 setter で error クリア
- replay-refresh-scope: replay OrderList も loading 対象（Refresh ボタンあり）。replay BuyingPower のみ対象外
- リグレッションガード: tests/regression_loading_toast_strings.rs で「取得中」grep 検知
- テスト観点: ライフサイクル統合 5 経路 + stale-error クリア + Negative test (set_replay_portfolio で loading 不変)

ログ: docs/✅order/review-fixes-2026-05-01.md
```

---

# review-fixes 2026-05-01 — positions-in-orders-panel-plan.md

`/review-fix-loop`（PlanLoop）の実行ログ。対象は計画書
`docs/✅order/positions-in-orders-panel-plan.md`（保有銘柄専用ペイン新設）。

## ラウンド 1（2026-05-01）

### 集計
HIGH 8 / MEDIUM 9 / LOW 4

### 統一決定

1. **B-1 踏襲先誤記**: §3.4 の「BuyingPowerPanel のパターンを踏襲」を「OrdersPanel のパターンを踏襲」に修正。BuyingPowerPanel には `loading`/`last_error` フィールドがない。
2. **B-2 market_value 型**: `PositionRecord.market_value` は `int`（デフォルト 0）。`Optional[int]` ではない。0 は `"0"` として送出し、UI で `"¥0"` と表示する。空文字 `""` または `i64::from_str` 失敗時は `"-"`（防御的フォールバック）。
3. **A-1/C-4 notify_engine_disconnected**: §3.7.2 に `notify_engine_disconnected` 経路を明記。`positions_request_id = None` + `distribute_positions_loading(false)` を実施する（inline-loading-indicator-plan.md [R03] と同方針）。
4. **C-2 replay guard**: `OrderFilled` → `GetPositions` は live モードのみ。`mode == replay` ガードでスキップする旨を §3.7.1 に明記。
5. **A-3 二重呼び出し禁止**: `PositionsSendCompleted(Err)` 時に `distribute_positions_loading(false)` を呼ばない。`set_error` 内で `loading = false` が一元化されるため（統一決定 2 準拠）。
6. **B-3 ALL size=13**: `ContentKind::ALL: [ContentKind; 13]` と定数サイズを明記（現 12 種 + Positions = 13）。
7. **D-1/D-2/D-3 テスト粒度**: §5 テスト計画に PositionsPanel ユニットテスト 4 件（loading state / set_positions / set_error / loading 解除後再 Refresh）を列挙し、integration test 経路（VenueReady → PositionsUpdated 受信 / OrderFilled → 自動 Refresh / notify_engine_disconnected → loading リセット）を追記。
8. **B-5 _tachibana_p_no_counter**: `GetPositions` ハンドラで `p_no_counter` は `_tachibana_p_no_counter` を使う（server.py の既存フィールド。GetBuyingPower ハンドラと同一参照）。
9. **C-3 serde 挙動**: 旧 Rust バイナリが新 JSON（`Positions` バリアント含む）を読んだ際の挙動は `deny_unknown_fields` の有無に依存。実装前に確認すること。
10. **A-2/D-5 invariant-tests.md**: `ContentKind::ALL` のサイズ不変条件テストを `engine-client/tests/invariant-tests.md` ではなく `cargo test --workspace` 対象の Rust ユニットテストとして追加する（md ファイルへの記述は不要）。

### Findings 一覧

| ID | 観点 | 重大度 | 対象節 | 修正概要 |
|---|---|---|---|---|
| A-1 | アーキテクチャ | HIGH | §3.7.2 | notify_engine_disconnected 経路未記載 → positions_request_id リセット + distribute_positions_loading(false) を追記 |
| A-2 | アーキテクチャ | HIGH | §3.3.2 | ContentKind::ALL サイズ N のまま → 13 固定に修正 |
| A-3 | アーキテクチャ | HIGH | §3.7.3 | PositionsSendCompleted(Err) で loading 二重解除 → set_error 一元化明記 |
| B-1 | 実装詳細 | HIGH | §3.4 | BuyingPowerPanel パターン踏襲の誤記 → OrdersPanel に訂正 |
| B-2 | 実装詳細 | HIGH | §3.1.1 / §3.2 | market_value Optional[int] の誤記 → int (default=0) に訂正、0→"¥0" 表示ルール追記 |
| B-3 | 実装詳細 | HIGH | §3.3.2 | ALL[N] → ALL[13] |
| C-2 | 経路特定 | HIGH | §3.7.1 | OrderFilled → GetPositions に replay guard 未記載 → live モードのみ明記 |
| C-4 | 経路特定 | HIGH | §3.7.2 | notify_engine_disconnected で positions_request_id リセット漏れ → 追記 |
| A-3 | アーキテクチャ | MEDIUM | §3.7.3 | 二重呼び出し禁止ルールを明文化 |
| B-4 | 実装詳細 | MEDIUM | §3.4.2 | loading 解除の一元化規約を PositionsPanel に明記 |
| B-5 | 実装詳細 | MEDIUM | §3.2 | p_no_counter フィールド名 (_tachibana_p_no_counter) を明記 |
| C-1 | 経路特定 | MEDIUM | §3.7.2 | EngineConnected 経路での positions_request_id リセットを追記 |
| C-3 | 経路特定 | MEDIUM | §4 | serde deny_unknown_fields 確認事項を追記 |
| D-1 | テスト | MEDIUM | §5.1 | PositionsPanel ユニットテスト 4 件を追記 |
| D-2 | テスト | MEDIUM | §5.2 | integration test 経路 3 件を追記 |
| D-3 | テスト | MEDIUM | §5.4 | Python 側 dispatch テスト (GetPositions) を追記 |
| A-2 | アーキテクチャ | LOW | §3.3.2 | ALL サイズ不変条件 Rust ユニットテストを §5 に追記 |
| D-5 | テスト | LOW | §5.8 | ContentKind::ALL length == 13 アサーションテストを新設 |

### 機械検証（Step 4）

- `OrdersPanel のパターンを踏襲`: 2 箇所確認（§3.4.1, §3.4.2）
- `live モードのみ`: 1 箇所確認（§3.7.1）
- `notify_engine_disconnected`: 4 箇所確認（§3.7.2）
- `market_value.*int.*デフォルト 0`: 3 箇所確認（§3.1.1, §3.2）
- `ALL.*13`: 1 箇所確認（§3.3.2）

## ラウンド 2（2026-05-01）

### 集計
HIGH 8 / MEDIUM 11 / LOW 7

### 統一決定（Round 2）

1. **R2-1 Python dispatch アーキテクチャ**: §3.2 の擬似コードを `_spawn_fetch(self._do_get_positions(msg), ...)` + `_do_get_positions(msg)` メソッドパターンに書き換える。`_send_error` は使わず `self._outbox.append({...})` パターンを使う（実際の `GetBuyingPower` = `server.py:619–622` と対称に）。
2. **R2-2 has_positions_pane() ヘルパー**: §3.7.1 VenueReady 行に `has_positions_pane(main_window) && tachibana_state.is_ready() && positions_request_id.is_none()` ガードを明記。`has_positions_pane()` を §3.5 distribute ヘルパー一覧と §4 変更ファイル一覧（`dashboard.rs`）に追加。
3. **R2-3 OrderFilled 変換経路**: 現行 `EngineEvent::OrderFilled` → `Message::OrderToast` ハンドラ内で live モード・`positions_request_id.is_none()` ガード付きの `GetPositions` 追加発行を採用（新バリアント不要）。§3.7.1 に実装箇所（`src/main.rs OrderToast` ハンドラ内）を明記。
4. **R2-4 spec.md §6 同時追記**: §5.8 に `spec.md §6` への I-Position-1 / I-Position-2 の追記エントリ文言を具体的に記載（`invariant-tests.md` 単独更新では `test_invariant_tests_doc.py` が FAIL するため）。
5. **R2-5 信用建玉 market_value 取得**: §3.1.1 に注記「Python 実装（`tachibana_orders.py:1794–1804`）では `sTategyokuZanKingaku` を現在未取得のため `market_value=0`（dataclass デフォルト）になる。実装タスクで `sTategyokuZanKingaku` の取得と `int(val) if val else 0` 変換を追加すること」を追記。
6. **R2-6 distribute 参照モデル整理**: §3.5 から `distribute_buying_power` への参照を削除し `distribute_order_list` パターンへの参照に統一。§3.5 に「`distribute_positions_loading(false)` を外部から呼ぶのは切断・再接続経路（`notify_engine_disconnected` / `EngineConnected` / `EngineRestarting(true)`）専用。完了 setter 経路では二重呼び出し禁止」を明記。
7. **R2-7 EngineRestarting(true) アーム**: §3.7.2 に「`EngineRestarting(true)` ブロック（`src/main.rs:1393`）にも `positions_request_id = None` + `distribute_positions_loading(main_window, false)` を追加する」を明記（`EngineConnected` / `notify_engine_disconnected` の 2 点だけでなく 3 点必要）。
8. **R2-8 deny_unknown_fields 確認完了**: §3.3.3 を「調査済み: `Pane` / `ContentKind` には `#[serde(deny_unknown_fields)]` が付いていない（`data/src/layout/pane.rs` で確認）。旧バイナリは `Positions` variant を unknown として Starter にフォールバックするため `saved-state.json` のロールバックは機能的には可能」に更新。
9. **R2-9 Ok ハンドラ記述**: §3.7.3 に `PositionsSendCompleted(Ok(()))` → `Task::none()` のコード例を追記（[R01] 再発防止）。
10. **R2-10 iter_dashboards_mut**: §3.7.2 の `notify_engine_disconnected` 経路記述に「`iter_dashboards_mut()` で全ダッシュボードを対象にする（`active_dashboard_mut()` ではない、既存コード `src/main.rs:1404–1410` と対称）」を明記。
11. **R2-11 OpenOrderPanel(OrderList) 欠落**: §3.7.4 に「既知: `OpenOrderPanel(ContentKind::OrderList)` のキャッチアップは現行コードに未実装（スコープ外）。本計画では Positions キャッチアップのみ追加する」と明記。
12. **R2-12 margin_general フォールバック**: §3.4.2 の区分ラベル変換表に `"margin_general" → "信用(一般)"` のフォールバック行を追加。

### Findings 一覧

| ID | 観点 | 重大度 | 対象節 | 修正概要 |
|---|---|---|---|---|
| R2-H1 | Python dispatch | HIGH | §3.2 | `_send_error` / インライン `await` が実装と乖離 → `_spawn_fetch` + `_do_get_positions` + `_outbox.append` パターンに書き換え |
| R2-H2 | アーキテクチャ | HIGH | §3.5 / §3.7.1 / §4 | `has_positions_pane()` ヘルパー未記載 → §3.5 / §3.7.1 / §4 に追記 |
| R2-H3 | 経路特定 | HIGH | §3.7.1 | `OrderFilled` → `GetPositions` 変換経路未特定 → `OrderToast` ハンドラ内追加発行として §3.7.1 に明記 |
| R2-H4 | テスト | HIGH | §5.8 | `invariant-tests.md` 更新時に `spec.md §6` 追記漏れで CI ブロック → §5.8 に spec.md §6 追記内容を明記 |
| R2-H5 | Python 実装 | HIGH | §3.1.1 / §3.2 | 信用建玉 `market_value` (`sTategyokuZanKingaku`) が未取得 → 実装タスク注記を追加 |
| R2-H6 | distribute 設計 | HIGH | §3.5 | `BuyingPower` 参照残存 + `loading=false` 二重呼出禁止規約未記載 → 参照モデルを OrderList に統一 + 禁止規約を注記 |
| R2-H7 | 経路特定 | HIGH | §3.7.3 | `distribute_positions_loading(false)` の切断経路専用規約と `PositionsSendCompleted(Err)` の二重呼出禁止の整合が不明瞭 → §3.5 に明文化 |
| R2-H8 | テスト / 経路 | HIGH | §5.2 / §3.7.1 | `OrderFilled` 統合テストが変換経路確定（R2-H3）に依存 → テスト記述を `OrderToast` ハンドラ経由と明記 |
| R2-M1 | アーキテクチャ | MEDIUM | §3.7.2 | `EngineRestarting(true)` ブロックへの positions_request_id リセット明記漏れ |
| R2-M2 | テスト | MEDIUM | §5 | `ALL.len() == 13` アサートテストの置き場所（`data/src/layout/pane.rs` 内 #[cfg(test)]）が未定義 |
| R2-M3 | 経路特定 | MEDIUM | §3.7.4 | `OpenOrderPanel(ContentKind::OrderList)` のキャッチアップが現行コード未実装 → スコープ外と明記 |
| R2-M4 | 経路特定 | MEDIUM | §3.7.3 | `PositionsSendCompleted(Ok(()))` ハンドラの記述が欠落 |
| R2-M5 | 経路特定 | MEDIUM | §3.7.2 | `notify_engine_disconnected` が `iter_dashboards_mut()` 対象である旨が未記載 |
| R2-M6 | テスト | MEDIUM | §5.6 | replay モード PositionsPanel の目視確認（空表示固定確認）が未記載 |
| R2-M7 | テスト | MEDIUM | §5.4 | request_id ミスマッチ時の挙動（無視 = None に戻さない）が未決定 → デバッグログ drop と明記 |
| R2-M8 | テスト | MEDIUM | §5.1 | ユニットテスト件数が統一決定の「4件」と不一致 → 実際の列挙件数に合わせて記述修正 |
| R2-M9 | 実装詳細 | MEDIUM | §3.3.3 | `deny_unknown_fields` が「確認事項」のまま → 調査済み結果（未付与）に更新 |
| R2-M10 | テスト | MEDIUM | §5.1 / §5.3 | Python テストに信用建玉 market_value 取得経路（R2-H5 修正後）の確認が欠落 |
| R2-M11 | 実装詳細 | MEDIUM | §3.4.2 | `position_type = "margin_general"` のフォールバック表示未定義 |
| R2-L1 | 経路特定 | LOW | §3.7.1 | replay ガードの実装手段（`APP_MODE.get()` vs `tachibana_state.is_ready()`）が未明確 |
| R2-L2 | テスト | LOW | §5.9 | CI 収集対象確認手段（cargo test --workspace で対象バイナリの確認）が未記載 |
| R2-L3 | テスト | LOW | §5.5 | `link_group: Some` バリアントラウンドトリップテスト欠落 |
| R2-L4 | 実装詳細 | LOW | §3.1.4 | schemas JSON 更新が §3.1.4 手順に未記載 |
| R2-L5 | テスト | LOW | §5.8 | 不変条件テスト文言が `is_none()` / `is_some()` 逆転 |
| R2-L6 | アーキテクチャ | LOW | §3.7.3 | stale な PositionsUpdated の request_id 不一致時（None に戻さない）を §3.7.3 に明記 |
| R2-L7 | テスト | LOW | §5.4 | schemas JSON ファイルの実在確認が未実施 |

---

# review-fixes 2026-05-01 — fix-sell-button-disabled-2026-05-01.md

`/review-fix-loop`（PlanLoop）の実行ログ。対象は計画書
`docs/✅order/fix-sell-button-disabled-2026-05-01.md`。

## ラウンド 1（2026-05-01）

### 統一決定

1. `sTatebiType` open-questions 起票をロールアウトの**マージ前必須条件**に格上げ
2. §7「赤確認」表記を訂正 — 新テストは model 経路が既存で通るため view 修正前でも GREEN になる。view `.on_press` 欠落の保護は §4.3 手動確認に依存する
3. `test_tachibana_submit_order.py` SELL 正常系追加を §5 変更範囲に明記・§7 に pytest コマンドを追記
4. spec.md との O1/O3 齟齬は計画書 §2 に説明一文を追加（spec.md 本体は本計画スコープ外）
5. §4.3 冒頭にデモ口座限定の明示を追加
6. §7 に `cargo clippy -- -D warnings` を `cargo test --workspace` 前に追記

### Findings 一覧

| ID | 観点 | 重大度 | 対象節 | 修正概要 |
|---|---|---|---|---|
| F1 | A+C | HIGH | §7 / §8 | sTatebiType リスク: MarginCreditRepay+Sell が `"*"` で送信される実弾パスを §8 に明記・起票をマージ前必須条件化 |
| F2 | B+C+D | HIGH | §4.2 / §7 | テスト「赤確認」不成立: 新テストは view 修正前でも GREEN。説明を「model 経路リグレッション防止」に訂正 |
| F3 | D | HIGH | §5 / §7 | Python SELL テスト未記載: `test_tachibana_submit_order.py` SELL 正常系追加を変更範囲に明記 |
| F4 | A | MEDIUM | §2 | spec.md O1/O3 齟齬: 「O1 設計・O3 で実装（後回し）」の説明を §2 に追記 |
| F5 | A | MEDIUM | §4.3 | ダイアログ文言: 受け入れ基準ステップ 3 に「売りと表示されることを確認」を追加 |
| F6 | B | MEDIUM | §4.1 | style fn 根拠: `sell_btn = cancel style` は警戒色付与の設計根拠を追記・将来の両ボタン統一禁止を明文化 |
| F7 | B | MEDIUM | §4.1 or §6 | SideChanged None 方針: 外部 Action 不要の設計であることを明記 |
| F8 | C | MEDIUM | §4.3 | デモ必須明示: §4.3 冒頭に `TACHIBANA_ALLOW_PROD=1` 不設定の注意書きを追加 |
| F9 | D | MEDIUM | §4.3 | 受け入れ基準に HTTP 補完: pytest による SELL 疎通確認を補助ステップとして追記 |
| F10 | A | LOW | §2 / §4 | 行番号参照陳腐化: 今ラウンドはスコープ外（列挙のみ） |
| F11 | C | LOW | §7 | bug-postmortem 文言: `バックエンド段階解禁 UI 追従漏れ` パターンを具体化して記録 |
| F12 | C | LOW | §4.3 / §8 | WAL 確認: SELL の WAL 記録は既存実装で保証済みと 1 行追記 |
| F13 | D | LOW | §7 | clippy 未記載: `cargo clippy -- -D warnings` を手順に追記 |

## 実装レビュー（2026-05-01 — inline-loading-indicator 実装後）

対象: `sasa/inline-loading-indicator` ブランチの変更 5 ファイル

### MEDIUM 以上: ゼロ

### LOW findings

| ID | 重要度 | 内容 | 対処 |
|----|--------|------|------|
| R-L1 | LOW | `BuyingPowerSendCompleted(Err)` ハンドラは現状 dead code。BuyingPower の Err 経路はすべて `IpcError` 経由。動作上の問題なし | enum docコメントに "Err arm は IpcError 経路のため到達しない" を追記 |
| R-L2 | LOW | `EngineConnected` の loading 解除が `active_dashboard_mut()` のみ。`EngineRestarting` は全 dashboard をループする非対称。計画 §3.4.5 が `active_dashboard_mut()` を指定しているため仕様通り | 変更なし（仕様）|

### 確認済み項目

- 廃止 `Toast::info("取得中")` 5 箇所すべて削除 ✅
- regression test `no_toast_info_with_torichu` PASS ✅
- `distribute_order_list_error` / `distribute_*_loading` 3 ヘルパ正常動作 ✅
- stale-error クリア（`set_loading(true)` → `error/last_error=None`）✅
- 4 経路 loading 解除: SendCompleted Err / IpcError / EngineConnected / EngineRestarting ✅
- `cargo test --workspace` 全緑 / `cargo clippy -- -D warnings` 警告ゼロ ✅
