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
| `docs/plan/✅order/task-buying-power-ipc.md` | IPC 配線の完成計画書（背景） |
