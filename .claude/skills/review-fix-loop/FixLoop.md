# 作業依頼プロンプト（オーケストレーター向け）

あなたはオーケストレーターです。e-station リポジトリ（Rust + Python の取引所統合アプリ）で、立花証券統合のフェーズ T3「クレデンシャル受け渡し配線」が実装完了しました。これをレビュー → 修正のループで仕上げてください。

## 背景・参照ドキュメント

すべての仕様・設計判断・タスク粒度は以下に記載されています。**必ず先に読むこと**。要約・推測で進めないでください。

* [docs/plan/tachibana/implementation-plan.md](docs/plan/tachibana/implementation-plan.md) — 計画とチェックリスト（T3 は §フェーズ T3）
* [docs/plan/tachibana/spec.md](docs/plan/tachibana/spec.md) — 仕様詳細
* [docs/plan/tachibana/architecture.md](docs/plan/tachibana/architecture.md) — IPC・login flow の設計
* [docs/plan/tachibana/data-mapping.md](docs/plan/tachibana/data-mapping.md)
* [docs/plan/tachibana/inventory-T0.md](docs/plan/tachibana/inventory-T0.md)
* [docs/plan/tachibana/open-questions.md](docs/plan/tachibana/open-questions.md)
* [.claude/skills/tachibana/SKILL.md](.claude/skills/tachibana/SKILL.md) — 立花 API の規約
* [.claude/skills/bug-postmortem/MISSES.md](.claude/skills/bug-postmortem/MISSES.md) — 過去の見逃しパターン
* [CLAUDE.md](CLAUDE.md) — プロジェクト規約

## レビュー対象（T3 で追加・変更されたコード）

```text
data/src/config/tachibana.rs
data/tests/tachibana_keyring_roundtrip.rs
engine-client/src/dto.rs / error.rs / process.rs
engine-client/tests/{process_creds_refresh_hook,process_venue_ready_gate,process_lifecycle,dev_login_flag_release,schema_v1_2_roundtrip}.rs
python/engine/__main__.py / server.py / schemas.py
python/engine/exchanges/{tachibana_login_dialog,tachibana_login_flow,tachibana_auth}.py
python/tests/test_tachibana_{dev_env_guard,login_started_semantics,startup_supervisor,unread_notices_terminal,auth}.py
src/main.rs
scripts/smoke_tachibana_login.py
```

## ループ手順

### 1. レビュー段階（並列）

以下のサブエージェントを **同一メッセージ内で並列起動**：

* `rust-reviewer` — Rust 側の所有権・エラー処理・iced パターン
* `silent-failure-hunter` — 握り潰しエラー、creds 漏洩リスク
* `iced-architecture-reviewer` — Elm アーキテクチャ逸脱
* `type-design-analyzer` — 型レベル不変条件
* `ws-compatibility-auditor` — IPC スキーマ・圧縮整合（schema_v1_2 影響範囲）
* `general-purpose` — Python 側コード品質と T3 計画書 §フェーズ T3 のタスク全項目とのクロスチェック

各エージェントには **「`docs/plan/tachibana/` 配下の関連ドキュメントを必ず参照し、実装が計画と整合しているか・MISSES.md の既知パターンに該当しないかを検証せよ」** と指示し、指摘を **CRITICAL / HIGH / MEDIUM / LOW** で分類させること。

### 2. 集約

全エージェントの指摘をマージし、重複統合 → 重要度順に並べた一覧を作成。

### 3. 修正段階

**MEDIUM 以上の指摘が 1 件でもあれば** `implementer` サブエージェントに修正依頼。

修正エージェントへの指示には必ず以下を含める：

* 該当ファイル・行・指摘内容
* 修正後に `cargo check --workspace` / `cargo test -p flowsurface-engine-client` / `uv run pytest python/tests/test_tachibana_*.py -v` を実行して緑であることを確認させる
* 修正の根拠が計画書に既存ならそのリンクを引用、なければ計画書に追記してから修正

### 4. ループ終了条件

* レビューを再実行し、**MEDIUM 以上の指摘がゼロ**になるまで 1〜3 を繰り返す
* LOW のみ残った場合は LOW 一覧を提示して終了

## 進捗共有ルール

進捗があり次第、[docs/plan/tachibana/implementation-plan.md](docs/plan/tachibana/implementation-plan.md) の T3 セクションに以下を追記：

* 完了した作業項目に ✅ を付ける
* 「進捗 (YYYY-MM-DD)」「設計判断（実装）」「Tips」「レビュー反映 (YYYY-MM-DD)」ブロックを T2 の記載スタイル（[implementation-plan.md:154-170](docs/plan/tachibana/implementation-plan.md#L154-L170)）に倣って書く
* 新たな知見・ハマりどころは他作業者が読んで再現できる粒度で記録

## 制約

* TDD で進めること: [.claude/skills/tdd-workflow/SKILL.md](.claude/skills/tdd-workflow/SKILL.md) に従い、修正前に失敗するテストを書いてから直す
* secret（user_id / password / session token）をログ・テスト・コミットメッセージに含めない（[architecture.md](docs/plan/tachibana/architecture.md) §秘匿情報規約）
* 既存テストを壊さない。壊した場合は修正対象に含める
* 不明点は計画書を読み直し、それでも不明なら `open-questions.md` に追記してから保守的判断で進める

## 開始

まず計画書 §フェーズ T3 を読み、現在の実装ファイル一覧を把握してからレビュー段階を開始してください。