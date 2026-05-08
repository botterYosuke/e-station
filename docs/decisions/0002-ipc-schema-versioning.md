---
id: 0002
title: IPC schema バージョニング (SCHEMA_MAJOR / SCHEMA_MINOR)
status: accepted
date: 2026-05-08
source_commit: da20f73
supersedes: []
---

# ADR 0002: IPC schema バージョニング (SCHEMA_MAJOR / SCHEMA_MINOR)

## Status

accepted

## Context

Rust GUI と Python data engine は IPC 経由（gRPC transport / 旧 WebSocket）で常時通信し、両者は別プロセス・別ライフサイクルで spawn / attach される。スキーマ不整合が起きるとハンドシェイク段階で気づけず、event の silent drop や型不一致による panic を招く。バージョン管理の SoT を一意に決めて、Rust ↔ Python ↔ JSON Schema ↔ protobuf の 4 系統が必ず同じ採番に追従できる仕組みが必要だった。

## Decision

IPC スキーマは **MAJOR.MINOR** 二段階で版管理し、SoT を `engine-client/src/lib.rs` の `SCHEMA_MAJOR` / `SCHEMA_MINOR` 定数および同ファイル冒頭の **時系列履歴コメント**に置く。

- **MAJOR**: 後方非互換変更時に bump。MAJOR 不一致ではハンドシェイクを失敗させ、`start_or_attach` が再 spawn する
- **MINOR**: 後方互換のフィールド追加・enum 追加で bump。フェーズ完了ごとに加算
- 履歴コメントは「N: 概要」を時系列順に書き、欠番（誤採番）も明記する
- Python 側 `python/engine/schemas.py` は同名定数を持ち、`scripts/check_schema_parity.py` が CI で Rust/Python/JSON/proto の 4 系統 parity を assert する
- JSON Schema の物理 artifact は `docs/specs/data-engine/schemas/{commands,events}.json`、年表は同ディレクトリ `CHANGELOG.md` に書く（再編後は `docs/roadmap/changelog.md` に移送）
- protobuf 定義は `proto/engine.proto`（gRPC transport）

## Consequences

良い影響:
- スキーマ不整合が即座にハンドシェイク失敗として検出される（silent failure の最大の温床を潰せる）
- 履歴が SoT 内コメントに集約され、PR レビュー時に「この MINOR は何の変更か」が即座に追える
- `check_schema_parity.py` で CI ゲート化されており、Python だけ忘れるパターンが防がれる

悪い影響 / トレードオフ:
- 4 系統（Rust 定数 / Python 定数 / JSON Schema / proto）を毎回 4 箇所同時更新する必要がある
- MINOR 採番ミス（番号 16 のような）が起きると履歴コメントで「欠番」を明記し続ける運用負荷が残る
- 履歴コメントは git blame との重複情報になりやすい

## Sources

- `engine-client/src/lib.rs` — **SoT**。`SCHEMA_MAJOR=3` / `SCHEMA_MINOR=23` 定数 + 時系列履歴コメント
- `python/engine/schemas.py` — Python 側のミラー定数
- `proto/engine.proto` — gRPC transport の protobuf 定義
- `scripts/check_schema_parity.py` — Rust/Python/JSON/proto の 4 系統 parity 検証
- `docs/specs/data-engine/schemas/CHANGELOG.md` — schema バージョン年表
- `docs/specs/data-engine/archive/refactor-rust-python-boundary-2026-05-01.md` — Phase F での MAJOR 2→3 bump の判断記録（§9, Q4）
