# ユーザー戦略サンプル

このディレクトリには、e-station 上で動かせる **ユーザー定義 Strategy** の
最小サンプルを置いています。コピーして自分の戦略を書く出発点として使ってください。

| ファイル | 内容 |
|---------|------|
| `test_strategy_daily.py` | 最初のバーで成行買いし、以降は保有し続ける最小戦略（Daily 足）|
| `test_strategy_minute.py` | 同上の Minute 足バリエーション。1 週間 ≒ 1,500 本の分足で動作確認できる |
| `test_strategy_trade.py` | 同上の歩み値（Trade）バリエーション。`subscribe_trade_ticks` + `on_trade_tick` で最初の tick で買う。GUI の TimeAndSales ペインに歩み値が流れる |

## 自己責任の注意

e-station の戦略は **ユーザー自身が書いた Python コードを同じプロセスで実行**します。

- サンドボックス・プロセス隔離・任意コード実行制限は **実装していません**
- バグによる誤発注・暴走・想定外損失はすべて **ユーザーの責任** です
- 本番口座への発注には別途 `TACHIBANA_ALLOW_PROD=1` が必要（誤本番送信の
  安全装置のみ提供）
- `replay` モードで十分検証 → demo 口座 → 本番、の順で動かすことを強く推奨します

リポジトリ直下の `README.md` 「戦略は自己責任」セクションも参照してください。

## 起動

### A. headless（GUI なし・最速）

```bash
uv run python -m engine.replay_session run \
    --strategy examples/test_strategy_daily.py \
    --instrument 1301.TSE \
    --start 2025-01-06 \
    --end 2025-03-31 \
    --mode inprocess
```

### B. GUI で目視しながら（attach）

別ターミナルで先に GUI を起動:

```bash
cargo run -- --mode replay
```

`%APPDATA%\flowsurface\engine-session.json` が書かれたら helper を attach mode で実行:

```bash
uv run python -m engine.replay_session run \
    --strategy examples/test_strategy_daily.py \
    --instrument 1301.TSE \
    --start 2025-01-06 \
    --end 2025-03-31 \
    --mode auto
```

GUI 側は `ReplayDataLoaded` を受信すると **TimeAndSales・CandlestickChart・
OrderList・BuyingPower の 4 ペインを自動生成**します。完全な手順とトラブル
シューティングは [docs/wiki/backtest.md](../wiki/backtest.md) と
[python/tests/test_replay_session_attach_manual_smoke.md](../../python/tests/test_replay_session_attach_manual_smoke.md) を参照。

> **注意**: `scripts/run-replay-debug.sh` と `scripts/replay_dev_load.sh` は
> Phase 8.2 で廃止されました（HTTP API ポート 9876 依存のため）。

### C. ライブで動かす（demo 口座）

> **TODO (Phase 5)**: replay → demo → prod の完全コマンド例を記載予定。
> 本セクションは `tools/lint/check_examples_readme.py`（受け入れ基準 #3 / #4）の
> 検証用見出しとして Phase 6 で先行起票したスタブです。

最小起動例（CLI、attach mode）:

```bash
# 別ターミナルで GUI を起動 → tachibana にログイン済の状態で:
uv run python -m engine.live_session_cli run \
    --strategy examples/test_strategy_minute.py \
    --instrument 8306.T \
    --max-qty 100 \
    --max-notional-jpy 500000 \
    --venue tachibana \
    --demo \
    --mode attach
```

詳細手順・第二暗証番号フロー・`TACHIBANA_ALLOW_PROD` ガード・
`is_market_open()` SoT は [`docs/specs/live-strategy.md §5`](../docs/specs/live-strategy.md)
を参照してください。

## test_strategy_daily.py の動作

デフォルトのパラメータ（`instrument_id=1301.TSE`, `lot_size=100`, `Daily` 足）で
2025-01-06〜2025-03-31（約 57 営業日）を実行すると、**初日に成行買い 100 株**を
1 回だけ発注し、以降は保有し続けます。

```
[BuyAndHoldStrategy] BuyAndHoldStrategy started: instrument=1301.TSE lot_size=100 ...
[BuyAndHoldStrategy] BUY: 100 shares @ XXXX
```

ターミナルのログ（debug ビルドは stdout）で確認できます。

## パラメータの渡し方

`strategy_init_kwargs` でコンストラクタ引数を上書きできます。CLI には
`--strategy-init-kwargs` フラグが無いので、Python helper か GUI フォームから
渡してください。

Python helper 経由:

```python
from engine.replay_session import ReplaySession

with ReplaySession() as s:
    s.load("1301.TSE", "2025-01-06", "2025-03-31")
    s.run(
        strategy_file="examples/test_strategy_daily.py",
        strategy_init_kwargs={"lot_size": 200},
        initial_cash=1_000_000,
    )
```

GUI フォーム経由: `File > Replay を開始...` の `strategy_init_kwargs` 欄に
`{"lot_size": 200}` を入力。

Minute 足で動かすには `bar_type_str` をキーワード引数で渡します。

```python
strategy_init_kwargs={"bar_type_str": "1301.TSE-1-MINUTE-LAST-EXTERNAL"}
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
s = load_strategy_from_file(Path('examples/test_strategy_daily.py'), {'instrument_id': '1301.TSE'})
print(type(s).__name__)
"
```

`BuyAndHoldStrategy` と表示されれば OK です。

## 依存ポリシー

- numpy / pandas / scikit-learn など追加依存は **増やさない方針**です
  （サンプルは標準ライブラリのみで書いてあります）
- AI/ML フレームワークは本体・SDK に同梱しません。機械学習を組み込む場合は
  ユーザー側で個別にインストールしてください
