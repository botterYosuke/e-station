---
title: "ベンチマーク索引"
status: migrated
migrated_from:
  - docs/✅python-data-engine/benchmarks/baseline.md
  - docs/✅python-data-engine/benchmarks/phase-2.md
  - docs/✅python-data-engine/benchmarks/phase-6.md
source_commit: f62bf94
---

# ベンチマーク索引

data-engine 関連のベンチマーク記録の一覧。本体の本文は `benchmarks/` 配下に据え置き複製しており、本ファイルは索引のみ提供する。

## 一覧

| フェーズ | ファイル | 主な指標 |
|---|---|---|
| Baseline | [baseline.md](benchmarks/baseline.md) | 起動時間 / depth・trade レイテンシ / アイドル時 CPU・メモリ（現行 Rust 直結のベースライン、Phase 0） |
| Phase 2 | [phase-2.md](benchmarks/phase-2.md) | IPC レイテンシ（FetchKlines RTT, IPC 純オーバーヘッド推定）/ 自動復旧時間 / depth 再同期 / CPU・メモリ |
| Phase 6 | [phase-6.md](benchmarks/phase-6.md) | PyInstaller `onefile` cold-start（first/warm median, Windows） |

## 合格ライン

各ベンチマークの合格ラインは [`specs/data-engine.md` §9 非機能要件](../specs/data-engine.md#9-非機能要件合格ライン) を参照。
