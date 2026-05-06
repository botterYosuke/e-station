# Replay コントロールバー計画書 レビュー修正ログ

対象: `C:\Users\sasai\.claude\plans\c-users-sasai-documents-e-station-spicy-gosling.md`

---

## ラウンド 1（2026-05-06）

【統一決定】
- actions_for_mode(Replay) = [Open, Save, SaveAs, Quit]（Live と同一）
- Action::ReplayStart / ReplayStop の enum variant 自体を削除する
- main.rs 行番号参照 → シンボル名参照に置換
- Pause セマンティクス = threading.Event ベース（CPU スピン排除）
- current_day 更新源 = DateChangeMarker イベントの date フィールド
- StepReplay 受付 state = PAUSED のみ
- Python AttemptedCommand / ReplayOnlyCommand Literal へ 3 op 追加

| Finding ID | 観点 | 対象 | 修正概要 |
|---|---|---|---|
| A-H1 | A | Step 6 テキスト | actions_for_mode 集合 [Open,Save,Quit]→[Open,Save,SaveAs,Quit] |
| A-H2 | A | Step 6 | ReplayStart/ReplayStop enum variant 削除を明記 |
| B-H1 | B | 既存の再利用節 | main.rs 行番号参照をシンボル名に変更 |
| C-H1 | C | §1 Pause セマンティクス | threading.Event ベース実装に書き換え |
| C-H2 | C | main.rs 変更欄 | EngineBusy.progress → DateChangeMarker.date に変更 |
| D-H1 | D | 検証方法テーブル | engine_command.rs → schema_v2_5_roundtrip.rs に修正 |
| D-H2 | D | test_replay_pause.py | 受け入れ条件 3 点を具体化 |
| A-M3 | A | Step 4 | 動的 Y アンカー非再導入の注記を追加 |
| A-M4 | A | Context 表 | 現行 Replay 集合の注記を追加 |
| B-M1 | B | Step 4 | main.rs への mode 引数伝播を明記 |
| B-M2 | B | menu_bar_state.rs 変更欄 | Granularity import パスを明記 |
| C-M3 | C | §1 / §3 | StepReplay は PAUSED のみ受け入れ・enable 条件を修正 |
| C-M4 | C | 既知のリスク | Pause 中 engine_busy 扱いの対処方針を具体化 |
| C-M5 | C | Step 2 | accelerator orphan 対策として dispatch アーム削除を明記 |
| D-M3 | D | Step 3 | 3×4 状態遷移テスト表を追加 |
| D-M4 | D | Step 6 | menu テスト否定 assert への書き換えを明記 |
| D-M5 | D | 検証方法テーブル | dropdown anchor assert 追記 |
| D-M7 | D | 検証方法テーブル | ReplayBarState 状態遷移テスト行を追加 |
| C-L6 | C | §1 schemas.py 行 | AttemptedCommand/ReplayOnlyCommand Literal 追加を明記 |

---

## ラウンド 2（2026-05-06）

【統一決定】
- 既知のリスク節の「Step backward: 完全に出さない」行を削除
- Step 3 状態表に ⏮ Step- 列を追加
- ReplayBarState に replay_has_history: bool フィールドを追加
- Step 2 IPC 拡張に ReplayHistoryChanged を追加
- StepBackward 受け入れ state = PAUSED のみ
- deepcopy 失敗時のログ出力を明記
- ui_events 再送信は RestoreSnapshot 専用 event で pane 全置換語義を明示
- test_replay_pause.py 条件 4 追加 + test_replay_snapshot.py 新設
- DateChangeMarker: dto.rs 実装済み・schemas.py のみ追加

| Finding ID | 観点 | 対象 | 修正概要 |
|---|---|---|---|
| R2-A-1 | A | 既知のリスク節 | 「Step backward: 完全に出さない」旧記述を削除 |
| R2-A-3 | A | Step 3 状態表 | ⏮ Step- 列を追加（idle=✗/running=✗/paused=○*） |
| R2-A-5 | A | menu_bar_state.rs 変更欄 | replay_has_history フィールドを追加 |
| R2-C-1 | C | Snapshot ring buffer 設計 | StepBackward 受信を 4 ステップ手順に書き換え（RestoreSnapshot 先行送信） |
| R2-C-2 | C | Pause セマンティクス | StepBackward は PAUSED のみ受け入れを追記 |
| R2-C-3 | C | deepcopy 失敗対策 | logger.warning でログ出力を明記 |
| R2-C-5/D-8 | C+D | Step 2 IPC 拡張 | ReplayHistoryChanged IPC を追加 |
| R2-D-6 | D | test_replay_pause.py | 受け入れ条件 (4) StepBackward テストを追加 |
| R2-D-7 | D | 検証方法テーブル | test_replay_snapshot.py 新設行を追加 |
| R2-B-6 | B | main.rs DateChangeMarker 記述 | dto.rs 実装済み・schemas.py のみ追加と確定 |
| R2-C-4 | C | 既知のリスク | maxlen=1000 粒度非対称リスクを追加 |

---

## ラウンド 3（2026-05-06）

【統一決定】
- RestoreSnapshot / ReplayHistoryChanged を schemas.py / dto.rs の追加対象に明記
- ⏹ 停止は PressStop / BarMessage::PressStop 独立経路（Action::ReplayStop 依存を除去）
- BarMessage に PressStop variant を追加
- Step 5 handler に PressStepBackward / PressStop の IPC dispatch を明記

| Finding ID | 観点 | 対象 | 修正概要 |
|---|---|---|---|
| R3-H-1 | A | §1 IPC 拡張 schemas.py/dto.rs 行 | RestoreSnapshot / ReplayHistoryChanged を追加対象に明記 |
| R3-H-2 | A/C | §3 ⏹ 停止行 + Step 5 + BarMessage | PressStop 独立経路に書き直し・variant 追加・Step 5 handler 明記 |

残存 LOW（対応不要）:
- R2-B-6（解消済み）
- R2-C-4（粒度非対称: 既知のリスクに追記済み）
