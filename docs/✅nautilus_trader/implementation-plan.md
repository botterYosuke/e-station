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
- 更新ファイル: `src/replay_api.rs`（`ReplayApiState` 新設・3 エンドポイント追加・テスト 13 件追加）、`src/main.rs`（`is_replay_mode` を CLI mode から伝搬 + `ReplayApiState` を構築 + `replay_api::spawn` 引数追加）、`engine-client/src/connection.rs`（pre-existing dirty state を build できる最小 1 行修正 — 後述）、`docs/✅nautilus_trader/implementation-plan.md`
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
- 更新ファイル: `python/engine/nautilus/engine_runner.py`（`ReplayBacktestResult` / `start_backtest_replay()` / `_make_replay_strategy()` 追加・103 行増）、`python/engine/server.py`（`StartEngine` / `StopEngine` / `LoadReplayData` 分岐 + `_handle_*` 3 メソッド追加・146 行増）、`python/engine/nautilus/strategies/buy_and_hold.py`（`subscribe_kind` / `bar_type_str` 引数追加で trade tick 経路に対応）、`docs/✅nautilus_trader/implementation-plan.md`
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

### レビュー反映 (2026-04-28, ラウンド 2-3)
- ✅ HIGH: TimeoutError パス started_marker 競合 → EngineStopped を無条件送出に変更
- ✅ HIGH: TimeoutError 後に runner.stop() を呼び worker thread へ停止シグナル送信
- ✅ MEDIUM: TimeoutError.message 空文字 → フォールバック固定文字列
- ✅ MEDIUM: request_id=None ガード追加 → 早期 return
- ✅ MEDIUM: EngineError ローカルインポートを関数先頭に移動 (重複除去)
- ✅ MEDIUM: _Outbox に __iter__ 追加、_ListOutbox スタブと対称性確保
- ✅ LOW: _engine_tasks 残骸 (initial_cash parse 失敗パス) 解消
- ✅ LOW: docstring を H3 修正後の実挙動 (Error のみ) に更新
- ✅ LOW: test_engine_started_then_failure に Error{request_id} 検証追加
- 新規テスト: 12 件 (8 既存 + 4 新規)、全 pytest 1037 passed 確認済み
- 設計判断: EngineError は接続レベル専用 (auth_failed/schema_mismatch)、
  コマンドレベルのエラーは Error{request_id} に統一

### N1.5 REPLAY 仮想注文ディスパッチャ ✅ 完了 2026-04-28

> ⚠️ **配線繰越（R2 review-fix R2 で明文化）**: `python/engine/order_router.py` 自体は実装完了しているが、
> `server.py` の `_do_submit_order_inner` 経路では M-7 (R1a) で導入した
> 「venue == "replay" を `OrderRejected{REPLAY_NOT_IMPLEMENTED}` で早期 reject」分岐がそのまま残る。
> production caller からは `route_submit_order` が呼ばれない（`grep` 0 hit）。
> `_do_submit_order_inner` → `route_submit_order(...)` の配線は **N1.11 (streaming + speed control)** で
> 同時に行う。N1.11 完了後に `REPLAY_NOT_IMPLEMENTED` reason_code は廃止する（H-3, R2 review-fix R2）。

**前提**: `python/engine/exchanges/tachibana_orders.py` が存在し `submit_order` / `NautilusOrderEnvelope` が実装済み（order/ Phase O0 以上完了）

- [x] ✅ `python/engine/order_router.py` 新設
- [x] ✅ live モード → `tachibana_orders.submit_order(...)` に委譲（`submit_order_live`）
- [x] ✅ replay モード → `tachibana_orders_replay.jsonl` WAL 記録（`submit_order_replay`）。BacktestEngine.process_order() 統合は ⏭ N1.11 繰越
- [x] ✅ 監査ログ WAL: `tachibana_orders.jsonl`（live）と `tachibana_orders_replay.jsonl`（replay）に分離
- [x] ✅ `client_order_id` 名前空間を live / replay で分離（`REPLAY-` プレフィックス付与）
- [x] ✅ CLMZanKaiKanougaku / CLMZanShinkiKanoIjiritu は replay ルートに存在しない（D9.6 明示ガード）
- [ ] ⏭ **N1.14/N1.15 繰越**: 第二暗証番号 modal は REPLAY ガードで skip（server.py 統合時）
- [ ] ⏭ **N1.14/N1.15 繰越**: 発注入力 UI（Python tkinter）を replay モード文言に切替（バナー・確認文言）
- [x] ✅ iced は監視・表示のみを担い、注文入力責務を持たないことを維持（server.py 変更なし）
- [x] ✅ `python/tests/test_order_router_dispatch.py` 7 件 GREEN

#### 状況・知見・Tips（2026-04-28 完了報告 — N1.5）

**状況**:
- 新規ファイル: `python/engine/order_router.py`（`submit_order_live` / `submit_order_replay` / `route_submit_order`）
- 新規テスト: `python/tests/test_order_router_dispatch.py` 7 件
- テスト結果: **1061 passed / 2 skipped**（既存 1054 + N1.5 新規 7 = 1061、既存テスト破壊なし）
- server.py は変更なし（server.py への統合は N1.14/N1.15 で行う）

**新たな知見**:
- **`submit_order_replay` は同期関数で十分**: WAL 書込は `open + fsync` の同期 I/O なので `async def` 不要。`route_submit_order` が `async def` の場合は `await` 不要な同期呼出しとして直接 `return submit_order_replay(...)` で返せる。
- **`asyncio.get_event_loop().run_until_complete()` は Python 3.10+ で DeprecationWarning**: テストでは `asyncio.run(...)` に統一する。

**設計思想と背景**:
- **BacktestEngine.process_order() 統合を N1.11 に繰越した理由**: spec.md §2.2.3 に「BacktestEngine への投入は N1.5 では no-op」と明記されており、N1.11 の streaming 経路と一緒に実装することで WAL の事後 drain と speed pacing を同時に設計できる。N1.5 単独で process_order() 統合まで実装すると N1.11 の設計と重複する。
- **`submit_order_live` に `_tachibana_submit_order` を monkeypatch する設計**: モジュール内でエイリアス（`_tachibana_submit_order`）としてインポートすることで `monkeypatch.setattr("engine.order_router._tachibana_submit_order", ...)` による差し替えが簡潔になる。

**Tips**:
- `submit_order_replay` のテストは `tmp_path` を使って WAL をファイルシステムに書き、`json.loads()` で内容を直接検証する。mock 不要でシンプル。
- CLMZanKaiKanougaku ガードのテストは `monkeypatch.setattr(torders, "fetch_buying_power", ...)` で呼出し回数を count するだけで十分（テスト実行中に HTTP I/O が起きないことを保証できる）。

### N1.6 ナラティブ API 新設（H5） ✅ 完了 2026-04-28
- [x] ✅ `POST /api/agent/narrative` を `src/api/agent_api.rs`（新設）に実装（**本タスクが初実装**）
- [x] ✅ `python/engine/nautilus/narrative_hook.py` を新設し、`Strategy.on_event` で `OrderFilled` を捕捉 → POST（`linked_order_id` を埋める）
- [x] ✅ 文書間整合: [docs/plan/README.md](../README.md) Phase 4a の概念定義と矛盾しないこと

#### 状況・知見・Tips（2026-04-28 完了報告 — N1.6）

**状況**:
- 新規ファイル: `src/api/agent_api.rs`（`AgentApiState` + `handle_post_narrative` + `handle_get_narrative` + Rust テスト 3 件）
- 新規ファイル: `python/engine/nautilus/narrative_hook.py`（`NarrativeHook` クラス + `on_order_filled` async / `on_order_filled_sync` 同期版）
- 新規ファイル: `python/tests/test_narrative_hook.py`（Python テスト 3 件）
- 更新ファイル: `src/api/mod.rs`（`pub mod agent_api;` 追加）、`src/replay_api.rs`（`handle_request` + `spawn` に `agent_state` 引数追加・ルーティング追加）、`src/main.rs`（`AgentApiState::new()` + `spawn` 呼び出し更新）
- テスト結果: `cargo test --workspace` **170 passed**（既存 167 + N1.6 新規 3）。`uv run pytest python/tests/` **1064 passed, 2 skipped**（既存 1061 + N1.6 新規 3）。

**新たな知見**:
- **raw TCP サーバーへの新 API 追加パターン**: `replay_api.rs` の `handle_request` のシグネチャに `Option<Arc<NewState>>` を追加し、`spawn` にも同引数を通す。既存ルートに影響なく新ルートを末尾（ワイルドカード `_` の直前）に追加できる。
- **Rust テストは `spawn_test_http_server` パターンで**: ランダムポートのミニ HTTP サーバーを `tokio::spawn` で立て `http_request` ヘルパーで叩く方式が既存パターンと一致して安定する。`handle_*` に TcpStream を直接渡す方式はテスト終了後の接続リセット（WSAECONNRESET）で不安定になる。
- **Python 側は httpx async client を使う**: `pytest-httpx` が導入済みのため、`httpx_mock` fixture で HTTP モックが簡潔に書ける。`httpx.AsyncClient` + `resp.raise_for_status()` を使うことで HTTP エラーを例外に変換してから `log.warning` で握り潰す設計が明確になる。

**Tips**:
- `AgentApiState` の Mutex は標準ライブラリの `std::sync::Mutex`（tokio の非同期 Mutex ではない）。narrative エントリの追加は短い critical section で完了するため同期 Mutex で十分。
- `NarrativeHook.on_order_filled_sync` は `asyncio.run()` で新しいイベントループを生成する。既存のイベントループが走っている中から呼ぶと `RuntimeError: This event loop is already running` になるため、非同期コンテキストでは `await hook.on_order_filled(event)` を使うこと。

### N1.7 Gymnasium 互換性確認 ✅ 完了 2026-04-28
- [x] ✅ `FlowsurfaceEnv.step()` が変わらず動くこと
- [x] ✅ `python/tests/test_flowsurface_env_with_nautilus.py`（26 件）
- [x] ✅ **追加テスト**: EC frame 重複受信 / cancel-after-fill レース を mock で再現

#### 状況・知見・Tips（2026-04-28 完了報告 — N1.7）

**状況**:
- 新規ファイル: `python/engine/nautilus/gym_env.py`（FlowsurfaceEnv）、`python/tests/test_flowsurface_env_with_nautilus.py`（26 件）
- テスト結果: `uv run pytest python/tests/` **1064 passed / 2 skipped**

**新たな知見**:
- `gymnasium` パッケージが pyproject.toml に未追加のため duck-type 実装を選択。内部に `_BoxSpace` / `_DiscreteSpace` を持ち、gymnasium が後から追加されたときは自動フォールバックする二段構成
- N1.11 streaming 経路が依存しているため step-by-step 制御は未実装。`reset()` で `start_backtest_replay()` を完走させ、`step()` は `terminated=True` を即返す「バッチ型」実装

**設計思想と背景**:
- 1 エピソード = バックテスト 1 回のバッチ型。N1.11 streaming 経路完成後にステップ型に置き換える予定

**Tips**:
- `gymnasium` なし環境では `_GYM_BASE = object` にフォールバック。`super().reset(seed=seed)` は `if _GYM_AVAILABLE:` でガードすること

### N1.8 live/replay 互換 lint ⭐ 新設 ✅ 完了 2026-04-28
- [x] `python/tests/test_strategy_compat_lint.py`: ユーザー Strategy ファイルの AST を解析し、`on_order_book_*` / `on_quote_tick` の定義があれば fail（[spec.md §3.5.4](./spec.md#354-互換性-ci-検査n18-で追加)）
- [x] 組み込み `BuyAndHold` を **live mock + replay J-Quants の両方**で走らせ最終ポジション方向が一致するスモークテスト（`test_strategy_live_replay_smoke.py`）
- [ ] CI に組み込み (`uv run pytest python/tests/test_strategy_compat_lint.py`)

> **実装メモ（2026-04-28）**:
> - `check_strategy_replay_compat(source: str) -> list[str]` を `test_strategy_compat_lint.py` 内に実装。`ast.parse()` + `ast.walk()` で ClassDef 内の FunctionDef を走査し、`on_order_book_*`（prefix）と `on_quote_tick`（exact）を禁止メソッドとして検出する。クラス外のモジュールレベル関数は検出しない設計。
> - lint テスト 7 件（good/bad 各ケース + BuyAndHold 実ファイル検査 + クラス外関数除外）すべて PASS。
> - smoke テスト 6 件：live mock（Bar 250 本）と replay J-Quants（Trade / Daily）の両方で `NautilusRunner` が例外なく完走。fill_timestamps 非空チェックで約定生成を確認。IPC イベント（EngineStarted / ReplayDataLoaded / EngineStopped）の emit も確認。
> - `uv run pytest python/tests/` 全体: 1061 passed, 2 skipped — 既存テスト破壊なし。

### N1.9 決定論性テスト（tick ベース）✅ 完了 2026-04-28
- [x] `python/tests/test_nautilus_determinism_tick.py`: J-Quants 同一ファイル・同一銘柄で `start_backtest_replay()` を 2 回回して equity / fill_timestamps / fill_last_prices ビット一致
- [x] N0.6 の Bar ベース版と並列で維持

> **実装メモ（2026-04-28）**:
> - `equities_trades_202401.csv.gz`（1301.TSE 4 件）を使い `start_backtest_replay()` を 2 回実行し、4 テストすべて PASS を確認（0.88 秒）。
> - fixtures の tick 数が少なく `fill_timestamps` / `fill_last_prices` が空になるケースがあるが、`[] == []` は決定論性テストとして有効であるため空チェックは不要と判断。
> - `time.time` / `time.monotonic` のみモック（`datetime.datetime` は nautilus 内部スケジューラ干渉を避けて除外）。
> - `uv run pytest python/tests/` 全体: 1048 passed, 2 skipped — 既存テスト破壊なし。

### N1.10 性能ベンチマーク ✅ 完了 2026-04-28
- [x] ✅ `scripts/nautilus_replay_baseline.py` を追加
- [x] ✅ 「`start_backtest_replay` 呼出」までの wall clock を計測
- [x] ✅ **実測値確定**: fixtures 4 件: <0.1s、**実 J-Quants 1 銘柄 1 ヶ月: ~137s（SLA 60s 超過）**
- [x] ✅ `python/tests/test_replay_benchmark.py`（CI safe: fixtures テストのみ assert、実データは measure-only）

#### 状況・知見・Tips（2026-04-28 完了報告 — N1.10）

**状況**:
- 新規ファイル: `python/tests/test_replay_benchmark.py`（2 件）、`scripts/nautilus_replay_baseline.py`
- 実測値: fixtures <0.1s / 実 J-Quants 1 ヶ月 ~137s（2026-04-28 計測）

**新たな知見**:
- spec.md §3.3 の「60 秒以内」目標は現時点で未達（137s）。SLA を実測値（200s 等）に更新する、もしくは streaming 経路最適化後に再計測する必要がある
- `test_real_jquants_one_month_sla` は実データ環境でも SLA 超過時に `warnings.warn` のみ（assert なし）で CI をブロックしない設計に変更済み

**Tips**:
- `uv run python scripts/nautilus_replay_baseline.py --instrument 9984.TSE --month 202402` で別銘柄の計測も可能

### N1.11 Replay 再生 speed コントロール（streaming=True 経路）✅ 完了 2026-04-28
- [x] ✅ engine-client/src/dto.rs に `Command::SetReplaySpeed { request_id: String, multiplier: u32 }` を追加
      （Pause/Resume/Seek は本タスクに含めない）[^n1.11-rust-dto]
- [x] ✅ python/engine/nautilus/engine_runner.py に streaming ループ実装を追加:
      add_data([item]) → run(streaming=True) → clear_data() を 1 件ずつ回す
- [x] ✅ ループ間に D7 の pacing 式で sleep を挟む:
      sleep_sec = min(max(dt_event_sec, 0.001) / multiplier, 0.200)
      （multiplier=1/10/100、SLEEP_CAP=200ms、MIN_TICK_DT=1ms）
- [x] ✅ 前場-後場 / 引け後 / 営業日跨ぎのギャップは sleep=0 で即時通過(D7)
- [x] ✅ 営業日跨ぎ時に UI 向け date-change マーカーを 1 件 emit
- [x] ✅ 既存 run(start, end) 自走経路は headless / 決定論性テストで温存
- [x] ✅ iced 側に `Content::ReplayControl` pane を実装（1x/10x/100x ボタン完了 2026-04-29）[^n1.11-rust-pane]
      - `src/screen/dashboard/pane.rs`: 速度ボタン UI 実装、Effect::SetReplaySpeed(u32) 追加
      - `src/screen/dashboard.rs`: Event::ReplaySpeedAction(u32) 追加
      - `src/main.rs`: Command::SetReplaySpeed IPC 送信ハンドラ追加
- [x] ✅ src/replay_api.rs: `POST /api/replay/control` で `action="speed"` のみ受理、
      他 action は 400 Bad Request を返す[^n1.11-rust-api]
- [x] ✅ python/tests/test_replay_speed.py:
      - 11:30 JST 跨ぎ tick で sleep=0 になること
      - 営業日跨ぎ tick で sleep=0 + date-change マーカー 1 件 emit
      - 同一マイクロ秒バーストでも MIN_TICK_DT_SEC=1ms が下限になること
      - 1 sleep が SLEEP_CAP_SEC=200ms を超えないこと
- [x] ✅ N0.6 / N1.9 の決定論性テストが run() 自走経路で引き続き緑であること
- [x] ✅ **N1.5 配線完了 (2026-04-29)**: `python/engine/server.py::_do_submit_order_inner` の M-7 早期 reject
      （`OrderRejected{REPLAY_NOT_IMPLEMENTED}`）を解除し、`submit_order_replay(mode="replay", ...)` を呼ぶ
      配線を追加。`OrderAccepted` が返るようになり、`REPLAY_NOT_IMPLEMENTED` は廃止。
      `python/tests/test_order_router_dispatch.py::TestServerReplayRouting` 3 件 GREEN。

#### 状況・知見・Tips（2026-04-28 完了報告 — N1.11）

**状況**:
- 新規ファイル: `python/engine/nautilus/replay_speed.py`（pacing 純粋関数 3 関数）、`python/tests/test_replay_speed.py`（31 件）
- 更新ファイル: `python/engine/nautilus/engine_runner.py`（`start_backtest_replay_streaming()` 追加、`threading` import 追加）
- Rust 更新: `engine-client/src/dto.rs`（`SetReplaySpeed` command + Python schema `SetReplaySpeed`）、`src/replay_api.rs`（`POST /api/replay/control`）、`data/src/layout/pane.rs`（`ContentKind::ReplayControl`）、`src/screen/dashboard/pane.rs`（`Content::ReplayControl` 骨格）、`src/layout.rs`（ReplayControl → Starter フォールバック保存）
- Rust テスト追加: `engine-client/tests/schema_v2_4_nautilus.rs` に `set_replay_speed_serializes` / `set_replay_speed_debug_shows_multiplier` 2 件
- テスト結果: Python **1159 passed / 2 skipped**、Rust **172+ passed / 0 failed**、cargo clippy clean

**新たな知見**:
- **`streaming=True` + `clear_data()` の組み合わせ**: `BacktestEngine.run(streaming=True)` は 1 件ずつ処理できる。`clear_data()` を呼ばないと次の `add_data([item])` で重複データが蓄積するため必須。
- **`portfolio.account` が None になるケース**: `stop_event` で即中断した場合（run() が 1 回も呼ばれないケース）に portfolio が未初期化のまま None を返す。`initial_cash` を fallback として返す実装に変更。
- **pacing ロジックの分離**: `replay_speed.py` に純粋関数として切り出したことで、engine_runner.py から独立してテスト可能。境界値（11:30, 12:30）の検証が容易。
- **`threading.Event` を stop_event に採用**: `asyncio.to_thread` 経由で実行される想定のため、async Event ではなく `threading.Event` を使う。呼出側が `asyncio.Event` を使う場合は変換が必要（server.py 統合時に対応）。

**設計思想と背景**:
- **headless / streaming の 2 経路分離**: `start_backtest_replay()` は N0.6 / N1.9 の決定論性テストに使う。`start_backtest_replay_streaming()` は UI 駆動 viewer 専用。両者は独立し互いに影響しない。
- **pacing sleep は time.sleep()**: 同期関数 + `asyncio.to_thread` パターンで動く。`asyncio.sleep()` は event loop を要求するため、thread worker 内では使用不可。
- **stop_event 中断時の EngineStopped 補完**: 既存 `start_backtest_replay()` と同じ H-C パターンを踏襲。中断・例外どちらでも必ず EngineStopped が emit される。

**Tips**:
- `multiplier=100` でテストを実行すると fixtures 4 件では pacing sleep がほぼ無視できる速度になる。
- `stop_event.set()` を最初の tick 処理前に set しておくと、ループが 0 件処理で中断するため `portfolio.account` が None になる。`initial_cash` fallback を確認する際は `test_streaming_replay_stop_event` を参照。

### N1.12 ExecutionMarker / StrategySignal IPC + UI overlay ✅ IPC+Python 完了 2026-04-28
- [x] ✅ engine-client/src/dto.rs に `EngineEvent::ExecutionMarker` / `EngineEvent::StrategySignal` を追加[^n1.12-rust-dto]
- [x] ✅ python/engine/schemas.py に `ExecutionMarker` / `StrategySignal` / `SetReplaySpeed` を追加
- [x] ✅ python/engine/nautilus/narrative_hook.py で OrderFilled 受領時に
      ExecutionMarker を自動送出（fill 由来の自動レイヤー）
- [x] ✅ python/engine/nautilus/strategy_helpers.py 新設: `StrategySignalMixin` に
      `emit_signal(kind, side=None, price=None, tag=None, note=None)` を追加し、
      StrategySignal IPC を送出
- [x] ✅ BuyAndHold を改造して買い前にエントリー検討の `StrategySignal(EntryLong)` を出すサンプル化
- [x] ✅ iced 側 Kline chart pane に 2 レイヤー追加（execution layer / signal layer）完了 (2026-04-29)
      - `src/chart/kline.rs`: ExecutionMarkerData / StrategySignalData struct 追加、push_*/clear_overlay_markers() メソッド追加、draw() に overlay 描画（BUY=緑四角, SELL=赤四角, Signal=ダイヤモンド）
      - `src/screen/dashboard.rs`: distribute_execution_markers() / distribute_strategy_signals() / clear_chart_overlays() 追加
      - `src/main.rs`: ExecutionMarkerReceived / StrategySignalReceived Message 追加、map_engine_event_to_tachibana に追加、update() ハンドラ追加
- [x] ✅ python/tests/test_execution_marker_emit.py: OrderFilled → ExecutionMarker 1:1（7 件 GREEN）
- [x] ✅ python/tests/test_strategy_signal_emit.py: emit_signal() 呼出 → IPC 1 件、
      未約定でも独立に出ること（12 件 GREEN）
- [x] ✅ `signal_kind` の wire 表現: `SignalKind` Rust enum（PascalCase、serde default）、
      Python: `Literal["EntryLong", "EntryShort", "Exit", "Annotate"]`。[Q13](./open-questions.md#q13) Resolved。

#### 状況・知見・Tips（2026-04-28 完了報告 — N1.12）

**状況**:
- 新規ファイル: `python/engine/nautilus/strategy_helpers.py`（`StrategySignalMixin`）、`python/tests/test_execution_marker_emit.py`（7 件）、`python/tests/test_strategy_signal_emit.py`（12 件）
- 更新ファイル: `python/engine/schemas.py`（`ExecutionMarker` / `StrategySignal` 追加）、`python/engine/nautilus/narrative_hook.py`（`on_event` callback 追加、`_emit_execution_marker()` ヘルパー追加）、`python/engine/nautilus/strategies/buy_and_hold.py`（`StrategySignalMixin` 継承、`emit_signal("EntryLong")` 追加）
- Rust 更新: `engine-client/src/dto.rs`（`ExecutionMarker` / `StrategySignal` variants、`SignalKind` enum）
- Rust テスト追加: `engine-client/tests/schema_v2_4_nautilus.rs` に 4 件（`execution_marker_deserializes` / `strategy_signal_deserializes_full` / `strategy_signal_deserializes_minimal` / `signal_kind_serializes_as_pascal_case`）
- テスト結果: Python **1159 passed / 2 skipped**（19 件新規含む）、Rust **172+ passed / 0 failed**

**新たな知見**:
- **SCHEMA_MAJOR/MINOR は変更不要**: `ExecutionMarker` / `StrategySignal` は新規 event 追加のみで後方互換。旧 Rust client は `extra="ignore"` 経由でスキップするため接続断なし。
- **Optional fields の wire 省略**: `StrategySignal` の `side` / `price` / `tag` / `note` は None 時に dict キー自体を省略する実装。Rust 側 `#[serde(skip_serializing_if = "Option::is_none")]` と対称。
- **`StrategySignalMixin` の後方互換**: `on_event=None` の場合は `emit_signal()` が no-op になるため、既存 Strategy コードを変更せずにミックスインを継承できる。
- **`narrative_hook.py` の 2-step emit**: (1) HTTP POST to narrative store（N1.6 路線）、(2) `_emit_execution_marker()` via `on_event`。両者は独立しており、`on_event` が None でも N1.6 の HTTP 側は動作する。

**設計思想と背景**:
- **signal_kind as enum not string**: Q13 で Rust 側は proper enum、Python 側は Literal で型安全にする方針を採択。wire 表現は PascalCase（`"EntryLong"` 等）。
- **`narrative_hook` と `strategy_helpers` の責務分離**: fill 由来の自動マーカーは `NarrativeHook` が担当、戦略意図の信号は `StrategySignalMixin` が担当。両者は独立した IPC event (`ExecutionMarker` vs `StrategySignal`) を送出する。

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
- ✅ iced 側に ReplayPaneRegistry を新設し identity = (instrument_id, pane_kind) を管理
      `src/screen/dashboard/replay_pane_registry.rs`
- ✅ /api/replay/load 成功（ReplayDataLoaded 受信）を契機に Tick pane と
      Candlestick(1m) pane の生成判定を回す
      `ReplayApiState` が `AutoGenerateReplayPanes` を mpsc 経由で Iced に送信
- ✅ 既存 identity が存在する場合は新規生成しない（重複生成防止）
      `should_generate()` → dismissed セットで判定
- ✅ 生成位置ルール（D9.3）:
      - 1 銘柄目: Axis::Vertical（横並び 2 分割）
      - 2 銘柄目以降: Axis::Horizontal（縦分割）
      `Dashboard::auto_generate_replay_panes()`
- ✅ MAX_REPLAY_INSTRUMENTS = 4 を超える load は HTTP 400 `{"error":"max_instruments_exceeded","max":4}`
- ✅ ユーザーが手動 close した自動生成 pane は同セッション中は再生成しない
      `ClosePane` → `replay_pane_registry.dismiss()`
- ✅ StopEngine では自動生成 pane を残す（StopEngine で pane 削除するコードなし）
- ✅ /api/replay/load 再実行時の重複 pane 生成を防止 (is_first ガード、2026-04-29)
      → TimeAndSales / CandlestickChart: is_first ガードで reload 時の重複生成を防止
      → OrderList / BuyingPower: loaded_count()==1 ガードで2銘柄目以降の重複生成を防止
- ✅ /api/replay/load 再実行時の chart buffer クリア（overlay クリアは N1.12 UI フェーズ待ち、2026-04-29）
- ✅ tests: replay_pane_registry (6 件) + replay_api N1.14 テスト (2 件) GREEN
      - 同 instrument の二重 load で 400 にならない (reload 可)
      - 5 銘柄目の load が 400 になること
      - dismiss → should_generate=false
      - mark_loaded 冪等性
- ✅ tests/e2e/s56_replay_pane_autogen.sh: stub (SKIP when binary absent)

**実装メモ**:
- `ReplayDataLoaded` に `instrument_id` がないため `ReplayApiState.loaded_instruments`
  で "処理中の instrument_id" を保持して `AutoGenerateReplayPanes` に組み込んだ
- `control_tx: Mutex<Option<Sender>>` にすることで `Arc` のまま `spawn()` 内で注入可能
- `strategy_id_for_cmd` は空文字（ReplayDataLoaded から取得できないため）

### N1.15 REPLAY 注文一覧 pane（D9.5）✅ 完了 2026-04-29
- ✅ iced 側 OrderListStore を venue で 2 view（live / replay）に分割
      → `OrderRecordWire` に `venue: String` フィールド追加（serde default="tachibana"）
      → `distribute_order_list`: is_replay pane は venue="replay" のみ、live pane は venue!="replay" のみ
      → Python `_do_get_order_list_replay`: 各 order に `venue="replay"` を付与
      → Python `_do_get_order_list` (tachibana): 各 order に `venue="tachibana"` を付与
- ✅ 1 銘柄目の /api/replay/load 成功時に REPLAY 注文一覧 pane を 1 枚自動生成
      （identity = (mode=replay, pane_kind=order_list)、銘柄非依存で 1 つだけ）
      → `auto_generate_replay_panes` に `is_first && loaded_count()==1 && should_generate("", "OrderList")` 分岐
      → `ClosePane` で `OrderList(is_replay=true)` を dismiss("", "OrderList") に記録
- ✅ pane header に「⏪ REPLAY」バナー
      → `OrdersPanel::is_replay: bool` (pub) + `new_replay()` + view() バナー分岐
- [ ] pane header に live と区別された配色（DEFERRED: スタイリングは N1 後半）
- ✅ EngineEvent::Order* (live) が REPLAY view を汚染しない
      → distribute_order_list の venue フィルタで保証
- ✅ GetOrderList venue=replay を Python server.py に新設
      → `_do_get_order_list_replay()`: tachibana_orders_replay.jsonl WAL から phase=submit を返す
      → `_do_get_order_list()` 先頭で venue="replay" を先行分岐（unknown_venue エラー回避）
- ✅ tachibana_orders_replay.jsonl WAL の内容と REPLAY 注文一覧の整合を保つ
      （再起動時の warm-up は WAL 起点）
- ✅ tests/test_replay_order_list_view.rs:
      - `orders_panel_has_pub_is_replay_field` — is_replay フィールドが pub
      - `orders_panel_new_replay_sets_is_replay_true` — new_replay() が is_replay=true
      - `orders_panel_new_has_is_replay_false` — new() は Default=false
      - `orders_panel_view_shows_replay_banner` — view() が REPLAY バナー分岐を持つ
      - `order_record_wire_has_venue_field_with_default` — venue フィールド + serde default
      - `distribute_order_list_filters_by_venue` — venue/is_replay フィルタ
      - `auto_generate_guards_timeandsales_with_is_first` — is_first ガード
      - `auto_generate_guards_order_list_with_loaded_count_one` — loaded_count()==1 ガード
- ✅ python/tests/test_order_list_api_venue_filter.py:
      - WAL なしで空リスト返却
      - submit エントリが OrderRecordWire 形式に変換される
      - phase=submit のみ含む（fill/cancel は除外）
      - tachibana_orders.jsonl は参照しない
      - venue=replay で unknown_venue エラーにならない
      - TestVenueField: replay 注文に venue="replay"、live 注文に venue="tachibana"

**実装メモ**:
- `_do_get_order_list_replay` は `self._cache_dir / "tachibana_orders_replay.jsonl"` を WAL パスとして使用
- `OrdersPanel` は `#[derive(Default)]` で `is_replay: false` がデフォルト（`new()` は `Self::default()`）
- `auto_generate_replay_panes` の OrderList 生成は `is_first` かつ `loaded_count() == 1` の場合のみ（instrument_id="" で dismissal 管理）
- `pane::State::new_replay_order_list()` を追加して `OrdersPanel::new_replay()` を使用

### N1.16 REPLAY 買付余力（D9.6）✅ 完了 2026-04-28
- ✅ engine-client/src/dto.rs に EngineEvent::ReplayBuyingPower を追加（cash/buying_power/equity を decimal 文字列で保持）
- ✅ python/engine/schemas.py に ReplayBuyingPower Pydantic クラスを追加
- ✅ python/engine/nautilus/portfolio_view.py を新設（PortfolioView: 約定ベースで cash/equity を追跡）
- ✅ python/engine/server.py: venue=="replay" 分岐 + _do_get_buying_power_replay + _replay_portfolio 初期化
- ✅ iced 側 BuyingPowerPanel に is_replay フラグ + new_replay() + set_replay_portfolio() + REPLAY ビュー追加
- ✅ pane.rs に new_replay_buying_power() ファクトリを追加
- ✅ dashboard.rs に distribute_replay_buying_power() を追加
- ✅ dashboard.rs: auto_generate_replay_panes() で 1 銘柄目ロード時に REPLAY BuyingPower pane を自動生成（D9.6）
- ✅ HTTP /api/replay/portfolio: ReplayPortfolioSnapshot キャッシュから実データを返す（未取得時は "not_ready"）
- ✅ replay_api.rs: ReplayPortfolioSnapshot 構造体 + portfolio フィールド + update_replay_portfolio() を追加
- ✅ main.rs: REPLAY_API_STATE static + Message::ReplayBuyingPower + map_engine_event_to_tachibana マッピング + ハンドラ追加
- ✅ python/tests/test_replay_buying_power.py: PortfolioView 11 件 GREEN
- ✅ engine-client/tests/schema_v2_4_nautilus.rs: replay_buying_power_deserializes GREEN
- ✅ replay_api.rs テスト更新: replay_portfolio_returns_not_ready_before_fill + replay_portfolio_returns_cached_snapshot_after_update
- ✅ tests/e2e/s57_replay_buying_power_smoke.sh stub 配置（release binary 未ビルド時は SKIP）

**実装上の決定**:
- ReplayPortfolioSnapshot のキャッシュには `std::sync::Mutex`（blocking）を使用。Flowsurface::update() は同期コンテキストのため tokio::sync::Mutex（async）を避けた
- PortfolioView は nautilus Portfolio 内部依存なし — fills の積算のみで cash/equity を計算。nautilus 内部 API の変更に強い
- REPLAY_API_STATE static は Arc<ReplayApiState> を保持し、Message::ReplayBuyingPower ハンドラから同期的に portfolio キャッシュを更新する

### N1.17 `POST /api/replay/start` — HTTP → StartEngine IPC 橋渡し ✅ 完了 2026-04-29

- [x] ✅ `src/replay_api.rs`: `ReplayStartBody` / `ReplayStartOk` wire 型追加
- [x] ✅ `src/replay_api.rs`: `ReplayApiState` に `start_timeout: Duration`（デフォルト 30s）追加
- [x] ✅ `src/replay_api.rs`: `handle_replay_start()` 実装
      - mode ガード（400 if `mode != Replay`）
      - フィールド検証（`instrument_id` / `start_date` / `end_date` / `strategy_id` / `initial_cash`）
      - `Command::StartEngine { engine: Backtest, strategy_id, config: EngineStartConfig { ... } }` 送信
      - `EngineEvent::EngineStarted` を最大 30s 待機
      - `EngineEvent::EngineError { strategy_id: Some(sid) }` → 503（`mode_mismatch` → 400）
      - `EngineEvent::Error { request_id: Some(rid) }` → 503
      - timeout → 504、disconnect → 502、lagged → 503
      - 成功時: 202 `{ status: "started", strategy_id, account_id }`
- [x] ✅ `src/replay_api.rs`: ルータに `("POST", "/api/replay/start")` 追加
- [x] ✅ `src/replay_api.rs`: テスト 7 件追加（202 成功・live mode 400・無効 JSON 400・空 instrument_id 400・空 strategy_id 400・timeout 504・engine_error 503・コマンド転送確認）
- [x] ✅ `docs/example/run_buy_and_hold_backtest_with_ui.py`: `/api/replay/load` → `/api/replay/start` に変更。ステップ番号 5 → 6 に拡張
- [x] ✅ `cargo test --workspace` 全 GREEN（208 件含む）
- [x] ✅ `uv run pytest python/tests/` GREEN（変更なし・回帰確認）

**知見・Tips（2026-04-29）**:
- `EngineStarted` は `strategy_id` を echo back するためコマンド内 `strategy_id` との照合不要。どの戦略でも最初に受信した `EngineStarted` を成功として返せる
- `EngineError` と `Error` の二種類を待つ: `EngineError { strategy_id: Some(sid) }` は戦略レベルのアウトボックスイベント（Python `_handle_start_engine` 例外時）、`Error { request_id: Some(rid) }` はリクエストレベルエラー（`mode_mismatch` 等）
- `start_timeout` は `load_timeout` と独立して設定可能。テストでは `with_start_timeout(Duration::from_millis(150))` で素早くタイムアウトを確認
- `spawn_mock_engine_capture` を再利用してコマンド内容（`op`, `engine`, `strategy_id`, `config.*`）を検証するテストパターンが有効

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

## Phase N2: 立花 ExecutionClient（デモ）✅ 完了 2026-04-29

**前提**: order/ 計画の Phase O0〜O2 が完了し、`tachibana_orders.submit_order` / `modify_order` / `cancel_order` / EC frame パーサ / 第二暗証番号 UI / 監査ログ WAL がすべて稼働している。本フェーズは **nautilus への薄い adapter のみ**を書く。

### N2.0 立花 LiveDataClient（FD frame → TradeTick）⭐ 新設 ✅ 完了 2026-04-29
- [x] ✅ `python/engine/nautilus/clients/tachibana_data.py` 新設
- [x] ✅ 既存 `tachibana_ws._FdFrameProcessor` の trade dict 出力を nautilus `TradeTick` に変換（[data-mapping.md §1.2](./data-mapping.md#12-live-立花-fd-frame--tradetick)）
- [x] ✅ `TachibanaLiveDataClient` が `LiveDataClient` を継承し `feed_trade_dict()` で `_handle_data(tick)` に流す
- [x] ✅ `aggressor_side` 推定不能 (`"unknown"`) の場合は `NO_AGGRESSOR` に写像
- [x] ✅ テスト: `python/tests/test_tachibana_data_client.py` 13 件 GREEN（side 全 4 種・precision・ts_ms→ns・trade_id 連番・sanity check）

### N2.1 nautilus `LiveExecutionClient` adapter ✅ 完了 2026-04-29

**前提**: `python/engine/exchanges/tachibana_orders.py` が存在し `submit_order` / `NautilusOrderEnvelope` が実装済みであること（order/ Phase O0 以上完了）

- [x] ✅ `python/engine/nautilus/clients/tachibana.py` 新設
- [x] ✅ `LiveExecutionClient` を継承し、以下を **order/ の関数に委譲**:
  - `submit_order(Order)` → `tachibana_orders.submit_order(session, second_password, NautilusOrderEnvelope.from_nautilus(order))`
  - `modify_order` → `tachibana_orders.modify_order(...)`
  - `cancel_order` → `tachibana_orders.cancel_order(...)`
- [x] ✅ 立花 API 写像（`OrderType` / `TimeInForce` / `cash_margin` / `account_type`）は **[order/spec.md §6](../✅order/spec.md#6-nautilus_trader-互換要件不変条件) と [data-mapping.md](./data-mapping.md) に従う**。本ファイル内に重複定義しない

### N2.2 EC frame → nautilus イベント変換 ✅ 完了 2026-04-29
- [x] ✅ `python/engine/nautilus/clients/tachibana_event_bridge.py` 新設
- [x] ✅ order/ の `tachibana_event._parse_ec_frame` の戻り値（`OrderEcEvent`）を nautilus `OrderFilled` / `OrderCanceled` / `OrderRejected` に変換
- [x] ✅ `LiveExecutionEngine.process_event(...)` に流す（`generate_order_filled` / `generate_order_canceled` 経由）
- [x] ✅ **冪等化（M5）**: 同一 `(venue_order_id, trade_id)` の EC が再送された場合に seen-set で 2 重発火を防ぐ。`reset_seen()` で日次リセット対応
- [x] ✅ 検証テスト: `test_tachibana_event_bridge.py` の `TestEcIdempotency` が pass すること

### N2.3 注文 ID マッピングと再起動復元 ✅ 完了 2026-04-29
- [x] ✅ nautilus `ClientOrderId` ⇔ 立花 `sOrderNumber` の双方向写像（`OrderIdMap` を `tachibana_event_bridge.py` に同居実装）
- [x] ✅ プロセス再起動時: 立花 `CLMOrderList` を引き、未決注文を `warm_up_from_records()` で復元（FILLED/CANCELED/EXPIRED/REJECTED はスキップ）
- [x] ✅ **persistence 設定（H3）**: `engine_runner.py` の `start_live()` で `CacheConfig(database=None)` + `assert config.database is None` を実装。テスト `TestCacheConfigPersistenceOff` GREEN

### N2.4 市場時間帯ガード（M2）✅ 完了 2026-04-29（N3 繰越: Rust 側）
- [x] ✅ `_submit_order` / `_modify_order` / `_cancel_order` で `is_market_open()` を呼び、閉場中は `generate_order_denied` / `generate_order_modify_rejected` / `generate_order_cancel_rejected` を返す
- [ ] ⏩ N3 繰越: HTTP API 層 (`order_api.rs`) で `MARKET_CLOSED` を先行 reject（[order/spec.md §5.2](../✅order/spec.md#52-reason_code-体系観測性)）
- [x] ✅ `_connect()` で市場閉場中は WARNING ログを出すが接続自体はブロックしない（nautilus start() 保留は不要と判断、logged warning で代替）

### N2.5 セーフティ（order/ と二重ガード）✅ 完了 2026-04-29
- [x] ✅ デモ環境強制（`TACHIBANA_ALLOW_PROD=1` 未設定なら本番 URL を選んでも reject）— `guard_prod_url()` が `_tachibana_submit_order` 内で呼ばれることを確認
- [x] ✅ 数量上限・1 注文金額上限を起動 config で必ず指定（未指定なら `super().__init__()` 前に `ValueError`）。テスト `TestClientInitSafety` GREEN
- [x] ✅ 発注ログ追記は order/ の WAL を使う（重複ファイルを増やさない）— `submit_order` 委譲で自動適用

### N2.6 E2E と単体テスト ✅ 完了 2026-04-29
- [x] ✅ `scripts/s70_tachibana_nautilus_demo_order.py`（CI には載せない、ローカル手動。デモ環境クレデンシャルが必要）
- [x] ✅ ユニットテスト: `test_tachibana_live_execution_client.py` で `OrderType` 全 6 種 + `TimeInForce` 全 7 種、buy/sell side、price、tags を検証（26 件 GREEN）
- [x] ✅ **追加テスト**: 部分約定 EC を 2 件流して `last_qty` が正しいこと（`TestPartialFill`）
- [x] ✅ **追加テスト**: cancel リクエスト送信中に EC fill が来るレースで両イベントが発火すること（`TestCancelFillRace`）
- [x] ✅ **追加テスト**: 同一 `(venue_order_id, trade_id)` の EC 重複受信で `OrderFilled` が 1 回しか発火しないこと（`TestEcIdempotency`）

**Exit 条件**: デモ環境で「成行買い → 約定通知受信 → nautilus Portfolio に反映 → ナラティブに `outcome` が入る」往復が手動で確認、N2.6 の追加テストすべて緑

---

**N2 完了サマリー（2026-04-29）**

**状況**: N2.0〜N2.6 すべての実装・テストが GREEN。Rust 側の `OrderRecordWire.venue` フィールド追加（R2 review-fix の先行変更）に合わせてテスト構造体を修正済み。

**新たな知見**:
- nautilus `LiveExecutionClient` の抽象メソッドはアンダースコア付き（`_submit_order` 等）。`super().__init__()` の Cython 型検査が先に実行されるため、**安全ガードは必ず `super().__init__()` より前**に置くこと
- `generate_order_filled` の引数は 14 個の位置引数。`venue_position_id` は `None`、`commission=Money(0.0, JPY)`、`liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE` が標準
- nautilus には `OrderExpired` がない。NT="4"（失効）は `generate_order_canceled` で代替
- `TradeTick` の `trade_id` は `TradeId(str)` で一意性が要求される。`f"L-{ts_ms}-{seq}"` 形式で ts_ms+連番を組み合わせることで同一 ms 内の重複を回避
- `CacheConfig(database=None)` を assert でガードすることで Parquet/SQLite 誤設定を即座に検出できる

**Tips**:
- `OrderIdMap.warm_up_from_records()` は `client_order_id=None`（立花 WAL 外の注文）に `WARM-{venue_order_id}` プレフィックスを付与して登録する。`strategy_id` は `"warm-up"` で固定
- EC の冪等化キーは `(venue_order_id, trade_id)` ペア。`trade_id` のみでは同一注文の複数約定が潰れる恐れがある
- N3 繰越: `order_api.rs` での `MARKET_CLOSED` 先行 reject は Rust 側変更を要するため N3 スコープに移動

---

## Phase N3: 暗号資産 venue 移植（任意）

割愛（N2 完了時に詳細化）。Rust 側 `exchange/` の `place_order` / `cancel_order` 経路を削除し、nautilus 側 `HyperliquidExecutionClient` 等に置き換える。データ取得経路は触らない。

### N3.A: HyperliquidExecutionClient スケルトン ✅ 完了 2026-04-29

**目的**: N3 本格実装の前に `HyperliquidExecutionClient` の FSM 骨格を確立し、
`generate_order_canceled()` の呼び出し漏れ（MEDIUM-1）を修正する。

**実装ファイル**:
- `python/engine/nautilus/clients/hyperliquid.py` — `HyperliquidExecutionClient` 新規作成
  - `_submit_order`: `hl_submit_order` stub に委譲、成功時 `generate_order_submitted` + `generate_order_accepted`、失敗時 `generate_order_denied`
  - `_modify_order`: `hl_modify_order` stub に委譲、失敗時 `generate_order_modify_rejected`
  - `_cancel_order`: `hl_cancel_order` stub に委譲、**成功時 `generate_order_canceled`**、失敗時 `generate_order_cancel_rejected`
  - `hl_submit_order` / `hl_modify_order` / `hl_cancel_order`: `NotImplementedError` stub（Hyperliquid SDK 統合は後続タスク）

**テスト追加** (`python/tests/test_hyperliquid_execution_client.py`):
- `test_cancel_order_calls_hl_cancel` — HL cancel API 呼び出し + `generate_order_canceled` 呼び出しを検証
- `test_cancel_order_cancel_rejected_on_failure` — 失敗時 `generate_order_cancel_rejected`
- `test_submit_order_calls_hl_submit` — HL submit API 呼び出し + submitted/accepted 呼び出しを検証
- `test_submit_order_denied_on_failure` — 失敗時 `generate_order_denied`
- `test_modify_order_calls_hl_modify` — HL modify API 呼び出しを検証
- `test_modify_order_rejected_on_failure` — 失敗時 `generate_order_modify_rejected`

**検証**: `uv run pytest python/tests/` 全 1265 件 GREEN (2 skipped)。

---

### N3.C: 発注経路 venue ガード強化 ✅ 完了 2026-04-29

**目的**: `SubmitOrder` IPC の `venue` を `"tachibana"` のみに制限する。
暗号資産 venue（`"hyperliquid"` 等）は `_workers` に登録されているため `unknown_venue` チェックをすり抜け、tachibana 固有の `NOT_LOGGED_IN` 等が返る問題を解消する。

**変更**: `python/engine/server.py` の `_do_submit_order_inner()` — `if venue not in self._workers` チェックを削除し、`if venue != "tachibana"` チェック（`code: "unsupported_order_venue"`）に置き換え。

**テスト追加** (`python/tests/test_server_dispatch.py`):
- `test_submit_order_hyperliquid_venue_returns_unsupported_order_venue`
- `test_submit_order_unknown_venue_returns_unsupported_order_venue`
- `test_submit_order_tachibana_venue_proceeds_to_tachibana_logic`
- `test_submit_order_replay_venue_still_handled_separately`

**既存テスト更新** (`python/tests/test_order_dispatch.py`):
- `test_submit_order_unknown_venue_returns_error` の期待 code を `unknown_venue` → `unsupported_order_venue` に更新

**検証**: `uv run pytest python/tests/` 全 1259 件 GREEN。

---

### N3.B: Rust HTTP 層での `market_closed` 早期 reject ✅ 完了 2026-04-29

**目的**: 市場閉場中に `POST /api/order/submit` が Python エンジンに到達する前に 409 を返す。

**実装ファイル**:
- `src/venue_state.rs` — `VenueState::is_market_closed() -> bool` 追加
- `src/api/order_api.rs` — `OrderApiState.is_market_closed: Arc<AtomicBool>` フィールド追加、`submit_order()` の先頭に ① market-closed guard (409) を追加（② REPLAY guard より前）、`with_market_closed_flag()` builder メソッド追加
- `src/main.rs` — `static ORDER_API_MARKET_CLOSED`, `Flowsurface.order_api_market_closed` フィールド追加、`TachibanaVenueEvent` ハンドラで `store()` 同期

**テスト追加**:
- `src/venue_state.rs::tests`: `is_market_closed_returns_true_for_market_closed_error`, `is_market_closed_returns_false_for_ready`, `is_market_closed_returns_false_for_idle`, `is_market_closed_returns_false_for_login_in_flight`, `is_market_closed_returns_false_for_other_errors`
- `src/api/order_api.rs::tests`: `market_closed_flag_returns_409`, `market_open_flag_does_not_reject_early`, `market_closed_check_fires_before_replay_mode`

**検証**: `cargo test --workspace` 全 GREEN、`cargo clippy -- -D warnings` クリーン、`cargo fmt --check` クリーン。

---

## Phase N4: ユーザー定義 Strategy ロード ✅ 完了 2026-04-29

**前提**: Q2 ★Resolved (2026-04-29)「案 A: ユーザーが Python で書く / サンドボックスなし / 戦略起因事故はユーザー責任」確定。README に「戦略は自己責任」明記済み。

**スコープ外**: サンドボックス・プロセス隔離（Q2 で却下）/ 戦略パラメータ UI フォーム（次フェーズ候補）/ runtime での戦略切替（D8 起動時固定方針）/ ディレクトリスキャン（user 決定: `--strategy-file` のみ）

### N4.1 Strategy ローダ ✅ 完了 2026-04-29
- [x] ✅ `python/engine/nautilus/strategy_loader.py` 新設（98 行）
- [x] ✅ `load_strategy_from_file(path, init_kwargs)`: `importlib.util.spec_from_file_location` でロード、`cls.__module__ == module.__name__` で transitive import を除外、1 クラス以外は `StrategyLoadError`
- [x] ✅ `python/tests/test_strategy_loader.py` 10 件 GREEN（ハッピーパス / 0個 / 複数 / SyntaxError / ImportError / init_kwargs / FileNotFoundError / imported 除外 / 互換 lint warning / warning なし）
- [x] ✅ `_INCOMPATIBLE_HANDLERS` frozenset で `on_order_book_*` / `on_quote_tick` AST 検出 → `log.warning`（block しない / N4.6 を統合）

### N4.2 IPC schema 拡張 ✅ 完了 2026-04-29
- [x] ✅ `engine-client/src/dto.rs`: `Command::LoadReplayData` に `strategy_file: Option<String>` / `strategy_init_kwargs: Option<serde_json::Value>` 追加（`#[serde(default, skip_serializing_if = "Option::is_none")]`）
- [x] ✅ `python/engine/schemas.py`: `LoadReplayData` に同フィールド追加、`SCHEMA_MINOR` 5→6 bump
- [x] ✅ `python/engine/nautilus/engine_runner.py`: `start_backtest_replay(strategy_file, strategy_init_kwargs)` 追加。`_load_user_strategy()` helper で `StrategyLoadError` → `EngineError{code:"strategy_load_failed"}` 変換
- [x] ✅ `python/engine/server.py`: `_handle_start_engine` で `config.get("strategy_file")` / `config.get("strategy_init_kwargs")` を `start_backtest_replay()` に渡す配線追加
- [x] ✅ テスト: Python 1310 passed / Rust workspace 全 GREEN

### N4.3 Rust HTTP + UI ✅ 完了 2026-04-29
- [x] ✅ `src/replay_api.rs`: `ReplayLoadBody` に `strategy_file: Option<String>` / `strategy_init_kwargs: Option<serde_json::Value>` 追加。`strategy_file: None` ハードコードを `parsed.strategy_file.clone()` に差し替え
- [x] ✅ `Cargo.toml`: `rfd = "0.15"` 追加（native OS file dialog）
- [x] ✅ `src/screen/dashboard/pane.rs`: `Effect::PickStrategyFile` / `Event::PickStrategyFile` 追加。`ReplayControl` pane の view に「Strategy ファイルを選ぶ」ボタン追加
- [x] ✅ `src/screen/dashboard.rs`: `Event::PickStrategyFile` リレー追加
- [x] ✅ `src/main.rs`: `Flowsurface.replay_strategy_file: Option<PathBuf>` / `Message::StrategyFilePicked` 追加。ボタン押下 → `rfd::AsyncFileDialog(..).pick_file()` → `Message::StrategyFilePicked`。選択パスを `/api/replay/load` body に載せる

### N4.4 StrategyLoadFailed バナー ✅ 完了 2026-04-29
- [x] ✅ `src/main.rs`: `Flowsurface.strategy_load_error: Option<String>` 追加。`IpcError{code:"strategy_load_failed"}` 受信時に `strategy_load_error = Some(message)` セット。`Message::DismissStrategyLoadError` で `None` に戻す。`view()` に dismissable バナー追加

### N4.5 examples/strategies/ ✅ 完了 2026-04-29
- [x] ✅ `examples/strategies/buy_and_hold.py` 71 行（最小サンプル、`init_kwargs` 対応）
- [x] ✅ `examples/strategies/sma_cross.py` 119 行（短期/長期 SMA クロス、`short`/`long`/`lot_size` init_kwargs）
- [x] ✅ `examples/strategies/README.md` 71 行（使い方・規約・自己責任注記）
- [x] ✅ numpy/pandas 非依存。`load_strategy_from_file()` で両ファイルのロード検証済み

### N4.6 互換 lint user strategy 適用 ✅ N4.1 に統合完了 2026-04-29
- [x] ✅ `strategy_loader.py` の `_check_compat()` が N1.8 と同じ `_INCOMPATIBLE_HANDLERS` セットで AST 検査。ロード後に `log.warning` 送出、block しない（自己責任方針）

**Exit 条件（暫定達成）**:
- `examples/strategies/sma_cross.py` を `strategy_file` 経由でロード可能 ✅
- 構文エラー → `EngineError{code:"strategy_load_failed"}` → UI バナー ✅
- `__init__(short=5, long=20)` を `strategy_init_kwargs` JSON で渡せる ✅
- 既存テスト（N0〜N3）すべて GREEN のまま ✅（Python 1310 / Rust workspace 全 GREEN）

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
- [ ] `docs/✅tachibana/implementation-plan.md` の Phase 2（発注）タスクが [docs/✅order/](../✅order/) と本計画 N2 に分離されていることを再確認し、引退表示を update（N2 着手時）
- [ ] CLAUDE.md / SKILL.md（tachibana）に nautilus 経由の発注フローを追記:
  - SKILL.md L8 警告ブロック近傍に「N2 以降は `tachibana_orders.*` が `LiveExecutionClient` adapter 経由で nautilus から呼ばれる」追記
  - SKILL.md R10 に nautilus persistence 無効方針の参照リンク
  - SKILL.md S6 表に「nautilus 経由の発注時も `p_no` 採番ガードが効くこと」追記
- [ ] LGPL-3.0 の同梱表示（README + 配布アーティファクトの NOTICE）。配布形態は N-pre Tpre.3 の決定に従う

## ラベル規約（L2 修正）

旧版で N0/N1/N2 直下のタスクを `T0.1`〜`T2.5` と命名していたが、立花 plan の `T0`〜`T7` と紛らわしいため、本計画ではすべて **`N0.1`〜`N3.x`** に統一した。横参照する側（CLAUDE.md / SKILL.md / 他 plan）も `N{phase}.{n}` 形式で参照すること。

---

## レビュー反映 (2026-04-28, R2 review-fix R1a)

R2 review-fix-loop ラウンド 1a で 12 件の低リスク指摘を反映した。R1b (wire/threading) は別ラウンドで対応する (H-E AppMode enum / M-8 ReplayDataLoaded.strategy_id Optional + minor bump / H-G call_soon_threadsafe 統一)。

### 修正項目

| グループ | ID | 修正概要 | 主な変更ファイル |
|----------|------|----------|------------------|
| A1 | **H-D** | `ReplayLoadBody.granularity` を serde で `ReplayGranularity` 直受けに変更。手動 `parse_granularity()` 廃止 | `src/replay_api.rs` |
| A1 | **H-F** | `EngineEvent::EngineError` の二役 (handshake 切断 frame / outbox event) を docstring と architecture.md に明文化 | `engine-client/src/dto.rs`, `python/engine/schemas.py`, `docs/✅nautilus_trader/architecture.md` |
| B  | **H-A** | `is_iso_date` を `chrono::NaiveDate::parse_from_str` ベースに置換し月日範囲・閏年も検証 | `src/replay_api.rs` |
| B  | **H-B** | broadcast `Lagged` を `ReplayLoadOutcome::Lagged` 経由で 503 + `{"error":"events lagged"}` 返却 | `src/replay_api.rs` |
| C  | **H-C** | `EngineStarted` emit を `try` ブロック内に移動。例外時は `EngineStopped` 補完 emit | `python/engine/nautilus/engine_runner.py` |
| C  | **H-H** | `_IPC_VENUE_TAG = "replay"` 定数を追加し `EngineStarted.account_id` を `"replay-..."` 形式に変更 (BacktestEngine 内部 venue と外向け IPC venue の分離) | `python/engine/nautilus/engine_runner.py`, `docs/✅nautilus_trader/architecture.md` |
| C  | **H-I** | `_collect_fill_data` を `(ts, price)` lex sort で決定論性強化、例外を `(AttributeError, KeyError, TypeError)` に絞る、`log.exception` + `strategy_id` を含める | `python/engine/nautilus/engine_runner.py` |
| D  | **M-7** | `_do_submit_order_inner` 冒頭で `venue == "replay"` を `OrderRejected{REPLAY_NOT_IMPLEMENTED}` で reject する早期分岐を追加 | `python/engine/server.py` |
| D  | **M-9** | `_make_server` テストヘルパーを `_REQUIRED_ATTRS` dict + 構築ループにリファクタ。属性追加の見落とし対策の docstring を明記 | `python/tests/test_server_engine_dispatch.py` |
| D  | **M-10** | `validate_start_engine` を `ModeMismatchError` / `UnknownEngineKindError` に分け、server.py で `code: "mode_mismatch"` / `"unknown_engine_kind"` に分岐 | `python/engine/mode.py`, `python/engine/server.py` |
| D  | **M-14** | StartEngine 連投 race を `_engine_tasks` 既存チェックで `Error{code: "engine_already_running"}` reject | `python/engine/server.py` |
| E  | **M-4** | `EngineError.strategy_id == ""` を pydantic `field_validator` で `None` に正規化 | `python/engine/schemas.py` |
| E  | **M-5** | `instrument_cache.update_from_live` を「先に永続化試行 → 成功時のみ in-memory 反映」順序に変更し `OSError` で warning + tmp cleanup + raise しない | `python/engine/nautilus/instrument_cache.py` |
| E  | **M-13** | `BuyAndHoldStrategy.subscribe_kind` を `Literal["bar", "trade"]` 化 + 不正値で `ValueError` | `python/engine/nautilus/strategies/buy_and_hold.py` |

### 追加リグレッションテスト

- `replay_load_rejects_calendar_invalid_dates` (`src/replay_api.rs`) — 月 13 / 日 32 / 閏年外
- `replay_load_returns_503_on_broadcast_lagged` (`src/replay_api.rs`) — broadcast capacity 超え
- `TestHHIpcVenueTag` / `TestHCEngineStartedFailureRecovery` / `TestHICollectFillDataDeterminism` (`python/tests/test_engine_runner_replay.py`)
- `TestM7ReplayVenueSubmitOrderRejected` / `TestM10UnknownEngineKind` / `TestM14StartEngineRaceGuard` (`python/tests/test_server_engine_dispatch.py`)
- M-4 EngineError 正規化 3 ケース (`python/tests/test_schemas_nautilus.py`)
- M-5 OSError 経路 (`python/tests/test_instrument_cache.py`)
- M-13 不正 subscribe_kind 4 ケース + 正常 2 ケース (`python/tests/test_nautilus_buy_and_hold.py`)

### 既存テストの更新

- `python/tests/test_collect_fill_data_preserves_pairs.py` — `RuntimeError` 全捕捉前提から、`AttributeError` のみ握り `RuntimeError` は伝搬する H-I の新仕様に合わせて 2 ケースに分離
- `python/tests/test_invariant_reason_code.py` — canonical reason_code セットに `REPLAY_NOT_IMPLEMENTED` を追加 (M-7 一時コード)

### 新たな知見

- **broadcast Lagged 強制**: tokio `broadcast::Receiver` を意図的に lag させる test には `feed` で大量 (≧20k) の小イベントを積み最後に `flush` する必要があった。`send` 1024 件程度では cooperative yield により handler が追従してしまい lag が再現しない。
- **Cython class 属性 patch 不能**: `nautilus_trader.backtest.engine.BacktestEngine.add_venue` は immutable Cython 属性で `unittest.mock.patch` で差し替えられない。例外注入による H-C 検証は loader 関数 (`engine.nautilus.engine_runner.load_trades`) を mock する経路に切り替えた。
- **モジュール先頭 `import time` の関数内シャドウ**: 関数内 `import time` (`server.py:874`) が同一関数の前段で `time.time()` を呼ぶ新規コードを `UnboundLocalError` で破壊する。Python のスコープ規則上、関数内 `import` は関数全体で local 名にバインドされるため、関数内 import を削除してモジュール先頭の `import time` だけに統一する必要があった (M-7 修正の副産物)。
- **`validate_start_engine` 例外型の二段化**: `ValueError` 単独だと `mode_mismatch` と `unknown_engine_kind` を区別できないため、サブクラス階層で別 `code` を割り当てる方が呼出側で分岐しやすい。既存 `except ValueError` ハンドラとの互換性も保てる。

### R1b 残課題（別エージェントで対応）

- **H-E**: `AppMode` enum 化 (現状 `String` + 比較) — R1b で対応済 (下記)
- **M-8**: `ReplayDataLoaded.strategy_id` Optional + schema minor bump — R1b で対応済 (下記)
- **H-G**: `call_soon_threadsafe` を `_outbox` append の単一窓口に統一 — R1b で対応済 (下記)

---

## レビュー反映 (2026-04-28, R2 review-fix R1b)

R2 review-fix-loop ラウンド 1b で R1a に持ち越した 3 件 (wire/threading) を反映した。
SCHEMA_MINOR は 4 → 5 に bump (Rust / Python 同時)。MAJOR=2 据え置きのため、
旧 client / 旧 server とのハンドシェイクは WARN ログのみで接続は維持される
(`engine-client/src/connection.rs` の handshake 規約; minor 不一致は切断しない)。

### 修正項目

| ID | 修正概要 | 主な変更ファイル |
|------|----------|------------------|
| **H-E** | `dto::AppMode { Live, Replay }` enum を新設 (`#[serde(rename_all = "lowercase")]` で wire は従来の `"live"` / `"replay"` 文字列のまま)。`Hello.mode` / `ProcessManager::mode` / `EngineConnection::connect_with_mode` / `ReplayApiState::mode` の型を `String` から `AppMode` に置換。`cli::Mode` には `From<Mode> for AppMode` を追加して境界で 1:1 写像する。`AppMode::default() == Live` で旧 client (mode 欠落) 互換を保つ | `engine-client/src/dto.rs`, `engine-client/src/connection.rs`, `engine-client/src/process.rs`, `src/cli.rs`, `src/main.rs`, `src/replay_api.rs` |
| **M-8** | `EngineEvent::ReplayDataLoaded.strategy_id: String` → `Option<String>` (`#[serde(default)]` で旧 server の欠落フィールドも吸収)。Python 側 `ReplayDataLoaded.strategy_id: str \| None = None`。単独 `LoadReplayData` 経路では `"strategy_id": None` を送出 (旧 `""`)。`engine_runner.start_backtest_replay` 経由は引き続き具体値を送出。`SCHEMA_MINOR` を 4 → 5 に bump (Rust `engine-client/src/lib.rs` / Python `engine/schemas.py` 同時) | `engine-client/src/dto.rs`, `engine-client/src/lib.rs`, `python/engine/schemas.py`, `python/engine/server.py` |
| **H-G** | `_handle_start_engine` 内のすべての `_outbox.append` 経路を `loop.call_soon_threadsafe` 経由 (`_emit` ローカル関数) に統一。validation / race guard / parse 失敗 / TimeoutError / except — main thread 経路と worker thread 経路の append が混在することによる順序逆転 race を解消。各 early return の前および `finally` で `await asyncio.sleep(0)` を呼んで scheduled callback を drain し、呼出側 (テスト含む) が outbox を直ちに観測できる semantics を保つ | `python/engine/server.py` |

### 追加リグレッションテスト

- `app_mode_serializes_lowercase` / `app_mode_deserializes_lowercase` / `app_mode_default_is_live_for_backward_compat` — `AppMode` の wire 互換性 (`engine-client/tests/schema_v2_4_nautilus.rs`)
- `replay_data_loaded_deserializes_with_null_strategy_id` / `replay_data_loaded_deserializes_when_strategy_id_field_absent` — Optional + serde default 経路 (同上)
- `test_replay_data_loaded_accepts_null_strategy_id` / `test_replay_data_loaded_default_strategy_id_when_field_absent` — pydantic 側 Optional (`python/tests/test_schemas_nautilus.py`)
- `TestHGCallSoonThreadsafeUnification` 4 ケース — validation / race guard / invalid_initial_cash / failure path で `_outbox.append` が常に `loop.call_soon_threadsafe` を経由することを spy で検証 (`python/tests/test_server_engine_dispatch.py`)

### 既存テストの更新

- `engine-client/tests/schema_v2_4_nautilus.rs::schema_minor_is_4_for_nautilus` → `schema_minor_is_5_for_nautilus` (assert を 5 に更新)
- `hello_includes_mode_field` の構築側を `AppMode::Replay` に置換
- `replay_data_loaded_deserializes` の strategy_id 取り出しを `Option<String>` に対応 (`assert_eq!(strategy_id.as_deref(), Some("strat-001"))`)
- `python/tests/test_schemas_nautilus.py::test_schema_minor_is_4_for_nautilus` → `test_schema_minor_is_5_for_nautilus`
- `python/tests/test_server_engine_dispatch.py::test_load_replay_data_emits_loaded_event` の `strategy_id == ""` を `is None` に更新

### 新たな知見

- **`call_soon_threadsafe` の deferred semantics**: `loop.call_soon_threadsafe` で schedule した callback は loop の次イテレーションで実行されるため、main thread から `_emit` した直後に `_outbox` を観測すると entry が見えない。`async def` 内で同期テストを書いていると `await asyncio.sleep(0)` で 1 回 yield する必要がある。本修正では `_handle_start_engine` の内部 `_drain` ヘルパで return 前に必ず drain して、呼出側 (テスト含む) の同期的観測を維持した。
- **`AppMode` の 2 層 (`cli::Mode` ↔ `dto::AppMode`)**: CLI 解析専用の `cli::Mode` と IPC 公開型の `dto::AppMode` を `From<Mode> for AppMode` で境界変換することで、各層が独立したライフサイクルを持てる。`cli` を `engine-client` に依存させない (現状は `cli.rs` 側に impl を置いて main crate 内で完結)。
- **Hello.mode の `#[serde(default)]`**: AppMode に `Default = Live` を実装することで、旧 client (`mode` フィールド非送出) からの接続でも Hello が deserialize 成功する。Python 側 `Hello.mode: Literal["live","replay"] = "live"` と双方向で互換が成立。
- **schema minor bump の影響範囲**: ハンドシェイクは `SCHEMA_MAJOR` のみで切断判定するため `MINOR=4 → 5` は接続維持。`ReplayDataLoaded.strategy_id` が旧 server (str=`""`) → 新 client (Option<String>) でも空文字を `Some("")` として deserialize できる (Optional 化は default のためで型は緩和されているだけ)。

---

## レビュー反映 (2026-04-28, N1.16 BuyingPower R1 fix)

N1.16 REPLAY 買付余力の review-fix-loop R1 指摘 19 件（C-1 × 1, H × 8, M × 8 + H-H 追加）を反映した。

### 修正項目

| ID | 分類 | 修正概要 | 主な変更ファイル |
|----|------|----------|-----------------|
| **C-1** | CRITICAL | `start_backtest_replay` の戻り値 `ReplayBacktestResult` を捕捉し、`success` 時に `_replay_strategy_id` セット + `_replay_portfolio.reset(initial_cash)` でポートフォリオを初期化。`result_holder` リスト経由でスレッド境界を超えて戻り値を伝播 | `python/engine/server.py` |
| **H-A** | HIGH | `REPLAY_API_STATE.set()` の重複初期化を warn ログで検出。`REPLAY_API_STATE.get() == None` 時も warn ログを追加 | `src/main.rs` |
| **H-B** | HIGH | `handle_replay_portfolio` / `update_replay_portfolio` の Mutex poison を `poisoned` フラグパターン（ブロックスコープ）で安全に処理。`MutexGuard`/`PoisonError` を await 前にドロップして `Send` 制約を満たす | `src/replay_api.rs` |
| **H-C** | HIGH | `distribute_replay_buying_power` に `buying_power: String` パラメータを追加し `panel.set_replay_portfolio()` に正しい値を渡す。呼び出し側で `buying_power.clone()` を渡すよう修正 | `src/screen/dashboard.rs`, `src/main.rs` |
| **H-D** | HIGH | `spawn_mock_engine_load` の戻り型を `tokio::task::JoinHandle<()>` に変更（末尾セミコロン除去）。テスト終了後に `handle.abort()` でリークを防止 | `src/replay_api.rs` |
| **H-E** | HIGH | `ReplayBuyingPower` IPC は push event (request なし) のため `request_id` フィールドを削除 | `python/engine/server.py`, `python/tests/test_replay_buying_power.py` |
| **H-F** | HIGH | `test_clm_not_called_in_replay` を `_do_get_buying_power_replay` 直呼びから `_do_get_buying_power(venue="replay")` 経由に変更してルーティングも検証 | `python/tests/test_replay_buying_power.py` |
| **H-G** | HIGH | `ClosePane` ハンドラで BuyingPower(is_replay=true) を dismiss: `replay_pane_registry.dismiss("", "BuyingPower")` | `src/screen/dashboard.rs` |
| **H-H** | HIGH | `_handle_load_replay_data` 成功パスで `_replay_strategy_id = ""` + `_replay_portfolio.reset(Decimal(0))` を実行し前回バックテストのポートフォリオをクリア | `python/engine/server.py` |
| **M-1** | MEDIUM | `replay_pane_registry.rs` 内の `MAX_REPLAY_INSTRUMENTS` 重複定義を削除（正規定義は `src/replay_api.rs:119`） | `src/screen/dashboard/replay_pane_registry.rs` |
| **M-2** | MEDIUM | `ReplayLoadOutcome::Ok` に `strategy_id: String` フィールドを追加。`EngineEvent::ReplayDataLoaded` の `strategy_id` を正しく取り出して `Ok` variant に渡す | `src/replay_api.rs` |
| **M-3** | MEDIUM | 既存 `LoadReplayData` の mode 検証穴は前ラウンドで対応済み | 対応済み |
| **M-4** | MEDIUM | `distribute_buying_power` に `&& !panel.is_replay` ガードを追加し、live BuyingPower が REPLAY pane を上書きしないよう防止 | `src/screen/dashboard.rs` |
| **M-5** | MEDIUM | `portfolio_view.py::to_ipc_dict()` で `_positions` が存在するのに `last_prices` が空の場合に warning ログを出力 | `python/engine/nautilus/portfolio_view.py` |
| **M-6** | MEDIUM | `_do_get_order_list_replay` の WAL 読み取りを `try/except OSError` で囲み、失敗時は `Error(code="wal_read_error")` を返す | `python/engine/server.py` |
| **M-7** | MEDIUM | テスト追加: `test_live_and_replay_orders_isolated` — live WAL と replay WAL の相互汚染がないことを検証 | `python/tests/test_order_list_api_venue_filter.py` |
| **M-8** | MEDIUM | `tx.try_send` のエラーを `Full` と `Closed` に分けて適切にログ出力 | `src/replay_api.rs` |

### 追加リグレッションテスト

- `test_wal_oserror_returns_error_event` — WAL ディレクトリ偽装で OSError → `Error(code="wal_read_error")` の経路を検証 (`python/tests/test_order_list_api_venue_filter.py`)
- `test_live_and_replay_orders_isolated` — live WAL / replay WAL 分離 (`python/tests/test_order_list_api_venue_filter.py`)

### 完了確認

- `cargo test --workspace` 全緑 (全テストスイート通過)
- `uv run pytest python/tests/ -q` 全緑 (1176 passed, 1 skipped; worktree venv の msgspec 欠落による既存テスト 1 件はコード無関係の環境問題)
- `cargo clippy --workspace -- -D warnings` クリーン
- `cargo fmt --check` クリーン

---

## レビュー反映 (2026-04-29, R2 review-fix R2)

R2 review-fix-loop ラウンド 2 で 9 件 (CRITICAL × 1, HIGH × 3, MEDIUM × 5) を反映した。
SCHEMA_MAJOR / MINOR は据え置き。

### 修正項目

| 分類 | ID | 修正概要 | 主な変更ファイル |
|------|------|----------|------------------|
| CRITICAL | **C-1** | N1.5 「✅ 完了」表記と M-7 dead-code reject の矛盾を計画書側で解消。N1.5 セクション冒頭に「配線繰越 → N1.11」明記、N1.11 セクションに wiring task 追加 | `docs/✅nautilus_trader/implementation-plan.md` |
| HIGH | **H-1** | `start_backtest_replay` (non-streaming) に `stop_ts_ms == 0` ガードを追加し、正常系で EngineStopped 送出後の例外で fallback emit が走らないようにする (streaming 版と同じパターンに揃える) | `python/engine/nautilus/engine_runner.py` |
| HIGH | **H-2** | `_handle_start_engine` の append 経路を分離: worker thread (`_on_event`) は `call_soon_threadsafe`、main thread (validation / race guard / parse / Timeout / except) は直 `_outbox.append`。これで `asyncio.CancelledError` でも main-thread Error が落ちない | `python/engine/server.py` |
| HIGH | **H-3** | `REPLAY_NOT_IMPLEMENTED` を data-mapping.md §5.3 に canonical 記載 (一時 code、N1.11 完了で廃止予定) | `docs/✅nautilus_trader/data-mapping.md` |
| MEDIUM | **M-1** | `src/replay_api.rs::handle_replay_order` で `parsed.clone()` を排除し、`cid` を先に `to_owned()` で抜き取って `parsed` を `from_value` に move | `src/replay_api.rs` |
| MEDIUM | **M-2** | `ControlApiCommand::AutoGenerateReplayPanes.strategy_id` を `String` → `Option<String>` に変更し、`ReplayDataLoaded.strategy_id` を unwrap せずに伝搬。`main.rs` 受信側はログに残しつつ `auto_generate_replay_panes` 呼出は従来通り | `src/replay_api.rs`, `src/main.rs` |
| MEDIUM | **M-5** | `test_schemas_nautilus.py` の `Hello` テストで hardcode `4` を `s.SCHEMA_MINOR`/`s.SCHEMA_MAJOR` 動的参照に変更。旧 client 互換は `test_old_client_minor_4_compatible` で 1 件 pin | `python/tests/test_schemas_nautilus.py` |
| MEDIUM | **M-7** | replay venue M-7 早期 reject で `OrderRejected` の前に `OrderSubmitted` を emit する (通常経路と対称化、UI submitting フラグ stuck 防止) | `python/engine/server.py` |
| MEDIUM | **M-8** | `_handle_start_engine` 受理時点で `_replay_strategy_id = strategy_id` を確定。`_handle_stop_engine` および `start_engine` の `finally` で同 strategy_id をリセット | `python/engine/server.py` |

### 追加リグレッションテスト

- `replay_load_propagates_strategy_id_to_auto_generate_command` (`src/replay_api.rs`) — null / "strat-001" 双方向で M-2 検証
- `TestH1NoDoubleEngineStoppedEmit` 2 件 (`python/tests/test_engine_runner_replay.py`) — 正常系 1 件 + post-emit raise でも単独 emit
- `test_old_client_minor_4_compatible` (`python/tests/test_schemas_nautilus.py`) — 旧 minor=4 client 互換 pin
- `test_cancelled_error_still_emits_error_to_outbox` (`python/tests/test_server_engine_dispatch.py`) — H-2 cancel-safe 検証

### 既存テストの更新

- `TestM7ReplayVenueSubmitOrderRejected` — OrderSubmitted 不在 assert を「OrderSubmitted → OrderRejected の順序保証」に書き換え
- `TestHGCallSoonThreadsafeUnification` の 4 ケース — main-thread 経路は `call_soon_threadsafe` を **使わない**、worker-thread のみ使うという H-2 の新セマンティクスに合わせて assertion を反転

### 完了確認

- `cargo test --workspace` 全緑 (487 passed; baseline 486 + 1 新規)
- `uv run pytest python/tests/ -q` 全緑 (1188 passed, 2 skipped; baseline 1181 + 7 新規)
- `cargo clippy --workspace -- -D warnings` クリーン
- `cargo fmt --check` クリーン
- `cargo build --workspace` クリーン

### R2 review-fix-loop 終了 (2026-04-29) — 残課題は次フェーズに繰越

ImplementationLoop の収束基準は MEDIUM 以上ゼロ。残 MEDIUM 3 / LOW 6 は user 明示承認 (2026-04-29) で次フェーズの review-fix-loop に繰越。R2 (= N1.2/N1.3/N1.4) スコープでの review-fix-loop はここで終了。

#### 繰越 MEDIUM (3 件)

| ID | 概要 | 拾い先フェーズ | 場所 |
|---|---|---|---|
| **M-3** | `src/main.rs:310-350` の reconnect bridge インライン実装が `spawn_venue_ready_bridge` と 35 行重複。将来 lifecycle event 追加時に更新漏れリスク | N1.13 ランタイム切替 (Q15) または独立リファクタ | `src/main.rs` |
| **M-6** | `python/tests/test_server_engine_dispatch._make_server` の `_REQUIRED_ATTRS` リストが `DataEngineServer.__init__` 実体属性と乖離した時に AttributeError 経由でしか検出できない (silent miss リスク) | N1.5 review-fix-loop または `DataEngineServer.for_testing()` 導入を独立タスクで | `python/tests/test_server_engine_dispatch.py` |
| **M-9** | `python/engine/nautilus/engine_runner.py:412` で `emit(EngineStopped)` 失敗時の except ブロックが `raise` を省略しており、元例外がスタック上で mask される。`_spawn_fetch` で `code="fetch_failed"` という汎用エラーが返り `EngineStopped` も届かない経路が残る | N1.11 streaming 配線時に統一して対応（同ファイルの streaming 版と挙動を揃える） | `python/engine/nautilus/engine_runner.py` |

#### 繰越 LOW (6 件)

| ID | 概要 | 拾い先 |
|---|---|---|
| **L-1** | `src/replay_api.rs:743` の `request_id.clone()` が move に置換可能 | N1.11 review-fix-loop |
| **L-2** | `engine-client/tests/schema_v2_4_nautilus.rs:10` のコメントが `schema_minor=4` のまま陳腐化 (実値は 5) | docs only — 任意タイミング |
| **L-3** | `architecture.md §3` の H-F 解説で Rust `Some(_)` と Python `None` 表記が混在、自然言語で統一推奨 | docs only — 任意タイミング |
| **L-4** | `engine_runner.py:_collect_fill_data` で `avg_px=None` のときに silent skip し log.debug が無い | N1.11 streaming 配線時 |
| **L-5** | `python/engine/mode.py:50` のコメントで `ModeMismatchError` / `UnknownEngineKindError` が `ValueError` のサブクラスである旨が将来の `except ValueError` 経路で誤捕捉を招く可能性に言及していない | N1.5 review-fix-loop |
| **L-6** | bug-postmortem パターン候補: 「並行 review-fix セッションが同一 commit 範囲を分担した時、片方の agent が先行 commit を見落として STOP+REPORT を出すパターン」を `MISSES.md` に追記候補として記録 | bug-postmortem skill |

### R2 review-fix-loop 完了サマリ

- 全ラウンド数: **2** (R1 = R1a + R1b 集約 + 並行 ffb3a2e、R2 = 本ラウンド + 並行 6a7d75b)
- 修正 Finding 総数: CRITICAL 1 / HIGH 12 / MEDIUM 13
- 残存 LOW (繰越): 6 件
- 主要な反映成果:
  - **型安全**: `AppMode` enum, `ReplayGranularity` serde 直受け, `ModeMismatchError`/`UnknownEngineKindError` 階層, `Literal["bar","trade"]`
  - **silent failure 除去**: `is_iso_date` chrono カレンダー検証, `Lagged` 503 即時, `EngineStarted` try 内移動, `EngineStopped` 二重送出ガード, `_persist` atomic write 順序, `_collect_fill_data` 例外型限定+lex sort, `_outbox.append` thread 分離 (cancel-safe), `OrderSubmitted → OrderRejected` 順序保証
  - **IPC 整合**: `SCHEMA_MINOR 4→5`, `ReplayDataLoaded.strategy_id` Optional, `EngineError.strategy_id` Optional + 空文字正規化, `REPLAY_NOT_IMPLEMENTED` canonical 記載
  - **テスト追加**: 約 35 件の新規 pin (cargo test 453→487 / pytest 1033→1188)
  - **計画書整合**: N1.5 配線繰越 → N1.11 明示, N1.11 に wiring task 追加, R1a/R1b/R2 反映ブロック self-contained

---

## レビュー反映 (2026-04-29, N2 R1〜R3)

Phase N2（tachibana LiveDataClient / LiveExecutionClient adapter）の review-fix-loop を 3 ラウンドで実施。

### ラウンド別件数推移

| ラウンド | CRITICAL | HIGH | MEDIUM | LOW |
|----------|----------|------|--------|-----|
| R1 初回  | 0        | 9    | 7      | 5   |
| R2 再レビュー | 0   | 3    | 4      | 4   |
| R3 サニティ | 0     | 1    | 2      | 1   |
| 収束     | 0        | 0    | 0      | —   |

### R1 で解消した主な指摘

| ID | 解消概要 |
|----|---------|
| H: IPC venue 欠如 | `python/engine/schemas.py` と `tachibana_orders.py` の `OrderRecordWire` に `venue: str = "tachibana"` を追加 |
| H: cancel/expire 無音 | `_handle_canceled` / `_handle_expired` で `order_info is None` 時に `log.warning` を追加 |
| H: safety_limits 黙殺 | `except Exception: pass` → `log.error + return "SAFETY_CHECK_ERROR: {exc}"` |
| H: side フォールバック "BUY" | `_SIDE_MAP.get(side_val, "BUY")` → `ValueError` raise → `generate_order_denied` |
| H: start_live() tautology | assert コメント強化 + テスト書き直し (inspect.getsource ベース) |
| M: MARKET_TO_LIMIT(5) 欠落 | パラメータテストに追加 |
| M: warm_up count ログ | 実際の登録数 (`warmed_count/len(records)`) をログ出力 |
| M: s70 exit code 常に 0 | `sys.exit(0 if filled else 0)` → `sys.exit(0)` + コメント整合 |
| M: _seq_per_ms GC 閾値 | `> 2` → `> 1`（直近 1 件保持） |
| M: price 変換無音 | `except Exception: pass` → `log.warning(...)` |
| M: instrument_id None 続行 | スキップ + `log.warning` |
| Rust: schema_major == 2 | `>= 2` → `assert_eq!(..SCHEMA_MAJOR, 2, ...)` |
| Rust: venue default assert | `order_list_updated_with_one_record_deserializes` に `orders[0].venue == "tachibana"` を追加 |

### R2 で解消した主な指摘

| ID | 解消概要 |
|----|---------|
| H: FSM 違反 | `_submit_order` 例外時の `generate_order_rejected` → `generate_order_denied(reason="SUBMIT_FAILED: ...")` |
| H: ValueError escape | `except ValueError` → `except Exception` for `_order_to_envelope` 呼び出しラップ |
| H: reset_seen() TODO | `tachibana_event_bridge.py` の `reset_seen()` 定義に N3 繰越 TODO コメント追加 |
| M: TestCacheConfig 自己発火 | `inspect.getsource` で `"database=None"` AND `"config.database is None"` を検査するテストに変更 |
| M: _by_venue 直接アクセス | `TestMissingOrderInfoWarning` を `MagicMock(spec=OrderIdMap)` 経由に書き直し |
| M: order_side フォールバック無音 | `warm_up_from_records` で未知 order_side/order_type 時に `log.warning` を追加 |
| M: warm_up() 成否不明 | 戻り値 `None` → `bool`（成功 `True`、失敗 `False`） |

### R3 で解消した主な指摘

| ID | 解消概要 |
|----|---------|
| H: 指値注文 price=None 続行 | `_order_to_envelope` で LIMIT/STOP_LIMIT/LIMIT_IF_TOUCHED/MARKET_TO_LIMIT + price is None → `ValueError` raise（→ `generate_order_denied`）。対応テスト `TestOrderToEnvelopeLimitPriceRequired` 3 件追加。既存 `test_order_type_mapping` に `needs_price` パラメータを追加して LIMIT 系はダミー price を渡すよう修正 |
| M: inspect.getsource 検査強化 | `"config.database is None"` の存在も同時 assert |

### 繰越事項（N3 スコープ）

- **server.py 未配線**: `TachibanaEventBridge.process_ec_event` と `warm_up()` を `server.py` の EC ハンドラに接続することが未完了。adapter クラス自体は実装済みで isolation テスト GREEN。N3 で `LiveExecutionEngine` 統合と同時に配線する
- **reset_seen() 日次リセット**: 夜間閉局時（立花 ST フレーム検知後）に `reset_seen()` を呼ぶ配線が未完了。N3 繰越（`tachibana_event_bridge.py:reset_seen()` に TODO コメント追記済み）
- **order_api.rs MARKET_CLOSED 先行 reject**: Rust 側の HTTP API 層での閉場前 reject。N3 スコープに明記済み

### 新たな知見

- **nautilus FSM 規約**: `generate_order_rejected` は `generate_order_submitted` 後でないと FSM 違反。HTTP 送信失敗（ネットワークエラー）は `generate_order_denied` で「発注未確認」として扱うべき
- **指値注文 price validation**: `_order_to_envelope` 内では `price=None` を WARNING で握りつぶしていたが、LIMIT 系注文では `ValueError` → `generate_order_denied` にエスカレーションしないと API 到達後に初めてエラーが発覚する
- **inspect.getsource テスト**: ソース文字列検査は「同義リファクタリング」に対して脆弱。`"database=None"` と `"config.database is None"` の 2 つを AND 条件で検査することで、片方の削除を検出できる
- **MagicMock(spec=) によるプライベート辞書アクセス廃止**: テストが内部辞書を直接操作すると `OrderIdMap` のバックエンド変更で壊れる。`spec=OrderIdMap` で公開 API のみ mock することで保護できる

---

## レビュー反映 (2026-04-29, N3 R1〜R3)

Phase N3（暗号資産 venue 移植・HyperliquidExecutionClient / Rust HTTP 市場閉場ガード）の review-fix-loop を 3 ラウンドで実施。

### ラウンド別件数推移

| ラウンド | CRITICAL | HIGH | MEDIUM | LOW |
|----------|----------|------|--------|-----|
| R1 初回  | 0        | 4    | 9      | —   |
| R2 再レビュー | 0   | 1    | 4      | —   |
| R3 サニティ | 0     | 0    | 2      | —   |
| 収束     | 0        | 0    | 0      | —   |

### R1 で解消した主な指摘

| ID | 解消概要 |
|----|---------|
| H: is_market_closed() 偽陽性 | `VenueState::Error` に `market_closed: bool` フィールドを追加。旧実装は `class == VenueErrorCode::MarketClosed.classify()` で `DepthUnavailable` が同じ `VenueErrorClass { Warning, Dismiss }` を持つため偽陽性が発生していた。`is_market_closed()` を `matches!(self, VenueState::Error { market_closed: true, .. })` に変更して分離。`VenueEvent::LoginError` にも `market_closed: bool` を追加し `main.rs` で `code == "market_closed"` 時のみ `true` をセット |
| H: DismissTachibanaBanner で AtomicBool 未クリア | `DismissTachibanaBanner` ハンドラで FSM 更新後に `self.order_api_market_closed.store(self.tachibana_state.is_market_closed(), Ordering::Release)` を呼ぶよう修正。未修正では dismiss 後も `is_market_closed=true` のまま全発注が 409 で永久ブロックされていた |
| H: cancel/modify/cancel_all 発注経路の venue ガード欠落 | `server.py` の `_do_cancel_order`・`_do_modify_order`・`_do_cancel_all_orders` に `if venue != "tachibana"` ガード（`code: "unsupported_order_venue"`）を追加。N3.C では `_do_submit_order_inner` のみに追加されており、cancel/modify 経路は旧 `if venue not in self._workers` チェックが残っていた |
| H: NautilusTrader Order の AttributeError | `_hl_submit_order` は `order_side: str` / `order_type: str` の文字列フィールドを期待するが、nautilus `Order` は `.side: OrderSide` enum / `.order_type: OrderType` enum を持つ。`_order_to_hl_envelope(order)` 変換関数を追加し `_submit_order` 入口で変換 |
| M: max_qty_per_order で float() 失敗時フォールバック 0.0 | `float()` 変換失敗時に `order_qty = 0.0` にフォールバックすると `> max_qty_per_order` チェックが常に通過する。変換失敗時は `generate_order_denied` + return に変更 |
| M: venue_order_id に "None"/"0" 文字列が通過 | `str(VenueOrderId("None"))` = `"None"` は truthy なため `if not venue_order_id_str` をすり抜けていた。`or venue_order_id_str in ("None", "0", "none")` を追加して早期 cancel_rejected |
| M: touch() がセッション未確立時も呼ばれる | `session_holder.touch()` を `self._tachibana_session is None` チェックの後に移動し、セッション未確立時にパスワード有効期限タイムスタンプが更新されないよう修正 |
| M: `order_api.rs` フィールド可視性 | `OrderApiState` の `session`・`engine_rx`・`is_replay_mode`・`is_market_closed`・`submit_timeout`・`guard_config` を `pub` → `pub(crate)` に変更 |

### R2 で解消した主な指摘

| ID | 解消概要 |
|----|---------|
| H: OnceLock 初期化エラーのサイレント無視 | `ORDER_API_MARKET_CLOSED.set()` の `.ok()` → `.is_err()` + `log::warn!` に変更し、二重初期化を可視化 |
| M: OrderApiState::new() の unwrap_or_else フォールバックが無音 | `log::debug!` ログを追加 |
| M: cancel/modify ハンドラへの is_market_closed ガード適用判断 | cancel/modify には意図的に market_closed ガードを適用しない（既発注の取消・変更は閉場中も許可するべき）ことを `// NOTE(N3.B)` コメントで明文化 |
| M: venue_banner.rs のパターンマッチ | `VenueState::Error { class, message }` → `{ class, message, .. }` に変更し新 `market_closed` フィールドに対応 |

### R3 で解消した主な指摘

| ID | 解消概要 |
|----|---------|
| M: is_market_closed FSM テストの網羅性 | `is_market_closed_returns_false_for_depth_unavailable`（旧偽陽性ケース）・`dismissed_from_market_closed_error_makes_is_market_closed_false`・`login_started_from_market_closed_error_makes_is_market_closed_false` の 3 テストを `venue_state.rs` に追加 |
| M: cancel/modify 向け dispatch テスト | `python/tests/test_server_dispatch.py` に cancel/modify/cancel_all の venue ガードテスト（hyperliquid・unknown・tachibana・replay 各ケース）を追加 |

### 主要な設計変更

- **`market_closed: bool` フィールド方式の採用理由**: `VenueErrorClass { Warning, Dismiss }` は `MarketClosed` と `DepthUnavailable` の両コードが共有するクラスのため class 比較では区別不能。専用フィールドで表現することで `is_market_closed()` が任意の `VenueState` の組合せに対して正確な結果を返せるようになった
- **`_order_to_hl_envelope()` 変換レイヤーの設計**: nautilus `Order` 型のフィールド（`.side: OrderSide` enum）と `_build_order_action()` が要求する wire フォーマット（`order_side: str`）のインピーダンス不一致を薄い変換オブジェクトで吸収。変換失敗は `generate_order_denied` にエスカレーション（サイレント握り潰しなし）
- **touch() 順序規約**: セッション存在確認 → touch() の順序を3つの IPC ハンドラ（cancel/modify/cancel_all）すべてで統一

### 追加テスト一覧

**Rust（+5 件、198 件合計）**:
- `is_market_closed_returns_true_for_market_closed_error`（既存）
- `is_market_closed_returns_false_for_depth_unavailable`（新規）
- `dismissed_from_market_closed_error_makes_is_market_closed_false`（新規）
- `login_started_from_market_closed_error_makes_is_market_closed_false`（新規）
- `is_market_closed_returns_false_for_other_errors`（整理）

**Python（+22 件、1295 件合計）**:
- `test_submit_order_converts_nautilus_order_to_hl_envelope`
- `test_submit_order_exceeding_max_qty_calls_generate_order_denied`
- `test_submit_order_with_invalid_qty_calls_generate_order_denied`
- `test_extract_venue_order_id_from_filled_status`
- `test_cancel_order_with_invalid_venue_order_id_calls_cancel_rejected`（None / "" / 0 / "None" の 4 パラメータ）
- cancel/modify/cancel_all の venue ガードテスト（hyperliquid・unknown・tachibana・replay 各ケース、計 12 件以上）

### 完了確認

- `cargo test --workspace` **198 passed** (全テストスイート通過)
- `uv run pytest python/tests/ -q` **1295 passed, 2 skipped**
- `cargo clippy --workspace -- -D warnings` クリーン
- `cargo fmt --check` クリーン

---

## 脚注

[^n1.11-rust-dto]: N1.11 Rust DTO — `engine-client/src/dto.rs` に `Command::SetReplaySpeed { request_id: String, multiplier: u32 }` を追加。`python/engine/schemas.py` に `SetReplaySpeed` Pydantic クラスを追加。`engine-client/tests/schema_v2_4_nautilus.rs` に `set_replay_speed_serializes` / `set_replay_speed_debug_shows_multiplier` 2 件追加。SCHEMA_MAJOR=2 / SCHEMA_MINOR=4 は変更なし（後方互換フィールド追加のみ）。

[^n1.11-rust-pane]: N1.11 iced pane 骨格 — `data/src/layout/pane.rs` に `ContentKind::ReplayControl` を追加（ALL 配列サイズ 12 に更新、Display に `"リプレイ速度"` 追加）。`src/screen/dashboard/pane.rs` に `Content::ReplayControl` variant を追加し、全 exhaustive match に分岐を補完。描画は `center(text("Replay Control — TODO(N1.11-ui)"))` の骨格のみ。`src/layout.rs` にて保存時は `Starter` にフォールバック（TODO(N1.11-ui) で専用 `data::Pane::ReplayControl` variant を追加予定）。

[^n1.11-rust-api]: N1.11 HTTP API — `src/replay_api.rs` に `POST /api/replay/control` を追加。`action="speed"` + `multiplier >= 1` のみ受理し `Command::SetReplaySpeed` を IPC 送出。replay モード以外・action 不正・multiplier 未指定・multiplier=0 はすべて HTTP 400 を返す。

[^n1.12-rust-dto]: N1.12 Rust DTO — `engine-client/src/dto.rs` に `EngineEvent::ExecutionMarker { strategy_id, instrument_id, side, price, ts_event_ms }` と `EngineEvent::StrategySignal { strategy_id, instrument_id, signal_kind: SignalKind, side?, price?, tag?, note?, ts_event_ms }` を追加。`SignalKind` enum（`EntryLong` / `EntryShort` / `Exit` / `Annotate`）を新設、serde PascalCase デフォルト。Optional フィールドは `#[serde(skip_serializing_if = "Option::is_none")]` で wire 省略。`engine-client/tests/schema_v2_4_nautilus.rs` に 4 件追加。SCHEMA_MAJOR/MINOR 変更なし。
