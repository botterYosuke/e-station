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

### その他（実装フェーズで確定）

- バックテスト性能 SLA の具体値（spec.md §3.3 の「30 秒以内」を N1.7 実測で確定）
- nautilus の `MessageBus` を IPC イベントに 1:1 で写すか、要約するか
- ナラティブ `reasoning` テキストの自動生成: nautilus の `Strategy.log` を流用するか、ユーザーが明示的に書くか
