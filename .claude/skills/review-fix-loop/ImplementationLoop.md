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

### 12. `isolation: "worktree"` がフィーチャーブランチで base 不整合を起こす

Phase 8 R1 の parallel-agent-dev で `Agent({ isolation: "worktree" })` を使うと、worktree が **現在のフィーチャーブランチ HEAD ではなく `main` (またはデフォルトブランチ) から作られる**ケースがあった（R1 で Phase 3/4 が同時に STOP+REPORT）。Phase 8 で新設した `python/engine/replay_session.py` や `engine-client/src/session_file.rs` が worktree に存在せず、エージェントが「対象ファイルが無い → 推測で再実装するのは事故」と正しく判断して停止した。

**対策**: フィーチャーブランチ作業中は **`isolation: "worktree"` を使わずメインリポジトリで直接作業させる**。並行性は「ファイル単位で担当を分ける」で確保し、計画書のような共有ファイルだけ orchestrator が最後に集約する。worktree を使う場合は agent prompt 冒頭で `git branch --show-current` と特定ファイルの存在チェックを必須化し、不一致なら STOP+REPORT。

### 13. 単一エージェント batch の限界（10 項目 + 大型機能 1 で STOP+REPORT）

Phase 8 R1 で CRITICAL 6 / HIGH 10 / MEDIUM 16 + 大型 LiveSession attach 本実装 + 898 行テスト分割 + 計画書追記 を **1 体の `general-purpose` に投げたら着手前に STOP+REPORT** された。理由は「単一会話で TDD 厳守 + 全検証緑で時間予算が読めない」「中途半端で `✅ 達成` と書くのは MISSES.md 記録対象の典型パターン」と subagent が正しく判断した。

**閾値の経験則**:
- 〜10 項目 + 軽い機能 → 単一 `general-purpose` で OK
- 10〜30 項目 → 依存順 batch（型基盤 → server → helper → Rust → test）に明示分割しても単一 agent でいける場合あり
- **30 項目超 / 大型新機能含む → `/parallel-agent-dev` 必須**。Phase 1 (foundation) → Phase 2 → Phase 3/4 並列 → Phase 5 のような multi-stage で組む

subagent に「困ったら STOP+REPORT」を明示しているなら、過剰スコープの自己防衛も信頼できる。orchestrator は STOP+REPORT を受けたら案 A/B/C をユーザーに提示する。

### 14. 計画書末尾「レビュー反映」ブロックの並行更新競合

Phase 3 (Python) と Phase 4 (Rust) を並行実行する際、両者が同じ `python-helper-direct-api.md` 末尾の「レビュー反映」ブロックに追記しようとすると競合する。

**対策**: 並行する agent のうち **1 つだけ計画書の追記責任を持たせ、他は完了報告に「計画書追記用差分サマリ」を箇条書きで返す**。orchestrator が最後に集約してまとめる。Phase 8 R1 では Phase 3 が計画書を更新、Phase 4 は差分サマリを report の末尾に明示する形で衝突回避できた。

### 15. multi-client 化時の broadcast → unicast 化漏れ（fanout ガード）

Phase 8.1b で `_Broadcaster` を導入したあと、R2 で「`_do_request_venue_login` の早期 reject (`mode_mismatch` / `unsupported_venue`) が `self._emit`（broadcast）のまま」と発見された。`EngineBusy` を unicast 化する R1 修正は `_check_*_state` 経由の reject のみカバーしており、handler 内での明示的な VenueError 送出は別経路で broadcast に取り残されていた。

**reviewer 観点**: multi-client 機構（broadcast / fanout）を導入する PR では「**当該 client への応答**」と「**全 client への通知**」を機械的に分類し、応答系（`*Error`、`EngineBusy`、reject 通知）は **すべて unicast (`send_to(ws, ...)`)** で送れているかをファイル全体で検査する。`grep -n "self._emit\|self._outbox.append" python/engine/server.py` で全送信経路を列挙して目検する。

### 16. illegal state 組合せを Literal の直交 union + model_validator で弾く

R1 で `EngineBusy.current_state` を Replay/Live フラットな 7 値 Literal で定義したところ、`(STOPPING, RequestVenueLogin)` のような Replay/Live 跨ぎの illegal な組合せが Pydantic を通過する設計欠陥が発覚。R1 修正で:

```python
ReplayStateName = Literal["IDLE","LOADED","RUNNING","STOPPING"]
LiveStateName = Literal["DISCONNECTED","CONNECTING","CONNECTED"]
CurrentEngineState = ReplayStateName | LiveStateName

# EngineBusy に @model_validator(mode="after") で
# (current_state ∈ ReplayStateName) ↔ (attempted_command ∈ ReplayOnlyCommand ∪ SharedCommand)
# (current_state ∈ LiveStateName) ↔ (attempted_command ∈ LiveOnlyCommand ∪ SharedCommand)
# を強制
```

の構造に変更。**Literal を直交軸ごとに分け model_validator で組合せ整合性を強制**するパターンは state machine が複数並走する system で再利用価値が高い。Rust 側にも `CurrentEngineState` enum + `AttemptedCommand` enum を追加し、`#[serde(deny_unknown_fields)]` ではなく enum 完全一致デシリアライズで未知値を弾く。

### 17. fix 自体が silent failure を生む（定量的傾向）

Phase 8 R1〜R4 で「ラウンド N の fix が ラウンド N+1 で新規 silent failure として発見される」連鎖が定量的に確認できた:

| ラウンド | 新規 silent failure 発見 | 前ラウンド fix 由来 |
|---|---|---|
| R1 (初回レビュー) | 32 件 | (実装由来) |
| R2 サニティ | 8 件 (CRITICAL 1 / HIGH 3 / MEDIUM 4) | R1 fix 由来が大半 |
| R3 サニティ | 4 件 (MEDIUM 2 / LOW 2) | R2 fix 由来が大半 |
| R4 サニティ | 0 件（**収束**） | — |

R2 で見つかった 8 件のうち、CRITICAL `_login_attach()` の `request_id` フィルタは「R1 の Silent-M3 (Error 即 raise) を attach login にも適用したら broadcast `request_id=None` の VenueError を取りこぼす」という連鎖。R3 で見つかった「credential fingerprint 誤 RuntimeError」は「R2 の MEDIUM-R2-7（demo flag 切替検知）を fingerprint 比較で実装したら env-resolved と explicit 渡しで fp が一致しない」という連鎖。

**毎ラウンド `silent-failure-hunter` を必ず投入**し、特に「前ラウンドで導入した新変数・新フィールド・新 if 分岐」をピンポイントで検査する prompt にすると効率的。

### 18. websockets `async for` vs `ws.recv()` の挙動差異（Issue #40, 2026-05-10）

websockets 10+ / 16.0 では `ClientConnection.__aiter__` が内部で `ConnectionClosedOK` を `return`（= `StopAsyncIteration`）に変換する:

```python
# websockets 16.0 実装
async def __aiter__(self):
    try:
        while True:
            yield await self.recv()
    except ConnectionClosedOK:
        return  # StopAsyncIteration に変換 → except ConnectionClosedOK に到達しない
```

**結果**: `async for raw in ws:` でループするコードは、サーバが正常切断（code=1000）しても `except ConnectionClosedOK` ブロックに到達しない。`async with` を正常終了してカウンタを一切触れずにループが続く → 無限高速再接続。

**修正パターン**: `ws.recv()` を直接呼び出す + `asyncio.wait_for` でタイムアウトを設ける:

```python
while True:
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)
    except TimeoutError:
        logger.warning("no message for %.0fs, reconnecting...", _RECV_TIMEOUT_S)
        break  # async with を抜けて再接続
    # ConnectionClosedOK/Error は asyncio.wait_for を素通りして外の except に到達する
```

**テストへの影響**: モックの `FakeWS` が `__aiter__`/`__anext__` を実装していた場合、プロダクションコードが `ws.recv()` に変わると `recv()` メソッドが必要になる。モックはプロダクションの実際の API を反映するように更新する。

**reviewer 観点**: WebSocket 受信ループを `async for` で書いたコードがあれば、`except ConnectionClosedOK` ブロックが実際に到達可能かを websockets バージョンと照合する。

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

### Phase 8 Python helper R1-R4（実測, 2026-05-04）

Python helper direct API（`ReplaySession` / `LiveSession` / `_AttachClient`）の review-fix-loop。Rust + Python、48 ファイル変更、+5549/-1261 行:

| ラウンド | 投入レビュアー | CRITICAL | HIGH | MEDIUM | LOW | 修正後の検証 |
|---|---|---|---|---|---|---|
| R1 初回 | 5 並列（rust / silent / type / ws-compat / general） | 6 | 10 | 16 | 10+ | (修正前) |
| R1 修正 | parallel-agent-dev: Phase 1 (型基盤) → Phase 2 (server) → Phase 3/4 並列 (helper / Rust) → Phase 5 (test 分割) | — | — | — | — | 4cmd 緑 / pytest 1598 → 1670 (+72) |
| R2 サニティ | 2 並列（silent-failure + general-purpose plan crosscheck） | 1 | 3 | 4 | 3 | — |
| R2 修正 | 単一 general-purpose（8 項目 batch） | — | — | — | — | 4cmd 緑 / pytest 1684 (+14) |
| R3 サニティ | silent-failure 単独 | 0 | 0 | 2 | 2 | — |
| R3 修正 | 単一 general-purpose（4 項目 batch） | — | — | — | — | 4cmd 緑 / pytest 1691 (+7) |
| R4 サニティ | silent-failure 単独 | 0 | 0 | 0 | 1 | **収束** |

総所要: レビュー 5+2+1+1=9 並列起動 + 修正 4 ラウンド。新規テスト +93 件（test_review_fixes.py 898 行を機能別 10 ファイルに分割。新規 phase8_round2 / round3 / phase3_review_fixes 等）。LOW のみ残存 (5 件)、HIGH 以上は全て収束。

**学んだこと**:

1. **R1 が大型（30 件超 + 大型新機能）の場合、単一 general-purpose は STOP+REPORT する** → `/parallel-agent-dev` で依存順分割が必須。Phase 1（型基盤）が後続全てに波及するため最初に確定 → 検証 → 並列展開。
2. **`isolation: "worktree"` がフィーチャーブランチ作業で base 不整合を起こす** → 並行性は worktree でなく「ファイル単位の担当分け」で確保する。Phase 8 では Phase 3 (Python) と Phase 4 (Rust) はメインリポジトリで直接並行できた。
3. **R1 32 件 → R2 8 件 → R3 4 件 → R4 0 件** の収束カーブが従来知見（25-30 → 10-15 → 5-7 → 0）の上限に近い。大規模 fix では半減ペースが標準。
4. **C-GP4 のような大型新機能（LiveSession attach 本実装）も review-fix-loop 内で完遂可能** だった。「Phase 9 持ち越し」の安易な降格を案 A〜C でユーザーに判断させた点が分岐点。
5. **broadcast → unicast の漏れ** が R1 で M-GP8 として 1 箇所修正されたが、R2 で `_do_request_venue_login` の早期 reject に同種漏れが残っていた。**multi-client 移行 PR は全送信経路の grep 走査を観点に明記**。

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
