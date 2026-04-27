# nautilus_trader 統合: 仕様

## 1. ゴール

1. **リプレイ機能**: nautilus `BacktestEngine` を使い、既存 `EventStore`（Klines / Trades / Snapshot）の履歴をフィードして決定論的にバックテストできる
2. **発注機能**: nautilus `LiveExecutionEngine` を使い、立花証券（株式現物・N2）と既存暗号資産 venue（N3 以降）に実弾で発注できる
3. **ナラティブ連携**: nautilus `Strategy` の意思決定が自動で Phase 4a のナラティブ Store に記録される

## 2. スコープ

### 2.0 Phase N-pre — feasibility 確認と前提固め（実装ゼロ）

N0 着手前に以下の **ブロッカー**を解決する。すべてレビュー指摘 H4 / H6 / M8 / L1（Q6）の前倒し処理。

- **H4 / Q3**: nautilus `BacktestEngine` の clock 注入 feasibility プロトタイプ（`tests/spike/`、捨てコード）。`BacktestEngine.run_streaming(...)` 系の API で外部 clock を進められるか、または `Strategy.on_event` を直接駆動する自作ループになるかを切り分け、結果を本 spec §3.1 に追記
- **H6 / Q1**: nautilus_trader のバージョン pin を確定（[open-questions.md Q1](./open-questions.md)）。`>=1.211,<2.0` SemVer 案 / `==1.211.x` 厳密 pin 案のどちらを N0 に採用するかを decision して spec §5 を書き換え
- **M8 / Q5**: 配布形態（venv 配布 / PyInstaller / インストーラ同梱）を確定し LGPL-3.0 同梱要件（差し替え可能性、NOTICE）を満たす実装方針をまとめる
- **L1 / Q6**: 既存暗号資産 venue の Rust 側発注経路の有無を grep で確定し、N3 の工数を「移植」/「新規実装」で確定
- **C6 / 新規 Q8**: 立花の動的呼値テーブルと nautilus `Instrument.price_increment` の整合方針（[open-questions.md Q8](./open-questions.md#q8) で扱う）
- **L7 / Q7**: 発注 UI を Python 側に統一する方針を確定（spec §4 公開 API 表との整合確認込み）

**Exit 条件**: 上記 6 件すべての decision が open-questions.md で `Resolved` ラベル付き、かつ spec.md / implementation-plan.md に反映済み。

### 2.1 Phase N0 — エンジン同梱とハロー戦略（MVP）

- `python/engine/` に `nautilus_trader` 依存を追加（`pyproject.toml` / `uv.lock`）
- 新規 venue ワーカー `python/engine/nautilus/` を新設し、既存 `EngineClientBackend` の IPC（[engine-client/src/dto.rs](../../../engine-client/src/dto.rs)）と並行で nautilus エンジンを起動できる
- `BacktestEngine` を起動し、既存 `EventStore` から OHLCV を投入する `DataLoader` を 1 本だけ書く（株式 1 銘柄・日足）
- "BuyAndHold" 程度のサンプル `Strategy` を組み込み、PnL とエクイティカーブが返ることを headless テストで検証
- **発注は出さない**。`SimulatedExchange` のみ

### 2.2 Phase N1 — リプレイ HTTP API の差し替え + REPLAY モード仮想注文

- 既存 `/api/replay/order` `/api/replay/portfolio`（Phase 2 構想）を **nautilus の `BacktestEngine` 経由**で実装
- HTTP API → Python ワーカー → nautilus `OrderFactory` の薄いブリッジを書く
- フロント（iced）からは引き続き同じ HTTP エンドポイントを叩く想定。**API 契約は変えない**（自作エンジン → nautilus の置き換えはユーザーから見えない）
- 既存 `FlowsurfaceEnv`（Gymnasium）は HTTP 越しなのでそのまま動く

**REPLAY モード仮想注文の統合（[docs/plan/✅order/](../✅order/) からの引き取り）**:

- `docs/plan/✅order/` で実装した `POST /api/order/submit` `/api/order/modify` `/api/order/cancel` を REPLAY モード時に **`SimulatedExchange` 経由にルーティング**
- Python 側 `tachibana_orders.NautilusOrderEnvelope` を共通入力とし、ディスパッチャ（`python/engine/order_router.py` 新設）で live / replay を分岐:
  - live → `tachibana_orders.submit_order(...)` → 立花 HTTP
  - replay → `BacktestExecutionEngine.process_order(...)` → SimulatedExchange
- iced 側 UI 差分（[wiki UX](../../wiki/orders.md#replay-モード中の動作)）:
  - バナー「⏪ REPLAYモード中 — 注文は無効です」
  - ボタンラベル「仮想注文確認」
  - 第二暗証番号 modal を **出さない**（[order/architecture.md §5](../✅order/architecture.md#5-第二暗証番号の取扱い) の `Event::SecondPasswordRequired` 発火を REPLAY ガードで skip）
- 約定通知は live と同じ IPC `Event::OrderFilled` を使う（UI はバナー以外で live/replay を区別しない）
- 監査ログ WAL（[order/architecture.md §4.2](../✅order/architecture.md#42-監査ログwal-write-ahead-log)）は REPLAY モードでは別ファイル `tachibana_orders_replay.jsonl` に出す（live と混ざらないようにする）
- `client_order_id` の名前空間は live / replay で分離（同一 ID を投入しても干渉しない）

### 2.3 Phase N2 — 立花 ExecutionClient

- `python/engine/exchanges/tachibana_nautilus.py` を新設し、nautilus の `LiveExecutionClient` を実装
- **前提: [docs/plan/✅order/](../✅order/) Phase O0〜O2 が完了していること**（Phase 1 + order/ の Python 関数・IPC enum・第二暗証番号 UI・EC frame パーサ・`tachibana_orders.NautilusOrderEnvelope` がすでに揃っている前提）
- 本フェーズでは **`tachibana_nautilus.py` を nautilus `LiveExecutionClient` の薄い adapter として書く**:
  - nautilus の `LiveExecutionClient.submit_order(Order)` → `tachibana_orders.submit_order(session, second_password, NautilusOrderEnvelope.from_nautilus(order))`
  - 同様に `modify_order` / `cancel_order` を委譲
  - `tachibana_event._parse_ec_frame` の戻り値 → nautilus `OrderFilled` / `OrderCanceled` イベントに変換し `LiveExecutionEngine.process_event(...)` に流す
- 立花 API の写像規則は **[data-mapping.md](./data-mapping.md)** および [order/spec.md §6](../✅order/spec.md#6-nautilus_trader-互換要件不変条件) を参照（本 spec で重複定義しない）
- 注文種別カバレッジは order/ の Phase O0〜O3 進捗に従う（O0=現物成行のみ、O3 で信用・逆指値・期日指定が解禁）
- **デモ環境のみ**。本番は env フラグで明示的に許可しない限り使わせない（`TACHIBANA_ALLOW_PROD=1`、[tachibana/spec.md §3.1](../✅tachibana/spec.md#31-セキュリティ) と同じガード）

### 2.4 Phase N3 — 暗号資産 venue ExecutionClient（任意）

- 既存 `exchange/` クレート（Rust）の Hyperliquid / Bybit 等の発注経路を nautilus 側に移植
- 案 A の長期方針（Rust 発注経路を持たない）に合わせて、Rust 側の発注関連コードは段階的に削除

### 2.5 含めないもの

- **nautilus の Rust コアを直接 Rust 側 crate から呼び出す**: しない。nautilus は **Python プロセス内**でだけ使い、Rust（iced）からは IPC 越しで叩く（Python 単独モード方針との整合）
- **マルチアカウント・複数戦略の同時実行**: Phase N0–N2 では 1 戦略 1 アカウントに固定
- **nautilus の Cluster / 分散実行**: 単一プロセスのみ
- **nautilus 標準 adapter（Binance, Interactive Brokers 等）の有効化**: 立花と既存 venue 以外は無効。バンドルサイズを増やさない

## 3. 非機能要件

### 3.1 決定論性

- リプレイは **同じ入力 → 同じ PnL** を保証する。`BacktestEngine` の clock を `Backtest`（仮想時刻）モードで使い、wall clock を一切参照しない
- ナラティブの `timestamp` も仮想時刻で記録（既存 `current_time` と整合）
- **検証テスト（必須、N0 Exit 条件）**: 同一 seed・同一データセットで `NautilusRunner.start_backtest(...)` を 2 回回し、最終 equity / 全約定タイムスタンプ / 全 OrderFilled `last_price` が**ビット一致**することを `tests/python/test_nautilus_determinism.py` で検証
- **wall clock 非参照テスト（必須、N0 Exit 条件）**: `time.time` / `time.monotonic` / `datetime.now` を `unittest.mock` で固定値に差し替えても backtest 結果が変わらないことを検証

### 3.2 セキュリティ

- 立花クレデンシャルは Phase 1 と同じ keyring 経路を使う。nautilus エンジンには **Python メモリ上で**だけ渡す
- nautilus の persistence 機能（Parquet ディスクキャッシュ・SQLite Cache backend）は **無効化**して始める。立花のセッション情報・約定履歴を nautilus 側ファイルに二重保存しない
- **persistence 無効化と Cache warm-up は別概念**: nautilus の `CacheConfig.database` を `None` に設定（ディスク永続化 OFF）したまま、in-memory `Cache` は N2 起動時に立花注文台帳（`CLMOrderList`）から **毎回 warm-up** する。詳細は [data-mapping.md §6](./data-mapping.md#6-cache-warm-up-vs-persistence)
- **Strategy 信頼境界（M3）**: ユーザー Python Strategy は engine プロセス内で実行されるため、立花 creds がメモリ上で同居する。`--strategy-file` 経路の信頼モデルは [open-questions.md Q2](./open-questions.md#q2) で確定。N0/N1 では「組み込み Strategy のみ」「ユーザー Strategy ロードを許さない」運用に縛り、Q2 解決まで `--strategy-file` フラグを実装しない

### 3.3 パフォーマンス

- リプレイは headless で動く（既存 `--headless` モードに乗る）
- **計測対象の定義**: 「1 年・日足・1 銘柄」の計測対象は **`NautilusRunner.start_backtest()` 呼出から `Event::EngineStopped` の IPC 受領までの wall clock**（IPC 越し全往復、ナラティブ書込み込み）。`BacktestEngine.run()` 単体時間は別ベンチマークとして計測
- **30 秒以内**を目安（具体目標は N1 の終盤で計測してから確定）。実測値は [docs/plan/✅python-data-engine/benchmarks/](../✅python-data-engine/benchmarks/) に nautilus 用ベースラインを別ファイルで追加して保存
- **市場時間帯と LiveExecutionEngine（M2）**: 立花 venue が閉場帯で `Disconnected{reason:"market_closed"}` を返している間、nautilus `LiveExecutionEngine` への発注は **HTTP API 層 (`order_api.rs`) で先行 reject** する（reason_code=`MARKET_CLOSED`、[order/spec.md §5.2](../✅order/spec.md#52-reason_code-体系観測性)）。nautilus 内部で reject されると `Strategy.on_event(OrderRejected)` 経由でナラティブが汚染されるため、**venue が閉場中は ExecutionClient を `start()` しない**運用にする

### 3.4 観測性

- nautilus のログは Python 側 `logging` 経由で既存ロガーに統合
- IPC イベントの欠落を防ぐため、`OrderFilled` / `PositionClosed` 等は nautilus → ワーカー → IPC `Event` の 3 段で必ず flush する

## 4. 公開 API（不変条件）

`POST /api/order/*` は [docs/plan/✅order/](../✅order/) で定義済み。本計画 N1/N2 でも **API 契約・冪等性規約・reason_code 体系を変更しない**。N1 では REPLAY モード時のみ Python ディスパッチャが SimulatedExchange に流す（[README.md §REPLAY モード仮想注文の取り込み](./README.md#replay-モード仮想注文の取り込み)）。

リプレイ・ナラティブ系の API:

| エンドポイント | 動作 | 備考 |
|---|---|---|
| `POST /api/replay/order` | nautilus `OrderFactory` で発注 → `BacktestEngine` 即時約定判定 | API 契約は既存案と同じ。**legacy パス**: 新規実装は `/api/order/submit` を REPLAY モードで使う方を推奨 |
| `GET /api/replay/portfolio` | nautilus `Portfolio` から position / PnL を取得 | 同上 |
| `GET /api/replay/state` | 既存実装のまま（`EventStore` 直読み） | nautilus を経由しない |
| `POST /api/agent/narrative` | **N1 で新設**（[H5]）。`docs/plan/README.md` Phase 4a の概念だが現リポジトリには未実装 | nautilus `Strategy` フックから Python が叩く。N1 タスクに API 新設を含める |

**発注 UI の所在（Q7 決定、2026-04-26）**: `POST /api/order/*` は Rust HTTP 層が受けるが、**発注入力 UI は Python tkinter に統一**（[open-questions.md Q7](./open-questions.md#q7)）。iced は Portfolio/PnL/Chart 等の監視・表示のみ担う。Python 単独モード方針と一貫する。

**`/api/replay/state` と nautilus 内部状態の二重管理（M-ack）**: `/api/replay/state` は `EventStore` 直読みで market data を返すのみ（position / PnL は含めない）。Strategy/Portfolio の状態は `/api/replay/portfolio` 経由で nautilus から取得する。両 API のレスポンスは独立しており **クライアント側で混在 join しない** ことを規約として明記。

## 5. 依存方針（確定版、2026-04-26）

- nautilus_trader は **Python パッケージとしてのみ**取り込む（`uv add nautilus_trader`）
- **配布形態（Q5 決定）**: **venv 配布**。PyInstaller one-binary 化は行わない。LGPL-3.0 差し替え可能性確保の追加実装は不要
- **バージョン pin 戦略（Q1 決定）**:
  - N0/N1: `>=1.211, <2.0`（開発中の柔軟性確保。検証済み最新: `1.225.0`）
  - N2 完了後: `==1.225.x` 厳密 pin（立花実弾発注が通った構成を固定）
  - GitHub `main` 直結はしない
- **wheel 入手性（N-pre 検証済み）**: Windows 11 で `uv pip install nautilus_trader` が `nautilus-trader==1.225.0` の wheel を取得することを確認（2026-04-26）。macOS arm64 / Linux x86_64 は未検証（N-pre 完了条件として記録）
