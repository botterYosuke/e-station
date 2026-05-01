# Review Fixes Log — Phase 8 計画書

## ラウンド 1（2026-05-01）

### 統一決定

1. `TachibanaLogin` → `RequestVenueLogin`：既存スキーマを再利用。新規 wire format を導入しない
2. token/SCHEMA_MAJOR mismatch 挙動：`_AttachClient.handshake()` は `ConnectionRefusedError` を raise。`ReplaySession.__enter__` は catch して in-process にフォールバック（warn ログ付き）。`force_mode="attach"` 時は例外をそのまま伝播
3. `check_data_exists` 所在：`NautilusRunner.check_data_exists()` → `jquants_loader.check_data_exists()` に統一
4. `data_path()` env-override バグ：Phase 8.1b B2 に Rust 修正タスクを追加（`path_name` が join されない問題）

### Findings 一覧

| ID | 観点 | 重要度 | 対象:行 | 修正概要 |
|---|---|---|---|---|
| A-H1 | A | HIGH | phase-8:4,913 | phase-8-review.md / phase-8-review2.md dead link を削除・注記に変更 |
| A-H2 | A | HIGH | phase-8:569,699,708 | TachibanaLogin → RequestVenueLogin に統一（統一決定 #1） |
| B-H1 | B | HIGH | phase-8:§4.2.1,§5/B2 | data_path() env-override バグ対応タスクを B2 作業項目に追記（統一決定 #4） |
| C-H1 | C | HIGH | phase-8:§4.2 | handshake 途中切断時の close() try/finally 保証を §4.2 に明記 |
| C-H2 | C | HIGH | phase-8:§4.2.1/B2 | engine-session.json のパーミッション 0o600 設定を B2 作業項目に追記 |
| D-H1 | D | HIGH | phase-8:§4.2,§6.1 | token/SCHEMA_MAJOR mismatch 挙動を確定・§4.2 と §6.1 に明記（統一決定 #2） |
| D-H2 | D | HIGH | phase-8:§6.1 | test_replay_session_double_enter.py を §6.1 テスト表に追加 |
| D-H3 | D | HIGH | phase-8:§4.1,§6.1 | strategy_file 不存在時の FileNotFoundError を §4.1 run() に明記・§6.1 テスト追加 |
| A-M1 | A | MEDIUM | phase-8:27,613 | Q4 識別子参照を open-questions.md の実在記述に変更・Phase 8.0 チェックリスト追加 |
| A-M2 | A | MEDIUM | phase-8:§9 | implementation-plan.md へのスタブ追加タスクを Phase 8.0 チェックリストに追加 |
| A-M3 | A | MEDIUM | phase-8:§5/8.1c | Phase 8.1c 冒頭に Q3b 決定前提条件ブロックを追加 |
| B-M1 | B | MEDIUM | phase-8:§4.1 | NautilusRunner.check_data_exists → jquants_loader.check_data_exists に変更（統一決定 #3） |
| B-M3 | B | MEDIUM | phase-8:§4.2 冒頭 | 行番号リンク陳腐化リスクの注記を §2 または §4.2 冒頭に追加 |
| B-M4 | B | MEDIUM | phase-8:§2.1 | 合計行数 6,758 → 6,756 に訂正 |
| C-M1 | C | MEDIUM | phase-8:§4.1/__exit__ | NautilusRunner.dispose() の冪等性前提を §4.1 に追記 |
| C-M2 | C | MEDIUM | phase-8:§4.2 | stale session ファイル時のフォールバック契約（pid live + probe 成功の両条件）を §4.2 に明記 |
| C-M3 | C | MEDIUM | phase-8:§6.1 | test_server_multi_client.py に Rust reconnect 非干渉ケースの assert を追記 |
| C-M5 | C | MEDIUM | phase-8:§4.1/load | check_data_exists False → FileNotFoundError の明示・§6.1 テスト追記 |
| D-M1 | D | MEDIUM | phase-8:§5/B2 | Rust B2 テストの assert 内容・実行コマンドを追記 |
| D-M2 | D | MEDIUM | phase-8:§6.1 | 模擬 GUI client の実装方法（Python websockets クライアント、J-Quants 不要）を §6.1 に追記 |
| D-M3 | D | MEDIUM | phase-8:§6 | §6.3 CI ゲート組込セクション新設（live マーカー・CI コマンド） |
| D-M4 | D | MEDIUM | phase-8:§5/8.1c | Phase 8.1c テスト項目にバリデーション string-assertion テストを追加 |

## ラウンド 2（2026-05-01）

### 統一決定

1. Q参照修正：計画書全体で `Q4 相当` → `Q2（Python プロセスのライフサイクル管理）` に変更
2. セッションパス修正：`<data_path>/engine/engine-session.json` の `engine/` サブフォルダを削除 → `<data_path>/engine-session.json`
3. メソッド名修正：`NautilusRunner.dispose()` → `NautilusRunner.stop()` に変更（dispose メソッドは実在しない）
4. `events()` 終端条件：`EngineStopped` 受信でジェネレータを終了する旨を §4.2 に明記

### Findings 一覧

| ID | 観点 | 重要度 | 対象:行 | 修正概要 |
|---|---|---|---|---|
| A3-R2 | A | HIGH | implementation-plan.md 末尾 | Phase 8 スタブセクションを実ファイルに追加 |
| A2-R2 | A | MEDIUM | phase-8:§5/Phase8.0行630 | Q4 相当 → Q2（Python プロセスのライフサイクル管理）に変更（統一決定 #1） |
| B1-R2 | B | MEDIUM | phase-8:§4.1/__enter__行347 | セッションパスの `engine/` サブフォルダ誤混入を削除（統一決定 #2） |
| B2-R2 | B | MEDIUM | phase-8:§4.1/__exit__行362 | NautilusRunner.dispose() → stop() に変更（統一決定 #3） |
| C-M-new-1 | C | MEDIUM | phase-8:§4.2/events() | EngineStopped 受信でジェネレータを終了する終端条件を §4.2 に追記（統一決定 #4） |
| C-M-new-2 | C | MEDIUM | phase-8:§4.2/実装注意 | token をログ/例外メッセージに含めない旨を §4.2 実装注意に追記 |

## ラウンド 3（2026-05-01）— 収束確認

全 R1/R2 統一決定の波及を機械検証（Grep）で確認。新規 HIGH/MEDIUM Finding ゼロ。**収束**。

- `TachibanaLogin` 残存: 0
- `NautilusRunner.check_data_exists` 残存: 0
- `Q4 相当` 残存: 0
- `engine/engine-session` 残存: 0
- `.dispose()` 残存: 0
- §4.2 `EngineStopped` 終端条件: 確認済み
- §4.2 token ログ禁止: 確認済み
- implementation-plan.md Phase 8 スタブ: 追加済み
