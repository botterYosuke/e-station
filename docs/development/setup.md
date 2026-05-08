---
title: 開発環境セットアップ
status: draft
authored: 2026-05-08
sources:
  - rust-toolchain.toml
  - Cargo.toml
  - pyproject.toml
  - uv.lock
  - README.md
  - AGENTS.md
---

# 開発環境セットアップ

## 必須 toolchain

| 言語 | バージョン | 取得元 |
|---|---|---|
| Rust | **1.95.0**（`rust-toolchain.toml` でピン） | rustup（自動切替） |
| Python | **3.11+**（`pyproject.toml: requires-python = ">=3.11"`） | OS 標準 / pyenv / uv |
| uv | 最新 | https://docs.astral.sh/uv/ |

`rustup` が入っていれば、リポジトリ内で `cargo` を実行するだけで 1.95.0 + clippy +
rustfmt が自動で揃う。

## 初回セットアップ

```bash
# Rust 側
cargo build                           # debug ビルド + dependency 解決

# Python 側（推奨: uv）
uv sync --all-extras --dev            # dev dependencies + docs / test ツール込み
```

`uv` は `uv.lock` を SoT として lockstep で依存解決する。`pip install -e .` でも動くが
CI と乖離するため非推奨。

## ローカル起動

```bash
# 1. Python engine 単独起動（gRPC サーバ）
uv run python -m engine --port 19876 --token dev-token

# 2. 別ターミナルで Rust GUI を起動
cargo run                             # debug
cargo run --release                   # release（ログは flowsurface-current.log）
```

`cargo run` は `engine-client/src/process.rs::EngineCommand::resolve` で Python
プロセスを auto-spawn する。明示起動した engine に attach させたい場合は
`--engine-cmd` または環境変数で接続先を指定する。

## ログ位置

- **release** ビルド: `~/AppData/Roaming/flowsurface/flowsurface-current.log` (Windows) /
  `~/Library/Application Support/flowsurface/...` (macOS) /
  `~/.local/share/flowsurface/...` (Linux)
- **debug** ビルド: stdout（ターミナルに直出し）

## IDE

- VS Code + `rust-analyzer` + Pyright（`pyproject.toml` の `[tool.pyright]` で
  `extraPaths = ["python"]` 設定済み）
- RustRover / PyCharm でも可

## トラブル時

- ビルド・起動・接続まわりのハマりどころは [troubleshooting.md](troubleshooting.md)
- IPC スキーマ不整合は [`scripts/check_schema_parity.py`](../architecture/ipc-schema.md) を実行
