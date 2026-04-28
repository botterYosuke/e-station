# nautilus_trader 統合: Open Questions

## N-pre ブロッカー（着手前に必ず resolve すること）

### Q1. nautilus のバージョン pin 戦略 ★Resolved (2026-04-26)

**決定: 二段階 pin 戦略**

- N0/N1 フェーズ: `>=1.211, <2.0` の SemVer 範囲 pin（開発中の柔軟性を確保）
- N2 完了後（立花実弾発注が通った構成）: `==1.225.x` 厳密 pin に切り替え

**根拠**:
- 検証時点（2026-04-26）の最新版は `1.225.0`（Windows wheel 取得済）
- 立花の発注往復が動いた後は壊れたくないため、N2 完了時に厳密 pin へ移行する
- `1.211` は `spec.md` の暫定値だが、実際の wheel は `1.225.0` が取得される。`>=1.211, <2.0` で現行最新を使う

**アクション完了**: `spec.md §5` を確定版に書き換え済み。

---

### Q3. `BacktestEngine` の clock 注入方式 ★Resolved (2026-04-26)

**決定: 案 B（BacktestEngine.run() 自走）を N0/N1 で採用**

`tests/spike/nautilus_clock_injection/spike_clock.py` で 3 案を検証した結果:

| 案 | 結果 | 詳細 |
|---|---|---|
| 案 B（run() 自走） | ✅ PASS | `run(start, end)` で完全自走。決定論性も確認済み |
| 案 A（streaming=True + clear_data()） | ✅ PASS | 1 Bar ずつ逐次投入でステップ実行が可能。将来の StepForward UX に使える |
| 案 A-2（advance_time() 外部制御） | ❌ FAIL | run() 中に `TestClock.advance_time()` を外部から呼ぶと Rust clock の非減少不変条件違反でパニック |

**N0/N1 の方針**:
- `BacktestEngine.run(start=range_start_ms, end=range_end_ms)` で自走させる
- `AdvanceClock` IPC Command は **実装しない**（Rust panic を引き起こすため）
- `StartEngine.config.range_start_ms / range_end_ms` のみで完結する設計

**将来の StepForward UX（N2 以降）**:
- `streaming=True` + `add_data([single_bar])` + `run(streaming=True)` + `clear_data()` サイクルで Bar 単位のステップ実行は技術的に可能
- IPC Command として `StepEngine { bars_to_advance: u32 }` を将来追加できる

**アクション完了**: `architecture.md §3` の `AdvanceClock` 条件書きを確定版（不採用）に書き換え済み。

---

### Q5. ライセンスの再配布形態 ★Resolved (2026-04-26)

**決定: venv 配布**

- PyInstaller one-binary 化は行わない（LGPL 追加対応不要）
- `pyproject.toml` の `[optional-dependencies] build = ["pyinstaller>=6.5"]` は build tool として残すが、nautilus を含む配布には使わない
- 配布は uv venv + `uv sync` で再現可能な仮想環境とする

**根拠**: Python 単独モード方針（memory: `project_python_only_mode.md`）と一貫し、venv 配布が最もシンプル。LGPL-3.0 の差し替え可能性確保の実装が不要になる。

**アクション完了**: Q5 即 Resolved。NOTICE 追加対応不要。

---

### Q6. 既存暗号資産 venue の発注経路（Rust 実装）はあるのか ★Resolved (2026-04-26)

**決定: Phase N3 は「新規実装」**

```
git grep -nE "(place_order|cancel_order|modify_order|submit_order)" exchange/src/
```
→ **0 hit**。`exchange/src/` には発注経路が存在しない（データ取得のみ）。

N3 で nautilus 側に `HyperliquidExecutionClient` 等を実装する作業は「移植」ではなく「新規実装」。Rust 側に削除すべき発注コードもない。

---

### Q7. 発注 UI の所在（iced vs Python） ★Resolved (2026-04-26)

**決定: 案 B（Python tkinter に発注 UI、iced は監視・表示のみ）**

**根拠**: Python 単独モード方針（memory: `project_python_only_mode.md`）と一貫する。iced から `POST /api/order/submit` を叩く必要はない。

- `POST /api/order/submit` 等の HTTP API は Rust 側で受けてもよいが、**発注入力 UI は Python tkinter に統一**
- iced は Portfolio/PnL/Chart 等の監視・表示のみを担う
- spec.md §4 の公開 API 表は Rust HTTP API 経路（iced から叩かれる前提）を残してよいが、「iced UI から直接呼ぶ想定」を「Python 発注 UI または監視ツールから呼ぶ想定」に修正する

**アクション完了**: spec.md §4 の備考欄を更新済み。

---

### Q8. 動的呼値テーブルと nautilus `Instrument.price_increment` ★Resolved (2026-04-26)

**決定: 案 A（`price_increment = Price(0.1, precision=1)` 固定）**

- N0〜N2 では `price_increment` を最小単位（0.1 円）で固定
- 実際の呼値丸めは `_compose_request_payload` の Python 写像層で行う
- nautilus 内部の `price_increment` 超過 reject は `RiskEngine` の `max_order_price` / `min_order_price` で吸収

**根拠**:
- 案 B（Instrument 複数切り替え）は nautilus 非標準で運用が困難
- 案 C（呼値テーブル動的反映の前倒し）は N0 スコープ外で工数過大
- 案 A は立花 Phase 1 の「Phase 2 以降」という既存方針と整合する

**アクション完了**: `data-mapping.md §3` に確定版を反映済み。

---

## 着手後に決めれば良い事項

### Q2. Strategy はユーザーが書くのか、組み込みか

N0〜N2 は **「組み込み Strategy のみ」**に制限（M3、spec.md §3.2）。`--strategy-file` によるユーザー Strategy ロードを許す場合、立花 creds が同居するプロセスへの任意コード実行になる。信頼境界の設計（別プロセス実行・credential 非同居・サンドボックス）が必要。

- 案 A: ユーザーが Python ファイルを書く（別プロセスで隔離実行、creds を渡さない）
- 案 B: 内蔵 "Manual Trading Strategy"（UI クリックを `OrderFactory` に流す bridge）だけ提供
- 案 C: 両方

**アクション**: N2 完了後に設計し、security review を通す。

### Q4. nautilus persistence の扱い（長時間ライブ運用）

spec.md §3.2 で `CacheConfig.database = None`（ディスク永続化 OFF）に確定。long-running ライブ運用での再起動復元は `CLMOrderList` から毎回 warm-up（N2.3 で実装）。

Parquet キャッシュを有効化したい場合は、立花の session 情報・約定履歴を nautilus 側ファイルに二重保存しない前提で別途検討（N3 以降）。

### Q9. J-Quants 分足 Bar の `ts_event` 揃え方 ★Resolved (2026-04-28)

**決定: 案 B（bar close 時刻に揃える）**

- `equities_bars_minute_*.csv.gz` の `Time="09:00"` は **`09:00:59.999999999` UTC ns（JST 09:00 の分の終わり）**を `ts_event` とする
- `equities_bars_daily_*` の `Date` は **15:30:00 JST**（大引け時刻）に揃える（N0 互換）

**根拠**:
- nautilus 公式 docs で `Bar.ts_event` は **closing time** を推奨。`time_bars_timestamp_on_close=True` が既定値
  - https://nautilustrader.io/docs/latest/concepts/data/
  - https://nautilustrader.io/docs/latest/concepts/backtesting/
- tick から `BarAggregator` で生成する Bar との整合が取れる（live で BarAggregator 経由 / replay で直接ロードのどちらでも `ts_event` の意味が同じ）

**アクション**: N1.2 で `data-mapping.md §2.1` の `ts_event` 行を確定版に更新（本書反映済み）。

---

### Q10. replay モードの `lot_size` をどう取るか ★Resolved (2026-04-28)

**決定: 案 B + 案 A fallback**

優先順位:
1. **立花の銘柄マスタが利用可能ならそれを使う**（live モードで取得済みの `sHikaku` をローカルキャッシュ → replay モードから読む）
2. キャッシュがない銘柄に当たったら **`100` を fallback** として使い、`log.warning` で「Tachibana master cache miss → fallback lot_size=100」を出す
3. ユーザーは起動 config で `lot_size_override: {instrument_id: int}` を渡してケース個別に上書きできる

**根拠**:
- 現コードに立花マスタ由来の `lot_size` 抽出 ([instrument_factory.py:18](../../../python/engine/nautilus/instrument_factory.py#L18) / [tachibana.py:380](../../../python/engine/exchanges/tachibana.py#L380)) があり再利用しやすい
- J-Quants `listed/info` API には売買単位フィールドがない（https://jpx.gitbook.io/j-quants-en/api-reference/listed_info）
- 一部 ETF / REIT は 1 株単位など 100 以外。100 固定では普通株以外で誤発注リスク
  - https://www.jpx.co.jp/equities/products/etfs/trading/
  - https://www.jpx.co.jp/equities/products/reits/trading/index.html

**アクション**:
- N1.2 で `python/engine/nautilus/instrument_cache.py` 新設: live モードで取得した `sHikaku` を `~/.cache/flowsurface/instrument_master.json` に永続化
- replay モードはこのキャッシュを `instrument_factory.make_equity_instrument()` から優先参照
- `data-mapping.md §3` を確定版に更新（本書反映済み）

---

### Q11-pre. 立花 live モードの曖昧 side が `"buy"` 寄せになっている ★Bug to fix first (2026-04-28 新設)

**先行修正タスク（Q11 着手前ブロッカー）**

[`tachibana_ws.py:190`](../../../python/engine/exchanges/tachibana_ws.py#L190) の `_determine_side` は曖昧時に `return "buy"` しており、live 側の trade に **buy bias** が乗る。replay は `NO_AGGRESSOR` 固定なので、live/replay の互換性検証で false positive が出る。

**修正方針**:
- `_determine_side` の戻り値を `str | None` に変更
- 曖昧時（quote rule も tick rule も決まらない）は `None` を返す
- 呼び出し側 ([tachibana_ws.py:156](../../../python/engine/exchanges/tachibana_ws.py#L156) 付近) で `None` を `"unknown"` などに写像し、`tachibana_data.py`（N2.0）で `AggressorSide.NO_AGGRESSOR` に変換
- 既存テスト: 曖昧時に `"buy"` を期待していたら `None` 期待に書き換える（[bug-postmortem](../../../.claude/skills/bug-postmortem/SKILL.md) で見逃しパターン記録）

**アクション**:
- N1.0（または N0 のホットフィックス枠）で先行修正
- 完了後に Q11 を Resolved にする

---

### Q11. live と replay の `aggressor_side` 差分の戦略影響 ★Resolved (2026-04-28、Q11-pre 完了が前提)

**決定: 案 B（lint WARNING + ドキュメント誘導）**

- N1.8 の lint で `tick.aggressor_side` 参照を AST 検出 → **WARNING**（fail にしない）
- 開発者向け docs（`docs/wiki/strategy-authoring.md`、N1.8 で新設）に以下を強く書く:
  - **live は推定値**で曖昧時 `NO_AGGRESSOR`（Q11-pre 修正後）
  - **replay は J-Quants 仕様により常に `NO_AGGRESSOR`**
  - nautilus の `SimulatedExchange` の fill 判定は `aggressor_side` を参照する箇所がある（ https://nautilustrader.io/docs/latest/concepts/backtesting/ ）。`NO_AGGRESSOR` は約定挙動が中立になるので、`aggressor_side` を意思決定の主要因子にする戦略は live/replay でズレる
  - 推奨: `aggressor_side` は分析・可視化の補助情報に限る。意思決定には `price` / `size` のみ使う

**不採用案**:
- 案 A（参照禁止）: デバッグ表示・ログ集計でも使えなくなり厳しすぎる
- 案 C（`ImputedSide` ラッパ提供）: nautilus 標準型から外れて互換性損失。N3 以降の検討に回す

**アクション**:
- Q11-pre のホットフィックスを先に出す
- N1.8 で lint WARNING + `docs/wiki/strategy-authoring.md` 新設
- `spec.md §3.5.3` の「`aggressor_side` 依存戦略は live で動作・replay で挙動変化」を「**両モードで使用非推奨**」に格上げ（本書反映済み）

---

### その他（実装フェーズで確定）

- バックテスト性能 SLA の具体値（spec.md §3.3 を N1.10 実測で確定）
- nautilus の `MessageBus` を IPC イベントに 1:1 で写すか、要約するか
- ナラティブ `reasoning` テキストの自動生成方針
