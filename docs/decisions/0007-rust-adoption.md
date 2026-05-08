---
id: 0007
title: Rust 言語選択の根拠
status: proposed
date: 2026-05-08
supersedes: []
---

# ADR 0007: Rust 言語選択の根拠

## Status

proposed

## Context

GUI / 描画 / 板キャッシュ層には次の要件がある:

- 数千 entry 規模の order book を毎秒数十回 regroup / re-render する低レイテンシ性能
- WebSocket / IPC 経由で流れ続ける diff event を panic なしに処理し続ける堅牢性
- iced（GUI） / Bevy（描画）等のクロスプラットフォームライブラリを native で動かせる
- 板表示の不変条件（価格は tick の整数倍、qty は venue 単位で正規化済み）を **型で表現**し、debug ビルドで `debug_assert!` 検証できる

GC ありの managed 言語ではフレームレート保証が難しく、C/C++ では unsafe 領域が広がりすぎる。

## Decision

GUI / 描画 / 板キャッシュ層の言語に **Rust（edition 2021、toolchain 1.95.0 pin）** を採用する。

採用観点:

- 所有権・借用検査による メモリ安全性 + ゼロコスト抽象
- `Price` / `Qty` / `MinTicksize` などの newtype による不変条件の型表現（[Rust patterns](../../.claude/rules/rust/patterns.md) 参照）
- iced / wgpu エコシステムによるネイティブ GUI
- `cargo` による単一の依存・テスト・ベンチマーク管理
- workspace 構成で `exchange` / `data` / `engine-client` などの境界を明示できる

ワークスペース構成と lint 設定（`clippy.toml`、`rustfmt.toml`）は [coding-standards](../contributing/coding-standards.md) で定義する。

## Consequences

良い影響:
- 板処理ホットパスでフレームレートを保証できる
- 型システムで「未正規化データが Rust に到達してはならない」という [ADR 0001](0001-rust-python-boundary.md) の不変条件を `debug_assert!` で形式化できる
- workspace 内での crate 分割により、`exchange` クレートだけ単体テスト・ベンチを回せる

悪い影響 / トレードオフ:
- 学習コスト・コンパイル時間が大きい
- venue 追加時に Rust 知識が要らないよう、venue ロジックは Python 側に閉じる必要がある（[ADR 0001](0001-rust-python-boundary.md)）
- toolchain pin（1.95.0）の更新は workspace 全体に影響する

## Sources

- `Cargo.toml` — `flowsurface` ワークスペースルート、ライセンス `GPL-3.0-or-later`
- `rust-toolchain.toml` — channel `1.95.0`、components `clippy` + `rustfmt`
- `clippy.toml` / `rustfmt.toml` — lint / 整形設定
- `engine-client/src/lib.rs` — Rust 側 IPC 公開 API（`SCHEMA_MAJOR/MINOR` 定数を含む）
- `exchange/src/depth.rs` — 板処理ホットパス（debug_assert ベースの不変条件検証）
- `docs/specs/data-engine/archive/refactor-rust-python-boundary-2026-05-01.md` — Rust 側を「描画・UI・ユーザー入力」に純化する決定（§0, §2.2）
