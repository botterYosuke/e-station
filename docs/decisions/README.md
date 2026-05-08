---
title: decisions
status: skeleton
---

# decisions

このディレクトリには ADR（Architecture Decision Record）が入ります（移送中）。

ナンバリング: `NNNN-<slug>.md`（4 桁ゼロ詰め連番）。`MANIFEST.md` は人間向け索引（YAML パース対象外）。

## 予約済み番号（計画書で固定）

- 0001 — Rust↔Python 境界
- 0002 — IPC schema versioning (SCHEMA_MAJOR/MINOR)
- 0003 — AI/ML 非同梱方針 (status: deferred)
- 0004 — ユーザー戦略は自己責任 (status: deferred)
- 0005 — 将来の Python 単独モード (status: deferred)
- 0006 — Rust + Python 二言語構成の採用根拠 (status: proposed)
- 0007 — Rust 言語選択の根拠 (status: proposed)
- 0008 — Python 採用根拠（data engine 側）(status: proposed)

0009 以降は `_migration-ledger.yaml` の `status: deferred` エントリで自動割当。

## ADR ステータス遷移ルール

| 遷移                  | トリガ                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------ |
| proposed → accepted   | PR で 1 名以上の reviewer 承認 + 出典 commit が main に到達後                              |
| proposed → deferred   | 出典が repo 内に確定できないと判明したとき（本文は空、解除待ち）                            |
| deferred → accepted   | 解除時は YAML フロントマター `source_commit:` に出典 commit SHA が必須                     |
| accepted → superseded | 新 ADR の YAML フロントマター `supersedes: NNNN` で参照されたとき自動                      |

`scripts/check_adr_status.py`（実行ステップ 6.5 で新設予定）の責務:

- `status: deferred` の ADR は本文を持たない（ヘッダのみ）ことを assert
- `status: accepted` の ADR は `source_commit:` フロントマターが必須
- `supersedes:` で参照される ADR が `superseded` になっているかの整合性検査

## 0009 以降の ADR（起票済み・deferred）

0009〜0133 の 125 件は `status: deferred` のスタブとして起票済み。各スタブには `old_path`（旧ファイルパス）と `source_commit`（典拠 commit）が記録されており、`git show <source_commit>:"<old_path>"` で原文を参照できる。

本文起票（deferred → accepted 昇格）は原本の内容を精査し Context / Decision / Consequences を記入した上で個別 PR で行う。

### グループ別一覧

| 番号 | グループ | 件数 |
|------|----------|------|
| 0009–0015 | floating-windows archive（iced/Bevy 採用検討）| 7 |
| 0016–0021 | kabusapi archive | 6 |
| 0022–0044 | menu-and-footer archive | 23 |
| 0045–0073 | nautilus_trader archive | 29 |
| 0074–0090 | order archive | 17 |
| 0091–0110 | python-data-engine archive | 20 |
| 0111–0133 | tachibana archive | 23 |
