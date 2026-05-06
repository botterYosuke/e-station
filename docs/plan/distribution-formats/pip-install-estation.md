# `pip install estation` 配布計画

> 作成: 2026-05-06 / 配布チャネル追加の設計記録
>
> 関連:
> - [linux-formats.md](./linux-formats.md) — 既存の tar.gz / AppImage / Flatpak 判断
> - [scripts/build-windows.sh](../../../scripts/build-windows.sh) — 既存 Windows portable zip
> - [scripts/build-engine.sh](../../../scripts/build-engine.sh) — PyInstaller による engine bundling
> - [pyproject.toml](../../../pyproject.toml) — 現状 `flowsurface-data` パッケージ

## 1. 目的とスコープ

`pip install estation` で **Rust GUI (`flowsurface.exe`) と Python データエンジンの両方** を入手できるようにする。GitHub Releases の zip / tar.gz と並ぶ第三の配布チャネルとして提供する。

- 提供単位: PyPI パッケージ `estation`（要確保）
- 同梱物: `flowsurface.exe`（Rust GUI）+ `python/engine/`（既存 Python パッケージ）+ `assets/`
- 起動 UX: `estation` console_script で GUI を spawn / `python -m estation.engine` で engine 単独
- 既存 `flowsurface-data` パッケージとの関係: **`estation` に rename して一本化** する案を本命とする（§5.3）

非ゴール:

- macOS / Linux 向け wheel の即時提供（§4 参照、Windows x64 から段階導入）
- conda-forge / Homebrew への登録
- AI / 機械学習依存の同梱（[memory: project_no_bundled_ai](../../../memory/project_no_bundled_ai.md) 方針に従う）

## 2. 結論（提案）

| 項目 | 採用案 |
|---|---|
| パッケージ名 | `estation`（PyPI 上で空き確認が前提） |
| ビルドバックエンド | **hatchling + custom build hook**（既存最小変更）。将来 PyO3 双方向呼び出しが必要になった時点で maturin 移行を再評価 |
| wheel platform tag | `cp311-cp311-{win_amd64,macosx_11_0_arm64,manylinux_2_28_x86_64}` 等の **platform-specific wheel** を `cibuildwheel` で量産 |
| 対応 OS | **Windows x64 / macOS arm64 / Linux x64 を同時 GA**。需要待ちにせず初版から 3 OS 揃える |
| Python ランタイム | **同梱しない**。利用者環境の Python を使う。`flowsurface` 側に「Python 起動コマンド」設定項目を新設して任意のパスを指定可（§5.4） |
| Rust バイナリ配置 | wheel 内 `estation/_bin/flowsurface{.exe,}` を **常に同梱**（§5.6） |
| 起動経路 | `estation` console_script → `flowsurface{.exe}` を spawn。`--mode` はそのまま渡す |
| Engine spawn | `flowsurface` から **設定の Python コマンド** で `-m estation.engine` を起動するように [engine-client/src/process.rs](../../../engine-client/src/process.rs) の `EngineCommand::resolve` を拡張 |

## 3. パッケージ構造

```
estation/                          ← PyPI distribution name = "estation"
├── pyproject.toml                  ← name="estation", build-backend=hatchling
├── hatch_build.py                  ← cargo build を呼び _bin/ にコピーする custom hook
├── src/estation/
│   ├── __init__.py
│   ├── __main__.py                 ← `python -m estation` = GUI 起動
│   ├── _bin/
│   │   └── flowsurface{.exe}       ← Rust GUI（hook がビルドして配置、OS 別 wheel に 1 個ずつ）
│   ├── engine/                     ← 既存 python/engine/ をここへ移動 or symlink
│   │   ├── __init__.py
│   │   ├── server.py
│   │   └── ...
│   └── assets/                     ← icons 等
└── README.md
```

OS 別 wheel に含まれる `_bin/` の中身:

| wheel tag | 同梱バイナリ |
|---|---|
| `*-win_amd64` | `flowsurface.exe` |
| `*-macosx_11_0_arm64` | `flowsurface`（Mach-O, ad-hoc 署名 or codesign） |
| `*-manylinux_2_28_x86_64` | `flowsurface`（ELF, `patchelf` で rpath 調整） |

console_scripts:

```toml
[project.scripts]
estation = "estation.__main__:main"          # GUI 起動
estation-engine = "estation.engine.__main__:main"  # engine 単独
```

## 4. 対応 OS — 3 プラットフォーム同時 GA

初版から Windows / macOS / Linux 全対応で公開する。段階導入はしない。

| Wheel tag | ターゲット | ビルド環境 | 補足 |
|---|---|---|---|
| `cp311/cp312-*-win_amd64` | Windows 10/11 x64 | `windows-latest` runner, MSVC | 既存 `build-windows.sh` の流用 |
| `cp311/cp312-*-macosx_11_0_arm64` | macOS 11+ Apple Silicon | `macos-14` runner | `delocate-wheel` で dylib を `.dylibs/` へ移動 / ad-hoc codesign |
| `cp311/cp312-*-manylinux_2_28_x86_64` | RHEL 9 / Ubuntu 22.04 系 | `manylinux_2_28` container | `auditwheel repair` で .so 同梱、Vulkan loader は `libvulkan.so.1` を含めない（OS 提供前提） |

- 全 OS で `cibuildwheel` を使い、GitHub Actions のマトリクスで一括ビルドする
- macOS は **arm64 のみ**（Intel mac は当面切り捨て、需要があれば追加）
- Linux は **x64 のみ**（aarch64 は需要待ち）
- `pip install estation` は **wheel が無い tag では `--no-binary` 不可エラー** で停止させる（sdist を提供しない）。中途半端な「コンパイル試行 → 失敗」体験を避けるため

## 5. 検討事項と判断

### 5.1 ビルドバックエンドの選定

| 案 | pros | cons | 判定 |
|---|---|---|---|
| **A. hatchling + build hook** | 現状の `pyproject.toml` から最小差分。Rust 側は `cargo build --release` を呼ぶだけ | wheel platform tag を自前で計算する必要あり | **採用** |
| B. maturin | Rust bin crate も同梱可、PyO3 連携が将来容易 | 現在 PyO3 を使っていない。bin-only crate で maturin を使うのは規約上やや変則 | 将来再評価 |
| C. setuptools-rust | 古くからの実績 | hatchling から戻る理由が薄い | 不採用 |

### 5.2 Python ↔ Rust の起動経路書き換え

現状 [engine-client/src/process.rs](../../../engine-client/src/process.rs) の `EngineCommand::resolve_with` は `<base_dir>/flowsurface-engine[.exe]`（PyInstaller 産物）を探す。`pip install` 配布では Python が利用者環境にあるため、以下を追加する:

1. `EngineCommand::resolve_with` に Python ベース起動経路を追加し、**設定ファイルから Python コマンドを読む**ように変更
2. 解決順序:
   1. `--engine-cmd` 引数（既存、最優先）
   2. **設定ファイルの `python_command`**（§5.4 で新設）→ `<python_command> -m estation.engine`
   3. `<base_dir>/flowsurface-engine[.exe]`（既存 PyInstaller bundle が同梱されている場合のみ）
   4. システム PATH 上の `python3` / `python` → `-m estation.engine`（fallback）
3. pip wheel に `flowsurface-engine.exe`（PyInstaller bundle）を含めない。利用者環境の Python を使う

これにより portable zip 配布（PyInstaller bundle）と pip 配布（同梱モジュール + ユーザー Python）が共存する。

### 5.3 既存 `flowsurface-data` パッケージとの関係

現在 [pyproject.toml](../../../pyproject.toml) は `name = "flowsurface-data"` で **wheel に Python のみ** を含める設定。これを以下のいずれかに変える:

| 案 | 内容 | 判定 |
|---|---|---|
| **A. rename して一本化** | `flowsurface-data` を `estation` に改名し、Rust 同梱を追加 | **本命**。配布チャネルが乱立しない |
| B. 並立 | `flowsurface-data`（Python のみ）と `estation`（Rust 同梱）を別 PyPI distribution として出す | 名前空間が分裂、import path 衝突リスク |

A を採用する場合、既存 `import engine` を `import estation.engine` に書き換える必要がある（影響範囲は `python/engine/` 内部参照と `python/tests/` のみ。Rust 側は文字列 `python -m engine` を `python -m estation.engine` に変えるだけ）。

### 5.4 Python ランタイム非同梱と Settings の Python コマンド項目（**新規**）

**方針**: pip wheel には Python ランタイムを含めない。利用者の `pip install estation` を実行した Python 環境を使うのが第一義だが、GUI を別の Python から起動するケース（venv / conda / uv 管理下の Python など）にも対応するため、設定で Python コマンドを上書きできるようにする。

#### Settings UI への追加項目

`File > Settings`（または該当する設定ダイアログ）に **「Python 起動コマンド」** 欄を追加:

| 項目 | 既定値 | 説明 |
|---|---|---|
| `python_command` | 空（=自動検出） | 例: `C:\Users\foo\.venv\Scripts\python.exe`、`/opt/homebrew/bin/python3.12`、`uv run --project /path python` |

- 空欄時の自動検出順: `sys.executable`（GUI を起動した Python）→ PATH 上の `python3` → `python`
- 文字列はシェル引数として split して `Command::new()` に渡す（クォート対応）
- 設定変更は engine 再起動時から反映（既起動の engine プロセスは触らない）

#### 永続化先

[`saved-state.json`](../../../src/) の `settings` セクションに `python_command: Option<String>` フィールドを追加。replay モードでは load/save しない方針（AGENTS.md D9）に従い、`python_command` は live モードでのみ書き出す。

#### PyInstaller との関係

現状 `scripts/build-engine.sh` は PyInstaller で `flowsurface-engine.exe` を作っている。pip 配布では不要だが、**portable zip / tar.gz 配布では引き続き必要**:

- **portable zip / tar.gz**: PyInstaller bundle を同梱（既存どおり、Python 不要で動く）
- **pip wheel**: PyInstaller bundle を同梱しない。設定の Python コマンドで `-m estation.engine` を起動

### 5.5 パッケージ名の確保

`estation` が PyPI で空いているか確認が必要。空いていない場合の代替案:

- `e-station`（ハイフン許可、import name は `e_station`）
- `flowsurface`
- `flowsurface-app`

### 5.6 AGENTS.md の方針との整合 — Rust は常に同梱

[memory: project_python_only_mode](../../../memory/project_python_only_mode.md) の「Python 単独モード」方針は、wheel を分割せず **「Rust を呼ばなければ Python 単独で動く」** という運用で実現する:

- pip wheel には **常に `flowsurface{.exe}` を同梱**（OS 別 wheel に 1 個ずつ）
- Python 単独モードのユーザーは `from estation.engine import ...` のように engine だけを import する。Rust バイナリは wheel 内に置かれているだけで実行されない
- `extras_require` の `gui` / `engine-only` 分割は **不要**。判断軸はインストールではなく実行時にユーザーが選ぶ

利点:
- wheel variant を増やさず CI matrix が単純
- ドキュメントの分岐が減る（`pip install estation` 一本で説明できる）

トレードオフ:
- Python 単独で使うユーザーも Rust バイナリ分（~30〜50MB）を強制ダウンロードする → §6 のサイズ制限と合わせて要監視

## 6. リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| `flowsurface.exe` の依存 DLL（VC++ runtime, etc.）が利用者環境に無い | 起動失敗 | wheel に必要な runtime を同梱 or `vc_redist` を README で案内 |
| `wgpu` / Vulkan loader の不在 | 描画失敗 | 起動時に `wgpu::Instance::enumerate_adapters` で検出して GUI ダイアログで案内 |
| keyring crate のクロスプラットフォーム差異 | 取引所トークン保存失敗 | Windows は Credential Manager 直結なので Phase 1 では問題なし |
| wheel サイズ肥大（Rust release バイナリ + Python 全部で 100MB 超） | PyPI upload 制限（100MB / file） | LZMA 圧縮、不要 asset の除外、超過時は wheel を分割（`estation-core` + `estation`） |
| PyPI への 100MB 制限超過 | upload 不可 | 申請で増枠可能、または GitHub Releases に wheel を置き `pip install -f` で対応 |
| 立花証券 API の Windows 専用ロジックが他 OS で実行される | 実行時エラー | `sys.platform` ガードを `python/engine/exchanges/tachibana_*.py` に追加 |
| パッケージ名 `estation` が既に取られている | 配布開始不可 | §5.5 の代替名へ即切替 |

## 7. 実装ステップ

### Phase 1: パッケージ骨格（OS 共通）

1. PyPI で `estation` の名前確保（または代替名確定）
2. `pyproject.toml` を `estation` に rename し、`packages = ["src/estation"]` に変更
3. `python/engine/` を `src/estation/engine/` に移動（git mv、import path 更新）
4. `hatch_build.py` を作成し、現在の OS 向けに `cargo build --release` → `src/estation/_bin/` にコピー
5. `src/estation/__main__.py` で `_bin/flowsurface{.exe}` を `subprocess.Popen` し、引数を pass-through

### Phase 2: Settings に Python コマンド項目を追加

6. `saved-state.json` の `settings` に `python_command: Option<String>` を追加（live モードでのみ persist）
7. `engine-client/src/process.rs` の `EngineCommand::resolve_with` を §5.2 の解決順序に書き換え
8. GUI 設定ダイアログに「Python 起動コマンド」入力欄を追加（変更は engine 再起動時に反映）
9. 設定の Python コマンドが解決失敗した場合の error dialog（silent failure 禁止 — `silent-failure-hunter` 観点）

### Phase 3: cibuildwheel で 3 OS マトリクスビルド

10. GitHub Actions workflow `.github/workflows/wheels.yml` を新規作成
    - `windows-latest` × `cp311 cp312` → `*-win_amd64`
    - `macos-14` × `cp311 cp312` → `*-macosx_11_0_arm64`（`delocate` 統合）
    - `ubuntu-latest` × `cp311 cp312` × `manylinux_2_28` container → `*-manylinux_2_28_x86_64`（`auditwheel repair`）
11. 各 job 内で `cargo build --release` をクロスコンパイルではなく **native build** で実行（runner OS = ターゲット OS）
12. wheel artifact をまとめて TestPyPI に push する `release` job を追加

### Phase 4: 検証

- 既存 E2E テスト（`tests/e2e/smoke.sh`）を pip 経由インストール環境で実行する shim を `python/tests/test_pip_install_smoke.py` として追加（3 OS で実行）
- portable zip 配布と pip 配布で **同じバージョン番号** を維持する CI ガード（`Cargo.toml` ↔ `pyproject.toml` の version 同期）
- `python_command` 設定の bug-postmortem 観点テスト: 不正パス指定時の error 表示・正常パスでの spawn 成功を assert
- TestPyPI で 3 OS smoke → 本番 PyPI に publish

## 8. 完了条件

- [ ] `pip install estation` が **Windows x64 / macOS arm64 / Linux x64** + Python 3.11 / 3.12 で成功する
- [ ] インストール後 `estation --mode replay` で GUI が起動し、replay フォームが開ける（3 OS）
- [ ] インストール後 `estation-engine --port 19876 --token dev-token` で engine 単独起動し、`uv run` 起動と同等の WS API を提供する
- [ ] Settings の「Python 起動コマンド」欄に任意のパスを入れて engine が再起動する
- [ ] 「Python 起動コマンド」が不正なときに silent failure せず error dialog が出る
- [ ] portable zip 配布の挙動が壊れていない（`tests/e2e/smoke.sh` が pass）
- [ ] CI で `pyproject.toml` と `Cargo.toml` の version 不一致を検出する

## 9. 未決事項（要意思決定）

1. **パッケージ名** — `estation` を確保するか、代替名（`e-station` / `flowsurface-app`）にするか
2. **PyPI 100MB 制限超過時の方針** — wheel 分割 or GitHub Releases ホスティング（特に Linux wheel が大きくなりがち）
3. **`flowsurface-data` の扱い** — 完全廃止 / yank / alias 維持のいずれか
4. **macOS Intel / Linux aarch64 の追加タイミング** — 需要待ち、それとも初版から含める？（現状は arm64 / x64 のみ）
5. **`python_command` の保存場所** — `saved-state.json` に混ぜるか、別の OS 標準設定ファイル（XDG / `%APPDATA%`）に分離するか

## 10. 参考

- 既存 Linux 配布判断: [linux-formats.md](./linux-formats.md)
- cibuildwheel: https://cibuildwheel.pypa.io/
- maturin（将来再評価用）: https://www.maturin.rs/
- PyPI ファイルサイズ制限: https://pypi.org/help/#file-size-limit
