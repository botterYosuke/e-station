---
date: 2026-04-28
status: proposal (R2 — レビュー反映)
scope: nautilus_trader 計画への追補（Rust UI の replay 中の役割確定）
affects: README.md / spec.md / architecture.md / implementation-plan.md / open-questions.md
---

# Replay モードにおける Rust UI 役割の確定（計画変更案）

## 改訂履歴

- R1 (2026-04-28): 初稿
- R2 (2026-04-28): レビュー指摘 4 件を反映
  - #1 `Pause/Seek/Speed` が `BacktestEngine.run()` 自走と衝突 → N1 では **speed のみ**（streaming=True による pacing）に縮小、pause/seek は Q14 で別途検討
  - #2 `StrategySignal` の実装が約定結果しか拾えていなかった → `ExecutionMarker`（自動）と `StrategySignal`（Strategy 明示送出）を分離
  - #3 HTTP `/api/replay/control` と IPC コマンドの写像を明示
  - #4 replay 中の市場データ UI 経路を明記（既存 `EngineEvent::Trades` / `KlineUpdate` を再利用）
- R3 (2026-04-28): レビュー指摘 2 件を反映
  - #1 `/api/replay/control {action: "play"}` 行を API 表から削除（N1 では `StartEngine` 一本に統一）
  - #2 `time.sleep(dt / multiplier)` の `dt` 定義を D7 で確定（営業セッション外ギャップ・sleep 上限・最低粒度）
- R4 (2026-04-28): モード切替セマンティクスを D8 で固定（N1 は起動時固定・ランタイム切替なし）
- R5 (2026-04-28): レビュー指摘 2 件を反映
  - #1 N1 の `--mode live` 扱いを明確化: 「データ閲覧用途のみ許可、`LiveExecutionEngine` は N2 まで起動しない」
  - #2 D8 から「`client_order_id` 生成カウンタ初期化」を削除（UUID v4 規約と矛盾）。実際にリセットする UI in-memory order map に表現を訂正
- R6 (2026-04-28): REPLAY 銘柄追加時の Rust UI pane 自動生成ルールを D9 として追加
- R7 (2026-04-28): D9 を拡張し REPLAY 実行中の業務 UI 連動（注文一覧・買付余力）を明文化

## 背景

`docs/plan/✅nautilus_trader/` で live / replay 2モード化が決まったが、replay 中の
Rust UI（iced）が何を担うか、matplotlib などの「戦略構築中の目視ツール」を
代替できるかが未確定だった。本書で確定する。

## 決定事項

### D1. データ粒度の不変条件: replay は TradeTick / Bar のみ

- **採用**: 「replay は trade stream のみサポート、depth（板）は live 専用」を
  計画全体の不変条件として明示する
- 根拠: J-Quants には板履歴が存在しない（[spec.md §6](../spec.md#6-j-quants-データ前提2026-04-28-追記)）
- これは [spec.md §3.5.1](../spec.md#351-strategy-が依存してよい一次データ) の
  既存規約と整合する。本書で **計画レベルの不変条件**として格上げする
- 影響: replay 中の iced UI には Depth ペインを表示しない（live のみ）

### D2. Rust UI が replay 中に担う役割（採用 3 機能、R2 で範囲明確化）

matplotlib 代替ではなく、**「再生中の市場の様子」と「戦略の挙動」の目視**に役割を絞る。

| 機能 | 採用 | 既存資産 | 新規実装範囲 |
|---|:---:|---|---|
| 時系列再生中のローソク足・歩み値表示 | ✅ | iced の chart pane を流用 | replay 時も既存 `EngineEvent::Trades` / `EngineEvent::KlineUpdate` を再利用（D5 参照）。**新規イベントを足さない** |
| 戦略マーカーのオーバーレイ（自動 fill + 明示シグナル） | ✅ | なし | `EngineEvent::ExecutionMarker`（OrderFilled 由来・自動）と `EngineEvent::StrategySignal`（Strategy が `emit_signal()` で明示送出）の **2 系統に分離**（D6 参照） |
| 再生速度コントロール（1x / 10x / 100x） | ✅（N1） | なし | streaming=True の bar/tick 逐次投入ループに wall-clock スリープを挟む。**pause / seek は N1 では含めない**（D4 参照） |
| 一時停止・シーク（`PauseReplay` / `SeekReplay`） | ⏸️ N1 では非対応 | — | Q14 で `run()` 自走 vs streaming への決定後に再評価 |

### D3. matplotlib が担う領域は引き受けない

- **不採用**: PnL 曲線・パラメータヒートマップ・最適化結果の可視化を iced で実装すること
- 代替: **AI ナラティブ層の中に保存**する（本計画の `POST /api/agent/narrative` を活用）
  - 戦略ごとの PnL 統計・パラメータスイープ結果は narrative entry の payload に格納
  - 後から marimo / Jupyter から narrative store を読んで分析する運用を許容
  - 専用の可視化エンドポイントは N1 以降の優先度を下げる
- 根拠: iced で数値プロットを再実装するコスパが悪く、戦略構築の主観点（再生中の
  目視）と分析点（PnL）は別ツールに分離した方が UX が素直

### D4. 実行モデル整合: N1 は streaming=True 上の speed pacing 一本

**問題**: `Pause/Seek/Speed` を素朴に「drain ループ停止」「wall-clock スリープ」と
書いたが、これは Q3 で確定済みの **「`BacktestEngine.run(start, end)` 自走 + wall
clock 非参照」**と衝突する（[Q3](../open-questions.md#q3) / [spec.md §3.1](../spec.md#31-決定論性) /
[architecture.md §3 H4 注](../architecture.md#3-新規-ipc-メッセージ)）。`run()` は
synchronous に全データを消費するため、外側から pacing できない。

**解決方針（R2 で確定）**:

1. **headless / 決定論性検証は引き続き `run()` 自走を使う**（N0.6 / N1.9 / spec §3.1
   の wall clock 非参照テストを維持）
2. **UI 駆動の replay viewer は streaming=True を採用**（[Tpre.1 spike](../implementation-plan.md#tpre1-clock-注入-feasibility-プロトタイプh4--完了-2026-04-26) で
   案 A が動作確認済み: `add_data([tick]) + run(streaming=True) + clear_data()` サイクル）
3. **N1 では speed のみ実装**:
   - 1x / 10x / 100x は streaming ループ間に `time.sleep(dt / multiplier)` を挟むだけ
   - 仮想時刻（`tick.ts_event`）は触らないので決定論性は streaming 経路でも保たれる
   - 「wall clock 非参照」不変条件は **headless パス限定**へ表現を緩める（spec §3.1 を改訂）
4. **pause / seek は N1 で実装しない**:
   - pause: streaming ループの suspend は技術的には可能だが UX 仕様（fill in-flight の扱い等）が未定義
   - seek（未来方向）: tick を捨てながら早送りする実装でも仮想時刻の整合は取れるが、Strategy
     が中間 tick を見ない挙動が決定論性テストの仮定を崩す
   - これらは **Q14**（新設）で「N2 以降の検討事項」として正式に open question 化する

**spec.md §3.1 改訂提案**:
> リプレイ（`run()` 自走経路）は wall clock を一切参照しない。UI 駆動 replay viewer
> （streaming 経路）は wall-clock pacing を許すが、Strategy が観測する仮想時刻
> （`tick.ts_event`）は wall clock から独立であることを保つ。決定論性テスト
> （N0.6 / N1.9）は `run()` 自走経路でのみ実施する。

### D5. 市場データの UI 経路（既存イベント再利用）

D2 の「IPC TradeTick / Bar をチャートに流す配線のみ」を曖昧にしないため明文化:

- **新規 IPC イベントを足さない**。replay でも live と同じ `EngineEvent::Trades`
  と `EngineEvent::KlineUpdate` を流す
- 送出元は streaming ループ内の Strategy hook ではなく、`engine_runner.py` の
  data feed 直前で「Rust 向けにも 1 件複製送出」する経路を 1 箇所追加する
- iced 側の chart pane は live / replay を区別しない（StrategySignal overlay レイヤー
  だけが追加される）
- venue タグは `"replay"`（既存 `SubmitOrder.venue` 列挙と整合）

### D7. Speed pacing の `dt` 定義（R3 で確定）

**問題**: `time.sleep(dt / multiplier)` の `dt` を「隣接 tick の `ts_event` 差分」と
素直に取ると、昼休み・引け後・翌営業日までで秒〜数万秒のギャップが入り、replay viewer
としては事実上停止する。

**N1 の pacing ルール（確定）**:

```
sleep_sec = min(
    max(dt_event_sec, MIN_TICK_DT_SEC) / multiplier,
    SLEEP_CAP_SEC
)
where:
  dt_event_sec      = next_tick.ts_event - prev_tick.ts_event を秒換算
  MIN_TICK_DT_SEC   = 0.001  # 同一マイクロ秒バーストでも最低 1ms 刻む（UI 描画整合）
  SLEEP_CAP_SEC     = 0.200  # 1 sleep の上限（昼休み・引け後ギャップを潰す）
```

加えて以下のセッション境界をハードコードでスキップする（multiplier に依存せず即時通過）:

| ギャップ | 検出方法 | 扱い |
|---|---|---|
| 前場-後場 11:30〜12:30（JST） | `prev_tick.ts_event` の時刻帯 + 次 tick の時刻帯比較 | sleep=0 で次 tick を即時投入 |
| 後場引け後（15:30 JST 以降〜翌 9:00） | 同上 | sleep=0、Strategy には連続 tick として観測される |
| 営業日跨ぎ（土日・祝日） | `prev_tick` と `next_tick` の `date` 不一致 | sleep=0 + UI に「日付変更」マーカーを 1 件 emit |

**仮想時刻の整合性**:
- `tick.ts_event` は J-Quants のオリジナル値を**そのまま流す**（操作しない）
- `Strategy.on_trade_tick` から見える `clock.utc_now()` も仮想時刻のまま（決定論性は維持）
- pacing は **wall-clock の sleep だけ**を変える。仮想時刻の連続性は保つ

**multiplier の意味（再定義）**:
- `1x`: 「1 営業セッション内の現実時間」≈ 1 で観測される pacing。セッション外ギャップは即時通過
- `10x` / `100x`: セッション内も 10/100 倍速。`SLEEP_CAP_SEC` で上限される
- 実測上、1x でも 1 銘柄 1 日分が約 5〜6 時間で再生される想定（tick 密度依存）。
  ユーザは大抵 10x / 100x で使う

**設定の表出**:
- `MIN_TICK_DT_SEC` / `SLEEP_CAP_SEC` は `engine_runner.py` のモジュール定数とし、
  N1 では config 化しない（H1 で起動 config に出すかは様子見）
- N1.11 のテストで両定数の境界条件（0 ms 連続 tick / 終値跨ぎ）を assert する

### D8. モード切替セマンティクス（N1 は起動時固定）

**問題**: market data 経路 / overlay レイヤー / 板ペインの可視性 / order UI 文言 /
WAL の名前空間がすべてモード依存だが、`live ⇄ replay` 切替時の破棄・再購読・再初期化
責務が未定義。曖昧なまま実装すると state leak の温床になる。

**N1 で確定するルール**:

1. **起動時モード固定**: アプリ起動時に `--mode {live|replay}` で 1 回だけ決定し、
   プロセスの寿命中は変更しない
2. **ランタイム切替を提供しない**:
   - `StartEngine` 後の `live ⇄ replay` 動的切替はサポートしない
   - モード変更は **`StopEngine` → プロセス再起動**でのみ行う
   - HTTP / IPC にモード切替コマンドを足さない
3. **in-memory state はモード境界を跨いで保持しない**:
   - Rust UI の in-memory state はプロセス起動時に空から初期化する
   - 永続化（`saved-state.json` 等）された UI レイアウトは引き継ぐが、データ系
     in-memory state（履歴・order list）は引き継がない
4. **モード開始時に空から初期化する対象**:
   - chart pane の履歴（live の Trades 履歴と replay の J-Quants 履歴は別物として扱う）
   - Depth ペインの表示状態（replay では生成しない・購読しない）
   - `ExecutionMarker` / `StrategySignal` overlay レイヤー
   - in-memory order list / position view
   - 直近注文表示 / `ClientOrderId → OrderState` の in-memory map（**`client_order_id`
     自体は order/ 規約どおり UUID v4 で都度生成される。「カウンタ初期化」は誤り**）
   - WAL 名前空間（`tachibana_orders.jsonl` / `tachibana_orders_replay.jsonl`）の
     分離は既存どおり保つ（永続側はリセットしない）
5. **N1 における `--mode` 値ごとの責務**（既存「live は N2 から」前提との整合）:

   | mode | N1 でできること | N1 でしないこと（N2 以降） |
   |---|---|---|
   | `replay` | J-Quants 投入・`BacktestEngine` 起動・streaming pacing・REPLAY 仮想注文 | — |
   | `live` | 立花 EVENT WS の閲覧（既存 Phase 1 経路）・iced UI の live レイアウト・**既存の `tachibana_orders` 直接経路での発注（order/ Phase で稼働中の経路）** | nautilus `LiveExecutionEngine` の起動。Hello capabilities は `nautilus.live=false` のまま（既存 [architecture.md §2](../architecture.md#2-プロセス起動とハンドシェイク) の `live: false_until_n2` を維持） |

   - N1 で `--mode live` を起動しても nautilus live engine は **stub のまま**（既存 N0.2
     の `start_live()` stub 方針を踏襲）
   - 発注経路の nautilus 化（`tachibana_nautilus.py` adapter）は引き続き **N2 で着手**
   - 「N1 で `--mode live` が起動できるか」: できる。ただし nautilus 側は live execution を
     起動しない、という二重定義を解消した形

6. **CLI 起動例**（D8 に従う）:
   ```
   cargo run -- --data-engine-url ws://127.0.0.1:19876/ --mode replay
   cargo run -- --data-engine-url ws://127.0.0.1:19876/ --mode live
   ```
7. **N1 は「切替できない」のではなく「切替責務を未解決のまま背負わない」**: ランタイム
   切替を将来導入する場合に必要な意思決定（下記 Q15）は open question にして繰り越す

**Q15（新設）— ランタイム切替を導入する場合の未解決事項**（N2 以降で再評価）:

- 立花 EVENT WebSocket 購読の解除・再購読の整合性（live → replay 時に subscribe を
  全部閉じるか、selected instrument を保ったまま suspend するか）
- 板ペインの visibility 制御（replay 化時にペインを破棄するか、空のまま残すか）
- in-memory order list のスコープ（モード境界で空にするか、live と replay で 2 つ持つか）
- selected instrument / chart range の引継ぎ可否（UX 上は引継ぎたいが、データ
  ソースが別なので range の妥当性は要検証）
- nautilus `BacktestEngine` と `LiveExecutionEngine` の同時起動可否（現計画では片方ずつ）

### D9. REPLAY 銘柄追加時のチャート自動生成 + 実行中の業務 UI 連動

**問題**: D2 / D5 で「replay の `Trades` / `KlineUpdate` を chart pane に流す」までは
決まったが、以下が未定義:

1. **銘柄を REPLAY 対象に追加したときにチャート pane を自動で開くか**
2. **REPLAY 実行中にチャート以外の業務 UI（注文一覧・買付余力）が live と同様に連動更新されるか**

特に 2 は重要で、「注文一覧」が live の立花注文を見せたまま、「買付余力」が live の
`CLMZanKaiKanougaku` をそのまま見せると、REPLAY 中に **実残高で発注可能と誤解**する
危険がある。本節で 1〜2 をまとめて固定する。

**N1 で確定するルール**:

#### D9.1 自動生成の対象

REPLAY 対象に銘柄を追加（`POST /api/replay/load` 成功）したとき、Rust UI は
**当該銘柄に紐付く以下 2 枚の pane を自動生成**する:

| pane 種別 | 配線する IPC イベント | 既存資産 |
|---|---|---|
| **Tick / 歩み値（time-and-sales）pane** | `EngineEvent::Trades`（venue=`replay`、instrument 一致） | 既存 live 用 trades pane を再利用 |
| **ローソク足 pane**（デフォルト粒度: 1 分足） | `EngineEvent::KlineUpdate`（venue=`replay`、instrument 一致、tf=`1m`） | 既存 live 用 candlestick pane を再利用 |

- `ExecutionMarker` / `StrategySignal` overlay は **ローソク足 pane** に重ねる
  （Tick pane には載せない。情報密度の問題）
- 1 分足を初期粒度とする根拠: J-Quants minute bar の整合がとりやすく、`BarAggregator`
  で `TradeTick → 1m Bar` の sanity check も再利用できる
- 他の粒度（5m / 1d 等）はユーザーが pane 上で切り替える既存 UI を流用

#### D9.2 重複生成防止

- pane の identity は `(mode=replay, instrument_id, pane_kind, granularity?)` の
  タプル。同 identity の pane が既存ならば **新規生成しない**
- 既存 pane がある場合は購読対象を「追加された instrument」に変更するのではなく、
  **既に当該 instrument に bind 済みの pane があればそのまま再利用**する
- live pane と replay pane は identity の `mode` が違うので **共存可**（D8 はランタイム
  切替を禁じているので実態上は同時に存在しない）

#### D9.3 pane 生成位置ルール

- **専用 replay レイアウトは作らない**。既存レイアウトを再利用する（実装コスト最小）
- 生成位置:
  1. 当該銘柄の Tick pane / Candlestick pane が **どこかに既に存在**する場合 → 再利用（新規生成しない）
  2. 1 銘柄目の追加時で空のレイアウト → `(Tick, Candlestick)` を**横並び 2 分割**で生成
  3. 2 銘柄目以降 → **フォーカス中の pane を縦分割**して `(Tick, Candlestick)` を追加
     （既存の手動分割 UX と同じ操作の自動版）
- `saved-state.json` で前回レイアウトが復元されている場合: 復元 pane に既に当該
  instrument の bind があれば再利用、なければ上記 1〜3 のルールで生成
- ユーザーが自動生成 pane を**手動で閉じた**場合は、同セッション中は**再生成しない**
  （明示的に閉じた意図を尊重）。`/api/replay/load` を再実行した場合のみ再生成判定を回す

#### D9.4 複数銘柄追加時の扱い

- 銘柄を追加するごとに pane を増やす（**全銘柄に Tick + Candlestick 2 枚**）
- N1 では **active / selected instrument のみ自動表示する省略形は採用しない**
  （切替 UX の責務が別途必要になり N1 のスコープ外）
- pane 数の上限ガード: 同時表示銘柄数の hard limit を `MAX_REPLAY_INSTRUMENTS = 4`
  とする（4 銘柄 × 2 pane = 8 pane）。超過時は `/api/replay/load` を 400 で拒否
- 上限の根拠: iced のレンダリング負荷と streaming ループの pacing 整合性。N2 以降に
  実測で再評価

#### D9.5 注文一覧の REPLAY 連動

REPLAY 実行中の「注文一覧」pane は **live の立花注文を表示せず、REPLAY の仮想注文・
仮想約定のみ**を表示する。

**データ経路**:

```
Strategy / UI が POST /api/order/submit (mode=replay)
  → python/engine/order_router.py が BacktestExecutionEngine.process_order(...) に dispatch
  → SimulatedExchange が約定判定
  → nautilus OrderAccepted / OrderFilled / OrderCanceled / OrderRejected
  → 既存 IPC EngineEvent::Order*（live と同じイベント型を再利用）を venue="replay" で送出
  → Rust UI 側 OrderListStore が venue でフィルタして REPLAY ビューに反映
```

**UI の表示規約**:

- 注文一覧 pane の **header にバナー**「⏪ REPLAY」を表示し、live と視覚的に区別する
- pane の identity は `(mode=replay, pane_kind=order_list)` で 1 つだけ。銘柄ごとに
  分けない（注文を横断して見られた方が UX 上自然）
- 自動生成タイミング: 1 銘柄目の `/api/replay/load` 成功時に **同時に 1 枚**生成する
  （D9.3 のチャート 2 枚と並べて配置）
- live モード時は当該 pane を生成しない（live は既存の注文一覧 pane を使う）
- 表示する状態列: `client_order_id` / `instrument_id` / side / qty / price / status /
  filled_qty / avg_fill_price / `ts_event`（仮想時刻）
- 監査ログ WAL `tachibana_orders_replay.jsonl` の内容と整合（live の `tachibana_orders.jsonl`
  は参照しない）

**使う IPC / state（既存資産の再利用範囲）**:

| 項目 | 既存 | 新規 |
|---|---|---|
| `EngineEvent::OrderAccepted/Filled/Canceled/Rejected` | ✅ live と共有 | venue=`"replay"` で発火する分岐を `engine_runner.py` に追加 |
| Rust 側 `OrderListStore` | ✅ 既存 | venue で 2 つの view（live / replay）に分割 |
| HTTP `/api/order/list` | ✅ 既存 | クエリ `?venue=replay` を追加（既存 live はデフォルト動作維持） |

#### D9.6 買付余力の REPLAY 連動

REPLAY 実行中の「買付余力（buying power）」表示は **live の立花 `CLMZanKaiKanougaku`
を一切参照せず、REPLAY 用の simulated portfolio / cash state**から算出する。

**データ経路**:

```
nautilus BacktestEngine の Portfolio / Account state
  → python/engine/nautilus/portfolio_view.py（新設）が定期 snapshot 化
  → 新規 IPC EngineEvent::ReplayBuyingPower { strategy_id, cash, buying_power, equity, ts_event_ms }
  → Rust UI 側 BuyingPowerStore が REPLAY ビューに反映
```

**算出ルール（N1 確定）**:

- `cash` = 起動 config `initial_cash` − 仮想約定の累計支払額 + 仮想約定の累計受取額
- `buying_power` = `cash` をそのまま使う（**現物のみ・信用なし**で N1 を確定。信用は
  N2 以降）
- `equity` = `cash` + 全仮想 position の `mark_to_market`（直近 `TradeTick.price` を使用）
- `mark_to_market` の price 取得は streaming ループの最新 tick を使う（決定論性：
  仮想時刻ベース）

**UI の表示規約**:

- 買付余力 pane / ヘッダ表示は **header にバナー**「⏪ REPLAY」を表示し、live と視覚的に区別する
- live モード時は既存の `CLMZanKaiKanougaku` 経路をそのまま使う（本決定は触らない）
- REPLAY モード時に立花 `CLMZanKaiKanougaku` を **参照しない**ことをコードで保証する
  （Python 側 `order_router.py` の dispatcher 分岐で REPLAY 中は HTTP 呼び出しを skip）
- 値の更新頻度: 仮想約定発生時に即時更新 + 一定間隔（**1 秒）の MTM 再計算**
- 桁・通貨表記は live の表示器を再利用（JPY、整数表示）

**使う IPC / API / state（新規 vs 既存）**:

| 項目 | 既存 | 新規 |
|---|---|---|
| nautilus `Portfolio.account_for_venue(SIM)` | ✅ nautilus 側で取れる | — |
| `EngineEvent::ReplayBuyingPower` | — | **新規 IPC**（dto.rs schema 1.4 に追加） |
| Rust 側 `BuyingPowerStore` | ✅ 既存 live 用 | venue で 2 つの view に分割 |
| HTTP `/api/replay/portfolio` | ✅ 既存（spec §4） | レスポンスに `cash` / `buying_power` / `equity` を追加 |

#### D9.7 REPLAY 終了 / 再読込時のクリーンアップ

| イベント | pane の扱い | overlay の扱い | 注文一覧 | 買付余力 | chart buffer |
|---|---|---|---|---|---|
| `StopEngine`（replay セッション終了） | 自動生成 pane は **残す**（最終状態の閲覧用） | 表示維持 | 最終状態を維持（新規受付なし） | 最終 snapshot を維持 | 維持 |
| `/api/replay/load` 再実行（同銘柄） | 既存 pane を再利用 | **クリア**（新セッション分のみ表示） | **クリア** | `initial_cash` から再計算 | **クリア** |
| `/api/replay/load`（別銘柄） | 別 identity なので新規生成（D9.3 ルール） | 当該 pane は空から | 既存 REPLAY 注文一覧に追加表示（同セッション） | 既存 portfolio に統合 | 当該 pane は空から |
| プロセス再起動（D8 経路） | レイアウト復元のみ。bind は再 load まで張られない | クリア（D8） | クリア（D8） | クリア（D8） | クリア（D8） |

- 「自動生成 pane を `StopEngine` で閉じる」案は **採らない**: バックテスト結果の
  目視確認が UX の主目的なので、終了で消えると逆効果
- 再読込時の overlay / buffer / 注文一覧 / 買余力クリアは「同 pane を別実験に再利用」する
  自然な UX
- WAL（`tachibana_orders_replay.jsonl`）は **再読込でも消さない**（永続監査の原則）

#### D9.8 N1 / N2 スコープ振り分け

| 項目 | N1 | N2 以降 |
|---|:---:|:---:|
| Tick pane / Candlestick pane の自動生成 | ✅ | — |
| 重複生成防止 | ✅ | — |
| 横並び 2 分割（1 銘柄目） / フォーカス縦分割（2 銘柄目以降） | ✅ | — |
| `MAX_REPLAY_INSTRUMENTS = 4` ハードリミット | ✅ | — |
| 自動生成 pane を手動で閉じた場合の再生成抑止 | ✅ | — |
| `StopEngine` 後の pane 残存 | ✅ | — |
| `/api/replay/load` 再実行時の overlay / buffer / 注文一覧 / 買余力クリア | ✅ | — |
| **REPLAY 注文一覧 pane（venue=replay 専用 view、バナー付き）** | ✅ | — |
| **REPLAY 買付余力（現物のみ、`cash` ベース）** | ✅ | — |
| **`EngineEvent::ReplayBuyingPower` IPC + 1 秒 MTM 更新** | ✅ | — |
| **REPLAY 中の `CLMZanKaiKanougaku` 参照禁止コードガード** | ✅ | — |
| 信用余力（margin buying power）算出 | — | ✅ |
| active / selected instrument のみ自動表示する省略 UX | — | ✅ |
| 専用 replay レイアウトテンプレート | — | ✅（要望次第） |
| pane 配置のユーザー設定永続化（`saved-state.json` 拡張） | — | ✅ |
| pane の D&D 並べ替え後も自動生成ルールが破綻しないこと | — | ✅ |
| 注文一覧の銘柄別フィルタ / ソート UX | — | ✅ |
| 買付余力の信用・建玉・手数料考慮 | — | ✅ |

### D6. マーカーの 2 系統分離（StrategySignal / ExecutionMarker）

R1 では `StrategySignal` を `OrderFilled` 受領時に narrative_hook から送出する
タスクだったが、これだと「発注したが約定しなかったシグナル」「戦略が出した注釈」が
取れない（D2 と矛盾）。R2 で 2 系統に分離する:

| イベント | 送出元 | 用途 | 例 |
|---|---|---|---|
| `EngineEvent::ExecutionMarker` | `narrative_hook.py` が `OrderFilled` を受けて自動送出 | **約定結果**を point で表示 | 買い fill ▲, 売り fill ▼ |
| `EngineEvent::StrategySignal` | Strategy が `self.emit_signal(kind, side, price, note)` で**明示送出** | **戦略の意思**を表示（未約定でも、エントリー条件成立タイミングなど） | EntryLong 候補 ◇, Annotate（任意ラベル） |

- `emit_signal()` は nautilus `Strategy` に薄いヘルパとして足す（adapter 層、nautilus
  本体に手を入れない）
- `signal_kind` の語彙は `EntryLong / EntryShort / Exit / Annotate{tag}`
- iced 側はレイヤー 2 段（execution layer / signal layer）を重ねて描画する

## 計画各文書への反映指示

### README.md

- 「2 つのモードと制約」表の脚注に **D1（板は live 専用、不変条件）** を太字で追加
- 「長期方針」末尾に新節「**Rust UI の役割境界**」を追加し D2 / D3 を要約
- 「2 つのモードと制約」直下に **D8（起動時モード固定・ランタイム切替なし）** を 1 段落で追加
- 「Rust UI の役割境界」節に **D9（REPLAY 銘柄追加で Tick + Candlestick pane を自動生成、重複生成なし、上限 4 銘柄、REPLAY 専用の注文一覧 + 買付余力で live と分離表示）** を 1 段落で要約

### spec.md

- §3.1 を D4 に従い改訂（wall clock 非参照を `run()` 自走経路に限定、streaming 経路を別扱い）
- §3.5.1 の表のすぐ下に「**この不変条件は計画レベルで確定（2026-04-28）**」を一行追記
- §2.2 N1 のスコープに以下を追加:
  - **N1.11（新設）** Replay 再生 speed コントロール（streaming=True 経路）+ IPC
    `Command::SetReplaySpeed` を schema 1.4 に追加。**Pause / Seek は N1 では含めない**
  - **N1.12（新設）** `EngineEvent::ExecutionMarker`（fill 由来・自動）と
    `EngineEvent::StrategySignal`（Strategy 明示送出）を追加
  - **N1.13（新設）** 起動時モード固定の CLI 引数 `--mode {live|replay}` を追加
    （D8）。ランタイム切替コマンドは追加しない
  - **N1.14（新設）** REPLAY 銘柄追加時に Tick pane と Candlestick pane を自動生成
    （D9.1〜D9.4）。重複生成防止・上限 4 銘柄・手動 close 後の再生成抑止を含む
  - **N1.15（新設）** REPLAY 注文一覧 pane の自動生成と `venue="replay"` フィルタ
    （D9.5）。バナー付き表示、`tachibana_orders_replay.jsonl` との整合
  - **N1.16（新設）** REPLAY 買付余力表示（D9.6）。`EngineEvent::ReplayBuyingPower`
    新規 IPC、`portfolio_view.py` 新設、`CLMZanKaiKanougaku` 誤参照防止コードガード
- §4 の API 表に追記（IPC 写像を併記）:

  | HTTP エンドポイント | body | IPC 写像 |
  |---|---|---|
  | `POST /api/replay/control` | `{action: "speed", multiplier: 1\|10\|100}` | `Command::SetReplaySpeed { multiplier }` |

  **N1 で受理する action は `"speed"` のみ**。streaming ループの開始は既存の
  `StartEngine` に統一する（`/api/replay/control` から `play` action を提供しない）。
  `pause` / `seek` を含む他 action は **400 Bad Request**。`seek` の意味論（相対 /
  絶対）の確定は Q14 に委ねる

### architecture.md（D9 反映）

- §1 配置原則の「責務分割」表に追記:
  | 責務 | 所在 | 備考 |
  | :--- | :--- | :--- |
  | **REPLAY pane の自動生成と identity 管理** | **Rust UI（iced）** | `(mode, instrument_id, pane_kind)` で identity を取り、`/api/replay/load` 成功イベントを契機に生成判定を行う |
  | **REPLAY 注文一覧 view** | **Rust UI（iced）** | `OrderListStore` を venue で 2 view に分割。REPLAY view は `venue="replay"` のイベントのみ反映、バナー付き |
  | **REPLAY 買付余力 view** | **Rust UI（iced）** | `BuyingPowerStore` を venue で 2 view に分割。REPLAY view は `EngineEvent::ReplayBuyingPower` のみ反映、`CLMZanKaiKanougaku` を一切参照しない |
  | **REPLAY portfolio snapshot** | **Python `python/engine/nautilus/portfolio_view.py`（新設）** | nautilus `Portfolio` から `cash` / `equity` / `mark_to_market` を 1 秒間隔で算出 |
- §3 IPC メッセージに追加:
  ```rust
  pub enum EngineEvent {
      ReplayBuyingPower {
          strategy_id: String,
          cash: String,            // 文字列精度規約
          buying_power: String,    // N1 は cash と同値（現物のみ）
          equity: String,          // cash + Σ position MTM
          ts_event_ms: i64,        // 仮想時刻
      },
  }
  ```
- §4 データフロー（replay モード）の冒頭に「**`/api/replay/load` 成功 → Rust UI が
  Tick + Candlestick + 注文一覧 + 買付余力 の 4 種 pane を自動生成（identity 重複なら
  skip）→ それぞれが対応する IPC（`Trades` / `KlineUpdate` / `Order*` / `ReplayBuyingPower`）
  を venue=replay で購読する**」の 3 行を追加
- §4 末尾に「**REPLAY 中は立花 `CLMZanKaiKanougaku` HTTP 呼び出しを `order_router.py`
  で skip する**」を 1 行追加（誤参照防止のコードガード）

### architecture.md

- §3 IPC メッセージ表に以下を追加（**N1 で実装する分のみ**）:

```rust
pub enum Command {
    // N1: streaming ループ間の wall-clock pacing を変える
    SetReplaySpeed { request_id: String, multiplier: u32 },   // 1 | 10 | 100
    // PauseReplay / ResumeReplay / SeekReplay は Q14 で再評価。N1 では追加しない
}

pub enum EngineEvent {
    // OrderFilled 由来・narrative_hook が自動送出
    ExecutionMarker {
        strategy_id: String,
        instrument_id: String,
        side: OrderSide,            // Buy | Sell
        price: String,              // 文字列精度規約
        qty: String,
        ts_event_ms: i64,
        client_order_id: String,
    },
    // Strategy.emit_signal(...) による明示送出
    StrategySignal {
        strategy_id: String,
        instrument_id: String,
        signal_kind: SignalKind,    // EntryLong | EntryShort | Exit | Annotate
        side: Option<OrderSide>,
        price: Option<String>,      // 注釈のみで価格を持たないケースあり
        ts_event_ms: i64,
        tag: Option<String>,        // Annotate 時の任意ラベル
        note: Option<String>,
    },
}
```

- §3 末尾に「**replay 中の市場データは既存 `EngineEvent::Trades` /
  `EngineEvent::KlineUpdate` を再利用する**（D5）。新規 market data event は足さない」
  を明記
- §4 データフロー（replay モード）図の末尾に以下を追加:
  - `OrderFilled → ExecutionMarker → iced execution layer`
  - `Strategy.emit_signal → StrategySignal → iced signal layer`
- §6 末尾に新節「**6.1 再生コントロールと実行モデル**」を追加（D4 の写像）:
  - **headless / 決定論性検証**: 既存の `BacktestEngine.run(start, end)` 自走をそのまま使う
  - **UI 駆動 viewer**: streaming=True ループ（Tpre.1 spike 案 A）を採用し、bar/tick
    を 1 件ずつ `add_data` → `run(streaming=True)` → `clear_data()` で進める
  - `SetReplaySpeed` は streaming ループ間の sleep のみ操作する。式は D7 の
    `min(max(dt_event_sec, MIN_TICK_DT_SEC) / multiplier, SLEEP_CAP_SEC)` で、
    セッション境界（前場-後場 / 引け後 / 営業日跨ぎ）は即時通過。
    仮想時刻 `tick.ts_event` は wall clock から独立で、multiplier に依存しない
  - **Pause / Seek は本フェーズでは実装しない**。Q14 で別途決める

### implementation-plan.md

- N1 の Exit 条件に追加:
  - 「再生 **speed** コントロール（1x / 10x / 100x）が iced UI から効くこと」
  - 「`ExecutionMarker` が `BuyAndHold` の fill に対応する位置に点描されること」
  - 「組み込み Strategy が `emit_signal()` で出した `StrategySignal` が overlay に表示されること」
  - **`/api/replay/load` を 1 件投げるだけで Tick pane と Candlestick pane が自動生成されること**（D9.1〜D9.4）
  - **同銘柄を 2 回 load しても pane が増えないこと**（D9 重複生成防止）
  - **5 銘柄目の load が 400 で拒否されること**（D9 上限ガード）
  - **REPLAY の仮想注文・仮想約定に応じて REPLAY 注文一覧が更新され、live 注文一覧を汚染しないこと**（D9.5）
  - **REPLAY の portfolio / cash 変化に応じて REPLAY 買付余力表示が更新されること**（D9.6）
  - **REPLAY 中に立花 `CLMZanKaiKanougaku` HTTP が呼ばれないこと**（D9.6 誤参照防止コードガード）
  - pause / seek は **N1 Exit 条件に含めない**（Q14 で再評価）
- N1.11 / N1.12 を新設:

```
### N1.11 Replay 再生 speed コントロール（streaming=True 経路）
- [ ] engine-client/src/dto.rs に Command::SetReplaySpeed { multiplier } を追加
      （Pause/Resume/Seek は本タスクに含めない）
- [ ] python/engine/nautilus/engine_runner.py に streaming ループ実装を追加:
      add_data([item]) → run(streaming=True) → clear_data() を 1 件ずつ回す
- [ ] ループ間に D7 の pacing 式で sleep を挟む:
      sleep_sec = min(max(dt_event_sec, 0.001) / multiplier, 0.200)
      （multiplier=1/10/100、SLEEP_CAP=200ms、MIN_TICK_DT=1ms）
- [ ] 前場-後場 / 引け後 / 営業日跨ぎのギャップは sleep=0 で即時通過（D7）
- [ ] 営業日跨ぎ時に UI 向け date-change マーカーを 1 件 emit
- [ ] 既存 run(start, end) 自走経路は headless / 決定論性テストで温存
- [ ] iced 側にコントロールバー pane を新設（1x / 10x / 100x ボタンのみ）
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
```

- D5 を反映: 「replay 用 market data IPC を新設しない（既存 Trades / KlineUpdate 再利用）」
  を計画レベルの方針として N1.4 BacktestEngine ハンドラの注記に追記
- D9 を反映して N1.14 / N1.15 / N1.16 を新設:

```
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
```
- D8 を反映して N1.13 を新設:

```
### N1.13 起動時モード固定（live / replay）
- [ ] Rust 側 main.rs に CLI 引数 --mode {live|replay} を追加（必須・デフォルトなし）
- [ ] IPC Hello に mode を載せ、Python 側で受け取って NautilusRunner に渡す
- [ ] Python 側 server.py の mode 別起動責務（既存「live は N2 から」と整合）:
      - replay: BacktestEngine を起動、LiveExecutionEngine は触らない
      - live  : 既存 Phase 1 の立花 EVENT WS 閲覧経路を起動。
                nautilus LiveExecutionEngine は N1 では起動しない（stub のまま）。
                Hello capabilities は nautilus.live=false を維持
      - mode と StartEngine.engine の不一致は ValueError で拒否
- [ ] iced 側: mode に応じて Depth ペイン visibility・order UI 文言・バナーを切替
- [ ] 切替コマンド（IPC / HTTP）は追加しない
- [ ] python/tests/test_mode_isolation.py:
      - live モードで /api/replay/* が 400 を返す
      - replay モードで /api/order/submit が REPLAY ディスパッチに流れる
      - mode 不一致の StartEngine が拒否される
      - live モードで Hello.capabilities.nautilus.live が false のまま
- [ ] tests/e2e/s55_mode_startup_smoke.sh: --mode live と --mode replay の両方で
      ハンドシェイクと最小 stream が動くこと
```

- D3 の「PnL は narrative」方針を反映し、N1 から **PnL 可視化タスクを除外**することを
  「削除リスト」に追記:
  - `[ ]` 旧計画にあった iced 側 PnL 曲線 pane 構想（あれば）→ N1 では実装しない

### open-questions.md

- **Q13（新設）** `StrategySignal.signal_kind` の語彙確定: `EntryLong / EntryShort
  / Exit / Annotate` の 4 値で N1 を始める。`Annotate` の `tag: String` を後方互換に
  保ったまま語彙を増やせるか（dto.rs の enum vs `kind: String` の選択）
- **Q14（新設、R2 でクリティカル化）** Replay の Pause / Seek 実行モデル:
  - 現状 Q3 は「`run(start, end)` 自走 + wall clock 非参照」で確定済み
  - N1 で speed pacing は streaming=True 経路（Tpre.1 spike 案 A）で実装する
  - **Pause / Seek を将来導入する場合に streaming 経路に統合するか、別の実行モードを増やすかは未定**
  - サブ問題:
    - Pause 中の fill in-flight（指値が pause 直前にクロスしている）の扱い
    - Seek（未来方向）で中間 tick を Strategy が見ない場合の決定論性テスト戦略
    - Seek（巻き戻し）は `BacktestEngine` 再起動が必要 → state cache の初期化責務
  - N2 着手前に再評価する
- **Q15（新設、D8 連動）** ランタイム `live ⇄ replay` 切替を将来導入する場合の責務:
  - 立花 EVENT WebSocket 購読の解除・再購読の整合性
  - 板ペイン visibility 制御（ペイン破棄か空保持か）
  - in-memory order list のスコープ（モード境界で空 / live と replay で 2 つ持つ）
  - selected instrument / chart range の引継ぎ可否
  - nautilus `BacktestEngine` と `LiveExecutionEngine` の同時起動可否
  - N1 では未解決のまま起動時固定で逃げる。N2 着手前に再評価

## 影響範囲（既存タスクとの衝突確認）

| 既存タスク | 衝突 | 対処 |
|---|---|---|
| N1.1 IPC schema 1.4 | あり（追加 enum） | 同 PR で `SetReplaySpeed` / `ExecutionMarker` / `StrategySignal` を含める。`Pause/Resume/Seek` は含めない。schema_minor は `4` のまま |
| Q3（決着済み） | **あり** | reopen はしない。`run()` 自走を headless 経路で温存しつつ、UI viewer は streaming=True 経路を別建てで足す（D4） |
| spec.md §3.1 wall clock 非参照 | あり | 「`run()` 自走経路に限定」へ表現を緩める。streaming 経路でも仮想時刻独立は維持 |
| N1.4 BacktestEngine ハンドラ | あり | streaming=True ループ実装は N1.11 に切り出し。`run()` 自走経路は触らない |
| N1.6 ナラティブ API | あり（D3） | narrative payload schema に `pnl_summary` / `param_sweep` を追加できる空きフィールドを作る |
| N1.8 互換 lint | なし | 影響なし |
| N2.0 LiveDataClient | なし | live モードは speed の対象外（multiplier=1 固定） |
| 既存 EngineEvent::Trades / KlineUpdate | **追加経路** | replay でも同イベントを再利用するため、`engine_runner.py` の data feed 直前に Rust 向け複製送出を 1 箇所追加（D5） |
| 既存 CLI 引数 / Hello ハンドシェイク | **拡張** | `--mode {live\|replay}` を必須引数として追加し、Hello に `mode` を載せる（D8 / N1.13） |
| iced 側 pane 管理 | **拡張** | `ReplayPaneRegistry` を新設し identity 重複・上限・user_dismissed を管理（D9 / N1.14）。既存 live pane の管理コードには触らない |
| iced 側 OrderListStore / BuyingPowerStore | **拡張** | venue で 2 view に分割し REPLAY 専用ビューを足す（D9.5 / D9.6）。live view 側のロジックは触らない |
| `/api/order/list` / `/api/replay/portfolio` | **拡張** | venue クエリ追加・cash/buying_power/equity 追加。既存契約は破らない |
| `python/engine/order_router.py` | **拡張** | REPLAY モード時に `CLMZanKaiKanougaku` HTTP 呼び出しを skip するガードを追加（D9.6） |

## Rationale 要約

- **板を replay しない**ことを早期に不変条件化することで、後から「分足から板を合成」
  方向への drift を防ぐ
- **iced は再生中の目視に専念、PnL/最適化は narrative + 外部ツール**という役割境界を
  引くことで、iced 側の機能膨張を防ぎつつ matplotlib を完全には置き換えない健全な
  分業を確立する
- **再生コントロールと信号 overlay**は実装コストに対して戦略構築 UX への効果が大きく、
  N1 に組み込む価値がある

## 受け入れ条件（本案を採用とする場合）

- [ ] README / spec / architecture / implementation-plan / open-questions の 5 文書に
      上記反映が入る
- [ ] N1 タスク表に N1.11 / N1.12 が追加される
- [ ] 不変条件「replay は trade stream のみ、板は live 専用」が README に明示される
- [ ] D3 を踏まえ N1 から PnL 専用 UI タスクが除外される
- [ ] D4 を踏まえ spec §3.1（wall clock 非参照）が `run()` 自走経路に限定された表現に改訂される
- [ ] D5 を踏まえ replay 用 market data IPC を新設しない方針が architecture §3 に明記される
- [ ] D6 を踏まえ `ExecutionMarker` と `StrategySignal` が別イベントとして dto.rs に追加される
- [ ] D7 を踏まえ pacing 式・セッション境界・上下限定数が `engine_runner.py` に実装され、N1.11 テストで境界条件が assert される
- [ ] D8 を踏まえ `--mode {live|replay}` が起動時に固定され、ランタイム切替コマンドが追加されないことが README / spec に明記される
- [ ] D9 を踏まえ「銘柄追加でチャートが自動生成されること」「重複 pane が増えないこと」「5 銘柄目で 400」が implementation-plan の N1 Exit 条件に入る
- [ ] D9 の `ReplayPaneRegistry` が architecture §1 の責務分割表に追加される
- [ ] D9.5 を踏まえ「REPLAY 仮想注文に応じて REPLAY 注文一覧が更新される」「live 注文一覧を汚染しない」が N1 Exit 条件に入る
- [ ] D9.6 を踏まえ「REPLAY portfolio / cash 変化に応じて REPLAY 買付余力が更新される」「`CLMZanKaiKanougaku` が REPLAY 中に呼ばれない」が N1 Exit 条件に入る
- [ ] `EngineEvent::ReplayBuyingPower` が dto.rs に追加される（schema 1.4）
- [ ] `python/engine/nautilus/portfolio_view.py` が新設される
- [ ] Q14（Pause / Seek 実行モデル）/ Q15（ランタイムモード切替）が open-questions.md に追加される
- [ ] `/api/replay/control` の N1 受理 action が `"speed"` のみであることが API 表で一貫している
