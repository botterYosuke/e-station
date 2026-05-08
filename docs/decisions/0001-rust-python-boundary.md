---
id: 0001
title: Rust と Python の責務境界
status: accepted
date: 2026-05-08
source_commit: da20f73
supersedes: []
---

# ADR 0001: Rust と Python の責務境界

## Status

accepted

## Context

`docs/specs/data-engine/archive/refactor-rust-python-boundary-2026-05-01.md` で扱った "alternating zeros" バグは、Rust 側に `TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32 = 1.0` のような venue 知識が漏出し、Python と Rust の双方で価格丸め・qty 正規化を行っていたことが根本原因だった。`Exchange::is_depth_client_aggr()` のように venue 判定を Rust enum マッチで持つ箇所が 10 箇所以上あり、venue 追加のたびに Rust 改修が必要な構造になっていた。

## Decision

**案 C — Rust 側 depth/price 正規化を完全撤去し、Python 側を「正規化済みデータの単一供給源」とする** を採用する。

責務分担:

- Python: venue REST/WS 接続、価格 tick 解決、tick 丸め、qty 正規化、best bid/ask 算出、venue capability (`VenueCaps`) の宣言
- Rust: 受信キャッシュ、`regroup_from_raw` step バケッティング、`TickMultiplier` ユーザー操作、ChaseTracker/Spread 表示、Ladder/Chart 描画

IPC 不変条件: Python が送る `Depth`/`Trade`/`Kline` は `min_ticksize` の整数倍に丸め済み・qty も venue 単位で正規化済みであることを Python 側で保証し、Rust は debug ビルドで `debug_assert!` 検証、release では信頼する。`VenueCaps` は IPC 境界 DTO `TickerEntry` の一部としてのみ運ばれ、Rust 側では `VenueCapsStore` に sidecar として保持する（`TickerInfo` 本体には載せない — `Hash`/`Eq` を破壊しないため）。

移行は Phase A（スキーマ硬化）→ B（VenueCaps 配信）→ C（Python 正規化一元化）→ D（Rust venue 知識除去）→ E（Rust 正規化 no-op 化）→ F（SCHEMA_MAJOR bump）の 6 段階で実施。

## Consequences

良い影響:
- venue 追加時に Rust 改修が不要
- 正規化バグが Python 側に一元化され追跡しやすい
- IPC スキーマが typed discriminated union で表現され、無型 `Vec<Value>` が消える

悪い影響 / トレードオフ:
- Python 側に正規化バグが入ると全 venue が同時に死ぬため、ユニットテスト・property test を厚く敷く必要がある
- Rust 側で隠蔽されていた Python 側の不整合が露出する（Phase E 投入前の soak が必須）
- `Vec<TickerEntry>` の per-entry tolerance はないため、1 entry の serde 失敗で frame 全体が drop される（ポストフェーズ監査 17.1 で silent failure を 1 件発見し修正済）

## Sources

- `docs/specs/data-engine/archive/refactor-rust-python-boundary-2026-05-01.md` — リファクタ実装計画と Phase A〜F 完了メモ、ポストフェーズ監査 17.1 / 17.2
- `engine-client/src/lib.rs` — SCHEMA_MAJOR=3 / SCHEMA_MINOR=23 の到達点と minor 履歴コメント
- `engine-client/src/dto.rs` — `TickerEntry` discriminated union、`VenueCaps` 定義
- `engine-client/src/venue_caps.rs` — Rust 側 sidecar `VenueCapsStore`
- `python/engine/exchanges/normalize.py` — Python 側正規化の単一供給源
