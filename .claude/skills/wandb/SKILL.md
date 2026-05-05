---
name: wandb
description: Flow Surface × Weights & Biases 統合ガイド。戦略実験のナラティブ記録・マルチエージェント比較・Sweep によるハイパーパラメータ探索の実装パターンを定義する。コア非汚染ルールと examples/wandb/ への閉じ込め方針も含む。
origin: flowsurface 向け W&B 統合パターン
---

# Flow Surface × Weights & Biases

## 位置づけ

**Flow Surface のコアは Narrative Store を唯一の真実とする。**  
W&B はユーザー戦略コードから呼ぶ外部ダッシュボードであり、
`python/engine/`・`src/`・`engine-client/` には一切 `import wandb` しない。

```text
[ W&B Dashboard ]
       ↑ wandb.log / wandb.Table / Artifact
[ ユーザー戦略コード ]            ← W&B はここから呼ぶ
       ↑ obs / reward / info
[ FlowsurfaceEnv (Gymnasium SDK) ]
       ↑ NautilusTrader IPC
[ Flow Surface コア (Narrative Store = 正データ) ]
```

---

## When to Use

このスキルを参照する状況：

- ユーザー戦略コードに W&B を組み込みたい
- バックテスト結果（Sharpe / PnL / Drawdown）を Dashboard に記録したい
- LLM の意思決定テキスト（Narrative）を可視化・比較したい
- 複数戦略を同じ市場スナップショットで比較したい
- ハイパーパラメータを Sweep で探索したい
- LLM 呼び出しのトレース・レイテンシ・コストを W&B Weave で記録したい
- `examples/wandb/` のレシピを追加・修正するとき

---

## セットアップ

```bash
# W&B インストール（ユーザー環境）
uv add wandb          # pyproject.toml に追加する場合
pip install wandb     # または pip で直接

# ログイン（初回のみ）
wandb login

# オフライン実行（閉域環境・立花証券ログデータを含む場合）
WANDB_MODE=offline uv run python my_strategy.py
# → 後で wandb sync ./wandb/ でアップロード可
```

---

## パターン 1: 最小実装（1 Run = 1 バックテスト）

```python
import wandb
from engine.replay_session import ReplaySession

wandb.init(
    project="flowsurface-strategies",
    config={
        "strategy":     "my_strategy_v1",
        "instrument":   "1301.TSE",
        "start_date":   "2025-01-06",
        "end_date":     "2025-03-31",
        "granularity":  "Daily",
        "initial_cash": 1_000_000,
    }
)

with ReplaySession() as session:
    session.load("1301.TSE", "2025-01-06", "2025-03-31")

    def on_event(evt):
        if evt.get("type") == "KlineUpdate":
            wandb.log({"close": evt["close"]})

    session.run(
        strategy_file="my_strategy.py",
        on_event=on_event,
    )

    result = session.result()
    wandb.summary.update({
        "total_pnl":    result["total_pnl"],
        "sharpe_ratio": result["sharpe_ratio"],
        "max_drawdown": result["max_drawdown"],
        "win_rate":     result["win_rate"],
        "total_trades": result["total_trades"],
    })

wandb.finish()
```

---

## パターン 2: Narrative を W&B Table に記録する

LLM の意思決定テキストを 1 決定 1 行で記録する。

```python
import wandb

narrative_table = wandb.Table(columns=[
    "timestamp", "close", "reasoning", "action", "confidence", "pnl_after"
])

# --- 戦略ループ内 ---
def on_narrative(evt):
    if evt.get("type") == "Narrative":
        narrative_table.add_data(
            evt["timestamp"],
            evt["close"],
            evt["reasoning"],   # LLM が出力したテキスト
            evt["action"],      # BUY / SELL / HOLD
            evt["confidence"],  # 0.0〜1.0
            evt["pnl_after"],
        )

# セッション終了後
wandb.log({"narratives": narrative_table})
```

Narrative の標準フィールド：

| フィールド | 型 | 説明 |
|---|---|---|
| `timestamp` | str | バー時刻（ISO8601） |
| `close` | float | 終値 |
| `reasoning` | str | LLM の意思決定テキスト |
| `action` | str | `BUY` / `SELL` / `HOLD` |
| `confidence` | float | 0.0〜1.0 |
| `pnl_after` | float | 約定後の実現損益 |

---

## パターン 3: マルチエージェント比較（Group）

同じ市場スナップショット上で複数戦略を比較する。
`group` に共通のスナップショット識別子を渡す。

```python
import wandb

# 戦略 A
wandb.init(
    project="flowsurface-strategies",
    group="snapshot-1301.TSE-2025-01-06",   # ← 全エージェント共通
    name="agent-llm-gpt4o",
    config={"model": "gpt-4o", "strategy": "llm_rsi"},
)
# ... バックテスト実行 ...
wandb.finish()

# 戦略 B（同じ group）
wandb.init(
    project="flowsurface-strategies",
    group="snapshot-1301.TSE-2025-01-06",
    name="agent-trend-follow",
    config={"strategy": "trend_follow_v2"},
)
# ... バックテスト実行 ...
wandb.finish()
```

Dashboard の group ビューで Sharpe / PnL / Drawdown が横並び表示される。
README の "Compare decisions, not just results" がここで実現する。

---

## パターン 4: Sweep によるハイパーパラメータ探索

Flow Surface の**決定論的 replay** × W&B Sweep = AutoML for Trading。

```yaml
# sweep.yaml
program: examples/wandb/sweep_runner.py
method: bayes
metric:
  name: sharpe_ratio
  goal: maximize
parameters:
  rsi_window:
    min: 5
    max: 30
  confidence_threshold:
    values: [0.5, 0.6, 0.7, 0.8, 0.9]
  stop_loss_pct:
    min: 0.01
    max: 0.05
```

```python
# examples/wandb/sweep_runner.py
import yaml
import wandb
from engine.replay_session import ReplaySession

def train():
    with wandb.init() as run:
        cfg = run.config

        with ReplaySession() as session:
            session.load("1301.TSE", "2025-01-06", "2025-03-31")
            session.run(
                strategy_file="my_strategy.py",
                strategy_params={
                    "rsi_window":           cfg.rsi_window,
                    "confidence_threshold": cfg.confidence_threshold,
                    "stop_loss_pct":        cfg.stop_loss_pct,
                },
            )
            result = session.result()
            wandb.log({
                "sharpe_ratio": result["sharpe_ratio"],
                "total_pnl":    result["total_pnl"],
            })

# wandb.sweep には dict を渡す（ファイルハンドルは不可）
with open("examples/wandb/sweep.yaml") as f:
    sweep_config = yaml.safe_load(f)

sweep_id = wandb.sweep(sweep_config, project="flowsurface-strategies")
wandb.agent(sweep_id, function=train)
```

```bash
# CLI から Sweep を起動する場合（Python コードなしで済む）
wandb sweep examples/wandb/sweep.yaml   # → sweep-id を出力
wandb agent <sweep-id>                  # エージェント起動（並列化可）
```

---

## パターン 5: W&B Weave で LLM 意思決定をトレースする

W&B Weave（`weave` パッケージ）は LLM の呼び出しを自動トレース・可視化するツール。
戦略の `reasoning` 生成部分に `@weave.op()` を付けるだけで、
入力・出力・レイテンシ・コストがすべて記録される。

```bash
uv add weave   # または pip install weave
```

```python
# examples/wandb/weave_strategy.py
import weave
import wandb
from engine.replay_session import ReplaySession

# weave.init で W&B プロジェクトと紐付け（wandb.init と別）
weave.init("flowsurface-strategies")

@weave.op()
def llm_decide(close: float, rsi: float) -> dict:
    """LLM による売買判断。入出力・レイテンシが自動記録される。"""
    import openai
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": f"close={close}, rsi={rsi}. BUY/SELL/HOLD?"}],
    )
    text = response.choices[0].message.content
    return {"action": text, "raw": text}

# 戦略ループ内で呼ぶだけでトレースが記録される
with ReplaySession() as session:
    session.load("1301.TSE", "2025-01-06", "2025-03-31")

    def on_event(evt):
        if evt.get("type") == "KlineUpdate":
            decision = llm_decide(evt["close"], evt.get("rsi", 50))
            # W&B Weave UI でこの呼び出しツリーが可視化される

    session.run(strategy_file="my_strategy.py", on_event=on_event)
```

Weave で記録される情報：

| 項目 | 内容 |
|-----|------|
| Inputs | `close`, `rsi` 等の引数 |
| Outputs | `action`, `raw` 等の戻り値 |
| Latency | LLM 呼び出し時間 |
| Cost | トークン数・推定コスト（OpenAI 等） |
| Trace tree | ネストした `@weave.op()` の呼び出し階層 |

> **コア非汚染**: `import weave` も `examples/wandb/` 内のみ許可。`python/engine/` には入れない。

---

## パターン 6: Artifact で戦略コードと結果を凍結する

バックテスト終了後に戦略コード・Narrative を一緒に保存する。

```python
import wandb

with wandb.init(project="flowsurface-strategies") as run:
    # ... バックテスト実行 ...

    # 戦略コードを Artifact として保存
    strategy_art = wandb.Artifact("strategy", type="strategy")
    strategy_art.add_file("my_strategy.py")
    run.log_artifact(strategy_art)

    # Narrative ログを Artifact として保存
    narrative_art = wandb.Artifact("narratives", type="dataset")
    narrative_art.add(narrative_table, "decisions")
    run.log_artifact(narrative_art)
```

「どのコードが・どのデータで・どう判断して・結果どうなったか」を
完全に再現できる状態で保存できる。

---

## コア非汚染ルール（必ず守ること）

| 場所 | `import wandb` | `import weave` | 理由 |
|---|---|---|---|
| `python/engine/` | **禁止** | **禁止** | IPC コア。外部依存を入れない |
| `src/` (Rust) | **禁止** | **禁止** | GUI コア。同上 |
| `python/engine/schemas.py` | **禁止** | **禁止** | IPC 契約。同上 |
| `examples/wandb/` | **ここのみ** | **ここのみ** | ユーザーレシピ置き場 |
| ユーザー戦略ファイル | 自由 | 自由 | ユーザー責任領域 |

`import wandb` / `import weave` がコアに漏れていないか確認：

```bash
grep -rn "import wandb\|import weave" python/engine/ src/ engine-client/
# → 結果が空であること
```

---

## examples/wandb/ の構成

```
examples/wandb/
├── README.md               # セットアップ手順 + パターン早見表
├── basic_run.py            # パターン 1（最小実装）
├── narrative_logging.py    # パターン 2（Narrative Table）
├── multi_agent_compare.py  # パターン 3（Group 比較）
├── sweep_runner.py         # パターン 4（Sweep 実行スクリプト）
├── sweep.yaml              # パターン 4（Sweep 定義）
├── weave_strategy.py       # パターン 5（Weave LLM トレース）
└── artifact_freeze.py      # パターン 6（Artifact 凍結）
```

新しいレシピを追加するときはここに閉じ込め、README.md にパターン一覧を追記する。

---

## オフライン実行（閉域環境・機密データ）

立花証券のログデータ等を W&B クラウドに上げたくない場合：

```bash
# オフラインで実行（ローカルに ./wandb/ 以下に保存）
WANDB_MODE=offline uv run python my_strategy.py

# 後からアップロード（任意）
wandb sync ./wandb/run-<timestamp>-<run-id>/
```

`WANDB_MODE=offline` 時もローカルでは全機能（Table / Artifact / Chart）が使える。

---

## 関連ドキュメント

| ファイル | 内容 |
|---------|------|
| [docs/plan/wandb-vision.md](../../../docs/plan/wandb-vision.md) | 完成図とアーキテクチャ全体像 |
| [examples/wandb/](../../../examples/wandb/) | 実装レシピ置き場 |
| [python/engine/replay_session.py](../../../python/engine/replay_session.py) | バックテスト駆動の正規ルート |
| [examples/test_strategy_daily.py](../../../examples/test_strategy_daily.py) | 最小戦略サンプル（Daily 足）|
| [examples/test_strategy_minute.py](../../../examples/test_strategy_minute.py) | 最小戦略サンプル（Minute 足）|
| [examples/test_strategy_trade.py](../../../examples/test_strategy_trade.py) | 最小戦略サンプル（Trade）|
