# 実装計画

親計画: [../🔄ai_agent_platform_roadmap.md](../🔄ai_agent_platform_roadmap.md)

## フェーズ分割

| Phase | 期間目安 | 目的 | Rust 改修 |
|---|---|---|---|
| Q0 PoC | 2〜3日 | qlib が Flow Surface のデータで動くことを Notebook で確認 | なし |
| Q1 アダプタ MVP | 1週間 | `qlib_adapter.py` を SDK に正式追加・E2E 1 本 | なし |
| Q2 ナラティブ統合 | 1週間 | reasoning 自動生成・SHAP 連携・デモコンテンツ | なし |
| Q3 ASI 連携準備 | Phase 4b 着手と並行 | qlib 製エージェントを uAgent ラッパーで公開 | なし（Phase 4b 側で必要分のみ） |

---

## Q0: PoC

**目的**: qlib が Flow Surface の OHLCV を食えるかの技術検証。

### タスク

- [ ] Q0.1 `pip install pyqlib` の動作確認（Windows / Linux）
- [ ] Q0.2 `examples/qlib_poc.ipynb` を作成
  - Flow Surface headless 起動 → `GET /api/replay/state` で OHLCV 取得
  - pandas DataFrame に整形 → qlib MultiIndex 形式へ変換
  - `Alpha158` ハンドラを通して 158 次元の特徴量が出ることを確認
- [ ] Q0.3 LightGBM で 1 銘柄 1 期間の最小学習が回ることを確認
- [ ] Q0.4 観察記録を `docs/plan/qlib/poc-notes.md` に残す（特に時間軸ミスマッチの問題）

### Exit 条件

- Notebook の全セルがエラーなく実行できる
- Alpha158 出力に NaN が想定範囲内（先頭数行は NaN でも OK）

---

## Q1: アダプタ MVP

**目的**: `python/flowsurface/qlib_adapter.py` を SDK の正式モジュールとして追加。

### タスク

- [ ] Q1.1 `pyproject.toml` に `[project.optional-dependencies] qlib` を追加
- [ ] Q1.2 `qlib_adapter.py` を実装
  - `to_qlib_dataframe(obs) -> pd.DataFrame`
  - `Alpha158FromObs` クラス
  - `QlibSignalAgent` クラス（`predict` / `explain`）
- [ ] Q1.3 ユニットテスト
  - `tests/test_qlib_adapter.py`
  - 境界値: 空 obs / 単一バー / 期間不足
- [ ] Q1.4 E2E テスト `tests/e2e/s60_qlib_narrative.py`
  - headless 起動 → qlib エージェントで 1 step → ナラティブが SQLite に保存
  - CI の headless マトリクスに追加（`extras=qlib` でインストール）
- [ ] Q1.5 README / SDK ドキュメント更新

### Exit 条件

- `pip install -e ".[qlib]"` 後に `from flowsurface.qlib_adapter import QlibSignalAgent` が通る
- E2E s60 が headless で green
- 既存 E2E に regression なし

---

## Q2: ナラティブ統合

**目的**: 「効いた alpha と寄与度」がナラティブの `reasoning` に自動で入ることでロードマップの差別化軸を満たす。

### タスク

- [ ] Q2.1 `build_reasoning(model, features)` 実装
  - LightGBM の `feature_importance` ベース版
  - SHAP 値ベース版（`shap` extra）
  - 出力: `"top features: RSI_5(+0.42), MA20(-0.18), VOL_RATIO(+0.11)"` のような文字列
- [ ] Q2.2 `QlibSignalAgent.act_and_record(env)` を追加
  - predict → step → explain → `env.record_narrative()` までを 1 メソッドで
- [ ] Q2.3 デモノートブック `examples/qlib_lgb_narrative.ipynb`
  - 1 期間学習 → 別期間でリプレイ実行 → チャートオーバーレイのスクショ
- [ ] Q2.4 ドキュメント
  - `docs/plan/qlib/narrative-recipe.md`（reasoning 生成のレシピ集）
- [ ] Q2.5 任意: `confidence` 値を LightGBM の予測スコアから算出するヘルパ

### Exit 条件

- デモノートブックでナラティブが Flow Surface のチャート上に可視化される
- `reasoning` 文字列に最低 3 個の特徴量が現れる

---

## Q3: ASI 連携準備（Phase 4b 並行）

**目的**: Phase 4b（ASI 統合）の uAgent ラッパーから `QlibSignalAgent` を呼べるようにする。

### タスク

- [ ] Q3.1 `QlibSignalAgent` を `uagents.Agent` の `on_interval` から呼び出すサンプル
- [ ] Q3.2 ナラティブ送信の `NarrativeMessage` ペイロードに `model_type: "qlib_lgb"` 等のメタを含めることを設計
- [ ] Q3.3 Phase 4b ドキュメントへリンク追加

### Exit 条件

- Phase 4b の最初のサンプル uAgent が qlib を使った構成になる

---

## スケジュール感

```
2026-04-25 ----▶ Q0 開始
2026-04-28 ----▶ Q0 完了 / Q1 開始
2026-05-05 ----▶ Q1 完了 / Q2 開始
2026-05-12 ----▶ Q2 完了
                Phase 4b 着手後に Q3 を合流
```

## 受け入れ条件（全体）

1. Rust 側のテストに regression なし（Phase 4a までの 22+ ユニットテスト・既存 E2E すべて green）
2. `pip install flowsurface-sdk` のみのユーザーには影響ゼロ（qlib は extras）
3. `examples/qlib_lgb_narrative.ipynb` で「学習 → リプレイ → ナラティブ可視化」が再現可能
4. ロードマップ Phase 4a の差別化軸（reasoning による納得感）が Q2 完了で **強化** されたことを文書で示せる

## リスクと緩和策

| リスク | 緩和策 |
|---|---|
| qlib の Windows 環境での導入失敗 | Q0 で早期に検証。失敗時は Linux / WSL2 を必須前提とする |
| 時間軸ミスマッチで Alpha158 の多くが意味をなさない | Q0 で観察記録 → Q1 で実用 alpha のサブセットを定義 |
| 依存サイズ増（PyTorch 等） | LightGBM のみを必須・PyTorch は二次 extras に分離 |
| 「Python 単独モード」方針との衝突 | Rust 側に取り込まない原則を architecture.md で明記済み |
| qlib のメンテ停滞 | アダプタ層を薄く保ち、将来 `polars` ベース等への移行余地を残す |
