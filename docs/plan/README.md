# Flow Surface — AIエージェント向け戦略検証プラットフォーム化プラン

## ビジョン

> **「エージェントの意思決定を、人間が納得できる物語として可視化するプラットフォーム」**

TradingView が「人間同士のソーシャルチャート体験」を作ったように、Flow Surface は **「AIエージェント同士のソーシャルチャート体験」** を作る。

単なる自動化ツールではなく、ユーザーの分身であるエージェントが「なぜそう判断したか」という文脈を空間的に可視化し、ユーザーが納得感を持って意思決定できる **ナラティブな分析体験** を提供する。

---

## 競合ポジショニング

| | TradingView | ASI Alliance (Fetch.ai 等) | [Kraken Desktop](https://www.kraken.com/desktop) | Flow Surface |
|---|---|---|---|---|
| 対象ユーザー | 人間トレーダー | 開発者・機関投資家 | 個人トレーダー | 個人トレーダー |
| ソーシャル単位 | 人間 → 人間 | エージェント → エージェント（プロトコル） | 人間 → 人間 | エージェント → エージェント（体験） |
| 差別化軸 | チャートの民主化 | 効率・自動化 | 取引所統合・UX | **判断の可視化・納得感** |
| 弱点 | エージェント非対応 | UX がない | エージェント非対応 | 後発（2026年〜） |

**後発としての勝機**: ASI Alliance が「高速道路（インフラ）」を作っているなら、Flow Surface はその上を走る **「乗り心地の良い車と、車窓の景色の説明」** を作る。プロトコルで競わず、**ASI のインフラを活用しながら UX・ナラティブで勝つ**。

---

## ロードマップ概要

```
Phase 1  観測 API              ← エージェントが市場を「見る」          ✅ 完了（2026-04-17）
Phase 2  仮想売買エンジン        ← エージェントが「行動」し「報酬」を受け取る ✅ 完了（2026-04-17）
Phase 3  Python SDK             ← 強化学習サイクルを高速に回す          ✅ 完了（2026-04-21）
Phase 4a Agent ナラティブ基盤   ← 「なぜ判断したか」をローカルで記録・可視化 ✅ 完了（2026-04-21）
Phase 4b-1 Agent 専用 Replay API ← /api/agent/session/:id/* に型契約ありの API を分離 ✅ 完了（2026-04-22）
Phase 4b-2 ASI Alliance 統合     ← uAgents でナラティブをネットワーク越しに共有 ← 次のフェーズ
Phase 4c データマーケットプレイス ← Ocean Protocol で戦略データを資産化（任意）
```

Phase 1〜3 は **強化学習・バックテストの基盤**。  
Phase 4a〜4c が Flow Surface の **本質的な差別化**。

---

## Phase 1: 環境状態 (Observation) API の提供

現状のリプレイ HTTP API は操作系に特化しており、エージェントが自律的に分析するためのデータを提供していない。指定ペインの現在データを JSON で返すエンドポイントを追加する。

### [NEW] HTTP API エンドポイント

- `GET /api/replay/state`
  - 仮想時刻（`current_time`）における最新 OHLCV（Klines）・直近 Trades・スナップショットを返す
  - AIエージェントが「今どのような価格推移にあるか」を外から取得できるようになる

### 技術的な接続点

既存の `EventStore::klines_in()` と `trades_in()` をそのまま使用できる。配線のみで実装可能。

---

## Phase 2: アクション (Action) と報酬 (Reward) の基盤構築

AIが「買い」「売り」のアクションを行い、PnL として報酬を受け取るループを構築する。

### [NEW] Virtual Exchange Engine (Rust)

- リプレイモード専用モジュール
- `current_time` で発生した Trade イベントを参照し、エージェント注文の約定判定と PnL 計算を行う

### [NEW] HTTP API エンドポイント

- `POST /api/replay/order` — 仮想注文受付（成行・指値等）
- `GET /api/replay/portfolio` — ポジション・未実現/実現 PnL（Reward）の取得

---

## Phase 3: Python 環境・AI フレームワークとの統合 ✅ 完了

### Python SDK / Gymnasium ラッパー

Flowsurface を Headless（GUI なし・最高速）で起動し、Python から `env.step(action)` で HTTP API を叩いて強化学習サイクルを回せるようにする。

```python
env = FlowsurfaceEnv(headless=True, ticker="HyperliquidLinear:BTC", timeframe="M1")
obs, info = env.reset(start="2026-01-01 00:00", end="2026-03-31 23:59")

while not done:
    action = agent.predict(obs)          # {"side": "buy", "qty": 0.1}
    obs, reward, done, truncated, info = env.step(action)
```

**Rust 側実装（完了）:**
- `src/headless.rs` — HeadlessEngine + run()（22 ユニットテスト）
- `src/main.rs` — `--headless` フラグによる分岐（`iced` GUI をスキップ）
- コマンドライン引数: `--headless --ticker HyperliquidLinear:BTC --timeframe M1`

**E2E テスト（完了）:**
- 既存 18 本のテストスクリプトを `IS_HEADLESS=true/false` 両対応に改修
- CI: S1 / S3 / S27 headless ステップを `e2e.yml` に追加
- 詳細: `docs/plan/phase3_headless_e2e.md`

---

## Phase 4a: Agent ナラティブ基盤（ローカル） ✅ 完了

**実装計画・進捗**: [✅phase4a_narrative_foundation.md](✅phase4a_narrative_foundation.md)

### コンセプト

TradingView は「人間が書いた分析アイデア」を共有する場。  
Flow Surface は「エージェントの意思決定の物語」を共有する場。

エージェントが行動するたびに「なぜそう判断したか」をローカルに保存し、チャート上で可視化する。この **ナラティブの蓄積** が Phase 4b の ASI 統合の入力になる。

### 実装サマリ（完了分）

- **Rust モジュール**: `src/narrative/`（`model.rs` / `store.rs` / `snapshot_store.rs` / `service.rs` / `marker.rs`）
- **ストレージ分離**: メタは SQLite（`data_path()/narratives.db`）、`observation_snapshot` は gzip + sha256 付きで `narratives/snapshots/{yyyy}/{mm}/{dd}/{uuid}.json.gz` に保存
- **HTTP API 7 エンドポイント**: CRUD・スナップショット取得・storage stats・orphans 検出（§下表）
- **FillEvent 自動連携**: `linked_order_id` 経由で `outcome` を自動埋め込み（GUI 継続再生・StepForward・headless の 3 経路で配線）
- **チャートオーバーレイ**: `NarrativeMarker`（エントリー三角形 + エグジット矩形、リプレイモード限定）
- **Python SDK**: `python/narrative.py`（`fs.narrative.create/list/get/publish/unpublish/snapshot/storage_stats/orphans` + `FlowsurfaceEnv.record_narrative()`）
- **E2E テスト**: `s51_narrative_crud.py` / `s52_narrative_outcome_link.py` / `s53_narrative_snapshot_size.py` を CI の headless マトリクスに追加
- **保留**: S54（チャートオーバーレイの pixel-diff 検証）は Phase 4b 以降に送付。描画ロジックは `narrative::marker::tests` の 4 ユニットテストで担保

### ナラティブの構造

```json
{
  "agent_id": "user_A_agent_v3",
  "uagent_address": "agent1qt2uqhx...",
  "timestamp": 1704067200000,
  "ticker": "BTCUSDT",
  "timeframe": "1h",
  "observation_snapshot": {
    "ohlcv": [{ "t": 1704067200000, "o": 92100, "h": 92800, "l": 91900, "c": 92500, "v": 1234.5 }],
    "indicators": { "rsi_4h": 28.3, "volume_ratio": 1.42 }
  },
  "reasoning": "RSI divergence on 4h, volume confirmed above 1.4x average",
  "action": { "side": "buy", "qty": 0.1, "price": 92500 },
  "confidence": 0.76,
  "outcome": { "pnl": 0.023, "closed_at": 1704240000000 },
  "public": false
}
```

### HTTP API エンドポイント（実装済み）

| メソッド | パス | 用途 |
|---|---|---|
| `POST` | `/api/agent/narrative` | 判断ログを記録（`idempotency_key` 対応） |
| `GET` | `/api/agent/narratives` | 履歴一覧（`agent_id` / `ticker` / `since_ms` / `limit` フィルタ） |
| `GET` | `/api/agent/narrative/:id` | メタ単体取得 |
| `GET` | `/api/agent/narrative/:id/snapshot` | `observation_snapshot` 本体（gzip 解凍・sha256 検証） |
| `PATCH` | `/api/agent/narrative/:id` | `public` フラグ更新（Phase 4b で ASI 送信対象となる／取消も対応） |
| `GET` | `/api/agent/narratives/storage` | 総件数・総バイトサイズ・WARN 件数 |
| `GET` | `/api/agent/narratives/orphans` | 孤児スナップショット検出（削除はしない） |

> 親計画初版の `POST /api/agent/narrative/publish` は、REST 的整合性と `public: false` への取消対応を考慮し、実装では `PATCH /api/agent/narrative/:id` に一般化した。

### ナラティブの可視化（チャートオーバーレイ）

- エントリー/エグジットポイントのマーカー（buy=緑三角 / sell=赤三角 / エグジット=矩形）
- リプレイで「あの時エージェントは何を見ていたか」を時系列に追体験（リプレイモード限定描画）
- **Phase 4a スコープ外**: インジケーターのハイライト・マーカークリックでのポップアップ詳細表示（Phase 4b 以降）

---

## Phase 4b: ASI Alliance 統合（Agent-to-Agent ネットワーク）

### ASI Alliance の各コンポーネントと Flow Surface での役割

| コンポーネント | 提供するもの | Flow Surface での使い方 |
|---|---|---|
| **Fetch.ai / uAgents** | エージェント間通信プロトコル・エージェントアドレス | ナラティブの P2P 共有・エージェントの身元証明 |
| **Agentverse** | エージェントのホスティング・検索・接続基盤 | 自分のエージェントを登録・他のエージェントを探す |
| **DeltaV** | 自然言語でエージェントを呼び出すインターフェース | 「BTC のトレンド転換を得意とするエージェントを探して」 |
| **SingularityNET** | AI サービスのマーケットプレイス | 高品質な分析モデルを呼び出す（将来） |
| **Ocean Protocol** | データマーケットプレイス・データの資産化 | 戦略データの販売・購入（Phase 4c） |

### アーキテクチャ

```
┌──────────────────────────────────────────────────────┐
│  ASI Alliance ネットワーク                            │
│                                                      │
│  Agentverse                                          │
│  ├── AgentA (agent1qt2uqhx...)  ← ユーザーAの分身    │
│  ├── AgentB (agent1qw9rkzm...)  ← ユーザーBの分身    │
│  └── AgentC (agent1q3fvnp8...)  ← ユーザーCの分身    │
│       ↕ uAgents P2P メッセージ（ナラティブの共有）     │
└──────────────────┬───────────────────────────────────┘
                   │ uAgents Python SDK
┌──────────────────▼───────────────────────────────────┐
│  Flow Surface ローカル                                │
│                                                      │
│  ┌─────────────────────┐   ┌───────────────────────┐ │
│  │ FlowsurfaceEnv      │   │ uAgent ラッパー        │ │
│  │ (Gymnasium)         │   │ ・ナラティブの送受信   │ │
│  │ env.step(action)    │   │ ・公開/購読の管理      │ │
│  └──────────┬──────────┘   └───────────┬───────────┘ │
│             │ HTTP (port 9876)          │             │
│  ┌──────────▼──────────────────────────▼───────────┐ │
│  │  Flowsurface (Rust)  ──  Narrative Store        │ │
│  │  ・Virtual Exchange Engine                      │ │
│  │  ・Headless モード                               │ │
│  │  ・チャートオーバーレイ可視化                    │ │
│  └─────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### uAgents の統合方式

ユーザーのエージェントは Flow Surface のローカルプロセスと uAgent の **ブリッジ** として動作する。

```python
from uagents import Agent, Context, Model
from flowsurface_sdk import FlowsurfaceEnv, Narrative

class NarrativeMessage(Model):
    agent_id: str
    ticker: str
    timeframe: str
    reasoning: str
    action: dict
    confidence: float
    outcome: dict | None

# Flow Surface エージェントの uAgent ラッパー
fs_agent = Agent(
    name="my_flowsurface_agent",
    seed="my_secret_seed",          # agent1qt2uqhx... のアドレスが確定的に生成される
    port=8001,
    endpoint=["http://localhost:8001/submit"]
)

@fs_agent.on_interval(period=60.0)
async def trade_and_share(ctx: Context):
    env = FlowsurfaceEnv(headless=True, ticker="BTCUSDT")
    obs, _ = env.reset()
    action = my_model.predict(obs)
    obs, reward, done, info = env.step(action)

    narrative = Narrative.from_step(obs, action, reward, reasoning=my_model.explain())

    # ローカルに記録（常時）
    await narrative.save_local()

    # 公開設定なら ASI ネットワークに送信
    if narrative.should_publish:
        await ctx.send(FLOWSURFACE_FEED_AGENT_ADDRESS, NarrativeMessage(**narrative.to_dict()))
```

### フィードの仕組み（公開側と購読側）

**公開側（TradingView の Publish Idea 相当）:**
- ユーザーが Flow Surface UI で「このナラティブを公開」を選択
- uAgent が Agentverse 経由で `FlowSurfaceFeedAgent`（中継エージェント）に送信
- 受け取った FlowSurfaceFeedAgent がフォロワーのエージェントに配信

**購読側（TradingView の Follow 相当）:**
- ユーザーが他のエージェントを「フォロー」
- フォロー先のナラティブが Flow Surface の UI に表示
- リプレイ機能で他のエージェントの判断を自分のチャート上で再現できる

### プライバシーモデル

**TradingView モデルを採用**: 公開したい人だけ公開。

| スコープ | 内容 | ASI ネットワーク上 |
|---|---|---|
| Private（デフォルト） | ローカルのみ保存・自分だけ閲覧 | 送信しない |
| Public（任意） | ナラティブを公開・コミュニティに共有 | Agentverse に送信 |

---

## Phase 4c: データマーケットプレイス（Ocean Protocol 連携）

### コンセプト

「良い戦略を持つエージェントのナラティブ」を **データ資産として売買できる** ようにする。

| 機能 | 説明 |
|---|---|
| 戦略データの公開 | 自分のエージェントのバックテスト結果・ナラティブをデータセットとして Ocean Protocol に登録 |
| 戦略データの購入 | 他のエージェントのナラティブデータを OCEAN トークンで購入し、自分のエージェントの学習に利用 |
| 実績に基づく信頼スコア | バックテスト PnL・勝率・シャープレシオを Ocean データに付与し、購入前に確認できる |

---

## 技術スタック整理

```
Rust (Flowsurface 本体)
├── HTTP API (port 9876)           ← Phase 1〜4 のすべての I/O
├── Virtual Exchange Engine        ← Phase 2
├── Headless モード (--headless)   ← Phase 3
└── Narrative Store (SQLite)       ← Phase 4a

Python (外部)
├── FlowsurfaceEnv (Gymnasium)     ← Phase 3
├── uAgent ラッパー                ← Phase 4b（Fetch.ai uAgents SDK）
└── Agent 実装（ユーザーが自由に書く）

ASI Alliance
├── Fetch.ai uAgents               ← Phase 4b：A2A 通信・エージェントアドレス
├── Agentverse                     ← Phase 4b：エージェントの登録・検索
├── DeltaV                         ← Phase 4b：自然言語でエージェントを探す
├── SingularityNET                 ← 将来：外部 AI モデルの呼び出し
└── Ocean Protocol                 ← Phase 4c：戦略データの資産化・売買
```

---

## 実装優先順位と依存関係

```
Phase 1 → Phase 2 → Phase 3 → Phase 4a → Phase 4b → Phase 4c
  (観測)    (行動)    (高速化)   (ローカル)   (ASI統合)  (マーケット)
                                 ↑
                        Phase 4a が最小の価値提供単位
                        (ASI 統合なしでもナラティブ可視化は動く)
```

Phase 4a は **Phase 2 完了後に独立して着手可能**。ASI 統合（4b）は 4a のナラティブデータ構造が固まってから設計する。

---

## 決定済みの方針

- **エージェントの所在**: Flowsurface 外部（Python スクリプト等）から HTTP 経由で操作
- **プライバシーモデル**: 完全公開型（TradingView モデル）— 公開したい人だけ公開
- **競合戦略**: ASI Alliance のインフラ（uAgents / Agentverse）を活用しながら、UX・ナラティブ体験で差別化する
- **ASI Alliance の位置づけ**: 自社開発しない領域（A2A 通信プロトコル・エージェントアドレス・データマーケット）を ASI に委ねる。Flow Surface は UX・可視化・ナラティブ設計に集中する。

---

## Open Questions

1. ~~**Phase 1 の着手**~~ → ✅ 解決: Phase 1 実装完了（2026-04-17）。`GET /api/replay/state` は `src/replay_api.rs` で稼働中
2. ~~**ナラティブの共有範囲**~~ → ✅ 解決: Phase 4a（ローカル）を先行完了（2026-04-21）。Phase 4b（ASI 統合）で配信レイヤーを段階的に追加する方針で確定

### 次フェーズ（Phase 4b）着手前の未解決事項

- **uAgent ブリッジの配置**: Flow Surface 本体（Rust）に uAgents SDK を直接取り込むのではなく、Python 側（`python/flowsurface/`）で `uagents` クレートを使ってブリッジを書く想定で良いか
- **`FlowSurfaceFeedAgent`（中継エージェント）の所在**: Agentverse にホストするか、自前インフラで持つか
- **プライバシー制御の粒度**: 現状の `public: bool` を維持するか、より細かい公開範囲（フォロワー限定など）を導入するか
