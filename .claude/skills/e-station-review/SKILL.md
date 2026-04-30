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
- 技術刷新系の計画では、現行機能の保持条件と「どこまで置換するか」の境界を先に固定する

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
- venue / mode / source など「出自を決める field」

1 箇所だけ見て安心しない。入口と出口の両方を見る。
特に「handler では venue を見て分岐したい」計画なら、その field が
途中の `Message` / DTO / callback で捨てられていないか確認する。

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
- command / event / DTO の削除や rename は `schema_minor` ではなく protocol 破壊の可能性をまず疑う
- 「optional field 追加」なのか「既存 variant / event 廃止」なのかを分けて判断し、後者は handshake 互換性を別扱いにする

### 5.1 計画レビューでも現行実装を必ず照合する

plan / spec / architecture のレビューでも、文書同士の整合だけで止めない。既存コードの責務配置と lifecycle を見ずに「自然そうな設計変更」を通すと、実装主体の勘違い、既存 invariant 破壊、移行不能な protocol 変更を見落としやすい。

- 対象 plan が置き換えようとしている現行 entry point を実コードで確認する
- 擬似コードの配置先が本当に現実の owner かを見る
- 既存 helper / latch / cache / counter / restore path の invariant を 1 回拾ってから plan を読む
- 既存の catch-up path、後から pane を追加した経路、初回表示時の補完経路を確認する
- 「新設ファイルで吸収できそう」に見えても、実際の dispatch / state / outbox / worker 注入点が別にないか確認する

特に次は findings 候補として先に疑う。

- plan の責務配置が現コードの `server.py` / worker / manager とずれている
- freshness 判定だけで startup validate を置き換えている
- persisted state 追加が既存 counter / replay / cache invariant を壊す
- migration 手順なしに wire variant を削除している
- `VenueReady` 時の自動処理だけ直して、`pane added later` の catch-up を放置している
- 現行コードに live / replay 分離ガードがあるのに、計画がそれを跨いで副作用を増やしている

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

## 追加チェック: イベント出自と live / replay 分離

イベント名だけ一致していても、そのイベントがどの venue / mode / source から来たものかを
途中で落とすと、plan 上は自然でも実装時に live と replay を混線させやすい。

- `OrderAccepted` / `OrderListUpdated` / `BuyingPowerUpdated` などで `venue` や mode を途中の message で捨てていないか確認する
- handler 側の分岐条件に必要な field が bridge / DTO / enum 変換で保持されているか確認する
- live 専用副作用を追加する plan なら、replay 起因の同名 event にもその副作用が走らないか確認する
- 「既存の配布側が replay pane を除外しているから安全」と短絡せず、送信側の IPC / fetch 自体が不要発火しないか確認する

イベント順序と bootstrap 競合の見落とし例:

- `EngineEvent` には `venue` があるのに `Message` へ写す段階で捨て、後段で live 限定ガードが書けない
- replay の `OrderAccepted` でも live venue 向け `GetBuyingPower` を送ってしまう
- non-replay pane への fanout ガードはあるが、fetch 副作用自体は発火してしまう

## 追加チェック: 既存手動導線と自動導線の parity

「手動更新を自動化する」計画では、既存の manual path を 1 本見るだけでは不足しやすい。
起動時、自動 catch-up、後から開いた pane、reconnect 後再表示のそれぞれで同じ責務が
どこに置かれているか確認する。

- refresh button の dispatch 先だけでなく、起動時 auto-fetch、pane 追加時 catch-up、reconnect 後 replay を確認する
- acceptance が「起動時に更新される」だけで、ログイン後に pane を開いたケースを落としていないか確認する
- buying power など既存に別経路実装がある場合、「同様に」と書かれた plan がその経路も含んでいるか確認する
- 「2 箇所だけ変更」と書かれていたら、本当に sidecar 経路が存在しないかコードで確かめる

見落とし例:

- `VenueReady` の auto-fetch だけ追加して、後から追加した `OrderList` pane では空のままになる
- buying power には pane-added catch-up があるのに、order list だけ startup path しか無い

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

## 計画文書レビュー観点（plan / spec / architecture / open-questions）

コードレビューではなく計画文書を対象とする場合、次の4観点で確認する。

| 観点 | 内容 |
|---|---|
| **A 文書間整合性** | 計画群（README/spec/architecture/implementation-plan/open-questions）+ 上位 SKILL の間で矛盾・旧表記・章節リンク死活・用語統一・enum 表記揺れ・フェーズ番号混在 |
| **B 既存実装・依存計画とのズレ** | コードベース実装済み（Phase 前段）と移植元（他リポジトリ）の関数名・型・呼出規約・フィールド構成・Debug マスク方針が計画記述と食い違っていないか。依存計画（他フェーズ）との引き取り境界 |
| **C 仕様漏れ・設計リスク** | 一次資料 SKILL（R1〜RN / API 規約 / セキュリティ規約 / エンコード規約 / 採番規約）から見て計画文書で未対処・曖昧の箇所。互換不変条件（API 境界の漏出禁止語）違反 |
| **D テスト不足** | 計画に書かれた実装タスクに対して、受け入れ条件・単体・結合・E2E・回帰・lint・CI ゲート組込の観測点（実行コマンド・テストファイル名・assert 内容）が明記されていない箇所 |

### 観点 A — 追加チェック

- 章節アンカーの死活：`Grep` で md 内の `[...](./other.md#anchor)` を抜き出し、参照先見出しが実在するか
- フェーズ番号の混在：複数計画間で同じ "Phase 1" が異なる意味で使われていないか
- 旧用語の残存：前ラウンドで廃止した用語の grep カウント

### 観点 B — 追加チェック

- 計画記述で関数名・モジュール名が実在のものと一致するか（`Grep` で対応コードを引いて確認）
- **行番号参照は陳腐化する**ので Finding として「シンボル名参照に置換」を推奨
- 移植元リポジトリ（他プロジェクト）の現状とのズレ（path で照合）

### 観点 C — 追加チェック

- SKILL の R1〜RN を網羅できているか（規約 ID をチェックリスト化）
- 互換不変条件（境界に漏出してはならない用語の集合）が機械検証できるよう lint タスク化されているか
- config キー名が docs に明示されているか（実装で骨抜きになる最大の温床）
- UI / frontend 再構成計画では、既存機能の parity 条件が「表示できる」ではなく「主要操作・設定変更・購読 lifecycle・overlay / indicator / sync-all 等の振る舞いまで維持」に上がっているか
- 固定 footer / header / status bar / toolbar を追加する plan では、既存 modal / toast / overlay / bottom sheet の配置基準がその新要素込みでずれないか確認する

### 観点 D — 追加チェック

- 各テストの観測点（pytest/cargo test ファイル名・実行コマンド）が明記されているか
- CI ゲート組込（`.github/workflows/`）が明記されているか — テスト存在と CI 組込は別物
- 不変条件 ID と test 関数名の対応表（`invariant-tests.md` 等）が起票されているか
- 技術置換系 plan では「placeholder 表示」だけで acceptance を通していないか。現行機能 parity を測る観測点が pane 種別ごとに書かれているか

### 観点 E — 技術置換の境界と責務

特定技術への置換計画（例: `iced` → `Bevy`、独自実装 → 外部基盤）では、「置換する理由がある面」と「既存のままでよい面」を分けて確認する。ここを曖昧にすると、必要以上の全面移植と、逆に置換すべき高頻度描画・入力面の見落としが同時に起きる。

- 新技術が本当に必要な責務を列挙する
- その技術でなくてよい責務まで計画が飲み込んでいないか確認する
- 逆に、その技術で先に持つべき高頻度描画 / hit test / input capture / z-order / camera などが後回しになっていないか確認する
- 現行 UI / modal / 設定画面 / 認証導線まで巻き込む場合、巻き込む理由と段階導入条件が書かれているか確認する
- open question のまま責務境界を曖昧にして、phase 後半で再分解が必要になる構成になっていないか確認する

特に findings 候補として先に疑う。

- layout 問題の解決を名目に、設定 UI や管理 UI まで新技術へ全面移植する前提になっている
- placeholder の接続計画はあるが、既存 chart / shader / overlay / indicator の保持戦略が無い
- 「新 frontend が描画担当」とだけ書かれ、既存 widget / scene / renderer を host するのか native 移植するのかが未確定
- 入力境界が曖昧で、タイトルバー操作・pane 内 UI・chart pointer capture の責務分離が書かれていない

### 観点 F — 現行機能 parity の棚卸し

大規模 UI 改修の計画レビューでは、文書どうしの整合だけでは足りない。置換対象の現行機能を棚卸しし、計画側に parity 条件として現れているか確認する。特に chart 系 pane は「表示」より「操作」「設定」「イベント注入」「close 時 teardown」が壊れやすい。

- 置換対象 pane / widget の現行機能を実コードから最低 1 回拾う
- overlay、indicator、study configurator、sync-all、stream 切替、close 時 teardown などの副機能を落としていないか確認する
- main から pane へ注入される event fanout が、新構成でも届く前提になっているか確認する
- 機能保持の acceptance が pane 種別ごとに書かれているか確認する
- lifecycle 契約（購読 cancel、aggregator drop、registry unregister、focus 移譲など）が plan に明文化されているか確認する

### 観点 G — 固定レイアウト追加時の基準面と重なり順

footer / status bar / header のような固定レイアウト追加は見た目の差分が小さくても、既存 overlay の基準面を静かに壊しやすい。計画レビューでも「要素を足せるか」ではなく、「既存 modal / toast / badge / drawer がどの bounds を基準に置かれているか」を先に見る。

- `base.push(...)` の追加先が、そのまま modal / overlay helper に渡される経路か確認する
- bottom-aligned / top-aligned overlay が新設固定要素ぶん押し出されるか、逆に重なるかを確認する
- `view_with_modal` / `dashboard_modal` / toast manager のような既存 helper が content 全体 bounds を使うのか、inner layout bounds を使うのか確認する
- 「overlay が全画面を覆うから大丈夫」と文書で仮定せず、現行実装の opacity・padding・anchor を見て事実確認する
- popout 非表示や main window 限定の条件だけで安心せず、main 側の overlay 配置が回帰しないかを別観点で確認する

特に findings 候補として先に疑う。

- 固定 footer を `base` に追加した結果、既存の下寄せ modal が footer に重なる
- status bar 追加後に toast の表示開始位置が footer の内側へ食い込む
- main window 専用要素の追加で popout は無事でも、main の overlay helper が別 bounds を取って見た目だけ崩れる
- plan が `Length::Fill` や `padding` の調整だけを述べ、既存 overlay の再配置条件を書いていない

pin 不足を疑う例:

- 「既存コンテンツが表示できる」で acceptance が止まっている
- chart 設定 modal や indicator 並べ替えのような副機能に観測点が無い
- pane close の teardown 順序が plan では未定義
- main thread からの marker / signal / stream 更新の配信経路が新設計で不明

### 計画文書レビューの禁止事項

- LOW の Finding を理由にループ継続してはいけない（LOW は対応不要）
- Finding を「修正済み」とマークする前に、対象ファイルを `Read` で確認するか `Grep` で検証すること（サブエージェントの自己申告だけを信用しない）
- 計画文書に存在しない新機能・新フェーズ・新スコープを追加してはいけない
- SKILL の一次資料を計画文書側の記述で上書きしてはいけない（SKILL が正）

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

## 追加チェック: コード・テスト設計パターン

### `From<inner>` を残した newtype

newtype 導入後に `impl From<InnerType> for NewType` を残しておくと、型による不変条件が骨抜きになる。

- grep: `impl From<` を検索し、newtype か否かを確認する
- 修正: newtype 導入時は `From` 実装を撤廃し、コンストラクタ or `TryFrom` に限定する

### `#[doc(hidden)] pub` は production 漏出

`#[doc(hidden)] pub fn test_only_helper()` は本番バイナリにも含まれる。テスト専用 API が production コードから呼べる状態になる。

- grep: `#\[doc(hidden)\]` の直後に `pub` があるケース
- 修正: `[features] testing = []` feature flag で gate する

### 正規表現ソース検査の false negative

正規表現でソースコードを検査するテストは、tuple unpack / walrus operator の書き方によって false negative を生じやすい。

- 確認: `re.search` / `re.findall` ベースの検査は、多行構文・代入式で見落としが起きないか
- 修正: AST ベース検査（`ast.parse` + `ast.walk`）に昇華する

### test sentinel と `.env` 値の衝突

テスト用の sentinel 値（例: `"test-token"`, `"dummy"`）が実環境 `.env` ファイルの realistic value と衝突すると、テストが本番設定で誤 PASS する。

- 確認: テスト用固定値が `TEST_SENTINEL_*` 形式になっているか
- 修正: `TEST_SENTINEL_TOKEN=xxx` のように接頭辞で分離し、realistic value との衝突を防ぐ

## メモ

- `exit 77` / `skip` / `scaffold` / `TODO(http-api)` / `placeholder` は、その場で完了扱いしない
- 新しい gate / FSM / banner / recovery path を見たら、bootstrap と reconnect を必ず追う
- 1 つの fix で複数 bootstrap 経路を救っているように見えたら、本当に両方通るか疑う
- helper や callback の再利用で隠れる race を疑う
