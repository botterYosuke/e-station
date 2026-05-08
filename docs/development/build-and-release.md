---
title: ビルドと配布
status: draft
authored: 2026-05-08
sources:
  - scripts/build-engine.sh
  - scripts/build-windows.sh
  - scripts/build-macos.sh
  - scripts/package-linux.sh
  - pyproject.toml
  - Cargo.toml
  - docs/development/_distribution-formats-fragment.md
---

# ビルドと配布

## バージョニング

- workspace バージョンは `Cargo.toml` の `[workspace.package].version` が SoT。
- Python 側 `pyproject.toml` の `version` は Rust 側と同期させる（CI ガード対象、
  pip 配布化時に必須化予定 → §pip 配布）。
- IPC スキーマの version 運用は別系統 → [../architecture/ipc-schema.md](../architecture/ipc-schema.md)

## 開発ビルド

```bash
cargo build                # debug
cargo build --release      # release
```

## 配布ビルド

### Python engine の bundling

`scripts/build-engine.sh` が PyInstaller で `flowsurface-engine` 単独実行可能
バイナリを生成する（`pyproject.toml` の `[project.optional-dependencies] build` で
`pyinstaller>=6.5` を導入）。出力先: `target/release/python-engine/flowsurface-engine[.exe]`。

```bash
uv sync --extra build
scripts/build-engine.sh
```

### OS 別パッケージ

| OS | スクリプト | 形式 |
|---|---|---|
| Windows | `scripts/build-windows.sh` | portable zip |
| macOS | `scripts/build-macos.sh` | `.app` バンドル |
| Linux | `scripts/package-linux.sh` | `flowsurface-x86_64-linux.tar.gz` (および aarch64) |

## Linux 配布形式の選定

**結論: tar.gz を維持する。AppImage / Flatpak は採用しない。**

判断根拠（要約。詳細は移送元 fragment を参照）:

- AppImage は glibc 互換問題を解決せず、FUSE 依存で UX が悪化するケースがある。`linuxdeploy` 系のビルド複雑度に見合うメリットがない。
- Flatpak は sandbox と `keyring` crate（OS keyring 連携）／`wgpu` GPU アクセス／loopback IPC（19876 等）の整合に追加コストがかかり、`org.freedesktop.Platform` の追従負荷も大きい。
- tar.gz は `cargo build` → `bin/` レイアウト → `tar czvf` の 3 ステップで完結し、`engine-client/src/process.rs::EngineCommand::resolve` の隣接探索とも整合する。
- AppImage / Flatpak へのユーザー要望は現時点で 0 件。

**再評価トリガ**: AppImage / Flatpak 要望が **3 件以上** 蓄積、glibc / fontconfig 不整合の
複数ディストリ報告、または GUI shop（GNOME Software / KDE Discover）露出の戦略目標化。

## `pip install estation` 配布計画（提案）

PyPI 経由で **Rust GUI + Python engine** を一括配布する第三チャネル。詳細仕様は
別 PR で着地予定（status: proposed）。

要点:

- パッケージ名候補: `estation`（PyPI 空き要確認、代替 `e-station` / `flowsurface-app`）
- ビルドバックエンド: **hatchling + custom build hook**（`hatch_build.py` で `cargo build --release` → `_bin/` にコピー）
- wheel: `cibuildwheel` で `cp311/cp312-{win_amd64, macosx_11_0_arm64, manylinux_2_28_x86_64}` を 3 OS 同時 GA
- Python ランタイムは **同梱しない**。GUI 側 Settings に「Python 起動コマンド」項目を追加し、`engine-client/src/process.rs::EngineCommand::resolve_with` の解決順序を `--engine-cmd` → 設定 `python_command` → PyInstaller bundle → PATH の `python3/python` に拡張する。
- Rust バイナリは **常に同梱**（Python 単独モードのユーザーも `flowsurface{.exe}` 分のサイズを許容）。`extras_require` での分割は行わない。
- 既存 `flowsurface-data` パッケージは `estation` に **rename して一本化**（`import engine` → `import estation.engine`）。

未決事項: パッケージ名確保 / PyPI 100MB 制限超過時の wheel 分割方針 / `python_command`
の永続化先（`saved-state.json` か OS 標準設定か）/ macOS Intel・Linux aarch64 の追加
タイミング。
