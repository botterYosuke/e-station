# 実装計画

[`spec.md`](./spec.md) の構成へ段階的に移行するためのフェーズ分け。
各フェーズは単独でマージ可能・動作確認可能な粒度を目指す。

## フェーズ 0: 準備（リスク低）

- [ ] `python/` に `pyproject.toml` と `flowsurface_data` パッケージのスケルトンを置く。
- [ ] [`docs/plan/schemas/`](./schemas/) に JSON Schema を作成（`Trade`, `Kline`, `Depth`, `Ticker`, `TickerInfo`, `TickerStats`, `OpenInterest`）。
  - 既存 Rust 型 ([`exchange/src/lib.rs`](../../exchange/src/lib.rs) 周辺) を参照。
- [ ] Rust 側に `--data-engine-url ws://...` CLI フラグを追加（未指定時は従来動作）。
- [ ] 既存 Rust テストを通したまま CI を維持する。

**完了条件**: 既存挙動を変えずにマージできる。

## フェーズ 1: Python データエンジン MVP（Binance のみ）

- [ ] `flowsurface_data.server` に WS サーバを実装（`websockets` ライブラリ）。
- [ ] `exchanges/binance.py` で REST メタデータ + Kline + WebSocket trade/depth を実装。
- [ ] `limiter.py` で Binance のレート制限を移植（[`exchange/src/adapter/limiter.rs`](../../exchange/src/adapter/limiter.rs) を参考）。
- [ ] スキーマは pydantic、出力は orjson。
- [ ] 単独で `python -m flowsurface_data --port 8765` で起動でき、`wscat` 等で `subscribe` → trade イベントが流れることを確認。
- [ ] pytest で REST/WS の最低限のテスト（モック取引所 or VCR）。

**完了条件**: Python のみで Binance のリアルタイム trade を取得・配信できる。

## フェーズ 2: Rust 側に engine-client を実装し Binance を切替

- [ ] `engine-client` crate（または `exchange` を改修）に `EngineClient` を実装。
  - 内部で WebSocket 接続、`Event` ストリームを `BoxStream` で公開。
  - 既存 `AdapterHandles` と同じ `subscribe` / `unsubscribe` / `fetch_*` API を提供。
- [ ] `--data-engine-url` 指定時のみ Binance を engine-client 経由に切替（feature flag or runtime switch）。
- [ ] 起動時に Python サブプロセスを spawn する `src/engine/process.rs` を追加（オプトイン）。
- [ ] UI 側の差分は最小（`AdapterHandles` の差し替えのみ理想）。
- [ ] レイテンシ・CPU 使用率を旧構成と比較。

**完了条件**: フラグ ON で Binance チャートが Python 経由で正しく描画される。

## フェーズ 3: 残り取引所の Python 移植

優先順（取引所の安定度・利用頻度で並べ替え可）:

- [ ] Bybit
- [ ] Hyperliquid
- [ ] OKX
- [ ] MEXC

各取引所ごとに：
1. `python/flowsurface_data/exchanges/<venue>.py` 実装
2. レート制限の移植
3. 統合テスト（Rust 側 UI で動作確認）

**完了条件**: 全 5 取引所が Python 経由で動作。

## フェーズ 4: ヒストリカルデータ・bulk download 移植

- [ ] [`src/connector/fetcher.rs`](../../src/connector/fetcher.rs) 相当の機能を Python に実装。
- [ ] `data.binance.vision` からの zip/csv 取得・展開を Python で実施。
- [ ] Rust 側は `FetchTrades` / `FetchKlines` コマンドを送って結果を待つだけにする。

**完了条件**: ヒストリカル trade のフェッチが Python に移管。

## フェーズ 5: Rust から取引所コードを削除

- [ ] `exchange/src/adapter/hub/` を削除。
- [ ] `limiter.rs`, `proxy.rs`（プロキシ設定の受け渡しは残す）, `connect.rs` の取引所固有部分を削除。
- [ ] `reqwest`, `fastwebsockets`, `tokio-rustls`, `tokio-socks`, `sonic-rs`, `csv`, `zip` 等、Python 移管で不要になった依存を削除。
- [ ] `--data-engine-url` フラグをデフォルト動作に格上げ、旧経路コードを撤去。

**完了条件**: Rust ビルドが Iced と engine-client のみに依存し、ビルドサイズが縮む。

## フェーズ 6: 配布・運用整備

- [ ] PyInstaller / Nuitka 等で Python サイドを単一実行ファイル化、Rust バイナリと同梱。
- [ ] [`scripts/`](../../scripts/) の Win/Mac/Linux ビルドスクリプトに Python 同梱手順を追加。
- [ ] 起動時の Python プロセス監視・再起動ロジックを本実装。
- [ ] エラーログを Rust 側 `fern` ロガーに集約（Python の stderr を吸い上げる）。
- [ ] README / ユーザードキュメント更新。

**完了条件**: ユーザーが Python ランタイムを別途インストールせずに既存と同じ操作で起動できる。

## ロールバック戦略

- フェーズ 5 完了までは旧 Rust 実装が残っているため、`--data-engine-url` を外せば従来動作に戻せる。
- フェーズ 5 のマージはタグを切ってから実施し、問題が出たら 1 リリース前に戻せるようにする。

## 計測指標

各フェーズ完了時に取得・記録する：

- 起動から最初の trade 表示までのレイテンシ
- trade 受信から canvas 描画までの追加レイテンシ（IPC オーバーヘッド）
- アイドル時 / 高負荷時の CPU・メモリ
- バイナリサイズ
- 異常系: WS 切断時の再接続時間、Python プロセス クラッシュ時の復旧時間
