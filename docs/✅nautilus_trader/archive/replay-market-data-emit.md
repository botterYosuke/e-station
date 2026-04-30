# Replay 経路 Market Data 配線（N1.11 続き）

## 背景・動機

`scripts/run-replay-debug.sh docs/example/buy_and_hold.py` を実行すると、
`/api/replay/start` は HTTP 202 を返し Python 側でも以下まで正常に動く：

```
[NautilusRunner] replay run starting: strategy='user-strategy' instrument='1301.TSE' trades=0 bars=57
[NautilusRunner] replay run completed: strategy='user-strategy'
EngineStarted → ReplayDataLoaded → EngineStopped
```

しかし **Rust UI 側のチャートペインは "No ticker selected" のまま変化しない**。
auto-generated `CandlestickChart` / `TimeAndSales` ペインは作成されているが、
そこに流すべき `KlineUpdate` / `Trades` IPC イベントが一度も送られていないため。

### 根本原因

[`/api/replay/start`](../../src/replay_api.rs#L763) → [`Command::StartEngine`](../../python/engine/server.py#L2238)
の経路は **非ストリーミング版** [`start_backtest_replay()`](../../python/engine/nautilus/engine_runner.py#L232)
を呼ぶ。この関数は docstring に明記のとおり：

> [engine_runner.py:264-265](../../python/engine/nautilus/engine_runner.py#L264-L265)
> `market data 複製 (Rust UI 向け Trades/KlineUpdate) は N1.4 では no-op。N1.11 streaming で実装する。`

ストリーミング版 [`start_backtest_replay_streaming()`](../../python/engine/nautilus/engine_runner.py#L448)
は per-tick ループ・pacing・`DateChangeMarker` まで実装済みだが、
**per-bar / per-trade の `KlineUpdate` / `Trades` emit は未実装**で、
かつ `_handle_start_engine` から呼ばれてもいない。

つまり N1.11 streaming は「枠だけ作って中身（UI 向け market data 配信）が空」のまま
N1.12 以降のフェーズに進んでしまっている。

## ゴール

1. replay モードで `/api/replay/start` を叩いたら、auto-generated CandlestickChart に
   ロードされた Daily / Minute バーがリアルタイムに描画される
2. `Trade` granularity では Time&Sales ペインに約定が流れる
3. 既存の決定論性テスト（`test_nautilus_determinism.py` 等）と
   N0 互換テスト 8 件を破壊しない
4. pacing は既存 `compute_sleep_sec()` を流用、`multiplier` は `SetReplaySpeed`
   IPC で実行中に変更可能

---

## 変更対象と内容

### 1. ✅ `python/engine/nautilus/engine_runner.py` — per-tick emit を実装

#### 1a. `start_backtest_replay_streaming()` のループに emit を追加

[engine_runner.py:590-635](../../python/engine/nautilus/engine_runner.py#L590-L635) の
`for item in items:` ループ内で、`item` の型に応じて IPC イベントを emit する。

```python
from nautilus_trader.model.data import Bar, TradeTick

# ループ冒頭で IPC 用の venue / ticker / market を確定（毎 tick 計算しない）
ipc_venue = _IPC_VENUE_TAG       # "replay"
ipc_ticker = symbol               # "1301"（venue 抜きシンボル）
ipc_market = "stock"              # equity 固定（B1 で見直し）
ipc_timeframe = _granularity_to_timeframe(granularity)  # "1d" / "1m" / "tick"

for item in items:
    # ...既存の stop_event / DateChangeMarker / pacing 計算...

    # 1 tick 処理
    engine.add_data([item])
    engine.run(streaming=True)
    engine.clear_data()

    # ★追加: per-tick emit
    if isinstance(item, Bar):
        emit({
            "event": "KlineUpdate",
            "venue": ipc_venue,
            "ticker": ipc_ticker,
            "market": ipc_market,
            "timeframe": ipc_timeframe,
            "kline": {
                "open_time_ms": item.ts_event // 1_000_000,
                "open":  str(item.open),
                "high":  str(item.high),
                "low":   str(item.low),
                "close": str(item.close),
                "volume": str(item.volume),
                "is_closed": True,   # backtest bar は確定足
            },
        })
    elif isinstance(item, TradeTick):
        emit({
            "event": "Trades",
            "venue": ipc_venue,
            "ticker": ipc_ticker,
            "market": ipc_market,
            "stream_session_id": account_id,  # 一貫性のため EngineStarted と揃える
            "trades": [{
                "price": str(item.price),
                "qty":   str(item.size),
                "side":  _aggressor_to_side(item.aggressor_side),
                "ts_ms": item.ts_event // 1_000_000,
                "is_liquidation": False,
            }],
        })

    prev_ts_ns = curr_ts_ns
    if sleep_sec > 0.0:
        time.sleep(sleep_sec)
```

ヘルパ関数:

```python
def _granularity_to_timeframe(g: str) -> str:
    return {"Daily": "1d", "Minute": "1m", "Trade": "tick"}[g]

def _aggressor_to_side(side) -> str:
    # nautilus AggressorSide.{BUYER, SELLER, NO_AGGRESSOR}
    name = getattr(side, "name", str(side)).upper()
    if "BUY" in name:  return "BUY"
    if "SELL" in name: return "SELL"
    return "BUY"  # fallback
```

#### 1b. `start_backtest_replay()` 側は変更しない

run-once 経路は決定論性テスト・gym_env 用に温存する。
docstring の `「N1.11 streaming で実装する」` 注記は今回の実装で解消するため
更新する（「N1.11 streaming で実装済み (replay-market-data-emit.md)」に置換）。

---

### 2. `python/engine/server.py` — replay モードで streaming 版に切り替え

[`_handle_start_engine`](../../python/engine/server.py#L2426-L2438) の `_run()` を
`mode == "replay"` のとき streaming 版に振り分ける。

```python
def _run() -> None:
    if self._mode == "replay":
        # 既存属性 self._replay_speed_multiplier を渡す（SetReplaySpeed 連動）
        result_holder[0] = runner.start_backtest_replay_streaming(
            strategy_id=strategy_id,
            instrument_id=config_obj.instrument_id,
            start_date=config_obj.start_date,
            end_date=config_obj.end_date,
            granularity=config_obj.granularity,
            initial_cash=initial_cash,
            multiplier=self._replay_speed_multiplier,
            base_dir=base_dir,
            on_event=_on_event_tracked,
            strategy_file=config_obj.strategy_file,
            strategy_init_kwargs=config_obj.strategy_init_kwargs,
            stop_event=self._engine_stop_events.setdefault(
                strategy_id, threading.Event()
            ),
        )
    else:
        # backtest（非 replay）は run-once 経路を維持
        result_holder[0] = runner.start_backtest_replay(...)
```

`stop_event` レジストリは既存 `StopEngine` ハンドラが利用済みであれば流用、
無ければ追加する（`_engine_stop_events: dict[str, threading.Event]`）。

---

### 3. `python/engine/server.py` — `SetReplaySpeed` の running runner 反映

[server.py:632-646](../../python/engine/server.py#L632-L646) の `_handle_set_replay_speed`
が現状 `self._replay_speed_multiplier` を更新するだけならば、
`runner` 側へ反映する経路を追加する（streaming ループが値を読み直せるように）。

実装案:
- `start_backtest_replay_streaming()` の引数 `multiplier` を `multiplier_provider: Callable[[], int]` に拡張
- ループ内で毎 tick `multiplier_provider()` を呼ぶ
- server 側は `lambda: self._replay_speed_multiplier` を渡す

> **判断保留**: streaming のシグネチャ変更はテスト破壊規模が大きい。
> N1.11 完了時点で速度変更は「次回 start で反映」運用なら、本変更は別タスクに切り出す。
> 開発中の運用負荷次第で判断する（Open Question Q-S1）。

---

### 4. Rust 側 — 自動生成 CandlestickChart の購読配線

#### 調査結果（2026-04-30）

| 項目 | 状態 | 場所 |
|---|---|---|
| `KlineUpdate` の venue 文字列 match | ✅ 動く | [backend.rs:174](../../engine-client/src/backend.rs#L174) — `if ev_venue != venue { continue }` は `String` 比較 |
| `timeframe="1d"` ↔ `Timeframe::D1` | ✅ 既存 | [backend.rs:1071-1090](../../engine-client/src/backend.rs#L1071-L1090) |
| `ticker.to_string() == "1301"` | ✅ symbol のみで返る | exchange/src/lib.rs |
| `Venue::Replay` enum バリアント | ❌ **無い** | [adapter.rs:275-283](../../exchange/src/adapter.rs#L275-L283) — Bybit/Binance/Hyperliquid/Okex/Mexc/Tachibana のみ |
| `Exchange::ReplayStock` バリアント | ❌ **無い** | [adapter.rs:336-353](../../exchange/src/adapter.rs#L336-L353) |
| `VENUE_NAMES` への replay 登録 | ❌ 無い | [src/main.rs:182-189](../../src/main.rs#L182-L189) |
| 自動生成 CandlestickChart の購読 bind | ❌ 未実装 | [dashboard.rs:960-972](../../src/screen/dashboard.rs#L960-L972) — `pane::State::with_kind(ContentKind::CandlestickChart)` のみで ticker 未バインド |

**現状の挙動**: `venue.parse::<Venue>()` が `"replay"` で失敗 →
[backend.rs:120](../../engine-client/src/backend.rs#L120) で `Binance` にフォールバック警告。
さらに自動生成 pane は ticker 未バインドなので「Choose a ticker」状態のまま。
=> Python が emit した KlineUpdate は **どの購読側にも届かない**。

#### 実装の分解

3 ステップに分ける。各ステップ完了時に `cargo build` + 既存テスト回帰確認を入れる。

##### 4a. 基盤（Venue::Replay / Exchange::ReplayStock の追加）

- `exchange/src/adapter.rs`
  - `Venue` enum に `Replay` バリアント追加、`ALL` を 7 要素に拡張
  - `Display` / `FromStr` で `"replay"` ↔ `Venue::Replay`
  - `Exchange` enum に `ReplayStock` 追加、`ALL` を 16 要素に拡張
  - `from_venue_and_market` / `market_type` / `venue` / `default_quote_currency` /
    `supports_kline_timeframe` の各 match に `ReplayStock` / `Venue::Replay` を追加
    （quote=Jpy、market=Stock、kline は `D1` / `M1` を許可）
- `src/main.rs:VENUE_NAMES` に `(Venue::Replay, "replay")` を追加
- 既存の Venue / Exchange 全網羅テスト（grep `Venue::ALL` / `Exchange::ALL`）の更新
- 受け入れ: `cargo build --workspace` 成功 + `cargo test --workspace` 全 pass

##### 4b. replay モード時の `EngineClientBackend` 登録

- `src/main.rs:1167-1175` の VENUE_NAMES ループは全 venue 用にバックエンドを作るので
  4a の VENUE_NAMES 拡張で自動的に `Venue::Replay` バックエンドも作られる
- ただし live モードでも replay バックエンドが作られるので、live 中は dead だが副作用なし
  （Subscribe は IPC 送信されるだけで、Python 側 mode=live なら拒否される）
- 受け入れ: replay 起動時のログに `EngineClientBackend (Python IPC)` が VENUE_NAMES 全件で出る

##### 4c. 自動生成 CandlestickChart pane の購読 auto-bind

- `dashboard.auto_generate_replay_panes()` のシグネチャを拡張し、`granularity` も受け取る:
  `auto_generate_replay_panes(main_window_id, instrument_id, granularity)`
- `instrument_id="1301.TSE"` から `ticker="1301"` を分解
- `granularity` から `Timeframe` をマップ:
  - `"Daily"` → `Timeframe::D1`
  - `"Minute"` → `Timeframe::M1`
  - `"Trade"` → CandlestickChart は生成しない（Bar が無いため）
- `TickerInfo` を `Venue::Replay` + `MarketKind::Stock` で構築
  （ticker metadata fetch を待たず stub で良い: 立花の lot_size=100 など最小値）
- `pane::State::with_kind(ContentKind::CandlestickChart)` の代わりに
  `pane::State::new_with_ticker(ContentKind::CandlestickChart, ticker_info, timeframe)`
  相当のコンストラクタを用意する（既存 live pane の作成経路を流用）
- TimeAndSales pane も同様に `Trades` 購読を auto-bind
- 受け入れ:
  - `bash scripts/run-replay-debug.sh docs/example/buy_and_hold.py` で
    「Choose a ticker」が消え、ローソクが順次描画される
  - buy_and_hold の BUY ログ条件で対応する実約定（Time&Sales）が表示される
  - 単体テスト: `auto_generate_replay_panes("1301.TSE", "Daily")` が
    `Kline { ticker.ticker == "1301", timeframe == D1 }` のストリームを返す

#### スコープ外（次フェーズ）

- replay の TickerInfo metadata fetch（lot_size / tick_size の正確値）
- replay 専用 venue UI フィルタボタン（既存の filter UI に Replay を追加するか別扱いか）
- 既存 live pane との境界（同一 chart pane で venue を replay ↔ tachibana 切替したときの挙動）

---

### 5. テスト追加

| レイヤー | テスト内容 |
|---|---|
| Python unit | streaming 経路で Daily 1 件投入 → `KlineUpdate` が emit される |
| Python unit | streaming 経路で Trade 1 件投入 → `Trades` が emit される |
| Python unit | `_granularity_to_timeframe` の値マッピング |
| Python unit | `_aggressor_to_side` の値マッピング |
| Python integration | `_handle_start_engine` が replay モードで streaming を呼ぶ |
| Python integration | `_handle_start_engine` が live モードで run-once を維持する |
| Rust | mock engine が `KlineUpdate { venue: "replay", ... }` を流したとき、CandlestickChart が描画する（既存 live mock を replay venue で再利用） |
| E2E (manual) | `bash scripts/run-replay-debug.sh docs/example/buy_and_hold.py` で buy_and_hold の BUY シグナルが UI に反映される |

決定論性テスト群（`test_nautilus_determinism.py`）は run-once 版を使い続けるため、
streaming 版変更で破壊しないことを確認する。

---

## 実装順序（依存関係）

```
1. ✅ python/engine/nautilus/engine_runner.py   per-tick emit 追加 + ヘルパ + unit test
2. ✅ python/engine/server.py                replay モードで streaming に振り分け + integration test
3. ✅ §4a Venue::Replay / Exchange::ReplayStock 追加 + 14 pin tests
4. ✅ §4b VENUE_NAMES に Venue::Replay 登録 + T36 pin test
5. ✅ §4c auto_generate_replay_panes で set_content_and_streams 呼び出し + 5 pin tests
6. E2E 手動確認                              buy_and_hold.py で chart に bar が描画される
7. docstring / 計画書更新                    engine_runner.py の "N1.11 で実装する" 注記を解消
```

§3（SetReplaySpeed の動的反映）は本タスクのスコープ外、別タスクに切り出す。

---

## 影響範囲まとめ

| ファイル | 変更種別 |
|---|---|
| `python/engine/nautilus/engine_runner.py` | streaming ループに emit 追加・docstring 更新 |
| `python/engine/server.py` | replay モードで streaming 版を呼ぶ振り分け追加 |
| `python/tests/test_engine_runner_replay.py` | streaming emit 検証テスト追加 |
| `python/tests/test_server_dispatch.py` | replay→streaming 振り分けテスト追加 |
| `exchange/src/adapter.rs` | ✅ Venue::Replay + Exchange::ReplayStock 追加 |
| `exchange/src/adapter/client.rs` | ✅ AdapterHandles replay フィールド追加 |
| `src/main.rs` | ✅ VENUE_NAMES Replay 追加 + ControlApiCommand 4c ハンドラ |
| `src/replay_api.rs` | ✅ AutoGenerateReplayPanes に granularity フィールド追加 |
| `src/screen/dashboard.rs` | ✅ auto_generate_replay_panes: set_content_and_streams 呼び出し |
| `docs/✅nautilus_trader/implementation-plan.md` | N1.11 完了状態を更新 |

---

## レビュー反映 (2026-04-30, ラウンド 1)

6 エージェント並列レビュー (rust-reviewer / silent-failure-hunter / iced-architecture-reviewer / type-design-analyzer / ws-compatibility-auditor / general-purpose)。

### 解消した指摘

| ID | 重大度 | 内容 | 修正コミット |
|----|--------|------|------------|
| F1 | MEDIUM | `Venue::Replay` が live モードサイドバーフィルタボタンに表示 (Venue::ALL ループ) | 0c21e3e |
| F2 | MEDIUM | Pin test 4c-2 の 4000 char 窓が CandlestickChart bind をカバーしない可能性 | 0c21e3e |
| F3 | MEDIUM | `panes.split()` が `None` を返したとき無音スキップ | 0c21e3e |
| F4 | MEDIUM | 空 `instrument_id` で `Ticker::new("", ReplayStock)` が作られる無音バグ | 0c21e3e |
| F5 | MEDIUM | doc comment が `replay_ticker_info` に誤付与 (本来は `auto_generate_replay_panes`) | 0c21e3e |

### 偽陽性として棄却した指摘

- silent HIGH-3/4: `set_content_and_streams` は line 521 で `self.streams = Ready` を直接書くため戻り値廃棄は問題なし
- silent CRITICAL-1: reload 時 `latest_x = 0` は `insert_hist_klines` の fix (MISSES.md 2026-04-27) で解決済み
- general M-4: `Command::Subscribe` to Python replay is intentional per §4b acceptance criteria

### 設計確認・知見

- `ControlApiCommand::AutoGenerateReplayPanes` は純粋 in-process mpsc — WS IPC 境界を越えない。SCHEMA_MAJOR 変更不要。
- `Venue::Replay` を `Venue::ALL` に追加すると sidebar loop が全 mode で影響を受ける。今後 enum を拡張するときは sidebar の `for venue in Venue::ALL` ループでの扱いを確認すること。

### ラウンド 2 追加修正 (commit d8a91e9)

| ID | 重大度 | 内容 |
|----|--------|------|
| R2-1 | MEDIUM | `mark_loaded("")` が `is_empty()` ガードの前に呼ばれていた (レジストリ汚染) → 順序を入れ替え |
| R2-2 | MEDIUM | Pin test 4c-2 の 8000-char 窓が CandlestickChart bind をカバーしない可能性 → 4c-2 + 4c-5 を 15_000 に拡大 |

### 残存 LOW (対応不要)

- `refresh_streams()` の戻り型が `Task<Message>` だが常に `Task::none()` を返す (実害なし)
- `granularity` 欠落 HTTP body に対するピンテストが未追加 (serde が機械的に弾く)
- `strategy_id` フィールドが `AutoGenerateReplayPanes` ハンドラでログのみ使用
- `Exchange::ReplayStock.is_perps() == false` のピンテストなし
- `SerTicker` ラウンドトリップテスト未追加

---

## Open Questions

- **Q-M1**: `KlineUpdate.market` を Equity replay でどう埋めるか。
  既存 live は `"stock"` 固定で問題ないか、replay 専用に `"replay"` などにすべきか
- **Q-M2**: Daily バーのタイムスタンプは `open_time_ms` のみで足りるか。
  既存チャートが `is_closed=true` のローソクをどう扱うか確認が必要
- **Q-S1**: `SetReplaySpeed` の動的反映を本タスクに含めるかどうか
- **Q-T1**: timeframe `"1d"` をチャートが描画できるか（既存はおそらく `"1m"` / `"5m"` 中心）
- **Q-V1**: TradeTick の aggressor_side が `NO_AGGRESSOR` のときの side フォールバック値

## 注意

- `start_backtest_replay()` (run-once) は **絶対に変更しない**。
  決定論性テスト・gym_env が依存している
- `_BYPASS_LOG = LoggingConfig(bypass_logging=True)` は維持。
  ユーザー Strategy のログを出すかどうかは別議論（誤発注事故時の証跡確保とトレードオフ）
- pacing sleep 中に emit を挟む順序を変更しないこと。
  `engine.run(streaming=True)` 完了後に emit、その後 sleep の順を厳守する
  （途中 emit すると UI が「未確定足」を見て描画する可能性がある）

---

## 実装メモ（作業者向け）

### Task 1 実装済み (2026-04-30)

**実装内容:**

1. `Bar` / `TradeTick` の import をファイル先頭の import セクションに追加
2. モジュールレベルに `_granularity_to_timeframe()` / `_aggressor_to_side()` を追加
3. `start_backtest_replay_streaming()` に `strategy_file: str | None = None` パラメータを追加
4. streaming ループ前に `ipc_venue/ticker/market/timeframe` を事前計算（毎 tick 再計算しない）
5. `engine.clear_data()` 直後に `Bar` → `KlineUpdate`、`TradeTick` → `Trades` の emit を追加
6. `multiplier=0` を「即時モード（sleep なし）」として処理するよう特例を追加
7. `start_backtest_replay()` の docstring を「N1.11 streaming で実装済み」に更新

**strategy_file 対応:**
- worktree 版の `_make_replay_strategy` が旧インターフェース（`strategy_id` ベース）だったため、
  `strategy_file` を受け取る形に拡張した
- `_load_user_strategy()` ヘルパと `strategy_loader.py` を worktree に追加
- `strategy_file` 指定時は `load_strategy_from_file()` でユーザー Strategy をロード、
  未指定時は従来の `buy-and-hold` Strategy にフォールバック

**multiplier=0 処理:**
- テスト要件では `multiplier=0` を「no sleep 即時」として使っているが、
  `compute_sleep_sec()` は `multiplier <= 0` を `ValueError` で拒否する
- streaming 関数内で `multiplier <= 0` のとき `compute_sleep_sec()` を呼ばず `sleep_sec=0.0` に
  短絡する特例を追加した

**テスト結果:** `uv run pytest python/tests/test_engine_runner_replay.py -v` で 24 件全緑

---

### バグ修正: replay チャートの Kline フェッチスパム (2026-04-30)

**症状:**  
`buy_and_hold.py` を実行すると、`CandlestickChart` pane が生成された直後から
`Kline fetch failed: Invalid request: not_found: unknown venue: replay` が毎秒ログに出続けた。

**根本原因:**  
`KlineChart::fetch_missing_data()` は `timeseries.datapoints.is_empty()` のとき
`let latest = chrono::Utc::now().timestamp_millis() as u64` でフェッチ範囲を計算する。
`latest` はミリ秒精度で毎フレーム変わるため、`RequestHandler::same_with()` の exact match が
常に miss → 毎フレーム新しい Pending リクエストが挿入 → 毎秒フェッチタスク起動 →
Python エンジンが `Subscribe: unknown venue 'replay'` で拒否 → ログスパム。

前フェーズで `FetchUpdate::Error` に `req_id` を持たせ `mark_failed()` を呼ぶ修正を入れたが、
次の tick では *別のタイムスタンプ* の新しいリクエストが作られるため再発していた。

**修正内容** ([src/chart/kline.rs:371-373](../../src/chart/kline.rs#L371-L373)):  
```rust
fn fetch_missing_data(&mut self) -> Option<Action> {
    if self.chart.ticker_info().exchange() == Exchange::ReplayStock {
        return None;
    }
    // ...
}
```
`Exchange::ReplayStock` のチャートは `fetch_missing_data()` を即リターン。
replay チャートは `KlineUpdate` ストリーミングで bar を受け取るため歴史フェッチは不要。

**副修正** — 前フェーズで投入済み:
- `FetchUpdate::Error` に `req_id: Option<Uuid>` を追加
- `dashboard.rs` に `Message::FetchFailed` バリアントを追加し、失敗した req を `mark_failed()` で解消
  （live モードでフェッチが失敗したときの pending リクエスト残留を防ぐ汎用修正）

---

## レビュー反映（review-fix-loop Round 1/2 — 2026-04-30）

### 指摘・修正一覧

| 重大度 | 箇所 | 内容 | 対応 |
|--------|------|------|------|
| HIGH | `engine_runner.py` `_granularity_to_timeframe` | KeyError が不明確 | ValueError に変更 |
| HIGH | `server.py` `__init__` | `_replay_speed_multiplier` 未初期化 (`getattr` フォールバック) | `self._replay_speed_multiplier: int = 1` を追加 |
| HIGH | `engine_runner.py` streaming ループ | `multiplier=0` 時に `compute_sleep_sec()` が ValueError | `multiplier <= 0` を sleep_sec=0.0 で短絡 |
| MEDIUM | `engine_runner.py` per-tick emit | 例外がループ全体を終了させる | `try/except Exception` + `log.error` + `break` で包む |
| MEDIUM | `engine_runner.py` `_aggressor_to_side` | NO_AGGRESSOR フォールバックがサイレント | `log.debug` を追加 |
| MEDIUM | `server.py` `SetReplaySpeed` | multiplier <= 0 の検証なし | `isinstance` + `<= 0` チェック + warning + return |
| MEDIUM | `engine_runner.py` pacing sleep | `time.sleep` で stop 応答が最大 200ms 遅れる | `stop_event.wait(timeout=sleep_sec)` に変更 |
| MEDIUM (テスト) | `TestHelperFunctions` | 未知 granularity の KeyError テスト未実装 | `test_granularity_to_timeframe_unknown_raises_value_error` 追加 |
| MEDIUM (テスト) | `TestStreamingEmit` | stop_event 中断テスト未実装 | `test_stop_event_stops_streaming_before_all_ticks` 追加 |
| LOW | `server.py` `SetReplaySpeed` | `hasattr` 分岐が冗長 | 単純代入に置き換え |
| LOW | `test_server_engine_dispatch.py` | `_REQUIRED_ATTRS` / `defaults` に `_replay_speed_multiplier` 未追加 | 両方に追加 |

### Round 2 確認結果

全 HIGH/MEDIUM/LOW が修正済み。`uv run pytest python/tests/ -v` で **1323 passed, 2 skipped**。

### 未解決（スコープ外）

- **`SetReplaySpeed` が走行中 streaming の速度を変更できない**: `multiplier` が `start_backtest_replay_streaming` 呼び出し時点でスナップショットされるため、実行中の `SetReplaySpeed` は次の StartEngine から有効。ループ内で `self._replay_speed_multiplier` を直接参照させるには関数シグネチャ変更が必要。将来の N1.x タスクで対応。
- **Rust 側 venue="replay" ルーティング**: Rust の `Venue` enum / `VENUE_NAMES` に "replay" 未定義、auto-generated ペインのストリームバインドなし。Python emit は正しいが Rust チャートへの表示は別タスクで対応。
