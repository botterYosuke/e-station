# Rust ビュアー化 + Python データエンジン化 計画

本ディレクトリは、現状 Rust 単体構成の Flowsurface (e-station) を、
**Rust = ビュアー専用 / Python = 取引所データ取得・配信** という二層構成に
リアーキテクトする計画をまとめたものです。

## 目次

- [`current-architecture.md`](./current-architecture.md) — 現状調査結果
- [`spec.md`](./spec.md) — 新仕様（責務分割・IPC・データモデル）
- [`implementation-plan.md`](./implementation-plan.md) — 段階的な実装計画
- [`open-questions.md`](./open-questions.md) — 未決事項・要相談事項
