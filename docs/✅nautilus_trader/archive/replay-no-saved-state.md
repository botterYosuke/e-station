# replay モードで saved-state.json を参照・保存しない

## 背景・動機

`saved-state.json` はウィンドウサイズ・テーマ・ペイン配置などの UI 状態を
セッション間で引き継ぐためのファイル（`%APPDATA%\flowsurface\saved-state.json`）。

現状、replay モードは **ロード時にペインレイアウトをすでに破棄している**（D8、
`src/main.rs` の `is_replay_mode` ガード）が、**終了時の保存はモードを問わず実行される**。

これにより：

1. replay セッション中にウィンドウをリサイズ・テーマを変更して終了すると、
   その変更が `saved-state.json` に書き込まれ live モードの設定を上書きする
2. `saved-state.json` が存在しない（= 初回起動・手動削除）状態で replay を起動すると、
   ペイングリッドが空のまま `auto_generate_replay_panes` が呼ばれ、
   `base_pane = None` でサイレントリターンして画面が変わらないバグが発生する
   （問題 2 は別途 `src/screen/dashboard.rs` で修正済み）

## ゴール

- **保存しない**: replay モード終了時に `save_state_to_disk()` を no-op にする
- **ロードはそのまま**: ウィンドウサイズ・テーマ・タイムゾーン等の非レイアウト設定は
  live モードの値を引き継いで使う（replay 起動ごとにデフォルトに戻るのは不便なため）
- live モードの設定を replay 操作で汚染しないことを保証する

---

## 変更対象と内容

### 1. `src/main.rs` — `save_state_to_disk()` に replay ガード追加

`Message::ExitRequested` と `Message::RestartRequested` の両方から呼ばれている
`save_state_to_disk()` の先頭に replay モードチェックを追加して即 return する。

```rust
fn save_state_to_disk(&mut self, windows: &HashMap<window::Id, WindowSpec>) {
    // replay モードでは live 設定を上書きしない
    if APP_MODE
        .get()
        .map(|m| *m == engine_client::dto::AppMode::Replay)
        .unwrap_or(false)
    {
        log::info!("replay mode: skipping save_state_to_disk");
        return;
    }

    // ... 既存コード
}
```

---

## 影響範囲まとめ

| ファイル | 変更種別 |
|---|---|
| `src/main.rs` | `save_state_to_disk()` 先頭に replay ガード追加（4 行） |

変更は 1 ファイル・4 行のみ。ロード側は変更なし。

---

## テスト方針

自動テストの追加は不要。理由：

- `save_state_to_disk()` は Iced の `update()` から呼ばれる副作用（ファイル書き込み）であり、
  現状ユニットテストの対象になっていない
- replay モードのガードは `APP_MODE` OnceLock に依存しており、
  単体テストで安全に切り替えることができない

手動確認手順：

1. `saved-state.json` を削除する
2. `--mode replay` で起動し、ウィンドウをリサイズして終了する
3. `saved-state.json` が生成されていないことを確認する
4. `--mode live` で起動し、デフォルトのウィンドウサイズで起動することを確認する

---

## 設計上の判断

**ロード側も skip する案（没）**: replay 起動ごとにデフォルトのウィンドウサイズ・
テーマに戻るのはユーザー体験として不便。live モードで設定したテーマや
ウィンドウサイズをそのまま replay でも使えることが自然なため、ロードは継続する。

**別ファイル（`replay-state.json`）を用意する案（没）**: 分離は完全だが、
replay は実験的・使い捨てのセッションであり専用の永続ストアを持つ価値がない。
オーバーエンジニアリングになるため不採用。
