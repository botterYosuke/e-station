# 修正計画書: Issue 3 — current time の表示を分（秒）まで対応

> **✅ 実装完了 (2026-05-08)**

## 根本原因

`menu_bar_state.rs:30` の `current_day: Option<String>` は
`handlers/replay.rs:181-183` の `ReplayMsg::DateChanged(date)` ハンドラで更新される。

Python 側では `python/engine/nautilus/engine_runner.py` が `DateChangeMarker` イベントを
**営業日が切り替わるときのみ** emit する設計になっている。
そのため、分足・tick 足リプレイでは日内時刻が進んでも `current_day` の表示は
「最後に日付が変わった時点の日付文字列」のまま停止する。

**根本原因**: メニューバー向けの dedicated time signal がない（KlineUpdate・Trades は
timestamp を保持しているが、メニューバーはそれらを購読しておらず、
`DateChangeMarker` しか受け取れない）。

## 影響範囲

- `python/engine/nautilus/engine_runner.py` — 各足処理後の timestamp emit
- `engine-client/src/dto.rs` — 新 IPC イベント `ReplayTimeUpdated` の定義
- `python/engine/schemas.py` — `ReplayTimeUpdated` スキーマ定義
- `src/messages.rs` — `ReplayMsg::TimeUpdated { timestamp_ms: i64 }` バリアント追加
- `src/handlers/replay.rs` — 新イベント受信 → `current_day` 更新
- `src/main.rs` — `map_engine_event_to_message` に `ReplayMsg::TimeUpdated` arm 追加
- `src/widget_menu_bar.rs:237` — granularity に応じたフォーマット切替
- `proto/engine.proto` — `ReplayTimeUpdated` メッセージ追加（gRPC parity）
- `python/engine/server_grpc.py` — gRPC 経路で `ReplayTimeUpdated` emit
- `engine-client/src/grpc_transport.rs` — gRPC 受信 → dto 変換

## 修正方針

### Step 1: Python — 新 IPC イベント `ReplayTimeUpdated` を定義

`python/engine/schemas.py` に追加（`DateChangeMarker` の近くに配置）:

```python
class ReplayTimeUpdated(IpcMessage):
    """各足処理後に現在リプレイ時刻を Rust に通知するイベント。"""
    event: Literal["ReplayTimeUpdated"] = "ReplayTimeUpdated"
    timestamp_ms: int
```

### Step 2: Python — `engine_runner.py` で各足処理後に emit（streaming 専用設計）

**注意**: `start_backtest_replay()`（run-once 経路）は決定論性テスト・gym_env 用に温存しており、
per-tick emit は意図的にスコープ外（streaming 専用設計）。`ReplayTimeUpdated` の emit は
`start_backtest_replay_streaming()` のみで実施する。

`DateChangeMarker` を emit している箇所の**同じループ内**に、毎足 `ReplayTimeUpdated` を追加する。
これは `DateChangeMarker` と異なり日付変更の有無に関わらず毎足 emit する。
Python 側では DateChangeMarker の emit 直後に ReplayTimeUpdated を emit し、Rust の message queue で常に TimeUpdated が DateChanged より後着することを保証する。

Daily 足の場合はイベントの `timestamp_ms` を日付開始 (00:00:00 JST) として扱う。

```python
# engine_runner.py — 既存の DateChangeMarker emit の前後に追加
server._outbox.append({
    "event": "ReplayTimeUpdated",
    "timestamp_ms": int(bar.ts_event / 1_000_000),  # ns → ms
})
```

### Step 3: Rust — `dto.rs` に `ReplayTimeUpdated` を追加

```rust
// engine-client/src/dto.rs
#[derive(Debug, Clone, Deserialize)]
pub struct ReplayTimeUpdated {
    pub timestamp_ms: i64,
}
```

IPC event dispatch テーブルにも追加し `ReplayMsg::TimeUpdated` にマッピングする。

### Step 4: Rust — `messages.rs` に `ReplayMsg::TimeUpdated` 追加

```rust
// src/messages.rs
ReplayMsg::TimeUpdated { timestamp_ms: i64 },
```

### Step 5: Rust — `main.rs` の `map_engine_event_to_message` に arm 追加

`src/main.rs` の `map_engine_event_to_message` 関数に `ReplayMsg::TimeUpdated` の arm を追加する。
これを省略すると event が UI に届かないため必須。

```rust
// src/main.rs — map_engine_event_to_message 内
EngineEvent::ReplayTimeUpdated(e) => {
    Some(Message::Replay(ReplayMsg::TimeUpdated { timestamp_ms: e.timestamp_ms }))
}
```

### Step 6: Rust — `handlers/replay.rs` でフォーマットして反映

```rust
ReplayMsg::TimeUpdated { timestamp_ms } => {
    // JST = UTC+9
    let secs = timestamp_ms / 1000;
    let ndt = chrono::DateTime::from_timestamp(secs, 0)
        .map(|utc| utc + chrono::Duration::hours(9));
    let formatted = match (ndt, self.menu_bar.replay_bar.granularity) {
        (Some(dt), Some(Granularity::Daily)) => dt.format("%Y-%m-%d").to_string(),
        (Some(dt), _) => dt.format("%H:%M:%S").to_string(),
        (None, _) => "--".to_string(),
    };
`granularity == None` のケースは時刻フォーマット (`%H:%M:%S`) にフォールスルーする（意図的）。
    self.menu_bar.replay_bar.current_day = Some(formatted);
    return Task::none();
}
```

また、`ReplayMsg::DataLoaded` ハンドラでは `current_day` を `None` にクリアすること。
新しい replay 開始直後から最初の `TimeUpdated` が届くまで古い日時が表示されたままになるのを防ぐ。

```rust
ReplayMsg::DataLoaded { .. } => {
    self.menu_bar.replay_bar.current_day = None;  // 旧日時を即クリア
    // ... 既存処理 ...
}
```

### Step 7: `DateChanged` イベントの扱い

`ReplayTimeUpdated` が毎足 emit されるようになれば `DateChanged` (schema 3.15) との
競合が問題になる可能性がある。`DateChanged` は `DateChangeMarker` の文字列をそのまま渡すため
フォーマット形式が異なる場合がある。

**方針**: `DateChanged` ハンドラはそのまま残し、`ReplayMsg::TimeUpdated` ハンドラが後勝ちで上書きする形とする
（`DateChanged` と `TimeUpdated` は同じ足イベントの異なるトリガーなので競合しない）。
`handlers/replay.rs` のテストモジュールに、DateChanged と TimeUpdated が連続で届いた場合に TimeUpdated が後勝ちで表示されることを確認する handler unit test を追加すること。

### Step 8: gRPC 経路の parity

WebSocket 経路と同様に gRPC 経路でも `ReplayTimeUpdated` を送受信できるようにする。

1. `proto/engine.proto` — `ReplayTimeUpdated` メッセージ定義を追加し、既存 stream 定義の `oneof EngineEvent` に `ReplayTimeUpdated` フィールドを組み込む
2. `python/engine/server_grpc.py` — 各足処理後に gRPC stream へ `ReplayTimeUpdated` を emit
3. `engine-client/src/grpc_transport.rs` — gRPC 受信側で `ReplayTimeUpdated` を dto に変換し
   既存の dispatch パスに流す

### 注意点（emit 頻度）

Daily 足なら 1 本 / 日なので emit 負荷は軽微。
Minute 足・Tick 足では高頻度になるため、Python 側で連続 emit を前の timestamp と同一の場合に
スキップする最適化を入れることを検討する（一時停止中に Step 操作する場合を除く）。
ts スキップ最適化は今回意図的に見送り（フォローアップ項目）。

## テスト計画

### 1. `engine-client/tests/schema_v2_4_nautilus.rs` — schema version pin

`SCHEMA_MINOR` が `ReplayTimeUpdated` 追加後の正しい値になっているかを確認する。
バンプ忘れによる無音の version mismatch を防ぐ。

```
cargo test --workspace -p engine-client schema_v2
```

### 2. `tests/engine_event_routing_exhaustive.rs` — explicit-arm guard

`map_engine_event_to_message` の網羅テーブルに `ReplayMsg::TimeUpdated` が追加されているかを
コンパイル時に強制する exhaustive match テスト。

```
cargo test --workspace engine_event_routing_exhaustive
```

### 3. `python/tests/test_replay_speed.py` 近辺 — replay event test

このテストは新規追加が必要。test_replay_speed.py または新設 test_replay_time_updated.py にテストを追加すること。

- `ReplayTimeUpdated` が毎足 emit されること
- Daily 足では `ts_event / 1_000_000` の ns → ms 変換が正しいこと

```
uv run pytest python/tests/test_replay_speed.py -k time_updated
```

観測コマンド全体:

```
cargo test --workspace
uv run pytest python/tests/test_replay_speed.py -k time_updated
```

### 4. `engine-client/tests/` — gRPC transport 受信テスト（新規追加が必要）

`grpc_transport.rs` が ReplayTimeUpdated を受信して dto に正しく変換することを確認する。
観測コマンド: `cargo test -p engine-client grpc_time_updated`

## 確認項目

- [ ] `chrono` クレートが既に依存関係に含まれているか
- [ ] `engine_runner.py` で `timestamp_ms` として使う時刻が正しいフィールドか（`ts_event` vs `ts_init`）
- [ ] SCHEMA_MINOR を +1 バンプする（ReplayTimeUpdated 新規追加のため必須）
- [ ] Live モード `current_time` (menu_bar_state.rs:84) と同じフォーマット（`%H:%M:%S`）になっているか
- [ ] `DataLoaded` 時の `current_day` クリアが実装されているか
- [ ] `current_day` フィールドの意味が day-only から full timestamp 表示に変わることを
  `menu_bar_state.rs` の型定義コメントで明示すること

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `python/engine/schemas.py` | `ReplayTimeUpdated` クラスを追加 |
| `python/engine/nautilus/engine_runner.py` | 各足処理後に `ReplayTimeUpdated` emit |
| `engine-client/src/dto.rs` | `ReplayTimeUpdated` struct と dispatch 追加 |
| `src/messages.rs` | `ReplayMsg::TimeUpdated { timestamp_ms: i64 }` 追加 |
| `src/handlers/replay.rs` | `ReplayMsg::TimeUpdated` ハンドラを追加し `current_day` をフォーマット、`DataLoaded` で `current_day` を None クリア |
| `src/main.rs` | `map_engine_event_to_message` に `ReplayMsg::TimeUpdated` arm 追加 |
| `proto/engine.proto` | `ReplayTimeUpdated` メッセージを追加（gRPC parity） |
| `python/engine/server_grpc.py` | gRPC 経路で `ReplayTimeUpdated` emit |
| `engine-client/src/grpc_transport.rs` | gRPC 受信 → dto 変換 |

## 実装難易度

**中**。新 IPC イベントの追加が必要（Python → Rust の全経路を実装）。
`DateChanged` との整合を保つ必要がある。

---

## レビュー反映 (2026-05-08, TDD 実装ラウンド)

### 実装済み ✅

| ファイル | 変更内容 |
|---|---|
| `python/engine/schemas.py` | `ReplayTimeUpdated` クラス追加、SCHEMA_MINOR: 21 → 22 |
| `python/engine/nautilus/engine_runner.py` | 各足処理後・DateChangeMarker 直後に `ReplayTimeUpdated` emit |
| `python/engine/server_grpc.py` | gRPC ディスパッチテーブルに `ReplayTimeUpdated` 追加 |
| `proto/engine.proto` | `ReplayTimeUpdatedEvent` メッセージ定義、oneof フィールド 52 追加 |
| `engine-client/src/lib.rs` | SCHEMA_MINOR: 21 → 22 |
| `engine-client/src/dto.rs` | `EngineEvent::ReplayTimeUpdated { timestamp_ms: i64 }` 追加 |
| `engine-client/src/grpc_transport.rs` | `Payload::ReplayTimeUpdated` → dto マッピング追加 |
| `src/messages.rs` | `ReplayMsg::TimeUpdated { timestamp_ms: i64 }` 追加 |
| `src/main.rs` | `map_engine_event_to_message` に `ReplayTimeUpdated` arm 追加 |
| `src/handlers/replay.rs` | `ReplayMsg::TimeUpdated` ハンドラ（JST フォーマット、granularity 切替）追加、`DataLoaded` で `current_day = None` クリア |
| `tests/engine_event_routing_exhaustive.rs` | 期待バリアント数 52 → 53 更新 |

### 新規テスト

| テストファイル | テスト内容 |
|---|---|
| `python/tests/test_replay_time_updated.py` | schema クラス存在・インスタンス化・SCHEMA_MINOR・source inspection（8 件） |
| `src/main.rs` 内 | `replay_time_updated_is_routed_to_replay_msg`・`replay_time_updated_handler_sets_current_day`・`data_loaded_clears_current_day`（3 件） |
| `engine-client/tests/schema_v2_4_nautilus.rs` | SCHEMA_MINOR == 22 ピン |

### 設計判断

- **emit 順序**: `DateChangeMarker` 直後に `ReplayTimeUpdated` を emit。同一 message queue で TimeUpdated が後着し DateChanged を上書きする。
- **フォーマット**: `Daily` 粒度は `%Y-%m-%d`、それ以外（`Minute`/`Trade`/`None`）は `%H:%M:%S` (JST)。`format_live_time` と同パターン（`Utc.timestamp_millis_opt().single()`）。
- **DataLoaded クリア**: 新リプレイ開始時に `current_day = None` をセット。最初の `TimeUpdated` が届くまで空表示（`--` でなく `None` = プレースホルダー表示）。
- **gRPC parity**: `engine_pb2.py` の再生成は別途 `grpc_tools.protoc` で対応（proto 定義と server_grpc.py マッピングは完了済み）。

### 検証結果

`cargo check / clippy / test --workspace` 全緑（322 Rust テスト通過）。
`uv run pytest python/tests/test_replay_time_updated.py` 全緑（8 件通過）。

## レビュー反映 (2026-05-08, R1)
✅ C-1: engine_pb2.py 再生成（gRPC AttributeError 修正）
✅ C-2: server_grpc.py SCHEMA_MINOR 21 → 22
✅ H-1: run-once 経路は streaming-only 設計として plan に明記
✅ H-2: per-tick emit 失敗を break → continue に変更
✅ H-3: test_emit_is_after_date_change_marker を行番号比較に強化
✅ H-4: Rust テストの src.contains を "EngineEvent::ReplayTimeUpdated" に強化
✅ M-1: current_day コメント更新
✅ M-2: format_replay_time ヘルパー抽出
✅ M-3: REQUIRED_EXPLICIT_ARMS に ReplayTimeUpdated 追加
✅ M-4: cargo fmt 修正（venue.rs / positions.rs）
✅ M-5: import ast 削除
✅ M-6: _dict_to_proto_event warning に payload 追加
✅ M-7: ts-skip 見送り追記
