# ❌ qlib 統合計画（ボツ）

**ステータス**: 不採用（2026-04-25）

Microsoft [qlib](https://github.com/microsoft/qlib) を Flow Surface に同梱する計画は不採用とした。本ディレクトリは検討経緯を残すための凍結アーカイブ。

---

## ボツ理由

### 結論

> **AI / ML フレームワークは本アプリに同梱しない。ユーザーが自分で用意する。**

Flow Surface は「エージェントが市場を観測し、行動し、ナラティブを残す」ための **基盤プラットフォーム** に徹する。脳の中身（特徴量エンジン・モデル・学習器）はユーザー側の領域とする。

### 不採用の根拠

1. **責務の分離**  
   Flow Surface の差別化軸は「ナラティブの可視化・納得感」（[親ロードマップ](../🔄ai_agent_platform_roadmap.md)）であり、特徴量生成やモデル学習ではない。qlib を同梱すると「自分で AI を選びたいユーザー」にとってノイズになる。TradingView がストラテジ言語（Pine Script）を提供しても、AI モデルそのものは同梱しないのと同じ思想。

2. **エージェントは外部から HTTP で繋ぐ前提**  
   親ロードマップで「エージェントの所在: Flowsurface 外部（Python スクリプト等）から HTTP 経由で操作」と決定済み。AI/ML ライブラリの選定もこの境界の外側に置くのが整合的。

3. **Python 単独モード方針との衝突回避**  
   将来予定の「Rust なし Python 単独モード」でも、Flow Surface 自体は AI を持たず、ユーザーが任意の Python AI スタックを使えるほうが自然。qlib を SDK extras であっても公式同梱すると「flowsurface = qlib エコシステム」という誤った印象を与える。

4. **依存サイズ・メンテ負担**  
   qlib は LightGBM / pyarrow / 場合により PyTorch を持ち込む。SDK の optional extras にしても、CI・ドキュメント・ユーザーサポート対象になり、保守コストが累積する。`scikit-learn` を選ぶユーザー、`PyTorch` 直書きするユーザー、`stable-baselines3` を使うユーザーをすべて差別なく扱うべき。

5. **代替が容易**  
   ユーザーは `pip install pyqlib` を自分で行い、`FlowsurfaceEnv` の observation を直接 qlib に渡せる。アダプタは「あれば便利」程度で、Flow Surface が公式に持つほどの価値はない。

---

## 当初の計画（参考・凍結）

| ファイル | 当時の内容 |
|---|---|
| [overview.md](overview.md) | qlib の正体・統合方針 A/B/C の比較 |
| [architecture.md](architecture.md) | Python SDK レイヤー (`qlib_adapter.py`) の設計 |
| [implementation-plan.md](implementation-plan.md) | Q0〜Q3 のフェーズ・タスク |
| [open-questions.md](open-questions.md) | 未解決事項 |

これらは検討の足跡として残すが、新規実装の参照元としない。

---

## 代替方針

ユーザー向けドキュメントで「お好みの AI フレームワークの繋ぎ方」をレシピ集として示す方針に切り替える。

| レシピ | 想定対象 |
|---|---|
| qlib + LightGBM | クオンツ研究志向ユーザー |
| stable-baselines3 (RL) | 強化学習志向ユーザー |
| scikit-learn / XGBoost | 古典 ML 志向ユーザー |
| PyTorch / Transformers | 自前モデル志向ユーザー |
| LLM ベース判断（Claude / GPT 等） | プロンプト志向ユーザー |

これらは **examples/ 配下のサンプルノートブック** として提供する想定であり、SDK 本体には依存を持ち込まない。具体的な計画化は別途行う。

---

## 派生する確定方針

- **Flow Surface SDK は AI/ML 依存ゼロ**（HTTP クライアント + 型定義 + ナラティブ記録ヘルパのみ）
- **AI モデルはユーザーの責任範囲**
- **`examples/` のサンプルは依存を `[examples-qlib]` 等の隔離 extras に切り出す**（CI でも分離）
