# e-station

<p align="center">
  <img src="docs/wiki/assets/flowsurface-og.png" alt="e-station overview" width="100%" />
</p>

**e-station** は、[Flowsurface](https://flowsurface.com/) のマイクロストラクチャ分析体験を土台に、**日本株（立花証券 e支店）** と **NautilusTrader ベースの REPLAY / 仮想売買** を統合したデスクトップ型トレーディングワークステーションです。

ローソク足だけでなく、**Heatmap / Footprint / DOM / Time & Sales / Comparison** を同じ作業面に並べ、観測、判断、検証をひとつの UI で回せます。

## 窓口

- [ユーザーガイド](https://github.com/botterYosuke/e-station/wiki)
- [エンジニア向けドキュメント](https://botteryosuke.github.io/e-station/)
- [セットアップDL](https://github.com/botterYosuke/e-station/releases)
- [Q&A/お知らせ](https://github.com/botterYosuke/e-station/discussions)
- [不具合](https://github.com/botterYosuke/e-station/issues)

## このアプリでできること

- **見る**: Kline、Heatmap、Footprint、DOM、歩み値をマルチペインで同時に観測
- **切り替える**: リンクグループと保存レイアウトで、複数銘柄・複数画面を素早く往復
- **出す**: live モードでは立花証券 e支店の注文系パネルを利用
- **試す**: replay モードでは過去データ再生と仮想注文を同じ UI で実行
- **つなぐ**: Rust UI と Python エンジンを分離し、HTTP API から自動化可能

## e-station の立ち位置

e-station は、次の 3 系統の中間にあるプロダクトです。

- **Flowsurface 系**: 板と約定を読むための視覚化、軽量な Rust デスクトップ UI
- **国内ブローカー系ツール**: 日本株の監視・注文・口座確認
- **NautilusTrader 系**: REPLAY / バックテスト / 戦略検証基盤

言い換えると、**「国内株も扱える Flowsurface 系観測 UI」** と **「裁量トレード寄りの NautilusTrader フロントエンド」** を両立させようとしているアプリです。

## 2 つのモード

現在の e-station は、**起動時にモードを固定**して使います。

| モード | 目的 | 主な特徴 |
|---|---|---|
| `--mode live` | リアルタイム監視・実運用 | 取引所 / 立花証券のストリームを購読。Depth 系ペインが主力 |
| `--mode replay` | 検証・振り返り・仮想売買 | `/api/replay/load` でデータ投入。REPLAY 注文一覧・買付余力を分離表示 |

> 画面上で live / replay を即時トグルする設計ではなく、**CLI の `--mode` 指定が前提**です。

## 画面イメージ

<p align="center">
  <img src="docs/wiki/assets/screenshot.png" alt="e-station multi-pane workspace" width="100%" />
</p>

<p align="center">
  <img src="docs/wiki/assets/flowsurface-panes.png" alt="multi-pane layout example" width="100%" />
</p>

## インストール

### 配布版を使う

- [Releases](https://github.com/botterYosuke/e-station/releases) から取得

### ソースから起動する

要件:

- Rust toolchain
- Python 3.11+
- `uv` 推奨

```bash
git clone https://github.com/botterYosuke/e-station
cd e-station
uv sync
cargo run -- --mode live
```

REPLAY を起動する場合:

```bash
cargo run -- --mode replay
```

## 最初のおすすめ導線

### ユーザーとして始める

1. [セットアップDL](https://github.com/botterYosuke/e-station/releases)
2. [ユーザーガイド](https://github.com/botterYosuke/e-station/wiki)
3. [Q&A/お知らせ](https://github.com/botterYosuke/e-station/discussions)

### 開発者として始める

1. `uv sync`
2. `cargo run -- --mode live`
3. [エンジニア向けドキュメント](https://botteryosuke.github.io/e-station/)

## 主要コンテンツ

- **ユーザーガイド**  
  使い方、live / replay の違い、チャートの見方、注文、設定。

- **エンジニア向けドキュメント**  
  Python データエンジン、立花証券 API 統合、注文機能、NautilusTrader 統合の仕様。

- **Discussions**  
  Q&A、使い方の相談、お知らせの窓口。

- **Issues**  
  バグ報告、再現手順つきの不具合報告の窓口。

## 立花証券 e支店について

e-station は、立花証券 e支店 API を使った国内株ワークフローを扱います。

- live モードでは、立花証券向けの注文系パネルを利用
- replay モードでは、立花注文とは分離された **仮想注文** を利用
- 立花証券の利用には、口座・認証・API 利用条件を満たしている必要があります

詳細はユーザーガイドと実装仕様書を参照してください。

## 画像・ルーツ

このプロジェクトは [Flowsurface](https://flowsurface.com/) をフォーク元として発展させています。  
README と Wiki の一部画像は、Flowsurface 系のビジュアル資産をもとに再構成しています。

## ライセンス

[GPL-3.0](./LICENSE)
