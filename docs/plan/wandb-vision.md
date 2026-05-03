# Flow Surface × Weights & Biases — 完成図

## 概念的な位置づけ

```text
[ W&B Dashboard (ユーザー視点) ]
         ↑ wandb.log / wandb.Table / Artifact
[ Python Strategy Code (ユーザー作成) ]
         ↑ obs / reward / narrative
[ FlowsurfaceEnv (Gymnasium SDK) ]
         ↑ NautilusTrader IPC
[ Flow Surface (Rust Core) ]
 ├── Replay Engine (deterministic)
 ├── Virtual Exchange (execution)
 ├── Narrative Store (決定ログ ← 正データ)
 └── Chart Visualization
```

**Flow Surface のコアは Narrative Store を唯一の真実とする。**
W&B はユーザーの戦略開発ループを支援する「外部ダッシュボード」であり、
コア（`python/engine/` / `src/`）には一切依存しない。

---

## W&B で何を記録するか

### 1 Run = 1 バックテストセッション

```python
import wandb
wandb.init(
    project="flowsurface-strategies",
    config={
        "strategy":    "buy_and_hold_v3",
        "instrument":  "1301.TSE",
        "start_date":  "2025-01-06",
        "end_date":    "2025-03-31",
        "granularity": "Daily",
        "initial_cash": 1_000_000,
        "data_hash":   env.info["data_hash"],   # 再現性の証明
    }
)
```

`data_hash` を config に固定することで、「同じ Run → 同じ市場スナップショット」
が保証される。Flow Surface の決定論的 replay と W&B の再現性が合わさる。

---

### 2 Narrative を W&B Table に流す

Flow Surface が生成する Narrative の構造体：

| フィールド | 型 | 説明 |
|---|---|---|
| `timestamp` | str | バー時刻（ISO8601） |
| `open/high/low/close/volume` | float | OHLCV スナップショット |
| `indicators` | dict | RSI / MA 等、AI が渡した特徴量 |
| `reasoning` | str | LLM が出力した意思決定テキスト |
| `action` | str | `BUY` / `SELL` / `HOLD` |
| `confidence` | float | 0.0〜1.0 |
| `pnl_after` | float | 約定後の実現損益 |

```python
narrative_table = wandb.Table(columns=[
    "timestamp", "close", "reasoning", "action", "confidence", "pnl_after"
])

obs, info = env.reset()
while True:
    action, reasoning, confidence = agent.decide(obs)
    obs, reward, done, _, info = env.step(action)

    narrative_table.add_data(
        info["timestamp"], obs["close"],
        reasoning, action, confidence,
        info["pnl_after"],
    )
    wandb.log({"pnl": info["pnl_after"], "reward": reward})
    if done:
        break

wandb.log({"narratives": narrative_table})
```

---

### 3 評価指標をまとめてログ

```python
wandb.summary.update({
    "total_pnl":     info["total_pnl"],
    "sharpe_ratio":  info["sharpe_ratio"],
    "max_drawdown":  info["max_drawdown"],
    "win_rate":      info["win_rate"],
    "total_trades":  info["total_trades"],
})
wandb.finish()
```

---

## マルチエージェント比較（README Phase 4b）

複数戦略を同じタイムラインで走らせるときは W&B の **Group** を使う：

```python
wandb.init(
    project="flowsurface-strategies",
    group="snapshot-2025-01-06",   # ← 同じ市場スナップショット
    name="agent-llm-gpt4o",
)
```

```text
W&B Dashboard: group = snapshot-2025-01-06
├── agent-llm-gpt4o    : Sharpe 1.42 / PnL +123,000
├── agent-rsi-rules    : Sharpe 0.87 / PnL  +67,000
└── agent-trend-follow : Sharpe 1.01 / PnL  +89,000
```

「同じ条件・異なる戦略の比較」が一画面で見える。
README の "Compare decisions, not just results" をそのまま実現する。

---

## Sweep による戦略ハイパーパラメータ探索

```yaml
# sweep.yaml
program: strategy_runner.py
method: bayes
metric:
  name: sharpe_ratio
  goal: maximize
parameters:
  rsi_window:
    min: 5
    max: 30
  confidence_threshold:
    min: 0.5
    max: 0.95
```

```bash
wandb sweep sweep.yaml
wandb agent <sweep-id>
```

Flow Surface の決定論的 replay × W&B Sweep = **AutoML for Trading**。
README が掲げるビジョンの具体的な姿がこれ。

---

## Artifact でモデルと Narrative を紐づける

```python
# 戦略コードを Artifact として保存
strategy_artifact = wandb.Artifact("strategy-buy-and-hold-v3", type="strategy")
strategy_artifact.add_file("buy_and_hold_v3.py")
run.log_artifact(strategy_artifact)

# Narrative ログを Artifact として保存
narrative_artifact = wandb.Artifact("narratives-2025-q1", type="dataset")
narrative_artifact.add(narrative_table, "decisions")
run.log_artifact(narrative_artifact)
```

戦略コード・Narrative・評価指標が一つの Run に紐づき、
任意の時点に「どのコードが・どのデータで・どう判断して・結果どうなったか」を
完全再現できる。

---

## 実装上の境界線（コア非汚染ルール）

| 場所 | W&B 依存 | 理由 |
|---|---|---|
| `python/engine/` | **禁止** | IPC コア。外部依存を入れない |
| `src/` (Rust) | **禁止** | GUI コア。同上 |
| `python/engine/schemas.py` | **禁止** | IPC 契約。同上 |
| `examples/wandb/` | **ここのみ** | ユーザーのレシピ置き場 |
| ユーザー戦略ファイル | 自由 | ユーザー責任領域 |

`import wandb` は `examples/wandb/` より外に出さない。
これにより "Flow Surface 本体は W&B なしで動く" が常に成立する。

---

## examples/wandb/ の構成案

```
examples/wandb/
├── README.md               # このビジョンの要約 + セットアップ手順
├── basic_run.py            # 最小実装（narrative_table + summary）
├── multi_agent_compare.py  # Group を使ったマルチエージェント比較
├── sweep_rsi_params.py     # Sweep によるパラメータ探索
└── sweep.yaml              # Sweep 定義
```

---

## ユーザーが得るもの

```text
1. 戦略を書く（VSCode）
2. FlowsurfaceEnv に繋ぐ
3. W&B でナラティブを可視化・比較
4. Sweep で最良パラメータを探索
5. Artifact でコード + 結果を凍結
6. 次の戦略へ iterate
```

> Flow Surface は実行の地盤。W&B は進化の記録簿。
> ユーザーの AI はその間で育つ。
