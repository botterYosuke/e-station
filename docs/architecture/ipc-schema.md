---
title: IPC スキーマと SCHEMA_MAJOR/MINOR 運用
status: draft
authored: 2026-05-08
sources:
  - engine-client/src/lib.rs
  - python/engine/schemas.py
  - proto/engine.proto
  - scripts/check_schema_parity.py
  - docs/roadmap/changelog.md
---

# IPC スキーマと SCHEMA_MAJOR/MINOR 運用

## Single Source of Truth

**SCHEMA_MAJOR / SCHEMA_MINOR の SoT は `engine-client/src/lib.rs` の履歴コメント** である。
本ドキュメントは要約であり、矛盾した場合は lib.rs を正とする。

```rust
// engine-client/src/lib.rs（抜粋）
pub const SCHEMA_MAJOR: u32 = 3;
pub const SCHEMA_MINOR: u32 = 23;
```

履歴は同ファイルの doc comment（10 → 23 の昇順）を参照。Python 側
`python/engine/schemas.py` の `SCHEMA_MAJOR` / `SCHEMA_MINOR` 定数は Rust 側と
**完全一致**しなければならない。

## バージョニングルール

| 種別 | 例 | 互換性 |
|---|---|---|
| **MAJOR インクリメント** | フィールド削除・型変更・コマンド削除 | 破壊的、`schema_major` 不一致は handshake 失敗で接続拒否 |
| **MINOR インクリメント** | 新コマンド追加 / 既存メッセージへの **optional** フィールド追加 | 後方互換、`schema_minor` 差は警告のみで接続継続 |
| **欠番** | 16（誤採番、17 を正規番号として採用） | スキップ可、再利用しない |

新フィールドは原則 optional で追加し MINOR を上げる。互換性が崩れる変更は MAJOR を
上げ、`docs/roadmap/changelog.md` に年表を残す。

## 整合性検証

`scripts/check_schema_parity.py` が以下を検証する:

- `engine-client/src/lib.rs` の `SCHEMA_MAJOR` / `SCHEMA_MINOR` ↔ `python/engine/schemas.py` 同一性
- `engine-client/src/dto.rs` の DTO ↔ `python/engine/schemas.py` の Pydantic モデル ↔ `proto/engine.proto` の三者一致

CI で必須チェック化されており、ローカルでも `uv run python scripts/check_schema_parity.py`
で実行できる。

## 関連ドキュメント

- 契約一覧（コマンド × イベント表）: [../reference/ipc-protocol.md](../reference/ipc-protocol.md)
- 境界の所有権: [boundaries.md](boundaries.md)
- スキーマ変更年表: [../roadmap/changelog.md](../roadmap/changelog.md)
