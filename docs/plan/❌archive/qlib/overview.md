# qlib 概要と Flow Surface への適用方針

## qlib とは

Microsoft が公開する **AI 指向の量的投資（クオンツ）プラットフォーム**。研究者向けの実験基盤として「データ取得 → 特徴量生成 → モデル学習 → バックテスト → ポートフォリオ運用」を一貫して扱う Python フレームワーク。

ライセンス: MIT。商用利用可。

### qlib の主要レイヤー

| 層 | 役割 | Flow Surface との対応 |
|---|---|---|
| Data Layer | 独自バイナリ形式での高速 OHLCV/指標ロード | `EventStore` / Klines に相当 |
| Expression Engine | 式ベース特徴量生成（Alpha158 / Alpha360 同梱） | **未実装**（Rust 側で個別指標を実装中） |
| Model Zoo | LightGBM, XGBoost, MLP, Transformer, GRU, ALSTM 等 20+ | **未実装**（SDK 利用者各自） |
| Workflow (`qrun`) | YAML で実験を宣言的に定義 | 未実装 |
| Backtest / Portfolio | TopKDropout 等のクロスセクション戦略 | Phase 2 Virtual Exchange Engine（粒度差あり） |
| RL Framework (`qlib.rl`) | 執行戦略向け MDP 抽象 | Phase 3 `FlowsurfaceEnv` |
| Online Serving / Meta | ローリング学習・モデル自動更新 | 未実装 |

### qlib の前提と制約

- **時間軸**: 日次〜分次のクロスセクション戦略が主戦場。ティックレベル・板情報は対象外。
- **データ単位**: 「銘柄 × 日付」の長尺データを前提。crypto の連続セッションには直接マッピングできない。
- **依存の重さ**: pandas / pyarrow / lightgbm / pytorch を持ち込む。`pip install` 後の環境サイズが大きい。

---

## Flow Surface との位置関係

ロードマップの差別化軸は「**判断の可視化・納得感**」（ナラティブ）。qlib の強みは「特徴量生成・モデル学習」。**競合せず補完関係**。

```
┌──────────────────────────────────────────────┐
│ Flow Surface（差別化領域）                     │
│  ・ティック単位の市場可視化                     │
│  ・ナラティブ基盤（Phase 4a）                   │
│  ・ASI 連携（Phase 4b）                         │
└──────────────────────────────────────────────┘
                ↑ 利用
┌──────────────────────────────────────────────┐
│ qlib（コモディティ領域・自社で作らない）          │
│  ・Alpha 特徴量エンジン                          │
│  ・Model Zoo                                    │
│  ・実験ワークフロー                              │
└──────────────────────────────────────────────┘
```

**自社で作らない**領域として qlib を位置づける。Phase 4 で ASI を「自社で作らない A2A プロトコル」として委ねるのと同じ判断軸。

---

## 統合方針の選択肢

### A. 軽量統合（特徴量＋モデルだけ借りる）★採用

`python/flowsurface/qlib_adapter.py` を 1 ファイル追加。`FlowsurfaceEnv` の observation を qlib 形式に変換し、Alpha158 と Model Zoo を呼び出す。Rust 改修ゼロ。

- **メリット**: 最短で価値検証できる。ナラティブの `reasoning` 自動生成と直結。
- **デメリット**: qlib の Workflow / Backtest 機能は使わない。

### B. データ層の橋渡し

`fs export-qlib --start ... --end ...` で EventStore → qlib バイナリ形式へエクスポート。qrun でバックテスト → 結果を Flow Surface にナラティブとして re-import。

- **メリット**: qlib の研究ワークフローをそのまま活用。
- **デメリット**: データ二重管理。エクスポータ実装コスト。

### C. 深い統合（Virtual Exchange を qlib backtest に置換）★非採用

`qlib.backtest` を Phase 2 エンジンと統合。

- **デメリット**: qlib backtest は日次/分次のクロスセクション前提。Flow Surface の強み（板・約定 tick）を捨てることになる。**筋が悪い**。

---

## 採用方針

**A → B の順で段階導入**。C には進まない。

| Phase | 採用範囲 |
|---|---|
| Q1 | A のみ（Alpha158 + LGBModel + ナラティブ連携） |
| Q2 | A の拡張（複数モデル・SHAP 値で reasoning 生成） |
| Q3 | B の検討（必要性が確認できた場合のみ） |

---

## ロードマップ各 Phase との関係

| Phase | qlib の役割 |
|---|---|
| Phase 3（完了） | `FlowsurfaceEnv.observation` を Alpha158 で拡張可能に |
| Phase 4a（完了） | ナラティブの `reasoning` に「効いた alpha + 寄与度」を入れる ← **本丸** |
| Phase 4b（次） | qlib 製エージェントを uAgent ラッパー越しに公開（"Alpha158+LGB エージェント"がフォロー対象に） |
| Phase 4c | qlib バックテストの Sharpe/勝率を Ocean Protocol の信頼スコアに流用 |

## 「Python 単独モード」方針との整合

将来予定の Python 単独モード（Rust なしで動かす構想）と qlib は相性が良い。qlib は Python 完結のため、Rust 側に組み込まないことで単独モード移行時の足枷にならない。逆に Rust 側に深く組み込むと単独モード化を阻害するため、**Rust 側統合は避ける** という判断軸を維持する。
