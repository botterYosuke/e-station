# ユーザー戦略サンプル

このディレクトリには、e-station 上で動かせる **ユーザー定義 Strategy** の
最小サンプルを置いています。コピーして自分の戦略を書く出発点として使ってください。

| ファイル | 内容 |
|---------|------|
| `sma_cross.py` | 短期 SMA × 長期 SMA のクロスで成行エントリー / クローズ |

## 自己責任の注意

e-station の戦略は **ユーザー自身が書いた Python コードを同じプロセスで実行**します。

- サンドボックス・プロセス隔離・任意コード実行制限は **実装していません**
- バグによる誤発注・暴走・想定外損失はすべて **ユーザーの責任** です
- 本番口座への発注には別途 `TACHIBANA_ALLOW_PROD=1` が必要（誤本番送信の
  安全装置のみ提供）
- `replay` モードで十分検証 → demo 口座 → 本番、の順で動かすことを強く推奨します

リポジトリ直下の `README.md` 「戦略は自己責任」セクションも参照してください。

## 起動

`scripts/run-replay-debug.sh` にストラテジファイルのパスを渡します。
銘柄コードと期間は **環境変数で必ず指定**します（隠れたデフォルトは持ちません）。

```bash
REPLAY_INSTRUMENT_ID=1301.TSE \
REPLAY_START_DATE=2025-01-06 \
REPLAY_END_DATE=2025-03-31 \
bash scripts/run-replay-debug.sh docs/example/sma_cross.py
```

リポジトリ直下の `.env` に書いておけば毎回タイプする必要はありません。

スクリプトは次を自動で行います。

1. `cargo build`（debug ビルド）
2. `flowsurface --mode replay` を起動
3. バックグラウンドで `POST /api/replay/load` → `POST /api/replay/start` を送信

GUI 側は `ReplayDataLoaded` を受信すると **TimeAndSales・CandlestickChart・
OrderList・BuyingPower の 4 ペインを自動生成**します。

### 必須・任意の環境変数

| 環境変数 | 必須 | 例 / デフォルト | 説明 |
|---------|:---:|-----|------|
| `REPLAY_INSTRUMENT_ID` | ✅ | `1301.TSE` | 銘柄コード |
| `REPLAY_START_DATE` | ✅ | `2025-01-06` | バックテスト開始日 |
| `REPLAY_END_DATE` | ✅ | `2025-03-31` | バックテスト終了日 |
| `REPLAY_GRANULARITY` |  | `Daily`（既定）/ `Minute` / `Trade` | 足種 |
| `REPLAY_INITIAL_CASH` |  | `1000000`（既定） | 初期資金（円） |

必須の 3 つを指定せずに起動するとスクリプトはエラーで終了します
（`REPLAY_INSTRUMENT_ID is required` など）。

## sma_cross.py の動作

デフォルトのパラメータ（`short=3, long=5`, `instrument_id=1301.TSE`, `Daily` 足）で
2025-01-06〜2025-03-31（約 57 営業日）を実行すると、複数回クロスが発生します。

```
[SmaCrossStrategy] BUY signal:  sma_short=XXX crossed above sma_long=XXX
[SmaCrossStrategy] SELL signal: sma_short=XXX crossed below sma_long=XXX
```

ターミナルのログ（debug ビルドは stdout）で確認できます。

## パラメータの渡し方

`strategy_init_kwargs` に JSON を指定すると、コンストラクタの引数を上書きできます。

```bash
# HTTP body を直接 curl で渡す例（replay_dev_load.sh 経由でなく手動確認したいとき）
curl -sS -X POST http://127.0.0.1:9876/api/replay/start \
  -H 'Content-Type: application/json' \
  -d '{
    "instrument_id": "1301.TSE",
    "start_date": "2025-01-06",
    "end_date": "2025-03-31",
    "granularity": "Daily",
    "strategy_id": "user-strategy",
    "initial_cash": 1000000,
    "strategy_file": "docs/example/sma_cross.py",
    "strategy_init_kwargs": {"short": 5, "long": 10, "lot_size": 200}
  }'
```

Minute 足で動かすには `bar_type_str` をキーワード引数で渡します。

```json
{"bar_type_str": "1301.TSE-1-MINUTE-LAST-EXTERNAL"}
```

## 規約

ローダ (`engine.nautilus.strategy_loader.load_strategy_from_file`) が
読み取れる戦略ファイルの形式は次のとおりです。

1. **`Strategy` 派生クラスをファイル中にちょうど 1 つだけ定義する**
   - 0 個 / 2 個以上はローダが `StrategyLoadError` で reject します
   - 他モジュールから import した `Strategy` 派生はカウントしないので
     ヘルパ import は自由です（`cls.__module__` で識別）
2. **`__init__` は keyword arguments のみ受ける設計を推奨**
   - HTTP body の `strategy_init_kwargs` JSON でそのまま渡せるようにするため
   - 例: `def __init__(self, *, instrument_id: str = "1301.TSE", lot_size: int = 100)`
3. **`on_bar` または `on_trade_tick` を実装する**
4. **`on_start` で `subscribe_bars(BarType.from_str(...))` か
   `subscribe_trade_ticks(...)` を呼ぶ**

`InstrumentId` はコンストラクタ内で `InstrumentId.from_str(instrument_id)` に
変換するパターンが書きやすいです（JSON は文字列しか運べないため）。

## ローダの単体確認

ストラテジファイルが読み込めるか単体で試したい場合:

```bash
uv run python -c "
from pathlib import Path
from engine.nautilus.strategy_loader import load_strategy_from_file
s = load_strategy_from_file(Path('docs/example/sma_cross.py'), {'instrument_id': '1301.TSE'})
print(type(s).__name__)
"
```

`SmaCrossStrategy` と表示されれば OK です。

## 依存ポリシー

- numpy / pandas / scikit-learn など追加依存は **増やさない方針**です
  （サンプルは `collections.deque` などの標準ライブラリのみで書いてあります）
- AI/ML フレームワークは本体・SDK に同梱しません。機械学習を組み込む場合は
  ユーザー側で個別にインストールしてください
