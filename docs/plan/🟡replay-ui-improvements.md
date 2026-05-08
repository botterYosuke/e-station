# 🟡 リプレイ UI 改善計画

## 背景

リプレイモード使用中に発見された 6 件の UX 問題を修正する。  
スクリーンショット確認日: 2026-05-08

---

## Issue 1: 銘柄入力欄の廃止 → チャートタイトルバー連動

### 現状

`widget_menu_bar.rs:242-244` にメニューバー 2 段目の `text_input("銘柄 (例: 7203)", ...)` が存在する。  
チャートタイトルバーからも銘柄は変更できるため、入力欄が重複している。

### 方針

1. メニューバーの銘柄入力欄（`text_input`）を削除。
2. チャートペインの銘柄が追加・変更されたとき（`PaneMsg::InstrumentChanged` 等）、  
   `ReplayBarState.instrument_id`（`menu_bar_state.rs:23`）を更新する。
3. リプレイ開始コマンドはこの `instrument_id` を参照して Python に渡す。  
   → 複数銘柄対応は既存の `instrument_ids: Vec<String>` 経路をそのまま使う。

### 変更ファイル候補

| ファイル | 内容 |
|---|---|
| `src/widget_menu_bar.rs:242-244` | `text_input` 削除 |
| `src/menu_bar_state.rs` | `InstrumentChanged` ハンドラを chart 側イベントから受け取るよう修正 |
| `src/messages.rs` | チャートタイトル変更 → `BarMessage::InstrumentChanged` へ橋渡し |

### 注意点

- `ReplayBarState` の `instrument_id` は Python へ渡すパラメータとして機能しているため、  
  削除ではなく「入力欄を非表示にして内部状態は維持する」変更にする。
- 複数銘柄（`instrument_ids`）の場合はチャートペインの全銘柄リストを収集して渡す。

---

## Issue 2: 保有銘柄ペインをリプレイ画面に追加

### 現状

`handlers/replay.rs:130-141` の `ReplayMsg::DataLoaded` ハンドラで自動ペイン生成時、  
注文一覧・買余力は生成されるが、保有銘柄（`PositionsPanel`）は追加されていない。

### 方針

`DataLoaded` ハンドラ内でデフォルトレイアウト生成時に `PositionsPanel::new_replay()` を追加する。  
`positions.rs:34-39` に既に `new_replay()` は実装済みなので呼び出すだけでよい。

### 変更ファイル候補

| ファイル | 内容 |
|---|---|
| `src/handlers/replay.rs:130-141` | デフォルトレイアウトに PositionsPane を追加 |

### 注意点

- リプレイ中の保有銘柄更新は現在 "no-op" になっている可能性がある。  
  `handlers/dashboard.rs` のポジション取得ハンドラでリプレイ時の push パスを確認・整備する。
- Python 側が `PositionUpdated` / `OrderListUpdated` と同タイミングで保有銘柄を emit しているか確認する。

---

## Issue 3: current time の表示を分（秒）まで対応

### 現状

`menu_bar_state.rs:30` の `current_day: Option<String>` は営業日単位（`DateChangeMarker` イベント）でしか更新されない。  
分足・tick 足リプレイ時は日内時刻が変化しても表示が日付のまま止まる。

### 方針

1. Python エンジンが各足処理後に emit する既存のスナップショットイベントか、  
   新規の `ReplayTimeUpdated { timestamp_ms: i64 }` イベントで現在時刻を Rust に通知する。
2. Rust 側で `timestamp_ms` を JST の `HH:MM:SS`（または `YYYY-MM-DD HH:MM`）にフォーマットして  
   `ReplayBarState.current_day` を上書きする。
3. granularity が Daily の場合は日付のみ表示、Minute/Tick の場合は `YYYY-MM-DD HH:MM` を表示。

### 変更ファイル候補

| ファイル | 内容 |
|---|---|
| `python/engine/…/engine_runner.py` | 各粒度処理後に現在 timestamp を emit（既存スナップに載せるか別イベント） |
| `src/handlers/replay.rs` | 新イベント受信 → `ReplayBarState.current_day` 更新 |
| `src/widget_menu_bar.rs:237` | granularity に応じてフォーマット切替 |

### 注意点

- `current_day` の型が `Option<String>` なので既存フィールドを流用できる。  
  フィールド名を `current_time` にリネームするとより意図が明確になるが、変更範囲が広がるため別 PR でよい。
- Live モードの `current_time`（`menu_bar_state.rs:84`）とフォーマットを揃える。

---

## Issue 4: メニューバーの各フィールドに現在設定値を表示

### 現状

`widget_menu_bar.rs:245-259` の各 `text_input` の placeholder は固定文字列であり、  
リプレイ開始後も設定値が反映されない。ユーザーが現在の設定を見て確認できない。

### 原因候補

`ReplayBarState` の各フィールド（`start_date`, `end_date`, `granularity`, `initial_cash`）が  
`DataLoaded` 時に Python からの応答値で上書きされていない可能性がある。

### 方針

1. `handlers/replay.rs` の `ReplayMsg::DataLoaded` 受信時に、  
   Python からの応答パラメータ（`start_date`, `end_date`, `granularity`, `initial_cash`）を  
   `ReplayBarState` に書き戻す。
2. `text_input` は `value` を `&bar.start_date` 等にバインドしているので、  
   フィールドに値が入れば自動的に表示される。
3. `granularity` の `pick_list` は `bar.granularity` が `Some` の場合に選択済み表示になるため、  
   同様に `DataLoaded` で設定する。

### 変更ファイル候補

| ファイル | 内容 |
|---|---|
| `src/handlers/replay.rs` | `DataLoaded` 時に `ReplayBarState` フィールドを Python 応答値で初期化 |
| `src/menu_bar_state.rs` | 必要に応じてフィールドのデフォルト値・初期化ロジックを整理 |

### 注意点

- Python の応答に `start_date` 等が含まれているか確認する（`dto.rs` 参照）。  
  含まれていない場合は Python 側の応答スキーマに追加が必要。
- ユーザーが入力欄を編集中の場合に上書きしないよう、  
  「フィールドが空のときのみ書き戻す」か「DataLoaded 直後のみ書き戻す」ロジックを入れる。

---

## Issue 5: 注文一覧・保有銘柄・買余力をアクションごとにリアルタイム更新

### 現状

- `handlers/replay.rs:145-178` の `ReplayMsg::Finished` で `GetOrderList` IPC を 1 回発行するだけ。
- リプレイ実行中（`Play` 中）の注文・保有銘柄更新は push されているが、  
  Step 操作後や Pause 直後のタイミングが不明瞭な可能性がある。

### 方針

以下のイベントをトリガーに注文一覧・保有銘柄・買余力を更新する：

| トリガーイベント | 更新対象 |
|---|---|
| `ReplayMsg::BuyingPower` 受信時（既存）| 買余力 ✅ |
| `OrderListUpdated` 受信時（既存）| 注文一覧 ✅ |
| `StepBackward` / `StepForward` 完了後の Python emit | 注文一覧・保有銘柄・買余力 |
| `Pause` 後の Python スナップショット push | 注文一覧・保有銘柄 |

`server.py:1156-1209` の `StepBackward` ハンドラは既に  
`OrderListUpdated` / `ReplayBuyingPower` を emit しているため、  
Rust 側でこれらを受け取った際に保有銘柄も合わせて更新するよう修正する。

保有銘柄の更新 push が未実装の場合は：
1. Python: `PositionsUpdated { positions: [...] }` イベントを `OrderListUpdated` と同タイミングで emit
2. Rust: `handlers/replay.rs` でイベント受信 → `PositionsPanel::set_positions()` 呼び出し

### 変更ファイル候補

| ファイル | 内容 |
|---|---|
| `python/engine/server.py` | `StepBackward`/`StepForward`/スナップショット push 時に `PositionsUpdated` emit を追加 |
| `src/handlers/replay.rs` | `PositionsUpdated` イベント受信ハンドラを追加 |
| `src/handlers/dashboard.rs` | リプレイモード時の `PositionsUpdated` ルーティング確認 |

### 注意点

- Play 中は高頻度更新になるため、描画負荷を考慮して  
  変化があった tick のみ emit する（差分 emit）か、描画側で debounce する。
- `Finished` 後の 1 回更新は残しつつ、イベント駆動更新を追加する形にする。

---

## Issue 6: 1step 戻る / 1step 進む / 一時停止 ボタンの動作確認

### 現状

ボタン定義は `widget_menu_bar.rs:196-219` に存在し、IPC コマンドも  
`engine-client/src/dto.rs:257-280` に定義されている。  
ただし実際に動作しているかスクリーンショットからは確認できない。

### 確認項目

| ボタン | IPC コマンド | Python ハンドラ | 確認状況 |
|---|---|---|---|
| ⏸ Pause | `PauseReplay` | `server.py` の `pause_replay` | 要確認 |
| ▶ Resume | `ResumeReplay` | `server.py` の `resume_replay` | 要確認 |
| ⏮ StepBackward | `StepBackward` | `server.py:1156-1209` | 実装あり |
| ⏭ StepForward | `StepReplay` | `server.py` の step_replay | 要確認 |

### 確認・修正方針

1. 各ボタン押下時に `BarMessage::Press*` → IPC コマンド発行の経路をトレースする。
2. Pause 後に ▶ で再開できるか、および `ReplayControlState` によるボタン有効/無効の遷移を確認する。
3. StepForward が単一足進めるだけか、複数足進む場合の挙動を確認する。
4. バグが見つかった場合は個別 fix PR を立てる。

### 変更ファイル候補（バグがあった場合）

| ファイル | 内容 |
|---|---|
| `src/main.rs` | ボタン押下 → IPC コマンド発行の経路 |
| `python/engine/server.py` | 各コマンドハンドラの実装 |
| `src/menu.rs` | `ReplayControlState` の状態遷移 |

---

## 実装状況

| 状態 | Issue | 完了日 / 備考 |
|---|---|---|
| ✅ 完了 | Issue 1 (銘柄入力欄廃止) | 2026-05-08 — R1 レビュー反映済み |
| ✅ 完了 | Issue 6 (ボタン動作確認) | 2026-05-08 — R5 まで全指摘解消済み |
| ✅ 完了 | Issue 4 (設定値表示) | 2026-05-08 — R1 レビュー反映済み |
| ✅ 完了 | Issue 3 (時刻表示) | 2026-05-08 |
| ✅ 完了 | Issue 5 (リアルタイム更新) | 2026-05-08 |
| ✅ 完了 | Issue 2 (保有銘柄ペイン) | 2026-05-08 — R1 レビュー反映済み |

---

## 関連ファイルマップ

```
src/
  widget_menu_bar.rs          # メニューバー UI レンダリング
  menu_bar_state.rs           # ReplayBarState / LiveBarState
  messages.rs                 # BarMessage / ReplayMsg 定義
  handlers/
    replay.rs                 # リプレイイベントハンドラ
    dashboard.rs              # 注文・ポジション・買余力ハンドラ
  screen/dashboard/panel/
    orders.rs                 # 注文一覧パネル
    positions.rs              # 保有銘柄パネル
    buying_power.rs           # 買余力パネル
  menu.rs                     # ReplayControlState

python/engine/
  server.py                   # IPC コマンドハンドラ・スナップショット push
  nautilus/engine_runner.py   # 各足処理・DateChangeMarker emit

engine-client/src/
  dto.rs                      # IPC コマンド / イベント DTO 定義
```
