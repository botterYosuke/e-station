# ImplementationLoop — ソースコードレビュー・修正ループ

ソースコード（Rust / Python 実装ファイル）のレビュー・更新に使う。
共通ルール・収束基準は [`SKILL.md`](./SKILL.md) を参照。

---

あなたはオーケストレーターです。
実装ファイル群（Rust / Python）に対して「レビュー → 集約 → 修正 → 検証」を 1 ラウンドとし、MEDIUM 以上の Finding がゼロになるまで反復させます。

## 収束の期待値（実測ベース）

中規模フェーズ（30 ファイル前後の Rust + Python）の典型的収束カーブ:

| ラウンド | CRITICAL+HIGH+MEDIUM 件数 | 説明 |
|---|---|---|
| R1（初回） | 25–30 | 設計層・サイレント・型・IPC が重複指摘される |
| R2 | 10–15 | 初回 fix 後、新規導入された軽微な問題が中心 |
| R3 | 5–7 | 残存 MEDIUM、コメント整合・テスト品質 |
| R4（収束） | 0 | サニティチェックのみ |

**1 ラウンドで収束することはほぼない。3–4 ラウンドを見積もる。** 件数が半減せず横ばいなら指示が曖昧で fix が浅い兆候。

## 起動チェック

ラウンド 1 開始前に必ず以下を実行する:

1. レビュー対象の計画書・規約・既知の見逃しパターンを**必ず先に読む**:
   - 該当フェーズの計画書（例: `docs/<feature>/implementation-plan.md`）
   - アーキテクチャ／仕様書／open-questions
   - `.claude/skills/bug-postmortem/MISSES.md`
   - `CLAUDE.md`
2. 現状の build/test 状態を実コマンドで確認。レビュアーが「全緑」と主張しても自分で `cargo fmt --check` などを叩いて裏を取ること（R6 で reviewer の「fmt 緑」主張を信じて CRITICAL を見落としかけた）

## ループ手順

### Step 1: レビュー（サブエージェント並列）

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

> `docs/<feature>/` 配下のドキュメントを必ず参照し、実装が計画と整合しているか・MISSES.md の既知パターンに該当しないかを検証せよ。レビュー観点・重点チェック項目・findings の書き方は [`.claude/skills/e-station-review/SKILL.md`](../e-station-review/SKILL.md) をすべて参照せよ。指摘は **CRITICAL / HIGH / MEDIUM / LOW** で分類し、`path:line`、根拠（計画書のどの条項に違反か）、推奨修正、回帰防止テストの提案を含めよ。**既知繰越（H5/H6/...）は再指摘不要だが、その繰越扱いの実装が本当に正しいかは検証せよ。** 末尾に重要度別件数サマリ。500 行以内。

GUI を含まないバックエンド変更なら `iced-architecture-reviewer` を省略してよい。Rust が無ければ `rust-reviewer` も省略。**スコープに合わせてエージェントを選ぶ**。

### Step 2: 集約（オーケストレーター本人）

全エージェントの指摘をマージし、重複統合 → 重要度順に並べた一覧を作成。CRITICAL / HIGH / MEDIUM の件数を要約。

**集約時の注意**:
- 同じ問題が複数エージェントから別 ID で報告されることが多い（例: `set_second_password_for_test` を type-designer が HIGH、rust-reviewer が MEDIUM 評価）。**高い方の重要度を採用**
- レビュアー間で重要度判断が割れた場合は、production リスクが高い方を採用
- 「Phase O1 繰越」と書かれた既知項目は再指摘されたら無視可。ただし「ラウンド N で完遂」と主張された項目が**実は実装と乖離**しているケースを毎回チェック（R7-R8 で頻出）

### Step 3: 修正（サブエージェント並列）

**MEDIUM 以上が 1 件でもあれば** `general-purpose` エージェントに修正依頼。

> **`implementer` サブエージェントは単一 RED→GREEN サイクル制約があり、大きな batch を拒否する。** 多項目を一括で進めたいときは `general-purpose` に「TDD 順序で順次着手せよ」と明示する。1 項目ずつ厳密に進めたい場合は test-writer → implementer のペアを項目ごとに回す。

修正エージェントへの指示には必ず以下を含める:

- 該当ファイル・行・指摘内容（オーケストレーター側で要約）
- 不可侵ルール一式（[`SKILL.md`](./SKILL.md) 参照）
- TDD 順守と各項目の RED → GREEN → REFACTOR 順序
- **uv 環境利用の明記**（Python 関連は `uv run pytest`、`uv run python -m engine`、`uv add` 必須。素の `python` 禁止）
- **「Phase O1 / 繰越に勝手に降格しない」**: ユーザーが (b) 全件指示を出している場合、エージェントは「影響範囲が大きい」「DTO restructuring が必要」等の理由で勝手に Phase O1 へ降格する傾向がある。**「降格判断はユーザー権限。実施できないと判断したら DEFER ではなく STOP+REPORT して指示を仰げ」と明示**
- **「対象ファイル外を変更しない」**: subagent が無関係な docs/* を編集することがある。「修正対象として列挙したファイル + 計画書反映ブロック以外は触らない」と明示
- 修正後の最終コマンド緑確認（cargo fmt --check も含む）
- **計画書の該当フェーズ末尾に「レビュー反映 (YYYY-MM-DD, ラウンド N)」ブロックを追記**

修正項目は依存関係順にグループ化する（例: docs only → 単独ファイル → cross-module → テスト品質）。**型シグネチャ変更や module 構造変更は最初に実施**（後続項目への影響を吸収しやすい）。

### Step 4: 修正の検証と再レビュー

修正後にレビュー段階を再実行。**ただし全 6 エージェントを毎回回す必要はない**:

- ラウンド 2 以降は **変更があった層のレビュアーのみ**（例: Python だけ変えたなら silent-failure-hunter + general-purpose、Rust の signature 変更なら rust-reviewer のみ）
- 変更していない層を再走させても新規発見は少なく、コンテキスト浪費になる
- **silent-failure-hunter は毎回必ず回す**: 「fix が新たな silent failure を導入する」パターンが頻出（例: R7 で `restore_failed=True` 時の VenueReady フィルタが、Rust 側 subscribe 残存という新たな silent failure を生んだ。R8 で発見）

### Step 5: 次ラウンドへ / ループ終了

収束基準は [`SKILL.md`](./SKILL.md) の「収束基準」セクションを参照。

CRITICAL/HIGH/MEDIUM が残存する場合は Step 1 に戻る。次ラウンドのレビュー指示には:
- **当ラウンドで修正された箇所を重点検査**する旨を明記
- ラウンド数が増えたら投入エージェントを絞る（変更層のみ + silent-failure-hunter 固定）

## 出力形式（毎ラウンド）

各ラウンド開始時:

```
=== ラウンド N ===
残存 CRITICAL: X件 / HIGH: Y件 / MEDIUM: Z件 / LOW: W件
```

計画書の該当フェーズ末尾に **「レビュー反映 (YYYY-MM-DD, ラウンド N)」** ブロックを追記し続ける:

- 完了項目に ✅
- 設計判断・新たな知見・Tips を他作業者が再現できる粒度で
- 既存の他フェーズ（例: T2）のスタイルを踏襲

書く内容:
1. 解消した指摘（id + 1 行サマリ）
2. 修正中に発覚した設計判断（plan を更新する根拠）
3. 新たな見逃しパターン候補（次回 MISSES.md 追記候補）
4. 持ち越し項目とその理由

**サイズ管理**: 各ラウンド反映ブロックが肥大化する。「ラウンド N で解消」と書いた項目は、次ラウンド以降では繰り返し書かない。サマリと差分のみ記録する。

## 完了サマリ

```
=== 完了 ===
全ラウンド数: N
修正した Finding 総数: CRITICAL X / HIGH Y / MEDIUM Z / LOW W
残存 LOW（対応不要）: K件
繰越（Phase O1）: L件（open-questions.md に明示済み）
主要な反映成果:
- 型安全: ...
- silent failure 除去: ...
- テスト追加: M件（cargo test / pytest 緑確認済）
- IPC 整合: ...
```

## ループ上限と escape hatch

- **最大 N ラウンド = 8** をハード上限とする。それを超えても収束しない場合は強制終了し、残存 CRITICAL/HIGH/MEDIUM を `open-questions.md` に **未決オープン質問として書き出す**
- CRITICAL/HIGH/MEDIUM 件数が **3 ラウンド連続で減らない**場合、投入レビュアーの観点が実装スコープとずれているサインなのでユーザーに相談する

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
| LOW | 列挙のみ。次フェーズで拾うかどうかをユーザーに判断してもらう |

### `implementer` vs `general-purpose`

- `implementer`: **1 項目厳密 TDD**。RED テストの handoff が必須。多項目を投げると拒否される
- `general-purpose`: **多項目 batch + TDD 順守可**。プロンプトで「各項目で RED→GREEN→REFACTOR」と明示する
- 1 項目を完璧にやりたい時は test-writer → implementer のペア。多項目を効率重視で進めたい時は general-purpose

### コミット時の選択的ステージング

修正エージェントが `cargo fmt --all` を実行すると、**フェーズと無関係なファイルにもフォーマット差分が出る**。さらに、エージェントが裁量で別フェーズの計画書（例: 隣接する `docs/✅order/*`）を更新することもある。

コミット時は `git add -A` を避け、フェーズに関連するファイルを **明示列挙**してステージング。判断基準:

- ✅ ステージ: 修正対象として明示したファイル、新設テスト、cargo fmt が触った同フェーズ範囲のソース、計画書の対象フェーズ
- ❌ ステージしない: 別フェーズの docs/、untracked な作業中ファイル、別エージェントが副次生成した artifact

## 禁止事項と失敗パターン

### ループ固有の禁止事項

- `silent-failure-hunter` を省略してはいけない。fix が新たな silent failure を生む頻度が高い
- 修正エージェントに勝手に繰越を決めさせてはいけない。降格はユーザー権限
- subagent の「全緑」主張を鵜呑みにしてはいけない。**必ず `cargo fmt --check` 等を自分で叩いて裏を取る**

### 失敗パターン（避けること）

1. **MEDIUM を無視して LOW だけ残した状態で「完了」にする** — ループ条件違反。MEDIUM ゼロまで繰り返す
2. **修正後の再レビューをスキップ** — 修正で新規 MEDIUM が混入していないか必ず確認する
3. **6 エージェントを順次起動** — 並列が原則
4. **修正エージェントを `implementer` で多項目投げる** — 拒否されて時間ロス。`general-purpose` に切り替える
5. **計画書追記を最後にまとめる** — ラウンドごとに追記しないと、次のレビュアーが「何が解消済みか」を判断できない
6. **secrets を含むテスト fixture を使う** — `password = "p"` のような短い値は偶然マッチでガード失敗を招く。ユニーク化必須
7. **「Phase O1 繰越」を subagent の判断で実行させる** — 降格はユーザー権限。プロンプトで明示禁止
8. **fix 後に silent-failure-hunter を回さない** — fix 由来の新規 silent failure を見落とす
9. **subagent の「全緑」主張を鵜呑み** — 自分で `cargo fmt --check` 等を叩いて裏を取る
10. **コミット時に `git add -A` を使う** — 別フェーズの作業や untracked artifact が混入。明示列挙する

## 知見（実績ベース）

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

R6 まで `.env` の dev creds と `test_tachibana_startup_supervisor.py` の漏洩検知 sentinel が **同一文字列**だった。テスト的には sentinel がユニークなので OK と扱われていたが、`.env` を変更すると検知が無効化される脆さ。**test sentinel は `TEST_SENTINEL_USER_<uuid8>` 形式で realistic value とは交わらないドメインに置く**。

### 9. `.env.sample` と `.env.example` の二重存在

`.env.sample` と `.env.example` の両方が git tracked になっている状態は dev のオンボーディングを壊す。**プロジェクト規約として `.env.example` 一本に統一**し、もう一方は削除。

### 10. `--token` CLI 引数 = secrets leakage

`argparse` で `--token VALUE` を受けると、`ps -ef` / Windows タスクマネージャの commandline 列に値が残る。**stdin 経路に統一し、CLI flag は `argparse.SUPPRESS` で隠して deprecation warning**。

### 11. cargo fmt の workspace 一括は無関係ファイルを汚す

`cargo fmt --all` は workspace 全体に走るため、フェーズと関係ない `exchange/` や `src/screen/dashboard/tickers_table.rs` まで diff が出る。コミット時に「これは fmt 由来か機能変更由来か」を `git diff --stat` で先に確認、無関係 fmt は同 PR に含めるか別 PR に分けるかを判断。

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

## 汎用呼び出しテンプレート

新フェーズ・PR を仕上げるときにオーケストレーター（あなた）に貼り付けて使う。`{{}}` プレースホルダーを実値に置換すること。

---

あなたは **オーケストレーター** です。`{{repo_name}}` リポジトリで `{{feature}}` のフェーズ `{{phase_id}}` 「`{{phase_title}}`」を レビュー → 修正のループで仕上げてください。

**唯一のリファレンス**: すべての手順・不可侵ルール・収束基準・既知の落とし穴は [`SKILL.md`](./SKILL.md) と本ファイル（[`ImplementationLoop.md`](./ImplementationLoop.md)）に集約されています。

### 必読ドキュメント

```text
{{plan_doc}}              # 例: docs/✅tachibana/implementation-plan.md
{{spec_doc}}              # 例: docs/✅tachibana/spec.md
{{architecture_doc}}      # 例: docs/✅tachibana/architecture.md
{{open_questions_doc}}    # 例: docs/✅tachibana/open-questions.md
{{feature_skill}}         # 例: .claude/skills/tachibana/SKILL.md
.claude/skills/bug-postmortem/MISSES.md
.claude/skills/e-station-review/SKILL.md
.claude/skills/review-fix-loop/SKILL.md
.claude/skills/review-fix-loop/ImplementationLoop.md
.claude/skills/tdd-workflow/SKILL.md
CLAUDE.md
```

### レビュー対象スコープ

```text
{{file_list}}             # 例:
                          # data/src/config/tachibana.rs
                          # engine-client/src/{dto,error,process}.rs
                          # python/engine/...
```

### プロジェクト固有の検証コマンド

```bash
{{verify_cmds}}
# 例（e-station）:
# cargo check --workspace
# cargo clippy --workspace -- -D warnings
# cargo fmt --check
# cargo test --workspace
# uv run pytest {{test_glob}} -v
```

### 起動するレビュアー

```text
{{reviewers}}
# デフォルト推奨セット（フルスタック変更時）:
# rust-reviewer, silent-failure-hunter, iced-architecture-reviewer,
# type-design-analyzer, ws-compatibility-auditor, general-purpose
```

### スコープ外（subagent が触らないこと）

```text
{{out_of_scope_paths}}
# 例:
# docs/<other-phase>/      # 別フェーズの計画書
# .claude/skills/<other>/       # 他のスキル
```

### 進捗反映先

- 計画書: `{{plan_doc}}` の `§{{phase_id}}` 末尾に「レビュー反映 (YYYY-MM-DD, ラウンド N)」ブロックを追記
- スタイル参考: `{{plan_doc_style_ref}}`

### 開始手順

1. 上記必読ドキュメントを読み、`{{plan_doc}}` の `§{{phase_id}}` の現状を把握する
2. `ImplementationLoop.md` の「起動チェック」→「Step 1（並列レビュー）」から開始する
3. 各ラウンドの集約・修正・再レビューは本ファイルの手順に従う
4. **MEDIUM 以上ゼロ** で終了。ループ完了後にユーザーへ最終サマリ（ラウンド毎の件数推移・繰越項目・新規追加テスト）を報告する

---

## ループ自体のメンテナンス

このスキル自体も品質収束する。新フェーズで適用した後、新しい知見が出たら本ファイルの「知見（実績ベース）」セクションに追記する。
