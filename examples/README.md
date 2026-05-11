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

> ⚠️ **必ず replay → demo → prod の順で動かしてください**。バグによる誤発注は
> ユーザー責任です（リポジトリ直下 `README.md` の「戦略は自己責任」セクション参照）。

各 example の `LIVE_SCENARIO` 定数（`schema_version=1` / `instrument` / `max_qty` /
`max_notional_jpy` / `venue`）は GUI フォームの prefill 用で、CLI 経路では
`--instrument` / `--max-qty` / `--max-notional-jpy` / `--venue` 引数が SoT です。
LIVE_SCENARIO がない戦略でも CLI / GUI ともに手入力で起動できます。

#### 同じ戦略ファイルを replay → demo → prod で動かす完全コマンド例

`examples/test_strategy_minute.py` を例に、3 段階の起動フローを示します。
`--mode inprocess` は CLI 単独で engine を直起動するモードで、`--mode attach` は
別ターミナルで先に GUI（`cargo run -- --mode live`）を立ち上げて attach するモード、
`--mode auto` は attach probe → fallback inprocess の自動切替です。
本サンプルでは挙動が予測しやすい `inprocess` を例示します。

##### Linux / macOS（bash / zsh）

```sh
# 1. replay で十分検証（headless / inprocess、GUI 不要）
uv run python -m engine.replay_session run \
    --strategy examples/test_strategy_minute.py \
    --instrument 1301.TSE \
    --start 2025-01-06 \
    --end 2025-01-10 \
    --granularity Minute \
    --mode inprocess

# 2. demo 口座で起動（第二暗証番号は stdin で渡す。env / argv 平文は非推奨）
echo "$DEV_TACHIBANA_SECOND_PASSWORD" | uv run python -m engine.live_session_cli run \
    --strategy examples/test_strategy_minute.py \
    --instrument 1301.TSE \
    --max-qty 100 \
    --max-notional-jpy 500000 \
    --venue tachibana \
    --demo \
    --mode inprocess \
    --second-password-stdin

# 3. 本番（要 TACHIBANA_ALLOW_PROD=1 + engine プロセス再起動）
TACHIBANA_ALLOW_PROD=1 uv run python -m engine.live_session_cli run \
    --strategy examples/test_strategy_minute.py \
    --instrument 1301.TSE \
    --max-qty 100 \
    --max-notional-jpy 500000 \
    --venue tachibana \
    --prod \
    --mode inprocess \
    --second-password-stdin
```

##### Windows（PowerShell 7+）

```powershell
# 1. replay
uv run python -m engine.replay_session run `
    --strategy examples/test_strategy_minute.py `
    --instrument 1301.TSE `
    --start 2025-01-06 `
    --end 2025-01-10 `
    --granularity Minute `
    --mode inprocess

# 2. demo 口座（PowerShell の echo は Write-Output。here-string で trailing CRLF 対策）
$env:DEV_TACHIBANA_SECOND_PASSWORD | uv run python -m engine.live_session_cli run `
    --strategy examples/test_strategy_minute.py `
    --instrument 1301.TSE `
    --max-qty 100 `
    --max-notional-jpy 500000 `
    --venue tachibana `
    --demo `
    --mode inprocess `
    --second-password-stdin

# 3. 本番（PowerShell では env は別行で設定し、その session でのみ有効）
$env:TACHIBANA_ALLOW_PROD = "1"
$env:DEV_TACHIBANA_SECOND_PASSWORD | uv run python -m engine.live_session_cli run `
    --strategy examples/test_strategy_minute.py `
    --instrument 1301.TSE `
    --max-qty 100 `
    --max-notional-jpy 500000 `
    --venue tachibana `
    --prod `
    --mode inprocess `
    --second-password-stdin
```

`test_strategy_daily.py` / `test_strategy_trade.py` でも `--strategy` を差し替えれば
同じ 3 段階のフローで動かせます（`--granularity` は replay でのみ指定）。

> **GUI と組み合わせる場合**: 別ターミナルで先に `cargo run -- --mode live` を
> 起動し、attach 経由で同じ engine プロセスに接続するなら `--mode attach`（または
> `--mode auto`）を使ってください。attach mode では第二暗証番号は wire に流さず、
> engine 側 SessionHolder で事前設定済みの前提です（受け入れ基準 #20、
> [`docs/specs/live-strategy.md §3.2-D.1`](../docs/specs/live-strategy.md)）。

#### kabu_station venue でも同じ戦略を起動可能

Phase 4 で `kabu_station` venue の `supports_live_strategy=True` にフリップ済み
（capability 経由）。`--venue kabu_station` を指定するだけで切り替わります。
戦略ファイル側は `LIVE_SCENARIO['venue']` を `"kabu_station"` に変えるとフォームの
prefill も連動します（`test_strategy_minute.py` のコメント例を参照）。

```sh
# kabu_station demo（bash / zsh）
echo "$DEV_TACHIBANA_SECOND_PASSWORD" | uv run python -m engine.live_session_cli run \
    --strategy examples/test_strategy_minute.py \
    --instrument 1301.TSE \
    --max-qty 100 \
    --max-notional-jpy 500000 \
    --venue kabu_station \
    --demo \
    --mode inprocess \
    --second-password-stdin
```

> **注意**: kabu_station venue は kabuステーション本体プロセスがローカルで
> 動作している必要があります（`localhost:18081` 検証 / `localhost:18080` 本番）。
> 詳細は [`docs/skills/kabusapi`](../docs/skills/kabusapi) と
> [`docs/specs/live-strategy.md §3.2`](../docs/specs/live-strategy.md) を参照。

#### 安全装置（demo → prod 移行で必ず確認）

| # | 安全装置 | 効果 |
|---|---------|------|
| 1 | `--max-qty` 必須（1 ≤ n ≤ 10000） | 1 注文あたりの最大株数を engine が validator で reject |
| 2 | `--max-notional-jpy` 必須（1 ≤ n ≤ 100_000_000） | 1 注文あたりの最大金額（円）を engine が validator で reject |
| 3 | `--prod` は `TACHIBANA_ALLOW_PROD=1` env と AND 条件 | env が無いと `--prod` を CLI が即 reject。GUI も capability `is_production` で disable |
| 4 | `is_market_open()` 認可 reject | engine 側が `start_live` 冒頭で確認、閉場時間帯は `EngineError{code:"market_closed"}` で abort |
| 5 | `SecondPasswordRequired` フロー | 第二暗証番号未設定で発注しようとした時点で stderr に固定文言「第二暗証番号を設定してください」。CLI exit code = 3 |
| 6 | warm_up 失敗で `exec_client.close()` | 例外 OR `False` 戻り値の OR で abort、必ず接続を閉じる（受け入れ基準 #14） |

`--prod` を AND ガードに反する形で叩くと CLI は `argparse.error` で即 reject します:

```sh
$ uv run python -m engine.live_session_cli run --strategy ... --prod ...
python -m engine.live_session_cli: error: --prod requires TACHIBANA_ALLOW_PROD=1
environment variable. Refusing to start live engine on production venue.
```

第二暗証番号は `--second-password-stdin`（推奨）または `DEV_TACHIBANA_SECOND_PASSWORD`
env を使い、`--second-password` 平文引数は **非推奨**（shell history / `ps` /
Windows タスクマネージャに露出する）です。詳細は
[`docs/specs/live-strategy.md §3.2-D.1`](../docs/specs/live-strategy.md) を参照。

#### GUI 経路（`File > Open` → 戦略選択 → ライブで開始）

iced GUI を `--mode live` で起動し、`File > Open...` から戦略 `.py` を選ぶと
`LiveStrategyFormModal` が開きます。

```sh
# GUI を起動（事前に tachibana にログイン）
cargo run -- --mode live
```

1. `File > Open...` から `examples/test_strategy_minute.py` を選択
2. engine が `LoadLiveStrategyScenario` を投げて `LIVE_SCENARIO` 値で
   `instrument_id` / `max_qty` / `max_notional_jpy` / `venue` を **自動 prefill**
   （受け入れ基準 #13）。LIVE_SCENARIO 不在時は手入力で続行
3. 「ライブで開始」ボタンで engine に `StartEngine{engine: "Live"}` を送信
4. `LiveStrategyReady` 受信で 4 ペイン（CandlestickChart / TimeAndSales / OrderList /
   BuyingPower / Positions）が **自動生成**（受け入れ基準 #11 / #17、冪等 key
   `(strategy_id, instrument_id, venue)`）

`prod` モードに切り替えるには engine プロセスを `TACHIBANA_ALLOW_PROD=1` 付きで
**再起動**してください（GUI からは env を触れない設計、統一決定 #14）。
capability `is_production` が `true` になると GUI フォームの `prod` チェックボックスが
有効化されます（false なら disabled で固定文言「TACHIBANA_ALLOW_PROD env が
未設定です（engine 再起動が必要）」を表示）。

#### CLI と GUI の対称性（参考）

CLI 経路と GUI 経路は engine 側で同じ `StartEngine{engine: "Live"}` に集約されます。
GUI 側の `LiveStrategyFormModal` は capability 経由で venue を切り替え、CLI 側は
`--venue` で同じ wire 値を送ります。

詳細手順・第二暗証番号フロー・`TACHIBANA_ALLOW_PROD` ガード・
`is_market_open()` SoT は [`docs/specs/live-strategy.md §5`](../docs/specs/live-strategy.md)
と [ADR 0071 / 0072](../docs/decisions/) を参照してください。

> **注意**: `examples/live_sample.py` は **発注しない最小ティック・ロガー**で、
> CLI 経路の例ではありません。本番用 CLI 起動には上記 `python -m engine.live_session_cli`
> を使ってください（live_sample.py は自前 `main()` でログだけ出すサンプルです）。

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
