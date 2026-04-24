# Baseline Measurements — Phase 0

Phase 0 の完了条件として記録する現行 Rust 直結構成のベースライン。
以降のフェーズでは各項目が **許容範囲内**（[spec.md §9](../spec.md#9-非機能要件合格ライン) 参照）であることを確認する。

## 環境

| 項目 | 値 |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| CPU | （計測時に記入） |
| RAM | （計測時に記入） |
| Rust toolchain | （`rustc --version` で確認） |
| 計測日 | 2026-04-24 |
| ブランチ | `phase-0/python-skeleton-and-ipc-schemas` |

## 1. アプリ起動時間

> 測定方法: `cargo build --release` 後、バイナリ起動から最初のウィンドウ描画完了までの時間。
> `RUST_LOG=info` でログから `"a stream connected to"` の初出タイムスタンプを利用。

| 試行 | 起動〜初回 WS 接続 (ms) |
|---|---|
| 1 | 未計測 |
| 2 | 未計測 |
| 3 | 未計測 |
| 中央値 | **未計測** |

**計測方法メモ**: `$env:RUST_LOG="info"; Measure-Command { .\target\release\flowsurface.exe }` 等で計測予定。

## 2. Depth 更新レイテンシ（Rust 直結）

> 測定方法: Binance BTCUSDT の WebSocket depth メッセージ受信〜 `ingest_depth` 完了のスパン。
> `log::trace!` で入口・出口を計測し、100 サンプルの中央値 / p99 を記録。

| 指標 | 値 (µs) |
|---|---|
| 中央値 | 未計測 |
| p99 | 未計測 |

**目標**: IPC 追加後も中央値 < 2 ms、p99 < 10 ms（[spec.md §9](../spec.md#9-非機能要件合格ライン)）。

## 3. Trade バッチ処理レイテンシ（Rust 直結）

> 33 ms バッチ受信〜 `ingest_trades` 完了のスパン。

| 指標 | 値 (µs) |
|---|---|
| 中央値 | 未計測 |
| p99 | 未計測 |

## 4. CPU・メモリ使用量（アイドル時）

> 計測ツール: Windows タスクマネージャー / `Get-Process flowsurface`

| 指標 | 値 |
|---|---|
| CPU（アイドル） | 未計測 |
| RSS（アイドル） | 未計測 |

**目標**: Python エンジン追加後 CPU +30% 以内（[spec.md §9](../spec.md#9-非機能要件合格ライン)）。

## 5. `cargo test --workspace` 実行時間

| 試行 | 時間 (s) |
|---|---|
| 1 | 約 59 s（ビルド込み） |

## 計測 TODO

- [ ] リリースビルドで起動時間を 3 回計測し中央値を記入
- [ ] depth / trade レイテンシ計測用のログ instrumentation を追加
- [ ] Linux 環境での計測（CI runner またはリモート環境）
- [ ] フェーズ 1 完了後に同一手順で再計測し差分を記録
