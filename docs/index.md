---
title: e-station ドキュメント
status: draft
authored: 2026-05-08
---

# e-station ドキュメント

**e-station** は Rust（Iced GUI）+ Python データエンジンで構成されるマーケットデータ可視化アプリです。
本サイトは **エンジニアおよびコントリビュータ向け** のドキュメントを集約します。

> エンドユーザー向けの操作手順 / Getting Started / Live Strategy / Replay / Backtest /
> Orders / Charts / Modes & Venues / File Menu / Settings / Troubleshooting は、
> **GitHub Wiki** に分離されています。
> → [GitHub Wiki](https://github.com/botterYosuke/e-station/wiki)

---

## エンジニア向け

実装仕様・アーキテクチャ・IPC 契約・モジュール詳細を読みたい方。

- **アーキテクチャ**
  - [全体像](architecture/overview.md) — プロセス構成と責務分担
  - [Rust ↔ Python 境界](architecture/boundaries.md) — 所有権と責務
  - [データフロー](architecture/data-flow.md) — live / replay / backtest
  - [IPC スキーマ](architecture/ipc-schema.md) — SCHEMA_MAJOR/MINOR 運用
  - モジュール別: [data-engine](architecture/modules/data-engine.md) / [tachibana-adapter](architecture/modules/tachibana-adapter.md) / [kabusapi-adapter](architecture/modules/kabusapi-adapter.md) / [nautilus-trader](architecture/modules/nautilus-trader.md) / [ui-shell](architecture/modules/ui-shell.md)
- **仕様（実装契約）**
  - [data-engine](specs/data-engine.md) / [order](specs/order.md) / [replay](specs/replay.md) / [backtest](specs/backtest.md) / [live-strategy](specs/live-strategy.md)
  - venue 別: [tachibana](specs/venues/tachibana.md) / [kabusapi](specs/venues/kabusapi.md)
- **リファレンス**
  - [IPC プロトコル契約](reference/ipc-protocol.md)
  - 外部 API: [external-apis/](reference/external-apis/)
- **ロードマップ / 進行中の論点**
  - [roadmap/](roadmap/README.md) — モジュール別 implementation-plan / open-questions
  - [スキーマ年表 changelog](roadmap/changelog.md)

## コントリビュータ向け

コードを書く / レビューする / リリースする方。

- **開発環境**
  - [setup](development/setup.md) — toolchain / 依存 / ローカル起動
  - [build-and-release](development/build-and-release.md) — ビルド / 配布 / バージョニング
  - [troubleshooting](development/troubleshooting.md) — 開発時のハマりどころ・runbook
- **規約**
  - [coding-standards](contributing/coding-standards.md)
- **テスト**
  - [strategy](testing/strategy.md) — unit / integration / E2E / invariant / TDD
  - [invariants](testing/invariants.md) — INV-ID 一覧
  - [benchmarks](testing/benchmarks.md)
- **意思決定**
  - [decisions/](decisions/README.md) — ADR 一覧（status: proposed / accepted / deferred / superseded）

---

## ソース

[github.com/botterYosuke/e-station](https://github.com/botterYosuke/e-station)
