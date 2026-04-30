# replay `/load` → `/start` race の修正計画

作成日: 2026-04-30
状態: 暫定対応のみ実施・本修正は未着手
関連: [replay-launch-empty-pane-issue.md](./replay-launch-empty-pane-issue.md)（本 race を 第五原因として同ファイルに追記予定）

## 問題の要約

`scripts/replay_dev_load.sh` が `POST /api/replay/load` 直後に `POST /api/replay/start` を投げると、CodeLLDB アタッチで GUI 初期化が遅延する F5 起動時に **bars streaming が pane 生成より先に始まり、bars が空の pane に流れて捨てられる**。

GUI 上の症状：

- chart pane: 「Waiting for data...」のまま
- BuyingPower pane: `仮想余力: ---` `評価額: ---`
- 注文一覧 pane: 「注文なし」（戦略は動いているのに）

CLI 起動（`bash scripts/run-replay-debug.sh ...`）はデバッガが無く GUI 初期化が
bars streaming に間に合うので顕在化しない。

## なぜ起きるか — `/api/replay/load` の実装上の race

[src/replay_api.rs](../../src/replay_api.rs) の `replay_load` 関数（`ReplayLoadOutcome::Ok` 分岐）：

```text
1. events_rx = conn.subscribe_events()
2. conn.send(Command::LoadReplayData)
3. await ReplayDataLoaded                  ← ここまでは同期 (blocking)
4. control_tx.try_send(AutoGenerateReplayPanes)   ← fire-and-forget
5. write_response(stream, 200, "OK", body)        ← HTTP 200 を返す
```

問題は **4 → 5 の間に dashboard 側の AutoGenerateReplayPanes 処理を待っていない**こと。`try_send` は mpsc キューに enqueue するだけで、`Flowsurface::update()` が `ControlApiCommand::AutoGenerateReplayPanes` を引き取って pane を実際に生成するまでには Iced のイベントループ 1 周分以上のラグがある。

その後スクリプトは即座に `/api/replay/start` を投げ、`StartEngine` IPC が Python に届く。Python は `streaming replay` を開始し、200ms 間隔で `KlineUpdate` / `ReplayBuyingPower` / `DateChangeMarker` を発行する。これらは Rust の WebSocket クライアントが受信して Iced の message bus に push する。

debug + lldb 起動の Iced は GUI/レンダラ初期化に時間がかかるため、message bus に積まれた数十件の KlineUpdate を処理する前に AutoGenerateReplayPanes を処理する保証がない。今回観測した実機ログでは：

```
22:05:43 ReplayDataLoaded 受信
22:05:43-54 KlineUpdate 57本 streaming
22:05:55 EngineStopped
22:05:58 (renderer 再初期化と思われる Settings ブロック)
22:06:00 AutoGenerateReplayPanes 処理 ← ReplayDataLoaded から 17 秒遅れ
```

pane が出来た時点では engine は既に停止しており、bars は受信先が無いまま破棄された。

## 暫定対応（実施済）

[scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh) の `/load` 後に sleep を入れた：

```bash
PANE_WARMUP_S="${REPLAY_PANE_WARMUP_S:-3}"
log "sleeping ${PANE_WARMUP_S}s for pane generation to settle ..."
sleep "$PANE_WARMUP_S"
```

`REPLAY_PANE_WARMUP_S` で延長可能。debug+lldb の重い環境では 5-8 秒に上げる必要が
出る可能性がある。**これは race を隠す band-aid であり、根本対策ではない。**

問題点：

- 環境依存（マシン性能・debug/release・lldb の有無で必要秒数が変わる）
- 短すぎれば再発、長すぎれば UX 悪化
- ユーザがいつまで sleep すべきかを推測する必要がある

## API 契約変更（本修正の前提）

本修正は `/api/replay/load` の成功条件を以下のように**明示的に変更**する：

> **変更前**: 200 = engine 側で `LoadReplayData` が成功し `ReplayDataLoaded` を受信した
> **変更後**: 200 = 上記に加え、Iced 側で `AutoGenerateReplayPanes` 処理（pane 生成）が完了し、`KlineUpdate` を受信できる状態である

これによりクライアント（dev script・E2E テスト・将来の UI フロント）は「200 が返れば即 `/api/replay/start` を投げてよい」と仮定してよい。Pane 生成の遅延は HTTP レスポンス遅延として現れる。

**選択しなかった代案**: `/load` は従来通り data load 成功で 200 を返し、pane ready は warning ログ＋メトリクスのみ。この場合は dev 側の sleep / polling が必要で race 解消にならないため不採用。

エラーセマンティクス：

| 条件 | ステータス | 意味 |
|------|----------|------|
| `ReplayDataLoaded` 受信 + pane ready | 200 | データ load 成功 + UI 準備完了 |
| `LoadReplayData` 自体が失敗 | 502/503/504 | engine 側エラー（既存挙動） |
| `ReplayDataLoaded` は受信したが pane ready が timeout | **504** Gateway Timeout（body に `error: "pane_ready_timeout"`） | engine 側 load は成功しているが UI 同期だけ失敗。クライアントは**リトライしてはいけない**（重複 load を避ける）。`retryable: false` で識別可能 |

`loaded_instruments` の更新タイミングは現状維持（`ReplayDataLoaded` 受信直後）。504 を返した場合でも次回 `/load` は idempotent に成功する（同じ instrument の再 load は engine 側で許容される）。

**504 後の遅延 ack 整合性**: 504 を返した後で `AutoGenerateReplayPanes` が遅延処理されると、pane は事後的に生成される。この時点で続けて `/api/replay/start` が来ても問題ないよう、`/start` ハンドラは pane 存在を前提条件にしない（既存設計）。逆に、504 後に**再度** `/load` が来た場合は同 instrument が `loaded_instruments` に既にあり idempotent に成功するが、AutoGenerateReplayPanes が二重発行される。Iced 側 `auto_generate_replay_panes` の `replay_pane_registry.is_loaded(...)` チェックで二重 pane 生成は抑止される（[src/screen/dashboard.rs](../../src/screen/dashboard.rs) の `Dashboard::auto_generate_replay_panes` 冒頭参照）。

## 波及更新が必要な文書

本修正は HTTP API のセマンティクスを変える破壊的変更なので、以下の文書も同 PR で更新する：

- [docs/✅nautilus_trader/replay-launch-empty-pane-issue.md](./replay-launch-empty-pane-issue.md) — 第五原因を「暫定」から「本修正完了」に変更。原因番号順序を再整列
- [.claude/CLAUDE.md](../../.claude/CLAUDE.md) — 「replay モードの使い方 > IPC イベントの流れ」の `POST /api/replay/load` 行に「200 = pane ready まで含む」を追記。「よくある落とし穴」にも sleep 不要化を反映

## 設計

`ControlApiCommand::AutoGenerateReplayPanes`（[src/replay_api.rs](../../src/replay_api.rs) の `ControlApiCommand` enum 定義）に **`Arc<tokio::sync::Notify>`** を追加する。

> **なぜ `oneshot::Sender<()>` ではないか**: `ControlApiCommand` は `#[derive(Debug, Clone)]` であり、Iced の `Message` 経路でも `Clone` が要求される。`oneshot::Sender<()>` は `Clone` 非実装なのでこの enum に載せられない。`Arc<Notify>` は `Clone` 可能で one-shot ack のセマンティクスを満たす。

### 実コード調査結果（実装前提）

計画策定時に以下を実コードで確認済み（`grep` ベース）：

- **emitter callsite**: `ControlApiCommand::AutoGenerateReplayPanes` を生成する箇所は `/load` ハンドラ（[src/replay_api.rs](../../src/replay_api.rs) `replay_load` 関数内、`ReplayLoadOutcome::Ok` 分岐）の **1 箇所のみ**。他の grep ヒットはテスト内 match pattern。よって `ack: Option<Arc<Notify>>` の `None` 経路は**将来用の互換余地**であり、現状は常に `Some(_)` で発行される
- **subscription bind の同期性**: [src/screen/dashboard.rs](../../src/screen/dashboard.rs) `Dashboard::auto_generate_replay_panes` 内で `state.set_content_and_streams(vec![ti], ContentKind::*)` を**同期で**呼び出して pane に stream を bind する。戻り `Task` の中身は chart 内 `fetch_klines` 等の追加 fetch のみで、subscription 確立とは無関係。よって ack を関数同期戻り直後に送ることで「`KlineUpdate` を受信できる状態」は保証される
- **control_tx bound 容量**: production は `mpsc::channel::<ControlApiCommand>(64)`、テストは `(8)`。Tachibana login 等の他経路と共用するが 64 は十分余裕がある

```rust
// src/replay_api.rs
use tokio::sync::Notify;

#[derive(Debug, Clone)]
pub enum ControlApiCommand {
    // ...
    AutoGenerateReplayPanes {
        instrument_id: String,
        strategy_id: Option<String>,
        granularity: ReplayGranularity,
        /// pane 生成完了を /load ハンドラに通知する。
        /// `None` のときは ack 不要（既存の他 callsite 互換）。
        ack: Option<Arc<Notify>>,
    },
}
```

### `/api/replay/load` 側

```rust
// ④' AutoGenerateReplayPanes を ack 付きで送る
let ack = Arc::new(Notify::new());
let notified = ack.clone();
// 重要: notified() future を notify_one() より「先に」作る必要がある。
// Notify は permit を 1 つ保持できるので順序は実装次第で正しく扱われるが、
// ここでは以下の順番で安全に書く:
//   1. Arc::new で ack を作成
//   2. notified.notified() を Future としてピン留め
//   3. tx.send().await で AutoGenerateReplayPanes を送る
//   4. timeout 付きで notified を await
let wait = notified.notified();
tokio::pin!(wait);

{
    let tx_guard = state.control_tx.lock().await;
    if let Some(tx) = tx_guard.as_ref() {
        let cmd = ControlApiCommand::AutoGenerateReplayPanes {
            instrument_id: parsed.instrument_id.clone(),
            strategy_id: strategy_id_for_cmd.clone(),
            granularity: granularity_for_cmd.clone(),
            ack: Some(ack.clone()),
        };
        // try_send → send().await。bound 容量は production 64（実コード確認済み）
        // で Tachibana login 等他経路と共用するが十分余裕がある。
        if let Err(e) = tx.send(cmd).await {
            log::error!("replay_api: AutoGenerateReplayPanes channel closed: {e}");
            // dashboard 側の subscription が死んでいる → 503 で抜ける
            write_error(stream, 503, "Service Unavailable",
                "ui control channel unavailable").await;
            return;
        }
    } else {
        // テストモードなど control_tx 未設定 → ack 待ちはスキップ（既存挙動互換）
        write_response(stream, 200, "OK", &body).await;
        return;
    }
}

// ⑤' ack または timeout を待つ
// デフォルト: release 10s / debug 30s（debug+lldb で観測 17s のケースに対応）。
// env var `REPLAY_PANE_READY_TIMEOUT_S` で上書き可能（dev 限定機能）。
let default_timeout_s: u64 = if cfg!(debug_assertions) { 30 } else { 10 };
let pane_ready_timeout = Duration::from_secs(
    std::env::var("REPLAY_PANE_READY_TIMEOUT_S")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(default_timeout_s),
);
match tokio::time::timeout(pane_ready_timeout, wait).await {
    Ok(()) => {
        // pane 生成完了 → 200 OK
        write_response(stream, 200, "OK", &body).await;
    }
    Err(_) => {
        // UI sync timeout. engine 側 load は成功しているがクライアントには
        // 504 + 識別可能な error code を返してリトライさせない。
        log::warn!(
            "replay_api: AutoGenerateReplayPanes ack timed out after {:?} \
             (engine load succeeded; UI did not finish pane generation)",
            pane_ready_timeout
        );
        let body = serde_json::json!({
            "error": "pane_ready_timeout",
            "message": "engine load succeeded but UI did not finish pane generation in time",
            "retryable": false
        }).to_string();
        write_response(stream, 504, "Gateway Timeout", &body).await;
    }
}
```

### Iced 側（main.rs のみ）

[src/main.rs](../../src/main.rs) の `Flowsurface::update` 内 `AutoGenerateReplayPanes` 分岐で、`Dashboard::auto_generate_replay_panes(...)` 呼出**直後**に `ack.notify_one()` を呼ぶ。`dashboard.rs` の改修は不要（実コード調査で確認済み: pane への stream bind は `set_content_and_streams(...)` を関数内で同期で呼んでおり、戻り `Task` には fetch 等の補助処理しか含まれない）。

```rust
ControlApiCommand::AutoGenerateReplayPanes {
    instrument_id,
    strategy_id,
    granularity,
    ack,
} => {
    // ... 既存の timeframe 変換と auto_generate_replay_panes 呼出 ...
    let task = dashboard
        .auto_generate_replay_panes(main_window_id, &instrument_id, timeframe)
        .map(/* ... */);

    // 戻り Task の中身は pane 内 fetch 等の追加処理。pane 自体の生成と
    // dashboard グリッドへの登録は同期戻り時点で完了しているので、
    // ここで ack して /load を解放する。
    if let Some(ack) = ack {
        ack.notify_one();
    }
    return task;
}
```

> **subscription bind の同期性は確認済み**: `Dashboard::auto_generate_replay_panes` が `set_content_and_streams(vec![ti], ContentKind::*)` を関数内で同期で呼ぶことを実コードで確認した。戻り `Task` には pane 内 chart の `fetch_klines` 等の補助処理のみが含まれ、`KlineUpdate` の subscription 確立とは無関係。よって ack を関数同期戻り直後に送って差し支えない。

### スクリプト側

`replay_dev_load.sh` の `sleep` を削除し、`/load` の 200 応答を信頼する：

```bash
# /load が pane 生成完了まで block するので sleep 不要
log "POST /api/replay/start"
```

### `/api/replay/start` 側の独立対応

`/start` は現在 `/load` と独立に動作する。本修正後は「`/load` が 200 で返る = pane 準備済み」が保証されるため `/start` 側は変更不要。

## 修正範囲

| ファイル | 変更内容 |
|---------|---------|
| [src/replay_api.rs](../../src/replay_api.rs) | `ControlApiCommand::AutoGenerateReplayPanes` に `ack: Option<Arc<Notify>>` を追加。`replay_load` の `ReplayLoadOutcome::Ok` 分岐で `Notify::notified()` を await（timeout 付き）。`try_send` → `send().await` に変更。timeout 時は 504 + `error: "pane_ready_timeout"` |
| [src/main.rs](../../src/main.rs) | `Flowsurface::update` の `AutoGenerateReplayPanes` arm で `Dashboard::auto_generate_replay_panes` 呼出直後に `ack.notify_one()`（dashboard.rs は触らない） |
| [scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh) | `PANE_WARMUP_S` の sleep を削除。`REPLAY_PANE_WARMUP_S` 環境変数も削除 |
| [docs/✅nautilus_trader/replay-launch-empty-pane-issue.md](./replay-launch-empty-pane-issue.md) | **第五原因セクションを新規追記**（現在の同ファイルには 第二〜第四原因しか存在しない）。本 race の症状・暫定対応・本修正完了を 第二〜第四原因と同じ書式で記載 |
| [.claude/CLAUDE.md](../../.claude/CLAUDE.md) | 「replay モードの使い方 > IPC イベントの流れ」の `POST /api/replay/load` 行に「200 = pane ready まで含む」を追記。「よくある落とし穴」の sleep 言及を削除 |

## テスト計画

### Rust 単体テスト追加

`src/replay_api.rs` の `tests` モジュール（既存 `replay_load_*` テスト群と同階層、inline `#[cfg(test)] mod tests`）に追加。実行コマンド: `cargo test -p flowsurface --lib replay_load_`。

1. **`replay_load_blocks_until_pane_ack`**
   `/api/replay/load` を投げ、200 が返る**前に** mock dashboard が `Notify::notify_one()` を呼んでいることを確認する。`control_rx` を receiver 側でドレインし、AutoGenerateReplayPanes を受け取ったら `ack.notify_one()` する mock dashboard を立てて検証。pin する不変条件: 「ack 前に 200 は返らない」。

2. **`replay_load_returns_504_when_pane_ack_times_out`**
   `notify_one()` を呼ばない mock dashboard を立てて、テスト用に短縮した `pane_ready_timeout`（例: 500ms）で 504 + body `{"error":"pane_ready_timeout","retryable":false}` が返ることを確認。timeout 値は env var `REPLAY_PANE_READY_TIMEOUT_S` で短縮するか、`ReplayApiState::with_pane_ready_timeout()` を新設して注入する。

3. **`replay_load_returns_503_when_control_channel_closed`**
   `control_tx` を `set_control_tx` した直後 receiver を drop し、`/load` を投げて `send().await` が `SendError` を返すケースで 503 + `ui control channel unavailable` が返ることを確認。

4. **`replay_load_504_does_not_block_subsequent_load`** (D7-1)
   504 を返した直後に同じ instrument で再 `/load` を投げ、idempotent に 200 が返ることを確認。`loaded_instruments` の重複登録が抑止されていることも assert。

5. **`auto_generate_replay_panes_skips_pane_when_ack_already_loaded`** (D7-2)
   504 後に遅延した `AutoGenerateReplayPanes` が処理されたあと、続く `/load` で同 instrument の AutoGenerateReplayPanes が再発行されても、`Dashboard::auto_generate_replay_panes` 冒頭の `replay_pane_registry.is_loaded()` ガードで二重 pane 生成が抑止されることを確認（`tests/auto_generate_replay_panes_auto_bind.rs` 拡張）。

### E2E スモークテストへの追加

`tests/e2e/smoke.sh` 相当の **replay E2E スクリプト** を新設するかは別チケット。本修正の収束確認では、最低限以下の手動検証チェックリストを `replay_dev_load.sh` 実行ログに残す：

- F5 (CodeLLDB アタッチ debug 起動) で `bash scripts/run-replay-debug.sh docs/example/buy_and_hold.py 1301.TSE 2025-01-06 2025-03-31` を実行
- chart pane に bars が描画される
- BuyingPower pane に値が入る（`仮想余力`/`評価額` が `---` のままにならない）
- 注文一覧に buy_and_hold の最初のバーでの 1 回買いが反映される
- ログに `engine ws read error` / `DepthGap` / `parse error` が出ない

### 回帰テスト

- `cargo test --workspace` の既存テストが全てパス（特に `tests/auto_generate_replay_panes_auto_bind.rs`）
- `bash tests/e2e/smoke.sh` で live モードに影響しないこと（replay モード固有の修正だが念のため）
- F5 起動の手動確認はテスト 1〜5 の自動化で大部分カバーされる前提

## 並行する別問題（本計画の対象外）

以下は本 race とは独立した既知の問題なので、混ぜずに別チケットで扱う：

- **chart の「初期履歴が描画されない」問題**: replay の `Subscribe` は Python が
  `unknown venue 'replay'` で拒否する仕様（CLAUDE.md 「よくある落とし穴」参照）。
  streaming で push される `KlineUpdate` のみで描画する設計。本 race を直しても
  「`FetchKlines` が失敗する」「初期 bar が一気に表示される」挙動は変わらない。

- **Okex `Fetch error: not_found: could not convert string to float: ''`**:
  live モード由来の取引所メタ取得エラー。replay とは無関係。Python ログにも
  以前から残っている。

## 実装の落とし穴

- **`ControlApiCommand` の Clone 制約**: enum は `#[derive(Debug, Clone)]` であり、Iced の `Message` 経路でも Clone が要求される。`oneshot::Sender<()>` は Clone 非実装なのでこの enum に載せると即コンパイルエラー。`Arc<Notify>` で回避する。
- **`Notify` の `Debug` 出力**: `ControlApiCommand` は `Debug` derive されており、`log::debug!("control-api command received: {cmd:?}")` 等で `Arc<Notify>` のポインタアドレスがログに出る。情報漏洩ではないが、ログ grep の冗長化要因として認識しておく。気になる場合は `Debug` を手書きして `ack` フィールドを `<Notify>` などに置換する。
- **`ack=None` 経路は将来用**: 現状の emitter callsite は `replay_load` の 1 箇所のみで常に `Some(_)` を発行する（実コード調査済み）。`Option` で包むのは将来別経路から発行されたときの互換余地。pin test `replay_load_blocks_until_pane_ack` の対称版で `None` 経路の挙動も assert する。
- **`auto_generate_replay_panes` の subscription bind は同期完了**: 実コード調査済み（`set_content_and_streams(...)` を関数内で同期呼出）。同期戻り直後の ack で race は残らない。実装時に追加で `is_lone_starter` 経路（`self.panes = grid_state` の差し替え）が pane 登録を破壊しないかも確認すること。
- **`control_tx` の bound 容量**: production は `mpsc::channel::<ControlApiCommand>(64)`、テストは `(8)`。Tachibana login 等と共用するが 64 で十分。`try_send` → `send().await` 変更後も backpressure で詰まる可能性は低い。
- **mpsc 受信側 drop**: `send().await` が `SendError` を返す（dashboard subscription 死亡）場合は 503 で抜ける。`try_send` 時代の「ログだけ出して 200」より厳しい挙動だが、本修正では「200 を返す = pane ready」契約を守るため必要。
- **`pane_ready_timeout` のデフォルト**: release 10s / debug 30s（debug+lldb で 17s 観測のため）。env var `REPLAY_PANE_READY_TIMEOUT_S` で上書き可能。
- **`ControlApiCommand` のフィールド追加は破壊的変更**: pattern matching の網羅性が崩れる箇所を `cargo check --workspace` で全て洗い出す（既存 callsite は match arm 含めて 4 箇所）。
- **504 後の再 load idempotency**: 504 を返した時点で engine は既に `LoadReplayData` を成功し `loaded_instruments` も更新済み。再 `/load` は同 instrument の `loaded_instruments` 重複登録を抑止しつつ idempotent に成功する。`AutoGenerateReplayPanes` 二重発行は `Dashboard::auto_generate_replay_panes` 冒頭の `replay_pane_registry.is_loaded()` ガードで二重 pane 生成が抑止される（テスト 5 で pin）。

## 関連ファイル

- [src/replay_api.rs](../../src/replay_api.rs) — `/api/replay/load` 実装と `ControlApiCommand` 定義
- [scripts/replay_dev_load.sh](../../scripts/replay_dev_load.sh) — race の暫定対応 sleep
- [scripts/run-replay-debug.sh](../../scripts/run-replay-debug.sh) — CLI 起動（race を顕在化させない経路）
- [docs/✅nautilus_trader/replay-launch-empty-pane-issue.md](./replay-launch-empty-pane-issue.md) — 全 5 原因の経緯まとめ
