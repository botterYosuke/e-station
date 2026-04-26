---
name: e-station-review
description: 実装計画書・仕様書・設計書・未解決事項を起点に、実装差分を仕様適合・回帰・テスト不足の観点でレビューするためのスキル。特に phase / task / invariant / source of truth の取り違え、Rust-Python 境界のずれ、イベント順序や bootstrap 経路の見落としを防ぐ。
origin: ECC (e-station 向けカスタム)
---

# e-station Review

このスキルは、実装コードをレビューするときに「コードがきれいか」よりも先に、
その変更が計画・仕様・設計・既知の制約に合っているかを確認するためのもの。

## 目的

- 実装が `implementation-plan.md` の該当 task / phase / acceptance に沿っているか確認する
- `spec.md` / `architecture.md` / `open-questions.md` と矛盾する実装を見つける
- silent failure、回帰、gate すり抜け、別経路だけ壊れる実装を見つける
- pin test が足りない変更を見つける

## 最初に読むもの

最低限、以下は最初に読む。

- 対象の `implementation-plan.md`
- 関連する `spec.md`
- 関連する `architecture.md`
- 関連する `open-questions.md`

レビューはコードを読んで終わりではなく、必ず文書の source of truth と照合する。

## 基本ルール

### 1. 実装より先に計画を読む

- どの task を実装した変更かを特定する
- 完了条件、deferred 項目、non-goal を確認する
- source of truth がどこかを決めてからコードを読む

### 2. 用語の揺れを grep する

レビュー開始時に、以下のような語を `rg` で拾って文脈を揃える。

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

### 3. 名前・型・wire 形状の一致を見る

- enum 名
- DTO 名
- event 名
- field / key 名
- wire 値
- error code
- phase 固有の feature flag / capability

1 箇所だけ直っていて、他の出現箇所が古いままのケースを疑う。

### 4. Findings First

レビュー結果は finding を先に出す。

1. 仕様・計画との不整合
2. gate / recovery / bootstrap の回帰
3. 別経路だけ壊れる実装
4. テスト不足や pin 漏れ

## 重点観点

### 1. 仕様語と wire 形状の一致

- 用語だけ正しくて wire 値が違っていないか
- enum / schema / IPC の文字列が文書と一致しているか
- UI 表示名と内部キーを取り違えていないか

例:

- `D1` と `"1d"`
- `display_symbol` と `display_name_en`
- `VenueError` と `EngineError`

### 2. deferred 項目の混入

- 先の phase に送った task を今の phase に混ぜていないか
- open question が未解決のまま実装を確定扱いしていないか
- source of truth が古い文書にずれていないか

### 3. フェーズ跨ぎの整合

- T3 の fix が T4/T5 の記述と矛盾していないか
- ブロッカー解消のつもりで別 phase の前提を壊していないか
- リスクや制約が README / plan / architecture の一部にしか書かれていない場合、それを見落としていないか

### 4. テストで pin すべき不変条件

- 退行しやすい構造か
- silent failure が起こるか
- negative test が必要か
- pin test があるべき箇所か

特に次は pin を疑う。

- 取りこぼしやすい event
- 1 回だけ起きるべき状態遷移
- 復旧後の replay
- 初期化時の hidden path

### 5. Rust と Python の境界

Rust と Python の両側にまたがる変更では、片側だけ読んで安心しない。

- IPC DTO / event の双方で形が一致しているか
- Python 側の意味論が Rust 側の想定と一致しているか
- 変換関数や normalizer に暗黙の仕様が埋まっていないか

### 6. ライフサイクル全体

「通常操作」だけではなく、次も確認する。

- startup
- reconnect
- restore
- persisted state の再適用
- retry / relogin
- cancel / dismiss
- helper / background task / callback

## 追加チェック: イベント順序と bootstrap 経路

今回の見落としを踏まえ、今後はここを固定観点にする。
FSM や gate が正しく見えても、イベント順序と別 bootstrap 経路の確認が抜けると実運用で壊れる。

- `EngineConnected` / `EngineRehello` / `VenueReady` / `VenueError` / reconnect callback / replay task の発火順を追う
- gate を閉じる前に refetch / replay / resubscribe が走っていないか確認する
- startup / reconnect / restore / persisted selection / `--data-engine-url` など bootstrap 経路を列挙する
- managed mode だけで fix が成立していないか確認する
- subscription 開始前に emit されたイベントを取りこぼしたとき、UI / FSM / gate がどう壊れるか確認する
- watch / broadcast / callback / cached readiness / sticky snapshot のどれに依存しているか整理する
- `new()` / `new_with_settings()` / `update_handles()` / restore helper / reconnect replay path が gate を迂回していないか個別に確認する
- レビューコメントでは「FSM は正しい」で終わらせず、イベント順序・初期値・購読開始タイミングを別々に書く

特に次を強く疑う。

- reset event より先に reconnect 後 refetch が走る
- bootstrap helper だけ gate を通らない
- managed mode の readiness cache では救えるが external mode では救えない

## findings の型

- `仕様不整合`: plan / spec / architecture と矛盾
- `未実装`: 必須 task や field が抜けている
- `回帰`: 既存 invariant や recovery path を壊している
- `silent failure`: ユーザーに見えない失敗が起きる
- `別経路破綻`: startup / restore / reconnect / external mode のどれかだけ壊れる
- `テスト不足`: positive case しかなく、negative / bootstrap / replay / race を押さえていない

## findings の書き方

各 finding には最低限これを含める。

- `path:line`
- 何が問題か
- どの仕様・計画・設計とズレているか
- なぜ実害があるか

例:

```text
- src/main.rs:568
  reconnect 時に EngineConnected を EngineRehello より先に流しており、
  Tachibana の reset 前に sidebar.update_handles() が走る。
  その結果、前回 Ready 状態を引きずったまま metadata refetch が発火し、
  VenueReady gate をすり抜ける。
```

## 実務メモ

- 文書、実装、テスト、リスク記述が全部そろって初めて「レビューできた」とみなす
- `exit 77` / `skip` / `scaffold` / `TODO(http-api)` / `placeholder` は、存在した時点で受け入れ済みとみなさない
- 新しい gate / FSM / banner / recovery path を見たら、通常操作だけでなく bootstrap と reconnect を必ず確認する
- 1 つの fix で複数経路を救っているように見える時ほど、別モードや別 helper の取りこぼしを疑う
