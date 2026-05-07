# アーキテクチャ

## 全体図

```
┌────────────────────────────────────────────────────────────────┐
│ ユーザー / エージェント開発者                                    │
└────────────────────────────────────────────────────────────────┘
                               │ Python スクリプト / Notebook
┌──────────────────────────────▼─────────────────────────────────┐
│ python/flowsurface/                                            │
│                                                                │
│  ┌──────────────────┐    ┌─────────────────────────────────┐  │
│  │ FlowsurfaceEnv   │    │ qlib_adapter.py（新規）          │  │
│  │ (Gymnasium)      │───▶│ ・obs → qlib DataFrame 変換      │  │
│  │ 既存             │    │ ・Alpha158 ハンドラ              │  │
│  └──────────────────┘    │ ・Model Zoo ラッパー             │  │
│                          │ ・SHAP/feature_importance 抽出   │  │
│  ┌──────────────────┐    │ ・reasoning 文字列ビルダ         │  │
│  │ narrative.py     │◀───│                                 │  │
│  │ 既存             │    └─────────────────────────────────┘  │
│  └────────┬─────────┘                                         │
└───────────┼────────────────────────────────────────────────────┘
            │ HTTP (9876) — 既存 API のみ使用、新エンドポイント不要
┌───────────▼────────────────────────────────────────────────────┐
│ Flow Surface (Rust) — 改修なし                                 │
│  ・/api/replay/state                                           │
│  ・/api/replay/order                                           │
│  ・/api/agent/narrative*                                       │
└────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                        ┌─────────────────┐
                        │ qlib (外部 lib)  │
                        │ ・Alpha158       │
                        │ ・LGBModel       │
                        │ ・DataHandler    │
                        └─────────────────┘
```

## レイヤー責務

### `python/flowsurface/qlib_adapter.py`

| 関数/クラス | 責務 |
|---|---|
| `to_qlib_dataframe(obs)` | `FlowsurfaceEnv` の observation（OHLCV list）を qlib が要求する MultiIndex DataFrame `(instrument, datetime)` に変換 |
| `Alpha158FromObs` | `qlib.contrib.data.handler.Alpha158` のサブクラス。Flow Surface 由来データを食べる |
| `QlibSignalAgent` | Alpha158 + LGBModel をラップし `predict(obs) -> action` と `explain() -> reasoning_dict` を提供 |
| `build_reasoning(model, features)` | feature_importance / SHAP の上位 N 個を「RSI_5 (+0.42), MA20 (-0.18)」のような文字列に変換 |

### Rust 側

**ゼロ改修**。既存の `/api/agent/narrative` と `/api/replay/*` で十分。

### データフロー

1. Python: `env.reset()` で OHLCV を取得
2. `to_qlib_dataframe(obs)` で qlib MultiIndex 形式に変換
3. `Alpha158FromObs.fetch()` で 158 次元の特徴量化
4. `LGBModel.predict()` でシグナル生成
5. `build_reasoning()` で寄与上位特徴量を文字列化
6. `env.step(action)` で仮想売買実行
7. `env.record_narrative(reasoning=..., confidence=...)` で Phase 4a の HTTP API に POST

## 依存関係

```
python/flowsurface/
├── pyproject.toml
│   └── [project.optional-dependencies]
│       qlib = [
│         "pyqlib>=0.9.5",
│         "lightgbm>=4.0",
│         "shap>=0.44",  # reasoning 生成用
│       ]
```

`pip install flowsurface-sdk[qlib]` の **オプション extras** とする。コア SDK には混ぜない。

## モジュール境界の原則

- **Rust 本体に qlib を取り込まない**: 「Python 単独モード」方針との整合性を保つため。
- **qlib_adapter.py は他モジュールに依存しない**: `narrative.py` / `env.py` から独立。`narrative.py` を import する向きは OK だが、逆向きは禁止。
- **qlib の YAML Workflow は使わない（Q1 段階）**: Python コードで直接組み立てる。Q2 以降に再評価。

## エラーハンドリング方針

- qlib のロード失敗（Alpha158 が銘柄不足等で空 DataFrame を返す等）は Flow Surface 側のリトライ対象外。**Python 側の例外として呼び出し元に伝搬**。
- ナラティブ記録失敗（HTTP エラー）は既存 `narrative.py` のリトライポリシに従う。
- LightGBM の警告は INFO ログに降格（criterion などの計測時のノイズ削減）。

## テスト戦略

| レベル | 内容 |
|---|---|
| Unit (Python) | `to_qlib_dataframe()` の境界値・空入力。`build_reasoning()` の出力フォーマット |
| Integration (Python) | モックの `FlowsurfaceEnv` から Alpha158 まで通す。LightGBM は最小データで学習 |
| E2E | `s60_qlib_narrative.py`（新規）— headless で Flow Surface を起動し、qlib エージェントで 1 ステップ → ナラティブが SQLite に保存されているか確認 |
| Rust 側 | 影響なし（既存テストのみ） |
