# engine pipe non-UTF-8 デッドロック修正計画

**作成日**: 2026-04-28  
**ブランチ**: fix/engine-pipe-non-utf8-deadlock  
**状態**: 修正完了（Bug-A/B/C 全修正済み）

---

## 問題の症状

市場時間外に Ladder ペインが "Waiting for data..." のまま表示されない。  
ログには以下のシーケンスが残る：

```
01:18:56.119 INFO  Python stdout: "DEBUG DepthSnapshot received: bids=10 asks=10"
01:18:56.132 INFO  Rust: VenueEvent was_ready=true → false (market_closed)
01:18:56.158 WARN  Python websockets.server > TEXT {"event":"DepthSnapshot"...} (817 bytes)
01:18:56.161 WARN  engine pipe read error: stream did not contain valid UTF-8  ← バグ
01:18:56.188 INFO  a stream disconnected from Tachibana Stock WS: "market_closed"
```

---

## 根本原因

### Bug-A: `forward_lines` が stderr の non-UTF-8 で停止する

`engine-client/src/process.rs` の `forward_lines` 関数は Python の
stderr/stdout をログに転送するが、非 UTF-8 バイト（Tachibana API の
Shift-JIS レスポンスやデバッグ出力）を受信すると `break` して終了していた。

**現在のコード状態**（`process.rs:493`）:

```rust
Err(e) if e.kind() == std::io::ErrorKind::InvalidData => {
    // Non-UTF-8 line — skip and continue
    log::debug!(target: "engine", "engine pipe: non-UTF-8 line skipped");
}
Err(e) => {
    log::warn!(target: "engine", "engine pipe read error: {e}");
    break;  // ← 以前はここに到達していた
}
```

コード上は `InvalidData` アームが存在する。しかし **実行中のバイナリが
このコミット以前のビルドである**ため、依然として WARN ログが出ている。

**デッドロック連鎖**:

1. `forward_lines(stderr)` が非 UTF-8 で終了
2. Rust が Python の stderr を読まなくなる
3. Python の stderr バッファ（通常 4 KB）が満杯
4. Python の `logging` が `stderr.write()` でブロック
5. Python の asyncio イベントループが停止
6. WebSocket 送信（DepthSnapshot JSON）がキューで詰まる
7. Rust の `depth_stream` が DepthSnapshot を受け取れない
8. Ladder は空のまま

### Bug-B: バイナリが古い（ビルド未実施）

`process.rs` の修正はソースコード上にあるが、`cargo build` が
実行されていないため実行バイナリに反映されていない。

---

## 検証方法

### Step 1: cargo build してログを再確認

```bash
cargo build
cargo run -- --data-engine-url ws://127.0.0.1:19876/
```

**期待するログ変化:**

| 修正前 | 修正後 |
|--------|--------|
| `WARN engine pipe read error: stream did not contain valid UTF-8` | `DEBUG engine pipe: non-UTF-8 line skipped` |

修正後に WARN が出なくなれば Bug-A は解消。

### Step 2: Ladder 表示を確認

ビルド後に市場時間外で起動し、Ladder に気配データが表示されるか確認する。

**表示されない場合**: Bug-C（下記）が存在する。

---

## 追加調査が必要なケース（Bug-C）

`cargo build` 後も Ladder が空の場合、WebSocket 送受信自体に問題がある。

### 調査用ログ追加箇所

**`src/screen/dashboard.rs:1152-1159`** の `ingest_depth` 内:

```rust
pane::Content::Ladder(panel) => {
    if let Some(panel) = panel {
        log::info!("ingest_depth: Ladder matched and data inserted for {stream:?}");
        panel.insert_depth(depth, depth_update_t);
    } else {
        log::warn!(
            "depth data for {stream:?} arrived before Ladder was initialized — dropped"
        );
    }
}
```

- `"Ladder matched and data inserted"` が出ない → `matches_stream` が false
- `"arrived before Ladder was initialized"` が出る → `set_content_and_streams` のタイミング問題

**`src/screen/dashboard.rs:1169-1173`** の後:

```rust
if !found_match {
    log::warn!("ingest_depth: no pane matched stream {stream:?} — calling refresh_streams");
}
```

これらのログを追加した後に再ビルド・再実行してログを確認する。

---

## 実装計画

### Task 1: バイナリ再ビルド（最優先）

```bash
cargo build
```

これだけで Bug-A と Bug-B が解消する可能性が高い。

### Task 2: テスト確認

```bash
cargo test -p flowsurface-engine-client
```

既存の `forward_lines_does_not_stop_after_non_utf8_line` テスト（`process.rs:530`）
が PASS することを確認する。

### Task 3: Ladder 表示確認（市場時間外）

アプリ起動後、以下を確認:
- "Waiting for data..." が消えて気配が表示される
- バナー「東証は現在市場時間外です」が表示される
- `engine pipe: non-UTF-8 line skipped` が DEBUG レベルで出る（WARN は出ない）

### Task 4: Bug-C が存在する場合の調査ログ追加

Task 3 で表示されない場合のみ実施。上記 Bug-C セクションの
ログを追加して原因を特定する。

---

## 非機能要件

- Binance/Bybit 等の他取引所には影響しない（変更箇所は `process.rs` のみ）
- `forward_lines` のテストが既に存在する（追加不要）
- Bug-C 調査ログは問題特定後に削除すること

---

## Bug-C: depth_stream が trade stream の Disconnected で終了する（新発見）

### 根本原因（2026-04-28 デバッグで判明）

`stream_trades` は市場時間外の場合 `await` なしで即座に
`Disconnected{stream="trade", ticker=XXX, reason="market_closed"}` を outbox に追加して返る。

`engine-client/src/backend.rs` の `depth_stream` ループの Disconnected ハンドラは
`stream` フィールドを検査していなかったため、trade stream の Disconnected を受信して
ループを終了し、その後 REST fetch で返ってくる `DepthSnapshot` を無視していた。

```rust
// 修正前
Ok(EngineEvent::Disconnected { venue: ev_venue, ticker, market: ev_market, reason, .. }) => {
    if ev_venue != venue || ticker != ticker_sym { continue; }
    // stream フィールドを見ていない → trade stream の Disconnected でも return!
    return;
}

// 修正後
Ok(EngineEvent::Disconnected { venue: ev_venue, ticker, stream: ev_stream, market: ev_market, reason, .. }) => {
    if ev_venue != venue || ticker != ticker_sym { continue; }
    if ev_stream != "depth" { continue; }  // stream フィールドを検査
    return;
}
```

`trade_stream` のハンドラも同様に `stream: "trade"` 検査を追加。

---

## Acceptance criteria

- [x] `cargo build` 後に `engine pipe read error` の WARN が出なくなる（Bug-A/B 修正済み）
- [x] `engine pipe: non-UTF-8 line skipped` が DEBUG で出る（実行ログで確認済み）
- [ ] 市場時間外に Ladder に TOYOTA の気配（bids=10, asks=10）が表示される
- [ ] バナー「東証は現在市場時間外です」が表示される
- [x] `cargo test -p flowsurface-engine-client` が全 PASS
- [x] `cargo test --workspace` が全 PASS
- [ ] Binance/Bybit の動作に変化なし

---

## 関連ファイル

| ファイル | 役割 |
|---------|------|
| `engine-client/src/process.rs:479-505` | `forward_lines` — non-UTF-8 スキップ修正済み |
| `engine-client/src/process.rs:530-553` | 回帰テスト（既存） |
| `python/engine/exchanges/tachibana.py:784-826` | `stream_depth` 市場時間外パス |
| `src/screen/dashboard.rs:1129-1173` | `ingest_depth` — Ladder データ挿入 |
| `docs/plan/✅tachibana/fix-ladder-off-hours-2026-04-28.md` | 前フェーズ（VenueReady bump実装済み） |
