---
title: テスト戦略
status: draft
authored: 2026-05-08
sources:
  - AGENTS.md
  - python/tests/
  - tests/
  - engine-client/tests/
  - .github/workflows/python-tests.yml
  - .github/workflows/rust-tests.yml
---

# テスト戦略

e-station は **Rust + Python の二言語構成** ゆえ、テストも両側に分かれる。
TDD を基本姿勢とし、不具合修正後は必ず既存テストで検出できなかった理由を
構造的に分析する（`/bug-postmortem` スキル）。

## テスト階層

| レイヤ | 場所 | フレームワーク | 目的 |
|---|---|---|---|
| Rust ユニット | `src/`・各 crate 内 `#[cfg(test)] mod tests` | 標準 `#[test]` + `tokio::test` + `rstest` | 関数 / モジュール単位の振る舞い |
| Rust 統合 | `tests/`・`engine-client/tests/` | tokio + tonic / mockito | IPC handshake / 状態遷移 / mode 切替 |
| Python ユニット | `python/tests/test_*.py` | pytest + pytest-asyncio | adapter / schema / dispatcher |
| Python 統合 | `python/tests/test_engine_*.py` | pytest + grpc test client | engine.server の multi-client broadcast 等 |
| E2E | Python helper（`ReplaySession` / `LiveSession`）+ pytest | — | アプリ全体（HTTP API・Playwright は不使用）|
| invariant | `python/tests/test_invariant_*.py` + 各 INV-ID | pytest | 不変条件 ID（`INV-<MODULE>-NNN`）の網羅 |
| benchmark | `docs/testing/benchmarks/` | criterion / 計測スクリプト | パフォーマンス退行検出 |

## カバレッジ方針

- 目標 80%+（Rust は `cargo-llvm-cov`、Python は pytest-cov）
- ただし silent failure の検出は カバレッジより **失敗経路アサーション** を重視
- venue モック・REST/WS スタブで外部依存を最小化（`mockito` / `pytest-httpx`）

## 外部依存マーク

`python/tests/` の `@pytest.mark.live` / `@pytest.mark.demo_tachibana` は実取引所 /
J-Quants / 立花 demo 環境に依存する。CI 既定では `-m "not live"` で除外し、
専用 workflow（`tachibana-demo.yml` / `kabu-mock.yml`）で個別実行する。

## TDD ループ

1. 失敗するテストを 1 件書く（red）
2. 最小実装で通す（green）
3. リファクタ（refactor）
4. バグ修正後は **必ず `/bug-postmortem`** で「なぜ既存テストで取れなかったか」を分析
5. 不変条件として残すべきものは `INV-<MODULE>-NNN` を付与し
   [invariants.md](invariants.md) 経由で 1:1 対応を取る

詳細パターン（property-based / mocking / 並列実行）は
[../contributing/coding-standards.md](../contributing/coding-standards.md) も参照。
