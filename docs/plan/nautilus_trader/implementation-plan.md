# nautilus_trader 統合: 実装計画

## マイルストーン一覧

| Phase | ゴール | 依存 |
|---|---|---|
| **N-pre** | feasibility / 配布形態 / pin 戦略 / 既存 Rust 発注経路の有無を確定（実装ゼロ）| python-data-engine 完了済 |
| N0 | nautilus 同梱・サンプル戦略 headless で PnL が出る（**Bar ベース MVP**）| N-pre |
| **N1** | **J-Quants → TradeTick → BacktestEngine の replay モード成立**、`/api/replay/*` を nautilus で実装、Gymnasium env が回る、REPLAY 仮想注文を SimulatedExchange に流す、`POST /api/agent/narrative` を新設、live/replay 互換 lint | N0、order/ Phase O0 完了 |
| N2 | 立花 `LiveExecutionClient` adapter（デモ）で実弾相当の発注往復が通る、**立花 FD frame → TradeTick の LiveDataClient** | N1、tachibana Phase 1 完了（T7 受け入れ緑）、order/ Phase O0〜O2 完了 |
| N3 | 暗号資産 venue を nautilus 側に新規実装、Rust 発注コード撤去 | N2 |

**Phase N1 の依存補正（C2）**: 旧版で「Phase 2（観測 API）完了済」を依存に挙げていたが、その「Phase 2」は本計画 N1 で置換・破棄する対象（[architecture.md §6](./architecture.md#6-既存計画との衝突点と整理)）。本計画では N1 の依存を「N0 + order/ Phase O0」に書き換え、自作 Virtual Exchange Engine の完成を依存条件にしない。

## Phase N-pre: feasibility と前提固め（実装ゼロ）

[spec.md §2.0](./spec.md#20-phase-n-pre--feasibility-確認と前提固め実装ゼロ) に対応するタスク列。

### Tpre.1 clock 注入 feasibility プロトタイプ（H4）✅ 完了 2026-04-26
- [x] `tests/spike/nautilus_clock_injection/` に捨てコード spike を作る
- [x] 案 A-2（外部 clock 駆動・`AdvanceClock` Command）: `TestClock.advance_time()` を `run(streaming=True)` と組み合わせると Rust clock 非減少不変条件違反でパニック → **実装不可**
- [x] 案 A（streaming=True + 1 Bar ずつ逐次投入）: `add_data([bar]) + run(streaming=True) + clear_data()` サイクルで動作。将来の StepForward UX に使える
- [x] 案 B（`BacktestEngine.run()` 自走）: 動作確認済み、決定論性も検証済み → **N0/N1 で採用**
- [x] 結果を [architecture.md §3](./architecture.md#3-新規-ipc-メッセージ) と [open-questions.md Q3](./open-questions.md#q3) に追記して resolve

### Tpre.2 nautilus_trader バージョン pin 確定（H6 / Q1）✅ 完了 2026-04-26
- [x] [open-questions.md Q1](./open-questions.md#q1) を resolve: **二段階 pin**（N0/N1: `>=1.211, <2.0`、N2 完了後: `==1.225.x` 厳密 pin）
- [x] [spec.md §5](./spec.md#5-依存方針) を確定版に書き換え済み

### Tpre.3 配布形態と LGPL-3.0（M8 / Q5）✅ 完了 2026-04-26
- [x] [open-questions.md Q5](./open-questions.md#q5) を resolve: **venv 配布** → LGPL 追加対応不要
- [x] PyInstaller 同梱なし。`[optional-dependencies] build` は build tool として残すだけ

### Tpre.4 既存 Rust 発注経路の有無確認（L1 / Q6）✅ 完了 2026-04-26
- [x] `git grep -nE "(place_order|cancel_order|modify_order|submit_order)" exchange/src/` → **0 hit**
- [x] N3 は「新規実装」にラベル変更
- [x] [open-questions.md Q6](./open-questions.md#q6) を resolve

### Tpre.5 動的呼値テーブル方針（C6 / 新規 Q8）✅ 完了 2026-04-26
- [x] [open-questions.md Q8](./open-questions.md#q8) を resolve: **案 A**（`price_increment = Price(0.1, precision=1)` 固定）
- [x] 結論を [data-mapping.md §3](./data-mapping.md#3-instrument-価格帯と呼値テーブル) に反映

### Tpre.6 発注 UI の所在統一（L7 / Q7）✅ 完了 2026-04-26
- [x] [open-questions.md Q7](./open-questions.md#q7) を resolve: **案 B**（Python tkinter に発注 UI 統一、iced は監視・表示のみ）
- [x] [spec.md §4](./spec.md#4-公開-api不変条件) に Q7 決定の備考を追記済み

### Tpre.7 wheel 入手性確認✅ 完了 2026-04-26
- [x] Windows 11 で `uv pip install nautilus_trader` → `nautilus-trader==1.225.0` wheel 取得成功
- [ ] macOS arm64 / Linux x86_64 は未検証（venv 配布決定で Q5 への影響なし。将来検証時に確認）

**Exit 条件**: Tpre.1〜Tpre.7 すべて DONE、open-questions.md の Q1/Q3/Q5/Q6/Q7/Q8 が `Resolved` ラベル付きで spec / architecture / data-mapping に反映済み。✅ **N-pre 完了 2026-04-26**

---

## Phase N0: 同梱と最小バックテスト ✅ 完了 2026-04-26

### N0.1 依存追加 ✅
- [x] `pyproject.toml` に `nautilus-trader>=1.211,<2.0` を追加
- [x] `uv lock` で `uv.lock` を更新（1.225.0 解決済み）
- [ ] CI（`.github/workflows/`）で `uv sync` 後の import スモークテストを 1 本追加（N1 で実施）

### N0.2 ワーカー骨格 ✅
- [x] `python/engine/nautilus/__init__.py`
- [x] `python/engine/nautilus/engine_runner.py`（`NautilusRunner`: `start_backtest()` / `start_live()` stub / `stop()`）
- [x] `python/engine/nautilus/instrument_factory.py`（`make_equity_instrument()`: price_increment=0.1 固定 Q8 案 A）
- [ ] `python/engine/server.py` の `StartEngine` ハンドラ追加（N1.1 で実施。N0 は直接 API 呼び出しのみ）
- [x] N0 では live execution を呼ばない（`start_live()` は stub）

### N0.3 EventStore → nautilus DataLoader ✅
- [x] `python/engine/nautilus/data_loader.py`（`klines_to_bars()`: KlineRow → Bar、JST 15:30 UTC 変換）
- [x] `python/tests/test_nautilus_data_loader.py`（7 件 GREEN）

### N0.4 サンプル戦略 ✅
- [x] `python/engine/nautilus/strategies/buy_and_hold.py`（最初のバーで 1 lot 成行買い）
- [x] `python/tests/test_nautilus_buy_and_hold.py`（3 件 GREEN: 全バー処理・equity 正・bought=True）
- [x] `--strategy-file` 経路は実装しない（M3、Q2 解決まで組み込み Strategy のみ）

### N0.5 headless スモーク ✅
- [x] `python/tests/test_nautilus_smoke.py`（5 件 GREEN: start_backtest 完走・戻り値・equity・strategy_id・stub）
- [ ] 既存 E2E への `s60_nautilus_backtest_smoke.py` 追加（N1 で実施）

### N0.6 決定論性テスト ✅
- [x] `python/tests/test_nautilus_determinism.py` — 同一データ 2 回実行で equity / fill_timestamps ビット一致
- [x] wall clock 独立性テスト（`time.time` / `time.monotonic` をモックしても結果不変）を同ファイルに統合
- [x] 3 件 GREEN

**Exit 条件達成**: スモーク + 決定論性テスト 計 8 件 GREEN (2026-04-26)

### レビュー反映 (2026-04-26, ラウンド R1)

#### 解消した指摘
- ✅ C-1: `_collect_fill_timestamps` に `log.warning` 追加、意図的フォールバックをコメントで明示
- ✅ C-2: `start_backtest` に try/finally 追加し dispose 漏れ解消
- ✅ H-1: `KlineRow.__post_init__` バリデーション追加 + `frozen=True` 化
- ✅ H-2: `engine.run()` の前後にログ追加
- ✅ H-3: `currency` サイレントフォールバックを `ValueError` に変更
- ✅ H-4: `BacktestResult.fill_last_prices` 追加 + 決定論性テスト `last_price` ビット一致・非空チェック
- ✅ M-1: `portfolio.account` の None チェック追加
- ✅ M-2: `test_wall_clock_independence` に `datetime.now` モックを追加
- ✅ M-3: `strategy_id` 二層構造を docstring に明記
- ✅ M-4: `test_data_mapping_instrument.py` を新規作成 (6 件)
- ✅ M-5: `instrument_factory.py` の `ts_event=0` 仮置きを docstring に明記
- ✅ M-6: `on_bar` の `instrument is None` に `log.warning` 追加

#### 繰越 (次フェーズ検討)
- H-5 (CI workflow): N1 着手前に `.github/workflows/python-test.yml` を追加すること（HIGH）
- strategy_id の `Literal` or `Enum` 化: N1 IPC schema 実装時に合わせて型を確定する
- `_date_to_ts_ns` の float 丸め誤差: N1 で nautilus 公式の timestamp_ns API に切り替え（テスト側の期待値も整数演算に統一）
- data-mapping.md のテストパス (`tests/python/` → `python/tests/`) 修正: docs-only fix
- `stop()` の N1 以降 asyncio lock 化: IPC ディスパッチャから並行呼出しされる場合に必要

### レビュー反映 (2026-04-26, ラウンド R2)

#### 解消した指摘
- ✅ R2-MEDIUM-1 (SFH): `test_wall_clock_independence` の `datetime.datetime` パッチを削除（nautilus 内部干渉リスク解消）
- ✅ R2-M-A (GPT): `test_two_runs_same_last_prices` に `assert len(fill_last_prices) > 0` 追加（偽陽性防止）
- ✅ R2-M-B (GPT): `test_data_mapping_instrument.py` の `pytest.approx` を `str(price_increment) == "0.1"` に変更（Decimal 厳密比較）

#### 知見
- `_collect_fill_data` の `avg_px` フィールドは nautilus 1.225.0 で正常動作を確認（非空テストが PASS）
- `fill_last_prices` の偽陽性ガードを `test_two_runs_same_last_prices` に入れることで、`avg_px` 属性名変更を将来のバージョンアップで即検知できる

---

## Phase N1: TradeTick 抽象 + J-Quants ローダ + REPLAY 仮想注文 + ナラティブ API

**スコープの中心は「J-Quants 過去歩み値で BacktestEngine を回すこと」**。これにより replay モードが実用化する。Strategy インタフェースは TradeTick 一本に統一し、live/replay 互換性を CI で守る。

**PR 切り方の規約**: N1.1〜N1.3（IPC schema + J-Quants loader + replay API 差し替え）は **1 PR でアトミック**にマージする。互換シムを残さない。

### N1.0 ホットフィックス: 立花曖昧 side → `None` 化（Q11-pre）⭐ 先行修正 ✅ 完了 2026-04-28
- [x] ✅ [`tachibana_ws.py:190`](../../../python/engine/exchanges/tachibana_ws.py#L190) `_determine_side` 戻り値を `str | None` に変更し、曖昧時 `None` を返す
- [x] ✅ 呼出側（[tachibana_ws.py:156](../../../python/engine/exchanges/tachibana_ws.py#L156) 付近）で `None` を内部表現の `"unknown"` に写像（既存 trade dict のキー互換は維持）
- [x] ✅ 既存テスト `python/tests/test_tachibana_fd_trade.py::test_tick_rule_up_gives_buy` の曖昧 side 期待値を `"buy"` → `"unknown"` に書き換え
- [x] ✅ [bug-postmortem](../../../.claude/skills/bug-postmortem/SKILL.md) を起動し MISSES.md に「曖昧 side が `"buy"` 寄せ → live/replay 互換性で false positive」を記録（2026-04-28 エントリ、教訓 3 点）
- [ ] N2.0 の `tachibana_data.py` 実装時に `"unknown"` → `AggressorSide.NO_AGGRESSOR` に写像（**N2 で実施**）

#### 状況・知見・Tips（2026-04-28 R0 完了報告）

**状況**: tachibana_ws.py / test_tachibana_fd_trade.py / MISSES.md の 3 ファイルを更新し commit 済み。test_tachibana_fd_trade.py 19 件全 GREEN。リグレッション確認: 旧実装（`return "buy"`）に戻すと `test_tick_rule_up_gives_buy` が `assert 'buy' == 'unknown'` で FAIL することを `git stash` で実証済み。

**新たな知見**:
- 「現状動作 pin」型のバグは、テストコメントに「default buy」のように **誤った仕様前提が文章化されている** ことで識別しやすい。テストコメント内の "default" / "fallback" / "current behavior" などのキーワードは仕様の正しさが検証されていない可能性のシグナルとして使える。
- `_determine_side` 戻り値の型変更（`str` → `str | None`）は呼出箇所が 1 箇所だったため安全に伝搬したが、複数箇所ある関数の戻り値型変更時は grep で全箇所を確認するルールが必要。

**設計思想と背景**:
- 曖昧時に `None` を内部表現として返し、呼出側で `"unknown"` 文字列に写像する二段構成を採用。理由: (a) `_determine_side` の責務は side 推定のみで、推定不能を `Optional` で素直に表現する、(b) trade dict の `side` キーは下流の集計ロジック（live/replay 互換）が文字列を期待するため `"unknown"` センチネル文字列で表現する、(c) N2.0 で `AggressorSide.NO_AGGRESSOR` 写像時に `"unknown"` を switch するのが直感的。
- 却下案: `_determine_side` が直接 `"unknown"` を返す → 集計側の責務分離が崩れる、`Literal["buy", "sell", "unknown"]` enum 化 → N1.0 のスコープを超える型整理になる。

**Tips**:
- `git stash push -- <path>` で特定ファイルだけ stash → リグレッション実証 → `git stash pop` で復元、というパターンは TDD 事後検証に有効。本セッションでも N1.0 の事後検証で活用。

### N1.1 IPC schema 1.4 ✅ 完了 2026-04-28
- [x] ✅ [engine-client/src/dto.rs](../../../engine-client/src/dto.rs) に追加（[architecture.md §3](./architecture.md#3-新規-ipc-メッセージ)）:
  - `Command::StartEngine` / `StopEngine`
  - `Command::LoadReplayData { instrument_id, start_date, end_date, granularity }`
  - `EngineEvent::EngineStarted` / `EngineStopped`
  - `EngineEvent::ReplayDataLoaded { bars_loaded, trades_loaded }`
  - `EngineEvent::PositionOpened` / `PositionClosed`（venue / instrument_id / 文字列精度）
- [ ] **`Order*` 系は order/ Phase O-pre PR マージ確認後**に着手（本タスク外、N2 で対応）
- [x] ✅ `schema_minor` を `4` に上げる。Ready capabilities に `nautilus.backtest=true, nautilus.live=false` を載せる
- [x] ✅ `cargo test -p flowsurface-engine-client` で既存テスト緑（44+12 件）、Python 側 `python/engine/schemas.py` も同期し `test_schemas_nautilus.py` 15 件 GREEN

#### 状況・知見・Tips（2026-04-28 R1 完了報告 — N1.1）

**状況**: dto.rs / schemas.py / lib.rs / server.py / `engine-client/tests/schema_v2_4_nautilus.rs` (12 件) / `python/tests/test_schemas_nautilus.py` (15 件) を追加・更新。`schema_v2_1_roundtrip.rs` は schema が前進したため削除。`cargo test --workspace` 全緑、`uv run pytest python/tests/` 986 passed / 2 skipped。`cargo clippy --workspace -- -D warnings` / `cargo fmt --check` も clean。

**新たな知見**:
- architecture.md は schema を 1.4 と書いていたが、実コードは N0 までに 2.x 系に bump 済みだった。**ドキュメントの version 表記は「論理 / 仕様番号」、実コードは「累積 minor 番号」と乖離しがち** — 本タスクでは実コードを正とし `SCHEMA_MAJOR=2`, `SCHEMA_MINOR=4` を採用。architecture.md の更新は別タスクで分離。
- pydantic v2 の `Literal["Trade", "Minute", "Daily"]` は orjson roundtrip でそのまま enum-string になる。Rust 側 `enum ReplayGranularity { Trade, Minute, Daily }` は serde default で PascalCase → `"Trade"` などになり、Python と wire 表現が一致するのが偶然便利。
- `Hello` に新フィールド (`mode`) を追加するときは `connect()` シグネチャ変更で全テストが破綻する。**old API を `connect()` に残し、`connect_with_mode()` を新設** することで pre-N1.13 テストの書き換えを最小化できる（後方互換ラッパパターン）。

**設計思想と背景**:
- `EngineKind` / `ReplayGranularity` は Rust enum + Python `Literal` で表現を冗長定義した。理由: (a) Rust 側で型安全な variant マッチが必要、(b) Python 側で `extra="forbid"` + `Literal` による拒否を効かせて IPC 契約を強制、(c) string-on-wire のため Pascal-case を両言語で一致させる必要がある。
- `Position*` イベントの `realized_pnl` / `avg_open_price` を `String` に統一: nautilus 内部で `Decimal` を使っており f64 round-trip での桁落ちを避けるため（既存 `BuyingPowerUpdated` は `i64` 円整数だが、PnL は小数点を扱うため文字列が安全）。
- `EngineStartConfig` を独立 struct に切り出した: 将来 `LoadReplayData` 以外のロード経路や config preset を追加するときに、`StartEngine` の引数列を破壊せず拡張できる。
- 旧 `schema_v2_1_roundtrip.rs::schema_minor_is_2_for_buying_power` を削除（互換シムを残さない PR 切り方規約に従う）。`get_buying_power_serializes` 等の機能テストは `BuyingPowerUpdated` deserialize テストとして十分カバーされているため別ファイル化は不要と判断。

**Tips**:
- `cargo test -p flowsurface-engine-client --test schema_v2_4_nautilus` で新規ファイルだけ走らせると RED→GREEN サイクルが 1 秒で回る。dto.rs を編集すると workspace 全体ビルドが入って遅くなるので、IPC dto を試行錯誤するときは新規テストファイルから先に書くと体感速度が大きく違う。

### N1.2 J-Quants ローダ + Instrument cache 実装 ⭐ replay モードの中核 ✅ 完了 2026-04-28
- [x] ✅ `python/engine/nautilus/jquants_loader.py` 新設（[data-mapping.md §1.3 / §8](./data-mapping.md#13-replay-j-quants-equities_trades_csvgz--tradetick)）
  - [x] ✅ `jquants_code_to_instrument_id(code)`: `"13010"` → `"1301.TSE"`、末尾非 0 で `ValueError`
  - [x] ✅ `load_trades(instrument_id, start_date, end_date) -> Iterator[TradeTick]`: `S:\j-quants\equities_trades_*.csv.gz` を gzip stream で順次読み、銘柄・期間でフィルタ
  - [x] ✅ `load_minute_bars(...)`: bar `ts_event` を **close 時刻**に揃える（Q9）
  - [x] ✅ `load_daily_bars(...)`: 同上、JST 15:30 で揃える
  - [x] ✅ 全関数: メモリ全量展開しない iterator 設計
- [x] ✅ `python/engine/nautilus/instrument_cache.py` 新設（Q10 案 B + fallback A）
  - [x] ✅ live モードで取得した `sHikaku` を `~/.cache/flowsurface/instrument_master.json` に永続化
  - [x] ✅ `get_lot_size(instrument_id) -> int`: cache hit ならそれを返す、miss なら `100` + `log.warning`
  - [x] ✅ `instrument_factory.make_equity_instrument()` から優先参照
  - [x] ✅ 起動 config の `lot_size_override` を最優先で適用
- [x] ✅ `python/tests/test_jquants_loader.py`:
  - [x] ✅ InstrumentId 写像（正常 / 末尾非 0 raise / 長さ違反 raise）
  - [x] ✅ マイクロ秒精度 timestamp 復元
  - [x] ✅ `aggressor_side == NO_AGGRESSOR`
  - [x] ✅ 銘柄フィルタ・期間フィルタ
  - [x] ✅ 月境界をまたぐ期間で複数ファイル開ける
- [x] ✅ テスト用フィクスチャ: 小さい CSV を `python/tests/fixtures/equities_*.csv.gz` に配置（実 J-Quants ファイルは CI に持ち込まない。各 200B 程度）

#### 状況・知見・Tips（2026-04-28 R2 完了報告 — N1.2）

**状況**:
- 新規ファイル: `python/engine/nautilus/jquants_loader.py`, `python/engine/nautilus/instrument_cache.py`, `python/tests/test_jquants_loader.py` (15 件), `python/tests/test_instrument_cache.py` (7 件), `python/tests/fixtures/{equities_trades_202401,equities_trades_202402,equities_bars_minute_202401,equities_bars_daily_202401}.csv.gz` (4 件・各 ~200B), `python/tests/fixtures/_build_jquants_fixtures.py` (再生成スクリプト)
- 更新ファイル: `python/engine/nautilus/instrument_factory.py`（cache 連携 + `lot_size_override` 引数追加）、`python/tests/test_data_mapping_instrument.py`（lot_size resolution 3 件追加）
- テスト結果: `uv run pytest python/tests/` で **832 passed / 2 skipped / 1 warning**（既存 813 + 新規 22 + factory 拡張 3 = 838 構成、N0 互換テストの破壊なし）
- 副次検証: `cargo build --workspace` 成功（IPC schema は本タスクで不変）

**新たな知見**:
- **J-Quants `equities_bars_minute_*` は月次 (YYYYMM) ファイル**だった。data-mapping.md §8.1 では "YYYYMMDD 日次" と記述されていたが、実態（`S:\j-quants\equities_bars_minute_202401.csv.gz` 等）は monthly。本タスクで data-mapping.md §8.1 / §8.2 / §8.4 を実態に合わせて訂正済み。
- daily bars CSV の実カラムは `Date,Code,O,H,L,C,UL,LL,Vo,Va,AdjFactor` の 11 列で、data-mapping.md §2.2 / §8.4 に未記載の `UL`（値幅制限フラグ上）/ `LL`（同下）/ `AdjFactor`（調整係数、株式分割等）が含まれる。N1 ローダは `O/H/L/C/Vo` のみ参照し、追加 3 列は無視。N3 以降で `AdjFactor` を使った補正が必要になる可能性あり（オープン課題候補）。
- `dt.datetime.timestamp()` は float なので `* 1_000_000_000` を直接掛けると ns 末尾で float 誤差が出る。**μs 整数化（`int(t.timestamp() * 1_000_000)` → `* 1000`）**することで `09:00:00.165806` のようなマイクロ秒精度を ns で正確に復元できる。テスト `test_microsecond_precision_ts_event` で確認。
- `csv.reader` を gzip stream の `gzip.open(path, "rt", newline="")` と組み合わせると、Excel 風 CRLF 改行も問題なく処理できる。`newline=""` を忘れると Windows では空行が混入することがある（フィクスチャ書き込み側でも `newline=""` を指定）。

**設計思想と背景**:
- **InstrumentCache を独立モジュールに切り出した理由**: live モード（立花 `sHikaku` 取得）と replay モード（JSON 読込）がライフサイクル不一致で動く。前者は network I/O 後に書き込む、後者は常に読込側。`instrument_factory` 内に閉じ込めると live 側 (`tachibana.py`) からの逆参照が必要になり循環気味。`InstrumentCache.shared()` シングルトンで両側から疎結合に参照する。
- **TradeTick / Bar を generator (Iterator) で返す設計**: 1 銘柄 1 ヶ月の trade tick は数十万行に達する。`list` で返すと replay 起動時にメモリスパイクが起きる。`yield` ベースで `BacktestEngine.add_data(...)` に直接流し込むことで RSS を一定に保つ。
- **price_precision の cache 経由参照**: Q8 案 A 確定（当面 0.1 円固定 = precision=1）だが、立花 `sYobinetane` から動的に呼値テーブルを引くようになった時のために、ローダが `instrument_cache.get_price_precision(id)` を呼ぶ形にした。N1 では cache miss → fallback=1 で従来挙動と完全一致。
- **`lot_size_override` を辞書で受ける**: ETF / REIT は `sHikaku=1` だが、初回 live 接続前の replay 起動時に cache が空のため fallback=100 が誤って適用される。ユーザーは起動 config に `lot_size_override: {"1301.TSE": 1}` を渡せば 1 件だけ強制上書きできる（cache 全体を無効化しない）。
- **atomic write (tmp → os.replace)**: 立花 live モードで多数銘柄の `sHikaku` を取得すると秒単位で cache を書き換える。途中でクラッシュしても破損 JSON が残らないよう `os.replace` を使う（POSIX/Windows 共に atomic）。`test_atomic_write_uses_tmp_then_rename` でガード。
- **既存 N0 引数 (`lot_size=100`) との後方互換**: `lot_size: int | None = None` に変えて、`None` のときだけ cache 経由で解決する分岐に。N0 テストは依然 `lot_size=100` 明示渡しのため破壊なし。

**Tips**:
- **フィクスチャ作成**: 実 J-Quants ファイルから先頭数行抽出ではなく、`python/tests/fixtures/_build_jquants_fixtures.py` で手書き定数を `gzip.open(..., "wt", newline="")` で書き出した。1KB 未満を維持しつつ「2 銘柄 × 2 日」「月境界 (202401/202402) 2 ファイル」などのテストシナリオを完全制御できる。再生成は `uv run python python/tests/fixtures/_build_jquants_fixtures.py`。
- **月境界テスト**: fixtures に 202401 (1301 4 行) と 202402 (1301 2 行) を入れ、`start="2024-01-30"`/`end="2024-02-01"` で呼ぶと `_iter_yyyymm` が `["202401", "202402"]` を返し両ファイルを開く。202401 内に 1/30 のデータがなくても skip されるが any_file=True なので FileNotFoundError は発生しない。逆に空ディレクトリでは any_file=False で raise される。
- **InstrumentCache.shared() のテスト分離**: シングルトンが test 間でリークするのを防ぐため、`InstrumentCache.reset_shared_for_testing()` を public に出した。`monkeypatch.setattr("engine.nautilus.instrument_cache._default_cache_path", lambda: tmp_path/...)` と組み合わせると、test ごとにクリーンな cache を持てる。
- **Decimal 経由の Price 構築**: `Price(Decimal("3775.0"), precision=1)` のように Decimal 経由にすると float 経路のラウンディング誤差を完全に避けられる（J-Quants daily の `"3775.0"` 文字列をそのまま `Decimal()` に渡す）。

### N1.3 Rust 側 replay_api 差し替え + replay/load 新設 ✅ 完了 2026-04-28（一部 N1.5 / N1.16 繰越）
- [x] ✅ `git grep -nE "VirtualExchangeEngine|virtual_exchange|replay_engine|replay/order" src/ exchange/ engine-client/ python/` を実行 → **0 hit**（test_mode_isolation.py の文字列リテラル `/api/replay/order` のみ一致）。**Rust 自作 Virtual Exchange Engine は存在しないので削除タスク不要**
- [x] ✅ `src/replay_api.rs` に `POST /api/replay/load` を新設し `Command::LoadReplayData` に橋渡し（バリデーション + UUID v4 request_id + 60 秒タイムアウト + ReplayDataLoaded 待ち + Error{mode_mismatch} → 400 マップ）
- [x] ✅ `POST /api/replay/order` を新設し `engine_client.send(SubmitOrder { venue: "replay", ... })` を発行
- [ ] ⏭ **N1.5 繰越**: `OrderFilled` を待つフロー。Python 側で replay venue の SubmitOrder を `BacktestExecutionEngine` にディスパッチする wrapper Strategy が未実装のため、本タスクでは IPC 送出 → 202 Accepted で返す skeleton に留めた
- [x] ✅ `GET /api/replay/portfolio` を skeleton 実装（200 + `{"status":"not_implemented","phase":"N1.16"}`）
- [ ] ⏭ **N1.16 繰越**: nautilus `Portfolio` から cash / buying_power / equity を取得する本実装
- [x] ✅ 自作 Rust 実装は存在しなかったため削除なし（互換シムも残らない）

#### 状況・知見・Tips（2026-04-28 R2 完了報告 — N1.3）

**状況**:
- 新規ファイル: `tests/e2e/s58_replay_load_smoke.sh`（手動 smoke skeleton。CI には載せない）
- 更新ファイル: `src/replay_api.rs`（`ReplayApiState` 新設・3 エンドポイント追加・テスト 13 件追加）、`src/main.rs`（`is_replay_mode` を CLI mode から伝搬 + `ReplayApiState` を構築 + `replay_api::spawn` 引数追加）、`engine-client/src/connection.rs`（pre-existing dirty state を build できる最小 1 行修正 — 後述）、`docs/plan/nautilus_trader/implementation-plan.md`
- テスト結果:
  - `cargo test --workspace`: **453 passed / 0 failed**（49 test binaries すべて ok）
  - 新規 13 件: `replay_api::tests` (Load 7 / Portfolio 2 / Order 3 / live mode 拒否 1)
  - Python 既存: **1033 passed / 2 skipped**（不変、N1.4 完了報告値と一致）
- ビルド/lint: `cargo build --workspace` clean、`cargo fmt --check` clean、`cargo clippy --workspace -- -D warnings` clean。`replay_api` モジュールに警告 0

**新たな知見**:
- **`tokio::sync::watch::Ref` は `Send` でない**ため `state.engine_rx.borrow().clone()` を `match` の中で受けて await を跨ぐと "future is not Send" で `tokio::spawn` に渡せなくなる。修正は `let conn_opt = state.engine_rx.borrow().clone();` で `Arc<EngineConnection>` を抜き出して Ref をスコープから抜けさせる。`order_api::submit_order` でも同じパターン
- **`EngineEvent::ReplayDataLoaded` は schema 2.4 で `request_id` を持たない**ため、HTTP 多重 LoadReplayData リクエストは混線する。`ReplayApiState` に `load_lock: Mutex<()>` を持たせて HTTP ハンドラ側でシリアライズし 1:1 対応を強制
- **`SubmitOrderRequest` の `tags` フィールドは serde の default 指定が無い**ため、`/api/replay/order` のテスト body にも `"tags": []` を必ず含める必要がある。`/api/order/submit` の HTTP 入力型 `SubmitOrderBody` は `#[serde(default)] tags: Vec<String>` で省略可、IPC 側 `SubmitOrderRequest` は必須、という非対称あり
- **未コミット dirty state の発見**: 受領時 `git status` clean だったが `git stash` した瞬間に `dto.rs` (EngineError に strategy_id) / `schemas.py` / `server.py` / `test_server_engine_dispatch.py` の未コミット差分が露出した。N1.4 直近作業の WIP 残骸と推測。`engine-client/src/dto.rs` で `EngineError` に `strategy_id: Option<String>` が追加されていたため `connection.rs:269` の構造体パターンが破綻。**1 行 `..` 追加でビルドを通す最小修正**（IPC schema 自体には触っていない）

**設計思想と背景**:
- **`request_id` 採番方法 (UUID v4)**: `uuid::Uuid::new_v4()` で生成。理由: (a) 既に workspace dep、(b) `order_api::submit_order` と同一パターンで読みやすい、(c) `Error{request_id}` 経路で correlation するには十分な衝突耐性。`load_lock` でシリアライズしているため厳密には `request_id` なしでも動くが、Error 経路で同 `request_id` 一致を確認することで「無関係なシステム Error をたまたま拾う」事故を防ぐ
- **`/api/replay/order` を独立エンドポイントにした判断**: `/api/order/submit` の `is_replay_mode` 経路で吸収する案も検討したが却下。理由: (a) N1.13 で `/api/order/submit` は replay モードで 503 を返す挙動が固定（`test_submit_order_replay_mode_returns_503`）、(b) replay 注文は WAL を別ファイル (`tachibana_orders_replay.jsonl`) にする方針が N1.5 計画にあり共有すると `OrderSessionState` の WAL 読込分離が後で複雑化、(c) spec.md §4 の API 表に `/api/replay/order` が legacy パスとして明記。N1.3 では薄い専用エンドポイントとして開通させ N1.5 で OrderFilled 統合と WAL 分離を一緒にやる
- **`/api/replay/portfolio` skeleton の戻り値選択 (200 vs 501)**: 200 + `{"status":"not_implemented","phase":"N1.16"}` を採用。理由: (a) UI から見ると 4xx/5xx は banner で alert する経路、200 + ステータス文字列なら静かにスキップできる、(b) 後で本実装が入ったとき HTTP コード自体は変わらず body 形状だけ拡張すればよい (forward-compatible)、(c) テストで `phase: "N1.16"` を pin することで「本実装が入ったときに必ずテストが落ちて気づく」ガードになる
- **`SubmitOrder` の OrderFilled 統合を N1.5 に繰越した理由**: 計画書 N1.4 完了報告に記載のとおり、外部 IPC `SubmitOrder` を `BacktestEngine.run()` 中に差し込むには wrapper Strategy + queue + WAL 連携が必要で N1.5 の REPLAY ディスパッチャと同時実装が整理良。N1.3 独立スコープでは IPC 送出パスを開通させて 202 Accepted で返すまでに留め、HTTP → IPC ラッパとしての役割を完遂
- **timeout 60 秒の根拠**: spec.md §3.3 のパフォーマンス目標「1 銘柄 1 ヶ月分 trade tick で 60 秒以内」。J-Quants `equities_trades_YYYYMM.csv.gz` (1 銘柄 1 ヶ月) のロード件数読み上げが 60 秒を超えるなら別問題。タイムアウトを短くするとレシピャル loader が完走前に 504 を返してしまう
- **`is_replay_mode` を CLI mode から伝搬**: `main.rs` で `Arc::new(AtomicBool::new(false))` ハードコードを `cli_args.mode == cli::Mode::Replay` に書き換え。N1.13 `--mode replay` 起動時に `/api/order/submit` が 503 reject、注文は `/api/replay/order` に流れる動線が貫通
- **`connection.rs` の `..` パッチ**: pre-existing N1.4 残骸 dirty state を build できる最小フィックス。schema 自体に変更は加えていない（N1.3 不変条件「IPC schema 変更しない」を守りつつ workspace ビルド成功条件を満たす）

**Tips**:
- `cargo test -p flowsurface --bin flowsurface replay_api::` で N1.3 の 13 件だけ素早く回せる（`--lib` ではなく `--bin` 指定。flowsurface は binary crate）
- engine-client mock は `tokio_tungstenite::accept_async` ベース。Hello → Ready → 1 コマンド受信 → イベント送出 の流れを `spawn_mock_engine_load` / `spawn_mock_engine_capture` に集約
- `tokio::sync::oneshot::Receiver<serde_json::Value>` で「mock engine が受け取ったコマンド本体を test 側で assert」できる。`SubmitOrder` の wire 表現が `op` / `venue` / `order` で正しいか直接検証可能
- `tokio::sync::watch::Ref` の Send 制約: match arm 内で Ref を await 跨ぎで保持しないため `let _opt = state.engine_rx.borrow().clone()` で必ず一度落とす
- E2E smoke (`s58_replay_load_smoke.sh`) は `/api/replay/status` 到達確認後にしか POST しないので binary 未起動時は SKIP で正常終了（CI を壊さない設計）

### N1.4 nautilus 側 BacktestEngine ハンドラ ✅ 完了 2026-04-28（一部 N1.5 繰越）
- [x] ✅ `engine_runner.py` の `start_backtest()` を J-Quants 入力対応に拡張:
  - [x] ✅ `LoadReplayData` IPC を受けて `jquants_loader.load_trades(...)` から `BacktestEngine.add_data(ticks)`（`start_backtest_replay()` 新設）
  - [x] ✅ `BacktestEngine.run()` で自走（[Q3 案 B](./open-questions.md#q3)）
  - [x] ✅ `ReplayDataLoaded` イベントで `bars_loaded` / `trades_loaded` を IPC 返送（`on_event` callback 経由）
- [ ] ⏭ **N1.5 繰越**: `engine_runner.py` で `SubmitOrder` を受けたとき `BacktestExecutionEngine.process_order(...)` で約定判定し `OrderFilled` を IPC で返送（外部 SubmitOrder の replay 内 queue 投入は wrapper Strategy が必要、N1.5 REPLAY 仮想注文ディスパッチャと一緒に実装するのが整理良）
- [ ] ⏭ **N1.5 繰越**: 約定モデル: 直近 TradeTick の last_price ベースの fill。指値は `last_price` クロスで fill
- [ ] ⏭ **N1.11 繰越**: replay 用 market data の Rust UI 複製送出は no-op。tick 数十万件を 1 件ずつ IPC で流すと爆発するため、N1.11 streaming で sleep pacing と一緒に実装する。N1.4 では `ReplayDataLoaded` の件数通知のみ。

#### 状況・知見・Tips（2026-04-28 R2 完了報告 — N1.4）

**状況**:
- 新規ファイル: `python/tests/test_engine_runner_replay.py` (10 件), `python/tests/test_server_engine_dispatch.py` (4 件)
- 更新ファイル: `python/engine/nautilus/engine_runner.py`（`ReplayBacktestResult` / `start_backtest_replay()` / `_make_replay_strategy()` 追加・103 行増）、`python/engine/server.py`（`StartEngine` / `StopEngine` / `LoadReplayData` 分岐 + `_handle_*` 3 メソッド追加・146 行増）、`python/engine/nautilus/strategies/buy_and_hold.py`（`subscribe_kind` / `bar_type_str` 引数追加で trade tick 経路に対応）、`docs/plan/nautilus_trader/implementation-plan.md`
- テスト結果: 全体 **1029 passed / 2 skipped**（既存 1015 + N1.4 新規 14 = 1029、N0 互換テスト破壊なし）。`cargo build --workspace` clean。
- 新規 14 件内訳: `test_engine_runner_replay.py` 10 件（Trades 4 / Bars 2 / Edge 2 / Determinism 1 / Mode 1）+ `test_server_engine_dispatch.py` 4 件（Load 1 / Start 2 / Stop 1）

**新たな知見**:
- **nautilus BacktestEngine 内部 venue は data の `instrument_id` の venue タグと一致させる必要がある**。`jquants_loader` は `1301.TSE` という instrument_id で TradeTick を emit するので、BacktestEngine の `add_venue(Venue("REPLAY"), ...)` + `add_instrument(make_equity_instrument(symbol, "REPLAY"))` にすると `add_data(ticks)` で `Instrument 1301.TSE not found` で raise する。設計書 D5 の「venue タグは `replay`」は **IPC EngineEvent の wire 表現** に関するもので、内部 BacktestEngine の venue とは独立している。本実装では nautilus 内部 venue は `instrument_id.venue.value` (= `"TSE"`) を使い、IPC 送出時に必要なら `"replay"` をスタンプする方針にした。
- **`BacktestEngine.run()` は同期実行で長時間 block する**。サーバ event loop を塞がないため `asyncio.to_thread(_run)` で別 thread に逃がす。`_outbox.append` は `deque.append + Event.set` で、`deque.append` は GIL 保護のもと atomic なので thread から呼んでも安全（次の `_send_loop` 周回で必ず drain される）。`Event.set()` は厳密には main loop からのみ安全だが、本実装では set されなくても次の append で叩き起こされる経路があり、最悪でも次の Ping/Pong まで遅延するだけで欠落はしない。
- **strategy `BuyAndHold` を trade tick 対応にした**: 既存の `subscribe_bars(BarType...)` だけでは TradeTick 経路で全く on_bar が発火しない。`subscribe_kind="trade"` で `subscribe_trade_ticks(instrument_id)` + `on_trade_tick` を追加。N0 互換のため default は `"bar"`。
- **`InstrumentId.from_str("INVALID-ID")` は ValueError を raise する**ことを利用して、`start_backtest_replay()` の入口で format validation が自動で効く。明示的な regex バリデータは不要。

**設計思想と背景**:
- **後方互換 API (`start_backtest` vs `start_backtest_replay`) を分けた理由**: N0 既存テスト 8 件 (`test_nautilus_smoke.py` / `test_nautilus_buy_and_hold.py` / `test_nautilus_determinism.py` / `test_nautilus_data_loader.py`) を破壊しないため。N0 は `klines: list[KlineRow]` を引数で受け取る Bar 経路、N1.4 は J-Quants パスから loader 起動する経路と、入力の抽象レベルが完全に異なる。同じ関数で両方サポートしようとすると引数列が肥大化し silent failure リスクが上がる。
- **`on_event` callback 設計**: IPC 送出の責務を engine_runner から server.py に分離する。engine_runner は「event dict を作って渡す」までを担い、outbox / WebSocket への積み方は server.py 側の責務。これにより engine_runner 単体テストで IPC 送出を mock 不要で検証できる（test_engine_runner_replay.py で `events: list[dict]` に append するだけ）。
- **SubmitOrder の replay 経路を N1.5 に繰越した判断根拠**:
  - 外部 IPC `SubmitOrder` を `BacktestEngine.run()` 中の Strategy に「外部から差し込む」には wrapper Strategy が `submit_order_queue: queue.Queue` を持ち、`on_trade_tick` で queue を drain → `self.submit_order(...)` を呼ぶ設計が必要。
  - 加えて N1.5 で実装する `order_router.py`（live / replay 切替）と `tachibana_orders_replay.jsonl` WAL がスコープに重なる。
  - 単独で書くと wrapper Strategy + queue + WAL 連携で 3 時間以内に収まらないと判断（実測 1 時間でスケルトンの設計図のみ書ける程度）。
  - **N1.4 ではユーザー Strategy が `on_trade_tick` 内で `self.submit_order(...)` する経路だけサポート** し、外部 IPC `SubmitOrder` の replay venue 内部 queue 投入は N1.5 で実装する。本ファイルの計画書 N1.5 章に記述あり。
- **market data 複製送出 (Trades/KlineUpdate) を N1.11 に繰越した判断**: 1 銘柄 1 ヶ月の trade tick は数十万件。1 件ずつ IPC で流すとフレーム数とシリアライズコストが爆発する。N1.11 streaming は `streaming=True` + `add_data([item]) → run() → clear_data()` のループで 1 件ずつ pacing するのでそこと一緒に複製送出を実装するのが効率的。N1.4 では headless 自走経路（`run()` 一発）のみ動かし、UI 描画は `ReplayDataLoaded` の件数通知だけで暫定運用する。
- **venue 内部値の選択**: `_REPLAY_VENUE = "REPLAY"` 定数を用意して全銘柄を REPLAY venue に集約する案も検討したが、`jquants_loader` が emit する TradeTick の `instrument_id` は固定で `1301.TSE` 形式のため、loader 側を venue パラメタライズしない限り合わない。loader 側を変えると N1.2 の API 互換が崩れ test 22 件のリグレッションコストが発生するため、本タスクでは BacktestEngine 内部の venue は `instrument_id.venue.value` を使う方針にした（コメントで明示）。
- **`asyncio.to_thread` の選択**: `ThreadPoolExecutor` も検討したが、`asyncio.to_thread` は 3.9+ 標準で daemon thread を自動管理し、submit-side が cancel された場合の thread 側挙動は明文化されている。本タスクのレベル（同時 1 走行）では `to_thread` で十分。
- **StopEngine の簡素実装**: BacktestEngine.run() の途中 cancel は nautilus 公式 API では非対応（最後まで走り切る）。`runner.stop()` を呼ぶと engine.dispose() を呼ぶが、to_thread 内で run() 中なら Cython 内部で再 dispose 防護が走り raise する可能性がある。本タスクでは「running 中の StopEngine は no-op + log info、終了は run() 完了時の自然停止」とした。streaming 経路 (N1.11) で chunk 間に stop signal を見る形にすれば中途 cancel 可能になるが、本タスク対象外。

**Tips**:
- **テスト用 J-Quants fixtures**: `python/tests/fixtures/equities_trades_202401.csv.gz` 等を `base_dir=FIXTURES` で渡すと runner / server.py の handler 両方で同じ fixtures を共有できる。`base_dir` は `start_backtest_replay()` / `_handle_load_replay_data()` / `_handle_start_engine()` の private 引数で、本番呼出では `None` (= デフォルト `S:/j-quants`) になる。
- **BacktestEngine のスレッド境界**: `asyncio.to_thread(_run)` で逃がした関数は thread のメインが完了するまで返らないが、その内部から呼ぶ `self._outbox.append` は別 thread からの append でも次回 `_send_loop` 周回で drain される。`_outbox_event.set()` の thread-safety は厳密でないが、`_send_loop` は `wait()` の前に常に `_outbox` の長さを確認するため deadlock しない。
- **strategy_id "buy-and-hold" のみサポート**: N1.4 では `_make_replay_strategy` でハードコード分岐。N1.6 で narrative_hook と `--strategy-file` を入れるときに plugin 化する。それまでは `ValueError` で明示拒否する（silent fallback 禁止）。
- **`granularity="Trade"` で 0 件ロード**: fixtures に存在しない code (例: `1306.TSE`) を渡すと、jquants_loader は trades file 自体は存在するので `FileNotFoundError` ではなく空 iterator を返す。`engine.add_data([])` は呼ばないようにガード (`if ticks:`) してあるが、空でも run() は完了する（戦略は何もしない）。`final_equity == initial_cash` であることを `test_empty_range_still_emits_engine_stopped` で確認。

### レビュー反映 (2026-04-28, ラウンド 1)
- ✅ H1: engine_run_failed 時に Error{request_id} 追加 → Rust 60s ハング解消
- ✅ H2: asyncio.to_thread に timeout=3600s 追加
- ✅ H3: validate_start_engine 失敗の EngineError 二重送出を除去 (Error のみに統一)
- ✅ H4: test_start_engine_live_mode_rejects_backtest に request_id 確認追加
- ✅ H5: test _outbox=[] を _ListOutbox duck-type スタブに差し替え
- ✅ M1: EngineError schema + Rust dto に strategy_id 追加
- ✅ M2: EngineError 送出を Pydantic model.dump() に統一
- ✅ M3: log.error に exc_info=True 追加 (2 箇所)
- ✅ M4: int(initial_cash) を to_thread 前にバリデーション
- ✅ M5-M7: テスト追加 (strategy_id/stop/ReplayDataLoaded)

### N1.5 REPLAY 仮想注文ディスパッチャ

**前提**: `python/engine/exchanges/tachibana_orders.py` が存在し `submit_order` / `NautilusOrderEnvelope` が実装済み（order/ Phase O0 以上完了）

- [ ] `python/engine/order_router.py` 新設
- [ ] live モード → `tachibana_orders.submit_order(...)` に委譲
- [ ] replay モード → `BacktestExecutionEngine.process_order(...)` に委譲
- [ ] 監査ログ WAL: `tachibana_orders.jsonl`（live）と `tachibana_orders_replay.jsonl`（replay）に分離
- [ ] `client_order_id` 名前空間を live / replay で分離
- [ ] 第二暗証番号 modal は REPLAY ガードで skip
- [ ] 発注入力 UI（Python tkinter）を replay モード文言に切替
      （例: バナー「⏪ REPLAYモード中 — 実注文は送信されません」、確認文言「仮想注文確認」）
- [ ] iced は監視・表示のみを担い、注文入力責務を持たないことを維持
- [ ] `python/tests/test_order_router_dispatch.py`: live 時は `tachibana_orders.submit_order` 呼出、replay 時は `tachibana_orders_replay.jsonl` 書込を mock で検証

### N1.6 ナラティブ API 新設（H5）
- [ ] `POST /api/agent/narrative` を `src/api/agent_api.rs`（新設）に実装（**本タスクが初実装**）
- [ ] `python/engine/nautilus/narrative_hook.py` を新設し、`Strategy.on_event` で `OrderFilled` を捕捉 → POST（`linked_order_id` を埋める）
- [ ] 文書間整合: [docs/plan/README.md](../README.md) Phase 4a の概念定義と矛盾しないこと

### N1.7 Gymnasium 互換性確認
- [ ] `FlowsurfaceEnv.step()` が変わらず動くこと
- [ ] `python/tests/test_flowsurface_env_with_nautilus.py`
- [ ] **追加テスト**: 部分約定 / cancel-after-fill レース / EC frame 重複受信を mock で再現

### N1.8 live/replay 互換 lint ⭐ 新設
- [ ] `python/tests/test_strategy_compat_lint.py`: ユーザー Strategy ファイルの AST を解析し、`on_order_book_*` / `on_quote_tick` の定義があれば fail（[spec.md §3.5.4](./spec.md#354-互換性-ci-検査n18-で追加)）
- [ ] 組み込み `BuyAndHold` を **live mock + replay J-Quants の両方**で走らせ最終ポジション方向が一致するスモークテスト（`test_strategy_live_replay_smoke.py`）
- [ ] CI に組み込み (`uv run pytest python/tests/test_strategy_compat_lint.py`)

### N1.9 決定論性テスト（tick ベース）
- [ ] `python/tests/test_nautilus_determinism_tick.py`: J-Quants 同一ファイル・同一銘柄で `start_backtest()` を 2 回回して equity / fill_timestamps / fill_last_prices ビット一致
- [ ] N0.6 の Bar ベース版と並列で維持

### N1.10 性能ベンチマーク
- [ ] `nautilus_replay_baseline.py` を追加
- [ ] 「`start_backtest` 呼出 → `EngineStopped` IPC 受領」までの wall clock を計測
- [ ] **目標**: 1 銘柄 1 ヶ月分 trade tick で 60 秒以内（[spec.md §3.3](./spec.md#33-パフォーマンス)）。実測値で確定し spec 更新

### N1.11 Replay 再生 speed コントロール（streaming=True 経路）
- [ ] engine-client/src/dto.rs に Command::SetReplaySpeed { multiplier } を追加
      （Pause/Resume/Seek は本タスクに含めない）
- [ ] python/engine/nautilus/engine_runner.py に streaming ループ実装を追加:
      add_data([item]) → run(streaming=True) → clear_data() を 1 件ずつ回す
- [ ] ループ間に D7 の pacing 式で sleep を挟む:
      sleep_sec = min(max(dt_event_sec, 0.001) / multiplier, 0.200)
      （multiplier=1/10/100、SLEEP_CAP=200ms、MIN_TICK_DT=1ms）
- [ ] 前場-後場 / 引け後 / 営業日跨ぎのギャップは sleep=0 で即時通過(D7)
- [ ] 営業日跨ぎ時に UI 向け date-change マーカーを 1 件 emit
- [ ] 既存 run(start, end) 自走経路は headless / 決定論性テストで温存
- [ ] iced 側にコントロールバー pane を新設(1x / 10x / 100x ボタンのみ)
- [ ] src/api/replay_api.rs: POST /api/replay/control で action="speed" のみ受理、
      他 action は 400 Bad Request を返す
- [ ] python/tests/test_replay_speed.py:
      - speed=10 で wall clock が ~1/10 になること（セッション内 tick 列で計測）
      - 仮想時刻（tick.ts_event）は multiplier 不変であること
      - 11:30 JST 跨ぎ tick で sleep=0 になること
      - 営業日跨ぎ tick で sleep=0 + date-change マーカー 1 件 emit
      - 同一マイクロ秒バーストでも MIN_TICK_DT_SEC=1ms が下限になること
      - 1 sleep が SLEEP_CAP_SEC=200ms を超えないこと
- [ ] N0.6 / N1.9 の決定論性テストが run() 自走経路で引き続き緑であること

### N1.12 ExecutionMarker / StrategySignal IPC + UI overlay
- [ ] engine-client/src/dto.rs に EngineEvent::ExecutionMarker / StrategySignal を追加
- [ ] python/engine/nautilus/narrative_hook.py で OrderFilled 受領時に
      ExecutionMarker を自動送出（fill 由来の自動レイヤー）
- [ ] python/engine/nautilus/strategy_helpers.py 新設: Strategy mixin に
      emit_signal(kind, side=None, price=None, tag=None, note=None) を追加し、
      StrategySignal IPC を送出
- [ ] BuyAndHold を改造して買い前にエントリー検討の StrategySignal(EntryLong) を出すサンプル化
- [ ] iced 側 chart pane に 2 レイヤー追加（execution layer / signal layer）
- [ ] python/tests/test_execution_marker_emit.py: OrderFilled → ExecutionMarker 1:1
- [ ] python/tests/test_strategy_signal_emit.py: emit_signal() 呼出 → IPC 1 件、
      未約定でも独立に出ること
- [ ] `signal_kind` の wire 表現（enum vs `kind: String`）は [Q13](./open-questions.md#q13) で確定するまで暫定 enum 実装、後方互換性を破らない

### N1.13 起動時モード固定（live / replay）✅ 完了 2026-04-28（一部繰越）
- [x] ✅ Rust 側 [src/cli.rs](../../../src/cli.rs) に CLI 引数 `--mode {live|replay}` を追加（必須・デフォルトなし、D8 起動時固定の踏襲）
- [x] ✅ IPC Hello に `mode` を載せ、Python 側 `server.py._handshake` で受け取って `self._mode` に保持。Ready capabilities にエコーバック (`capabilities.mode`)。
- [x] ✅ Python 側 server.py の mode 別起動責務:
      - replay: 既存 BacktestEngine 起動経路を維持（N0 で実装済み）、LiveExecutionEngine は触らない
      - live  : 既存 Phase 1 の立花 EVENT WS 閲覧経路を継続。nautilus LiveExecutionEngine は N1 では起動しない（stub のまま）。
                Ready capabilities は `nautilus.live=false` を維持
      - mode と StartEngine.engine の不一致は `engine.mode.validate_start_engine()` が `ValueError` で拒否
- [ ] iced 側: mode に応じた Depth ペイン visibility・order UI 文言・バナー切替は **N1.14/N1.15 に委譲**（本タスクではログ出力のみ — main.rs に `Started in mode: live|replay`）
- [x] ✅ 切替コマンド（IPC / HTTP）は追加していない（D8 起動時固定方針）
- [x] ✅ [python/tests/test_mode_isolation.py](../../../python/tests/test_mode_isolation.py) 12 件 GREEN:
      - live モードで /api/replay/* が拒否される (`is_replay_path_allowed`)
      - replay モードで /api/order/submit が REPLAY ディスパッチに流れる (`order_dispatch_target`)
      - mode 不一致の StartEngine が拒否される (`validate_start_engine`)
      - live モードで Hello.capabilities.nautilus.live が false のまま (`nautilus_capabilities`)
- [x] ✅ [tests/e2e/s55_mode_startup_smoke.sh](../../../tests/e2e/s55_mode_startup_smoke.sh) **stub** 配置（`bash s55_mode_startup_smoke.sh` で実行可能、release binary 未ビルド時は SKIP）。**完全な E2E は N1.14 で実装** — pane visibility 切替実装と一緒に書くのが効率的なため。
- [ ] ランタイム切替の責務は [Q15](./open-questions.md#q15) で N2 着手前に再評価（本タスク対象外）

#### 状況・知見・Tips（2026-04-28 R1 完了報告 — N1.13）

**状況**: cli.rs に `Mode` enum と `--mode` 引数（必須）追加。連動して `engine-client/src/process.rs::ProcessManager::set_mode()` と `connection.rs::EngineConnection::connect_with_mode()` を新設。後方互換のため旧 `connect(url, token)` は `mode="live"` にフォールバック。`python/engine/mode.py` に policy ヘルパー (`is_replay_path_allowed` / `order_dispatch_target` / `validate_start_engine` / `nautilus_capabilities`) を新設し、server.py から呼ぶ前段としてピュア関数として独立させた。`schemas.py::Hello` に `mode: Literal["live", "replay"] = "live"` 追加（旧 client 互換のため default 値あり）。

**新たな知見**:
- `/api/replay/*` の HTTP ルーティングは現在まだ Rust 側に存在しない（`replay_api.rs` には `/api/replay/status` のみ）。本タスクで HTTP 層の dispatch を実装するのは **N1.3 のスコープ越境** になるため、policy ヘルパー (`is_replay_path_allowed`) を先行実装し、テストは関数単位で書いた。N1.3 で Rust 側 `replay_api.rs` 拡張時に `if !is_replay_path_allowed(mode, path) { return 400; }` を 1 行追加する形で接続する。
- `ProcessManager` に直接フィールドを足すと既存テストの `with_command()` 構築箇所が破綻する。`mode: Arc<Mutex<String>>` + `set_mode()` のセッター方式にすることで全テストの構築コードに変更が要らなかった。
- pydantic `Literal["live", "replay"]` に default 値 `"live"` をつけることで、旧 Rust client (mode field 無し) からの接続でも `extra="ignore"` 経路で素直に default が効く。`extra="forbid"` だと旧 client が即座に schema_mismatch になるので、Hello だけは `extra="ignore"` のままにすることが重要。

**設計思想と背景**:
- `engine.mode` は **server.py から完全に切り離した純粋関数モジュール**。理由: (a) policy をユニットテスト可能な単位に切る、(b) server.py の dispatch 関数本体は I/O と policy が密結合しており、policy だけ独立テストするのが最も TDD と相性がよい、(c) N1.3 / N1.5 で /api/replay や order_router からも同じ policy を呼びたくなるため、再利用可能な共有モジュールにしておく。
- iced UI の mode 切替は **本タスクでは log のみ**。理由: (a) 計画書冒頭にも「pane visibility / order UI 文言切替は N1.14/N1.15 で実装」と明記、(b) `--mode replay` で実用 UI を出すには N1.14 の ReplayPaneRegistry が必要、(c) 中途半端に banner だけ追加すると D9 の UI 設計と齟齬が出る。
- `connect()` を破壊的に変更せず `connect_with_mode()` を新設した: pre-N1.13 で書かれた integration test (`handshake.rs` 等) と、`ProcessManager::start()` 内部の `EngineConnection::connect` 呼び出しが多数存在し、一気に署名を変えると変更点が分散する。後方互換ラッパは N3 で `connect()` を削除する形で整理する。
- E2E (`s55_mode_startup_smoke.sh`) を stub にとどめた: 完全な E2E には Python engine 起動・mode injection (smoke.sh の MODE 環境変数対応) が必要で、それ自体が pane visibility 実装 (N1.14) と同時にやらないと assertion がスカスカになる。今は「ファイルが存在する・bash で起動できる・SKIP 経路が動く」だけ確認。

**Tips**:
- `cargo test -p flowsurface --lib cli::` だけで `--mode` 周りの 6 件を素早く回せる。
- `engine.mode` のような純粋関数モジュールは pytest でドキュメント先行で書ける（テストが要件仕様書になる）。`test_mode_isolation.py` は計画書の N1.13 要件 4 項目をそのまま 12 ケースに展開した。
- `Hello` に新フィールドを追加するときは default 値必須。pydantic の field validation はマッチ順だが orjson roundtrip では default が常に出力されるため、`mode_dump(mode="json")` の出力に `"mode"` が現れることをテストでガードしておくと、将来 default を消したときの破壊的変更検知になる。

### N1.14 REPLAY 銘柄追加時のチャート pane 自動生成（D9.1〜D9.4）
- [ ] iced 側に ReplayPaneRegistry を新設し identity = (mode=replay, instrument_id, pane_kind, granularity?) を管理
- [ ] /api/replay/load 成功（ReplayDataLoaded 受信）を契機に Tick pane と
      Candlestick(1m) pane の生成判定を回す
- [ ] 既存 identity が存在する場合は新規生成しない（重複生成防止）
- [ ] 生成位置ルール（D9.3）:
      - 1 銘柄目: 横並び 2 分割
      - 2 銘柄目以降: フォーカス pane を縦分割
- [ ] MAX_REPLAY_INSTRUMENTS = 4 を超える load は HTTP 400
- [ ] ユーザーが手動 close した自動生成 pane は同セッション中は再生成しない
      （registry に user_dismissed フラグを持つ）
- [ ] StopEngine では自動生成 pane を残す。/api/replay/load 再実行時は overlay と
      chart buffer をクリア
- [ ] tests/test_replay_pane_registry.rs:
      - 同 instrument の二重 load で pane が増えないこと
      - 4 銘柄超過の load が 400 になること
      - StopEngine 後も pane が残ること
      - 再 load で overlay / buffer がクリアされること
      - 手動 close 後に再 load しても自動生成されないこと
- [ ] tests/e2e/s56_replay_pane_autogen.sh:
      /api/replay/load 1 件で Tick + Candlestick の 2 pane が現れること

### N1.15 REPLAY 注文一覧 pane（D9.5）
- [ ] iced 側 OrderListStore を venue で 2 view（live / replay）に分割
- [ ] 1 銘柄目の /api/replay/load 成功時に REPLAY 注文一覧 pane を 1 枚自動生成
      （identity = (mode=replay, pane_kind=order_list)、銘柄非依存で 1 つだけ）
- [ ] pane header に「⏪ REPLAY」バナー + live と区別された配色
- [ ] EngineEvent::Order* を venue でフィルタし REPLAY view にのみ反映
- [ ] HTTP /api/order/list?venue=replay を新設（既存 live は default 動作維持）
- [ ] tachibana_orders_replay.jsonl WAL の内容と REPLAY 注文一覧の整合を保つ
      （再起動時の warm-up は WAL 起点）
- [ ] tests/test_replay_order_list_view.rs:
      - venue=replay の OrderFilled が REPLAY view にのみ入り live view を汚染しないこと
      - /api/replay/load 再実行で REPLAY 注文一覧がクリアされること
      - StopEngine 後も最終状態が残ること
- [ ] python/tests/test_order_list_api_venue_filter.py:
      - /api/order/list?venue=replay が tachibana_orders_replay.jsonl のみ返すこと

### N1.16 REPLAY 買付余力（D9.6）
- [ ] engine-client/src/dto.rs に EngineEvent::ReplayBuyingPower を追加（schema 1.4）
- [ ] python/engine/nautilus/portfolio_view.py を新設:
      - nautilus Portfolio.account_for_venue(SIM) から cash / equity を取得
      - 仮想 position の MTM を直近 TradeTick.price で算出
      - 1 秒間隔 + 約定即時のハイブリッド送出
- [ ] 起動 config の initial_cash を NautilusRunner に渡し、Portfolio 初期化に使う
- [ ] python/engine/order_router.py: REPLAY モード時は CLMZanKaiKanougaku の HTTP
      呼び出しを skip する明示ガード（assert mode != "replay" or skip_clm_call）
- [ ] iced 側 BuyingPowerStore を venue で 2 view に分割し、REPLAY view は
      ReplayBuyingPower のみ反映（CLMZanKaiKanougaku を参照しない）
- [ ] 表示器に「⏪ REPLAY」バナー、live と区別された配色
- [ ] HTTP /api/replay/portfolio のレスポンスに cash / buying_power / equity を追加
- [ ] python/tests/test_replay_buying_power.py:
      - 仮想買い約定で cash が支払額分だけ減ること
      - 売り約定で cash が受取額分だけ増えること
      - position 保有中の equity = cash + MTM になること
      - /api/replay/load 再実行で initial_cash から再計算されること
      - REPLAY 中に CLMZanKaiKanougaku が呼ばれないこと（mock で 0 call assert）
- [ ] tests/test_replay_buying_power_view.rs:
      - REPLAY view が ReplayBuyingPower で更新され live view が影響を受けないこと
- [ ] tests/e2e/s57_replay_buying_power_smoke.sh:
      load → 仮想買い → cash 減少 → 売り → cash 復元 が UI に反映されること

**Exit 条件**:
- J-Quants `equities_trades_202401.csv.gz` から 1 銘柄をロードし、`BuyAndHold` 戦略でバックテストが完走、`OrderFilled` が IPC 経由で受信できる
- N1.8 の live/replay smoke が両方緑
- N1.9 の tick 決定論性テストが緑
- `s51`〜`s53` ナラティブ系 E2E が全部緑のまま、`/api/replay/*` と `/api/order/*`（REPLAY モード）の挙動が nautilus 経由で同じ
- 1 ヶ月バックテスト SLA 確定
- 再生 **speed** コントロール（1x / 10x / 100x）が iced UI から効くこと
- `ExecutionMarker` が `BuyAndHold` の fill に対応する位置に点描されること
- 組み込み Strategy が `emit_signal()` で出した `StrategySignal` が overlay に表示されること
- `/api/replay/load` を 1 件投げるだけで Tick pane と Candlestick pane が自動生成されること（D9.1〜D9.4）
- 同銘柄を 2 回 load しても pane が増えないこと（D9 重複生成防止）
- 5 銘柄目の load が 400 で拒否されること（D9 上限ガード）
- REPLAY の仮想注文・仮想約定に応じて REPLAY 注文一覧が更新され、live 注文一覧を汚染しないこと（D9.5）
- REPLAY の portfolio / cash 変化に応じて REPLAY 買付余力表示が更新されること（D9.6）
- REPLAY 中に立花 `CLMZanKaiKanougaku` HTTP が呼ばれないこと（D9.6 誤参照防止コードガード）
- pause / seek は **N1 Exit 条件に含めない**（Q14 で再評価）

---

## Phase N2: 立花 ExecutionClient（デモ）

**前提**: order/ 計画の Phase O0〜O2 が完了し、`tachibana_orders.submit_order` / `modify_order` / `cancel_order` / EC frame パーサ / 第二暗証番号 UI / 監査ログ WAL がすべて稼働している。本フェーズは **nautilus への薄い adapter のみ**を書く。

### N2.0 立花 LiveDataClient（FD frame → TradeTick）⭐ 新設
- [ ] `python/engine/nautilus/clients/tachibana_data.py` 新設
- [ ] 既存 `tachibana_ws._FdFrameProcessor` の trade dict 出力を nautilus `TradeTick` に変換（[data-mapping.md §1.2](./data-mapping.md#12-live-立花-fd-frame--tradetick)）
- [ ] `LiveDataClient` を継承し `LiveDataEngine.process(tick)` に流す
- [ ] `aggressor_side` 推定不能の場合は `NO_AGGRESSOR` に写像（[tachibana_ws.py:183](../../../python/engine/exchanges/tachibana_ws.py#L183) の警告を補足）
- [ ] テスト: FD frame サンプル → TradeTick 変換、`NO_AGGRESSOR` 比率の sanity check

### N2.1 nautilus `LiveExecutionClient` adapter

**前提**: `python/engine/exchanges/tachibana_orders.py` が存在し `submit_order` / `NautilusOrderEnvelope` が実装済みであること（order/ Phase O0 以上完了）

- [ ] `python/engine/nautilus/clients/tachibana.py` 新設
- [ ] `LiveExecutionClient` を継承し、以下を **order/ の関数に委譲**:
  - `submit_order(Order)` → `tachibana_orders.submit_order(session, second_password, NautilusOrderEnvelope.from_nautilus(order))`
  - `modify_order` → `tachibana_orders.modify_order(...)`
  - `cancel_order` → `tachibana_orders.cancel_order(...)`
- [ ] 立花 API 写像（`OrderType` / `TimeInForce` / `cash_margin` / `account_type`）は **[order/spec.md §6](../✅order/spec.md#6-nautilus_trader-互換要件不変条件) と [data-mapping.md](./data-mapping.md) に従う**。本ファイル内に重複定義しない

### N2.2 EC frame → nautilus イベント変換
- [ ] `python/engine/nautilus/clients/tachibana_event_bridge.py` 新設
- [ ] order/ の `tachibana_event._parse_ec_frame` の戻り値（`OrderEcEvent`）を nautilus `OrderFilled` / `OrderCanceled` / `OrderRejected` に変換
- [ ] `LiveExecutionEngine.process_event(...)` に流す
- [ ] **冪等化（M5）**: 同一 `p_eda_no` の EC が再送された場合に nautilus 側で 2 重 `OrderFilled` を発火しないよう、ClientOrderId 単位の seen-set を adapter 内に持つ。order/ の Python 側 seen-set と二重ガードになるが、IPC 経路を跨ぐ再起動時に守りを兼ねる
- [ ] 検証テスト: N2.6 の同一 `p_eda_no` 重複受信テスト（`test_ec_idempotency`）が pass すること

### N2.3 注文 ID マッピングと再起動復元
- [ ] nautilus `ClientOrderId` ⇔ 立花 `sOrderNumber` の双方向写像（order/ の `OrderSessionState` を流用）
- [ ] プロセス再起動時: 立花 `CLMOrderList` を引き、未決注文を nautilus `Cache` に warm-up（[spec.md §3.2](./spec.md#32-セキュリティ) の persistence 無効・Cache warm-up 規約）
- [ ] **persistence 設定（H3）**: `engine_runner.py` の `NautilusEngineConfig` 組み立て箇所で `database` を `None` にハードコードし、直後に `assert config.database is None` を入れる。Parquet/SQLite 永続化を OFF にしたまま warm-up を毎回行う。テストで `CacheConfig` の設定値を assert

### N2.4 市場時間帯ガード（M2）
- [ ] 立花 venue が `Disconnected{reason:"market_closed"}` の間は `LiveExecutionClient.start()` を保留する
- [ ] HTTP API 層 (`order_api.rs`) で `MARKET_CLOSED` を先行 reject（[order/spec.md §5.2](../✅order/spec.md#52-reason_code-体系観測性)）
- [ ] nautilus 内部で reject されてナラティブが汚染されないよう、ExecutionClient `start()` 前に `RiskEngine` への渡し前段で stop する経路を確認

### N2.5 セーフティ（order/ と二重ガード）
- [ ] デモ環境強制（`TACHIBANA_ALLOW_PROD=1` 未設定なら本番 URL を選んでも reject）
- [ ] 数量上限・1 注文金額上限を起動 config で必ず指定（未指定なら起動拒否）
- [ ] 発注ログ追記は order/ の WAL を使う（重複ファイルを増やさない）

### N2.6 E2E と単体テスト
- [ ] `s70_tachibana_nautilus_demo_order.py`（CI には載せない、ローカル手動。デモ環境クレデンシャルが必要）
- [ ] ユニットテスト: nautilus `OrderFactory` から発注 → adapter → mock `tachibana_orders` の往復で order/ の **`OrderType` 全 6 種 + `TimeInForce` 全 7 種**（[order/spec.md §6.1](../✅order/spec.md#61-用語型の整合必須)）を検証
- [ ] **追加テスト**: 部分約定 EC を 2 件流して `cumulative_qty` / `leaves_qty` が nautilus `OrderFilled` で正しく累積すること
- [ ] **追加テスト**: cancel リクエスト送信中に EC fill が来るレースで `OrderStatus` が壊れないこと
- [ ] **追加テスト**: 同一 `p_eda_no` の EC 重複受信で `OrderFilled` が 1 回しか発火しないこと

**Exit 条件**: デモ環境で「成行買い → 約定通知受信 → nautilus Portfolio に反映 → ナラティブに `outcome` が入る」往復が手動で確認、N2.6 の追加テストすべて緑

---

## Phase N3: 暗号資産 venue 移植（任意）

割愛（N2 完了時に詳細化）。Rust 側 `exchange/` の `place_order` / `cancel_order` 経路を削除し、nautilus 側 `HyperliquidExecutionClient` 等に置き換える。データ取得経路は触らない。

---

## 削除リスト（N1 完了時点）

- `docs/plan/README.md` Phase 2 の「自作」前提の文言
- 自作 Virtual Exchange Engine の Rust 実装は **N1.2 着手前の grep で存在確認** し、見つかった場合のみ削除（C3 修正、`(あれば)` hedge は廃止）

## 削除リスト（N3 完了時点）

- Rust `exchange/src/adapter/*` の発注関連メソッド（**N-pre Tpre.4 の grep で存在が確認できた場合のみ**）
- `engine-client` の venue 別発注 IPC の crypto 分岐（venue 列挙から `<crypto>` を落とす。`"tachibana"` と `"replay"` は残る）

**`venue` 列挙の長期形（C-ack）**: N3 完了後も `SubmitOrder.venue` は `"tachibana"` / `"replay"` の 2 値が残る。`"replay"` は架空 venue として恒久的に残す（README §既存計画との関係 で明示）。

## 横断タスク

- [ ] `docs/plan/README.md` の Phase 2 セクションを「nautilus 統合」に書き換え（N1 完了直後）
- [ ] `docs/plan/✅tachibana/implementation-plan.md` の Phase 2（発注）タスクが [docs/plan/✅order/](../✅order/) と本計画 N2 に分離されていることを再確認し、引退表示を update（N2 着手時）
- [ ] CLAUDE.md / SKILL.md（tachibana）に nautilus 経由の発注フローを追記:
  - SKILL.md L8 警告ブロック近傍に「N2 以降は `tachibana_orders.*` が `LiveExecutionClient` adapter 経由で nautilus から呼ばれる」追記
  - SKILL.md R10 に nautilus persistence 無効方針の参照リンク
  - SKILL.md S6 表に「nautilus 経由の発注時も `p_no` 採番ガードが効くこと」追記
- [ ] LGPL-3.0 の同梱表示（README + 配布アーティファクトの NOTICE）。配布形態は N-pre Tpre.3 の決定に従う

## ラベル規約（L2 修正）

旧版で N0/N1/N2 直下のタスクを `T0.1`〜`T2.5` と命名していたが、立花 plan の `T0`〜`T7` と紛らわしいため、本計画ではすべて **`N0.1`〜`N3.x`** に統一した。横参照する側（CLAUDE.md / SKILL.md / 他 plan）も `N{phase}.{n}` 形式で参照すること。
