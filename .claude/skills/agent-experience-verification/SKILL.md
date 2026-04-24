---
name: agent-experience-verification
description: flowsurface の HTTP API を「ユーザーの分身としてのエージェント」として実際に叩き、ナラティブ基盤など機能が体験として成立しているかを検証するスキル。E2E テストでは拾えない実環境バグ（バッファ制限・配線漏れ・非決定性）を炙り出す。Phase 4a ナラティブ基盤の検証パターンに準拠。
---

# Agent Experience Verification

E2E テスト（仕様駆動）と単体テスト（実装駆動）の隙間を埋める「**実体験ベース検証**」のスキル。
あなたがエージェントとして HTTP API を叩き、観測 → 判断 → 発注 → 記録 → 振り返り のサイクルを回す。

仕様書通りに動くだけでなく、**実環境で違和感なく使えるか** を確かめる。

## いつ使うか

- 新機能（特に HTTP API + 永続化 + 非同期処理が絡むもの）の実装直後
- E2E と単体テストが全パスしているのに「本当に動くのか」不安が残るとき
- Phase X-a の verification-loop 直前の最終チェック
- 既存機能のリグレッション疑いを手早く確認したいとき

## 起動モード選定（最初に決める）

headless と GUI は別モードなので、検証目的に応じて選ぶ。**両方で回す必要があるケースが多い** — headless で回るだけだと GUI 固有の配線バグを見逃す（Phase 4a C-2 の `step_forward` 配線漏れが実例）。

| モード | 使う場面 | バイナリ | 注意 |
|---|---|---|---|
| **headless** | 仕様通り動作することの確認、CI での再現、純 API 経路 | `target/release/flowsurface.exe --headless --ticker ... --timeframe ...` | `--ticker` / `--timeframe` CLI が効く |
| **GUI (debug)** | GUI 経路でのみ発火するイベント配線・描画更新・Subscription 同期の検証、立花証券 API を含む検証 | `target/debug/flowsurface.exe`（CLI 引数は無視） | `#[cfg(debug_assertions)]` 下の `DEV_USER_ID` / `DEV_PASSWORD` 自動ログインは **debug ビルドのみ**有効 |
| **GUI (release)** | プロダクション配布バイナリの挙動確認のみ | `target/release/flowsurface.exe` | 自動ログイン無効・ログイン画面で手動操作必須。自動エージェント検証には不向き |

**GUI で `--ticker` / `--timeframe` は効かない**（`clap` 定義は `src/headless.rs` のみ）。GUI では保存済みダッシュボード設定（`AppData/Roaming/flowsurface/saved-state.json`）が復元されるので、違う ticker/timeframe を使いたければ **`POST /api/pane/set-ticker` / `POST /api/pane/set-timeframe` で動的に切り替える**。保存状態が想定外（前回 Tachibana:7203 のまま残っていた等）でも API で修正できる。

---

## 行動ループ（テンプレート）

```
1. ビルド: cargo build && cargo build --release
   ※ debug / release 両方必須。FlowsurfaceEnv._find_binary() は FLOWSURFACE_BINARY 未指定で
     target/debug を優先するため、release だけビルドすると古い debug で再体験することになる。

2. 既存プロセス確認（ポート衝突予防）:
   netstat -ano | grep "9876 " | grep LISTENING
   tasklist //FI "IMAGENAME eq flowsurface.exe"
   ※ 見つかったら taskkill //PID <N> //F で落としてから起動する。
     既存プロセスが 9876 を掴んだままだと、新しく起動した方は sub ライブラリ bind に失敗するが
     プロセス自体は生き続ける「無言フェイルオープン」状態になり、
     curl の応答は既存プロセスから返る → 違うバイナリを検証していることに気付けない。
     起動後のログで `Failed to bind replay API server on 127.0.0.1:9876 (os error 10048)`
     が出ていないか grep で必ず確認する。

3. 起動:
   ## (a) headless モード
   ./target/release/flowsurface.exe --headless --ticker BinanceLinear:BTCUSDT --timeframe M1 &

   ## (b) GUI debug モード（立花証券 API 検証を含む場合・自動ログインが必要な場合）
   set -a; source .env; set +a                           # .env から DEV_USER_ID/PASSWORD/IS_DEMO を読み込む
   ./target/debug/flowsurface.exe &                      # CLI 引数は効かないので渡さなくて良い
   until curl -s --max-time 1 http://127.0.0.1:9876/api/replay/state >/dev/null; do sleep 2; done
   grep "Failed to bind" <log>                           # 空であることを確認（衝突検知）
   grep -iE "Attempting to restore tachibana|Loaded tachibana|auto[-_]login" <log>
                                                         # 自動ログインまたは keyring 復元成功を確認

   ※ FLOWSURFACE_DATA_PATH は設定しない（既定 AppData を使う）。env 上書きすると
     data_path() の path_name 引数が捨てられる既知バグ (data/src/lib.rs:133-144) を踏む。

4. 検証対象機能のセットアップ
   ## GUI モードの場合、saved-state のペイン構成が想定と違うことがあるので最初に確認:
   curl -s http://127.0.0.1:9876/api/pane/list | python -m json.tool

   ## 必要に応じて API で ticker / timeframe を切り替える:
   curl -s -X POST http://127.0.0.1:9876/api/pane/set-ticker  -d '{"pane_id":"<candlestick pane id>","ticker":"BinanceLinear:BTCUSDT"}'
   curl -s -X POST http://127.0.0.1:9876/api/pane/set-timeframe -d '{"pane_id":"<candlestick pane id>","timeframe":"M1"}'
   ※ "ticker info not loaded yet" が返る場合はメタデータ fetch を待つ（最大 10 秒程度リトライ）。

   ## リプレイ開始:
   curl -s -X POST http://127.0.0.1:9876/api/replay/play -d '{"start":"2023-11-14 12:00","end":"2023-11-14 14:00"}'
   curl -s -X POST http://127.0.0.1:9876/api/replay/pause
   curl -s http://127.0.0.1:9876/api/replay/state                   # klines が 0 件なら準備未完了

   ## Windows での tmp パス注意:
   ## git-bash の `/tmp/foo` は Python (Windows ネイティブ) からは見えない。
   ## ヘルパースクリプトや state.json のキャッシュは `C:/tmp/` 配下を使う:
   ##   mkdir -p C:/tmp/verify && python script.py C:/tmp/verify/state.json

   ## ★ Windows git-bash の `python` / `python3` は Windows Store スタブ:
   ## 実体は "Python" を stderr に吐いて exit 49 で終わるリダイレクタ。
   ## `curl ... | python -m json.tool` や `python -c "..."` はサイレントに失敗し、
   ## パイプ先変数が空になる（2026-04-22 検証で 5 回踏んだ）。
   ## 代替:
   ##   - `uv run python script.py` （pytest も uv run 経由で実行すること）
   ##   - `node -e "..."` （JSON パースには node のほうが軽い）
   ##   - curl の出力をファイルに保存してから node で読む:
   ##       curl -s ... > C:/tmp/verify/state.json
   ##       node -e "const d=require('C:/tmp/verify/state.json'); console.log(d.klines.length)"

5. エージェントとして 1 サイクル実行:
   a. 状態観測（GET /api/replay/state など）
      - `/api/replay/state` の返却は `{current_time_ms, klines, trades}` の 3 フィールドのみ。
        `status` / `mode` は含まれず、play/pause 状態は `/api/replay/status` で別取得する。
      - 同じ状態を 2-3 回観測して決定的か確認（2026-04-22 検証では完全一致を確認済み）
   b. 観測から判断（reasoning は自然言語で自分で書く）
   c. アクション実行（POST /api/replay/order など）
      - 正しいスキーマ: `{"ticker":"...","side":"buy|sell","qty":N,"order_type":"market"}`
        または `{"order_type":{"limit":<price>}}`（H6 参照 — 誤った形式はサイレントに market 扱い）
   d. 判断を記録（POST /api/agent/narrative）
      - `action` は `{side, qty, price}` の 3 フィールド固定（`src/narrative/model.rs:78-82`）。
        `"kind":"observation"` のような観測専用バリアントは存在しない。
        観測サイクルは narrative を書かず state 決定性のみ検証するのが素直。
   e. 時間を進める（POST /api/replay/step-forward × 数回）
   f. 結果を確認（GET /api/agent/narrative/:id で outcome 自動充填確認）

6. バリエーションを変えて 3-5 サイクル繰り返す:
   - 成行 / 指値 × buy / sell
   - idempotency_key あり/なし（再送で結果安定するか）
   - public フラグ true / false 往復
   - payload サイズ 極小 / 大きめ（数百 KB） / 上限超え

7. 振り返り:
   - 履歴一覧（GET /api/agent/narratives?...）
   - スナップショット復元（GET /api/agent/narrative/:id/snapshot で記録時と一致するか）
     ※ 返却は **decompressed の JSON（Content-Type: application/json）**。gzip バイナリ
       ではないので `gunzip` 不要。`snapshot_ref.size_bytes` は **gzip 圧縮後**のサイズ、
       `snapshot_ref.sha256` も **gzip バイト列のハッシュ**。整合性検証は gzip 側を再ダウンロード
       するのではなく、保存された sha256 と `storage` 経由で照合する設計。
   - 蓄積量（GET /api/agent/narratives/storage）
   ※ narrative ストアは AppData に永続化されるので過去セッションのレコードが残る。
     **修正前のバグで `outcome=null` のまま残った legacy narrative は、修正後でも自動充填されない**
     （該当 order_id は VirtualEngine リセットで消滅済み）。検証時は agent_id を毎回ユニークに
     する（例: `agent-gui-verify-YYYYMMDD-HHMM`）か、`since_ms` フィルタで今回分に絞る。

8. Python SDK でも同サイクルが回るか:
   import flowsurface as fs
   fs.narrative.create(...) / list / get / publish / unpublish / snapshot / storage_stats

9. 破綻を検知 → 根本修正 → debug + release 両方リビルド → 3 へ戻る。
```

---

## GUI 経路で遭遇しがちなハマりどころ

### H1. ポート 9876 の「無言フェイルオープン」
二重起動時、後から起動した側は `Failed to bind replay API server on 127.0.0.1:9876 (os error 10048)` を吐いたまま GUI は生きる。curl は既存プロセスに届き、検証対象と違うバイナリを触る事故が起きる。起動直後に必ずログを grep で検査する。

### H2. `--ticker` / `--timeframe` は GUI では無視される
CLI 引数は `src/headless.rs` の `clap` でのみ定義（`src/main.rs:419-449` は `--headless` の有無しか見ない）。GUI は保存済みレイアウトを復元するため、想定外の ticker（例: 前回セッションで Tachibana:7203 を使っていた）で立ち上がる。**`/api/pane/list` で必ず確認**し、違えば `/api/pane/set-ticker` と `/api/pane/set-timeframe` で動的に合わせる。

**E2E テストの罠**: `tests/e2e/s5*.py` は `FlowsurfaceEnv._start_process()` で `--ticker BinanceLinear:BTCUSDT --timeframe M1` を渡して spawn するが、GUI モード（`IS_HEADLESS` 未設定）ではこれが効かないため BTCUSDT pane が立たず、`synthetic_trades_at_current_time()` が空 → `on_tick()` 呼ばれず → 注文が永遠 Pending → S52 の `TC-S52-04` で 30 秒タイムアウト。**GUI モードで E2E を回す前に `/api/pane/set-ticker` を手動で叩く必要がある**（または headless で回す）。Phase 4b で GUI 起動時の CLI 引数尊重が課題。

### H3. `NarrativeAction.price` は `f64`（Option ではない）
成行注文でも `price: 0.0` を明示的に送る必要がある。`price: null` / `price` 欠落は **400 "invalid JSON body"** になる（`src/narrative/model.rs:77-82`）。エラーメッセージからは原因が特定できない（§9 の DX 観察項目と一致）。

### H4. auto-login は debug ビルド専用 + keyring 優先
`.env` の `DEV_USER_ID` / `DEV_PASSWORD` / `DEV_IS_DEMO` / `DEV_SECOND_PASSWORD` は `#[cfg(debug_assertions)]` 下でのみ読まれる（`src/screen/login.rs:140`, `src/connector/auth.rs:91-100`）。release GUI で検証すると手動ログインが必須となり自動エージェント化できない。**立花証券 API を含む検証は必ず debug GUI で行う。**

**keyring セッションが優先される**: 過去に一度ログイン済みなら起動時に `Loaded tachibana session from keyring` ログが出て、`.env` の auto-login は発火せず復元される（demo 環境セッションは閉局まで有効）。これは速くて安定だが、`.env` を更新しても反映されない。新しい認証情報で試したい場合は `POST /api/test/tachibana/delete-persisted-session`（debug 専用）でクリアしてから再起動する。

**起動直後の `p_no` 競合**: keyring 復元 → 即時に daily history fetch が走ると `Tachibana daily history fetch failed: API エラー: code=6, message=引数（p_no:[N] <= 前要求.p_no:[N+1]）エラー` が出ることがある。一度だけのレースで以降は通常動作するため narrative 検証には実害なし（記録のみ）。

### H5. **Output-as-Input サイレント破綻パターン**（バグ 1 の一般化）
エージェントは API レスポンスのフィールドをそのままリクエストにコピーして再投入する。**出力側のフォーマットと入力側が受け付けるフォーマットが一致していないと、サイレントに動かない経路ができる**。

実例（修正済み）: `/api/replay/state` の `klines[].stream` は `"BinanceLinear:BTCUSDT:1m"`（SerTicker 形式 + timeframe）で返るが、`/api/replay/order` の `ticker` は内部で symbol 単体（`Ticker::Display` = `"BTCUSDT"`）と比較。エージェントが state からコピーした `"BinanceLinear:BTCUSDT"` を渡すと 201 で受理されるが `order_book::on_tick` の比較で全件スキップされ永遠 Pending、エラー伝播も Toast もない。修正は `parse_virtual_order_command` で prefix を剥がす（`src/replay_api.rs:584-598`）。

**検証時のチェックリスト**:
- 各リクエスト前に「このフィールドは別 API のレスポンスで見た形式そのままか？」を確認
- 受理されたが「想定通りの副作用」（fill, ペイン更新, narrative outcome 自動充填など）が起きないなら、**まず入力フォーマットの normalize/match ずれを疑う**
- 受信側 API は寛容に（normalize して受ける）、ただし silent-broken な経路を作らないことが原則。受け付けない形式は 400 で明示的に拒否する

### H6. `/api/replay/order` のスキーマ寛容 = サイレント market 化（未修正・2026-04-22 観測）
正しい形式:
- 成行: `{"ticker":"...","side":"buy|sell","qty":N,"order_type":"market"}`
- 指値: `{"ticker":"...","side":"...","qty":N,"order_type":{"limit":<price>}}`

**踏んだ失敗例**: `{"type":"limit","price":2960,...}` を送ると、`parse_virtual_order_command` は未知フィールド `type` / `price` を黙って捨て、`order_type` 欠落 → フォールバックで **market 扱い**（[`src/replay_api.rs:614-629`](../../src/replay_api.rs#L614)）。HTTP 201 で `order_id` / `status:"pending"` が返り、次 tick で close 価格即約定。エージェントは「指値が pending のままで期待通り」と誤読し、outcome.fill_price を見て初めて気付く。H5 の一般則の具体例。Phase 4b で 400 拒否 or 別名正規化が改善候補。

**チェック**: `/api/replay/order` を叩いた直後に **`GET /api/replay/orders` で `order_type` フィールドを目視**し、意図と一致するか確認する癖をつける。

### H7. D1（日足）データは合成トレード = close 1 本
Tachibana などの日足銘柄では Trade EventStore が空で、`synthetic_trades_at_current_time`（[`src/replay/controller/tick.rs:191`](../../src/replay/controller/tick.rs#L191)）が「その日の kline.close」を 1 ティックだけ合成する。結果として:
- **成行**: その日の close で約定（≠ 実取引の寄り付き/引け）
- **指値**: `close >= limit`（sell）/ `close <= limit`（buy）を満たす最初の日に close 価格で約定。**高値/安値が limit に触れても close が越えない限り約定しない**

実取引のザラ場ロジックとは違うので、限界値スレッショルドを設計するときは「close 基準」で考える。検証時は `/api/replay/state` の `trades` が常に空（`[]`）になるのが目印。

### H8. D1 リプレイは 1 秒 = 4 日進行（高速消化に注意）
`/api/replay/play` は `speed: "1x"` でも tick 駆動なので、**D1 で 1 秒間に約 4 本 kline が消化される**（2026-04-22 検証で 6 ヶ月レンジが 2 秒で end 到達）。step-forward で手動進行したいサイクルでは:

1. `play` を叩いた**直後に即 `pause`** を送る（遅延 < 1 秒を推奨）、または
2. `end` を start から 1 週間以内の短レンジに設定して「消化しきっても困らない」状態にする

Binance の M1 / BTC では逆に「1 秒 = 1 bar」でユーザー感覚と合うため気付きにくい。**マルチ銘柄で検証する場合は timeframe 別に進行速度を見積もる**。`/api/replay/pause` → `/api/replay/status` で `current_time` が進んでいないことを確認してからサイクルを始めること。

### H9. Idempotency は narrative 専用、order 側は非対応
`/api/replay/order` のリクエストボディに `idempotency_key` を入れても **silently 無視**される（`parse_virtual_order_command` は読まない）。同じキーで 2 回 POST すると 2 つの別 order_id が発行される。

Idempotency が効くのは `/api/agent/narrative` の `idempotency_key` だけ（再送で `idempotent_replay: true` + 同 id）。エージェントが「記録の冪等性」と「発注の冪等性」を混同しないよう、narrative 側で重複防止し、order 側は重複検出のないフロント層で防ぐ前提で設計する。

---

## マルチエージェント構成

本スキルは **Orchestrator 直列 + 周辺並列** が最適。リプレイ時計・narrative ストア・9876 ポートがすべて single-writer なので、サイクル自体を並列化すると状態競合で再現性が壊れる。以下の 4 役に分割し、破綻が起きないときは Orchestrator + Builder の 2 役で走らせ、Investigator と SDK Verifier は必要時のみ起動する。

### 役割表

| 役 | エージェント種別 | 並列度 | 責務 |
|---|---|---|---|
| **Orchestrator** | メインスレッド（このスキルを発動した Claude 自身） | 1 | 行動ループ（H1–H9）の全工程、narrative id / pane id / リプレイ時計の唯一保持、ログ grep（`Failed to bind` / auto-login 成立）、サイクル間シリアル実行、§9 追記 |
| **Builder** | `rust-build-resolver` | 2（debug / release を 1 メッセージ内 parallel Agent call） | `cargo build` + `cargo build --release` を同時実行、コンパイルエラー時は最小修正。両方成功するまで Orchestrator をブロック。リビルド時も同じ |
| **Investigator**（破綻時のみ） | `silent-failure-hunter` → `implementer` のチェーン | 1（Orchestrator は GUI を落として pause） | Orchestrator が作った「期待 / 実際 / 差分」ログを入力として握り潰しエラー・配線漏れを特定、根本修正を書く。修正後は Builder にバトンタッチ |
| **SDK Verifier**（HTTP サイクル完走後） | 軽量 `general-purpose` | 1（サーバ共有のため Orchestrator と順次） | `tests/python/test_narrative.py` および `fs.narrative.*` の等価サイクルを実行。Orchestrator が残した narrative id を引き継いで SDK からも同一オブジェクトが見えるか確認 |

### Orchestrator の起動フロー（行動ループ側の差分）

```
[step 1 ビルド] を以下に差し替え:
  1 つのメッセージ内で Agent tool を 2 回呼ぶ（parallel）:
    - subagent_type: rust-build-resolver, prompt: "cargo build を通せ（debug）"
    - subagent_type: rust-build-resolver, prompt: "cargo build --release を通せ"
  両方の完了を待ってから step 2 に進む。

[step 9 破綻検知 → 修正] を以下に差し替え:
  9a. GUI を落とす（taskkill）。ポート 9876 が解放されたことを netstat で確認
  9b. Agent(subagent_type: silent-failure-hunter) に差分ログ + 再現手順を渡し調査
  9c. Agent(subagent_type: implementer) に修正を書かせる（失敗テスト経路があれば TDD）
  9d. Builder を再度 parallel 起動（debug + release）
  9e. step 3 に戻る

[step 8 Python SDK] を以下に差し替え（HTTP サイクル完走後）:
  Agent(subagent_type: general-purpose) に narrative id 一覧と想定 outcome を渡し、
  SDK 経由の create/get/list/publish/unpublish/snapshot/storage_stats を走らせ
  「HTTP で見た結果と完全一致するか」だけを報告させる。
```

### 委譲プロンプトのひな型

**Builder（debug / release 並列）**
```
flowsurface の cargo build{,/--release} を通してほしい。失敗時は最小変更で修正。
成功条件: 終了コード 0 かつ target/{debug,release}/flowsurface.exe が更新済み。
作業後、diff 要約と生成バイナリのタイムスタンプを報告。
```

**Investigator（破綻検知時）**
```
Phase 4a narrative 基盤の GUI 検証中に以下の差分を観測した:
- 期待: <Orchestrator が記録した期待挙動>
- 実際: <API 応答のログ>
- 再現: <curl 一式>
握り潰しエラー・エラー伝播の欠落・GUI 固有配線漏れを src/narrative / src/replay_api.rs /
src/screen/dashboard/panel を中心に調査し、根本原因 1 点と最小修正案を報告。
実装はまだしない（implementer に引き継ぐ）。
```

**SDK Verifier**
```
http://127.0.0.1:9876 で稼働中の flowsurface GUI (debug) に対して:
1. tests/python/test_narrative.py を実行
2. 以下の既存 narrative id を SDK 経由で取得し、HTTP 側で記録した値と一致するか検証:
   <id 一覧と期待値を列挙>
3. fs.narrative.create/publish/unpublish/snapshot/storage_stats を 1 回ずつ叩く
結果は「N/M PASS」と一致しなかったフィールドのみを返す。GUI/サーバは落とさない。
```

### 並列化してはいけない境界

- **サイクル間**（成行/指値・idempotency・public・payload サイズ）: 共有リプレイ時計と narrative ストアが single-writer。必ず Orchestrator が 1 本ずつ直列実行する
- **ログ監視**: `Failed to bind` と auto-login 成立確認は起動直後 5 秒以内にしか意味がない。Orchestrator が自分でやる（別エージェント化すると「無言フェイルオープン」検知の責任が曖昧になる）
- **narrative id の発番**: Orchestrator だけが POST を打つ。SDK Verifier は **確認専用**で、新規 id を作らない

### 成功判定

Orchestrator は最終報告でこのスキルのタスク固有出力フォーマットを埋めつつ、Builder / Investigator / SDK Verifier の **サブエージェント呼び出し回数と所要時間** を 1 行付記する。再現コストの見える化のため。

