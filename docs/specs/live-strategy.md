---
title: ライブ戦略 venue 横断契約
status: proposed
migrated_from:
  - docs/✅tachibana/spec.md#abstract-contract
source_commit: 236c0d2
---

# ライブ戦略 venue 横断契約

> **status: proposed** — 本ドキュメントは venue 横断の抽象契約をまとめる新規スケルトンであり、内容は各 venue spec から抽象化される過程にある。tachibana / kabusapi / 暗号資産 venue のいずれもこの契約に従う前提で実装されているが、文書としての整合化は移送後の継続課題。

## 1. 目的

「ライブ戦略 = 実 venue（broker / 取引所）に対して subscribe / fetch / 認証を行うモード」全般に共通する抽象契約を、venue 固有 spec から切り出して 1 箇所に集約する。venue 固有事情は各 `docs/specs/venues/<venue>.md` 側に置き、本書はそこからの参照のみを持つ。

## 2. 契約の構成要素（スケルトン）

### 2.1 VenueState FSM

`Idle / LoginInFlight / Ready / Error` の 4 状態を持つ単一 enum。venue ごとの実 state 機械は `src/venue_state.rs::VenueState` を共通利用する。

- 状態遷移の DTO 名は `VenueLoginStarted` / `VenueLoginCancelled` / `VenueReady` / `VenueError`（Python engine event）。
- UI 側の状態管理は `VenueState::{Idle/LoginInFlight/Ready/Error}` 1 本化。
- 詳細な遷移表と冪等性の規約は各 venue spec の「セッション寿命と復旧」節に記載。

### 2.2 venue-ready ゲート

- venue の業務リクエスト（`ListTickers` / `GetTickerMetadata` / `FetchTickerStats` / `Subscribe`）は `VenueReady` 受信後にのみ送ってよい。
- `VenueReady` は冪等イベント。Python サブプロセス再起動検知時のみ Rust 側が状態をリセットし、active subscriptions を 1 度だけ resubscribe する。
- 詳細な timeout / cache / bridge 規約は `engine-client/src/process.rs::ProcessManager` を一次ソースとする。

### 2.3 VenueError DTO 形状

```
VenueError { venue, request_id, code, message }
```

- `code` は UI 側の severity 判定とアクションボタン出し分けにのみ使う。
- `message` は Python 側 venue コードが詰めた user-facing 文言を Rust UI がそのまま描画する。
- venue 固有 `code` 一覧と severity マッピングは各 venue spec の「失敗モードと UI 表現」節を参照。

### 2.4 認証ライフサイクル

- runtime 中の自動再ログインは禁止（再ログインは「ユーザー明示」操作が起点のときだけ許可）。
- アプリ起動直後の session 復元フェーズに限り、復元 session の validate が失敗した場合の再ログインを 1 回だけ許可する。
- 「自動」と「手動（ユーザー明示）」の境界判別基準は `Command::RequestVenueLogin` の受信を起点とするか否かで分ける。

### 2.5 Strategy SDK 接点

ユーザー戦略（Strategy）が live モードで venue とやり取りする際の境界。本節は別 spec（`docs/specs/strategy-sdk.md`）と相互参照。

## 3. venue 固有 hook

### 3.1 tachibana

venue 固有の契約は `docs/specs/venues/tachibana.md` を参照。本書の 2.1〜2.4 はすべて tachibana の旧 spec.md の決定事項を venue 横断に抽象化したものであり、tachibana 側の実装規約（FD frame quote rule / `p_errno=2` 検知 / dead-frame timeout / banner code 一覧）はそちらで完結する。

### 3.2 kabusapi

# kabusapi 共通 hook（`docs/specs/live-strategy.md` 追記断片）

本ファイルは tachibana エージェントが Wave 4 で作成する `docs/specs/live-strategy.md` に append すべき、kabusapi venue 由来の **venue 横断抽象契約** を切り出したもの。本ファイル単独では参照されず、Wave 4 で統合された後は内容が `docs/specs/live-strategy.md` 側に同化する。

## A. IPC venue キー命名規則

- IPC `venue` フィールド文字列は Rust `Venue::*` enum と 1:1 対応する `snake_case`。
- kabuステーション venue の場合: Rust `Venue::KabuStation` ↔ IPC `"kabu_station"`。
- 立花 venue 同様、`Venue::from_str` で受理し、未知 venue は明示的に reject する。
- venue 文字列追加時は SCHEMA_MINOR bump が必要。

## B. venue ログイン共通ライフサイクル

| IPC メッセージ | 方向 | 発火タイミング |
| :--- | :--- | :--- |
| `RequestVenueLogin{venue}` | Rust → Python | GUI から「ログイン」ボタン押下 |
| `VenueLoginStarted{venue}` | Python → Rust | startup_login 開始時 |
| `VenueLoginCancelled{venue}` | Python → Rust | ユーザーがダイアログをキャンセル |
| `VenueReady{venue}` | Python → Rust | 認証完了（トークン取得など） |
| `VenueError{venue, code, message}` | Python → Rust | エラー検出時 |

すべての venue でこの遷移を満たすこと。

## C. `VenueError.code` 共通予約値

| code | 意味 | 期待挙動 |
| :--- | :--- | :--- |
| `"token_expired"` | トークン失効。retry 失敗時にこの code を発火 | tkinter 再ログインダイアログへ誘導。**自動再ログインは禁止**（ユーザー入力を伴う） |
| `"local_app_down"` | venue 提供アプリ（kabuステーション本体等）が落ちている / 接続不可 | 5s × N 回のバックオフ retry 後に発火。早朝強制ログアウト窓では INFO 扱い |

新 venue 追加時はこの命名空間衝突を `test_schema_compat.py` で検証する。

## D. credential 取り扱い原則

- API パスワード / トークン / 取引パスワードは **Python メモリのみ** に保持し、Rust 経路に流さない。
- ファイル永続化禁止（kabusapi はトークン短命、立花は session cache あり等、venue 毎に差異）。
- `caplog` に credential が出ないことをテストで確認（venue ごとに `test_*_logging.py` を持つ）。
- debug env は venue prefix 付き: `DEV_TACHIBANA_*` / `DEV_KABU_*`。

### D.1 第二暗証番号: プロセス引数経路の禁止（issue #42 統一決定 #7）

**「Rust に流さない」だけでなく「プロセス引数（`argv`）にも入れない」** を加える。
理由は OS の露出経路:

- shell history（`.bash_history` / `.zsh_history` / PowerShell `ConsoleHost_history.txt`）
- `ps` / `wmic process` / Windows タスクマネージャの「コマンド ライン」列
- `procmon` / `dtrace` / `strace` の syscall ログ

非対称な対策として、`python -m engine.live_session_cli` は次の優先順で第二暗証番号を解決する:

| 優先順 | 経路 | 推奨度 |
|--------|------|--------|
| 1 | `--second-password-stdin`（stdin から読む） | **推奨** |
| 2 | `DEV_TACHIBANA_SECOND_PASSWORD` env | 推奨 |
| 3 | `--second-password <plain>`（平文 argv） | **非推奨**（stderr 警告） |

attach mode では CLI は第二暗証番号を **wire に流さない**（engine 側 `SessionHolder` で
事前設定済みである前提）。ユーザーが上記 1-3 のいずれかで指定した場合は、
silent ignore 防止のため stderr に hint を出して捨てる。

#### stdin 仕様（4 経路の挙動 pin）

CLI 内部では `sys.stdin.read().rstrip("\r\n")` で読み取る。trailing newline / CRLF
だけ除去し、内部空白は意図的に保持する（パスワードに `" "` が含まれるケースを潰さないため）。
対話判定は `sys.stdin.isatty()` で行う。

| ケース | 例 | 期待挙動 |
|--------|------|----------|
| heredoc | `python -m engine.live_session_cli ... --second-password-stdin <<< "$PW"` | stdin から `$PW` を読み、内部空白保持で渡す |
| pipe | `echo "$PW" \| python -m engine.live_session_cli ... --second-password-stdin` | 同上（trailing `\n` を rstrip で除去） |
| empty stdin | `python -m engine.live_session_cli ... --second-password-stdin < /dev/null` | 非対話 + 空入力 → `argparse.error` で reject（CI silent failure 防止） |
| 非対話 CI | tty 不在の CI runner で `--second-password-stdin` のみ指定 | tty 不在判定 → 空入力 reject パス（`isatty() == False && raw == ""`） |

実装ファイル: `python/engine/live_session_cli.py::_resolve_second_password`。
受け入れ基準 #20（`test_second_password_stdin_handles_heredoc_pipe_empty_and_noninteractive`）で pin。

## E. ログイン UI は Python tkinter subprocess に統一

- Rust にダイアログコードを書かない。
- subprocess 隔離（メイン engine プロセスをフリーズさせない）。
- 取引パスワード（取消/発注時）の収集 UI も同じ方式に揃える。

## F. capabilities キー追加プロトコル

- `Ready.capabilities.venue_capabilities[<venue>]` に venue 別 capability dict を追加する。
- 必須キー候補: `requires_local_app: bool` / `max_push_symbols: int|null` / `supports_amend: bool` / `requires_trade_password_for_cancel: bool` / `is_production: bool`。
- 数値の一次ソース（例: PUSH 上限）は venue spec ファイルに置き、Rust 定数 / Python 定数と 1:1 一致を invariant test で保証する。

## G. `SubscriptionEvicted{symbol}` 共通通知

- PUSH 銘柄上限のある venue（kabuステーション = 50 上限）で LRU evict が発生したとき、`SubscriptionEvicted{symbol}` を IPC で送出する。
- UI は当該 symbol のチャートに「再登録が必要」バナーを表示する。
- 立花 venue は現状上限なしのため発火しないが、将来 venue 追加時の共通契約として定義する。

### G.1 kabu 再ログイン後の最初 PUSH frame は state seed として skip する（R5 / R7）

kabu PUSH は per-trade qty を持たず、累積 `TradingVolume` のみを返す。サーバ側で
ticker 別 `_kabu_last_trading_volume` を保持し、`delta_qty = current_volume - last_volume`
を per-trade qty として `_live_fd_queue` に流す（`KabuStationLiveDataClient` 経由
で Strategy SDK に届く）。

セッション再ログイン時 (`_clear_kabu_session` 経由) は `_kabu_last_trading_volume`
を `clear()` する副作用があり、**再ログイン直後の最初の kabu PUSH frame は
state seed として skip される**（累積値を 1 件の trade として流すと dedup 異常 /
過剰約定量として silent failure を生むため意図的）。その結果、再ログイン後の
1 件目 live trade tick は失われる。本契約は kabu live data 経路の安全装置と
して扱い、将来 PUSH protocol が per-trade qty を提供するようになった時点で
撤廃する。

## H. URL リテラル所在原則

- 各 venue の API URL リテラルは Python 側 1 ファイル（kabusapi なら `kabusapi_url.py`、立花なら `tachibana_url.py`）に集約。
- Rust / engine-client / その他 Python ファイルへの URL リテラル漏出は CI lint で阻止。
- `localhost:18080` 本番 / `localhost:18081` 検証など環境別 base URL もこのファイルでのみ定義。


## 4. 出典

- `docs/specs/venues/tachibana.md`（旧 `docs/specs/venues/tachibana/spec.md`、source_commit: 236c0d2）
- `src/venue_state.rs`
- `engine-client/src/process.rs::ProcessManager`
- `engine-client/src/dto.rs::EngineEvent::{VenueReady, VenueLoginStarted, VenueLoginCancelled, VenueError}`

---

## 5. ユーザー戦略の live 投入手順

> **status**: issue #42 で起票（Phase 6）。replay → demo → prod の順で動かすことを強く推奨。
> 実コマンド例の充実は `examples/README.md` および `docs/wiki/live-strategy.md` を参照。

リプレイで検証したユーザー戦略ファイルを **無改変で** demo / prod venue に投入する公式手順。

### 5.1 CLI 経路（`python -m engine.live_session_cli`）

`replay_session.py::ReplaySession.run` と対称な live サブコマンド:

```bash
uv run python -m engine.live_session_cli run \
    --strategy examples/test_strategy_minute.py \
    --instrument 8306.T \
    --max-qty 100 \
    --max-notional-jpy 500000 \
    --venue tachibana \
    --demo \
    --mode {auto|attach|inprocess}
```

- 認証: `--user-id` / `--password`（省略時は `DEV_TACHIBANA_USER_ID` /
  `DEV_TACHIBANA_PASSWORD` env）
- 第二暗証番号: §3.2-D.1 を参照。`--second-password-stdin` または env を推奨
- safety:
  - `--max-qty` / `--max-notional-jpy` は **必須**（受け入れ基準 #6）
  - `--prod` は env `TACHIBANA_ALLOW_PROD=1` との **AND 条件**（受け入れ基準 #7）
- exit code:
  - `0` 正常 / `1` 一般エラー / `2` busy / `3` 第二暗証番号要求

#### `--mode {auto|attach|inprocess}` の意味論

`--mode` は `LiveSession` の経路選択 force-override で、既定値は `auto`:

- **`auto`** — `engine-session.json` + `FLOWSURFACE_ENGINE_TOKEN` env が一致する
  engine プロセスへ attach probe を試み、成功すれば `attach`、失敗すれば
  `inprocess` に fallback する (`replay_session.py::_resolve_endpoint_and_token`)。
  CLI 段階では credential の有無を確認しない (attach 経路では engine 側
  `SessionHolder` が credential を保持しているため、CLI が知る必要が無い)。
- **`attach`** — engine プロセスへの attach 必須。失敗時はエラーで終了する。
  credential を CLI 引数で受け取っても **wire に流さない** (統一決定 #7
  「credential を Rust に流さない」の不変条件)。`SecondPasswordRequired` event
  を engine から受信した場合は固定文言を stderr 出力 + exit code `3`。
- **`inprocess`** — helper プロセス内で `NautilusRunner` を直接起動する。
  立花 venue は **credential が必須**: 第二暗証番号が無い場合、`LiveSession.run()`
  は `SecondPasswordRequired` event を `on_event` に emit してから `RuntimeError`
  を raise する (R4 Group B silent-HIGH-2 修正; attach 経路の event と対称)。
  CLI は exit code `3` で終了する。

`auto` 経路で credential 解決を CLI で強制しないのは、attach 経路に到達した場合に
不要な credential 入力を求めない UX 設計 (CLI が解決を試みる場所は env / `--password` /
stdin の 3 経路だけで、それらが無くても attach 経路では成立する)。`inprocess` への
fallback で credential 不在が判明した場合のみ `LiveSession.run()` 側で
`SecondPasswordRequired` event / `ValueError` として表面化する。

`SecondPasswordRequired` を engine から受信した場合、CLI は stderr に固定文言
**「第二暗証番号を設定してください」** を出力して exit code `3` で終了する
（受け入れ基準 #8 CLI 部分、`SECOND_PASSWORD_REQUIRED_MESSAGE` 定数で pin）。

### 5.2 GUI 経路（`File > Open` → 戦略ファイル選択）

iced GUI で `File > Open...` メニューから `.py` 戦略ファイルを選択すると、
`LiveStrategyFormModal` が開いて 4 フィールド（`instrument_id` / `strategy_file` /
`max_qty` / `max_notional_jpy`）の入力を促す。Submit すると engine に
`StartEngine{engine: "Live"}` を送る。

`LIVE_SCENARIO` 定数を持つ戦略ファイルを選んだ場合は、engine が
`LiveStrategyScenarioLoaded` 経由でフォームを **自動 prefill** する
（受け入れ基準 #13）。`LIVE_SCENARIO` を持たない戦略ファイルでも engine は
即時 `LiveStrategyScenarioLoaded { instrument_id: None, ... }` を返す
（5s 待たせない、受け入れ基準 #23）。

`LiveStrategyReady` 受信で 4 ペイン（CandlestickChart / TimeAndSales /
OrderList / BuyingPower / Positions）が自動生成され、冪等 key
`(strategy_id, instrument_id, venue)` で重複生成を防ぐ（受け入れ基準 #11 / #17）。

`SecondPasswordRequired` を engine から受信した場合、GUI は
**ステータスバーに赤帯で固定文言「第二暗証番号を設定してください」** を表示する
（受け入れ基準 #8 GUI 部分、CLI と同一文言）。

### 5.3 `TACHIBANA_ALLOW_PROD` ガード

prod venue に発注する経路を物理的に塞ぐ env 固定 SoT。**engine プロセスの env が
single source of truth** で、GUI から触れない（issue #42 統一決定 #14）。

- `TACHIBANA_ALLOW_PROD=1` リテラル一致のみ true 扱い。`"true"` / `"yes"` 等は
  unsafe を倒すために **false** とする。
- env 変更には engine プロセスの **再起動が必須**。GUI が capability `is_production`
  を読み取って disable 判定するが、env を runtime に書き換える経路は持たない。
- tachibana worker は `capabilities()` で `{"is_production": <bool>}` を expose
  し、Rust 側 `engine_client::capabilities::is_production(caps, "tachibana")` 経由で
  読み取る（受け入れ基準 #18）。

### 5.4 `is_market_open()` ガード SoT

「市場閉場時刻に live を起こさない」ガードは **engine 側の authoritative reject** を
SoT とする（issue #42 統一決定 #5）。

- `engine.nautilus.engine_runner.start_live()` の冒頭で `is_market_open(now_utc)`
  を確認し、false なら `EngineError{code:"market_closed", strategy_id}` を emit して
  warm_up に到達する前に abort する（受け入れ基準 #9）。
- CLI / GUI は **事前 hint のみ**（stderr or banner）。authoritative reject は engine
  に集約することで、time skew や境界 race を engine 1 箇所で管理する。

### 5.5 demo → prod 移行の安全装置リスト

| # | 安全装置 | 実装場所 | 受け入れ基準 |
|---|---------|---------|------------|
| 1 | `max_qty` 必須（1 ≤ n ≤ 10000） | CLI argparse + `EngineStartConfig` validator | #6 |
| 2 | `max_notional_jpy` 必須（1 ≤ n ≤ 100_000_000） | CLI argparse + `EngineStartConfig` validator | #6 |
| 3 | `--prod` は `TACHIBANA_ALLOW_PROD=1` env と AND | CLI argparse | #7 |
| 4 | `is_market_open()` 認可 reject | `engine_runner.start_live` 冒頭 | #9 |
| 5 | warm_up 失敗（例外 OR `False` 戻り値）→ `EngineError{warm_up_failed}` + `exec_client.close()` | `engine_runner.start_live` | #14 |
| 6 | `SecondPasswordRequired` フロー（CLI 非ゼロ + GUI 赤帯、固定文言） | CLI / GUI 両経路 | #8 |
| 7 | 同 venue concurrent live → `EngineBusy{busy_kind:"another_strategy_on_venue"}` | `server.py::_handle_start_engine` | #16 |
| 8 | 同一 strategy_id concurrent → `Error{code:"engine_already_running"}` | `server.py::_engine_tasks` ガード | #16 |
| 9 | credential（特に第二暗証番号）を Rust / argv に流さない | §3.2-D / §3.2-D.1 | — |
| 10 | `LiveSession.login()` 未呼出 → `LiveSession.run()` 経路の不在 | `tools/lint/check_live_login_call.py` AST lint | #10 |
| 11 | kabu 再ログイン直後の最初 PUSH frame は state seed として skip（累積 TradingVolume を 1 件として流す silent failure 防止）| `server.py::_clear_kabu_session` + `_on_kabu_board_push` の state seed 判定 | §3.2-G.1 |

**移行フロー推奨**:

1. `python -m engine.replay_session run --mode inprocess --strategy <file> ...` で
   過去データに対する PnL / behavior を検証
2. `python -m engine.live_session_cli run --demo --mode attach --strategy <file> ...`
   で demo 口座に発注（`SubscriptionEvicted` / 板乖離 / `is_market_open` などの
   live 固有エッジを実機で確認）
3. **十分な demo 試験を経たうえで** `TACHIBANA_ALLOW_PROD=1` を set し、
   engine プロセスを再起動してから `--prod` で本番発注

各段階で同じ戦略ファイル（`examples/test_strategy_*.py` 等）を **無改変で** 持ち回せる
ことが本仕様の核（受け入れ基準 #1）。
