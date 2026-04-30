# e-station

<p align="center">
  <img src="https://raw.githubusercontent.com/wiki/botterYosuke/e-station/assets/screenshot.png" alt="e-station hero" width="100%" />
</p>

**e-station** は、**板・約定・価格の流れを 1 画面で読むためのデスクトップ型トレーディングワークステーション**です。  
[Flowsurface](https://flowsurface.com/) 系のオーダーフロー可視化を土台に、**日本株の実運用導線** と **NautilusTrader ベースの REPLAY / 仮想売買 / 戦略検証** をひとつにまとめています。

## 何がうれしいか

- **観測が主役**: Heatmap / Footprint / DOM / Time & Sales / Kline / Comparison を同じ作業面に並べられます
- **live と replay を同じ思想で使える**: 実運用と検証を、似た UI と似た判断フローで往復できます
- **レイアウト資産が作れる**: 複数ペイン、リンクグループ、ポップアウトで「自分の観測面」を育てられます
- **ローカル完結で軽い**: Rust UI + Python engine の構成で、重い SaaS 依存を避けています
- **自動化しやすい**: REPLAY や注文まわりは localhost の HTTP API から操作できます

## こんな人向け

- ローソク足だけではなく、**板の厚み・約定の流れ・価格帯ごとの反応**まで見たい
- ブローカー専用ツールの発注導線に加えて、**Flowsurface 系の観測 UI** も欲しい
- 裁量トレードの振り返りや仮説検証を、**REPLAY と仮想注文**で回したい
- 将来的に自分の Python strategy を試したい

## まず知っておくべきこと

e-station は現在、**起動時にモードを固定**して使います。

| モード | 目的 | 主な特徴 |
|---|---|---|
| `--mode live` | リアルタイム監視と実運用 | 取引所 / 立花証券のストリームを購読。Heatmap / DOM / Ladder が主役 |
| `--mode replay` | 過去データ再生、仮想注文、戦略検証 | `/api/replay/load` でデータ投入。REPLAY 用ペインが自動生成 |

```bash
cargo run -- --mode live
cargo run -- --mode replay
```

`--mode` を省略すると起動できません。

## スクリーンショット

<p align="center">
  <img src="https://raw.githubusercontent.com/wiki/botterYosuke/e-station/assets/flowsurface-panes.png" alt="multi-pane layouts" width="100%" />
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/wiki/botterYosuke/e-station/assets/heatmap.png" alt="heatmap view" width="100%" />
</p>

## クイックスタート

### 1. 前提

- Rust toolchain
- Python 3.11+
- `uv`

### 2. セットアップ

```bash
git clone https://github.com/botterYosuke/e-station
cd e-station
uv sync
```

### 3. live を触る

```bash
cargo run -- --mode live
```

最初のおすすめ:

1. `Kline Chart` を置く
2. `Heatmap Chart` または `DOM/Ladder` を追加する
3. `Time & Sales` を並べる
4. 同じリンクグループに入れて銘柄同期を試す

### 4. replay を触る

```bash
cargo run -- --mode replay
```

その後、別ターミナルからデータをロードします。

```bash
curl -X POST http://127.0.0.1:9876/api/replay/load ^
  -H "Content-Type: application/json" ^
  -d "{\"instrument_id\":\"7203.TSE\",\"start_date\":\"2024-01-01\",\"end_date\":\"2024-03-31\",\"granularity\":\"Daily\"}"
```

読み込み成功後は、対象銘柄の `Kline Chart` と `Time & Sales`、セッション共通の `Order List (REPLAY)` と `Buying Power (REPLAY)` が自動生成されます。

## セットアップ前チェックリスト

| 用途 | 必要なもの |
|---|---|
| すべての起動 | `--mode live` または `--mode replay` を必ず指定 |
| 外部エンジンに attach | `FLOWSURFACE_ENGINE_TOKEN` を Python 側 `--token` と一致させる |
| 立花証券 dev 自動ログイン | `.env` に `DEV_TACHIBANA_USER_ID` / `DEV_TACHIBANA_PASSWORD` / `DEV_TACHIBANA_DEMO=true` を設定 |
| 本番 URL への発注 | `TACHIBANA_ALLOW_PROD=1` を明示 |
| `/api/order/submit` を有効化 | `FLOWSURFACE_ORDER_GUARD_ENABLED=1` を設定 |
| replay | J-Quants データを配置 |
| replay 補助スクリプト | `bash scripts/run-replay-debug.sh <strategy.py> <instrument_id> <start_date> <end_date>` |

ひな形は [.env.example](.env.example) を参照してください。

## できること

| 領域 | できること |
|---|---|
| **Order Flow** | Heatmap / Footprint / DOM / Time & Sales で板と約定の流れを観測 |
| **Charting** | Kline / Comparison で価格推移と相対比較を確認 |
| **Workspace** | 複数ペイン、リンクグループ、ポップアウトで作業面を構築 |
| **Execution** | live では国内株の注文導線、replay では仮想注文 |
| **Verification** | 過去データロード、再生、速度変更、振り返り、戦略検証 |
| **Automation** | localhost HTTP API から replay / order / portfolio を操作 |

## REPLAY と戦略

REPLAY は単なるチャート再生ではなく、**NautilusTrader ベースの再生エンジン**と UI をつないだ検証モードです。

- データ投入は `/api/replay/load`
- 速度変更は `/api/replay/control`
- 戦略起動は `/api/replay/start`
- `strategy_file` は **現行実装では必須**です
- `ReplayControl` ペインから `1x / 10x / 100x` を切り替えられます

ユーザー定義 strategy の最小サンプルは [docs/example/buy_and_hold.py](docs/example/buy_and_hold.py) と [docs/example/README.md](docs/example/README.md) にあります。

## 注文まわり

live と replay では意味が異なります。

| モード | 注文の意味 |
|---|---|
| **live** | 立花証券 e支店に対する実注文 |
| **replay** | 検証用の仮想注文 |

補足:

- `FLOWSURFACE_ORDER_GUARD_ENABLED=1` を設定しない限り、`/api/order/submit` は 503 で拒否されます
- `TACHIBANA_ALLOW_PROD=1` を設定しない限り、本番 URL への送信は遮断されます
- replay 注文は実口座には送られず、REPLAY 用の注文一覧と買付余力に分離されます

## ドキュメントの読み分け

このリポジトリのドキュメントは 2 系統あります。

- **ユーザー向け**: [GitHub Wiki](https://github.com/botterYosuke/e-station/wiki)
- **開発者向け**: [MkDocs サイト](https://botteryosuke.github.io/e-station/)

ユーザー向けの入口:

- [Getting Started](docs/wiki/getting-started.md)
- [Modes & Venues](docs/wiki/modes-and-venues.md)
- [Charts](docs/wiki/charts.md)
- [Replay](docs/wiki/replay.md)
- [Backtest](docs/wiki/backtest.md)
- [Orders](docs/wiki/orders.md)
- [Settings](docs/wiki/settings.md)
- [Troubleshooting](docs/wiki/troubleshooting.md)

## アーキテクチャ概要

- **Rust / Iced**: デスクトップ UI、ペイン管理、レイアウト、テーマ、HTTP 制御の受け口
- **Python engine**: 市場データ、NautilusTrader 統合、立花証券連携、REPLAY 実行
- **localhost IPC**: WebSocket と HTTP API で UI と engine が連携

この分離のおかげで、UI を保ったまま replay や strategy 実行を外部からドライブできます。

## 安全に関する注意

e-station はローカル実行の単一ユーザー向けツールです。  
特に strategy 実行は、**ユーザーが書いた Python コードを同じプロセスで動かす**前提です。

- サンドボックス、プロセス隔離、任意コード実行制限は実装していません
- バグによる誤発注、暴走、想定外損失はユーザーの責任です
- replay で十分に検証してから demo、本番へ進むことを強く推奨します

## コントリビュート / フィードバック

- [Issues](https://github.com/botterYosuke/e-station/issues)
- [Discussions](https://github.com/botterYosuke/e-station/discussions)

不具合報告では、`live / replay` のどちらか、使用したエンドポイント、銘柄コード、期間、起動方法を添えると再現しやすくなります。

## License

[GPL-3.0](LICENSE)
