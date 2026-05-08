---
id: 0006
title: Rust + Python 二言語構成の採用
status: proposed
date: 2026-05-08
supersedes: []
---

# ADR 0006: Rust + Python 二言語構成の採用

## Status

proposed

## Context

e-station は (a) リアルタイム描画と低レイテンシな板処理を要する **GUI + 板キャッシュ層**と、(b) 多数の venue（tachibana / kabusapi / hyperliquid / binance 等）の REST/WS 接続・正規化・データ供給を行う **データエンジン層**の 2 つの責務を抱える。両者は要求性能・依存ライブラリ・更新頻度・言語エコシステムが大きく異なり、単一言語で両方を担うと、どちらかが慢性的に不向きな選択を強いられる。

## Decision

GUI / 描画 / 板キャッシュは Rust（`flowsurface` クレート + ワークスペース crates）、データエンジンは Python（`python/engine/`）として、IPC 境界（[ADR 0002](0002-ipc-schema-versioning.md)）で分離する二言語構成を採用する。両者は別プロセスとして spawn し、`engine-client` クレート（`engine-client/src/`）が Rust 側のクライアント実装となる。

詳細な責務境界は [ADR 0001](0001-rust-python-boundary.md) を参照。Rust と Python それぞれの選定根拠は [ADR 0007](0007-rust-adoption.md) / [ADR 0008](0008-python-adoption.md) を参照。

## Consequences

良い影響:
- GUI 側は Rust の所有権・型システム・低オーバーヘッドで描画ホットパスを最適化できる
- データエンジン側は Python の豊富な venue SDK / `nautilus-trader` / 動的型付けの素早い実験性を活用できる
- 言語境界が明示されることで、各層のコーディング規約・テスト戦略・依存管理を独立に進化させられる

悪い影響 / トレードオフ:
- IPC 境界（schema / 版管理 / parity 検証）の運用コストが恒久的に発生する（[ADR 0002](0002-ipc-schema-versioning.md)）
- 両言語の toolchain（Rust 1.95 + Python 3.11+）を開発者・CI 双方で揃える必要がある
- venue ロジックが Python に集中するため、GUI 側のデバッグでも Python ログを併読する習慣が必要

## Sources

- `Cargo.toml` — Rust ワークスペース定義（`flowsurface` パッケージ、`edition.workspace = true`）
- `pyproject.toml` — Python パッケージ `flowsurface-data` 定義（`requires-python = ">=3.11"`、`nautilus-trader` 依存）
- `rust-toolchain.toml` — Rust 1.95.0 を pin
- `engine-client/` — Rust ↔ Python IPC クライアントクレート（commit `6a9870c` で導入）
- `python/engine/` — Python データエンジン本体（commit `b71ae5f` で `data` → `engine` リネーム）
- `docs/specs/data-engine/archive/refactor-rust-python-boundary-2026-05-01.md` — 二言語構成における責務境界の決定
