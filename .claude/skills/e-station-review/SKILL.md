---
name: e-station-review
description: e-station の実装レビュー用スキル。plan/spec/architecture/open questions の整合、phase/task/deferred の境界、Rust-Python 間の契約、bootstrap/reconnect/recovery の見落としや silent failure を優先して洗う。新規テストの収集不能、イベント順序の取りこぼし、pending replay、ready cache、UI 文言契約の崩れも重点確認する。
---

# e-station Review

コードが動くかだけではなく、「計画どおりに安全に動くか」を見るためのレビュー手順として使う。特に phase またぎ、bootstrap、reconnect、FSM、gate、Rust-Python 境界、silent failure を重点的に洗う。

## 使い方

- `implementation-plan.md` の task / phase / acceptance に照らして差分を確認する
- `spec.md` / `architecture.md` / `open-questions.md` と実装のズレを探す
- deferred / non-goal / follow-up を今回の phase に誤って混入させていないか確認する
- pin test が不足している高リスク箇所を先に挙げる
- `git diff` だけでなく `git status --short` を見て未追跡ファイルも review 対象に含める

## 最初に読むもの

- 対象 phase の `implementation-plan.md`
- 関連する `spec.md`
- 関連する `architecture.md`
- 必要なら `open-questions.md`

source of truth を先に固定し、読んだ範囲で何を期待してよいかを明確にしてからコードを見る。

## 基本ルール

### 1. まず phase と task の境界を確定する

- どの task を今回レビューしているかを明示する
- deferred / non-goal / follow-up を先に分離する
- source of truth のどの記述に照らしているかをメモする

### 2. `rg` で関連語を広く拾う

優先 grep 語:
- `invariant`
- `acceptance`
- `source of truth`
- `deferred`
- `TODO`
- `Phase`
- `Task`
- `Ready`
- `Error`
- `Login`
- `pending`
- `reconnect`
- `restore`
- `bootstrap`

### 3. enum / DTO / event / wire を横断する

- enum 名
- DTO 名
- event 名
- field / key
- wire literal
- error code
- capability / feature flag

1 箇所だけ見て安心しない。入口と出口の両方を見る。

### 4. Findings First

レビュー結果は finding 優先で出す。順序は次を基本にする。

1. バグ、回帰、構文エラー
2. gate / recovery / bootstrap の穴
3. silent failure
4. pin test 不足

## 重点観点

### 1. 実装と wire の一致

- 用語だけ正しくて wire literal が違っていないか
- enum / schema / IPC の表現揺れがないか
- UI 表示名と内部キーを取り違えていないか

例:
- `D1` と `"1d"`
- `display_symbol` と `display_name_en`
- `VenueError` と `EngineError`

### 2. deferred 混入の検出

- 別 phase に送る task を今回の phase に紛れ込ませていないか
- open question のまま実装を確定していないか
- source of truth が複数文書で食い違っていないか

### 3. フォローアップ実装の逆流

- T3 の fix が T4/T5 の前提を壊していないか
- ブロッカー解消のつもりで別 phase の責務まで抱え込んでいないか
- README / plan / architecture のどこか 1 箇所にしか書かれていない挙動を実装事実として扱っていないか

### 4. テストで pin すべき不変条件

- 既存成功系の上書きだけで満足しない
- silent failure が起きる経路かを見る
- negative test があるかを見る

特に次は pin 不足を疑う。

- 戻り値だけ正しくて event が抜ける
- 1 回だけ起きるべき副作用
- 復旧後の replay
- 取消や失敗からの hidden path

### 5. Rust と Python の契約

- IPC DTO / event の片側だけ更新していないか
- Python 側の生成値が Rust 側の期待と一致しているか
- normalizer / helper に責務を寄せた結果、呼び出し側のテストが薄くなっていないか

### 6. ライフサイクル全周回

次を最低 1 回ずつ頭の中で通す。

- startup
- reconnect
- restore
- persisted state の再利用
- retry / relogin
- cancel / dismiss
- helper / background task / callback
- ready cache / sticky snapshot の invalidate
- pending intent / deferred replay の clear

## 追加チェック: イベント順序と bootstrap 競合

後発イベントだけを見ると、前発競合を見落としやすい。FSM や gate が正しく見えても、イベント順序や bootstrap 競合のレビューが抜けると本番で壊れる。

- `EngineConnected` / `EngineRehello` / `VenueReady` / `VenueError` / reconnect callback / replay task の発火順を追う
- gate 通過前に refetch / replay / resubscribe が走っていないか確認する
- startup / reconnect / restore / persisted selection / `--data-engine-url` など bootstrap 経路を全部見る
- watch / broadcast / callback / cached readiness / sticky snapshot の再利用を確認する
- `new()` / `new_with_settings()` / `update_handles()` / restore helper / reconnect replay path に同じ gate が入っているか確認する

見落とし例:

- reset event より先に reconnect 後 refetch が走る
- bootstrap helper だけ gate を通り本線が通らない
- managed mode だけ readiness cache を見て external mode で見ない

## 追加チェック: pending replay の戻り漏れ

gate で後で再生する設計は、設定時よりも解除時と失敗時の方が壊れやすい。

- `pending=true` を立てた箇所ごとに、`OFF` / dismiss / cancel / unselect / retry failure / reconnect reset でどう落ちるか確認する
- replay 実行後に `pending` を消しているか確認する
- UI の `selected` / `enabled` / `state` と pending を混同していないか確認する
- `new()` / `restore` / `update_handles()` / reconnect path でも pending が意図せず温存されていないか確認する

pin 例:

- positive だけでなく、途中解除したら replay しない negative test があるか
- reset 後に pending を戻す想定なら、unselect まで含めて検証しているか

## 追加チェック: 新規 venue / enum 追加時の漏れ

新しい venue を足した review では、schema / gate / banner だけ見て満足しない。end-to-end で経路を見る。

- 入口定義、fanout、match を横断して確認する
- `VENUE_NAMES` / backend handle / capability map / filter button / metadata fetch / stats fetch / kline fetch を追う
- `VenueReady` 後に UI gate が正しく動いても、backend 未配線で落ちる経路を疑う
- 起動時初期構築、reconnect 後再構築、restore 後 replay を別々に確認する

## 追加チェック: ready cache / sticky snapshot の invalidate

readiness cache は `VenueError` だけで消して安心しない。`LoginStarted` / `LoginCancelled` / relogin / reconnect でも stale ready を引きずらないかを見る。

- cache を読む側だけでなく、cache を更新する bridge / callback / manager 側も追う
- managed mode と external mode の両方で invalidate が効くか確認する

## 追加チェック: ack 前後の整合

command 送信成功だけで state を進めていないか確認する。authoritative event が来るまで state を確定してはいけない。

- `RequestVenueLogin` などで duplicate suppression をしているなら、`VenueLoginStarted` 受信前に確定していないか確認する
- event 到着競合で重複送信や取りこぼしが起きないか確認する
- `LoginStarted` 後の idempotency だけでなく、pre-ack duplicate の negative test があるか確認する

## 追加チェック: UI 文言契約

state machine や error class が正しくても、最終 UI 文言が本来の責務から漏れていると壊れる。

- action button の表示責務を emitter 側と renderer 側で混ぜていないか確認する
- Python/Rust のどちらが message を組み立てる責務かを確認する
- banner / toast / dialog / inline button のどれが唯一の表示面かを固定して見る
- fixed message 定数を grep し、UI renderer の parse 規約とズレていないか確認する
- メッセージのスナップショット test があっても、その test 自体が壊れていないか別に確認する

## 追加チェック: 新規テストの構文健全性

新規テストが増えていても、そのテスト自体が import / collection できなければ防波堤にならない。未追跡の `??` テストも review 対象に含め、内容の妥当性より前に「そもそもロードできるか」を疑う。

- `git diff` だけで終えず `git status --short` の `??` を必ず拾う
- 日本語 snapshot、長い fixture、文字列リテラルが続く新規 test は syntax error の温床として優先確認する
- pytest や `uv` がその場で動かなくても、少なくとも行番号つきで本文を開き、quote 閉じ忘れ、途中改行崩れ、壊れた文字列リテラルを目視確認する
- 「テストが追加されているから安心」と扱わず、「そのテストは収集可能か」を findings 候補として先に判定する

## 追加チェック: 並列配列の sort による契約破壊

決定論テストや観測用フィールドの追加では、値の一致だけでなく要素同士の対応関係が保たれているかを見る。timestamp 配列と price 配列のような並列リストを別々に `sorted()` していたら、見かけ上安定でもペアリング契約を壊している可能性が高い。

- 複数フィールドが同じ実体を表すなら tuple / struct 単位で並べ替えてから分解する
- `fill_timestamps` と `fill_last_prices` のような関連配列は「同じ index が同じ fill を指すか」を確認する
- determinism test が個別一致しか見ていない場合、対応関係破壊の見逃しとして追加 finding 候補にする

## 追加チェック: Rust テスト品質パターン（T6 実測 2026-04-26）

Rust 統合テストで繰り返し見つかる品質問題を優先確認する。

### `.expect()` フォーマット文字列の未補間

`ops.iter().position(|o| ...).expect("message: {ops:?}")` のように `expect` の引数に `{...}` を書いても**補間されない**（`expect` は `&str` を受け取るためリテラル扱い）。失敗時にデバッグ情報が得られないまま「message: {ops:?}」という文字列がパニックする。

- grep: `.expect(".*{` でファイル全体を検索する
- 修正: `unwrap_or_else(|| panic!("message: {ops:?}"))` に変更する

### `tokio::spawn` の JoinHandle 捨て

`tokio::spawn(async move { ... })` の戻り値 (`JoinHandle`) を捨てると、スポーン内のパニックがテストスレッドに伝播しない。mock サーバが token 不一致・handshake 失敗で静かに死んでもテストは PASS する。

- grep: `tokio::spawn(async move` の後の行で `let _` バインドも `.await` も無いケース
- 修正: `let handle = tokio::spawn(...)` → テスト末尾で `handle.await.expect("mock server panicked")`

### production タイムアウトをテストに引き込む

`apply_after_handshake(&conn).await` は 60 秒の `VENUE_READY_TIMEOUT` を内蔵している。VenueReady が届かない（mock 設計ミス・race）と CI テストが 60 秒ハングする。

- grep: `apply_after_handshake(&` の呼び出しを確認し、テスト用短縮タイムアウト版 (`apply_after_handshake_with_timeout`) に変更されているか確認する
- 修正: `apply_after_handshake_with_timeout(&conn, Duration::from_secs(5))`

### `sleep()` ベースの op flush 待ち（race condition）

`tokio::time::sleep(Duration::from_millis(N)).await` の後に `try_recv()` ループで op を収集するパターンは、CI 負荷環境で N が足りずにテストが flaky になる。

- grep: `sleep(Duration::from_millis` と `try_recv()` が近傍にあるケース
- 修正: timeout 付き drain ループ（`timeout_at` + `channel.recv()`）または `tokio::time::pause()` + `advance()` による決定論的時間制御

### テストが自己発火する Notify / AtomicBool

テストコード自身が `notify_one()` や `store(true)` を呼んでいる場合、そのコールバックが実際にプロダクションコードから呼ばれることを検証していない。テスト名が「callback が呼ばれる」を主張していても、コールバック削除で PASS のままになる。

- 確認: コールバック/Notify を発火しているのがプロダクションコードか、テストコード自身か
- 修正: `run_with_recovery(on_ready: impl Fn() -> ...)` の引数クロージャを渡し、クロージャ内で `Notify.notify_one()` → テスト本体で `notify.notified().await`

## findings の型

- `構文エラー`: test / source が import できない、quote が閉じていない
- `実装ズレ`: plan / spec / architecture と実装が不一致
- `未実装`: 必須 task / field が抜けている
- `順序バグ`: event / invariant / recovery path が壊れる
- `silent failure`: ユーザーに見えない失敗が起きる
- `起動経路漏れ`: startup / restore / reconnect / external mode のどれかが落ちる
- `テスト不足`: positive だけで negative / bootstrap / replay / race を押さえていない

## findings の書き方

各 finding には最低限これを含める。

- `path:line`
- 何が壊れるか
- どの plan / spec / architecture / source of truth とズレているか
- なぜ回帰または本番影響になるか

例:

```text
- src/main.rs:568
  reconnect 時に EngineConnected が EngineRehello より先に流れ、
  Tachibana の reset 前に sidebar.update_handles() が走る。
  その結果、古い Ready 状態を見た metadata refetch が起動し、
  VenueReady gate をすり抜ける。
```

## メモ

- `exit 77` / `skip` / `scaffold` / `TODO(http-api)` / `placeholder` は、その場で完了扱いしない
- 新しい gate / FSM / banner / recovery path を見たら、bootstrap と reconnect を必ず追う
- 1 つの fix で複数 bootstrap 経路を救っているように見えたら、本当に両方通るか疑う
- helper や callback の再利用で隠れる race を疑う
