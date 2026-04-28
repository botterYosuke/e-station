# e-station

<p align="center">
  <img src="docs/wiki/assets/hero.avif" alt="e-station hero" width="100%" />
</p>

**e-station** は、**板・約定・価格の流れを 1 画面で読むためのデスクトップ型トレーディングワークステーション**です。  
Flowsurface 系のオーダーフロー可視化を土台に、**日本株の発注導線** と **NautilusTrader ベースの REPLAY / 仮想売買** をひとつにまとめています。

競合ツールの良さはそれぞれあります。

- **Flowsurface / NautilusTrader** は深い分析力と拡張性が強い
- **TradeStation** はワークスペース型の多機能デスクトップ体験が強い
- **HYPER SBI 2 / MARKETSPEED / kabuステーション / マネックストレーダー / ネットストック・ハイスピード** は国内株の実務導線が強い

e-station が狙っているのは、その中間です。  
**「国内株も触れる、観測重視のマルチペイン型ワークステーション」** として、見る・並べる・比較する・検証する、を一続きにします。

## まず伝えたいこと

- **観測が主役**: Heatmap / Footprint / DOM / Time & Sales / Comparison を並べて読む前提です
- **レイアウトで戦う**: 1 枚チャートではなく、複数ペインを自分の判断フローに合わせて組みます
- **live と replay を分けて使う**: 起動時にモードを固定し、用途を明確に切り替えます
- **国内株と REPLAY を同じ UI で扱う**: 実売買と検証を行き来しやすい設計です
- **ローカル実行が前提**: Rust UI + Python engine で、重い SaaS 依存を避けた構成です

## こんな人に向いています

- 国内株の発注ツールは使っているが、観測画面が物足りない人
- 板・約定・出来高の偏りを、ローソク足の補助ではなく主役として見たい人
- live の裁量判断と replay の検証を同じ操作感で回したい人
- フロー系 UI が好きだが、日本株や独自ワークフローにも広げたい人

## e-station の見せ場

<p align="center">
  <img src="docs/wiki/assets/screenshot.png" alt="e-station multi-pane workspace" width="100%" />
</p>

<p align="center">
  <img src="docs/wiki/assets/flowsurface-panes.png" alt="e-station pane layouts" width="100%" />
</p>

e-station の価値は、単機能の多さではなく、**複数の観測面を同時に並べたときの判断しやすさ** にあります。  
チャートを 1 枚見るのではなく、**価格・板・約定・比較対象・注文状態** を横断して読むための UI です。

## 主なワークフロー

### 1. live モード

```bash
cargo run -- --mode live
```

- リアルタイムの市場データを観測
- Heatmap / DOM / Time & Sales を組み合わせて板と約定を読む
- 日本株では注文パネル、注文一覧、Buying Power を並べて実務導線に寄せる

### 2. replay モード

```bash
cargo run -- --mode replay
```

- 過去データをロードして再生
- 仮想売買と REPLAY 用 Buying Power で検証
- live と近い UI で「見直す」「試す」「比較する」を回せる

> e-station は現在、**live / replay を起動時に切り替える設計**です。  
> 画面上でモードを頻繁に行き来するより、目的ごとにワークスペースを分けて使う思想です。

## 何ができるか

| 領域 | できること |
|---|---|
| **Order Flow** | Heatmap / Footprint / DOM / Time & Sales で板と約定の流れを観測 |
| **Charting** | Kline / Comparison で価格推移と相対比較を確認 |
| **Workspace** | 複数ペインを並べて、判断に必要な面を 1 画面へ集約 |
| **Execution** | live では国内株の注文導線、replay では仮想売買を利用 |
| **Verification** | 過去データの読み込み、再生、振り返り、仮説検証 |

## 競合と比べた立ち位置

| 比較先 | 強いところ | e-station の答え |
|---|---|---|
| **TradeStation** | 多機能デスクトップ、ワークスペース運用 | e-station はより観測特化で、板・約定の読みやすさを前面に出す |
| **HYPER SBI 2 / MARKETSPEED / kabuステーション / マネックストレーダー / ネットストック・ハイスピード** | 国内株の発注実務、情報集約、ブローカー密着 UX | e-station は実務導線に加えて、Heatmap / Footprint / DOM を中心に据える |
| **Flowsurface** | オーダーフロー可視化、軽量 Rust デスクトップ | その視覚言語をベースに、日本株と replay を統合 |
| **NautilusTrader** | REPLAY / バックテスト / 戦略検証基盤 | e-station は裁量トレード寄りのフロントエンドとして接続する |

つまり e-station は、**ブローカー依存の発注ツール** と **分析専用のフロー観測ツール** のあいだを埋めるプロダクトです。

## セットアップ

### リリースを使う

- [Releases](https://github.com/botterYosuke/e-station/releases) から取得

### ソースから起動する

前提:

- Rust toolchain
- Python 3.11+
- `uv`

```bash
git clone https://github.com/botterYosuke/e-station
cd e-station
uv sync
cargo run -- --mode live
```

REPLAY を使う場合:

```bash
cargo run -- --mode replay
```

## 最初の 5 分で触る順番

1. `cargo run -- --mode live` で起動する
2. まずは `Kline Chart` を置く
3. 次に `Heatmap Chart` または `DOM/Ladder` を追加する
4. `Time & Sales` を並べて、値動きと約定の流れを結びつける
5. 慣れたら複数ペインをリンクして、自分の観測レイアウトを作る

REPLAY を試すなら:

1. `cargo run -- --mode replay`
2. `/api/replay/load` でデータを読み込む
3. `Kline Chart` と `Time & Sales` を並べる
4. `Order List (REPLAY)` と `Buying Power (REPLAY)` で仮想売買を確認する

## ドキュメント

- [ユーザーガイド](https://github.com/botterYosuke/e-station/wiki)
- [Getting Started](docs/wiki/getting-started.md)
- [Modes & Venues](docs/wiki/modes-and-venues.md)
- [Charts](docs/wiki/charts.md)
- [Replay](docs/wiki/replay.md)
- [Orders](docs/wiki/orders.md)
- [Settings](docs/wiki/settings.md)
- [エンジニア向けドキュメント](https://botteryosuke.github.io/e-station/)
- [Discussions](https://github.com/botterYosuke/e-station/discussions)
- [Issues](https://github.com/botterYosuke/e-station/issues)

## 現在の前提と注意点

- プロジェクト内部の実行バイナリ名や crate 名には **`flowsurface`** が残っています
- README ではプロダクト名として **e-station** を使っています
- 日本株の live 注文と replay の役割は明確に分かれています
- すべての機能が全 venue / 全 mode で同じように使えるわけではありません

このあたりの詳細は [Modes & Venues](docs/wiki/modes-and-venues.md) と [Orders](docs/wiki/orders.md) を見るのが早いです。

## クレジット

このプロジェクトは [Flowsurface](https://flowsurface.com/) / [flowsurface-rs/flowsurface](https://github.com/flowsurface-rs/flowsurface) を出発点に発展しています。  
REPLAY / 仮想売買まわりでは [NautilusTrader](https://nautilustrader.io/) の考え方と基盤を活用しています。

## License

[GPL-3.0](./LICENSE)
