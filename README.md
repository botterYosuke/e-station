# e-station

<p align="center">
  <img src="https://raw.githubusercontent.com/wiki/botterYosuke/e-station/assets/screenshot.png" alt="e-station hero" width="100%" />
</p>

**e-station** は、**板・約定・価格の流れを 1 画面で読むためのデスクトップ型トレーディングワークステーション**です。  
Flowsurface 系のオーダーフロー可視化を土台に、**日本株の発注導線** と **NautilusTrader ベースの REPLAY / 仮想売買** をひとつにまとめています。

- **観測が主役**: Heatmap / Footprint / DOM / Time & Sales / Comparison を並べて読む前提です
- **レイアウトで戦う**: 1 枚チャートではなく、複数ペインを自分の判断フローに合わせて組みます
- **live と replay を分けて使う**: 起動時にモードを固定し、用途を明確に切り替えます
- **国内株と REPLAY を同じ UI で扱う**: 実売買と検証を行き来しやすい設計です
- **ローカル実行が前提**: Rust UI + Python engine で、重い SaaS 依存を避けた構成です


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

## 戦略は自己責任

e-station は **NautilusTrader ベースのユーザー定義 Strategy** を Python で書いて走らせる前提です。
本ツールはローカル実行の単一ユーザー製品で、戦略コードは立花証券の認証情報と同じプロセス内で動きます。

- バグった戦略が意図せず発注する・暴走する・想定外の損失を出す等の事故は **ユーザーの責任** です
- サンドボックス・プロセス隔離・任意コード実行制限は **実装しません**（設計上の明示判断）
- 本番口座へ実弾を飛ばすには別途 `TACHIBANA_ALLOW_PROD=1` の明示が必要（誤本番送信の安全装置のみ提供）
- replay モードでの十分な検証 → demo 口座 → 本番、の順で動かすことを推奨します

## 何ができるか

| 領域 | できること |
|---|---|
| **Order Flow** | Heatmap / Footprint / DOM / Time & Sales で板と約定の流れを観測 |
| **Charting** | Kline / Comparison で価格推移と相対比較を確認 |
| **Workspace** | 複数ペインを並べて、判断に必要な面を 1 画面へ集約 |
| **Execution** | live では国内株の注文導線、replay では仮想売買を利用 |
| **Verification** | 過去データの読み込み、再生、振り返り、仮説検証 |

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

## License

[GPL-3.0](./LICENSE)
