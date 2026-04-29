# ユーザー戦略サンプル

このディレクトリには、e-station 上で動かせる **ユーザー定義 Strategy** の
最小サンプルを置いています。コピーして自分の戦略を書く出発点として使ってください。

| ファイル | 内容 |
|---------|------|
| `buy_and_hold.py` | 最初の Bar で 1 lot 成行買い → 以後ホールド |
| `sma_cross.py` | 短期 SMA × 長期 SMA のクロスで成行エントリー / クローズ |

## 自己責任の注意

e-station の戦略は **ユーザー自身が書いた Python コードを同じプロセスで実行**します。

- サンドボックス・プロセス隔離・任意コード実行制限は **実装していません**
- バグによる誤発注・暴走・想定外損失はすべて **ユーザーの責任** です
- 本番口座への発注には別途 `TACHIBANA_ALLOW_PROD=1` が必要（誤本番送信の
  安全装置のみ提供）
- `replay` モードで十分検証 → demo 口座 → 本番、の順で動かすことを強く推奨します

リポジトリ直下の `README.md` 「戦略は自己責任」セクションも参照してください。

## 規約

ローダ (`engine.nautilus.strategy_loader.load_strategy_from_file`) が
読み取れる戦略ファイルの形式は次のとおりです。

1. **`Strategy` 派生クラスをファイル中にちょうど 1 つだけ定義する**
   - 0 個 / 2 個以上はローダが `StrategyLoadError` で reject します
   - 他モジュールから import した `Strategy` 派生はカウントしないので
     ヘルパ import は自由です（`cls.__module__` で識別）
2. **`__init__` は keyword arguments のみ受ける設計を推奨**
   - HTTP body の `strategy_init_kwargs` JSON でそのまま渡せるようにするため
   - 例: `def __init__(self, *, instrument_id: str, lot_size: int = 100)`
3. **`on_bar` または `on_trade_tick` を実装する**
4. **`on_start` で `subscribe_bars(BarType.from_str(...))` か
   `subscribe_trade_ticks(...)` を呼ぶ**

`InstrumentId` はコンストラクタ内で `InstrumentId.from_str(instrument_id)` に
変換するパターンが書きやすいです（JSON は文字列しか運べないため）。

## 起動

```
cargo run -- --mode replay --strategy-file examples/strategies/sma_cross.py
```

## パラメータの渡し方

- **暫定**: HTTP body の `strategy_init_kwargs` JSON
  ```json
  {"instrument_id": "1301.TSE", "short": 5, "long": 20, "lot_size": 100}
  ```
- UI のパラメータフォームは N4 のスコープ外（次フェーズで対応予定）

## 依存ポリシー

- numpy / pandas / scikit-learn など追加依存は **増やさない方針**です
  （サンプルは `collections.deque` などの標準ライブラリのみで書いてあります）
- AI/ML フレームワークは本体・SDK に同梱しません。機械学習を組み込む場合は
  ユーザー側で個別にインストールしてください

## 動作確認

ローダが読み込めるか単体で試したい場合:

```bash
uv run python -c "from pathlib import Path; from engine.nautilus.strategy_loader import load_strategy_from_file; s = load_strategy_from_file(Path('examples/strategies/buy_and_hold.py'), {'instrument_id': '1301.TSE'}); print(type(s).__name__)"
```

`BuyAndHoldStrategy` と表示されれば OK です。
