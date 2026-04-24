# 実装計画

[`spec.md`](./spec.md) の構成へ段階的に移行するためのフェーズ分け。
各フェーズは単独でマージ可能・動作確認可能な粒度を目指す。

## フェーズ 0: 準備 & ベースライン計測（リスク低）

- [ ] `python/` に `pyproject.toml` と `flowsurface_data` パッケージのスケルトンを置く。
- [ ] [`docs/plan/schemas/`](./schemas/) に IPC DTO の JSON Schema を作成。
  - 対象: `TradeMsg`, `KlineMsg`, `DepthSnapshotMsg`, `DepthDiffMsg`, `TickerMsg`, `TickerInfoMsg`, `TickerStatsMsg`, `OpenInterestMsg`, および各コマンド (`Hello` / `Ready` / `Subscribe` / `Unsubscribe` / `FetchKlines` / `FetchTrades` / `FetchOpenInterest` / `FetchTickerStats` / `ListTickers` / `GetTickerMetadata` / `RequestDepthSnapshot` / `SetProxy` / `Shutdown` / `Error` / `EngineError` / `DepthGap`)。
  - 参考: 既存 Rust 型 [`exchange/src/lib.rs`](../../exchange/src/lib.rs), [`exchange/src/adapter.rs`](../../exchange/src/adapter.rs) の `Event`。
  - スキーマ ⇔ 型定義の生成方針（`quicktype` / `datamodel-code-generator` 等）を決定。
  - `schema_major` / `schema_minor` の運用ポリシーを [`CHANGELOG.md`](./schemas/) に記載。
- [ ] Rust 側に `--data-engine-url ws://...` CLI フラグを追加（未指定時は従来動作）。dev モード時の接続トークンは環境変数 `FLOWSURFACE_ENGINE_TOKEN` から読み、本番同梱 spawn 時は stdin から受け取る（[spec.md §4.1.1](./spec.md#411-ローカル-ipc-のアクセス制御)）。
- [ ] **ベースライン計測**（[spec.md §9.3](./spec.md#93-ベースライン計測)）を実施し `docs/plan/benchmarks/baseline.md` に記録。以降のフェーズで比較する基準。Windows (開発環境) 必須、可能なら Linux も。
- [ ] 既存 Rust テストを通したまま CI を維持する。

**完了条件**: 既存挙動を変えずにマージでき、ベースラインが数値で記録されている。

## フェーズ 0.5: venue 単位 backend 抽象化（Rust 側のみ）

[spec.md §5.1](./spec.md#51-venue-単位の-backend-抽象化先行作業) に対応。取引所単位の段階移行を現実的にする前提工事。

- [ ] `VenueBackend` trait を定義。現行 `AdapterHandles` の全経路を網羅すること:
  - 初期化: `list_tickers` / `get_ticker_metadata`（[`exchange/src/adapter/client.rs`](../../exchange/src/adapter/client.rs) L200 付近・L269 付近）
  - ストリーム: `subscribe` / `unsubscribe` / イベントストリーム取得
  - フェッチ: `fetch_klines` / `fetch_open_interest` / `fetch_ticker_stats` / `fetch_trades`
  - 運用: `request_depth_snapshot` / `health`
- [ ] `AdapterHandles` の各 venue フィールドを `Box<dyn VenueBackend>` 相当に置換（[`exchange/src/adapter/client.rs`](../../exchange/src/adapter/client.rs) L21〜）。
- [ ] 既存 `hub/{venue}` を包む `NativeBackend` を実装し、挙動が完全に同一であることを確認。
- [ ] venue 毎に backend を指定できる起動設定を追加（未指定時は全 `NativeBackend`）。

**完了条件**: 抽象化導入後も従来の挙動・レイテンシが維持されている。

## フェーズ 1: Python データエンジン MVP（Binance のみ）

- [ ] `flowsurface_data.server` に WS サーバを実装（`websockets` ライブラリ）。loopback バインドのみ、単一クライアント制限 + トークン一致時の既存接続置換（[spec.md §4.5.2](./spec.md#452-既存接続の置換半死接続対策)）、接続トークン検証、起動ハンドシェイク（[spec.md §4.5](./spec.md#45-起動ハンドシェイク)）、ping/pong keepalive を初期実装に含める。
- [ ] `ExchangeWorker` 抽象 / server↔worker dispatch の境界を最初から設ける（[spec.md §6.1](./spec.md#61-プロセスモデルフェーズ-1-時点)）。フェーズ 1 は asyncio 単一プロセスだが、将来 venue 分割できる構造で着地させる。
- [ ] `exchanges/binance.py` で REST メタデータ + Kline + **Open Interest** + 24h 統計 + WebSocket trade/depth/kline を実装（OI はインジケータが継続要求するため初期から必須）。
- [ ] depth 整合性プロトコル（[spec.md §4.4](./spec.md#44-バックプレッシャと整合性保証)）: `session_id` / `sequence_id` / `prev_sequence_id` の付与、gap 検知時の `DepthGap` 送出と自発的再スナップショット、checksum がある場合の検証を実装。
- [ ] `limiter.py` で Binance のレート制限を移植（[`exchange/src/adapter/limiter.rs`](../../exchange/src/adapter/limiter.rs) を参考）。
- [ ] スキーマは pydantic、出力は orjson。
- [ ] stdin から `{port, token}` JSON を受け取り、ランダムポート・トークンで起動できるようにする（開発時は環境変数フォールバックを許容）。
- [ ] pytest で REST/WS の最低限のテスト（モック取引所 or VCR）＋ depth gap / session 切替の再同期テスト。

**完了条件**: Python のみで Binance のリアルタイム trade / depth / kline / OI を取得・配信でき、depth の gap 検知と再同期が動作する。

## フェーズ 2: Rust 側に engine-client を実装し Binance を切替

- [ ] `engine-client` crate（または `exchange/engine_backend` モジュール）に IPC DTO と WebSocket クライアントを実装。
- [ ] 起動ハンドシェイク（`Hello` / `Ready`、[spec.md §4.5](./spec.md#45-起動ハンドシェイク)）と接続トークン受け渡し（[spec.md §4.1.1](./spec.md#411-ローカル-ipc-のアクセス制御)）を実装。
- [ ] `EngineClientBackend` が `VenueBackend` trait を実装（DTO ⇔ `exchange::Event` / `Kline` / `OpenInterest` / `Arc<Depth>` / `Box<[Trade]>` の相互変換もここで行う）。depth は `session_id` / `sequence_id` で gap 検知し、不一致なら `RequestDepthSnapshot` を送る（[spec.md §4.4](./spec.md#44-バックプレッシャと整合性保証)）。
- [ ] **Python プロセス監視・自動再起動・状態再投入**（[spec.md §5.3](./spec.md#53-python-プロセス復旧プロトコル)）を実装:
  - 購読セット・進行中フェッチ・プロキシ設定を Rust 側に保持。
  - 異常終了検知 → 指数バックオフで spawn → `Hello`/`Ready` → `SetProxy` → 購読再送。
  - 進行中フェッチは `EngineRestarting` で fail し UI にリトライさせる。
  - UI に「データエンジン再起動中」ステータスを出す。
- [ ] `--data-engine-url` 指定時に Binance の backend を `EngineClientBackend` に差し替える（フェーズ 0.5 で入れた venue 単位切替を利用）。
- [ ] 起動時に Python サブプロセスを spawn する `src/engine/process.rs` を追加（オプトイン）。ポート・接続トークンは stdin 経由で Python に渡し、プロキシ資格情報は `Ready` 受領後の IPC `SetProxy` で渡す（[spec.md §5.4](./spec.md#54-プロキシ資格情報の受け渡し)）。
- [ ] UI 側コードはゼロ変更を目標（`AdapterHandles` の API シェイプを維持）。ただし「エンジン再起動中」ステータス表示のための軽微な UI 追加は許容。
- [ ] レイテンシ・CPU 使用率を旧構成と比較。
- [ ] 障害試験: Python を kill → 自動復旧 → 板が snapshot で再同期されることを手動＋自動テストで確認。

**完了条件**: フラグ ON で Binance チャートが Python 経由で正しく描画される。**加えて Python を kill しても自動復旧し、購読と板整合性が回復する**。

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

## 計測指標と合格ライン

詳細は [spec.md §9](./spec.md#9-非機能要件合格ライン)。各フェーズ完了時に再計測し `docs/plan/benchmarks/` に追記する。

フェーズ 2 合格ライン（抜粋）:
- IPC 追加レイテンシ: 中央値 < 2 ms / p99 < 10 ms
- Python クラッシュ → 自動復旧完了: < 3 秒
- depth 再同期: < 500 ms
- CPU 使用率: 現行 Rust 直結の +30% 以内
- depth gap 検知漏れ: 0

未達時の対応:
- レイテンシ / CPU 不足 → [spec.md §4.3.1](./spec.md#431-depth-チャネルのバイナリ化検討) のバイナリ化を適用。
- 慢性的な性能差 → [spec.md §7.1](./spec.md#71-rust-直結モードの長期方針要決定) の案 C（Rust 直結の optional 残置）を再検討。
