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
- **完了時の検証**: プロジェクトの最終コマンド全件緑（e-station なら `cargo check --workspace` / `cargo clippy --workspace -- -D warnings` / `cargo test --workspace`（デフォルト並列）/ `uv run pytest <対象>`）

---

## ループ手順

### Phase 0 — 前提読込

レビュー対象の計画書・規約・既知の見逃しパターンを **必ず先に読む**:
- 該当フェーズの計画書（例: `docs/plan/<feature>/implementation-plan.md`）
- アーキテクチャ／仕様書／open-questions
- `.claude/skills/bug-postmortem/MISSES.md`
- `CLAUDE.md`

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

各エージェントへの指示テンプレ:

> `docs/plan/<feature>/` 配下のドキュメントを必ず参照し、実装が計画と整合しているか・MISSES.md の既知パターンに該当しないかを検証せよ。指摘は **CRITICAL / HIGH / MEDIUM / LOW** で分類し、ファイル名:行番号、根拠（計画書のどの条項に違反か）、推奨修正、回帰防止テストの提案を含めよ。500 行以内に収めよ。

GUI を含まないバックエンド変更なら `iced-architecture-reviewer` を省略してよい。Rust が無ければ `rust-reviewer` も省略。**スコープに合わせてエージェントを選ぶ**。

### Phase 2 — 集約

全エージェントの指摘をマージし、重複統合 → 重要度順に並べた一覧を作成。CRITICAL / HIGH / MEDIUM の件数を要約。

### Phase 3 — 修正段階

**MEDIUM 以上が 1 件でもあれば** `general-purpose` エージェントに修正依頼。

> **`implementer` サブエージェントは単一 RED→GREEN サイクル制約があり、大きな batch を拒否する。** 多項目を一括で進めたいときは `general-purpose` に「TDD 順序で順次着手せよ」と明示する。1 項目ずつ厳密に進めたい場合は test-writer → implementer のペアを項目ごとに回す。

修正エージェントへの指示には必ず以下を含める:

- 該当ファイル・行・指摘内容（オーケストレーター側で要約）
- 不可侵ルール一式
- TDD 順守と各項目の RED → GREEN → REFACTOR 順序
- 修正後の最終コマンド緑確認
- **計画書の該当フェーズ末尾に「レビュー反映 (YYYY-MM-DD, ラウンド N)」ブロックを追記**

修正項目は依存関係順にグループ化する（例: docs only → 単独ファイル → cross-module → テスト品質）。

### Phase 4 — 再レビュー

修正後にレビュー段階を再実行。**ただし全 6 エージェントを毎回回す必要はない**:

- ラウンド 2 以降は **変更があった層のレビュアーのみ**（例: Python だけ変えたなら silent-failure-hunter + general-purpose、Rust の signature 変更なら rust-reviewer のみ）
- 変更していない層を再走させても新規発見は少なく、コンテキスト浪費になる
- 「修正で新規導入された問題がないか」を主眼に置く（既解消項目の確認は副次的）

### Phase 5 — ループ終了条件

- **MEDIUM 以上ゼロ** で終了
- LOW のみ残った場合は LOW 一覧を提示して終了
- HIGH 以上が「次イテレーション持ち越し」と判断される場合は、計画書の「繰越 / 次イテレーション」ブロックに明示記載した上で終了（理由・期限・代替策を必ず添える）

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
| HIGH（大規模リファクタ・別 PR スコープ） | 計画書「繰越」に明示してパス |
| MEDIUM | 同 PR で修正（このスキルの停止条件） |
| LOW | 列挙のみ。次フェーズで拾うかどうかを user に判断してもらう |

### `implementer` vs `general-purpose`

- `implementer`: **1 項目厳密 TDD**。RED テストの handoff が必須。多項目を投げると拒否される
- `general-purpose`: **多項目 batch + TDD 順守可**。プロンプトで「各項目で RED→GREEN→REFACTOR」と明示する
- 1 項目を完璧にやりたい時は test-writer → implementer のペア。多項目を効率重視で進めたい時は general-purpose

### 計画書のサイズ管理

各ラウンド反映ブロックが肥大化する。「ラウンド N で解消」と書いた項目は、次ラウンド以降では繰り返し書かない。サマリと差分のみ記録する。

---

## 失敗パターン（避けること）

1. **MEDIUM を無視して LOW だけ残った状態で「完了」にする** — ループ条件違反。MEDIUM ゼロまで繰り返す
2. **修正後の再レビューをスキップ** — 修正で新規 MEDIUM が混入していないか必ず確認する
3. **6 エージェントを順次起動** — 並列が原則
4. **修正エージェントを `implementer` で多項目投げる** — 拒否されて時間ロス。`general-purpose` に切り替える
5. **計画書追記を最後にまとめる** — ラウンドごとに追記しないと、次のレビュアーが「何が解消済みか」を判断できない
6. **secrets を含むテスト fixture を使う** — `password = "p"` のような短い値は偶然マッチでガード失敗を招く。ユニーク化必須

---

## 適用例（参考）

立花 T3 フェーズ（バックエンド配線、Rust + Python、~30 ファイル変更）:
- ラウンド 4 初回レビュー: CRITICAL 1, HIGH 13, MEDIUM 19, LOW 10
- ラウンド 4 修正: CRITICAL 1 + HIGH 5 + MEDIUM 12 解消、HIGH 7 を T3.5 持ち越し明示
- ラウンド 4 再レビュー（rust + silent のみ）: 新規 MEDIUM 7
- ラウンド 5 修正: 新規 MEDIUM 7 解消（孤児プロセス回収・log.exception 防御・bool 型強制ほか）
- ラウンド 5 再レビュー（silent のみ）: **No new MEDIUM+ findings** → 終了

総所要: レビュー 6 並列 × 2 ラウンド + 軽量 1 ラウンド + 修正 2 ラウンド。所要時間オーケストレーター時間で約 1 セッション分。
