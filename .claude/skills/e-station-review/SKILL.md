---
name: e-station-review
description: e-station の実装レビュー用スキル。plan/spec/architecture/open questions の整合、phase/task/deferred の境界、Rust-Python 間の契約、bootstrap/reconnect/recovery の見落としや silent failure を優先して洗う。
origin: ECC
---

# e-station Review

このスキルは、コードが動くかだけではなく「計画どおりに安全に動くか」を見るためのレビュー手順です。特に phase またぎ、bootstrap、reconnect、FSM、gate、Rust-Python 境界での見落としを重点的に探します。

## 目的
- `implementation-plan.md` の task / phase / acceptance に照らして実装を確認する
- `spec.md` / `architecture.md` / `open-questions.md` と矛盾する変更を見つける
- silent failure、復旧経路、deferred 項目の取り違えを見つける
- pin test が不足している高リスク箇所を先に挙げる

## 最初に読むもの
- 対象の `implementation-plan.md`
- 関連する `spec.md`
- 関連する `architecture.md`
- 関連する `open-questions.md`

レビューはコードから始めてもよいが、source of truth を読まずに「見た目でよさそう」と判断しない。

## 基本ルール

### 1. まず phase と task の境界を固定する
- どの task をレビューしているかを明確にする
- deferred / non-goal / follow-up を区別する
- source of truth がどれかを先に固定してからコードを読む

### 2. grep で用語の揺れを拾う
最低限、次を `rg` で確認する。
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

### 3. enum / DTO / event / wire を一周する
- enum 名
- DTO 名
- event 名
- field / key
- wire 値
- error code
- phase 制約
- feature flag / capability

1 箇所だけ合っていても安心しない。出入口の全経路が揃っているかを見る。

### 4. Findings First
レビュー結果は finding 優先で出す。
1. バグ、回帰、仕様ズレ
2. gate / recovery / bootstrap の穴
3. silent failure
4. pin test 不足

## 重点観点

### 1. 仕様と wire の一致
- 用語は正しくても wire 値が違っていないか
- enum / schema / IPC の表記揺れがないか
- UI 表示名と内部キー名を混同していないか

例:
- `D1` と `"1d"`
- `display_symbol` と `display_name_en`
- `VenueError` と `EngineError`

### 2. deferred 項目の扱い
- 別 phase に送った task を今 phase に紛れ込ませていないか
- open question が未解決のまま実装を正当化していないか
- source of truth が古い文書にずれていないか

### 3. フォローアップ前提の混入
- T3 の fix が T4/T5 の前提を壊していないか
- ブロッカー解消のつもりで別 phase の責務を持ち込んでいないか
- リスクや制約が README / plan / architecture のどこか一箇所にしか書かれていない場合、それを見落としとして扱う

### 4. テストで pin すべき不変条件
- 構造的な不変条件か
- silent failure が起きうるか
- negative test が必要か

特に次は pin を疑う。
- 戻り値だけ正しくて event が欠ける
- 1 回だけ起きるべき状態遷移
- 復旧後の replay
- 普段通らない hidden path

### 5. Rust と Python の差分
- IPC DTO / event の往復で形が一致しているか
- Python 側の意味論が Rust 側の状態遷移と一致しているか
- normalizer や helper に吸収される前提が混ざっていないか

### 6. ライフサイクル全般
ここが一番壊れやすい。次を確認する。
- startup
- reconnect
- restore
- persisted state の再利用
- retry / relogin
- cancel / dismiss
- helper / background task / callback
- ready cache / sticky snapshot の invalidate 条件

## 追加チェック: イベント順序と bootstrap 競合

後発のイベントだけを追うと、前発の競合を見落としやすい。FSM や gate が正しく見えても、イベント順序や bootstrap 競合のレビューが抜けると本番で壊れる。
- `EngineConnected` / `EngineRehello` / `VenueReady` / `VenueError` / reconnect callback / replay task の発火順を追う
- gate を閉じる前に refetch / replay / resubscribe が走っていないか確認する
- startup / reconnect / restore / persisted selection / `--data-engine-url` など bootstrap 経路を全部見る
- managed mode だけで fix して external mode を落としていないか確認する
- subscription が emit した event を、UI / FSM / gate がどう消費するかまで見る
- watch / broadcast / callback / cached readiness / sticky snapshot の寿命を確認する
- `new()` / `new_with_settings()` / `update_handles()` / restore helper / reconnect replay path に同じ gate が入っているか確認する

特に次を疑う。
- reset event より先に reconnect 後 refetch が走る
- bootstrap helper だけ gate を通り本線が通らない
- managed mode の readiness cache では拾えるが external mode では拾えない

## 追加チェック: 新規 venue / enum 追加時の配線監査

新しい venue を足した review では、schema / gate / banner だけ見て安心しない。必ず end-to-end で配線を追う。
- 登録表・fanout 配列・match を総点検する。例: `VENUE_NAMES` / `AdapterHandles::set_backend` / `get_backend_arc` / `available_markets` / capability map / filter button / metadata fetch / stats fetch / kline fetch
- `VenueReady` 後に実際に叩かれる callsite から逆引きする。UI gate が正しくても backend 未登録なら本番で `No adapter handle configured` 系の失敗になる
- 「起動時の初期構築」「reconnect 後の再構築」「restore 後の replay」で同じ venue が漏れていないかを別々に確認する
- 新規 venue 追加 PR では「UI 操作 → backend call まで到達する」経路の pin 不足をまず疑う

## 追加チェック: ready cache / sticky snapshot の invalidate

readiness cache は `VenueError` でしか落としていないなら危険信号。
- `LoginStarted` / `LoginCancelled` / relogin / reconnect でも stale ready を引きずらないか確認する
- cache を読む側だけでなく、cache を更新する bridge task / callback / ProcessManager 側の寿命も追う
- managed mode と external mode で同じ invalidation が効くかを分けて確認する

## 追加チェック: ack 前の重複送信窓

非同期 command の review では、「authoritative event が返るまで state が変わらない」設計を危険信号として扱う。
- `RequestVenueLogin` / `Request*` 系で duplicate suppression を `VenueLoginStarted` 受信後にしか掛けていないなら、event 前の連打で多重送信できる
- `Task::perform(...send...)` の直前で state を見ているだけなら、event 到着前の再押下窓がないか確認する
- `Auto` と `Manual` が同じ command に収束する場合、両者が event 前に重なる race を疑う
- テストが `LoginStarted` 後の idempotency しか pin していない場合、pre-ack duplicate の negative test 不足として扱う

## findings の型
- `仕様ズレ`: plan / spec / architecture と不整合
- `未実装`: 必須 task / field が欠けている
- `順序バグ`: event / invariant / recovery path が壊れる
- `silent failure`: ユーザーに見えない失敗が起きる
- `起動経路漏れ`: startup / restore / reconnect / external mode のどれかが落ちる
- `テスト不足`: positive だけで negative / bootstrap / replay / race を押さえていない

## findings の書き方

各 finding には最低限これを含める。
- `path:line`
- 何が壊れるか
- どの仕様 / 設計 / source of truth とズレているか
- なぜ見落としやすいか

例:

```text
- src/main.rs:568
  reconnect 時に EngineConnected が EngineRehello より先に流れ、
  Tachibana の reset 前に sidebar.update_handles() が走る。
  その結果、古い Ready 状態を見た metadata refetch が発火し、
  VenueReady gate をすり抜ける。
```

## メモ
- `exit 77` / `skip` / `scaffold` / `TODO(http-api)` / `placeholder` は、その場で完了扱いしない
- 新しい gate / FSM / banner / recovery path を見たら、bootstrap と reconnect を必ず追う
- 1 つの fix で複数 bootstrap 経路を救っているように見えたら、本当に両方通るか疑う
- helper や callback の寿命で隠れる race を甘く見ない
