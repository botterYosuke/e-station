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

## 0009 以降の ADR

`docs/_migration-ledger.yaml` で `status: deferred` かつ `new_path: docs/decisions/NNNN-...` を持つエントリは ADR 抽出候補（0009〜0133、125 件）として台帳に予約されている。本ステップ（ステップ 4 = ADR 抽出）では **0001〜0008 のみを実装**し、0009 以降は将来の個別 PR で抽出する。抽出時は計画書「採用根拠 ADR の棚卸し」「既存資産の再利用」セクションの出典ポリシー（repo 内 commit 済み artifact 限定）に従い、典拠が確定しないものは `status: deferred` で起票する。
