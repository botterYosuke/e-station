# Flow Surface — AutoML for Trading (Execution & Verification Layer)

## Vision

> **“We don’t build trading AI. We build the environment where trading AI competes.”**

Flow Surface is a **deterministic execution and verification platform** for AI trading strategies.

It does not provide AI models.
It provides the **ground-truth environment** where strategies are executed, compared, and evolved.

Flow Surface separates **execution** from **experimentation**.
Execution is handled by NautilusTrader.
Experimentation is handled by Flow Surface.

---

## What is this?

Flow Surface is an **AutoML for Trading infrastructure**:

* Run multiple AI-driven strategies on the **same market snapshot**
* Compare decisions under **identical conditions**
* Replay and verify outcomes with **full reproducibility**
* Iterate strategies via **code (Python), not UI**

👉 This is not a trading bot.
👉 This is not a signal provider.
👉 This is a **research and experimentation platform**.

---

## Philosophy

Flow Surface **does not include any AI model**.

Instead, it provides:

* Deterministic market replay
* Virtual execution engine
* Structured decision logging (narratives)
* Reproducible evaluation loop

Users are expected to:

* Write strategies in Python
* Use any AI model (LLM / RL / ML / rules)
* Iterate in VSCode or CLI

> Flow Surface is the **execution layer** beneath your AI.

---

## System Architecture

```text
[ AI Model (LLM / RL / ML) ]
            ↑
[ Python Strategy Code (VSCode) ]
            ↑
[ FlowsurfaceEnv (Gymnasium SDK) ]
            ↑
[ Flow Surface (Rust Core) ]
 ├── Replay Engine (deterministic)
 ├── Virtual Exchange (execution)
 ├── Narrative Store (decision logs)
 └── Chart Visualization
```

---

## Core Capabilities

### 1. Deterministic Replay

* Historical market data is replayed with a controllable clock
* All strategies observe the **exact same state**

👉 Enables fair comparison

---

### 2. Structured Decision Logging (Narratives)

Every action records:

* Market snapshot (OHLCV, indicators)
* Reasoning
* Action (buy/sell/hold)
* Confidence
* Outcome (PnL)

👉 No hindsight bias
👉 Fully auditable decisions

---

### 3. Multi-Agent Comparison

Multiple strategies can run on the same timeline:

```text
Snapshot T

Agent A → BUY (RSI)
Agent B → SELL (Trend)
Agent C → HOLD (Volatility)
```

👉 Compare decisions, not just results

---

### 4. Replay-Based Verification

* Reproduce exact decisions at any timestamp
* Inspect what each strategy “saw”

👉 Debug strategies, not just evaluate them

---

### 5. Evaluation Loop

Strategies are evaluated with:

* PnL
* Sharpe Ratio
* Drawdown

👉 **Results, not opinions**

---

## Execution Engine

Flow Surface does not implement a trading engine from scratch.

Instead, it delegates execution to a specialized engine:

* NautilusTrader

### Responsibility Split

| Layer                           | Responsibility |
| ------------------------------- | -------------- |
| AI / Strategy                   | User           |
| Execution / Orders / PnL        | NautilusTrader |
| Replay / Narrative / Comparison | Flow Surface   |

---

### Design Principle

> NautilusTrader is the execution engine.
> Flow Surface is the experimentation layer.

This separation allows:

* high-performance, production-grade execution
* research-to-live consistency
* focus on strategy evolution, not infrastructure

## Workflow (Recommended)

```text
1. Write strategy in Python (VSCode)
2. Plug into FlowsurfaceEnv
3. Run simulation (headless)
4. Inspect narratives + replay
5. Compare against other strategies
6. Iterate
```

Example:

```python
env = FlowsurfaceEnv(headless=True, ticker="BTCUSDT", timeframe="1m")

obs, info = env.reset()

while True:
    action = agent.predict(obs)
    obs, reward, done, truncated, info = env.step(action)

    if done:
        break
```

---

## Use Cases

### Strategy Development

* Build and test AI-driven strategies
* Iterate with full reproducibility

---

### Strategy Debugging

* Identify *why* a strategy failed
* Inspect incorrect assumptions

---

### Multi-Agent Benchmarking

* Compare strategies under identical conditions
* Find dominant approaches

---

### Research / Experimentation

* Test LLM reasoning vs statistical models
* Explore hybrid approaches

---

## Positioning

Flow Surface is:

* Not a trading platform
* Not a signal generator
* Not an AI model provider

It is:

> **A reproducible experimentation environment for trading strategies**

Comparable to:

* Gymnasium (RL environments)
* Backtesting engines
* Quant research infrastructure

But with:

* Structured reasoning logs
* Multi-agent comparison
* Replay-driven verification

---

## Roadmap (Relevant)

* Phase 1–3: Core execution + SDK ✅
* Phase 4a: Narrative logging + visualization ✅
* Phase 4b: Multi-agent synchronization (in progress)
* Phase 4c: Strategy evolution + marketplace (future)

## 実装トラック詳細（立花証券 e支店 統合）

| フェーズ | 内容 | 状態 |
|---|---|---|
| **Tachibana Phase 1** | 認証・マーケットデータ受信（FD/EC）・気配 | ✅ 完了 |
| **Order Phase O-pre** | IPC 型定義・スキーマ凍結 | ✅ 完了 |
| **Order Phase O0** | 第二暗証番号 iced modal・現物成行買・WAL 冪等再送 | ✅ 完了 |
| **Order Phase O1** | 訂正・取消・注文一覧 | ✅ 完了 |
| **Order Phase O2** | EVENT EC 約定通知・重複検知 | ✅ 完了 |
| **Order Phase O3** | 信用・逆指値・期日指定・余力 API | ✅ 完了 |
| **Rust UI U-pre〜U3** | Order Entry / Order List / Buying Power パネル | ✅ 完了（scaffold） |

計画ドキュメント: [docs/✅order/](✅order/)

---

## Design Principles

* Deterministic over stochastic
* Reproducibility over convenience
* Code-first over UI-first
* Evaluation over opinion

---

## Key Concept

Flow Surface enables:

> **AutoML for Trading**

Where strategies:

* Compete
* Are evaluated
* Are iterated
* And ultimately **evolve**

---

## Getting Started (Conceptual)

1. Clone repository
2. Start Flow Surface (headless or GUI)
3. Write Python strategy
4. Connect via SDK
5. Run and analyze

(Detailed setup instructions coming soon)

---

## Final Note

> Trading automation is solved.
> Strategy evolution is not.

Flow Surface exists to solve the latter.
