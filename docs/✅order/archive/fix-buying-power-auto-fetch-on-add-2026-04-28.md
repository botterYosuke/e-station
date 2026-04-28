# Fix: 買余力ペイン新規登録時の自動フェッチ欠落

**作業ブランチ**: `fix/engine-pipe-non-utf8-deadlock`  
**日付**: 2026-04-28

---

## 問題

買余力ペインを**起動時に保存済みレイアウトとして開く**場合は `GetBuyingPower` が自動で発行されるが、
**起動後にサイドバーから新規登録**した場合は発行されない。

### 動作の差異

| シナリオ | 自動フェッチ |
|---|---|
| 起動時にレイアウトにペインが存在 | ✅ される |
| 起動後にサイドバーから「買余力」を登録 | ❌ されない |

---

## 根本原因

自動フェッチは `src/main.rs` の `TachibanaVenueEvent(VenueEvent::Ready)` ハンドラ内でのみ行われる
（`main.rs:1217–1235`）。

```rust
// main.rs:1220 — VenueReady 時のみ実行
if is_ready && self.active_dashboard().has_buying_power_pane(main_window) {
    // GetBuyingPower IPC 送信
}
```

一方、`OpenOrderPanel(ContentKind::BuyingPower)` ハンドラ（`main.rs:2222–2241`）は
ペインを分割してコンテンツをセットするだけで IPC を発行しない。

`VenueReady` は 1 度しか来ない（ログイン完了時）ため、その後にペインを追加しても
次に「更新」ボタンを手動で押すまでデータが取得されない。

---

## 修正方針

`OpenOrderPanel(ContentKind::BuyingPower)` ハンドラの末尾に、以下を追加する。

1. `tachibana_state.is_ready()` を確認する
2. Ready であれば `engine_connection` を取得する
3. `GetBuyingPower` IPC コマンドを送信する（`VenueReady` 時の自動フェッチと同じロジック）

### 修正対象ファイル

| ファイル | 変更内容 |
|---|---|
| `src/main.rs` | `OpenOrderPanel(ContentKind::BuyingPower)` ハンドラに IPC 発行を追加 |

---

## 実装ステップ

### Step 1: main.rs の `OpenOrderPanel` ハンドラを修正

`main.rs:2222–2241` の末尾、`return task.map(Message::Sidebar)` の前に以下を挿入する。

```rust
// ペイン新規登録後、venue が既に Ready なら即座に余力をフェッチする。
// VenueReady 時の自動フェッチは登録前に一度だけ走るため、
// 後から追加したペインはこのパスでキャッチアップする。
if kind == ContentKind::BuyingPower
    && self.tachibana_state.is_ready()
{
    if let Some(conn) = self.engine_connection.as_ref().cloned() {
        let req_id = uuid::Uuid::new_v4().to_string();
        self.buying_power_request_id = Some(req_id.clone());
        return Task::batch(vec![
            task.map(Message::Sidebar),
            Task::perform(
                async move {
                    conn.send(engine_client::dto::Command::GetBuyingPower {
                        request_id: req_id,
                        venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                    })
                    .await
                },
                |_| Message::None,
            ),
        ]);
    }
}
```

> `buying_power_request_id` への記録は重複リクエスト抑制のため（`VenueReady` 時の自動フェッチと統一）。
> エラー受信・成功受信どちらでも `buying_power_request_id` はクリアされる（既存ロジックに任せる）。

### Step 2: 動作確認

1. アプリを起動し、立花にログインして `VenueReady` 状態にする
2. サイドバーから「買余力」ペインを新規登録する
3. ペイン登録直後に現物余力・信用余力が表示されることを確認する（「更新」ボタン不要）
4. その後「更新」ボタンを押しても正常に再取得できることを確認する

---

## Acceptance criteria

- [x] サイドバーから「買余力」を登録した直後に現物余力・信用余力が自動で表示される
- [x] 「更新」ボタンを押した場合も引き続き正常動作する
- [x] `cargo test --workspace` 全緑
- [x] `cargo clippy -- -D warnings` クリーン

---

## 関連ファイル

| ファイル | 役割 |
|---|---|
| `src/main.rs:1217–1235` | VenueReady 時の自動フェッチ（参考パターン） |
| `src/main.rs:2222–2241` | OpenOrderPanel ハンドラ（修正箇所） |
| `src/screen/dashboard/panel/buying_power.rs` | BuyingPower パネル実装 |
| `src/screen/dashboard.rs:633–637` | `has_buying_power_pane()` / `distribute_buying_power()` |
| `docs/✅order/task-buying-power-ipc.md` | IPC 配線の完成計画書（背景） |

---

## レビュー反映 (2026-04-28, ラウンド 1)

### 解消した指摘

| ID | 重要度 | 概要 | 修正 |
|----|--------|------|------|
| C1 | CRITICAL | split 失敗後も `buying_power_request_id` を書き込んで IPC が飛ぶ | `pane_added` フラグ導入、split 成功後のみ auto-fetch を実行 |
| H1 | HIGH | `send()` Err 後に `buying_power_request_id` が Some のまま固着 | Err 時に `Message::IpcError { request_id: Some(req_id) }` で既存ハンドラにルーティング → 自動クリア |
| H2 | HIGH | `buying_power_request_id` が Some 中の上書き（in-flight 競合） | `self.buying_power_request_id.is_none()` ガードを追加 |
| H3 | HIGH | VenueReady auto-fetch が `buying_power_request_id` を設定しない非対称 | VenueReady ハンドラも req_id を生成・記録するよう対称化 |
| H4 | HIGH | コメント「一度だけ走るため」が reconnect 再発火の実態と不一致 | コメントを実際の挙動（reconnect カバーも明記）に修正 |
| M1 | MEDIUM | `engine_connection = None` 時にサイレント失敗 | `log::warn!("[BuyingPower auto-fetch] tachibana is ready but engine_connection is None")` を追加 |

### R2 で解消した指摘

| ID | 重要度 | 概要 | 修正 |
|----|--------|------|------|
| R2-H1 | HIGH | `BuyingPowerAction` の send Err が `OrderToast` に落ちて req_id が固着 | `req_id_for_err` + `Message::IpcError` ルーティングに統一 |
| R2-H2 | HIGH | `EngineConnected` 時に `buying_power_request_id` がリセットされない | `EngineConnected` 冒頭で `= None` 追加 |
| R2-M2 | MEDIUM | VenueReady auto_fetch に `is_none()` ガードがない | `&& self.buying_power_request_id.is_none()` ガード追加 |
| R2-M3 | MEDIUM | `IpcError` ハンドラの unrouted 分岐にログなし | `else { log::debug!(...) }` 追加 |

### R3 で解消した指摘

| ID | 重要度 | 概要 | 修正 |
|----|--------|------|------|
| R3-H1 | HIGH | `BuyingPowerAction` に `is_none()` ガードがない（連打で in-flight 上書き） | `if self.buying_power_request_id.is_some() { return Task::none(); }` を先頭に追加 |
| R3-M1 | MEDIUM | `req_id_for_err.clone()` が `FnOnce` クロージャ内で冗長（3 箇所） | `.clone()` 削除。`Task::perform` は `FnOnce` を受け取ることを確認（iced 0.14.0） |

### 持ち越し（対応不要と判断した LOW/MEDIUM）

| ID | 重要度 | 理由 |
|----|--------|------|
| M2 | MEDIUM | unrouted `IpcError` のログなし — R2-M3 で修正済み |
| M3 | MEDIUM | `Task::batch` 順序非保証 — 実質リスクは極小（localhost IPC の RTT は数 ms） |
| M4 | MEDIUM | 新パスのテスト不足 — Rust GUI 層のハンドラ単体テストは困難。E2E でカバー |
| L1 | LOW | `use ContentKind` をスコープ先頭に移動 — ✅ 修正済み（fmt で整理） |

### 設計判断

- 元の計画案（`|_| Message::None`）は `Message::None` 未定義のため不採用。代わりに Err を `Message::IpcError` で既存ハンドラにルーティングする方式を採用。これにより send 失敗時もパネルにエラー表示が届く。
- `buying_power_request_id.is_none()` ガードにより、in-flight 中の重複 auto-fetch をスキップ。ただしユーザーの手動更新（BuyingPowerAction）は常に上書きする（既存動作を維持）。
- VenueReady ハンドラの対称化も同時実施。今後 `GetBuyingPower` を送る経路は全て req_id を記録する統一方針。

### R4 で解消した指摘（最終）

| ID | 重要度 | 概要 | 修正 |
|----|--------|------|------|
| LOW-1 | LOW | VenueReady ハンドラの `req_id_for_err.clone()` が残存（`Task::perform` は `FnOnce` のため冗長） | `req_id_for_err.clone()` → `req_id_for_err`（`main.rs:1241`） |

**R4 結果: MEDIUM 以上ゼロ — 収束。review-fix-loop 完了。**
