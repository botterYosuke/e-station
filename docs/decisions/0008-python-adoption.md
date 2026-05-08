---
id: 0008
title: Python 採用根拠（data engine 側）
status: proposed
date: 2026-05-08
supersedes: []
---

# ADR 0008: Python 採用根拠（data engine 側）

## Status

proposed

## Context

データエンジン層には次の要件がある:

- 多数の venue（tachibana / kabusapi / hyperliquid / binance / bybit / mexc / okex）の REST + WebSocket クライアントを実装・保守する
- 各 venue 固有の正規化（tick 解決、yobine band、qty 単位、Shift-JIS、`^A^B^C` 区切り、X-API-KEY 等）を吸収する
- バックテストには `nautilus-trader` を活用する
- 戦略開発者（ユーザー）が読み書きしやすい言語で書ける（[user strategy responsibility — ADR 0004 deferred](0004-user-strategy-responsibility.md)）
- 実験的な adapter 追加・修正のサイクルが速い

低レイテンシ要求は GUI/板処理側（[ADR 0007](0007-rust-adoption.md)）が担うため、データエンジン側は表現力・エコシステムを優先できる。

## Decision

データエンジン層の言語に **Python（`requires-python = ">=3.11"`）** を採用する。パッケージ名は `flowsurface-data`、コードは `python/engine/` に集約する。

採用観点:

- venue SDK / HTTP / WebSocket クライアントの成熟したエコシステム（`websockets`, `httpx`, `pydantic`, `orjson`）
- `nautilus-trader>=1.211,<2.0` をバックテストエンジンとして直接組み込める
- `asyncio` ベースで多数 venue の並行接続を素直に書ける
- 動的型付けによる adapter プロトタイプの速さ（pydantic で IPC 境界のみ厳格化）
- ユーザー戦略を Python で書かせる方針（[ADR 0004](0004-user-strategy-responsibility.md) deferred）と整合

`pyproject.toml` は `hatchling` ビルド + workspace ルートに置き、`pytest` でテスト discovery する（`pytest.ini` 参照）。

## Consequences

良い影響:
- venue 追加が Python 単独で完結し、Rust 改修が不要（[ADR 0001](0001-rust-python-boundary.md)）
- `nautilus-trader` をバックテスト基盤として再利用できる
- 戦略 SDK をユーザーに Python で提供できる

悪い影響 / トレードオフ:
- IPC 境界（[ADR 0002](0002-ipc-schema-versioning.md)）で Rust ↔ Python の型整合を恒久的に維持する必要がある
- 動的型付け由来の silent failure リスク（実例: ポストフェーズ監査 17.1 の `list_tickers` `min_ticksize` 欠落 silent skip）に対しては、IPC 出力の jsonschema validation と adapter ごとの統合テストで継続的に防御する必要がある
- Python toolchain（3.11+ + uv / hatchling + grpc tooling）を開発者・CI 双方で揃える必要がある

## Sources

- `pyproject.toml` — `flowsurface-data` 定義、`requires-python = ">=3.11"`、`nautilus-trader` / `pydantic` / `httpx` / `websockets` / `grpcio` 依存
- `python/engine/` — データエンジン本体（commit `b71ae5f` で `data` → `engine` リネーム）
- `python/engine/schemas.py` — Python 側 IPC スキーマ定義
- `python/engine/exchanges/` — venue 別 adapter（tachibana / kabusapi / hyperliquid / binance / bybit / mexc / okex）
- `python/engine/exchanges/normalize.py` — Python 側の正規化単一供給源
- `pytest.ini` — pytest discovery 設定
