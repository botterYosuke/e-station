# test_replay_session_attach_manual_smoke — 人手確認手順書

Phase 8.1b: `cargo run -- --mode replay` + `uv run python -m engine.replay_session run ...` による
GUI pane 生成・bar 蓄積を人手で確認する手順。CI では自動実行できない GUI 視覚確認の観測点を固定する。

> ユーザー向けの解説（戦略の書き方・パラメータの意味・経路 A/B の比較）は
> [docs/wiki/backtest.md](../../docs/wiki/backtest.md) にある。
> このファイルは attach mode 動作の **観測点チェックリスト** に特化している。

---

## 前提条件

- `S:\j-quants` に 1301.TSE の 2025-01-06〜2025-03-31 の Daily データが存在すること
- `.env` に `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` が不要（replay のみ）
- Rust release ビルド済み: `cargo build --release`（または `cargo run --release` で自動ビルド）

---

## 手順

### Step 1: GUI を replay モードで起動する

```powershell
# 別ターミナルで起動（ブロックする）
cargo run -- --mode replay
```

**期待状態**: GUI ウィンドウが表示され、空のペイングリッドが表示される。
`saved-state.json` は **load しない**（replay モードはデフォルト空ペイン）。

---

### Step 2: engine-session.json が生成されることを確認

GUI 起動後、Rust engine-client がハンドシェイクを完了すると session ファイルが書き込まれる。

```powershell
# Windows: %APPDATA%\flowsurface\engine-session.json
Get-Content "$env:APPDATA\flowsurface\engine-session.json"
```

**期待出力例**:
```json
{
  "port": 19876,
  "pid": 12345,
  "schema_major": 3,
  "started_at": "2026-05-03T10:00:00Z"
}
```

> `token` フィールドは出力されていても値は確認しない（セキュリティ上ログ禁止）。

---

### Step 3: helper を attach mode で実行する（別ターミナル）

```powershell
uv run python -m engine.replay_session run `
    --strategy docs/example/buy_and_hold.py `
    --instrument 1301.TSE `
    --start 2025-01-06 `
    --end 2025-03-31 `
    --mode auto
```

**期待ログ（helper 側）**:
```
INFO engine.replay_session: ReplaySession: attach mode (endpoint=ws://127.0.0.1:19876/)
INFO engine.replay_session: [_AttachClient] handshake ok (endpoint=ws://127.0.0.1:19876/)
```

helper は `engine-session.json` から token / port を解決し、attach mode で起動する。

---

## 観測点

### 観測点 A: GUI に TimeAndSales / CandlestickChart / OrderList / BuyingPower ペインが生成される

`ReplayDataLoaded` event を受信すると `auto_generate_replay_panes` が発火し、
4 種類のペインが GUI に自動生成される。

- [ ] CandlestickChart ペインが表示される
- [ ] TimeAndSales ペインが表示される
- [ ] OrderList ペインが表示される
- [ ] BuyingPower ペインが表示される

---

### 観測点 B: bar が時系列で積まれる

replay が開始されると `KlineUpdate` event が流れ、CandlestickChart に bar が増えていく。

- [ ] CandlestickChart に bar が 1 本以上表示される
- [ ] bar が時間経過とともに増加する（replay 速度倍率 1x の場合は実時間より早い）

---

### 観測点 C: helper の stdout に event が流れる

```
{"event": "ReplayDataLoaded", ...}
{"event": "KlineUpdate", ...}
{"event": "ReplayBuyingPower", "cash": ..., "equity": ...}
{"event": "EngineStopped"}
```

- [ ] `ReplayDataLoaded` が最初に来る
- [ ] 複数の `KlineUpdate` が来る
- [ ] 最後に `EngineStopped` が来て helper が正常終了する

---

### 観測点 D: EngineBusy 通知（二重操作を試みる場合）

replay 実行中に別ターミナルから再度 helper を起動して `LoadReplayData` を送ると、
engine の state guard が弾いて `EngineBusy` event が返される。

```powershell
# 実行中に別ターミナルで（意図的に拒否されることを確認）
uv run python -m engine.replay_session run `
    --strategy docs/example/buy_and_hold.py `
    --instrument 1301.TSE `
    --start 2025-01-06 `
    --end 2025-03-31
```

**期待動作**: `BusyError: EngineBusy: state='LOADED' cmd='LoadReplayData'` が stderr に出て終了コード 2 で終了する。

- [ ] `BusyError` が stderr に表示される
- [ ] 終了コードが 2 である（`echo $LASTEXITCODE` または `echo $?`）

---

### 観測点 E: GUI 終了後 engine-session.json が削除される

GUI ウィンドウを閉じると:

```powershell
Test-Path "$env:APPDATA\flowsurface\engine-session.json"
```

**期待出力**: `False`（ファイルが削除されている）

> NOTE: Drop impl の設計変更（H2）により、`PythonProcess::Drop` ではなく
> 次回起動時の `reap_stale()` が orphan を掃除するため、
> GUI クラッシュ時はファイルが残ることがある。正常終了時は削除される。

- [ ] 正常終了後にファイルが削除される

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `attach mode` ではなく `inprocess mode` で起動 | `engine-session.json` が見つからない / stale pid | GUI が起動・handshake 完了済みかを確認。`Get-Content` でファイル内容を確認 |
| `ConnectionRefusedError` | token 不一致 / port 不一致 | `engine-session.json` の port が 19876 か確認。GUI のトークンと一致しているか |
| helper が即終了する | J-Quants データが無い（FileNotFoundError） | `S:\j-quants\1301.TSE\2025\` にデータファイルがあるか確認 |
| GUI にペインが生成されない | `ReplayDataLoaded` が届いていない | helper ログに `ReplayDataLoaded` が出ているか確認 |
