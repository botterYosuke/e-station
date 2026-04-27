---
name: review-fix-loop
description: 並列の専門サブエージェントで多角レビュー → 修正エージェントで TDD 修正 → 再レビュー、を MEDIUM 以上の指摘がゼロになるまで繰り返すオーケストレーション手法。新フェーズ完了後・大規模 PR 着地前に使う。
origin: ECC (e-station 向けカスタム)
---

# Review-Fix Loop — オーケストレーター主導の品質収束ループ

新フェーズや大規模 PR の実装が完了したあと、このスキルを起動する。

```
/review-fix-loop
```

オーケストレーター（あなた）が レビュー段階 → 集約 → 修正段階 → 再レビュー を **MEDIUM 以上の指摘がゼロになるまで** 繰り返す。

---

## なぜこの手法か

- **単一視点の盲点を消す**: rust 所有権・iced 逸脱・型設計・サイレント障害・IPC 整合・Python 品質は、それぞれ専門観点が異なる。1 エージェントの順次走査ではいずれかが薄くなる
- **並列で速い**: 6 並列で走らせれば、1 視点 5 分のレビューが約 5 分で終わる
- **収束基準が明確**: 「MEDIUM 以上ゼロ」は主観的判断に依らない停止条件
- **計画書が成長する**: 各ラウンドのレビュー反映ブロックが、次の作業者への引継ぎ情報として蓄積される

---

## 不可侵ルール

- **secrets を log/test/comment/commit に含めない**
- **TDD 厳守**: 修正は `.claude/skills/tdd-workflow/SKILL.md` に従い RED → GREEN → REFACTOR
- **既存テストを壊さない**
- **完了時の検証**: プロジェクトの最終コマンド全件緑（e-station なら `cargo check --workspace` / `cargo clippy --workspace -- -D warnings` / `cargo fmt --check` / `cargo test --workspace`（デフォルト並列）/ `uv run pytest <対象>`）
- **prompt は self-contained**: サブエージェントは前会話を見ない。必読ドキュメントの相対パスを毎回明記する

---

## 収束の期待値（R6-R9 実測ベース）

中規模フェーズ（30 ファイル前後の Rust + Python）の典型的収束カーブ:

| ラウンド | CRITICAL+HIGH+MEDIUM 件数 | 説明 |
|---|---|---|
| R1 (初回) | 25–30 | 設計層・サイレント・型・IPC が重複指摘される |
| R2 | 10–15 | 初回 fix 後、新規導入された軽微な問題が中心 |
| R3 | 5–7 | 残存 MEDIUM、コメント整合・テスト品質 |
| R4 (収束) | 0 | サニティチェックのみ |

**1 ラウンドで収束することはほぼない。3-4 ラウンドを見積もる。** 件数が半減せず横ばいなら指示が曖昧で fix が浅い兆候。

---

## ループ手順

### Phase 0 — 前提読込 + 状態確認

レビュー対象の計画書・規約・既知の見逃しパターンを **必ず先に読む**:
- 該当フェーズの計画書（例: `docs/plan/<feature>/implementation-plan.md`）
- アーキテクチャ／仕様書／open-questions
- `.claude/skills/bug-postmortem/MISSES.md`
- `CLAUDE.md`

**さらに**: 現状の build/test 状態を実コマンドで確認。レビュアーが「全緑」と主張しても自分で `cargo fmt --check` などを叩いて裏を取ること（R6 で reviewer の「fmt 緑」主張を信じて CRITICAL を見落としかけた）。

### Phase 1 — レビュー段階（並列）

以下のサブエージェントを **同一メッセージ内で並列起動**（独立タスクは並列が原則）:

| エージェント | 観点 |
|---|---|
| `rust-reviewer` | 所有権・ライフタイム・unsafe・エラー処理 |
| `silent-failure-hunter` | 握り潰しエラー・creds 漏洩・ログ不足 |
| `iced-architecture-reviewer` | Elm アーキテクチャ逸脱（GUI 変更時のみ） |
| `type-design-analyzer` | Newtype・状態機械・enum 不変条件 |
| `ws-compatibility-auditor` | IPC スキーマ・圧縮設定・schema bump |
| `general-purpose` | Python コード品質 + 計画書クロスチェック |

各エージェントへの指示テンプレ（self-contained 必須）:

> `docs/plan/<feature>/` 配下のドキュメントを必ず参照し、実装が計画と整合しているか・MISSES.md の既知パターンに該当しないかを検証せよ。指摘は **CRITICAL / HIGH / MEDIUM / LOW** で分類し、`path:line`、根拠（計画書のどの条項に違反か）、推奨修正、回帰防止テストの提案を含めよ。**既知繰越（H5/H6/...）は再指摘不要だが、その繰越扱いの実装が本当に正しいかは検証せよ。** 末尾に重要度別件数サマリ。500 行以内。

GUI を含まないバックエンド変更なら `iced-architecture-reviewer` を省略してよい。Rust が無ければ `rust-reviewer` も省略。**スコープに合わせてエージェントを選ぶ**。

### Phase 2 — 集約

全エージェントの指摘をマージし、重複統合 → 重要度順に並べた一覧を作成。CRITICAL / HIGH / MEDIUM の件数を要約。

**集約時の注意**:
- 同じ問題が複数エージェントから別 ID で報告されることが多い（例: `set_second_password_for_test` を type-designer が HIGH、rust-reviewer が MEDIUM 評価）。**高い方の重要度を採用**
- レビュアー間で重要度判断が割れた場合は、production リスクが高い方を採用
- 「Phase O1 繰越」と書かれた既知項目は再指摘されたら無視可。ただし「ラウンド N で完遂」と主張された項目が**実は実装と乖離**しているケースを毎回チェック（R7-R8 で頻出）

### Phase 3 — 修正段階

**MEDIUM 以上が 1 件でもあれば** `general-purpose` エージェントに修正依頼。

> **`implementer` サブエージェントは単一 RED→GREEN サイクル制約があり、大きな batch を拒否する。** 多項目を一括で進めたいときは `general-purpose` に「TDD 順序で順次着手せよ」と明示する。1 項目ずつ厳密に進めたい場合は test-writer → implementer のペアを項目ごとに回す。

修正エージェントへの指示には必ず以下を含める:

- 該当ファイル・行・指摘内容（オーケストレーター側で要約）
- 不可侵ルール一式
- TDD 順守と各項目の RED → GREEN → REFACTOR 順序
- **uv 環境利用の明記**（Python 関連は `uv run pytest`、`uv run python -m engine`、`uv add` 必須。素の `python` 禁止）
- **「Phase O1 / 繰越に勝手に降格しない」**: ユーザーが (b) 全件指示を出している場合、エージェントは「影響範囲が大きい」「DTO restructuring が必要」等の理由で勝手に Phase O1 へ降格する傾向がある。**「降格判断はユーザー権限。実施できないと判断したら DEFER ではなく STOP+REPORT して指示を仰げ」と明示**
- **「対象ファイル外を変更しない」**: subagent が無関係な docs/plan/* を編集することがある。「修正対象として列挙したファイル + 計画書反映ブロック以外は触らない」と明示
- 修正後の最終コマンド緑確認（cargo fmt --check も含む）
- **計画書の該当フェーズ末尾に「レビュー反映 (YYYY-MM-DD, ラウンド N)」ブロックを追記**

修正項目は依存関係順にグループ化する（例: docs only → 単独ファイル → cross-module → テスト品質）。**型シグネチャ変更や module 構造変更は最初に実施**（後続項目への影響を吸収しやすい）。

### Phase 4 — 再レビュー

修正後にレビュー段階を再実行。**ただし全 6 エージェントを毎回回す必要はない**:

- ラウンド 2 以降は **変更があった層のレビュアーのみ**（例: Python だけ変えたなら silent-failure-hunter + general-purpose、Rust の signature 変更なら rust-reviewer のみ）
- 変更していない層を再走させても新規発見は少なく、コンテキスト浪費になる
- **silent-failure-hunter は毎回必ず回す**: 「fix が新たな silent failure を導入する」パターンが頻出（例: R7 で `restore_failed=True` 時の VenueReady フィルタが、Rust 側 subscribe 残存という新たな silent failure を生んだ。R8 で発見）

### Phase 5 — ループ終了条件

- **MEDIUM 以上ゼロ** で終了
- LOW のみ残った場合は LOW 一覧を提示して終了
- HIGH 以上が「次イテレーション持ち越し」と判断される場合は、計画書の「繰越 / 次イテレーション」ブロックに明示記載した上で終了（理由・期限・代替策を必ず添える）。**降格はユーザーの明示承認が要る**

---

## 進捗共有（毎ラウンド）

計画書の該当フェーズ末尾に **「レビュー反映 (YYYY-MM-DD, ラウンド N)」** ブロックを追記:

- 完了項目に ✅
- 設計判断・新たな知見・Tips を他作業者が再現できる粒度で
- 既存の他フェーズ（例: T2）のスタイルを踏襲

書く内容:
1. 解消した指摘 (id + 1 行サマリ)
2. 修正中に発覚した設計判断（plan を更新する根拠）
3. 新たな見逃しパターン候補（次回 MISSES.md 追記候補）
4. 持ち越し項目とその理由

**サイズ管理**: 各ラウンド反映ブロックが肥大化する。「ラウンド N で解消」と書いた項目は、次ラウンド以降では繰り返し書かない。サマリと差分のみ記録する。

---

## オーケストレーター運用 Tips

### 並列起動

> 独立タスクは **同一メッセージ内で複数 Agent 呼出**。「6 件並列」＝ 1 メッセージで 6 ツール呼出。順次起動するとコストも時間も無駄。

### バックグラウンド実行

レビューエージェントは長時間（数十秒〜数分）かかるため `run_in_background: true` で投入し、完了通知を待つ。Sleep ループは禁止。

### 修正範囲の判断

| 発見 | 対応 |
|---|---|
| CRITICAL | 必ず即修正 |
| HIGH（コード変更） | 同 PR で修正 |
| HIGH（大規模リファクタ・別 PR スコープ） | **ユーザーに承認を取る**。承認後に計画書「繰越」に明示してパス |
| MEDIUM | 同 PR で修正（このスキルの停止条件） |
| LOW | 列挙のみ。次フェーズで拾うかどうかを user に判断してもらう |

### `implementer` vs `general-purpose`

- `implementer`: **1 項目厳密 TDD**。RED テストの handoff が必須。多項目を投げると拒否される
- `general-purpose`: **多項目 batch + TDD 順守可**。プロンプトで「各項目で RED→GREEN→REFACTOR」と明示する
- 1 項目を完璧にやりたい時は test-writer → implementer のペア。多項目を効率重視で進めたい時は general-purpose

### コミット時の選択的ステージング

修正エージェントが `cargo fmt --all` を実行すると、**フェーズと無関係なファイルにもフォーマット差分が出る**。さらに、エージェントが裁量で別フェーズの計画書（例: 隣接する `docs/plan/✅order/*`）を更新することもある。

コミット時は `git add -A` を避け、フェーズに関連するファイルを **明示列挙**してステージング。CLAUDE.md の規約と整合する。判断基準:

- ✅ ステージ: 修正対象として明示したファイル、新設テスト、cargo fmt が触った同フェーズ範囲のソース、計画書の対象フェーズ
- ❌ ステージしない: 別フェーズの docs/plan/、untracked な作業中ファイル、別エージェントが副次生成した artifact

---

## R6-R9 で得た新たな知見

### 1. サブエージェントの「勝手に Phase O1 繰越」癖

ユーザーが「(b) 全件修正」と明示しても、修正エージェントは「DTO restructuring が必要」「影響範囲が大きい」等の理由で **9 件を独断で Phase O1 へ降格** することがあった（R6）。**プロンプトに「降格判断はユーザー権限。困ったら STOP+REPORT」を明記**するまでこの癖は再発する。

### 2. fix 自体が silent failure を生む

修正は新たな silent failure を生む。例:

- HIGH-1 fix: Python 側で `restore_failed=True` 時に `VenueReady` を emit から除外 → Rust 側の `apply_after_handshake` で当該 venue が `failed_venues` 登録されない経路ができ、後続 Subscribe が送出される silent breakage（R8 で発見）
- HIGH-7 fix: `try/finally` で credential scrub → 対称性ガードがないため `_do_request_venue_login` 側に同種コードが追加されたら漏れる（R7 で発見）

**silent-failure-hunter は毎ラウンド必ず回す。** rust-reviewer や type-designer の専門レビュアーは見つけられない。

### 3. `#[doc(hidden)] pub` ≠ `#[cfg(test)]`

test-only API を `#[doc(hidden)] pub fn ...` にしても **production バイナリに symbol が残る**。外部クレートから呼べる。Rust の `cargo test` 由来の integration test (`tests/`) は外部クレート扱いなので `#[cfg(test)]` だと呼べない。**正解は `#[features] testing = []` + self dev-dep で feature-gate**。

### 4. Newtype を作ったら `From` 実装を慎重に削る

`TachibanaUserId(String)` を作っても `From<String>` / `From<&str>` を残すと、`password.expose_secret().clone().into()` 一発で newtype に化けてしまい newtype の意図（誤代入のコンパイル検知）が無効化される。**newtype 導入時は `From<inner>` を削除し、`new(impl Into<inner>)` 一本化**。

### 5. リスナー / spawn の JoinHandle 捨て

`tokio::spawn(async move { ... })` で `JoinHandle` を捨てると、再起動時に新旧 listener が同一 broadcast channel を購読する窓ができる。冪等な処理なら実害なしだが、hook が副作用持ち（カウンタ・通知）になった瞬間に二重実行 silent bug が出る。**spawn handle は `Mutex<Option<JoinHandle>>` で保持し、再 spawn 前に `abort().await`**。

### 6. 「削除した」とコメントしたのに impl が残る

R8 で発見: `// dropped: callers use into_string()` というコメント直下に `impl From<TachibanaUserId> for String` が残っていた。**コメントと実装の乖離は最終レビューで毎回チェックする**。grep `"dropped:" "removed:" "deleted:"` 等のキーワードで該当箇所を機械抽出。

### 7. 正規表現ベースのソース検査は脆い → AST へ

「`fallback_*` 変数が出現したら `finally:` も必須」を `re.search(r"^\s*fallback_\w+\s*=", source, re.MULTILINE)` で pin しても、tuple unpacking `(fallback_a, fallback_b) = (...)` や walrus `(fallback_a := ...)` で false negative になる。**ソースコード解析テストは AST ベースに昇華**。`ast.parse` + visitor で `Assign` / `AnnAssign` / `NamedExpr` を網羅。

### 8. テスト sentinel と `.env` の値衝突

R6 まで `.env` の dev creds (`uxf05882`) と `test_tachibana_startup_supervisor.py` の漏洩検知 sentinel が **同一文字列**だった。テスト的には sentinel がユニークなので OK と扱われていたが、`.env` を変更すると検知が無効化される脆さ。**test sentinel は `TEST_SENTINEL_USER_<uuid8>` 形式で realistic value とは交わらないドメインに置く**。

### 9. `.env.sample` と `.env.example` の二重存在

`.env.sample` と `.env.example` の両方が git tracked になっている状態は dev のオンボーディングを壊す。**プロジェクト規約として `.env.example` 一本に統一**し、もう一方は削除。

### 10. `--token` CLI 引数 = secrets leakage

`argparse` で `--token VALUE` を受けると、`ps -ef` / Windows タスクマネージャの commandline 列に値が残る。**stdin 経路に統一し、CLI flag は `argparse.SUPPRESS` で隠して deprecation warning**。

### 11. cargo fmt の workspace 一括は無関係ファイルを汚す

`cargo fmt --all` は workspace 全体に走るため、フェーズと関係ない `exchange/` や `src/screen/dashboard/tickers_table.rs` まで diff が出る。コミット時に「これは fmt 由来か機能変更由来か」を `git diff --stat` で先に確認、無関係 fmt は同 PR に含めるか別 PR に分けるかを判断。

---

## 失敗パターン（避けること）

1. **MEDIUM を無視して LOW だけ残った状態で「完了」にする** — ループ条件違反。MEDIUM ゼロまで繰り返す
2. **修正後の再レビューをスキップ** — 修正で新規 MEDIUM が混入していないか必ず確認する
3. **6 エージェントを順次起動** — 並列が原則
4. **修正エージェントを `implementer` で多項目投げる** — 拒否されて時間ロス。`general-purpose` に切り替える
5. **計画書追記を最後にまとめる** — ラウンドごとに追記しないと、次のレビュアーが「何が解消済みか」を判断できない
6. **secrets を含むテスト fixture を使う** — `password = "p"` のような短い値は偶然マッチでガード失敗を招く。ユニーク化必須
7. **「Phase O1 繰越」を subagent の判断で実行させる** — 降格はユーザー権限。プロンプトで明示禁止
8. **fix 後に silent-failure-hunter を回さない** — fix 由来の新規 silent failure を見落とす
9. **subagent の「全緑」主張を鵜呑み** — 自分で `cargo fmt --check` 等を叩いて裏を取る
10. **コミット時に `git add -A` を使う** — 別フェーズの作業や untracked artifact が混入。明示列挙する

---

## 適用例

### 立花 T3 フェーズ R6-R9（実測）

バックエンド配線 + 型封印 + Wire DTO 移動、Rust + Python、~40 ファイル変更:

| ラウンド | 投入レビュアー | CRITICAL | HIGH | MEDIUM | 修正後の検証 |
|---|---|---|---|---|---|
| R6 初回 | 6 並列 | 3 | 8 | 16 | 4cmd 緑 / pytest 108 |
| R7 再レビュー | 4 並列（iced/ws 省略） | 0 | 5 | 10 | 4cmd 緑 / pytest 111 |
| R8 再レビュー | 2 並列（rust + silent） | 0 | 0 | 5 | 4cmd 緑 / pytest 112 |
| R9 サニティ | 1 体（rust-reviewer） | 0 | 0 | 0 | **収束** |

総所要: レビュー 13 並列起動 + 修正 3 ラウンド。新規統合テスト 5 件追加。Phase 2/O1 繰越 2 件のみ明示。

**学んだこと**: 「(b) 全件指示」でも subagent は独断繰越する → R6 で 9 件取りこぼし → R6.5 として強制修正バッチを別途投入。**初回プロンプトに「降格禁止」明記で R7 以降は再発なし**。

---

## ループ自体のメンテナンス

このスキル自体も品質収束する。新フェーズで適用した後、新しい知見が出たら本ファイルの「R6-R9 で得た新たな知見」セクションに追記する。蓄積されたパターンは後続の review-fix-loop 起動者の起動コストを下げる。
