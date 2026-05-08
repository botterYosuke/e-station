# Plan Review-Fix Log — 2026-05-08

対象計画書: `docs/plan/fix-issue1-instrument-input-removal.md`

## ラウンド R1（2026-05-08）

### 統一決定

- **タイトルバー連動の本命経路**: `Effect::SwitchTickersInGroup` (`dashboard.rs:1526`) ハンドラに replay pane focus 時の `bar.instrument_id` 同期フックを追加する。`ReplayDataLoaded` での初期化はその前段。
- **新規 replay 開始入口**: `PressPlay` を「validate-and-submit 直行」から「`replay_form_modal` を `Some` にして UI を開く」に変更する。modal 内既存 `text_input` が新規入力経路。SCENARIO prefill 経路は併存。
- **複数銘柄表示ルール**: focused pane の単一 instrument_id を表示する。focus 無し / 起動直後は `ids[0]`（= `LoadReplayData` の `first_id`）。`ids.join(", ")` 案は採用しない。
- **`BarMessage::InstrumentChanged`**: 削除。update arm・テストも同時削除。

### Findings

| ID | 観点 | 重要度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| R1-01 | A 流れ | HIGH | `fix-issue1-instrument-input-removal.md` Step 2 / 旧 Step 2 | 「`PaneMsg` がないので未実装」分析を訂正。`Effect::SwitchTickersInGroup` 経由の同期フックを Step 4 として新規追加 |
| R1-02 | A 流れ | HIGH | 同上 Step 1 後の「Play 入口」 | `text_input` 削除後の新規 replay 入口を `PressPlay` の modal 表示化（Step 2）として明記 |
| R1-03 | B 不変条件 | MEDIUM | 同上 Step 2 末尾「複数銘柄」 | 「`ids.join(", ")`」案を廃止。focused pane 単一表示ルールに統一（Step 3／Step 4） |
| R1-04 | B 不変条件 | MEDIUM | 同上 Step 3 「`InstrumentChanged` の影響確認」 | 「残す」方針を撤回し削除に統一（Step 5）。テスト追従も計画化 |
| R1-05 | D テスト観点 | MEDIUM | 同上 確認項目 | テスト計画 1〜5 を新設（fresh-state Play / SwitchTickers 同期 / 複数銘柄 / バリアント不在 / SCENARIO 維持） |

### 反映内容（修正後セクション一覧）

- 冒頭に R1 反映ノートと「3 経路の対応表」を追加
- 影響範囲節を再構成（dashboard.rs / menu.rs を新規追加）
- Step 1（text_input 削除）を読取専用ラベル化に変更
- Step 2 を新設: `PressPlay` を modal 表示化
- Step 3（旧 Step 2）を維持しつつ複数銘柄ルールを `ids[0]` に固定
- Step 4 を新設: `switch_tickers_in_group` 同期フック
- Step 5 を新設: `BarMessage::InstrumentChanged` 削除
- Step 6 を新設: 既知の制約（focus 切替・link_group 越し）
- 「テスト計画」節を新設: テスト 1〜5・観測コマンド
- 「確認項目」「変更ファイル一覧」を再生成
- 実装難易度を「低 → 中」に修正

## ラウンド R2（サニティチェック）

### Grep ベース機械検証

- `BarMessage::InstrumentChanged` 残留 → 削除完了は実装フェーズで担保（計画書では削除方針を明記）
- 旧表現「`ids.join(", ")`」が計画書に残っていないこと（廃止と明記する箇所のみ残置）
- 旧表現「`PaneMsg` という橋渡し型は存在しない」由来の誤分析が残っていないこと
- 各見出しからのファイル参照（`pane.rs:1878` / `dashboard.rs:1526` / `handlers/menu.rs:74` / `handlers/replay.rs:90` / `replay_form.rs:108`）が実コードと一致していること（R1 段階で確認済み）

### 残存 Finding

- HIGH: 0 件
- MEDIUM: 0 件
- LOW: 0 件（実装フェーズでの観察ポイントとして「focus 切替フォロー」は Step 6 に明示済み）

## 完了サマリ

```
=== 完了 ===
全ラウンド数: 2（R1 = 提供レビュー反映、R2 = サニティ）
修正した Finding 総数: HIGH 2 / MEDIUM 3 / LOW 0
残存 LOW（対応不要）: 0 件
主要な反映成果（規約レベル）:
- 新規 replay 起動: PressPlay は modal を開く方式に統一
- バー表示ルール: focused pane 単一銘柄／初期は ids[0]
- 同期フック: Effect::SwitchTickersInGroup を本命経路に明記
- メッセージ整理: BarMessage::InstrumentChanged 削除
- テスト観点: fresh-state Play / SwitchTickers 同期 / 複数銘柄 / バリアント不在 / SCENARIO 維持
ログ: docs/plan/review-fixes-2026-05-08.md
```

---

# 対象計画書: `docs/plan/fix-issue2-positions-pane.md`

## ラウンド R1（2026-05-08）

### 統一決定（Issue 2）

- **D1 — authoritative emit point**: 前進再生時の `PositionsUpdated` emit は Python 側
  `engine_runner.py` の fill_handler および `_on_bar` で行う（`ReplayBuyingPower` と同タイミング）。
  StepBackward は `server.py` で snapshot 復元後に re-emit。
- **D2 — IPC ペイロード形式**: `PositionsUpdated.positions` は `PositionRecordWire` 互換配列。
  `portfolio_state["positions"]` の `{iid: {qty, cost}}` は engine_runner / server で配列に
  変換してから outbox。`PortfolioView.to_position_records()` を新設。
- **D3 — replay pane への broadcast**: `distribute_positions()` の `!panel.is_replay()` 除外を撤去。
- **D4 — request_id ガード**: `handlers/venue.rs:523` で空 `request_id` は in-flight 比較を
  バイパスして broadcast。
- **D5 — view() の描画**: `PositionsPanel::view()` の `is_replay()` 早期 return を削除。
  REPLAY 区別は header の小さなバッジで実現。
- **D6 — Finished 後の表示維持**: 終了後も最終ポジションを保持。

### Findings

| ID | 観点 | 重要度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| R1-01 | A 流れ | HIGH | `panel/positions.rs:111-115` | view() の REPLAY 早期 return を削除し、live と同一描画フローに統合（Step 3） |
| R1-02 | A 流れ | HIGH | `handlers/venue.rs:523-542` / `main.rs:1434` | request_id ガードを relax。空 request_id を broadcast 経路に通す（Step 5）。修正対象を `handlers/replay.rs` から `handlers/venue.rs` に変更 |
| R1-03 | A 流れ | HIGH | `screen/dashboard.rs:910-924` | `distribute_positions()` の `!panel.is_replay()` 除外を撤去（Step 4） |
| R1-04 | B 不変条件 | HIGH | `python/engine/server.py:131-146,586`, `portfolio_view.py:113` | スナップショット構造を訂正（`portfolio` ≠ `positions`、保持は `portfolio_state["positions"]`）。`to_position_records()` を新設し配列変換を明示（Step 6 / Step 8） |
| R1-05 | A 流れ | MEDIUM | `python/engine/nautilus/engine_runner.py:771,986` | 前進再生中の authoritative emit ポイントを fill_handler / `_on_bar` に固定（Step 7）。StepBackward だけの片肺修正を回避 |
| R1-06 | D テスト観点 | MEDIUM | 計画書 確認項目 | acceptance テスト T1〜T6 を新設（pane 出現 / Step 巻き戻り / Play 中更新 / 空表示 / 配列変換 / broadcast） |

### 反映内容（修正後セクション一覧）

- 冒頭に R1 反映ノート追加
- 「根本原因」を 4 経路 + Python 側 2 件に再構造化
- 「統一決定 D1〜D6」セクションを新設
- 影響範囲表を 8 行に拡張（経路ラベル付き）
- 修正方針を Step 1〜Step 8 に再編
- 「テスト計画」セクションを新設（T1〜T6 + 観測コマンド + 失敗パターン）
- 「確認項目」を 12 項目に拡張
- 「変更ファイル一覧」を 8 行に再生成
- 実装難易度を「低〜中 → 中」に修正

## ラウンド R2（2026-05-08）

### Findings

| ID | 観点 | 重要度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| R2-01 | B 不変条件 | HIGH | 計画 Step 6 / Step 8（dto.rs:752-768 schema） | `to_position_records()` 案が実 schema と不一致。`cost` フィールド不在・`market_value` 必須・`tategyoku_id: Option<String>` 必須・`position_type` は snake_case wire 値（`"cash"` 等）・`#[serde(deny_unknown_fields)]`。`market_value` に `last_price * qty` を詰める形に差替。`venue: "replay"` 許容のため dto.rs:766 コメント更新を計画項目化 |
| R2-02 | A 流れ | HIGH | 計画 Step 5 注記 | 「OrderListUpdated 側と整合」は事実誤認（`VenueMsg::OrderListUpdated` は request_id を持たない）。PositionsUpdated 独自に「空 request_id = push event」ルールを導入する根拠（server.py:1192 の前例）に書き換え |
| R2-03 | A 流れ | MEDIUM | 計画 Step 7 / engine_runner.py:787 | `_on_bar` は `has_open_positions` ガードで早期 return するため空配列 emit 不能。役割分担を明記: fill_handler が authoritative（空配列 emit を含む）、`_on_bar` は MTM 更新のみ |
| R2-04 | A 流れ | MEDIUM | 計画 Step 2 / 確認項目 | `ReplayPaneRegistry` whitelist への `"Positions"` 追加サブステップ Step 2a を新設 |
| R2-05 | D テスト観点 | LOW | 計画 T2/T3 | e2e helper の Positions pane 観測 API 確認を確認項目に追加 |
| R2-06 | B 不変条件 | LOW | 計画 Step 6 | `position_type: "cash"` 固定の TODO(margin) コメントをコード雛形に追加 |

### 反映内容

- D2 の説明を実 schema に整合（`market_value` / `tategyoku_id` / snake_case enum / venue 拡張）
- Step 2a を新設（whitelist 追加）
- Step 5 注記から「OrderList と整合」を削除し前例ベース根拠に置換
- Step 6 の `to_position_records()` を market_value / tategyoku_id 形式に書き換え + TODO 追記
- Step 7 役割分担を fill_handler authoritative / `_on_bar` MTM-only に整理
- Step 8 配列変換を market_value 計算込みに書き換え
- 確認項目に whitelist / dto.rs コメント更新 / e2e 観測 API の 3 件追加

## ラウンド R3（2026-05-08）

### Findings

| ID | 観点 | 重要度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| R3-01 | B 不変条件 | HIGH | `python/engine/schemas.py:950` | `PositionRecord.venue: Literal["tachibana"]` 固定で `"replay"` を emit すると Pydantic validation で reject。`Literal["tachibana", "replay"]` に拡張する手順を D2 / 確認項目 / 変更ファイル一覧に追加 |
| R3-02 | B 不変条件 | MEDIUM | 計画 Step 6 / Step 8 | Decimal の `str()` は `"100.0"` や指数表記を生む可能性。`qty` / `market_value` は schemas.py:946-947 が整数文字列を要求するため `str(int(Decimal))` 正規化を明記 |

### 反映内容

- D2 に schemas.py Literal 拡張を必須項目として追加
- Step 6 `to_position_records()` で `qty_str = str(int(qty))` の正規化を明示
- Step 8 配列変換に `_qty_str` ヘルパを追加し Decimal→int→str を保証
- 確認項目に schemas.py 拡張 / 整数文字列正規化を追加
- 変更ファイル一覧に schemas.py / dto.rs を追加

## ラウンド R4（サニティチェック）

### Grep ベース機械検証

- `cost` 文字列の出力経路残留: 0 件（入力形式の説明のみ）
- `position_type": "Cash"` 大文字残留: 0 件
- `venue.*Literal\["tachibana"\]` 単独残留: 0 件（拡張形に置換）
- 計画引用行（dto.rs:752-768 / schemas.py:940-950 / venue.rs:523-542 / dashboard.rs:910-924 /
  positions.rs:111-115 / engine_runner.py:771,986 / server.py:131-146,1175-1208）が実コードに存在することを R1〜R3 各ラウンドで確認済み

### 残存 Finding

- HIGH: 0 件
- MEDIUM: 0 件
- LOW: 0 件（「実装時に判断」は Step 8 末尾の重複ロジック整理 1 件のみで許容範囲）

## 完了サマリ（Issue 2）

```
=== 完了 ===
全ラウンド数: 4（R1 = 提供レビュー、R2 = schema/役割分担、R3 = schemas.py/正規化、R4 = サニティ）
修正した Finding 総数: HIGH 6 / MEDIUM 5 / LOW 2
残存: 0 件
主要な反映成果（規約レベル）:
- 4 経路修正方針: pane 自動生成 / view() 早期 return / venue.rs request_id ガード / distribute_positions() 除外
- IPC schema 整合: PositionRecordWire = market_value + tategyoku_id（cost フィールド非存在）
- venue 拡張: dto.rs コメント + schemas.py Literal を "tachibana" | "replay" に
- 整数文字列正規化: qty / market_value は str(int(Decimal))
- emit 役割分担: fill_handler が authoritative（空配列含む）/ _on_bar は MTM-only
- request_id ルール: 空文字列 = push event, server.py:1192 前例ベース
- ReplayPaneRegistry whitelist に "Positions" 追加が前提
- acceptance テスト 6 本（T1〜T6）+ 観測点定義
ログ: docs/plan/review-fixes-2026-05-08.md
```

---

# 対象計画書: `docs/plan/fix-issue3-current-time-display.md`

## ラウンド R1（2026-05-08 — ユーザー提供レビューを反映）

### 統一決定

- D1: gRPC 経路 parity — `proto/engine.proto` / `python/engine/server_grpc.py` / `engine-client/src/grpc_transport.rs` を影響範囲に追加
- D2: `src/main.rs` の `map_engine_event_to_message` arm 追加を Step として明示
- D3: 根本原因を「IPC event が存在しない」→「メニューバー向け dedicated signal がない」に修正
- D4: `ReplayMsg::DataLoaded` で `current_day` を None クリアする Step を追加
- D5: テスト計画節を新設（schema version pin / routing exhaustive / replay event）
- D6: 非 Daily フォーマットを `%H:%M:%S`（live 側に合わせる）
- D7: enum バリアント名を `ReplayMsg::TimeUpdated` に統一
- D8: `current_day` の意味変更を確認項目に注記

### Findings

| ID | 観点 | 重要度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| R1-H1 | A 流れ | HIGH | 影響範囲・変更ファイル一覧 | gRPC 経路 3 ファイルを追加 |
| R1-H2 | A 流れ | HIGH | Step（欠落） | main.rs routing arm の Step を新設 |
| R1-M1 | B 不変条件 | MEDIUM | 根本原因節 | 「IPC event が存在しない」→「dedicated signal がない」 |
| R1-M2 | B 不変条件 | MEDIUM | Step 6 | DataLoaded で current_day = None クリアを追記 |
| R1-M3 | D テスト観点 | MEDIUM | 確認項目 | テスト計画節を新設（3 テスト） |
| R1-L1 | A 流れ | LOW | Step 6 | %H:%M:%S に変更 |
| R1-L2 | B 不変条件 | LOW | 確認項目 | current_day 意味変更の注記 |
| R1-L3 | C スキーマ | LOW | 本文 | enum 名を ReplayMsg::TimeUpdated に統一 |

## ラウンド R2（2026-05-08）

### 統一決定

- A-M: Python emit 順を「DateChangeMarker → ReplayTimeUpdated」に固定し後着を保証
- C1: SCHEMA_MINOR +1 バンプを「必須」と断言形に変更
- C2: Step 8 に `oneof EngineEvent` への組み込みを明記
- D1: テスト計画 test 3 に「新規追加が必要」を明記
- D2: gRPC transport 受信テスト節を追加
- D3: Step 7 に handler unit test 追記指示を追記

### Findings

| ID | 観点 | 重要度 | 対象ファイル:行 | 修正概要 |
|---|---|---|---|---|
| R2-D1 | D テスト観点 | HIGH | テスト計画 test 3 | pytest コマンドに「新規追加が必要」明記 |
| R2-AM | A 流れ | MEDIUM | Step 2 / Step 7 | emit 順序保証を追記 |
| R2-C1 | C スキーマ | MEDIUM | 確認項目 | SCHEMA_MINOR を断言形に変更 |
| R2-C2 | C スキーマ | MEDIUM | Step 8 | oneof EngineEvent への組み込みを明記 |
| R2-D2 | D テスト観点 | MEDIUM | テスト計画 | gRPC transport テスト節を新設 |
| R2-D3 | D テスト観点 | MEDIUM | Step 7 | handler unit test 追記指示を追記 |
| R2-AL | A 流れ | LOW | Step 6 | granularity=None フォールスルーの意図を補記 |

## ラウンド R3（サニティチェック）

### Grep ベース機械検証

- 「IPC イベントが存在しない」残留: 0 件
- `%Y-%m-%d %H:%M`（秒なし非 Daily フォーマット）残留: 0 件
- `ReplayTimeUpdated { ... }` が enum バリアントとして使われていないこと: 確認済み（struct 定義のみ）
- SCHEMA_MINOR の断言形: 確認済み（「+1 バンプする（必須）」）
- oneof EngineEvent 組み込み: 確認済み（Step 8 箇条 1）

### 残存 Finding

- HIGH: 0 件
- MEDIUM: 0 件
- LOW: 1 件（Step 6 コードブロック末尾のコメント位置が断片的 — 実装時に確認で十分）

## 完了サマリ（Issue 3）

```
=== 完了 ===
全ラウンド数: 3（R1 = ユーザー提供レビュー反映、R2 = emit 順序/テスト/proto 補強、R3 = サニティ）
修正した Finding 総数: HIGH 3 / MEDIUM 7 / LOW 4
残存 LOW（対応不要）: 1 件（コメント位置 — 実装時確認）
主要な反映成果（規約レベル）:
- transport parity: WebSocket + gRPC 両経路を影響範囲に明示
- routing: main.rs map_engine_event_to_message arm を Step 5 として追加
- 根本原因: 「dedicated signal がない」が正確な表現（KlineUpdate/Trades は timestamp を持つ）
- reset: DataLoaded で current_day を None クリア（表示の古い日時残留を防止）
- format: 非 Daily は %H:%M:%S（live モードと統一）
- emit 順序: DateChangeMarker 直後に ReplayTimeUpdated を emit し後着を保証
- SCHEMA_MINOR: 必須バンプと断言
- テスト 4 本: schema version pin / routing exhaustive / replay event（新規追加） / gRPC transport（新規追加）
ログ: docs/plan/review-fixes-2026-05-08.md
```
